"""
The banner at the top of every interactive run: the KESTRA wordmark in block letters, then the product's name and
version.

    ██╗  ██╗███████╗...      a left-to-right gradient from Kestra's purple to a light lavender
                             (24-bit color when COLORTERM says the terminal has it, the nearest of the 256 colors else)
    Migration Assistant for VMware vRA 1.2.0     the terminal's own color, the product's name in bold

No color when NO_COLOR is set, and no escape codes at all when the output is not a terminal. A plain `KESTRA` line
instead of the block letters when the terminal is narrower than MIN_WIDTH or cannot encode them. Only the
interactive run prints it: batch mode and a program calling the command print nothing extra.
"""
from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Mapping
from typing import TextIO

from kestra_migration_assistant import __version__

WORDMARK = """\
██╗  ██╗███████╗███████╗████████╗██████╗  █████╗
██║ ██╔╝██╔════╝██╔════╝╚══██╔══╝██╔══██╗██╔══██╗
█████╔╝ █████╗  ███████╗   ██║   ██████╔╝███████║
██╔═██╗ ██╔══╝  ╚════██║   ██║   ██╔══██╗██╔══██║
██║  ██╗███████╗███████║   ██║   ██║  ██║██║  ██║
╚═╝  ╚═╝╚══════╝╚══════╝   ╚═╝   ╚═╝  ╚═╝╚═╝  ╚═╝""".split("\n")
PLAIN = "KESTRA"
PRODUCT = "Migration Assistant for VMware vRA"
MIN_WIDTH = 52                          # columns: narrower, and the plain line is shown instead
START, END = (0x84, 0x05, 0xFF), (0xD9, 0xB8, 0xFF)      # Kestra's purple, a light lavender
BOLD, RESET = "\033[1m", "\033[0m"


def color_mode(out: TextIO, env: Mapping[str, str]) -> str:
    """"none", "256" or "24bit"."""
    if not getattr(out, "isatty", lambda: False)() or env.get("NO_COLOR"):
        return "none"
    return "24bit" if env.get("COLORTERM", "").lower() in ("truecolor", "24bit") else "256"


def encodable(out: TextIO) -> bool:
    """The output can encode the block letters."""
    try:
        "".join(WORDMARK).encode(getattr(out, "encoding", None) or "utf-8")
    except (UnicodeEncodeError, LookupError):
        return False
    return True


LEVELS = (0, 95, 135, 175, 215, 255)    # the 6x6x6 cube of the 256 colors


def _level(c: int) -> int:
    return min(range(6), key=lambda i: abs(LEVELS[i] - c))


def nearest_256(rgb: tuple[int, int, int]) -> int:
    """The nearest of the 256 colors: the color cube or the grey ramp, whichever is closer."""
    r, g, b = (_level(c) for c in rgb)
    cube = (LEVELS[r], LEVELS[g], LEVELS[b])
    grey_i = min(23, max(0, round((sum(rgb) / 3 - 8) / 10)))
    grey = (8 + 10 * grey_i,) * 3

    def dist(x: tuple[int, ...]) -> int:
        return sum((p - q) ** 2 for p, q in zip(rgb, x, strict=True))
    return 232 + grey_i if dist(grey) < dist(cube) else 16 + 36 * r + 6 * g + b


def _at(i: int, n: int) -> tuple[int, int, int]:
    t = i / max(1, n - 1)
    return (round(START[0] + (END[0] - START[0]) * t), round(START[1] + (END[1] - START[1]) * t),
            round(START[2] + (END[2] - START[2]) * t))


def _code(rgb: tuple[int, int, int], mode: str) -> str:
    if mode == "24bit":
        return f"\033[38;2;{rgb[0]};{rgb[1]};{rgb[2]}m"
    return f"\033[38;5;{nearest_256(rgb)}m"


def gradient(lines: list[str], mode: str) -> list[str]:
    """Each line colored column by column, the same column the same color on every line; one code per run of the
    same color, and spaces left as they are."""
    if mode == "none":
        return list(lines)
    n = max(len(x) for x in lines)
    out = []
    for line in lines:
        s, last = "", None
        for i, ch in enumerate(line):
            if ch != " ":
                code = _code(_at(i, n), mode)
                if code != last:
                    s += code
                    last = code
            s += ch
        out.append(s + RESET)
    return out


def render(version: str, out: TextIO, env: Mapping[str, str] | None = None, width: int | None = None) -> str:
    """The banner as printed: the wordmark (or the plain line), a blank line, the product line, a blank line."""
    env = os.environ if env is None else env
    width = width if width is not None else shutil.get_terminal_size((80, 24)).columns
    mode = color_mode(out, env)
    mark = WORDMARK if width >= MIN_WIDTH and encodable(out) else [PLAIN]
    bold = getattr(out, "isatty", lambda: False)()          # bold is not color: NO_COLOR keeps it
    name = f"{BOLD}{PRODUCT}{RESET}" if bold else PRODUCT
    return "\n".join([*gradient(mark, mode), "", f"{name} {version}", "", ""])


def show(out: TextIO | None = None) -> None:
    out = out or sys.stdout
    print(render(__version__, out), end="", file=out, flush=True)
