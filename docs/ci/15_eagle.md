# 15 · Eagle

Loop 18 · 2026-08-31 · commit `3fd9b86`

---

## What it was

`ohm/pkggen/eagle.py` emits `.lbr`, an Eagle XML library — pads, symbol, courtyard
on `tKeepout`, BOM attributes — sharing geometry with the KiCad emitter
(`symbol.symbol_layout`) so the two cannot drift apart. `_EAGLE_VERSION = "6.3"` is
written as `<!DOCTYPE eagle SYSTEM "eagle.dtd">` then `<eagle version="6.3">`.

`tests/test_eagle.py` already asserted well-formedness, pad mirroring against the
`.kicad_mod` (validated in turn against KiCad's own reference footprints), role
coverage over `VALID_ROLES`, the courtyard landing on `tKeepout`, and determinism —
but **never asserted the version string at all**.

## What changed

Nothing in `ohm/pkggen/eagle.py`. `6.3` is the schema revision that introduced XML
libraries — Eagle was a binary format before 6.0 — and **there is no older XML
library format to fall back to**, so it is simultaneously the conservative
forward-read choice and the only one available. The value stays; the reason it
stays is now recorded rather than only inferable from the surrounding code
comment.

**Autodesk EAGLE reached end of life on 7 June 2026.** Autodesk stopped selling and
supporting it and shut down the licence servers, so the application does not run
at all any more — not even for someone holding a subscription. The successor
reader is **Autodesk Fusion Electronics**, which still reads `.lbr`.

Two tests added to `tests/test_eagle.py`:

- `test_eagle_version_stamp_is_the_xml_library_schema_revision` — asserts the
  DOCTYPE and the `version="6.3"` attribute, and pins the literal `"6.3"` too, so a
  registry-only edit that stopped agreeing with the emitted XML would still be
  caught.
- `test_eagle_version_matches_the_formats_registry` — cross-checks the emitted
  value against `ohm/formats.py`'s `_EAGLE_VERSION` registry entry, so the two
  cannot drift apart independently.

## What proves it

The two new tests, run alongside the existing eleven in `tests/test_eagle.py`. No
behavioural change: every pre-existing assertion (pad mirroring, role coverage,
courtyard placement, determinism, XML escaping) is untouched.

## Still not proven

**No Eagle and no Fusion Electronics exists on any machine here.** Nothing in this
loop, or any before it, asserts the library renders, opens, or that Fusion
Electronics reads the file the way Eagle 6.3 would have. `tests/test_eagle.py`'s
own module docstring already said as much — "No Eagle install exists here...
Anything requiring Eagle itself is deliberately NOT claimed" — and that is
unchanged. This loop does not narrow that gap; it only names it more precisely:
Eagle is now unreachable in principle (no licence to have), not merely unavailable
in this repo.
