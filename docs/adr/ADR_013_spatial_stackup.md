# ADR-013 — Spatial awareness: stackup, board, and Tier-L layout checks

Date: 2026-07-28 · Status: **proposed** (awaiting owner approval)

## Motivation (research)

Ohm emits a *placed board* but cannot describe physical space. There is no way to
write "this board is 200 × 100 mm and 8 layers." The constraint seam
(`shared/constraints_schema.py`) already carries `impedance`, `length`,
`keepout`, `diffpair`, `netclass` and DSN-shaped placement — but those are
**recorded and forwarded, never checked** (ADR-005: "recorded, never solved").
The compiler has no signal-integrity rules today; `microstrip_z0` (Hammerstad)
exists in the closed-form engine as a callable function bound to no board, no
layer, and no impedance constraint.

The gap matters most for the failure class Ohm already targets. Return-path
discontinuity is the spatial analogue of the bring-up tier: it passes every DRC,
it is invisible to netlist-level checking, and it kills boards on the bench.
The literature is unambiguous about the mechanism — when a signal changes layers
and the reference plane changes net (ground → power), the return current has no
continuous path, loop inductance rises, and unrelated signals begin sharing a
return path, producing crosstalk and EMI. The mitigations are equally crisp:
if the two reference planes share a net, stitching **vias** suffice; if they do
not, a stitching **capacitor** is required near the transition.

**That rule is a graph question over a declared stackup. It needs no field
solver.** So do stackup symmetry (IPC-6012 warpage), reference-plane adjacency,
and layer-role sanity. This is the hermetic core of the milestone.

### The taxonomy this ADR turns on

Every candidate rule was sorted by *what it costs to decide*. This is the
research contribution and it defines the tier boundary:

| Class | Rules | Cost | Owner |
|---|---|---|---|
| **Combinatorial** — pure graph/structure over declared stackup | plane adjacency; reference-net continuity across a layer transition; stitching-cap requirement; stackup symmetry/warpage; layer-role validity; diff-pair co-layer + co-reference | free, exact, hermetic | **Tier-L, compile time** |
| **Closed-form** — published formula, exact Decimal in, bounded error out | Z₀ microstrip/stripline from stackup geometry; propagation delay; critical length vs rise time; IPC-2221 clearance from voltage; IPC-2221/2152 width from current + ΔT | cheap, hermetic, **approximate** | **Tier-L, compile time, reported as an estimate** |
| **Solver-bound** — needs numerical field/circuit solution | crosstalk magnitude; reflections/ringing; insertion loss vs frequency; PDN impedance; EMI radiation; thermal; Z₀ outside the closed-form validity band | expensive, non-hermetic | **Hole → Meridian/solver (N1, N2)** |
| **Post-route geometric** — needs the routed result | actual layer adherence; real stitching structures; 3W spacing; clearance | needs a routed board | **Tier-V signoff / delegated DRC** |

Accuracy bounds that force honesty in class 2: IPC-2141 closed-form is roughly
**5–7 % error for microstrip and 1–2 % for stripline** versus a 2D field solver,
best inside 0.2 ≤ W/H ≤ 2.0, degrading above ~2 GHz and on asymmetric geometry;
Wheeler's formulation is ~10× tighter for microstrip. Therefore Tier-L reports a
**band with the method named**, never a verified impedance. Ground truth remains
the fabricator's field solver against their real stackup — and Tier-L says so.

### Competitive position (ADR-only, per the positioning rule)

An earlier internal claim that stackup-aware checking is unclaimed territory is
**wrong and is corrected here.** JITX supports stackups as first-class input and
ships an SI constraint system: `RoutingStructure` / `DifferentialRoutingStructure`
(microstrip, stripline, edge-coupled), `NeckDown`, `TimingConstraint`,
`TimingDifferenceConstraint` (intra/inter-pair skew), `InsertionLossConstraint`.
tscircuit exposes a layer count (`layers={4}`). The quantitative constraint space
is occupied.

What remains open, and what this ADR claims:

1. **Structural stackup checking.** JITX's constraints describe the *trace*
   (impedance, delay, skew, loss). None of them asks *is this stackup sane* or
   *does every high-speed net have a continuous return path*. Return-path
   topology is the bring-up-tier move applied to space.
2. **Deterministic compile-time proof.** JITX satisfies constraints by
   *continuous routing-engine evaluation* — iterative, coupled to their router,
   reported in an issues list. Tier-L decides at compile time, hermetically,
   byte-identically, before any router runs, with machine-readable diagnostics an
   agent can loop on.
3. **Router-agnostic and open.** Constraints leave through the open seam; any
   router consumes them; Tier-V then verifies whatever came back against source.
4. **Derivation from Tier-Q, which is Ohm-only.** Ohm already declares operating
   points *per pin* (ADR-004). No other tool has that, so no other tool can
   *derive* required clearance and trace width from the design's own declared
   voltages and currents. Elsewhere these are hand-entered net-class numbers that
   silently rot; here they are computed and checked.

## Decision

### Two new declaration forms

`stackup` and `board`, symmetric with `module` / `component` / `kind`:

