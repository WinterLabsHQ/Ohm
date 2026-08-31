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
Out of scope by design: Meridian router, convergence solver, hosted platform (proprietary side of the D6 boundary). Open items from the blueprint: ~~OSS license needs legal advice (D6)~~ **decided 2026-08-05 — Ohm is proprietary, see "Licence — D6 decided" below**; `pcb` toolchain cross-check on the .zen interop path when the binary is available.

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
  **Still parked 2026-08-05**: the licence is decided but Ohm is not published to
  any index, so nothing shares a site-packages namespace with it yet.
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

---

## Sprint — the schematic becomes a DRAWING (2026-07-29 / 30)

Held against a professionally drafted reference board, the emitted schematic was
electrically exact and read as a netlist rendered as boxes. Closed over this
sprint; **840 tests green, ruff + mypy clean, deterministic, and the fmuv2
reference board still reads back 77 nets / 377 pins IDENTICAL from KiCad at every
step.**

**ADR-016 — hierarchical sheets.** One `.kicad_sch` per top-level module plus a
root block diagram. No longer the default (see ADR-017); reachable as
`--sheets hier`, and chosen automatically when the drawing does not fit A3.

**ADR-017 — drawn, not labelled.** Two-terminal parts are drawn as their
conventional glyph from the declared `kind`, not as labelled boxes (a passive
went from 20 mm to 7.6 mm). A passive serving one pin of an IC is placed against
that pin and wired to it; shunt parts stand upright with the rail above or ground
below; clusters (crystal + load caps, LED + series resistor, reset RC) are wired
end to end; parts in parallel share one rail wire with junctions and return to
their own ground symbols; each module gets a dashed titled partition, and each
cluster inside it a lighter frame titled by kind. Blocks pack across the page,
zoned power → compute → peripheral → interface. A flat `_overview` sheet is
emitted alongside the hierarchy — same netlist, two renderings.

**ADR-018 — a language for the document.** `board` (title block, revision),
`net NAME = <label>` (a net had no name, only an election over its labels),
`note "..."`, `unused_pins = open`, and `vmax`/`dielectric` printed on values.
New checks M009/M010/M011 report the absence of each rather than guessing.

**ADR-019 — first pass, not twentieth.** Every connectivity defect this sprint
was caught automatically by the round-trip oracle; every geometric one was caught
by rendering a PDF and looking. That asymmetry is closed by a **D tier**
(`ohm/check_drawing.py`, D001–D004: overlapping text, text over a symbol body,
overlapping symbols, duplicate uuids), run on every emitted sheet. `AGENTS.md`
and `docs/STYLE.md` carry the author-facing contract.

**Also landed:** `ohm signoff` runs the geometry gate itself (**V007**) through
KiCad's own DRC engine, because it used to advise `kicad-cli pcb drc` — a
subcommand that does not exist in KiCad 7, so nothing checked geometry at all.
**S011** catches parts sharing board area, which KiCad found on fmuv2 and every
Ohm tier had passed. Generated footprints no longer draw silkscreen over pads
(76 violations → 0).

**Defects the oracle caught that would have shipped:** a label's text rotation
clobbering the symbol's between pins; a net drawn as a bare wire silently losing
the netclass pattern that carries its diffpair trace rule; a part dropped from
the sheet entirely by a rider chain; a rail line running past a box symbol's
pins because the ladder assumed glyph geometry; eight resistors on one bus
telescoping into a chain that shorted four nets. Three separate
variable-shadowing bugs in one file (`group`, `rot`, `want`).

**Closed as not planned:** position-based reference annotation and buses — see
ADR-018.

**Open, and now reported by the tools rather than discovered by a reviewer:**
D001×3 / D002×2 / D003×2 on fmuv2 (including riders overlapping the MCU body),
S011×5 placeholder-courtyard conflicts, M010×30 unnamed nets, no `fp-lib-table`
emitted, no `Edge.Cuts` outline, no router (Meridian scope).


## Multi-EDA native targets — Altium + Allegro (2026-07-31, ADR-020)

"Multi-EDA" was an aspiration: KiCad first-class, Eagle `.lbr` because Eagle is
the only *other* published textual library format, and nothing a team on Altium
or Allegro could open. Closed for Altium, half-closed for Allegro, and the half
that is not closed is stated rather than papered over.

