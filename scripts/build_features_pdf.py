"""
Builds Zatch-Features.pdf from docs/FEATURES.md, with a contents page.

    uv run --with markdown python scripts/build_features_pdf.py

WHAT IT DOES:
    Markdown -> HTML (python-markdown, tables and heading ids) -> PDF,
    printed by a headless Chromium browser (Edge or Chrome).

WHY A BROWSER AND NOT A PDF LIBRARY:
    The document quotes real Hindi replies. Devanagari needs complex text
    shaping - vowel signs and conjuncts are re-ordered and joined when
    drawn - and ReportLab does no shaping, so those lines come out
    garbled. A browser shapes them correctly, along with the rupee sign,
    tables and clickable contents links.

THE ACCESS CODE GOES IN THE PDF, NEVER IN GIT:
    The first page carries the demo access code, read at build time from
    --access-code, the DEMO_ACCESS_CODE environment variable, or .env -
    in that order. docs/FEATURES.md never contains it, and
    Zatch-Features.pdf is listed in .gitignore so the built file cannot
    be committed by accident. Share the PDF only with people who may use
    the demo.
"""

import argparse
import html
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "docs" / "FEATURES.md"
OUTPUT = ROOT / "Zatch-Features.pdf"
DEMO_URL = "https://zatch-unified-conversational-ai-assistant.onrender.com/demo"

BROWSERS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
]
BROWSER_COMMANDS = ["chromium", "chromium-browser", "google-chrome", "microsoft-edge", "msedge"]

CSS = """
@page { size: A4; margin: 18mm 16mm 18mm 16mm; }
body { font-family: "Segoe UI", "Nirmala UI", "Noto Sans", "Noto Sans Devanagari",
       Arial, sans-serif; font-size: 10.5pt; line-height: 1.5; color: #1d232b; }
h1 { font-size: 22pt; margin: 0 0 6pt; }
h2 { font-size: 15pt; margin: 22pt 0 6pt; padding-bottom: 3pt; border-bottom: 2px solid #2fd39b;
     page-break-after: avoid; }
h3 { font-size: 12pt; margin: 16pt 0 4pt; page-break-after: avoid; }
h4 { font-size: 10.5pt; margin: 10pt 0 2pt; }
p, li { margin: 3pt 0; }
table { border-collapse: collapse; width: 100%; margin: 6pt 0 10pt; font-size: 9.5pt;
        page-break-inside: auto; }
th, td { border: 1px solid #cfd6de; padding: 4pt 6pt; vertical-align: top; text-align: left; }
th { background: #eef2f6; }
tr { page-break-inside: avoid; }
code { font-family: Consolas, "Cascadia Mono", monospace; font-size: 9pt; background: #f2f4f7;
       padding: 0 2pt; border-radius: 2pt; }
pre { background: #f2f4f7; padding: 6pt 8pt; border-radius: 4pt; white-space: pre-wrap;
      font-size: 9pt; page-break-inside: avoid; }
pre code { background: none; padding: 0; }
blockquote { margin: 6pt 0; padding: 4pt 10pt; border-left: 3px solid #2fd39b; background: #f6fbf9; }
a { color: #0b6e4f; text-decoration: none; }
.cover { page-break-after: always; padding-top: 60mm; }
.cover h1 { font-size: 28pt; }
.cover .sub { color: #5a6573; font-size: 12pt; margin-bottom: 18pt; }
.access { margin-top: 18pt; padding: 10pt 14pt; border: 2px solid #2fd39b; border-radius: 6pt;
          display: inline-block; font-size: 12pt; }
.access b { font-family: Consolas, monospace; font-size: 13pt; }
.warn { color: #8a5a00; font-size: 9.5pt; margin-top: 8pt; }
.toc { page-break-after: always; }
.toc h2 { border: none; }
.toc ol { list-style: none; padding-left: 0; }
.toc li.g { font-weight: 650; margin-top: 7pt; }
.toc li.f { padding-left: 14pt; font-size: 9.5pt; }
"""


def read_access_code(explicit: str | None) -> str | None:
    if explicit:
        return explicit
    if os.environ.get("DEMO_ACCESS_CODE"):
        return os.environ["DEMO_ACCESS_CODE"].strip()
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            match = re.match(r"\s*DEMO_ACCESS_CODE\s*=\s*(.*)$", line)
            if match:
                value = match.group(1).split(" #", 1)[0].strip().strip('"').strip("'")
                return value or None
    return None


def find_browser() -> str | None:
    for path in BROWSERS:
        if Path(path).exists():
            return path
    for name in BROWSER_COMMANDS:
        found = shutil.which(name)
        if found:
            return found
    return None


_LIST_ITEM = re.compile(r"^\s*(?:[-*]|\d+\.)\s")


