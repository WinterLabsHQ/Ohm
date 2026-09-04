# 25 — the second sweep: what the first round's gates could not see

Started 2026-09-02 · branch `ci1-eda-upgrade` · baseline commit `2e9f203`

The previous round ended green: `pytest -q` 1044 passed / 91 skipped (every skip
"KiCad not installed"), `ruff` and `mypy` clean, and all four KiCad legs
certified. So the question this round asks is not *do the gates pass* but **what
can the gates not see** — and it was answered by execution rather than by
reading. Every defect below was reproduced before it was fixed, and every gate
added was run against the pre-fix code to confirm it fails there.

The core architecture is unchanged. No pass was added to, removed from or
reordered in `driver.CHECKERS`; no IR shape moved; `check_drawing` stays outside
the spine because it needs emitted text; no emitted format version moved.

**A pattern worth naming, because it is the round's main finding.** Six of the
eleven defects are the *unfixed half* of something the last round fixed:

| The last round fixed | What it left |
|---|---|
| KiCad uuids derived from the typed path | `.net`, `.zen`, converted `.ohm` and `.kicad_pro` still did it |
| the code-explanation gate omitting `V` | it still omitted `X`, and its docstring claimed it no longer had an allowlist |
| the DRC severity regex missing KiCad 8's spelling | severity was still last-write-wins, so the gate still could not return `error` |
| five inline format literals promoted to the registry | `.kicad_pro`'s three and STEP's second AP242 name were never noticed |
| V008, for a board Ohm cannot READ | the reader's dispatch was still open-ended, and `arc` fell through it silently |
| the read-back gate | `.kicad_pro` sheet uuids matched no schematic Ohm writes |

A fix applied at one call site, when the defect is a *class*, leaves the class.
Most of the new gates below are therefore written to enumerate rather than to
list: discover every emitter that takes a `source`, discover every module that
could carry a stamp, scan the whole alphabet for codes, report any unmodelled
net-bearing form.

---

## The defects, reproduced

### 1. `ohm build` crashed on a cyclic `near`, after writing half its output

`near(ra, rb)` together with `near(rb, ra)` made each part the other's follower,
so neither was placed and `placement()` returned `{}`. `emit_kicad_pcb` indexed
it blind:

```
warning[M012]: no board outline: the design places no parts ...
  File "ohm/emit_kicad.py", line 2239, in emit_kicad_pcb
KeyError: 'ra'
```

`ohm check` returned 0 on that source. The traceback landed *after* the
`.kicad_sch` was written. M012 blamed the design for an emitter failure, and S011
checked an empty placement and found nothing.

`placement()` is on the **analysis** path as well as the emit path, so a partial
map is not merely a crash — it makes two checkers report confidently about parts
they never saw. The fix is therefore totality, not a `.get()` guard: a `.get()`
would emit a board with a part missing and let `ohm signoff` report V001 about
it, which is a design verdict for a compiler bug.

The emitter also contradicted its own comment, which claimed a cycle "is placed
in the flow like anything else" — it was dropped instead. It now is: the seating
loop repeats while it makes progress, and when a pass seats nothing exactly one
cycle member gives up its adjacency preference and flows. One edge broken, not
all of them.

**And the premise the fix was planned on was wrong.** A cycle *reads* as
unsatisfiable and is not: two parts can each sit beside the other. With totality,
`near(ra, rb)` + `near(rb, ra)` places them 2.00 mm apart and satisfies **both**.
So the new **S013** measures the placement instead of pattern-matching the
constraint graph — a graph check would have rejected a design Ohm delivers
correctly.

- **error** when the placer never received the declaration: a second `near` on a
  part that already has one, or a `near` on a part pinned by `fixed`. That is the
  source asking for an adjacency no placement can act on, and erroring stops the
  build before anything is written.
- **warning** when the placer tried and could not fit it. Congestion is a limit
  of Ohm's placer, not a defect in the source.

S013 is silent across `examples/`, `tests/golden/` and `ohm/std/`.

### 2. `ohm signoff` was blind to arcs — a correct board reported as WRONG

`read_board` dispatched on `zone`/`segment`/`via`/`footprint`. KiCad's top-level
`(arc ...)` — what its own router writes in arc mode, and what dragging a track
produces — fell through all four silently, landing in no collection and counted
by none of the structural-sanity counters, so **V008 could not fire**. Measured
on one board, same net, three files:

| board | verdict |
|---|---|
| unrouted | `error[V002] net 'RA_P2' is incomplete: 2 pads in 2 islands` |
| routed, straight segment | clean, exit 0 |
| **routed, arc** | **`error[V002] … incomplete`, exit 1** |

A completely routed board declared wrong in the last gate before fabrication —
the failure mode `ohm/signoff.py` itself calls the worst possible one. And
symmetrically: an arc shorting two nets, or on a net the source never declared,
was invisible to V003 and V004 and reported clean.

