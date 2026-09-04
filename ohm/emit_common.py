"""What every emitter shares: the source identity, and how a value reads.

Ohm has nine emitters. Three things are common to all of them — the identity an
artifact is named and seeded from, the canonical text of an attribute value, and
which attributes a BOM tool expects to find — and all three lived inside one
emitter each, so the other eight reached across for them:

    ohm/emit_netlist.py  ->  emit_kicad._source_id
    ohm/emit_ohm.py      ->  emit_kicad._source_id
    ohm/emit_zen.py      ->  emit_kicad._source_id
    ohm/emit_allegro.py  ->  emit_kicad._source_id
    ohm/emit_kicad_pro.py->  emit_kicad._source_id
    ohm/emit_kicad.py    ->  emit_netlist._value_text, emit_netlist.BOM_FIELDS

The last line is the one that mattered: `emit_kicad` and `emit_netlist` each
imported a private name from the other, a cycle broken only by making one of the
two imports function-local. A cycle held apart by import placement is a cycle
that comes back the first time someone tidies an import to the top of a file.

So the shared vocabulary lives here, below every emitter and depending on none
of them. `emit_kicad` stops being the place other emitters go for things that
were never about KiCad — which is what `_source_id` had become, having been
written for schematic uuids and then needed by every artifact that carries a
file name.

Hermetic (N1): stdlib and `ohm.ir` only. No I/O, no subprocess, no clock.
"""

from __future__ import annotations

from ohm import ir

# Ohm attr -> the field name a BOM tool expects to find it under. Ordered, so
# the emitted fields are deterministic.
BOM_FIELDS = {"mpn": "MPN", "manufacturer": "Manufacturer", "datasheet": "Datasheet"}


def _source_id(source: str) -> str:
    """The stable identity of a source file, for uuid derivation.

    Every uuid and tstamp in the emitted schematic and board is
    `uuid5(_NS, source + role + ...)`, and `source` used to be the path string
    the CALLER typed. So the same design emitted different bytes depending on
    how it was named:

        ohm build tests/golden/divider.ohm       -> uuid 5d81bef0-7da1-57ac-...
        ohm build D:/repo/tests/golden/divider.ohm -> uuid 7998f527-d7fb-5a58-...

    Two consequences, and the second is the serious one. It broke the promise
    that "the same source is the same bytes on any machine, any day" — two
    developers whose checkouts sit at different paths got different files. And
    KiCad identifies a sheet BY its uuid, so rebuilding from another directory
    reads to KiCad as a different sheet, which is how per-sheet state gets
    silently orphaned. Found by the cross-platform determinism gate
    (`tools/emit_manifest.py`), which is what that gate is for.

    The file name alone is the stable part: it survives a move, a checkout at
    another path, and a Windows caller typing backslashes. Two designs sharing
    a basename cannot collide, because a uuid only has to be unique WITHIN the
    file it appears in and each build writes its own.

    Idempotent, which is why it is safe for both a caller and the emitter it
    calls to apply it.
    """
    # Accept either separator: a Windows caller types backslashes and a POSIX
    # one forward slashes, and the same file must hash the same either way.
    return source.replace("\\", "/").rsplit("/", 1)[-1]


def _value_text(v: ir.Value) -> str:
    """Canonical text for an attr value; Quantities render as authored (10kohm +/- 1%)."""
    if isinstance(v, ir.Quantity):
        s = f"{v.value}{v.unit}"
        if v.tol_raw is not None:
            tval, tunit = v.tol_raw
            s += f" +/- {tval}{tunit or '%'}"
        return s
    return str(v)
