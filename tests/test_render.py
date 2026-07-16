"""Regression guards for the Harel statechart renderer.

Standalone: drives the renderer off checked-in XState v5 JSON fixtures
(examples/*.machine.json) — no external dependency. These lock in the
rendering bugs found during development so they cannot silently return:

  1. Occlusion — edges must paint ABOVE node boxes, or a wrapping root
     OR-state's fill hides every transition. Invariant: the `nodes` group is
     appended before the `edges` group (a proof, not a pixel sample).
  2. Visibility — entry/exit/do/transition-action labels must be tagged
     `lod-l2` so they show at the normal fit zoom (not gated to deep zoom).
  3. Edge encoding — guarded transitions are classed `conditional` and drawn
     dashed/teal; `ε(always)` is suppressed; the guard is the caption with a
     display-only operator prettifier (raw guard names preserved).
"""
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor"
EXAMPLES = ROOT / "examples"

sys.path.insert(0, str(ROOT))
_spec = importlib.util.spec_from_file_location("render_statechart", ROOT / "render_statechart.py")
render_statechart = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(render_statechart)

VIEWER_JS = (VENDOR / "viewer.js").read_text(encoding="utf-8")
VIEWER_CSS = (VENDOR / "viewer.css").read_text(encoding="utf-8")
LAYOUT_JS = (VENDOR / "layout_boot.js").read_text(encoding="utf-8")

FIXTURES = ["banktran", "posting"]


def _config(name):
    doc = json.loads((EXAMPLES / f"{name}.machine.json").read_text(encoding="utf-8"))
    return render_statechart.extract_machine(doc)


# -- 1. occlusion invariant: nodes paint before edges ----------------------

def test_node_layer_appended_before_edge_layer():
    i_nodes = VIEWER_JS.find('.attr("class", "nodes")')
    i_edges = VIEWER_JS.find('.attr("class", "edges")')
    assert i_nodes != -1 and i_edges != -1
    assert i_nodes < i_edges, (
        "edges must be appended AFTER nodes so transitions paint on top of "
        "(and are never occluded by) container fills")


def test_boundary_edges_also_above_nodes():
    assert VIEWER_JS.find('.attr("class", "boundary-edges")') > VIEWER_JS.find('.attr("class", "nodes")')


# -- 2. visibility invariant: behavior shows at the fit zoom (LOD 2) --------

@pytest.mark.parametrize("snippet,what", [
    ('`compartment ${cls} lod-l2`', "entry/exit/SR compartments"),
    ('"activity-badge lod-l2"', "do/activity badges"),
    ('"ac lod-l2"', "transition actions"),
])
def test_behavior_labels_visible_at_mid_zoom(snippet, what):
    assert snippet in VIEWER_JS, f"{what} must be tagged lod-l2 (visible at fit zoom)"


def test_behavior_labels_not_regated_to_l3():
    assert 'compartment ${cls} lod-l3' not in VIEWER_JS
    assert '"activity-badge lod-l3"' not in VIEWER_JS
    assert '"ac lod-l3"' not in VIEWER_JS


# -- 3. conditional vs sequential edge encoding ----------------------------

def test_conditional_edges_are_classed_distinctly():
    assert '" conditional"' in VIEWER_JS and '" seq"' in VIEWER_JS
    assert ".edge.conditional path" in VIEWER_CSS
    assert ".edge.seq path" in VIEWER_CSS
    cond_rule = VIEWER_CSS.split(".edge.conditional path", 1)[1].split("}", 1)[0]
    assert "stroke-dasharray" in cond_rule, "conditional edges must be dashed"


def test_always_noise_is_suppressed_as_a_label():
    assert "isAuto" in VIEWER_JS
    assert "if (!label.cap && !label.ac) return;" in VIEWER_JS


def test_edges_have_tooltip_with_condition():
    assert 'edgeSel.append("title")' in VIEWER_JS
    assert 'when [' in VIEWER_JS


def test_guard_operators_prettified_for_display_only():
    assert "function prettyGuard" in VIEWER_JS
    assert ', "=")' in VIEWER_JS and ', "<")' in VIEWER_JS and ', ">")' in VIEWER_JS
    assert "prettyGuard(e.guard)" in VIEWER_JS
    # raw slug guard names in the data are NOT rewritten (search/provenance intact)
    _, graph, _ = render_statechart.render_html(_config("banktran"))
    guards = [e["guard"] for e in graph["edges"] if e.get("guard")]
    assert any("_eq_" in g for g in guards), "raw slug guard names must be preserved"


# -- 4. graph + HTML over the fixtures -------------------------------------

@pytest.mark.parametrize("name", FIXTURES)
def test_graph_has_resolved_transitions(name):
    graph = render_statechart.build_graph(_config(name))
    nodes = set(graph["nodes"])
    drawn = [e for e in graph["edges"]
             if not e["internal"] and isinstance(e["target"], str)
             and not e["target"].startswith("#")]
    assert drawn, f"{name}: no drawable transitions"
    for e in drawn:
        assert e["target"] in nodes, f"{name}: edge {e['id']} -> unknown {e['target']}"


