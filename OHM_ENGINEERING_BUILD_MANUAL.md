# OHM — ENGINEERING BUILD MANUAL (Ohm language + compiler only)

> **Read-only canonical build manual.** Scope: the Ohm language and its own compiler,
> end to end — parser, IR, checkers, emitters, CLI, tests. Meridian (router) and the
> physics *solver* layer are OUT OF SCOPE here; Ohm only exposes clean seams for them.
> This manual is written to be executed by Claude Code phase by phase. Every phase has an
> exit gate with concrete acceptance tests. **Do not start a phase until the previous
> phase's gate passes.**

---

## PART 0 — BASE TECHNOLOGY DECISIONS (locked)

| Concern | Decision | Why |
|---|---|---|
| Compiler implementation language | **Python 3.12** | Build is pure/hermetic (no perf pressure); fastest agent+human iteration; KiCad/parts/ML ecosystem is Python; PCB-as-code precedent (SKiDL, atopile toolchain) is Python. |
| Surface-syntax parser | **Lark** (`lark`, MIT, pure-Python, zero deps) | EBNF grammar, auto AST, line/col tracking, Earley→LALR same grammar. |
| Parser algorithm | **Earley during dev → LALR(1) once grammar stabilizes** | Earley is forgiving while iterating; LALR is O(n) for production. Same `.lark` grammar for both. |
| Surface syntax style | **Custom grammar, Python-*shaped*** (indentation blocks, `#` comments) | Ohm owns its syntax (superset dropped, ADR-001); Starlark can't cleanly express `role=`, `~`, `10kohm +/- 1%`. Python shape keeps LLM familiarity. |
| Netlist / layout output | **Own emitters targeting KiCad documented formats** | Own the representation (ADR-001); validate via KiCad import + golden corpus. |
| `.zen` | **Import + export interop only** | Cross-compatible, never a runtime dependency. |
| Packaging | `pyproject.toml`, `uv` or `pip`; console entry point `ohm` | Standard, reproducible. |
| Python deps (compiler core) | `lark` only (hard dep). `pytest` (dev). | Keep the open compiler dependency-light. |
| Symbol/footprint generation | **Deterministic generation from frozen pin data; emit KiCad `.kicad_sym` + `.kicad_mod`** | Package geometry is IPC-7351 formula-driven → pure/hermetic. Solves KiCad's sparse library + manual pin-by-pin pain (Part 11). |

**Why NOT Rust now:** buys speed you don't need for parse-check-emit, at the cost of the
iteration velocity a startup needs. Mitigation: keep IR + emitters behind clean module
boundaries so a hot path can later be ported to Rust without touching the rest. Do not do
this pre-emptively.

**Why NOT reuse Starlark's parser now:** it was only attractive while Ohm was a Zener
superset. That's gone. A custom Lark grammar gives exactly the designed syntax with better
errors and no fight.

---

## PART 1 — WHAT OHM IS (recap of founding decisions; do not reverse)

- **D1 Own the core.** Own compiler, IR, emitters. `.zen` = interop only.
- **D2 Physics-aware by design, safe side only.** Closed-form/algebraic physics is
  deterministic+hermetic → evaluated in-compiler (advisory + checks). Convergence physics
  (field/SPICE/EM) → NEVER in-compiler; declared as a *hole* for the external solver layer.
  Enforced by grammar: closed-form = functions returning values; convergence = declarations.
- **D3 Detectability = declared intent.** Compiler catches a bug iff it can compare BUILT
  vs DECLARED-MEANT. More declarable intent ⇒ more caught. Never claim "catches all."
- **D4 Ergonomics ⇄ safety aligned.** The easiest authoring path is the most checkable one
  (typed-interface connect is shorter AND enables miswire detection).
- **D5 Human + agent friendly.** Python-shaped, declarative, free declaration order,
  defaults+overrides, first-class units/tolerances.
- **D6 Open-core.** Language + reference compiler = open source (license TBD, get legal
  advice). Meridian + physics solver + ML + hosted platform = proprietary.

