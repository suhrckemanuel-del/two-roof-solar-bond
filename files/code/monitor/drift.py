"""Drift check: which sentences in the published documents would show a different figure under the market scenario.

Uses claim_checker.py's numbers layer unchanged: every displayed number is traced to a fact in the BASE facts, at
the precision shown. That fact's market-scenario value is then compared at the same displayed precision. A sentence
is "stale under today's market data" when at least one of its numbers would change.
"""
import re
import sys
from pathlib import Path

MODEL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODEL))
import claim_checker as cc  # noqa: E402

DOCS = [MODEL.parent / "deliverables" / "factsheet.html", MODEL.parent / "deliverables" / "proposal.html"]
# Sourced external figures that are dated readings of an input this monitor fetches: if the new reading differs at the
# displayed precision, a sentence quoting the old reading is out of date (the old figure stays true for its period).
EXTERNAL_LINKS = {"market.lux_business_price_h1_2026": "host_cost", "market.capture_2025": "capture"}
UNITS = ("%", "x", "M", "k")


def same(val, dec, v):
    return isinstance(v, (int, float)) and (round(v, dec) == round(val, dec) or cc.matches(val, dec, v))


def readings(number, fact_value):
    """The readings of a displayed number, in claim_checker's order (the natural reading first: EUR 2M = 2,000,000
    before 2). The unit is the one under which the checker's traced fact matches (a bare first number of a range,
    6.5-9%, takes the range's unit)."""
    m = cc.NUM.search(number)
    raw, unit = m.group(2), m.group(3)
    for u in ((unit,) if unit else (None,) + UNITS):
        c = cc.candidates(raw, u)
        if any(same(val, dec, fact_value) for val, dec in c):
            return raw, c
    return raw, None


def retrace(number, sentence, traced, facts):
    """claim_checker pools the matches of every reading and keeps the best label overlap, so 'EUR 2M' can land on a
    ratio of 1.6 (read as 2). Keep the checker's fact when it is in the most natural reading that has any match
    (exact before rounded); otherwise take that reading's best label overlap. Returns (fact, reading, tier facts)."""
    raw, c = readings(number, traced["value"])
    if c is None:
        return None, None, []
    for val, dec in c:
        num = [f for f in facts if isinstance(f["value"], (int, float))]
        exact = [f for f in num if round(f["value"], dec) == round(val, dec)]
        tier = exact or [f for f in num if cc.matches(val, dec, f["value"])]
        if tier:
            pick = traced if traced in tier else max(tier, key=lambda f: cc.overlap(sentence, f["label"]))
            return pick, (raw, val, dec), tier
    return None, None, []


def show(number, raw, val, dec, new):
    """The displayed number re-written with the new value, same unit, decimals and thousands style."""
    v = float(raw.replace(",", ""))
    factor = val / v if v else 1.0
    x = new / factor
    d = len(raw.split(".")[1]) if "." in raw else 0
    if d == 0 and abs(v) >= 1000:  # thousands-rounded figure (EUR 49,500): keep its rounding
        mag = 10 ** (len(str(int(abs(v)))) - len(str(int(abs(v))).rstrip("0")))
        x = round(x / mag) * mag
    s = f"{x:,.{d}f}" if "," in raw else f"{x:.{d}f}"
    return number.replace(raw, s, 1)


YEARLY = re.compile(r"\.y\d+$")


def moved(base_facts, market):
    """Headline model facts (not inputs, not year-by-year rows) whose value differs in the market scenario."""
    return [f for f in base_facts if str(f.get("kind", "")).startswith("MODEL") and f.get("key") in market
            and not YEARLY.search(f["key"]) and isinstance(f["value"], (int, float)) and abs(market[f["key"]] - f["value"]) > 1e-12]


DURATION = re.compile(r"payback", re.I)  # year COUNTS only; year INDEXES (first late year) collide with 'year 5'


def period_moved(number, sentence, cands, market):
    """claim_checker files a small whole number next to 'years' as a period ("pay back in about 14 years") and does
    not trace it. Second look, for payback only: in a sentence about paying back, does the number equal a payback
    figure that moves? (Year indexes such as 'first late year' are left out: they collide with 'year 5'.)"""
    if not DURATION.search(re.sub(r"pay\s+back", "payback", sentence)):
        return None, None
    m = cc.NUM.search(number)
    raw, unit = m.group(2), m.group(3)
    for val, dec in cc.candidates(raw, unit):
        for f in cands:
            if DURATION.search(f["label"]) and round(f["value"], dec) == round(val, dec) and not same(val, dec, market[f["key"]]):
                return f, (raw, unit, val, dec)
    return None, None


