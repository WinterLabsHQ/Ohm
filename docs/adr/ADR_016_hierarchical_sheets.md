# ADR-016 — One schematic sheet per module

Date: 2026-07-29 · Status: **accepted, no longer the default** (see ADR-017)

> Splitting the page was the wrong first move. It made each page smaller without
> making any of it a circuit: a decoupling cap still sat across the sheet from the
> pin it decouples, joined by a label pair. ADR-017 fixes that on one sheet, and
> `ohm build` emits a single sheet again. Everything below still describes what
> `--sheets hier` does, and remains the escape hatch for a design too big for one
> page.

## Problem

The emitted `.kicad_sch` was electrically correct — `kicad-cli` extracted the
same 77 nets / 377 pins Ohm emitted, enforced by
`tests/test_schematic_roundtrip.py` — and unreadable. Everything landed on a
single **1251 × 927 mm (49 × 36 in)** page. No engineer opens that and reviews
it; they scroll it.

The measured fact that made the fix safe:

```
nets touching more than one top-level module: 46
nets entirely inside one module:              31
```

Every net is either wholly inside one module or a declared boundary crossing.
Nothing straddles a module in a way that a per-module page would orphan.

## Decision

`ohm build --emit kicad` writes **N+1 files**: `<stem>.kicad_sch` (the root) and
`<stem>_<module>.kicad_sch` per top-level module. The root sheet carries no
parts at all — one box per module, wired to its neighbours by name. That page
*is* the block diagram; every symbol lives on its module's sheet.

Three-way rule for a net on a module sheet:

1. **Rail** (ground, or a supply with fan-out ≥ `_PWR_MIN_PINS`) → power symbol.
   These connect across sheets on their own; no sheet pin, no bookkeeping.
2. **Local** (all nodes in this module) → plain label, sheet-local. Complete
   and correct on its own page.
3. **Crossing** (nodes in more than one module) → one `hierarchical_label` per
   sheet, matched by a `pin` of the same name on that sheet's symbol in the
   root. The net's other pins on the same sheet keep plain labels, which join it
   locally — so the sheet symbol shows one entry per signal, not per pin.

Page size is **A3** when the module fits (11 of fmuv2's 12 do). A module that
does not fit gets a `User` page rather than content clipped off the sheet:
overflowing an A3 would be a drawing that lies about what it contains.

### Crossing is decided by nodes, not by `net.ports`

A net whose pins all sit in one module is fully drawn on that page whatever the
source called it, so a sheet-local label is complete for it. `net.ports` is
non-empty for one further net on fmuv2 (`VDD_5V.v`) that never actually leaves
its module; hoisting it to the root would put a pin on the block diagram for a
signal that goes nowhere.

### Rail classification is computed over the whole design, never per sheet

This is the trap. If `GND.gnd` were classified from only the parts on one
sheet, it could come out a power symbol on one page and a plain — therefore
sheet-local — label on another, and the two would be different nets. The
drawing would look right on both pages. `_rail_nets()` is a module-level
function taking the whole `IRLow` for exactly this reason, and it is the same
call the hierarchy split and the sheet emitter both make.

## Verification

The round-trip oracle is extended to the hierarchy rather than weakened for it:
`kicad-cli sch export netlist` on the *root* file walks all sheets, and the
result must still equal Ohm's own netlist pin-for-pin. On fmuv2 that is **77
nets / 377 pins IDENTICAL across 13 sheets**. A second test asserts the
hierarchical labels and the root's sheet pins are the same set in both
directions — the netlist survives a mismatch there; ERC and a human reader do
not.

Symbol instance paths are `/<root uuid>/<sheet symbol uuid>`, both from the
existing deterministic `_uid()`. Getting that wrong breaks reference annotation
*silently*; it is covered because a wrong path yields wrong refs in the
extracted netlist, which the oracle compares.

## Consequences

- `emit_kicad_sch()` keeps its signature and its flat behaviour. A design with
  fewer than two modules stays one file — a hierarchy of one is indirection.
- `.kicad_pro` now lists `sheets`; KiCad will not open a hierarchy missing from
  the project file.
- Sheet-pin names are Ohm's canonical net names, port suffixes and all
  (`accel.spi.cs`). They are now the most-read surface of the drawing, so
  net naming is worth revisiting — deliberately not in scope here.

## Since decided — all three landed

This ADR deferred component orientation by kind, point-to-point wires for the
local nets, and ERC severities. All three shipped:

- orientation and local wiring in **ADR-017** (riders against the pin they
  serve, rotation for shunt parts, clusters and parallel ladders);
- ERC severities in the emitted `.kicad_pro`, since KiCad 7 has no headless ERC
  and a GUI run is only worth doing against the right severities.

Nothing in this ADR is open.
