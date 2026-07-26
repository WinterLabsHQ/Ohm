# Changelog

## 0.1.0 (unreleased) — 2026-07

Initial development, complete through signoff. Milestones (details in
`PROGRESS.md`, decisions in `docs/adr/`):

- **P0–P6**: grammar/parser/AST with error recovery; resolver → IR; exact
  Decimal units with tolerance intervals; netlist + KiCad 7 emitters with
  hand-verified golden corpus; structural (S) and intent (I) tiers.
- **P7–P9**: `.zen` interop (import + export, ADR-002); closed-form evaluation
  with interval arithmetic + obligations-as-holes (ADR-003); operating-point
  vs ratings tier Q (ADR-004).
- **P10–P12**: `constraint` declarations + router seam (ADR-005); stdlib-only
  LSP; `ohm fmt`; `ohm explain`; IPC-7351 package generation (ADR-006);
  live-LLM validation loop.
- **L**: standard library — ~106 generic blocks + honest templates across 17
  domains, pinouts adversarially verified against KiCad libraries (ADR-007).
- **K**: checked component `kind` taxonomy — structural conformance contracts
  (~42 kinds), K001–K005 (ADR-008).
- **F**: first-spin tier — `expects` (pullup/decoupling/loadcaps/termination/
  tie, `when` gates, auditable waivers), bring-up checks B001–B010, DFM lints
  M001–M008, placement intent + routed-handoff KiCad emit (ADR-009).
- **G**: repetition — arrays, bounded `for` loops, loop-index arithmetic,
  compile-time computed values; adversarially verified (ADR-010).
- **V**: `ohm signoff` — routed-board verification against the source
  (V001–V006), validated against KiCad's connectivity engine (ADR-011).
- **E0/X0/X1**: expressiveness + ecosystem (ADR-012) — pure `fn` functions,
  config-driven array counts/loop bounds, compile-time `if`/`else` variants
  (all folding at elaborate, override-guarded); `.zen` designs as check input
  + `ohm convert` to annotatable Ohm source; `ohm import-symbol` turns real
  KiCad symbols into Ohm components with true pads and roles.
- **pkggen catalog expansion**: extended from gull-wing + chip to the common
  modern packages (ADR-006's extensible-catalog path, each validated against a
  KiCad 7 reference land pattern before shipping): `QFN` (four-side no-lead +
  optional thermal pad), `QFP` (four-side gull-wing, per-part lead `span`),
  `DFN`/`SON` (two-side no-lead), `BGA` (rows×cols grid of circular pads, JEDEC
  A1.. naming, `ball`/`pad` diameter, optional `border=<n>` for perimeter /
  depopulated grids), `LGA` (rectangular-land grid, same options), `PLCC`
  (J-lead, pin 1 centered on the top edge, counterclockwise), and the fixed tab
  packages `SOT223`/`SOT89`/`TO252`/`TO263` (three signal leads + a solid tab
  pad, no arguments). Shared 4-/2-side placement helpers back the perimeter
  families; the footprint emitter gained a `circle` pad shape for BGA balls.
  QFN/BGA/PLCC (vs 20/28)/SOT-223 and the power-package signal leads reproduce
  their KiCad references exactly; QFP/DFN match within 0.05 mm; power-package
  tabs are solid-rectangle simplifications of KiCad's paste-subdivided lands.

669 tests; ruff + mypy clean; deterministic double-build byte-identity and
hermeticity enforced in CI.
