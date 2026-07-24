# ADR-010 — Bounded repetition: arrays, for-loops, computed values

Date: 2026-07-24 · Status: accepted

## Motivation
Real boards repeat: LED bars, DIP-switch banks, channel arrays, bussed pull-ups.
Hand-unrolling 8 identical LED+resistor legs is where humans (and LLM authors) make
copy-paste wiring mistakes — exactly the mistake class the compiler exists to remove.
And repeated parts usually need a *derived* value (`(vcc - Vf) / If`), which until now
had to be computed outside the language and pasted in as a magic literal.

## Design: pure elaboration, nothing downstream changes
Three features, one principle — **everything folds at compile time**, so every checker,
emitter, and diagnostic sees only plain instances/connections it already understands:

```
leds = 8 * LED()                        # array instantiation
rs   = 8 * Resistor(value=(vcc - 2V) / 10mA)   # computed value (folds to 130ohm)
for i in 0..7:                          # bounded loop, inclusive range
    pwr.v ~ rs[i].P1
    rs[i].P2 ~ leds[i].A
    leds[i].K ~ pwr.gnd
```

- **`N * Call()`** unrolls to instances `name[0] .. name[N-1]` at resolve, before the
  main binding loop. N must be a literal integer 1..1024 (**R021**). Each element gets
  its own designator, its own kind conformance, its own `expects` obligations.
- **`for VAR in LO..HI:`** — literal integer bounds, inclusive, ≤1024 iterations
  (**R021**). Body is connects/asserts/constraints only (no bindings inside loops —
  arrays are the way to make instances). Unrolled by textual substitution of `[VAR]`
  indices; the loop variable exists *only* inside index brackets of its own loop
  (**R021** elsewhere).
- **Computed attr/kwarg values** — `attr = <closed-form arithmetic>` anywhere a value
  was legal (component attrs, instantiation overrides). Reuses the existing closed-form
  engine (ADR-003): literals, quantities with tolerance intervals, and the enclosing
  module's configs with literal defaults. Tolerances propagate (`5V±10% / 10mA` →
  `500ohm [450,550]`). Anything that cannot fold at compile time — instance refs,
  division by zero, comparisons — is **R022**, never a runtime value.

## What was deliberately excluded (v1)
- No `while`, no unbounded or config-driven counts — bounds are literals so elaboration
  is trivially terminating and hermetic (same guarantee as ADR-003 closed forms).
- No bindings inside `for` bodies — one way to instantiate repetition (arrays), not two.
- ~~No arithmetic on loop variables~~ **Shipped as G1** (same day): `rs[i+1]` /
  `rs[i-1]` — loop-var ± literal integer offset only, evaluated during unroll.
  Negative or non-integer results → **R021** once per loop. This is exactly what
  chains/ladders/cascades need; general index expressions (`rs[2*i]`) remain
  excluded until real demand.
- fmt preserves source forms (`8 * LED()`, the `for` block, unfolded expressions) —
  the *author's* intent is the artifact; unrolling is the compiler's business.

## Adversarial verification (2026-07-24)
The verify pass confirmed emitters (netlist, KiCad pcb/sch, `.zen` with identifier
sanitization), per-element asserts/expects (C001 and B001 fire and stay quiet
correctly over loop-built topologies), module arrays, and fmt-with-comments — and
found **3 real defects**, all fixed + regression-locked:

1. **Silent config-override lie (critical)**: computed values fold against the
   config's *default* at elaborate; `Child(vcc=5V)` was silently ignored — the
   netlist carried the 3.3V-derived value with zero diagnostics. v1 fix: folding a
   config bakes the default in, so *overriding* such a config at instantiation is
   now **R022** (per-instance re-folding needs lower-time context — future work if
   demanded). Overrides of configs not consumed by folds stay legal.
2. **Plain/array name collision**: `rs = Resistor(...)` and `rs = 3 * Resistor(...)`
   coexisted silently (separate scope keys `rs` vs `rs[0..]`). Now **R004**.
3. **`N * io(...)` / `N * config(...)` accepted**: half-broken (an arrayed config
   `vs[0]` cannot even be overridden — kwargs are plain NAMEs). Now **R021**:
   arrays instantiate components or modules only.

Known minor (documented, accepted): dimensionless computed values collapse a
tolerance interval to its midpoint (`5V±10% / 1V` → `5`); dimensioned results keep
the interval. Plain `Decimal` attrs have no tolerance slots — revisit if a real
design needs toleranced dimensionless attrs.

## Determinism & honesty
Unrolling is order-preserving and index-ordered → double-build byte-identical netlists
(tested). Loop-wiring vs hand-written equivalence is tested structurally (identical
netlist modulo `[i]` naming). Per-element checking is tested (an `expects tie` kind in
a 3-array fires B002 exactly 3 times). R021/R022 cover: non-integer/oversized counts,
reversed ranges, out-of-range indices, loop-var escape, bare-array refs (with an
"index it" hint), unfoldable expressions, division by zero.
