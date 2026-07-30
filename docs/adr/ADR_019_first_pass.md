# ADR-019 — Getting it right on the first pass

Date: 2026-07-30 · Status: **accepted**

## Problem

The schematic emitter reached professional drafting quality over roughly twenty
iterations, driven by a reviewer pointing at renders. That is the wrong process
to require of the next user. The output was never the deliverable; the
*learnings* were, and they were sitting in one session's history rather than in
the tool.

Two asymmetries made the iteration necessary.

**Every connectivity defect was caught automatically. Every geometric one was
caught by looking.** The round-trip oracle — KiCad re-reading the emitted
schematic, compared pin-for-pin against Ohm's own netlist — caught a rotation
transform that put both pins of every part at one coordinate, a part silently
dropped from the sheet, a rail line that ran past a diode's pins, and eight
resistors telescoping into a chain that shorted four nets. It caught them
immediately and unambiguously. Meanwhile a frame drawn around three other
frames, a rail name printed through a MOSFET, and a title overprinting a label
were each found by rendering a PDF and squinting, then fixed in two or three
attempts because there was nothing to confirm the fix but another render.

**The language could not state what a drawing needs.** Nine of ten drafting
tells a reviewer listed traced to facts Ohm had no vocabulary for — board
identity, a net's name, why a pin is unused, a value's rating, a placement note.
Where a language cannot state a fact, its compiler invents a substitute, and the
inventions are what read as amateur (ADR-018).

## Decision

Both asymmetries get closed in the tool, not in a process.

### 1. A gate for legibility, matching the one for connectivity

`ohm/check_drawing.py` — **D001** overlapping text, **D002** text over a symbol
body, **D003** overlapping symbols, **D004** duplicate uuids. Pure
`(kicad_sch text) -> diagnostics`, run over every sheet at emit time.

Advisory, deliberately: the netlist is proved exactly and separately, so an
overlapping drawing is ugly rather than wrong, and a gate that refused to emit
would stop people using it.

Parsing the emitted FILE rather than the emitter's internals matters — it holds
for a hand-edited sheet and cannot drift from what was written to disk. It is
the same seam `ohm signoff` uses.

**A noisy check is worse than no check.** The first version reported thousands
of collisions on a clean board: it counted library definitions (every one
declares a reference of `U` at its own origin) and every part's own reference
sitting against its own body, and it measured symbols by their PINS, so every
rider legitimately touching its host's pin read as a collision. Three fixes —
skip the `lib_symbols` block, attribute text to its owning instance, measure the
`_0_1` graphics rather than the `_1_1` pins — took it from thousands of false
positives to a handful of real ones, including symbol overlaps nobody had
noticed by eye. A check that cries wolf is a check nobody reads.

### 2. The author-facing contract, in `AGENTS.md`

What to declare so the first emit is right: `board`, `net NAME =`, `kind`
conformance (which drives the symbol and the frame titles, not just checks),
values with ratings, `unused_pins = open`, `note`. Each is backed by a check
that fires when it is missing — M009, M010, M011 — so the guidance is
discoverable from the diagnostics rather than only from the document.

### 3. One process rule, because it cost the most

**Measure before fixing a drawing defect.** Print the element's coordinates and
extents against its neighbour's. Several defects here took three or four
attempts because a theory was fixed instead of the geometry; the measurement
that finally settled the worst of them was one command, and it should have been
the first one, not the fourth.

## Consequences

- fmuv2 currently reports D001×3, D002×2, D003×2 across its 14 sheets. Those are
  real and now *visible* rather than discovered by a reviewer — including riders
  overlapping the MCU body, which no one had spotted by eye.
- The D tier measures text with an under-estimate of glyph width and a 0.3 mm
  slop, so it reports overlaps that are certainly real rather than ones that
  depend on kerning. It will miss marginal cases; that is the correct trade for
  a check meant to be believed.
- Symbol extents come from the library definition in the same file, so a symbol
  Ohm did not generate is still measured correctly.
