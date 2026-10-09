"""
The report pages, for a platform lead who will not read the reports: stages/<estate>/index.html (the
overview) and everything else in stages/<estate>/pages/, including every Markdown report rendered as a
page (stages/pages.py).

Code draws, the model names: every node, edge, size and count below comes from
assess.json and brief.json, which come from the parsed export. The only
model-written text is what those files already mark as such (cluster summary
names, domain names, spec purposes, walkthrough text), and each page says so
where it shows it.

Everything works offline. Mermaid is copied from plugin/assets/vendor/ into
pages/vendor/ with its licence, and every page carries a CSP that
forbids network requests.
"""
from __future__ import annotations

import html
import json
import os
import re
import shutil
from collections import Counter, defaultdict

from . import common as C
from . import pages as PG
from . import theme as TH


def _out() -> str:
    C.require_estate()
    return C.PAGES


# uid -> (display name, short name), set by run() from assess.json: pages show names, never workflow IDs
_NAMES: dict[str, tuple[str, str]] = {}


def _set_names(assess) -> None:
    _NAMES.clear()
    _NAMES.update({u: (w["name"], w.get("short") or u) for u, w in assess["workflows"].items()})


def nm(u: str | None) -> str:
    return (_NAMES.get(u) or (u or "", u or ""))[0]


def href(u: str, prefix: str = "workflows/") -> str:
    return f"{prefix}{(_NAMES.get(u) or (u, u))[1]}.html"


def _load():
    assess = C.read_json(os.path.join(C.ASSESS, "assess.json"))
    brief = C.read_json(os.path.join(C.BRIEF, "brief.json"))
    pre = C.read_json(os.path.join(C.PRE, "preflight.json"))
    return assess, brief, pre


def _crit_cls(e: dict) -> str:
    return {"done": "done", "blocked": "stale"}.get(e.get("status"), "wait")


def _vendor():
    dst = os.path.join(_out(), "vendor")
    C.ensure(dst)
    src = os.path.join(C.ASSETS, "vendor", "mermaid")
    shutil.copy2(os.path.join(src, "mermaid.min.js"), os.path.join(dst, "mermaid.min.js"))
    shutil.copy2(os.path.join(src, "LICENSE"), os.path.join(dst, "LICENSE-mermaid.txt"))
    a = os.path.join(C.ASSETS, "vendor", "anthropic-code-modernization", "LICENSE")
    shutil.copy2(a, os.path.join(dst, "LICENSE-anthropic-code-modernization.txt"))
    # The theme's font and the Kestra logo, so pages render the brand with no network.
    for sub in ("mona-sans", "brand"):
        src_dir = os.path.join(C.ASSETS, "vendor", sub) if sub == "mona-sans" else os.path.join(C.ASSETS, sub)
        shutil.copytree(src_dir, os.path.join(dst, sub), dirs_exist_ok=True)
    C.write_text(os.path.join(dst, "README.txt"),
                 "Third-party code used by these pages, kept here so they open with no network.\n\n"
                 "mermaid.min.js      Mermaid 11.17.2, MIT licence (LICENSE-mermaid.txt).\n"
                 "explorer.html       adapted from Anthropic's code-modernization plugin\n"
                 "                    assets/topology-viewer.html, Apache 2.0\n"
                 "                    (LICENSE-anthropic-code-modernization.txt). It inlines a d3 v7\n"
                 "                    subset, ISC licence, Copyright Mike Bostock.\n"
                 "mona-sans/          Mona Sans variable font v2.0.27, SIL Open Font License 1.1\n"
                 "                    (mona-sans/OFL.txt), unmodified.\n"
                 "brand/              the official Kestra logo SVGs, unmodified.\n")


def _write(rel: str, text: str, mapping: dict | None = None) -> str:
    if rel == "index.html":
        return PG.write_index(text, mapping)
    return PG.write(rel, text, mapping)


def palette(assess) -> dict:
    return {"outcome": TH.OUTCOME, "readiness": TH.READINESS, "target": TH.TARGET,
            "cluster": TH.cluster_colours([c["cluster_id"] for c in assess["clusters"]])}


def _cluster_outcome(members, tri):
    c = Counter(tri[u]["outcome"] for u in members)
    return c.most_common(1)[0][0]


# ------------------------------------------------------------------ run

def run() -> list[str]:
    C.ensure(_out(), os.path.join(_out(), "workflows"))
    _vendor()
    assess, brief, pre = _load()
    _set_names(assess)
    m = PG.link_map()
    written = [
        _write("architecture.html", architecture(assess, brief), m),
        _write("call-graph.html", call_graph(assess, brief), m),
        _write("waves.html", waves(assess, brief), m),
        _write("explorer.html", explorer(assess, brief), m),
        _write("vra.html", vra_view(assess, brief), m),
        _write("plugins.html", plugins_view(assess, brief), m),
    ]
    keep = {href(u, "") for u in assess["workflows"]} | {"index.html"}
    for f in os.listdir(os.path.join(_out(), "workflows")):      # a workflow no longer in the export loses its page
        if f.endswith(".html") and f not in keep:
            os.remove(os.path.join(_out(), "workflows", f))
    for u in assess["workflows"]:
        written.append(_write(href(u), workflow_page(u, assess, brief), m))
    written.append(_write("workflows/index.html", workflows_index(assess, brief), m))
    written.append(_write("translation.html", translation_page(assess, brief), m))
    written.append(_write("run.html", run_page(assess, brief, pre), m))
    written += PG.render_reports(m)
    written.append(_write("index.html", index(assess, brief, pre), m))
    return written


def index(assess, brief, pre) -> str:
    """The front page: the brief (front.py)."""
    from . import front
    _set_names(assess)
    return front.page(assess, brief, pre)


def translation_page(assess, brief) -> str:
    from . import front
    return front.translation_page(assess, brief)


def workflows_index(assess, brief) -> str:
    from . import front
    return front.workflows_index(assess, brief)


# ------------------------------------------------------------------ architecture

def architecture(assess, brief) -> str:
    wf, tri = assess["workflows"], brief["triage"]
    targets = assess["targets"]
    L = ["flowchart LR"]
    for o, c in TH.OUTCOME.items():
        L.append(TH.fill_class(f"o_{re.sub('[^A-Za-z]', '', o)}", c))
    L.append(TH.class_def("unexported", "unexported") + ",stroke-dasharray:5 4")
    for k in targets:
        L.append(TH.fill_class(f"t_{re.sub('[^A-Za-z]', '', k)}", TH.target_colour(k)))
    for d in assess["domains"]["domains"]:
        L.append(f'  subgraph {d["id"]}["{TH.mm_label(d["name"])}"]')
        L.append("    direction LR")
        for cid in d["clusters"]:
            c = next(x for x in assess["clusters"] if x["cluster_id"] == cid)
            oc = _cluster_outcome(c["members"], tri)
            L.append(f'    {cid}["{cid}: {TH.mm_label(c["summary_name"] or "")}<br/>{c["size"]} workflow(s)"]'
                     f':::o_{re.sub("[^A-Za-z]", "", oc)}')
        L.append("  end")
    L.append('  subgraph TS["Target systems"]')
    L.append("    direction TB")
    for k, t in targets.items():
        L.append(f'    {TH.mm_id(k)}[("{TH.mm_label(t["name"])}<br/>Kestra: {t["reach"]}")]:::t_{re.sub("[^A-Za-z]", "", k)}')
    L.append("  end")
    # unexported workflows, grouped by kind and what they reach: one dashed node per group
    def ux_group(x):
        return f"{x['kind']}|{','.join(x['reaches']) or 'unknown'}"
    groups = defaultdict(set)
    for x in assess["unexported_calls"]:
        groups[ux_group(x)].add(x["workflow_id"])
    L.append('  subgraph UX["Not in the export"]')
    L.append("    direction TB")
    for g, ids in sorted(groups.items()):
        kind, reach = g.split("|")
        rname = ", ".join(targets[r]["name"] if r in targets else r for r in reach.split(","))
        L.append(f'    {TH.mm_id("ux_" + g)}["{len(ids)} {TH.mm_label(kind)}(s)<br/>reaching {TH.mm_label(rname)}"]:::unexported')
    L.append("  end")
    # edges: cluster -> target, labelled with the number of distinct operations
    link_styles = []
    idx = 0
    for c in assess["clusters"]:
        per = defaultdict(set)
        for u in c["members"]:
            for o in wf[u]["ops"]:
                per[o["target"]].add(o["op"])
        for t, ops in sorted(per.items()):
            L.append(f'  {c["cluster_id"]} -->|"{len(ops)} op{"s" if len(ops) != 1 else ""}"| {TH.mm_id(t)}')
            link_styles.append(f"  linkStyle {idx} stroke:{TH.target_colour(t)},stroke-width:1.5px")
            idx += 1
    seen = set()
    for x in assess["unexported_calls"]:
        k = (wf[x["caller"]]["cluster"], ux_group(x))
        if k in seen:
            continue
        seen.add(k)
        L.append(f'  {k[0]} -.-> {TH.mm_id("ux_" + k[1])}')
        link_styles.append(f"  linkStyle {idx} stroke:{TH.UNEXPORTED},stroke-dasharray:4 4")
        idx += 1
    L += link_styles
    src = "\n".join(L)
    body = f"""
<h1>Architecture</h1>
<p>Which areas the estate's workflows fall into, and which systems each one calls.</p>
<p class="sub">Domains as boxes holding their clusters. Target systems on the right. Each arrow is a cluster calling a
target, labelled with how many distinct operations its workflows use. Dashed red nodes are workflows the estate
calls that were never exported, grouped by kind and by the system they reach (listed in full below). Clusters are coloured by the brief outcome most of their workflows got.</p>
{TH.legend(TH.OUTCOME)}
{TH.legend({v["name"]: TH.target_colour(k) for k, v in targets.items()}, "square")}
<p class="sub">Drawn from <code>1-assess/assess.json</code> and <code>3-brief/brief.json</code>.
Domain and cluster names are model-written from the cached cluster summaries; every box, arrow and number is from the parsed export.</p>
<pre class="mermaid">
{html.escape(src)}
</pre>
<h2>Not in the export ({len(assess["unexported_calls"])} calls)</h2>
<div class="scroll"><table><tr><th>Called</th><th>Kind</th><th>Reaches</th><th>Called from</th></tr>
{"".join(f'<tr><td>{C.esc(x["display_name"] or x["workflow_id"])}</td><td>{C.esc(x["kind"])}</td><td>{C.esc(", ".join(x["reaches"]))}</td><td><a href="{href(x["caller"])}">{C.esc(nm(x["caller"]))}</a> ({C.esc(wf[x["caller"]]["cluster"])})</td></tr>' for x in assess["unexported_calls"])}
</table></div>
<h2>Targets</h2>
<div class="scroll"><table><tr><th>Target</th><th>Kestra reach</th><th>Workflows</th><th>Operations (read / write)</th><th>Kestra tasks</th></tr>
{"".join(f'<tr><td><span class="dot" style="background:{TH.target_colour(k)}"></span>{C.esc(t["name"])}</td><td>{C.esc(t["reach"])}</td><td>{len(t["workflows"])}</td><td>{len(t["operations"])} ({t["reads"]} / {t["writes"]})</td><td>{C.esc(", ".join(t["kestra_tasks"]))}</td></tr>' for k, t in targets.items())}
</table></div>
"""
    return TH.page("Architecture", body, mermaid=True, active="architecture.html")