@pytest.mark.parametrize("name", FIXTURES)
def test_states_carry_entry_behavior(name):
    graph = render_statechart.build_graph(_config(name))
    assert sum(len(n["entry"]) for n in graph["nodes"].values()) > 0


def test_external_io_boundary_built_from_meta_io():
    """The posting fixture declares meta.io — endpoints/boundary must be built."""
    graph = render_statechart.build_graph(_config("posting"))
    assert graph["index"]["endpoints"], "expected endpoints from meta.io"
    assert graph["boundary"]["nodes"] or graph["boundary"]["edges"]


def test_render_html_is_self_contained_and_carries_edges():
    html, graph, used_cdn = render_statechart.render_html(_config("banktran"))
    assert not used_cdn, "expected fully offline output with vendored libs"
    m = re.search(r'<script type="application/json" id="raw-graph">(.*?)</script>', html, re.S)
    assert m, "raw-graph data block missing"
    assert json.loads(m.group(1))["edges"], "no edges embedded"
    assert 'id="viewer-src"' in html and "ELK()" in html and "d3" in html
    assert "</script>" not in m.group(1), "unescaped </script> in embedded JSON"


def test_bundle_input_is_accepted():
    cfg = _config("banktran")
    assert render_statechart.extract_machine({"machine": cfg}) is cfg
    assert render_statechart.extract_machine(cfg) is cfg


# -- 5. raised events are internal, not external inputs ---------------------

def test_iter_raised_events_handles_string_and_structured_forms():
    f = render_statechart.iter_raised_events
    assert list(f("raise(FOO)")) == ["FOO"]
    assert list(f({"type": "raise", "event": "BAR"})) == ["BAR"]
    assert list(f({"type": "xstate.raise", "event": {"type": "BAZ"}})) == ["BAZ"]
    assert list(f([{"type": "assign"}, {"type": "raise", "event": "Q"}])) == ["Q"]
    assert list(f("assign(x)")) == []
    assert list(f(None)) == []


@pytest.mark.parametrize("action", [
    {"type": "raise", "event": "DONE"},                 # structured (contract form)
    {"type": "xstate.raise", "event": {"type": "DONE"}},  # xstate canonical form
    "raise(DONE)",                                       # string form
])
def test_structured_raise_is_not_misclassified_as_external_input(action):
    machine = {
        "id": "m", "initial": "a",
        "states": {
            "a": {"on": {"GO": {"target": "b", "actions": [action]}}},
            "b": {"on": {"DONE": {"target": "a"}}},
        },
    }
    boundary = render_statechart.build_graph(machine)["boundary"]
    assert "DONE" not in boundary["externalInputEvents"], (
        "an internally-raised event must not appear as an external input")
    assert "GO" in boundary["externalInputEvents"]


def test_entry_raised_event_is_internal():
    machine = {
        "id": "m", "initial": "a",
        "states": {
            "a": {"entry": [{"type": "raise", "event": "TICK"}],
                  "on": {"TICK": {"target": "b"}}},
            "b": {},
        },
    }
    boundary = render_statechart.build_graph(machine)["boundary"]
    assert "TICK" not in boundary["externalInputEvents"]


# -- 6. #id target resolution: explicit id, collision safety ----------------

def test_hashid_resolves_by_explicit_id_over_colliding_local_name():
    machine = {
        "id": "m", "initial": "p",
        "states": {
            "p": {"initial": "done", "states": {"done": {"id": "pDone"}}},
            "q": {"initial": "done", "states": {"done": {}},
                  "on": {"X": {"target": "#pDone"}}},
        },
    }
    graph = render_statechart.build_graph(machine)
    e = next(e for e in graph["edges"] if e["event"] == "X")
    assert e["target"] == "m.p.done"
    assert not e.get("unresolved")


def test_hashid_resolves_dotted_descendant():
    machine = {
        "id": "m", "initial": "p",
        "states": {
            "p": {"id": "pp", "initial": "c", "states": {"c": {}}},
            "q": {"on": {"X": {"target": "#pp.c"}}},
        },
    }
    e = next(e for e in render_statechart.build_graph(machine)["edges"]
             if e["event"] == "X")
    assert e["target"] == "m.p.c"


def test_hashid_ambiguous_local_name_is_flagged_not_guessed():
    machine = {
        "id": "m", "initial": "p",
        "states": {
            "p": {"initial": "done", "states": {"done": {}}},
            "q": {"initial": "done", "states": {"done": {}}},
            "r": {"on": {"X": {"target": "#done"}}},
        },
    }
    e = next(e for e in render_statechart.build_graph(machine)["edges"]
             if e["event"] == "X")
    assert e["target"] == "#done", "an ambiguous local name must not be guessed"
    assert e["unresolved"] is True
    assert e.get("ambiguous") is True


