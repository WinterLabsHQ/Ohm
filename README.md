# Ohm

**Write your circuit as code. The compiler catches the mistakes that kill a
first spin — before you order boards.**

You describe the circuit in a text file: the parts, what connects to what, and
what you expect (this is a 3.3 V rail, this is an I2C bus, this pin sinks
20 mA). Ohm checks the design against that and emits a KiCad project, a
netlist, and constraints for routers. Same input, same output bytes, every
time.

```
use std.interfaces
use std.passives
use std.discrete

module LedBar:
    pwr = io(Power)
    vcc = config(Voltage, default=3.3V)
    leds = 8 * LED()
    rs = 8 * Resistor(value=(vcc - 2V) / 10mA)   # works out to 130ohm at compile time
    for i in 0..7:
        pwr.v ~ rs[i].P1
        rs[i].P2 ~ leds[i].A
        leds[i].K ~ pwr.gnd
```

`ohm build led_bar.ohm` gives you a KiCad schematic and board, a netlist, a
`.zen` file, and a constraints JSON.

Other CAD tools too: `--emit altium` writes real Altium `.PcbLib`/`.SchLib`
libraries, `--emit allegro` writes the three files Cadence Allegro imports, and
`--emit eagle` writes an Eagle `.lbr`. All of them get their land patterns from
the same internal package model, so they can't disagree about a footprint.

## Why

Most boards need about three spins. A large share of those respins are caused
by things you could have caught before layout — and the worst of them **pass
DRC and fail on the bench**: a missing pull-up, a floating gate, no decoupling,
an unterminated bus, 5 V driven into a 3.3 V pin. A normal ERC can't see any of
that, because it doesn't know what the net is *for*. Ohm does, because you told
it:

| Checks | Examples |
|---|---|
| Structural (S) | pins connected to nothing, footprint/pad mismatches |
| Intent (I) | SDA and SCL swapped, two outputs driving each other, an input nobody drives |
| Units (U) | `3.3V + 10kohm`, and any other unit that doesn't add up |
| Quantities (Q) | the operating point vs. what the pin is actually rated for |
| Kinds (K) | a part declared as an op-amp that isn't wired like one |
| Bring-up (B) | missing I2C pull-ups, decoupling, crystal load caps, bus termination |
| Manufacturing (M) | DFM/DFT lints once you've picked real part numbers |
| Signoff (V) | a **routed** board re-checked against the source: missed nets, floating copper, drift |

Every check can be waived with `waive=` — which leaves a note in the output,
never silence. Every code explains itself: `ohm explain B001`.

**You don't have to start from scratch.** `ohm check design.zen` checks an
existing `.zen` netlist as-is. `ohm convert design.zen` turns it into Ohm source
with TODOs where the intent is missing — fill those in (yourself or with an
agent) and the rest of the checks switch on. `ohm import-symbol` pulls in any
symbol from your installed KiCad libraries.

## Install

Needs Python 3.12+.