# ------------------------------------------------------------------ call graph

def call_graph(assess, brief) -> str:
    wf = assess["workflows"]
    cl = {c["cluster_id"]: c for c in assess["clusters"]}
    edges = Counter()
    for _u, w in wf.items():
        for n in w["nested"]:
            v = n.get("target_uid")
            if v in wf and wf[v]["cluster"] != w["cluster"]:
                edges[(w["cluster"], wf[v]["cluster"])] += 1
    internal = Counter()
    for _u, w in wf.items():
        for n in w["nested"]:
            v = n.get("target_uid")
            if v in wf and wf[v]["cluster"] == w["cluster"]:
                internal[w["cluster"]] += 1
    ux_edges = Counter()
    for x in assess["unexported_calls"]:
        ux_edges[(wf[x["caller"]]["cluster"], x["workflow_id"])] += 1
    names = defaultdict(list)
    for x in assess["unexported_calls"]:
        n = x["display_name"] or x["workflow_id"]
        if n not in names[x["workflow_id"]]:
            names[x["workflow_id"]].append(n)
    ux_names = {wid: (" / ".join(ns) if len(ns) == 1 else f"{wid[:8]}, called as {', '.join(ns)}")
                for wid, ns in names.items()}
    involved = {a for a, b in edges} | {b for a, b in edges} | {a for a, _ in ux_edges}
    entry = {c for c in involved if any(not [x for x in wf[u]["called_by"] if x in wf or x in assess["wid_to_uid"].values()]
                                        for u in cl[c]["members"])}
    entry_wf = sorted(u for u, w in wf.items() if not w["called_by"])
    started = [u for u in entry_wf if w_eps(wf[u])]
    not_started = [u for u in entry_wf if not w_eps(wf[u])]
    ep_src = entry_points_src(assess)
    L = ["flowchart TD",
         TH.class_def("entry", "entry") + ",stroke-width:3px",
         TH.class_def("plain", "node"),
         TH.class_def("unexported", "unexported") + ",stroke-dasharray:5 4"]
    for c in sorted(involved):
        label = f'{c}: {TH.mm_label(cl[c]["summary_name"] or "")}<br/>{cl[c]["size"]} workflow(s)'
        if internal.get(c):
            label += f"<br/>{internal[c]} call(s) inside"
        L.append(f'  {c}["{label}"]:::{"entry" if c in entry else "plain"}')
    for wid in sorted({w for _, w in ux_edges}):
        L.append(f'  {TH.mm_id("ux_" + wid)}["{TH.mm_label(ux_names[wid])}<br/>not in export"]:::unexported')
    for (a, b), n in sorted(edges.items()):
        L.append(f'  {a} -->|"{n}"| {b}')
    for (a, wid), n in sorted(ux_edges.items()):
        L.append(f'  {a} -.->|"{n}"| {TH.mm_id("ux_" + wid)}')
    alone = sorted(set(cl) - involved)
    src = "\n".join(L)
    n_ep = assess["counts"].get("entry_points", 0)
    if n_ep:
        note = (f"{n_ep} entry point(s) are on record, from the vRA export and <code>entry-points.yaml</code> "
                "(preflight Q2). The first graph shows what starts each workflow from outside vRO. A workflow "
                "no other workflow calls and no entry point starts may be started by hand or be unused; only an "
                "owner can tell which.")
    else:
        note = ("This export has no catalog items, subscriptions or schedules (preflight Q2 is open), so an "
                "<b>entry point</b> here means a workflow that no other workflow in the export calls. Clusters "
                "holding one have a highlighted border. Some are real entry points; some may be unused. Only an "
                "owner can tell which.")
    ep_block = f"""<h2>Entry points ({n_ep})</h2>
<p class="sub">A catalog item leads to the cloud template it deploys, and an event topic to the workflow its
subscription runs (the edge names the subscription). Dashed red: a workflow the entry point names that is not in
the package.</p>
<pre class="mermaid">
{html.escape(ep_src)}
</pre>""" if ep_src else ""
    body = f"""
<h1>Call graph</h1>
<p>Which workflow calls which action and which other workflow, and what starts each one from outside vRO.</p>
<div class="note">{note}</div>
{ep_block}
<h2>Workflow calls</h2>
<p class="sub">Cluster to cluster calls, counted per workflow call. Dashed red: a call to a workflow that was not
exported.</p>
<pre class="mermaid">
{html.escape(src)}
</pre>
<h2>Clusters that make no calls and receive none ({len(alone)})</h2>
<p>{", ".join(f'{c} ({C.esc(cl[c]["summary_name"] or "")}, {cl[c]["size"]})' for c in alone)}</p>
<h2>Workflows no other workflow calls ({len(entry_wf)})</h2>
<p class="sub">Started by an entry point on record ({len(started)}):
{", ".join(f'<a href="{href(u)}">{C.esc(nm(u))}</a>' for u in started) or "none"}.</p>
<p class="sub">No entry point on record ({len(not_started)}):
{", ".join(f'<a href="{href(u)}">{C.esc(nm(u))}</a>' for u in not_started) or "none"}.</p>
"""
    return TH.page("Call graph", body, mermaid=True, active="call-graph.html")


def w_eps(w: dict) -> list:
    return w.get("entry_points") or []


def entry_points_src(assess) -> str:
    """Mermaid for the entry points: catalog item -> cloud template, event topic -> workflow. Empty if none."""
    edges = (assess.get("call_graph") or {}).get("entry_edges") or []
    if not edges:
        return ""
    wf = assess["workflows"]
    tpls = {t["name"]: t for t in ((assess.get("entry_points") or {}).get("model") or {}).get("cloud_templates", [])}
    L = ["flowchart LR",
         TH.class_def("epsrc", "entry") + ",stroke-width:3px",
         TH.class_def("plain", "node"),
         TH.class_def("unexported", "unexported") + ",stroke-dasharray:5 4"]
    nodes = {}

    def node(end) -> str:
        kind, name = end
        nid = TH.mm_id(f"{kind}_{name}")
        if nid in nodes:
            return nid
        if kind == "workflow":
            label, cls = f"workflow<br/>{TH.mm_label(wf[name]['name'] if name in wf else name)}", "plain"
        elif kind == "missing workflow":
            label, cls = f"workflow {TH.mm_label(name)}<br/>not in the package", "unexported"
        elif kind == "cloud template":
            t = tpls.get(name) or {}
            creates = ", ".join(f"{x.get('name')} ({x.get('type')})" for x in t.get("creates") or [])
            label = f"cloud template<br/>{TH.mm_label(name)}" + (f"<br/>creates {TH.mm_label(creates)}" if creates else "")
            cls = "plain" if t else "unexported"
        else:
            label, cls = f"{TH.mm_label(kind)}<br/>{TH.mm_label(name)}", "epsrc"
        nodes[nid] = f'  {nid}["{label}"]:::{cls}'
        return nid

    lines = []
    for e in edges:
        a, b = node(tuple(e["from"])), node(tuple(e["to"]))
        arrow = "-.->" if e["to"][0] == "missing workflow" else "-->"
        lines.append(f'  {a} {arrow}|"{TH.mm_label(e["label"])}"| {b}' if e["label"] else f"  {a} {arrow} {b}")
    for (kind, name) in dict.fromkeys(tuple(e["to"]) for e in edges if e["to"][0] == "workflow"):
        if name in wf:
            lines.append(f'  click {TH.mm_id(f"{kind}_{name}")} href "{href(name)}"')
    return "\n".join(L + list(nodes.values()) + lines)


