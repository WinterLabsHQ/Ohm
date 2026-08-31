# 23 · What is still not proven

A green matrix that implies more than it checked is worse than no matrix. This is
the standing list, inherited from `KICAD_CI_IMPLEMENTATION.md` and updated by
this round. Entries are removed only when something measures them.

---

## Closed by this round

Recorded first, so the list below is understood as what *remains* rather than as
the whole picture.

| Was not proven | Now |
|---|---|
| **KiCad 7 at all** — "certified by hand or not at all" | A measured matrix leg: `kicad/kicad:7.0.11`, 337 tests, 0 failed, **0 skipped** |
| KiCad 7 footprint pad data | In the shipped table; `fpdata.kicad_majors()` and `SUPPORTED_KICAD` are now the same set |
| **Cross-platform byte identity** — stated as an invariant, measured by nothing | 175 artifacts, all eight emit kinds, Windows against Debian, byte-identical |
| That the gerber package is **orderable** | Boards carry a closed `Edge.Cuts` profile, asserted against the plotted gerber's own coordinates |
| The `fpdata` rebuild being byte-reproducible | Dumped in three containers and rebuilt: 162 075 bytes, identical to what is committed |
| That the matrix pins are current | `tools/check_kicad_tags.py`, run against the live registry and gated in CI |
| DRC/ERC rule-id drift (audit finding **F6**) | `_INFO_RULES` is a version-annotated union; the `.kicad_pro` writes both ERC spellings |

---

## Still open

### The workflow YAML has never run on a GitHub runner

Every job's *behaviour* was executed locally — all four KiCad legs in their
containers, the `fpdata` rebuild end to end, the determinism comparison across
two real platforms, the `tags` check against the live registry. What remains
unmeasured is the YAML's **orchestration**: job dependencies, artifact upload and
download between jobs, matrix expansion, and the `if:` triggers. A wiring error
surfaces only on the first real run. There is no `gh` here and the repo is
private.

### The `v007` geometry gate

Two distinct problems, and the second is new.

**It runs at one KiCad version and cannot run at more.** `pcbnew` is a compiled
CPython extension built against its distribution's own Python; Ohm requires
≥3.12; `ubuntu-24.04` is the only runner whose system Python matches. This is a
fact about how `pcbnew` is packaged, not something to try harder at.

**And running it found that the job does not pass.** See
[`24_v007_headless.md`](24_v007_headless.md). This is the one job that had never
been executed, by anyone, at any point — the earlier "proven at 10.0.5" grading
did not come from a run of this job.

### No external non-KiCad tool has ever opened Ohm's output

No headless Eagle, Altium Designer, Allegro or Fusion Electronics exists on any
machine Ohm is developed or tested on. What *is* proven for those targets is
internal consistency — the Altium container read back by `olefile` (an
independent MS-CFB implementation), record framing and pad geometry asserted
equal to the `.kicad_mod` that KiCad itself validates, the Allegro triplet
asserted node-for-node against Ohm's own netlist, Eagle well-formedness and pad
agreement. ADR-020 is explicit that **the claim stops where the evidence does**:
nothing asserts that Altium Designer opens the libraries or that Allegro imports
the triplet. The first user with a licence closes that gap.

Eagle additionally reached **end of life on 7 June 2026** — Autodesk shut down
the licence servers, so the application will not run at all even for someone who
has it. The successor reader is Fusion Electronics, which reads `.lbr`; that is
also untested.

### That a fabricator would accept the gerber package

A closed board outline is necessary, not sufficient. The plot gate proves the
files are producible and now that the profile is non-empty; nothing proves a fab
would build it. **No Ohm-designed board has come back from a fab and booted** —
the bring-up tier is test-validated, not field-validated, and `README.md` says so.

### That a newer emitted stamp would be wrong

The version policy — write the oldest stamp a current reader accepts — is an
argument, not a measurement. Nobody has emitted a KiCad 10 stamp and confirmed
KiCad 7 refuses it. The cost of being wrong is asymmetric, which is why the
conservative choice stands without the experiment.

### The macOS determinism leg

Wired into CI, not run locally — there is no macOS machine here. The
ubuntu/windows pair is measured.

### Legibility

D001–D004 (`ohm/check_drawing.py`) are advisory **by design**, not by omission.
"The drawing reads well" is not automatable, and saying so plainly is better than
a matrix cell that implies a gap where there is a decision.

### Undeclared intent

Unchanged and structural: an un-kinded part, a pin with no rating and a
`# TEMPLATE` stdlib block are all **honestly unchecked** and say so. Diagnostics
scale with declared intent — which is the design thesis, and also the honest
reason a sparsely annotated design gets a quieter compiler.