def test_synthetic_events_are_not_indexed():
    machine = {
        "id": "m", "initial": "a",
        "states": {
            "a": {"always": {"target": "b"}, "after": {"1000": {"target": "b"}}},
            "b": {"on": {"GO": {"target": "a"}}},
        },
    }
    events = render_statechart.build_graph(machine)["index"]["events"]
    assert "GO" in events
    assert not any(e.startswith(("ε(", "after(")) for e in events)


# -- 7. richer tooltips: edge meta + state source threaded into the graph -----

def test_transition_meta_is_captured_for_edge_tooltips():
    """The control-flow lowering puts kind/note/cobolLine on a transition's meta
    (e.g. 'GO TO - no return', 'AT_END'). That program logic must survive into the
    graph so the edge tooltip can show it — it was being dropped."""
    graph = render_statechart.build_graph(_config("banktran"))
    metas = [e["meta"] for e in graph["edges"] if e.get("meta")]
    assert metas, "expected transition meta (kind/note/cobolLine) on banktran edges"
    assert any(m.get("note") == "GO TO - no return" for m in metas)
    assert any(m.get("kind") == "loop-exit" for m in metas)
    assert all(("cobolLine" in m or "kind" in m or "note" in m) for m in metas)
    # and mirrored into the search index so a note like "AT_END" is findable
    idx_metas = [t["meta"] for t in graph["index"]["transitions"] if t.get("meta")]
    assert any(m.get("note") == "AT_END" for m in idx_metas)


def test_edges_without_meta_stay_clean():
    """posting transitions carry no meta — the field must be None, not {}."""
    graph = render_statechart.build_graph(_config("posting"))
    assert all(e.get("meta") is None for e in graph["edges"])


def test_state_level_cobol_line_and_kind_are_surfaced():
    """banktran states record source as meta.cobolLine / meta.kind (no provenance
    block). Both must reach the node so the tooltip shows 'line N · GOBACK'."""
    graph = render_statechart.build_graph(_config("banktran"))
    end = graph["nodes"]["BANKTRAN.0000-MAIN__end1"]
    assert end["cobolLine"] == 21
    assert end["sourceKind"] == "GOBACK"


def test_layout_threads_edge_meta_and_node_source():
    """layout_boot must carry the new fields through ELK flattening to the viewer."""
    assert "_meta: e.meta" in LAYOUT_JS
    assert "meta: e._meta" in LAYOUT_JS
    assert "_cobolLine" in LAYOUT_JS and "_sourceKind" in LAYOUT_JS
    # leaf box sizing now reserves a row for do-activities (was omitted -> spill)
    assert "harel.activities" in LAYOUT_JS.split("function leafSize", 1)[1].split("return", 1)[0]


# -- 8. final-state labels are visible (inner ring must not occlude) ----------

def test_final_inner_ring_is_unfilled_so_name_shows():
    """A FILLED inner ring drawn after the name hid every final state's label.
    The ring must be its own unfilled class, never a filled rect.box."""
    assert 'kind === "final").append("rect").attr("class", "final-ring")' in VIEWER_JS
    assert '.attr("fill", "#44506a")' not in VIEWER_JS, "final inner ring must not be filled"
    ring_rule = VIEWER_CSS.split(".state.final .final-ring", 1)
    assert len(ring_rule) == 2 and "fill: none" in ring_rule[1].split("}", 1)[0]


# -- 9. rich hover tooltips for nodes AND edges -------------------------------

def test_rich_hover_tooltip_exists_for_nodes_and_edges():
    assert "function nodeTooltipHTML" in VIEWER_JS
    assert "function edgeTooltipHTML" in VIEWER_JS
    assert '.attr("id", "tooltip")' in VIEWER_JS
    assert 'stage.addEventListener("mousemove"' in VIEWER_JS
    assert "#tooltip" in VIEWER_CSS


@pytest.mark.parametrize("label", ["on enter", "on exit", "do (activity)"])
def test_node_tooltip_exposes_enter_exit_and_do(label):
    assert label in VIEWER_JS, f"node tooltip must surface '{label}'"


def test_edge_tooltip_exposes_actions_and_cobol_note():
    body = VIEWER_JS.split("function edgeTooltipHTML", 1)[1].split("function boundaryNodeTooltipHTML", 1)[0]
    assert '"do"' in body and '"note"' in body and "prettyGuard" in body


def test_edges_have_wide_hover_hit_area():
    assert '.attr("class", "hit")' in VIEWER_JS
    assert ".edge path.hit" in VIEWER_CSS


# -- 10. legibility-first initial view (tall COBOL graphs) --------------------

def test_initial_view_is_legibility_first_and_fit_button_kept():
    assert "function initialView" in VIEWER_JS
    assert "initialView();" in VIEWER_JS, "load must use the legible initial view"
    assert "function fit()" in VIEWER_JS, "the Fit button must still frame everything"
    # Fit button + 'f' key still call fit, not initialView
    assert 'getElementById("fitBtn").addEventListener("click", fit)' in VIEWER_JS


# -- 11. large-graph readability: width clamp, truncation, legible huge view --