# ------------------------------------------------------------------ waves

def waves(assess, brief) -> str:
    plan = brief["plan"]
    cl = {c["cluster_id"]: c for c in assess["clusters"]}
    tri = brief["triage"]
    from .front import wave_title
    L = ["flowchart TB", TH.class_def("wave", "wave")]
    for o, c in TH.OUTCOME.items():
        L.append(TH.fill_class(f"o_{re.sub('[^A-Za-z]', '', o)}", c))
    for w in plan["waves"]:
        if w.get("kind") == "vra-replacement":
            L.append(f'  subgraph W{w["wave"]}["{TH.mm_label(wave_title(w, assess))}, '
                     f'{len(w["entry_criteria"])} entry criteria"]')
            L.append("    direction LR")
            for i, x in enumerate(w["scope"]):
                if x["kind"] in ("App", "flow", "resource"):
                    L.append(f'    W{w["wave"]}_r{i}["{TH.mm_label(x["kind"] + ": " + x["name"])}<br/>'
                             f'{TH.mm_label(x["kestra"])}"]' + (":::o_Manual" if x.get("gap") else ":::o_Convert"))
            L.append("  end")
            continue
        L.append(f'  subgraph W{w["wave"]}["{TH.mm_label(wave_title(w, assess))}, '
                 f'{len(w["entry_criteria"])} entry criteria"]')
        L.append("    direction LR")
        for cid in w["clusters"]:
            mem = [u for u in w["workflows"] if assess["workflows"][u]["cluster"] == cid]
            oc = _cluster_outcome(mem, tri)
            L.append(f'    W{w["wave"]}_{cid}["{cid}: {TH.mm_label(cl[cid]["summary_name"] or "")}<br/>'
                     f'{len(mem)} workflow(s)"]:::o_{re.sub("[^A-Za-z]", "", oc)}')
        L.append("  end")
    order = [w["wave"] for w in plan["waves"]]
    deps = {w["wave"]: w["depends_on"] for w in plan["waves"]}
    for a, b in zip(order, order[1:], strict=False):
        if a not in deps[b]:
            L.append(f"  W{a} -.-> W{b}")
    for w in plan["waves"]:
        for d in w["depends_on"]:
            L.append(f'  W{d} ==>|"called by"| W{w["wave"]}')
    src = "\n".join(L)

    # blocked work, and what blocks it
    B = ["flowchart LR",
         TH.class_def("blocked", "unexported"),
         TH.class_def("reason", "reason") + ",stroke-dasharray:3 3",
         TH.fill_class("manual", TH.OUTCOME["Manual"])]
    reasons = {}
    manual_nodes = set()
    blocked_ids = {b["uid"] for b in plan["blocked"]}
    for b in plan["blocked"]:
        B.append(f'  {TH.mm_id("b_" + b["uid"])}["{TH.mm_label(nm(b["uid"]))}"]:::blocked')
    for b in plan["blocked"]:
        for why in b["blocked_on"]:
            mm = (re.match(r"calls Manual work, rewritten by hand first: (.*)$", why)
                  or re.match(r"its first of a kind (\S+) is Manual", why))
            if mm:
                for v in [x.strip() for x in mm.group(1).split(",")]:
                    if v not in manual_nodes:
                        manual_nodes.add(v)
                        B.append(f'  {TH.mm_id("m_" + v)}["{TH.mm_label(v)}<br/><small>Manual: hand rewrite</small>"]:::manual')
                    B.append(f'  {TH.mm_id("b_" + b["uid"])} -->|"waits for"| {TH.mm_id("m_" + v)}')
                continue
            m = re.match(r"calls blocked work: (.*)$", why) or re.match(r"its first of a kind (\S+) is blocked", why)
            if m:
                for v in [x.strip() for x in m.group(1).split(",")]:
                    if v in blocked_ids:
                        B.append(f'  {TH.mm_id("b_" + b["uid"])} -->|"waits for"| {TH.mm_id("b_" + v)}')
                continue
            key = re.sub(r"^\d+ ", "", why.split(":")[0]).strip()
            if key not in reasons:
                reasons[key] = TH.mm_id("r_" + key)
                B.append(f'  {reasons[key]}["{TH.mm_label(key)}"]:::reason')
            B.append(f'  {TH.mm_id("b_" + b["uid"])} --> {reasons[key]}')
    bsrc = "\n".join(B)

    rows = [_wave_panel(w, assess, tri) for w in plan["waves"]]
    blocked = "".join(f"<tr><td><a href='{href(b['uid'])}'>{C.esc(nm(b['uid']))}</a></td><td>{C.esc(b['outcome'])}</td>"
                      f"<td>{C.esc('; '.join(b['blocked_on']))}</td></tr>" for b in plan["blocked"])
    blocked_part = (
        f"<h2>Outside the waves ({len(plan['blocked'])})</h2>"
        '<p class="sub">Each red box waits for a hand rewrite, for another workflow outside the waves, or for a '
        'decision (grey).</p>\n<pre class="mermaid">\n' + html.escape(bsrc) + "\n</pre>"
        '<div class="scroll"><table><tr><th>Workflow</th><th>Outcome</th><th>Waits on</th></tr>'
        f"{blocked}</table></div>" if plan["blocked"] else "")
    nw = plan["not_waved"]
    by_design = [f"{len(nw[k])} {what}" for k, what in (("Wrapper", "wrappers, whose callers call what they wrap"),
                                                         ("Rebuild", "workflows rebuilt for Kestra"),
                                                         ("Retire", "workflows to retire")) if nw.get(k)]
    met = sum(1 for w in plan["waves"] for e in w["entry_criteria"] if e["status"] == "done")
    total = sum(len(w["entry_criteria"]) for w in plan["waves"])
    body = f"""
<h1>Waves</h1>
<p class="sub">This page lets you check what each wave holds, which element goes first, what it writes to, and which
entry criteria are met ({met} of {total} today). Each open one names the key in
<code>your-input/answers.yaml</code> that closes it.</p>
<pre class="mermaid">
{html.escape(src)}
</pre>
{"".join(rows)}
{blocked_part}
{f"<p class='sub'>Not in a wave by design: {C.esc(', '.join(by_design))}.</p>" if by_design else ""}
"""
    return TH.page("Waves", body, mermaid=True, active="waves.html")


def _wave_panel(w: dict, assess: dict, tri: dict) -> str:
    """One wave: what is in it, its first of a kind, what it writes to, and its entry criteria, met or open."""
    targets = assess["targets"]
    short = lambda t: targets[t].get("short_name") or targets[t]["name"]
    if w.get("kind") == "vra-replacement":
        kinds = Counter(x["kind"] for x in w["scope"])
        names = {k: [x["name"] for x in w["scope"] if x["kind"] == k] for k in kinds}
        parts = [f"{'an App' if kinds[k] == 1 else f'{kinds[k]} Apps'} ({', '.join(names[k])})" if k == "App" else
                 f"{'the flow' if kinds[k] == 1 else f'{kinds[k]} flows'} {', '.join(f'<code>{C.esc(n)}</code>' for n in names[k])}"
                 if k == "flow" else
                 f"{kinds[k]} {k}{'s' if kinds[k] != 1 else ''} ({', '.join(C.esc(n) for n in names[k])})"
                 for k in kinds]
        what = _and_html(parts)
        first = C.esc(w["pilot"])
        writes = ", ".join(w.get("target_names") or []) or "nothing on record"
        writes_l = "Creates resources in"
        gaps = (f"<div>Gaps: {C.esc(', '.join(w['gaps']))} (no Kestra task).</div>" if w.get("gaps") else "")
    else:
        what = (f"{w['n']} workflow{'s' if w['n'] != 1 else ''}: "
                + ", ".join(f'<a href="{href(u)}">{C.esc(nm(u))}</a>' for u in w["workflows"]))
        first = C.esc(nm(w["pilot"])) + ("".join(f", then {C.esc(nm(v))}" for v in w.get("pilot_with") or []))
        writes = ", ".join(short(t) for t in w.get("writes_to") or w.get("targets") or [] if t in targets) or "nothing"
        writes_l = "Writes to"
        gaps = ""
    crit = criteria_lines(w, assess)
    n_met = sum(1 for e in w["entry_criteria"] if e["status"] == "done")
    dep = ", ".join(f"Wave {d}" for d in w["depends_on"])
    from .front import wave_title
    return (f'<div class="panel" style="margin-bottom:10px"><h2>{C.esc(wave_title(w, assess))}</h2>'
            f"<div>In it: {what}.</div><div>First of a kind: {first}.</div>"
            f"<div>{writes_l}: {C.esc(writes)}.</div>{gaps}"
            + (f"<div>Starts after {dep}.</div>" if dep else "")
            + f"<h3>Entry criteria: {n_met} of {len(w['entry_criteria'])} met</h3><ul>"
            + "".join(f"<li><span class='status {cls}'>{st}</span> {C.esc(label)} {keys}</li>" for cls, st, label, keys in crit)
            + "</ul></div>")


