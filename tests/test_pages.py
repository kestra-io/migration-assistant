"""
The estate layout, the report pages, and the Markdown reports rendered as pages.

    stages/<estate>/index.html, pages/, 0-preflight/, 1-assess/, 2-understand/, 3-brief/
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from vro2k_assess.stages import common as C, mdhtml, pages as PG  # noqa: E402

LINK = re.compile(r'''(?:href|src)=["']([^"']+)["']|click\s+\S+\s+href\s+"([^"]+)"|url\(["']?([^"')]+)''')


# ------------------------------------------------------------------ Markdown

def test_markdown_blocks():
    md = ("# Title\n\nA *para* with `code | pipe` and **bold**.\n\n## Two\n\n| A | B |\n|---|--:|\n| x \\| y | 2 |\n\n"
          "- one\n  - nested\n- [x] done\n- [ ] open\n\n1. first\n2. second\n\n> quoted\n\n```\nraw <b>\n```\n\n---\n")
    body, info = mdhtml.render(md)
    assert info["title"] == "Title" and not info["mermaid"]
    assert '<h1 id="title">Title</h1>' in body and '<h2 id="two">' in body
    assert "<em>para</em>" in body and "<strong>bold</strong>" in body and "<code>code | pipe</code>" in body
    assert "<td>x | y</td>" in body and 'style="text-align:right"' in body
    assert body.count("<ul") == 2 and "<ol>" in body and "&#x2611;" in body and "&#x2610;" in body
    assert "<blockquote><p>quoted</p></blockquote>" in body
    assert '<pre class="code">raw &lt;b&gt;</pre>' in body and "<hr>" in body


def test_markdown_never_passes_html_through():
    body, _ = mdhtml.render("<script>alert(1)</script>\n\n[x](javascript:alert(1)) <img src=x onerror=y>\n\n"
                            "<!-- approval:start -->\nkept\n<!-- approval:end -->")
    assert "<script" not in body and "<img" not in body and "javascript:" not in body
    assert "approval:start" not in body and "kept" in body


def test_mermaid_fence_becomes_a_diagram():
    body, info = mdhtml.render("```mermaid\nflowchart LR\n  A --> B\n```")
    assert info["mermaid"] and '<pre class="mermaid">flowchart LR\n  A --&gt; B</pre>' in body


def test_links_are_kept_for_relinking():
    body, _ = mdhtml.render("See [the brief](../3-brief/BRIEF.md#2-recommendation) and [csv](triage.csv).")
    assert 'href="../3-brief/BRIEF.md#2-recommendation"' in body and 'href="triage.csv"' in body


# ------------------------------------------------------------------ links

def test_relink_points_reports_at_their_pages_and_the_overview_up(tmp_path):
    est = tmp_path / "e"
    pages = est / "pages"
    mapping = {str(est / "3-brief" / "BRIEF.md"): str(pages / "reports" / "brief.html"),
               str(pages / "index.html"): str(est / "index.html")}
    page = ('<a href="../3-brief/BRIEF.md#x">b</a><a href="index.html">o</a><a href="waves.html">w</a>'
            '<a href="https://example.com/">ext</a><a href="#top">t</a><img src="vendor/brand/a.svg">'
            '<style>@font-face{src:url("vendor/f.ttf")}</style>\n  click n1 href "workflows/a.html"')
    # drawn for pages/, written to the estate folder (the overview)
    out = PG.relink(page, str(pages), str(est), mapping)
    assert 'href="pages/reports/brief.html#x"' in out and 'href="index.html"' in out
    assert 'href="pages/waves.html"' in out and 'src="pages/vendor/brand/a.svg"' in out
    assert 'url("pages/vendor/f.ttf")' in out and 'href "pages/workflows/a.html"' in out
    assert 'href="https://example.com/"' in out and 'href="#top"' in out
    # drawn for pages/workflows/, written there
    wf = PG.relink('<a href="../index.html">o</a><a href="../../3-brief/BRIEF.md">b</a>',
                   str(pages / "workflows"), str(pages / "workflows"), mapping)
    assert 'href="../../index.html"' in wf and 'href="../reports/brief.html"' in wf


# ------------------------------------------------------------------ the layout, on a sample

@pytest.fixture(scope="module")
def avi(sample_stages):
    stages = sample_stages("avi-sample")
    src = os.path.join(os.path.dirname(os.path.dirname(stages)), "sources", "avi-sample")
    C.configure("avi-sample", source_dir=src, stages_dir=stages)
    from vro2k_assess.stages import status, visuals
    os.environ["VRO_NO_MODEL"] = "1"
    try:
        visuals.run()
        status.run()
    finally:
        os.environ.pop("VRO_NO_MODEL", None)
    return stages


def test_estate_layout(avi):
    names = set(os.listdir(avi))
    assert {"index.html", "your-input", "report", "data"} <= names
    assert not names & {"specs", "2-brief", "visuals", "pages", "0-preflight", "3-brief"}
    assert {"0-preflight", "1-assess", "2-understand", "3-brief"} <= set(os.listdir(os.path.join(avi, "data")))
    assert {"architecture.html", "waves.html", "translation.html", "run.html", "reports", "workflows", "vendor",
            "BRIEF.md"} <= set(os.listdir(os.path.join(avi, "report")))
    # what the customer needs, nothing more: the Avi sample has no vRA export, so entry-points.yaml is there too
    assert set(os.listdir(os.path.join(avi, "your-input"))) == {"answers.yaml", "entry-points.yaml"}
    assert os.path.exists(os.path.join(avi, "data", "2-understand", "cluster-summaries.json"))


def test_every_markdown_report_is_rendered(avi):
    reports = os.path.join(avi, "report", "reports")
    for md in PG.report_sources():
        page = PG.report_pages()[os.path.normpath(md)]
        assert os.path.exists(page), md
        text = open(page).read()
        assert "Rendered from" in text and "Content-Security-Policy" in text
    assert {"brief.html", "readiness.html", "estate-report.html", "target-inventory.html",
            "open-questions.html", "index.html"} <= set(os.listdir(reports))
    # the wave diagram is drawn, offline
    waves = open(os.path.join(avi, "report", "waves.html")).read()
    assert '<pre class="mermaid">' in waves and 'src="vendor/mermaid.min.js"' in waves


def test_every_link_in_every_page_resolves(avi):
    n = 0
    for d, _, files in os.walk(avi):
        if "sanitized" in d:
            continue
        for f in files:
            if not f.endswith(".html"):
                continue
            p = os.path.join(d, f)
            for m in LINK.finditer(open(p, encoding="utf-8").read()):
                u = next(x for x in m.groups() if x)
                if re.match(r"^[a-z]+:", u) or u.startswith("#") or "{" in u:
                    continue
                path = u.split("#")[0].split("?")[0]
                if path:
                    n += 1
                    assert os.path.exists(os.path.normpath(os.path.join(d, path))), f"{os.path.relpath(p, avi)} -> {u}"
    assert n > 100


def test_nav_and_index_open_the_rendered_reports(avi):
    idx = open(os.path.join(avi, "index.html")).read()
    assert 'href="report/reports/brief.html"' in idx and 'href="report/waves.html"' in idx
    assert ".md\"" not in idx          # every report link opens the rendered page
    wave = open(os.path.join(avi, "report", "waves.html")).read()
    assert 'href="call-graph.html"' in wave and 'href="../index.html"' in wave


# ------------------------------------------------------------------ an estate with the old layout

def test_old_layout_gets_a_message_not_a_crash(tmp_path, monkeypatch, capsys):
    from vro2k_assess import cli
    src = tmp_path / "sources" / "old"
    shutil.copytree(os.path.join(ROOT, "tests", "conformance", "helpers", "vro"), src / "vro")
    stages = tmp_path / "stages"
    (stages / "old" / "specs").mkdir(parents=True)
    (stages / "old" / "specs" / "x.json").write_text("{}")
    (stages / "old" / "2-brief").mkdir()
    (stages / "old" / "2-brief" / "BRIEF.md").write_text("signed")
    (stages / "old" / "visuals").mkdir()
    monkeypatch.setattr(C, "SOURCES_ROOT", str(tmp_path / "sources"))
    monkeypatch.setattr(C, "STAGES_ROOT", str(stages))
    rc = cli.main(["brief", "--estate", "old", "--no-model"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "older layout" in err and "mv " in err and "2-understand" in err and "3-brief" in err and "rm -r" in err
    # nothing was moved, deleted or written
    assert (stages / "old" / "specs" / "x.json").exists() and (stages / "old" / "2-brief" / "BRIEF.md").read_text() == "signed"
    assert sorted(os.listdir(stages / "old")) == ["2-brief", "specs", "visuals"]


def test_use_sample_keeps_the_export_alone_and_seeds_the_stages(tmp_path, monkeypatch):
    from vro2k_assess import cli
    monkeypatch.setattr(C, "SOURCES_ROOT", str(tmp_path / "sources"))
    monkeypatch.setattr(C, "STAGES_ROOT", str(tmp_path / "stages"))
    assert cli.use_sample("nuage", force=False) == 0
    src = tmp_path / "sources" / "nuage-sample"
    assert not (src / "model-output").exists() and not (src / "WALKTHROUGH.md").exists()
    st = tmp_path / "stages" / "nuage-sample"
    d = st / "data"
    assert (d / "2-understand" / "PROVENANCE.json").exists() and (d / "3-brief" / "walkthroughs.json").exists()
    assert (d / "1-assess" / "domain-names.json").exists() and (st / "WALKTHROUGH.md").exists()
    # never over a file already there
    (d / "2-understand" / "PROVENANCE.json").write_text("mine")
    assert cli.use_sample("nuage", force=True) == 0
    assert (d / "2-understand" / "PROVENANCE.json").read_text() == "mine"


def test_a_2c_estate_is_moved_and_its_answers_carried_into_answers_yaml(tmp_path, monkeypatch, capsys):
    """An estate written with the 2c/2d layout: the stage folders move into data/, the pages are regenerated in
    report/, and every answer, decision, review and the signature lands in your-input/answers.yaml."""
    import yaml
    from vro2k_assess import cli
    from vro2k_assess.stages import preflight as P
    src = tmp_path / "sources" / "old"
    helpers = os.path.join(ROOT, "tests", "conformance", "helpers")
    shutil.copytree(os.path.join(helpers, "vro"), src / "vro")
    shutil.copytree(os.path.join(helpers, "vra"), src / "helpers-vra")
    stages = tmp_path / "stages"
    old = stages / "old"
    (old / "2-understand").mkdir(parents=True)
    for f in os.listdir(os.path.join(helpers, "specs")):
        shutil.copy(os.path.join(helpers, "specs", f), old / "2-understand" / f.replace("helpers-", "vro-"))
    (old / "0-preflight").mkdir()
    (old / "0-preflight" / "answers.md").write_text(
        "# Preflight answers\n\nAnswered by: a stand-in\nDate: 2026-09-30\n\n### Q3. old wording\n\nAnswer: a lab "
        "for each system\n\n### Q9. old\n\nAnswer: replace\n")
    (old / "3-brief").mkdir()
    (old / "3-brief" / "decisions.yaml").write_text(yaml.safe_dump({"decisions": {
        "edition": {"question": "q", "decision": "available", "decided_by": "a stand-in", "date": "2026-09-30",
                    "comment": "EE"}}}))
    (old / "3-brief" / "BRIEF.md").write_text("<!-- approval:start -->\n```\nApproved by: a stand-in  Date: 2026-09-30\n"
                                              "Approval covers: [ ] Wave 1 only   [x] Full plan\n```\n<!-- approval:end -->\n")
    (old / "3-brief" / "overrides.yaml").write_text("outcomes:\n  x:\n    outcome: Retire\n    reason: r\n")
    (old / "pages").mkdir()
    monkeypatch.setattr(C, "SOURCES_ROOT", str(tmp_path / "sources"))
    monkeypatch.setattr(C, "STAGES_ROOT", str(stages))
    assert cli.main(["all", "--estate", "old", "--no-model"]) == 0
    out = capsys.readouterr().out
    assert "2c layout" in out and "moved 0-preflight/ to data/0-preflight/" in out and "carried what a person wrote" in out
    names = set(os.listdir(old))
    assert {"data", "report", "your-input", "index.html"} <= names and not names & {"0-preflight", "3-brief", "pages"}
    doc = yaml.safe_load(open(old / "your-input" / "answers.yaml"))
    from vro2k_assess.stages import your_input as Y
    flat = Y.flat(doc)
    assert flat[P.answer_key("Q3")]["answer"] == "a lab for each system"
    assert flat[P.answer_key("Q9")]["answer"] == "replace"
    assert flat["edition"]["answer"] == "available"
    assert doc["signed_for"] == "full plan"
    assert yaml.safe_load(open(old / "your-input" / "corrections.yaml"))["outcomes"]["x"]["outcome"] == "Retire"
    assert (old / "data" / "0-preflight" / "answers.md.migrated").exists()
    assert (old / "data" / "3-brief" / "decisions.yaml.migrated").exists()
    b = json.load(open(old / "data" / "3-brief" / "brief.json"))
    assert b["approval"]["signed"] and b["approval"]["scope"] == "full"
    # a second run carries nothing again and changes no answer
    before = open(old / "your-input" / "answers.yaml").read()
    assert cli.main(["all", "--estate", "old", "--no-model"]) == 0
    assert "carried what a person wrote" not in capsys.readouterr().out
    assert yaml.safe_load(before) == yaml.safe_load(open(old / "your-input" / "answers.yaml").read())
