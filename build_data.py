"""Write demo/data.js (window.DATA) for the demo page from the model's own files. No hand-typed numbers:
every value is read from model/*facts.json, mc_results.json, the checker reports or the monitor runs, and
carries its source (cell, file or run). A .js file (not .json) so the page also works opened from disk.

usage: python demo/build_data.py   (from the project folder)
"""
import csv, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
M = ROOT / "model"
sys.path.insert(0, str(M))

facts = {}
for f in ["facts.json", "proposal_facts.json", "feasibility_facts.json", "mc_facts.json", "external_facts.json"]:
    for x in json.loads((M / f).read_text(encoding="utf-8")):
        facts[x["key"]] = {"v": x["value"], "src": x.get("cell") or x.get("source", ""), "label": x["label"], "file": f}


def F(key):
    x = facts[key]
    return {"v": x["v"], "src": f'{x["file"]}: {x["src"]}'}


# 1. headline numbers
case = {k: F(v) for k, v in {
    "bond": "bond.bond_amount_used", "token": "input.token", "tokens": "factsheet.number_of_tokens",
    "coupon": "input.coupon", "tenor": "input.tenor", "pay_y1": "factsheet.payment_per_token_year_1_interest_principal_eur",
    "total": "factsheet.total_paid_per_token_over_the_bond_life_if_all_paid_eur", "debt_service": "test2m.annual_debt_service.a",
    "kwp": "input.kwp", "host_share": "input.host_share", "ppa": "input.ppa", "ppa_tenor": "input.ppa_tenor",
    "host_cost": "input.host_cost", "capture": "input.capture", "lockup": "input.lockup",
    "reserve": "factsheet.debt_service_reserve_at_closing_eur", "reserve_months": "factsheet.debt_service_reserve_in_months_of_payments",
    "min_plan": "scenario.minimum_dscr_over_bond_life.a", "min_down": "prop.ds_min_dscr", "min_p90": "prop.ds_p90_min_dscr",
    "p90_late": "prop.ds_p90_first_late", "p90_owed": "prop.ds_p90_owed_y15", "p90_factor": "prop.p90_10y_factor",
    "tender_cap": "market.tender_cap_8th", "tender_award7": "market.tender_award_7th", "guarantee": "tender.guarantee_two_roofs",
    "issuance": "su.issuance_costs__placement_fee___legal___register_token_set_up", "running": "input.bond_admin",
    "bond_allin": "feas.cost.bond_allin", "bank_lo": "feas.cost.bank_allin_A", "bank_hi": "feas.cost.bank_allin_E",
    "cost_per_investor": "prop.cost_per_investor",
    "ref": "input.ref", "neg": "input.neg", "dm_fee": "input.dm_fee", "p2": "input.p2", "p2_plan": "feas.p2.min_dscr",
    "nw_plan": "feas.notwon.min_dscr", "nw_exit": "feas.notwon.leave2_dscr_exit", "nw_min": "feas.notwon.leave2_min_dscr",
    "nw_unpaid": "feas.notwon.leave2_unpaid",
}.items()}

# 1b. past tender rounds (dated sources in model/external_facts.json, from research/r4-lux-solar-inputs.md)
tender = {k: F(f"market.tender_{k}") for k in [
    "6th_awarded_mw", "6th_volume_mw", "6th_awarded_share", "7th_awarded_mw", "7th_volume_mw", "7th_awarded_share",
    "8th_rooftop_volume_mw"]}
for k in tender:
    tender[k]["date"] = next(x["date"] for x in json.loads((M / "external_facts.json").read_text(encoding="utf-8"))
                             if x["key"] == f"market.tender_{k}")

# 2. cover series, plan / both hosts leave / + P90 (same engine calls as model/charts.py)
from mc_engine import YEARS, host_path, load_inputs, run  # noqa: E402
import numpy as np  # noqa: E402

p = load_inputs()
tenor = int(p["tenor"])
yf90 = np.full((1, YEARS + 1), facts["prop.p90_10y_factor"]["v"])
series = {
    "plan": [facts[f"a.dscr.y{i}"]["v"] for i in range(1, 16)],
    "down": [facts[f"downside.dscr.y{i}"]["v"] for i in range(1, 16)],
    "p90": [float(v) for v in run(p, host=host_path(p, "Downside"), yf=yf90)["dscr"][0, 1:tenor + 1]],
}
assert abs(series["p90"][-1] - facts["prop.ds_p90_min_dscr"]["v"]) < 1e-9
cover = {"years": list(range(1, 16)), "series": series,
         "src": {"plan": "facts.json: 'Case A'!D45:R45", "down": "facts.json: 'Downside'!D45:R45",
                 "p90": "mc_engine.run (reconciled engine), Downside hosts, P90 10-year yield x0.933 every year, as model/charts.py"}}

