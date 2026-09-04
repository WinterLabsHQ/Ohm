# 05 · Pad translation, S012 and the shipped table

Loop 5 (reshaped by the KiCad 7 correction) · 2026-08-31 · commit `5cd9415`

---

## What it was

A `package = "Lib:Footprint"` reference emits a **pad-less stub**: KiCad supplies
the geometry from the library on that machine, and only the pad id travels, in
the netlist. So the netlist is correct only if the pad id Ohm names still exists
in the library on that machine — a library Ohm does not ship, does not read and
could not check.

ADR-021 closed that with `ohm/fpdata/pads.json.gz`, a frozen table of the pad ids
KiCad's own footprints carry, plus S012 (`ohm check` reports a declared pad the
target's footprint lacks) and `ohm.lower._pad_translator` (writes the *target's*
spelling into the netlist, so `pad=11` emits `11` on 8/9 and `SH` on 10 from one
source).

The table covered **KiCad 8, 9 and 10**. It did not cover **KiCad 7**, which was
a supported target — so `_pad_translator` abstained on 7 (`ohm/lower.py:71-75`)
and S012 fell back to reporting across the covered majors. Ohm's default target
was 7. **The default target was the one nothing checked.**

The reason given was that KiCad 7 could not be containerised, and it was false —
see [`10_container_tag_currency.md`](10_container_tag_currency.md).

---

## What changed

**The table now covers 7, 8, 9 and 10.** Dumped from all four official containers
with `tools/gen_fpdata.py`, merged, and committed: 15 545 footprints, 178 KB —
20 KB more than the three-major table, for a whole additional supported version.

`fpdata.kicad_majors()` and `cli.SUPPORTED_KICAD` are now the same set, and a
test asserts it. While they disagreed, a version Ohm advertised as supported was
one S012 silently abstained on.

**The rebuild is byte-reproducible, proven end to end.** Dumping 8.0.9, 9.0.9 and
10.0.5 in their own containers and rebuilding produced 162 075 bytes identical to
what was committed. That is what makes the `fpdata` CI job's `git diff` check
signal rather than noise.

---

## What having KiCad 7 data immediately found

### 1. Ohm's own standard library named a footprint KiCad 7 does not have

`ohm/std/connectors.ohm` bound both screw-terminal parts to
`TerminalBlock_CUI:TerminalBlock_CUI_TB007-508-02_1x02_P5.08mm_Horizontal` and
its 1x03 sibling. **KiCad added the `TerminalBlock_CUI` family after 7.** So on
the *default* target, `TerminalBlock_2` and `TerminalBlock_3` bound to a footprint
the user's library does not contain, and the pins arrived unconnected.

This is the same class of defect as the KiCad 10 shield renames that motivated
ADR-021 — running the other way in time. It was invisible because S012 abstained
on 7.

Switched to `TerminalBlock_Phoenix:TerminalBlock_Phoenix_MKDS-1,5-2-5.08_…`,
present in 7, 8, 9 and 10 with identical pad ids. The stdlib comment already
called these "**Generic** 5.08 mm rising-clamp screw terminals" — a generic
placeholder has to exist in every supported KiCad, which is the whole argument.

**The rule now has a gate.** `tests/test_std_connectors.py` walks every
`package = "Lib:Footprint"` in `ohm/std/*.ohm` and requires it to be present in
every major the table covers, abstaining on `generate(...)` packages and on
libraries the table does not know. This is the **third** time a stdlib footprint
has been fixed one part at a time — `TerminalBlock_bornier-*` for KiCad 9 was the
first — and fixing these two would have left the next instance for whoever hit
it. Verified by mutation: reintroducing the CUI footprint fails the gate with
`connectors.ohm:36 … -> missing in KiCad 7 (added_later)`.

### 2. S012 called a never-added footprint "removed"

The diagnostic said:

> `'TerminalBlock_2' uses footprint '…', which KiCad 7 no longer ships`
> *help: KiCad removed this footprint from its own libraries…*

Wrong, and actively misleading — it would send a reader hunting for a removal
that never happened. **The two reasons a footprint can be missing need opposite
fixes:**

| | Meaning | Fix |
|---|---|---|
| **removed** | The part is gone from KiCad's libraries | Name its replacement, or vendor the old one |
| **added_later** | The part is fine; this KiCad is older than it | Target a newer KiCad, or name one this KiCad has |

Neither is rare. Against the shipped table: **728** footprints exist in 8, 9 and
10 but not in 7, and **379** that KiCad 7 shipped are gone by 10.

`fpdata.absence(footprint, major)` now returns `"added_later"`, `"removed"`,
`"gap"` or `None`, and S012 writes the message and help that match. `"gap"` — a
footprint present on both sides of a version but not in it — is reported as the
odd thing it is rather than forced into one of the two clean stories; nothing in
the table exhibits it today.

---

## Still not proven

- The table is a **maintenance obligation**: a new KiCad major means a new dump
  and a reviewed diff. The `fpdata` CI job makes that visible rather than
  optional, and `tools/check_kicad_tags.py` reports when a newer image appears.
- The table answers for **KiCad's own libraries only**. A user's or a third
  party's footprint is unknown, and S012 abstains — guessing about those is how a
  checker earns the right to be ignored (ADR-021 §3).
- `ohm verify-footprints` re-reads the *real* installed library and is the
  author-time half. It runs only where KiCad is installed and only when someone
  runs it; the shipped table is what fires during an ordinary `ohm check`.
