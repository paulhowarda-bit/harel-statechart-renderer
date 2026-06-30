/* layout_boot.js — in-browser ELK layout + viewer boot.
 *
 * A faithful port of the skill's scripts/elk_layout.mjs, adapted to run in the
 * browser instead of Node: it reads the raw ELK graph from the inlined
 * <script id="raw-graph"> block, lays it out with the global elkjs bundle
 * (ELK), flattens to absolute coordinates exactly as elk_layout.mjs does, sets
 * window.GRAPH, then evaluates the inlined viewer source.
 *
 * Moving layout client-side is what lets the generating program stay pure
 * Python (no Node at build time) while the emitted HTML stays self-contained.
 * The layout math below is line-for-line the same as the skill's so fidelity is
 * identical.
 */
(function () {
  "use strict";

  function setStatus(msg, isError) {
    var el = document.getElementById("boot-status");
    if (!el) return;
    if (msg === null) { el.style.display = "none"; return; }
    el.style.display = "flex";
    el.className = isError ? "error" : "";
    el.querySelector(".boot-msg").textContent = msg;
  }

  function layout(graph) {
    // Rough text sizing so leaf boxes fit their labels AND their entry/exit/SR
    // compartments (now shown at L2). Width tracks the widest visible line so the
    // PERFORM entry actions don't overflow the box; height reserves one row per
    // compartment.
    var CHAR_W = 7.2, LINE_H = 18, PAD_X = 16, PAD_Y = 12;
    // Size tiers by role: real COBOL paragraphs are the larger landmarks; the
    // generated plumbing is compact (which also trims overall height). Mirrors
    // the viewer's stateRole so box size and colour agree.
    var ROLE_PADY = { paragraph: 15, decision: 12, io: 11, plumbing: 9, final: 12 };
    function leafRole(node) {
      var label = (node.labels && node.labels[0] && node.labels[0].text) || node.id || "";
      if (node.kind === "final") return "final";
      if (label.indexOf("__") === -1) return "paragraph";
      var m = /__([a-z]+)\d*$/i.exec(label);
      var k = m ? m[1].toLowerCase() : "";
      if (/^(if|when|elif|else|eval|case|cond|loop|until)/.test(k)) return "decision";
      if (/^io/.test(k)) return "io";
      return "plumbing";
    }
    function leafSize(node) {
      var lines = [(node.labels && node.labels[0] && node.labels[0].text) || node.id];
      (node.entry || []).forEach(function (a) { lines.push("entry / " + a); });
      (node.exit || []).forEach(function (a) { lines.push("exit / " + a); });
      ((node.harel && node.harel.staticReactions) || []).forEach(function (sr) {
        lines.push("SR: " + sr);
      });
      ((node.harel && node.harel.activities) || []).forEach(function (act) {
        lines.push("do " + act.name + " (" + act.binding + ")");
      });
      // Clamp the box width to a readable maximum. A single long COBOL statement
      // (a big COMPUTE, a chained MOVE) would otherwise size one box thousands of
      // pixels wide and wreck the whole layout — exactly what blew up a 397-state
      // machine into an unreadable sliver. The full text stays in the hover
      // tooltip; the on-canvas line is truncated to match (see viewer `trunc`).
      var MAX_CHARS = 46;
      var rawMax = lines.reduce(function (m, s) { return Math.max(m, s.length); }, 0);
      var role = leafRole(node);
      var minW = role === "paragraph" ? 104 : (role === "plumbing" ? 66 : 80);
      var w = Math.max(minW, Math.min(MAX_CHARS, rawMax) * CHAR_W + PAD_X * 2);
      // Reserve one row per visible compartment line. Activities ("do") were
      // previously left out, so a leaf with an activity rendered too short and
      // its badge spilled into the next box — count them here too.
      var rows = (node.entry || []).length + (node.exit || []).length +
        ((node.harel && node.harel.staticReactions) || []).length +
        ((node.harel && node.harel.activities) || []).length;
      return { w: w, h: LINE_H + (ROLE_PADY[role] || PAD_Y) * 2 + rows * LINE_H };
    }

    var HEADER_H = 22, REGION_PAD = 10;

    function buildElk(path) {
      var n = graph.nodes[path];
      var kids = n.children || [];
      var base = {
        id: path,
        labels: [{ text: (n.labels[0] && n.labels[0].text) || path }],
        _kind: n.kind, _depth: n.depth, _harel: n.harel, _entry: n.entry,
        _exit: n.exit, _provenance: n.provenance, _initial: n.initial || null,
        _historyDepth: n.historyDepth || null, _description: n.description || null,
        _cobolLine: (n.cobolLine != null ? n.cobolLine : null),
        _sourceKind: n.sourceKind || null,
      };
      if (kids.length === 0) {
        var s = leafSize(n);
        base.width = s.w; base.height = s.h;
        return base;
      }
      // reserve a row per compartment/activity the container itself carries, so
      // its entry/exit/do labels sit in a band below the header instead of
      // overlapping the first child/region.
      var compRows = (n.entry || []).length + (n.exit || []).length +
        ((n.harel && n.harel.staticReactions) || []).length +
        ((n.harel && n.harel.activities) || []).length;
      var topPad = HEADER_H + REGION_PAD + compRows * 16;
      base.layoutOptions = {
        "elk.algorithm": "layered",
        "elk.direction": "DOWN",
        "elk.padding": "[top=" + topPad + ",left=" + REGION_PAD +
          ",bottom=" + REGION_PAD + ",right=" + REGION_PAD + "]",
        "elk.spacing.nodeNode": "26",
        "elk.layered.spacing.nodeNodeBetweenLayers": "30",
        // make ELK reserve room for edge labels and place them beside the edge,
        // so POSTED/DONE/guard captions don't pile onto each other or the boxes.
        "elk.edgeLabels.placement": "CENTER",
        "elk.spacing.edgeLabel": "4",
        "elk.layered.spacing.edgeLabelSpacing": "4",
      };
      base.children = kids.map(buildElk);
      base.edges = [];
      return base;
    }

    function lca(aPath, bPath) {
      if (!aPath || !bPath) return graph.root;
      var a = aPath.split("."), b = bPath.split(".");
      var i = 0;
      while (i < a.length && i < b.length && a[i] === b[i]) i++;
      var common = a.slice(0, i).join(".");
      return common || graph.root;
    }

    var root = buildElk(graph.root);
    // Keep the clean top-to-bottom flow (the diagram's whole value is its reading
    // order). We earlier tried MULTI_EDGE column-wrapping to fight "very long",
    // but it broke flow legibility — column jumps, long cross-column connectors,
    // and labels displaced off their edges. The professional fix for "long" is
    // visual hierarchy + landmarks (see viewer role styling), not squashing the
    // aspect ratio. Just keep spacing tight to trim height.
    root.layoutOptions = {
      "elk.algorithm": "layered",
      "elk.direction": "DOWN",
      "elk.hierarchyHandling": "INCLUDE_CHILDREN",
      "elk.spacing.nodeNode": "26",
      "elk.layered.spacing.nodeNodeBetweenLayers": "32",
      "elk.spacing.edgeNode": "14",
      "elk.spacing.edgeEdge": "10",
      "elk.edgeRouting": "ORTHOGONAL",
      "elk.edgeLabels.placement": "CENTER",
      "elk.spacing.edgeLabel": "4",
    };

    // The caption the viewer will paint for an edge (mirrors viewer.labelFor),
    // used here only to size the label so ELK reserves space for it. Auto edges
    // (ε(always)/after) with no guard show nothing, so they get no label box.
    function edgeIsAuto(e) {
      return !e._event || e._event === "ε(always)" ||
        (typeof e._event === "string" && e._event.indexOf("after(") === 0);
    }
    function edgeCaption(e) {
      var cap = edgeIsAuto(e)
        ? (e._guard ? "[" + e._guard + "]" : "")
        : (e._event + (e._guard ? " [" + e._guard + "]" : ""));
      var ac = (e._actions && e._actions.length) ? " / " + e._actions.join("; ") : "";
      return (cap + ac).trim();
    }

    var containerById = {};
    (function indexContainers(node) {
      containerById[node.id] = node;
      (node.children || []).forEach(indexContainers);
    })(root);

    for (var ei = 0; ei < graph.edges.length; ei++) {
      var e = graph.edges[ei];
      if (e.internal || !e.target || (typeof e.target === "string" && e.target.startsWith("#"))) {
        continue;
      }
      var host = containerById[lca(e.source, e.target)] || root;
      host.edges = host.edges || [];
      var elkEdge = {
        id: e.id, sources: [e.source], targets: [e.target],
        _event: e.event, _guard: e.guard, _actions: e.actions, _meta: e.meta || null,
      };
      var cap = edgeCaption(elkEdge);
      if (cap) {
        // give ELK a real label box so it routes with room for the caption
        // (clamped — a long action string shouldn't reserve a kilopixel label)
        elkEdge.labels = [{ text: cap, width: Math.min(cap.length, 48) * 6.2 + 4, height: 14 }];
      }
      host.edges.push(elkEdge);
    }

    var elk = new ELK();
    return elk.layout(root).then(function (laid) {
      var out = { root: graph.root, nodes: [], edges: [], index: graph.index };

      function flatten(node, ox, oy) {
        var ax = ox + (node.x || 0), ay = oy + (node.y || 0);
        if (node.id !== "root" || graph.nodes[node.id]) {
          out.nodes.push({
            id: node.id,
            x: ax, y: ay, width: node.width, height: node.height,
            kind: node._kind, depth: node._depth, harel: node._harel,
            entry: node._entry, exit: node._exit, provenance: node._provenance,
            initial: node._initial, historyDepth: node._historyDepth,
            description: node._description,
            cobolLine: node._cobolLine, sourceKind: node._sourceKind,
            label: (node.labels && node.labels[0] && node.labels[0].text) || node.id,
            isContainer: !!(node.children && node.children.length),
            ioBadges: graph.nodes[node.id] ? (graph.nodes[node.id].ioBadges || null) : null,
          });
        }
        (node.edges || []).forEach(function (e) {
          var secs = (e.sections || []).map(function (s) {
            return {
              start: { x: ax + s.startPoint.x, y: ay + s.startPoint.y },
              bends: (s.bendPoints || []).map(function (b) { return { x: ax + b.x, y: ay + b.y }; }),
              end: { x: ax + s.endPoint.x, y: ay + s.endPoint.y },
            };
          });
          // ELK placed the label box; hand its absolute position to the viewer
          // so the caption sits where space was reserved, not at a guessed mid.
          var lbl = (e.labels && e.labels[0] && e.labels[0].x != null) ? e.labels[0] : null;
          out.edges.push({
            id: e.id, source: e.sources[0], target: e.targets[0],
            event: e._event, guard: e._guard, actions: e._actions, meta: e._meta || null,
            sections: secs,
            labelPos: lbl ? { x: ax + lbl.x, y: ay + lbl.y, width: lbl.width, height: lbl.height } : null,
          });
        });
        (node.children || []).forEach(function (c) { flatten(c, ax, ay); });
      }
      flatten(laid, 0, 0);

      graph.edges.forEach(function (e) {
        if (e.internal && e.source) {
          out.edges.push({
            id: e.id, source: e.source, target: null,
            event: e.event, guard: e.guard, actions: e.actions, meta: e.meta || null,
            internal: true, sections: [],
          });
        }
      });

      out.width = laid.width; out.height = laid.height;

      // external-boundary placement (left/right gutters), identical to elk_layout.mjs
      var boundary = graph.boundary || { nodes: [], edges: [] };
      var nodeAbs = {};
      out.nodes.forEach(function (n) { nodeAbs[n.id] = n; });

      var GUT = 230, BN_W = 150, BN_H = 46, BN_VGAP = 24;
      var epDir = {};
      boundary.edges.forEach(function (e) {
        epDir[e.endpoint] = epDir[e.endpoint] || { in: 0, out: 0 };
        epDir[e.endpoint][e.direction]++;
      });
      var leftEps = [], rightEps = [];
      boundary.nodes.forEach(function (bn) {
        var d = epDir[bn.endpointId] || { in: 0, out: 0 };
        (d.in >= d.out ? leftEps : rightEps).push(bn);
      });
      function placeColumn(eps, x) {
        var total = eps.length * BN_H + Math.max(0, eps.length - 1) * BN_VGAP;
        var y = (out.height - total) / 2;
        eps.forEach(function (bn) {
          bn.x = x; bn.y = y; bn.width = BN_W; bn.height = BN_H;
          bn.isBoundary = true;
          y += BN_H + BN_VGAP;
          out.nodes.push(bn);
        });
      }
      placeColumn(leftEps, -GUT);
      placeColumn(rightEps, out.width + GUT - BN_W);

      var bnById = {};
      boundary.nodes.forEach(function (bn) { bnById[bn.id] = bn; });
      // Route each boundary edge ORTHOGONALLY through the side margin instead of
      // as a straight diagonal across the interior: gutter → vertical rail in the
      // empty margin → horizontal into the state. The label sits on the rail (in
      // the margin), never on top of an interior box. Rails are staggered per
      // endpoint column so parallel edges don't overlap into one thick line.
      var leftRail = -36, rightRail = out.width + 36, RAIL_STEP = 18;
      var railSlot = {};
      boundary.edges.forEach(function (be) {
        var st = nodeAbs[be.state]; var bn = bnById[be.endpointNode];
        if (!st || !bn) return;
        var isLeft = bn.x < 0;
        // stagger the vertical rail per endpoint so its fan of edges is separable
        var key = (isLeft ? "L" : "R") + be.endpointNode;
        if (railSlot[key] === undefined) railSlot[key] = Object.keys(railSlot).length;
        var railX = (isLeft ? leftRail : rightRail) +
          (isLeft ? -1 : 1) * (railSlot[key] % 4) * RAIL_STEP;
        var stX = isLeft ? st.x : st.x + st.width;
        var stY = st.y + st.height / 2;
        var bnX = isLeft ? bn.x + bn.width : bn.x;
        var bnY = bn.y + bn.height / 2;
        var pts = [
          { x: bnX, y: bnY },      // leave the gutter node, inner side
          { x: railX, y: bnY },    // to the rail
          { x: railX, y: stY },    // down/up the rail (in the margin)
          { x: stX, y: stY },      // into the state's near side
        ];
        if (be.direction !== "in") pts.reverse();  // arrow points state → gutter
        out.boundaryEdges = out.boundaryEdges || [];
        out.boundaryEdges.push({
          id: be.id, endpoint: be.endpoint, state: be.state,
          direction: be.direction, kind: be.kind, label: be.label,
          unconfirmedEndpoint: !!be.unconfirmedEndpoint,
          sections: [{ start: pts[0], bends: pts.slice(1, -1), end: pts[pts.length - 1] }],
          labelX: railX + (isLeft ? -6 : 6),
          labelY: (bnY + stY) / 2,
          labelAnchor: isLeft ? "end" : "start",
        });
      });
      out.boundaryNodes = out.nodes.filter(function (n) { return n.isBoundary; });
      out.index = graph.index;

      var minX = 0, maxX = out.width;
      out.nodes.forEach(function (n) {
        if (n.x < minX) minX = n.x;
        if (n.x + n.width > maxX) maxX = n.x + n.width;
      });
      out.viewMinX = minX; out.viewMaxX = maxX;

      return out;
    });
  }

  function boot() {
    if (typeof ELK === "undefined") {
      setStatus("elkjs failed to load — cannot lay out the statechart.", true);
      return;
    }
    var raw;
    try {
      raw = JSON.parse(document.getElementById("raw-graph").textContent);
    } catch (err) {
      setStatus("Could not parse the embedded graph: " + err.message, true);
      return;
    }
    setStatus("Laying out " + (raw.index ? raw.index.states.length : "?") + " states…", false);
    layout(raw).then(function (laid) {
      window.GRAPH = laid;
      setStatus(null);
      var code = document.getElementById("viewer-src").textContent;
      // eslint-disable-next-line no-eval
      (0, eval)(code); // runs the (unmodified) viewer IIFE now that GRAPH exists
    }).catch(function (err) {
      setStatus("Layout failed: " + (err && err.message ? err.message : err), true);
      // eslint-disable-next-line no-console
      console.error(err);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
