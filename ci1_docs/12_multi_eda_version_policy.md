# 12 · Multi-EDA version policy — ADR-022

Loop 14 · 2026-08-31 · commit `a0eb5d1`

---

## What it was

ADR-021 settled the KiCad question: write KiCad 7, never bump, because KiCad reads
forward — four constants, named and tested. Loop 2 (`ohm/formats.py`, commit
`d299c9a`, see `02_version_registry.md`) generalised the *naming* to all fourteen
other stamps Ohm writes and stated the policy once in the module docstring — but a
registry entry with a reason attached is not the same as a stamp somebody checked
against the state of its reader *today*. Ten of the fourteen non-KiCad entries had
never been re-examined since whichever loop first wrote them.

## What changed

`docs/adr/ADR_022_emitted_format_versions.md` generalises ADR-021's rule from
KiCad's four constants to all eight formats Ohm emits on the build path, plus
`.zen` on the interop path:

> **Write the oldest stamp a current reader accepts. Bump only when a reader
> proves the new one is needed.**

Two corollaries decide the cases below: *"current" is a property of the protocol,
not of the file* — a stamp can stop being right even though every reader still
accepts it, which is what moved STEP; and *"proves" means an oracle that fails* —
a reader that opens a file and warns proves nothing, one that yields no answer
when the stamp is wrong is evidence.

ADR-022 reproduces this table:

| Format | Stamp | Reader run against Ohm's output | Status |
|---|---|---|---|
| `.kicad_sch` / `.kicad_pcb` / `.kicad_mod` / `.kicad_sym` | the four KiCad constants | **yes** — `kicad-cli` at 7.0.11, 8.0.9, 9.0.9, 10.0.5 | unchanged (ADR-021) |
| `.step` | `AP242_MANAGED_MODEL_BASED_3D_ENGINEERING_MIM_LF { 1 0 10303 442 1 1 4 }` | **yes** — gmsh/OpenCASCADE meshes the solid, `rel=1e-9` | **CHANGED**, AP214 → AP242 |
| `.zen` | `pcb-version = "0.4"` | **yes** — `pcbc` 0.4.41 builds the file | **NEW** |
| `.lbr` | `6.3` | no — no Eagle anywhere | unchanged |
| `.PcbLib` / `.SchLib` | five Altium-generation strings | no — container only (`olefile`) | unchanged |
| MS-CFB | version `3` | partly — `olefile` reads the container back, not Altium's own reader | unchanged |
| Allegro `pstxprt.dat` | `PST_HDL_CENTRIC_VERSION_0` | no — no Allegro | unchanged |
| Ohm netlist | `"E"` | n/a — Ohm's own format | unchanged |
| `constraints.json` | `"1"` | n/a — the open↔proprietary seam | unchanged |
| Land patterns | IPC-7351**B** | yes, indirectly — KiCad's own library is the comparand | unchanged, and already current |

Of eight formats, exactly two had evidence for a bump — STEP and Zener — and both
were made (`13_step_ap242.md`, `14_zener_zen.md`). The other six are recorded as
deliberately unchanged, each for a different reason (`15_eagle.md` through
`18_pkggen_ipc7351.md`).

### Why "oldest that works" is not conservatism for its own sake

A newer stamp never buys correctness — every reader in the table reads *forward* —
and it always costs something: an older installation's ability to open the file,
or, for the Altium and MS-CFB constants, agreement with what the authoring tool
itself writes, which is the only ground truth a reverse-engineered binary format
has. Bumping every stamp to the newest available number would cost real
compatibility for zero gain in correctness, and for the Altium and Allegro strings
it would replace a fact about what the tool itself writes with a guess.

### The odd row

IPC-7351 is not a stamp Ohm writes into a file — it is a standard Ohm *computes
from* (`ohm/pkggen/packages.py`). `tests/test_formats.py` requires every registered
value to appear in the emitted bytes, and IPC-7351 never does, so it has no
`FormatVersion` entry and cannot get one. It sits in the ADR's table anyway because
the version question was asked about it too — see `18_pkggen_ipc7351.md`.

## What proves it

This loop is a decision record, not a code change, so its own proof is the six
loops it documents: `13_step_ap242.md` through `18_pkggen_ipc7351.md` each carry
the tests for their row. The registry-level guarantee is `tests/test_formats.py`
(19 tests as of this commit): every registered value appears in the bytes its
owner emits, every entry has a non-empty `reason`, and the set of owner modules is
closed — a ninth format cannot be added without a decision.

Of fifteen registry entries (`.PcbLib`/`.SchLib` carry two stamps each under one
owner), exactly one moved (STEP) and one is new (`.zen`). That is not a
coincidence — it is the policy working: the only two stamps re-examined *and found
needing a bump* were the only two with an oracle able to say so.

## Still not proven

- **Four rows are unverifiable here by construction, not by oversight.** Eagle,
  Altium's semantics (as opposed to its container), and Allegro have no headless
  tool anywhere this repo can reach — Eagle because Autodesk shut the licence
  servers, Altium and Allegro because no such tool has ever run here. No amount of
  documentation changes that; ADR-022 says so in as many words rather than padding
  the six "unchanged" rows to look like six proofs.
- **The MS-CFB check is partial, not full.** `olefile` proves the container is a
  real, independent-implementation-readable compound file. It says nothing about
  whether Altium accepts the PCB and schematic records packed inside it.
- **This is a snapshot.** ADR-022 dates itself 2026-08-31; tool versions move, and
  the next survey starts from `ohm/formats.py`'s reasons and this table, not from a
  clean slate.
