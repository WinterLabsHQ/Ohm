# ADR-003 — Closed-form evaluation semantics (P8)

Date: 2026-07-23 · Status: accepted

1. **Time units added** (`s, ms, us, ns`) plus `resistance × capacitance → time`,
   needed by `rc_tau`. Extension of the manual §3.1 v1 set in the same style.
2. **Worst-case assert semantics**: an assert passes only if it holds across the
   entire tolerance interval. Certain violation → error C001; possible violation
   (interval overlaps the bound) → error C002. Both block emission — a "maybe"
   on a physical limit is not a pass.
3. **Unevaluable asserts → info C003**, never silently dropped (D3 honesty: the
   author sees exactly what the build could not check) and never an error (a
   top-level module legitimately references externally-supplied values, e.g.
   `vin.v.voltage` in the canonical example).
4. **Per-context folding**: asserts are folded once per reachable instantiation
   context with that instance's config overrides (defaults otherwise), so an
   override that violates a submodule assert is caught. Cycle-guarded.
5. **Float boundary**: only transcendental formulas (microstrip) use float;
   results are rounded to 4 decimal places before re-entering Decimal world, so
   values are identical across machines. `thermal_rise` takes θJA as a plain
   number (degC/W has no unit in the v1 table).
6. Closed-form function names (`divider`, `series`, `parallel`, `p_vi`, `p_i2r`,
   `p_v2r`, `rc_tau`, `thermal_rise`, `microstrip_z0`) live in one registry in
   `closedform.py`; unknown calls simply make an assert unevaluable (C003).
