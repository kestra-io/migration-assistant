"""Graph facts derived from the normalized model: reachability, shape, cycles."""
from __future__ import annotations

from collections import defaultdict


def build_adjacency(model: dict, kinds=("next", "alt", "case")) -> dict[str, list[str]]:
    adj: dict[str, list[str]] = defaultdict(list)
    for i in model.get("items", []):
        adj.setdefault(i["name"], [])
    for e in model.get("edges", []):
        if e["kind"] in kinds:
            adj[e["from"]].append(e["to"])
    return dict(adj)


def root_path_items(model: dict) -> list[str]:
    """
    Items reachable from root-name over control edges, in breadth-first order.

    root-name is the entry point. It is not the first item in document order,
    and the difference is what made the baseline tool emit the wrong branch for
    29 of 86 workflows.
    """
    adj = build_adjacency(model)
    start = model.get("root_item") or ""
    if start not in adj:
        return []
    seen, order, queue = {start}, [start], [start]
    while queue:
        n = queue.pop(0)
        for m in adj.get(n, []):
            if m not in seen and m in adj:
                seen.add(m)
                order.append(m)
                queue.append(m)
    return order


def error_handler_path_items(model: dict) -> list[str]:
    """
    Items reachable from the workflow's default error handler (<error-handler>)
    over control edges, breadth first. They are not on the root path, but they
    run whenever an uncaught error does, so they are not unreachable.
    """
    h = model.get("default_error_handler") or {}
    adj = build_adjacency(model)
    start = h.get("target") or ""
    if start not in adj:
        return []
    seen, order, queue = {start}, [start], [start]
    while queue:
        n = queue.pop(0)
        for m in adj.get(n, []):
            if m not in seen and m in adj:
                seen.add(m)
                order.append(m)
                queue.append(m)
    return order


def item_type_sequence(model: dict) -> list[str]:
    """The item types along the root path, which is the workflow's shape."""
    by_name = {i["name"]: i for i in model.get("items", [])}
    return [by_name[n]["type"] for n in root_path_items(model) if n in by_name]


def has_cycle(model: dict) -> bool:
    """A cycle over control edges. vRO allows them; Kestra flows are a DAG."""
    adj = build_adjacency(model, kinds=("next", "alt", "case", "error"))
    colour: dict[str, int] = {}

    def visit(n: str) -> bool:
        if colour.get(n) == 1:
            return True
        if colour.get(n) == 2:
            return False
        colour[n] = 1
        for m in adj.get(n, []):
            if m in adj and visit(m):
                return True
        colour[n] = 2
        return False

    return any(visit(n) for n in adj)


def max_depth(model: dict) -> int:
    """Longest simple path from the root, bounded so a pathological graph cannot hang."""
    adj = build_adjacency(model)
    start = model.get("root_item") or ""
    if start not in adj:
        return 0
    best = 0
    stack = [(start, frozenset([start]), 0)]
    steps = 0
    while stack:
        steps += 1
        if steps > 200_000:
            break
        n, seen, d = stack.pop()
        best = max(best, d)
        for m in adj.get(n, []):
            if m in adj and m not in seen:
                stack.append((m, seen | {m}, d + 1))
    return best


def shape(model: dict) -> dict:
    adj = build_adjacency(model)
    indeg: dict[str, int] = defaultdict(int)
    for _n, outs in adj.items():
        for m in outs:
            indeg[m] += 1
    branches = sum(1 for n, outs in adj.items() if len(outs) > 1)
    joins = sum(1 for n, d in indeg.items() if d > 1)
    reachable = root_path_items(model)
    handler_path = error_handler_path_items(model)
    all_items = [i["name"] for i in model.get("items", [])]
    return {
        "items": len(all_items),
        "edges": len(model.get("edges", [])),
        "branches": branches,
        "joins": joins,
        "cycle": has_cycle(model),
        "depth": max_depth(model),
        "reachable_from_root": len(reachable),
        "error_handler_path": handler_path,
        "unreachable_items": sorted(set(all_items) - set(reachable) - set(handler_path)),
        "linear": branches == 0 and joins == 0,
    }
