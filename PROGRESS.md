# Ohm — Build Progress Log

Blueprint docs (`docs/blueprint/OHM_*.md`) are read-only canonical. This file
tracks execution.
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
only (no emit change). **K1** (2026-07-24, fan-out): ~55 std blocks retrofitted to
declare kinds across 12 domains; blocks with no clean kind (dual op-amp, 555, DIP
switch, encoder, RGB/addressable LED, TVS array) left honestly un-kinded (never
role-coerced); taxonomy grown to ~42 kinds; 2 contract bugs (relay coil, opto
cathode roles) fixed. The parallel expansion-pinout re-verify caught + fixed **2
board-breaking 7-segment pad maps** (CC56/CA56 — segments were on common pins).
**K2** (2026-07-24, →d62bd3c): adversarial contract audit found + fixed **4
false-positive `requires`** each verified against the shipping part it wrongly
rejected — LDO `gnd` optional (LM317 has no GND), Comparator `out` accepts
open_collector/open_drain (LM393, via a role-compatibility rule in check_kinds),
Sensor `signal` optional (I2C sensors use bidirectional SDA), SwitchingConverter
`sw` optional (Recom R-78 integrated inductor); live-LLM authored a kind demo
(self-declared OpAmp + multi-kind MOSFET+ThermalPad + a USER-DEFINED
`kind QuadratureEncoder`) frozen as a fixture. **Milestone K COMPLETE — 483 tests
green, ruff+mypy clean.** Remaining: OSS license (legal) is the last open item.

## Milestone F — first-spin success (2026-07-24, ADR-009, →dc946d0+)

Research-driven (3 tracks: ~2.9 respins/project avg, 35–40% of causes pre-layout-
addressable; Cadence concedes ERC can't see protocol-level mistakes; Flux does it
only LLM-heuristically — deterministic compile-time bring-up checking was an empty
lane). **F0**: the `expects` mechanism — kinds/interfaces declare requirement
predicates (pullup/decoupling/loadcaps/cap/termination/tie) over members' nets;
`when` gates (endpoint-only termination); waivers suppress-to-B010-note;
must_connect/tolerant_to pin attrs; check_bringup B001–B010. **F1**: std adoption
(I2C pullups, RS485/CAN endpoint termination, MOSFET gate tie, gated crystal/LDO
caps, universal IC decoupling) — B005 immediately caught 2 of our own examples
missing decoupling caps (fixed; dogfooding); check_mfg M001–M008 DFM/DFT lints
(BOM-stage activation: fire once any part declares mpn/dnp); placement intent
(fixed/near/edge constraints) + routing-friendly KiCad handoff (real placed
footprints, locked anchors, module-hierarchy groups + ohm_path join keys,
.kicad_pro net classes, _P/_N diff-pairs, DSN-shaped constraints JSON). **F2**:
seeded-mistake boards fire exactly their intended finding; adversarial FP suite
(single-supply op-amp, mid-bus RS485, integrated-cap crystal, LM317 no-GND,
off-board pulls, pre-BOM board) = zero false errors/warnings — it caught 2 I006
FPs on analog sense pins, resolved by documenting the 555-precedent convention
(passive-fed analog sense pins are role=passive) in STYLE.md. 566 tests green.
**Deferred**: live-LLM diagnostic-loop test (subagent session limit; run when
capacity resets — the mechanism is otherwise fully verified by the F2 suites).
**Deferred item CLOSED 2026-07-24**: live LLM given only STYLE.md excerpts authored
an I2C I/O-expander board; first attempt invented Power member names (R003 ×3) and
omitted the I2C pull-ups — **B001 caught it** (the guide never mentioned pull-ups;
the compiler taught the requirement). One round of machine diagnostics → clean
board, frozen as tests/examples/io_expander_board.ohm + test_f2_livellm.py.

## Milestone G0 — bounded repetition (2026-07-24, ADR-010)

Arrays (`leds = 8 * LED()`, literal count 1..1024), bounded `for i in 0..7:` loops
(connects/asserts/constraints only), computed attr/kwarg values reusing the ADR-003
closed-form engine (`value=(vcc - 2V) / 10mA` folds to `130ohm`; tolerances
propagate to intervals). Pure elaboration — unrolled at resolve before the main
binding loop, so every checker/emitter sees plain instances; determinism +
hermeticity hold (double-build byte-identical, tested). New diagnostics **R021**
(array/loop misuse: bounds, out-of-range index, loop-var escape, bare-array ref
with "index it" hint) and **R022** (expression doesn't fold: instance refs, div by
zero, comparisons). fmt preserves source forms, idempotent. Per-element checking
verified (`expects tie` in a 3-array fires B002 ×3); loop wiring structurally
equals hand-written. Excluded from v1 (documented in ADR): loop-var arithmetic
(`rs[i+1]`), bindings in loop bodies, non-literal bounds. **584 tests green,
ruff+mypy clean.**

**G1 (2026-07-24): loop-var arithmetic** — `rs[i+1]` / `rs[i-1]` (loop-var ±
literal integer offset, evaluated at unroll) for chains/ladders/cascades. Grammar
reuses the existing `+`/`-` tokens (`!`-kept index rule, no new terminals under the
basic lexer). Bad indices → **R021** once per loop (negative result, non-integer
offset; whole loop dropped, no partial expansion); off-by-one overruns caught by
the existing out-of-range check; non-integer *literal* indices (`rs[1.5]`) now get
an honest "must be an integer" message instead of the loop-var one. Chain wiring
structurally equals hand-written (netlist-identical modulo naming); fmt preserves
`rs[i+1]` idempotently. General index expressions (`rs[2*i]`) still excluded until
real demand. **591 tests green, ruff+mypy clean.**

