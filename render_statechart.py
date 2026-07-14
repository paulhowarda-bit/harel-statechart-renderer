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
import re
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


_IO_NAME = r'([A-Z0-9$#@][A-Z0-9$#@._-]*)'


def _classify_io_action(a):
    """Recognize an I/O operation captured as an action and map it to an external
    endpoint. Returns (endpoint_id, kind, endpoint_label, direction, edge_label) or
    None. Direction is 'in' (into the program) or 'out'. OPEN/CLOSE are lifecycle,
    not data flow, so a bare CLOSE (or OPEN without a mode) yields nothing —
    READ/WRITE of the same file already surface it."""
    u = str(a).strip().upper()
    # EXEC SQL <verb> -> Db2. (The runnable .mjs flattens the statement, so the
    # table is unknown here; the JSON bundle's semantics carry it — see BACKLOG.)
    m = re.match(r'EXEC[ _]+SQL[ _]+([A-Z]+)', u)   # [A-Z]+ so \w doesn't eat the '_'
    if m:
        v = m.group(1)
        d = "out" if v in ("INSERT", "UPDATE", "DELETE", "MERGE") else "in"
        return ("db2", "db2", "Db2 (SQL)", d, "SQL " + v)
    # EXEC CICS <verb>: LINK/XCTL are calls, SEND/RECEIVE terminal, READ/WRITE a
    # file; HANDLE/RETURN/etc. are control flow, not I/O.
    m = re.match(r'EXEC[ _]+CICS[ _]+([A-Z]+)(?:[ _]+' + _IO_NAME + r')?', u)
    if m:
        v, tgt = m.group(1), m.group(2)
        if v in ("LINK", "XCTL") and tgt:
            return ("call:" + tgt, "subprogram", tgt, "out", "CICS " + v + " " + tgt)
        if v in ("SEND", "RECEIVE"):
            return ("cics", "cics", "CICS terminal", "out" if v == "SEND" else "in", "CICS " + v)
        if v in ("READ", "WRITE", "REWRITE", "STARTBR", "READNEXT") and tgt:
            d = "out" if v in ("WRITE", "REWRITE") else "in"
            return ("file:" + tgt, "file", tgt, d, "CICS " + v + " " + tgt)
        return None
    m = re.match(r'(LINK|XCTL)[ _]+' + _IO_NAME, u)   # link_POSTLOG / xctl_CLOSEDPG
    if m:
        return ("call:" + m.group(2), "subprogram", m.group(2), "out", "CICS " + m.group(1) + " " + m.group(2))
    m = re.match(r'(?:EXEC\s+)?CALL[ _]+' + _IO_NAME, u)
    if m:
        return ("call:" + m.group(1), "subprogram", m.group(1), "out", "CALL " + m.group(1))
    if u.startswith("DISPLAY"):
        return ("console", "console", "console (SYSOUT)", "out", "DISPLAY")
    if u.startswith("ACCEPT"):
        return ("console", "console", "console (SYSIN)", "in", "ACCEPT")
    m = re.match(r'OPEN[ _]+(INPUT|OUTPUT|I-O|EXTEND)[ _]+' + _IO_NAME, u)
    if m:
        d = "in" if m.group(1) == "INPUT" else "out"
        return ("file:" + m.group(2), "file", m.group(2), d, "OPEN " + m.group(1))
    m = re.match(r'(READ|WRITE|REWRITE|DELETE|START)[ _]+' + _IO_NAME, u)
    if m:
        d = "in" if m.group(1) in ("READ", "START") else "out"
        return ("file:" + m.group(2), "file", m.group(2), d, m.group(1) + " " + m.group(2))
    return None


