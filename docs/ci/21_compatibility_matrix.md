# 21 · The compatibility matrix — measured, 2026-08-31

Grading vocabulary is the audit's own (`history/01_audit_kicad_7_to_10.md`), so the two are
comparable:

- **proven** — a harness in this repo produced the result.
- **assumed** — follows from a policy, but nothing here measures it.
- **broken** — verified not to work.

---

## KiCad — the certification run

Executed on this machine, in the official containers, with
`OHM_REQUIRE_KICAD=1` so a KiCad-shaped skip **fails** the run instead of
quietly passing it. Python 3.12 installed into each container by `uv`, Ohm
installed with `uv pip install -e ".[dev]"`, repo mounted at `/work`.

**This table is the single place the pass count lives.** It moves whenever tests
are added, and it had been copied by hand into six other documents which then
disagreed with each other — 337, 340, 342 and 365 were all in the repo at once,
with the headline table citing this file for a number this file did not have.
Every other document now cites this one instead of carrying its own.

Re-measured **2026-09-02**, running exactly the test list the `kicad` job runs:

| KiCad | Image | KiCad-dependent tests | Failed | **Skipped** |
|---|---|---|---|---|
| **7.0.11** | `kicad/kicad:7.0.11` | **421** | 0 | **0** |
| 8.0.9 | `kicad/kicad:8.0.9` | **421** | 0 | **0** |
| 9.0.9 | `kicad/kicad:9.0.9` | **421** | 0 | **0** |
| 10.0.5 | `kicad/kicad:10.0.5` | **421** | 0 | **0** |

<sub>Earlier snapshots of this same table, kept so the numbers scattered through
this folder can be dated rather than doubted: 337 at loop 20 (2026-08-31), 340
after `Edge.Cuts`, 365 after the read-back hardening.</sub>

**KiCad 7 had never been measured before this run.** The repo asserted five times
that it could not be — see [`10_container_tag_currency.md`](10_container_tag_currency.md).
It now certifies identically to the other three, with **no step excluded at any
version**: every `kicad-cli` subcommand these gates drive (`sch export netlist`,
`sch export pdf`, `pcb export gerbers`, `pcb export drill`, `pcb export pos`,
`sym upgrade`) predates KiCad 8.

Zero skips is the load-bearing column. It also answers a question that was open
when the leg was wired: **the read-back gate does run at KiCad 7.**
`kicad-cli pcb upgrade` is KiCad 10 only, so at 7, 8 and 9
`tests/test_signoff_readback.py` falls through to an out-of-process
`pcbnew.LoadBoard`/`SaveBoard` re-save — and the 7 image ships an importable
`pcbnew` for its system interpreter, which was not known until it ran.

### Row by row

| | KiCad 7.0.11 | KiCad 8.0.9 | KiCad 9.0.9 | KiCad 10.0.5 |
|---|---|---|---|---|
| Emit opens / board parses | proven | proven | proven | proven |
| `kicad-cli sch export netlist` matches Ohm's netlist | proven | proven | proven | proven |
| Gerber + drill + schematic PDF plot | proven | proven | proven | proven |
| Generated `.kicad_mod` / `.kicad_sym` import | proven | proven | proven | proven |
| `ohm signoff` reading a board **KiCad re-saved** | proven | proven | proven | proven |
| `ohm import-symbol` against that version's libraries | proven | proven | proven | proven |
| stdlib footprint refs exist in the library | proven | proven | proven | proven |
| stdlib symbol refs exist in the library | proven | proven | proven | proven |
| `ohm doctor` finds the installed libraries | proven | proven | proven | proven |
| Footprint pad table (`fpdata`) covers this major | proven | proven | proven | proven |
| V007 geometry gate | **proven** | **proven** | **proven** | **proven** |

**V007 was the one remaining gap, and it turned out not to be a gap but a
crash.** Running it found that `pcbnew.WriteDRCReport` **segfaults on KiCad 10**,
taking `ohm signoff` with it, and that since KiCad 8 the report parser could not
read a severity at all — so the gate could never return `error`. Both are fixed:
the gate now delegates to `kicad-cli pcb drc` where available and to `pcbnew` in
a child process otherwise, so it runs at **every** supported version and a crash
degrades to a warning instead of killing the verdict. See
[`24_v007_headless.md`](24_v007_headless.md).

