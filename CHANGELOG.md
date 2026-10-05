# Changelog

## Unreleased

- **feat(signoff)**: **the physics return channel, read: `--physics-report FILE`
  and V009.** Ohm records every `obligation` as a hole and never evaluates it,
  and until now nothing came back: a routed board signed off clean while every
  obligation its source declared had never been looked at. A solver layer now
  writes `physics_report.json` — one verdict per hole (`proven` / `violated` /
  `unproven` / `waived`), each named by a rebuild-stable `hole_ref` derived from
  `(kind, args, op)` and the hole's ordinal within that group — and
  `ohm signoff --physics-report FILE` reads it (`ohm/physics_report.py`, pure).
  V009 is an **error** for an unreadable report; a **stale** one (its
  `kicad_pcb_hash`, `constraints_hash`, `ohm_source_sha256` or `ohm_version` is
  not that of what is being signed off), which is refused outright so none of
  its verdicts is read; an obligation with no verdict (a dropped obligation is a
  violation, never a pass) or with several; a verdict for an obligation the
  source does not declare, or whose fields disagree with its identity; a count
  that does not reconcile; a violated obligation; and a verdict that files one
  number under both `verified` and `estimated`, or claims a result with none. A
  **warning** for `unproven`, and **info** for a waiver, for a proof that rests
  on an ESTIMATE, and for a summary counting verified and estimated results
  separately. **Without the flag nothing changes**: measured over 165 sources
  and 89 boards against cc4a52d, every `check`, `build` and `signoff` output —
  stdout, stderr, exit status and every emitted file — is byte-identical.
  tests/test_physics_report.py (25).
- **fix(cli)**: **a diagnostic quoted a file it was not about.** `_report`
  renders one source text against every diagnostic in the list, which was right
  while the list held only findings about the `.ohm`. When the D tier joined it —
  so `--strict` and `--json-diagnostics` could see it — every drawing finding
  started carrying an excerpt of the `.ohm`'s line 1, with a caret, beneath a
  location naming the emitted `.kicad_sch`. The excerpt is now printed only for
  diagnostics about the file whose text was passed; the rest keep their location,
  message and help line.
- **fix(check_drawing)**: **the gate could not see a `(justify ...)` on a
  property.** The trailing group of `_PROP_RE` was `([^)]*)`, which stops at the
  first `)` and therefore could never contain `(justify left)` — so every
  property Ohm writes with a justification was measured as centred, 1.5mm from
  where it is drawn. `hide`, the other thing that group is read for, is a bare
  word, which is why that half went on working and hid this one. The label
  branch's justify lookahead was a fixed 120-character window that could reach
  into the NEXT element; it now stops at this element's own `(uuid`.
- **fix(check_drawing)**: removed `_symbols()`, fifteen lines called by nothing —
  and wrong: `_body_half` needs the `lib_symbols` block, which the sheet body
  that its docstring implies passing has had removed, so every symbol came back
  on the 7.62mm fallback. `check_drawing()` computes the same boxes inline,
  correctly, because it also needs each instance's own text.
- **fix(emit_kicad)**: **four shipped designs failed Ohm's own drawing gate**,
  all of them text Ohm placed itself in a column something else had claimed.
  `_rider_offset` reserved a flat 2.54mm for an in-line rider's wire and nothing
  for the net LABEL that wire carries, so `PWR_GND` — 7.11mm of text — printed
  across the capacitor 4.32mm along (D002); it now reserves the label's width,
  read from the same map that decides whether a label is written at all, the way
  `_chain_span` already did for upright riders. A ladder head's rail name was the
  only placed text that never reached `taken_text`, so a cluster title was drawn
  through it (D001). A symbol's reference and value never consulted it either,
  though they are free text; they now go through `_free_spot` like the frame
  titles, with their existing position offered first so nothing moves unless it
  must. `test_every_shipped_design_draws_clean` holds all 29 designs at zero.
- **fix(emit_kicad)**: `_text_box` measured text with no `(justify ...)` as
  left-aligned; KiCad draws it centred, and `check_drawing._text_box` measured it
  that way — so the emitter's own collision avoidance and its own gate disagreed
  by half a string width. Latent until symbol properties started going through
  it, which is this release.
- **fix(check_placement)**: **S013 asserted a cause it had not established.** On
  a `near` cycle it printed "every side of the target was already taken" — but a
  cycle has no root, so the part that loses is flowed BEFORE anything is on the
  board and the target had not been placed at all. The two causes have different
  fixes, so the message and the help now distinguish congestion from a cycle.
- **refactor(architecture)**: **three modules pairs were mutually dependent**,
  each held apart only by an import written inside a function body. `_extent` —
  pure geometry — lived in `check_placement` and was imported back by the placer,
  so the emitter had to ask a checker what a rectangle is; it now sits with the
  rest of the placement geometry. `_source_id`, `_value_text` and `BOM_FIELDS`
  are now **`ohm/emit_common.py`**, below every emitter and depending on none,
  which also removes the five `emit_* -> emit_kicad` edges for a helper that was
  never about KiCad. The third (`import_symbol`/`provenance`) is the shape of
  that relationship and is declared rather than churned.
- **test(architecture)**: `tests/test_architecture.py` — no new import cycles in
  `ohm/`, `driver.CHECKERS` is the list the documents name, `shared/` does not
  import from `ohm/`, and the non-mutation invariant is RUN over the corpus. Two
  of the three stated invariants had gates; the shape had none, and prose does
  not fail.
