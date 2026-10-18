"""Run the claim checker on the demo page as a reader sees it.

The page draws most of its numbers from data.js with JavaScript, and the checker reads static HTML, so this script
takes the rendered page from headless Chrome and hands that to model/claim_checker.py unchanged. Left out, because
they are not claims about the bond: the planted test sentences (#sents, #detail: wrong on purpose, they are what the
checker demo catches), the price setting's readout and controls (#px-*: simulated reruns, each labelled with its
run id, not the published model), the cover chart's axis and end labels (#cover-chart: the same values are in the
readout below it), the checker's own counts (#real: numbers checked per document, read from the reports) and the
blind test (#blind: it quotes planted wrong figures on purpose).

usage: python demo/check_page.py [--no-ai]   ->  demo/claim-report.json and .html, demo/claim-input.html
"""
import subprocess
import sys
import tempfile
from html.parser import HTMLParser
from pathlib import Path

HERE = Path(__file__).resolve().parent
CHECKER = HERE.parent / "model" / "claim_checker.py"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
SKIP_IDS = {"sents", "detail", "px-out", "px-src", "px-scale", "px-cap-v", "px-chart", "px-axis", "cover-chart", "real", "blind"}
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class Strip(HTMLParser):
    """Re-emit the HTML without the subtrees whose id is in SKIP_IDS."""

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.out, self.depth = [], 0

    def handle_starttag(self, tag, attrs):
        if self.depth:
            self.depth += tag not in VOID
            return
        if dict(attrs).get("id") in SKIP_IDS and tag not in VOID:
            self.depth = 1
            return
        self.out.append(self.get_starttag_text())

    def handle_startendtag(self, tag, attrs):
        if not self.depth:
            self.out.append(self.get_starttag_text())

    def handle_endtag(self, tag):
        if self.depth:
            self.depth -= tag not in VOID
            return
        self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if not self.depth:
            self.out.append(data)

    handle_entityref = lambda self, name: None if self.depth else self.out.append(f"&{name};")
    handle_charref = lambda self, name: None if self.depth else self.out.append(f"&#{name};")


def rendered(page):
    with tempfile.TemporaryDirectory() as prof:
        r = subprocess.run([CHROME, "--headless=new", "--disable-gpu", f"--user-data-dir={prof}", "--no-first-run",
                            "--virtual-time-budget=4000", "--dump-dom", page.resolve().as_uri()],
                           capture_output=True, text=True, encoding="utf-8", timeout=120)
    if "<body" not in r.stdout:
        raise SystemExit(f"Chrome returned no page: {r.stderr[-400:]}")
    return r.stdout


def main():
    dom = rendered(HERE / "index.html")
    s = Strip()
    s.feed(dom)
    s.close()
    out = HERE / "claim-input.html"
    out.write_text("".join(s.out), encoding="utf-8")
    missing = [i for i in SKIP_IDS if f'id="{i}"' not in dom]
    if missing:
        raise SystemExit(f"rendered page lacks {missing}: the page changed, update SKIP_IDS")
    args = [sys.executable, str(CHECKER), str(out), "--out", str(HERE / "claim-report")] + sys.argv[1:]
    return subprocess.run(args).returncode


if __name__ == "__main__":
    sys.exit(main())
