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

**Milestone 2 (P7–P9) COMPLETE** — 123 tests green.

| Phase | Status | Gate evidence |
|---|---|---|
| P10 Constraints+CLI+LSP | ✅ 2026-07-23 | `constraint` decls (ADR-005) → `shared/constraints_schema.py` seam incl. holes; stdlib-only `ohm lsp` publishes I005 as severity-1 at the offending line (red squiggle before build); `ohm fmt` idempotent + comment-preserving; `ohm explain` covers every emitted code (test-enforced); venv pinned back to Python 3.12 |
| P11 pkggen | ✅ 2026-07-23 | IPC-7351 catalog SOIC/SSOP/TSSOP/SOT23/CHIP (ADR-006, out-of-catalog = S006); SOIC-8 within tolerance of KiCad 7's official land pattern; generated .kicad_mod imports via kicad-cli, .kicad_sym parses via `sym upgrade`; byte-identical; `ohm gen-package` standalone + build wiring; pindata_iface (acquisition never in build) |
| P12 Agent validation | ✅ 2026-07-23 | live LLM given ONLY docs/STYLE.md + MCP9808 datasheet excerpt authored a clean design incl. generated TSSOP-8 (zero-shot had 1 error — R005 undeclared Power, a guide gap; 1 machine-diagnostic round fixed it); swapped SDA/SCL caught; **found+fixed a Tier-I gap**: require-pair crossed onto one instance's like-named pins now I005; frozen as tests/fixtures/agent_mcp9808.ohm |

**Milestone 3 (P10–P12) COMPLETE — ALL PHASES P0–P12 DONE, 150 tests green.**
Out of scope by design: Meridian router, convergence solver, hosted platform (proprietary side of the D6 boundary). Open items from the blueprint: OSS license needs legal advice (D6); `pcb` toolchain cross-check on the .zen interop path when the binary is available.

## Production audit 2026-07-23 (post-P12)

Full multi-lens audit (11 passes to convergence, ~75 verified findings) followed by
three fix waves; all CRITICAL/HIGH/MEDIUM and nearly all LOW/IMPROVEMENT findings
fixed with regression tests. **Recorded deviations from the blueprint docs (the
docs are immutable; deviations live here per the session ritual):**

- **Tier-S "declared package ≠ footprint package"** (manual Part 2): not checkable
  for external string footprints without footprint-library data at build time
  (hermeticity N1 forbids fetching). Fully enforced for `generate(...)` packages;
  external footprints get S004 completeness warnings instead.
- **Repo layout naming**: `ohm/ast_nodes.py` (not `ast.py` — stdlib shadowing) and
  `units.py` (covers the planned `types_phys.py`).
- **`shared/` top-level package name**: kept as the blueprint pins the
  `shared/constraints_schema.py` seam path; the site-packages name-collision risk
  is accepted until packaging/publish time (revisit with the license decision).
- **.zen export pins keys**: keyed by pin *name*, which is Zener-spec-correct
  (consumers resolve pads via their own symbol data); audit finding cleared.
- **Q005 thermal**: ambient fixed at 25 degC (ADR-004 addendum).
- **mypy gate**: ~~deferred~~ CLOSED 2026-07-23 — all annotation errors fixed
  (65 at default strictness), `[tool.mypy]` pinned in pyproject, gated in CI
  alongside ruff + pytest.

## Standard library — Milestone L (2026-07-23)

Building blocks across the electronics spectrum (user's idea: generic blocks,
not market parts; a real part = an instantiation with overrides). Blueprint R9
made real. **L0** (foundation): `use` imports + `ohm/loader.py` (R015/R016,
per-decl file fidelity, post-order merge keeps entry `top`), per-instance
`package=` override with instance-site Tier-S, spec-named shared generated
footprints, ratings fallback chain (pin → part attrs), package-data shipping;
ADR-007. **L1** (fan-out, 4 agents): 11 `ohm/std/*.ohm` files, ~60 blocks —
interfaces/passives/discrete/protection/analog/power/logic/electromech/
connectivity/opto + sensors adaptation patterns; every string footprint + pad
id verified against the local KiCad libraries; industry-standard pinouts with
vendor-variance comments; non-standard pinouts kept as `# TEMPLATE` blocks.
**L2** (verification): adversarial pinout audit vs KiCad symbol libs — 44/45
CONFIRMED, 1 comment fix (LMV331), VRef demoted; live-LLM sensor-node built
from STYLE.md + stdlib in one diagnostic round. 353 tests green, ruff+mypy clean.

## Spectrum expansion (2026-07-24, commit 52a8c9a)

+41 declared blocks closing the coverage gaps found in the "does it cover the
whole spectrum?" audit. New/extended domains: **pcb** (MountingHole/TestPoint/
Fiducial/NetTie/ZeroOhm — 0-pin & 1-pin parts verified to build+emit), **clock**
(Oscillator_XO_4/CeramicResonator3/Timer555), **magnetics** (CommonModeChoke/
Transformer4/CoupledInductor4), **rf** (ChipAntenna/Balun + SAW/RFModule
templates), **connectors** (TerminalBlock/DCBarrelJack/JST_XH/RJ45/MicroSD/
AudioJack/SMA/UFL/CoinCellHolder/ICSocket/Battery2 — every pad id read off the
.kicad_mod), **discrete** (BridgeRectifier/DigitalTransistor/JFET + SCR/TRIAC
templates), **electromech** (DIPSwitch_4/RotaryEncoder_EC11/Buzzer + DPDT/SSR
templates), **opto** (WS2812 AddressableLED/RGBLED/7-segment/IR). Non-standard
pinouts kept as honest `# TEMPLATE` blocks. 7 example designs. Library now ~106
declared blocks + templates. Adversarial pinout re-verification of the new blocks
run 2026-07-24 (see below).

## Coverage after expansion (vs the 20-family taxonomy)

Closed to concrete blocks: passives, diodes, transistors (BJT/MOSFET/JFET/bridge/
digital), magnetics, clock, protection, connectors, electromech, optoelectronics,
PCB mechanical features, power sources (coin-cell). Template-by-design (part-
specific, honest): MCU/memory, most application ICs, SCR/TRIAC, switching
converters, RF modules, sensors. The taxonomy is now navigable end-to-end — every
family has either concrete blocks or a documented adaptation template.

## Component `kind` system — Milestone K0 (2026-07-24, commit 956845c, ADR-008)

Checked component-type taxonomy — the Zener differentiator. `kind` = a contract
(pins-by-role + params); `component X(OpAmp):` declares conformance, `is=` binds
pins, `check_kinds.py` verifies (K001 conflict / K002 unsatisfied / K003 role /
K004 param / K005 stray). Nominal claim + structural check; multiple kinds =
union; conflict→error (no linearize); shallow IS-A + orthogonal capabilities;
open-world base (no kind = unchecked). New roles open_collector/free; std/kinds.ohm
(~30 research-minimal contracts); exemplar retrofits (R/C/L/OpAmp). Author-time
only (no emit change). 418 tests green. **Remaining: K1** bulk-retrofit the ~100
std blocks to declare kinds; **K2** adversarial contract audit + live-LLM
custom-part-via-kind test.