def _github_compatible(text: str) -> str:
    """Inserts the blank line python-markdown needs before a list.

    GitHub lets a list start straight after a line of text ("**Real
    output**" then "- ..."); python-markdown folds those items into the
    paragraph. Adding the blank line changes nothing on GitHub, so the
    markdown file can stay written for GitHub.
    """
    out: list[str] = []
    in_code = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_code = not in_code
        if (not in_code and _LIST_ITEM.match(line) and out and out[-1].strip()
                and not _LIST_ITEM.match(out[-1]) and not out[-1].startswith((" ", "|"))):
            out.append("")
        out.append(line)
    return "\n".join(out)


def to_html(markdown_text: str) -> tuple[str, list[tuple[int, str, str]]]:
    try:
        import markdown
    except ImportError:
        sys.exit("python-markdown is missing. Run:\n"
                 "  uv run --with markdown python scripts/build_features_pdf.py")

    md = markdown.Markdown(extensions=["tables", "fenced_code", "toc", "sane_lists"])
    body = md.convert(_github_compatible(markdown_text))
    # ~~struck~~ is GitHub syntax python-markdown does not know.
    body = re.sub(r"~~(.+?)~~", r"<del>\1</del>", body)
    headings: list[tuple[int, str, str]] = []

    def walk(tokens):
        for t in tokens:
            headings.append((t["level"], t["id"], html.unescape(t["name"])))
            walk(t.get("children", []))

    walk(md.toc_tokens)
    return body, headings


def build_page(body: str, headings, access_code: str | None) -> str:
    title = next((name for level, _, name in headings if level == 1), "Zatch Chatbot Features")
    if access_code:
        access = (f'<div class="access">Demo access code: <b>{html.escape(access_code)}</b></div>'
                  f'<div class="warn">This page carries the access code. Share this PDF only '
                  f'with people who may use the demo.</div>')
    else:
        access = ('<div class="warn">No access code was available when this PDF was built - '
                  'ask the project owner for it.</div>')

    cover = (
        f'<section class="cover"><h1>{html.escape(title)}</h1>'
        f'<div class="sub">The main features, each tested live. Built {date.today():%d %B %Y}.</div>'
        f'<p>Live demo: <a href="{DEMO_URL}">{DEMO_URL}</a></p>{access}</section>'
    )

    items = []
    for level, anchor, name in headings:
        if level == 2:
            items.append(f'<li class="g"><a href="#{anchor}">{html.escape(name)}</a></li>')
        elif level == 3:
            items.append(f'<li class="f"><a href="#{anchor}">{html.escape(name)}</a></li>')
    toc = f'<section class="toc"><h2>Contents</h2><ol>{"".join(items)}</ol></section>'

    # The markdown's own H1 is the cover title; drop it from the body.
    body = re.sub(r"<h1[^>]*>.*?</h1>", "", body, count=1, flags=re.S)
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<title>{html.escape(title)}</title><style>{CSS}</style></head>'
            f'<body>{cover}{toc}{body}</body></html>')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--access-code", help="overrides DEMO_ACCESS_CODE / .env")
    parser.add_argument("--out", default=str(OUTPUT), help="output PDF path")
    parser.add_argument("--keep-html", action="store_true", help="also write the HTML beside the PDF")
    args = parser.parse_args()

    if not SOURCE.exists():
        sys.exit(f"{SOURCE} not found")
    browser = find_browser()
    if not browser:
        sys.exit("No Chromium browser found (Edge or Chrome). Install one, or add its path to BROWSERS.")

    body, headings = to_html(SOURCE.read_text(encoding="utf-8"))
    page = build_page(body, headings, read_access_code(args.access_code))

    out = Path(args.out).resolve()
    with tempfile.TemporaryDirectory() as tmp:
        html_path = Path(tmp) / "features.html"
        html_path.write_text(page, encoding="utf-8")
        if args.keep_html:
            out.with_suffix(".html").write_text(page, encoding="utf-8")
        cmd = [
            browser, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
            f"--user-data-dir={Path(tmp) / 'profile'}",
            f"--print-to-pdf={out}", html_path.as_uri(),
        ]
        completed = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if completed.returncode != 0 or not out.exists():
            sys.stderr.write(completed.stderr[-2000:])
            sys.exit("The browser did not produce the PDF.")

    # Numbered feature entries only ("### 12. ..."), not the intro's own subheadings.
    features = sum(1 for level, _, name in headings if level == 3 and re.match(r"\d+\.", name))
    print(f"Wrote {out} ({out.stat().st_size // 1024} KB) - {features} feature sections, "
          f"access code {'included on page 1' if read_access_code(args.access_code) else 'NOT included'}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
