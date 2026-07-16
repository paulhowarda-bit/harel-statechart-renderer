#!/usr/bin/env python3
"""
validate_render.py — fidelity check for a recovered statechart before/after render.

Enforces the one rule: render Harel faithfully, or annotate the gap — never
silently drop a feature. Reports against the three-rung fidelity ladder:

  rung 1 (faithful)  — features that WILL render as distinct glyphs
  rung 2 (annotated) — features inexpressible in plain XState but recorded in
                       meta.harel, so the renderer annotates them
  rung 3 (flagged)   — features implied but NOT recorded; reported as unconfirmed

plus an INTEGRITY section above the ladder for things that are missing from the
picture outright — an edge drawn to nowhere, or a whole sub-chart never merged
in. The ladder's rungs are all honest outcomes; an integrity failure is not.

This builds the graph through `render_statechart.build_graph`, with the same
bundle handling as the renderer (`charts` sub-charts, the `interface` perimeter,
the data dictionary). That is the point: a checker that walks the machine itself
drifts from the renderer and then certifies a picture nobody draws.

Usage:
    python3 validate_render.py machine.json [--strict]

Exit 0 if nothing is silently lost, 1 if it is (`--strict` also fails rung 3).
"""
from __future__ import annotations

import argparse
import sys

import render_statechart as R


def _count_states(node):
    return sum(1 + _count_states(c) for c in (node.get("states") or {}).values())


def check(graph, doc=None):
    """-> (faithful, annotated, flagged, dropped) — the three rungs + integrity.

    `doc` is the original bundle. Pass it: some losses are invisible from inside
    the graph (see the `charts` check below), so a graph-only check cannot find
    them."""
    nodes = graph["nodes"]
    faithful, annotated, flagged, dropped = [], [], [], []

    # ---- integrity: is the whole program actually in the picture? -----------
    # These are real silent losses, not un-annotated features: the renderer
    # still emits a plausible diagram, which is exactly what makes them costly.
    for e in graph["edges"]:
        if e.get("danglingTarget"):
            dropped.append(f"{e['source']}: transition to '{e['target']}' — no such state "
                           "(the edge is dropped and ELK layout can abort)")
        elif e.get("ambiguous"):
            dropped.append(f"{e['source']}: transition target is ambiguous — left "
                           "unresolved rather than guessed")
        elif e.get("unresolved"):
            dropped.append(f"{e['source']}: transition target '{e['target']}' unresolved")
        elif e.get("recoveredTarget"):
            annotated.append(f"{e['source']}: cross-level target recovered by unique "
                             f"name -> {e['target']}")

    # A renderer that ignores the bundle's `charts` emits a perfectly
    # self-consistent graph: a tidy skeleton, every edge resolved, nothing to
    # find. The evidence that the program body is missing exists ONLY in the
    # bundle — so compare against it. Checking the graph alone would certify
    # exactly the silent drop this tool exists to catch.
    ci = graph.get("charts") or {}
    doc_charts = (doc or {}).get("charts") or {}
    if doc_charts and not ci.get("inlined"):
        n = sum(_count_states(c) for c in doc_charts.values() if isinstance(c, dict))
        dropped.append(f"bundle carries {len(doc_charts)} `charts` sub-chart(s) "
                       f"(~{n} states) that are not in the diagram — the body of the "
                       f"program is missing; only {len(nodes)} state(s) were drawn")
    for key in (ci.get("nameClashes") or []):
        dropped.append(f"sub-chart state '{key}' collided with the machine's own name "
                       "and was not drawn")
    for name in (ci.get("uncalled") or []):
        flagged.append(f"sub-chart '{name}': nothing PERFORMs it — its return target is "
                       "unknown, so the return edge is not drawn")

    # ---- external I/O boundary ---------------------------------------------
    b = graph.get("boundary") or {"nodes": [], "edges": [], "externalInputEvents": []}
    for be in b["edges"]:
        if be.get("unconfirmedEndpoint"):
            flagged.append(f"{be['state']}: external input '{be['label']}' detected but no "
                           "endpoint recorded — endpoint unconfirmed")
        else:
            d = "input <-" if be["direction"] == "in" else "output ->"
            annotated.append(f"{be['state']}: {d} {be['label']} @ {be['endpoint']} ({be['kind']})")
    for ev in b.get("externalInputEvents", []):
        faithful.append(f"external input event detected: {ev}")

    # ---- the Harel ladder, per state ---------------------------------------
    for path, n in nodes.items():
        k = n["kind"]
        h = n["harel"]

        # rung 1 — structural features that render as glyphs
        if k == "and":
            faithful.append(f"{path}: AND-state -> orthogonal regions")
        if k == "history":
            faithful.append(f"{path}: history -> {n.get('historyDepth', 'shallow')} glyph")
        if k == "or" and n.get("initial"):
            faithful.append(f"{path}: default entry -> dot")
        if n["entry"] or n["exit"]:
            faithful.append(f"{path}: entry/exit actions -> compartments")

        # rung 2 — recorded annotations
        if h.get("trigger"):
            annotated.append(f"{path}: event algebra '{h['trigger']}' -> annotated")
        if h.get("staticReactions"):
            annotated.append(f"{path}: {len(h['staticReactions'])} static reaction(s) -> SR compartment")
        if h.get("activities"):
            annotated.append(f"{path}: {len(h['activities'])} activity(ies) -> badge")
        if h.get("broadcast"):
            annotated.append(f"{path}: {len(h['broadcast'])} broadcast -> dashed annotation")
        if h.get("sensing"):
            annotated.append(f"{path}: sensing '{h['sensing']}' -> legend/edge tag")

        # rung 3 — likely-implied-but-unrecorded
        # AND-states almost always involve broadcast; flag if none recorded
        if k == "and" and not h.get("broadcast"):
            flagged.append(f"{path}: AND-state with no recorded broadcast — "
                           "orthogonal-region interaction unconfirmed from input")
        # `always` transitions approximate static reactions; flag if SRs absent
        for e in graph["edges"]:
            if e["source"] == path and e["event"] == "ε(always)" and not h.get("staticReactions"):
                flagged.append(f"{path}: uses XState `always` but no recorded static reaction — "
                               "SR semantics approximated, not confirmed")
                break

    return faithful, annotated, flagged, dropped