def _and_html(xs: list[str]) -> str:
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]


def criteria_lines(w: dict, assess: dict) -> list[tuple[str, str, str, str]]:
    """A wave's entry criteria as short lines: (css class, met or open, what, the key that closes it). Criteria
    of one family about one workflow (its possible defects, its spec questions) share a line."""
    targets = assess["targets"]
    from .gates import key_part
    by_part = {key_part(t): (x.get("short_name") or x["name"]) for t, x in targets.items()}
    by_name = {x["name"]: (x.get("short_name") or x["name"]) for x in targets.values()}
    wf_by_short = {x["short"]: x["name"] for x in assess["workflows"].values()}
    groups: dict[str, dict] = {}
    for e in w["entry_criteria"]:
        c = e.get("closes") or {}
        key = str(c.get("field") or c.get("key") or "")
        label, gkey = _criterion_label(e, key, by_part, by_name, wf_by_short)
        g = groups.setdefault(gkey, {"label": label, "keys": [], "done": 0, "n": 0, "when": ""})
        g["n"] += 1
        g["done"] += e["status"] == "done"
        if c.get("kind") == "gate":
            g["when"] = c["when"]
        elif key and key not in g["keys"]:
            g["keys"].append(key)
    out = []
    for g in groups.values():
        ok = g["done"] == g["n"]
        label = g["label"] + (f" ({g['n'] - g['done']} open)" if g["n"] > 1 and not ok else "")
        ks = g["keys"]
        if len(ks) > 1 and len({k.rsplit(".", 1)[0] for k in ks}) == 1:
            ks = [ks[0].rsplit(".", 1)[0] + ".*"]
        keys = " ".join(f"<code>{C.esc(k)}</code>" for k in ks) or C.esc(g["when"])
        out.append(("done" if ok else "wait", "met" if ok else "open", label, keys))
    return sorted(out, key=lambda x: x[1] == "met")


def _criterion_label(e: dict, key: str, by_part: dict, by_name: dict, wf_by_short: dict) -> tuple[str, str]:
    """What a criterion is about, in a few words, and the line it shares (its group key)."""
    fam, _, rest = key.partition(".")
    parts = rest.split(".") if rest else []
    sys_ = " and ".join(by_part.get(p, p) for p in (parts[0].split("+") if parts else []))
    quoted = re.findall(r"'([^']+)'", e["criterion"])
    step = quoted[0] if quoted else ""
    fixed = {"edition": "Kestra Enterprise Edition", "typed-inputs": "Typed inputs for each flow",
             "mappings": "Image, size and placement of the machines", "ip-allocation": "Who assigns IP addresses",
             "day2-scale": "Scaling on Day 2", "deployment-record": "Which resources belong to each deployment",
             "app": "The App and who uses it", "existing-deployments": "Deployments vRA already made",
             "writes": "Read and write classification", "signed_for": "The signature covers this wave"}
    if fam in ("defect", "question", "spec") and parts:
        name = wf_by_short.get(parts[0], parts[0])
        what = {"defect": "Possible defects in", "question": "Spec questions on", "spec": "Spec review of"}[fam]
        return f"{what} {name}", f"{fam}.{parts[0]}"
    if fam in ("endpoint", "client-route", "auth", "tls"):
        what = {"endpoint": "endpoint and secrets", "client-route": "client route", "auth": "authentication",
                "tls": "certificate checks"}[fam]
        return f"{sys_} {what}", key
    if fam in ("timeout", "failure", "cancel"):
        what = {"timeout": "Timeout and failure policy", "failure": "Failure policy",
                "cancel": "What a cancelled request undoes"}[fam]
        return (f"{what}: {step}" if step else what), key
    if fam == "lifecycle":
        return f"Lifecycle group {rest.upper()} converts together", key
    if fam == "state-store":
        m = re.search(r"element (\S+)", e["criterion"])
        return f"Where {m.group(1) if m else 'the state'} lives in Kestra", key
    if fam == "wave-exit":
        return f"Wave {rest} is done first", key
    if fam == "gap":
        return f"No Kestra task for {step or rest}", key
    if fam in fixed:
        return fixed[fam], key
    if fam.startswith("q3"):
        m = re.search(r"non-production (.+?) is named", e["criterion"])
        name = next((s for n, s in by_name.items() if n in e["criterion"]), m.group(1) if m else "")
        return f"Non-production {name}".strip(), f"{key}:{name}"
    if fam.startswith("q9"):
        return "Whether vRA stays or is replaced (preflight Q9)", key
    m = re.match(r"q(\d+)-", fam)
    if m:
        return f"Preflight Q{m.group(1)}: " + fam.split("-", 1)[1].replace("-", " "), key
    text = re.sub(r"\s*\([^)]*\)", "", e["criterion"]).strip()
    return (text if len(text) <= 80 else text[:77].rsplit(" ", 1)[0] + "..."), key or text


# ------------------------------------------------------------------ explorer

def explorer_data(assess, brief) -> dict:
    wf, tri = assess["workflows"], brief["triage"]
    specs = {u: C.spec(u) or {} for u in wf}
    cl = {c["cluster_id"]: c for c in assess["clusters"]}
    domains = []
    for d in assess["domains"]["domains"]:
        clusters = []
        for cid in d["clusters"]:
            kids = []
            for u in cl[cid]["members"]:
                w = wf[u]
                kids.append({
                    "id": u, "name": w["name"], "kind": "module", "language": "vRO JavaScript",
                    "loc": max(4, w["script_lines"]), "file": w["file"],
                    "outcome": tri[u]["outcome"], "readiness": w["readiness"], "cluster": cid,
                    "reason": tri[u]["reason"], "purpose": (specs[u].get("purpose") or "")[:900],
                    "links": [{"label": "Flowchart and spec summary", "href": href(u)},
                              {"label": "Spec (JSON)", "href": f"../data/2-understand/{u}.json"}],
                })
            clusters.append({"id": f"cl:{cid}", "name": f"{cid} {cl[cid]['summary_name'] or ''}", "kind": "cluster",
                             "children": kids})
        domains.append({"id": f"dom:{d['id']}", "name": d["name"], "kind": "domain", "children": clusters})
    tgt_children = [{"id": k, "name": t["name"], "kind": "datastore",
                     # Targets have no lines of script; size them by how many workflows use them.
                     "loc": max(40, 12 * len(t["workflows"])),
                     "note": f"Kestra reach: {t['reach']}. "
                     f"{len(t['workflows'])} workflows, {len(t['operations'])} operations."}
                    for k, t in assess["targets"].items()]
    domains.append({"id": "dom:targets", "name": "Target systems", "kind": "domain", "children": tgt_children})
    ux = {}
    for x in assess["unexported_calls"]:
        ux.setdefault(x["workflow_id"], x)
    if ux:
        domains.append({"id": "dom:unexported", "name": "Not in the export", "kind": "domain", "children": [
            {"id": f"ux:{wid}", "name": x["display_name"] or wid, "kind": "external",
             "note": f"{x['kind']}. Called but not exported."} for wid, x in ux.items()]})
    edges = []
    for u, w in wf.items():
        for n in w["nested"]:
            if n.get("target_uid") in wf:
                edges.append({"source": u, "target": n["target_uid"], "kind": "call"})
            elif not n["in_export"]:
                edges.append({"source": u, "target": f"ux:{n['workflow_id']}", "kind": "call"})
        modes = defaultdict(set)
        for o in w["ops"]:
            if o["target"] in assess["targets"]:
                modes[o["target"]].add("read" if o["mode"] == "read" else "write")
        for t, ms in modes.items():
            for m in sorted(ms):
                edges.append({"source": u, "target": t, "kind": m})
    seen, uniq = set(), []
    for e in edges:
        k = (e["source"], e["target"], e["kind"])
        if k not in seen:
            seen.add(k)
            uniq.append(e)
    flows = []
    for f in brief["walkthroughs"]["flows"]:
        flows.append({"name": f["name"], "persona": f["persona"], "description": f["description"],
                      "steps": [{"label": s["label"], "outcome": s["outcome"], "nodes": [s["workflow"]]}
                                for s in f["steps"]]})
    pal = palette(assess)
    pal["labels"] = {}
    return {
        "system": f"vRO estate: {C.ESTATE}",
        "root": {"id": "sys", "name": f"{C.ESTATE} estate", "kind": "system", "children": domains},
        "edges": uniq,
        "entryPoints": sorted(u for u, w in wf.items() if not w["called_by"]),
        "deadEnds": sorted(f"ux:{wid}" for wid in ux),
        "observations": [
            f"{assess['counts']['workflows']} workflows in {assess['counts']['clusters']} clusters and "
            f"{assess['counts']['domains']} domains; circles sized by lines of script.",
            f"{assess['counts']['unexported_calls']} nested calls go to workflows or actions that are not in the export (dashed).",
            "Entry points (lavender ring) are workflows nothing in the export calls. "
            + ("Some are started from outside vRO (see the call graph and the vRA view); one with no entry point on "
               "record may be unused." if assess["counts"].get("entry_points") else
               "No entry point is on record (preflight Q2), so some may be unused."),
            "Target colours match the architecture view. Workflow colours follow the control on the left.",
            "Walkthrough steps are chosen by code; their text is model-written from the specs.",
        ],
        "flows": flows,
        "palette": pal,
    }


