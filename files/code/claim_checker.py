"""Claim checker: test every number and promise in an investor-facing document against the model and term sheet.

Three layers, reported separately so a human sees which layer raised what:
  1. NUMBERS (deterministic): every number in the text must match a model cell (facts.json) or a sourced
     external fact (external_facts.json) at the precision shown, or it is flagged for a human.
  2. WORDING (deterministic): risky words (guaranteed, risk-free, liquid, safe, ...) are flagged; if the
     sentence negates them ("not guaranteed") they are listed as OK-negated for review, not as errors.
  3. PROMISES (Claude): reads the whole text with the term sheet and flags implied promises the regexes
     cannot see (implied repayment certainty, implied liquidity, unrestricted access, contradictions,
     unlabelled fiction). Advisory only: a human decides.

Usage:
  python claim_checker.py ../deliverables/factsheet.html [more.html ...] [--no-ai] [--out report]
Writes report.json and report.html (default name: claim-report) next to this script.
"""
import argparse
import html
import json
import re
from html.parser import HTMLParser
from pathlib import Path

HERE = Path(__file__).parent
MODEL = "claude-opus-5-5"

RISKY = [
    (r"\bguarantee[ds]?\b|\bguaranteeing\b", "implies guaranteed repayment"),
    (r"\brisk[- ]free\b|\bno risk\b|\bwithout risk\b", "implies no risk"),
    (r"\bsafe\b|\bsecure investment\b|\bprotected\b", "implies safety"),
    (r"\bliquid(ity)?\b|\bsell (at )?any ?time\b|\binstant(ly)?\b|\btradable 24/7\b", "implies liquidity"),
    (r"\bcertain(ly)?\b|\bwill (be )?(repaid|paid)\b|\balways\b", "implies certainty"),
    (r"\banyone can\b|\bopen to all\b|\bno restrictions?\b", "implies unrestricted access"),
]
NEGATION = re.compile(r"\b(not|no|none|never|nor|cannot|can't|isn't|without any)\b", re.I)
NUM = re.compile(r"(?<![\w.])(EUR\s?)?(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s?(%|x\b|M\b|k\b|MWp\b|MWh\b|kWp\b)?", re.I)
YEARISH = re.compile(r"^(19|20)\d{2}$")
# a number right after one of these words is part of a name or a legal reference, not a figure (KfW 270, Opus 5.5, Art. 2)
NAME_BEFORE = re.compile(r"\b(KfW|Opus|Sonnet|Haiku|Fable|Art\.|Article|Law|Regulation|Reg\.|round|section|page|Exhibits?|seed)\s?$", re.I)
# dates, clock times, durations, run ids and file sizes are labels, not figures (14 Oct, 18:53, 5:17, 2026-10-17_1055, 24 MB)
MONTH_AFTER = re.compile(r"^\s?(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\b")
CLOCK = (re.compile(r"^:\d\d\b"), re.compile(r"\d:$"))
ISO_BEFORE = re.compile(r"\b\d{4}-(\d{2}-)?$")
SIZE_AFTER = re.compile(r"^\s?(KB|MB|GB|MiB)\b")
RANGE_UNIT = re.compile(r"^-\s?\d[\d.,]*\s?(%|x\b|M\b|k\b)")
QUOTED = re.compile(r"[\"“‘']\s*$")


class Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self.skip, self.secno = [], 0, False

    def handle_starttag(self, tag, attrs):
        if tag in ("style", "script", "title"):
            self.skip += 1
        if tag in ("p", "div", "li", "tr", "h1", "h2", "br", "td", "th"):
            self.parts.append("\n")
        # a numbered section heading (<span class="n">8</span>Risks ...) is a label, not a figure:
        # emit it as "Section 8:" so NAME_BEFORE skips the number and the AI layer still sees the numbering
        if tag == "span" and "n" in (dict(attrs).get("class") or "").split():
            self.secno = True

    def handle_endtag(self, tag):
        if tag in ("style", "script", "title"):
            self.skip -= 1
        if tag == "span":
            self.secno = False

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(f"Section {data.strip()}: " if self.secno else data)


def visible_text(path):
    p = Text()
    p.feed(Path(path).read_text(encoding="utf-8"))
    return re.sub(r"[ \t]+", " ", html.unescape("".join(p.parts)))


def sentences(text):
    out = []
    for line in text.split("\n"):
        line = line.strip()
        if line:
            out += [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z])", line) if s.strip()]
    return out


