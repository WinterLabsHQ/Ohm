# Ohm for agents

Ohm is designed to be authored by LLM agents. The contract:

1. **Read [`docs/STYLE.md`](docs/STYLE.md) first.** It is self-contained — file
   shape, interfaces, roles, kinds, `expects`, operating points, placement.
   Do not guess syntax from other HDLs; `.ohm` is not Starlark or Python.
2. **Iterate on machine diagnostics.** Run `ohm check <file> --json-diagnostics`
   and fix what it reports; every code is explained by `ohm explain <CODE>`.
   One diagnostic round typically reaches clean (measured in our live-LLM
   fixtures — see `examples/io_expander_board.ohm`).
3. **Declare, don't decorate.** Diagnostics scale with declared intent: use
   typed interfaces (`io(I2C)`) over raw signals, declare `kind` conformance
   on components, declare pin operating points (`voltage=`, `current=`).
   Undeclared values are honestly unchecked — never invent numbers.
4. **Waive visibly.** If a check does not apply, use `waive="..."` on the
   instance — it becomes an auditable note (B010). Never suppress by
   restructuring the design to hide intent.
5. **After routing, run `ohm signoff <src.ohm> <routed.kicad_pcb>`** and treat
   V-errors like compile errors: fix the board (or the source) until exit 0.
   Signoff also runs the geometry gate (V007) through KiCad's own DRC engine.

## Writing a design that DRAWS well, first time (ADR-018, ADR-019)

The emitter can only draw what the source states. Declare these and the sheet
comes out as a controlled document rather than a netlist rendered as boxes —
miss them and Ohm has to invent a substitute, which is what reads as amateur.

- **`board`** — `title`, `rev`, `date`, `company`, `designer`. Without it the
  title block is blank and the drawing cannot be cited in an ECO (**M009**).
- **`net NAME = <label>`** — a net has no name in Ohm, only an election over the
  labels on it, so when no port wins, a capacitor pin names the net
  (`FMU_C_Y1_P1`, after `fmu.c_y1.P1`). Name the signals that matter (**M010**).
- **`kind` conformance** — this is not decoration. It drives the SYMBOL: a part
  declaring `Capacitor` is drawn as a capacitor rather than a labelled box, and
  a cluster's frame is titled by kind ("Crystal", "Connector"). It also drives
  functional zoning of the sheet.
- **Values with ratings** — `Capacitor(value=100nF, vmax=16V, dielectric="X7R")`.
  A rail capacitor with no `vmax` is not specified (**M011**), and the rating is
  printed on the drawing.
- **`unused_pins = open`** on a component with spare pins — one line instead of
  a `must_connect=false` per pin. Every unwired pin becomes a no-connect marker,
  and a pin nobody accounted for is then *visible* because it has none.
- **`note "..."`** for anything geometry cannot say. `near(...)` constraints
  render themselves onto the sheet as placement notes.

## The gates, and why there are several

- **Connectivity is proved, exactly.** `tests/test_schematic_roundtrip.py` makes
  KiCad re-read the emitted schematic and compares it to Ohm's own netlist. A
  wire that misses its pin splits a net and the gate fails. Do not weaken this
  to make a change pass — it is what made every layout rewrite safe.
- **Read-back is proved too.** `tests/test_signoff_readback.py` runs an emitted
  board through `kicad-cli pcb upgrade` — KiCad rewriting it, as it does the
  moment anyone opens and saves — and asserts `ohm signoff` reaches the same
  verdict. Signoff identifies parts by a custom `ohm_path` property, so a KiCad
  release that moved or dropped it would silently stop identifying parts in the
  last check before fabrication. Everything else here tests what Ohm *writes*;
  this is the only gate on what Ohm *reads*.
- **Legibility is checked, advisory.** `ohm/check_drawing.py` (**D001–D004**)
  reports overlapping text, text over a symbol body, overlapping symbols and
  duplicate uuids on every emitted sheet. It exists because every geometric
  defect in this emitter's history was found by rendering a PDF and squinting at
  it, while every connectivity defect was found automatically. A class of defect
  with no gate is a class of defect the next person rediscovers.

**Measure before you fix a drawing defect.** Print the offending element's
coordinates and extents against its neighbour's and read the numbers. Several
bugs in this emitter took multiple attempts because the theory was fixed instead
of the geometry — the measurement that finally settled one of them took a single
command that should have been run first.

Repo hygiene for coding agents: `PROGRESS.md` is the execution log,
`docs/blueprint/` is read-only canonical, tests gate everything
(`pytest -q`, `ruff check .`, `mypy ohm`), builds must stay hermetic and
deterministic (the suite enforces both).