**Altium is native, not interchange.** `ohm build --emit altium` writes real
`.PcbLib` and `.SchLib` binaries. That required writing the MS-CFB compound-file
container from scratch (`ohm/pkggen/cfb.py`, ~190 lines, zero runtime deps: v3
sectors, a real mini stream because Altium libraries are dozens of sub-4 KB
streams a reader locates by size alone) and packing Altium's own records
(`ohm/pkggen/altium.py`): binary pad/track/arc on the PCB side, pipe-delimited
parameter strings with the pins — and only the pins — in binary on the schematic
side.

**Nothing was guessed.** Every field offset, block length and magic constant was
read out of Altium-authored libraries and cross-checked against the published
format descriptions. The rule applied throughout: an unknown field set to zero is
a guess, an unknown field set to what Altium writes is a fact — so the undocumented
tail of the pad record is `_OBSERVED_*` copied verbatim, not zeroed.

**Geometry is shared, so it cannot drift.** Pads from `PackageModel`, silkscreen
from `silk_segments`, symbols from `symbol_layout` — the same functions the KiCad
emitter calls. The test asserts pad-for-pad equality with the `.kicad_mod`, which
`test_pkggen.py` validates against KiCad's own reference footprints:
KiCad reference ← `.kicad_mod` ← `PackageModel` → `.PcbLib`.

**Allegro gets connectivity, and that is said plainly.** Allegro's `.dra`/`.psm`/
`.pad` are undocumented binary only Cadence's tools write, so Ohm emits none.
What Allegro *does* document is the third-party logic import, and
`ohm build --emit allegro` writes all three files (`pstchip.dat`, `pstxprt.dat`,
`pstxnet.dat`) with syntax and canonical-path escaping taken from a real
OrCAD-written triplet. Ohm net names routinely contain `.` (`r1.P1`), which
Allegro reads as a hierarchy descent unless backslash-wrapped — so it is. Parity
on connectivity and footprint binding; none on library geometry.

**What the tests prove, layer by layer:** the container is read back by `olefile`
(an implementation Ohm did not write — now a test-only dev dep, because a
self-written reader would agree with a self-written writer about a broken file);
record framing is asserted against the block lengths Altium itself writes (pad
110, track 41, arc 45); geometry is asserted equal to the KiCad-validated
`.kicad_mod`; the Allegro triplet is asserted node-for-node against Ohm's own
proved netlist. All four emitters now share the role-map contract — every role in
`VALID_ROLES` must appear in every target's electrical-type map, or the suite
fails.

**Evidence beyond the gate:** the emitted files were parsed by `pyAltiumLib`, a
third-party Altium reader, which recovered the library header, part names,
descriptions, every pad (designator, position, size, shape, layer, no hole) and
every pin (name, number, electrical type, side, length). Recorded, not wired into
CI — a second parser is still not Altium.

**Not claimed:** that Altium Designer opens/renders/DRC-passes these files, or
that Allegro imports the triplet. No Altium and no Cadence tool exists on any
machine Ohm is developed or tested on. First user with a licence closes that gap.

**Also fixed in passing:** the five golden netlists still carried `ohm 0.1.0`
after the version bump, so six tests were red before this work started. Verified
the only delta was the version string, then regenerated.

## Tooling fixes found by a real design (2026-08-02, →190ca77, b540c32)

Two bugs the Mehzabeen ECG board exposed. Neither was a language bug; neither
ever reached the netlist.

- `ohm/provenance.py` — `verify-pinouts` ignored a declared
  `pinout_source = "datasheet:..."`, fell through to the MPN search, and
  `_prefix_score` then accepted a same-family sibling (ADS1292R vs ADS1299 share
  exactly 6 characters and cleared `n >= 6` by one). 64 confident false findings.
  Now a declared non-KiCad origin abstains rather than guessing, and divergence
  digit-against-digit scores 0 — a letter still passes, since that is the variant
  wildcard.
- `ohm/emit_kicad.py` — the synthesized anchor pin on a pinless component
  (Fiducial, MountingHole) was emitted bare, so KiCad ERC reported unconnected
  pins the source never declared and `unused_pins = open` had nothing to reach.
  It now carries a no-connect.

