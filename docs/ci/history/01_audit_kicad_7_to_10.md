# KiCad compatibility — the 7 → 10 upgrade, audited

Audit date: 2026-08-26 · Repo at `37fb632` (last code commit `49204e4`, 2026-08-05)

This file is an **audit**, not a change. It reads every document and every code
path that carries a KiCad-version claim, separates what is actually proven from
what is merely stated, and lists the gaps with file:line. No fix is applied
here, and no other file in the repo was touched producing it.

---

## Verdict

**The emit direction is genuinely upgraded and genuinely proven.** Ohm writes
the KiCad 7 file format and relies on KiCad reading forward — the direction
KiCad guarantees — and that is now measured rather than assumed: KiCad 10.0.5
extracts the FMUv2's netlist as 77 nets, partition-identical to KiCad 9.0.9, and
plots its gerbers and schematic PDF. Two real defects were found by running the
new versions (`EDA_UNITS_MILLIMETRES` on 9, the shared `#PWR` reference on 10)
and both are fixed. No format stamp was bumped, which was the correct call.

**Three things around it did not come along.**

1. The **read-back** direction — `ohm signoff` parsing a board that KiCad 8/9/10
   routed and re-saved — has no test at any version. Forward-read protects what
   Ohm writes; it says nothing about what Ohm reads.
2. **Environment discovery** still stops at KiCad 9. On a KiCad-10-only machine
   `ohm doctor` reports no libraries, and `--target-kicad 10` is refused with
   "only KiCad 7 is supported in v1".
3. **Certification is a manual, undated act** on one developer's machine. CI
   installs no KiCad, so nothing in the repo re-proves the 7/9/10 result, and
   nothing records when it was last true.

So: the claim in `README.md:153-154` is honest about the emitted files and
silently over-broad about the toolchain around them.

---

## The policy

Written down here for the first time; it currently exists only in commit prose
and changelog entries (see F7).

> **Ohm emits the KiCad 7 format, on every target, forever — and never bumps the
> stamp.** Newer KiCad reads it; writing a newer stamp would cost backward
> compatibility for no gain.

The stamps, all four of them:

| Artifact | Constant | Value | Where |
|---|---|---|---|
| Schematic `.kicad_sch` | `_SCH_VERSION` | `20230121` | `ohm/emit_kicad.py:49` |
| Board `.kicad_pcb` | `_PCB_VERSION` | `20221018` | `ohm/emit_kicad.py:50` |
| Footprint `.kicad_mod` | `_FP_VERSION` | `20221018` | `ohm/pkggen/footprint.py:9` |
| Symbol `.kicad_sym` | `_SYM_VERSION` | `20220914` | `ohm/pkggen/symbol.py:8` |

Two consequences the docs do not currently draw out:

- **"Target" means the emitted format, not the installed application.** The
  `--target-kicad` flag and `ohm.toml`'s `target = "kicad7"` name the former;
  `README.md`'s "verified … in KiCad 7 through 10.0.5" names the latter. Same
  word, two meanings. This is F1.
- **Every reader Ohm owns must be version-tolerant, because readers face the
  other direction.** `signoff.read_board`, `import_symbol`, `provenance` and the
  test oracle all parse files written by *the user's* KiCad, which is whatever
  version they installed. Three of those four have been made structural; the
  fourth has never been tested against a non-Ohm-written file (F3).

---

## Support matrix

> **Superseded 2026-08-27 — this matrix is now measured.** Every `assumed` cell
> below was resolved by running the gates in the official KiCad containers; see
> [`03_ci_implementation.md`](03_ci_implementation.md) for the result
> (**342 tests, 0 failed, 0 skipped at 8.0.9, 9.0.9 and 10.0.5**) and for the
> five real defects the measuring found — including `ohm signoff` being unable
> to read a KiCad 10 board at all, which is the F3 risk below, realised.
> The original grading is kept unedited, as the record of what was believed
> before anyone checked.

`proven` = a harness in this repo produced the result. `assumed` = follows from
the forward-read policy but nothing here measures it. `broken` = verified not to
work.