def check(base_facts, market_facts, changed_inputs):
    """changed_inputs: {input_key: new_value} actually applied in the scenario."""
    by_src = {(f["cell"], f["label"]): f for f in base_facts}
    market = {f["key"]: f["value"] for f in market_facts if "key" in f}
    cands = moved(base_facts, market)
    base_in = {f["key"][6:]: f["value"] for f in base_facts if f.get("key", "").startswith("input.")}
    pins = [re.compile(rf"(?<![\d.]){re.escape(f'{base_in[k]:g}')}(?![\d])") for k in changed_inputs if k in base_in]
    out = {"documents": [], "stale": [], "weak_moves": [], "moved_headline_facts": len(cands),
           "moved_facts": [{"key": f["key"], "label": f["label"], "base": f["value"], "market": market[f["key"]]} for f in cands]}
    for doc in DOCS:
        sents = cc.sentences(cc.visible_text(doc))
        rows = cc.check_numbers(sents, base_facts)
        traced = [r for r in rows if r["status"] in ("MATCHED", "MATCHED-WEAK")]
        n_unmatched = sum(r["status"] == "UNMATCHED" for r in rows)
        per_sentence, weak = {}, []

        def add(r, f, sc, new, note, how):
            raw, u, val, dec = sc
            per_sentence.setdefault(r["sentence"], []).append({
                "shown": r["number"], "new": show(r["number"], raw, val, dec, new), "fact": f.get("key", ""), "label": f["label"],
                "base_value": f["value"], "market_value": new, "note": note, "how": how})

        for r in rows:
            if r["status"] == "CONTEXT" and r["why"] == "time period or year index":
                # "pay back in about 14 years": the checker files small whole numbers next to 'years' as periods
                f, sc = period_moved(r["number"], r["sentence"], cands, market)
                if f:
                    add(r, f, sc, market[f["key"]], "a period figure that equals a model figure which moves; check by eye", "period")
                continue
            if r["status"] not in ("MATCHED", "MATCHED-WEAK"):
                continue
            f0 = by_src.get((r["source"], r["why"]))
            if f0 is None:
                continue
            if r["status"] == "MATCHED-WEAK":  # small bare whole numbers (table headers, counts): listed, not flagged
                if f0.get("key") in market and not same(round(f0["value"]), 0, market[f0["key"]]):
                    weak.append({"document": doc.name, "shown": r["number"], "fact": f0.get("key"), "sentence": r["sentence"]})
                continue
            f, rd, tier = retrace(r["number"], r["sentence"], f0, base_facts)
            if f is None:
                continue
            key = f.get("key", "")
            if f.get("kind") == "EXTERNAL":
                link = EXTERNAL_LINKS.get(key)
                if link not in changed_inputs:
                    continue
                new, note = changed_inputs[link], f"dated source figure; new reading of input '{link}'"
            elif key in market:
                new, note = market[key], ("" if f is f0 else f"re-traced from '{f0['label']}' to the natural reading of the number")
            else:
                continue
            raw, val, dec = rd
            if not same(val, dec, new):
                add(r, f, (raw, None, val, dec), new, note, "traced")
                continue
            # twin: the checker traced to a fact PINNED at the old input value (e.g. feasibility's 'grid EUR 121.9' run),
            # and the same quantity for the live case (Summary cell, identical value in the base) moves
            if not any(pin.search(f["label"]) for pin in pins):
                continue
            tw = [g for g in tier if g is not f and g.get("key") in market and str(g.get("kind", "")).startswith("MODEL")
                  and abs(g["value"] - f["value"]) <= 1e-9 * max(1.0, abs(f["value"])) and not same(val, dec, market[g["key"]])]
            if tw:
                g = tw[0]
                add(r, g, (raw, None, val, dec), market[g["key"]],
                    f"traced to '{f['label']}', which does not move; its identical twin '{g['label']}' does", "twin")
        for s, nums in per_sentence.items():
            out["stale"].append({"document": doc.name, "sentence": s, "numbers": nums})
        out["weak_moves"] += weak
        out["documents"].append({"file": doc.name, "numbers_traced": len(traced), "unmatched": n_unmatched,
                                 "stale_sentences": len(per_sentence),
                                 "stale_numbers": sum(len(v) for v in per_sentence.values()), "weak_moves": len(weak)})
    return out


def load(folder=None):
    return cc.load_facts(folder)


def clean(s):
    return re.sub(r"\s+", " ", s).strip()
