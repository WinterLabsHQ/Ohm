# 06 · The four KiCad stamps, and why they do not move

Loop 6 · 2026-08-31

---

## The stamps

| Artifact | Constant | Value | Emitted by |
|---|---|---|---|
| `.kicad_sch` | `_SCH_VERSION` | `20230121` | `ohm/emit_kicad.py` |
| `.kicad_pcb` | `_PCB_VERSION` | `20221018` | `ohm/emit_kicad.py` |
| `.kicad_mod` | `_FP_VERSION` | `20221018` | `ohm/pkggen/footprint.py` |
| `.kicad_sym` | `_SYM_VERSION` | `20220914` | `ohm/pkggen/symbol.py` |

All four are the **KiCad 7** format. None changed this round, and none should.
They now live in `ohm/formats.py` (see
[`02_version_registry.md`](02_version_registry.md)) so the value, the owner and
the reason sit in one place.

## Why they do not move

KiCad reads **forward**, and that is the direction it guarantees. A newer stamp
buys nothing — every supported KiCad already opens the KiCad 7 format — and costs
every older KiCad the ability to open the file.

**The distinction easiest to get wrong**, and which this round tested by accident:
the emitted format is independent of which KiCad *application* is supported. When
the floor was briefly raised to 8, none of these four constants changed, because
"the oldest KiCad we vouch for" and "the format we serialise" are different facts.
The docstrings and the policy comment now say so explicitly rather than leaving a
reader to infer it, each cross-referencing ADR-021.

## The corollary the policy forces

Writing the oldest format is a decision about what Ohm **emits**. Ohm's
**readers** face the other way — at files the *user's* KiCad wrote, which is
whatever version they installed. So:

> Every reader Ohm owns must be structural, not layout-anchored.

Not theory. A line-anchored parser has been broken three times by nothing more
than KiCad changing how it prints:

- KiCad 9 pretty-printed `(class "Default")` differently;
- KiCad 10 split nets across lines;
- KiCad 10 **dropped the top-level net table entirely** and now names nets inline
  — `(net "R1_P2")` where 7 to 9 wrote `(net 3 "R1_P2")` — which made
  `ohm signoff` report V001 on **every pad of every board KiCad 10 had saved**. A
  parse failure presenting as a design failure, in the last check before
  fabrication.

`ohm/signoff.py`'s `_net_slot` dispatches on the payload's *shape* rather than on
a version, which is why it handles all four without knowing which KiCad wrote the
file.

## What did change here

Nothing about the stamps. Two things around them:

- **The `Edge.Cuts` outline** — geometry added to the existing format, not a move
  to a new one. See [`11_edge_cuts.md`](11_edge_cuts.md).
- **The uuid derivation**, which claimed in its own module docstring to use
  "stable path strings" and in fact used the path the caller typed. See
  [`20_cross_platform_determinism.md`](20_cross_platform_determinism.md).

## What proves them

`tests/test_formats.py` asserts each registered value appears in the bytes the
named emitter produces. The four-version certification proves KiCad 7.0.11
through 10.0.5 all read what is written: netlist extraction, gerber and PDF
plotting, symbol and footprint import, and re-saving a board and reading it back.

## Still not proven

- **That a newer stamp would be wrong.** The policy is an argument, not a
  measurement — nobody has emitted a KiCad 10 stamp and confirmed KiCad 7 refuses
  it. The cost of being wrong is asymmetric, which is why the conservative choice
  stands without the experiment.
