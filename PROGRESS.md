# Ohm — Build Progress Log

Blueprint docs (`OHM_*.md`) are read-only canonical. This file tracks execution.
Plan: Milestone 1 = P0–P6 (approved 2026-07-23).

| Phase | Status | Gate evidence |
|---|---|---|
| P0 Scaffold | ✅ 2026-07-23 | `ohm --version` ok; suite green |
| P1 Grammar+parser+AST | ✅ 2026-07-23 | canonical §3.8 → AST; 20 tests: 10 good + 6 malformed fixtures, multi-error recovery, machine+human diagnostics |
| P2 Resolver+IR | ✅ 2026-07-23 | §3.8 → IR-HIGH; JSON byte-identical; R001–R008 + poison anti-cascade |
| P3 Units | ✅ 2026-07-23 | `3.3V + 10kohm` → U001; `+/- 1%` → tol_lo/hi propagate via intervals; conversions exact (Decimal) |
| P4 Emitters+golden | ✅ 2026-07-23 | 5 hand-verified golden netlists match; KiCad 7 (`kicad-cli` 7.0.11) imports every emitted .kicad_sch/.kicad_pcb; double-build byte-identical; hermeticity grep green |
| P5 Tier-S | ✅ 2026-07-23 | S001–S005 known_bad fixtures exact code+span; good designs still emit |
| P6 Tier-I ★ | ✅ 2026-07-23 | swapped SDA/SCL + D+/D− → I005 at the offending `~` line; output↔output → I003; I001/I002/I006; golden designs clean; errored design emits nothing |

**Milestone 1 (P0–P6) COMPLETE** — 89 tests green.

| Phase | Status | Gate evidence |
|---|---|---|
| P7 `.zen` interop | ✅ 2026-07-23 | real-shaped .zen imports (ast-based Starlark subset, ADR-002); structural round-trip on the import fixture AND all 5 golden designs; build-path isolation test; `pcb` cross-check deferred (binary unavailable) |
| P8 Closed-form + holes | ✅ 2026-07-23 | microstrip Z frozen golden 51.3159 (fixed-precision float boundary); interval-exact divider (monotonic, no decorrelation blowup); per-context assert folding C001/C002/C003; `obligation` → `Hole(kind,args,op,"50mV")` in IR-LOW, bound never evaluated; solver-import grep green (ADR-003) |
| P9 Tier-Q | ✅ 2026-07-23 | Q001–Q004 known_bad fixtures error deterministically with exact code+span; ratings via `ratings_iface` (provider (d) baseline, seam respected by provider-swap test); declared operating points per ADR-004; undeclared values honestly unchecked; golden designs clean |

**Milestone 2 (P7–P9) COMPLETE** — 123 tests green. Next: M3 = P10 constraints emitter + CLI/LSP, P11 pkggen (IPC-7351), P12 agent-usability validation.