- **fix(docs)**: **three documents asserted a registry size the registry had not
  had for a round.** `ohm/formats.py`, ADR-022 and the multi-EDA policy record
  all said "fifteen" after `.kicad_pro`'s three section versions and STEP's
  protocol name made it nineteen. A number in prose is exactly the unnamed
  literal `ohm/formats.py` exists to abolish. Corrected, ADR-022 gains an
  amendment rather than being rewritten, and
  `test_the_registry_is_the_size_its_documentation_says` names all three.
- **docs**: the CI and compatibility record moved from `ci1_docs/` to
  **`docs/ci/`**, and the three superseded root documents
  (`KICAD_COMPATIBILITY.md`, `KICAD_CI_PLAN.md`, `KICAD_CI_IMPLEMENTATION.md`) to
  `docs/ci/history/`, where a README says what each is and why a document that
  turned out to be wrong is kept rather than deleted. New `docs/README.md` maps
  the four kinds of document and what each ages by. The repo root is now the
  seven files a newcomer should actually read first.

- **fix(signoff)**: **a board routed with ARCS was reported as unrouted.**
  `read_board` dispatched on `zone`/`segment`/`via`/`footprint`, so KiCad's
  top-level `(arc ...)` — what its own router writes in arc mode, and what
  dragging a track produces — fell through silently. It landed in no collection
  and was counted by none of the structural-sanity counters, so V008 could not
  fire either. Measured on one board, same net: unrouted -> `V002 incomplete`;
  routed with a segment -> clean; **routed with an arc -> `V002 incomplete`,
  exit 1** — a correct board declared wrong in the last gate before fabrication.
  Symmetrically, an arc shorting two nets or carrying an undeclared net was
  invisible to V003/V004 and reported clean. An arc is now read as its CHORD,
  which is exact rather than approximate: the connectivity analysis is
  point-topology, so an arc and a segment with the same endpoints are already
  the same object to every V code. Any OTHER top-level form carrying a `(net )`
  is now reported as **V008** rather than skipped in silence — that silence is
  how arcs went unread.
- **fix(provenance)**: **`ohm verify-pinouts` certified real transpositions.**
  `_same_pin` fell back to any shared underscore token, and KiCad's
  disambiguating suffix on a duplicated pin is itself such a token — so
  `VDD_1`/`GND_1`, `VDD_2`/`VSS_2`, `SDA_1`/`SCL_1`, `IN_2`/`OUT_2` and
  `NC_1`/`VDD_1` all came back as the same pin, the finding was dropped, and the
  component was reported `ok` by the one tool built to catch a transposition
  that arrives on copper. Every existing negative pair carries a suffix on at
  most one side, which is why the branch was never exercised in the failing
  direction.
- **fix(drc)**: **the V007 geometry gate demoted whole rule classes.** Severity
  was kept once per RULE with last-write-wins, while KiCad prints one severity
  line per VIOLATION and `--severity-all` makes exclusions visible. Two real
  courtyard overlaps followed by one exclusion reported
  `warning | 3x courtyards_overlap`, so the gate passed a defective board with
  exit 0 — on every version and both report spellings, since the demotion is in
  the aggregation and the earlier regex fix never reached it. The count included
  the excluded item, and `message`/`where` came from the first violation while
  `severity` came from the last. Now grouped by `(rule, severity)`, and
  `_severity` is an explicit map: KiCad's own `info` was being promoted to a
  warning too.
- **fix(emit)**: **`ohm build` crashed on a cyclic `near` constraint**, with an
  unhandled `KeyError` out of `emit_kicad_pcb` AFTER the `.kicad_sch` had been
  written, leaving a half-populated output directory — while `ohm check`
  returned 0 and M012 reported "the design places no parts" about a design that
  declares two. `placement()` is on the ANALYSIS path as well as the emit path,
  so it must be TOTAL over `low.parts`; it now is, and a cycle breaks exactly
  one edge instead of dropping every member. New **S013** measures every
  declared `near` against the placement Ohm actually produced.
- **fix(determinism)**: **the caller's typed path reached the emitted bytes** of
  `.net`, `.zen`, converted `.ohm` and `.kicad_pro`. `_source_id` was written
  for exactly this defect and applied only to the KiCad schematic and board.
  Two consequences: the determinism invariant was broken for four of the eight
  emit kinds, and a fab/BOM artifact shipped an absolute path carrying the drive
  letter and the developer's user name. `tools/emit_manifest.py` could not see
  it — it passed a repo-relative path *deliberately*, which made the
  cross-platform gate structurally unable to observe the variable it was
  describing; it now passes the absolute path on purpose.
- **fix(emit)**: **`.kicad_pro` listed sheets that were never written.** The
  hierarchy was decided twice — `emit_kicad_sheets` by FIT, `emit_kicad_pro` by
  `len(sheet_groups(low)) > 1` — so a multi-module design that fits one page had
  a project naming two `.kicad_sch` files that do not exist, with none of its
  three sheet uuids present in the schematic that was emitted. One predicate
  now: `sheet_plan()`.
- **fix(cli)**: **D001-D004 escaped `--strict`, `--json-diagnostics` and the
  exit code.** `ohm build --strict` printed `warning[D001]` and exited 0,
  against a help text promising "exit 1 (and emit nothing) on any warning or
  worse". Text artifacts are now rendered into memory, the drawing gate runs
  over them, and everything is reported and gated once before anything is
  written — which also means a failure part-way through emitting can no longer
  leave a partly-written output directory.
- **fix(formats)**: `.kicad_pro`'s three KiCad schema versions and the STEP DATA
  section's second AP242 name were still bare literals, unregistered and
  unmentioned by ADR-022. The owner gate could not have found them: it compared
  the registry against a hand-written list, and an emitter that never registered
  appears in neither side. It now discovers candidates and requires each to be
  registered or exempt with a stated reason. Registry 15 -> 19, and the two
  AP242 names are tied together by derivation instead of by a second copy.
