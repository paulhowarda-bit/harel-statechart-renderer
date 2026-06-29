# harel-statechart-renderer

Turn an **XState v5 Harel statechart JSON** into **one self-contained, offline,
interactive HTML diagram** — zoomable, pannable, searchable, with a mini-map,
semantic zoom, and (where present) an external-I/O boundary.

A single, standalone, **pure-standard-library** Python program. ELK layout runs
**in the browser** (vendored `elk.bundled.js`), so the tool needs **no Node.js**
and the output opens in any browser with no server and no network.

It is built on the `harel-statechart-render` skill: it reuses the skill's
`viewer.js`/`viewer.css` and ports its `elk_layout.mjs` to run client-side.

## Usage

```bash
# XState v5 config (or a wrapper bundle with a `machine` key) -> HTML
python render_statechart.py examples/banktran.machine.json
python render_statechart.py machine.json -o diagram.html --open
cat machine.json | python render_statechart.py - -o diagram.html
```

Open the resulting `.html` in any browser (double-click / `file://`).

Input may be a bare XState v5 `createMachine` config (has `states`) or a wrapper
object with the config under a `machine` key (override with `--machine-key`).

## How it works

```
XState v5 JSON ──► build_graph()  ──► ELK graph + search index   (Python, this file)
                                       │
                   one HTML file inlining:
                     • elkjs (browser build)   ← lays out on load, in the browser
                     • d3                       ← renders + drives interaction
                     • viewer.js / viewer.css   ← the skill's viewer
                     • the graph JSON
```

`vendor/layout_boot.js` reads the embedded graph, lays it out with elkjs,
flattens to absolute coordinates, then evaluates the viewer.

## Edge encoding

- **Conditional (guarded) transitions** — dashed teal, with the guard as the
  caption; relational operators are shown as `= < > ≤ ≥ ≠` by a display-only
  prettifier (the stored guard name is untouched, so search/provenance use the
  raw form). Full transition detail on hover (tooltip).
- **Sequential (unconditional `always`) flow** — solid light-gray, unlabeled
  (the noisy `ε(always)` is suppressed).

## Fidelity

Render Harel faithfully or annotate the gap — never silently draw UML. Glyph
hints trace to real XState fields or `meta.harel` annotations; OR/AND states,
history, default entry, entry/exit compartments, and the `meta.io` external
boundary are drawn faithfully. See `UPSTREAM_SKILL_BUGS.md` for rendering bugs
found and fixed here.

## Layout / vendor

| file              | role                                   |
|-------------------|----------------------------------------|
| `render_statechart.py` | XState JSON → graph + self-contained HTML |
| `vendor/d3.min.js`     | rendering                          |
| `vendor/elk.bundled.js`| in-browser layout                  |
| `vendor/viewer.js`     | viewer + interaction               |
| `vendor/viewer.css`    | viewer styles                      |
| `vendor/layout_boot.js`| browser layout + viewer boot       |
| `examples/*.machine.json` | XState v5 fixtures (test inputs) |

If a vendored lib is missing, the program falls back to a CDN `<script>` for
d3/elkjs (the output then needs network to open) and says so.

## Tests

```bash
python -m pytest -q
```

Pure standard library + `pytest`. Tests drive the renderer off the JSON fixtures
in `examples/` and assert the rendering invariants (edges paint above nodes,
behavior labels visible at fit zoom, conditional-edge encoding, guard
prettifier is display-only).