def test_leaf_box_width_is_clamped():
    """A single long COBOL statement must not size a box thousands of px wide and
    blow up the whole layout (a 397-state machine became an unreadable sliver)."""
    assert "function leafSize" in LAYOUT_JS
    assert "MAX_CHARS" in LAYOUT_JS and "Math.min(MAX_CHARS, rawMax)" in LAYOUT_JS


def test_long_oncanvas_text_truncated_but_tooltip_keeps_full_text():
    assert "const trunc =" in VIEWER_JS
    assert "trunc(d.label" in VIEWER_JS          # state name truncated on canvas
    assert "trunc(txt, " in VIEWER_JS            # entry/exit/SR compartments truncated
    # the hover tooltip still shows the COMPLETE entry/exit lists (not truncated)
    assert "d.entry.map(esc).join" in VIEWER_JS
    assert "d.exit.map(esc).join" in VIEWER_JS


def test_initial_view_stays_legible_on_huge_graphs():
    body = VIEWER_JS.split("function initialView", 1)[1].split("\n  function ", 1)[0]
    assert "Math.max(0.7" in body, "oversized graphs must open at a legible zoom floor"
    assert "vw * k <= sw" in body, (
        "center on the bbox when it fits the viewport, on the entry region when it doesn't")


# -- 12. visual design: role-coded hierarchy, accents, leaders, linear flow ----

def test_states_are_role_classified():
    assert "function stateRole" in VIEWER_JS
    assert "role-${stateRole(d)}" in VIEWER_JS
    for role in ["paragraph", "decision", "io", "plumbing"]:
        assert f".state.role-{role}" in VIEWER_CSS, f"missing CSS for role {role}"


def test_real_paragraphs_emphasized_and_plumbing_recedes():
    assert ".state.role-paragraph .accent { display: inline" in VIEWER_CSS
    assert ".state.role-plumbing rect.box" in VIEWER_CSS
    assert 'attr("class", "accent")' in VIEWER_JS, "states get a left accent bar"


def test_edge_labels_have_leaders_to_their_edge():
    assert "closestOnPolyline" in VIEWER_JS
    assert '"leader lod-l2"' in VIEWER_JS
    assert ".edge .leader" in VIEWER_CSS


def test_column_wrapping_is_disabled_for_flow_legibility():
    # MULTI_EDGE wrapping was tried and reverted: it broke reading order and
    # displaced labels. The clean top-down flow is intentional.
    assert "wrapping.strategy" not in LAYOUT_JS
    assert '"elk.direction": "DOWN"' in LAYOUT_JS


# -- 13. design polish: monospace code, rounded edges, decision shape, sizes ---

def test_cobol_code_is_monospace_with_dominant_names():
    assert "--mono:" in VIEWER_CSS, "a monospace stack must be defined"
    assert "font-family: var(--mono)" in VIEWER_CSS, "COBOL code text must use it"
    # edge labels (guards/events/actions are code) are mono too
    elabel = VIEWER_CSS.split(".edge .elabel {", 1)[1].split("}", 1)[0]
    assert "var(--mono)" in elabel
    # the state name stays the dominant sans label
    assert ".state .name { font-size: 13px; font-weight: 600" in VIEWER_CSS


def test_edges_drawn_with_rounded_corners():
    assert "function roundedPath" in VIEWER_JS
    assert "roundedPath([s.start" in VIEWER_JS


def test_decisions_have_a_shape_cue_not_just_colour():
    assert 'stateRole(d) === "decision"' in VIEWER_JS
    assert '"decision-mark"' in VIEWER_JS
    assert ".state .decision-mark" in VIEWER_CSS


def test_box_size_tiers_by_role():
    assert "ROLE_PADY" in LAYOUT_JS and "function leafRole" in LAYOUT_JS


def test_edge_hover_hit_area_is_wide_and_inline():
    # width set inline so the per-kind colour rules can't shrink the hit target
    assert '.style("stroke-width", "26px")' in VIEWER_JS


# -- 14. target resolution: recover cross-paragraph jumps, never crash layout ---

def test_cross_level_target_is_recovered_to_the_real_state():
    """A jump to a state that isn't a true sibling (nested COBOL paragraphs) is
    resolved RELATIVE to a non-existent path; it must be recovered to the unique
    real state of that name, not left dangling — a dangling endpoint made the
    in-browser ELK layout abort with 'Referenced shape does not exist'."""
    machine = {
        "id": "m", "initial": "A",
        "states": {
            "A": {"initial": "A__if1",
                  "states": {"A__if1": {"on": {"GO": {"target": "B"}}}}},
            "B": {},
        },
    }
    graph = render_statechart.build_graph(machine)
    e = next(x for x in graph["edges"] if x["event"] == "GO")
    assert e["target"] == "m.B", "cross-level target should reconnect to the real state"
    assert e["target"] in graph["nodes"]
    assert not e.get("unresolved")
    assert e.get("recoveredTarget") is True


def test_truly_missing_target_is_flagged_not_crashing():
    machine = {
        "id": "m", "initial": "a",
        "states": {"a": {"on": {"GO": {"target": "ghost"}}}, "b": {}},
    }
    e = next(x for x in render_statechart.build_graph(machine)["edges"]
             if x["event"] == "GO")
    assert e.get("unresolved") is True and e.get("danglingTarget") is True


