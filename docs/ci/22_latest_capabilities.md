# 22 · What Ohm can do now that it could not before

2026-08-31 · branch `ci1-eda-upgrade`

A summary of the round in terms a user would notice, rather than in terms of the
files that changed. Each entry names the record with the detail.

---

## Ohm's boards are orderable

Boards now carry a real closed **`Edge.Cuts` profile**, derived from the
placement extents. Before, the layer was declared and nothing was drawn on it, so
the gerber package KiCad produced had no outline and no fabricator could build
it. New advisory **M012** when there are no extents to derive from.
→ [`11_edge_cuts.md`](11_edge_cuts.md)

## KiCad 7 is supported *and* measured, for the first time

`kicad/kicad:7.0.11` certifies clean — 337 tests at this loop, 0 failed,
**0 skipped** — on
the same terms as 8, 9 and 10, with no gate excluded. Previously KiCad 7 was
claimed and checked by nothing, on the strength of a false premise. It also now
has footprint pad data, so `--target-kicad 7` translates pad ids like every other
target instead of abstaining.
→ [`10_container_tag_currency.md`](10_container_tag_currency.md),
[`21_compatibility_matrix.md`](21_compatibility_matrix.md)

## `ohm signoff` takes `--target-kicad`

The last check before fabrication now reads pad ids in the target KiCad's
vocabulary. Before, a board laid out on KiCad 10 carried `SH` pads while
signoff's source model said `11`, and because signoff skips pads absent from the
board, the mismatch was **not reported at all** — an unconnected shield passed the
final gate in silence.
→ [`08_signoff.md`](08_signoff.md)

## The build is provably identical across platforms

175 artifacts across all eight emit formats, byte-identical between Windows and
Linux, and now a CI job on ubuntu, windows and macos that runs on every pull
request. The invariant was stated on page 8 of the architecture document and
measured by nothing.
→ [`20_cross_platform_determinism.md`](20_cross_platform_determinism.md)

## The same source is the same bytes, wherever your checkout lives

Schematic and board uuids were derived from the source path **as typed**, so two
developers with checkouts at different paths got different files — and since
KiCad identifies a sheet by its uuid, a rebuild from another directory read as a
*different sheet*. Now derived from the file name alone.
→ [`20_cross_platform_determinism.md`](20_cross_platform_determinism.md)

## `ohm check` tells you *why* a footprint is missing

S012 said "which KiCad N no longer ships" for every absence. Two opposite causes
were being conflated: **removed** (the part is gone, name its replacement) and
**added later** (the part is fine, this KiCad is older than it). 728 footprints
are in the second class against KiCad 7, 379 in the first against KiCad 10 — and
the help text was sending readers to hunt for removals that never happened.
→ [`05_lower_and_fpdata.md`](05_lower_and_fpdata.md)

## The standard library works on every supported KiCad

Both screw-terminal parts named a footprint KiCad added *after* 7, so on the
default target they bound to something the user's library did not have and the
pins arrived unconnected. Fixed — and the rule now has a gate over the whole
standard library, rather than being fixed one part at a time for the third time.
→ [`05_lower_and_fpdata.md`](05_lower_and_fpdata.md)

## ERC severities hold on KiCad 10

The emitted `.kicad_pro` wrote `label_dangling`, which KiCad 10 renamed to
`isolated_pin_label`. The key named nothing and the rule reverted to KiCad's
default severity. Both spellings are now written, and the DRC rule table is a
version-annotated union rather than a snapshot — closing audit finding **F6**,
the last one open.
→ [`07_drc_erc_rule_drift.md`](07_drc_erc_rule_drift.md)

## The `.step` body is AP242

Upgraded from AP214, which ISO superseded — and the bump is the *only* kind this
round permits, one with a real reader: gmsh/OpenCASCADE meshes the file and
checks the volume to `rel=1e-9`. Getting there required repairing that oracle,
which had been silently skipping.
→ [`13_step_ap242.md`](13_step_ap242.md)

## The `.zen` export loads in the real Zener toolchain

Upgraded to the current `pcb` 0.4 surface, and — for the first time — **verified
by running `pcbc` against Ohm's output** rather than against the published spec.
ADR-002's deferral, open since the interop subset was written, is closed.
→ [`14_zener_zen.md`](14_zener_zen.md)

## Every emitted format has a written version policy

ADR-022 extends ADR-021 from KiCad's four constants to all eight formats, and
`ohm/formats.py` is the registry that executes it. Five stamps were bare inline
literals with no test and no recorded reason; all are named, asserted, and
carrying the reason for their value.
→ [`02_version_registry.md`](02_version_registry.md),
[`12_multi_eda_version_policy.md`](12_multi_eda_version_policy.md)

## The CI pins cannot rot silently

`tools/check_kicad_tags.py` resolves the newest available image per major and
fails when a pin has drifted. It found, on its first run, that KiCad 10.0.6 has
no container image — so 10.0.5 is not a stale pin but the ceiling of what is
provable — and that KiCad 7 had images all along.
→ [`10_container_tag_currency.md`](10_container_tag_currency.md)

---

## The geometry gate works — for the first time on a modern KiCad

Running the `v007` job, which nobody ever had, found two defects at once.
`pcbnew.WriteDRCReport` **segfaults on KiCad 10**, and because the gate imported
it in-process, `ohm signoff` died with it — exit 139, no verdict, not even the
source-fidelity checks already computed. And since **KiCad 8** the report parser
could not read a severity at all, so every finding fell back to `warning`: a
courtyard overlap or a clearance violation could not fail the gate on any modern
KiCad.

The gate now delegates out-of-process — `kicad-cli pcb drc` where it exists,
`pcbnew` in a child otherwise — so it runs at **all four** supported versions and
a crash in KiCad's own engine degrades to a warning instead of destroying the
run.
→ [`24_v007_headless.md`](24_v007_headless.md)

## A board Ohm cannot read no longer reports that the board is wrong

New **V008**. The readers were structural but not defensive: six shapes raised
straight out of the last gate before fabrication, and four more returned a
confident wrong verdict — including telling a user their symbol was
"graphic-only" when the truth was that Ohm had failed to read the file.
→ [`09_readback_hardening.md`](09_readback_hardening.md)
