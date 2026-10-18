"""Living factsheet market monitor.

  python model/monitor/monitor.py run                         fetch -> scenario copy -> Excel recalc -> engine
                                                              reconcile -> drift check -> report (runs/<id>/)
  python model/monitor/monitor.py run --simulate host_cost=145 same pipeline, no fetch, inputs set by hand (SIMULATED)
  python model/monitor/monitor.py approve <run-id> --by NAME --note "..."
  python model/monitor/monitor.py reject  <run-id> --by NAME --note "..."

approve / reject only append a line to approvals.csv. They never change the base workbook, model/*facts.json or the
published documents: an analyst applies an approved change by hand and re-runs the project's own checks.
"""
import argparse
import contextlib
import csv
import io
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODEL = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(MODEL))

import drift  # noqa: E402
import report  # noqa: E402
import scenario  # noqa: E402
from sources import SOURCES  # noqa: E402
from sources.common import FAILED, now_iso  # noqa: E402

RUNS = HERE / "runs"
APPROVALS = HERE / "approvals.csv"
STATUS_LINE = "Proposed for review. The base case and the published documents are unchanged until an analyst approves."

# key outputs shown in the report: (facts.json key, label, format)
KEY_OUTPUTS = [
    ("scenario.minimum_dscr_over_bond_life.a", "Lowest cover, plan", "x"),
    ("scenario.minimum_dscr_over_bond_life.downside", "Lowest cover, both hosts leave yr 5", "x"),
    ("scenario.scheduled_debt_service_not_paid_when_due_eur.downside", "Missed when due, both hosts leave", "eur"),
    ("debt.capacity_annuity", "Debt capacity at 1.30x (plan)", "eur"),
    ("scenario.year_1_revenue.a", "Year-1 revenue, plan", "eur"),
    ("scenario.hosts_power_savings_after_tax_year_1_eur.a", "Hosts' savings after tax, year 1", "eur"),
    ("scenario.owner_simple_payback_on_capex_project_cash_after_tax_savings_years.a", "Owner payback, years", "yr"),
    ("scenario.owner_irr_unlevered_project_cash_after_tax_savings_25_years.a", "Owner return after tax, unlevered", "pct"),
    ("scenario.sponsor_irr_incl_after_tax_power_savings_25_years.a", "Owner return after tax, with the bond", "pct"),
]


def log(msg):
    print(msg, flush=True)


def new_run_id(simulated):
    rid = datetime.now().strftime("%Y-%m-%d_%H%M") + ("_SIM" if simulated else "")
    i, base = 2, rid
    while (RUNS / rid).exists():
        rid, i = f"{base}-{i}", i + 1
    return rid


def parse_sim(items):
    out = {}
    for it in items or []:
        for part in it.split(","):
            k, v = part.split("=")
            out[k.strip()] = float(v)
    return out


def facts_by_key(folder=None):
    return {f["key"]: f["value"] for f in drift.load(folder) if "key" in f}


def base_sync_check(run_dir):
    """Re-export facts from the UNCHANGED base workbook and compare with the published model/*facts.json, so a
    difference in the drift check can only come from the market inputs."""
    tmp = run_dir / "_basecheck"
    try:
        scenario.export_facts(scenario.BASE_XLSX, tmp / "facts", log=lambda *_: None)
        diffs = []
        for f in ("facts.json", "proposal_facts.json", "feasibility_facts.json"):
            pub = {x["key"]: x["value"] for x in json.loads((MODEL / f).read_text(encoding="utf-8"))}
            new = {x["key"]: x["value"] for x in json.loads((tmp / "facts" / f).read_text(encoding="utf-8"))}
            for k in set(pub) | set(new):
                a, b = pub.get(k), new.get(k)
                if a is None or b is None or abs(a - b) > 1e-9 * max(1, abs(a)):
                    diffs.append(f"{f}:{k}")
        return {"in_sync": not diffs, "differences": len(diffs), "examples": diffs[:5]}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def reconcile(xlsx, changes):
    """The project's own reconciliation (mc_engine.reconcile) on the recalculated copy, plus a check that the engine
    read the changed inputs."""
    import mc_engine
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        worst = mc_engine.reconcile(xlsx)
    p = mc_engine.load_inputs(xlsx)
    seen = {k: p.get(k) for k in changes}
    inputs_ok = all(abs(seen[k] - v) < 1e-9 for k, v in changes.items())
    ok = worst < 0.5 and inputs_ok
    return {"result": "PASS" if ok else "FAIL", "worst_abs_diff_eur": worst, "engine_inputs": seen,
            "engine_read_changed_inputs": inputs_ok, "detail": buf.getvalue().strip().splitlines()}


