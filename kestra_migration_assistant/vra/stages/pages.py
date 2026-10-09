"""
Where the report pages live, and the links between them.

    stages/<estate>/index.html        the way in: the brief
    stages/<estate>/report/           every other page: the views, workflows/, vendor/, BRIEF.md (the
                                      brief to download) and reports/, the Markdown reports rendered as pages

The views are drawn as if they all sat in report/, and every link in them is a plain relative path
to the file it means, a Markdown report included (`../data/1-assess/target-inventory.md`). `relink`
then points each link at what a reader should open: a Markdown report that has a rendered page goes
to that page, and the front page goes to index.html one level up. The .md files stay where the stages
write them; the rendered pages are regenerated from them on every run.
"""
from __future__ import annotations

import os
import posixpath
import re

from . import common as C
from . import mdhtml
from . import theme as TH

REPORTS = "reports"
# Folders under stages/<estate>/ whose Markdown is not a report: redacted copies of the export, the
# parser's work files, specs kept from before a regeneration, and the pages themselves.
SKIP_DIRS = ("sanitized", "work", "previous", "vendor", "reports", "workflows")

_ATTR = re.compile(r"""(\b(?:href|src)=)(["'])(.*?)\2""", re.S)
_CSS_URL = re.compile(r"""(url\()(["']?)([^"')]+)\2(\))""")
_MERMAID_CLICK = re.compile(r"""(click\s+\S+\s+href\s+)(")([^"]+)(")""")


def report_sources() -> list[str]:
    """Every Markdown report in the estate's stage folders, sorted by stage, then name."""
    C.require_estate()
    out = []
    for d, dirs, files in os.walk(C.STAGES):
        dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS and not x.startswith("."))
        out += [os.path.join(d, f) for f in sorted(files) if f.lower().endswith(".md")]
    return sorted(out, key=lambda p: (os.path.relpath(p, C.STAGES).count(os.sep) == 0, os.path.relpath(p, C.STAGES)))


def report_pages() -> dict[str, str]:
    """Markdown report -> its rendered page, both absolute. A name used twice is prefixed with its folder."""
    srcs = report_sources()
    names: dict[str, int] = {}
    for p in srcs:
        names[_base(p)] = names.get(_base(p), 0) + 1
    out = {}
    for p in srcs:
        b = _base(p)
        if names[b] > 1:
            b = C.slug(os.path.basename(os.path.dirname(p))).lower() + "-" + b
        out[os.path.normpath(p)] = os.path.join(C.PAGES, REPORTS, b + ".html")
    return out


def _base(p: str) -> str:
    b = os.path.splitext(os.path.basename(p))[0].lower()
    return {"brief": "brief"}.get(b, C.slug(b))


def link_map() -> dict[str, str]:
    """Where a link to a file should go instead: rendered reports, and the overview one level up."""
    m = dict(report_pages())
    m[os.path.normpath(os.path.join(C.PAGES, "index.html"))] = os.path.normpath(C.INDEX)
    return m


def relink(text: str, virtual_dir: str, actual_dir: str, mapping: dict[str, str] | None = None) -> str:
    """
    Rewrite every relative link in a page written as if it sat in `virtual_dir`, so it works from
    `actual_dir` and opens the rendered page for a Markdown report. Absolute URLs, anchors and data:
    are left alone (the pages' CSP forbids the network either way).
    """
    mapping = link_map() if mapping is None else mapping

    def fix(url: str) -> str:
        if not url or url.startswith(("#", "data:", "mailto:", "/", "about:")) or re.match(r"^[a-z][a-z0-9+.-]*:", url, re.I):
            return url
        path, frag = (url.split("#", 1) + [""])[:2]
        path, query = (path.split("?", 1) + [""])[:2]
        if not path:
            return url
        target = os.path.normpath(os.path.join(virtual_dir, path))
        target = mapping.get(target, target)
        new = posixpath.join(*os.path.relpath(target, actual_dir).split(os.sep))
        return new + (f"?{query}" if query else "") + (f"#{frag}" if frag else "")

    text = _ATTR.sub(lambda m: m.group(1) + m.group(2) + fix(m.group(3)) + m.group(2), text)
    text = _CSS_URL.sub(lambda m: m.group(1) + m.group(2) + fix(m.group(3)) + m.group(2) + m.group(4), text)
    return _MERMAID_CLICK.sub(lambda m: m.group(1) + m.group(2) + fix(m.group(3)) + m.group(4), text)


def write(rel_path: str, text: str, mapping: dict[str, str] | None = None) -> str:
    """Write a page drawn for its own place under pages/."""
    p = os.path.join(C.PAGES, rel_path)
    C.write_text(p, relink(text, os.path.dirname(p), os.path.dirname(p), mapping))
    return p