def load_facts(folder=None):
    """Model facts from `folder` (default: this script's folder). external_facts.json is always the sourced file
    next to this script. monitor/ passes a market-scenario run's facts folder; the default is unchanged."""
    folder = Path(folder) if folder else HERE
    facts = json.loads((folder / "facts.json").read_text(encoding="utf-8"))
    ext = json.loads((HERE / "external_facts.json").read_text(encoding="utf-8"))
    for e in ext:
        e["cell"] = e["source"]
        e["kind"] = "EXTERNAL"
    # *_facts.json: simulation outputs (mc_run.py) and proposal figures (proposal_facts.py), each tagged with its kind
    extra = []
    for f in sorted(folder.glob("*_facts.json")):
        if f.name != "external_facts.json":
            extra += json.loads(f.read_text(encoding="utf-8"))
    return facts + ext + extra


def candidates(raw, unit):
    """All plausible true values a displayed number could stand for."""
    v = float(raw.replace(",", ""))
    dec = len(raw.split(".")[1]) if "." in raw else 0
    c = [(v, dec)]
    u = (unit or "").lower()
    if u == "%":
        c = [(v / 100, dec + 2), (v, dec)]
    elif u == "m":
        c = [(v * 1e6, max(0, dec - 6)), (v, dec)]
    elif u == "k":
        c = [(v * 1e3, max(0, dec - 3)), (v, dec)]
    elif u == "mwp":
        c = [(v * 1000, max(0, dec - 3)), (v, dec)]
    return c


def matches(value, dec, fact_value):
    if not isinstance(fact_value, (int, float)):
        return False
    # match at displayed precision (rounded), allowing thousands-rounding of large figures written without decimals
    if round(fact_value, dec) == round(value, dec):
        return True
    if dec == 0 and abs(value) >= 1000 and fact_value:
        mag = 10 ** (len(str(int(abs(value)))) - len(str(int(abs(value))).rstrip("0")))
        return abs(round(fact_value / mag) * mag - value) < 0.5 and abs(fact_value - value) / abs(fact_value) < 0.01
    return False


WORD = re.compile(r"[a-z]{3,}")
STOP = {"the", "and", "for", "eur", "year", "years", "per", "from", "with", "this", "that", "are", "you", "your"}


def overlap(sentence, label):
    a = set(WORD.findall(sentence.lower())) - STOP
    b = set(WORD.findall(label.lower())) - STOP
    return len(a & b)


def check_numbers(sents, facts):
    rows = []
    for s in sents:
        for m in NUM.finditer(s):
            raw, unit = m.group(2), m.group(3)
            v = float(raw.replace(",", ""))
            ctx = s[max(0, m.start() - 30): m.end() + 30]
            if s[m.end():m.end() + 2].lower() in ("st", "nd", "rd", "th"):
                rows.append({"number": m.group(0).strip(), "status": "CONTEXT", "why": "ordinal (e.g. 8th round)", "sentence": s})
                continue
            label = ("date" if not unit and v <= 31 and "." not in raw and MONTH_AFTER.match(s[m.end():]) else
                     "clock time or duration" if CLOCK[0].match(s[m.end():]) or CLOCK[1].search(s[:m.start()]) else
                     "date or run id" if ISO_BEFORE.search(s[:m.start()]) else
                     "file size" if SIZE_AFTER.match(s[m.end():]) else None)
            if label:
                rows.append({"number": m.group(0).strip(), "status": "CONTEXT", "why": label, "sentence": s})
                continue
            if NAME_BEFORE.search(s[:m.start()]):
                rows.append({"number": m.group(0).strip(), "status": "CONTEXT", "why": "part of a name or legal reference", "sentence": s})
                continue
            rng = RANGE_UNIT.match(s[m.end():])
            if not unit and rng:  # first number of a range like 6.5-9% takes the range's unit
                unit = rng.group(1)
            if YEARISH.match(raw) and not unit:
                rows.append({"number": m.group(0).strip(), "status": "CONTEXT", "why": "calendar year", "sentence": s})
                continue
            hits = []
            for val, dec in candidates(raw, unit):
                exact = [f for f in facts if isinstance(f["value"], (int, float)) and round(f["value"], dec) == round(val, dec)]
                approx = [f for f in facts if f not in exact and matches(val, dec, f["value"])]
                hits += [(2, f) for f in exact] + [(1, f) for f in approx]
            hit = None
            if hits:
                hits.sort(key=lambda h: (h[0], overlap(s, h[1]["label"])), reverse=True)
                hit = hits[0][1]
            weak = hit is not None and ("." not in raw and v <= 25 and (unit or "") == "")
            if hit and weak and re.search(r"\b(year|years|months?|weeks?|days?)\b", ctx, re.I):
                rows.append({"number": m.group(0).strip(), "status": "CONTEXT", "why": "time period or year index", "sentence": s})
            elif hit:
                rows.append({"number": m.group(0).strip(), "status": "MATCHED-WEAK" if weak else "MATCHED", "why": hit["label"], "source": hit["cell"],
                             "kind": hit.get("kind", ""), "sentence": s})
            elif re.search(r"\b(year|years|months?|weeks?|days?)\b", ctx, re.I) and v <= 25 and "." not in raw:
                rows.append({"number": m.group(0).strip(), "status": "CONTEXT", "why": "time period or year index", "sentence": s})
            else:
                rows.append({"number": m.group(0).strip(), "status": "UNMATCHED", "why": "no model cell or sourced fact at this precision", "sentence": s})
    return rows