`proven` = a harness in this repo produced the result. `assumed` = follows from
the forward-read policy but nothing here measures it. `broken` = verified not to
work.

| | KiCad 7.0.x | KiCad 8 | KiCad 9.0.9 | KiCad 10.0.5 |
|---|---|---|---|---|
| Emit opens in the GUI / CLI | proven | assumed | proven | proven |
| `kicad-cli sch export netlist` matches Ohm's netlist | proven | assumed | proven | proven (77 nets, partition-identical to 9.0.9) |
| Gerber + schematic PDF plot | proven | assumed | proven | proven |
| Generated `.kicad_mod` / `.kicad_sym` import | proven | assumed | proven | assumed |
| V007 geometry gate (`pcbnew.WriteDRCReport`) | proven | assumed | proven (enum rename fixed) | assumed |
| `ohm signoff` reading a board **KiCad re-saved** | untested | untested | untested | untested |
| `ohm import-symbol` against that version's libraries | proven | assumed | proven (`Q_NMOS_GDS` move fixed) | assumed |
| stdlib footprint refs still exist in the library | proven | assumed | proven (`TerminalBlock_bornier-*` fixed) | assumed |
| `ohm doctor` finds the installed libraries | proven | proven | proven | **broken** |
| `--target-kicad <N>` accepted | proven | **refused** | **refused** | **refused** |

The KiCad 8 column is empty of evidence on purpose: it was skipped. Nothing is
known to be wrong with it, and nothing has been run against it.

---

## What the two commits actually changed

### `1a138e2` — `fix(kicad9)`, 2026-08-02

20 failures under KiCad 9.0.9. **Seventeen were serialisation, not geometry** —
no golden land pattern moved, and Ohm's runtime never *reads* a `.kicad_mod`, it
only writes them.

- KiCad 9 pretty-prints and quotes pad ids (`(pad 1 smd roundrect (at …)` became
  `(pad "1" smd roundrect` + newline-indented children). Nine golden comparisons
  anchored a regex to the old layout and failed on `len()` or `KeyError` rather
  than on a tolerance. Replaced by one format-tolerant reader in
  `tests/kicad_golden.py` — deliberately in `tests/`, because a footprint reader
  is scaffolding, not product surface. That placement decision is correct and
  should survive any later refactor.
- KiCad 9 inserts `(class "Default")` between a net's name and its nodes, so the
  round-trip oracle's node group matched zero times and every net came back
  empty.

**Three were real:**

| Breakage | Fix |
|---|---|
| `std.connectors` pointed `TerminalBlock_2/_3` at `TerminalBlock_bornier-*`, which KiCad 9 deleted | moved to the CUI TB007 series — current, and a *named* part, better provenance than the anonymous block |
| `pcbnew.EDA_UNITS_MILLIMETRES` renamed `EDA_UNITS_MM`, breaking V007 | `ohm/drc.py:101-107` asks for whichever this KiCad has and raises `DrcUnavailable` with a readable message if neither exists |
| `Q_NMOS_GDS` moved `Device` → `Transistor_FET` | test updated |

The V007 fix deserves its reasoning recorded: the geometry gate is the last check
before fabrication, and an `AttributeError` on a KiCad upgrade is exactly when it
must not fail. That reasoning generalises and should be the template for every
`pcbnew` attribute Ohm touches.

### `3dd90ca` — `feat(emit)`, 2026-08-05 (KiCad 10 half)

- **No emitter version bump, and none needed.** Recorded as a decision, not an
  omission.
- **The roundtrip READER was broken a second time by a pretty-printer.** KiCad 10
  splits every net across a dozen tab-indented lines. Both times the netlist was
  *correct* and a line-anchored regex matched zero nets, so the gate failed as
  though the circuit were wrong — 7 of 23 cases. Replaced with the balanced-paren
  `_blocks()` reader at `tests/test_schematic_roundtrip.py:61-86`: paren depth is
  the only part of the format that is actually promised. Getting broken twice by
  the same class of change and then fixing the class is the right response.
