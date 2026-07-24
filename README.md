# Ohm

**An AI-native, physics-aware hardware description language for electronics — and
a hermetic compiler that catches the mistakes that kill first spins.**

Circuits are declared intent: typed interfaces, component `kind` contracts,
operating points, and bring-up expectations. The more a design declares, the more
the compiler proves — deterministically, at compile time, with machine-readable
diagnostics that both humans and LLM agents iterate on.

```
use std.interfaces
use std.passives
use std.discrete

module LedBar:
    pwr = io(Power)
    vcc = config(Voltage, default=3.3V)
    leds = 8 * LED()
    rs = 8 * Resistor(value=(vcc - 2V) / 10mA)   # folds to 130ohm at compile time
    for i in 0..7:
        pwr.v ~ rs[i].P1
        rs[i].P2 ~ leds[i].A
        leds[i].K ~ pwr.gnd
```

`ohm build led_bar.ohm` emits a KiCad 7 project (schematic, placed board,
net classes), a netlist, a `.zen` interop file, and a constraints JSON for
routers — byte-identical on every run.

## Why

Industry surveys put the average at ~2.9 respins per project; 35–40% of respin
causes are addressable before layout, and the dominant class *passes every DRC
and dies on the bench*: missing pull-ups, floating gate straps, missing
decoupling, unterminated buses, 5V into a 3.3V pin. Generic ERC cannot see
protocol-level intent. Ohm's typed interfaces make these deterministic
compile-time errors:

| Tier | Catches |
|---|---|
| Structural (S) | dangling pins, package/pad mismatches |
| Intent (I) | swapped SDA/SCL, output↔output, undriven inputs |
| Units (U) | `3.3V + 10kohm`, dimension mismatches — exact Decimal, tolerance intervals |
| Quantities (Q) | operating point vs pin ratings (vmax/imax/pmax, thermal) |
| Kinds (K) | a part claiming `component X(OpAmp)` must structurally satisfy the OpAmp contract |
| Bring-up (B) | `expects pullup` on I2C, decoupling, crystal load caps, `when endpoint` bus termination |
| Manufacturing (M) | DFM/DFT lints once the BOM stage starts (MPNs declared) |
| Signoff (V) | a **routed** board re-verified against the source: incomplete nets, floating copper, drift |

Every check has a declared escape (`waive=` → an auditable note, never silence),
and every code has an explanation: `ohm explain B001`.

**Ohm is also an on-ramp from the existing ecosystem.** `ohm check design.zen`
lints a Zener design's flat netlist directly; `ohm convert design.zen` turns it
into annotatable Ohm source whose TODOs are exactly the intent `.zen` cannot
express — fill them (or hand them to your agent) and the full tier stack lights
up. Most of the table above is what other design languages list as explicitly
unchecked; you don't have to start from scratch to get it.

## Install (from source)

Requires Python 3.12+.

```sh
git clone <this repo> && cd MaishaReloaded
python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/ohm --version
```

## Quick start

```sh
ohm check examples/led_bar.ohm          # all checks, no emit (fast inner loop)
ohm build examples/led_bar.ohm -o out/  # emit netlist + KiCad + zen + constraints
ohm fmt examples/led_bar.ohm            # canonical formatting
ohm explain I005                        # what a diagnostic means and how to fix it
ohm signoff board.ohm routed.kicad_pcb  # verify a routed board against the source
ohm check design.zen                    # lint a Zener design's flat netlist
ohm convert design.zen -o design.ohm    # Zener design -> annotatable Ohm source
ohm import-symbol Device.kicad_sym R    # real KiCad symbol -> Ohm component
ohm lsp                                 # stdio LSP for editor red-squiggles
```

The language stays deliberately small but composes: pure compile-time functions
(`fn led_r(vcc, vf, i) = (vcc - vf) / i`), config-driven arrays and loops
(`rs = n * Resistor(...)`, `for i in 0..n-1:`), and compile-time board variants
(`if endpoint:` / `else:`) — everything folds at elaborate, so builds stay
hermetic, terminating, and byte-identical.

Authoring guide (self-contained, written for humans *and* LLM agents):
[`docs/STYLE.md`](docs/STYLE.md). Agents: see [`AGENTS.md`](AGENTS.md).

## Repository layout

```
ohm/            the compiler: parse → resolve → check tiers → lower → emit
ohm/std/        standard library (~106 blocks + honest templates, 17 domains)
ohm/pkggen/     IPC-7351 footprint/symbol generation
shared/         the constraints-schema seam consumed by routing/solver layers
docs/STYLE.md   the authoring guide
docs/adr/       11 architecture decision records
docs/blueprint/ the founding design docs (read-only, canonical)
examples/       complete boards, CI-enforced to compile clean
tests/          608 tests: golden netlists, adversarial suites, determinism gates
```

## Design principles

- **Hermetic**: the compiler does no I/O, network, clock, or solver calls —
  enforced by tests. Same source, same bytes out, forever.
- **Honest**: no "catches all bugs" claims; what isn't checked says so
  (see the V005 delegation notices, un-kinded parts, `# TEMPLATE` blocks).
- **Open language, proprietary computation**: the language, compiler, checks,
  and emitters are this repo. Routing (Meridian) and the convergence solver
  are separate products consuming the `shared/` seam.

## Status & license

Pre-release (v0.1.0). The language and compiler are feature-complete through
signoff (see [`CHANGELOG.md`](CHANGELOG.md)).

**Honesty about validation**: every claim above is enforced by the test suite
(golden netlists, adversarial suites, KiCad ground-truth agreement, live-LLM
authoring fixtures) — but no Ohm-designed board has been manufactured yet.
Until a first spin comes back from a fab and boots, treat the bring-up tier as
*test-validated, not field-validated*. That first board is the next gate, and
this line will change when it happens.

The license is being finalized — **all rights reserved until it lands**.
