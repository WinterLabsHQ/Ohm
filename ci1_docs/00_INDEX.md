# ci1_docs — the EDA compatibility upgrade and its CI

Started 2026-08-31 · branch `ci1-eda-upgrade` · baseline commit `78338e6`

Ohm compiles a hardware description language into files that other people's tools
open. Those tools move. Until this round, exactly one of them — KiCad — had ever
been *measured*, and even that measurement stopped at the emit direction.

This folder is the record of extending the version policy to every target Ohm
claims, raising what is measured to match what is claimed, and building the CI
that keeps re-proving it. One file per part of the repository, each stating four
things: **what that part was, what changed, what now proves it, and what is still
unproven.**

The core architecture is unchanged. The spine —
`load → resolve → annotate_units → lower(once) → 8 checkers → collect_holes → emit`
over `AST → IR-HIGH → IR-TYPED → IR-LOW` — and its three invariants (hermetic,
deterministic, non-mutating analysis) are load-bearing here, not incidental:
several of the decisions below are decided *by* them.

---

## Status

| # | Part | Loop | State |
|---|---|---|---|
| 01 | [Baseline and gate hygiene](01_baseline_and_gates.md) | 1 | done |
| 02 | Version registry (`ohm/formats.py`) | 2 | pending |
| 03 | KiCad floor → 8 (`cli.py`, `project.py`, docs) | 3, 7 | pending |
| 04 | Environment discovery (`kicad_env.py`) | 4 | pending |
| 05 | Pad translation and the shipped table (`lower.py`, S012, `fpdata/`) | 5 | pending |
| 06 | The four KiCad stamps (`emit_kicad.py`, `pkggen/`) | 6 | pending |
| 07 | DRC/ERC rule-id drift (`drc.py`, `emit_kicad_pro.py`) | 8 | pending |
| 08 | `ohm signoff --target-kicad` | 9 | pending |
| 09 | Read-back hardening (`signoff`, `import_symbol`, `provenance`) | 10 | pending |
| 10 | Container-tag currency | 11 | pending |
| 11 | `Edge.Cuts` — producible → orderable | 12, 13 | pending |
| 12 | Multi-EDA version policy (ADR-022) | 14 | pending |
| 13 | STEP AP214 → AP242 | 15 | pending |
| 14 | Zener `.zen`, both directions | 16, 17 | pending |
| 15 | Eagle | 18 | pending |
| 16 | Altium | 19 | pending |
| 17 | Allegro, netlist, constraints | 20 | pending |
| 18 | `pkggen` and IPC-7351 | 20 | pending |
| 19 | The CI workflow | 21, 23, 24 | pending |
| 20 | Cross-platform determinism | 22 | pending |
| 21 | Compatibility matrix | — | pending |
| 22 | Latest capabilities | — | pending |
| 23 | What is still not proven | — | pending |

---

## The four decisions this round rests on

1. **The KiCad floor rises to 8.** No official container image exists below
   8.0.4, so KiCad 7 had no CI leg, no pad data and no pad translation — a
   `--target-kicad 7` build was *unchecked*, not *checked and clean*. ADR-021
   itself recommended this. Every supported version is now measured.
2. **The KiCad version policy extends to every emitted format** (ADR-022):
   *write the oldest stamp a current reader accepts; bump only when a reader
   proves the new one is needed.* Two bumps have evidence; the rest are recorded
   as deliberate and asserted by tests instead of sitting as bare literals.
3. **Boards get an `Edge.Cuts` outline.** The plot gate proved the gerber package
   was *producible*; without a board outline it was never *orderable*.
4. **CI gains a Windows and macOS determinism leg.** Byte-identical output across
   platforms is stated as an invariant on page 8 of the architecture document and
   was measured by nothing.

## How to re-run every gate

```sh
# the local gate — no KiCad needed
.venv/Scripts/python -m pytest -q
.venv/Scripts/python -m ruff check ohm/ shared/ tests/ tools/
.venv/Scripts/python -m mypy

# one KiCad version, exactly what CI runs. OHM_REQUIRE_KICAD is what makes the
# result evidence: without it a job whose container failed to start reports green
# with everything skipped.
OHM_REQUIRE_KICAD=1 OHM_KICAD_VERSION=10.0.5 OHM_KICAD_CLI=tests/kicad_in_docker.sh \
  .venv/bin/python -m pytest -q tests/test_schematic_roundtrip.py \
    tests/test_signoff_readback.py tests/test_kicad_plot.py tests/test_pkggen.py \
    tests/test_verify_footprints.py tests/test_std_*.py
```

## Related, and still authoritative

- `KICAD_COMPATIBILITY.md` — the audit that graded every KiCad claim
  `proven` / `assumed` / `untested` / `broken`. This folder uses the same
  vocabulary so the two are comparable.
- `KICAD_CI_PLAN.md` / `KICAD_CI_IMPLEMENTATION.md` — the feasibility study and
  the record of what measuring found. `ci1_docs/` picks up where they stop.
- `docs/adr/ADR_021_kicad_version_policy.md` — one emitted format, forever, plus
  footprint pad provenance. ADR-022 generalises it.
- `PROGRESS.md` — the execution log. This folder is the round's detail; that file
  stays the chronology.