def explorer(assess, brief) -> str:
    tpl = open(os.path.join(C.ASSETS, "explorer-viewer.html")).read()
    marker = "/*__TOPOLOGY_DATA__*/ null"
    assert marker in tpl
    data = C.no_em_dashes(json.dumps(explorer_data(assess, brief), ensure_ascii=False))
    # Same escaping as Anthropic's injector: node names come from untrusted source.
    data = data.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    th = TH.explorer_theme()
    for m in ("/*__THEME_VARS__*/", "/*__THEME__*/ null", "__FONT_FILE__", "__LOGO__"):
        assert m in tpl, m
    tpl = (tpl.replace("/*__THEME_VARS__*/", th["css_vars"])
              .replace("/*__THEME__*/ null", "/*__THEME__*/ " + json.dumps(th))
              .replace("__FONT_FILE__", "vendor/" + th["font_file"].split("vendor/", 1)[-1])
              .replace("__LOGO__", TH.logo_html("")))
    return C.no_em_dashes(tpl.replace(marker, "/*__TOPOLOGY_DATA__*/ " + data))


# ------------------------------------------------------------------ per-workflow flowchart

def flowchart_src(m: dict, assess: dict, brief: dict) -> str:
    wf = assess["workflows"]
    items = {i["name"]: i for i in m["items"]}
    handler = m.get("default_error_handler") or {}
    handler_path = set(m.get("error_handler_path_items") or [])
    reach = set(m.get("root_path_items") or []) | handler_path
    nested = {n["item"]: n for n in wf.get(m["uid"], {}).get("nested", [])}
    L = ["flowchart TD",
         TH.class_def("task", "task"), TH.class_def("decision", "decision"),
         TH.class_def("endok", "end_ok"), TH.class_def("endthrow", "end_throw"),
         TH.class_def("link", "link"),
         TH.class_def("unexported", "unexported") + ",stroke-dasharray:5 4",
         TH.class_def("human", "human"), TH.class_def("timer", "timer"),
         f"  classDef unreach opacity:{TH.D['unreachable_opacity']}",
         TH.class_def("start", "start")]
    L.append('  START(("start")):::start')
    for name, i in items.items():
        t = i["type"]
        disp = i.get("display_name") or ""
        base = f"{TH.mm_label(disp)}" if disp else ""
        tag = f"{t} {name}"
        nid = TH.mm_id(name)
        if t == "link" or (t == "task" and i["attributes"].get("launched-workflow-id")):
            n = nested.get(name) or {}
            what = TH.mm_label(n.get("display_name") or disp or "workflow")
            mode = "async call" if i["attributes"].get("launched-workflow-id") else "calls"
            cls = "link" if n.get("in_export", True) else "unexported"
            extra = "" if n.get("in_export", True) else "<br/>not in export"
            L.append(f'  {nid}[["{mode}: {what}<br/><small>{tag}</small>{extra}"]]:::{cls}')
        elif t in ("condition", "custom-condition", "decision-activity"):
            L.append(f'  {nid}{{"{base or "decision"}<br/><small>{tag}</small>"}}:::decision')
        elif t == "switch":
            L.append(f'  {nid}{{{{"{base or "switch"}<br/><small>{tag}</small>"}}}}:::decision')
        elif t == "end":
            throws = i["attributes"].get("end-mode") not in (None, "0")
            L.append(f'  {nid}(("{"end, throws" if throws else "end"}<br/><small>{name}</small>")):::{"endthrow" if throws else "endok"}')
        elif t == "input":
            L.append(f'  {nid}[/"waits for a person: {base or "user interaction"}<br/><small>{tag}</small>"/]:::human')
        elif t == "waiting-timer":
            L.append(f'  {nid}>"timer: {base or "wait"}<br/><small>{tag}</small>"]:::timer')
        elif t == "foreach":
            L.append(f'  {nid}[["for each: {base}<br/><small>{tag}</small>"]]:::link')
        else:
            sa = i["attributes"].get("script-module")
            label = base or ("action " + sa.split("/")[-1] if sa else name)
            L.append(f'  {nid}["{label}<br/><small>{tag}</small>"]:::task')
        if reach and name not in reach:
            L.append(f"  class {nid} unreach")
    styles = []
    idx = 0
    if m.get("root_item") in items:
        L.append(f"  START --> {TH.mm_id(m['root_item'])}")
        idx += 1
    if handler.get("target") in items:
        # <error-handler>: the workflow-wide catch. Any error no catch-name handles goes here.
        bind = TH.mm_label(handler.get("throw_bind_name") or "")
        L.append(f'  ERRH(["any uncaught error<br/><small>error-handler{", binds " + bind if bind else ""}</small>"]):::endthrow')
        L.append(f'  ERRH -.->|"on error"| {TH.mm_id(handler["target"])}')
        styles.append(f"  linkStyle {idx} stroke:{TH.D['end_throw']['stroke']}")
        idx += 1
    for e in m["edges"]:
        if e["to"] not in items:
            continue
        a, b = TH.mm_id(e["from"]), TH.mm_id(e["to"])
        src_t = items[e["from"]]["type"]
        if e["kind"] == "error":
            L.append(f'  {a} -.->|"on error"| {b}')
            styles.append(f"  linkStyle {idx} stroke:{TH.D['end_throw']['stroke']}")
        elif e["kind"] == "alt":
            L.append(f'  {a} -->|"{"no" if src_t in ("condition", "custom-condition") else "else"}"| {b}')
        elif e["kind"] == "case":
            L.append(f'  {a} -->|"{TH.mm_label(e.get("label") or "")[:40]}"| {b}')
        elif src_t in ("condition", "custom-condition") and items[e["from"]]["attributes"].get("alt-out-name"):
            L.append(f'  {a} -->|"yes"| {b}')
        elif src_t == "switch":
            L.append(f'  {a} -->|"default"| {b}')
        else:
            L.append(f"  {a} --> {b}")
        idx += 1
    for name, n in nested.items():
        v = n.get("target_uid")
        if v in wf:
            L.append(f'  click {TH.mm_id(name)} href "{href(v, "")}" "open {TH.mm_label(nm(v))}"')
    return "\n".join(L + styles)