## KiCad 9 and KiCad 10 (2026-08-02 →1a138e2, 2026-08-05 →3dd90ca)

Ohm writes the KiCad 7 format and relies on KiCad reading forward, which is the
direction KiCad guarantees. Both versions are now run against, not assumed.

**KiCad 9.0.9.** The framing "regenerate the goldens" was wrong: no golden land
pattern moved, and Ohm's runtime never *reads* a `.kicad_mod`, only writes them.
17 of 20 failures were serialisation — KiCad 9 pretty-prints and quotes pad ids
(9 golden regexes matched nothing; fixed by one tolerant reader in
`tests/kicad_golden.py`, deliberately in tests/ because a footprint reader is
scaffolding, not product), and inserts `(class "Default")` between a net's name
and its nodes, which made the round-trip oracle see every net as empty. 3 were
real: `std.connectors` pointed at `TerminalBlock_bornier-*`, which KiCad 9
deleted; `pcbnew.EDA_UNITS_MILLIMETRES` was renamed `EDA_UNITS_MM`, breaking
signoff's V007 gate (now version-tolerant, raises `DrcUnavailable`); `Q_NMOS_GDS`
moved Device → Transistor_FET.

**KiCad 10.0.5.** Extracts the FMUv2's netlist as 77 nets, partition-identical to
KiCad 9.0.9, and plots its gerbers and schematic. KiCad 10 splits every net
across a dozen tab-indented lines, breaking the same oracle a second time — the
reader is now whitespace-shaped rather than line-shaped. Recorded, not fixed:
KiCad 10 reports 39 more `footprint_link_issues` than KiCad 9 on the same board
and renamed `label_dangling`.

Same commit, net names: a derived name was the Ohm instance path printed on every
label on every sheet. `readable_net_name()` is now the one definition of the
mapping (`fmu.spi_fram.miso` → `FMU_SPI_FRAM_MISO`); the path is kept whole and
never shortened, because `accel.spi.cs` and `gyro.spi.cs` are different chip
selects, and a rename that would collide with an existing name is skipped —
connectivity is carried BY the label, so that collision would be a short, not a
cosmetic clash. **BREAKING**: a board routed against an earlier build carries the
old names in its copper, so `ohm signoff` reports V001 on every pad until the
netlist is re-imported. Authored `net NAME = ...` is never rewritten.

## Track widths and a placer that places (2026-08-05, →49204e4)

The two things that made the board output a stub rather than a starting point.

`netclass(rail, "power")` produced a class whose `track_width` was byte-identical
to Default — the intent was named and then the rail was routed at signal width.
`ohm/trace_width.py` sizes it from the current the design already declares (the
same per-pin `current=` sum Q002 checks) by the IPC-2221A external curve, 1oz,
10 degC rise, rounded UP to 0.05mm, because rounding a current-carrying trace to
the nearer step is the one direction that is not safe. IPC-2221 rather than the
newer 2152 deliberately: 2152 needs copper area, dielectric and via field, which
Ohm does not know, and 2221 errs wide. Sizing only ever widens, and only where a
current was declared — a width invented from no current is a default wearing a
standard's name. **Q007** reports a netclass Ohm could not size, and fires only
where the declaration has somewhere to go, since a rail reaching nothing but
passives has no `current=` to write.

The placer advanced only x, so 116 parts landed on a single 500mm line. It now
flows one block per module, wraps into rows against the board the `fixed` parts
imply, and keeps clear of them — a mounting hole has a screw through it. `near`
was dropped outright when its target was `fixed`, which is the most load-bearing
`near` on a board; followers are now seated the moment their target lands, on the
first free side, at the declared distance measured EDGE TO EDGE. Board
coordinates stopped being rounded to the 1.27mm *schematic* grid — a 0.05in
imperial pitch applied to a metric board, which is why a declared 2mm arrived as
2.9mm — and now quantise to 0.01mm toward the target, so the quantum can only
ever shorten a declared gap. FMUv2: 382mm × 1 row → 88 × 164mm, zero S011
conflicts contributed, 17 of 18 `near` constraints met exactly (the 18th is the
tail of a five-deep connector chain that runs out of free sides).

**895 tests + 1 skipped green, ruff+mypy clean.**

## KiCad compatibility, measured (2026-08-26/27)

