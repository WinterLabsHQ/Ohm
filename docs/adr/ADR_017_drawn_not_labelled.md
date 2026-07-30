# ADR-017 — A schematic is drawn, not labelled

Date: 2026-07-29 · Status: **accepted** · Supersedes the default set by ADR-016

## Problem

Held against a hand-drawn reference of the same class of board (an STM32 flight
controller: MCU, IMU, barometer, microSD, USB-C, two buck converters), Ohm's
schematic was structurally different in three ways, all of them the same mistake:

1. The reference is **one sheet** partitioned into titled, dashed-boxed blocks —
   "Battery Charger", "3.3V Buck", "IMU". Ohm emitted twelve pages (ADR-016) or,
   before that, one 1251 × 927 mm page.
2. Inside each block the reference **wires parts to each other**: the inductor
   sits between the switch node and the output cap; the feedback divider is a
   visible ladder. Ohm drew every part as an island with a net label on each pin.
   `C40`'s label read `accel.c_c1.P1`, and 40 mm away `U7`'s VDD pin carried the
   same string. The cap and the pin it decouples were joined by **a matching pair
   of strings** rather than by a wire.
3. Only the MCU uses label fanout in the reference — correctly, because fifty
   point-to-point wires are unreadable. Ohm applied the MCU rule to everything.

Measured on fmuv2: of 77 nets, 41 cross a module, 7 are rails, and **24 join
exactly two pins inside one module**. Those 24 were drawn as 48 labels. 82 of the
121 parts are two-pin passives, so most of the sheet was islands.

## Decision

**One sheet, partitioned; local two-pin nets drawn as wires.** `ohm build`
emits a single `.kicad_sch` again; `--sheets hier` still gives ADR-016's
hierarchy for a design too big for one page.

### Riders

A part of ≤ `_RIDER_MAX_PINS` pins that shares exactly one drawable net with a
part of ≥ `_HOST_MIN_PINS` pins is placed **against that pin** and wired to it —
one straight segment, because the rider is positioned from its own pin geometry
so the two pins share a y. When the rider's connecting pin faces the wrong way
the symbol is turned 180°, which for a two-pin part is an exact left/right swap.

Deliberately not attached, and left in the flow with labels:

- a part bridging **two** pins of the same host (a crystal across OSC_IN and
  OSC_OUT) — it can ride neither, and routing it needs a path this does not
  attempt;
- a rider that would **overlap** one already placed on that side of the host
  (host pins are on a 2.54 mm pitch, a two-pin symbol is 5.08 mm tall). Losing
  the lane degrades to the old behaviour rather than drawing a wire through
  another symbol.

### A wired net is anonymous, unless something refers to it by name

A bare wire carries no label, so KiCad names the node `Net-(C2-P1)` where Ohm
called it `c_ctrl.P1`. That is correct for a local RC node and is what a human
schematic does. It is **not** correct for a net anything refers to by name:
`.kicad_pro` matches netclasses — including the diffpair track/gap rules — by
net-name *pattern*, so anonymising one would silently drop its trace rule the
next time the board is updated from the schematic. Nets named in any constraint,
and both halves of every diffpair, keep their label.

### Layout

Each module is packed into a block of roughly `_BLOCK_ASPECT` width-for-area
rather than run out in one row, and blocks are then packed left-to-right across
the page. The wrap width is chosen by trying each standard sheet from A4 up and
keeping the first the drawing fits: fmuv2 lands on **A0**, from 1251 × 927 mm of
"User". Each pin reserves the width its *actual* label needs — a rail is a small
graphic, a wired pin needs nothing — instead of a flat 30 mm, which is what made
the sheet metres wide.

## Verification

The round-trip oracle now compares the **partition** — which pins share a net —
and separately asserts that every net KiCad has a real name for matches Ohm's.
This is not a weakening: a wire to the wrong pin still moves a pin between sets
and still fails. It allows only the one thing that genuinely changed, which is
that an unnamed node is unnamed on both sides.

fmuv2: **77 nets / 377 pins IDENTICAL**, 377 labels → 151, with 14 nets drawn as
wires. Full suite 791 passing.

The oracle earned its keep twice during this change: it caught the netclass
anonymisation above, and it caught a variable-shadowing bug where the label's
text rotation overwrote the symbol's rotation between pins, putting both pins of
every part at the same coordinate.

## Consequences

- Position now carries meaning for a rider: it sits at the pin it serves.
- Passive-to-passive chains (9 nets on fmuv2) are still labelled — neither end
  is a host, so neither rides. They need a chain layout, not a rider.
- Riders are drawn horizontally. The reference draws decoupling caps vertically,
  rail above and ground below; that needs 90° rotation, where the pin transform
  is not the trivial swap 180° is.
- Blocks are grids of cells, not topology-driven placement, so a block has more
  whitespace than a hand-drawn one.