- **Every power symbol shared one reference.** All emitted as bare `#PWR`, so the
  FMUv2 shipped 191 symbols with one designator between them. KiCad 9 was silent;
  KiCad 10 reports "annotation errors" on every netlist export, and it is right.
  Now `#PWR01`, `#PWR02`, … numbered across the **whole hierarchy**, because
  restarting per sheet only moves the collision up a level
  (`ohm/emit_kicad.py:63-68`, `665-675`). Latent since power symbols were
  introduced; found only by running KiCad 10 — the strongest single argument for
  keeping the certification loop.
- **`$OHM_KICAD_CLI` + `tests/kicad_in_docker.sh`** let the same gate certify a
  KiCad the developer has not installed. Verified on 7, 9.0.9 and 10.0.5.

**Recorded, not fixed:** KiCad 10 reports 39 more `footprint_link_issues` than
KiCad 9 on the same board, and renamed `label_dangling` → `isolated_pin_label`.
Both are library-resolution and rule-naming differences in an environment with no
footprint tables configured, not findings about the design. See F6 — the second
one has a code consequence that was not followed through.

---

## Proven vs assumed

**Claims with a harness behind them.**

| Claim | Harness |
|---|---|
| the emitted schematic is electrically the design, on 7 / 9.0.9 / 10.0.5 | `tests/test_schematic_roundtrip.py` — KiCad extracts the netlist, Ohm compares its own against it pin-for-pin, 5 example boards |
| generated land patterns match KiCad's references | `tests/test_pkggen.py` + `tests/kicad_golden.py`, tolerant of 7/9 serialisation |
| the V007 enum lookup survives a KiCad upgrade | `ohm/drc.py:101-107`, plus `tests/test_drc_gate.py` against real report text |
| emission is byte-identical run to run | the determinism gates in the suite |

**Claims with nothing behind them.**

- *"KiCad 10 will read every Ohm artifact"* — measured for `.kicad_sch` and
  `.kicad_pcb` on five example boards. **Not** measured for `.kicad_mod` /
  `.kicad_sym` on 10, and not measured for `.kicad_pro`.
- *"`ohm signoff` works after you route in KiCad"* — the workflow `AGENTS.md:19-21`
  instructs. Never once tested against a KiCad-written board (F3).
- *KiCad 8* — untouched. Between two proven versions, so low risk, but the docs
  imply a continuous range that was sampled at three points.
- *"895 tests"* (`README.md:137`, `PROGRESS.md:461`) — that count includes the
  KiCad-dependent tests, which **skip** on any machine without KiCad. The number
  a bare CI runner actually passes is lower and is not written down anywhere.

---

## Findings

### F1 — `--target-kicad 10` is refused, while the README says 10 is supported

`ohm/cli.py:556-559` errors with `only KiCad 7 is supported in v1` for anything
but `7`. The flag's own help says `v1 supports: 7` (`ohm/cli.py:439-441`),
`ohm.toml` ships `target = "kicad7"` (`ohm/templates/ohm.toml.tmpl:12`,
`ohm/project.py:20,48`), and two tests lock the refusal in
(`tests/test_fixes_w1_cli.py:13`, `tests/test_project_scaffold.py:149-153`).

**Impact.** A user on KiCad 10 reads "verified … through 10.0.5", types
`--target-kicad 10`, and is told their KiCad is not supported. The message is
technically about the output format and reads as being about their install.

**Suggested fix.** Keep the single emitted format; change what the flag *says*.
Accept `7|8|9|10` and emit the same bytes, with a note that Ohm writes the
KiCad 7 format on purpose because newer KiCad reads it — or rename the concept to
`--kicad-format 7` and reword the error. Either way the two tests are the
specification and must be updated deliberately, not deleted.

### F2 — `ohm doctor` does not know KiCad 10 exists

`_find_kicad()` (`ohm/cli.py:216-236`) probes, in order:
`KICAD9_FOOTPRINT_DIR`, `KICAD8_FOOTPRINT_DIR`, `KICAD7_FOOTPRINT_DIR`,
`KICAD_FOOTPRINT_DIR`, then `/usr/share`, `/usr/local/share`, then
`C:/Program Files/KiCad/{7,8,9}.0/share/kicad/footprints`.