def test_ambiguous_recovery_is_not_guessed():
    """If the name collides, don't guess — flag it so layout drops it cleanly."""
    machine = {
        "id": "m", "initial": "P",
        "states": {
            "P": {"initial": "P__if1",
                  "states": {"P__if1": {"on": {"GO": {"target": "dup"}}}}},
            "Q": {"initial": "dup", "states": {"dup": {}}},
            "R": {"initial": "dup", "states": {"dup": {}}},
        },
    }
    e = next(x for x in render_statechart.build_graph(machine)["edges"]
             if x["event"] == "GO")
    assert e.get("unresolved") is True


def test_layout_skips_edges_with_missing_endpoints():
    # belt-and-suspenders: even if a target still dangles, ELK must not be handed
    # an edge whose endpoint isn't a real shape (it would abort the whole layout)
    assert "containerById[e.source]" in LAYOUT_JS and "containerById[e.target]" in LAYOUT_JS


def test_io_endpoints_anchored_to_their_states_not_centered():
    # endpoints used to pile at the vertical center, so on a tall graph the whole
    # I/O boundary fell outside the top-anchored opening view ("inputs/outputs no
    # longer show"). They now sit beside the states they connect to.
    assert "epTargetY" in LAYOUT_JS and "function anchorY" in LAYOUT_JS
    assert "(out.height - total) / 2" not in LAYOUT_JS, "endpoints must not be vertically centered"


# -- 15. render by paragraph: collapse/expand grouping ------------------------

def test_paragraph_grouping_detected_for_flat_cobol():
    g = render_statechart.build_graph(_config("banktran"))
    gr = g["grouping"]
    assert gr["enabled"] is True
    assert "0000-MAIN" in gr["groups"] and "2000-DISPATCH" in gr["groups"]
    # every member maps back to its paragraph
    assert all(gr["paragraphOf"][m] == p for p, ms in gr["groups"].items() for m in ms)
    # the paragraph's entry member is the bare state of that name
    assert gr["entry"]["0000-MAIN"].split(".")[-1] == "0000-MAIN"


def test_paragraph_grouping_disabled_for_nested_machine():
    assert render_statechart.build_graph(_config("posting"))["grouping"]["enabled"] is False


def test_grouping_disabled_when_no_synthetic_states():
    machine = {"id": "m", "initial": "a",
               "states": {"a": {"on": {"GO": {"target": "b"}}}, "b": {}}}
    assert render_statechart.build_graph(machine)["grouping"]["enabled"] is False


def test_viewer_has_collapse_expand_machinery():
    assert "function buildCollapsedGraph" in LAYOUT_JS
    assert "window.__relayout" in LAYOUT_JS
    assert "function render()" in VIEWER_JS, "drawing must be re-callable for toggles"
    assert "function toggleParagraph" in VIEWER_JS
    assert "collapsed-group" in VIEWER_JS and ".state.collapsed-group" in VIEWER_CSS


def test_classify_io_action():
    f = render_statechart._classify_io_action
    assert f("read_TRAN-FILE")[1] == "file" and f("read_TRAN-FILE")[3] == "in"
    assert f("WRITE_LEDGER")[3] == "out"
    assert f("call_POSTLOG")[1] == "subprogram" and f("call_POSTLOG")[3] == "out"
    assert f("DISPLAY_INQUIRY")[1] == "console" and f("DISPLAY_INQUIRY")[3] == "out"
    assert f("CLOSE_TRAN-FILE") is None          # lifecycle, not data flow
    assert f("ADD_1_TO_WS-COUNT") is None         # not I/O


def test_interface_section_drives_the_perimeter():
    # the generator's structured `interface` is authoritative: real endpoint names
    # (Db2 tables, programs) with events tied to their state and direction.
    machine = {"id": "P", "initial": "look",
               "states": {"look": {"always": {"target": "done"}}, "done": {"type": "final"}}}
    interface = {
        "endpoints": [{"endpoint": "CUST", "type": "db2", "directions": ["get"]},
                      {"endpoint": "POSTLOG", "type": "program", "directions": ["create"]}],
        "events": [{"endpoint": "CUST", "direction": "get", "verb": "SELECT", "state": "look"},
                   {"endpoint": "POSTLOG", "direction": "create", "verb": "CICS LINK", "state": "look"}],
    }
    g = render_statechart.build_graph(machine, interface=interface)
    assert {n["label"]: n["kind"] for n in g["boundary"]["nodes"]} == \
        {"CUST": "db2", "POSTLOG": "subprogram"}
    dirs = {(e["endpoint"], e["direction"]) for e in g["boundary"]["edges"]}
    assert ("if:CUST", "in") in dirs and ("if:POSTLOG", "out") in dirs


