# ADR-018 — A language for the document, not only the circuit

Date: 2026-07-29 · Status: **accepted**

## Problem

A hardware reviewer, shown Ohm's fmuv2 schematic and asked whether it read as
professionally drafted, said no and listed ten tells. Nine of them share one
root cause, and it is not in the emitter.

**Ohm had a vocabulary for the circuit and none for the document.** It could not
state what the board is called, what revision it is, who drew it, what a net is
*named*, why a pin is unused, what a value's rating is, or what layout must
honour. When a language cannot state a fact, its compiler invents a substitute —
and the inventions are exactly what reads as amateur:

| tell | fact that could not be stated | invention |
|---|---|---|
| blank title block | board identity, revision, owner | blank; KiCad's generator string where a company goes |
| `fmu.c_y1.P1` | what the net IS | `lower.py`'s label election fell through to a capacitor pin |
| `VDD_3V3.v` | same | interface member name leaked into the rail |
| no notes | placement/assembly intent | silence |
| no NC flags | "considered, deliberately unused" | silence, indistinguishable from forgotten |
| `100nF` | rating, dielectric | the value alone |

A net in Ohm had no name — it had an *election*: `min(port_labels or members,
key=lambda l: (l.count("."), l))`. With no port label on the net, a capacitor
pin won.

## Decision

Add a **design-intent layer** to the language. Every item below is a fact about
the design that only the author knows; none of it can be inferred, and asking an
agent to supply it at emit time would be asking it to invent again.

- **`board <name>:`** — `title`, `rev`, `date`, `company`, `designer`,
  `license`, `comment`. Closed field set (R027 on a typo, R026 on a second
  block). Feeds KiCad's `title_block`. Sheet *n of m* is **computed** from what
  is emitted, never declared — the only honest source for it.
- **`net NAME = <label>`** — an explicit name always beats the election. Two
  names on one net is R028.
- **`note "..."`** — engineering notes reach the sheet. `near(...)` constraints
  auto-render as "PLACEMENT: c must sit within 2mm of u", because layout already
  reads that fact from constraints.json and the reviewer reading the PDF cannot.
- **`unused_pins = open`** — one line on a 100-pin MCU instead of 145
  `must_connect=false` lines. Every unwired pin becomes a no-connect marker, and
  B011 goes quiet. A pin nobody accounted for is then *visible*, because it has
  no marker.
- **`vmax` / `dielectric` on a value** — shown on the sheet as `100nF 16V X7R`.
  Tolerance rides inside the Quantity already.

Three checks report the absence of intent rather than guessing at it: **M009**
(no board block, or no title/rev), **M010** (a net named after one of its own
part pins — objective test, no heuristic), **M011** (a capacitor across a supply
rail with no `vmax`).

### Rail names lose the interface member, when that is unambiguous

`VDD_3V3.v` carries an Ohm implementation detail onto a drawing where the reader
wants the rail. The suffix is dropped when the port instance identifies the net
on its own: exactly one net hangs off that prefix, nothing else claims the bare
name, and **the name came from a port label**. That last condition matters — an
earlier version collapsed pin-derived names too, turning `r1.P2` into `r1` and
naming a net after a *part*, which is worse than naming it after a pin. Those
are M010's business instead.

### Sheet count follows fit

A0 is a plotter size; nobody reviews on one. `emit_kicad_sheets` now emits a
single sheet when the drawing fits A3, and otherwise splits into one A3 sheet
per module (ADR-016). `--sheets flat|hier` forces either. fmuv2 goes from one A0
to 13 sheets: eight A4, three A3, one A2 (the 100-pin MCU block) and one User.

## Considered and NOT planned

Both were deferred when this ADR landed. Both are now closed as not planned —
recorded here so a future reader does not mistake them for a backlog.

**Position-based reference annotation.** Professional practice numbers parts by
sheet position, left-to-right and top-to-bottom; Ohm numbers them in
instantiation walk order, which groups them by module and is stable across
re-generation. Doing it properly means the *schematic* reading order — computed
inside `emit_kicad_sch` — must feed designator assignment in `lower`, with the
netlist, schematic and board emitters all agreeing, because a designator is a
BOM contract as well as a drawing label. The cost is a churn of every
designator in every existing design and its BOM; the benefit is that a reviewer
reading a BOM line finds the part slightly faster. Not worth it, and a
deterministic module-grouped numbering is defensible on its own terms. Reopen
only if a real review process asks for it.

**Buses.** `SD_DAT[0..3]` drawn as a bus rather than four labels. Purely
presentational: the four labels are already correct, connected and verified by
the round-trip oracle. Buses would need bus-entry geometry, bus-member naming
in the language, and their own ERC severities, for a drawing that reads the same
to anyone who zooms in. Not planned.