**Hermetic build invariant (protects everything):** `ohm build` does NO I/O, network,
clock, or solver. Same source ⇒ identical bytes on any machine/commit.

---

## PART 2 — DETECTABILITY MODEL (what the compiler must catch)

| Tier | Needs | Examples | Where implemented |
|---|---|---|---|
| **S — Structural** | nothing beyond a well-formed design (self-closing loop) | pin↔pad count/name mismatch; declared package ≠ footprint package; unmapped pin/pad | `check_structural.py` |
| **I — Intent-relative** | declared typed intent | miswired typed interface (SDA↔SCL, D+↔D−); output↔output; role/direction conflict | `check_intent.py` |
| **Q — Quantitative** | component ratings data | power > rating; current > pin/source budget; voltage > absolute-max; coarse thermal | `check_quant.py` |
| **Undetectable** | — | "wrong IC for job", app-level value judgments | (never claimed) |

Incorrect footprint = Tier S (both sides declared → checkable). Miswired pair = Tier I
(valid graph; only catchable because the interface is typed). This split is the core of D3.

---

## PART 3 — THE LANGUAGE SPEC (v1)

### 3.1 Lexical
- UTF-8. `#` line comments. Indentation-based blocks (like Python). Newline-significant.
- Identifiers: `[A-Za-z_][A-Za-z0-9_]*`.
- **Quantity literals** (first-class): `<number><unit>` e.g. `3.3V`, `100mA`, `10kohm`,
  `85degC`, `50ohm`, `4.7uF`, `2mm`. Optional tolerance: `10kohm +/- 1%` or `10kohm +/- 500ohm`.
- Units (v1 set): `V, mV, kV; A, mA, uA; ohm, kohm, Mohm; F, uF, nF, pF; H, uH, nH;
  W, mW; degC; Hz, kHz, MHz, GHz; m, mm, um, mil`. SI prefixes handled in the unit table.

### 3.2 Top-level declarations
```
interface <Name>:                 # R5 — a typed protocol/bundle
    <member>: <SignalType>[, role=<role>]
    [require <relation>]          # optional declared relationship (enables Tier-I)

component <Name>:                 # R1 — a typed record mapped to a package
    package = "<footprint-id>"    # reference an existing KiCad footprint, OR:
    [package = generate(<PackageType>, pins=<N>[, pitch=..., body=..., ...])]  # GENERATE it (Part 11)
    [<attr> = <value>]            # mpn, manufacturer, datasheet, designator_prefix...
    pin <Name>: role=<role>[, pad=<pad-id>][, vmax=<V>][, imax=<A>][, pmax=<W>]
    ...                           # pad=<pad-id> maps a pin to a generated pad (Tier-S loop)

module <Name>:                    # R6 — function-like: typed config + ports + body
    [<cfg> = config(<Type>[, default=...])]
    [<port> = io(<Type>)]
    <inst> = <Component|Module>(<args>)      # R2 — constructor/variable style
    <a> ~ <b>                                 # R4 — connection (interface-typed preferred)
    [assert <expr>]                           # R7 — closed-form check OR solver hole
```

### 3.3 Roles (pin/interface member semantics — drive Tier-I)
`power_in, power_out, input, output, bidirectional, passive, ground, nc`.
Rules (examples): two `output`/`power_out` on one net → error; `input` with no driver →
warning; `power_in` net voltage > `vmax` → error (Tier-Q); interface member role mismatch
on connect → error (Tier-I).

### 3.4 Connections (R4)
- `a ~ b` connects. If both are interfaces of the same type, members are matched by
  name/role and checked (Tier-I). If both are pins, connected raw (escape hatch).
- Connecting mismatched interface types → error. Connecting swapped members (when a
  `require` relation exists) → error.

### 3.5 Quantities, units, tolerances (R3)
- Dimensional arithmetic; unit mismatch (e.g. `3.3V + 10kohm`) → error.
- Comparisons across a rating are the basis of Tier-Q: `assert net.voltage <= pin.vmax`.
- Tolerances propagate through closed-form expressions (interval arithmetic in v1).

