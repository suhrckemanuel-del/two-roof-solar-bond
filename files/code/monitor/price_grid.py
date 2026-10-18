"""Precompute the demo page's price setting from real model reruns.

  python model/monitor/price_grid.py          run every missing combination, then stop
  python model/monitor/price_grid.py --list   print the manifest

Each combination is one ordinary simulated monitor run (monitor.py run --simulate capture=X,ref=Y,p2=Z): a copy of
the workbook recalculated in Excel, facts exported by the project's own scripts, engine reconciled. Runs land in
runs/ labelled SIMULATED and are never approved or rejected (they are not market data). The manifest
price-grid.json maps each combination (and each grid price with the premium not won) to its run id; demo/build_data.py reads the values from those runs.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

MON = Path(__file__).resolve().parent
MANIFEST = MON / "price-grid.json"

CAPTURE = [30, 35, 40, 45, 50, 55, 60, 61.17, 65, 70, 75, 80, 85, 90]  # grid price, EUR/MWh (61.17 = monitor 17 Oct)
REF = [92.5, 85, 87.5, 90, 95]                                          # tender bid, EUR/MWh (92.5 = plan, cap 95)
P2 = [0, 1]                                                             # premium counted on the 30% while hosts buy
# premium not won (Inputs!C23 p1 = 0): one run per grid price; the bid and the 30% switch do not apply without it
NOT_WON = [(c, 92.5, 0, 0) for c in CAPTURE]
OUT = {"plan": "scenario.minimum_dscr_over_bond_life.a",
       "down": "scenario.minimum_dscr_over_bond_life.downside",
       "down_missed": "scenario.scheduled_debt_service_not_paid_when_due_eur.downside"}


def key(c, r, p, p1=1):
    return f"capture={c:g},ref={r:g},p2={p}" + ("" if p1 else ",p1=0")


def load():
    return json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {"runs": {}}


def save(m):
    MANIFEST.write_text(json.dumps(m, indent=1), encoding="utf-8")


def read_run(rid):
    rep = json.loads((MON / "runs" / rid / "report.json").read_text(encoding="utf-8"))
    out = {o["key"]: o["market"] for o in rep["key_outputs"]}
    return {"run_id": rid, "simulated": rep["simulated"], "inputs_changed": rep["inputs_changed"],
            "reconciliation": rep["reconciliation"]["result"], **{name: out[k] for name, k in OUT.items()}}


def main():
    m = load()
    m["about"] = __doc__.strip().splitlines()[0]
    m["outputs"] = OUT
    if "--list" in sys.argv:
        for k, v in m["runs"].items():
            print(k, v)
        return 0
    if "--adopt" in sys.argv:  # record an existing run: --adopt KEY RUN_ID
        i = sys.argv.index("--adopt")
        m["runs"][sys.argv[i + 1]] = read_run(sys.argv[i + 2])
        save(m)
        return 0
    # order: the main slider first (bid 92.5, premium not counted), then the switch, then the other bids
    combos = [(c, r, p, 1) for r in REF for p in P2 for c in CAPTURE]
    combos.sort(key=lambda x: (x[1] != 92.5, REF.index(x[1]), x[2]))
    for c, r, p, p1 in combos + NOT_WON:
        k = key(c, r, p, p1)
        if k in m["runs"]:
            continue
        proc = subprocess.run([sys.executable, str(MON / "monitor.py"), "run", "--simulate", k],
                              capture_output=True, text=True)
        rid = re.search(r"^run (\S+)", proc.stdout, re.M)
        if proc.returncode or not rid:
            print(f"FAILED {k}: {proc.stderr[-400:]}", flush=True)
            continue
        m["runs"][k] = read_run(rid.group(1))
        save(m)
        v = m["runs"][k]
        print(f"{k}: plan {v['plan']:.4f} down {v['down']:.4f} missed {v['down_missed']:.0f} "
              f"{v['reconciliation']} ({v['run_id']})", flush=True)
    print(f"done: {len(m['runs'])} of {len(combos) + len(NOT_WON)} settings")
    return 0


if __name__ == "__main__":
    sys.exit(main())
