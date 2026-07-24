# ADR-009 — First-spin success: `expects`, bring-up checks, DFM lints, placement intent

Date: 2026-07-24 · Status: accepted

## Motivation (research, cited in the F-milestone reports)
Industry surveys: ~2.9 respins per project at ~$28k each; schematic-phase errors cause
40–60% of failures; ~35–40% of respin causes are addressable pre-layout. The dominant
class is "passes every DRC, dies on the bench": missing pull-ups, floating straps,
missing decoupling, unterminated buses, domain mismatches. Generic ERC cannot see these
(Cadence's own docs concede ERC "lacks notion about protocol interfaces"); Flux.ai checks
them only LLM-heuristically. Ohm's typed interfaces/kinds/roles/operating-points make
them *deterministically* checkable — an empty lane.

## The `expects` mechanism
One construct instead of twenty special cases: kinds and interfaces declare requirement
predicates over their members' nets —

```
interface I2C:
    ...
    expects pullup on sda, scl

kind MOSFET:
    ...
    expects tie on gate

interface RS485:
    ...
    expects termination(100ohm, 130ohm) on a, b when endpoint
```

- **Closed v1 pattern set** (each a checker predicate, O(net)): `pullup`, `decoupling`,
  `loadcaps`, `cap`, `termination`, `tie`. User-defined predicates are a future ADR.
- **`when <attr>`** gates on a truthy instance attr — the false-positive killer from the
  research (a mid-bus RS485 node must NOT terminate; only `endpoint=1` nodes are checked).
- **Waivers**: `q = NMOS(waive="tie")` suppresses a finding into an info note (**B010**)
  — visible and auditable in the design source, never silent. This is the honesty
  contract: every check must have a declared escape.
- Pin attrs `must_connect=true` and `tolerant_to=<V>` extend the same discipline.

## Checks (B-codes)
B001 pull-ups (expects + automatic open-drain-net detection, port-exempt) · B002 tie/strap
(error — BOOT0/EN/gate class; netless bound members count as floating in wired modules) ·
B003 unconnected must_connect/power_in pins (power_in only in modules that wire something —
bare part-catalog instantiations stay quiet) · B004 tolerant_to domain damage (error; the
message states a series resistor does not fix it) · B005 decoupling · B006 cap +
regulator headroom vs declared `v_dropout` · B007 termination · B008 loadcaps · B009
`Polarized` reverse-bias vs declared operating voltages (certain-reverse only) · B010
waiver note. Severity rule: damage-class = error, presence-class = warning.

## DFM/DFT lints (M-codes) and honesty
`check_mfg.py`: missing MPN, BOM grouping conflicts, valueless passives, power rails
without test access, fine-pitch/mixed-tech/fiducial process notes — warning/info only,
summary-style (one diagnostic per finding class), skipped entirely for unwired designs.
STYLE.md documents the **not-checkable-without-layout** list (copper geometry, mask,
spacing, SI/PI/EMC ≈30% of respins) — N3: we claim exactly what we check.

## Placement intent (not routing)
Constraint kinds `fixed(inst, x, y[, rot])`, `near(a, b, maxdist)`, `edge(inst)`; module
hierarchy is the implicit grouping (no syntax). The emitters ship the DSN-shaped triple
routers conventionally consume — placement seeds (anchors locked, everything else
courtyard-aware-clustered by module), KiCad `(group)` objects with an `ohm_path` join-key
property, net classes in `.kicad_pro` (KiCad 7+ ignores board-file classes), `_P/_N`
diff-pair naming (KiCad-artifact-only rename; netlist and constraints JSON stay
canonical). Scope guards: no stitching vias, no coordinates on non-anchors, no drawn
topologies — declared endpoints/classes only. Routing is Meridian's job.

## Boundaries
Hermetic (no lib reads at emit; string-footprint parts emit stubs hydrated by KiCad's
"Update Footprints from Library"), deterministic (Decimal grid, uuid5 namespace), and
kinds/expectations never change the netlist.
