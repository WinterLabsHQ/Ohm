# Changelog

## 0.1.1 — 2026-07

- **fix(init)**: the interactive `ohm init` / `ohm create-project` wizard now
  prompts for a project *name* and creates a new subfolder by default; a blank
  answer scaffolds `./my-board` (not the current directory, which previously
  surprised users into scaffolding their parent folder). Use `.` to scaffold in
  place. An explicit name argument was always correct and is unchanged.

## 0.1.0 — 2026-07 (first PyPI release)

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

- **project UX**: `ohm init [name]` / `ohm create-project [name]` scaffold a
  full project (starter design, `ohm.toml`, README, `AGENTS.md`,
  `docs/LANGUAGE.md`, `lib/`, `out/`, `.gitignore`) from bundled templates —
  strictly additive, never clobbering existing files. `ohm.toml` makes the CLI
  framework-aware: bare `ohm build` / `ohm check` walk up to the project root
  and use the configured entry, out dir, and KiCad target (an explicit file,
  `-o`, or `--target-kicad` always wins).
  `ohm refactor <dir>` restructures a messy folder into that layout (dry-run by
  default; `--apply` to execute; stray root `.ohm` files move to `lib/`).
  `ohm doctor` diagnoses the install (Python, PATH, encoding, KiCad libs,
  project). The CLI now forces UTF-8 on its own stdout/stderr, so a non-UTF-8
  console (Windows cp1252 / Git Bash) no longer crashes on non-ASCII
  diagnostics. Global install is documented via `pipx install .`.

680 tests; ruff + mypy clean; deterministic double-build byte-identity and
hermeticity enforced in CI.