def workflow_page(u: str, assess: dict, brief: dict) -> str:
    m = C.read_json(os.path.join(C.WORK, "model", f"{u}.json"))
    w = assess["workflows"][u]
    t = brief["triage"][u]
    sp = C.spec(u) or {}
    src = flowchart_src(m, assess, brief)
    dom = next(d for d in assess["domains"]["domains"] if d["id"] == w["domain"])
    ops = "".join(f"<tr><td><span class='dot' style='background:{TH.target_colour(o['target'])}'></span>{C.esc(o['target'])}</td>"
                  f"<td><code>{C.esc(o['op'])}</code></td><td>{C.esc(o['mode'])}</td><td>{C.esc(o['reach'])}</td>"
                  f"<td>{C.esc(o.get('via') or o.get('item') or '')}</td></tr>" for o in w["ops"])
    oq = brief.get("open_questions") or {}
    covered = (oq.get("covered") or {}).get(u) or []
    cov_q = {c["question"] for c in covered}
    keys = {x["question"]: g.get("key") for g in oq.get("groups") or [] for x in g["wordings"] if x["workflow"] == u}
    asked = [q for q in sp.get("open_questions", []) if q["question"] not in cov_q]
    qs = "".join(f"<li><i>{C.esc(q.get('theme') or '')}</i>: {C.esc(q['question'])}"
                 + (f" <span class='muted'>answer in <code>your-input/answers.yaml</code> → <code>{C.esc(keys[q['question']])}</code></span>"
                    if keys.get(q["question"]) else "") + "</li>" for q in asked)
    qs += "".join(f"<li class='muted'>Asked once, by a decision: {C.esc(q)} → "
                  + ", ".join(f"<code>{C.esc(c['key'])}</code>" for c in covered if c["question"] == q) + "</li>"
                  for q in sorted(cov_q))
    mine = [x for x in brief.get("suspected_defects") or [] if x["workflow"] == u]
    defects = "".join(f"<li>{C.esc(x['what'])} <span class='muted'>Evidence: {C.esc(x.get('evidence') or '')}</span>"
                      + (f"<br>{C.esc(x['question'])}" if x.get("question") else "")
                      + f" <span class='muted'>Keep or fix: <code>{C.esc(x['decision'])}</code></span></li>"
                      for x in mine)
    decisions = "".join(f"<li><b>{C.esc(d['item'])}</b>: {C.esc(d['condition'])}</li>" for d in sp.get("decisions", []))
    callers = ", ".join(f'<a href="{href(x, "")}">{C.esc(nm(x))}</a>' if x in assess["workflows"] else C.esc(x)
                        for x in w["called_by"]) or ("no other workflow" if w_eps(w) else
                                                     "nothing in the export (an entry point, or unused)")
    eps = "; ".join(C.esc(f"{e['kind']} {e['name']}" + (f" on {e['event_topic']}" if e.get("event_topic") else "")
                          + (" (blocking)" if e.get("blocking") else "")
                          + (f", when {e['condition']}" if e.get("condition") else ""))
                    for e in w_eps(w)) or "none on record"
    doc_note = ""
    if w["doc_order_would_mislead"]:
        doc_note = (f"<div class='note'>The first task in the file is <code>{C.esc(w['first_task_in_doc_order'])}</code>, "
                    f"but the run starts at <code>{C.esc(w['root_item'])}</code> and its first task is "
                    f"<code>{C.esc(w['first_task_on_root_path'])}</code>. A converter that reads the file top down starts in the wrong place.</div>")
    def nested_row(n: dict) -> str:
        # Written outside the page's f-string: a nested f-string reusing its quotes needs Python 3.12
        if n.get('target_uid') in assess['workflows']:
            called = (f'<a href="{href(n["target_uid"], "")}">'
                      + C.esc(n['display_name'] or nm(n['target_uid'])) + '</a>')
        else:
            called = C.esc(n['display_name'] or n['workflow_id'])
        in_export = 'yes' if n['in_export'] else 'no (' + C.esc(n.get('kind') or '') + ')'
        outcome = C.esc(brief['triage'][n['target_uid']]['outcome']) if n.get('target_uid') in brief['triage'] else ''
        return f"<tr><td>{C.esc(n['item'])}</td><td>{called}</td><td>{in_export}</td><td>{outcome}</td></tr>"
    nested_rows = "".join(nested_row(n) for n in w['nested']) or "<tr><td colspan=4>none</td></tr>"
    body = f"""
<p class="sub"><a href="../index.html">Brief</a> / <a href="index.html">Workflows</a> / {C.esc(dom['name'])} / {C.esc(w['cluster'])}</p>
<h1>{C.esc(w['name'])}</h1>
<p>What this workflow does, how it runs and what may be wrong in it: its spec, its flowchart and its possible defects.</p>
<div>{TH.outcome_badge(t['outcome'])}
<span class="badge"><span class="dot" style="background:{TH.READINESS[w['readiness']]}"></span>readiness {C.esc(w['readiness'])}</span>
<span class="badge">spec confidence {C.esc(w['spec_confidence'])}</span>
<span class="badge">{w['items']} items, {w['script_lines']} lines of script</span></div>
<p><b>Why {C.esc(t['outcome'])}:</b> {C.esc(t['reason'])}</p>
{"<p class='sub'>Other rules that matched: " + C.esc(" | ".join(t['also'])) + "</p>" if t['also'] else ""}
{doc_note}
<div class="grid side4">
<div>
<h2>Flowchart</h2>
<p class="sub">Drawn from the item graph in the XML, labelled by vRO item name. Faded items are not reachable from
<code>root-name</code> or from the workflow's default error handler (<code>&lt;error-handler&gt;</code>, drawn as "any uncaught error"). Purple double boxes call another workflow (click to open it); dashed red ones call a workflow
that is not in the export.</p>
<pre class="mermaid">
{html.escape(src)}
</pre>
</div>
<div>
<h2>Purpose</h2>
<p class="sub">From the spec (<a href="../../data/2-understand/{u}.json">the spec file</a>), model-written.</p>
<p>{C.esc(sp.get('purpose') or 'no spec')}</p>
<h3>Decisions</h3><ul>{decisions or "<li>none</li>"}</ul>
<h3>Open questions ({len(asked)}{f", and {len(cov_q)} asked once by a decision" if cov_q else ""})</h3><ul>{qs or "<li>none</li>"}</ul>
<h3 id="defects">Possible defects ({len(mine)})</h3><ul>{defects or "<li>none</li>"}</ul>
<p class="sub">Review this spec in <code>your-input/answers.yaml</code> → <code>spec.{C.esc(w.get('short') or u)}</code>.</p>
<h3>Called by</h3><p>{callers}</p>
<h3>Started from outside vRO by</h3><p>{eps}</p>
<h3>Source</h3><p class="sub"><code>{C.esc(w['file'])}</code></p>
</div>
</div>
<h2>Workflows it calls</h2>
<div class="scroll"><table><tr><th>Item</th><th>Calls</th><th>In export</th><th>Its brief outcome</th></tr>
{nested_rows}
</table></div>
<h2>What it touches itself</h2>
<p class="sub">Operations in its own scripts and the actions it calls. Calls made by the workflows above are on their pages.</p>
<div class="scroll"><table><tr><th>Target</th><th>Operation</th><th>Mode</th><th>Kestra reach</th><th>Where</th></tr>
{ops or "<tr><td colspan=5>no external operations</td></tr>"}</table></div>
"""
    return TH.page(w["name"], body, depth=1, mermaid=True)


# ------------------------------------------------------------------ index

def _xml_excerpt(w: dict, max_lines: int = 60) -> tuple[str, str]:
    """The workflow tag and the items up to the entry point, from the file on disk."""
    path = os.path.join(C.BASE, w["file"])
    from kestra_migration_assistant.vra import safety
    from kestra_migration_assistant.vra.parse import formats
    raw = safety.read_file(path)
    text = formats.decode_bytes(raw)
    lines = text.splitlines()
    first = next((i for i, l in enumerate(lines) if "<workflow" in l), 0)
    root = w["root_item"]
    root_line = next((i for i, l in enumerate(lines) if f'name="{root}"' in l and "workflow-item" in l), first)
    doc_first = w["first_task_in_doc_order"]
    doc_line = next((i for i, l in enumerate(lines) if f'name="{doc_first}"' in l and "workflow-item" in l), first)
    keep = set(range(first, min(first + 2, len(lines))))
    for anchor in (doc_line, root_line):
        keep |= set(range(anchor, min(anchor + 6, len(lines))))
    out, prev = [], None
    for i in sorted(keep):
        if prev is not None and i != prev + 1:
            out.append("      ...")
        l = html.escape(lines[i][:180])
        cls = "hl" if i in (first, root_line, doc_line) else ""
        out.append(f'<span class="{cls}">{i + 1:4d}  {l}</span>' if cls else f"{i + 1:4d}  {l}")
        prev = i
    return "\n".join(out[:max_lines]), w["file"]


def pick_story(assess, brief) -> str:
    """A real workflow with a clear story: its first task in the file is not where the run starts,
    and the brief says not to convert it. Prefer a wrapper, then a rebuild."""
    wf, tri = assess["workflows"], brief["triage"]
    for want in ("Wrapper", "Rebuild"):
        c = sorted((u for u, w in wf.items() if w["doc_order_would_mislead"] and tri[u]["outcome"] == want),
                   key=lambda u: (wf[u]["items"], u))
        if c:
            return c[0]
    return sorted(wf)[0]


# The files the overview links to, from the estate folder, when they exist (a .md opens rendered).
INDEX_REPORTS = [("data/0-preflight/readiness.md", "readiness"), ("data/1-assess/estate-report.md", "estate report"),
                 ("data/1-assess/target-inventory.md", "target inventory"), ("data/1-assess/domains.md", "domains"),
                 ("data/1-assess/unexported-calls.md", "unexported calls"),
                 ("data/1-assess/wrappers-and-libraries.md", "wrappers and libraries"),
                 ("data/1-assess/clusters.md", "clusters"), ("data/1-assess/parse-inventory.md", "parse inventory"),
                 ("report/BRIEF.md", "brief (to download)"), ("data/3-brief/open-questions.md", "open questions"),
                 ("data/3-brief/triage.csv", "triage.csv"), ("WALKTHROUGH.md", "walkthrough")]


