# KiCad 10 compatibility in CI — feasibility and the workflow

Written: 2026-08-26 · Sequel to [`KICAD_COMPATIBILITY.md`](KICAD_COMPATIBILITY.md),
whose finding numbers (F1–F8) are referenced throughout.

That audit graded every compatibility claim `proven` / `assumed` / `untested` /
`broken`. This file answers the next question: **can the `assumed` cells be turned
into proof of work by CI, what has to change first, and what does it cost?**

Matrix scope: **KiCad 8.0.9, 9.0.9, 10.0.5**, via the official container.
Nothing in the repo is changed by this file — it is a specification, not the
workflow itself.

---

## Feasibility verdict

**Feasible, and cheaper than the audit assumed — with two hard ceilings.**

Nine of the eleven matrix rows can be moved from `assumed` to `proven` by CI.
Two of those need no KiCad at all (they are unit tests that were never written).
One — the signoff read-back gap, F3, the audit's highest-value finding — turns out
to be closable with a single `kicad-cli pcb upgrade` call rather than the fixture
surgery it looked like.

The two ceilings are real and neither is worked around by trying harder:

1. **KiCad 7 cannot be containerised.** The official `kicad/kicad` image starts at
   8.0.4. There is no 7.x tag, so `tests/kicad_in_docker.sh` — which the CHANGELOG
   credits with verifying "7, 9.0.9 and 10.0.5" — can only ever have supplied the
   9 and 10 legs. The 7 leg came from a local install, and in CI it stays a dated
   manual claim or the supported floor moves to 8.
2. **`pcbnew` is importable at exactly one version per runner.** It is a compiled
   CPython extension built against the host distro's Python. Ohm requires 3.12
   (`pyproject.toml:6`); the container is Debian 12 bookworm with Python **3.11**;
   Ubuntu 24.04 noble is 3.12. So V007 runs only on a noble runner with KiCad from
   a PPA — one version at a time, not across the matrix.

**Per-row verdict:**

| Audit matrix row | CI mechanism | Verdict |
|---|---|---|
| `kicad-cli sch export netlist` matches Ohm's netlist | today's roundtrip oracle | **provable** — needs only the job |
| Emit opens / board parses | `kicad-cli pcb export pos` | **provable** — after change #3 |
| `.kicad_mod` / `.kicad_sym` import | `pcb export pos` on an embedded fp; `sym upgrade` | **provable** — after change #3 |
| stdlib footprint refs still exist | the container's version-matched libraries | **provable** — after change #2 |
| Gerber + schematic PDF plot | `pcb export gerbers`, `sch export pdf` | **provable** — needs a new test (none exists) |
| **`ohm signoff` reading a KiCad-re-saved board (F3)** | `pcb upgrade` → `ohm signoff` | **provable** — needs a new test; the headline win |
| V007 geometry gate (`pcbnew.WriteDRCReport`) | PPA job on `ubuntu-24.04` | **partly** — one version, not the matrix |
| `ohm doctor` finds the libraries (F2) | unit test on the resolver | **provable with no KiCad at all** |
| `--target-kicad <N>` accepted (F1) | unit test | **provable with no KiCad at all** |
| Anything at KiCad 7 | — | **not feasible** in this design |
| "the drawing reads well" (D001–D004) | — | **not automatable**; stays advisory, by design |

---

## What makes this possible

Six facts established 2026-08-26. All six are claims about software outside this
repo and can change; each is sourced so the next person can re-check rather than
re-derive.

