# 19 · The CI workflow — eight jobs, and what each one can actually fail on

Loops 21, 23 · 2026-08-31 · `.github/workflows/ci.yml`

---

## The jobs

| Job | Trigger | Runs on | What it proves |
|---|---|---|---|
| `fast` | **every push and PR** | `ubuntu-latest` | ruff, mypy, the full suite. No KiCad, no image pull. |
| `determinism` ×3 | **every push and PR** | ubuntu, windows, macos | Hashes every artifact of every emit kind. |
| `determinism-compare` | after `determinism` | `ubuntu-latest` | The three platforms agree byte for byte. |
| `kicad` ×4 | schedule / tag / dispatch | `container: kicad/kicad:{7.0.11,8.0.9,9.0.9,10.0.5}` | Emit, library, read-back, plot and version-resolution gates. |
| `v007` | schedule / tag / dispatch | `ubuntu-24.04` + KiCad 10 PPA | The geometry gate, which imports `pcbnew` in-process. |
| `fpdata` | after `kicad` | `ubuntu-latest` | The shipped pad table still matches the real libraries. |
| `tags` | schedule / tag / dispatch | `ubuntu-latest` | Every pinned image is still the newest for its major. |
| `certify` | after the above | `ubuntu-latest` | Writes the dated provenance record as an artifact. |

The KiCad jobs pull roughly 1.5 GB each, so they are deliberately off the
per-commit path. `fast` and `determinism` are the two that gate a pull request,
and between them they need no image at all.

## What changed this round

**A fourth KiCad leg, 7.0.11**, with **no step excluded at any version**. That
last part was checked rather than assumed: every `kicad-cli` subcommand these
gates drive — `sch export netlist`, `sch export pdf`, `pcb export gerbers`,
`pcb export drill`, `pcb export pos`, `sym upgrade` — predates KiCad 8. (`pcb drc`
and `sch erc` are KiCad 8+, but nothing in this job calls them; the geometry gate
is `pcbnew` in-process, in `v007`.) A blanket skip would have been unjustified.

**Three test modules that ran nowhere** were wired in.
`tests/test_verify_footprints.py` joins the library gates, since it reads the real
installed library; `test_kicad_env.py` and `test_fpdata.py` get a step of their
own — they pass without KiCad, but they are the code that *decides* which KiCad
the suite talks to and which pad ids it expects, and a resolver that answers
correctly on a bare runner and wrongly in front of a real installation should not
be able to hide. See [`01_baseline_and_gates.md`](01_baseline_and_gates.md).

**The `tags` job** runs `tools/check_kicad_tags.py` against every pin. It is
deliberately **not** in the `certify` chain: a new upstream release must not
invalidate a certification of what actually ran.

**The `determinism` pair** is new — see
[`20_cross_platform_determinism.md`](20_cross_platform_determinism.md).

**`ruff` now covers `tools/`**, which writes the pad table `ohm check` reads.

## The mechanism that makes it honest

`OHM_REQUIRE_KICAD=1` (`tests/conftest.py:120-167`). Every KiCad guard is a
`skipif`, so a job whose container failed to start would report **green with
everything skipped** — certifying nothing while looking exactly like a job that
certified everything.

It closes that from two directions: a missing KiCad aborts *before collection*
with one message (exit code 4), and *after each test* any skip whose reason
mentions kicad or pcbnew becomes a **failure** — including a `pytest.skip()`
inside a test body, which no decorator can see.

One exemption, the `NOT_APPLICABLE` sentinel, for a capability a supported KiCad
genuinely lacks (`kicad-cli pcb upgrade` is KiCad 10 only). That is a fact about
KiCad, not a broken environment, and failing on it would train people to ignore
the switch. **This round found a skip that was missing the sentinel** and would
have failed the certification for exactly the reason the switch exists not to.

## Two runner details that are load-bearing

- **`ubuntu-24.04` is pinned on `v007`, not `ubuntu-latest`.** `pcbnew` is a
  compiled CPython extension built against the distribution's own Python, and
  24.04 is the release whose system Python is 3.12 — which is what Ohm requires.
  When that label moves, `pcbnew` stops importing. The pin must be **revisited**,
  not bumped.
- **Python 3.12 comes from `uv` inside each container.** KiCad 7, 8 and 9 images
  are Debian 12 (Python 3.11); KiCad 10 is Debian 13 (3.13). None of the image
  interpreters will do.

## What was actually run, and what was not

`KICAD_CI_IMPLEMENTATION.md` recorded that the YAML itself had never executed on
a GitHub runner. That is still true — there is no `gh` here and the repo is
private. What *was* done is stronger than reading it:

- **Every KiCad leg's steps executed locally** in the matching container, all
  four versions, `OHM_REQUIRE_KICAD=1`, **337 passed / 0 failed / 0 skipped each**.
- **The `fpdata` job proven end to end**: dumping in 8.0.9, 9.0.9 and 10.0.5 and
  rebuilding reproduced the committed table **byte for byte**, 162 075 bytes.
- **The `determinism` comparison run for real** across Windows and Linux: 175
  artifacts, identical.
- **The `tags` job run** against the live registry: all four pins current.
- **`fast` is the local gate** and runs on every change here.

So each job's *behaviour* is measured; what remains unmeasured is the YAML's
*orchestration* — job dependencies, artifact upload and download between jobs,
matrix expansion, and the `if:` triggers. A syntax or wiring error would still
surface only on the first real run.

## Still not proven

- The workflow has never run on a GitHub runner.
- The **macOS** determinism leg is wired but was not run locally — no macOS
  machine here. The ubuntu/windows pair is measured.
- **V007 runs at one KiCad**, and cannot run at more without a `pcbnew` built
  against Ohm's interpreter at each version.
- No caching. The uv install and every image pull are uncached on each run — a
  cost decision left where it was rather than changed alongside everything else.