- No `KICAD10_FOOTPRINT_DIR`, which is the variable KiCad 10 sets.
- No `C:/Program Files/KiCad/10.0/…`.
- No macOS candidate at all (`/Applications/KiCad/KiCad.app/Contents/SharedSupport/footprints`).

**Impact.** On a KiCad-10-only machine — the version the README certifies —
`ohm doctor` prints `[WARN] KiCad libraries  not detected` (`ohm/cli.py:271-273`).
The one command whose job is to tell a user their environment is fine reports it
is not, because of the upgrade the README is advertising.

**Suggested fix.** Generate the probe list from a descending version tuple
(`10, 9, 8, 7`) so the next release costs one number, and add the macOS path.
`ohm/cli.py:472`'s `--lib-dir` default (`/usr/share/kicad/symbols`) should come
from the same resolver rather than being a second hard-coded Linux path.

### F3 — the read-back direction is untested at every version

`ohm signoff` is the last gate before fabrication, and its input is a board a
**router or KiCad** produced. `ohm/signoff.py:113-155` reads it structurally and
keys every footprint on `(property "ohm_path" "…")`, taking the value as the
third element of the property form.

`tests/test_signoff.py:35` builds its board with `emit_kicad_pcb(r.low, name)` —
Ohm reading bytes Ohm just wrote. Every signoff test does this.

**Impact.** KiCad 8 rewrote footprint text properties into full field forms with
`(at …)`, `(layer …)`, `(uuid …)`, `(effects …)` children. The value stays in
position 3, so the reader most likely survives — but "most likely" is the entire
finding: the one path where a KiCad version change could silently move Ohm's
custom property is the path with no coverage, in a tool whose output is a
fabrication go/no-go. The same exposure applies to `ohm import-symbol`
(`ohm/import_symbol.py:33-38`) and `verify-pinouts`, both of which parse
libraries written by the user's KiCad.

**Suggested fix.** One fixture per certified version: emit a board, open and
re-save it with the containerised KiCad, commit the re-saved file, and assert
`read_board` recovers the same footprints, pads and nets. This is the single
highest-value gap in the whole upgrade.

### F4 — certification is manual, unautomated and undated

`.github/workflows/ci.yml` runs `ruff`, `mypy` and `pytest -q` on
`ubuntu-latest` with **no KiCad installed**. Every KiCad-dependent test therefore
skips in CI: the roundtrip oracle (its `kicad-cli` guard), the V007 gate, and the
stdlib pinout tests (`tests/conftest.py`). `tests/kicad_in_docker.sh` exists,
works, and is invoked by nobody.

**Impact.** The 7/9.0.9/10.0.5 result is a point-in-time measurement on one
machine. Nothing in the repo says which build it was taken against or when, and
nothing would notice it going stale. A future emitter change can break KiCad 10
and pass CI green.

**Suggested fix.** A separate CI job — not the fast one — that runs
`OHM_KICAD_VERSION=10.0.5 OHM_KICAD_CLI=tests/kicad_in_docker.sh pytest
tests/test_schematic_roundtrip.py` in a matrix over `7`, `9.0.9`, `10.0.5`, on a
schedule or on release tags. Until that exists, this document should carry the
date and commit each version was last certified against, as a table someone
updates by hand.

Note also that `tests/kicad_in_docker.sh` is bash + docker + `id -u`, so the
recertification command cannot be run on the Windows machine this repo is
currently checked out on.

### F5 — the CI test guard is Linux-only and ignores what the CLI already knows

`tests/conftest.py:9-11` skips on `not Path("/usr/share/kicad/footprints").is_dir()`.
It does not consult `KICAD*_FOOTPRINT_DIR`, which `_find_kicad()` already reads.

**Impact.** The eight `test_std_*` pinout suites skip on Windows and macOS even
with KiCad installed — so a developer on the platform where Ohm is being written
never runs the environment-verification tests, and the "895 tests" figure means
something different on every machine.

**Suggested fix.** Have `conftest.py` call `ohm.cli._find_kicad()` (or the
resolver F2 extracts from it) so one definition of "is KiCad here" serves both.