def test_interface_resolves_states_nested_in_parallel_regions():
    # events name a state by bare name, but it lives under a region container
    machine = {"id": "P", "type": "parallel",
               "states": {"R": {"states": {"look": {}}}}}
    interface = {"endpoints": [{"endpoint": "T", "type": "db2"}],
                 "events": [{"endpoint": "T", "direction": "get", "verb": "SELECT", "state": "look"}]}
    g = render_statechart.build_graph(machine, interface=interface)
    assert any(e["endpoint"] == "if:T" for e in g["boundary"]["edges"])


def test_classify_sql_cics_link_actions():
    f = render_statechart._classify_io_action
    assert f("exec_sql_select")[1] == "db2" and f("exec_sql_select")[3] == "in"
    assert f("exec_sql_insert")[3] == "out"
    assert f("link_POSTLOG")[1] == "subprogram" and f("link_POSTLOG")[2] == "POSTLOG"
    assert f("EXEC_CICS_LINK_POSTLOG")[1] == "subprogram"
    assert f("exec_cics_handle") is None          # control flow, not I/O


def test_mjs_runnable_module_is_renderable():
    text = ('export const machineConfig = {"id":"M","initial":"a","states":'
            '{"a":{"entry":["exec_sql_select"],"always":{"target":"z"}},'
            '"z":{"type":"final"}}};\nexport const machine = 1;')
    doc = render_statechart._load_doc(text)
    assert doc["id"] == "M"
    g = render_statechart.build_graph(render_statechart.extract_machine(doc))
    assert any(n["kind"] == "db2" for n in g["boundary"]["nodes"])


def test_io_boundary_derived_from_actions_when_no_meta_io():
    # COBOL machines emit I/O as actions (read_/call_/DISPLAY…), not meta.io — the
    # external input/output events must still render as an endpoint boundary.
    g = render_statechart.build_graph(_config("banktran"))
    assert g["boundary"]["nodes"], "expected a derived I/O boundary"
    kinds = {n["kind"] for n in g["boundary"]["nodes"]}
    labels = {n["label"] for n in g["boundary"]["nodes"]}
    assert {"file", "subprogram", "console"} <= kinds
    assert "TRAN-FILE" in labels and "POSTLOG" in labels
    dirs = {(e["label"].split()[0], e["direction"]) for e in g["boundary"]["edges"]}
    assert ("READ", "in") in dirs and ("CALL", "out") in dirs and ("DISPLAY", "out") in dirs


def test_data_flow_reads_and_writes():
    w, r = render_statechart._data_flow(
        {"actions": {"x": {"assignments": [{"target": "A", "expr": "B + C"}]}},
         "guards": {"g": {"left": "D", "right": "5"}}},
        {"A", "B", "C", "D"})
    assert w == {"A"} and r == {"B", "C", "D"}


def test_linkage_parameters_become_the_perimeter():
    # LINKAGE 01-groups are the program's in/out parameters; direction from the
    # captured data flow (target=written=output, expr/guard=read=input).
    machine = {"id": "m", "initial": "a",
               "states": {"a": {"always": {"target": "z"}}, "z": {"type": "final"}}}
    data = {
        "IN-REC":  {"level": 1, "section": "LINKAGE"},
        "IN-FLD":  {"level": 5, "section": "LINKAGE", "parent": "IN-REC"},
        "OUT-REC": {"level": 1, "section": "LINKAGE"},
        "OUT-FLD": {"level": 5, "section": "LINKAGE", "parent": "OUT-REC"},
        "WS-X":    {"level": 1, "section": "WORKING-STORAGE"},
    }
    semantics = {"actions": {"MOVE_IN-FLD_TO_OUT-FLD":
                             {"assignments": [{"target": "OUT-FLD", "expr": "IN-FLD"}]}}}
    g = render_statechart.build_graph(machine, data, semantics)
    eps = {n["label"]: n["kind"] for n in g["boundary"]["nodes"]}
    assert eps.get("IN-REC") == "parameter" and eps.get("OUT-REC") == "parameter"
    assert "WS-X" not in eps          # WORKING-STORAGE is not the perimeter
    dirs = {(e["label"], e["direction"]) for e in g["boundary"]["edges"]}
    assert ("IN-REC", "in") in dirs and ("OUT-REC", "out") in dirs


def test_hand_authored_meta_io_is_not_overridden_by_derivation():
    g = render_statechart.build_graph(_config("posting"))
    labels = {n["label"] for n in g["boundary"]["nodes"]}
    assert "CICS (terminal in)" in labels        # kept its own endpoints
    assert "TRAN-FILE" not in labels             # no action-derivation happened


def test_opens_fully_expanded_with_paragraph_boxes():
    # each paragraph is a real container BOX and the diagram opens fully expanded;
    # clicking a box contracts it. (Not a collapsed overview.)
    assert "var initialCollapsed = [];" in LAYOUT_JS, "must open fully expanded"
    assert "isParagraphBox" in LAYOUT_JS and "paragraph-box" in VIEWER_JS
    assert ".state.paragraph-box" in VIEWER_CSS


# ---------------------------------------------------------------------------
# `charts`: the bundle emits each PERFORMed paragraph as its own actor sub-chart
# ---------------------------------------------------------------------------

