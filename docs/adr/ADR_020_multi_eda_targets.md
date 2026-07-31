# ADR-020 — Multi-EDA native targets (Altium, Allegro)

Date: 2026-07-31 · Status: accepted

## Motivation
Ohm's emitters were KiCad-first with Eagle `.lbr` as the one alternate target,
because Eagle is the only non-KiCad library format that is published, textual and
therefore emittable without reverse engineering. That made "multi-EDA" an
aspiration rather than a property: a team on Altium or Allegro could compile an Ohm
design and still had nothing they could open.

The gap was not architectural — the emitters already hang off a shared
`PackageModel` and `symbol_layout` — it was that the remaining formats are binary
(Altium) or unpublished (Allegro). This ADR records how far each one can honestly
be taken, and what is deliberately left unclaimed.

## Decision

### Altium: native `.PcbLib` and `.SchLib`
Emit the real binary library formats, not an interchange the user must import.

`ohm/pkggen/cfb.py` writes the MS-CFB (OLE2) compound-file container: version 3,
512-byte sectors, a real mini stream (Altium libraries are dozens of sub-4 KB
streams, and a reader locates those by size alone), no DIFAT sectors. Output is
byte-identical run to run — all timestamps and CLSIDs are zero and sibling order is
the spec's own collation.

`ohm/pkggen/altium.py` writes the contents. The PCB side is packed binary records
(pad, track, arc) each opening with the same 13-byte common header; the schematic
side is pipe-delimited parameter strings with the pins — and only the pins — in
binary. Every field offset, block length and magic constant was read out of
Altium-authored libraries and cross-checked against the published format
descriptions. Fields whose meaning is still unknown are copied from those files
verbatim as `_OBSERVED_*`, not zeroed: **an unknown field set to zero is a guess; an
unknown field set to what Altium writes is a fact.**

Geometry is not reimplemented. Pads come from `PackageModel`, silkscreen from
`footprint.silk_segments`, symbol layout from `symbol.symbol_layout` — the same
functions the KiCad emitter calls. The Altium output therefore cannot drift from
the `.kicad_mod` that `tests/test_pkggen.py` validates against KiCad's own
reference footprints.

Altium libraries are multi-part files, so unlike Eagle this emits one `.PcbLib`
and one `.SchLib` per design rather than a file per component.

Two deliberate simplifications, neither electrical:
* roundrect pads become Altium **rectangular** pads. Corner rounding lives in a
  596-byte per-layer array Altium only writes for shape 9; the land pattern is
  identical, and Altium's own IPC-generated libraries use plain rectangles.
* two-terminal **glyphs** (the resistor zig-zag and friends) are not carried over;
  every part is the labelled box. The glyph bodies sit on a finer grid than
  Altium's integer schematic unit, and a box with the right pins is correct where a
  mis-gridded glyph would be pretty and wrong.

### Allegro: the third-party logic import, and nothing more
Allegro's library formats (`.dra`, `.psm`, `.pad`) are undocumented binary that only
Cadence's tools write. Ohm does not pretend otherwise and emits no `.psm`.

What Allegro *does* document and accept from any third-party front end is the logic
import: `pstchip.dat` (pin tables and the footprint each part binds to),
`pstxprt.dat` (the part list) and `pstxnet.dat` (every net and its nodes).
`ohm/emit_allegro.py` writes all three, with syntax, quoting and canonical-path
escaping taken from a real OrCAD-written triplet. Ohm net names routinely contain
`.` (`r1.P1`), which Allegro reads as a hierarchy descent unless the segment is
backslash-wrapped — so it is.

This is genuine parity on **connectivity and part binding** and no parity at all on
**library geometry**. Closing the second half needs either Cadence tooling in the
loop or an IPC-2581 path, and neither is claimed here.

## Validation
The bar is the one the KiCad emitter set, and it is stated per layer rather than
asserted globally:

1. **Container** — read back with `olefile`, an independent MS-CFB implementation.
   A self-written reader would agree with a self-written writer about a broken
   file, so `olefile` is a test-only dependency for exactly this reason.
2. **Record framing** — block lengths (pad 110, track 41, arc 45) and the common
   header are asserted against the values Altium itself writes.
3. **Geometry** — pad positions and sizes are asserted equal to the `.kicad_mod`,
   which is validated against KiCad's reference footprints. Chain:
   KiCad reference ← `.kicad_mod` ← `PackageModel` → `.PcbLib`.
4. **Connectivity** — the Allegro triplet is asserted node-for-node against Ohm's
   own netlist, so Allegro receives the netlist the compiler proved rather than a
   re-derivation of it.
5. **Role coverage** — every role in `resolve.VALID_ROLES` must appear in the
   Altium electrical-type map and the Allegro PINUSE map, the same guard the KiCad
   and Eagle maps carry. A role falling through to a passive default silently
   disables the ERC a reviewer is relying on.

During development the emitted files were additionally parsed by `pyAltiumLib`, a
third-party Altium reader, which recovered the library header, part names,
descriptions, all pads (designator, position, size, shape, layer, no hole) and all
pins (name, number, electrical type, side, length). That is evidence, not a gate:
it is recorded here and not wired into CI, because a second parser is still not
Altium.

**Not claimed, anywhere:** that Altium Designer opens, renders or DRC-passes these
files, or that Allegro imports the triplet. No Altium and no Cadence tool exists on
any machine Ohm is developed or tested on. The first user with a licence closes
that gap; until then the claim stops where the evidence does.

## Consequences
* `ohm build --emit altium` writes `<stem>.PcbLib` + `<stem>.SchLib`.
* `ohm build --emit allegro` writes the three `.dat` files.
* `olefile` joins the dev extras. It is test-only and never imported at runtime —
  Ohm writes the container itself.
* Four emitters now share one role map contract. Adding a fifth means adding a
  fifth map, and the test suite will fail until every role is in it.
