"""DE-LU day-ahead wholesale price, trailing 12 months (Luxembourg is in the DE-LU bidding zone).

Model input `capture` (EUR/MWh) is a SOLAR CAPTURE price: what exported solar power earns on the market, i.e. the
day-ahead price weighted by solar generation hour by hour (sum price x solar / sum solar). That is the same
definition as the German "Marktwert Solar" the base value (45.08 for 2025) comes from. The plain time-average
(baseload) price is fetched too, for context only; it is NOT written into the model.

Sources, in order:
  1. Fraunhofer ISE energy-charts API (price?bzn=DE-LU, public_power?country=de for solar), with retry + backoff.
  2. If energy-charts is down: SMARD, the Bundesnetzagentur's public data API (filter 4169 = DE-LU day-ahead price,
     4068 = solar generation, region DE-LU), same calculation.
  3. If both fail: the last good cached value, else the model's current input (no change), flagged in `status`.
Both series are averaged to whole hours (UTC) before weighting, so 15-minute and hourly data combine the same way.
"""
from datetime import datetime, timedelta, timezone

from .common import OK, fallback, get_json, record, save_cache

INPUT = "capture"
NAME = "DE-LU solar capture price, trailing 12 months (day-ahead weighted by solar output)"
EC = "https://api.energy-charts.info"
SMARD = "https://www.smard.de/app/chart_data"
MIN_COVER = 0.97   # share of hours that must have both price and solar data, else the result is not used


def window(today=None):
    end = (today or datetime.now(timezone.utc)).replace(hour=0, minute=0, second=0, microsecond=0)
    start = end.replace(year=end.year - 1)
    return start, end


def hourly(ts_seconds, values):
    b = {}
    for t, v in zip(ts_seconds, values):
        if v is not None:
            b.setdefault(int(t) // 3600, []).append(float(v))
    return {h: sum(x) / len(x) for h, x in b.items()}


def from_energy_charts(start, end, log):
    price, solar = {}, {}
    m = start
    while m < end:
        n = min(end, (m.replace(day=1) + timedelta(days=32)).replace(day=1))
        q = {"start": m.strftime("%Y-%m-%dT%H:%MZ"), "end": n.strftime("%Y-%m-%dT%H:%MZ")}
        d = get_json(f"{EC}/price", {"bzn": "DE-LU", **q}, log=log)
        price.update(hourly(d["unix_seconds"], d["price"]))
        g = get_json(f"{EC}/public_power", {"country": "de", **q}, log=log)
        s = next(pt for pt in g["production_types"] if pt["name"].lower() == "solar")
        solar.update(hourly(g["unix_seconds"], s["data"]))
        m = n
    url = f"{EC}/price?bzn=DE-LU&start={start:%Y-%m-%d}&end={end:%Y-%m-%d}"
    return price, solar, "Fraunhofer ISE energy-charts API (day-ahead DE-LU; public net solar generation DE)", url


def from_smard(start, end, log):
    def series(f, region):
        idx = get_json(f"{SMARD}/{f}/{region}/index_hour.json", log=log)["timestamps"]
        lo, hi = (start - timedelta(days=8)).timestamp() * 1000, end.timestamp() * 1000
        out = {}
        for ts in (t for t in idx if lo <= t < hi):
            d = get_json(f"{SMARD}/{f}/{region}/{f}_{region}_hour_{ts}.json", log=log)
            pts = [(a // 1000, b) for a, b in d["series"]]
            out.update(hourly([a for a, _ in pts], [b for _, b in pts]))
        return out
    url = f"{SMARD}/4169/DE-LU/index_hour.json (price) + {SMARD}/4068/DE-LU/index_hour.json (solar)"
    return series(4169, "DE-LU"), series(4068, "DE-LU"), "SMARD, Bundesnetzagentur (day-ahead DE-LU, filter 4169; solar generation DE-LU, filter 4068)", url


def fetch(model_value=None, log=print, today=None):
    start, end = window(today)
    h0, h1 = int(start.timestamp()) // 3600, int(end.timestamp()) // 3600
    period = f"{start:%Y-%m-%d} to {(end - timedelta(days=1)):%Y-%m-%d}"
    errors = []
    for name, fn in (("energy-charts", from_energy_charts), ("SMARD", from_smard)):
        try:
            log(f"  wholesale: trying {name}")
            price, solar, src, url = fn(start, end, log)
            hours = range(h0, h1)
            ph = [h for h in hours if h in price]
            both = [h for h in ph if h in solar]
            cover = len(both) / len(hours)
            if cover < MIN_COVER:
                raise RuntimeError(f"only {cover:.1%} of hours have price and solar data")
            w = sum(max(solar[h], 0) for h in both)
            capture = sum(price[h] * max(solar[h], 0) for h in both) / w
            base = sum(price[h] for h in ph) / len(ph)
            neg = sum(1 for h in ph if price[h] < 0)
            note = ("" if not errors else f"Primary source failed ({errors[0]}, 3 tries); used the secondary public source. ")
            stats = {"hours_expected": len(hours), "hours_with_price": len(ph), "hours_with_price_and_solar": len(both),
                     "negative_price_hours": neg, "solar_twh": round(w / 1e6, 2)}
            cap = record(INPUT, NAME, round(capture, 2), "EUR/MWh", period, src, url, OK,
                         note + f"Solar-weighted day-ahead price, {len(both):,} of {len(hours):,} hours (UTC days); "
                         f"{neg:,} negative-price hours included. Rounded to EUR 0.01.",
                         extra={"method": "solar-weighted (capture)", "stats": stats, "unrounded": capture,
                                "short": "Export price: DE-LU solar capture, trailing 12 months"})
            bl = record("baseload_context", "DE-LU day-ahead baseload average, trailing 12 months (context only)", round(base, 2),
                        "EUR/MWh", period, src, url, OK, "Plain average of hourly day-ahead prices. Not a model input.",
                        applies=False, extra={"method": "time average (baseload)",
                                                       "short": "DE-LU baseload average, trailing 12 months"})
            save_cache(cap)
            return [cap, bl]
        except Exception as e:  # noqa: BLE001 - try the next source, keep the reason
            errors.append(f"{name}: {e}")
            log(f"  wholesale: {name} failed: {e}")
    return [fallback(INPUT, NAME, "EUR/MWh", model_value, f"{EC}/price?bzn=DE-LU", "; ".join(errors))]