- **fix(ci)**: the `kicad` job ran `apt-get` inside a container whose default
  user is `uid=1000(kicad)` — measured exit 100, and exit 0 with
  `options: --user root` — which would have taken down all four matrix legs,
  `fpdata` and `certify` on the workflow's first real run. And the `certify`
  job's machine-written provenance record claimed an in-process geometry gate at
  one version, contradicted by `ohm/drc.py` and by a step in the same file that
  runs it in all four containers. `tests/test_ci_workflow.py` now gates both.
- **test**: the code-explanation gate still had a hand-kept tier-letter
  allowlist and still omitted one (`X`), while its docstring claimed the
  allowlist was gone. It now scans the whole alphabet, checks both directions,
  and ties its anti-vacuity floor to `len(EXPLAIN)`.

Full record: [`docs/ci/25_second_sweep.md`](docs/ci/25_second_sweep.md).

- **fix(ci)**: **KiCad 7 is in the matrix — the image existed all along.** The
  repo asserted in five places, as fact, that "no official KiCad container image
  exists below 8.0.4", and `tests/kicad_in_docker.sh` turned that into an
  `exit 2` on any 7.x. It is false: `kicad/kicad` carries `7.0`, `7.0.8`,
  `7.0.9`, `7.0.10` and `7.0.11`, and `kicad/kicad:7.0.11` (linux/amd64, 440 MB,
  pushed 2024-02-23) is built by `Dockerfile.7.0-stable` in the same
  `kicad-cli-docker` repo as the 8/9/10 images, cloning `kicad-symbols` and
  `kicad-footprints` at the matching `7.0` branch — so the version-matched
  libraries that make the container strategy honest are there too.
  - **How it went wrong**, since it was copied between documents without ever
    being re-checked: Docker Hub's tag listing is ordered by `last_updated` and
    returns 50 rows per page, 7.x last moved in February 2024, and page one is
    what somebody looked at.
  - The matrix is now **7.0.11, 8.0.9, 9.0.9, 10.0.5**, with no step excluded
    and no step conditional — every `kicad-cli` subcommand the gates use
    (`sch export netlist`, `sch export pdf`, `pcb export pos`,
    `pcb export gerbers`, `pcb export drill`, `sym upgrade`) predates KiCad 8.
    `pcb upgrade` is still KiCad 10 only, so the read-back gate re-saves through
    `pcbnew` at 7 as it already does at 8 and 9.
  - **The supported floor stays at 7.** `docs/ci/history/02_ci_feasibility_plan.md` and ADR-021 both
    proposed raising it to 8 *because* nothing could exercise 7; that proposal is
    withdrawn, not implemented.
  - **`tools/check_kicad_tags.py` now runs in CI** against every pinned tag. It
    paginates the whole tag list, it is what found this, and it is why the same
    mistake cannot be made by eye again. Kept out of the `certify` chain
    deliberately: a KiCad release must not invalidate the certification of what
    actually ran.
  - The 7 leg also dumps KiCad 7's footprint pad ids, so `ohm/fpdata/` gains a
    fourth major. The `fpdata` job rebuilds the table from every matrix dump and
    fails on any difference, which means the matrix and the committed
    `pads.json.gz` must name the same majors — a KiCad 7 target stops being
    *unchecked* by S012 at the same moment the leg exists to check it.

- **feat(ci)**: **KiCad compatibility is measured, and a skipped test can no
  longer pass for one.** `.github/workflows/ci.yml` runs the emit, library,
  read-back and plot gates inside the official `kicad/kicad` containers at
  **8.0.9, 9.0.9 and 10.0.5** — 342 tests, 0 failed, **0 skipped** at each. The
  claim "verified through 10.0.5" was previously a sentence; it is now a job.
  - **`OHM_REQUIRE_KICAD=1` makes a KiCad-shaped skip a failure.** Every guard
    was a `skipif`, so a job whose container failed to start reported green with
    everything skipped — certifying nothing while looking exactly like a job that
    certified everything. It aborts before collection when KiCad is absent, and
    converts skips (including `pytest.skip()` inside a test body, which no
    decorator can see) after each test.
  - Running it found five real defects, listed in `docs/ci/history/03_ci_implementation.md`.
    Two are worth repeating here.

- **fix(signoff)**: **`ohm signoff` could not read a KiCad 10 board at all.**
  KiCad 10 dropped the top-level net table and names nets inline — `(net "R1_P2")`
  where 7-9 wrote `(net 3 "R1_P2")`. Every pad and track read back bound to `""`,
  so signoff reported **V001 on every pad** of any board KiCad 10 had saved: a
  parse failure presenting as a design failure, in the last check before
  fabrication. `read_board` now resolves either shape to a name once.

- **feat(check)**: **S012 — the pads an external footprint actually carries**
  (ADR-021). `package = "Lib:Footprint"` emits a pad-LESS stub, so the pad id is
  the only thing that reaches the layout, and KiCad renames pads between
  releases: KiCad 10 moved shield and shell pads to `"SH"` across **163**
  footprints, silently unbinding `pin SHIELD: pad=11` on every board that named
  the old id. Three of Ohm's own standard blocks did.
  - `ohm/fpdata/` ships the pad ids of **15,473 footprints × KiCad 8/9/10** in
    158 KB, generated by `tools/gen_fpdata.py` from real libraries and rebuilt by
    CI at every matrix version, so the table cannot rot unnoticed.
  - **`--target-kicad` now means something.** It emitted byte-identical output
    for every value; the FORMAT is still always KiCad 7, but the LIBRARY
    VOCABULARY follows the target, so `pad=11` emits `11` on KiCad 8/9 and `SH`
    on KiCad 10 from one source. S012 reports only what no rename can resolve.
  - **`ohm verify-footprints`** re-reads the real installed library, the
    author-time half of the same pattern `ohm verify-pinouts` uses for symbols.
  - **`ohm doctor` knew nothing about KiCad 10** and reported "not detected" on
    the version the README certifies. `ohm/kicad_env.py` is now the one resolver
    for "where is KiCad", shared with the test suite, covering 7-10 on Linux,
    Windows and macOS.
  - **`--target-kicad 10` was refused** as "only KiCad 7 is supported in v1".
    It accepts `7|8|9|10`.