def cmd_run(a):
    sim = parse_sim(a.simulate)
    simulated = bool(sim)
    rid = new_run_id(simulated)
    run_dir = RUNS / rid
    run_dir.mkdir(parents=True)
    log(f"run {rid}{' (SIMULATED)' if simulated else ''}")
    base_inputs = {k: v for k, (_, v) in scenario.input_rows(scenario.BASE_XLSX).items()}

    # 1. FETCH (skipped in a simulation: the inputs are set by hand)
    fetched = []
    if simulated:
        for k, v in sim.items():
            if k not in base_inputs:
                raise SystemExit(f"unknown input {k!r}; see the Inputs sheet / model/build_model.py")
            fetched.append({"input": k, "name": f"SIMULATED value for {k}", "value": v, "unit": "", "period": "simulation",
                            "source": "set by hand (--simulate)", "source_url": "", "fetched_at": now_iso(),
                            "status": "SIMULATED", "detail": "Not market data. Drift-detection test.", "applies": True})
    else:
        for src in SOURCES:
            log(f"fetch {src.__name__.split('.')[-1]}")
            fetched += src.fetch(model_value=base_inputs.get(src.INPUT), log=log)
    for r in fetched:
        log(f"  {r['input']}: {r['value']} {r['unit']} [{r['period']}] status: {r['status']}")
    changes = {r["input"]: r["value"] for r in fetched
               if r.get("applies") and r["status"] != FAILED and r["value"] is not None
               and abs(float(r["value"]) - float(base_inputs[r["input"]])) > 1e-9}

    # 2. SCENARIO on a copy, recalculated in Excel; facts exported with the project's own scripts
    xlsx = run_dir / "model-market.xlsx"
    log("scenario copy + Excel recalc")
    written = scenario.recalc_copy(xlsx, changes, log=log)
    log("export facts from the copy")
    facts_dir = scenario.export_facts(xlsx, run_dir / "facts", log=log)

    # 3. RECONCILE engine vs recalculated copy
    log("reconcile engine vs copy")
    rec = reconcile(xlsx, changes)
    log(f"  {rec['result']} (max abs difference EUR {rec['worst_abs_diff_eur']:.6f}; engine read changed inputs: {rec['engine_read_changed_inputs']})")

    # 4. DRIFT against the published documents (base facts trace, market facts compare)
    log("drift check")
    sync = base_sync_check(run_dir)
    log(f"  base facts in sync with base workbook: {sync['in_sync']} ({sync['differences']} differences)")
    base_facts, market_facts = drift.load(None), drift.load(facts_dir)
    d = drift.check(base_facts, market_facts, changes)
    for doc in d["documents"]:
        log(f"  {doc['file']}: {doc['numbers_traced']} numbers traced, {doc['unmatched']} unmatched, "
            f"{doc['stale_sentences']} stale sentences ({doc['stale_numbers']} numbers)")

    # 5. REPORT
    b, m = facts_by_key(None), facts_by_key(facts_dir)
    outputs = [{"key": k, "label": lab, "format": fmt, "base": b.get(k), "market": m.get(k)} for k, lab, fmt in KEY_OUTPUTS]
    rep = {"run_id": rid, "simulated": simulated, "created": now_iso(), "status": STATUS_LINE,
           "approval": "Not yet reviewed. Record a decision with monitor.py approve/reject; that only appends to approvals.csv.",
           "fetched": fetched, "inputs_changed": written, "key_outputs": outputs,
           "monte_carlo": "Not re-run; its base facts (mc_facts.json) are carried over unchanged.",
           "reconciliation": rec, "base_facts_sync": sync, "drift": d,
           "files": {"workbook": "model-market.xlsx", "facts": "facts/", "report": "report.html"}}
    (run_dir / "report.json").write_text(json.dumps(rep, indent=1, default=str), encoding="utf-8")
    (run_dir / "report.html").write_text(report.render(rep), encoding="utf-8")
    latest = {"run_id": rid, "path": f"runs/{rid}", "simulated": simulated, "created": rep["created"],
              "stale_sentences": len(d["stale"]), "reconciliation": rec["result"]}
    prev = json.loads((RUNS / "latest.json").read_text(encoding="utf-8")) if (RUNS / "latest.json").exists() else {}
    latest["latest_live_run"] = rid if not simulated else prev.get("latest_live_run")
    (RUNS / "latest.json").write_text(json.dumps(latest, indent=1), encoding="utf-8")
    log(f"report: {run_dir / 'report.html'}")
    log(f"stale sentences: {len(d['stale'])}; reconciliation: {rec['result']}")
    log(STATUS_LINE)
    return 0 if rec["result"] == "PASS" else 1


def cmd_decide(a, decision):
    run_dir = RUNS / a.run_id
    if not (run_dir / "report.json").exists():
        raise SystemExit(f"no such run: {a.run_id}")
    rep = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    new = not APPROVALS.exists()
    with APPROVALS.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["decided_at", "run_id", "decision", "by", "note", "simulated", "stale_sentences", "reconciliation"])
        w.writerow([now_iso(), a.run_id, decision, a.by, a.note, rep["simulated"], len(rep["drift"]["stale"]),
                    rep["reconciliation"]["result"]])
    log(f"{decision} recorded for {a.run_id} by {a.by} in {APPROVALS.name}. "
        "Nothing else changed: the base model and the published documents are untouched.")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--simulate", action="append", metavar="INPUT=VALUE", help="e.g. host_cost=145 or capture=30 (repeatable)")
    for name in ("approve", "reject"):
        p = sub.add_parser(name)
        p.add_argument("run_id")
        p.add_argument("--by", required=True)
        p.add_argument("--note", default="")
    a = ap.parse_args()
    if a.cmd == "run":
        return cmd_run(a)
    return cmd_decide(a, "approved" if a.cmd == "approve" else "rejected")


if __name__ == "__main__":
    sys.exit(main())
