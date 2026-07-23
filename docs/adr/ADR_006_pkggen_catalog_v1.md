# ADR-006 — pkggen v1 catalog + IPC-7351 model (P11)

Date: 2026-07-23 · Status: accepted

## v1 catalog (honest scope, N3)
`SOIC`, `SSOP`, `TSSOP` (dual gull-wing), `SOT23` (3/5/6, fixed JEDEC
positions), `CHIP0402/0603/0805/1206` (2-terminal). Anything else
(`QFN/QFP/BGA/...`) is a **compile error S006 naming the supported set** —
shipping an unvalidated footprint family silently would be the exact failure N3
guards against. The catalog is one table; families are added by adding entries.

## IPC-7351 land-pattern math (deterministic)
Gull-wing:  Zmax = Lmax + 2·J_toe;  Gmin = Smax − 2·J_heel − √(Cs² + F² + P²)
with S = lead-heel span (Smax = Lmax − 2·Tmin, Smin = Lmin − 2·Tmax,
Cs = Smax − Smin), fab/placement tolerances F = P = 0.05 mm.
Pad length = (Z−G)/2, center = ±(Z+G)/4, width = b_max + 2·J_side.
Fillet goals per density (least/nominal/most) from IPC-7351B: gull-wing
(0.15/0.25/0.01, 0.35/0.35/0.03, 0.55/0.45/0.05), chip (0.1/0/0, 0.2/0/0.05,
0.4/0.05/0.05). The single √ uses float and is rounded to 0.01 mm before
re-entering Decimal (same discipline as microstrip, ADR-003). Validated against
KiCad 7's official SOIC-8 land pattern within tolerance.

## Acquisition stays out of the build (N1)
`pindata_iface.py` mirrors `ratings_iface.py`: the default provider reads the
pin table declared in the source (names/pads/roles on `pin` lines). Datasheet/
API/LLM acquisition happens at authoring time and is frozen into the source —
`ohm build` and `ohm gen-package` never fetch.