**Recommended** — [pipx](https://pipx.pypa.io) puts `ohm` on your PATH with no
venv to activate:

```sh
git clone <this repo> && cd Ohm
pipx install .              # or from anywhere: pipx install /path/to/Ohm
ohm --version
ohm doctor                  # checks Python, install, encoding, KiCad libraries
```

**For development** — editable install in a virtualenv:

```sh
# macOS / Linux
python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]" && .venv/bin/ohm --version
# Windows PowerShell
py -3.12 -m venv .venv; .venv\Scripts\pip install -e ".[dev]"; .venv\Scripts\ohm --version
```

## Quick start

```sh
ohm init myboard    # make a project (ohm.toml, a starter design, docs, lib/)
cd myboard
ohm check           # run every check, emit nothing
ohm build           # write netlist + KiCad + zen + constraints to out/
```

Inside a project, commands find your design through `ohm.toml`. You can also
just point at a file:

```sh
ohm check examples/led_bar.ohm          # fast inner loop: checks only
ohm build examples/led_bar.ohm -o out/  # everything, into out/
ohm fmt examples/led_bar.ohm            # tidy the formatting
ohm explain I005                        # what a message means and how to fix it
ohm signoff board.ohm routed.kicad_pcb  # check a routed board against the source
ohm check design.zen                    # check an existing .zen netlist
ohm convert design.zen -o design.ohm    # .zen -> Ohm source you can annotate
ohm import-symbol Device.kicad_sym R    # KiCad symbol -> Ohm component
ohm build board.ohm --emit altium       # Altium .PcbLib + .SchLib
ohm build board.ohm --emit allegro      # Allegro third-party import (3 .dat files)
ohm build board.ohm --emit eagle        # Eagle .lbr
ohm doctor                              # what's wrong with my environment
ohm lsp                                 # editor support (red squiggles) over stdio
ohm refactor .                          # reorganise an existing folder (dry run; --apply)
```

The language stays small on purpose, but it composes: compile-time functions
(`fn led_r(vcc, vf, i) = (vcc - vf) / i`), arrays and loops driven by config
(`rs = n * Resistor(...)`, `for i in 0..n-1:`), and board variants
(`if endpoint:` / `else:`). All of it is worked out at compile time, so a build
always finishes and always produces the same bytes.

How to write Ohm: [`docs/STYLE.md`](docs/STYLE.md) — written for humans and for
LLM agents. If you're an agent: [`AGENTS.md`](AGENTS.md).

## What's in here

```
ohm/            the compiler: parse -> resolve -> check -> lower -> emit
ohm/std/        standard library (~106 blocks across 17 domains)
ohm/pkggen/     footprint and symbol generation (IPC-7351)
shared/         the constraints format the routing/solver products read
docs/           documentation — start at docs/README.md
  STYLE.md        how to write Ohm
  adr/            why things are built the way they are
  blueprint/      the original design docs (read-only)
  ci/             what has actually been measured, and what has not
examples/       complete boards, kept compiling by CI
tests/          golden netlists, a KiCad round-trip oracle, a signoff
                read-back gate, adversarial suites, determinism gates
tools/          author-time generators (never on the build path)
```

Two of those are worth naming. [`docs/ci/`](docs/ci/) is the compatibility
record: which KiCad versions, which other EDA formats, and — in its own file —
[what is still **not** proven](docs/ci/23_not_proven.md). [`docs/adr/`](docs/adr/)
is why, not what; where an ADR and the blueprint disagree, the ADR is the later
decision and wins.

## Principles

- **Predictable.** The compiler does no network, disk, clock, or solver calls —
  and there are tests that prove it. Same source in, same bytes out, forever.
- **Honest.** No "catches everything" claims. Where Ohm doesn't check something,
  it says so (V005 notices, parts with no declared kind, `# TEMPLATE` blocks).

## Status

Pre-release (v0.1.1). Language and compiler are feature-complete through
signoff — see [`CHANGELOG.md`](CHANGELOG.md).

Ohm writes the KiCad 7 file format, and KiCad reads it forward. That is
**measured, not assumed**: CI runs the emit, library, read-back and plot gates
inside the official KiCad containers at **7.0.11, 8.0.9, 9.0.9 and 10.0.5** —
the whole supported range — with a switch that makes a skipped KiCad test fail
the run rather than pass it quietly. The geometry gate (V007) runs at every one
of them: it drives `kicad-cli pcb drc` where that exists and KiCad's `pcbnew` in
a child process otherwise, so a crash in KiCad's own engine degrades to a warning
instead of taking the verdict with it.

Where KiCad renames a pad between releases — it moved shield and shell pads to
`SH` across 163 footprints in KiCad 10 — `--target-kicad` writes that version's
spelling into the netlist, so one source stays correct across the range.

**About validation, plainly**: everything claimed above is backed by the test
suite — golden netlists, adversarial suites, agreement with KiCad itself — but
no board designed in Ohm has been manufactured yet. Until one comes back from a
fab and boots, treat the bring-up checks as *tested, not field-proven*. That
first board is the next milestone, and this paragraph changes when it happens.

## Licence

Ohm is **proprietary software**. Copyright 2026 WinterLabs, all rights
reserved. It is licensed, not sold, and use requires a written agreement with
WinterLabs — see [`LICENSE`](LICENSE). This repository is not open source and
must not be redistributed.

Your designs are yours: the licence claims no ownership of anything you make
with Ohm.

Routing (Meridian) and the convergence solver are separate products that read
the `shared/` format.

Licensing enquiries: admin@upcheck.in
