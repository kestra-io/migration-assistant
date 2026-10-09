"""
One theme for every view. Every colour, font and spacing value lives in
assets/theme/tokens.json; this module holds none. It turns the tokens into
CSS custom properties (dark on screen, light when printed), the Mermaid
theme and node classes, and the page shell with the Kestra logo.

A colour means the same thing everywhere: one per triage outcome, one per
target system. Outcome colours are the colour-blind-safe categorical steps
and always appear with their name. Text on a coloured fill takes whichever of
the two diagram text colours has the higher contrast (`text_on`).
"""
from __future__ import annotations

import colorsys
import json
import os
import re

from . import common as C

TOKENS_PATH = os.path.join(C.ASSETS, "theme", "tokens.json")


def _load() -> dict:
    with open(TOKENS_PATH) as f:
        return json.load(f)


T = _load()
DARK, LIGHT = T["themes"]["dark"], T["themes"]["light"]
D = T["diagram"]
F, S = T["font"], T["space"]

OUTCOME = {k: v for k, v in T["outcome"].items() if not k.startswith("$")}
READINESS = dict(T["readiness"])
TARGET = {k: v for k, v in T["target"].items() if not k.startswith("_")}
TARGET_DEFAULT = T["target"]["_default"]
UNEXPORTED = T["unexported"]
ACCENT = DARK["accent_text"]


def target_colour(key: str) -> str:
    return TARGET.get(key, TARGET_DEFAULT)


# ------------------------------------------------------------------ contrast (WCAG 2.x)

def _rgb(c: str) -> tuple[float, ...]:
    c = c.strip()
    m = re.match(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", c)
    if m:
        return tuple(int(x) / 255 for x in m.groups())
    c = c.lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    return tuple(int(c[i:i + 2], 16) / 255 for i in (0, 2, 4))


def luminance(c: str) -> float:
    def lin(v):
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(v) for v in _rgb(c))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def text_on(fill: str) -> str:
    """The diagram text colour (near-black or white) with the higher contrast on `fill`."""
    dark, light = D["text_on_light_fill"], D["text_on_dark_fill"]
    return dark if contrast(dark, fill) >= contrast(light, fill) else light


def cluster_colours(ids: list[str]) -> dict[str, str]:
    """Golden-ratio hues, so neighbouring clusters stay apart. Generated, so not a token."""
    out = {}
    for i, cid in enumerate(sorted(ids)):
        h = (i * 0.618033988749895) % 1.0
        r, g, b = colorsys.hls_to_rgb(h, T["cluster_hues"]["lightness"], T["cluster_hues"]["saturation"])
        out[cid] = "#%02x%02x%02x" % (int(r * 255), int(g * 255), int(b * 255))
    return out


# ------------------------------------------------------------------ CSS

def _vars(theme: dict) -> str:
    names = {"bg": "bg", "raised": "panel", "raised_2": "panel-2", "code_bg": "code-bg", "border": "border",
             "border_strong": "border-strong", "text": "text", "text_strong": "text-strong",
             "text_secondary": "text-2", "text_muted": "muted", "accent_text": "accent", "link": "link",
             "link_hover": "link-hover", "fill": "fill", "on_fill": "on-fill", "highlight": "highlight",
             "status_ok": "ok", "status_wait": "wait", "status_bad": "bad"}
    return " ".join(f"--{css}:{theme[k]};" for k, css in names.items())


def _layout_vars() -> str:
    fv = {f"--fs-{k[5:]}": v for k, v in F.items() if k.startswith("size_")}
    fw = {f"--fw-{k[7:]}": v for k, v in F.items() if k.startswith("weight_")}
    sv = {f"--sp-{k}": v for k, v in S.items()}
    return " ".join(f"{k}:{v};" for k, v in {**fv, **fw, **sv}.items()) + \
        f" --font:{F['stack']}; --mono:{F['mono_stack']}; --lh:{F['line_height']};"


