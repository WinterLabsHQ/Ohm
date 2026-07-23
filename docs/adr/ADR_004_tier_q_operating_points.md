# ADR-004 — Tier-Q ratings source + declared operating points (P9)

Date: 2026-07-23 · Status: accepted

## Ratings source (resolves manual Part 7)
Baseline = **option (d)**: ratings are typed fields on `pin` declarations
(`vmax`, `imax`, `pmax`) — always hermetic, zero dependencies, frozen into the
source by construction. The pluggable seam is `ratings_iface.py`
(`RatingsProvider` protocol, default `DeclaredRatings`); richer providers
(curated DB, parts APIs frozen at authoring time) plug in behind it later.

## Declared operating points (new)
Tier-Q compares "computed/declared electrical quantity against the rating"
(manual §P9). v1 has no simulation, so operating values must be *declared*:

- `pin SW: role=power_out, voltage=3.3V` — the voltage this pin drives its net to
- `pin VDD: role=power_in, current=150mA` — the current this pin draws

Net voltage := declared voltage of its driver pins (`power_out`/`output`);
`ground`-role pins pin their net to 0 V. Grammar unchanged (pin attrs are
already generic); `ir.Pin` gains `voltage`/`current`.

## Checks (worst-case over tolerance intervals)
- **Q001** over-voltage: net voltage (hi) > `vmax` of any pin on the net
- **Q002** over-current: sum of declared draws (hi) on a net > driver `imax`
- **Q003** over-power: 2-pin component with resistance-dimension `value` whose
  both nets have known voltages: worst-case (ΔV)²/R > `pmax`
- **Q004** conflicting declared voltages on one net (incl. a driver shorted to
  ground)

Undeclared values ⇒ unchecked (D3: detectability is bounded by declared intent;
never claim more).

Bidirectional pins participate only through explicit declarations: a
`bidirectional` pin with `voltage=` counts as a declared source (Q001/Q004);
with `current=` it joins the Q002 draw sum. Without declarations it stays
unchecked, as before.

## Q005 — coarse thermal check (added)
- **Q005** over-temperature: when a part declares attrs `theta_ja` (plain
  number, degC/W) and `tmax` (temperature quantity), and its dissipation is
  computable via the Q003 path (2-pin resistive part, both net voltages
  known): 25 degC + P_hi × theta_ja > tmax ⇒ error.

**Fixed-ambient assumption**: ambient is fixed at 25 degC. v1 has no ambient
or enclosure model; a declared ambient attr can relax this later without
changing the check's shape.
