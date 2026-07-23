# ADR-008 — `kind`: a checked component-type taxonomy

Date: 2026-07-24 · Status: accepted

## The idea
Extend Ohm's "type system for intent" from nets/interfaces to **component kinds**. A
`kind` is a first-class, checkable contract for a category of part — the pins (by role)
and params every real member has. A component declares the kinds it *is*
(`component TLV9061(OpAmp):`) and binds each required member to a pin
(`pin OUT: role=output, pad=1, is=out`); the compiler verifies conformance. This applies
D3 one level up: the *kind* is the DECLARED-MEANT, the *pins* are the BUILT.

## Prior art (research)
- **Zener** ships an unchecked `type=` string — no conformance check (confirmed).
- **JITX** (`supports`/`require`) and **IP-XACT** (abstractionDefinition + SCR conformance)
  do check role-contracts, but only for *buses/signals*, and heavyweight.
- **tscircuit** checks params-per-kind (via TS/zod) but not pin-shape.
- Nobody does "named discrete-part archetype = pins-contract **+** params-contract, checked
  cheaply per member." That empty lane is what this occupies — the clean differentiator vs Zener.

## Decisions
1. **Nominal claim, structural check.** The author *declares* the kind (catchable intent);
   the checker *verifies* the pins/params satisfy it. No structural auto-inference (avoids
   Go's accidental-conformance trap).
2. **Multiple kinds per component; contract = their union** (Rust `T: A+B` / Swift protocol
   composition). Flat set; no composite type.
3. **Explicit per-pin binding** `is=<member>` (user's call). Maximum declared intent.
4. **Conflict → error, never linearize** (Rust's rule, not Scala/Python MRO). Two declared
   kinds requiring the same member name with different role is K001, *unless* the roles match
   — then one pin satisfies both (the good diamond). Roles/dimensions are part of member
   identity.
5. **Shallow single-parent IS-A refinement** (`kind Schottky(Diode)`) + **flat orthogonal
   capability kinds** (`ThermalPad`, `ExposedPad`). No deep/multi-parent inheritance
   (re-imports the diamond).
6. **Open-world.** A component with no declared kind is the base `Component` — unchecked.
   Absence of a kind = "unasserted," never "false." `requires` is adversarially minimal;
   non-universal members are `optional` (single-supply op-amp V−, fixed-LDO EN, shunt-ref
   supply, 4-terminal MOSFET body) so a legitimate part never false-fails.
7. **Kinds are a separate namespace** from interface/component/module, so
   `component Resistor(Resistor)` (a part named for its category) is legal.
8. **Author-time only.** Kinds never change the emitted netlist/KiCad/constraints — they are
   a correctness surface, verified by test (`build` of a kinded design == its un-kinded twin).

## Mechanics
- Grammar: `kind NAME (parents)? [":" body]` (bodyless = pure refinement) with
  `requires/optional NAME: role` and `param NAME: dimension`; `component NAME (kinds)? ":"`.
  Pin binding `is=<member>` is just an attr (no pin-grammar change).
- New pin roles for contract fidelity: `open_collector`/`open_drain` (comparator/I2C outputs
  — wired-OR, not an I003 conflict) and `free` (base default, connects to anything).
- New checker `check_kinds.py` (pure, O(pins), after `check_structural`): K001 conflict,
  K002 unsatisfied requirement, K003 role mismatch, K004 param missing/wrong-dimension, K005
  stray binding. Resolver adds R017 (unknown kind), R018 (refinement cycle), R019 (unknown
  param dimension).

## Non-goals
No solver/capability-matching (JITX territory); no sound row/intersection metatheory (it's
the mental model, the impl is set-containment); no deep inheritance.

## Rollout
K0 foundation + `std/kinds.ohm` taxonomy (~30 kinds, research-minimal contracts) + exemplar
retrofits (Resistor/Capacitor/Inductor/OpAmp_SOT23_5). K1 retrofits the rest of the library.
K2 adversarially audits each contract (any `requires` a real part lacks → demote to
`optional`) + a live-LLM custom-part-via-kind test.