def derive_io_from_actions(elk_nodes):
    """Synthesize the external I/O boundary from I/O verbs captured as entry/exit
    actions, for machines that carry no `meta.io` (the COBOL→XState lowering emits
    I/O as actions like `read_TRAN-FILE` / `call_POSTLOG` / `DISPLAY_…`, not as the
    structured boundary). Populates each state's `io.inputs/outputs` and returns the
    derived endpoint list, so the input/output events render as endpoints + arrows
    instead of buried action text. Heuristic — keyed on COBOL I/O verb names."""
    endpoints = {}
    for n in elk_nodes.values():
        refs_in, refs_out = [], []
        for a in (n.get("entry") or []) + (n.get("exit") or []):
            c = _classify_io_action(a)
            if not c:
                continue
            ep_id, kind, ep_label, direction, edge_label = c
            endpoints.setdefault(ep_id, {"id": ep_id, "kind": kind, "label": ep_label})
            (refs_in if direction == "in" else refs_out).append(
                {"event": edge_label, "endpoint": ep_id})
        if refs_in or refs_out:
            io = n.setdefault("io", {"inputs": [], "outputs": []})
            io.setdefault("inputs", []).extend(refs_in)
            io.setdefault("outputs", []).extend(refs_out)
    return list(endpoints.values())


def build_external_io(elk_nodes, index, raised, io_meta, data=None):
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
                label = ref.get("label") or ref.get("event") or field_label(ref)
                flds = ref.get("fields") or []
                fdetail = _fields_detail(flds, data)
                boundary_edges.append({
                    "id": f"io{len(boundary_edges)}", "endpoint": ep_id,
                    "endpointNode": bn["id"], "state": path,
                    "direction": "in" if direction == "inputs" else "out",
                    "kind": kind, "label": label, "fields": flds, "fieldsDetail": fdetail,
                    "fieldId": ref.get("field"), "event": ref.get("event"),
                })
                n.setdefault("ioBadges", {"in": [], "out": []})
                n["ioBadges"]["in" if direction == "inputs" else "out"].append(
                    {"kind": kind, "label": label, "fields": flds,
                     "fieldsDetail": fdetail, "endpoint": ep_id})
                bucket = "inputs" if direction == "inputs" else "outputs"
                index[bucket].append({
                    "state": path, "endpoint": ep_id, "kind": kind,
                    "label": label, "fields": flds, "fieldsDetail": fdetail,
                    "event": ref.get("event"), "field": ref.get("field"),
                })

    declared_input_events = {be["event"] for be in boundary_edges if be["event"]}
    # skip the "unspecified external input" guesswork when the generator's
    # interface already gave us the authoritative perimeter.
    for ev in ([] if io_meta.get("fromInterface") else external_inputs):
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


def compute_paragraph_grouping(elk_nodes, root_path):
    """Group a flat COBOL control-flow machine by paragraph for collapse/expand.

    The COBOL→XState lowering emits each paragraph and each lowered construct
    (`PARA__if5`, `PARA__seq6`, …) as a TOP-LEVEL sibling. We group them by the
    paragraph prefix (the text before `__`) so the viewer can render the program
    as a handful of collapsible paragraph nodes instead of a flat sea of boxes.

    Enabled only for a flat machine (no top-level state is itself a container —
    so genuinely-nested Harel machines like `posting` are left as-is) that uses
    the `__` naming. Returns {enabled, order, groups, entry, paragraphOf} or
    {enabled: False}.
    """
    root = elk_nodes.get(root_path)
    top = (root or {}).get("children", []) if root else []
    if not top:
        return {"enabled": False}

    groups, order, any_synth = {}, [], False
    for cid in top:
        if elk_nodes[cid].get("children"):
            return {"enabled": False}            # a nested machine — don't group
        name = cid.split(".")[-1]
        para = name.split("__", 1)[0] or name    # "__END__" -> "__END__"
        if "__" in name:
            any_synth = True
        if para not in groups:
            groups[para] = []
            order.append(para)
        groups[para].append(cid)
    if not any_synth:
        return {"enabled": False}                # already paragraph-level; nothing to fold

    # The paragraph's entry member is the bare state named exactly like the
    # paragraph (the PERFORM/fall-through entry point); fall back to the first.
    entry = {}
    for para, members in groups.items():
        entry[para] = next((m for m in members if m.split(".")[-1] == para), members[0])

    paragraph_of = {cid: (cid.split(".")[-1].split("__", 1)[0] or cid.split(".")[-1])
                    for cid in top}
    return {"enabled": True, "order": order, "groups": groups,
            "entry": entry, "paragraphOf": paragraph_of}


