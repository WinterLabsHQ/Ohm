# ADR-005 — R7 constraint syntax + shared schema + stdlib LSP (P10)

Date: 2026-07-23 · Status: accepted

## Constraint syntax
The manual's IR has `Constraint(kind, args)` (impedance/length/diffpair/
netclass/keepout) but Part 3 pins no surface syntax. Decision: a declaration
form symmetric with `obligation`:

    constraint impedance(mcu.bus.scl, 50ohm)
    constraint diffpair(usb.dp, usb.dm)
    constraint length(usb.dp, usb.dm, 0.5mm)
    constraint netclass(pwr.v, "power")

Like obligations, constraints are **recorded, never solved** (routing is
Meridian's job, N6). Ref args resolve to flat net names during lowering; the
kind catalog lives in `shared/constraints_schema.py` (the single seam, imported
by both sides); an unknown kind is warning R009 (catalog is extensible).

## Emission
`emit_constraints.py`: IR-LOW → `<stem>.constraints.json` — a `ConstraintSet`
of constraint entries + hole entries (the solver seam gets both, manual Part 8).
Deterministic JSON.

## LSP
Stdlib-only JSON-RPC server over stdio (`ohm lsp`) — no `pygls` dependency
(Part 0: `lark` stays the only hard dep). Full-document sync,
`publishDiagnostics` on open/change/save. Editor wiring documented in
`docs/LSP.md`.

## fmt
`ohm fmt` reprints canonically from the AST; comments are re-attached by source
line (full-line comments before the nearest following item, trailing comments
on their line). Files with syntax errors are left untouched.
