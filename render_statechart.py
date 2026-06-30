#!/usr/bin/env python3
"""
render_statechart.py — XState v5 Harel statechart JSON → one self-contained HTML viewer.

A standalone, pure-standard-library program built on the `harel-statechart-render`
skill. It reproduces the skill's pipeline in a single file:

  1. Walk the XState v5 `createMachine` config (states, transitions, history,
     parallel, entry/exit, `meta` provenance, `meta.io` external boundary) into
     an ELK graph + a flat search index — the logic of the skill's
     scripts/build_elk_graph.py.
  2. Emit a single HTML file that inlines elkjs (browser build), d3, the skill's
     viewer.js/viewer.css, and the graph data. ELK layout runs **in the browser**
     on load (see vendor/layout_boot.js), so this program needs no Node — and the
     output opens in any browser with no server and no network.

Fidelity rule (inherited from the skill): render Harel faithfully or annotate the
gap — never silently draw UML. Every glyph hint traces to a real XState field or a
`meta.harel` annotation; subset-inexpressible features are recorded as hints for the
viewer, never upgraded to a confident-but-false picture.

Usage:
    python render_statechart.py machine.json [-o diagram.html]
    python render_statechart.py machine.json --machine-key machine   # bundle → pick .machine
    cobol-xstate prog.cbl --machine-only | python render_statechart.py -  -o prog.html

The input may be either a bare XState v5 config (has `states`) or the cobol-xstate
bundle (`{"machine": {...}, ...}`); the bundle's `machine` is used automatically.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import webbrowser
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
VENDOR = os.path.join(HERE, "vendor")

# d3/elkjs CDN fallbacks if a vendored copy is missing (output then needs network).
CDN = {
    "d3.min.js": "https://cdn.jsdelivr.net/npm/d3@7/dist/d3.min.js",
    "elk.bundled.js": "https://cdn.jsdelivr.net/npm/elkjs@0.9.3/lib/elk.bundled.js",
}


# ===========================================================================
# Part 1 — XState v5 config → ELK graph + search index
# (a faithful port of the skill's scripts/build_elk_graph.py)
# ===========================================================================

def state_kind(node):
    t = node.get("type")
    if t == "parallel":
        return "and"
    if t == "history":
        return "history"
    if t == "final":
        return "final"
    if "states" in node and node["states"]:
        return "or"
    return "basic"


def history_depth(node):
    return node.get("history", "shallow")


def norm_actions(val):
    if val is None:
        return []
    if isinstance(val, str):
        return [val]
    if isinstance(val, dict):
        return [val.get("type", json.dumps(val))]
    if isinstance(val, list):
        out = []
        for a in val:
            out.extend(norm_actions(a))
        return out
    return [str(val)]


def norm_guard(val):
    if val is None:
        return None
    if isinstance(val, str):
        return val
    if isinstance(val, dict):
        return val.get("type", json.dumps(val))
    return str(val)


def _event_from_action(a):
    s = a.strip()
    for sep in ("(", ":", " "):
        if sep in s:
            cand = s.split(sep, 1)[1].strip(" )'\"")
            if cand:
                return cand
    return s


def iter_raised_events(val):
    """Yield the event name(s) a `raise` action produces, from either the
    string form (`"raise(FOO)"`) or the structured form
    (`{"type":"raise","event":"FOO"}` / `{"type":"xstate.raise",
    "event":{"type":"FOO"}}`). Structured actions are what the
    xstate-cobol-contract skill emits, so missing them here would
    misclassify an internally-raised event as an external input."""
    if val is None:
        return
    if isinstance(val, list):
        for a in val:
            yield from iter_raised_events(a)
        return
    if isinstance(val, dict):
        if "raise" in (val.get("type") or "").lower():
            ev = val.get("event")
            if isinstance(ev, dict) and ev.get("type"):
                yield ev["type"]
            elif isinstance(ev, str) and ev:
                yield ev
        return
    if isinstance(val, str):
        low = val.strip().lower()
        if low.startswith("raise") or "raise(" in low:
            name = _event_from_action(val.strip())
            if name and name.lower() != "raise":
                yield name


def iter_transitions(node):
    on = node.get("on", {})
    for event, t in on.items():
        for one in (t if isinstance(t, list) else [t]):
            yield event, one
    for one in (node.get("always", []) or []):
        yield "ε(always)", one
    after = node.get("after", {})
    if isinstance(after, dict):
        for delay, t in after.items():
            for one in (t if isinstance(t, list) else [t]):
                yield f"after({delay})", one


def resolve_target(source_path, target):
    if target is None:
        return None
    if isinstance(target, list):
        target = target[0] if target else None
    if target is None:
        return None
    if target.startswith("#"):
        return target
    if target.startswith("."):
        return source_path + target
    parent = ".".join(source_path.split(".")[:-1])
    return (parent + "." + target) if parent else target


def walk(node, path, elk_nodes, edges, index, depth, raised):
    kind = state_kind(node)
    name = path.split(".")[-1] if path else node.get("id", "root")

    meta = node.get("meta", {}) or {}
    harel = meta.get("harel", {}) or {}
    prov = meta.get("provenance", {}) or {}
    io = meta.get("io", {}) or {}

    elk_node = {
        "id": path,
        "labels": [{"text": name}],
        "kind": kind,
        "depth": depth,
        "harel": {
            "trigger": harel.get("trigger"),
            "sensing": harel.get("sensing"),
            "staticReactions": harel.get("staticReactions", []),
            "activities": harel.get("activities", []),
            "broadcast": harel.get("broadcast", []),
        },
        "entry": norm_actions(node.get("entry")),
        "exit": norm_actions(node.get("exit")),
        "provenance": prov,
        "io": {
            "inputs": io.get("inputs", []),
            "outputs": io.get("outputs", []),
        },
        "children": [],
        "edges": [],
    }
    if kind == "history":
        elk_node["historyDepth"] = history_depth(node)
    if "description" in node:
        elk_node["description"] = node["description"]
    if node.get("id"):
        elk_node["xstateId"] = node["id"]
    # Some emitters (e.g. the COBOL→XState control-flow lowering) record the
    # source location directly on the state's `meta` as `cobolLine`/`kind`
    # instead of a full `meta.provenance` block. Surface both so the viewer's
    # tooltip can show "line N · GOBACK" even when there's no paragraph record.
    if meta.get("cobolLine") is not None:
        elk_node["cobolLine"] = meta.get("cobolLine")
    if meta.get("kind"):
        elk_node["sourceKind"] = meta.get("kind")

    # entry/exit actions can raise internally-produced events too.
    for ev in iter_raised_events(node.get("entry")):
        raised.add(ev)
    for ev in iter_raised_events(node.get("exit")):
        raised.add(ev)

    index["states"].append({
        "id": path, "name": name, "path": path, "kind": kind, "provenance": prov,
    })
    if prov:
        index["provenance"].append({
            "stateId": path,
            "paragraph": prov.get("cobolParagraph"),
            "file": prov.get("file"),
            "lines": prov.get("sourceLines"),
        })

    initial = node.get("initial")
    if kind == "or" and initial is not None:
        elk_node["initial"] = f"{path}.{initial}" if path else initial

    for key, child in (node.get("states", {}) or {}).items():
        child_path = f"{path}.{key}" if path else key
        walk(child, child_path, elk_nodes, edges, index, depth + 1, raised)
        elk_node["children"].append(child_path)

    for event, t in iter_transitions(node):
        if isinstance(t, str):
            t = {"target": t}
        target = resolve_target(path, t.get("target"))
        guard = norm_guard(t.get("guard") or t.get("cond"))
        actions = norm_actions(t.get("actions"))
        for ev in iter_raised_events(t.get("actions")):
            raised.add(ev)
        # A transition's own `meta` carries the program logic the user wants in
        # edge tooltips: `kind` (seq / loop-exit / goto / io-handler / when…),
        # a human `note` ("GO TO - no return", "AT_END"), and the `cobolLine`.
        # Drop empties so edges without meta stay clean.
        tmeta = t.get("meta") or {}
        emeta = {k: tmeta[k] for k in ("kind", "note", "cobolLine")
                 if tmeta.get(k) is not None}
        edge = {
            "id": f"e{len(edges)}",
            "source": path,
            "target": target,
            "event": event,
            "guard": guard,
            "actions": actions,
            "meta": emeta or None,
            "internal": target is None,
        }
        edges.append(edge)
        index["transitions"].append({
            "source": path, "target": target, "event": event,
            "guard": guard, "actions": actions, "meta": emeta or None,
        })
        if (event and not event.startswith(("ε(", "after("))
                and event not in index["events"]):
            index["events"].append(event)
        if guard and guard not in index["guards"]:
            index["guards"].append(guard)

    elk_nodes[path] = elk_node
    return elk_node


def build_external_io(elk_nodes, index, raised, io_meta):
    endpoints = {}
    for ep in io_meta.get("endpoints", []):
        endpoints[ep["id"]] = {
            "id": ep["id"], "kind": ep.get("kind", "external"),
            "label": ep.get("label", ep["id"]),
        }
    fields = {fld["id"]: fld for fld in io_meta.get("fields", [])}
    index["endpoints"] = list(endpoints.values())
    index["fields"] = list(fields.values())

    produced = set(raised)
    for n in elk_nodes.values():
        for b in (n.get("harel", {}).get("broadcast") or []):
            if b.get("event"):
                produced.add(b["event"])
    consumed = set(index["events"])
    external_inputs = sorted(
        e for e in consumed
        if e and not e.startswith(("ε(", "after(")) and e not in produced
    )

    boundary_nodes = {}
    boundary_edges = []

    def ensure_boundary(ep_id, kind_hint=None):
        if ep_id in boundary_nodes:
            return boundary_nodes[ep_id]
        ep = endpoints.get(ep_id, {"id": ep_id, "kind": kind_hint or "external", "label": ep_id})
        node = {"id": f"__io__{ep_id}", "endpointId": ep_id, "kind": ep["kind"], "label": ep["label"]}
        boundary_nodes[ep_id] = node
        return node

    def field_label(ref):
        if "field" in ref and ref["field"] in fields:
            f = fields[ref["field"]]
            pic = f.get("picture")
            return f["name"] + (f" PIC {pic}" if pic else "")
        return ref.get("field") or ref.get("event") or "?"

    for path, n in elk_nodes.items():
        io = n.get("io", {})
        for direction in ("inputs", "outputs"):
            for ref in io.get(direction, []):
                ep_id = ref.get("endpoint")
                if not ep_id:
                    continue
                bn = ensure_boundary(ep_id, ref.get("endpointKind"))
                kind = "event" if ref.get("event") else "field"
                label = ref.get("event") or field_label(ref)
                boundary_edges.append({
                    "id": f"io{len(boundary_edges)}", "endpoint": ep_id,
                    "endpointNode": bn["id"], "state": path,
                    "direction": "in" if direction == "inputs" else "out",
                    "kind": kind, "label": label,
                    "fieldId": ref.get("field"), "event": ref.get("event"),
                })
                n.setdefault("ioBadges", {"in": [], "out": []})
                n["ioBadges"]["in" if direction == "inputs" else "out"].append(
                    {"kind": kind, "label": label, "endpoint": ep_id})
                bucket = "inputs" if direction == "inputs" else "outputs"
                index[bucket].append({
                    "state": path, "endpoint": ep_id, "kind": kind,
                    "label": label, "event": ref.get("event"), "field": ref.get("field"),
                })

    declared_input_events = {be["event"] for be in boundary_edges if be["event"]}
    for ev in external_inputs:
        if ev in declared_input_events:
            continue
        bn = ensure_boundary("__unspecified_in__")
        bn["kind"] = "external"
        bn["label"] = "external (unspecified)"
        for t in index["transitions"]:
            if t["event"] == ev:
                boundary_edges.append({
                    "id": f"io{len(boundary_edges)}", "endpoint": "__unspecified_in__",
                    "endpointNode": bn["id"], "state": t["source"], "direction": "in",
                    "kind": "event", "label": ev, "event": ev, "unconfirmedEndpoint": True,
                })
                elk_nodes[t["source"]].setdefault("ioBadges", {"in": [], "out": []})
                elk_nodes[t["source"]]["ioBadges"]["in"].append(
                    {"kind": "event", "label": ev, "endpoint": None, "unconfirmed": True})
                index["inputs"].append({
                    "state": t["source"], "endpoint": None, "kind": "event",
                    "label": ev, "event": ev, "field": None, "unconfirmedEndpoint": True,
                })

    return {
        "nodes": list(boundary_nodes.values()),
        "edges": boundary_edges,
        "externalInputEvents": external_inputs,
    }


def build_graph(machine):
    """XState v5 config dict -> {root, nodes, edges, boundary, index}."""
    root_path = machine.get("id", "root")
    elk_nodes, edges = {}, []
    index = {"states": [], "transitions": [], "events": [], "guards": [],
             "provenance": [], "inputs": [], "outputs": [], "endpoints": [], "fields": []}
    raised = set()
    walk(machine, root_path, elk_nodes, edges, index, 0, raised)

    # Resolve `#id` targets. XState `#foo` references a state's explicit `id:`
    # field (any custom string), optionally with a `.child.tail` below it.
    # Prefer the explicit id; fall back to the local path-segment name, but
    # only when that name is unique — colliding local names (idle, done, …)
    # would otherwise resolve to whichever state happened to be walked last.
    explicit, local, ambiguous_local = {}, {}, set()
    for path, n in elk_nodes.items():
        xid = n.get("xstateId")
        if xid:
            explicit[xid] = path
        seg = path.split(".")[-1]
        if seg in local:
            ambiguous_local.add(seg)
        else:
            local[seg] = path
    for e in edges:
        tgt = e["target"]
        if isinstance(tgt, str) and tgt.startswith("#"):
            head, _, tail = tgt[1:].partition(".")
            base = explicit.get(head)
            if base is None and head in local and head not in ambiguous_local:
                base = local[head]
            if base is not None:
                e["target"] = f"{base}.{tail}" if tail else base
            elif head in ambiguous_local:
                e["ambiguous"] = True  # left unresolved rather than guessed
            e["unresolved"] = isinstance(e["target"], str) and e["target"].startswith("#")

    io_meta = (machine.get("meta", {}) or {}).get("io", {}) or {}
    boundary = build_external_io(elk_nodes, index, raised, io_meta)

    return {"root": root_path, "nodes": elk_nodes, "edges": edges,
            "boundary": boundary, "index": index}


# ===========================================================================
# Part 2 — assemble the self-contained HTML
# ===========================================================================

def _escape_for_inline(text):
    """Make text safe to embed inside an HTML <script>/<style> element."""
    return text.replace("</script>", "<\\/script>").replace("</style>", "<\\/style>")


def read_vendor(name):
    """Return (inline_text_or_None, used_cdn_bool). None text => asset missing, use CDN tag."""
    path = os.path.join(VENDOR, name)
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            return f.read(), False
    return None, True


def script_block(name):
    """Inline <script> for a vendored lib, or a CDN <script src> with a note."""
    text, used_cdn = read_vendor(name)
    if not used_cdn:
        return f"<script>{_escape_for_inline(text)}</script>", False
    url = CDN.get(name)
    if not url:
        raise FileNotFoundError(f"missing vendored asset and no CDN fallback: {name}")
    return f'<script src="{url}"></script>', True


HTML_SHELL = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>{css}</style>
<style>
#boot-status{{position:fixed;inset:0;display:flex;align-items:center;justify-content:center;
  background:var(--bg,#0e1116);color:var(--fg,#cdd6e4);font:14px/1.5 system-ui,sans-serif;z-index:50}}
#boot-status.error{{color:#ff8a8a}}
#boot-status .boot-msg::before{{content:"\\2699  "}}
</style>
</head><body>
<div id="app">
  <div id="bar">
    <span class="title">{title}</span>
    <input id="search" placeholder="Search states, events, guards, COBOL provenance…  ( / )">
    <label class="toggle"><input type="checkbox" id="filterToggle">filter</label>
    <input id="radius" type="range" min="0" max="3" value="1" title="neighborhood radius">
    <span id="matchcount"></span>
    <span class="spacer"></span>
    <button id="fitBtn">Fit (f)</button>
    <button id="mmBtn">Mini-map (m)</button>
    <button id="lgBtn">Legend</button>
  </div>
  <div id="stage" data-lod="2">
    <svg id="canvas"></svg>
    <div id="inspector"></div>
    <div id="legend">
      <h4>Legend</h4>
      <div class="li"><span class="sw" style="background:#fff;border-color:#4a6fa5;box-shadow:inset 3px 0 0 #4a72b0"></span>COBOL paragraph</div>
      <div class="li"><span class="sw" style="background:#fff9ed;border-color:#dcb368;box-shadow:inset 3px 0 0 #d59a32"></span>decision (IF / EVALUATE / loop)</div>
      <div class="li"><span class="sw" style="background:#f3f9f8;border-color:#bcd6d2;box-shadow:inset 3px 0 0 #4f9b91"></span>I/O step</div>
      <div class="li"><span class="sw" style="background:#f6f8fb;border-color:#d8dfe9"></span>sequential step</div>
      <div class="li"><span class="sw" style="background:#fff;border-width:2.2px"></span>final · <span class="sw" style="background:#eef3fb;border-color:#9fb2d2"></span>OR · <span class="sw" style="background:#eaf6ef;border-color:#97c6ac"></span>AND</div>
      <div class="li">Ⓒ / Ⓒ* history · ● default entry</div>
      <div class="li"><span style="color:#1f6f8b;font-weight:700">– –▶</span> conditional [guard] · <span style="color:#aab2c0;font-weight:700">──▶</span> sequential</div>
      <div class="li"><span style="color:#b5651d">– – –</span> broadcast · <span style="color:#d98a00">▭</span> search hit</div>
      <div class="semantics" id="semantics"></div>
    </div>
    <div id="minimap"></div>
  </div>
</div>
<div id="boot-status"><span class="boot-msg">Loading…</span></div>
{d3}
{elk}
<script type="application/json" id="raw-graph">{graph}</script>
<script type="text/plain" id="viewer-src">{viewer}</script>
<script>{boot}</script>
</body></html>
"""