def _expr_idents(expr):
    """COBOL data-name identifiers referenced in an expression string (drop
    figurative constants and numeric literals)."""
    if not isinstance(expr, str):
        return []
    fig = {"ZERO", "ZEROS", "ZEROES", "SPACE", "SPACES", "HIGH-VALUE", "HIGH-VALUES",
           "LOW-VALUE", "LOW-VALUES", "QUOTE", "QUOTES", "NULL", "ALL", "TRUE", "FALSE"}
    return [t for t in re.findall(r"[A-Za-z][A-Za-z0-9-]*", expr) if t.upper() not in fig]


def _data_flow(semantics, field_names):
    """From the captured semantics, the set of fields WRITTEN (assignment targets)
    and READ (assignment expressions + guard operands). Restricted to real fields."""
    written, read = set(), set()
    sem = semantics or {}
    for a in (sem.get("actions") or {}).values():
        if not isinstance(a, dict):
            continue
        for asg in (a.get("assignments") or []):
            t = asg.get("target")
            if t:
                written.add(str(t).split("(")[0].strip())     # drop subscripts
            for i in _expr_idents(asg.get("expr", "")):
                read.add(i)
    for g in (sem.get("guards") or {}).values():
        if not isinstance(g, dict):
            continue
        for side in ("left", "right"):
            if isinstance(g.get(side), str):
                for i in _expr_idents(g[side]):
                    read.add(i)
    return written & field_names, read & field_names


def derive_perimeter_params(data, semantics, elk_nodes, root_path, machine):
    """The LINKAGE 01-level groups are the program's parameters — its perimeter for
    a called subprogram (COMMAREA-style data in/out). Direction is taken from the
    captured data flow: a group with any field WRITTEN is an output; any field READ
    makes it an input. Inputs attach to the initial state, outputs to the final
    state(s), so the parameters read as 'in at the start, out at the end'."""
    if not data:
        return []
    children = {}
    for name, f in data.items():
        p = (f or {}).get("parent")
        if p:
            children.setdefault(p, []).append(name)

    def descendants(name):
        acc, stack = {name}, [name]
        while stack:
            for c in children.get(stack.pop(), []):
                if c not in acc:
                    acc.add(c)
                    stack.append(c)
        return acc

    field_names = set(data.keys())
    written, read = _data_flow(semantics, field_names)
    initial = machine.get("initial")
    initial_path = f"{root_path}.{initial}" if initial else None
    finals = [p for p, n in elk_nodes.items() if n.get("kind") == "final"]

    def attach(path, direction, ref):
        n = elk_nodes.get(path)
        if not n:
            return
        io = n.setdefault("io", {"inputs": [], "outputs": []})
        io.setdefault("inputs" if direction == "in" else "outputs", []).append(ref)

    endpoints = []
    for name, f in data.items():
        if not str((f or {}).get("section", "")).upper().startswith("LINKAGE"):
            continue
        if (f or {}).get("level") not in (1, "1", "01"):
            continue
        ds = descendants(name)
        is_out = bool(ds & written)
        is_in = bool(ds & read) or not is_out          # a passed-in param defaults to input
        ep_id = "param:" + name
        endpoints.append({"id": ep_id, "kind": "parameter", "label": name})
        if is_in:
            attach(initial_path, "in", {"event": name, "endpoint": ep_id})
        if is_out:
            for fp in finals[:2]:
                attach(fp, "out", {"event": name, "endpoint": ep_id})
    return endpoints


def _field_type_str(name, data):
    """A field's COBOL type for display — 'PIC S9(7)V99 COMP-3' from the data
    dictionary. Empty when the field isn't in the dictionary (e.g. a bare literal)
    or carries no PICTURE (group item)."""
    d = (data or {}).get(name)
    if not isinstance(d, dict):
        return ""
    t = d.get("type") or {}
    pic = t.get("pic")
    if not pic:
        return ""
    usage = t.get("usage")
    s = "PIC " + pic
    if usage and usage not in ("DISPLAY", ""):
        s += " " + usage
    return s


def _fields_detail(names, data):
    """[{name, type}] for a list of field names, resolving each COBOL type from the
    data dictionary — the field-level list the viewer shows on an I/O event hover."""
    return [{"name": n, "type": _field_type_str(n, data)} for n in (names or [])]


