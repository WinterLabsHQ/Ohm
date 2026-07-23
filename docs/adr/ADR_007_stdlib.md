# ADR-007 — Standard library: `use` imports + generic building blocks

Date: 2026-07-23 · Status: accepted

## The idea
Instead of modeling every market part, ship **generic parameterized building
blocks** covering the categories of electronics, declared in Ohm source inside
the compiler package (`ohm/std/*.ohm`). A real part is an instantiation:
`Resistor(value=10kohm +/- 1%, package="Resistor_SMD:R_0805_2012Metric",
pmax=125mW, mpn="RC0805FR-0710KL")`.

## `use` statements (the one language addition)
- `use std.<domain>` loads a stdlib file; `use "./lib.ohm"` loads a local file
  relative to the importer. Loading is parse-stage local file reads only —
  hermeticity (N1) intact, deterministic given the installed compiler version.
- Imports dedup by absolute path; cycles are cut and diagnosed (**R016**);
  unknown targets are **R015** (help lists the installed std modules).
- Merge is post-order (imports before importer), so `design.top` remains the
  entry file's last module. Namespace is flat; a collision with a user
  declaration is the existing R001 (rename or drop the `use`).
- Every declaration is stamped with its source file, so diagnostics inside an
  imported file point at that file with its own line numbers. Known limitation:
  checker-tier (S/I/Q) diagnostics about imported *components* carry the entry
  path (IR-level file threading is future work); stdlib blocks ship
  checker-clean so this only affects defective local imports.

## Block semantics
- **Per-instance `package=` override** (blueprint R9 made real): string
  footprint or `generate(...)`; Tier-S re-closes the pin↔pad loop against the
  effective package, anchored at the instantiation site.
- **Ratings fallback chain**: pin-declared rating → part-level attr (which
  includes instance overrides). `Resistor(pmax=250mW)` is Tier-Q-checkable.
- Generated footprints are **spec-named and shared** (`ohm:CHIP0603`,
  `ohm:SOIC-8`): footprint identity is the package, not the part; identical
  specs emit one `.kicad_mod`. Symbols stay per-component (pin names).

## Honesty rules (N3, enforced in review)
- Concrete pads ONLY where the pinout is a genuine industry standard
  (SOT23-5 single op-amp, MAX485-lineage RS485 SOIC-8, SOT-23 BJT/MOSFET…);
  vendor-variance is stated in comments at the block.
- Everything else ships as a `# TEMPLATE (adapt to your datasheet):` comment
  block — never as a declared component that would silently netlist wrong.
  (Declared blocks must be instantiable and Tier-S clean; a pad-less
  "abstract" component would be a footgun, so abstract = template.)
- String footprints use the `Lib:Name` form and are verified against the
  local KiCad installation (`/usr/share/kicad/footprints/`), including pad ids
  read from the actual `.kicad_mod`.
- No invented default ratings. Blocks are categories; datasheet values are the
  user's to declare per instance.

## Adversarial pinout verification (wave L2, 2026-07-23)
A fresh-context auditor checked every concrete pad map against hermetic sources
(KiCad `*.kicad_sym` symbol libraries for real parts, USB/JEDEC specs, one TI
datasheet). ~45 pinned blocks across 9 files: **44 CONFIRMED** (9 with
vendor-variance caveats the block comments already state), **1 correction** —
`Comparator_SOT23_5`'s pad map is valid (MCP6561/TLV703x) but its comment cited
LMV331, whose OUT is on pad 4; the citation was removed and a variance warning
added. `VRef_SOT23_3` was demoted to a template during L1 for the same honesty
reason (no cross-vendor SOT-23-3 reference pinout). Live-agent test: a fresh
LLM built a BME280 I2C sensor node from STYLE.md + stdlib after one diagnostic
round (frozen as `tests/examples/sensor_node_bme280.ohm`).
