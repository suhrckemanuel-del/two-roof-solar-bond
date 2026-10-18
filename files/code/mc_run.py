"""Monte Carlo on the reconciled engine (mc_engine.py). All random inputs are ASSUMPTIONS unless noted.

Run:  python mc_run.py   -> mc_results.json (headline + by-year curves for the chart)
"""
import json
from pathlib import Path

import numpy as np

from mc_engine import YEARS, load_inputs, run

HERE = Path(__file__).parent
N = 20_000
SEED = 20261012

BASE = dict(
    weather_sd=None,        # SOURCED: PVGIS interannual SD / P50 (filled from Inputs)
    p50_error_sd=0.04,      # ASSUMPTION: long-run resource and model uncertainty, persistent for the whole life
    hazard_common=0.01,     # ASSUMPTION: yearly chance the shared holding fails, both hosts stop buying
    hazard_each=0.01,       # ASSUMPTION: yearly chance one host stops buying on its own (move, closure)
    capture_vol=0.15,       # ASSUMPTION: yearly volatility of the merchant capture price (log random walk, no drift)
)


def simulate(p, cfg, n=N, seed=SEED):
    rng = np.random.default_rng(seed)
    T = YEARS + 1
    t = np.arange(T)
    yf = (1 + rng.normal(0, cfg["p50_error_sd"], (n, 1))) * (1 + rng.normal(0, cfg["weather_sd"], (n, T)))
    # host exits: permanent, common shock hits both roofs
    common = rng.random((n, T)) < cfg["hazard_common"]
    each = rng.random((n, int(p["n_roofs"]), T)) < cfg["hazard_each"]
    gone = np.logical_or(each, common[:, None, :])
    gone[:, :, 0] = False
    gone = np.logical_or.accumulate(gone, axis=2)
    host = (1 - gone.mean(axis=1)) * (t <= p["ppa_tenor"])
    shocks = rng.normal(-0.5 * cfg["capture_vol"] ** 2, cfg["capture_vol"], (n, T))
    shocks[:, :2] = 0  # year 1 starts at the sourced 2025 value
    cap = np.exp(np.cumsum(shocks, axis=1))
    out = run(p, host=host, yf=yf, cap_mult=cap)
    tenor = int(p["tenor"])
    yrs = slice(1, tenor + 1)
    missed = out["unpaid"][:, yrs] > 0.5
    drawn = out["draw"][:, yrs] > 0.5
    locked = out["dscr"][:, yrs] < p["lockup"]
    any_missed = missed.any(axis=1)
    on_time_by_year = 1 - np.logical_or.accumulate(missed, axis=1).mean(axis=0)
    hosts_left_by_15 = gone[:, :, tenor].any(axis=1)
    lost = out["unpaid"][:, yrs].sum(axis=1)
    return dict(
        p_all_on_time=float(1 - any_missed.mean()),
        p_clean=float((~drawn.any(axis=1) & ~any_missed).mean()),
        p_reserve_no_miss=float((drawn.any(axis=1) & ~any_missed).mean()),
        p_missed=float(any_missed.mean()),
        p_reserve_used=float(drawn.any(axis=1).mean()),
        p_lockup_ever=float(locked.any(axis=1).mean()),
        p_any_host_left_by_15=float(hosts_left_by_15.mean()),
        mean_missed_eur_given_miss=float(lost[any_missed].mean()) if any_missed.any() else 0.0,
        p10_min_dscr=float(np.percentile(np.nanmin(out["dscr"][:, yrs], axis=1), 10)),
        on_time_by_year=[round(float(x), 4) for x in on_time_by_year],
    )


def main():
    p = load_inputs()
    base = dict(BASE, weather_sd=p["yield_sd"] / p["yield"])
    res = {"n_paths": N, "seed": SEED, "config": base, "base": simulate(p, base)}
    # the host hazard is the least known input, so show the answer across a range of it (same 50/50 split)
    res["by_host_hazard"] = {}
    for h in (0.0, 0.01, 0.02, 0.04):
        cfg = dict(base, hazard_common=h / 2, hazard_each=h / 2)
        res["by_host_hazard"][f"{h:.0%}"] = simulate(p, cfg)
    res["no_p50_error"] = simulate(p, dict(base, p50_error_sd=0.0))
    res["weather_only"] = simulate(p, dict(base, p50_error_sd=0.0, hazard_common=0, hazard_each=0, capture_vol=0))
    (HERE / "mc_results.json").write_text(json.dumps(res, indent=1))
    facts = []
    def add(key, value, label):
        facts.append({"key": f"mc.{key}", "value": value, "cell": f"mc_results.json:{key}", "label": label, "kind": "MONTE CARLO"})
    add("n_paths", N, "Monte Carlo paths simulated")
    for name, r in [("base", res["base"])] + [(f"hazard_{k}", v) for k, v in res["by_host_hazard"].items()]:
        add(f"{name}.p_all_on_time", r["p_all_on_time"], f"Share of paths with all bond payments on time [{name}]")
        add(f"{name}.p_missed", r["p_missed"], f"Share of paths with at least one missed payment [{name}]")
        add(f"{name}.p_reserve_used", r["p_reserve_used"], f"Share of paths where the reserve is drawn [{name}]")
        add(f"{name}.p_reserve_no_miss", r["p_reserve_no_miss"], f"Share of paths: reserve drawn, all paid [{name}]")
    b0 = res["base"]
    add("base.p_lockup_ever", b0["p_lockup_ever"], "Share of paths where owners are locked out at least once [base]")
    add("base.p_any_host_left_by_15", b0["p_any_host_left_by_15"], "Share of paths where a host has stopped buying by year 15 [base]")
    add("base.mean_missed_eur_given_miss", b0["mean_missed_eur_given_miss"], "Average EUR missed in paths with a miss [base]")
    add("cfg.weather_sd", base["weather_sd"], "Yearly yield SD as share of P50 (SOURCED, PVGIS)")
    add("cfg.p50_error_sd", base["p50_error_sd"], "Persistent P50 error SD (ASSUMPTION)")
    add("cfg.host_hazard_total", base["hazard_common"] + base["hazard_each"], "Yearly chance each host stops buying (ASSUMPTION)")
    add("cfg.hazard_common", base["hazard_common"], "Yearly chance the shared holding fails (ASSUMPTION)")
    add("cfg.capture_vol", base["capture_vol"], "Yearly capture price volatility (ASSUMPTION)")
    (HERE / "mc_facts.json").write_text(json.dumps(facts, indent=1))
    b = res["base"]
    print(f"BASE ({N} paths): all 15 payments on time {b['p_all_on_time']:.1%}; reserve used {b['p_reserve_used']:.1%}; "
          f"lock-up at least once {b['p_lockup_ever']:.1%}; a host gone by yr 15 {b['p_any_host_left_by_15']:.1%}; "
          f"avg missed given a miss EUR {b['mean_missed_eur_given_miss']:,.0f}; P10 min DSCR {b['p10_min_dscr']:.2f}")
    for k, v in res["by_host_hazard"].items():
        print(f"  host exit {k}/yr per host: on time {v['p_all_on_time']:.1%}, reserve used {v['p_reserve_used']:.1%}, "
              f"a host gone {v['p_any_host_left_by_15']:.1%}")
    print(f"  no P50 error: on time {res['no_p50_error']['p_all_on_time']:.1%}")
    print(f"  weather only: on time {res['weather_only']['p_all_on_time']:.1%}")


if __name__ == "__main__":
    main()
