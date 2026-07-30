# Changelog

## Unreleased

- **feat(check)**: a **D tier** — the drawing is checked, not eyeballed
  (ADR-019). `ohm build --emit kicad` now reports **D001** overlapping text,
  **D002** text over a symbol body, **D003** overlapping symbols and **D004**
  duplicate uuids on every sheet it writes. Every connectivity defect in this
  emitter's history was caught automatically by the round-trip oracle; every
  geometric one was caught by rendering a PDF and looking at it. That asymmetry
  is why the drawing took twenty iterations, and it is now closed.
  Advisory by design: the netlist is proved exactly and separately, so an
  overlapping drawing is ugly rather than wrong, and a gate that refused to emit
  would stop people using it. The first version reported thousands of collisions
  on a clean board — library definitions, and every part's own reference over its
  own body — so it also skips `lib_symbols`, attributes text to its owning
  instance, and measures a symbol's graphics rather than its pins. A check that
  cries wolf is a check nobody reads.
- **docs**: `AGENTS.md` now carries the first-pass contract — what to declare so
  the drawing comes out right without iteration, which checks enforce each one,
  and the one process rule that cost the most: measure a drawing defect before
  fixing it.

- **fix(schematic)**: a rail name is placed where there is ROOM for it, and a
  parallel bank returns to ground per part.
  - A power port's name is a property, so unlike a net label — which is bound to
    the wire it names and would disconnect it if moved — it is free. It now
    picks the first of several candidate spots (outward first, then stepped off
    the pin's row, then the other side) that collides with no other text AND no
    symbol body. A buzzer connector's `VDD_5V_PERIPH` needed 13mm and had 7.6mm
    of gap, so it printed straight through the MOSFET beside it; there was no
    "outward" answer, only a clear one.
  - An upright rider reserves room for the name its rail port prints, not a
    token 2.54mm: it stands vertical, but the name is still drawn horizontally.
  - **A parallel bank shares only its supply rail.** Each part returns to its
    own ground symbol instead of a common return wire running the width of the
    bank. That is the convention, it is what the caps' own symbols already say,
    and it stops the bank stretching its block for no information.

- **fix(schematic)**: text no longer overprints text. Ohm now tracks the extent
  of every label, rail name and symbol body it places, and puts the text it may
  freely MOVE — a module or cluster frame title — in the first candidate spot
  that collides with none of them. A net label is deliberately NOT movable: it
  is bound to the wire it names, so sliding it off to tidy the page would
  disconnect the net; the collision is resolved by moving its neighbour instead.
  Rail-name candidates run outward only, because flipping a name back over the
  part it belongs to trades one overlap for a worse one — it lands on that
  part's own pin names. Frames also expand to enclose what is actually inside
  them (a rotated symbol swaps its axes, and a glyph draws past its pins), so a
  frame edge no longer cuts through an LED.

- **feat(schematic)**: parts in SERIES are drawn as a chain. A rider may now
  ride another rider — rail into a ferrite into a filter cap, a resistor into
  the LED it feeds — so a series circuit is drawn end to end instead of as two
  parts with a label between them. Chains were previously rejected outright
  because a rider is positioned from its host and one could be reached before
  its host had a position, silently dropping a part; placement now walks riders
  shallow-first by depth, only a cycle is rejected, and the host pin is
  transformed by the host's OWN rotation, which a chained host can have. Cell
  width reserves the whole chain rather than the first link, so a chain cannot
  overrun into the next cell and draw over its symbols.
  A net above `_SAT_MAX_FANOUT` endpoints is excluded from clustering: it is a
  BUS, not a local connection, and picking one of its members to ride is
  arbitrary. Eight LED resistors sharing a supply node each rode the next,
  telescoping into a chain that overlapped itself and shorted four separate LED
  nets together — caught by the round-trip oracle.
- **fix(schematic)**: element uuids are unique. Deriving a wire's uuid from its
  net name alone was fine while every net was one stub per pin; as soon as a net
  became several segments the same id was emitted repeatedly, and KiCad's
  behaviour on duplicate uuids is undefined. Wire, label and sub-block frame ids
  now include their coordinates, with a test asserting no duplicates.

- **feat(schematic)**: parts in PARALLEL are drawn as a ladder. Eleven bypass
  caps across one rail were eleven copies of the same two-terminal drawing, each
  with its own rail label and its own ground symbol — nothing on the page said
  they were in parallel. A run of same-row parts sharing the same pair of nets
  now stands upright on one rail wire along the top and one return along the
  bottom, with junction dots at each rung and the rail arriving ONCE at the head.
  On fmuv2: 31 parts in 10 parallel groups, labels 136 -> 44.
  The rail line's height is taken from the members' real pin geometry rather
  than assumed: a group mixing a glyph (pins at 3.81mm) with a box symbol ran
  the rail straight past the box's pins, leaving a diode on the page connected
  to nothing. Such a group is now left alone. The whole ladder is also decided
  before anything is mutated — an earlier version rotated and silenced members
  as it went, then bailed on one that could not form a rung, stranding the rest.

- **feat(schematic)**: structure INSIDE a sheet, and a second rendering of it.
  - **Sub-block frames.** A module box on a sheet that holds one module frames
    the whole page and says nothing; the structure a reader wants is one level
    down. Each cluster — a host and the riders wired onto its pins — and the
    bank of rail-only parts now get their own dotted, titled frame. Titles come
    from the declared KIND, so they read functionally ("Crystal", "Connector",
    "Mcu", "Decoupling") rather than by designator; a cluster mixing a generic
    passive with something specific takes the specific name, so an LED and its
    series resistor is an "LED", not a "Resistor".
  - **Flat overview plot.** `<stem>_overview.kicad_sch` renders every block on
    one sheet alongside the hierarchy — the same netlist, twice: hierarchical
    sheets to EDIT, one sheet to REVIEW without losing the system view. It is
    standalone, never referenced by the root, so KiCad cannot load the design
    twice. Verified against Ohm's netlist independently of the hierarchy.
  - **Even density.** The drawing is centred on whatever standard sheet it lands
    on, instead of sitting in the top-left with three quarters of the page
    blank. The bank starts its own row so its frame stops reaching up and
    enclosing the clusters above it.
  - **Functional zoning.** Blocks are laid out power, compute, peripheral,
    interface — read from the declared kind taxonomy (ADR-008), so no name
    heuristics. Zone is a PRECEDENCE, not a vote: a block holding a regulator is
    the power block whatever else is in it. Ranking by pin count instead put
    `pwr` in the interface zone, its 6-pin input connector outvoting the two
    regulators the block exists for.

- **fix(schematic)**: local nets are wired in CLUSTERS, not only in pairs. The
  rider rule required a net to have exactly TWO nodes, and that one condition
  excluded every recognisable cluster on the board: a crystal load cap sits on
  cap+crystal+MCU, three nodes, so crystal circuits, reset RCs and LED series
  resistors all stayed as scattered label pairs. A local net is now drawn as a
  wire between two of its parts whatever its node count; when it reaches
  further, the remaining pins keep their labels and ONE label rides the wire, so
  the cluster has shape and a single signal leaves it. The host is the SMALLEST
  qualifying neighbour rather than the richest — a load cap belongs against its
  crystal, not dragged across the block to the 100-pin MCU the same net reaches.
- **fix(schematic)**: a part could vanish from the drawing. A rider is
  positioned from its host, so when the host was itself a rider the two were
  ordered by chance and the dependent one could be reached before its host had a
  position — then silently skipped. Rider chains are now rejected outright, and
  a new test asserts every part in the BOM appears on a sheet, which is the
  invariant that caught it.

- **feat(language)**: a **design-intent layer** (ADR-018) — the vocabulary a
  schematic needs to read as a controlled document rather than a generated one.
  A hardware reviewer listed ten drafting tells on the fmuv2 board; nine came
  from facts Ohm could not state, so the emitter invented substitutes.
  - **`board <name>:`** with `title` / `rev` / `date` / `company` / `designer` /
    `license` / `comment` fills KiCad's title block; sheet *n of m* is computed
    from what is emitted. Closed field set: R027 on an unknown field, R026 on a
    second block.
  - **`net NAME = <label>`** names a signal, beating the label election that
    used to let a capacitor pin name a net (`fmu.c_y1.P1`). R028 when two
    declarations name one net. Rails also drop their interface-member suffix
    when unambiguous: `VDD_3V3.v` -> `VDD_3V3`, `GND.gnd` -> `GND`.
  - **`note "..."`** puts engineering notes on the sheet; `near(...)`
    constraints render themselves as placement notes.
  - **`unused_pins = open`** marks every spare pin no-connect from one line.
  - **`vmax` / `dielectric`** show on the drawing: `100nF 16V X7R`.
  - New checks: **M009** (blank title block), **M010** (net named after a
    component pin), **M011** (rail capacitor with no voltage rating).
  - Sheet count follows FIT: one sheet while the drawing fits A3, otherwise one
    A3 sheet per module. `--sheets auto|flat|hier`. fmuv2: one A0 -> 13 sheets
    (8xA4, 3xA3, 1xA2, 1 oversize).
  - `ohm fmt` round-trips `net` and `note` — a formatter that silently deletes
    source is worse than no formatter.

- **fix(pkggen)**: generated footprints no longer draw silkscreen over pads.
  The body outline was a plain rectangle at courtyard - 0.25mm, which on any
  package whose pads reach the courtyard lay across them — 76 `silk_over_copper`
  violations on the fmuv2 board, and in fabrication silk on bare copper is
  either clipped by the mask or lands on the solderable surface. The outline is
  now cut against every pad (KLC F5.1 clearance) and only the clear pieces are
  drawn, which is what KiCad's own R_0603 does; the pin-1 marker is dropped when
  a pad reaches its corner. Package agnostic: a chip keeps its two stubs, a QFN
  keeps its corners. **76 -> 0.**
- **feat(check)**: **S011** — two parts cannot share board area. `emit_kicad`
  claimed no courtyard overlaps "by construction", true only of AUTO-placed
  parts; a `constraint fixed(...)` puts a part exactly where the source says and
  nothing checked whether the source said something impossible. KiCad's DRC
  found five overlapping pairs on fmuv2 that every Ohm tier had passed. The
  check runs over the SAME `placement()` the emitter uses, so verdict and board
  cannot drift. Severity follows what Ohm knows: **error** when both courtyards
  come from real `generate(...)` geometry, **warning** when either part is a
  string footprint, whose land pattern Ohm has never read and places on a 5x5mm
  placeholder — calling that a collision would assert a fact Ohm does not have.

- **feat(signoff)**: `ohm signoff` now runs the **geometry gate itself** (V007)
  instead of telling you to run `kicad-cli pcb drc` — a subcommand that does not
  exist in KiCad 7, the version Ohm pins, so geometry was in practice never
  checked. `ohm/drc.py` drives KiCad's own DRC engine through its Python API
  (`pcbnew.WriteDRCReport`), including from inside a virtualenv, where KiCad's
  bindings are not importable by name, and reports one diagnostic per rule class
  with a count and first location. Unconnected pads and unresolved footprint
  libraries are info, not error: an Ohm board is unrouted until a router runs,
  and it carries footprint geometry inline by design. `--no-geometry` skips it;
  a missing KiCad degrades to a warning rather than a failure. `signoff.py`
  stays pure (N1). `.kicad_pro` now also carries ERC severities, raising
  `similar_labels` and `pin_not_connected` to error for the GUI ERC run —
  KiCad 7 has no headless ERC.

- **feat(schematic)**: the schematic is now DRAWN rather than labelled
  (ADR-017). A passive serving one pin of an IC is placed against that pin and
  wired to it, instead of floating elsewhere on the sheet joined to it by a
  matching pair of net labels; each module gets a dashed, titled partition box;
  blocks pack across the page and the page snaps to the smallest standard sheet
  that fits. fmuv2: one A0 sheet instead of 1251 x 927 mm of "User", 377 net
  labels down to 151. A net drawn as a bare wire is anonymous, EXCEPT where a
  netclass or diffpair constraint names it — anonymising those would silently
  drop their trace rules. `--sheets hier` still emits the ADR-016 hierarchy.
- **feat(schematic)**: `ohm build --emit kicad` now writes a **hierarchical**
  schematic — a root sheet of module blocks plus one `<stem>_<module>.kicad_sch`
  per top-level module, A3 where the module fits (ADR-016). The fmuv2 reference
  board goes from one 1251 × 927 mm page to 13 sheets, 11 of them A3. Signals
  that leave a module become hierarchical labels matched by sheet pins; rails
  stay power symbols; module-local nets stay sheet-local labels. Designs with
  fewer than two modules are unchanged, single-sheet. `.kicad_pro` now lists its
  sheets. The KiCad round-trip oracle covers the hierarchy: 77 nets / 377 pins
  read back IDENTICAL across all 13 sheets.

## 0.1.1 — 2026-07

- **fix(init)**: the interactive `ohm init` / `ohm create-project` wizard now
  prompts for a project *name* and creates a new subfolder by default; a blank
  answer scaffolds `./my-board` (not the current directory, which previously
  surprised users into scaffolding their parent folder). Use `.` to scaffold in
  place. An explicit name argument was always correct and is unchanged.

## 0.1.0 — 2026-07 (first PyPI release)

Initial development, complete through signoff. Milestones (details in
`PROGRESS.md`, decisions in `docs/adr/`):

- **P0–P6**: grammar/parser/AST with error recovery; resolver → IR; exact
  Decimal units with tolerance intervals; netlist + KiCad 7 emitters with
  hand-verified golden corpus; structural (S) and intent (I) tiers.
- **P7–P9**: `.zen` interop (import + export, ADR-002); closed-form evaluation
  with interval arithmetic + obligations-as-holes (ADR-003); operating-point
  vs ratings tier Q (ADR-004).
- **P10–P12**: `constraint` declarations + router seam (ADR-005); stdlib-only
  LSP; `ohm fmt`; `ohm explain`; IPC-7351 package generation (ADR-006);
  live-LLM validation loop.
- **L**: standard library — ~106 generic blocks + honest templates across 17
  domains, pinouts adversarially verified against KiCad libraries (ADR-007).
- **K**: checked component `kind` taxonomy — structural conformance contracts
  (~42 kinds), K001–K005 (ADR-008).
- **F**: first-spin tier — `expects` (pullup/decoupling/loadcaps/termination/
  tie, `when` gates, auditable waivers), bring-up checks B001–B010, DFM lints
  M001–M008, placement intent + routed-handoff KiCad emit (ADR-009).
- **G**: repetition — arrays, bounded `for` loops, loop-index arithmetic,
  compile-time computed values; adversarially verified (ADR-010).
- **V**: `ohm signoff` — routed-board verification against the source
  (V001–V006), validated against KiCad's connectivity engine (ADR-011).
- **E0/X0/X1**: expressiveness + ecosystem (ADR-012) — pure `fn` functions,
  config-driven array counts/loop bounds, compile-time `if`/`else` variants
  (all folding at elaborate, override-guarded); `.zen` designs as check input
  + `ohm convert` to annotatable Ohm source; `ohm import-symbol` turns real
  KiCad symbols into Ohm components with true pads and roles.
- **pkggen catalog expansion**: extended from gull-wing + chip to the common
  modern packages (ADR-006's extensible-catalog path, each validated against a
  KiCad 7 reference land pattern before shipping): `QFN` (four-side no-lead +
  optional thermal pad), `QFP` (four-side gull-wing, per-part lead `span`),
  `DFN`/`SON` (two-side no-lead), `BGA` (rows×cols grid of circular pads, JEDEC
  A1.. naming, `ball`/`pad` diameter, optional `border=<n>` for perimeter /
  depopulated grids), `LGA` (rectangular-land grid, same options), `PLCC`
  (J-lead, pin 1 centered on the top edge, counterclockwise), and the fixed tab
  packages `SOT223`/`SOT89`/`TO252`/`TO263` (three signal leads + a solid tab
  pad, no arguments). Shared 4-/2-side placement helpers back the perimeter
  families; the footprint emitter gained a `circle` pad shape for BGA balls.
  QFN/BGA/PLCC (vs 20/28)/SOT-223 and the power-package signal leads reproduce
  their KiCad references exactly; QFP/DFN match within 0.05 mm; power-package
  tabs are solid-rectangle simplifications of KiCad's paste-subdivided lands.

- **project UX**: `ohm init [name]` / `ohm create-project [name]` scaffold a
  full project (starter design, `ohm.toml`, README, `AGENTS.md`,
  `docs/LANGUAGE.md`, `lib/`, `out/`, `.gitignore`) from bundled templates —
  strictly additive, never clobbering existing files. `ohm.toml` makes the CLI
  framework-aware: bare `ohm build` / `ohm check` walk up to the project root
  and use the configured entry, out dir, and KiCad target (an explicit file,
  `-o`, or `--target-kicad` always wins).
  `ohm refactor <dir>` restructures a messy folder into that layout (dry-run by
  default; `--apply` to execute; stray root `.ohm` files move to `lib/`).
  `ohm doctor` diagnoses the install (Python, PATH, encoding, KiCad libs,
  project). The CLI now forces UTF-8 on its own stdout/stderr, so a non-UTF-8
  console (Windows cp1252 / Git Bash) no longer crashes on non-ASCII
  diagnostics. Global install is documented via `pipx install .`.

680 tests; ruff + mypy clean; deterministic double-build byte-identity and
hermeticity enforced in CI.