def run_page(assess, brief, pre) -> str:
    """The run, stage by stage: what each stage did, the estate's counts, and one workflow all the way through."""
    from . import facts, preflight
    from . import status as S
    st = S.collect()
    fx = facts.estate(assess, preflight.load_answers())
    c = assess["counts"]
    kpis = [("Workflows", f"{c['workflows']}", "all of them in scope"),
            ("Actions", c["actions"], "script modules"),
            ("Config elements", c["config_elements"], ""),
            ("Resource elements", c["resource_elements"], ""),
            ("Target systems", c["targets"], "plus vRO itself"),
            ("Clusters", c["clusters"], f"{c['singletons']} one of a kind"),
            ("Domains", c["domains"], ""),
            ("Entry points", c.get("entry_points", 0),
             f"start {c.get('entry_point_workflows', 0)} workflow(s)"
             + (f", {c['entry_points_unresolved']} unresolved" if c.get("entry_points_unresolved") else "")
             if c.get("entry_points") else "none on record (Q2)"),
            ("Tests", c["tests"], "vRO estates have none")]
    kp = "".join(f'<div class="panel kpi"><div class="v">{C.esc(v)}</div><div class="l">{C.esc(k)}'
                 f'{"<br>" + C.esc(s) if s else ""}</div></div>' for k, v, s in kpis)
    stage_cards = []
    for s in st["stages"]:
        cls = {"done": "done", "waiting on a person": "wait", "stale": "stale"}.get(s["state"], "todo")
        stage_cards.append(f"""<div class="panel card"><h3>{C.esc(s['title'])} <span class="status {cls}">{C.esc(s['state'])}</span></h3>
<div class="sub">What Claude did</div><ul>{"".join(f"<li>{C.esc(x)}</li>" for x in s['did'])}</ul>
<div class="sub">What a person decides next</div><ul>{"".join(f"<li>{C.esc(x)}</li>" for x in s['next'])}</ul>
<div>{" · ".join(f'<a href="{h}">{C.esc(t)}</a>' for t, h in s['links'])}</div></div>""")

    u = pick_story(assess, brief)
    w = assess["workflows"][u]
    t = brief["triage"][u]
    sp = C.spec(u) or {}
    m = C.read_json(os.path.join(C.WORK, "model", f"{u}.json"))
    xml, xml_file = _xml_excerpt(w)
    fc = flowchart_src(m, assess, brief).replace('href "', 'href "workflows/')
    report_links = " · ".join(f'<a href="../{f}">{C.esc(label)}</a>' for f, label in INDEX_REPORTS
                              if os.path.exists(os.path.join(C.STAGES, f))) + ' · <a href="reports/index.html">all reports</a>'
    body = f"""
<h1>The run</h1>
<p>What each stage did with the export in <code>{C.esc(C.rel(C.SOURCE_DIR))}/</code>, and one workflow followed from its
XML to its outcome. Generated {C.esc(st['generated'])}; nothing was deployed anywhere.</p>
<div class="grid kpis">{kp}</div>
<h2>Stages</h2>
<div class="grid cards">{"".join(stage_cards)}</div>

<p class="sub">Reports: {report_links}</p>

<h2>One workflow, all the way through: {C.esc(w['name'])}</h2>
<p>Picked by code: a real workflow whose first task in the file is not where it starts, and which the brief says not
to convert as is. <a href="{href(u)}">Full page</a>.</p>
<div class="grid side4">
<div class="panel"><h3>1. The XML</h3>
<p class="sub"><code>{C.esc(xml_file)}</code>. Highlighted: the workflow's <code>root-name</code>, the first task in
the file (<code>{C.esc(w['first_task_in_doc_order'])}</code>), and the item the run starts at (<code>{C.esc(w['root_item'])}</code>).</p>
<pre class="xml">{xml}</pre></div>
<div class="panel"><h3>2. The flowchart</h3>
<p class="sub">Drawn from the parsed item graph. The run starts at the filled "start" node.</p>
<pre class="mermaid">
{html.escape(fc)}
</pre></div>
<div class="panel"><h3>3. What the spec says it does</h3>
<p class="sub">Model-written by the Understand stage, from the parsed workflow.</p>
<p>{C.esc(sp.get('purpose') or '')}</p></div>
<div class="panel"><h3>4. What the brief decided</h3>
<p>{TH.outcome_badge(t['outcome'], big=True)}</p>
<p>{C.esc(t['reason'])}</p>
<p class="sub">{C.esc(" | ".join(t['also']))}</p>
<p class="sub">Targets it touches: {C.esc(", ".join(w['targets']) or "none")}. Called by: {C.esc(", ".join(w['called_by']) or "nothing in the export")}.</p></div>
</div>
<h2>What is untestable here</h2>
<ul>
<li>Retire: {C.esc(fx['history_sentence'])} {C.esc(fx['triggers'])} No workflow is marked Retire.</li>
<li>Read or write is judged from operation names, not observed. Each wave asks its owner to confirm.</li>
<li>Nothing was run against a target: there is no non-production instance on record (Q3), and this work does not deploy.</li>
</ul>
"""
    return TH.page("The run", body, mermaid=True, active="run.html")


# ------------------------------------------------------------------ vRA view