An arc is read as its **chord**, and that is exact rather than an approximation:
the connectivity analysis is point-topology, unioning an item's two endpoints and
never consulting the geometry between them, so an arc and a segment with the same
endpoints are already the same object to every V code. Registering `mid` would
give arcs a T-junction rule that segments do not have. Where the bulge swings is
geometry, and geometry belongs to V007.

**The dispatch's silent default is closed too.** Any other top-level form
carrying a `(net …)` is now counted and reported as V008 — "this reader cannot
READ your board", never "your board is WRONG". Through KiCad 10 the net-bearing
forms are exactly `segment`, `arc`, `via` and `zone`; the point is that the
*next* one says so instead of quietly shrinking what the reader can see.

### 3. `ohm verify-pinouts` certified real transpositions

`_same_pin` fell back to any shared underscore token, and KiCad's disambiguating
suffix on a duplicated pin is itself such a token:

```
VDD_1 / GND_1 -> True     SDA_1 / SCL_1 -> True     NC_1 / VDD_1 -> True
VDD_2 / VSS_2 -> True     IN_2  / OUT_2 -> True     VDD   / GND   -> False
```

The finding was dropped and the component reported `ok` — by the one tool whose
stated purpose is to catch a transposition that "passes every checker tier and
arrives on copper". A VDD/VSS transposition is a dead board.

The cover: every existing negative pair has a suffix on at most **one** side, so
the branch was never exercised in the failing direction. Tokens are now taken
after the disambiguator is stripped, single characters never carry a match, and
all six documented tolerances still hold.

### 4. The V007 geometry gate demoted whole rule classes

Severity was stored once per **rule** with last-write-wins, while KiCad prints one
severity line per **violation**. `--severity-all` is passed on purpose, so
exclusions appear in the report. Two real courtyard overlaps followed by one
exclusion:

```
before:  warning | 3x courtyards_overlap     <- gate passes a defective board, exit 0
after:   error   | 2x courtyards_overlap     <- the real defects, counted correctly
         info    | 1x courtyards_overlap     <- the excluded one, recorded not dropped
```

The count included the excluded item, and `message`/`where` came from the *first*
violation while `severity` came from the *last*, so one diagnostic could describe
two items. The regex fix of the previous round did not touch any of this: the
demotion is in the aggregation, so it survived on every version and on both
report spellings. Grouping by `(rule, severity)` fixes all three at once.
`_severity` is now an explicit map — it had also been silently *promoting*
KiCad's own `info` to a warning.

### 5. The path the caller typed reached the emitted bytes

`_source_id` was written for exactly this and applied only to the KiCad schematic
and board. Measured on one design named four ways:

| | before | after |
|---|---|---|
| `.kicad_sch`, `.kicad_pcb` | identical | identical |
| `.kicad_pro` | **3 different hashes** | identical |
| `.net`, `.zen`, converted `.ohm` | **differ** | identical |

Two consequences: the determinism invariant was broken for four of the eight emit
kinds, and a fab/BOM artifact shipped an absolute path carrying the drive letter
and the developer's user name.

**Why no gate could see it.** `tools/emit_manifest.py` — the cross-platform
determinism gate — passed a repo-relative path *deliberately*, and its docstring
explained that it did so to stop the harness reporting a difference that was
really about naming. That made it structurally unable to observe the defect it
was describing. It now passes the **absolute** path on purpose: 175 artifacts,
generated from two differently-named checkouts on this machine, compare
identical.

### 6. `.kicad_pro` listed sheets that were never written

Two bugs in three lines. The uuids came from the raw `source` (defect 5), and the
hierarchy was decided **twice**: `emit_kicad_sheets` decides by *fit*, while
`emit_kicad_pro` asked the cheaper `len(sheet_groups(low)) > 1`. On
`tests/golden/i2c_bus.ohm` — the only multi-module design in the repo, and one
that fits — a single flat `i2c_bus.kicad_sch` was written while the project
listed three sheets naming two files that do not exist, and **none** of the three
uuids appeared in the emitted schematic. The file's own comment says "KiCad will
not open a hierarchy it cannot find in the project".

One predicate now: `sheet_plan()`. `emit_kicad_sheets` writes files from it and
`emit_kicad_pro` lists them from it.

### 7–9. Three gates that could not fail

- **The code-explanation gate still had an allowlist.** `X` was outside
  `[PRUSICQZKBMVD]`, so `X000` was uncovered — and the docstring claimed the
  class was derived from `EXPLAIN`, which was untrue. Fixing a hand-kept
  allowlist by hand leaves a hand-kept allowlist. The class is now the whole
  alphabet (measured: no false positives anywhere in `ohm/`), the check runs in
  **both** directions so an explanation for a deleted code is caught too, and the
  anti-vacuity floor is tied to `len(EXPLAIN)` rather than a literal `> 50`
  against a real 113.
