# OHM — COMPILER DESIGN (internals)

> **Read-only, canonical. Companion to `OHM_ENGINEERING_BUILD_MANUAL.md`.**
> The build manual says WHAT to build and in what phase order. This document specifies HOW
> the compiler is designed internally: the pass architecture, the IR levels and lowering,
> the symbol table, the semantic-analysis/checking engine, error recovery, and the
> contracts between passes. Claude Code should read this alongside the manual before
> writing any compiler code.

---

## 0. What KIND of compiler this is (framing that drives every decision)

Ohm does not compile to machine code. It compiles a **declarative description of a physical
artifact** into (a) a netlist, (b) KiCad files, (c) a constraint set for the proprietary
platform. Consequences for the design:

- The center of gravity is **semantic analysis over a graph**, not code generation. Ohm is
  architecturally closer to a **type checker + linter (TypeScript/clippy-style)** than to a
  classic lex→parse→codegen compiler.
- The "program" is a **graph** (components, nets, interfaces), not a linear instruction
  stream. Passes traverse and annotate a graph, not a control-flow/AST spine only.
- There is **no runtime**. All meaning is resolved at build time. This is why hermeticity
  (D2) is achievable and mandatory.
- Therefore the pass pipeline is: **parse → build symbol table → resolve/elaborate →
  annotate (types/quantities/roles) → check (S/I/Q) → lower → emit.** Checking is the
  product; emission is mechanical.

---

## 1. Compiler pass architecture (the spine)

A **nanopass-style** pipeline: many small, single-purpose passes, each with an explicit
input and output type, no hidden state, no pass mutating another pass's output. This is the
single most important design choice — it makes the compiler testable pass-by-pass (each
phase gate in the manual maps to one or more passes) and keeps determinism auditable.

```
 SOURCE (.ohm text)
   │  P-LEX/PARSE
   ▼
 CST  (Lark concrete syntax tree)
   │  P-AST         (lower CST → typed AST dataclasses; discard syntax noise)
   ▼
 AST  (faithful tree, spans attached)
   │  P-SYMBOLS     (build symbol table: interfaces, components, modules)
   ▼
 AST + SymbolTable
   │  P-RESOLVE      (resolve names/refs; report undefined/duplicate)
   │  P-ELABORATE    (instantiate modules/components; expand defaults/overrides)
   ▼
 IR-HIGH  (graph: instances, nets, interfaces, unresolved quantities)
   │  P-UNITS        (parse/normalize quantities; dimensional typing; tolerance intervals)
   │  P-CONNECT      (resolve `~` connections into concrete Nets; interface member mapping)
   ▼
 IR-TYPED  (fully typed graph; every endpoint has role + quantity where known)
   │  P-CHECK-S      (structural)                       ┐
   │  P-CHECK-I      (intent/interface/role)            ├─ produce Diagnostics; DO NOT mutate IR
   │  P-CHECK-UNITS  (dimensional consistency)          │
   │  P-CHECK-Q      (quantitative, via ratings)        ┘
   │  P-CLOSEDFORM   (evaluate assert exprs; fold closed-form physics)   (may annotate, not restructure)
   │  P-HOLES        (collect `obligation`s into Hole records)
   ▼
 IR-CHECKED  (typed graph + diagnostics + holes + folded values)
   │  P-LOWER        (IR-CHECKED → IR-LOW: flat netlist-shaped form)
   ▼
 IR-LOW  (flat: nets, nodes, parts, constraints, holes, package_specs — emitter-ready)
   │  P-EMIT-NETLIST / P-EMIT-KICAD / P-EMIT-ZEN / P-EMIT-CONSTRAINTS / P-GEN-PACKAGE
   ▼
 OUTPUTS  (netlist, .kicad_sch/.kicad_pcb, .zen, constraint schema, .kicad_sym/.kicad_mod)
```

**P-GEN-PACKAGE** is a deterministic emit-family pass (like the other emitters): it reads
`package_specs` from IR-LOW (each produced by a `generate(...)` declaration, §4.8) and writes
KiCad `.kicad_sym` + `.kicad_mod`. It performs NO acquisition — pin/geometry data is already
frozen in the IR by authoring time. Pure IPC-7351 geometry + pin-table layout arithmetic.

**Pass contract (enforced in code):** each pass is a pure function
`pass(input_ir, ctx) -> (output_ir, [Diagnostic])`. `ctx` is read-only config
(target KiCad version, ratings provider, flags). No pass performs I/O except the emit
passes writing final files and the parse pass reading source. No pass mutates its input in
place — it returns a new structure (or a structurally-shared copy). This guarantees the
hermetic + deterministic invariants and makes every pass unit-testable in isolation.

