# KiCad CI — what was built, and what it proved

Completed: 2026-08-27 · Implements [`02_ci_feasibility_plan.md`](02_ci_feasibility_plan.md),
closes findings F1–F8 of [`01_audit_kicad_7_to_10.md`](01_audit_kicad_7_to_10.md).

The audit graded every KiCad claim `proven` / `assumed` / `untested` / `broken`.
This is the record of converting those grades into measurements, and of what the
measuring found.

---

## Result

**The matrix is green, and green now means something.**

| KiCad | KiCad-dependent tests | Failed | **Skipped** |
|---|---|---|---|
| 8.0.9 | **342** | 0 | **0** |
| 9.0.9 | **342** | 0 | **0** |
| 10.0.5 | **342** | 0 | **0** |

(A 7.0.11 leg was added on 2026-08-31 and is not in this table: the table is the
record of the 2026-08-27 certification run, not a running total. See the
correction table below for why 7 was absent.)

Zero skips is the load-bearing column. Under `OHM_REQUIRE_KICAD=1` a skipped
KiCad test **fails** the run, so these 342 actually executed against a real KiCad
rather than quietly standing down.

Local gate (no KiCad): **888 passed, 83 skipped**, `ruff` and `mypy` clean, on
uv-managed CPython 3.12. Baseline before this work was 838 passed / 58 skipped.

---

## What running it actually found

Five real defects, none of which any existing test could see. This is the return
on the exercise; the CI is just the machine that keeps finding them.

### 1. `ohm signoff` could not read a KiCad 10 board — at all

The audit's F3, live. KiCad 10 dropped the top-level net table and now names nets
inline:

```
KiCad 7-9   (net 3 "R1_P2")            <- table, referenced by number
            (pad "2" … (net 3 "R1_P2"))
KiCad 10    (pad "2" … (net "R1_P2"))  <- name only, no table at all
```

`ohm/signoff.py` read only the old shape, so **every pad and every track came
back bound to `""`** and signoff reported V001 — "the board does not match the
source" — on every pad of any board KiCad 10 had saved. A parse failure
presenting as a design failure, in the last check before fabrication.

Fixed by `_net_slot` / `_net_name`, which accept both shapes and resolve to a
name once, in `read_board`, so nothing downstream needs to know which KiCad wrote
the file.

### 2. Ohm's standard library bound three shields to pads that do not exist

On KiCad 10, `MicroSD.SHIELD` → pad `11`, `USBConnector_MicroB.SHIELD` → `6`,
`USBConnector_C16.SHIELD` → `S1` — none of which that library has. The shield
would simply not be connected, and nothing in Ohm could see it.

Measuring the scope showed it was not three parts but a class: KiCad 10
**normalised shield and shell pads to `"SH"` across 163 footprints** (`0`, `S1`,
`6`, `5`, `11`, `9` → `SH`). Any design referencing one of those had the same
silent defect. See "The long-term fix" below.

### 3. A golden comparison that had never run, on any machine, at any version

`test_bga49_matches_kicad_golden_exactly` named
`Analog_BGA-49_…_P0.8mm_Ball0.5mm_Pad0.4mm.kicad_mod`. KiCad 8 has that file;
9 and 10 renamed it. So the test skipped everywhere: on 9/10 the file was gone,
and on a developer machine there was no KiCad at all. It now names both spellings
— and passes, which means Ohm's generated BGA-49 land pattern is validated
against KiCad's reference for the first time.

Found purely because `OHM_REQUIRE_KICAD` refuses to let a skip count as a pass.

### 4. A test guarded on the wrong library

`Q_NMOS_GDS` moved `Device` → `Transistor_FET` at KiCad 9. A guard naming
`Transistor_FET` passes on KiCad 8 — that library exists there — but the part is
not in it. Now resolved by asking which installed library *contains* the symbol.

### 5. Two bugs in the new gates themselves

KiCad writes a `.gbrjob` JSON sidecar among the gerbers (not a gerber), and
writes a well-formed hole-less `.drl` for an all-SMD board. Both assertions were
wrong about KiCad's behaviour and were corrected against what it actually does.

---

## The long-term fix: footprint pad provenance

Finding 2 needed more than a patch. Ohm emits a **pad-less stub** for
`package = "Lib:Footprint"` — geometry comes from the user's library at layout
time, and only the pad id travels, in the netlist. So correctness depended on a
library Ohm did not ship, did not read, and could not check. There was **no
diagnostic in the product** for the entire class.

This completes the pattern ADR-014 already established for symbols, one layer
down — recorded in [`docs/adr/ADR_021_kicad_version_policy.md`](../../adr/ADR_021_kicad_version_policy.md).