def css(up: str = "") -> str:
    return f"""
@font-face {{ font-family:"{F['family']}"; src:url("{up}{F['file']}") format("truetype");
  font-weight:200 900; font-style:normal; font-display:swap; }}
:root {{ {_vars(DARK)} {_layout_vars()} }}
@media print {{ :root {{ {_vars(LIGHT)} }} header.top {{ position:static; }} .logo-dark {{ display:none; }}
  .logo-light {{ display:inline-block !important; }} button.fit {{ display:none; }} }}
* {{ box-sizing:border-box; }}
html,body {{ margin:0; background:var(--bg); color:var(--text); font:var(--fs-base)/var(--lh) var(--font); }}
a {{ color:var(--link); text-decoration:none; }} a:hover {{ color:var(--link-hover); text-decoration:underline; }}
header.top {{ display:flex; flex-wrap:wrap; gap:var(--sp-xs) var(--sp-lg); align-items:center; padding:var(--sp-md) var(--sp-xl);
             border-bottom:var(--sp-border_width) solid var(--border); background:var(--panel); position:sticky; top:0; z-index:5; }}
header.top .brand {{ display:flex; align-items:center; gap:var(--sp-sm); color:var(--text-strong); font-weight:var(--fw-semibold); margin-right:var(--sp-sm); }}
header.top .brand img {{ height:var(--sp-logo_height); width:auto; display:inline-block; }}
header.top .brand .logo-light {{ display:none; }}
header.top .brand .product {{ color:var(--text-2); font-weight:var(--fw-medium); padding-left:var(--sp-sm); border-left:var(--sp-border_width) solid var(--border-strong); }}
header.top a {{ color:var(--text-2); font-size:var(--fs-small); }} header.top a.on {{ color:var(--accent); font-weight:var(--fw-semibold); }}
main {{ padding:var(--sp-xl); max-width:var(--sp-page_max_width); margin:0 auto; }}
h1 {{ font-size:var(--fs-h1); font-weight:var(--fw-semibold); margin:var(--sp-xs) 0 var(--sp-sm); color:var(--text-strong); }}
h2 {{ font-size:var(--fs-h2); font-weight:var(--fw-semibold); margin:var(--sp-xxl) 0 var(--sp-sm); color:var(--text-strong); }}
h3 {{ font-size:var(--fs-h3); font-weight:var(--fw-semibold); margin:var(--sp-md) 0 var(--sp-sm); color:var(--text-strong); }}
.sub, .muted {{ color:var(--text-2); font-size:var(--fs-small); }}
.panel {{ background:var(--panel); border:var(--sp-border_width) solid var(--border); border-radius:var(--sp-radius); padding:var(--sp-md) var(--sp-lg); }}
.grid {{ display:grid; gap:var(--sp-md); }}
.kpis {{ grid-template-columns:repeat(auto-fit,minmax(130px,1fr)); }}
.kpi .v {{ font-size:var(--fs-kpi); font-weight:var(--fw-semibold); color:var(--text-strong); }} .kpi .l {{ color:var(--text-2); font-size:var(--fs-xsmall); }}
.cards {{ grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); }}
.card h3 {{ margin-top:0; }} .card ul {{ padding-left:var(--sp-lg); margin:var(--sp-xs) 0; }} .card li {{ margin:var(--sp-xxs) 0; font-size:var(--fs-small); }}
.status {{ display:inline-block; font-size:var(--fs-tiny); padding:1px var(--sp-sm); border-radius:var(--sp-radius_pill); border:var(--sp-border_width) solid var(--border); }}
.status.done {{ color:var(--ok); border-color:var(--ok); }} .status.wait {{ color:var(--wait); border-color:var(--wait); }}
.status.todo {{ color:var(--text-2); }} .status.stale {{ color:var(--bad); border-color:var(--bad); }}
.badge {{ display:inline-flex; align-items:center; gap:var(--sp-xs); font-size:var(--fs-tiny); padding:1px var(--sp-sm); border-radius:var(--sp-radius_pill);
         border:var(--sp-border_width) solid var(--border); margin:0 var(--sp-xs) var(--sp-xxs) 0; color:var(--text); }}
.badge.big {{ font-size:var(--fs-h3); }}
.dot {{ display:inline-block; width:10px; height:10px; border-radius:5px; margin-right:var(--sp-xs); vertical-align:middle; flex:none; }}
table {{ border-collapse:collapse; width:100%; font-size:var(--fs-small); }}
th, td {{ text-align:left; padding:var(--sp-xs) var(--sp-sm); border-bottom:var(--sp-border_width) solid var(--border); vertical-align:top; }}
th {{ color:var(--text-2); font-weight:var(--fw-medium); }}
.scroll {{ overflow-x:auto; }}
pre, code {{ font-family:var(--mono); font-size:var(--fs-code); }}
pre.xml {{ white-space:pre-wrap; word-break:break-all; background:var(--code-bg); border:var(--sp-border_width) solid var(--border); border-radius:var(--sp-radius); padding:var(--sp-md); overflow:auto; max-height:460px; }}
pre.xml .hl {{ background:var(--highlight); display:block; }}
.mermaid {{ background:{D['background']}; border:var(--sp-border_width) solid var(--border); border-radius:var(--sp-radius); padding:var(--sp-md); overflow:auto; }}
.legend span {{ margin-right:var(--sp-lg); font-size:var(--fs-xsmall); white-space:nowrap; }}
article.report {{ max-width:var(--sp-report_max_width); }}
article.report blockquote {{ margin:var(--sp-sm) 0; padding:var(--sp-xs) var(--sp-md); border-left:3px solid var(--border-strong); color:var(--text-2); background:var(--panel); }}
article.report pre.code {{ white-space:pre-wrap; background:var(--code-bg); border:var(--sp-border_width) solid var(--border); border-radius:var(--sp-radius); padding:var(--sp-md); overflow:auto; }}
article.report ul.tasks {{ list-style:none; padding-left:var(--sp-md); }} article.report li.task .box {{ color:var(--text-2); }}
article.report li.task.done .box {{ color:var(--ok); }} article.report li {{ margin:var(--sp-xxs) 0; }}
article.report hr {{ border:0; border-top:var(--sp-border_width) solid var(--border); margin:var(--sp-lg) 0; }}
article.report .scroll {{ margin:var(--sp-sm) 0 var(--sp-md); }}
.headline .status {{ font-size:var(--fs-h3); padding:var(--sp-xs) var(--sp-md); }}
td.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
.note {{ border-left:3px solid var(--fill); padding:var(--sp-sm) var(--sp-md); background:var(--panel); font-size:var(--fs-small); }}
.side4 {{ grid-template-columns:repeat(auto-fit,minmax(320px,1fr)); }}
details summary {{ cursor:pointer; color:var(--text-2); }}
button.fit {{ background:var(--fill); color:var(--on-fill); border:0; border-radius:var(--sp-radius_sm);
             padding:var(--sp-xs) var(--sp-md); font:inherit; font-size:var(--fs-xsmall); font-weight:var(--fw-medium); cursor:pointer; margin:var(--sp-xs) 0; }}
pre.mermaid.fitted svg {{ max-width:100% !important; height:auto !important; max-height:85vh; }}
"""