The claim "verified through 10.0.5" was a sentence in three files, taken once by
hand on one machine. It is now a CI job, and taking it properly found five real
defects. Full record: `KICAD_CI_IMPLEMENTATION.md`; audit that scoped it:
`KICAD_COMPATIBILITY.md`; decision: ADR-021.

**342 KiCad-dependent tests, 0 failed, 0 skipped, at 8.0.9 / 9.0.9 / 10.0.5.**
Zero skips is the point: `OHM_REQUIRE_KICAD=1` turns a KiCad-shaped skip into a
failure, because every guard was a `skipif` and a job whose container failed to
start would otherwise report green having certified nothing. Its own first catch
was a bug in itself — the hook checked only the `call` phase, so `skipif`
decorators, which fire at setup, all slipped through.

**`ohm signoff` could not read a KiCad 10 board.** KiCad 10 dropped the net table
and names nets inline (`(net "R1_P2")` vs `(net 3 "R1_P2")`), so every pad came
back on net `""` and signoff reported V001 on *every pad* — a parse failure
wearing the costume of a design failure, in the last gate before fabrication.
This is exactly the read-back gap the audit called F3 and could only call
"likely fine"; it was not.

**Three standard blocks bound their shields to pads that do not exist.** KiCad 10
normalised shield/shell pads to `SH` across 163 footprints. Scope was measured
with the parser, not a regex, after a regex scan produced three false positives:
of 66 std components referencing a KiCad footprint, 8 and 9 are 66/66 correct and
10 is 63/66.

The fix is structural rather than a re-spelling, because no single pad id is
right on every supported KiCad. `ohm/fpdata/` ships the pad ids of 15,473
footprints across three majors in 158 KB (84% are contiguous `1..N` and store as
one integer; only 163 differ across versions), generated by `tools/gen_fpdata.py`
and rebuilt by CI so it cannot rot. S012 reports a pad the target's footprint
lacks; `lower()` writes the target's spelling for a pad that was merely renamed,
which is what finally gives `--target-kicad` a meaning — the FORMAT stays KiCad 7
for every target, the LIBRARY VOCABULARY follows it. `ohm verify-footprints` is
the author-time half, mirroring `verify-pinouts`.

Also found: a BGA-49 golden comparison that **had never executed on any machine
at any version** (it named a spelling KiCad renamed at 9) — it now runs, and
passes; a FET test guarded on a library that exists on KiCad 8 but does not
contain the part; and `ohm doctor` reporting "KiCad libraries not detected" on
KiCad 10, since the probe stopped at 9.

Corrections to what this log's own predecessors assumed: `kicad-cli pcb upgrade`
is **KiCad 10 only** (8 and 9 expose drc/export/render), and rewrites in place
with no `--output`; the read-back gate therefore falls back to `pcbnew`
Load/Save, which every version ships. The KiCad 10 image is Debian 13 / Python
3.13, not bookworm / 3.11 like 8 and 9.

**Not proven, and said so in the README:** KiCad 7 (~~no container image exists
below 8.0.4, so it is hand-verified and carries no pad data~~ — **the first half
of that is false; corrected 2026-08-31, see the section at the end of this
log**), V007 at more than one version (pcbnew is built against the distro's
Python), and the workflow YAML itself running on a GitHub runner. Separately, worth its own issue: **Ohm's
boards carry no `Edge.Cuts` outline**, so the gerber package is not orderable
as-is — found while proving the plot claim.

**888 passed + 83 skipped locally, ruff+mypy clean.**

## Licence — D6 decided (2026-08-05, →273601e)

**Ohm is proprietary.** The open-core question is closed the other way, by owner
decision. `LICENSE` is the Ohm Software License: all rights reserved, use only
under a written agreement with WinterLabs, no redistribution, no derivative
works, no patent grant. Apache 2.0 granted every one of those to anyone who
cloned the repo, which is what it was there to stop granting.

- **Output is untouched.** Designs, netlists and boards built with Ohm belong to
  their author; the licence claims no ownership of design data. A tool that took
  a share of what it compiled would not be usable by anyone.
- `NOTICE` and the `pyproject` metadata were changed to match rather than left
  contradicting the file they point at, and the packaging carries
  `Private :: Do Not Upload`, so an accidental publish is refused by the index
  instead of being an irreversible open-sourcing.
