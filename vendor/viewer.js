/* viewer.js — renders the laid-out Harel statechart and drives all interaction.
   Expects globals: GRAPH (laid-out graph), d3.
   See references/interaction-design.md and elk-harel-mapping.md. */
(function () {
  "use strict";
  const G = window.GRAPH;
  const svg = d3.select("#canvas");
  const stage = document.getElementById("stage");
  const root = svg.append("g").attr("class", "viewport-root");
  // Layer order = paint order (later groups draw on top). Nodes are drawn first
  // so that container fills (e.g. a root OR-state wrapping the whole machine, as
  // the COBOL→XState emitter produces) do NOT paint over the transitions. Edges
  // and their labels sit above the node boxes; annotations on top of all.
  const gNodes = root.append("g").attr("class", "nodes");
  const gBoundary = root.append("g").attr("class", "boundary-nodes");
  const gBoundaryEdges = root.append("g").attr("class", "boundary-edges");
  const gEdges = root.append("g").attr("class", "edges");
  const gAnnot = root.append("g").attr("class", "annots");

  // arrow markers
  const defs = svg.append("defs");
  defs.append("marker").attr("id", "arrow").attr("viewBox", "0 0 10 10")
    .attr("refX", 9).attr("refY", 5).attr("markerWidth", 7).attr("markerHeight", 7)
    .attr("orient", "auto-start-reverse")
    .append("path").attr("d", "M0,0 L10,5 L0,10 z");
  // distinct arrowhead for conditional (guarded) transitions
  defs.append("marker").attr("id", "arrow-cond").attr("viewBox", "0 0 10 10")
    .attr("refX", 9).attr("refY", 5).attr("markerWidth", 7.5).attr("markerHeight", 7.5)
    .attr("orient", "auto-start-reverse")
    .append("path").attr("d", "M0,0 L10,5 L0,10 z").attr("class", "condarrowhead");
  defs.append("marker").attr("id", "ioarrow").attr("viewBox", "0 0 10 10")
    .attr("refX", 9).attr("refY", 5).attr("markerWidth", 8).attr("markerHeight", 8)
    .attr("orient", "auto-start-reverse")
    .append("path").attr("d", "M0,0 L10,5 L0,10 z").attr("class", "ioarrowhead");

  const nodeById = {};
  G.nodes.forEach(n => nodeById[n.id] = n);

  // Truncate long on-canvas labels with an ellipsis. Boxes are width-clamped at
  // layout time, so a 1000-char COBOL statement must not be painted in full or it
  // overflows every neighbor. The complete text is always in the hover tooltip.
  const trunc = (s, n) => { s = String(s == null ? "" : s); return s.length > n ? s.slice(0, n - 1) + "…" : s; };

  // ---- draw nodes (containers first so children sit on top) ----
  const ordered = G.nodes.slice().sort((a, b) => a.depth - b.depth);

  // A "synthetic" state is one the COBOL→XState lowering generated for control
  // flow (…__if/__seq/__io/__loop/__iter/__goto/__when…), as opposed to a real
  // COBOL paragraph. The "__" separator is the marker. Finals keep their own
  // styling. Used to de-emphasize plumbing so real paragraphs read as structure.
  const isSynthetic = d => d.label.indexOf("__") !== -1 && d.kind !== "final";

  // Role = the visual-language tier. Real COBOL paragraphs are the structural
  // landmarks; decision states (lowered IF/EVALUATE/loop tests) are branch points
  // worth highlighting; everything else is recessive plumbing. Encoding role in
  // colour/accent turns a monotonous box-ribbon into a navigable flow.
  function stateRole(d) {
    if (d.kind === "final") return "final";
    if (!isSynthetic(d)) return d.isContainer ? "group" : "paragraph";
    const m = /__([a-z]+)\d*$/i.exec(d.label);
    const k = m ? m[1].toLowerCase() : "";
    if (/^(if|when|elif|else|eval|case|cond|loop|until)/.test(k)) return "decision";
    if (/^io/.test(k)) return "io";
    return "plumbing";   // seq, iter (loop body), next, goto, cont, end, …
  }

  const nodeSel = gNodes.selectAll("g.state").data(ordered, d => d.id)
    .enter().append("g")
    .attr("class", d => `state ${d.kind}${d.isContainer ? " container" : ""}`
      + `${isSynthetic(d) ? " synthetic" : ""} role-${stateRole(d)}`)
    .attr("data-depth", d => d.depth)
    .attr("data-id", d => d.id)
    .attr("transform", d => `translate(${d.x},${d.y})`)
    .on("click", (ev, d) => { ev.stopPropagation(); inspectState(d); });

  nodeSel.append("rect").attr("class", "box")
    .attr("width", d => d.width).attr("height", d => d.height)
    .attr("rx", 8).attr("ry", 8);

  // Left accent bar — a "section marker" that gives the flow landmarks: a strong
  // colour for real paragraphs, a warm one for decisions. Styling/visibility per
  // role lives in CSS; plumbing states get none.
  nodeSel.filter(d => !d.isContainer).append("rect").attr("class", "accent")
    .attr("x", 0).attr("y", 8).attr("width", 4)
    .attr("height", d => Math.max(6, d.height - 16)).attr("rx", 2);

  // header band for containers
  nodeSel.filter(d => d.isContainer).append("rect").attr("class", "hband")
    .attr("width", d => d.width).attr("height", 22).attr("rx", 8).attr("ry", 8);

  // name
  nodeSel.append("text").attr("class", "name")
    .attr("x", 8).attr("y", 5).text(d => trunc(d.label, 44));

  // region dividers inside AND-states
  nodeSel.filter(d => d.kind === "and").each(function (d) {
    const regions = G.nodes.filter(n =>
      n.id.startsWith(d.id + ".") && n.id.split(".").length === d.id.split(".").length + 1);
    const g = d3.select(this);
    regions.slice(1).forEach(r => {
      const lx = r.x - d.x - 5;
      g.append("line").attr("class", "region-divider")
        .attr("x1", lx).attr("y1", 22).attr("x2", lx).attr("y2", d.height);
    });
  });

  // default-entry dot for OR-states
  nodeSel.filter(d => d.kind === "or" && d.initial && nodeById[d.initial]).each(function (d) {
    const init = nodeById[d.initial];
    const g = d3.select(this).append("g").attr("class", "glyph default-dot lod-l2");
    const dx = init.x - d.x + 10, dy = init.y - d.y - 10;
    g.append("circle").attr("cx", dx).attr("cy", dy).attr("r", 4);
  });

  // history glyph
  nodeSel.filter(d => d.kind === "history").each(function (d) {
    const g = d3.select(this).append("g").attr("class", "glyph history");
    g.append("circle").attr("cx", d.width / 2).attr("cy", d.height / 2).attr("r", 11);
    g.append("text").attr("x", d.width / 2).attr("y", d.height / 2)
      .text(d.historyDepth === "deep" ? "H*" : "H");
  });

  // final-state inner ring — the conventional double-border "final" cue. It must
  // stay UNFILLED: a filled inner rect (appended after the name) painted right
  // over the state's label, so every final state rendered as a blank box. With
  // fill:none it's a thin inner border and the name shows through inside it.
  nodeSel.filter(d => d.kind === "final").append("rect").attr("class", "final-ring")
    .attr("x", 4).attr("y", 4).attr("width", d => d.width - 8).attr("height", d => d.height - 8)
    .attr("rx", 5);

  // Compartments: entry / exit / static reactions / activities ("do") at L2 so
  // they are visible at the normal fit zoom (the COBOL behavior lives here —
  // entry actions are PERFORMs). Provenance (COBOL file/para/line) stays at L3
  // as the finest reference detail; it is also in the click inspector.
  nodeSel.each(function (d) {
    const g = d3.select(this);
    let yy = 22;
    const add = (cls, txt) => {
      g.append("text").attr("class", `compartment ${cls} lod-l2`)
        .attr("x", 8).attr("y", yy + 12).text(trunc(txt, 46));
      yy += 16;
    };
    (d.entry || []).forEach(a => add("entry", `entry / ${a}`));
    (d.exit || []).forEach(a => add("exit", `exit / ${a}`));
    ((d.harel && d.harel.staticReactions) || []).forEach(sr => add("sr", `SR: ${sr}`));
    ((d.harel && d.harel.activities) || []).forEach(act =>
      g.append("text").attr("class", "activity-badge lod-l2")
        .attr("x", 8).attr("y", (yy += 16) - 4).text(trunc(`⏲ ${act.name} (${act.binding})`, 46)));
    // Provenance is the finest detail (L3). Skip it on final states — they're
    // small terminal markers, so the text overflows into neighbors; it stays in
    // the click inspector. Draw it on roomy states only.
    if (d.provenance && d.provenance.cobolParagraph && d.kind !== "final") {
      const p = d.provenance;
      const ln = p.sourceLines ? ` ${p.sourceLines.join("–")}` : "";
      g.append("text").attr("class", "provenance lod-l3")
        .attr("x", 8).attr("y", d.height - 6).text(`${p.file || ""} ${p.cobolParagraph}${ln}`);
    }
  });

  // ---- draw edges ----
  function edgePath(e) {
    if (!e.sections || !e.sections.length) return null;
    const s = e.sections[0];
    let pts = [s.start, ...(s.bends || []), s.end];
    return "M" + pts.map(p => `${p.x},${p.y}`).join(" L");
  }

  // An "automatic" transition is XState's `always` (and ε/after): it fires on
  // its own, so its only meaningful content is the guard. A guarded automatic
  // edge is a *decision*; an unguarded one is just sequential flow.
  function isAuto(e) { return !e.event || e.event === "ε(always)" || e.event.startsWith("after("); }

  const edgeSel = gEdges.selectAll("g.edge").data(G.edges.filter(e => !e.internal), d => d.id)
    .enter().append("g")
    .attr("class", d => "edge" + (d.guard ? " conditional" : (isAuto(d) ? " seq" : "")))
    .attr("data-id", d => d.id)
    .on("click", (ev, d) => { ev.stopPropagation(); inspectEdge(d); });

  edgeSel.append("path").attr("d", edgePath)
    .attr("marker-end", d => d.guard ? "url(#arrow-cond)" : "url(#arrow)");

  // A wide, invisible hit area so the 1.4px edge is easy to hover (rich tooltip)
  // and click (inspector) — not a pixel-perfect target.
  edgeSel.append("path").attr("class", "hit").attr("d", edgePath);

  // native tooltip: full transition meaning, available at any zoom level
  edgeSel.append("title").text(tooltipFor);

  // nearest point on a polyline to (px,py) — used to tie a label to its edge
  function closestOnSeg(px, py, ax, ay, bx, by) {
    const dx = bx - ax, dy = by - ay, len2 = dx * dx + dy * dy;
    let t = len2 ? ((px - ax) * dx + (py - ay) * dy) / len2 : 0;
    t = Math.max(0, Math.min(1, t));
    return { x: ax + t * dx, y: ay + t * dy };
  }
  function closestOnPolyline(pts, px, py) {
    let best = null, bd = Infinity;
    for (let i = 0; i < pts.length - 1; i++) {
      const c = closestOnSeg(px, py, pts[i].x, pts[i].y, pts[i + 1].x, pts[i + 1].y);
      const d = (c.x - px) ** 2 + (c.y - py) ** 2;
      if (d < bd) { bd = d; best = c; }
    }
    return best ? { pt: best, dist: Math.sqrt(bd) } : null;
  }

  edgeSel.each(function (e) {
    const p = edgePath(e); if (!p) return;
    const s = e.sections[0];
    const label = labelFor(e);
    if (!label.cap && !label.ac) return;   // drop the "ε(always)" noise: nothing to say
    // Prefer the position ELK reserved for this label (left-anchored at the box);
    // fall back to the edge midpoint only if no label box was laid out.
    let lx, ly, anchor;
    if (e.labelPos) {
      lx = e.labelPos.x; ly = e.labelPos.y + 10; anchor = "start";
    } else {
      const mid = (s.bends && s.bends.length) ? s.bends[Math.floor(s.bends.length / 2)]
        : { x: (s.start.x + s.end.x) / 2, y: (s.start.y + s.end.y) / 2 };
      lx = mid.x; ly = mid.y - 4; anchor = "middle";
    }
    const g = d3.select(this);
    // Faint leader from the label to the nearest point on its edge, so it's clear
    // which transition a caption belongs to when several run in parallel. Only
    // drawn when the label sits clear of the line (otherwise it's just clutter).
    const pts = [s.start].concat(s.bends || [], [s.end]);
    const near = closestOnPolyline(pts, lx, ly - 4);
    if (near && near.dist > 10) {
      g.append("line").attr("class", "leader lod-l2")
        .attr("x1", lx).attr("y1", ly - 4)
        .attr("x2", near.pt.x).attr("y2", near.pt.y);
      g.append("circle").attr("class", "leader-dot lod-l2")
        .attr("cx", near.pt.x).attr("cy", near.pt.y).attr("r", 2);
    }
    const t = g.append("text").attr("class", "elabel")
      .attr("x", lx).attr("y", ly).attr("text-anchor", anchor);
    if (label.cap) t.append("tspan").attr("class", label.capClass).text(trunc(label.cap, 42));
    if (label.ac) t.append("tspan").attr("class", "ac lod-l2").text(" " + trunc(label.ac, 38));
  });

  // Display-only prettifier: the emitter slugs COBOL conditions into
  // identifier-safe guard NAMES — relational operators become lowercase words
  // (= -> eq, < -> lt, …) and separators become underscores, while data names
  // stay UPPER-CASE and hyphenated (see naming.py `_slug`). This restores the
  // readable form for captions/tooltips WITHOUT touching the stored guard name
  // (search and provenance still use the raw name). Operator words are matched
  // lowercase/word-bounded, so UPPER-CASE field names are never rewritten.
  function prettyGuard(g) {
    if (!g) return g;
    return g.replace(/_/g, " ")
      .replace(/\beq\b/g, "=").replace(/\bne\b/g, "≠")
      .replace(/\bge\b/g, "≥").replace(/\ble\b/g, "≤")
      .replace(/\bgt\b/g, ">").replace(/\blt\b/g, "<")
      .replace(/\s+/g, " ").trim();
  }

  // caption: the meaningful part of the transition. For automatic edges the
  // guard IS the caption ("ε(always)" is suppressed); real events show the event.
  function labelFor(e) {
    const guard = e.guard ? `[${prettyGuard(e.guard)}]` : "";
    let cap = "", capClass = "ev";
    if (isAuto(e)) { cap = guard; capClass = "gd"; }
    else { cap = e.event + (guard ? " " + guard : ""); capClass = "ev"; }
    const ac = (e.actions && e.actions.length) ? "/ " + e.actions.join("; ") : "";
    return { cap, capClass, ac };
  }

  function tooltipFor(e) {
    const nm = id => (nodeById[id] ? nodeById[id].label : id);
    let s = nm(e.source) + "  →  " + nm(e.target);
    if (e.guard) s += "\nwhen [" + prettyGuard(e.guard) + "]";
    else if (isAuto(e)) s += "\n(unconditional — always)";
    if (e.event && !isAuto(e)) s += "\non " + e.event;
    if (e.actions && e.actions.length) s += "\ndo " + e.actions.join("; ");
    const m = e.meta || {};
    if (m.note) s += "\n" + m.note;
    if (m.kind || m.cobolLine != null)
      s += "\nCOBOL " + [m.kind, m.cobolLine != null ? "line " + m.cobolLine : null]
        .filter(Boolean).join(" · ");
    return s;
  }

  // ---- broadcast annotation edges (from meta.harel) ----
  G.nodes.forEach(d => {
    const bc = d.harel && d.harel.broadcast;
    if (!bc || !bc.length) return;
    bc.forEach(b => {
      const fromId = `${d.id}.${b.from}`, toIds = (b.to || []).map(t => `${d.id}.${t}`);
      const from = nodeById[fromId]; if (!from) return;
      toIds.forEach(tid => {
        const to = nodeById[tid]; if (!to) return;
        const x1 = from.x + from.width / 2, y1 = from.y + from.height / 2;
        const x2 = to.x + to.width / 2, y2 = to.y + to.height / 2;
        const g = gAnnot.append("g").attr("class", "annot lod-l2");
        g.append("path").attr("d", `M${x1},${y1} L${x2},${y2}`);
        const tag = `broadcast ${b.event}` + (d.harel.sensing ? ` (${d.harel.sensing})` : "");
        // sit the label a third of the way along and lifted clear of the line, so
        // it doesn't land on the region-divider where edge labels already crowd.
        g.append("text").attr("text-anchor", "middle")
          .attr("x", x1 + (x2 - x1) * 0.33).attr("y", y1 + (y2 - y1) * 0.33 - 12).text(tag);
      });
    });
  });

  // ---- external I/O boundary: endpoint nodes, arrows, per-state badges ----
  const epKindClass = k => "ep-" + String(k || "external").toLowerCase().replace(/[^a-z0-9]/g, "");

  // boundary endpoint nodes (typed by endpoint kind)
  const bNodes = G.boundaryNodes || [];
  const bSel = gBoundary.selectAll("g.boundary").data(bNodes, d => d.id)
    .enter().append("g")
    .attr("class", d => `boundary ${epKindClass(d.kind)}${d.endpointId === "__unspecified_in__" ? " unconfirmed" : ""}`)
    .attr("data-endpoint", d => d.endpointId)
    .attr("transform", d => `translate(${d.x},${d.y})`)
    .on("click", (ev, d) => { ev.stopPropagation(); inspectEndpoint(d); });
  bSel.append("rect").attr("class", "ep-box")
    .attr("width", d => d.width).attr("height", d => d.height).attr("rx", 6);
  bSel.append("text").attr("class", "ep-kind").attr("x", 8).attr("y", 14)
    .text(d => d.kind);
  bSel.append("text").attr("class", "ep-label").attr("x", 8).attr("y", 30)
    .text(d => d.label.length > 20 ? d.label.slice(0, 19) + "…" : d.label);

  // boundary edges (input arrows into states, output arrows out of states)
  const bEdges = G.boundaryEdges || [];
  const beSel = gBoundaryEdges.selectAll("g.bedge").data(bEdges, d => d.id)
    .enter().append("g")
    .attr("class", d => `bedge ${d.direction}${d.unconfirmedEndpoint ? " unconfirmed" : ""}`)
    .attr("data-id", d => d.id);
  beSel.append("path")
    .attr("d", d => {
      const s = d.sections[0];
      const pts = [s.start].concat(s.bends || [], [s.end]);
      return "M" + pts.map(p => `${p.x},${p.y}`).join(" L");
    })
    .attr("marker-end", "url(#ioarrow)");
  // The field/event is already shown as a per-state in/out badge at the state
  // itself; a second copy mid-rail just collides with the badges and the
  // endpoint boxes. Keep it on hover instead of painting it on the canvas.
  beSel.append("title")
    .text(d => `${d.direction === "in" ? "input" : "output"} · ${d.label}`);

  // per-state in/out badges (so a state's interface reads without tracing to edge)
  gNodes.selectAll("g.state").each(function (d) {
    const b = d.ioBadges; if (!b) return;
    const g = d3.select(this);
    const mk = (items, side) => {
      // start below the header band on containers so badges never sit on the title
      let yy = d.isContainer ? 26 : 4;
      items.forEach(it => {
        const bw = 7;
        const grp = g.append("g").attr("class",
          `io-badge ${side}${it.unconfirmed ? " unconfirmed" : ""}`);
        grp.append("circle")
          .attr("cx", side === "in" ? -bw : d.width + bw)
          .attr("cy", yy + 6).attr("r", 4);
        // The field name is redundant on-canvas (the state already connects by a
        // labeled arrow to a labeled endpoint box) and its text was the main
        // source of overlap when zoomed in. Show it on hover; keep the dot.
        grp.append("title")
          .text((it.kind === "event" ? "event " : "field ") + it.label
            + (it.endpoint ? " · " + it.endpoint : ""));
        yy += 12;
      });
    };
    mk(b.in || [], "in");
    mk(b.out || [], "out");
  });

  // ---- zoom + semantic LOD ----
  const zoom = d3.zoom().scaleExtent([0.05, 4])
    .on("zoom", (ev) => {
      root.attr("transform", ev.transform);
      const k = ev.transform.k;
      // Detail tiers by zoom. The densest layers (COBOL provenance, I/O field
      // PIC labels) are gated to L3 so the DEFAULT fit view stays a clean
      // structure+behavior diagram; you zoom past ~1.4× to read the reference
      // detail. Without this, a small machine fits at high zoom and dumps every
      // label at once → unreadable.
      stage.dataset.lod = k < 0.5 ? "1" : (k < 1.4 ? "2" : "3");
      updateMinimapViewport(ev.transform);
    });
  svg.call(zoom).on("dblclick.zoom", null);
  svg.on("click", clearInspect);

  function fit() {
    const sw = stage.clientWidth, sh = stage.clientHeight;
    const vx = (G.viewMinX != null) ? G.viewMinX : 0;
    const vw = ((G.viewMaxX != null) ? G.viewMaxX : G.width) - vx;
    const k = Math.min(sw / vw, sh / G.height) * 0.92;
    const tx = (sw - vw * k) / 2 - vx * k, ty = (sh - G.height * k) / 2;
    svg.transition().duration(300).call(zoom.transform,
      d3.zoomIdentity.translate(tx, ty).scale(k));
  }

  // The opening view favors LEGIBILITY over framing the whole graph. Tall
  // control-flow graphs (typical of COBOL) fit-to-both at a scale where text is
  // ~6px and unreadable — the core "hard to read" complaint. When the
  // see-everything scale would drop below a legible floor, open instead at a
  // readable zoom, centered horizontally and anchored to the top of the flow, and
  // let the user pan/scroll down. The Fit button / `f` still frames everything.
  function initialView() {
    const sw = stage.clientWidth, sh = stage.clientHeight;
    const vx = (G.viewMinX != null) ? G.viewMinX : 0;
    const vw = ((G.viewMaxX != null) ? G.viewMaxX : G.width) - vx;
    const kSeeAll = Math.min(sw / vw, sh / G.height) * 0.92;
    const LEGIBLE = 0.62;
    if (kSeeAll >= LEGIBLE) {
      // Small enough to frame the whole graph and still read it.
      const k = kSeeAll;
      svg.call(zoom.transform, d3.zoomIdentity
        .translate((sw - vw * k) / 2 - vx * k, (sh - G.height * k) / 2).scale(k));
      return;
    }
    // Too big to show legibly at once (tall/wide COBOL flows). Open at a READABLE
    // zoom anchored to the top of the flow, centered on where the entry-region
    // nodes actually are — robust to a few outliers that stretch the bounding box,
    // which otherwise leaves you staring at an empty margin. Pan / search / minimap
    // navigate the rest; Fit (f) still frames everything.
    const k = Math.min(1.25, Math.max(0.7, (sw / vw) * 0.98));
    const leaves = G.nodes.filter(n => !n.isContainer);
    const pool = leaves.length ? leaves : G.nodes;
    let minY = Infinity;
    pool.forEach(n => { if (n.y < minY) minY = n.y; });
    let tx;
    if (vw * k <= sw) {
      tx = (sw - vw * k) / 2 - vx * k;          // fits horizontally — center, nothing clipped
    } else {
      // Wider than the viewport: center on where the entry-region nodes actually
      // are, so you don't open staring at an empty margin that a few outliers
      // (far-flung branches) stretched the bounding box across.
      const band = minY + sh / k;               // first screenful of the flow
      const centers = pool.filter(n => n.y <= band).map(n => n.x + n.width / 2).sort((a, b) => a - b);
      const cx = centers.length ? centers[Math.floor(centers.length / 2)] : (vx + vw / 2);
      tx = sw / 2 - cx * k;
    }
    svg.call(zoom.transform, d3.zoomIdentity.translate(tx, 16 - minY * k).scale(k));
  }
  window.addEventListener("resize", () => updateMinimapViewport(d3.zoomTransform(svg.node())));

  // double-click a container → zoom to its subtree bbox
  nodeSel.filter(d => d.isContainer).on("dblclick", (ev, d) => {
    ev.stopPropagation();
    const sw = stage.clientWidth, sh = stage.clientHeight;
    const pad = 30, k = Math.min(sw / (d.width + pad * 2), sh / (d.height + pad * 2));
    const tx = sw / 2 - (d.x + d.width / 2) * k, ty = sh / 2 - (d.y + d.height / 2) * k;
    svg.transition().duration(400).call(zoom.transform,
      d3.zoomIdentity.translate(tx, ty).scale(k));
  });

  // ---- mini-map ----
  const mini = d3.select("#minimap").append("svg")
    .attr("viewBox", `0 0 ${G.width} ${G.height}`).attr("preserveAspectRatio", "xMidYMid meet");
  mini.selectAll("rect.mini-node").data(G.nodes.filter(n => !n.isContainer || n.kind === "and"))
    .enter().append("rect")
    .attr("class", d => "mini-node" + (d.kind === "and" ? " and" : ""))
    .attr("x", d => d.x).attr("y", d => d.y).attr("width", d => d.width).attr("height", d => d.height);
  const viewportRect = mini.append("rect").attr("class", "viewport");

  function updateMinimapViewport(t) {
    const sw = stage.clientWidth, sh = stage.clientHeight;
    const x = (-t.x) / t.k, y = (-t.y) / t.k, w = sw / t.k, h = sh / t.k;
    viewportRect.attr("x", x).attr("y", y).attr("width", w).attr("height", h);
  }
  d3.select("#minimap").on("click", function (ev) {
    const pt = d3.pointer(ev, mini.node());
    const vb = mini.node().viewBox.baseVal;
    const rect = mini.node().getBoundingClientRect();
    const gx = pt[0] / rect.width * vb.width, gy = pt[1] / rect.height * vb.height;
    const t = d3.zoomTransform(svg.node());
    const sw = stage.clientWidth, sh = stage.clientHeight;
    svg.transition().duration(200).call(zoom.transform,
      d3.zoomIdentity.translate(sw / 2 - gx * t.k, sh / 2 - gy * t.k).scale(t.k));
  });

  // ---- search (four modes) ----
  const SI = G.index;
  const searchBox = document.getElementById("search");
  const radiusEl = document.getElementById("radius");
  const filterToggle = document.getElementById("filterToggle");
  const matchCountEl = document.getElementById("matchcount");
  let matches = [], matchIdx = -1;

  function runSearch() {
    const q = (searchBox.value || "").trim().toLowerCase();
    clearClasses();
    if (!q) { matches = []; matchIdx = -1; matchCountEl.textContent = ""; return; }

    const stateHits = new Set();
    const edgeHits = new Set();

    // mode 1: state name
    SI.states.forEach(s => { if (s.name.toLowerCase().includes(q) || s.id.toLowerCase().includes(q)) stateHits.add(s.id); });
    // mode 2: transitions/events/guards/provenance
    SI.transitions.forEach((t, i) => {
      const hay = `${t.event} ${t.guard || ""} ${(t.actions || []).join(" ")}`.toLowerCase();
      if (hay.includes(q)) {
        const e = G.edges.find(e => e.source === t.source && e.target === t.target && e.event === t.event);
        if (e) edgeHits.add(e.id);
      }
    });
    SI.provenance.forEach(p => {
      const hay = `${p.paragraph || ""} ${p.file || ""} ${(p.lines || []).join(" ")}`.toLowerCase();
      if (hay.includes(q)) stateHits.add(p.stateId);
    });

    // mode 2b: external I/O — endpoints, fields, input/output events
    const epHits = new Set();
    (SI.inputs || []).forEach(io => {
      const hay = `${io.label || ""} ${io.event || ""} ${io.field || ""} ${io.endpoint || ""} in input`.toLowerCase();
      if (hay.includes(q)) { stateHits.add(io.state); if (io.endpoint) epHits.add(io.endpoint); }
    });
    (SI.outputs || []).forEach(io => {
      const hay = `${io.label || ""} ${io.event || ""} ${io.field || ""} ${io.endpoint || ""} out output`.toLowerCase();
      if (hay.includes(q)) { stateHits.add(io.state); if (io.endpoint) epHits.add(io.endpoint); }
    });
    (SI.endpoints || []).forEach(ep => {
      const hay = `${ep.label || ""} ${ep.kind || ""} ${ep.id || ""}`.toLowerCase();
      if (hay.includes(q)) epHits.add(ep.id);
    });
    gBoundary.selectAll("g.boundary").classed("match", d => epHits.has(d.endpointId));
    gBoundaryEdges.selectAll("g.bedge").classed("match",
      d => epHits.has(d.endpoint) || stateHits.has(d.state));

    matches = [...stateHits];
    matchIdx = matches.length ? 0 : -1;

    gNodes.selectAll("g.state").classed("match", d => stateHits.has(d.id));
    gEdges.selectAll("g.edge").classed("match", d => edgeHits.has(d.id));

    // mode 3: neighborhood filter (dim non-matches)
    if (filterToggle.checked) applyFilter(stateHits, edgeHits, +radiusEl.value);

    matchCountEl.textContent = `${stateHits.size} state(s), ${edgeHits.size} edge(s)`;
    if (matchIdx >= 0) centerOn(matches[matchIdx]);
  }

  function applyFilter(stateHits, edgeHits, radius) {
    const keep = new Set(stateHits);
    // ancestors for context
    stateHits.forEach(id => { const parts = id.split("."); for (let i = 1; i < parts.length; i++) keep.add(parts.slice(0, i).join(".")); });
    // neighborhood hops
    let frontier = new Set(stateHits);
    for (let r = 0; r < radius; r++) {
      const next = new Set();
      G.edges.forEach(e => {
        if (frontier.has(e.source) && e.target) { keep.add(e.target); next.add(e.target); }
        if (frontier.has(e.target) && e.source) { keep.add(e.source); next.add(e.source); }
      });
      frontier = next;
    }
    gNodes.selectAll("g.state").classed("dim", d => !keep.has(d.id));
    gEdges.selectAll("g.edge").classed("dim", d =>
      !(keep.has(d.source) && keep.has(d.target)) && !edgeHits.has(d.id));
  }

  function clearClasses() {
    gNodes.selectAll("g.state").classed("match", false).classed("dim", false);
    gEdges.selectAll("g.edge").classed("match", false).classed("dim", false);
    gBoundary.selectAll("g.boundary").classed("match", false).classed("dim", false);
    gBoundaryEdges.selectAll("g.bedge").classed("match", false).classed("dim", false);
  }

  function centerOn(id) {
    const n = nodeById[id]; if (!n) return;
    const t = d3.zoomTransform(svg.node());
    const sw = stage.clientWidth, sh = stage.clientHeight;
    const k = Math.max(t.k, 0.9);
    svg.transition().duration(300).call(zoom.transform,
      d3.zoomIdentity.translate(sw / 2 - (n.x + n.width / 2) * k, sh / 2 - (n.y + n.height / 2) * k).scale(k));
  }

  searchBox.addEventListener("input", runSearch);
  radiusEl.addEventListener("input", runSearch);
  filterToggle.addEventListener("change", runSearch);

  // ---- inspector ----
  function inspectState(d) {
    const el = document.getElementById("inspector");
    const h = d.harel || {};
    let html = `<span class="close" onclick="document.getElementById('inspector').classList.remove('show')">✕</span>`;
    html += `<h3>${d.label}</h3><span class="kind">${kindLabel(d.kind)}</span>`;
    if (d.kind === "history") html += `<div class="row"><span class="k">history:</span> ${d.historyDepth}</div>`;
    if (d.entry && d.entry.length) html += `<div class="row"><span class="k">entry:</span> ${d.entry.join("; ")}</div>`;
    if (d.exit && d.exit.length) html += `<div class="row"><span class="k">exit:</span> ${d.exit.join("; ")}</div>`;
    if (h.staticReactions && h.staticReactions.length) html += `<div class="row"><span class="k">static reactions:</span> ${h.staticReactions.join("; ")}</div>`;
    if (h.activities && h.activities.length) html += `<div class="row"><span class="k">activities:</span> ${h.activities.map(a => `${a.name} (${a.binding})`).join("; ")}</div>`;
    if (h.broadcast && h.broadcast.length) html += `<div class="row annot-note">broadcast: ${h.broadcast.map(b => `${b.event}: ${b.from}→${(b.to || []).join(",")}`).join("; ")}${h.sensing ? ` (${h.sensing})` : ""}</div>`;
    if (d.provenance && d.provenance.cobolParagraph) {
      const p = d.provenance;
      html += `<div class="row"><span class="k">COBOL:</span> ${p.file || ""} ${p.cobolParagraph}${p.sourceLines ? ` (lines ${p.sourceLines.join("–")})` : ""}</div>`;
    }
    if (d.kind === "and") {
      const regions = G.nodes.filter(n => n.id.startsWith(d.id + ".") && n.id.split(".").length === d.id.split(".").length + 1);
      html += `<div class="row"><span class="k">regions:</span> ${regions.map(r => r.label + (r.initial ? ` (default ${nodeById[r.initial] ? nodeById[r.initial].label : "?"})` : "")).join(", ")}</div>`;
    }
    const b = d.ioBadges;
    if (b && ((b.in && b.in.length) || (b.out && b.out.length))) {
      const fmt = it => `${it.kind === "event" ? "event" : "field"} ${it.label}` +
        (it.endpoint ? ` ← ${epLabel(it.endpoint)}` : (it.unconfirmed ? " (endpoint unconfirmed)" : ""));
      if (b.in && b.in.length)
        html += `<div class="row"><span class="k">inputs:</span> ${b.in.map(fmt).join("; ")}</div>`;
      if (b.out && b.out.length)
        html += `<div class="row"><span class="k">outputs:</span> ${b.out.map(it =>
          `${it.kind === "event" ? "event" : "field"} ${it.label}` +
          (it.endpoint ? ` → ${epLabel(it.endpoint)}` : "")).join("; ")}</div>`;
    }
    el.innerHTML = html; el.classList.add("show");
  }
  function epLabel(id) {
    const ep = (G.index.endpoints || []).find(e => e.id === id);
    return ep ? `${ep.label} [${ep.kind}]` : id;
  }
  function inspectEndpoint(d) {
    const el = document.getElementById("inspector");
    let html = `<span class="close" onclick="document.getElementById('inspector').classList.remove('show')">✕</span>`;
    html += `<h3>${d.label}</h3><span class="kind">endpoint · ${d.kind}</span>`;
    const ins = (G.index.inputs || []).filter(io => io.endpoint === d.endpointId);
    const outs = (G.index.outputs || []).filter(io => io.endpoint === d.endpointId);
    if (ins.length)
      html += `<div class="row"><span class="k">into program:</span> ${ins.map(io => `${io.label} → ${nodeById[io.state] ? nodeById[io.state].label : io.state}`).join("; ")}</div>`;
    if (outs.length)
      html += `<div class="row"><span class="k">out of program:</span> ${outs.map(io => `${nodeById[io.state] ? nodeById[io.state].label : io.state} → ${io.label}`).join("; ")}</div>`;
    if (d.endpointId === "__unspecified_in__")
      html += `<div class="row annot-note">External input events detected structurally but no endpoint recorded in meta.io — endpoint unconfirmed.</div>`;
    el.innerHTML = html; el.classList.add("show");
  }
  function inspectEdge(e) {
    const el = document.getElementById("inspector");
    const lab = labelFor(e);
    let html = `<span class="close" onclick="document.getElementById('inspector').classList.remove('show')">✕</span>`;
    html += `<h3>transition</h3><span class="kind">${nodeById[e.source] ? nodeById[e.source].label : e.source} → ${nodeById[e.target] ? nodeById[e.target].label : e.target}</span>`;
    html += `<div class="row"><span class="k">label:</span> ${lab.ev} ${lab.gd} ${lab.ac}</div>`;
    el.innerHTML = html; el.classList.add("show");
  }
  function clearInspect() { document.getElementById("inspector").classList.remove("show"); }
  function kindLabel(k) { return { or: "OR-state", and: "AND-state (orthogonal)", basic: "basic state", final: "final state", history: "history" }[k] || k; }

  // ---- rich hover tooltip (states, transitions, boundary) ----
  // A styled HTML tooltip that follows the cursor and shows the FULL program
  // logic at a glance — enters, exits, static reactions, do-activities, broadcast,
  // COBOL source, and the external I/O interface for states; event/guard/actions
  // plus COBOL note/kind/line for transitions. Richer than the native <title>
  // (which stays as an accessibility fallback) and appears instantly.
  const tip = d3.select("body").append("div").attr("id", "tooltip");

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }
  function ttRow(label, val, cls) {
    if (!val) return "";
    return `<div class="tt-row${cls ? " " + cls : ""}"><span class="tt-k">${label}</span> ${val}</div>`;
  }
  const stLabel = id => esc(nodeById[id] ? nodeById[id].label : id);

  function nodeTooltipHTML(d) {
    const h = d.harel || {};
    let s = `<div class="tt-title">${esc(d.label)}</div>`;
    s += `<div class="tt-kind">${esc(kindLabel(d.kind))}` +
      (d.kind === "history" && d.historyDepth ? ` · ${esc(d.historyDepth)}` : "") + `</div>`;
    if (d.description) s += `<div class="tt-row tt-desc">${esc(d.description)}</div>`;
    if (d.entry && d.entry.length)
      s += ttRow("on enter", d.entry.map(esc).join("; "), "tt-entry");
    if (d.exit && d.exit.length)
      s += ttRow("on exit", d.exit.map(esc).join("; "), "tt-exit");
    if (h.staticReactions && h.staticReactions.length)
      s += ttRow("reactions", h.staticReactions.map(esc).join("; "), "tt-sr");
    if (h.activities && h.activities.length)
      s += ttRow("do (activity)", h.activities.map(a => `${esc(a.name)} (${esc(a.binding)})`).join("; "), "tt-do");
    if (h.broadcast && h.broadcast.length)
      s += ttRow("broadcast", h.broadcast.map(b => `${esc(b.event)}: ${esc(b.from)}→${(b.to || []).map(esc).join(",")}`).join("; ") +
        (h.sensing ? ` (${esc(h.sensing)})` : ""), "tt-bc");
    if (d.provenance && d.provenance.cobolParagraph) {
      const p = d.provenance;
      s += ttRow("COBOL", `${esc(p.file || "")} ${esc(p.cobolParagraph)}` +
        (p.sourceLines ? ` (lines ${p.sourceLines.join("–")})` : ""), "tt-src");
    } else if (d.cobolLine != null || d.sourceKind) {
      const parts = [];
      if (d.sourceKind) parts.push(esc(d.sourceKind));
      if (d.cobolLine != null) parts.push("line " + d.cobolLine);
      s += ttRow("COBOL", parts.join(" · "), "tt-src");
    }
    const b = d.ioBadges;
    if (b && b.in && b.in.length)
      s += ttRow("inputs", b.in.map(it =>
        `${it.kind === "event" ? "event" : "field"} ${esc(it.label)}` +
        (it.endpoint ? ` ← ${esc(epLabel(it.endpoint))}` : (it.unconfirmed ? " (endpoint unconfirmed)" : ""))).join("; "), "tt-in");
    if (b && b.out && b.out.length)
      s += ttRow("outputs", b.out.map(it =>
        `${it.kind === "event" ? "event" : "field"} ${esc(it.label)}` +
        (it.endpoint ? ` → ${esc(epLabel(it.endpoint))}` : "")).join("; "), "tt-out");
    s += `<div class="tt-hint">click to pin details</div>`;
    return s;
  }

  function edgeTooltipHTML(e) {
    let s = `<div class="tt-title">${stLabel(e.source)} → ${stLabel(e.target)}</div>`;
    s += `<div class="tt-kind">${isAuto(e) ? "automatic (always)" : "on " + esc(e.event)}</div>`;
    if (e.guard) s += ttRow("when", `[${esc(prettyGuard(e.guard))}]`, "tt-guard");
    else if (isAuto(e)) s += ttRow("when", "unconditional", "tt-guard");
    if (e.actions && e.actions.length) s += ttRow("do", e.actions.map(esc).join("; "), "tt-do");
    const m = e.meta || {};
    if (m.note) s += ttRow("note", esc(m.note), "tt-note");
    const src = [];
    if (m.kind) src.push(esc(m.kind));
    if (m.cobolLine != null) src.push("line " + m.cobolLine);
    if (src.length) s += ttRow("COBOL", src.join(" · "), "tt-src");
    return s;
  }

  function boundaryNodeTooltipHTML(d) {
    let s = `<div class="tt-title">${esc(d.label)}</div>`;
    s += `<div class="tt-kind">endpoint · ${esc(d.kind)}</div>`;
    const ins = (G.index.inputs || []).filter(io => io.endpoint === d.endpointId);
    const outs = (G.index.outputs || []).filter(io => io.endpoint === d.endpointId);
    if (ins.length)
      s += ttRow("into program", ins.map(io => `${esc(io.label)} → ${stLabel(io.state)}`).join("; "), "tt-in");
    if (outs.length)
      s += ttRow("out of program", outs.map(io => `${stLabel(io.state)} → ${esc(io.label)}`).join("; "), "tt-out");
    if (d.endpointId === "__unspecified_in__")
      s += `<div class="tt-row tt-note">endpoint unconfirmed (detected structurally)</div>`;
    return s;
  }

  function positionTip(ev) {
    const node = tip.node();
    const pad = 16, vw = window.innerWidth, vh = window.innerHeight;
    const w = node.offsetWidth, h = node.offsetHeight;
    let x = ev.clientX + pad, y = ev.clientY + pad;
    if (x + w > vw - 8) x = ev.clientX - pad - w;
    if (y + h > vh - 8) y = vh - 8 - h;
    node.style.left = Math.max(8, x) + "px";
    node.style.top = Math.max(8, y) + "px";
  }
  function hideTip() { tip.classed("show", false); }

  stage.addEventListener("mousemove", (ev) => {
    const t = ev.target;
    if (!t || !t.closest) { hideTip(); return; }
    let html = null, el;
    if ((el = t.closest("g.edge"))) {
      const d = d3.select(el).datum(); if (d) html = edgeTooltipHTML(d);
    } else if ((el = t.closest("g.bedge"))) {
      const d = d3.select(el).datum();
      if (d) html = `<div class="tt-title">${d.direction === "in" ? "input" : "output"} · ${esc(d.label)}</div>`;
    } else if ((el = t.closest("g.boundary"))) {
      const d = d3.select(el).datum(); if (d) html = boundaryNodeTooltipHTML(d);
    } else if ((el = t.closest("g.state"))) {
      const d = d3.select(el).datum(); if (d) html = nodeTooltipHTML(d);
    }
    if (html) { tip.html(html).classed("show", true); positionTip(ev); }
    else hideTip();
  });
  stage.addEventListener("mouseleave", hideTip);

  // ---- controls ----
  document.getElementById("fitBtn").addEventListener("click", fit);
  document.getElementById("mmBtn").addEventListener("click", () => document.getElementById("minimap").classList.toggle("hidden"));
  document.getElementById("lgBtn").addEventListener("click", () => document.getElementById("legend").classList.toggle("hidden"));
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "/" && document.activeElement !== searchBox) { ev.preventDefault(); searchBox.focus(); }
    else if (ev.key === "Escape") { searchBox.value = ""; runSearch(); clearInspect(); }
    else if (ev.key === "f") fit();
    else if (ev.key === "m") document.getElementById("minimap").classList.toggle("hidden");
    else if (ev.key === "n") { if (matches.length) { matchIdx = (matchIdx + (ev.shiftKey ? -1 : 1) + matches.length) % matches.length; centerOn(matches[matchIdx]); } }
  });

  // legend semantics line
  document.getElementById("semantics").textContent =
    "Assumes STATEMATE next-step sensing unless an edge annotation says otherwise.";

  initialView();
  updateMinimapViewport(d3.zoomTransform(svg.node()));
})();