**Halting policy:** checking passes are **non-fatal and run to completion** so the user/
agent sees ALL errors at once (linter behavior), then the driver stops before P-LOWER if
any diagnostic has severity=error. Parse/resolve errors that make later passes meaningless
stop earlier (see §5 error recovery).

---

## 2. The IR at three levels (why lowering exists)

A single IR cannot serve parsing, checking, and emission well. Three explicit levels, each
with a clear job. This is standard practice (high→mid→low lowering) and it is what keeps
each pass simple.

| Level | Shape | Purpose | Key contents |
|---|---|---|---|
| **IR-HIGH** | hierarchical graph | mirrors the source: modules contain instances/connections | `Module`, `Instance`, raw `~` connections, unresolved `Quantity` strings |
| **IR-TYPED** | hierarchical graph, fully annotated | the substrate the checkers read | concrete `Net`s, resolved interface member maps, typed `Quantity` with tolerance intervals, roles on every endpoint |
| **IR-LOW** | flat | emitter-ready; hierarchy flattened, names uniquified | `parts[]`, `nets[]` (each a list of `(part, pin)` nodes), `constraints[]`, `holes[]` |

Lowering IR-HIGH→IR-TYPED is *annotation* (add information, keep shape). Lowering
IR-TYPED→IR-LOW is *flattening* (resolve hierarchy, uniquify names, drop module structure).
Checkers run on IR-TYPED (they need hierarchy + types); emitters run on IR-LOW (they need
flat connectivity). Keeping these separate is why the checker code stays readable.

**Determinism rule (applies to every IR level):** all collections are insertion-ordered or
sorted by a stable key; name uniquification in P-LOWER uses a deterministic scheme
(`<module-path>.<local-name>`). Two builds of the same source produce byte-identical IR-LOW.

---

## 3. Symbol table & scoping

- **Global scope:** all top-level `interface`, `component`, `module` names. Duplicate name →
  error at P-SYMBOLS.
- **Module scope:** each `module` introduces a scope holding its `config`/`io` params and
  its instance names. Instances and ports are looked up here.
- **Instance member access:** `reg.VIN` resolves through the instance's `ComponentDef` pins;
  `mcu.i2c` resolves through the instance's interface ports. Unknown member → error at
  P-RESOLVE with a "did you mean" suggestion (nearest name by edit distance — cheap, big UX
  win for humans and agents).
- **Forward references (R8):** P-SYMBOLS runs over ALL top-level decls before P-RESOLVE, so
  order-independence is free — a module may reference a component declared later.
- **No shadowing in v1:** a module-local name may not shadow a global; keeps mental model
  simple for both audiences. Revisit only if a real need appears.

Symbol table is built once (P-SYMBOLS), then read-only for all later passes.

---

## 4. The checking engine (the product — design it well)

All checkers share one design so they behave consistently and compose.

### 4.1 Common shape
Each checker is a **visitor over IR-TYPED** that emits `Diagnostic`s and mutates nothing.
A checker never stops the pipeline itself; the driver decides based on collected severities.

```
class Checker(Protocol):
    code_prefix: str                     # "S", "I", "Q", "U"
    def check(self, ir: IRTyped, ctx) -> list[Diagnostic]: ...
```

### 4.2 Tier-S (structural) — `check_structural.py`
Graph-local, needs no external data. For each `Instance`: verify every `ComponentDef.pin`
has a `PadMap` entry and vice versa; verify declared `package` matches the footprint's pad
set; flag unmapped pins/pads. Pure structural comparison of two things both present in the
program (the "self-closing loop").

### 4.3 Tier-I (intent) — `check_intent.py`
Runs over `Net`s and interface connections. For each connection made via `~`:
- if both endpoints are interfaces: check same interface type; map members by name; verify
  role compatibility (e.g. `output`↔`input`, not `output`↔`output`); if a `require`
  relation exists, verify member pairing (catches SDA↔SCL, D+↔D− swaps).
- for each `Net`: apply role rules (≥2 `output`/`power_out` → error; `input` with no driver
  → warning).
This pass is the headline differentiator (manual P6). Its correctness is the make-or-break
gate.

### 4.4 Tier-U (units) — `check_units.py`
Dimensional consistency of every expression and connection: a `power_in` net's voltage vs a
`vmax` must be comparable (both volts); `assert` expressions must be dimensionally sound.
Runs before Q (Q depends on unit-correct comparisons).

