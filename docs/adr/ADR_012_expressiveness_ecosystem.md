# ADR-012 — Expressiveness + ecosystem leverage (E0, X0, X1)

Date: 2026-07-24 · Status: accepted

## Motivation
The honest Zener comparison identified two structural disadvantages, declared
non-negotiable by the owner: **expressiveness** (Zener embeds in Starlark and
gets functions/loops/composition free; Ohm pays retail for every construct) and
**ecosystem leverage** (Zener rides the whole KiCad library ecosystem; Ohm's
verified stdlib is a garden). The response is NOT to become Starlark — owning
the grammar is what makes the checking tiers provable — but to close the
day-to-day gap inside the hermetic/deterministic envelope, and to make the
existing ecosystem a supply of inputs rather than a rival moat.

## E0 — expressiveness, all folding at elaborate

**Config-driven counts and bounds.** `rs = n * Resistor(...)`, `for i in 0..n-1:`
where `n` is a config with a literal dimensionless integer default (± literal
offset in bounds). Same guard as computed values: a parent overriding a config
consumed by a fold is **R022** — never a silent stale elaboration.

**Pure functions.** `fn led_r(vcc, vf, i) = (vcc - vf) / i` — top-level, its own
namespace, folded by the closed-form engine everywhere expressions fold (attr
values, instantiation kwargs, asserts, if-conditions). Bodies are pure: params
and literals only (**R023** on free names), calls resolve to fns or closed-form
builtins (**R023**), the call graph must be acyclic (**R023**), positional-only
with checked arity. Tolerance intervals propagate through fn results.

**Compile-time variants.** `if <cond>:` / `else:` at module level, gating any
items including nested arrays and loops. The condition must fold to a definite
truth — comparisons over point values of one dimension, or a nonzero
dimensionless point (**R024** otherwise). Only the taken branch exists
downstream, so checkers/emitters still see plain instances. `io()`/`config()`
must be declared unconditionally (**R024**): a module's boundary cannot depend
on a variant. Conditions consuming config defaults are override-guarded (R022).

What this deliberately is NOT: runtime control flow, recursion, unbounded
iteration, or Turing-completeness. Elaboration remains trivially terminating,
hermetic, and byte-identical.

## X0 — the .zen on-ramp
`ohm check design.zen` imports a Zener design and lints the flat netlist
(**Z007** single-endpoint nets, **Z008** unconnected parts, **Z009** honesty
notice about what a bare netlist cannot express). `ohm convert design.zen`
emits annotatable Ohm source — component decls per (name, pin-set) signature,
numeric pins keep their pads, placeholder pads are loudly TODO-marked
never-for-fabrication, instances and connections preserve the imported net
partition exactly (tested). The TODOs are the work list: fill them (human or
LLM) and the tier stack lights up. This is the verification-layer play without
overclaiming: we never pretend intent-level checks ran on a netlist that
carries no intent.

## X1 — the KiCad symbol ecosystem
`ohm import-symbol lib.kicad_sym Name` generates a component from the REAL
symbol: pad = KiCad pin number, role = mapped from KiCad's electrical type
(input/output/power_in/open_collector/... — no guessed pinouts, no TODO roles),
`+`/`-` pin names become `_P`/`_N`, `extends`-derived symbols resolve their
base, multi-unit symbols merge into the one physical package, footprint and
datasheet properties carry over. Author-time codegen (like `gen-package`):
never in the build path, hermeticity intact. Validated against the installed
KiCad libraries (Device:R, LM2904 via extends, AMS1117-3.3).

## Recorded limits (the next milestones if demand shows)
- Per-instance re-elaboration (true module generics): would replace the R022
  override guard with context-sensitive folding at lower time. The guard is the
  honest v1; this is the known ceiling and the single biggest remaining
  expressiveness gap.
- `elif` chains (write nested `if` in `else:` for now), fn kwargs, quantity
  literals as loop bounds.
- Zen import remains flat (no module hierarchy reconstruction).