def write_index(text: str, mapping: dict[str, str] | None = None) -> str:
    """Write the overview, drawn as if it sat in pages/, to stages/<estate>/index.html."""
    C.write_text(C.INDEX, relink(text, C.PAGES, C.STAGES, mapping))
    return C.INDEX


# ------------------------------------------------------------------ rendered reports

def render_reports(mapping: dict[str, str] | None = None) -> list[str]:
    """Render every Markdown report as a page in pages/reports/, plus a list of them. Returns the paths."""
    mapping = link_map() if mapping is None else mapping
    out_dir = os.path.join(C.PAGES, REPORTS)
    C.ensure(out_dir)
    pages = report_pages()
    keep = {os.path.basename(v) for v in pages.values()} | {"index.html"}
    for f in os.listdir(out_dir):                   # a report that is gone loses its page
        if f.endswith(".html") and f not in keep:
            os.remove(os.path.join(out_dir, f))
    written, listing = [], []
    for src, dst in pages.items():
        md = open(src, encoding="utf-8", errors="replace").read()
        if os.path.abspath(src) == os.path.abspath(C.BRIEF_MD) and "\n---\n" in md:
            md = md.rsplit("\n---\n", 1)[0]       # its call to action and run facts: the page's footer says them
        body, info = mdhtml.render(md)
        rel = posixpath.join(*os.path.relpath(src, C.STAGES).split(os.sep))
        title = info["title"] or os.path.basename(src)
        head = (f'<p class="sub report-source">Rendered from <a href="{C.esc(os.path.relpath(src, out_dir))}">'
                f"<code>{C.esc(rel)}</code></a>. {_edit_note(rel)}</p>")
        # the report's links are relative to the .md, the shell's (nav, styles) to pages/reports/; the line
        # naming the source goes in last, since it opens the .md itself
        body = relink(body, os.path.dirname(src), out_dir, mapping)
        page = TH.page(title, f'<article class="report">{HEAD_SLOT}\n{body}</article>', depth=1,
                       mermaid=info["mermaid"], active=REPORTS + "/index.html")
        page = relink(page, out_dir, out_dir, mapping).replace(HEAD_SLOT, head)
        C.write_text(dst, page)
        written.append(dst)
        listing.append((rel, title, os.path.basename(dst)))
    written.append(_write_listing(listing, out_dir))
    return written


HEAD_SLOT = "<!--report-source-->"


def _edit_note(rel: str) -> str:
    if rel.endswith("BRIEF.md"):
        return ("The brief to download; answers and the signature go in your-input/answers.yaml. "
                "This page is a copy, regenerated on every run.")
    if rel.endswith("WALKTHROUGH.md"):
        return "Hand-written; this page is a copy."
    return "Written by the tool; this page is a copy, regenerated on every run."


STAGE_TITLES = {C.LAYOUT["preflight"]: "0. Preflight", C.LAYOUT["assess"]: "1. Assess",
                C.LAYOUT["understand"]: "2. Understand", C.LAYOUT["brief"]: "3. Brief", C.LAYOUT["pages"]: "The brief"}


def _write_listing(listing: list[tuple[str, str, str]], out_dir: str) -> str:
    groups: dict[str, list] = {}
    for rel, title, name in listing:
        parts = rel.split("/")
        top = "/".join(parts[:2]) if parts[0] == "data" and len(parts) > 2 else (parts[0] if len(parts) > 1 else "")
        groups.setdefault(STAGE_TITLES.get(top, "Estate"), []).append((rel, title, name))
    parts = ["<h1>Reports</h1>",
             "<p>Every other view and every report the stages wrote, to check a detail the brief counts.</p>",
             "<h2>Views</h2><ul>"]
    parts += [f'<li><a href="../{h}">{C.esc(t)}</a>: {C.esc(d)}.</li>' for h, t, d in TH.VIEWS]
    parts.append("</ul>")
    for g in sorted(groups, key=lambda k: (k != "The brief", k == "Estate", k)):
        parts.append(f"<h2>{C.esc(g)}</h2><ul>")
        parts += [f'<li><a href="{C.esc(name)}">{C.esc(title)}</a> <span class="muted"><code>{C.esc(rel)}</code></span></li>'
                  for rel, title, name in groups[g]]
        parts.append("</ul>")
    p = os.path.join(out_dir, "index.html")
    C.write_text(p, relink(TH.page("Reports", "\n".join(parts), depth=1, active=REPORTS + "/index.html"),
                           out_dir, out_dir))
    return p