# 3. where it breaks (Exhibit 9 rows)
def row(label, pre, note=""):
    r = {"label": label, "min": F(f"prop.{pre}_min_dscr"), "late": F(f"prop.{pre}_late")}
    if f"prop.{pre}_first_late" in facts:
        r["first_late"] = F(f"prop.{pre}_first_late")
    if f"prop.{pre}_owed_y15" in facts:
        r["owed_y15"] = F(f"prop.{pre}_owed_y15")
    if f"prop.{pre}_reserve" in facts:
        r["reserve"] = F(f"prop.{pre}_reserve")
    r["note"] = note
    return r


breaks = [
    {"label": "Plan", "min": F("scenario.minimum_dscr_over_bond_life.a"), "note": "central case"},
    {"label": "Hosts pay EUR 100/MWh", "min": F("prop.ppa100_plan_min_dscr"), "note": "price after an arm's-length check"},
    row("One host leaves in year 5", "premium_1host", "premium won"),
    row("Both hosts leave in year 5", "ds", "premium won; 15% of grid power lost to negative prices (A)"),
    row("Both leave, 20% lost to negative prices (base 15%)", "ds_neg20", "premium won"),
    row("Both leave + P90 sunshine", "ds_p90", "premium won"),
]

# 4. Monte Carlo
mc = json.loads((M / "mc_results.json").read_text(encoding="utf-8"))
keep = ["p_all_on_time", "p_clean", "p_reserve_no_miss", "p_missed", "p_reserve_used", "p_any_host_left_by_15", "mean_missed_eur_given_miss"]
montecarlo = {"n_paths": mc["n_paths"], "seed": mc["seed"], "config": mc["config"],
              "base": {k: mc["base"][k] for k in keep},
              "by_host_hazard": {h: {k: v[k] for k in keep} for h, v in mc["by_host_hazard"].items()},
              "src": "model/mc_results.json (mc_run.py, 20,000 paths, seed shown)"}

# 5. claim checker: planted factsheet + real documents
bad = json.loads((M / "demo-bad-factsheet-report.json").read_text(encoding="utf-8"))["documents"][0]
sentences = [s.strip() for s in (M / "tests" / "bad-factsheet.html").read_text(encoding="utf-8").split("<p>")[1:]]
sentences = [s.split("</p>")[0] for s in sentences]
planted = []
for s in sentences:
    planted.append({
        "sentence": s,
        "numbers": [{"number": n["number"], "status": n["status"], "why": n["why"], "source": n.get("source", "")}
                    for n in bad["numbers"] if n["sentence"] == s and n["status"] != "CONTEXT"],
        "words": [{"phrase": w["phrase"], "why": w["why"]} for w in bad["wording"] if w["sentence"] == s],
        "claude": [{"issue": f["issue"], "why": f["explanation"], "rewrite": f["suggested_rewrite"]}
                   for f in bad["promises"]["findings"] if f["quote"] == s],
    })


def summary(path):
    d = json.loads((M / path).read_text(encoding="utf-8"))["documents"][0]
    return {"file": d["file"], "numbers": len([n for n in d["numbers"] if n["status"] != "CONTEXT"]),
            "unmatched": len([n for n in d["numbers"] if n["status"] == "UNMATCHED"]),
            "flags": len([w for w in d["wording"] if w["status"] == "FLAG"]),
            "promises": len(d["promises"].get("findings", [])), "model": d["promises"].get("model", ""), "report": path}


checker = {"planted": planted, "planted_model": bad["promises"]["model"],
           "real": [summary("claim-report.json"), summary("proposal-claim-report.json")]}

