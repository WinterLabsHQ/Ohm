# ADR-011 — `ohm signoff`: routed-board verification against the source

Date: 2026-07-24 · Status: accepted

## Motivation (research)
PCBWorld (arXiv 2607.05915, 2026) benchmarked LLM agents on engine-grounded KiCad
routing. Open-loop agents failed catastrophically: invalid KiCad syntax in 34–51%
of outputs, 16.3 DRC violations vs 1.29 for closed-loop agents, 0.00 clean pass on
larger boards, unconnected nets, and "floating traces unrelated to net
connectivity". PCB-Bench (ICLR 2026) adds: errors in rule adherence, logical
consistency, component semantics.

Mapping those failures onto Ohm's product architecture (LLM writes Ohm → compiler
emits → Meridian routes → ?):

| PCBWorld failure | Owner | Status |
|---|---|---|
| invalid KiCad syntax | **DSL** — LLM never writes KiCad; compiler emits it | solved since P4 |
| component semantics / netlist logic | **DSL** — kinds, expects, Tier-I/S/Q | solved (K/F) |
| geometric routing + clearance | **Meridian** (or any router) + kicad-cli DRC | out of DSL scope |
| unconnected nets, floating copper, board≠netlist drift | **DSL** — *nobody verified the routed result against the source* | **this ADR** |

The last row is the missing leg of the loop: routers and board-local DRC check a
board against *itself* (its embedded net table). Nothing checked the routed board
against the *Ohm source*. `ohm signoff` closes the loop with machine-readable
diagnostics the LLM layer can iterate on deterministically — the same contract as
every other tier.

## The gate
`ohm signoff <src.ohm> <routed.kicad_pcb>` — compiles the source, parses the
routed board (own minimal S-expression reader, pure text→diagnostics, hermetic
N1: no I/O or subprocess in the compiler), and checks:

- **V001** board ≠ source: footprint missing/foreign (matched by the `ohm_path`
  property the emitter plants), pad bound to a different net than the source
  declares (diff-pair `_P/_N` renames applied).
- **V002** incomplete net: union-find point topology — segment endpoints chain,
  vias join layers at a point, an endpoint lands on a pad if within the pad's
  axis-aligned half-size + 0.05 mm on a shared layer. A net whose pads span >1
  island is incomplete.
- **V003** floating/foreign copper: net-0 segments, copper on nets the source
  never defined, trace islands touching no pad of their net (the PCBWorld
  failure verbatim).
- **V004** topology short: one quantized point (0.001 mm) carrying copper of two
  nets — segment×segment, via×any-layer, and wrong-net-copper-into-pad.
- **V005** (info) honest scope notices: zones and pad-less stub footprints are
  not traced; geometric rules (clearance, overlap shorts) are **delegated** to
  `kicad-cli pcb drc` — never claimed.
- **V006** `constraint fixed(...)` drift: fixed placement is a source-level
  promise the router must keep.

## Validation
Beyond the per-code positive/negative suite (unrouted → V002, synthesized route →
clean, multi-hop chains, corrupted boards for V001/V003/V004/V006, determinism):
the synthesized-route geometry is validated against **KiCad's own connectivity
engine** (`pcbnew.BuildConnectivity`): unrouted divider = exactly 1 unconnected
item, our synthesized route = 0. That pins our pad-position math (footprint
rotation included) to ground truth, not self-consistency.

## Deliberately out of scope (v1)
Zones, mid-segment T-junctions (KiCad DRC covers connectivity through both — V005
says so), geometric clearance (kicad-cli / Meridian DRC), arcs, and net-class
width verification (router consumes `.kicad_pro` net classes; geometric checking
belongs to the DRC layer). The delegation is stated in output, never silent.