### F6 — a recorded-not-fixed item has an unfollowed code consequence

KiCad 10 renamed the DRC rule `label_dangling` → `isolated_pin_label`. The rename
is recorded in `CHANGELOG.md:78-82` and `PROGRESS.md:417-419` and nowhere else.
`ohm/drc.py`'s `_INFO_RULES` (`:33`, `:37`) classifies rules **by id** —
`unconnected_items`, `lib_footprint_issues` — so an id-keyed classification
silently changes meaning across versions, and the 39 extra
`footprint_link_issues` on KiCad 10 are demoted by a name that happens to still
match today.

**Impact.** Low today (neither renamed id is in the set). Structural: a
version-sensitive lookup table with no version-sensitivity test.

**Suggested fix.** Comment the version each id in `_INFO_RULES` was observed
under, and add later spellings alongside rather than instead — a set is cheap and
an unclassified rule is a false error at the geometry gate.

### F7 — no ADR records the version-compatibility policy

ADRs 002–020 exist under `docs/adr/`. None covers KiCad version compatibility.
The decision — *write KiCad 7, rely on forward read, never bump the stamp, make
every reader structural* — is the most load-bearing compatibility choice in the
project and lives only in two commit messages and a changelog entry.

Separately, `docs/blueprint/OHM_ENGINEERING_BUILD_MANUAL.md:20` cites **ADR-001**
for "own the representation", and there is no `ADR_001_*.md` file. The
blueprint's own instruction at `:357` — "Pin the targeted KiCad file-format major
version (as in P4)" — is satisfied by the four constants above, but nothing links
the instruction to the implementation.

**Suggested fix.** ADR-021, "KiCad file-format version policy": the stamps, the
forward-read rationale, the reader-must-be-structural rule (with both
pretty-printer breaks as its evidence), the certification procedure, and what
would have to change for Ohm to emit a newer format. Confirm separately whether
ADR-001 was ever written or the reference is aspirational.

### F8 — "KiCad 7" is used in prose to mean two different things

Several comments state a KiCad 7 limitation as a present-tense fact about the
user's environment, when it is now only true for users still on 7:

- `ohm/drc.py:6-13` — "`kicad-cli pcb drc`, a subcommand that **does not exist in
  KiCad 7**, the version Ohm pins: the advice was unrunnable, so in practice
  nothing checked geometry at all." The module's rationale is sound and the
  `pcbnew` route is still the better one (it works inside a virtualenv and needs
  no CLI), but "the version Ohm pins" now means "the format Ohm writes", and
  `kicad-cli pcb drc` does exist for users on later KiCad.
- `ohm/emit_kicad_pro.py:84-88` and `docs/adr/ADR_016_hierarchical_sheets.md:101`
  — "KiCad 7 has no headless ERC (`kicad-cli sch` only exports), so ERC is a GUI
  run". Later KiCad ships `kicad-cli sch erc`. The emitted `.kicad_pro` severities
  are still the right thing to ship, but the stated reason no longer holds for
  most users, and a headless ERC gate is now a *possible* addition that the
  comment currently forecloses. (Flagged as a documentation problem: nothing in
  this repo tests `kicad-cli sch erc`, so treat the capability as unverified here
  until it is run.)
- `docs/adr/ADR_006_pkggen_catalog_v1.md:21` and
  `docs/adr/ADR_009_first_spin.md:63` — "KiCad 7's official SOIC-8 land pattern",
  "KiCad 7+ ignores board-file classes". Both are fine as historical records of
  what was compared against; they read as current statements.

**Impact.** Documentation only, but it is the layer this audit exists to fix, and
two of these hide a capability (headless ERC, headless DRC) that later KiCad
offers.

**Suggested fix.** Distinguish consistently between *the format Ohm writes*
(always 7) and *the KiCad a user runs* (7 through 10). ADRs are historical records
and should be left alone except where they state a limitation as current;
`ohm/drc.py` and `ohm/emit_kicad_pro.py` are live comments and should say
"KiCad 7, the oldest supported" rather than "the version Ohm pins".

---

## Documentation consistency — the worklist for the next step

