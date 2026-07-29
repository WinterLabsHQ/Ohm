# ADR-015 — Connection intent, unbacked ratings, and package-family confusion

Date: 2026-07-29 · Status: **accepted**

Companion to ADR-014. That one closed the "where did this pinout come from"
gap. These three close the remaining ways the same board reached `0 errors`
while being wrong.

## 1. `must_connect` targets, and B011

### Problem

`must_connect` was a boolean, and it was read by **presence**:

```python
must_connect="must_connect" in amap
```

So `must_connect=false` — the thing an author writes to record *"I checked this
pin, floating is correct"* — produced a **B003 error**. The one way to declare
intent to float was the one way to trigger the check for not connecting.

Worse, boolean is not what datasheets say. ST marks L3GD20 pins 8–12
"Reserved — **connect to GND**" and pin 15 "Reserved — **connect to VDD**";
InvenSense marks MPU-6000 CLKIN "connect to GND if unused". Wiring such a pin to
a signal net is as wrong as leaving it open, and Ohm could express neither.

On the board that motivated this, **twelve** such pins floated. All were
`role=passive` with no `must_connect`, so every tier was silent — correctly,
because nothing in the source said they mattered.

### Decision

`must_connect` becomes `bool | str | None`:

| value | meaning |
|---|---|
| *absent* (`None`) | nobody has said whether this pin may float → **B011** |
| `false` | declared optional: someone checked, floating is correct |
| `true` | must be on some net |
| `ground` / `rail` | must be on a net of that **class** |

`None` and `False` are both falsy, so every existing `if pin.must_connect:`
still reads correctly; only B011 distinguishes them. An unrecognised value is
**R025**, and resolves to `True` — an unreadable intent is not permission to
float.

**B003** gains the target check: a `ground`-targeted pin sitting on a signal net
is an error naming the net it actually landed on.

**B011** (info) reports, once per component, the unconnected pins with no
declared intent — gated on `mpn=` (a generic shape block is a template, not a
claim about silicon), on `ctx.wired`, and on roles where floating is a real
choice. Outputs and bidirectional pins are excluded: an unused MCU GPIO is
normal and B011 would be pure noise on it.

The point is not the warning. It is that **writing `must_connect=false` converts
an omission into a decision**, and the decision is then visible in review.

### Consequence

On the corrected board B011 found exactly one thing: a USB `ID` pin whose source
carried the comment *"ID left open: device-only port, no OTG host role"* and no
declaration. The comment became `must_connect=false`. That is the whole
mechanism working.

## 2. Q006 — a pass is only as good as the number it passed against

### Problem

Tier-Q compares a declared load against a declared rating. Both are written by
the author. When the rating is *programmed by an external component*, nothing
relates the two:

```ohm
pin ILIM: role=passive,   pad=3
pin OUT:  role=power_out, pad=4, imax=1.5A
```

with `r_ilim = R10k()` on ILIM. The AP22653 sets `I_LIM ≈ 6800/R_ILIM`, so 10 kΩ
programs **~0.68 A**. Q002 summed 1.05 A of declared connector load, compared it
to the literal `1.5A`, and **passed**. The board would current-limit at two
thirds of its budget, and the checker reported coverage.

### Decision

**Q006** (info): when Q002 *clears* against an `imax` on a part that also exposes
a `passive` pin — the shape of every current-limit / feedback programming input —
say so. The message states plainly that the rating is a literal Ohm cannot relate
to the circuit, and points at the escape hatch that already exists:

```ohm
fn ilim(r) = 6800ohm*A / r
assert ilim(r_ilim.value) >= 1.05A
```

Deliberately **info, not warning**: the declaration may well be right, and the
detection is a shape heuristic. The failure being fixed is silence, not the
value.

### Rejected for now

A `derives` clause binding a rating to a closed-form function of the netlist —

```ohm
derives imax on OUT = 6800ohm*A / r_to_gnd(ILIM)
```

— which would make this an **error** when the derivation contradicts the
declaration. It needs new grammar plus net-walking builtins (`r_to_gnd`,
`c_to_gnd`, `v_on`) in the closed-form engine. Q006 buys the visibility now;
the derivation is the right eventual answer.

## 3. S010 — `LGA` means two different things

### Problem

Ohm's `LGA`/`BGA` are **grid arrays**: JEDEC alphanumeric pads, `border=n` keeps
the outer rings. Datasheets also say "LGA" for leadless **perimeter** packages
whose pads are numbered 1..N counterclockwise — which in Ohm are `QFN`/`DFN`.

Reaching for `generate(LGA, rows=5, cols=5, border=1)` on an ST LGA-16 produced a
**5/3/3/5** ring for a part that is **4 pads per side**, and the "datasheet pin N
→ Nth pad in emission order" mapping made pins 6–11 zigzag between opposite edges
of the package. Three of five sensors on the board sat on a footprint with no
relationship to the silicon.

S003 saw nothing, correctly: the map was total.

### Decision

**S010** (info) on a generated grid package with `border=` whose ring has unequal
sides, telling the author what the ring actually is and what to use instead:

```
'L3GD20H' uses generate(LGA, border=...) with rows=5, cols=5, giving a perimeter
ring of unequal sides (5/3/3/5) and JEDEC grid pad names
  help: if the datasheet numbers this part's pads 1..N around the outline rather
  than as a letter/number grid, it is a QFN/DFN here:
  generate(QFN, pins=16, pitch=..., body=...)
```

Info rather than warning because a genuine depopulated grid array is a legitimate
use of the same syntax — Ohm cannot tell them apart without the datasheet, which
is exactly the boundary ADR-014 draws.

## Consequences

Together with ADR-014, all three of the failure modes behind the motivating board
are now visible:

| failure mode | before | after |
|---|---|---|
| fabricated pin↔pad map | silent | S009 / S008 / `verify-pinouts` |
| rating with no circuit behind it | silent pass | Q006 |
| pin nobody thought about | indistinguishable from a deliberate float | B011, `must_connect=ground\|rail` |
| grid family on a perimeter part | silent | S010 |

None of these is an error by default. The design principle they share is that
**silence must be a choice** — every one of them can be discharged by writing a
declaration, and the declaration is what a reviewer reads.
