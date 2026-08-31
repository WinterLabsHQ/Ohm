# 18 · `pkggen` and IPC-7351

Loop 20 · 2026-08-31 · no code change — the finding is recorded in ADR-022 §6
(commit `a0eb5d1`); `ohm/pkggen/packages.py` itself last changed at commit
`49a2dca`, unrelated to this round

---

**The finding here is that nothing needed changing, and why that is worth a
file.**

## What it was

`ohm/pkggen/packages.py` implements IPC-7351 land-pattern math. The module
docstring (`:1`) and two fillet-table comments (`:45`, `:243`) name the revision
explicitly: **IPC-7351B**. ADR-006 agrees.

## What changed

Nothing in the code. What changed is that the question — "why B, and not the
presumably-newer C" — was asked and answered rather than left for someone to
wonder about later: **IPC-7351C was drafted, presented at conferences, and built
into CAD tools — and never published.** Revision B, released June 2010, remains
the current published standard as of 2026. A "C" bump would be a bump to a
document that does not exist. Ohm is already on the latest revision there is.

Two things worth recording alongside that, because both are easy to misread if
left implicit:

- **Ohm's density names are IPC's levels under a different label.** `least` /
  `nominal` / `most` map to IPC's **Level C / Level B / Level A** — Ohm names the
  fillet goal (how much material the joint gets), IPC names the level. Ohm never
  writes `A`/`B`/`C` anywhere in source or output, and this mapping is not
  documented in `packages.py` itself; it is stated here and in ADR-022 §6 rather
  than left implicit in the code.
- **The validation oracle is KiCad's own library, not IPC's worked examples.**
  `packages.py:82-83` records that QFN nominal "reproduces KiCad 7's official QFN
  land pattern exactly (validated vs QFN-16/32/48, P0.5mm)," and `packages.py:96-97`
  that QFP nominal "matches KiCad 7's LQFP-32/48/64 land patterns within tolerance
  (≤0.05 mm on pad center/length)." That is a *stronger* oracle than reproducing a
  table out of a standards PDF — agreement with land patterns thousands of boards
  have actually been fabricated from — but it is a different claim than "implements
  IPC-7351B," and it should be stated rather than left for a reader to discover by
  tracing the comments.

`docs/blueprint/OHM_ENGINEERING_BUILD_MANUAL.md:356` says "Follow IPC-7351B/C
land-pattern rules" — the blueprint's aspiration names both revisions, the
implementation delivers one, because only one exists to deliver. Noted here as a
discrepancy between aspiration and fact; the blueprint is read-only canon and was
**not** edited to match.

Also recorded in ADR-022 §2's table: **IPC-7351 is the one row that is not a
stamp Ohm writes.** It is a standard Ohm computes *from*, not a version string
written into a file, so it has no `ohm/formats.py` entry and — per
`tests/test_formats.py`'s rule that every registered value must appear in emitted
bytes — cannot get one.

## What proves it

Nothing new. `tests/test_pkggen.py` is unchanged this round (last touched at
`aa1ab6a`, before this loop began) and continues to validate QFN/BGA/PLCC/SOT223
exactly and QFP/DFN within 0.05 mm against KiCad 7 reference footprints — the
same oracle the comments above describe. This loop adds no test because it
changes no behaviour; the deliverable is the survey, not a diff.

## Still not proven

- **That IPC-7351C staying unpublished is a permanent fact rather than a 2026
  one.** If IPC ever does publish a "C," this file's central claim — that B is
  current because C does not exist — needs re-checking, and nothing here would
  notice that on its own; it would need to be re-asked the way ADR-022 re-asked
  it for this round.
- **The KiCad-agreement oracle covers QFN and QFP only.** `packages.py:82-83` and
  `:96-97` are specific to those two families; the chip, DFN/SON, BGA, LGA, PLCC
  and tab-package land patterns validated in `test_pkggen.py` use looser or
  different tolerances, and this record does not extend the "reproduces KiCad
  exactly" claim to them.
- **The blueprint's "B/C" line was flagged, not corrected**, per instruction — a
  reader of the blueprint alone would still expect a "C" mode that does not exist
  in the code.
