"""Python mirror of solar_bond_model.xlsx, vectorised for Monte Carlo.

Reads every input from the workbook's Inputs sheet (cached values, so recalc in Excel first),
replicates the scenario-sheet formulas row by row, and reconciles to Excel to the euro.

Run:  python mc_engine.py      -> reconciliation report for Case A, Case B, Downside
"""
from pathlib import Path

import numpy as np
from openpyxl import load_workbook

HERE = Path(__file__).parent
XLSX = HERE / "solar_bond_model.xlsx"
YEARS = 25


def load_inputs(path=XLSX):
    ws = load_workbook(path, data_only=True)["Inputs"]
    p = {}
    for r in range(5, ws.max_row + 1):
        k, v = ws.cell(row=r, column=1).value, ws.cell(row=r, column=3).value
        if k and isinstance(v, (int, float)):
            p[k] = float(v)
    p["kwp_tot"] = p["kwp"] * p["n_roofs"]
    return p


def debt_schedule(p):
    """Annuity bond of the fixed amount (size_mode 2, profile 1: the frozen G2 case)."""
    assert p["size_mode"] == 2 and p["profile"] == 1, "engine mirrors the fixed-amount annuity case only"
    b, c, n = p["fixed_bond"], p["coupon"], int(p["tenor"])
    pay = b * c / (1 - (1 + c) ** -n)
    ds, intr = np.zeros(YEARS + 2), np.zeros(YEARS + 2)   # index = year; ds[YEARS+1] = 0 for the DSRA target
    opn = np.zeros(YEARS + 2)                              # opening balance (Debt sheet row 'Opening balance')
    bal = b
    for t in range(1, n + 1):
        opn[t] = bal
        intr[t] = bal * c
        prin = max(0.0, min(bal, pay - intr[t]))
        ds[t] = intr[t] + prin
        bal -= prin
    opn[n + 1:] = bal
    return ds, intr, opn


