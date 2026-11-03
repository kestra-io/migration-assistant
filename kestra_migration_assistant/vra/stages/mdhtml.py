"""
Markdown to HTML for the reports the stages write, with no library and no network.

It covers what the stages' Markdown uses, and nothing else: ATX headings, paragraphs, bullet and
numbered lists (nested by indentation, with [ ] / [x] task boxes), pipe tables with an alignment row,
fenced code (a `mermaid` fence becomes a diagram), block quotes, horizontal rules, and inline code,
links, bold and italics. HTML in the source is shown as text, never run: a report can quote a vRO
name or a model-written sentence, and neither may inject markup. HTML comments are dropped (preflight's "why it matters" notes are for the Markdown reader).
"""
from __future__ import annotations

import html
import re
from typing import Any

_FENCE = re.compile(r"^(\s*)(```|~~~)\s*([A-Za-z0-9_-]*)\s*$")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_HR = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
_ITEM = re.compile(r"^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$")
_COMMENT = re.compile(r"<!--.*?-->", re.S)


def slug(text: str) -> str:
    s = re.sub(r"<[^>]+>", "", text)
    s = re.sub(r"[^\w\s-]", "", html.unescape(s)).strip().lower()
    return re.sub(r"[\s]+", "-", s) or "section"


# ------------------------------------------------------------------ inline

def inline(text: str) -> str:
    """Inline Markdown to HTML. Code spans first, so nothing inside them is touched."""
    out, pos = [], 0
    for m in re.finditer(r"(`+)(.+?)\1", text):
        out.append(_inline_plain(text[pos:m.start()]))
        out.append(f"<code>{html.escape(m.group(2).strip() if m.group(2).strip() else m.group(2))}</code>")
        pos = m.end()
    out.append(_inline_plain(text[pos:]))
    return "".join(out)


def _inline_plain(text: str) -> str:
    # escapes the reader meant literally: \| in table cells, \* and \_ around names
    text = re.sub(r"\\([\\`*_{}\[\]()#+\-.!|])", lambda m: f"\x00{ord(m.group(1))}\x00", text)
    links = []

    def link(m):
        label, url = m.group(1), m.group(2).strip()
        if url.startswith("<") and url.endswith(">"):
            url = url[1:-1]
        url = url.split(" ", 1)[0]
        if re.match(r"^\s*(javascript|vbscript|data):", url, re.I):
            return label
        links.append((label, url))
        return f"\x01{len(links) - 1}\x01"
    text = re.sub(r"\[([^\]]+)\]\(([^)\s]+(?:\s+\"[^\"]*\")?)\)", link, text)
    s = html.escape(text, quote=False)
    s = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<![\w*])\*(?=\S)(.+?)(?<=\S)\*(?![\w*])", r"<em>\1</em>", s)
    s = re.sub(r"(?<![\w_])_(?=\S)(.+?)(?<=\S)_(?![\w_])", r"<em>\1</em>", s)

    def relink(m):
        label, url = links[int(m.group(1))]
        return f'<a href="{html.escape(url, quote=True)}">{_inline_plain(label)}</a>'
    s = re.sub(r"\x01(\d+)\x01", relink, s)
    return re.sub(r"\x00(\d+)\x00", lambda m: html.escape(chr(int(m.group(1)))), s)


# ------------------------------------------------------------------ blocks

def _cells(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    cells, cur, i = [], "", 0
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s) and s[i + 1] == "|":
            cur += "\\|"
            i += 2
            continue
        if s[i] == "|":
            cells.append(cur.strip())
            cur = ""
        else:
            cur += s[i]
        i += 1
    cells.append(cur.strip())
    return cells


def _table(rows: list[str]) -> str:
    head, sep, body = _cells(rows[0]), _cells(rows[1]), [_cells(r) for r in rows[2:]]
    align = ["right" if c.endswith(":") and not c.startswith(":") else
             "center" if c.startswith(":") and c.endswith(":") else "" for c in sep]

    def cell(tag, i, v):
        a = align[i] if i < len(align) else ""
        style = f' style="text-align:{a}"' if a else ""
        return f"<{tag}{style}>{inline(v)}</{tag}>"
    out = ['<div class="scroll"><table>', "<thead><tr>" + "".join(cell("th", i, v) for i, v in enumerate(head)) + "</tr></thead>",
           "<tbody>"]
    for r in body:
        r = (r + [""] * len(head))[:max(len(head), len(r))]
        out.append("<tr>" + "".join(cell("td", i, v) for i, v in enumerate(r)) + "</tr>")
    out.append("</tbody></table></div>")
    return "\n".join(out)


