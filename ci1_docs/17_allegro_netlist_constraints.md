# 17 · Allegro, the netlist, and the seam

Loop 20 · 2026-08-31 · commit `3fd9b86` (Allegro); netlist and constraints
registry entries established at loop 2, commit `d299c9a`

---

Three stamps with no reader here, and that is the honest through-line of this
record.

## Allegro

`ohm/emit_allegro.py` emits the documented third-party logic import triplet
(`pstchip.dat`, `pstxprt.dat`, `pstxnet.dat`) and **no library geometry** —
ADR-020 says why, and does not claim Allegro imports it. The one format-version
token is `PST_VERSION='PST_HDL_CENTRIC_VERSION_0'`, taken verbatim from a real
OrCAD-written triplet (`ohm/emit_allegro.py:1-21`: "syntax taken from a real
OrCAD-written triplet, not from prose"). Cadence Allegro X is at 25.1; this
directive is not a release number and stays unchanged.

Three tests added to `tests/test_allegro.py`:

- `test_pst_version_is_the_string_a_real_orcad_triplet_carries`.
- `test_file_type_header_lines_are_exact_including_the_one_that_looks_like_a_typo`
  — pins all three `FILE_TYPE` header lines byte-for-byte, not merely their
  prefix (the existing `.startswith` checks). Worth recording the asymmetry it
  catches: `pstxnet.dat` writes `FILE_TYPE = EXPANDEDNETLIST;` **with spaces
  around the `=`**, while `pstchip.dat` and `pstxprt.dat` do not. Copied verbatim
  from the real triplet, so it looks like a typo and is not — normalizing it to
  match the other two "would produce a file that no longer matches what real
  Allegro-adjacent tooling writes" (test docstring).
- `test_allegro_version_stamp_matches_the_formats_registry` — cross-checks
  `_PST_VERSION` against `ohm/formats.py`.

## The netlist

`ohm/emit_netlist.py` writes `(export (version "E"))`. This was an inline literal
until loop 2 (`d299c9a`, see `02_version_registry.md`); it is now the named
`_NETLIST_VERSION` in the registry, unchanged in value — `"E"` is the version
letter KiCad's own netlist exporter currently writes, and Ohm's netlist is a
sibling format a BOM/CAM tool reads the same way, so it carries the same token
rather than inventing a competing one.

Ohm's own format, so "has a reader been run against it" is not quite the right
question the way it is for the other rows — but KiCad *does* re-read the emitted
schematic (`tests/test_schematic_roundtrip.py`) and the resulting netlists are
compared, which is, in practice, the strongest check any stamp in this survey
gets.

## The constraints seam

`shared/constraints_schema.py:12` — `SCHEMA_VERSION = "1"`. Pure data, **69
lines**, consumed by Meridian and the convergence solver, which are separate
products outside this repo. It has **no reader here, no migration code, and has
never been version 2**. That is the deliberate state of it, recorded rather than
left to look like an oversight.

The import direction that keeps the seam a seam: `shared/` must not depend on
`ohm/` (`shared/constraints_schema.py:1-5`), so `SCHEMA_VERSION` stays *defined*
in `shared/` and `ohm/formats.py` *imports* it (`ohm/formats.py:37,159`) —
recorded in the registry with `owner="shared.constraints_schema"` (not an
`ohm.*` module) so the direction is visible in the table itself rather than
something a reader has to go check.

## What proves it

Allegro: the three new tests plus the existing pin-table, footprint-binding and
part-list checks in `tests/test_allegro.py`, all of it explicitly scoped ("What is
NOT claimed: that Allegro opens them") to agreement with the OrCAD-derived
grammar, never to Allegro's own behaviour.

Netlist: `test_double_build_byte_identical` and the schematic round-trip gate,
unmodified by this loop.

Constraints: `tests/test_formats.py`'s registry checks, plus the existing
hermeticity walk that would fail if `shared/` ever imported from `ohm/`.

## Still not proven

- **Allegro, and always will be here, absent the tool.** No Cadence software
  exists on any machine in this repo's reach; nothing asserts the triplet imports
  cleanly into Allegro, only that its syntax matches a real OrCAD-written one.
- **The constraints schema has no migration path exercised, because it has never
  needed one.** If Meridian or the solver ever need a version 2, nothing here has
  been built or tested to carry a design across that boundary — the seam has only
  ever spoken one dialect.