### 4.5 Tier-Q (quantitative) — `check_quant.py`
Needs ratings (from `ratings_iface`, frozen into the design — hermetic). For each part/net:
compare computed/declared electrical quantity against the rating: `net.voltage > pin.vmax`,
branch current > `imax`, dissipation (via closed-form P=I²R etc.) > `pmax`, coarse thermal
rise > limit. Emits errors; the closed-form values come from P-CLOSEDFORM.

### 4.6 Closed-form evaluation — `closedform.py` (P-CLOSEDFORM)
Pure, deterministic functions: voltage divider, series/parallel R/C, RC time constant,
microstrip/stripline impedance (Wheeler/Hammerstad), P=I²R / P=V²/R, coarse thermal rise.
Used to (a) fold `assert` expressions to booleans, (b) supply quantities Tier-Q compares.
**Interval arithmetic** carries tolerances through (a `10kohm ±1%` in a divider yields an
output range). NEVER iterative/convergent — that is a Hole.

### 4.7 Holes — `holes.py` (P-HOLES)
Collect every `obligation` declaration into a `Hole(kind, expr, bound)` in the IR. These are
NOT evaluated. They flow to IR-LOW and out through the constraints emitter to the solver
seam. A test asserts no solver code is importable/callable from the build path.

### 4.8 Package generation — `pkggen/` (P-GEN-PACKAGE, an emitter-family pass)
Handles `package = generate(<Type>, ...)` to solve KiCad's sparse-library / manual-pin pain.
Design mirrors the closed-form/hole discipline exactly:
- **Elaboration (in P-ELABORATE):** a `generate(...)` declaration becomes a `PackageSpec` in
  the IR (type, pin count, pitch, body, pad params, pin→pad map). No geometry computed yet.
- **Structural check (P-CHECK-S):** verify each component `pin ... pad=<id>` maps to exactly
  one pad the `PackageSpec` will produce, and vice versa (the pin↔pad loop). A mismatch is a
  compile error — a generation mistake never silently ships.
- **Generation (P-GEN-PACKAGE emit pass):** `packages.py` turns the `PackageSpec` into pad
  geometry via **IPC-7351** formulas (deterministic; density level is a parameter);
  `footprint.py` writes `.kicad_mod`; `symbol.py` writes `.kicad_sym` from the pin table.
- **Pin-data acquisition is NOT here.** Any datasheet/parts-API/LLM-derived pin data is
  resolved at authoring time through `pindata_iface.py` and **frozen into the source** before
  the build. The build path (parse→…→emit) never fetches. This keeps §6 hermeticity intact.
- **Purity:** given a frozen `PackageSpec`, output is byte-identical across machines
  (`Decimal`/fixed-precision geometry, stable ordering). Same guarantees as every emitter.

Rationale recorded so a later session doesn't "improve" it into a live datasheet scraper:
*generation is perfect; acquisition is best-effort and must be author-verified.* Moving
acquisition into the build would break hermeticity (N1) and the honesty claim (N3).

---

## 5. Error handling & recovery (first-class, because agents + humans depend on it)

Diagnostics are a product surface, not an afterthought. Design:

- **Diagnostic record:** `{severity: error|warning|info, code: "I014", message, primary_span,
  related_spans[], help, endpoints[]}`. Codes are stable and documented (`--explain I014`).
- **Two audiences, one record:** render a terse machine-parseable line (for the LLM loop:
  `path:line:col: error[I014]: message`) AND a human block with source excerpt + caret +
  `help:` suggestion. Same underlying record.
- **Error recovery in the parser:** use Lark's error handling to **skip to the next
  top-level declaration** on a syntax error, so one bad `component` doesn't hide errors in
  the rest of the file. Collect multiple syntax errors per run.
- **Semantic recovery:** checking passes never abort on the first error; they record and
  continue (so a design with 5 miswires reports all 5). Use **poison values**: when a name
  fails to resolve, substitute a `Poison` node that suppresses cascading errors downstream
  (prevents one undefined name from generating 20 spurious diagnostics).
- **Fatal boundary:** if P-PARSE or P-RESOLVE produced any error, run remaining *analysis*
  passes if they can operate on the partial/poisoned IR (best-effort, more diagnostics), but
  **never run P-LOWER/emit** — never emit output from an errored design.
- **Suggestions:** unknown member/name → nearest-by-edit-distance hint. Cheap; large UX
  gain, especially for agent self-correction loops.

---

## 6. Determinism & hermeticity (how the invariants are enforced in the design)