def _bundle_with_charts():
    """The shape cobol-xstate emits: `machine` is only the top-level skeleton and
    each PERFORMed paragraph is an actor sub-chart, entered by `invoke` and ended
    at a `__RET__` final state."""
    machine = {
        "id": "P", "initial": "0000-MAIN",
        "states": {"0000-MAIN": {
            "initial": "_entry",
            "states": {
                "_entry": {"invoke": {"src": "actor:1000-OPEN",
                                      "onDone": {"target": "#0000-MAIN__k1"}},
                           "id": "0000-MAIN"},
                "k1": {"always": [{"target": "#0000-MAIN__end"}], "id": "0000-MAIN__k1"},
                "end": {"type": "final", "id": "0000-MAIN__end"},
            }}}}
    charts = {"actor:1000-OPEN": {
        "initial": "1000-OPEN",
        "states": {
            "1000-OPEN": {"initial": "_entry", "states": {
                "_entry": {"entry": ["OPEN_INPUT_TRAN-FILE"],
                           "always": [{"target": "#__RET__"}], "id": "1000-OPEN"},
            }, "meta": {"kind": "paragraph", "paragraph": "1000-OPEN"}},
            "__RET__": {"type": "final", "id": "__RET__"},
        }}}
    return machine, charts


def test_charts_sub_chart_states_are_drawn_not_dropped():
    # walking `machine` alone draws the skeleton and silently loses the program
    machine, charts = _bundle_with_charts()
    skeleton = render_statechart.build_graph(machine)
    full = render_statechart.build_graph(machine, charts=charts)
    assert not any(p.endswith("1000-OPEN") for p in skeleton["nodes"]), \
        "precondition: the paragraph is not in `machine`"
    assert "P.1000-OPEN" in full["nodes"], "the sub-chart's paragraph must be drawn"
    assert "P.1000-OPEN._entry" in full["nodes"], "its states must be drawn too"
    assert full["charts"]["inlined"] == 2      # the paragraph + its return state


def test_charts_invoke_and_return_are_wired_to_real_states():
    machine, charts = _bundle_with_charts()
    g = render_statechart.build_graph(machine, charts=charts)
    nodes = set(g["nodes"])
    kinds = {(e.get("meta") or {}).get("kind"): e for e in g["edges"]}
    call, ret = kinds.get("invoke"), kinds.get("return")
    assert call and ret, "PERFORM must draw a call edge and a return edge"
    # the call enters the paragraph, the return lands on the call site's onDone
    assert call["source"] == "P.0000-MAIN._entry" and call["target"] in nodes
    assert call["target"].startswith("P.1000-OPEN")
    assert ret["target"] == "P.0000-MAIN.k1"
    assert "PERFORM 1000-OPEN" in call["meta"]["note"]
    # a dangling target aborts the whole ELK layout, so nothing may be unresolved
    assert not [e for e in g["edges"]
                if e.get("unresolved") or e.get("danglingTarget") or e.get("ambiguous")]


def test_each_sub_chart_keeps_its_own_return_state():
    # every chart names its return state `__RET__`; merging as-is would collapse
    # all of them onto one shared box and cross-wire the paragraphs' returns.
    machine, charts = _bundle_with_charts()
    charts["actor:9000-CLOSE"] = {
        "initial": "9000-CLOSE",
        "states": {"9000-CLOSE": {"initial": "_entry", "states": {
                       "_entry": {"always": [{"target": "#__RET__"}], "id": "9000-CLOSE"}}},
                   "__RET__": {"type": "final", "id": "__RET__"}}}
    machine["states"]["0000-MAIN"]["states"]["k1"] = {
        "invoke": {"src": "actor:9000-CLOSE", "onDone": {"target": "#0000-MAIN__end"}},
        "id": "0000-MAIN__k1"}
    g = render_statechart.build_graph(machine, charts=charts)
    assert "P.1000-OPEN__RET__" in g["nodes"] and "P.9000-CLOSE__RET__" in g["nodes"]
    assert not g["charts"]["nameClashes"], "return states must not collide"
    # each paragraph returns only to the site that PERFORMs it
    rets = {e["source"]: e["target"] for e in g["edges"]
            if (e.get("meta") or {}).get("kind") == "return"}
    assert rets["P.1000-OPEN__RET__"] == "P.0000-MAIN.k1"
    assert rets["P.9000-CLOSE__RET__"] == "P.0000-MAIN.end"


def test_charts_perimeter_state_resolves_by_its_xstate_id():
    # `interface` names perimeter states by explicit `id` ("1000-OPEN__io5"),
    # which is neither a path nor a path segment ("io5") — match it or the whole
    # external boundary silently empties out.
    machine, charts = _bundle_with_charts()
    charts["actor:1000-OPEN"]["states"]["1000-OPEN"]["states"]["io5"] = {
        "entry": ["read_TRAN-FILE"], "always": [{"target": "#__RET__"}],
        "id": "1000-OPEN__io5"}
    interface = {"endpoints": [{"endpoint": "TRAN-FILE", "type": "file", "directions": ["get"]}],
                 "events": [{"endpoint": "TRAN-FILE", "direction": "get", "verb": "READ",
                             "state": "1000-OPEN__io5"}]}
    g = render_statechart.build_graph(machine, interface=interface, charts=charts)
    edges = g["boundary"]["edges"]
    assert edges, "the perimeter event must anchor to its state in the sub-chart"
    assert edges[0]["state"] == "P.1000-OPEN.io5" and edges[0]["direction"] == "in"