| Piece | What it does |
|---|---|
| `ohm/fpdata/pads.json.gz` | Pad ids for **15,473 footprints × KiCad 8/9/10**, 158 KB. 84% of footprints are contiguous `1..N` and store as one integer; only 163 differ across versions. |
| `tools/gen_fpdata.py` | Builds it from real installed libraries. Byte-reproducible (sorted keys, `gzip mtime=0`) so a diff is a real library change, never churn. |
| `ohm/fpdata/__init__.py` | Hermetic loader. Abstains — never guesses — on an unknown footprint, an unknown major, or one KiCad removed. |
| **S012** | `ohm check` reports a declared pad the target's footprint does not have, or a footprint KiCad deleted. |
| `ohm.lower._pad_translator` | Writes the **target's spelling** into the netlist. A renamed pad is the same copper under a new name, so `pad=11` emits `11` on KiCad 8/9 and `SH` on KiCad 10 — from one source. |
| `ohm verify-footprints` | The author-time half, re-reading the *real* installed library, exactly as `ohm verify-pinouts` does for symbols. |
| CI `fpdata` job | Regenerates the table from the matrix dumps and fails if it differs from what is committed. |

**This is why `--target-kicad` exists.** The audit found the flag emitted
byte-identical output for every value — it named nothing. Now the *format* is
always KiCad 7 (ADR-021) and the *library vocabulary* follows the target.

Result: the standard library is clean at every target, and the netlist is
**correct** at every target, from one source. The `STD_TARGET_KICAD` workaround
introduced mid-implementation was deleted once translation made it unnecessary.

---

## The mechanism that makes CI honest

`OHM_REQUIRE_KICAD=1` (`tests/conftest.py`). Every KiCad guard was a
`pytest.mark.skipif`, so a job whose container failed to start would report green
with everything skipped — certifying nothing while looking exactly like a job
that certified everything.

It closes the hole from two directions, and both were verified rather than
assumed:

- **Before collection** — a missing KiCad aborts with one clear message.
  Verified: **exit code 4**.
- **After each test** — a KiCad-shaped skip becomes a failure, including
  `pytest.skip()` inside a test body, which no decorator can see. Verified with a
  throwaway test: the KiCad-reason skip failed, an unrelated skip ("no network")
  still skipped.

One exemption, `NOT_APPLICABLE`, for a capability a supported KiCad genuinely
lacks — `kicad-cli pcb upgrade` is KiCad 10 only. That is a fact about KiCad, not
a broken environment, and failing on it would train people to ignore the switch.

**Its first catch was a bug in itself:** the hook checked only the `call` phase,
so `skipif` decorators — most of the guards — slipped through. Found by running a
certification and noticing one test still skipped.

---

## The workflow

`.github/workflows/ci.yml`, **eight** jobs. (This said five, over a table of six, while the YAML had eight: `determinism` and `determinism-compare` were added later and never reached this table.)

| Job | Runs on | What it proves |
|---|---|---|
| `fast` | every push + PR | ruff, mypy, full suite. No KiCad, no image pull. Unchanged. |
| `kicad` ×4 | schedule / tag / dispatch | `container: kicad/kicad:{7.0.11,8.0.9,9.0.9,10.0.5}`, `fail-fast: false`, `OHM_REQUIRE_KICAD=1`. Emit, library, read-back and plot gates. The 7 leg was added 2026-08-31; it runs every step the others do, with nothing excluded and nothing made conditional, because every `kicad-cli` subcommand these gates use — `sch export netlist`, `sch export pdf`, `pcb export pos`, `pcb export gerbers`, `pcb export drill`, `sym upgrade` — predates KiCad 8. |
| `v007` | `ubuntu-24.04` | The geometry gate against a PACKAGED KiCad (PPA), out of process, in a deliberately plain venv. |
| `determinism` ×3 | every push + PR, on ubuntu/windows/macos | Hashes every artifact of every emit kind. |
| `determinism-compare` | after `determinism` | The three platforms agree byte for byte. |
| `fpdata` | after `kicad` | Rebuilds the pad table from the matrix dumps; fails on any diff. |
| `tags` | schedule / tag / dispatch | `tools/check_kicad_tags.py` against every pinned image. Deliberately **not** in the `certify` chain: a KiCad release must not invalidate the certification of what actually ran. |
| `certify` | after all | Writes version + commit + date as an artifact. |

Two runner details that are load-bearing:

- **`ubuntu-24.04` is pinned on `v007`**, not `ubuntu-latest` — but no longer
  for the reason first written here. That reason was the in-process `pcbnew`
  import needing a system Python matching Ohm's 3.12; the import moved out of
  process in loop 24. What remains is that the KiCad 10 PPA publishes per Ubuntu
  series, so `ubuntu-latest` moving to an unbuilt series would fail at apt.
- **Python 3.12 comes from `uv` inside the container.** KiCad 7, 8 and 9 images
  are Debian 12 (Python 3.11); KiCad 10 is Debian 13 (3.13). Ohm requires ≥3.12,
  so none of the image interpreters will do.

---

## Corrections to the plan, forced by measurement

Stated because the plan document asserted them and they were wrong:

| Claimed | Actually |
|---|---|
| KiCad images are Debian 12 / Python 3.11 | True of 8 and 9. **KiCad 10 is Debian 13 / Python 3.13.** |
| `kicad-cli pcb upgrade` exists from KiCad 8 | **KiCad 10 only.** 8 and 9 expose `drc`, `export`, `render`. The read-back gate falls back to `pcbnew` Load/Save, which every version ships — so it covers all three. |
| `pcb upgrade` takes `--output` | It rewrites **in place**; there is no `--output`. |
| The stdlib had 3 bad pad references on every version | An artifact of a regex scan. Parser-based: **8 and 9 are 66/66 correct; only 10 has 3**, and they are the shield renames. |
| No official container image exists below 8.0.4, so KiCad 7 cannot be a CI leg *(added 2026-08-31 — this document repeated the plan's claim rather than checking it, which is what this table exists to catch)* | **`kicad/kicad:7.0.11` exists** — linux/amd64, 440 MB, pushed 2024-02-23 — along with `7.0.10`, `7.0.9`, `7.0.8` and a rolling `7.0`. `Dockerfile.7.0-stable` clones `kicad-symbols`, `kicad-footprints` and `kicad-templates` at the matching `7.0` branch, so the version-matched-libraries property holds at 7 as well, on the same Debian 12 base as 8 and 9. The claim came from page one of Docker Hub's tag listing, which is ordered by `last_updated`, 50 per page; 7.x last moved in Feb 2024. `tools/check_kicad_tags.py` paginates and now runs in CI. |

---

## What is still not proven

Deliberately listed, because a green matrix that implies more than it checked is
worse than no matrix.

- ~~**KiCad 7.** No official container image exists below 8.0.4, so 7 is
  certified by hand or not at all, and the pad table has no 7 data — a KiCad 7
  target is *unchecked*, not *checked and clean*. If that is not acceptable, the
  honest move is to raise the supported floor to 8.~~
  **Corrected 2026-08-31 — see the correction table above.** `kicad/kicad:7.0.11`
  exists, so KiCad 7 is now a matrix leg on the same terms as the other three
  and the floor stays at 7. The pad-table half of the sentence was true only
  because there was no 7 leg to dump from: `tools/gen_fpdata.py dump` now runs at
  7 like everywhere else, and the `fpdata` job rebuilds from every matrix dump
  and fails on any difference — so the 7 leg and a `pads.json.gz` carrying major
  7 must land together, in either order, and the job says so loudly if they do
  not. Wherever the table has no data for a major, `ohm/fpdata/__init__.py`
  abstains and S012 reports *unchecked* rather than clean; that rule never
  depended on which majors happened to be in it.
- ~~**V007 at 8 and 9.** `pcbnew` is importable only where the system Python
  matches Ohm's, so the geometry gate runs at one version.~~ **Closed:** the
  constraint was the in-process import, not the ABI. The gate runs out of process
  at all four versions. What is still assumed is that its two routes —
  `kicad-cli pcb drc` and a `pcbnew` child — agree on every rule class.
- **That a gerber is manufacturable.** The plot gate proves the files are
  *producible*. ~~Separately: **Ohm's boards carry no `Edge.Cuts` geometry** — the
  layer is declared, no outline is drawn — so the gerber package is not orderable
  as-is.~~ **Closed:** the outline is drawn from the placed courtyards, and M012
  reports a design Ohm cannot derive one from.
- **The workflow YAML running on GitHub.** Every job's *steps* were executed
  locally in the same containers, and the `fpdata` job was proven end-to-end
  (dumping in all four containers rebuilds the committed table byte-for-byte).
  The YAML itself has not run on a GitHub runner — there is no `gh` here.
- **That any of this survives a fabricated board.** Unchanged, and `README.md`
  says so.

---

## Files

**New** — `ohm/kicad_env.py`, `ohm/fpdata/{__init__.py,pads.json.gz}`,
`ohm/verify_footprints.py`, `tools/gen_fpdata.py`,
`docs/adr/ADR_021_kicad_version_policy.md`,
`tests/{test_kicad_env,test_fpdata,test_signoff_readback,test_kicad_plot,test_verify_footprints}.py`.

**Changed** — `ohm/{cli,driver,lower,signoff,check_structural,diagnostics,drc,emit_kicad,emit_kicad_pro,project}.py`,
`tests/conftest.py` and the guard call sites, `.github/workflows/ci.yml`,
`tests/kicad_in_docker.sh`, `pyproject.toml`, `MANIFEST.in`, `AGENTS.md`,
`docs/adr/ADR_016_hierarchical_sheets.md`.

## Reproducing the certification

```sh
# one version, exactly what CI runs
docker run --rm --user root -v "$PWD:/w:ro" \
  -e OHM_REQUIRE_KICAD=1 -e PYTHONUTF8=1 -e HOME=/tmp \
  kicad/kicad:10.0.5 bash -lc '
    mkdir -p /src && cd /w && tar cf - --exclude=.venv . | (cd /src && tar xf -)
    cd /src && apt-get update -qq && apt-get install -y -qq curl ca-certificates
    curl -LsSf https://astral.sh/uv/install.sh | sh && export PATH="$HOME/.local/bin:$PATH"
    uv python install 3.12 && uv venv --python 3.12 /tmp/venv
    uv pip install --python /tmp/venv/bin/python -e . "pytest>=8"
    /tmp/venv/bin/python -m pytest -q tests/test_schematic_roundtrip.py …'
```

`OHM_REQUIRE_KICAD=1` is what makes the result evidence rather than decoration.