# Scripts, styles, fonts and images come from these files only; nothing may leave the machine.
CSP = ("default-src 'none'; script-src 'self' 'unsafe-inline' file:; style-src 'unsafe-inline'; "
       "img-src 'self' file: data:; font-src 'self' file: data:; connect-src 'none'; base-uri 'none'; "
       "form-action 'none'")


def mermaid_init() -> str:
    tv = {"background": D["background"], "primaryColor": D["node"]["fill"], "primaryTextColor": D["node"]["text"],
          "primaryBorderColor": D["node"]["stroke"], "lineColor": D["line"], "textColor": D["node"]["text"],
          "fontFamily": F["stack"], "fontSize": F["diagram_size"], "clusterBkg": D["cluster_bg"],
          "clusterBorder": D["cluster_border"], "edgeLabelBackground": D["background"]}
    return """document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('pre.mermaid').forEach(pre => {
    const b = document.createElement('button');
    b.textContent = 'Fit to screen'; b.className = 'fit';
    b.onclick = () => { const on = pre.classList.toggle('fitted'); b.textContent = on ? 'Actual size' : 'Fit to screen'; };
    pre.parentNode.insertBefore(b, pre);
  });
});
mermaid.initialize({startOnLoad:true, theme:'base', securityLevel:'loose', maxTextSize:500000,
  maxEdges:2000, flowchart:{htmlLabels:true, curve:'basis', useMaxWidth:false},
  themeVariables:%s});""" % json.dumps(tv)