### 3.6 Assertions & holes (R7 + D2)
- `assert <closed-form-bool>` → evaluated at build (deterministic). Failure = compile error.
- Convergence obligations use a distinct **declaration** form, NOT a function call, e.g.
  `obligation ripple(vout) < 50mV` → recorded in IR as a hole; NEVER evaluated at build;
  emitted to the solver-layer seam. Grammar makes this categorically different from `assert`
  so the D2 line cannot be crossed by accident.

### 3.7 Defaults & overrides (R9), declaration order (R8)
- Library parts carry default package/footprint; override via attrs.
- Forward references allowed; resolver builds the graph after full parse.

### 3.8 Illustrative program (canonical example; use as a test fixture)
```
interface I2C:
    scl: Signal, role=bidirectional
    sda: Signal, role=bidirectional

interface Power:
    v:   Signal, role=power_in
    gnd: Signal, role=ground

component NCP1529:
    package = "TSOT-23-5"
    mpn = "NCP1529ASNT1G"
    pin EN:  role=input
    pin GND: role=ground
    pin SW:  role=power_out
    pin VIN: role=power_in, vmax=6.0V
    pin FB:  role=input

module Supply:
    vin  = io(Power)
    vout = io(Power)
    reg  = NCP1529()
    vin.v   ~ reg.VIN
    vin.gnd ~ reg.GND
    assert vin.v.voltage <= reg.VIN.vmax        # Tier-Q closed-form
    obligation ripple(vout.v) < 50mV            # HOLE → solver seam (not built)
```

---

## PART 4 — THE IR (the crown asset; keep it OURS and stable)

Typed, serializable graph. Suggested dataclasses (Python):
```
Quantity(value: Decimal, unit: str, tol_lo: Decimal|None, tol_hi: Decimal|None)
Pin(name, role, vmax: Quantity|None, imax, pmax)
PadMap(pin_name -> pad_id)                      # for Tier-S
ComponentDef(name, package, pins: [Pin], attrs, padmap)
Instance(name, def_ref, arg_overrides)
InterfaceDef(name, members: [(name, signal_type, role)], requires: [Relation])
Net(name, connected_endpoints: [(instance, pin|member)])
Constraint(kind, args)                          # impedance/length/diffpair/keepout (R7)
Hole(kind, expr, bound)                         # D2 convergence obligation
Module(name, configs, ports, instances, nets, constraints, holes, asserts)
Design(modules, top)                            # whole program
```
Rules: IR is produced only by the resolver; checkers READ it and emit diagnostics; emitters
READ it and never mutate. Serialize to JSON for tests/debug. **Determinism:** stable
ordering everywhere (sorted keys, insertion-ordered lists) so builds are byte-reproducible.

---

## PART 5 — COMPILER PIPELINE & MODULE MAP

```
cli.py
  └─ parse.py      (Lark grammar ohm.lark → Lark tree → AST dataclasses)
  └─ resolve.py    (names, forward refs, module elaboration, defaults/overrides → IR)
  └─ check_*.py    (structural / intent / quant / units; produce Diagnostics; never mutate IR)
  └─ closedform.py (pure physics fns: divider, series/parallel, microstrip Z, P=I²R, RC…)
  └─ emit_*.py     (netlist, kicad, zen_export, constraints)   ; imp_zen.py (zen import)
```
Diagnostics: `{severity, code, message, span(line,col), endpoints}`; machine-parseable line
+ human explanation; `ohm check --explain <code>`.