- **The registry's owner gate could not see an unregistered emitter.**
  `{entry.owner for entry in REGISTRY} == EXPECTED_OWNERS` compares the registry
  to a hand-written list; an emitter that never registered appears in neither
  side. `ohm/emit_kicad_pro.py`'s three KiCad schema versions and
  `ohm/pkggen/step.py`'s second AP242 name were exactly that — and `step.py`'s
  own comment says the two AP242 names "ha[ve] to agree … or the file claims one
  protocol in the header and another in the data", which nothing enforced. The
  gate now *discovers* candidates and requires each to be registered or exempt
  **with a stated reason**. Registry 15 → 19.
- **D001–D004 escaped `--strict`, `--json-diagnostics` and the exit code.**
  `check_drawing` ran after `_report` and after the strict gate, printing to
  stderr. `ohm build --strict` printed `warning[D001]` and **exited 0**, against
  a help text promising "exit 1 (and emit nothing) on any warning or worse".
  `ohm/cli.py` now renders every text artifact into memory, runs the drawing gate
  over the rendered sheets, reports once, gates once, and only then writes — so
  the D tier joins the same list as every other tier, and nothing reaches the
  disk until every artifact has rendered.

### 10–11. The workflow would have failed, and then lied

- **`apt-get` as a non-root user.** The `kicad` job's first step installs
  packages inside `container: kicad/kicad:…`, with no `options: --user root`.
  Measured here:

  ```
  docker run --rm             kicad/kicad:10.0.5 id             -> uid=1000(kicad)
  docker run --rm             kicad/kicad:10.0.5 apt-get update -> exit 100
  docker run --rm --user root kicad/kicad:10.0.5 apt-get update -> exit 0
  ```

  Every local reproduction in this repo passes root explicitly, so the one path
  never exercised was the one that assumes it. A failure there takes down all
  four matrix legs, `fpdata` and `certify` together.
- **The `certify` job wrote a false record.** It said `v007: 10.0.5 — pcbnew
  in-process` while `ohm/drc.py` runs the engine out of process always and the
  job installs a plain venv as a tripwire against that; and it said the geometry
  gate did "not run" at 7, 8 and 9, contradicted by a step named "Geometry gate —
  V007, at every version" 250 lines earlier in the same file. A record a machine
  wrote is worth more than a sentence someone typed only if the machine writes
  what actually ran.

---

## What now proves it

New or rewritten gates, each confirmed to **fail against the pre-fix code**:

| Gate | Catches |
|---|---|
| `test_placement_places_every_part` (11 designs) | `placement()` not total over `low.parts` |
| `test_a_near_cycle_*` (3) | the crash, the placement, the emitted board |
| `test_s013_*` (5) | dropped `near` intent, and silence when it was honoured |
| `test_an_arc_*` (3) + `test_unmodelled_net_bearing_copper_is_v008` | arc copper, and the next form nobody has seen |
| `test_same_pin_is_not_fooled_by_kicads_duplicate_pin_suffix` (5) | the suffix-on-both-sides direction |
| `test_a_mixed_severity_class_reports_its_worst` (2) + 3 more | a rule class that cannot return `error` |
| `test_no_emitter_lets_the_typed_path_reach_the_bytes` (8 × 2) | a path leak in any emitter |
| `test_the_table_covers_every_emitter_that_takes_a_source` | a NEW emitter with a `source`, by signature |
| `test_the_project_lists_only_sheets_that_were_written` | project/schematic disagreement |
| `test_every_emitter_is_either_registered_or_exempt_with_a_reason` | an emitter that never registered |
| `test_the_step_protocol_agrees_with_the_file_schema` | the two AP242 names diverging |
| `test_strict_fails_on_a_drawing_warning_and_writes_nothing` + 3 | the D tier escaping the exit code |
| `tests/test_ci_workflow.py` (5) | a container without root, a false certification record, a job naming a file nobody wrote |

The discovery gate earned its keep immediately: asked for every emitter taking a
`source`, it named three the plan had not — `emit_ohm`, `emit_allegro` and
`title_block` — two of which now normalise defensively and one of which is exempt
with its reason recorded.

---

## Still not proven

- **The workflow YAML has still never run on a GitHub runner.** Its
  orchestration — job dependencies, artifact upload/download, matrix expansion,
  `if:` triggers — remains unmeasured. What changed is that one step which would
  certainly have failed no longer will.
- **V007's two routes are assumed to agree.** `kicad-cli pcb drc` and a `pcbnew`
  child were compared on the rule classes these boards produce, not on every
  class KiCad can emit.
- **No Eagle, Altium or Allegro runs on any machine here.** Those formats remain
  proven internally consistent and nothing more.
- **`ohm signoff` still does not model zones or mid-segment T-junctions.**
  Unchanged, and stated in the module docstring; arcs have joined the modelled
  set, zones have not.
- **No Ohm board has come back from a fab and booted.**
