"""Read pads out of a real KiCad .kicad_mod, whatever KiCad wrote it.

The golden-reference tests compare Ohm's generated land patterns against
KiCad's own library footprints. Those comparisons are about GEOMETRY, but they
were written against KiCad 7's serialisation and broke wholesale on KiCad 9 —
which changed the file format without moving a single pad:

    KiCad 7   (pad 1 smd roundrect (at -2.475 -1.905) (size 1.95 0.6)
    KiCad 9   (pad "1" smd roundrect
                  (at -2.475 -1.905)
                  (size 1.95 0.6)

Two differences, both cosmetic: the pad id is quoted, and the s-expression is
pretty-printed across lines. A regex anchored to the old layout matches
nothing, so `golden` came back empty and eight tests failed on `len(golden)`
or a KeyError rather than on a tolerance.

This parses either. It is deliberately in `tests/` and not in `ohm/`: the
compiler only ever WRITES footprints, so a footprint reader is test scaffolding,
not library surface.
"""
from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

# `(pad` then an id that may or may not be quoted, then the pad type and shape.
_PAD_HEAD = re.compile(r'\(pad\s+"?([^"\s)]+)"?\s+(\w+)\s+(\w+)')
_AT = re.compile(r"\(at\s+(-?[\d.]+)\s+(-?[\d.]+)(?:\s+(-?[\d.]+))?\s*\)")
_SIZE = re.compile(r"\(size\s+([\d.]+)\s+([\d.]+)\s*\)")


def pads_from_kicad_mod(path: str | Path, *, as_float: bool = False) -> dict[str, tuple]:
    """{pad id: (x, y, width, height)} for every pad in the footprint.

    `as_float` for the callers that compare against floats; Decimal otherwise.
    Mixing the two raises, so the choice is explicit rather than incidental.

    Reads the `(at ...)` and `(size ...)` nearest each `(pad ...)` header,
    which is unambiguous because both appear exactly once per pad and before
    the next pad begins.
    """
    text = Path(path).read_text()
    out: dict[str, tuple[Decimal, Decimal, Decimal, Decimal]] = {}

    heads = list(_PAD_HEAD.finditer(text))
    for i, head in enumerate(heads):
        chunk = text[head.end(): heads[i + 1].start() if i + 1 < len(heads) else len(text)]
        at, size = _AT.search(chunk), _SIZE.search(chunk)
        if at is None or size is None:
            continue  # a pad with no geometry is not a comparison candidate
        num = float if as_float else Decimal
        out[head.group(1)] = (num(at.group(1)), num(at.group(2)),
                              num(size.group(1)), num(size.group(2)))
    return out


def pad_ids_from_kicad_mod(path: str | Path) -> set[str]:
    """Just the pad ids — for "does every declared pad exist" checks."""
    return {m.group(1) for m in _PAD_HEAD.finditer(Path(path).read_text())}
