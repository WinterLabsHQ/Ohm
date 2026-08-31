# 24 · The V007 geometry gate — the one job nobody had run

Loop 24 · 2026-08-31

The plan for this round included "run the workflow", on the grounds that
`KICAD_CI_IMPLEMENTATION.md` says plainly the YAML had never executed on a
GitHub runner. Every other job's steps had at least been executed locally in the
matching container. **The `v007` job had not** — it is the one job that runs on a
plain `ubuntu-24.04` runner rather than in a KiCad image, so the container
rehearsal never covered it.

Running it found that it does not pass, and then that the reason is much worse
than a failing job.

---

## The measurement

`ohm/drc.py`'s `run_geometry_drc()` imports KiCad's `pcbnew` **in-process** and
calls `pcbnew.WriteDRCReport(board, report, mm, True)`. That exact call, against a
real Ohm-emitted board, using each image's own system Python:

| KiCad | `import pcbnew` | `LoadBoard` | `WriteDRCReport` | Result |
|---|---|---|---|---|
| 7.0.11 | ok | ok | **ok** | 1928-byte report, exit 0 |
| 8.0.9 | ok | ok | **ok** | 1838-byte report, exit 0 |
| 9.0.9 | ok | ok | **ok** | 1878-byte report, exit 0 |
| **10.0.5** | ok | ok | **SEGFAULT** | **exit 139** |
| **10.0.6** (PPA, `ubuntu-24.04`) | ok | ok | **SEGFAULT** | **exit 139** |

On 7, 8 and 9 the call emits a benign wx assertion — `assert ""traits"" failed in
Get(): create wxApp before calling this` — and then works. On KiCad 10 it emits
`assert "m_choices.GetCount() > 0" failed in PROPERTY_ENUM()` and
`assert "process" failed in Pgm()`, then dies with SIGSEGV.

## Why it matters more than a red job

**`ohm signoff` crashes with exit 139 and prints no verdict at all** on any
machine where KiCad 10's `pcbnew` is importable. Not "reports a problem" — prints
*nothing*. The V001–V006 source-fidelity diagnostics are pure and had already
been computed before the geometry gate ran, and they die with the process.

`ohm/cli.py:856-861` already catches `DrcUnavailable` and downgrades it to
`warning: geometry gate skipped`. That safety net is correctly designed and
completely inapplicable: **a segfault cannot be caught.** The process is gone.

And the `v007` CI job is pinned to `ubuntu-24.04` with
`ppa:kicad/kicad-10.0-releases` — **precisely the version where the gate
crashes.** Running its steps gave `1 failed, 16 passed`.

There is a second-order finding in that grading. The compatibility matrix
recorded V007 as `proven` at 10.0.5. That grade did not come from a run of this
job, because this job had never run. It is the exact failure mode
`OHM_REQUIRE_KICAD` exists to prevent, one level up: a cell that looked measured
and was not.

## What the comment got right, and wrong

`ohm/drc.py:103-114` carries this, about the KiCad 9 unit-enum rename:

> KiCad 9 renamed the millimetre unit enum … Ask for whichever this KiCad has
> rather than pinning a version: **signoff's geometry gate is the last check
> before a board is fabricated, and it failing with an AttributeError on a KiCad
> upgrade is exactly when it must not.**

Right about the risk, wrong about the failure mode. The next KiCad did not raise
an `AttributeError` that a defensive `getattr` could absorb. It took the process.

## The alternative, also measured

`kicad-cli pcb drc` **works on 9.0.9 and 10.0.5**, and emits exactly the report
format `parse_drc_report` already parses:

```
** Found 4 DRC violations **
[lib_footprint_issues]: The current configuration does not include the footprint library 'ohm'
    Local override; warning
    @(13.0000 mm, 26.0000 mm): Footprint R1
```

It arrived in **KiCad 8**. KiCad 7 does not have it — and KiCad 7's `pcbnew`
route works, so the two together cover the whole supported range.

This contradicts a decision `ohm/drc.py:1-27` argues for explicitly: in-process
`pcbnew` over `kicad-cli`, on the grounds that "one code path serves every
supported version". **That premise is now false.** The `pcbnew` path does not
serve KiCad 10; it crashes on it. The reasoning is rewritten to match the
measurement rather than deleted, in the house style this repo already uses for
corrections.

## The fix

Two parts, and the first is non-negotiable regardless of the second:

1. **The geometry gate runs out-of-process, always.** A crash in a delegated
   engine must not destroy a verdict that was already computed. A dead child
   becomes `DrcUnavailable`, which the CLI already handles.
2. **`kicad-cli pcb drc` where available, `pcbnew` in a child interpreter
   otherwise** — restoring geometry coverage on KiCad 10 and keeping it on
   KiCad 7.

---

## Still not proven

- The workflow YAML has still never run on a GitHub runner. What each job *does*
  is now measured, including this one; what remains untested is the orchestration
  — dependencies, artifact hand-off, matrix expansion, triggers.
- **The `ubuntu-24.04` pin's justification changes** if the gate no longer
  imports `pcbnew` in-process. The pin existed because a compiled extension had
  to match Ohm's 3.12; that constraint weakens once the import moves to a child.
- The PPA now serves **10.0.6** while the container matrix is pinned at
  **10.0.5** — the two can drift, and the crash reproduced on both.
