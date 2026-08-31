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
| 02 | [Version registry (`ohm/formats.py`)](02_version_registry.md) | 2 | done |
| 03 | [`--target-kicad`, the range, and discovery](03_kicad_target_range.md) | 3, 4, 7 | done |
| 05 | [Pad translation, S012 and the shipped table](05_lower_and_fpdata.md) | 5 | done |
| 06 | [The four KiCad stamps](06_emit_kicad.md) | 6 | done |
| 07 | [DRC/ERC rule-id drift — closing F6](07_drc_erc_rule_drift.md) | 8 | done |
| 08 | [`ohm signoff --target-kicad`](08_signoff.md) | 9 | done |
| 09 | [Read-back hardening](09_readback_hardening.md) | 10 | done |
| 10 | [Container-tag currency](10_container_tag_currency.md) | 11 | done |
| 11 | [`Edge.Cuts` — producible to orderable](11_edge_cuts.md) | 12, 13 | done |
| 12 | [Multi-EDA version policy (ADR-022)](12_multi_eda_version_policy.md) | 14 | done |
| 13 | [STEP AP214 to AP242](13_step_ap242.md) | 15 | done |
| 14 | [Zener `.zen`, both directions](14_zener_zen.md) | 16, 17 | done |
| 15 | [Eagle](15_eagle.md) | 18 | done |
| 16 | [Altium](16_altium.md) | 19 | done |
| 17 | [Allegro, netlist, constraints](17_allegro_netlist_constraints.md) | 20 | done |
| 18 | [`pkggen` and IPC-7351](18_pkggen_ipc7351.md) | 20 | done |
| 19 | [The CI workflow](19_ci_workflow.md) | 21, 23 | done |
| 20 | [Cross-platform determinism](20_cross_platform_determinism.md) | 22 | done |
| 21 | [The compatibility matrix — measured](21_compatibility_matrix.md) | — | done |
| 22 | [What Ohm can do now that it could not](22_latest_capabilities.md) | — | done |
| 23 | [What is still not proven](23_not_proven.md) | — | done |
| 24 | [V007 — the one job nobody had run](24_v007_headless.md) | 24 | done |

## The headline

**Docker was available, so this round stopped being wiring and became
measurement.** The KiCad matrix was executed here, all four versions, with
`OHM_REQUIRE_KICAD=1` so zero skips means zero skips:

| KiCad | Passed | Failed | Skipped |
|---|---|---|---|
| **7.0.11** | 365 | 0 | 0 |
| 8.0.9 | 365 | 0 | 0 |
| 9.0.9 | 365 | 0 | 0 |
| 10.0.5 | 365 | 0 | 0 |

Measuring found **eight** defects that no existing test could see:

1. **The repo's premise about KiCad 7 was false** — five documents said no
   container image existed below 8.0.4; `kicad/kicad:7.0.11` is real, and a
   decision to drop KiCad 7 had already been taken on that premise.
2. **Ohm's own standard library named a footprint KiCad 7 does not have**, on the
   default target, leaving two connector parts' pins unconnected.
3. **S012 called a never-added footprint "removed"** — opposite fixes, and the
   help text sent readers hunting for a removal that never happened.
4. **Schematic uuids depended on the path you typed**, so two checkouts at
   different paths produced different files — and KiCad identifies a sheet by its
   uuid.
5. **`ohm signoff` ran with pad translation off**, so a mismatch in the last gate
   before fabrication was silently unverified rather than reported.
6. **`pcbnew.WriteDRCReport` segfaults on KiCad 10**, taking `ohm signoff` with
   it — exit 139, no verdict at all — and the CI job was pinned to exactly that
   version, and had never run.
7. **The geometry gate could not return `error` anyway.** KiCad 8 dropped the
   `Severity: ` prefix from its DRC report, and the parser matched only the
   KiCad 7 spelling, so since KiCad 8 every finding fell back to `warning`. A
   clearance violation could not fail the gate on any modern KiCad.
8. **A Windows-only test failure** in the UTF-8 invariant, which would have
   red-lit the new Windows leg on its first run.

Two of those are second-order and worth more than the rest. The compatibility
matrix graded V007 `proven at 10.0.5` on the strength of a job that had never
run — the exact failure `OHM_REQUIRE_KICAD` exists to prevent, one level up. And
the "every code has an explanation" gate omitted `V` from its character class, so
the tier that runs last, on a routed board, immediately before fabrication, was
exempt from the check that every code carries a cause and a fix.

---|---|---|---|
| 01 | [Baseline and gate hygiene](01_baseline_and_gates.md) | 1 | done |
| 02 | Version registry (`ohm/formats.py`) | 2 | pending |
| 03 | ~~KiCad floor → 8~~ **withdrawn** — floor stays at 7, 7.0.11 joins the matrix | 3, 7 | see decision 1 |
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

1. ~~**The KiCad floor rises to 8.** No official container image exists below
   8.0.4, so KiCad 7 had no CI leg, no pad data and no pad translation — a
   `--target-kicad 7` build was *unchecked*, not *checked and clean*. ADR-021
   itself recommended this. Every supported version is now measured.~~

   **WITHDRAWN, 2026-08-31 — the premise was false.** `kicad/kicad:7.0.11`
   exists (linux/amd64, 440 MB, pushed 2024-02-23) and is built by
   `Dockerfile.7.0-stable` in the same `kicad-cli-docker` repo as the 8, 9 and 10
   images, cloning `kicad-symbols` and `kicad-footprints` at the matching `7.0`
   branch — so KiCad 7 is containerised *with version-matched libraries*, which
   is the exact property this whole strategy rests on. The claim came from
   reading page one of Docker Hub's tag listing, which is ordered by
   `last_updated` and returns 50 rows; 7.x last moved in February 2024.
   `tools/check_kicad_tags.py` paginates and now runs in CI against every pin.

   **So: the floor stays at 7.** `SUPPORTED_KICAD = ("7", "8", "9", "10")` is
   unchanged, ADR-021's recommendation to raise it is withdrawn there too, and
   the `kicad` matrix is **7.0.11, 8.0.9, 9.0.9, 10.0.5** with no step excluded
   — every `kicad-cli` subcommand the gates use predates KiCad 8. Every
   supported version really is measured now, which is what decision 1 wanted;
   dropping one was never the only way to get it.
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