```
stackup EightLayer:
    thickness = 1.6mm
    layer(L1, signal, copper=35um)
    dielectric(prepreg, h=0.1mm, er=4.2)
    layer(L2, plane, net=gnd, copper=35um)
    dielectric(core,    h=0.2mm, er=4.4)
    layer(L3, signal, copper=17um)
    ...

board Main:
    outline = rect(200mm, 100mm)
    stackup = EightLayer
```

### Everything else reuses existing syntax

Spatial intent is expressed with the **existing `constraint call` grammar**
(ADR-005) plus new kinds in the seam catalog — `layer`, `length_match`,
`stitch`, `reference`:

```
constraint layer(mcu.spi.sck, L3)
constraint impedance(mcu.spi.sck, 50ohm, tol=10%)
for i in 0..7:
    constraint length_match(ddr.dq[i], to=ddr.dq[0], tol=0.5mm)
```

This is deliberate: the repetition machinery (ADR-010 arrays, `for`, loop-var
arithmetic) already makes constraint groups expressible, so **spatial intent in
loops costs no new grammar.** The language delta is two declaration forms; the
rest is catalog plus checks.

### Tier-L diagnostics (compile time, hermetic)

Letter `L` — `S` and `P` are taken in the code namespace. The
`test_p10.py` explanation guard regex `[PRUSICQZKBM]\d{3}` must gain `L`.

- **L001** stackup asymmetry about the centerline (copper weight / dielectric
  height / material) — warpage risk, IPC-6012.
- **L002** signal layer with no adjacent reference plane.
- **L003** net assigned to a layer that is absent, or is not a signal layer.
- **L004** **return-path break**: a net's layer transition changes reference-plane
  *net* with no stitching capacitor declared near the transition. The headline
  check.
- **L005** differential pair split across layers, or referenced to different planes.
- **L006** impedance target unreachable on the assigned layer given the declared
  stackup — closed-form estimate with method and error band stated.
- **L007** declared operating voltage (Tier-Q) demands more clearance than the
  net class allows (IPC-2221).
- **L008** declared operating current (Tier-Q) demands more copper than the net
  class width provides (IPC-2221/2152 + ΔT).
- **L009** rise time makes the net a transmission line (prop delay > ½ t_r) but no
  impedance constraint is declared.
- **L010** placement outside the board outline, or intruding a keepout.
- **L011** *(info)* honest delegation: crosstalk, insertion loss, PDN, EMI and
  thermal are **not** checked — recorded as Holes for the solver layer.

Every code keeps the `waive=` escape and an `ohm explain` entry, per house rule.

### Legal constraint on the reference data

IPC standards are copyrighted. Tier-L implements **published formulas and
physics**, never transcribed tables — the same path already taken for IPC-7351 in
`pkggen`, where geometry is computed and then validated against independent
ground truth (KiCad's own patterns). IPC-2152's empirical charts are *data*, not
formula: v1 uses the IPC-2221 closed form with an explicit note that it is the
conservative older model, rather than embedding chart data we cannot lawfully ship.

## Scope — v1

**In:** the two declaration forms; layer/stitch/reference/length_match kinds in
the seam; L001–L011; closed-form Z₀ from stackup geometry (microstrip + stripline)
wired to the `impedance` constraint; Tier-Q-derived clearance and width; stackup +
outline into the KiCad emitter and the constraints JSON; `ohm explain` entries.

**Deliberately out (stated in output, never silent):** length/skew *verification*
(needs routed geometry → Tier-V, next milestone); neckdown and BGA escape; HDI,
microvias, blind/buried vias; flex and rigid-flex; zone/plane-shape generation;
crosstalk and loss of any kind; frequency-dependent dielectric behaviour; anything
above the closed-form validity band, which becomes a Hole rather than a number.

## Validation plan

Ground truth, not self-consistency — the ADR-011 standard:

- Z₀ closed form checked against **published worked examples and an independent
  calculator**, with the error band asserted, plus a test that the reported band
  *widens* outside the validity range instead of silently lying.
- L004 proven on a real 8-layer stackup fixture: a GND→PWR reference change must
  fire, the same transition with shared-net planes must not, and adding the
  stitching cap must clear it — the mutation discipline used for kinds and STEP.
- Symmetry (L001) checked against known-good and deliberately unbalanced stackups.
- Determinism: stackup and constraints emission byte-identical across builds.
- Hermeticity: the forbidden-import grep must still pass — no solver, no I/O.
- A live-LLM authoring fixture: agent writes an 8-layer 200 × 100 mm board from
  `docs/STYLE.md` and the diagnostics alone, as done for F2/G0.

## Consequences

- The compiler learns physical structure but gains **no** physics engine; the
  hermetic non-negotiable (N1) and Meridian's routing lane (N6) are untouched.
- Tier-Q stops being advisory and starts driving geometry — the first time
  declared operating points constrain the physical board.
- The `shared/` seam gains stackup, so Meridian can route to a real stackup
  rather than inferring one.
- Blueprint docs remain untouched and read-only; this is an ADR-level decision.
- Risk accepted: closed-form impedance is an estimate. Mitigation is disclosure —
  method and error band in the diagnostic text, fabricator field solver named as
  ground truth.