def test_machine_without_charts_is_untouched():
    # the business and reactive views are flat single machines: no charts, and the
    # inlining must be a no-op for them.
    machine = {"id": "P", "initial": "a",
               "states": {"a": {"always": [{"target": "b"}]}, "b": {"type": "final"}}}
    base = render_statechart.build_graph(machine)
    for charts in (None, {}):
        g = render_statechart.build_graph(machine, charts=charts)
        assert set(g["nodes"]) == set(base["nodes"])
        assert len(g["edges"]) == len(base["edges"])
        assert g["charts"] == {}


def test_inlining_does_not_mutate_the_callers_bundle():
    machine, charts = _bundle_with_charts()
    before = json.dumps(charts, sort_keys=True)
    render_statechart.build_graph(machine, charts=charts)
    assert json.dumps(charts, sort_keys=True) == before, "input doc must not be mutated"


# ---------------------------------------------------------------------------
# validate_render.py — the fidelity check
# ---------------------------------------------------------------------------

_vspec = importlib.util.spec_from_file_location("validate_render", ROOT / "validate_render.py")
validate_render = importlib.util.module_from_spec(_vspec)
sys.modules["validate_render"] = validate_render
_vspec.loader.exec_module(validate_render)


def test_validator_catches_a_bundle_whose_charts_were_never_drawn():
    # THE case this tool exists for. A renderer that ignores `charts` emits a
    # perfectly self-consistent graph — a tidy skeleton, every edge resolved,
    # nothing wrong to find *in the graph*. The evidence lives only in the
    # bundle, so a graph-only check would certify the silent drop.
    machine, charts = _bundle_with_charts()
    doc = {"machine": machine, "charts": charts}
    skeleton = render_statechart.build_graph(machine)          # charts ignored
    assert not [e for e in skeleton["edges"] if e.get("unresolved")
                or e.get("danglingTarget")], "precondition: the graph looks clean"

    _f, _a, _fl, dropped = validate_render.check(skeleton, doc)
    assert dropped, "a bundle with undrawn `charts` must fail integrity"
    assert "not in the diagram" in dropped[0]

    # and it passes once they are actually drawn
    full = render_statechart.build_graph(machine, charts=charts)
    _f, _a, _fl, dropped = validate_render.check(full, doc)
    assert not dropped


def test_validator_ignores_charts_it_cannot_see_without_the_doc():
    # graph-only call must not invent a failure it has no evidence for
    machine, charts = _bundle_with_charts()
    skeleton = render_statechart.build_graph(machine)
    _f, _a, _fl, dropped = validate_render.check(skeleton)
    assert not dropped


def test_validator_flags_a_dangling_target_as_lost():
    machine = {"id": "P", "initial": "a",
               "states": {"a": {"always": [{"target": "nowhere"}]}}}
    g = render_statechart.build_graph(machine)
    _f, _a, _fl, dropped = validate_render.check(g, {"machine": machine})
    assert any("no such state" in d for d in dropped)


def test_validator_reports_the_harel_ladder():
    machine = {"id": "P", "type": "parallel",
               "states": {"R1": {"states": {"a": {"entry": ["act"]}}},
                          "R2": {"states": {"h": {"type": "history", "history": "deep"}}}}}
    g = render_statechart.build_graph(machine)
    faithful, annotated, flagged, dropped = validate_render.check(g, {"machine": machine})
    assert any("AND-state" in x for x in faithful)
    assert any("deep glyph" in x for x in faithful)
    assert any("entry/exit actions" in x for x in faithful)
    # an AND-state with no recorded broadcast is rung 3, not a silent drop
    assert any("no recorded broadcast" in x for x in flagged)
    assert not dropped


def test_validator_exit_codes(tmp_path):
    clean = tmp_path / "clean.json"
    clean.write_text(json.dumps({"id": "P", "initial": "a", "states": {"a": {}}}))
    assert validate_render.main([str(clean)]) == 0

    # main() threads `charts` through to build_graph, so they are drawn -> 0
    machine, charts = _bundle_with_charts()
    bundle = tmp_path / "bundle.json"
    bundle.write_text(json.dumps({"machine": machine, "charts": charts}))
    assert validate_render.main([str(bundle)]) == 0

    # a lost construct fails without needing --strict
    dangling = tmp_path / "dangling.json"
    dangling.write_text(json.dumps({"id": "P", "initial": "a",
                                    "states": {"a": {"always": [{"target": "nowhere"}]}}}))
    assert validate_render.main([str(dangling)]) == 1