def _table(head: list[str], rows: list[list[str]]) -> str:
    """An HTML table; cells are already escaped HTML."""
    return ("<table><thead><tr>" + "".join(f"<th>{h}</th>" for h in head) + "</tr></thead><tbody>"
            + "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows) + "</tbody></table>")


def _wf_link(uid: str | None, name: str | None, tri: dict) -> str:
    if uid and uid in tri:
        return (f'<a href="{href(uid)}">{C.esc(name or nm(uid))}</a> '
                f'{TH.outcome_badge(tri[uid]["outcome"])}')
    return C.esc(name or "-") + (" <em>(not in the package)</em>" if name else "")


def vra_view(assess, brief) -> str:
    from . import triggers as TG
    model = (assess.get("entry_points") or {}).get("model") or {}
    tri = brief["triage"]
    tp = brief.get("trigger_plan") or TG.plan(model, None)
    views = TG.lifecycle_view(model)
    if not views:
        body = ("<h1>vRA view</h1><div class=\"note\">No vRA content is on record: no .vra-ng export in the estate "
                "folder and no catalog item, cloud template or subscription in <code>entry-points.yaml</code> "
                "(preflight Q2). Nothing here to show.</div>")
        return TH.page("vRA view", body, active="vra.html")
    choice = {TG.KEEP: "vRA stays as the front end (preflight Q9): subscriptions call Kestra's webhook.",
              TG.REPLACE: "vRA is replaced (preflight Q9): the catalog item becomes a Kestra App, the cloud template "
                          "a flow, each event below a step in it.",
              TG.BRIDGE: "vRA is replaced, with a bridge until then (preflight Q9): subscriptions call Kestra's "
                         "webhook while the replacement is built, then each event below becomes a step.",
              TG.BOTH: "Preflight Q9 (keep vRA or replace it) is open, so the plan below shows both."}[tp["front_end"]]
    parts = [f"""<h1>vRA flow</h1>
<p>How a request becomes machines and what the workflows do with them: the catalog item and its form, the cloud
template and what it creates, then the lifecycle events in the order vRA fires them, each with the workflow its
subscription runs, and what each becomes in Kestra. {C.esc(choice)}</p>"""]
    for v in views:
        t = v["template"] or {}
        name = v["cloud_template"]
        if name:
            parts.append(f"<h2>Cloud template: {C.esc(name)}</h2>")
            if t.get("description"):
                parts.append(f'<p class="sub">{C.esc(t["description"])}</p>')
        else:
            parts.append("<h2>Subscriptions no cloud template claims</h2><p class=\"sub\">Their project scope matches "
                         "no single catalog item, so their place in a deployment's life is shown without a template.</p>")
        for c in v["catalog_items"]:
            parts.append(f"""<div class="panel" style="margin-bottom:10px"><h3>Catalog item: {C.esc(c['name'])}</h3>
<p class="sub">Content source {C.esc(c.get('content_source') or '-')}; entitled to {C.esc(', '.join(c.get('projects') or []) or 'not on record')}.
{C.esc(c.get('description') or '')}</p>
{_table(["Form field", "Label", "Type", "Required", "Default"],
        [[f"<code>{C.esc(f.get('id'))}</code>", C.esc(f.get('label') or ''), C.esc(f.get('type') or ''),
          "yes" if f.get('required') else "no", C.esc('' if f.get('default') is None else
                                                     (str(f['default']).lower() if isinstance(f['default'], bool) else str(f['default'])))]
         for f in c.get('form_fields') or []])}</div>""")
        if t:
            parts.append(f"""<div class="panel" style="margin-bottom:10px"><h3>What the cloud template creates</h3>
<p class="sub">Inputs: {C.esc(', '.join(t.get('inputs') or []) or 'none')}.</p>
{_table(["Resource", "Type", "Count", "Custom properties (what subscriptions can filter on)"],
        [[f"<code>{C.esc(x['name'])}</code>", C.esc(x['type']), C.esc(x.get('count') or '1'),
          C.esc(', '.join(x.get('custom_properties') or []) or '-')] for x in t.get('creates') or []])}</div>""")
        if v["events"]:
            rows = []
            for e in v["events"]:
                blk = ("blocking" + (f", {e['timeout_minutes']} min" if e.get("timeout_minutes") else "")) if e["blocking"] else "not blocking"
                rows.append([C.esc(TG.stage_label(e["position"])), f"<code>{C.esc(e['event_topic'])}</code>", C.esc(e["name"]),
                             C.esc(blk), f"<code>{C.esc(e['condition'])}</code>" if e["condition"] else "-",
                             _wf_link(e.get("workflow_uid"), e.get("workflow"), tri)])
            parts.append("<h3>Lifecycle events, in order</h3>"
                         + _table(["Stage", "Event topic", "Subscription", "vRA waits?", "Condition", "Runs workflow"], rows))
            # the same, drawn: catalog item -> template -> each stage -> workflow
            L = ["flowchart LR", TH.class_def("epsrc", "entry") + ",stroke-width:3px", TH.class_def("plain", "node")]
            prev = None
            for c in v["catalog_items"]:
                cid = TH.mm_id("cat_" + c["name"])
                L.append(f'  {cid}["catalog item<br/>{TH.mm_label(c["name"])}"]:::epsrc')
                prev = cid
            if name:
                tid = TH.mm_id("tpl_" + name)
                L.append(f'  {tid}["cloud template<br/>{TH.mm_label(name)}"]:::plain')
                if prev:
                    L.append(f"  {prev} --> {tid}")
                prev = tid
            for i, e in enumerate(v["events"]):
                eid = TH.mm_id(f"ev_{i}_{e['name']}")
                L.append(f'  {eid}["{TH.mm_label(TG.stage_label(e["position"]))}<br/>{TH.mm_label(e["event_topic"])}'
                         + ('<br/>blocking' if e["blocking"] else '') + '"]:::epsrc')
                if prev:
                    L.append(f"  {prev} --> {eid}")
                prev = eid
                if e.get("workflow"):
                    wid = TH.mm_id("wf_" + (e.get("workflow_uid") or e["workflow"]))
                    L.append(f'  {wid}["{TH.mm_label(e["workflow"])}"]:::plain')
                    L.append(f'  {eid} -.->|"{TH.mm_label(e["name"])}"| {wid}')
                    if e.get("workflow_uid") in tri:
                        L.append(f'  click {wid} href "{href(e["workflow_uid"])}"')
            parts.append(f'<pre class="mermaid">\n{html.escape(chr(10).join(dict.fromkeys(L)))}\n</pre>')
    parts += _trigger_plan(assess, brief, tp)
    groups = assess.get("lifecycle_groups") or []
    if groups:
        parts.append("<h2>Deployment lifecycle groups</h2><p class=\"sub\">Workflows these events start that share state "
                     "one of them writes. Each group migrates in one wave.</p><ul>"
                     + "".join(f"<li><b>{g['id']}</b>: {', '.join(_wf_link(u, assess['workflows'][u]['name'], tri) for u in g['members'])}. "
                               f"{C.esc(g['why'])}</li>" for g in groups) + "</ul>")
    return TH.page("vRA flow", "\n".join(parts), mermaid=True, active="vra.html")


def _trigger_plan(assess, brief, tp) -> list[str]:
    """What starts each workflow today, and what it becomes in Kestra (both plans while Q9 is open)."""
    from . import triggers as TG
    if not tp.get("rows"):
        return []
    choice = tp["front_end"]
    head = ["Entry point", "Kind", "Starts", "Today", "vRA waits?"]
    keep = choice in (TG.KEEP, TG.BOTH, TG.BRIDGE)
    repl = choice in (TG.REPLACE, TG.BOTH, TG.BRIDGE)
    if keep:
        head.append("Until vRA goes (the bridge)" if choice == TG.BRIDGE else "If vRA stays")
    if repl:
        head.append("When vRA is replaced" if choice == TG.BRIDGE else "If vRA is replaced")
    rows = []
    for x in tp["rows"]:
        blk = ("yes" + (f", {x['timeout_minutes']} min" if x.get("timeout_minutes") else "")
               if x["blocking"] else ("no" if x["blocking"] is False else "-"))
        wn = ((assess["workflows"].get(x.get("workflow_uid")) or {}).get("name") or x.get("workflow") or "-")
        row = [C.esc(x["name"]), C.esc(x["kind"]), C.esc(wn), C.esc(x["today"]), C.esc(blk)]
        if keep:
            row.append(C.esc(x["keep"]))
        if repl:
            row.append(C.esc(x["replace"]))
        rows.append(row)
    out = ["<h2>In Kestra</h2>", _table(head, rows)]
    notes = [(u, n) for u, t in sorted(brief["triage"].items()) for n in t.get("trigger_notes", [])
             if not n.startswith("part of deployment lifecycle group")]
    if notes:
        out.append("<h3>Notes the conversion carries</h3><ul>" + "".join(
            f"<li>{C.esc((assess['workflows'].get(u) or {}).get('name') or u)}: {C.esc(n)}.</li>" for u, n in notes)
            + "</ul>")
    return out


# ------------------------------------------------------------------ plugin coverage

def plugins_view(assess, brief) -> str:
    cov = assess.get("plugin_coverage") or {}
    m = cov.get("snapshot") or {}
    if not m.get("file"):
        body = ("<h1>Plugin coverage</h1><div class=\"note\">No plugin snapshot is bundled with this install "
                "(<code>data/plugins/</code> in the package), so coverage is not shown.</div>")
        return TH.page("Plugin coverage", body, active="plugins.html")
    srcs = []
    for s, v in m["sources"].items():
        if v.get("available"):
            srcs.append(f"<b>{C.esc(s)}</b>: {v.get('plugins')} plugins from <code>{C.esc(v.get('url'))}</code>"
                        + (f" (Kestra {C.esc(v.get('kestra_version'))} {C.esc(v.get('edition'))})" if v.get("kestra_version") else ""))
        else:
            srcs.append(f"<b>{C.esc(s)}</b>: not available ({C.esc(v.get('reason', ''))})")

    def side(x):
        if not x.get("available"):
            return "<em>not checked</em>"
        return C.esc(", ".join(f"{p['name']} {p['version']}" if p.get("version") else
                               ("built in" if p["name"] == "core" else p["name"]) for p in x["plugins"]) or "none")

    def where(w):
        return " / ".join({True: "yes", False: "no", None: "?"}[w.get(s)] for s in ("installed", "catalog"))
    rows = [[C.esc(r["system"]), C.esc(r["via"]), f"<b>{C.esc(r['label'])}</b>", side(r["installed"]), side(r["catalog"]),
             C.esc(str(r["operations"])) if r["operations"] else "-"] for r in cov["rows"]]
    details = []
    for r in cov["rows"]:
        det = [f"<div class=\"panel\" style=\"margin-bottom:10px\"><h3>{C.esc(r['system'])}: {C.esc(r['label'])}</h3>"]
        if r.get("created_by"):
            det.append(f"<p class=\"sub\">Created by the cloud template: {C.esc(', '.join(r['created_by']))} "
                       f"({C.esc(r.get('resource_type') or '')}).</p>")
        if r.get("search_words") and not any(p for s in ("installed", "catalog") for p in r[s].get("plugins") or []):
            det.append(f"<p class=\"sub\">Searched the snapshot for {C.esc(', '.join(r['search_words']))}: no plugin.</p>")
        if r["task_map"]:
            det.append(_table(["Operation", "Kestra task type", "Installed / catalogue"],
                              [[f"<code>{C.esc(t['op'])}</code>", f"<code>{C.esc(t['task'])}</code>", where(t["where"])]
                               for t in r["task_map"]]))
        if r["status"] == "replace" and r.get("ops"):
            det.append("<p class=\"sub\">What it replaces: " + ", ".join(f"<code>{C.esc(o)}</code>" for o in r["ops"])
                       + ". Settings become namespace variables or secrets; state written at run time goes to the KV store.</p>")
        if r["unmatched_ops"]:
            det.append("<p class=\"sub\">No plugin task for: " + ", ".join(f"<code>{C.esc(o)}</code>" for o in r["unmatched_ops"])
                       + (f". These go over HTTP with <code>{C.esc(r['http_task'])}</code> "
                          f"(installed / catalogue: {where(r['http_task_where'])})" if r.get("http_task") else "") + ".</p>")
        if r.get("api") and r["status"] in ("http", "partial", "new plugin"):
            det.append(f"<p class=\"sub\">API: {C.esc(r['api'])}</p>")
        if r["disagreements"]:
            det.append("<p><b>Instance and catalogue disagree:</b></p><ul>"
                       + "".join(f"<li>{C.esc(d)}</li>" for d in r["disagreements"]) + "</ul>")
        if r.get("enterprise_only"):
            det.append("<p class=\"sub\">Enterprise Edition plugin (licence EE in the snapshot).</p>")
        det.append("</div>")
        details.append("".join(det))
    body = f"""<h1>Plugin coverage</h1>
<p>Which Kestra plugin and tasks reach each system the estate touches, and where HTTP tasks or a gap stand in.</p>
<div class="note">Snapshot taken <b>{C.esc(m['taken'])}</b> (<code>{C.esc(m['file'])}</code>). {'; '.join(srcs)}.
Built from that snapshot only: a plugin counts when its group is in it, and a task when its class is. When the
instance and the catalogue disagree, both are shown.</div>
{_table(["System", "Touched through", "Coverage", "Installed", "Catalogue", "Operations"], rows)}
<h2>Per system</h2>
{"".join(details)}"""
    return TH.page("Plugin coverage", body, active="plugins.html")