def _event_fields(ev):
    """The data fields an interface I/O event moves across the boundary. Prefer the
    generator's explicit `fields` (SELECT INTO targets, the SQLCODE response, a
    COMMAREA layout); otherwise fall back to the SQL host variables (`:WS-VAR`)
    parsed from the event's raw COBOL, so an INSERT/UPDATE/DELETE that binds values
    but records no INTO still shows what it sends. Order-preserving, de-duplicated."""
    out, seen = [], set()
    for f in (ev.get("fields") or []):
        if f and f not in seen:
            seen.add(f); out.append(f)
    if out:
        return out
    for m in re.finditer(r':\s*([A-Za-z][A-Za-z0-9_-]*)', ev.get("cobol") or ""):
        v = m.group(1)
        if v not in seen:
            seen.add(v); out.append(v)
    return out


def build_io_from_interface(interface, elk_nodes, root_path, machine):
    """Consume the generator's structured `interface` section — the authoritative
    external perimeter: named endpoints (real Db2 tables, called programs, files,
    console) plus I/O events each tied to the state where they happen (with the raw
    COBOL). Preferred over the heuristic action/LINKAGE derivations when present."""
    kmap = {"db2": "db2", "program": "subprogram", "console": "console",
            "condition": "condition", "vsam": "file", "qsam": "file", "file": "file",
            "cics": "cics", "terminal": "cics", "commarea": "commarea", "caller": "caller"}
    endpoints = {}
    for ep in (interface.get("endpoints") or []):
        eid = ep.get("endpoint")
        if eid:
            endpoints[eid] = {"id": "if:" + eid, "label": eid,
                              "kind": kmap.get(str(ep.get("type", "")).lower(),
                                               ep.get("type") or "external")}
    # An event names its state by bare name, but the state may be nested under a
    # region container in a parallel machine (CICSINQ.PROGRAM.1000-LOOKUP), so
    # resolve by the unique final path segment (COBOL paragraph names are unique).
    by_seg, ambiguous = {}, set()
    for path in elk_nodes:
        seg = path.split(".")[-1]
        if seg in by_seg:
            ambiguous.add(seg)
        else:
            by_seg[seg] = path

    def resolve(state):
        if not state:
            return None
        full = f"{root_path}.{state}"
        if full in elk_nodes:
            return full
        return by_seg.get(state) if state not in ambiguous else None

    for ev in (interface.get("events") or []):
        eid = ev.get("endpoint")
        node = elk_nodes.get(resolve(ev.get("state")) or "")
        if eid not in endpoints or not node:
            continue
        direction = "in" if str(ev.get("direction", "")).lower() in ("get", "read", "in") else "out"
        verb = ev.get("verb") or ev.get("event") or eid
        flds = _event_fields(ev)
        # don't repeat a field the verb already spells out (e.g. verb "response
        # (SQLCODE)" + field SQLCODE would read "response (SQLCODE) (SQLCODE)").
        shown = [f for f in flds if f not in verb]
        label = verb + (" (" + ", ".join(shown) + ")" if shown else "")
        io = node.setdefault("io", {"inputs": [], "outputs": []})
        io.setdefault("inputs" if direction == "in" else "outputs", []).append(
            {"event": verb, "label": label, "fields": flds, "endpoint": "if:" + eid})
    return list(endpoints.values())