- **licence**: **Ohm is proprietary.** The D6 open-core question is closed the
  other way: `LICENSE` is now the Ohm Software License (all rights reserved,
  use only under a written agreement), `NOTICE` and the `pyproject` metadata
  match it, and the packaging carries `Private :: Do Not Upload` so an
  accidental `twine upload` is refused rather than irreversible. Output is
  untouched by this: designs and netlists you build with Ohm remain yours.

- **docs**: README rewritten in plain language — same claims, fewer of them
  said twice, and the stale counts (tests, KiCad version support) corrected.

- **feat(pcb)**: **track widths are computed, and the placer places.** The two
  things that made the board output a stub rather than a starting point.
  - **A netclass now carries a width, not just a name.** `netclass(rail,
    "power")` produced a class whose `track_width` was byte-identical to
    Default: the intent was named and then the rail was routed at signal width.
    `ohm/trace_width.py` sizes it from the current the design already declares
    (the same per-pin `current=` sum Q002 checks), by the IPC-2221A external
    curve — 1oz, 10degC rise, rounded UP to 0.05mm, because rounding a
    current-carrying trace to the nearer step is the one direction that is not
    safe. The FMUv2's power class goes 0.25mm -> 0.35mm off its declared 1.05A.
    IPC-2221 rather than the newer 2152 deliberately: 2152 needs copper area,
    dielectric and via field, which Ohm does not know, and 2221 errs wide.
  - Sizing only ever WIDENS, and only where a current was declared. A width
    invented from no current is a default wearing a standard's name.
  - **Q007** reports a netclass Ohm could not size, so a decorative class is
    visible instead of silent. It fires only where the declaration has somewhere
    to go — a rail reaching nothing but passives has no `current=` to write, and
    warning there is how a check teaches people to ignore it.
  - **The placer wraps.** It advanced only x, so 116 parts landed on a single
    500mm line. It now flows one block per module, wraps into rows against the
    board the `fixed` parts imply, and keeps clear of them — a mounting hole has
    a screw through it. The FMUv2 goes from 382mm x 1 row to 88 x 164mm, and
    contributes zero S011 conflicts (the five that remain are between the
    author's own fixed poses, unchanged).
  - **`near` is honoured, and honoured exactly.** It was dropped outright when
    the target was `fixed` — `near(c_vdd1, u, 2mm)` against a pinned MCU is the
    most load-bearing near on a board. Followers are now seated the moment their
    target lands, on the first free side, at the declared distance measured
    EDGE TO EDGE, which is what `near` means. 17 of the FMUv2's 18 near
    constraints are now met exactly; the 18th is the tail of a five-deep
    connector chain that runs out of free sides.
  - Board coordinates stopped being rounded to the 1.27mm SCHEMATIC grid. A
    0.05in imperial pitch was being applied to a metric board, which is why a
    declared 2mm arrived as 2.9mm. They quantise to 0.01mm, toward the target,
    so the quantum can only ever shorten a declared gap.

- **fix(kicad10)**: **certified against KiCad 10.0.5**, and fixed the one thing
  that was genuinely broken.
  - **Ohm's emit was already compatible and is now proven so.** Ohm writes the
    KiCad 7 format and relies on KiCad reading forward, which is the direction
    KiCad guarantees. KiCad 10.0.5 extracts the FMUv2's netlist as **77 nets,
    partition-identical to KiCad 9.0.9**, and plots its gerbers and schematic
    PDF. No emitter version bump was needed and none was made — writing a newer
    stamp would only cost backward compatibility for no gain.
  - **The roundtrip READER was broken, for the second time by a pretty-printer.**
    KiCad 9 inserted `(class "Default")` between a net's name and its nodes;
    KiCad 10 splits every net across a dozen tab-indented lines. Both times the
    netlist was correct and a line-anchored regex matched zero nets, so the gate
    failed as though the circuit were wrong — 7 of 23 cases. Replaced with a
    balanced-paren reader: paren depth is the only part of the format that is
    actually promised.
  - **Every power symbol shared one reference.** Ohm emitted them all as bare
    `#PWR`, so the FMUv2 shipped 191 symbols with one designator between them.
    KiCad 9 was silent; KiCad 10 reports "annotation errors" on every netlist
    export, which is the correct reading — that is what a reference means. They
    are now `#PWR01`, `#PWR02`, ... numbered across the WHOLE hierarchy, because
    restarting per sheet just moves the collision up a level. Latent since the
    power symbols were introduced; found only by running KiCad 10.
  - **`$OHM_KICAD_CLI`** redirects the roundtrip gate at another KiCad, and
    `tests/kicad_in_docker.sh` supplies one from a container. Certifying a
    release is now one line and does not touch the developer's toolchain:
    `OHM_KICAD_VERSION=10.0.5 OHM_KICAD_CLI=tests/kicad_in_docker.sh pytest
    tests/test_schematic_roundtrip.py`. Verified on **7, 9.0.9 and 10.0.5**.
  - Not fixed, recorded instead: KiCad 10 reports 39 more `footprint_link_issues`
    than KiCad 9 on the same board and renamed `label_dangling` to
    `isolated_pin_label`. Both are library-resolution and rule-naming
    differences in an environment with no footprint tables configured, not
    findings about the design.

- **BREAKING**: **derived net names changed spelling** (`fmu.spi_fram.miso` ->
  `FMU_SPI_FRAM_MISO`). Anything holding the old names must be regenerated:
  - A board routed against an earlier build carries the old names in its copper,
    and `ohm signoff` compares board net names to the source's — so it will
    report V001 on **every pad** until the netlist is re-imported into the board.
    Re-import, then re-run signoff.
  - Explicit `net NAME = ...` is unaffected: authored names are never rewritten.
  - A rename is skipped whenever the readable form is already taken by another
    net, so no design silently changes which pins are connected.
- **feat(emit)**: **the drawing reads like one an engineer drew.** Five changes,
  all in what happens after lowering; the language is untouched.
  - **Net names are signal names.** A derived name was the Ohm instance path —
    `fmu.spi_fram.miso` — printed on every label on every sheet. It is now
    `FMU_SPI_FRAM_MISO` (`readable_net_name`, the single definition of the
    mapping). The path is kept whole and never shortened: `accel.spi.cs` and
    `gyro.spi.cs` are different chip selects, and collapsing them to `SPI_CS`
    would short two nets in the drawing. An explicit `net NAME =` is left exactly
    as authored, and a rename that would collide is not applied.
  - **M010 was silently switched off by that rename** and is fixed in the same
    change: it tested `name == "<part_ref>.<pin>"` against the raw dotted path,
    which after the rewrite matched nothing, so "nobody named this signal"
    stopped being reported on every board. It now asks in the spelling the net
    actually carries. This is the check that motivated the feature, so shipping
    the feature with the check dead would have been the worst outcome.
  - **Property text is never upside down.** The angle compensation ran mod 360,
    which put a 180-degree part's value at 180 (printed upside down and
    mirrored) and a 90-degree part's at 270 (read top-to-bottom). A drawing has
    two legal text angles; it is now mod 180.
  - **`near(cap, ic)` places the cap on the SHEET, not just the board.** A bypass
    cap sits on two rails and nothing else, so connectivity can never recover
    which part it serves — every one looks alike. The constraint already says
    it, and the schematic was throwing that away and banking them.
  - **A frame only encloses what it names.** The "Decoupling" bank was framed by
    the bounding box of its members, which swallowed a 3.3V regulator, two
    diodes and four test points that happened to sit inside it. Rows are now
    recovered from the placed positions (parts in a row differ in y by their own
    half-heights, so exact y never grouped them) and only a *contiguous run* of
    entirely-bank rows is framed.
  - **Sheet fit reserves the title block**, so a module frame no longer runs
    through "Title:" and "Rev:".