**Adversarial verify of Milestone G (2026-07-24)** — attack sweep over emitters
(netlist/KiCad/zen), checkers (asserts, expects/bring-up both directions), module
arrays, name collisions, config interactions, fmt-with-comments. Held: everything
above. **Found + fixed 3 real defects** (regression-locked in test_repetition.py):
(1) **CRITICAL silent wrong netlist** — computed values folded against config
*defaults*, so `Child(vcc=5V)` was ignored (130ohm emitted where 300ohm belongs,
zero diagnostics); fix = override guard: instantiation-overriding a config that
feeds folded values is now R022 (fold-uses tracked per module, sites cross-checked
at end of run so decl order doesn't matter); (2) plain `rs` + array `rs` coexisted
silently → now R004; (3) `N * io()` / `N * config()` accepted but half-broken →
now R021 (arrays instantiate components/modules only). Documented minor: dimension-
less folds collapse tolerance to midpoint. ADR-010 updated with the verify record.
**596 tests green, ruff+mypy clean.**

## Milestone V — `ohm signoff` (2026-07-24, ADR-011)

Research-driven (PCBWorld arXiv 2607.05915: open-loop LLM agents fail on invalid
KiCad syntax 34-51%, unconnected nets, floating traces; PCB-Bench ICLR 2026):
the missing leg of the LLM→compile→route loop was verifying the ROUTED board
against the SOURCE — board-local DRC only checks a board against itself. New
`ohm signoff <src> <routed.kicad_pcb>`: pure hermetic S-expr reader + checks
V001 (board≠source: missing/foreign footprint by ohm_path, pad on wrong net,
diffpair renames applied), V002 (incomplete net via point-topology union-find:
endpoint chaining, vias join layers, pad-catch within half-size+0.05mm),
V003 (net-0 copper, foreign nets, floating trace islands — the PCBWorld failure
verbatim), V004 (one point two nets: seg×seg, via×layer, copper-into-wrong-pad),
V005 (info: zones/stubs/geometric DRC delegated to kicad-cli — honest scope),
V006 (constraint fixed(...) drift). CLI subcommand with --json-diagnostics,
exit 1 on errors — machine-iterable by the LLM layer like every tier. Validated
against KiCad's own connectivity engine (pcbnew: unrouted divider = exactly 1
unconnected item, our synthesized route = 0 — pins pad-position/rotation math to
ground truth). 608 tests green, ruff+mypy clean. NEXT: repo structuring
(Zener-style layout) + release signoff; OSS license (legal) still open.

## Repo restructure — Zener-style layout (2026-07-24)

Root now mirrors the professional layout of diodeinc/pcb (Zener): README.md
(pitch, tier table, install, quickstart, layout, principles, honest
license-pending status), CHANGELOG.md (0.1.0 milestone history), AGENTS.md
(the 5-rule agent contract: STYLE.md first, iterate on --json-diagnostics,
declare don't decorate, waive visibly, signoff after routing), examples/
(6 curated boards incl. new led_bar.ohm showing repetition; CI-enforced clean
by tests/test_examples.py so the showcase can never rot), blueprint docs moved
to docs/blueprint/ (git mv, content untouched — immutability is about content),
.gitignore += ruff/mypy caches. No package/module paths changed: the
blueprint-pinned shared/ seam and ohm/ layout stay put (site-packages naming
revisit stays parked with the license decision, as recorded in the audit).
615 tests green, ruff+mypy clean. Remaining: license (legal) → then publish.


## Milestone E0/X0/X1 — expressiveness + ecosystem leverage (2026-07-24, ADR-012)

Owner decision: expressiveness and ecosystem leverage are non-negotiable (from
the honest Zener comparison), and the framing must state field-validation
status honestly. **Framing**: README now positions Ohm as an on-ramp/checking
layer over the existing ecosystem and says plainly that claims are
test-validated, not field-validated, until a first board is fabbed. **X0**:
`ohm check design.zen` (flat lints Z007/Z008 + Z009 honesty notice) and
`ohm convert design.zen` → annotatable Ohm skeleton (compiles as-is; preserves
the net partition exactly; placeholder pads loudly never-for-fab; the TODOs are
the annotation work list). **X1**: `ohm import-symbol` — real .kicad_sym
symbols become components (pad = pin number, role = KiCad electrical type,
extends resolved, multi-unit merged, +/- → _P/_N); validated on Device:R,
LM2904, AMS1117-3.3. **E0**: config-driven counts/bounds (`n * R()`,
`0..n-1`), pure `fn` functions folded by the closed-form engine everywhere
expressions fold (values/kwargs/asserts/conditions; R023 purity/arity/cycle
checks), compile-time `if`/`else` variants (definite-truth folding, R024;
io/config must be unconditional; only the taken branch exists downstream).
Override guard extends to counts and conditions — variant/count configs
overridden by a parent are R022, never a silent stale elaboration. Golden IR
snapshot regenerated (Design.fns schema addition). Recorded ceiling:
per-instance re-elaboration (module generics) is the next expressiveness
milestone if demand shows. **648 tests green, ruff+mypy clean.**