### Repo layout
```
ohm/
  pyproject.toml
  ohm/ohm.lark                 # THE grammar
  ohm/parse.py  ohm/ast.py     # tree → AST
  ohm/resolve.py  ohm/ir.py
  ohm/units.py  ohm/types_phys.py
  ohm/check_structural.py  ohm/check_intent.py  ohm/check_quant.py
  ohm/closedform.py  ohm/holes.py
  ohm/ratings_iface.py         # Tier-Q data contract (provider pluggable; see Part 7)
  ohm/pkggen/                  # SYMBOL + FOOTPRINT GENERATION (see Part 11)
    ohm/pkggen/packages.py     #   parametric package models (SOIC/QFP/QFN/BGA/SOT/passives...)
    ohm/pkggen/footprint.py    #   IPC-7351-based pad geometry → KiCad .kicad_mod
    ohm/pkggen/symbol.py       #   pin table + layout → KiCad .kicad_sym
    ohm/pkggen/pindata_iface.py#   pin-data contract (declared / frozen / provider) — like ratings_iface
  ohm/emit_netlist.py  ohm/emit_kicad.py  ohm/emit_zen.py  ohm/emit_constraints.py
  ohm/imp_zen.py
  ohm/diagnostics.py  ohm/cli.py
shared/constraints_schema.py   # seam to proprietary Meridian/physics (imported by both)
tests/golden/                  # our designs + expected netlists (oracle)
tests/known_bad/               # one fixture per S/I/Q error code
tests/fixtures/                # the Part 3.8 canonical program etc.
docs/                          # language spec (generated), STYLE.md (for humans + LLM)
```

---

## PART 6 — PHASES & EXIT GATES (execute in order)

**P0 — Scaffold.** Repo, `pyproject.toml`, `ohm` entry point, `pytest`, CI.
*Gate:* `ohm --version` runs; empty test suite green.

**P1 — Grammar + parser + AST.** Write `ohm.lark` for Part 3 syntax (Earley). AST
dataclasses. Parse the 3.8 canonical program.
*Gate:* 3.8 parses to expected AST; ≥10 syntax fixtures (incl. malformed → clean error with
line/col) pass.

**P2 — Resolver + IR.** Names, forward refs (R8), module elaboration, defaults/overrides
(R9) → IR. JSON serialization with deterministic ordering.
*Gate:* 3.8 → IR; IR JSON is byte-identical across two runs/machines.

**P3 — Units + dimensional checking (R3).** `units.py` table; quantity parsing;
interval/tolerance arithmetic; unit-mismatch errors.
*Gate:* `3.3V + 10kohm` → error; `10kohm +/- 1%` parses and propagates; conversions correct.

**P4 — Own netlist emitter + KiCad emitter + golden corpus.** Emit netlist + `.kicad_sch/
.kicad_pcb`. Build a golden corpus WE own (hand-verified). 
*Gate:* emitted netlist matches golden for ≥5 designs; KiCad imports the output without
error.

**P5 — Tier-S structural checks.** pin↔pad, package match, unmapped pins.
*Gate:* every `tests/known_bad` S-fixture errors with the right code; good designs still emit.

**P6 — Interface type system + Tier-I intent checks. ★ HEADLINE — make-or-break.**
Interface matching on `~`; role/direction rules; `require` relations; swapped-member
detection.
*Gate:* swapped SDA/SCL and D+/D− fixtures error; output↔output errors; correct designs
pass. *This is the feature that beats standard ERC; do not proceed until it is solid.*

**P7 — `.zen` import + export interop.** `imp_zen.py` (.zen→IR), `emit_zen.py` (IR→.zen).
*Gate:* a real `.zen` file imports to IR and re-exports; round-trips structurally; optional
`pcb` cross-check on the interop path only (never wired into `ohm build`).

**P8 — Closed-form physics + holes (D2).** `closedform.py` pure functions; `obligation`
form → `Hole` in IR; assert build path invokes NO solver.
*Gate:* (a) a closed-form value (e.g. microstrip Z) is bit-identical across two machines;
(b) an `obligation` becomes a hole and a test asserts no solver import/call in `ohm build`.

**P9 — Tier-Q quantitative checks.** Needs ratings provider (Part 7 decision). Power/
current/voltage/thermal comparisons against ratings.
*Gate:* over-power/over-current/over-voltage fixtures error deterministically; ratings come
through `ratings_iface`; builds stay hermetic (ratings frozen into design).

**P10 — Constraint-schema emitter + CLI + LSP.** `emit_constraints.py` → `shared/
constraints_schema.py`; `ohm build|check|fmt`; LSP surfacing S/I/Q diagnostics in VS Code.
*Gate:* R7 constraints emit the shared schema; a Tier-I error shows as a red squiggle before
build.