- **feat(emit)**: **sourcing attrs reach the BOM.** `mpn`, `manufacturer` and
  `datasheet` were declared in the source and dropped at the seam — a netlist
  whose parts say only "100nF" cannot be ordered. They now ride the netlist as
  fields and the KiCad symbol as hidden properties (hidden because a part number
  is what you order by, not what you read the circuit by).

- **feat(emit)**: **native Altium and Cadence Allegro targets** (ADR-020) — the
  multi-EDA claim stops being an aspiration.
  - `ohm build --emit altium` writes real `.PcbLib` and `.SchLib` binaries, not an
    interchange to import and repair. That meant writing the MS-CFB compound-file
    container from scratch (`ohm/pkggen/cfb.py`, no runtime dependency) and packing
    Altium's own records. Every field offset, block length and magic constant was
    read out of Altium-authored libraries; fields whose meaning is still unknown are
    copied verbatim rather than zeroed, because an unknown field set to zero is a
    guess and an unknown field set to what Altium writes is a fact.
  - `ohm build --emit allegro` writes `pstchip.dat` / `pstxprt.dat` / `pstxnet.dat`,
    the logic import Allegro accepts from any third-party front end. Allegro's
    library binaries are undocumented and Ohm emits none — this is parity on
    connectivity and footprint binding, and explicitly none on library geometry.
  - Geometry is shared, not reimplemented: pads from `PackageModel`, silkscreen from
    `silk_segments`, symbols from `symbol_layout`. The new targets cannot drift from
    the `.kicad_mod` that is validated against KiCad's own reference footprints, and
    the tests assert that equality rather than trusting it.
  - Validation is stated per layer and stops where the evidence does: the container
    is read back by `olefile` (an implementation Ohm did not write, now a test-only
    dev dependency), record framing is asserted against the lengths Altium itself
    writes, and the Allegro triplet is asserted node-for-node against Ohm's own
    proved netlist. That Altium or Allegro *opens* these files is **not** claimed —
    neither tool exists on any machine Ohm is developed on.

- **feat(check)**: a **D tier** — the drawing is checked, not eyeballed
  (ADR-019). `ohm build --emit kicad` now reports **D001** overlapping text,
  **D002** text over a symbol body, **D003** overlapping symbols and **D004**
  duplicate uuids on every sheet it writes. Every connectivity defect in this
  emitter's history was caught automatically by the round-trip oracle; every
  geometric one was caught by rendering a PDF and looking at it. That asymmetry
  is why the drawing took twenty iterations, and it is now closed.
  Advisory by design: the netlist is proved exactly and separately, so an
  overlapping drawing is ugly rather than wrong, and a gate that refused to emit
  would stop people using it. The first version reported thousands of collisions
  on a clean board — library definitions, and every part's own reference over its
  own body — so it also skips `lib_symbols`, attributes text to its owning
  instance, and measures a symbol's graphics rather than its pins. A check that
  cries wolf is a check nobody reads.
