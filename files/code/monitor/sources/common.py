"""Shared helpers for the market-input fetchers: HTTP with retry and backoff, the last-good-value cache, and the
record every fetcher returns. A fetcher never invents a value: if its source fails it hands back the cached value or
the model's own input, and says so in `status`."""
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

CACHE = Path(__file__).resolve().parents[1] / "cache"
UA = {"User-Agent": "lhoft-stokr-case-monitor/1.0 (student case study; contact via repository owner)"}

OK = "ok"
CACHED = "source unavailable, using last good value"
MODEL = "source unavailable, using model input (no cached value)"
FAILED = "failed"


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def get_json(url, params=None, tries=3, waits=(5, 20), timeout=60, log=print):
    """GET a JSON document, retrying on network errors and HTTP 5xx / 429 with growing waits."""
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=timeout)
            if r.status_code < 400:
                return r.json()
            last = f"HTTP {r.status_code}"
            if r.status_code < 500 and r.status_code != 429:
                break
        except (requests.RequestException, ValueError) as e:
            last = type(e).__name__ + (f" after {timeout} s" if isinstance(e, requests.Timeout) else f": {e}"[:120])
        if i < tries - 1:
            w = waits[min(i, len(waits) - 1)]
            log(f"    {url[:70]}... {last}; retry in {w}s")
            time.sleep(w)
    raise RuntimeError(last or "unknown error")


def record(input_key, name, value, unit, period, source, source_url, status, detail="", fetched_at=None, applies=True, extra=None):
    """One dated market input. `applies` = False marks a context figure that is not written into the model."""
    return {"input": input_key, "name": name, "value": value, "unit": unit, "period": period, "source": source,
            "source_url": source_url, "fetched_at": fetched_at or now_iso(), "status": status, "detail": detail,
            "applies": applies, **(extra or {})}


def save_cache(rec):
    CACHE.mkdir(exist_ok=True)
    (CACHE / f"{rec['input']}.json").write_text(json.dumps(rec, indent=1), encoding="utf-8")


def fallback(input_key, name, unit, model_value, source_url, why):
    """Last good cached record if there is one, else the model's current input, both clearly flagged."""
    f = CACHE / f"{input_key}.json"
    if f.exists():
        c = json.loads(f.read_text(encoding="utf-8"))
        c.update(status=CACHED, detail=f"{why}. Cached value fetched {c['fetched_at']} from {c['source']}.",
                 cached_fetched_at=c["fetched_at"], fetched_at=now_iso())
        return c
    if model_value is None:
        return record(input_key, name, None, unit, "", "none", source_url, FAILED, why)
    return record(input_key, name, model_value, unit, "model input", "model base case (solar_bond_model.xlsx)", source_url,
                  MODEL, f"{why}. No cached value yet, so the model's current input is kept (no change).")
