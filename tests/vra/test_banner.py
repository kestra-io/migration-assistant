"""
The banner at the top of every interactive run (kestra_migration_assistant/banner.py): the KESTRA wordmark in a
purple-to-lavender gradient, then the product's name and version. Each fallback: 256 colors without COLORTERM, no
color with NO_COLOR, no escape code at all when the output is not a terminal, a plain KESTRA line when the terminal
is narrow or cannot encode the block letters; and nothing at all in batch mode or for a program.
"""
from __future__ import annotations

import io
import re

from kestra_migration_assistant import __version__, banner

ESC = re.compile(r"\x1b\[[0-9;]*m")


class Tty(io.StringIO):
    def __init__(self, encoding: str = "utf-8"):
        super().__init__()
        self._enc = encoding

    @property
    def encoding(self) -> str:          # type: ignore[override]
        return self._enc

    def isatty(self) -> bool:
        return True


def _plain(text: str) -> list[str]:
    return ESC.sub("", text).split("\n")


def test_the_wordmark_exactly_as_drawn_then_the_product_line_in_24_bit_color():
    text = banner.render("1.2.0", Tty(), {"COLORTERM": "truecolor"}, width=100)
    lines = _plain(text)
    assert lines[:6] == banner.WORDMARK and lines[6] == "" and lines[7] == f"{banner.PRODUCT} 1.2.0"
    assert lines[8:] == ["", ""]                                     # a blank line before the first screen
    first = text.split("\n")[0]
    assert first.startswith("\x1b[38;2;132;5;255m")                  # #8405FF on the left
    assert "\x1b[38;2;217;184;255m" in text                           # #D9B8FF on the right
    assert "\x1b[38;5;" not in text
    product = text.split("\n")[7]
    assert product == f"\x1b[1m{banner.PRODUCT}\x1b[0m 1.2.0"           # bold name, the terminal's own color
    # the same column has the same color on every line: a gradient across columns
    def color_at(line: str, col: int) -> str:
        code, i = "", 0
        for m in re.finditer(r"(\x1b\[[0-9;]*m)|(.)", line):
            if m.group(1):
                code = m.group(1)
            else:
                if i == col:
                    return code
                i += 1
        return ""
    rows = text.split("\n")[:6]
    assert color_at(rows[0], 0) == color_at(rows[5], 0) and color_at(rows[0], 0) != color_at(rows[0], 47)


def test_without_colorterm_the_nearest_256_colors():
    text = banner.render("1.2.0", Tty(), {}, width=100)
    assert "\x1b[38;2;" not in text and "\x1b[38;5;93m" in text      # #8405FF is nearest to 93
    assert banner.nearest_256((0xD9, 0xB8, 0xFF)) == 183
    assert _plain(text)[:6] == banner.WORDMARK


def test_no_color_keeps_the_letters_and_the_bold_name_but_no_color():
    text = banner.render("1.2.0", Tty(), {"NO_COLOR": "1", "COLORTERM": "truecolor"}, width=100)
    assert "\x1b[38;" not in text and text.split("\n")[:6] == banner.WORDMARK
    assert f"\x1b[1m{banner.PRODUCT}\x1b[0m 1.2.0" in text           # bold is not color


def test_output_that_is_not_a_terminal_has_no_escape_code():
    text = banner.render("1.2.0", io.StringIO(), {"COLORTERM": "truecolor"}, width=100)
    assert "\x1b" not in text and text.split("\n")[:8] == [*banner.WORDMARK, "", f"{banner.PRODUCT} 1.2.0"]


def test_a_narrow_terminal_gets_the_plain_line():
    for width in (banner.MIN_WIDTH - 1, 40):
        lines = _plain(banner.render("1.2.0", Tty(), {"COLORTERM": "truecolor"}, width=width))
        assert lines[:3] == ["KESTRA", "", f"{banner.PRODUCT} 1.2.0"]
    assert _plain(banner.render("1.2.0", Tty(), {}, width=banner.MIN_WIDTH))[:6] == banner.WORDMARK


def test_a_terminal_that_cannot_encode_the_block_letters_gets_the_plain_line():
    lines = _plain(banner.render("1.2.0", Tty("ascii"), {}, width=100))
    assert lines[:3] == ["KESTRA", "", f"{banner.PRODUCT} 1.2.0"]
    assert _plain(banner.render("1.2.0", Tty("latin-1"), {}, width=100))[0] == "KESTRA"


def test_the_version_is_the_packages(monkeypatch):
    out = Tty()
    monkeypatch.setenv("COLUMNS", "100")
    banner.show(out)
    assert f"{banner.PRODUCT} {__version__}" in _plain(out.getvalue())


def test_only_the_interactive_run_prints_it(tmp_path, monkeypatch):
    from test_interactive import _export, _run, _typed_run
    export = _export(tmp_path)
    _code, out = _run(monkeypatch, export, None)                     # a program calling the command
    assert banner.PRODUCT not in out and "██" not in out
    _code, out = _run(monkeypatch, export, "", "--batch")            # typed, with --batch
    assert banner.PRODUCT not in out
    _code, out, _p = _typed_run(monkeypatch, export, [""])           # typed in a terminal: first of all
    assert out.startswith(banner.WORDMARK[0] + "\n") and f"\n\n{banner.PRODUCT} {__version__}\n\n" in out