- **docs**: `AGENTS.md` now carries the first-pass contract — what to declare so
  the drawing comes out right without iteration, which checks enforce each one,
  and the one process rule that cost the most: measure a drawing defect before
  fixing it.

- **fix(schematic)**: a rail name is placed where there is ROOM for it, and a
  parallel bank returns to ground per part.
  - A power port's name is a property, so unlike a net label — which is bound to
    the wire it names and would disconnect it if moved — it is free. It now
    picks the first of several candidate spots (outward first, then stepped off
    the pin's row, then the other side) that collides with no other text AND no
    symbol body. A buzzer connector's `VDD_5V_PERIPH` needed 13mm and had 7.6mm
    of gap, so it printed straight through the MOSFET beside it; there was no
    "outward" answer, only a clear one.
  - An upright rider reserves room for the name its rail port prints, not a
    token 2.54mm: it stands vertical, but the name is still drawn horizontally.
  - **A parallel bank shares only its supply rail.** Each part returns to its
    own ground symbol instead of a common return wire running the width of the
    bank. That is the convention, it is what the caps' own symbols already say,
    and it stops the bank stretching its block for no information.

- **fix(schematic)**: text no longer overprints text. Ohm now tracks the extent
  of every label, rail name and symbol body it places, and puts the text it may
  freely MOVE — a module or cluster frame title — in the first candidate spot
  that collides with none of them. A net label is deliberately NOT movable: it
  is bound to the wire it names, so sliding it off to tidy the page would
  disconnect the net; the collision is resolved by moving its neighbour instead.
  Rail-name candidates run outward only, because flipping a name back over the
  part it belongs to trades one overlap for a worse one — it lands on that
  part's own pin names. Frames also expand to enclose what is actually inside
  them (a rotated symbol swaps its axes, and a glyph draws past its pins), so a
  frame edge no longer cuts through an LED.

- **feat(schematic)**: parts in SERIES are drawn as a chain. A rider may now
  ride another rider — rail into a ferrite into a filter cap, a resistor into
  the LED it feeds — so a series circuit is drawn end to end instead of as two
  parts with a label between them. Chains were previously rejected outright
  because a rider is positioned from its host and one could be reached before
  its host had a position, silently dropping a part; placement now walks riders
  shallow-first by depth, only a cycle is rejected, and the host pin is
  transformed by the host's OWN rotation, which a chained host can have. Cell
  width reserves the whole chain rather than the first link, so a chain cannot
  overrun into the next cell and draw over its symbols.
  A net above `_SAT_MAX_FANOUT` endpoints is excluded from clustering: it is a
  BUS, not a local connection, and picking one of its members to ride is
  arbitrary. Eight LED resistors sharing a supply node each rode the next,
  telescoping into a chain that overlapped itself and shorted four separate LED
  nets together — caught by the round-trip oracle.
- **fix(schematic)**: element uuids are unique. Deriving a wire's uuid from its
  net name alone was fine while every net was one stub per pin; as soon as a net
  became several segments the same id was emitted repeatedly, and KiCad's
  behaviour on duplicate uuids is undefined. Wire, label and sub-block frame ids
  now include their coordinates, with a test asserting no duplicates.

- **feat(schematic)**: parts in PARALLEL are drawn as a ladder. Eleven bypass
  caps across one rail were eleven copies of the same two-terminal drawing, each
  with its own rail label and its own ground symbol — nothing on the page said
  they were in parallel. A run of same-row parts sharing the same pair of nets
  now stands upright on one rail wire along the top and one return along the
  bottom, with junction dots at each rung and the rail arriving ONCE at the head.
  On fmuv2: 31 parts in 10 parallel groups, labels 136 -> 44.
  The rail line's height is taken from the members' real pin geometry rather
  than assumed: a group mixing a glyph (pins at 3.81mm) with a box symbol ran
  the rail straight past the box's pins, leaving a diode on the page connected
  to nothing. Such a group is now left alone. The whole ladder is also decided
  before anything is mutated — an earlier version rotated and silenced members
  as it went, then bailed on one that could not form a rung, stranding the rest.

- **feat(schematic)**: structure INSIDE a sheet, and a second rendering of it.
  - **Sub-block frames.** A module box on a sheet that holds one module frames
    the whole page and says nothing; the structure a reader wants is one level
    down. Each cluster — a host and the riders wired onto its pins — and the
    bank of rail-only parts now get their own dotted, titled frame. Titles come
    from the declared KIND, so they read functionally ("Crystal", "Connector",
    "Mcu", "Decoupling") rather than by designator; a cluster mixing a generic
    passive with something specific takes the specific name, so an LED and its
    series resistor is an "LED", not a "Resistor".
  - **Flat overview plot.** `<stem>_overview.kicad_sch` renders every block on
    one sheet alongside the hierarchy — the same netlist, twice: hierarchical
    sheets to EDIT, one sheet to REVIEW without losing the system view. It is
    standalone, never referenced by the root, so KiCad cannot load the design
    twice. Verified against Ohm's netlist independently of the hierarchy.
  - **Even density.** The drawing is centred on whatever standard sheet it lands
    on, instead of sitting in the top-left with three quarters of the page
    blank. The bank starts its own row so its frame stops reaching up and
    enclosing the clusters above it.
  - **Functional zoning.** Blocks are laid out power, compute, peripheral,
    interface — read from the declared kind taxonomy (ADR-008), so no name
    heuristics. Zone is a PRECEDENCE, not a vote: a block holding a regulator is
    the power block whatever else is in it. Ranking by pin count instead put
    `pwr` in the interface zone, its 6-pin input connector outvoting the two
    regulators the block exists for.

- **fix(schematic)**: local nets are wired in CLUSTERS, not only in pairs. The
  rider rule required a net to have exactly TWO nodes, and that one condition
  excluded every recognisable cluster on the board: a crystal load cap sits on
  cap+crystal+MCU, three nodes, so crystal circuits, reset RCs and LED series
  resistors all stayed as scattered label pairs. A local net is now drawn as a
  wire between two of its parts whatever its node count; when it reaches
  further, the remaining pins keep their labels and ONE label rides the wire, so
  the cluster has shape and a single signal leaves it. The host is the SMALLEST
  qualifying neighbour rather than the richest — a load cap belongs against its
  crystal, not dragged across the block to the 100-pin MCU the same net reaches.
- **fix(schematic)**: a part could vanish from the drawing. A rider is
  positioned from its host, so when the host was itself a rider the two were
  ordered by chance and the dependent one could be reached before its host had a
  position — then silently skipped. Rider chains are now rejected outright, and
  a new test asserts every part in the BOM appears on a sheet, which is the
  invariant that caught it.

- **feat(language)**: a **design-intent layer** (ADR-018) — the vocabulary a
  schematic needs to read as a controlled document rather than a generated one.
  A hardware reviewer listed ten drafting tells on the fmuv2 board; nine came
  from facts Ohm could not state, so the emitter invented substitutes.
  - **`board <name>:`** with `title` / `rev` / `date` / `company` / `designer` /
    `license` / `comment` fills KiCad's title block; sheet *n of m* is computed
    from what is emitted. Closed field set: R027 on an unknown field, R026 on a
    second block.
  - **`net NAME = <label>`** names a signal, beating the label election that
    used to let a capacitor pin name a net (`fmu.c_y1.P1`). R028 when two
    declarations name one net. Rails also drop their interface-member suffix
    when unambiguous: `VDD_3V3.v` -> `VDD_3V3`, `GND.gnd` -> `GND`.
  - **`note "..."`** puts engineering notes on the sheet; `near(...)`
    constraints render themselves as placement notes.
  - **`unused_pins = open`** marks every spare pin no-connect from one line.
  - **`vmax` / `dielectric`** show on the drawing: `100nF 16V X7R`.
  - New checks: **M009** (blank title block), **M010** (net named after a
    component pin), **M011** (rail capacitor with no voltage rating).
  - Sheet count follows FIT: one sheet while the drawing fits A3, otherwise one
    A3 sheet per module. `--sheets auto|flat|hier`. fmuv2: one A0 -> 13 sheets
    (8xA4, 3xA3, 1xA2, 1 oversize).
  - `ohm fmt` round-trips `net` and `note` — a formatter that silently deletes
    source is worse than no formatter.

- **fix(pkggen)**: generated footprints no longer draw silkscreen over pads.
  The body outline was a plain rectangle at courtyard - 0.25mm, which on any
  package whose pads reach the courtyard lay across them — 76 `silk_over_copper`
  violations on the fmuv2 board, and in fabrication silk on bare copper is
  either clipped by the mask or lands on the solderable surface. The outline is
  now cut against every pad (KLC F5.1 clearance) and only the clear pieces are
  drawn, which is what KiCad's own R_0603 does; the pin-1 marker is dropped when
  a pad reaches its corner. Package agnostic: a chip keeps its two stubs, a QFN
  keeps its corners. **76 -> 0.**
- **feat(check)**: **S011** — two parts cannot share board area. `emit_kicad`
  claimed no courtyard overlaps "by construction", true only of AUTO-placed
  parts; a `constraint fixed(...)` puts a part exactly where the source says and
  nothing checked whether the source said something impossible. KiCad's DRC
  found five overlapping pairs on fmuv2 that every Ohm tier had passed. The
  check runs over the SAME `placement()` the emitter uses, so verdict and board
  cannot drift. Severity follows what Ohm knows: **error** when both courtyards
  come from real `generate(...)` geometry, **warning** when either part is a
  string footprint, whose land pattern Ohm has never read and places on a 5x5mm
  placeholder — calling that a collision would assert a fact Ohm does not have.

- **feat(signoff)**: `ohm signoff` now runs the **geometry gate itself** (V007)
  instead of telling you to run `kicad-cli pcb drc` — a subcommand that does not
  exist in KiCad 7, the version Ohm pins, so geometry was in practice never
  checked. `ohm/drc.py` drives KiCad's own DRC engine through its Python API
  (`pcbnew.WriteDRCReport`), including from inside a virtualenv, where KiCad's
  bindings are not importable by name, and reports one diagnostic per rule class
  with a count and first location. Unconnected pads and unresolved footprint
  libraries are info, not error: an Ohm board is unrouted until a router runs,
  and it carries footprint geometry inline by design. `--no-geometry` skips it;
  a missing KiCad degrades to a warning rather than a failure. `signoff.py`
  stays pure (N1). `.kicad_pro` now also carries ERC severities, raising
  `similar_labels` and `pin_not_connected` to error for the GUI ERC run —
  KiCad 7 has no headless ERC.

- **feat(schematic)**: the schematic is now DRAWN rather than labelled
  (ADR-017). A passive serving one pin of an IC is placed against that pin and
  wired to it, instead of floating elsewhere on the sheet joined to it by a
  matching pair of net labels; each module gets a dashed, titled partition box;
  blocks pack across the page and the page snaps to the smallest standard sheet
  that fits. fmuv2: one A0 sheet instead of 1251 x 927 mm of "User", 377 net
  labels down to 151. A net drawn as a bare wire is anonymous, EXCEPT where a
  netclass or diffpair constraint names it — anonymising those would silently
  drop their trace rules. `--sheets hier` still emits the ADR-016 hierarchy.
- **feat(schematic)**: `ohm build --emit kicad` now writes a **hierarchical**
  schematic — a root sheet of module blocks plus one `<stem>_<module>.kicad_sch`
  per top-level module, A3 where the module fits (ADR-016). The fmuv2 reference
  board goes from one 1251 × 927 mm page to 13 sheets, 11 of them A3. Signals
  that leave a module become hierarchical labels matched by sheet pins; rails
  stay power symbols; module-local nets stay sheet-local labels. Designs with
  fewer than two modules are unchanged, single-sheet. `.kicad_pro` now lists its
  sheets. The KiCad round-trip oracle covers the hierarchy: 77 nets / 377 pins
  read back IDENTICAL across all 13 sheets.

## 0.1.1 — 2026-07

- **fix(init)**: the interactive `ohm init` / `ohm create-project` wizard now
  prompts for a project *name* and creates a new subfolder by default; a blank
  answer scaffolds `./my-board` (not the current directory, which previously
  surprised users into scaffolding their parent folder). Use `.` to scaffold in
  place. An explicit name argument was always correct and is unchanged.

## 0.1.0 — 2026-07 (first PyPI release)

Initial development, complete through signoff. Milestones (details in
`PROGRESS.md`, decisions in `docs/adr/`):

- **P0–P6**: grammar/parser/AST with error recovery; resolver → IR; exact
  Decimal units with tolerance intervals; netlist + KiCad 7 emitters with
  hand-verified golden corpus; structural (S) and intent (I) tiers.
- **P7–P9**: `.zen` interop (import + export, ADR-002); closed-form evaluation
  with interval arithmetic + obligations-as-holes (ADR-003); operating-point
  vs ratings tier Q (ADR-004).
- **P10–P12**: `constraint` declarations + router seam (ADR-005); stdlib-only
  LSP; `ohm fmt`; `ohm explain`; IPC-7351 package generation (ADR-006);
  live-LLM validation loop.
- **L**: standard library — ~106 generic blocks + honest templates across 17
  domains, pinouts adversarially verified against KiCad libraries (ADR-007).
- **K**: checked component `kind` taxonomy — structural conformance contracts
  (~42 kinds), K001–K005 (ADR-008).
- **F**: first-spin tier — `expects` (pullup/decoupling/loadcaps/termination/
  tie, `when` gates, auditable waivers), bring-up checks B001–B010, DFM lints
  M001–M008, placement intent + routed-handoff KiCad emit (ADR-009).
- **G**: repetition — arrays, bounded `for` loops, loop-index arithmetic,
  compile-time computed values; adversarially verified (ADR-010).
- **V**: `ohm signoff` — routed-board verification against the source
  (V001–V006), validated against KiCad's connectivity engine (ADR-011).
- **E0/X0/X1**: expressiveness + ecosystem (ADR-012) — pure `fn` functions,
  config-driven array counts/loop bounds, compile-time `if`/`else` variants
  (all folding at elaborate, override-guarded); `.zen` designs as check input
  + `ohm convert` to annotatable Ohm source; `ohm import-symbol` turns real
  KiCad symbols into Ohm components with true pads and roles.
- **pkggen catalog expansion**: extended from gull-wing + chip to the common
  modern packages (ADR-006's extensible-catalog path, each validated against a
  KiCad 7 reference land pattern before shipping): `QFN` (four-side no-lead +
  optional thermal pad), `QFP` (four-side gull-wing, per-part lead `span`),
  `DFN`/`SON` (two-side no-lead), `BGA` (rows×cols grid of circular pads, JEDEC
  A1.. naming, `ball`/`pad` diameter, optional `border=<n>` for perimeter /
  depopulated grids), `LGA` (rectangular-land grid, same options), `PLCC`
  (J-lead, pin 1 centered on the top edge, counterclockwise), and the fixed tab
  packages `SOT223`/`SOT89`/`TO252`/`TO263` (three signal leads + a solid tab
  pad, no arguments). Shared 4-/2-side placement helpers back the perimeter
  families; the footprint emitter gained a `circle` pad shape for BGA balls.
  QFN/BGA/PLCC (vs 20/28)/SOT-223 and the power-package signal leads reproduce
  their KiCad references exactly; QFP/DFN match within 0.05 mm; power-package
  tabs are solid-rectangle simplifications of KiCad's paste-subdivided lands.

- **project UX**: `ohm init [name]` / `ohm create-project [name]` scaffold a
  full project (starter design, `ohm.toml`, README, `AGENTS.md`,
  `docs/LANGUAGE.md`, `lib/`, `out/`, `.gitignore`) from bundled templates —
  strictly additive, never clobbering existing files. `ohm.toml` makes the CLI
  framework-aware: bare `ohm build` / `ohm check` walk up to the project root
  and use the configured entry, out dir, and KiCad target (an explicit file,
  `-o`, or `--target-kicad` always wins).
  `ohm refactor <dir>` restructures a messy folder into that layout (dry-run by
  default; `--apply` to execute; stray root `.ohm` files move to `lib/`).
  `ohm doctor` diagnoses the install (Python, PATH, encoding, KiCad libs,
  project). The CLI now forces UTF-8 on its own stdout/stderr, so a non-UTF-8
  console (Windows cp1252 / Git Bash) no longer crashes on non-ASCII
  diagnostics. Global install is documented via `pipx install .`.

680 tests; ruff + mypy clean; deterministic double-build byte-identity and
hermeticity enforced in CI.