- The repo (github.com/WinterLabsHQ/Ohm) stays **private**, and that is now the
  permanent answer rather than a holding position.
- Downstream: any binary embedding Ohm — PinForge's desktop bundle — is now a
  WinterLabs-internal call rather than an open-source grant.
- **Not legal advice.** The text is a standard-shape proprietary licence; counsel
  should read it against the first real customer agreement before it is signed.

Also in that commit: README rewritten in plain language. It opened by naming
itself an "AI-native, physics-aware hardware description language" and expected
the reader to already know what that bought them; it now says what Ohm does in a
sentence a hardware engineer can act on, keeps every claim, and drops the ones it
made twice. Two stale numbers corrected — 840 tests → 895, and "emits a KiCad 7
project" → writes the KiCad 7 format, verified through KiCad 10.0.5.

**Note on the blueprint.** `docs/blueprint/OHM_ENGINEERING_BUILD_MANUAL.md` still
states D6 as open-core with the licence TBD. The blueprint is immutable by
design; this log is where the deviation is recorded, per the standing ritual.
The language and compiler are no longer the open half of D6 — the boundary that
survives is the one that always mattered: Meridian and the convergence solver are
separate products consuming the `shared/` seam, never in this repo.

## KiCad 7 was containerised all along (2026-08-31)

A correction, recorded rather than quietly applied, because five files stated it
as fact and one of them turned it into an `exit 2`.

**The claim.** "No official KiCad container image exists below 8.0.4." It
appeared in `KICAD_CI_PLAN.md` (four times, once as a "hard ceiling"),
`KICAD_CI_IMPLEMENTATION.md`, ADR-021 (twice), `README.md`, this log, the
`ci.yml` matrix comment and `tests/kicad_in_docker.sh`, which rejected any 7.x
before it reached `docker`.

**The fact.** `kicad/kicad` carries `7.0`, `7.0.8`, `7.0.9`, `7.0.10` and
`7.0.11`. `kicad/kicad:7.0.11` is linux/amd64, 440 MB, pushed 2024-02-23.
`Dockerfile.7.0-stable`, in the same `kicad-cli-docker` repo that builds the 8, 9
and 10 images, clones `kicad-symbols`, `kicad-footprints` and `kicad-templates`
at the matching `7.0` branch — so the property the whole container strategy rests
on, *the binary and the libraries are the same version by construction*, holds at
7 exactly as it does at 8, 9 and 10. Debian 12 base, like 8 and 9, so the
uv-installs-3.12 step needed no change.

**How it went wrong.** Docker Hub's tag listing is ordered by `last_updated` and
returns 50 rows per page. 7.x last moved in February 2024, so it is not on page
one, and page one is what somebody looked at. It was then copied from document to
document without being re-checked, which is the failure mode this repo's own
"corrections forced by measurement" habit exists to interrupt — and did not,
because nothing re-measured it. `tools/check_kicad_tags.py` paginates properly
and is what found it; it now runs in CI (`tags` job) against every pinned tag, so
the same mistake cannot be made by eye a second time.

**What changed.** The floor stays at **7** — `SUPPORTED_KICAD = ("7","8","9",
"10")` is unchanged, and the proposal in ADR-021 and `KICAD_CI_PLAN.md` to raise
it to 8 is withdrawn, since it rested entirely on the false claim. The `kicad`
matrix is now **7.0.11, 8.0.9, 9.0.9, 10.0.5** with no step excluded and no step
conditional: every `kicad-cli` subcommand the gates use — `sch export netlist`,
`sch export pdf`, `pcb export pos`, `pcb export gerbers`, `pcb export drill`,
`sym upgrade` — predates KiCad 8. `pcb upgrade` is still KiCad 10 only, so the
read-back gate re-saves through `pcbnew` at 7 as it already does at 8 and 9.

**The pad table follows.** The 7 leg dumps KiCad 7's pad ids like every other
leg, and the `fpdata` job rebuilds the table from every matrix dump and fails on
any difference — so a 7 leg and a `pads.json.gz` carrying major 7 have to land
together, in either order, and the job is loud if they do not. Wherever the table
has no data for a major, `ohm/fpdata/__init__.py` abstains and S012 reports
*unchecked* rather than clean; that was never a statement about KiCad 7 in
particular.