**P11 — Symbol + footprint generation (see Part 11).** Depends on the KiCad emitter (P4) and
the pin↔pad model (P5). Build `ohm/pkggen/`: parametric package models, IPC-7351 footprint
geometry → `.kicad_mod`, pin-table → `.kicad_sym`, and the `package = generate(...)` grammar.
*Gate:* (a) `generate(SOIC, pins=8, pitch=1.27mm)` produces a `.kicad_mod` + `.kicad_sym` that
imports into KiCad clean and matches a golden reference footprint within tolerance; (b) the
generated pads match the component's `pin ... pad=` map (Tier-S passes on generated packages);
(c) generation is byte-identical across two machines (hermetic/deterministic). `ohm gen-package`
CLI works standalone. NO network/datasheet fetch in the build path.

**P12 — Agent-usability validation.** Author `docs/STYLE.md`. Give an LLM only the spec +
STYLE + a datasheet.
*Gate:* the LLM produces a correct `component` + `module`, **including a generated package for
a part missing from KiCad's library**; a deliberately swapped interface member is caught by
`ohm check`. (Both human and agent authorship demonstrated, and the library-gap pain solved.)

---

## PART 7 — OPEN DECISION BEFORE P9: ratings-data source
Tier-Q is only as good as ratings data. Options: (a) parse KiCad/vendor symbol libs
(open, sparse); (b) parts API e.g. Octopart/Nexar (rich; network + licensing; must be
fetched at authoring time and **frozen into the source** to keep builds hermetic);
(c) curated DB we maintain (quality; effort; candidate proprietary asset); (d) ratings as
typed fields in the `component` decl (always hermetic, zero dep; author/agent supplies).
**Recommended:** (d) as the always-on baseline (already in the grammar via `vmax/imax/pmax`)
+ (c) proprietary enrichment + optional (b) frozen at author time. Whatever the source,
**ratings must be frozen into the design** so `ohm build` stays hermetic. Resolve with a
short research pass before P9.

---

## PART 8 — SEAMS TO THE PROPRIETARY PLATFORM (keep clean; open never depends on closed)
- **`shared/constraints_schema.py`** — impedance/length/diffpair/netclass/keepout entries
  (authored via R7). Emitted by Ohm; consumed by Meridian + physics layer. Single interface.
- **Holes** — `obligation` declarations recorded in IR; handed to the solver layer;
  confirmed at a downstream verification stage. Ohm records, never solves.
- **Ratings provider** — `ratings_iface.py` is an open contract; a specific provider may be
  proprietary. The open compiler ships with provider (d).

---

## PART 11 — SYMBOL & FOOTPRINT GENERATION (solve KiCad's sparse-library pain)

**Problem being solved.** KiCad's built-in library lacks many parts, and users must build
symbols + footprints pin-by-pin by hand — slow and error-prone. Ohm generates both from a
compact declaration, so any needed component becomes usable in KiCad schematic + PCB design.

**The safe/dangerous split (this is the whole architecture of the feature).**
- **Generation from known pin/geometry data = DETERMINISTIC + HERMETIC → in-compiler.**
  Footprint pad geometry follows **IPC-7351** formulas per package family; symbol layout is
  arithmetic over a pin table. Same inputs ⇒ byte-identical `.kicad_mod` + `.kicad_sym`.
- **Acquisition of pin/geometry data = the risky part → NEVER in the build path.** Pulling a
  pinout from a datasheet/PDF/parts-API or LLM inference is non-hermetic and can be wrong.
  It happens at **authoring time**, is **frozen into the source** (or user/agent-declared),
  and only then fed to the deterministic generator. Same discipline as ratings (Part 7) and
  the closed-form/convergence line (D2).

