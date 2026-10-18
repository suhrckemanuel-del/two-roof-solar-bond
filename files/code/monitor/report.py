"""report.html for one monitor run: one A4 page, plain language."""
import html
import re


def esc(x):
    return html.escape(str(x))


def fmt(v, kind):
    if not isinstance(v, (int, float)):
        return "n/a"
    if kind == "eur":
        return f"EUR {v / 1e6:.2f}M" if abs(v) >= 1e6 else f"EUR {v / 1e3:,.1f}k" if abs(v) >= 1e3 else f"EUR {v:,.0f}"
    return {"x": f"{v:.2f}x", "yr": f"{v:.1f}", "pct": f"{v:.1%}"}.get(kind, f"{v:,.2f}")


def delta(b, m, kind):
    if not isinstance(b, (int, float)) or not isinstance(m, (int, float)):
        return ""
    d = m - b
    if abs(d) < 1e-9:
        return "no change"
    sign = "+" if d > 0 else "-"
    d = abs(d)
    return sign + {"x": f"{d:.2f}x", "eur": fmt(d, "eur")[4:], "yr": f"{d:.1f} yrs", "pct": f"{d * 100:.1f} pts"}.get(kind, f"{d:,.2f}")


def num(v):
    if v is None:
        return "n/a"
    return f"{v:,.6g}" if isinstance(v, float) else str(v)


def when(iso):
    return str(iso)[:16].replace("T", " ") if iso else ""


def short_src(src):
    return src.split(" (")[0]


def tidy(sentence):
    """Display only: put back the space the HTML-to-text step loses between a table label and its cell."""
    return re.sub(r"(?<=[a-z]{2})(?=[A-Z][a-z])", " ", sentence)


CSS = """
@page{size:A4;margin:9mm 12mm}
:root{--ink:#1b2430;--muted:#5d6672;--line:#d5dae0;--bg:#fff;--bad:#a3361f;--ok:#2f6b3a;--sim:#8a4b00}
@media (prefers-color-scheme:dark){:root:not([data-theme='light']){--ink:#e6e9ee;--muted:#9aa3ae;--line:#3a424d;--bg:#14181d;--bad:#ff8a70;--ok:#7fcf8c;--sim:#f0b060}}
:root[data-theme='dark']{--ink:#e6e9ee;--muted:#9aa3ae;--line:#3a424d;--bg:#14181d;--bad:#ff8a70;--ok:#7fcf8c;--sim:#f0b060}
@media print{:root{--ink:#1b2430;--muted:#5d6672;--line:#d5dae0;--bg:#fff;--bad:#a3361f;--ok:#2f6b3a;--sim:#8a4b00}}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--ink);font:8.3pt/1.32 "Segoe UI",Arial,Helvetica,sans-serif;margin:0 auto;max-width:190mm;padding:0 16px}
h1{font-size:14pt;margin:0 0 1mm;letter-spacing:-.01em}
h2{font-size:9.4pt;margin:3.2mm 0 .8mm;text-transform:none;border-bottom:1.2px solid var(--ink);padding-bottom:.6mm}
.meta{color:var(--muted);margin:0 0 2mm}
.status{font-weight:700;margin:1.5mm 0 0}
.sim{color:var(--sim);font-weight:800;letter-spacing:.04em}
table{border-collapse:collapse;width:100%;margin:0}
td,th{border-bottom:1px solid var(--line);padding:.7mm 1.4mm .7mm 0;text-align:left;vertical-align:top}
th{color:var(--muted);font-weight:600;font-size:7.8pt}
td.n,th.n{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
.pass{color:var(--ok);font-weight:700}.fail{color:var(--bad);font-weight:700}
.chg{font-weight:700}
.small{font-size:7.6pt;color:var(--muted)}
a{color:inherit}
.src{word-break:break-all}
ol{margin:0;padding-left:5mm}ol li{margin:0 0 1.1mm}
.doc{font-weight:700;margin:2mm 0 .8mm}
.fig{white-space:nowrap;font-weight:700}
.two{display:grid;grid-template-columns:.9fr 1.1fr;gap:6mm}
.nw{white-space:nowrap}table.fetch{table-layout:fixed}table.fetch td{overflow-wrap:break-word;padding-right:2.5mm}
.stale{columns:2;column-gap:6mm}.stale ol{break-inside:auto}.stale li{break-inside:avoid}.stale .doc{break-after:avoid}
.foot{margin-top:2mm;border-top:1px solid var(--line);padding-top:1.2mm;color:var(--muted);font-size:7.8pt}
code{font-size:7.6pt}
@media (max-width:640px){.two{grid-template-columns:1fr}.stale{columns:1}}
"""