1. **The image tags exist, from 8.0.4 up.** `kicad/kicad` carries `10.0.5`,
   `10.0.4`, `10.0.2`–`10.0.0`, `9.0.9` down to `9.0.0`, and `8.0.9`–`8.0.4`, each
   with `-full` and `-amd64` variants. No 7.x.
   ([hub.docker.com/r/kicad/kicad](https://hub.docker.com/r/kicad/kicad))
2. **The image ships the libraries, version-matched.** The Dockerfile clones
   `kicad-symbols` and `kicad-footprints` at the same `$KICAD_VERSION` branch as
   the build, so `/usr/share/kicad/{symbols,footprints}` is present *and agrees
   with the binary*. Plain tags suffice — the `include_3d` build arg (the `-full`
   variants) only adds packages3D, which Ohm never reads.
   ([Dockerfile.9.0-stable](https://gitlab.com/kicad/packaging/kicad-cli-docker/-/blob/main/Dockerfile.9.0-stable))
   This is what unlocks every `requires_kicad_fp` / `requires_kicad_sym` /
   `KICAD_SOIC8` test **at the version under test**, which a host-side apt install
   of `kicad-footprints` could not do.
3. **`kicad-cli pcb upgrade` exists** — "converts a KiCad board file from a
   previous KiCad board file format to the native format". So the F3 fixture is:
   emit a board → `pcb upgrade` it under KiCad N → hand the re-saved file to
   `ohm signoff` → assert the verdict is unchanged. No `pcbnew`, no GUI, no
   hand-committed binary fixture. `sch upgrade`, `sym upgrade` and `fp upgrade`
   exist alongside it. ([KiCad 10 CLI docs](https://docs.kicad.org/10.0/en/cli/cli.html))
4. **`kicad-cli pcb drc` and `sch erc` arrived in KiCad 8.** This confirms audit
   F8 — `ohm/drc.py:6-13` and `ohm/emit_kicad_pro.py:84-88` describe a KiCad 7
   limitation as a current one — and it means a headless ERC gate is available at
   8/9/10 as a *future* addition. ([KiCad 8 CLI docs](https://docs.kicad.org/8.0/en/cli/cli.html))
5. **`ppa:kicad/kicad-10.0-releases` covers Ubuntu 24.04 noble**, whose system
   Python is 3.12. That is the single combination where `pcbnew`'s compiled module
   matches Ohm's interpreter, so `ohm/drc.py:_import_pcbnew()`'s existing
   `/usr/lib/python3/dist-packages` shim works from a 3.12 venv.
   ([kicad.org Ubuntu install](https://www.kicad.org/download/details/ubuntu/) ·
   [launchpad PPA](https://launchpad.net/~kicad/+archive/ubuntu/kicad-10.0-releases))
6. **The repo already has the redirection seam.** `$OHM_KICAD_CLI`
   (`tests/test_schematic_roundtrip.py:35`) and `tests/kicad_in_docker.sh` were
   built for exactly this. The CI work is mostly finishing what they started —
   see change #3.

---

## What makes it partly impossible

**Python 3.11 in the container.** Debian 12 bookworm. Ohm is
`requires-python = ">=3.12"`, so `pip install -e .` on the image's own interpreter
is refused. The fix is cheap — install CPython 3.12 with `uv`, which the repo
already uses (`uv.lock`) — but it has one consequence that is not fixable: the
`pcbnew` in that image is built for 3.11 and will not import into the 3.12 venv
that runs pytest. Everything subprocess-shaped (`kicad-cli`) is unaffected;
everything in-process (`ohm/drc.py:run_geometry_drc`) is.

**No 7.x image.** Stated above. The alternative — `ppa:kicad/kicad-7.0-releases`
on `ubuntu-22.04` — pins a runner image GitHub will eventually retire, and 22.04's
system Python is 3.10, so it could only ever cover the `kicad-cli` legs, never
V007. Out of scope per the chosen matrix; recorded so the choice is visible.

**One version of V007.** Following from the two above. The `v007` job proves the
geometry gate against KiCad 10 and nothing else. That is still strictly better
than today, where it is proven against nothing.

**Legibility is not automatable.** D001–D004 are advisory by design
(`AGENTS.md:53-58`) and no CI job changes that. Worth saying plainly so the matrix
does not imply a gap that is actually a decision.

---

## Required updates before CI can prove anything

**Nothing below is a workflow change.** These are the repo changes without which
the workflow runs green and proves nothing. Ordered by dependency.

### 1. `OHM_REQUIRE_KICAD=1` — make a skip a failure

Every KiCad guard in the suite is a `pytest.mark.skipif`. There are about 40 of
them. If the container fails to start, the PPA 404s, or a path moves, **the job
reports green with everything skipped** — CI would certify nothing while looking
exactly like CI that certified everything. This is the difference between a proof
of work and a decoration, and it is the one change that must not be skipped.

Implement in `tests/conftest.py`: when `OHM_REQUIRE_KICAD` is set, every guard
raises `pytest.fail(...)` instead of skipping. Set it in the `kicad` and `v007`
jobs only, so a developer's laptop still skips politely.

### 2. One resolver for "where is KiCad"

Five constants hardcode the same Linux path independently:

| File | Line | Constant |
|---|---|---|
| `tests/conftest.py` | 9-11 | `requires_kicad_fp` → `/usr/share/kicad/footprints` |
| `tests/test_provenance.py` | 17 | `KICAD_SYMBOLS` |
| `tests/test_import_symbol.py` | 29 | `SYSLIB` |
| `tests/test_pkggen.py` | 20 | `KICAD_SOIC8` |
| `tests/test_std_analog.py` | 13 | `KICAD_FP` |

`ohm/cli.py:_find_kicad()` (`:216-236`) already does this resolution and none of
them call it. Lift it to a shared helper, teach it `KICAD10_FOOTPRINT_DIR`,
`C:/Program Files/KiCad/10.0/…` and the macOS bundle path, and derive all five
from it. Closes audit **F2** (`ohm doctor` blind to KiCad 10) and **F5**
(guards skip on Windows/macOS even with KiCad installed) as a side effect, and
means the CI job does not have to care where the image puts things.

### 3. Route every `kicad-cli` invocation through one constant

Five call sites hardcode the literal, so `$OHM_KICAD_CLI` redirects one test file
out of six:

```
tests/test_lower_emit.py:49,53      subprocess.run(["kicad-cli", ...])
tests/test_placement.py:206         subprocess.run(["kicad-cli", ...])
tests/test_pkggen.py:49             subprocess.run(["kicad-cli", ...])
tests/test_pkggen.py:408            subprocess.run(["kicad-cli", "sym", "upgrade", ...])
tests/test_fixes_w3_parser.py:123   subprocess.run(["kicad-cli", ...])
```

Their `skipif` decorators call `shutil.which("kicad-cli")` directly too
(`test_drc_gate.py:82`, `test_fixes_w3_parser.py:114`, `test_lower_emit.py:40`,
`test_pkggen.py:39,403`, `test_placement.py:201`). Move `KICAD_CLI` and
`requires_kicad_cli` from `tests/test_schematic_roundtrip.py:35-36` into
`tests/conftest.py` and import them everywhere.

Strictly speaking the `kicad` job below does not *need* this — inside the
container `kicad-cli` is on `PATH`. It is needed for the manual
`OHM_KICAD_CLI=tests/kicad_in_docker.sh` route to cover more than the roundtrip,
and it removes the last reason for two mechanisms to disagree.

### 4. New test — signoff read-back (closes F3)

The audit's highest-value gap. `tests/test_signoff.py:35` builds every board with
`emit_kicad_pcb()` — Ohm reading its own bytes — while the real workflow
(`AGENTS.md:19-21`) is: Ohm emits → **KiCad routes and re-saves** → Ohm reads.

```
emit_kicad_pcb()  →  kicad-cli pcb upgrade  →  ohm.signoff.read_board()
                     (KiCad N rewrites it)     same footprints, pads, nets?
```

Assert the footprint set, the pad→net bindings and the `ohm_path` property survive
the round trip, then run `signoff()` on both and assert identical diagnostics.
This is what would catch KiCad moving Ohm's custom `(property "ohm_path" …)` —
the one thing that would silently break the last gate before fabrication.

### 5. New test — the plot gate

`CHANGELOG.md:56` claims KiCad 10 "plots its gerbers and schematic PDF". No test
makes that claim. `kicad-cli pcb export gerbers` and `sch export pdf` exiting 0
with non-empty output is a cheap, real assertion. It proves the files are
*producible*, not that they are visually correct — say so in the test docstring.

### 6. Two unit tests that need no KiCad

- The resolver from #2 finds a KiCad 10 layout in a `tmp_path` fixture (F2).
- `--target-kicad 10` behaves as decided (F1). Note that
  `tests/test_fixes_w1_cli.py:13` and `tests/test_project_scaffold.py:149-153`
  currently assert the *refusal*; they are the specification and must be changed
  deliberately, alongside whatever F1 is resolved to.

### 7. A machine-written certification record

Today "verified through 10.0.5" is prose with no date and no commit. The `certify`
job should write version, image digest, commit SHA and timestamp as an artifact,
so the claim has provenance. Until that job exists, keep the same table by hand in
`KICAD_COMPATIBILITY.md`.

---

## The workflow

Complete and copy-pasteable, replacing `.github/workflows/ci.yml`. Four jobs.

```yaml
name: ci

# `fast` gates every push and PR. The KiCad jobs pull a ~1.5 GB image and are
# deliberately kept off that path: compatibility with a program we do not ship
# changes on KiCad's release cadence, not on ours, so it is checked weekly, on
# demand, and at every tag — not on every commit.
on:
  push:
    branches: [master]
    tags: ["v*"]
  pull_request:
  schedule:
    - cron: "0 4 * * 1"        # Mondays 04:00 UTC
  workflow_dispatch:

jobs:
  # ------------------------------------------------------------------
  # The gate that must stay fast. Unchanged from the original workflow.
  # ------------------------------------------------------------------
  fast:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -e ".[dev]"
      - run: ruff check ohm/ shared/ tests/
      - run: mypy
      - run: pytest -q

  # ------------------------------------------------------------------
  # Ohm's output, read by real KiCad. Everything inside one image, so the
  # binary and the symbol/footprint libraries are the same version by
  # construction — the failure mode where a KiCad 10 binary is checked
  # against KiCad 8 libraries cannot occur.
  # ------------------------------------------------------------------
  kicad:
    if: github.event_name != 'pull_request'
    runs-on: ubuntu-latest
    container: kicad/kicad:${{ matrix.kicad }}
    strategy:
      fail-fast: false          # one version failing must not hide the others
      matrix:
        kicad: ["8.0.9", "9.0.9", "10.0.5"]
    env:
      # A skipped test is not a passed test. Without this the job is green
      # when KiCad is missing, which is the exact failure this job exists
      # to prevent. See "Required updates" #1.
      OHM_REQUIRE_KICAD: "1"
      # kicad-cli writes its config on first run; /github/home is writable.
      HOME: /github/home
    steps:
      - name: Container prerequisites
        # The image is a kicad-cli runtime, not a build environment: it has
        # neither git (which actions/checkout prefers) nor curl (which the uv
        # installer needs).
        run: |
          apt-get update
          apt-get install -y --no-install-recommends git curl ca-certificates

      - uses: actions/checkout@v4

      - name: Python 3.12
        # The image is Debian 12 bookworm -> Python 3.11, and Ohm is
        # requires-python >= 3.12, so the image interpreter cannot install it.
        # uv fetches a standalone 3.12 and is already this repo's lock tool.
        run: |
          curl -LsSf https://astral.sh/uv/install.sh | sh
          echo "$HOME/.local/bin" >> "$GITHUB_PATH"

      - name: Install Ohm
        run: |
          uv python install 3.12
          uv venv --python 3.12
          uv pip install -e ".[dev]"

      - name: Record what is actually under test
        run: |
          kicad-cli version
          echo "libraries: $(ls -d /usr/share/kicad/footprints | head -1)"

      - name: KiCad-dependent gates
        # The roundtrip oracle is the load-bearing one: KiCad extracts the
        # netlist and Ohm compares it to its own, pin for pin.
        run: |
          .venv/bin/python -m pytest -q \
            tests/test_schematic_roundtrip.py \
            tests/test_pkggen.py \
            tests/test_placement.py \
            tests/test_lower_emit.py \
            tests/test_fixes_w3_parser.py \
            tests/test_import_symbol.py \
            tests/test_provenance.py \
            tests/test_std_*.py

      - name: Read-back and plot gates
        # New tests, per "Required updates" #4 and #5. Split out so a failure
        # here is legible as "KiCad changed what it writes", distinct from
        # "KiCad changed what it reads".
        run: |
          .venv/bin/python -m pytest -q \
            tests/test_signoff_readback.py \
            tests/test_kicad_plot.py

  # ------------------------------------------------------------------
  # V007 — the geometry gate, which imports pcbnew IN-PROCESS. Only runs
  # where the system Python matches Ohm's: ubuntu-24.04 is 3.12 and the
  # KiCad 10 PPA builds pcbnew against it. ubuntu-latest is NOT used here
  # deliberately: when it moves to a newer Ubuntu, the system Python moves
  # with it and pcbnew stops importing.
  # ------------------------------------------------------------------
  v007:
    if: github.event_name != 'pull_request'
    runs-on: ubuntu-24.04
    env:
      OHM_REQUIRE_KICAD: "1"
    steps:
      - uses: actions/checkout@v4

      - name: KiCad 10 from the PPA
        run: |
          sudo add-apt-repository --yes ppa:kicad/kicad-10.0-releases
          sudo apt-get update
          sudo apt-get install -y --install-recommends kicad
          kicad-cli version
          python3 -c "import pcbnew; print('pcbnew', pcbnew.GetBuildVersion())"

      - name: Install Ohm against the system interpreter
        # --system-site-packages so the venv can see KiCad's pcbnew. ohm/drc.py
        # also appends /usr/lib/python3/dist-packages by itself, but relying on
        # both means a failure here is a real failure, not a path accident.
        run: |
          python3 -m venv --system-site-packages .venv
          .venv/bin/pip install -e ".[dev]"

      - name: Geometry gate
        run: |
          .venv/bin/python -m pytest -q \
            tests/test_signoff.py \
            tests/test_drc_gate.py

  # ------------------------------------------------------------------
  # The claim, with provenance. "Verified through 10.0.5" should be a file
  # a machine wrote, not a sentence someone typed.
  # ------------------------------------------------------------------
  certify:
    if: github.event_name != 'pull_request'
    needs: [kicad, v007]
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Write the certification record
        run: |
          {
            echo "commit:    ${GITHUB_SHA}"
            echo "date:      $(date -u +%Y-%m-%dT%H:%M:%SZ)"
            echo "run:       ${GITHUB_SERVER_URL}/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID}"
            echo "kicad:     8.0.9, 9.0.9, 10.0.5 (container, emit + read-back)"
            echo "v007:      10.0.5 (ubuntu-24.04 PPA, pcbnew in-process)"
            echo "not run:   7.0.x — no official container image exists"
          } > kicad-certification.txt
          cat kicad-certification.txt
      - uses: actions/upload-artifact@v4
        with:
          name: kicad-certification
          path: kicad-certification.txt
```

### Notes on specific lines

- **`fail-fast: false`** — three versions are three independent questions. A
  KiCad 8 failure cancelling the KiCad 10 leg would hide the answer that matters
  most.
- **`container:` rather than `docker run`** — `tests/kicad_in_docker.sh` mounts
  host paths and rewrites nothing, which is right for a developer certifying from
  their own machine. In CI the whole job belongs inside the image: no mounts, no
  path rewriting, and the libraries are automatically the matching version.
- **`runs-on: ubuntu-24.04` pinned on `v007`** — the only job whose correctness
  depends on the runner's *system* Python. Using `ubuntu-latest` here would break
  silently the day GitHub moves the label.
- **`apt-get install git`** — `actions/checkout` falls back to a REST tarball
  without git, which works but loses the SHA metadata the `certify` job reports.
- **`if: github.event_name != 'pull_request'`** — a fork PR cannot pull the image
  with the repo's credentials anyway, and a 4-minute image pull on every PR is how
  a CI gate teaches people to ignore it.

---

## Cost

| Job | Trigger | Wall clock | Notes |
|---|---|---|---|
| `fast` | every push + PR | ~1–2 min | unchanged from today |
| `kicad` ×3 | weekly, tag, dispatch | ~5–8 min each, in parallel | dominated by the ~1.5 GB image pull; uv install of 3.12 is ~20 s |
| `v007` | weekly, tag, dispatch | ~4–6 min | PPA `apt-get install kicad` pulls a large dependency tree |
| `certify` | after both | seconds | |

Roughly **25–35 runner-minutes per weekly run**, none of it on the PR path. On a
private repo that is real money; if it matters, drop `schedule` and keep tags +
`workflow_dispatch`, which still gives every release a dated proof.

---

## What is still not proven after all this

Stated plainly, because a CI badge that implies more than it checks is worse than
no badge.

- **KiCad 7.** No image, no job. Either the README's floor moves to 8, or the 7
  claim is footnoted as last verified manually on a stated date.
- **V007 at 8 and 9.** One version only, for the Python-ABI reason above.
- **That a gerber is correct.** The plot gate proves files are produced, not that a
  fab could build from them. Nothing short of a fabricated board proves that — the
  milestone `README.md:156-160` already names honestly.
- **That the schematic is legible.** D001–D004 are advisory by design.
- **KiCad versions that do not exist yet.** The scheduled run is what converts this
  from a one-time certification into a standing one; that is its whole purpose.

---

## Migration order

Landing this in one commit would produce a red CI that tells you nothing. The
sequence that keeps every step diagnosable:

1. **Changes #2 and #3** (one resolver, one `kicad-cli` constant). Pure
   refactor — the suite behaves identically on a developer machine, and this is
   verifiable locally without any CI change.
2. **Change #1** (`OHM_REQUIRE_KICAD`). Still inert: nothing sets it yet.
3. **The `kicad` job**, matrix reduced to `10.0.5` only, with `OHM_REQUIRE_KICAD`
   set. First real signal — and the first time a skipped KiCad test can fail a
   build. Expect to spend the debugging here.
4. **Widen the matrix to 8.0.9 and 9.0.9.** The audit predicts KiCad 8 is fine;
   this is where that stops being a prediction.
5. **Changes #4 and #5** (read-back + plot tests). Added once the job around them
   is trustworthy, so a failure means the test found something rather than the
   harness misfiring.
6. **The `v007` job**, then **`certify`**.
7. **Change #6 and the F1 decision.** Independent of everything above; can be done
   at any point, and needs a product call on what `--target-kicad` should mean.

Step 3 is the one worth budgeting for. Everything after it is incremental.