def render_html(machine, title=None):
    graph = build_graph(machine)
    css, _ = read_vendor("viewer.css")
    viewer, _ = read_vendor("viewer.js")
    boot, _ = read_vendor("layout_boot.js")
    if css is None or viewer is None or boot is None:
        missing = [n for n, v in
                   (("viewer.css", css), ("viewer.js", viewer), ("layout_boot.js", boot))
                   if v is None]
        raise FileNotFoundError("missing vendored asset(s): " + ", ".join(missing)
                                + f" (looked in {VENDOR})")

    d3_tag, d3_cdn = script_block("d3.min.js")
    elk_tag, elk_cdn = script_block("elk.bundled.js")

    title = title or (machine.get("id", "statechart") + " — Harel statechart")
    html = HTML_SHELL.format(
        title=title,
        css=_escape_for_inline(css),
        d3=d3_tag, elk=elk_tag,
        graph=_escape_for_inline(json.dumps(graph)),
        viewer=_escape_for_inline(viewer),
        boot=_escape_for_inline(boot),
    )
    return html, graph, (d3_cdn or elk_cdn)


# ===========================================================================
# Part 3 — CLI
# ===========================================================================

def extract_machine(doc, machine_key="machine"):
    """Accept a bare XState config or the cobol-xstate bundle; return the config."""
    if isinstance(doc, dict) and "states" in doc:
        return doc
    if isinstance(doc, dict) and isinstance(doc.get(machine_key), dict):
        return doc[machine_key]
    raise ValueError(
        "input is neither a bare XState v5 config (needs `states`) nor a bundle "
        f"with a `{machine_key}` object")


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="XState v5 Harel statechart JSON -> self-contained interactive HTML")
    ap.add_argument("machine", help="path to XState v5 JSON, or '-' for stdin")
    ap.add_argument("-o", "--out", help="output HTML (default: <machine>.html, or ./statechart.html for stdin)")
    ap.add_argument("--machine-key", default="machine",
                    help="bundle key holding the XState config (default: machine)")
    ap.add_argument("--title", help="override the diagram title")
    ap.add_argument("--open", action="store_true", dest="open_browser",
                    help="open the written HTML in the default browser")
    args = ap.parse_args(argv)

    if args.machine == "-":
        doc = json.load(sys.stdin)
        default_out = "statechart.html"
    else:
        with open(args.machine, encoding="utf-8") as f:
            doc = json.load(f)
        default_out = args.machine.rsplit(".", 1)[0] + ".html"

    machine = extract_machine(doc, args.machine_key)
    out = args.out or default_out

    html, graph, used_cdn = render_html(machine, title=args.title)
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)

    idx = graph["index"]
    nb = graph["boundary"]
    print(f"Wrote {out}  ({len(html) // 1024} KB)")
    print(f"  states={len(idx['states'])} transitions={len(idx['transitions'])} "
          f"events={len(idx['events'])} guards={len(idx['guards'])} "
          f"boundary={len(nb['nodes'])} endpoints")
    if used_cdn:
        print("  note: a vendored lib was missing; output uses a CDN <script> and "
              "needs network to open. Re-vendor viz/vendor/ for a fully offline file.")
    else:
        print("  layout: in-browser elkjs (no Node needed) · fully offline, self-contained")

    if args.open_browser:
        uri = Path(out).resolve().as_uri()
        webbrowser.open(uri)
        print(f"  opened {uri}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