def report(faithful, annotated, flagged, dropped, out=print):
    out("FIDELITY REPORT")
    out("=" * 60)
    if dropped:
        out(f"\n[INTEGRITY] Silently lost from the diagram ({len(dropped)}):")
        for x in dropped:
            out(f"  X {x}")
    out(f"\n[rung 1] Faithful glyphs ({len(faithful)}):")
    for x in faithful:
        out(f"  + {x}")
    out(f"\n[rung 2] Annotated (subset-inexpressible, recorded) ({len(annotated)}):")
    for x in annotated:
        out(f"  ~ {x}")
    out(f"\n[rung 3] Flagged unconfirmed ({len(flagged)}):")
    for x in flagged:
        out(f"  ! {x}")
    if not flagged:
        out("  (none)")

    out("\n" + "=" * 60)
    if dropped:
        out(f"FAIL: {len(dropped)} construct(s) are missing from the picture, not merely")
        out("unannotated. The diagram is incomplete and does not say so.")
    elif flagged:
        out(f"{len(flagged)} feature(s) implied but not recorded — annotated as unconfirmed.")
        out("This is expected when the upstream XState lacks meta.harel. Nothing was")
        out("silently upgraded to look faithful.")
    else:
        out("No unconfirmed features. Every Harel construct is faithful or annotated.")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Harel render fidelity check")
    ap.add_argument("machine", help="path to XState v5 JSON (bare config or bundle), "
                                    "or '-' for stdin")
    ap.add_argument("--machine-key", default="machine",
                    help="bundle key holding the XState config (default: machine)")
    ap.add_argument("--strict", action="store_true",
                    help="also exit non-zero on rung-3 flags (implied but unrecorded)")
    args = ap.parse_args(argv)

    # The Windows console defaults to cp1252, which cannot encode the report's
    # em dashes — without this the run dies on UnicodeEncodeError mid-report.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    text = sys.stdin.read() if args.machine == "-" else \
        open(args.machine, encoding="utf-8").read()
    doc = R._load_doc(text)
    machine = R.extract_machine(doc, args.machine_key)
    d = doc if isinstance(doc, dict) else {}
    graph = R.build_graph(machine, d.get("data"), d.get("semantics"),
                          d.get("interface"), d.get("charts"))

    faithful, annotated, flagged, dropped = check(graph, d)
    report(faithful, annotated, flagged, dropped)
    if dropped:
        return 1
    return 1 if (args.strict and flagged) else 0


if __name__ == "__main__":
    sys.exit(main())