These are not aspirations; they are enforced structurally:
1. **No I/O outside parse(read source) and emit(write files).** Ratings are loaded into
   `ctx` before the pipeline starts and frozen; no pass calls network/disk/clock. A unit
   test greps the build path for forbidden imports (`socket`, `requests`, `time`, solver
   modules).
2. **Pure passes.** Each pass returns new data; inputs are treated immutable. A debug mode
   deep-compares that a pass didn't mutate its input.
3. **Stable ordering everywhere.** Sorted/insertion-ordered collections; deterministic name
   uniquification. A test builds the canonical program twice and asserts byte-identical
   IR-LOW JSON and byte-identical emitted netlist.
4. **Decimal, not float, for quantities.** Use `Decimal` (or a rational type) for quantity
   values so arithmetic is reproducible across machines; only closed-form transcendental
   formulas (impedance) use float, and those are rounded to a fixed precision before compare.

---

## 7. Driver & CLI design

- `ohm build <file>` → full pipeline → emit (only if no errors).
- `ohm check <file>` → pipeline through checkers, **no emit**; fast path (skips lowering/
  emit). This is the agent/human inner-loop command.
- `ohm fmt <file>` → parse → canonical re-print from AST (deterministic formatter).
- `ohm explain <code>` → prints the documented cause/fix for a diagnostic code.
- `ohm gen-package <Type> --pins N [--pitch ... --out dir]` → run P-GEN-PACKAGE standalone to
  emit `.kicad_sym` + `.kicad_mod` without a full build (for library building; §4.8).
- Flags: `--target-kicad <ver>`, `--ratings <provider>`, `--pindata <provider>`,
  `--emit netlist|kicad|zen|constraints|package`, `--json-diagnostics` (agent-loop machine mode).
- The driver orchestrates passes as a list; `--dump-ir <level>` prints IR-HIGH/TYPED/LOW for
  debugging and for test fixtures. Each phase gate in the manual is testable via `--dump-ir`.

---

## 8. Testing strategy tied to the pass architecture
- **Per-pass unit tests:** feed a hand-built input IR, assert output IR + diagnostics. Because
  passes are pure functions, this is trivial and covers the compiler exhaustively.
- **Golden IR tests:** `--dump-ir` snapshots for the canonical program at each level; changes
  must be intentional.
- **Golden netlist tests:** emitted netlist vs owned corpus (manual P4 oracle).
- **Known-bad fixtures:** one per diagnostic code (S/I/Q/U); assert exact code + span.
- **Determinism test:** double-build byte-identity (§6).
- **Hermeticity test:** forbidden-import grep over the build path (§6).
- **Round-trip test:** `.zen` import→export structural equivalence (manual P7).
- **Package-generation test:** `generate(...)` → `.kicad_mod`/`.kicad_sym` matches a golden
  reference within tolerance, imports into KiCad clean, byte-identical across machines, and
  its pads satisfy the component pin↔pad map (manual P11).

---

## 9. How this maps onto the build-manual phases
- Manual **P1** = P-LEX/PARSE + P-AST.  **P2** = P-SYMBOLS + P-RESOLVE + P-ELABORATE (IR-HIGH).
- **P3** = P-UNITS + P-CHECK-UNITS.  **P4** = P-LOWER + emit-netlist/kicad.
- **P5** = P-CHECK-S.  **P6** = P-CONNECT + P-CHECK-I (headline).  **P7** = imp/emit zen.
- **P8** = P-CLOSEDFORM + P-HOLES.  **P9** = P-CHECK-Q.  **P10** = emit-constraints + CLI/LSP.
- **P11** = P-GEN-PACKAGE (`pkggen/`) — depends on P4 (KiCad emit) + P5 (pin↔pad).  **P12** =
  agent-usability validation (incl. generating a library-missing part).
Build passes in this order; each is independently testable, which is exactly why the phase
gates are achievable without regressions.

---

## 10. Design invariants (protect these)
1. Nanopass purity: passes are pure `(ir, ctx) -> (ir, diagnostics)`; no hidden state, no
   input mutation. (Enables testing + determinism.)
2. Three IR levels stay distinct; checkers read IR-TYPED, emitters read IR-LOW.
3. Checkers never mutate IR and never halt the pipeline; the driver gates on severities.
4. No output is ever emitted from a design containing an error.
5. Closed-form vs convergence enforced at the IR level: `assert`→folded value; `obligation`→
   `Hole`. Emit/lower treats them categorically differently.
6. Everything deterministic + hermetic (§6), verified by tests, not by convention.
