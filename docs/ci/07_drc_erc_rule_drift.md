# 07 · DRC/ERC rule-id drift — closing F6

Loop 8 · 2026-08-31 · commit `5cd9415`

`history/01_audit_kicad_7_to_10.md` graded every KiCad-version claim in the repo and raised
eight findings. F1-F5, F7 and F8 were closed by code. **F6 was the one still
open**, recorded only in prose. It is closed here.

---

## What it was

KiCad renames DRC and ERC rule identifiers between releases, and Ohm keyed
behaviour off those identifiers in two places, neither of which knew a version.

**`ohm/drc.py`** — the V007 geometry gate loads the board with `pcbnew`, calls
`pcbnew.WriteDRCReport`, and grades each rule class. `_INFO_RULES` was a bare set:

```python
_INFO_RULES = {"unconnected_items", "lib_footprint_issues"}
```

No version annotation, no alternate spellings. A rule KiCad renamed drops out of
the set and its findings are then reported at the wrong severity.

**`ohm/emit_kicad_pro.py`** — the emitted `.kicad_pro` raises ERC severities,
including `"label_dangling": "error"`. **KiCad 10 renamed that rule to
`isolated_pin_label`**, so on KiCad 10 the key names nothing and the rule
silently reverts to KiCad's default severity. The guarantee held on 7, 8 and 9
and quietly did not on 10.

Two facts were recorded in `CHANGELOG.md` and `PROGRESS.md` and appeared nowhere
in code: that rename, and that KiCad 10 reports **39 more** `footprint_link_issues`
than KiCad 9 on the same board — a rule not in `_INFO_RULES` at all, so those 39
surfaced as findings on 10 and did not on 9.

---

## What changed

`_INFO_RULES` is now a **union across supported versions**, each entry annotated
with the KiCad it was observed under. Why it must be a union rather than a
snapshot is worth stating, because it is not obvious: **a DRC report carries no
version stamp.** `WriteDRCReport` writes a date and a count. At classification
time Ohm cannot know which KiCad wrote the text it was handed, so it cannot pick
a spelling — it has to accept every spelling any supported version writes.

`footprint_link_issues` is classified **info**, on two grounds. The recorded
evidence reads the 39-count delta as library-resolution and rule-naming
differences in an environment with no footprint tables configured, not findings
about the design — verbatim the rationale under which `lib_footprint_issues` was
already info. And structurally: V007 gates *geometry*, and a footprint that
cannot be linked back to its source library cannot be a geometry defect, because
the geometry is already inline in the board file. Leaving it unclassified meant
the same board passed on 9 and failed on 10 over a library table neither KiCad
was given.

**What was deliberately not concluded:** whether KiCad 10 *renamed*
`lib_footprint_issues` to `footprint_link_issues`, or whether they are distinct
rules that both fire here. The evidence is a violation count on one board, not a
mapping. The comment says so. It does not change the verdict — both are info, so
the union is right either way — but it would matter the day either is promoted to
a defect.

The `.kicad_pro` now writes **both** `label_dangling` and `isolated_pin_label`.
That this is safe was **not** verifiable here, and the code says so: no KiCad is
installed on this machine, and — checked directly — **no gate in this repo ever
puts a `.kicad_pro` in front of `kicad-cli`**. The roundtrip and plot gates write
a bare `.kicad_sch` or `.kicad_pcb`. So the four-version certification is not
evidence about `.kicad_pro` tolerance and is not cited as if it were. The
reasoning used instead: KiCad's settings loader reads the severities it knows out
of `erc.rule_severities` rather than enumerating the object's members, which is
the same forward tolerance Ohm already relies on to write KiCad 7 files that
KiCad 10 opens. The recovery path is recorded at the line — if a KiCad is ever
found to reject an unknown ERC key, the fix is per-target severities, not
dropping one.

---

## What proves it

`tests/test_kicad_rule_ids.py`, three tests. The rename table is written out
**independently of the source**, because a table derived from `_INFO_RULES` would
agree with any edit including the wrong one:

```python
DRC_ALTERNATE_SPELLINGS = (("lib_footprint_issues", "footprint_link_issues", "10"),)
ERC_ALTERNATE_SPELLINGS = (("label_dangling", "isolated_pin_label", "10"),)
```

- **The behavioural one** takes the real KiCad 7 report already used by
  `tests/test_drc_gate.py`, respells the rule id to the KiCad 10 spelling, and
  asserts `parse_drc_report` produces an identical rule-to-severity map modulo
  the rename. It also guards that the older id is present in the sample, so it
  cannot pass vacuously.
- A second pins the verdict itself (`info`), so the first cannot pass by grading
  both spellings wrongly in the same way. `courtyards_overlap == "error"` is the
  control.
- A third asserts the emitted `.kicad_pro` carries both ERC keys with the same
  severity — which the behavioural test structurally cannot reach, since nothing
  in this repo can run ERC at all (KiCad 7 has no headless ERC).

**Verified by mutation:** deleting the `footprint_link_issues` line *and* the
`isolated_pin_label` line fails all three; restoring passes all three. Dropping
`lib_footprint_issues` instead fails the same way — the comparison is symmetric.

The `.kicad_pro` gains one line, +34 bytes. Nothing pins those bytes: the golden
directory holds only `.net`, `.ohm` and `.ir.json`, and
`test_double_build_byte_identical` compares two fresh runs against each other
rather than against a snapshot.

---

## Still not proven

- ~~**V007 runs at one version.** `pcbnew` is a compiled extension built against
  its distribution's Python, and only `ubuntu-24.04` with the KiCad 10 PPA gives
  a system Python (3.12) that matches Ohm's. The geometry gate is proven at
  10.0.5 and assumed at 7, 8 and 9.~~ **CLOSED, loop 24.** The constraint was the
  in-process import, not the ABI: `ohm/drc.py` now runs the engine out of process
  (`kicad-cli pcb drc` from 8, a `pcbnew` child at 7), so the gate runs at all
  four and is `proven` at all four
  ([24_v007_headless.md](24_v007_headless.md), [21](21_compatibility_matrix.md)).
- **A route difference is not measured.** `kicad-cli pcb drc` and
  `pcbnew.WriteDRCReport` are assumed to agree on every rule class; they were
  compared on the classes these boards produce, not on all of them.
- **No ERC is ever run.** `kicad-cli sch erc` exists from KiCad 8 and Ohm does
  not drive it — recorded at `ohm/emit_kicad_pro.py:84-91` as an open item, and
  deliberately out of scope this round.
- The `lib_footprint_issues` / `footprint_link_issues` relationship, as above.