def class_def(name: str, key: str) -> str:
    """A Mermaid classDef from a diagram token (fill, stroke, text)."""
    d = D[key]
    return f"  classDef {name} fill:{d['fill']},stroke:{d['stroke']},color:{d['text']}"


def fill_class(name: str, fill: str, dashed: bool = False) -> str:
    """A Mermaid classDef for a data colour (outcome, target), text picked by contrast."""
    return (f"  classDef {name} fill:{fill},stroke:{D['background']},color:{text_on(fill)}"
            + (",stroke-dasharray:5 4" if dashed else ""))


# Hrefs as seen from report/; pages.relink points them at the real files (index.html, the brief, is one level
# up; a Markdown report opens as its rendered page).
NAV = [("index.html", "Brief"), ("vra.html", "vRA flow"), ("call-graph.html", "Call graph"),
       ("translation.html", "Translation"), ("waves.html", "Waves"), ("workflows/index.html", "Workflows")]
# the other views, linked from the reports page (and once from the brief's "Where to check")
VIEWS = [("architecture.html", "Architecture", "domains, clusters and the systems they call"),
         ("explorer.html", "Explorer", "zoom through every workflow, coloured by outcome or cluster"),
         ("plugins.html", "Plugins", "per system, the Kestra plugin and tasks, HTTP, or a gap, from a dated snapshot"),
         ("run.html", "The run", "what each stage did, and one workflow followed from its XML to its outcome")]


def logo_html(up: str) -> str:
    return (f'<img class="logo-dark" src="{up}vendor/{T["logo"]["on_dark"]}" alt="Kestra">'
            f'<img class="logo-light" src="{up}vendor/{T["logo"]["on_light"]}" alt="Kestra">')


def page(title: str, body: str, *, depth: int = 0, mermaid: bool = False, active: str = "") -> str:
    up = "../" * depth
    nav = "".join(f'<a href="{up}{h}" class="{"on" if h == active else ""}">{C.esc(t)}</a>' for h, t in NAV)
    if C.STAGES and os.path.exists(os.path.join(C.STAGES, "WALKTHROUGH.md")):
        nav += f'<a href="{up}../WALKTHROUGH.md">Walkthrough</a>'
    mm = ""
    if mermaid:
        mm = (f'<script src="{up}vendor/mermaid.min.js"></script>\n<script>\n' + mermaid_init() + "\n</script>")
    return C.no_em_dashes(f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="{CSP}">
<title>{C.esc(title)}</title>
<style>{css(up)}</style>
{mm}
</head><body>
<header class="top"><span class="brand">{logo_html(up)}<span class="product">vRO to Kestra</span></span>{nav}</header>
<main>
{body}
</main>
</body></html>
""")


def legend(items: dict[str, str], shape: str = "dot") -> str:
    return '<div class="legend">' + "".join(
        f'<span><span class="dot" style="background:{c};{"border-radius:2px" if shape == "square" else ""}"></span>{C.esc(k)}</span>'
        for k, c in items.items()) + "</div>"


def outcome_badge(outcome: str, big: bool = False) -> str:
    """Outcome colour as a dot and border; the name in body text colour, so it always passes AA."""
    c = OUTCOME[outcome]
    return (f'<span class="badge{" big" if big else ""}" style="border-color:{c}">'
            f'<span class="dot" style="background:{c}"></span>{C.esc(outcome)}</span>')


def mm_label(s) -> str:
    """Text safe inside a Mermaid "quoted" label."""
    s = "" if s is None else str(s)
    return (s.replace("&", "&amp;").replace('"', "#quot;").replace("<", "&lt;").replace(">", "&gt;")
             .replace("\n", " ").replace("|", "/").replace("`", "'").replace("[", "(").replace("]", ")")
             .replace("{", "(").replace("}", ")"))


def mm_id(s: str) -> str:
    return "n_" + re.sub(r"[^A-Za-z0-9_]", "_", s)


def explorer_theme() -> dict:
    """What the explorer's canvas and its CSS need, from the tokens."""
    return {"css_vars": _vars(DARK) + " " + _layout_vars(), "canvas": T["explorer"], "font": F["stack"],
            "font_file": F["file"]}