def render(r):
    sim = r["simulated"]
    title = "Market monitor run " + r["run_id"]
    p = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
         f"<title>Market monitor run</title><style>{CSS}</style></head><body>"]
    p.append(f"<h1>{esc(title)}{' <span class=sim>SIMULATED</span>' if sim else ''}</h1>")
    p.append(f"<p class='meta'>Solar bond case (fictional project), LHoFT x STOKR. Created {esc(when(r['created']))}. "
             + ("Inputs set by hand to test drift detection, not market data." if sim else
                "Public market data, written into a copy of the model.") + "</p>")
    p.append(f"<p class='status'>{esc(r['status'])}</p>")

    # 1. fetched
    p.append("<h2>1. What was fetched</h2><table class=fetch><colgroup><col style='width:23%'><col style='width:14%'><col style='width:14%'><col style='width:16%'><col></colgroup>"
             "<tr><th>Input</th><th class=n>Value</th><th>Period</th><th>Source, fetched</th><th>Status</th></tr>")
    for f in r["fetched"]:
        src = esc(short_src(f["source"]))
        link = f"<a href='{esc(f['source_url'])}'>{src}</a>" if f.get("source_url") else src
        used = "" if f.get("applies", True) else " <span class=small>(context, not a model input)</span>"
        det = f.get("detail", "")
        if f.get("dataset_updated"):
            det = f"{f.get('source_value')} {f.get('source_unit', '')}, newest semester; dataset updated {when(f['dataset_updated'])}."
        p.append(f"<tr><td>{esc(f.get('short') or f['name'])}{used}</td><td class=n>{esc(num(f['value']))} {esc(f['unit'])}</td><td>{esc(f['period'])}</td>"
                 f"<td>{link}<br><span class=small>{esc(when(f['fetched_at']))}</span></td><td><b>{esc(f['status'])}</b>"
                 + (f"<br><span class=small>{esc(det)}</span>" if det and f.get("applies", True) else "") + "</td></tr>")
    p.append("</table>")

    # 2 + 4 left, 3 right
    rec, sync = r["reconciliation"], r["base_facts_sync"]
    cls = "pass" if rec["result"] == "PASS" else "fail"
    p.append("<div class=two><div>")
    p.append("<h2>2. Model inputs changed, in the copy only</h2>")
    if r["inputs_changed"]:
        p.append("<table><tr><th>Input</th><th>Cell</th><th class=n>Base case</th><th class=n>Market copy</th></tr>")
        for k, c in r["inputs_changed"].items():
            p.append(f"<tr><td>{esc(k)}</td><td>{esc(c['cell'])}</td><td class=n>{esc(num(c['old']))}</td><td class='n chg'>{esc(num(c['new']))}</td></tr>")
        p.append("</table>")
    else:
        p.append("<p>None. Every fetched value equals the base case input, so nothing in the model moves.</p>")
    p.append("<h2>4. Checks</h2><table>")
    p.append(f"<tr><td>Engine vs Excel copy</td><td><span class={cls}>{rec['result']}</span>: the Python engine (mc_engine.py) matches the Excel copy, "
             f"largest difference EUR {rec['worst_abs_diff_eur']:.6f} (Case A, Case B, Downside, IRRs, payback); "
             f"it read the new inputs: {'yes' if rec['engine_read_changed_inputs'] else 'NO'}.</td></tr>")
    p.append(f"<tr><td>Published facts vs base</td><td>{'In sync' if sync['in_sync'] else 'NOT in sync'}: "
             f"{sync['differences']} differences on re-export, so every change here comes from the new inputs.</td></tr>")
    p.append(f"<tr><td>Monte Carlo</td><td>{esc(r['monte_carlo'])}</td></tr></table>")
    p.append("</div><div>")
    p.append("<h2>3. Effect on key outputs</h2><table><tr><th>Output</th><th class=n>Base</th><th class=n>Market copy</th><th class=n>Change</th></tr>")
    for o in r["key_outputs"]:
        dl = delta(o["base"], o["market"], o["format"])
        p.append(f"<tr><td>{esc(o['label'])}</td><td class=n>{fmt(o['base'], o['format'])}</td><td class='n{' chg' if dl not in ('', 'no change') else ''}'>{fmt(o['market'], o['format'])}</td>"
                 f"<td class=n>{esc(dl)}</td></tr>")
    p.append("</table></div></div>")

    # 5. stale sentences
    d = r["drift"]
    n = len(d["stale"])
    p.append(f"<h2>5. Sentences that are stale under {'the simulated inputs' if sim else 'today&#39;s market data'}: {n}</h2>")
    counts = "; ".join(f"{esc(x['file'])} {x['stale_sentences']} ({x['numbers_traced']} numbers traced, {x['unmatched']} unmatched)" for x in d["documents"])
    p.append(f"<p class=small>{counts}. Stale: a number the reader sees would differ at the precision shown. A dated figure "
             "(EUR 45 in 2025) stays true for its year but is listed because the model input it stands for would change. "
             "Table cells show the model row they trace to.</p><div class=stale>")
    for doc in d["documents"]:
        items = [x for x in d["stale"] if x["document"] == doc["file"]]
        if not items:
            continue
        p.append(f"<p class=doc>{esc(doc['file'])}</p><ol>")
        for x in items:
            seen, figs = set(), []
            for y in x["numbers"]:
                if (y["shown"], y["new"]) in seen:
                    continue
                seen.add((y["shown"], y["new"]))
                tag = " <span class=small>(check by eye)</span>" if y.get("how") in ("period", "twin") else ""
                figs.append(f"<span class=fig>{esc(y['shown'])} &rarr; {esc(y['new'])}</span>{tag}")
            sent = tidy(x["sentence"])
            body = (f"<span class=small>table cell: {esc(x['numbers'][0]['label'])}</span>" if len(sent) <= 24
                    else esc(sent))
            p.append(f"<li>{'; '.join(figs)} &nbsp;{body}</li>")
        p.append("</ol>")
    p.append("</div>")
    p.append(f"<p class='foot'>Decision: <code>monitor.py approve|reject {esc(r['run_id'])} --by NAME --note \"...\"</code> only adds a line to "
             "approvals.csv. The base model, its facts and the documents stay as they are until an analyst changes them by hand.</p>")
    p.append("</body></html>")
    return "".join(p)
