"""Eurostat nrg_pc_205: Luxembourg non-household electricity price, band ID (2,000-19,999 MWh), excluding VAT.

Model input `host_cost` (EUR/MWh): what the two hosts would otherwise pay for grid power. Eurostat publishes EUR/kWh
per semester; the latest semester with a value is taken and multiplied by 1,000 (no other rounding)."""
import re

from .common import OK, fallback, get_json, record, save_cache

INPUT = "host_cost"
NAME = "Hosts' grid electricity cost (Eurostat nrg_pc_205, LU, band ID, excl. VAT)"
URL = ("https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/nrg_pc_205"
       "?geo=LU&nrg_cons=MWH2000-19999&tax=X_VAT&currency=EUR&unit=KWH&format=JSON&lang=EN")
EXPECT = {"geo": "LU", "nrg_cons": "MWH2000-19999", "tax": "X_VAT", "currency": "EUR", "unit": "KWH"}


def latest(d):
    """Decode JSON-stat 2.0: flat index = sum(position_k * stride_k); return (period label, EUR/kWh) of the newest
    time period that has a value, after checking every non-time dimension is the single category we asked for."""
    ids, sizes = d["id"], d["size"]
    for dim, want in EXPECT.items():
        cats = d["dimension"][dim]["category"]["index"]
        if list(cats) != [want]:
            raise ValueError(f"dimension {dim} is {list(cats)}, expected [{want}]")
    strides = [1] * len(ids)
    for k in range(len(ids) - 2, -1, -1):
        strides[k] = strides[k + 1] * sizes[k + 1]
    t = ids.index("time")
    times = sorted(d["dimension"]["time"]["category"]["index"].items(), key=lambda kv: kv[1])
    vals = d["value"]
    for label, pos in reversed(times):
        v = vals.get(str(pos * strides[t])) if isinstance(vals, dict) else vals[pos * strides[t]]
        if v is not None:
            if not re.fullmatch(r"\d{4}-S[12]", label):
                raise ValueError(f"unexpected period label {label!r}")
            return label, float(v)
    raise ValueError("no period with a value")


def fetch(model_value=None, log=print):
    try:
        d = get_json(URL, log=log)
        period, kwh = latest(d)
        rec = record(INPUT, NAME, round(kwh * 1000, 6), "EUR/MWh", period, f"Eurostat nrg_pc_205 (dataset updated {d.get('updated', '?')})",
                     URL, OK, f"{kwh} EUR/kWh x 1,000. Latest semester with a value: {period}.",
                     extra={"short": "Hosts' grid power cost: Eurostat LU band ID, excl. VAT", "source_value": kwh, "source_unit": "EUR/kWh", "dataset_updated": d.get("updated")})
        save_cache(rec)
        return [rec]
    except Exception as e:  # noqa: BLE001 - any failure falls back, and the reason is recorded
        return [fallback(INPUT, NAME, "EUR/MWh", model_value, URL, f"Eurostat fetch failed ({e})")]