def check_wording(sents):
    rows = []
    for s in sents:
        for pat, why in RISKY:
            for m in re.finditer(pat, s, re.I):
                if m.group(0) == "Liquid":  # capitalised: the Liquid Network's name, not a liquidity claim
                    continue
                if QUOTED.search(s[:m.start()]):  # the word is quoted (mentioned, not used)
                    rows.append({"phrase": m.group(0), "status": "OK-QUOTED", "why": why, "sentence": s})
                    continue
                negated = bool(NEGATION.search(s[:m.start()][-60:])) or bool(re.search(r"\bnot\b", s[m.end():m.end() + 15], re.I))
                rows.append({"phrase": m.group(0), "status": "OK-NEGATED" if negated else "FLAG", "why": why, "sentence": s})
    return rows


def term_sheet(facts):
    keep = ("input.coupon", "input.tenor", "input.token", "input.fixed_bond", "input.ppa", "input.ppa_tenor", "input.host_share",
            "input.ref", "input.prem_tenor", "factsheet.", "scenario.minimum_dscr", "scenario.debt_service_paid_late",
            "su.issuance_costs", "su.initial_dsra", "mc.")
    lines = [f"- {f['label']}: {f['value']}" for f in facts if f.get("kind") != "EXTERNAL" and f["key"].startswith(keep)]
    return "\n".join(lines)


SCHEMA = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "quote": {"type": "string"},
                    "issue": {"type": "string", "enum": ["implied_guarantee", "implied_liquidity", "unrestricted_access",
                                                          "contradicts_terms", "overstated_certainty", "missing_risk_context",
                                                          "unlabelled_fiction", "unclear_rights"]},
                    "severity": {"type": "string", "enum": ["high", "medium", "low"]},
                    "explanation": {"type": "string"},
                    "suggested_rewrite": {"type": "string"},
                },
                "required": ["quote", "issue", "severity", "explanation", "suggested_rewrite"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["findings"],
    "additionalProperties": False,
}

SYSTEM = """You review investor-facing text for a fictional tokenised solar bond case study. Your job is to catch promises the text makes that the term sheet and model do not support. Flag a sentence when it states or implies that repayment or interest is guaranteed or certain, that the token can be sold easily or at a fair price, that anyone can invest without checks, or when it contradicts the term sheet, overstates certainty about projections, omits a risk a retail investor needs right next to a claim, leaves an invented fact unlabelled, or leaves investor rights unclear.
Do not flag sentences that clearly negate these ideas ("not guaranteed", "there may be no buyer"). Do not rewrite style. Quote the exact sentence. Report only real issues; an empty list is a valid answer. Suggested rewrites must be plain English without em dashes."""


def check_promises(text, facts):
    import anthropic

    client = anthropic.Anthropic()
    msg = client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        extra_body={"fallbacks": "default"},  # SDK 0.96 has no typed param yet
        output_config={"effort": "high", "format": {"type": "json_schema", "schema": SCHEMA}},
        system=SYSTEM,
        messages=[{"role": "user", "content": f"TERM SHEET AND MODEL FIGURES:\n{term_sheet(facts)}\n\nDOCUMENT TEXT:\n{text}"}],
    )
    if msg.stop_reason == "refusal":
        return {"error": "model declined the request", "findings": []}
    body = next(b.text for b in msg.content if b.type == "text")
    out = json.loads(body)
    out["model"] = msg.model
    out["usage"] = {"input_tokens": msg.usage.input_tokens, "output_tokens": msg.usage.output_tokens}
    return out


