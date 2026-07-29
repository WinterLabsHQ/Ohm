# ADR-014 — Pinout provenance: `pinout_source`, `pinout_digest`, `ohm verify-pinouts`

Date: 2026-07-29 · Status: **accepted**

## Motivation

Ohm's Tier-S proves a pin↔pad map is **total**: every pin has a pad, every pad has
a pin, no duplicates. It has never been able to prove the map is **correct**. A
transposition — two pins swapped, or a whole block shifted by one — keeps the map
total and bijective, so S001, S002 and S003 all pass, and the error arrives on
copper.

This was documented as a known limit rather than a defect. A real board made the
cost concrete. In a Pixhawk-class flight controller authored end-to-end in Ohm,
`ohm check` reported **0 errors, 1 warning** on a design in which:

* the MCU's pads 87–100 were shifted by two, putting **GND on a GPIO**, **3V3 on a
  GPIO**, leaving **BOOT0 floating** and leaving one **VDD/VSS pair unbonded**;
* the IMU's supply pin landed on a no-connect while its **internal regulator
  output was wired onto the shared SPI MISO net**, taking the whole sensor bus
  with it;
* the barometer and gyroscope were each shifted, putting bus outputs onto chip
  selects.

Four of seven hand-authored pinouts were wrong. Every checker tier passed. The
part files carried a `!!! PINOUT NOT INDEPENDENTLY VERIFIED !!!` banner and the
generator that produced them stamped `"confidence": 1.0`.

Critically, **the tool that would have prevented this already shipped**:
`ohm import-symbol` (ADR-012 X1) reads real KiCad symbol libraries and emits
components with real pins. It was never run, and nothing in the language, the
checkers or the docs suggested it should be. The compiler had no way to notice
that a part's pin table was invented.

## Decision

Make the **origin** of a pin↔pad map a first-class, checkable property, split
across the hermeticity boundary (N1) rather than pushed through it.

### 1. `pinout_source` — where the map came from

An optional component attr naming the origin:

```ohm
pinout_source = "kicad:Sensor_Motion:MPU-6000"     # machine-checkable
pinout_source = "datasheet:DS9405 rev 9 Table 8"   # human attestation
```

Malformed values are **S007** (error). An attestation is recorded and surfaced;
it is never silently treated as equivalent to a machine-checked origin.

### 2. `pinout_digest` — freeze the map at the moment it was verified

```ohm
pinout_digest = "sha256:0f8fcd36216097d0"
```

`ohm check` recomputes this from the **declared pins only** — no file read, no
network, no clock. Hermetic by construction, exactly as Tier-Q freezes ratings in
source. A map that drifts after verification is **S008** (error).

The digest covers **pin name → pad only**. Roles, ratings, `must_connect` and every
other attr are the intent layer, which is *expected* to be edited after import;
that is what enrichment steps are for. The pad map is not.

Declaration order is deliberately excluded from the identity: reordering the pin
block is a no-op, moving a pin to a different pad is not.

### 3. `S009` — say so when there is no origin at all

Info-level, on components that declare `mpn=` and carry ≥4 mapped pads. Gating on
`mpn=` mirrors the M-tier's own activation rule: a generic stdlib shape block is a
template, not a claim about silicon, and stays silent. Zero stdlib components trip
it; everything PinForge mints does.

The message states the guarantee plainly — *S003 proves this map is total, never
correct* — and names the two ways to fix it.

### 4. `ohm verify-pinouts` — actually re-read the libraries

An author-time command, alongside `signoff` and `import-symbol`, on the far side
of N1 and **never in the build path**. It diffs every component against real
`.kicad_sym` libraries. Components with an explicit `kicad:` source are checked
against exactly that symbol; components with only an `mpn=` are searched for by
part number, so **a design that never recorded provenance can still be audited** —
which is the case that matters, because it is the one that ships wrong.

Two design constraints learned from the first run against the real board:

* **Candidate matching must be strict.** A loose rule "verified" a DF13 connector
  against `Device:D` and reported a false ok. Matching now requires ≥6 shared
  leading alphanumerics and ≥60% of the shorter name, keeping the best hit.
* **Naming noise must be folded, position never.** Libraries and datasheets
  disagree on supply spelling (`VI`/`IN`, `I_O2_2`/`IO2_B`, `VREF_P`/`VREFP`) far
  more often than on position. Half the findings in the first run were spelling.
  A checker people learn to ignore protects nothing, so aliases and trailing
  disambiguators fold; `PD6`/`VSS` and `CS`/`AUX_CL` never do.

On the design that motivated this ADR the command reports **3 verified, 5
mismatched, 0 false positives**, and the 5 are exactly the 5 broken parts.

## Consequences

* The one failure class Ohm structurally could not see is now visible (S009),
  machine-checkable where a library exists (`verify-pinouts`), and frozen once
  verified (S008).
* `import-symbol` stamps both attrs, so the good path is also the shortest path.
* Hermeticity (N1) is preserved: `check` reads nothing outside the project.
* **Unverifiable is not the same as correct.** Parts with no library coverage
  (here: an FRAM and a magnetometer) still depend on a human reading a datasheet.
  Ohm can make that visible and refuse to imply more; it cannot close it. The
  command says so in its own output rather than reporting a clean exit.

## Alternatives rejected

* **Read KiCad libraries during `ohm check`.** Simplest and strongest, and it
  breaks N1 outright — the build would depend on machine-local library versions,
  destroying reproducibility. Rejected. The digest gives most of the benefit with
  none of the cost.
* **Make S009 a warning or error.** It would fire on every part of every existing
  design at once, which is the M001 avalanche this project already learned from.
  Info, with an exact fix in the `help:` line, and `--strict` for teams that want
  the gate.
* **Digest the roles and ratings too.** Would break on every legitimate enrichment
  pass and train users to regenerate the digest reflexively — which is precisely
  the habit that makes S008 worthless.
