# 24 · The V007 geometry gate — the one job nobody had run

Loop 24 · 2026-08-31

The plan for this round included "run the workflow", on the grounds that
`history/03_ci_implementation.md` says plainly the YAML had never executed on a
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

## The fix, and the second defect it uncovered

Two parts, and the first is non-negotiable regardless of the second:

1. **The geometry gate delegates and never imports KiCad.**
   `kicad-cli pcb drc` where one resolves — through `kicad_env.kicad_cli()`, so
   `$OHM_KICAD_CLI` still steers it and there is no second resolver — otherwise
   `pcbnew` in a **child interpreter**, which is KiCad 7's only route since its
   `kicad-cli pcb` has `export` and nothing else.
2. A crashed child becomes `DrcUnavailable`, which `ohm/cli.py` already turns
   into `warning: geometry gate skipped`. `cli.py` needed no change at all.

Two details worth keeping:

- **`sys.executable` is tried LAST.** `pcbnew` is a compiled extension built for
  the *system* Python, and the container matrix runs Ohm on a uv-managed 3.12
  while KiCad 10's `pcbnew` is built for 3.13. The obvious choice is the wrong
  one.
- **A crashed child is refused even when a report file exists.** KiCad 10 dies
  *inside* `WriteDRCReport`, and a truncated report parses fine and
  under-reports violations — which is a worse outcome than no report.

`drc.py` needed `subprocess`, which the N1 import gate forbids across all of
`ohm/`. It is now exempt **by name**, following the precedent `gitutil.py`
already set — and paid for rather than merely asserted: a new AST test proves
nothing but `cli.py` imports `ohm.drc`. `FORBIDDEN` is untouched, and the gate is
net stronger than before.

### The second defect

Measured across all four versions and both routes:

```
KiCad 7       "    Local override; Severity: warning"
KiCad 8/9/10  "    Local override; warning"
```

`parse_drc_report` matched **only the KiCad 7 spelling**, having been written
against a KiCad 7 report. So **since KiCad 8, every finding fell back to the
default `warning`** — on every route, on every modern KiCad. A courtyard overlap
or a clearance violation could not make V007 an error, and nothing said so.

*A gate that cannot return "error" is not a gate.* This one had been unable to
since KiCad 8, in a repo whose CI runs at 8, 9 and 10.

---

## What it does now

`ohm signoff` on a real board:

| KiCad | Route | Exit | Verdict |
|---|---|---|---|
| 7.0.11 | `pcbnew` child | **1** | V002 ×3, V005, **V007 ×2** |
| 8.0.9 | `kicad-cli` | **1** | same shape |
| 9.0.9 | `kicad-cli` | **1** | same shape |
| **10.0.5** | `kicad-cli` | **1** | same shape — *was exit 139, no output* |

The real `v007` runner was reproduced as well — `ubuntu:24.04` with the KiCad 10
PPA — and goes from `1 failed, 16 passed` to **25 passed**.

**V007 now also runs in the container matrix job**, at every version. It was
previously exercised only by the `v007` job, which is to say only on the one
KiCad where it crashed. The route choice is a per-version decision and only a
four-version matrix can check it.

### The `ubuntu-24.04` pin stays, for a different reason

The old justification is gone: it existed because in-process `pcbnew` needed a
system Python matching Ohm's 3.12, and nothing is imported in-process now. The
job installs into a **plain** venv, which is both the honest user environment and
a tripwire — if it ever needs `--system-site-packages` again, someone has moved
the import back in-process.

What still pins it is the PPA, which publishes per Ubuntu series: `ubuntu-latest`
moving to a series KiCad has not built for fails at `apt`. Note also that the PPA
serves **10.0.6** while the container matrix pins **10.0.5**, and the crash
reproduced on both.

---

## Still not proven

- The workflow YAML has still never run on a GitHub runner. Every job's steps
  have now been executed locally, **including this one**, which was the gap this
  loop existed to close.
- `history/01_audit_kicad_7_to_10.md` still grades V007 `proven` at 10.0.5 on evidence that
  never existed. Left standing deliberately — that document carries its own
  banner saying it is the record of what was believed before anyone checked, and
  editing it would destroy the thing it is for.
- Whether `kicad-cli pcb drc` and `pcbnew.WriteDRCReport` agree on *every* rule
  class. They were measured byte-for-byte identical on the boards tested at
  8.0.9, 9.0.9 and 10.0.5; that is a strong sample, not a proof.