def render_html(report):
    def esc(x):
        return html.escape(str(x))

    def table(rows, cols):
        head = "".join(f"<th>{esc(c)}</th>" for c in cols)
        body = "".join("<tr>" + "".join(f"<td class='{esc(r.get('status', r.get('severity', '')))}'>{esc(r.get(c, ''))}</td>" for c in cols) + "</tr>" for r in rows)
        return f"<table><tr>{head}</tr>{body}</table>"

    parts = ["<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Claim check</title><style>",
             ":root{--ink:#1b2430;--muted:#5b6573;--line:#d3d9e0;--bg:#fff;--bad:#a3361f;--ok:#2f6b3a}",
             "@media (prefers-color-scheme:dark){:root:not([data-theme='light']){--ink:#e6e9ee;--muted:#9aa3ae;--line:#3a424d;--bg:#14181d;--bad:#ff8a70;--ok:#7fcf8c}}",
             ":root[data-theme='dark']{--ink:#e6e9ee;--muted:#9aa3ae;--line:#3a424d;--bg:#14181d;--bad:#ff8a70;--ok:#7fcf8c}",
             "body{background:var(--bg);color:var(--ink);font:14px/1.45 Arial,Helvetica,sans-serif;margin:0 auto;padding:16px;max-width:1100px}",
             "table{border-collapse:collapse;width:100%;margin:8px 0 20px;font-size:13px}td,th{border-bottom:1px solid var(--line);padding:5px 6px;text-align:left;vertical-align:top}",
             "th{color:var(--muted)}.UNMATCHED,.FLAG,.high{color:var(--bad);font-weight:700}.MATCHED,.OK-NEGATED{color:var(--ok)}.wrap{overflow-x:auto}h1{font-size:20px}h2{font-size:16px;margin-top:22px}",
             "</style></head><body>"]
    for doc in report["documents"]:
        n = doc["numbers"]
        s = {k: sum(1 for r in n if r["status"] == k) for k in ("MATCHED", "MATCHED-WEAK", "CONTEXT", "UNMATCHED")}
        w = sum(1 for r in doc["wording"] if r["status"] == "FLAG")
        a = len(doc.get("promises", {}).get("findings", []))
        parts.append(f"<h1>Claim check: {esc(doc['file'])}</h1><p>{s['MATCHED']} numbers traced to the model or a cited source, {s['MATCHED-WEAK']} small numbers matched loosely (check by eye), {s['CONTEXT']} are dates or periods, "
                     f"<b>{s['UNMATCHED']} unmatched</b>. Risky wording flags: <b>{w}</b>. AI promise findings: <b>{a}</b>.</p>")
        parts.append("<h2>1. Numbers</h2><div class='wrap'>" + table([r for r in n if r["status"] != "CONTEXT"], ["number", "status", "why", "source", "sentence"]) + "</div>")
        parts.append("<h2>2. Wording</h2><div class='wrap'>" + table(doc["wording"], ["phrase", "status", "why", "sentence"]) + "</div>")
        pr = doc.get("promises")
        if pr is not None:
            note = f" ({esc(pr.get('model', ''))})" if pr.get("model") else ""
            parts.append(f"<h2>3. Implied promises, checked by Claude{note}</h2><p style='color:var(--muted)'>Advisory: a person decides on every finding.</p><div class='wrap'>"
                         + table(pr.get("findings", []), ["severity", "issue", "quote", "explanation", "suggested_rewrite"]) + "</div>")
    parts.append("</body></html>")
    return "".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--no-ai", action="store_true")
    ap.add_argument("--out", default="claim-report")
    ap.add_argument("--facts-dir", default=None, help="load facts.json and *_facts.json from this folder (default: model/)")
    a = ap.parse_args()
    facts = load_facts(a.facts_dir)
    report = {"documents": []}
    for f in a.files:
        text = visible_text(f)
        sents = sentences(text)
        doc = {"file": Path(f).name, "numbers": check_numbers(sents, facts), "wording": check_wording(sents)}
        if not a.no_ai:
            doc["promises"] = check_promises(text, facts)
        report["documents"].append(doc)
        n = doc["numbers"]
        print(f"{doc['file']}: numbers matched {sum(r['status']=='MATCHED' for r in n)}, weak {sum(r['status']=='MATCHED-WEAK' for r in n)}, context {sum(r['status']=='CONTEXT' for r in n)}, "
              f"UNMATCHED {sum(r['status']=='UNMATCHED' for r in n)}; wording FLAG {sum(r['status']=='FLAG' for r in doc['wording'])}, "
              f"negated {sum(r['status']=='OK-NEGATED' for r in doc['wording'])}"
              + (f"; AI findings {len(doc['promises'].get('findings', []))}" if 'promises' in doc else ""))
    (HERE / f"{a.out}.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    (HERE / f"{a.out}.html").write_text(render_html(report), encoding="utf-8")


if __name__ == "__main__":
    main()