def _list(lines: list[str]) -> str:
    """A list block; nesting follows indentation. Continuation lines join the item above."""
    items: list[list[Any]] = []                   # (indent, ordered, text)
    for ln in lines:
        m = _ITEM.match(ln)
        if m:
            items.append([len(m.group(1).expandtabs(4)), m.group(2)[0].isdigit(), m.group(3), m.group(2)])
        elif items:
            items[-1][2] += " " + ln.strip()
    out: list[str] = []
    stack: list[tuple[int, str]] = []             # stack of (indent, tag)

    def close_to(indent):
        while stack and stack[-1][0] > indent:
            out.append(f"</li></{stack.pop()[1]}>")
    for indent, ordered, text, marker in items:
        tag = "ol" if ordered else "ul"
        close_to(indent)
        if stack and stack[-1][0] == indent:
            out.append("</li>")
            if stack[-1][1] != tag:
                out.append(f"</{stack.pop()[1]}>")
        if not stack or stack[-1][0] < indent:
            start = f' start="{int(marker[:-1])}"' if ordered and marker[:-1] != "1" else ""
            box = re.match(r"^\[[ xX]\]\s", text)
            cls = ' class="tasks"' if box else ""
            out.append(f"<{tag}{start}{cls}>")
            stack.append((indent, tag))
        box = re.match(r"^\[([ xX])\]\s+(.*)$", text)
        if box:
            done = box.group(1).lower() == "x"
            out.append(f'<li class="task{" done" if done else ""}"><span class="box">{"&#x2611;" if done else "&#x2610;"}</span> '
                       + inline(box.group(2)))
        else:
            out.append("<li>" + inline(text))
    close_to(-1)
    return "".join(out)


def render(md: str) -> tuple[str, dict]:
    """(body HTML, info): info has the first heading as `title` and whether a Mermaid diagram is in it."""
    md = _COMMENT.sub("", md.replace("\r\n", "\n"))
    lines = md.split("\n")
    out: list[str] = []
    info: dict[str, Any] = {"title": None, "mermaid": False, "headings": []}
    ids: dict[str, int] = {}
    i = 0
    para: list[str] = []

    def flush():
        if para:
            out.append("<p>" + "<br>".join(inline(" ".join(x.strip() for x in chunk)) for chunk in _hard_breaks(para)) + "</p>")
            para.clear()
    while i < len(lines):
        ln = lines[i]
        fm = _FENCE.match(ln)
        if fm:
            flush()
            fence, lang = fm.group(2), fm.group(3).lower()
            code = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith(fence):
                code.append(lines[i])
                i += 1
            i += 1
            text = "\n".join(code)
            if lang == "mermaid":
                info["mermaid"] = True
                out.append(f'<pre class="mermaid">{html.escape(text, quote=False)}</pre>')
            else:
                out.append(f'<pre class="code">{html.escape(text, quote=False)}</pre>')
            continue
        if not ln.strip():
            flush()
            i += 1
            continue
        hm = _HEADING.match(ln)
        if hm:
            flush()
            level, text = len(hm.group(1)), hm.group(2)
            body = inline(text)
            hid = slug(text)
            ids[hid] = ids.get(hid, 0) + 1
            if ids[hid] > 1:
                hid = f"{hid}-{ids[hid]}"
            if info["title"] is None and level == 1:
                info["title"] = re.sub(r"<[^>]+>", "", html.unescape(body))
            info["headings"].append((level, hid, body))
            out.append(f'<h{level} id="{hid}">{body}</h{level}>')
            i += 1
            continue
        if _HR.match(ln) and not para:
            flush()
            out.append("<hr>")
            i += 1
            continue
        if ln.lstrip().startswith(">"):
            flush()
            quote = []
            while i < len(lines) and lines[i].lstrip().startswith(">"):
                quote.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            inner, _ = render("\n".join(quote))
            out.append(f"<blockquote>{inner}</blockquote>")
            continue
        if "|" in ln and i + 1 < len(lines) and _TABLE_SEP.match(lines[i + 1]) and "-" in lines[i + 1]:
            flush()
            rows = [ln, lines[i + 1]]
            i += 2
            while i < len(lines) and lines[i].strip() and "|" in lines[i]:
                rows.append(lines[i])
                i += 1
            out.append(_table(rows))
            continue
        if _ITEM.match(ln) and (not para or not ln.startswith(" ")):
            flush()
            block: list[str] = []
            while i < len(lines):
                cur = lines[i]
                if not cur.strip():
                    # a blank line ends the list unless the next line is another item or indented under one
                    nxt = lines[i + 1] if i + 1 < len(lines) else ""
                    if _ITEM.match(nxt) or (nxt.startswith("  ") and nxt.strip()):
                        i += 1
                        continue
                    break
                if block and not _ITEM.match(cur) and not cur.startswith((" ", "\t")) and (
                        _HEADING.match(cur) or _FENCE.match(cur) or cur.lstrip().startswith(">")):
                    break
                block.append(cur)
                i += 1
            out.append(_list(block))
            continue
        para.append(ln)
        i += 1
    flush()
    return "\n".join(out), info


def _hard_breaks(lines: list[str]) -> list[list[str]]:
    """Split a paragraph where a line ends with two spaces or a backslash (a Markdown hard break)."""
    chunks, cur = [], []
    for ln in lines:
        cur.append(ln.rstrip("\\").rstrip())
        if ln.endswith("  ") or ln.endswith("\\"):
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    return chunks
