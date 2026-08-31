# 11 · `Edge.Cuts` — producible was not orderable

Loops 12-13 · 2026-08-31 · commit `3fd9b86`

---

## What it was

`ohm/emit_kicad.py` declared the layer:

```python
'    (44 "Edge.Cuts" user)',
```

…and drew nothing on it. A `grep -rn "Edge\.Cuts" ohm/` returned that one line.

`tests/test_kicad_plot.py` proved the emitted board plots — `kicad-cli pcb export
gerbers` and `pcb export drill` succeed and produce well-formed files. But **a
gerber package with no board outline is not orderable**: every fabricator needs a
profile layer to know what to cut. The plot gate proved *producible*, and
`KICAD_CI_IMPLEMENTATION.md` recorded the gap honestly rather than letting it pass
as more than it was.

---

## Where the outline comes from

**A board size is not declarable in the language today**, and that was established
rather than assumed:

- `ohm/ohm.lark:17-18` — `board_item: NAME "=" STRING`. Every board field is a
  **string**; there is no numeric or dimensional form.
- `ohm/resolve.py:32` — `_BOARD_FIELDS` is a closed set of seven text fields
  (`title`, `rev`, `date`, `company`, `designer`, `license`, `comment`), enforced
  by R027. Nothing size-shaped.
- `ohm/ir.py:217` — `BoardDef` holds only those string pairs, and
  `emit_kicad_pcb(low, source)` **is not handed the `Design` at all**, so the PCB
  emitter could not read a `board` block even if one carried a size.

The "60 × 30 mm" in `real_run_reports/` is prose in a human's run report about a
board they enlarged by hand in KiCad, not an Ohm construct.

So the outline is **derived**: `board_extents(low)` is the bounding box of every
placed part's courtyard, grown by a 2 mm margin. It reuses `placement()` — the
same poses the footprints are emitted from, passed in rather than recomputed —
and imports `_extent` from `ohm/check_placement.py` for the rotated
axis-aligned half-extent, so the outline and the S011 overlap verdict cannot
disagree about how big a rotated part is.

**A declared size would be better**, and what it would take is written down so the
next person does not re-derive it: `size = "60x30mm"` already *parses* (the
grammar accepts any `NAME = STRING`), so it needs `size` added to
`_BOARD_FIELDS`, a parse in `resolve`, carrying it into `IRLow` or passing
`Design` to `emit_kicad_pcb`, and a check that the declared box encloses the
placement. That is a grammar and IR change, deliberately out of scope here.

---

## What is emitted

```
(gr_line (start 9.75 19.30) (end 28.25 19.30) (stroke (width 0.1) (type solid)) (layer "Edge.Cuts") (tstamp "…"))
(gr_line (start 28.25 19.30) (end 28.25 28.70) …)
(gr_line (start 28.25 28.70) (end 9.75 28.70) …)
(gr_line (start 9.75 28.70) (end 9.75 19.30) …)
```

Four segments joined end to end and closing onto the first — chosen over a single
`gr_rect` **because closure then becomes an assertable property of the file.**

Determinism: fixed corner order (clockwise from top-left); tstamps from the
existing `_uid(...)` uuid5 over the fixed namespace; `Decimal` throughout, no
float anywhere. The 0.01 mm quantize rounds **outward** — `ROUND_FLOOR` on the
minima, `ROUND_CEILING` on the maxima — rather than `_mm`'s half-even, so the
quantum can only ever make the board larger and never clip a courtyard.
`_PCB_VERSION` is untouched at `20221018`: this is geometry added to an existing
format, not a move to a new one.

`ohm/check_drawing.py` needs no change — its only call site iterates the
schematic sheets, and the `gr_line` lives in the board.

---

## The diagnostic

**M012**, severity **warning** — Tier-M is advisory by design and never blocks a
build:

> no board outline: the design places no parts, so Ohm has no extents to derive
> `Edge.Cuts` from — the emitted board declares the layer and draws nothing on
> it, and a gerber package with no profile is not orderable

It is asked of `board_extents` itself, so the warning and the file cannot drift
apart. It sits **above** `check_mfg`'s BOM-stage gate, because below it the check
would be unreachable: a part-less design has no `mpn=` anywhere. And it is
reachable from real source — two `io()` ports wired to each other is a design
with zero parts.

---

## What proves it

The gate had to be built carefully, and the reason is a measurement worth keeping:

> **KiCad plots a 429-byte, perfectly well-formed, entirely empty profile gerber
> for an outline-less board** — right header, right `%TF.FileFunction,Profile,NP*%`,
> right `M02*`, zero apertures.

A test asserting only that a profile *file* arrived **could not have failed**. So:

- `tests/test_kicad_plot.py` recognises the profile by both filename suffix and
  the X2 `TF.FileFunction` attribute, requires at least four `D01*` draw
  operations, and then parses the `X…Y…D01/D02` commands out of `%FSLAX46Y46*%`
  nanometres, un-negates KiCad's Y, and asserts **the plotted corners equal the
  `gr_line` corners in the `.kicad_pcb`** and that the path closes.
- `tests/test_board_outline.py` (no KiCad needed) checks all 17 example and
  golden designs: exactly four closed segments forming a rectangle, the box
  encloses every placed part's courtyard, the 2 mm margin present on all four
  sides to within one quantum, and two builds emit identical outline lines. Plus
  a rotated `fixed` SOIC-8 with the numbers written out — `(54.44, 45.70)`, where
  ignoring rotation would give `(55.70, 44.44)` — and the part-less case.
- `tests/test_mfg.py` checks M012 fires once, clears the moment a part exists, and
  escapes the BOM-stage gate.

**Mutation, actually run.** Commenting out the single emit line:
- locally, **18 failed** in `test_board_outline.py`;
- inside `kicad/kicad:10.0.5`, **24 failed**, including every
  `test_kicad_plots_gerbers_from_the_emitted_board` and every
  `test_the_plotted_profile_is_the_outline_ohm_wrote`.

**Verified in containers:** 68 passed at 10.0.5 and at 9.0.9 for the two outline
files; and the full gate set at **7.0.11 and 10.0.5, 340 passed each** — the
oldest supported KiCad reads and plots the new geometry with no format change.
At 10.0.5 the profile gerber's real content was confirmed:
`%TA.AperFunction,Profile*%`, `%ADD10C,0.100000*%`, and four `D01*` draws at
exactly `9.75 / 19.30 / 28.25 / 28.70`.

---

## Still not proven

- **That a fab would accept the package.** A closed profile is necessary, not
  sufficient — no board from Ohm has been ordered.
- **The outline is a rectangle.** Cutouts, rounded corners, non-rectangular
  profiles and mounting-hole keepouts are not emitted. A design needing one still
  edits the board in KiCad, and signoff will see the edit.
- **The 2 mm margin is a constant, not a rule.** It is not derived from a
  clearance requirement and no check relates it to one.
