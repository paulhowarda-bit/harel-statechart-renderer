# Backlog

Future work for the renderer and the COBOL-modernization views, roughly in
priority order.

## Next
- **Full calculation syntax in the diagram** — show the actual `COMPUTE` /
  arithmetic and `MOVE` expressions (from `semantics.actions[*].raw` /
  `assignments`) on the state or in an expandable panel, so the calculation logic
  is visible, not just the action name. *(Requested 2026-07-01 — do after the
  external-perimeter work.)*

## External perimeter (in progress)
Derive the program's I/O boundary from the captured data, since COBOL machines
carry no `meta.io`.
- **Done:** LINKAGE parameters as input/output endpoints (direction from data
  flow); file `READ`/`WRITE`/`OPEN`; `CALL` subprograms; `DISPLAY`/`ACCEPT`
  console; `EXEC SQL` → generic **Db2** endpoint; `EXEC CICS LINK`/`XCTL` →
  subprogram; `SEND`/`RECEIVE` → CICS terminal. Also: runnable `.mjs` modules are
  now renderable (config extracted from `machineConfig`).
- **TODO — SQL table names:** the runnable `.mjs` flattens `EXEC SQL SELECT … FROM
  CUST` to a bare `exec_sql_select`, so we can only show a *generic* Db2 endpoint,
  not the table `CUST` or the host variables. The JSON **bundle** (with
  `semantics`) carries the statement/host-vars — surface the actual tables from
  there once a SQL program is emitted as a bundle. Same for `EXEC CICS` file
  control datasets and bulk load/unload.

## Business-state view (the real goal)
Project the technical control-flow up to a **business** state model: choose a
status field (88-level condition names are the richest signal), its values become
the states, and the code paths that MOVE/SET it become the transitions; hide the
rest. The `data` + `semantics` sections already hold everything needed. See the
`business-state-goal` project memory.

## Other ideas
- **PERFORM call arrows** — draw dashed "performs ▷" edges from a performing state
  to the called paragraph, so the call graph is visible (currently only an entry
  action).
- **Distinguish GO TO / fall-through / loop** edges using the `meta.kind` / `note`
  already captured, so unconditional jumps read differently from fall-through.
