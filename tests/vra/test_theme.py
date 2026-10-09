"""
The theme: one tokens file, WCAG AA contrast for text, and no colour anywhere else.
"""
from __future__ import annotations

import json
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from kestra_migration_assistant.vra.stages import theme as TH

AA, AA_LARGE = 4.5, 3.0
HEX = re.compile(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b|rgba?\(\s*\d")


@pytest.mark.parametrize("name", ["dark", "light"])
def test_text_pairs_meet_aa(name):
    t = TH.T["themes"][name]
    pairs = [(fg, bg) for fg in ("text", "text_strong", "text_secondary", "text_muted", "accent_text", "link")
             for bg in ("bg", "raised", "raised_2", "code_bg")]
    pairs.append(("on_fill", "fill"))
    bad = [(fg, bg, round(TH.contrast(t[fg], t[bg]), 2)) for fg, bg in pairs if TH.contrast(t[fg], t[bg]) < AA]
    assert not bad, f"{name}: below 4.5:1: {bad}"


def test_status_colours_meet_aa_on_their_theme():
    for name in ("dark", "light"):
        t = TH.T["themes"][name]
        for k in ("status_ok", "status_wait", "status_bad"):
            assert TH.contrast(t[k], t["bg"]) >= AA, (name, k)


def test_diagram_node_text_meets_aa():
    for key, d in TH.D.items():
        if isinstance(d, dict) and {"fill", "text"} <= set(d):
            assert TH.contrast(d["text"], d["fill"]) >= AA, key


@pytest.mark.parametrize("group", ["outcome", "target", "readiness"])
def test_text_on_every_data_colour_meets_aa(group):
    colours = {k: v for k, v in TH.T[group].items() if not k.startswith(("$", "_"))}
    bad = {k: round(TH.contrast(TH.text_on(c), c), 2) for k, c in colours.items() if TH.contrast(TH.text_on(c), c) < AA}
    assert not bad, bad


def test_outcome_colours_are_distinguishable_from_the_page():
    """Graphics (dots, borders, fills) need 3:1 against the page (WCAG 1.4.11); names always sit beside them."""
    bg = TH.DARK["bg"]
    bad = {k: round(TH.contrast(c, bg), 2) for k, c in TH.OUTCOME.items() if TH.contrast(c, bg) < AA_LARGE}
    assert not bad, bad


def test_brand_values_are_as_given():
    b = TH.T["brand"]
    assert (b["purple"], b["ink"], b["body"], b["muted"], b["border"], b["tint"], b["near_black"], b["lavender"]) == \
        ("#631BFF", "#26282D", "#5A5E67", "#8A8E97", "#E2E2E7", "#F8F6FF", "#0B0B0B", "#E2BCFF")
    d = TH.DARK
    assert (d["bg"], d["text_secondary"], d["accent_text"], d["link"], d["fill"]) == \
        ("#0B0B0B", "#B9BAC0", "#E2BCFF", "#E2BCFF", "#631BFF")
    assert "Mona Sans" in TH.F["stack"] and TH.F["stack"].endswith('system-ui, -apple-system, "Segoe UI", sans-serif')


def test_no_colour_outside_the_tokens_file():
    files = [os.path.join(ROOT, "kestra_migration_assistant", "vra", "stages", f) for f in ("theme.py", "visuals.py", "status.py")]
    viewer = open(os.path.join(ROOT, "kestra_migration_assistant", "vra", "assets", "explorer-viewer.html")).read()
    viewer = "\n".join(l for l in viewer.splitlines() if not l.startswith("var d3="))   # the vendored d3 bundle
    found = {os.path.basename(f): HEX.findall(open(f).read()) for f in files}
    found["explorer-viewer.html"] = HEX.findall(viewer)
    assert not {k: v for k, v in found.items() if v}, found


def test_font_and_logo_are_vendored_and_unmodified():
    a = os.path.join(ROOT, "kestra_migration_assistant", "vra", "assets")
    assert os.path.getsize(os.path.join(a, TH.F["file"])) > 100_000
    assert "SIL OPEN FONT LICENSE" in open(os.path.join(a, "vendor", "mona-sans", "OFL.txt")).read().upper()
    for v in ("on_dark", "on_light"):
        svg = open(os.path.join(a, TH.T["logo"][v])).read()
        assert svg.startswith("<svg") and 'viewBox="0 0 889 203"' in svg


def test_tokens_file_is_the_only_source():
    tokens = json.load(open(TH.TOKENS_PATH))
    assert set(tokens) >= {"brand", "themes", "outcome", "target", "diagram", "explorer", "font", "space", "logo"}
