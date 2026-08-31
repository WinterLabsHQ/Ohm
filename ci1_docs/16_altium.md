# 16 · Altium

Loop 19 · 2026-08-31 · commit `3fd9b86`

---

## What it was

`ohm/pkggen/altium.py` and `ohm/pkggen/cfb.py` emit native `.PcbLib`/`.SchLib` as
MS-CFB containers. Five version-bearing strings live in the registry
(`ohm/formats.py`), all owned by `ohm.pkggen.altium` or `ohm.pkggen.cfb`:

| Constant | Value | Where it is written |
|---|---|---|
| `_PCB_HEADER` | `"PCB 6.0 Binary Library File"` | `.PcbLib` `FileHeader` stream |
| `_PCB_LIB_VERSION` | `"3.00"` | `VERSION=` in the `.PcbLib` library-parameters block (`altium.py:247`) |
| `_SCH_HEADER` | `"Protel for Windows - Schematic Library Editor Binary File Version 5.0"` | `.SchLib` header record (`altium.py:430`) |
| `_SCH_LIB_MINOR_VERSION` | `2` | `MinorVersion=` in the same header (`altium.py:431`) |
| `_CFB_VERSION` | `3` | the MS-CFB container's own major-version field (`cfb.py:227`) |

All five are **format-generation identifiers Altium itself writes, not Altium
Designer release numbers** — Altium Designer is at 26.9.1 (August 2026), and that
changes nothing here, since there is no newer value of any of these five to choose
between. The governing rule is stated once at `ohm/pkggen/altium.py:14-15`:

> "an unknown field set to zero is a guess, an unknown field set to what Altium
> writes is a fact."

Before this loop, `tests/test_altium.py` asserted two of the five directly:
`_PCB_HEADER` (via `test_pcblib_has_the_streams_altium_looks_for`) and the
container's major version indirectly, round-tripped through `olefile` in the
general container test — but not the other three, and not by name against the
registry.

## What changed

Nothing in `ohm/pkggen/altium.py` or `ohm/pkggen/cfb.py`. Five tests added to
`tests/test_altium.py`, and the work of writing them **found one more unasserted
stamp the earlier survey had missed**: `VERSION=3.00` in the `.PcbLib`
library-parameters block was a registered `formats.py` constant that no test had
ever checked — one field short of the existing `KIND=Protel_Advanced_PCB_Library`
assertion.

- `test_pcb_library_version_is_the_value_altium_itself_writes` — the `.PcbLib`
  `VERSION=3.00`.
- `test_schlib_header_carries_the_file_kind_string_altium_itself_writes` — the
  `.SchLib` header string.
- `test_schlib_minor_version_is_the_value_altium_itself_writes` —
  `MinorVersion=2`.
- `test_ms_cfb_container_version_field_is_3_read_by_olefile_not_by_ohm` — the
  MS-CFB major version.
- `test_altium_version_stamps_match_the_formats_registry` — cross-checks all five
  against `ohm/formats.py`'s `REGISTRY` in one place, so any of the five can drift
  from its emitted bytes and this test catches it.

**No mismatches were found.** Every emitted stamp matched both the registry and the
value a real Altium-authored file carries.

The MS-CFB major version is read back through **`olefile`'s `dll_version`** —
deliberately an implementation Ohm did not write, per `pyproject.toml:30-33`: "a
self-written reader would agree with a self-written writer about a broken file,"
which is the whole reason `olefile` is a dev dependency at all. `olefile` exposes
no spec-named accessor for the *minor* version field, so
`test_ms_cfb_container_version_field_is_3_read_by_olefile_not_by_ohm` reads that
one field straight from the header bytes at offset 24, per `[MS-CFB] 2.2`, with a
comment explaining why one field goes through `olefile` and the adjacent one does
not.

## What proves it

The five new tests, plus everything `tests/test_altium.py` already asserted:
container structure verified through `olefile` (an independent reader), record
framing (pad geometry block 110 bytes, track 41, arc 45, every primitive's common
13-byte header) checked against constants read out of a real Altium-authored
library, and pad geometry pinned equal to the `.kicad_mod` that
`tests/test_pkggen.py` validates against KiCad's own reference footprints.

## Still not proven

**No Altium Designer exists on any machine here.** ADR-020 is explicit that
nothing claims Altium opens, renders or DRC-passes these files, and this loop does
not change that — it only closes the gap between "the registry names five values"
and "all five are actually asserted against the emitted bytes." What *is* proven
is internal consistency: the container reads back through an independent
implementation, the record framing matches what a real Altium library carries, and
the geometry matches the KiCad footprint that has its own independent validation.
Altium's own acceptance of the file is a separate, still-unclosed claim.