def build_graph(machine, data=None, semantics=None, interface=None):
    """XState v5 config dict -> {root, nodes, edges, boundary, index, grouping}.
    The external perimeter comes from the generator's `interface` section when
    present (real endpoints/events); otherwise it's derived from the LINKAGE
    section + I/O actions, when the machine carries no hand-authored meta.io."""
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

    # Integrity + recovery pass over resolved targets. A bare XState target is
    # resolved RELATIVE to the source's parent (correct for true siblings). But a
    # COBOL→XState machine often nests a paragraph's lowered states under the
    # paragraph, so a cross-paragraph jump (`PERFORM 2100-DEPOSIT`) resolves to a
    # path that doesn't exist (`…2000-DISPATCH.2100-DEPOSIT` instead of the real
    # top-level `…2100-DEPOSIT`). That dangling target made the in-browser ELK
    # layout abort the WHOLE diagram with "Referenced shape does not exist".
    # Recover it by the unique state whose final name segment matches — COBOL
    # paragraph names are program-unique, so this reconnects the transition to the
    # right state. Only flag it unresolved when the name is ambiguous or truly
    # absent, so a genuinely-broken target is dropped rather than crashing layout.
    node_ids = set(elk_nodes)
    for e in edges:
        tgt = e["target"]
        if e["internal"] or not isinstance(tgt, str) or tgt.startswith("#") or tgt in node_ids:
            continue
        seg = tgt.split(".")[-1]
        if seg in local and seg not in ambiguous_local:
            e["target"] = local[seg]          # reconnected to the real state
            e["recoveredTarget"] = True
        else:
            e["unresolved"] = True
            e["danglingTarget"] = True

    io_meta = (machine.get("meta", {}) or {}).get("io", {}) or {}
    if interface and interface.get("endpoints"):
        # the generator's structured perimeter — authoritative (real table names)
        io_meta = {"endpoints": build_io_from_interface(interface, elk_nodes, root_path, machine),
                   "fromInterface": True}
    elif not io_meta.get("endpoints") and not io_meta.get("fields"):
        # older bundles carry no interface — derive the perimeter from the LINKAGE
        # section (parameters) and the I/O actions (files/CALL/DISPLAY/SQL/CICS).
        derived = derive_io_from_actions(elk_nodes)
        derived += derive_perimeter_params(data, semantics, elk_nodes, root_path, machine)
        if derived:
            io_meta = dict(io_meta)
            io_meta["endpoints"] = derived
            io_meta["derivedFromActions"] = True
    boundary = build_external_io(elk_nodes, index, raised, io_meta, data)
    grouping = compute_paragraph_grouping(elk_nodes, root_path)

    return {"root": root_path, "nodes": elk_nodes, "edges": edges,
            "boundary": boundary, "index": index, "grouping": grouping}


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


def render_html(machine, title=None, data=None, semantics=None, interface=None):
    graph = build_graph(machine, data, semantics, interface)
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

def _extract_config_from_mjs(text):
    """Pull the config object out of a cobol-xstate runnable `.mjs` module
    (`export const machineConfig = {...}` / `createMachine({...})`). The object is
    JSON-clean (string entry actions), so we balance braces and json.loads it.
    Returns the config dict or None. Note: the runnable form has no data/semantics
    bundle, so the LINKAGE perimeter isn't available — only action-derived I/O."""
    for marker in ("machineConfig =", "machineConfig=", "createMachine("):
        i = text.find(marker)
        if i == -1:
            continue
        j = text.find("{", i)
        if j == -1:
            continue
        depth = 0
        for k in range(j, len(text)):
            if text[k] == "{":
                depth += 1
            elif text[k] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[j:k + 1])
                    except json.JSONDecodeError:
                        return None
    return None


def _load_doc(text):
    """Parse an input as JSON (bundle or bare config); fall back to extracting the
    config from a runnable `.mjs` module."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        cfg = _extract_config_from_mjs(text)
        if cfg is None:
            raise
        return cfg


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
        doc = _load_doc(sys.stdin.read())
        default_out = "statechart.html"
    else:
        with open(args.machine, encoding="utf-8") as f:
            doc = _load_doc(f.read())
        stem = args.machine.rsplit(".", 1)[0]
        if stem.endswith(".machine"):
            stem = stem[:-len(".machine")]
        default_out = stem + ".html"

    machine = extract_machine(doc, args.machine_key)
    out = args.out or default_out

    # the cobol-xstate bundle carries the data dictionary + semantics alongside
    # `machine`; they drive the external perimeter (LINKAGE params) and provenance.
    data = doc.get("data") if isinstance(doc, dict) else None
    semantics = doc.get("semantics") if isinstance(doc, dict) else None
    interface = doc.get("interface") if isinstance(doc, dict) else None
    html, graph, used_cdn = render_html(machine, title=args.title, data=data,
                                        semantics=semantics, interface=interface)
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
