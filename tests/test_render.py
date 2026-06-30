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