def run(p, host=None, yf=None, cap_mult=None):
    """Run the cash waterfall for N paths at once.

    host[N, t]     share of rooftops with a host buying on-site (0..1), t = 1..YEARS (column 0 unused)
    yf[N, t]       yield factor on P50 for that year
    cap_mult[N, t] multiplier on the merchant capture price
    Returns a dict of [N, YEARS+1] arrays named like the Excel rows.
    """
    t = np.arange(YEARS + 1, dtype=float)
    op = ((t >= 1) & (t <= p["life"])).astype(float)
    if host is None:
        host = np.where(t <= p["ppa_tenor"], 1.0, 0.0)[None, :]
    N = max(np.shape(host)[0], 1 if yf is None else np.shape(yf)[0])
    host = np.broadcast_to(host, (N, YEARS + 1)) * op
    yf = np.ones((N, YEARS + 1)) if yf is None else np.broadcast_to(yf, (N, YEARS + 1))
    cap_mult = np.ones((N, YEARS + 1)) if cap_mult is None else np.broadcast_to(cap_mult, (N, YEARS + 1))
    tm1 = np.maximum(t - 1, 0)

    gen = op * p["kwp_tot"] * p["yield"] * yf * p["avail"] * (1 - p["deg"]) ** tm1 / 1000
    hvol = gen * host * p["host_share"]
    xh = gen * host * (1 - p["host_share"])
    xf = gen * (1 - host)
    prem = op * p["p1"] * (1 - p["grant_on"]) * (t <= p["prem_tenor"])
    pvol = prem * (xf + xh * p["p2"])
    mvol = xf + xh - pvol
    ppa_p = p["ppa"] * (1 + p["ppa_idx"]) ** tm1
    cap_p = p["capture"] * (1 + p["capture_esc"]) ** tm1 * cap_mult
    rev = hvol * ppa_p + pvol * (1 - p["neg"]) * (p["ref"] - p["dm_fee"]) + mvol * (cap_p - p["dm_fee"])
    esc = (1 + p["esc"]) ** tm1 * op
    opex = (p["kwp_tot"] * (p["om"] + p["ins"] + p["roof"] + p["inv"]) + p["admin"] + p["grid"] * p["n_roofs"]
            + p["bond_admin"] * (t <= p["tenor"])) * esc
    ebitda = rev - opex
    dep = p["kwp_tot"] * p["capex"] / p["depr"] * (t <= p["depr"]) * op
    ds_full, intr, opn = debt_schedule(p)
    ds = ds_full[: YEARS + 1]
    ltax = np.maximum(0, (ebitda - dep - intr[: YEARS + 1]) * p["tax"])
    # net wealth tax: 0.5% of unitary value on 1 January, never below the minimum set by the balance-sheet total
    nwt_min = lambda a: np.where(a > p["nwt_b2"], p["nwt_min_hi"], np.where(a > p["nwt_b1"], p["nwt_min_mid"], p["nwt_min_lo"]))
    nbv = np.zeros(YEARS + 1)
    nbv[1] = p["kwp_tot"] * p["capex"]
    for y in range(2, YEARS + 1):
        nbv[y] = max(0.0, nbv[y - 1] - dep[y - 1])
    nwt_u = op * np.maximum(nwt_min(nbv), p["nwt"] * nbv)
    # owner economics: sizing CFADS (unlevered tax and NWT), hosts' savings before and after tax, integrated owner cash
    scfads = ebitda - np.maximum(0, (ebitda - dep) * p["tax"]) - nwt_u
    sav = hvol * (p["host_cost"] * (1 + p["host_cost_esc"]) ** tm1 - ppa_p)
    sav_at = sav * (1 - p["tax"])
    proj = scfads.copy()
    proj[:, 0] = -p["kwp_tot"] * p["capex"]
    integ = proj + sav_at

    z = lambda: np.zeros((N, YEARS + 1))
    cfads, nwt_l, paid, draw, unpaid, dsra_c, cash_c, dist, pre, arr_c, arr_i = z(), z(), z(), z(), z(), z(), z(), z(), z(), z(), z()
    cash_o, arr_o = np.zeros(N), np.zeros(N)
    dsra_o = np.full(N, p["dsra_m"] / 12 * ds[1])
    for y in range(1, YEARS + 1):
        assets = nbv[y] + dsra_o + cash_o
        uv = np.maximum(0, assets - opn[y] - arr_o)
        nwt_l[:, y] = op[y] * np.maximum(nwt_min(assets), p["nwt"] * uv)
        cfads[:, y] = ebitda[:, y] - ltax[:, y] - nwt_l[:, y]
        cf, d = cfads[:, y], ds[y]
        arr_i[:, y] = arr_o * p["coupon"]
        due = d + arr_o + arr_i[:, y]
        paid[:, y] = np.maximum(0, np.minimum(due, cf + cash_o))
        draw[:, y] = np.maximum(0, np.minimum(due - paid[:, y], dsra_o))
        arr_c[:, y] = np.maximum(0, due - paid[:, y] - draw[:, y])
        unpaid[:, y] = np.minimum(d, arr_c[:, y])
        target = p["dsra_m"] / 12 * ds_full[y + 1]
        left = dsra_o - draw[:, y]
        topup = np.maximum(0, np.minimum(cf + cash_o - paid[:, y], np.maximum(0, target - left)))
        release = np.maximum(0, left - target)
        dsra_c[:, y] = left + topup - release
        pre[:, y] = cf + cash_o - paid[:, y] - topup + release
        cover = cf / d if d > 0 else np.full(N, 99.0)
        dist[:, y] = np.where((arr_c[:, y] < 0.5) & (cover >= p["lockup"]), pre[:, y], 0)
        cash_c[:, y] = pre[:, y] - dist[:, y]
        cash_o, dsra_o, arr_o = cash_c[:, y], dsra_c[:, y], arr_c[:, y]
    with np.errstate(divide="ignore", invalid="ignore"):
        dscr = np.where(ds > 1, cfads / ds, np.nan)
    out = dict(gen=gen, rev=rev, opex=opex, ebitda=ebitda, cfads=cfads, ds=ds, dscr=dscr, paid=paid, draw=draw,
               unpaid=unpaid, dsra_c=dsra_c, pre=pre, dist=dist, cash_c=cash_c, arr_c=arr_c, arr_i=arr_i,
               nwt_l=nwt_l, nwt_u=nwt_u,
               scfads=scfads, sav=sav, sav_at=sav_at, proj=proj, integ=integ)
    return {k: np.broadcast_to(v, (N, YEARS + 1)) for k, v in out.items()}


def arrears_summary(o, path=0, tenor=15):
    """How far behind bondholders fall on one path. A missed amount stays owed (arrears, with default interest at the
    coupon) and is paid first from later cash, so summing yearly 'unpaid' would double count: use these instead."""
    a, u = o["arr_c"][path], o["unpaid"][path]
    late = [y for y in range(1, YEARS + 1) if u[y] > 0.5]
    first = late[0] if late else 0
    cleared = next((y for y in range(first, YEARS + 1) if a[y] < 0.5), 0) if late else 0
    return dict(late=bool(late), first_late=first, owed_y15=float(a[tenor]), owed_max=float(a.max()),
                owed_end=float(a[YEARS]), cleared_year=cleared, reserve_drawn=float(o["draw"][path, 1:tenor + 1].sum()))


def equity_flows(p, o, path=0):
    """Sponsor equity cash flow t = 0..YEARS as on the scenario sheets (row 'Equity cash flow')."""
    ds1 = debt_schedule(p)[0][1]
    issue = p["fixed_bond"] * p["fee"] + p["legal"] + p["vehicle"]
    equity = p["kwp_tot"] * p["capex"] + issue + p["dsra_m"] / 12 * ds1 - p["fixed_bond"] - p["grant_on"] * p["kwp_tot"] * p["grant"]
    eq = o["dist"][path].copy()
    life = int(p["life"])
    eq[life] += o["cash_c"][path, life] + o["dsra_c"][path, life]
    eq[0] = -equity
    return eq