# 6. monitor: the decided run, the fresh run, the log
def run_report(rid):
    r = json.loads((M / "monitor" / "runs" / rid / "report.json").read_text(encoding="utf-8"))
    return {"run_id": rid, "created": r["created"],
            "fetched": [{k: f.get(k) for k in ("name", "value", "unit", "period", "source", "source_url", "fetched_at", "status")}
                        for f in r["fetched"] if f.get("applies")],
            "inputs_changed": r["inputs_changed"], "key_outputs": r["key_outputs"],
            "reconciliation": r["reconciliation"]["result"], "stale": r["drift"]["stale"],
            "stale_count": sum(d["stale_sentences"] for d in r["drift"]["documents"])}


with open(M / "monitor" / "approvals.csv", encoding="utf-8") as fh:
    log = list(csv.DictReader(fh))
monitor = {"decided": run_report("2026-10-14_1852"), "latest": run_report("2026-10-17_1055"), "log": log}

# 7. price setting: one simulated monitor run per combination (model/monitor/price_grid.py); values read from each
# run's own report, never computed here or in the browser
grid = json.loads((M / "monitor" / "price-grid.json").read_text(encoding="utf-8"))
OUT = grid["outputs"]
runs = {}
for k, g in grid["runs"].items():
    r = json.loads((M / "monitor" / "runs" / g["run_id"] / "report.json").read_text(encoding="utf-8"))
    assert r["simulated"], f"{g['run_id']} is not a simulated run"
    out = {o["key"]: o["market"] for o in r["key_outputs"]}
    runs[k] = {"run": g["run_id"], "rec": r["reconciliation"]["result"],
               "cells": {n: c["cell"] for n, c in r["inputs_changed"].items()},
               **{name: out[key] for name, key in OUT.items()}}
# the cover chart's "premium not won" line: the Excel rerun of the downside with Inputs!C23 = 0, checked against the
# engine facts that the prose quotes
nw_run = runs["capture=45,ref=92.5,p2=0,p1=0"]["run"]
nw_facts = {x["key"]: x["value"] for x in json.loads(
    (M / "monitor" / "runs" / nw_run / "facts" / "facts.json").read_text(encoding="utf-8"))}
cover["series"]["notwon"] = [nw_facts[f"downside.dscr.y{i}"] for i in range(1, 16)]
cover["src"]["notwon"] = f"model/monitor/runs/{nw_run}/facts/facts.json: 'Downside'!D45:R45 with Inputs!C23 = 0 (SIMULATED rerun)"
assert abs(min(cover["series"]["notwon"]) - case["nw_min"]["v"]) < 1e-9
assert abs(cover["series"]["notwon"][int(p["ds_exit"]) - 1] - case["nw_exit"]["v"]) < 1e-9
base = runs["capture=45,ref=92.5,p2=0"]
assert abs(base["plan"] - case["min_plan"]["v"]) < 1e-9 and abs(base["down"] - case["min_down"]["v"]) < 1e-9
assert abs(runs["capture=45,ref=92.5,p2=1"]["plan"] - case["p2_plan"]["v"]) < 1e-9
assert abs(runs["capture=61.17,ref=92.5,p2=0"]["plan"] - monitor["latest"]["key_outputs"][0]["market"]) < 1e-9
# premium not won: the Excel rerun and the engine facts the prose quotes must agree
nw = runs["capture=45,ref=92.5,p2=0,p1=0"]
assert abs(nw["plan"] - case["nw_plan"]["v"]) < 1e-9 and abs(nw["down"] - case["nw_min"]["v"]) < 1e-9
assert abs(nw["down_missed"] - case["nw_unpaid"]["v"]) < 0.01
prices = {"capture": sorted({float(k.split(",")[0].split("=")[1]) for k in runs}),
          "ref": sorted({float(k.split(",")[1].split("=")[1]) for k in runs if "p1=0" not in k}),
          "runs": runs, "n": len(runs), "outputs": OUT,
          "src": "model/monitor/price-grid.json -> model/monitor/runs/<run>/report.json (monitor.py run --simulate)"}

DATA = {"built_from": "demo/build_data.py", "case": case, "tender": tender, "cover": cover, "breaks": breaks,
        "montecarlo": montecarlo, "checker": checker, "monitor": monitor, "prices": prices}
out = ROOT / "demo" / "data.js"
out.write_text("window.DATA = " + json.dumps(DATA, ensure_ascii=False, indent=1) + ";\n", encoding="utf-8")
print(f"wrote {out} ({out.stat().st_size // 1024} KB): {len(planted)} planted sentences, "
      f"{monitor['latest']['stale_count']} stale sentences in the latest run, {len(log)} log rows")