Ordered by how misleading each is. Nothing below is changed by this file.

| # | File | Line(s) | Says | Problem |
|---|---|---|---|---|
| 1 | `ohm/cli.py` | 558 | `only KiCad 7 is supported in v1` | contradicts the README for anyone on 8/9/10 (F1) |
| 2 | `ohm/cli.py` | 439-441 | `--target-kicad … v1 supports: 7` | same, in the help text a user reads first |
| 3 | `ohm/templates/ohm.toml.tmpl` | 12 | `target = "kicad7"  # v1 supports: kicad7` | shipped into every new project by `ohm init` |
| 4 | `ohm/drc.py` | 6-13 | "KiCad 7 … the version Ohm pins" | conflates format with install; hides `kicad-cli pcb drc` on later KiCad (F8) |
| 5 | `ohm/emit_kicad_pro.py` | 84-88 | "KiCad 7 has no headless ERC" | true only for 7 (F8) |
| 6 | `docs/adr/ADR_016_hierarchical_sheets.md` | 101 | the same ERC claim | ADR is historical; add a dated note rather than rewriting |
| 7 | `README.md` | 153-154 | "verified to open and extract identically in KiCad 7 through 10.0.5" | accurate for emit; silently covers signoff read-back and `ohm doctor`, which it should not (F3, F2) |
| 8 | `README.md` | 137 | "895 tests" | machine-dependent — the KiCad-gated tests skip without KiCad |
| 9 | `AGENTS.md` | 19-21 | "run `ohm signoff <src> <routed.kicad_pcb>`" | the workflow with no cross-version coverage (F3) |
| 10 | `PROGRESS.md` | 397-429 | the KiCad 9/10 record | correct and complete; add the certification date/commit table (F4) |
| 11 | `CHANGELOG.md` | 51-82 | the `fix(kicad10)` entry | correct; the `label_dangling` rename has no code follow-through (F6) |
| 12 | `docs/adr/` | — | no ADR for the version policy | F7 |

---

## How to re-certify

The whole procedure, as it exists today:

```sh
OHM_KICAD_VERSION=10.0.5 \
OHM_KICAD_CLI=tests/kicad_in_docker.sh \
  .venv/bin/python -m pytest tests/test_schematic_roundtrip.py -q
```

`tests/kicad_in_docker.sh` pulls `kicad/kicad:$OHM_KICAD_VERSION`, mounts the repo
and `/tmp` at the same absolute paths so the gate's arguments need no rewriting,
and runs `kicad-cli` inside it. The developer's own KiCad is not touched.
Requires bash + docker (so: not runnable from a Windows checkout).

**What that command proves:** for five example boards, KiCad N reads Ohm's
`.kicad_sch` and extracts a netlist whose partition matches Ohm's own,
pin-for-pin, plus the named-net assertions.

**What it does not cover, and would have to be added:**

- the board file — no `.kicad_pcb` is opened by that gate
- `.kicad_mod` / `.kicad_sym` import at version N
- the V007 geometry gate, which needs `pcbnew` in-process rather than `kicad-cli`
- **signoff read-back** — the F3 gap; nothing exercises it at any version
- gerber and PDF plotting, which was done by hand for 10.0.5 and is not a test

**Before this can run in CI** (F4): a job that is allowed to pull a large image
and take minutes, kept off the fast PR path; a version matrix; and a decision
about whether a KiCad regression should fail the build or open an issue.

---

## Summary of recommended work, in order

1. **F3** — a signoff read-back fixture per certified version. Highest value: it
   is the only untested path that ends at a fabricated board.
2. **F1 / F2** — make `--target-kicad` and `_find_kicad()` know about 8/9/10, and
   reword the error so it names the format, not the user's install.
3. **F4** — a scheduled container-KiCad CI job, plus a dated certification table
   in this file until that job exists.
4. **F7** — ADR-021 recording the policy, so the next person does not rediscover
   it from two commit messages.
5. **F5 / F6 / F8** — one resolver for "where is KiCad", version-annotated DRC
   rule ids, and the prose split between *the format Ohm writes* and *the KiCad
   you run*.