def irr(cf, guess=0.05):
    """IRR as the NPV root nearest the guess. Late-life cash flows can be slightly negative, so NPV may have two
    roots; Excel's IRR(range, guess) behaves the same way. Returns nan if there is no root in -30%..60%."""
    cf = np.asarray(cf, dtype=float)
    npv = lambda r: float(np.sum(cf / (1 + r) ** np.arange(len(cf))))
    grid = np.arange(-0.3, 0.6, 0.0025)
    v = [npv(r) for r in grid]
    roots = [i for i in range(len(grid) - 1) if v[i] * v[i + 1] <= 0]
    if not roots:
        return float("nan")
    i = min(roots, key=lambda k: abs(grid[k] - guess))
    lo, hi = grid[i], grid[i + 1]
    for _ in range(100):
        m = (lo + hi) / 2
        if npv(lo) * npv(m) <= 0:
            hi = m
        else:
            lo = m
    return (lo + hi) / 2


def payback(cf):
    """Simple payback in years: first year cumulative cash turns >= 0, interpolated within that year."""
    cf = np.asarray(cf, dtype=float)
    c = np.cumsum(cf)
    for i in range(1, len(c)):
        if c[i] >= 0 > c[i - 1]:
            return i - 1 + (-c[i - 1]) / cf[i]
    return float("nan")


def host_path(p, case):
    t = np.arange(YEARS + 1)
    in_ppa = (t <= p["ppa_tenor"]).astype(float)
    if case == "Case A":
        return in_ppa[None, :]
    if case == "Case B":
        return np.zeros((1, YEARS + 1))
    share = np.where(t < p["ds_exit"], 1.0, 1 - p["ds_hosts"] / p["n_roofs"])
    return (in_ppa * share)[None, :]


def reconcile(path=XLSX):
    p = load_inputs(path)
    wb = load_workbook(path, data_only=True)
    rows = {"Generation": "gen", "Total revenue": "rev", "Total operating costs": "opex", "EBITDA": "ebitda",
            "CFADS": "cfads", "Debt service due (from Debt)": "ds", "DSRA drawn": "draw",
            "Debt service NOT paid on time": "unpaid", "DSRA, closing": "dsra_c",
            "Cash available for distribution": "pre", "Retained cash, closing": "cash_c",
            "Sizing CFADS": "scfads", "Hosts' power savings vs grid": "sav", "Hosts' power savings after tax": "sav_at",
            "Owner cash flow, unlevered: project": "integ", "Net wealth tax (0.5%": "nwt_l",
            "Net wealth tax, unlevered": "nwt_u", "Arrears owed to bondholders, closing": "arr_c",
            "Default interest on arrears": "arr_i"}
    worst = 0.0
    for case in ("Case A", "Case B", "Downside"):
        ws = wb[case]
        yf = p["ds_yield"] if case == "Downside" else 1.0
        cm = 1 + p["ds_shock"] if case == "Downside" else 1.0
        out = run(p, host=host_path(p, case), yf=np.full((1, YEARS + 1), yf), cap_mult=np.full((1, YEARS + 1), cm))
        labels = {ws.cell(row=r, column=1).value: r for r in range(5, ws.max_row + 1)}
        case_worst = 0.0
        for lab, key in rows.items():
            r = [rr for l, rr in labels.items() if l and l.startswith(lab)][0]
            xl = np.array([ws.cell(row=r, column=3 + y).value or 0 for y in range(1, YEARS + 1)], dtype=float)
            diff = np.abs(out[key][0, 1:] - xl).max()
            case_worst = max(case_worst, diff)
        print(f"{case}: max abs difference across {len(rows)} rows x {YEARS} years = EUR {case_worst:.6f}")
        worst = max(worst, case_worst)
    # Summary returns (Case A): Excel IRR(range, guess) and payback vs the engine helpers, in basis points / years
    sm = wb["Summary"]
    lab = {sm.cell(row=r, column=1).value: r for r in range(1, sm.max_row + 1) if sm.cell(row=r, column=1).value}
    o = run(p, host=host_path(p, "Case A"))
    eq = equity_flows(p, o)
    checks = {"Project IRR, unlevered (25 years)": irr(o["proj"][0]),
              "Equity IRR (25 years)": irr(eq, -0.05),
              "Sponsor IRR incl. after-tax power savings (25 years)": irr(eq + o["sav_at"][0]),
              "Owner IRR unlevered, project cash + after-tax savings (25 years)": irr(o["integ"][0]),
              "Owner simple payback on capex, project cash + after-tax savings (years)": payback(o["integ"][0])}
    ret_worst = 0.0
    for k, v in checks.items():
        xl = sm.cell(row=lab[k], column=3).value
        ret_worst = max(ret_worst, abs(xl - v))
    print(f"Summary returns (Case A): max abs difference across {len(checks)} IRR/payback cells = {ret_worst:.2e}")
    return worst if ret_worst < 1e-6 else 99.0


if __name__ == "__main__":
    import sys
    w = reconcile(Path(sys.argv[1]) if len(sys.argv) > 1 else XLSX)
    print("RECONCILED" if w < 0.5 else "MISMATCH")