The residual constraint is narrower than it was. `pcbnew` is still a compiled
extension built against its distribution's own Python, so the **in-process** route
was only ever possible where those matched — but nothing imports it in-process
any more, which is what removed the single-version ceiling.

### The pad table is byte-reproducible, proven end to end

Dumping `/usr/share/kicad/footprints` inside 8.0.9, 9.0.9 and 10.0.5 and
rebuilding produced **162 075 bytes, byte-identical** to the committed
`ohm/fpdata/pads.json.gz`. That is what makes the `fpdata` CI job's `git diff`
check signal rather than noise: a difference is a real library change, never
churn. With KiCad 7 added the table is 15 545 footprints × 4 majors, 178 KB —
20 KB for a whole additional supported version.

---

## The other seven formats

No headless version of Eagle, Altium Designer, Allegro or Fusion Electronics
exists on any machine Ohm is developed or tested on, so these rows are graded on
what a harness in *this* repo can produce.

| Format | Version targeted | Grade | What actually proves it |
|---|---|---|---|
| Ohm netlist | `(export (version "E"))` | **proven** | Golden netlists pin the bytes; KiCad re-reads the emitted schematic and the netlists are compared partition-by-partition |
| `.kicad_mod` / `.kicad_sym` | KiCad 7 format | **proven** | Compared against KiCad's own reference footprints at four versions; BGA-49 compared exactly |
| STEP | see [`13_step_ap242.md`](13_step_ap242.md) | **proven** | Meshed with gmsh/OpenCASCADE, which only succeeds on a watertight correctly-oriented shell; volume checked to `rel=1e-9` |
| Altium `.PcbLib` / `.SchLib` | format generation `PCB 6.0` / `Version 5.0`, MS-CFB v3 | **partly proven** | Container read back with `olefile`, an independent MS-CFB implementation; record framing and pad geometry asserted equal to the `.kicad_mod`. **That Altium Designer opens it is not claimed** (ADR-020) |
| Allegro triplet | `PST_HDL_CENTRIC_VERSION_0` | **partly proven** | Asserted node-for-node against Ohm's own netlist. **That Allegro imports it is not claimed** (ADR-020) |
| Eagle `.lbr` | `6.3` XML schema | **partly proven** | Well-formedness, pad geometry agreeing with the `.kicad_mod`, role coverage, determinism. **That Eagle renders it is not claimed** — and Eagle reached EOL on 7 June 2026 |
| Zener `.zen` | see [`14_zener_zen.md`](14_zener_zen.md) | see that file | — |
| Constraints seam | `SCHEMA_VERSION = "1"` | **assumed** | Pure data, consumed by products outside this repo. No reader here, no migration path, never been version 2 |
| IPC-7351 | Revision **B** | **proven, and already current** | C was drafted, built into CAD tools and never published; B (June 2010) is still the published standard. Land patterns validated against KiCad's own library |

---

## Reproducing this

```sh
docker run --rm --user root -v "$PWD:/w:ro" \
  -e OHM_REQUIRE_KICAD=1 -e PYTHONUTF8=1 -e HOME=/tmp \
  kicad/kicad:7.0.11 bash -lc '
    mkdir -p /src && cd /w && tar cf - --exclude=.venv --exclude=.git . | (cd /src && tar xf -)
    cd /src && apt-get update -qq && apt-get install -y -qq curl ca-certificates
    curl -LsSf https://astral.sh/uv/install.sh | sh && export PATH="$HOME/.local/bin:$PATH"
    uv python install 3.12 && uv venv --python 3.12 /tmp/venv
    uv pip install --python /tmp/venv/bin/python -e . "pytest>=8"
    /tmp/venv/bin/python -m pytest -q tests/test_schematic_roundtrip.py …'
```

Substitute `8.0.9`, `9.0.9`, `10.0.5`. `OHM_REQUIRE_KICAD=1` is what makes the
result evidence rather than decoration.

Check the pins are still the newest images for their majors:

```sh
python tools/check_kicad_tags.py 7.0.11 8.0.9 9.0.9 10.0.5
```
