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