**Grammar.** `package = generate(<PackageType>, pins=<N>[, pitch=..., body=..., pad=...,
thermal_pad=...])` (see §3.2). `PackageType` ∈ a parametric catalog: passives (0402/0603/
0805…), SOT-23/223, SOIC, SSOP/TSSOP, QFP, QFN, DFN, BGA, SON, headers/connectors, and a
generic fallback (explicit pad list). Each type is a parametric model in `pkggen/packages.py`.

**Pin↔pad loop (ties into Tier-S).** `pin <Name>: ... pad=<pad-id>` maps each electrical pin
to a generated pad. The Tier-S checker (P5) then verifies every pin maps to exactly one
generated pad and vice versa — so a generation/mapping mistake is a compile error, not a
silent bad board. This is the detectability principle (D3) applied to generation.

**Outputs & modules.**
- `pkggen/footprint.py` → `.kicad_mod` (IPC-7351 pad geometry, courtyard, silkscreen, origin).
- `pkggen/symbol.py` → `.kicad_sym` (pin table → schematic symbol with pin roles/numbers).
- `pkggen/packages.py` → parametric package math (the catalog).
- `pkggen/pindata_iface.py` → pin-data contract: declared-in-source (baseline, always
  hermetic) | frozen-from-provider (authoring-time, then frozen). Mirrors `ratings_iface`.
- `ohm gen-package ...` CLI → generate standalone, without a full build, for library building.

**Honest claim (N3 discipline — critical).** Claim exactly: *"Ohm generates correct KiCad
symbols and footprints from the pin/geometry data you provide or freeze in."* NEVER claim it
*perfectly* infers any component from nothing — auto-acquired pin data can be wrong, and an
unverified footprint that reaches fab is the exact failure N3 guards against. Generation is
perfect; *acquisition* is best-effort and must be author-verified. Keep that line bright.

**Standards note.** Follow IPC-7351B/C land-pattern rules; expose density level (least/
nominal/most) as a parameter. Pin the targeted KiCad file-format major version (as in P4).

---

## PART 9 — INVARIANTS (protect against all future edits)
1. `ohm build` hermetic/reproducible — no I/O, network, clock, solver. Ever. **Includes
   package generation: no datasheet/network fetch in the build path; pin data is frozen.**
2. D2 line enforced by grammar (`assert` = closed-form/built; `obligation` = hole/delegated).
   "Just add a quick solve" is forbidden — it breaks #1.
3. Detectability claims bounded by declared intent (D3). Never "catches all mistakes."
   **Package generation: claim generation is correct from given data; never claim perfect
   auto-inference of any component.**
4. Ergonomics ⇄ safety alignment (D4): reject syntax where the easy path is less checkable.
5. IR stays OURS and is the stable core (D1).
6. Open/closed boundary = representation (open) vs hard computation (closed) (D6); the open
   compiler must not import proprietary code. **Package generation (IPC-7351 geometry) is
   representation → OPEN. A proprietary pin-data provider stays behind `pindata_iface`.**
7. Determinism: stable ordering in IR + all emitters; builds are byte-identical. **Generated
   `.kicad_mod`/`.kicad_sym` are byte-identical across machines.**

---

## PART 10 — HONEST CAVEATS
- Owning netlist/KiCad emission is real scope (accepted, D1); it's bounded engineering
  (documented formats), not research.
- Ohm catches S/I/Q errors — valuable, but NOT design-judgment errors; analog subtlety
  resists capture, so make the interface/type system richest where analog lives.
- Closed-form physics is advisory for design and authoritative only for its specific
  realizability checks; convergence-physics sign-off belongs to the proprietary solver
  layer, never the compiler.
- Open-source license (D6) is unresolved and needs legal advice; it affects adoption vs
  protection materially.
- KiCad file formats evolve across versions; pin the targeted KiCad major version in P4 and
  test against it explicitly. This applies to generated `.kicad_mod`/`.kicad_sym` too (P11).
- Package generation (P11) makes *generation* perfect but *pin-data acquisition* best-effort:
  a generated footprint is only as correct as the pin/geometry data frozen into the source.
  Always surface generated packages for author/agent review before fabrication; never imply
  a datasheet-scraped pinout is guaranteed correct.
```
