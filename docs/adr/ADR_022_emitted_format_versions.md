# ADR-022 — Every version stamp Ohm writes: one policy, and the evidence for each

Date: 2026-08-31 · Status: accepted

## Motivation

ADR-021 settled the version question for KiCad: write KiCad 7, never bump, because
KiCad reads forward. That covered four constants. Ohm writes **eight** external
formats on the build path and a ninth on the interop path, and every one of them
carries a version stamp of some kind — a numbered file-format revision, an ISO
schema string, a header line an authoring tool itself writes, a toolchain lane.
For the other eleven stamps there was no policy at all, only literals.

`ohm/formats.py` (2026-08-31) gathered them into one registry and wrote the
policy down. What it could not do, being a refactor, was answer the question a
registry invites: **is each of these values still the right one?** A stamp with a
reason attached is not the same as a stamp somebody checked.

So each was checked against the state of its tool on 2026-08-31, and the answers
turn out to be sharply uneven. Two stamps have a reader on this machine that can
be pointed at Ohm's output and made to succeed or fail. Four have a reader
somewhere in the world that nobody here can run. Three have no external reader at
all, by construction. That unevenness is the honest content of this ADR, and
hiding it behind a uniform table of "reasons" would be the failure mode it exists
to prevent.

The result: of the fifteen registry entries this ADR surveyed, **one moved**
and **one is new**. (The registry has since grown to nineteen — see the
amendment at the end.)
Both are the ones with an executable reader. That is not a coincidence, it is the
policy working.

## Decision

### 1. The policy, for every format and not just KiCad

> **Write the oldest stamp a current reader accepts. Bump only when a reader
> proves the new one is needed.**

A newer stamp never buys correctness — every reader in the table below reads
forward — and it always costs something: an older installation's ability to open
the file, or, for a reverse-engineered binary format, agreement with what the
authoring tool itself writes, which is the only ground truth such a format has.

Two corollaries, both of which decide cases below:

- **"Current" is a property of the protocol, not of the file.** A stamp stops
  being right when its standards body stops maintaining it, even though every
  reader still accepts it. That is what moved STEP.
- **"Proves" means an oracle that fails.** A reader that opens a file and warns
  proves nothing; a reader that yields a wrong answer, or no answer, when the
  stamp is wrong is evidence. Ohm has two such oracles, and they are the two
  stamps that changed.

### 2. Every stamp, with what it is and who checked it

| Format | Stamp | Owner | Reader run against Ohm's output | Status |
|---|---|---|---|---|
| `.kicad_sch` | `20230121` | `ohm/emit_kicad.py` | **yes** — `kicad-cli` in CI at 7.0.11, 8.0.9, 9.0.9, 10.0.5 | unchanged (ADR-021) |
| `.kicad_pcb` | `20221018` | `ohm/emit_kicad.py` | **yes** — same matrix | unchanged (ADR-021) |
| `.kicad_mod` | `20221018` | `ohm/pkggen/footprint.py` | **yes** — same matrix | unchanged (ADR-021) |
| `.kicad_sym` | `20220914` | `ohm/pkggen/symbol.py` | **yes** — same matrix | unchanged (ADR-021) |
| `.step` | `AP242_MANAGED_MODEL_BASED_3D_ENGINEERING_MIM_LF { 1 0 10303 442 1 1 4 }` | `ohm/pkggen/step.py` | **yes** — gmsh (OpenCASCADE) meshes the solid, volume to `rel=1e-9` | **CHANGED** from AP214 |
| `.zen` | `pcb-version = "0.4"` | `ohm/emit_zen.py` | **yes** — `pcbc` 0.4.41 builds the emitted file | **NEW** — and the export was rewritten to earn it |
| `.lbr` | `6.3` | `ohm/pkggen/eagle.py` | no — no Eagle on any machine here | unchanged |
| `.PcbLib` | `PCB 6.0 Binary Library File`, `VERSION=3.00` | `ohm/pkggen/altium.py` | no — container only (`olefile`) | unchanged |
| `.SchLib` | `…Schematic Library Editor Binary File Version 5.0`, `MinorVersion=2` | `ohm/pkggen/altium.py` | no — container only (`olefile`) | unchanged |
| MS-CFB | version `3` | `ohm/pkggen/cfb.py` | **partly** — `olefile`, an implementation Ohm did not write, reads the container back | unchanged |
| Allegro `pstxprt.dat` | `PST_HDL_CENTRIC_VERSION_0` | `ohm/emit_allegro.py` | no — no Allegro here | unchanged |
| Ohm netlist | `"E"` | `ohm/emit_netlist.py` | n/a — Ohm's own format | unchanged |
| `constraints.json` | `"1"` | `shared/constraints_schema.py` | n/a — the open↔proprietary seam | unchanged |
| Land patterns | IPC-7351**B** | `ohm/pkggen/packages.py` | **yes, indirectly** — KiCad's own library is the comparand | unchanged, and **already current** |

Fourteen rows, fifteen registry entries at the time of this survey: `.PcbLib`
and `.SchLib` each carry two
stamps under one owner. The last row is the odd one — IPC-7351B is a *standard Ohm
computes from*, not a stamp it writes into a file, so it has no `FormatVersion`
entry and cannot have one (`tests/test_formats.py` requires every registered value
to appear in the emitted bytes). It is here because the version question was asked
about it too, and section 6 is the answer.

### 3. STEP: AP214 → AP242 (the one bump)

`ohm/pkggen/step.py` wrote `AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }` — AP214
first edition — with the matching `APPLICATION_CONTEXT('core data for automotive
mechanical design processes')` and an `APPLICATION_PROTOCOL_DEFINITION(...,
'automotive_design', 2000, ...)`.

Nothing was wrong with it in the sense of being unreadable. gmsh reads AP214
today and will keep reading it. What changed is upstream: **ISO/TC 184/SC 4
stopped work on AP203 and AP214 when AP242 published in 2014**, and AP242
subsumes both. Under corollary one, AP214 stopped being *current* while remaining
*accepted*, and the policy asks for the oldest current protocol — which is AP242
**edition 1**, `{ 1 0 10303 442 1 1 4 }`, not edition 2's `3 1 4` or edition 3's
`4 1 4`.

The reason this bump shipped and the six unverified ones did not is the oracle.
`tests/test_step.py` meshes the emitted file with gmsh, which embeds
OpenCASCADE, and asserts the solid's volume to `rel=1e-9`. That test fails
loudly on a schema a reader rejects: there is no solid, so there is no volume.
Before: 33.4425 mm³ for a 4.9 × 3.9 × 1.75 body, one watertight solid. After the
bump: the same, unchanged, from a file whose header now says AP242. The stamp is
*proved read*, not assumed read.

Getting the value right mattered more than getting it new. All three strings are
copied verbatim from OpenCASCADE's own AP242 write lane
(`STEPConstruct_ContextTool.cxx`, `AddAPD()`, `mySchema == 5`) — the same rule the
Altium constants follow, that what the tool itself writes is a fact and what we
compose from prose is a guess. That rule was already in force here without being
named: the AP214 triple these lines replace was OCC's `mySchema == 4` lane, right
down to the year 2000. Two details survive as they are, deliberately, and both are
commented at the call site:

- `APPLICATION_PROTOCOL_DEFINITION`'s schema name is
  `ap242_managed_model_based_3d_engineering` — **without** the `_mim_lf` suffix
  the `FILE_SCHEMA` name carries. OCC's `IsAP242()` lower-cases that field and
  compares it to exactly that spelling; "correcting" it to match `FILE_SCHEMA`
  would make OCC stop recognising the file as AP242 on read-back.
- The year is `2013`, from the AP242 DIS that OCC implemented, where ISO
  published edition 1 in 2014. Nothing reads it. Agreeing with the one AP242
  writer Ohm's output can be diffed against beats a silent divergence.

The epoch-frozen `FILE_NAME` timestamp is untouched: byte-identical output across
machines and days is not negotiable, and `tests/test_step.py` still asserts it.

### 4. `.zen`: a version that did not exist to be pinned

There was no `.zen` entry in the registry before this ADR, and the reason is worse
than an oversight. ADR-002 shipped in July 2026 with its section 5 deferred — "the
optional `pcb` toolchain cross-check is deferred until the `pcb` binary is
available locally" — and it stayed deferred for a year. In the meantime
`ohm/emit_zen.py` described what it wrote as "the universal `Component()` form".

diodeinc/pcb 0.4.41 (released 2026-08-31) was obtained and run. The form is not
universal; it does not load:

```
Either `pin_defs` or a Symbol value for `symbol` must be provided
[bom.unspecified] Component 'U1' is included in the BOM but is missing part
information. Specify `part=Part(...)`.
```

So the `.zen` entry pins `pcb-version = "0.4"` — the current released lane, and
the oldest one whose `Component()` surface Ohm's export has actually been built
against. ADR-002 carries the detail of what changed in the export and the
importer; the point *here* is that this is the only row in the table whose stamp
is not inherited from a format's history. It is the first version of the `.zen`
surface Ohm has ever been in a position to claim it writes.

The lane string goes into the generated file's header comment rather than into a
manifest, because `emit_zen` produces one `.zen` and no workspace around it: a
file that outlives this repo should still say which surface it was written
against. That also makes the entry checkable by `tests/test_formats.py` the same
way as every other entry — the registered value appears in the emitted bytes.

### 5. The six that did not move, and why each is a different kind of "no"

**Eagle `.lbr`, `6.3`.** Eagle reached **end of life on 7 June 2026**; Autodesk
shut the licence servers, and the successor, Fusion Electronics, reads `.lbr`.
`6.3` is the revision that introduced XML libraries — there is no older XML
library format to fall back to, so it is simultaneously the conservative
forward-read choice and the only one available. **No Eagle exists on any machine
here**, so this stamp is unverified by any reader, and saying so is the entry's
most important content.

**Altium `.PcbLib` / `.SchLib` / MS-CFB.** `PCB 6.0 Binary Library File`,
`VERSION=3.00`, `…Version 5.0`, `MinorVersion=2`, container version 3. None of
these is an Altium Designer release number — they are **format-generation strings
Altium itself writes**, read off real Altium-authored files. Altium Designer is at
26.9.1 (August 2026) and changes nothing here; there is no newer value to choose.
The container alone is verified, and by a genuinely independent reader: `olefile`
is a test-only dependency precisely so the CFB bytes are read back by an
implementation Ohm did not write (`pyproject.toml`'s own note: "a self-written
reader would agree with a self-written writer about a broken file"). The Altium
*semantics* inside that container remain unverified.

**Allegro `PST_HDL_CENTRIC_VERSION_0`.** Taken verbatim from a real OrCAD-written
triplet, which is how the whole of `ohm/emit_allegro.py` was derived: "syntax
taken from a real OrCAD-written triplet, not from prose". Allegro X is at 25.1 and
this directive is not a release number. No Allegro here.

**Ohm's netlist, `"E"`.** The export-format version letter KiCad's own netlist
exporter currently writes. Ohm's netlist is a sibling format a BOM/CAM tool reads
the same way, so it carries the same token rather than inventing a competing one.
There is no external reader to verify it against, because it is not an external
format.

**`constraints.json`, `"1"`.** The open↔proprietary seam. No reader outside this
project, no migration path, and it has never been version 2. `shared/` owns it and
`ohm/formats.py` imports it rather than defining it, because `shared/` must not
depend on `ohm/`.

### 6. IPC-7351 revision B is a finding, not an omission

`ohm/pkggen/packages.py:45,243` says IPC-7351**B**, and someone will eventually
ask why not C. **IPC-7351C was drafted, presented and built into CAD tools, and
was never published.** B (June 2010) remains the published standard in 2026.
Nothing to bump; the value is already current, and the interesting fact is that
its apparent successor does not exist.

Two things worth recording alongside it:

- Ohm's density names map to IPC's levels: `least` → Level C (least material
  condition, high density), `nominal` → Level B, `most` → Level A (most material,
  low density). Ohm names the fillet goal; IPC names the level.
- **The validation oracle is KiCad's own library, not the standard's worked
  examples.** `packages.py:82-83` records that QFN nominal "reproduces KiCad 7's
  official QFN land pattern exactly (validated vs QFN-16/32/48, P0.5mm)" and
  `:96-97` that QFP nominal "matches KiCad 7's LQFP-32/48/64 land patterns within
  tolerance (≤0.05 mm on pad center/length)". That is a stronger oracle than
  reproducing a table from a PDF — it is agreement with land patterns thousands of
  boards have been fabricated from — and it is a different claim, which is why it
  is written down rather than implied by the "IPC-7351B" label.

## Consequences

- **The registry is now a claim with evidence behind it**, per row. Four rows say
  "unverified by any reader" in as many words. That is the information a future
  bump proposal needs, and it was previously unavailable at any price.
- **Two new executable gates.** `tests/test_step.py`'s gmsh oracle now actually
  runs from the plain `pytest` command rather than skipping (`pip install gmsh`
  puts the executable in the venv's `Scripts/`, which is not on `PATH` unless the
  venv was activated; the launcher also had to bypass a broken `gmsh.bat` shim).
  `tests/test_zen_pcb.py` is new and drives the real `pcb` binary.
- **ADR-002 section 5's deferral is closed**, and closing it cost an export
  rewrite. A cross-check deferred for a year is how a broken exporter stays
  described as universal.
- **Four stamps stay unverifiable here**, and no amount of documentation changes
  that. Verifying Eagle, Altium and Allegro output needs those tools, which are
  commercial and, in Eagle's case, no longer licensable at all. The honest record
  is the deliverable.
- **A maintenance obligation, dated.** This survey is a snapshot of 2026-08-31.
  Tool versions move; `ohm/formats.py`'s reasons and this table are where the next
  survey starts, and `tests/test_formats.py` fails if a stamp is registered but not
  written, or written but not registered.

## Alternatives rejected

- **Bump everything to the newest available.** The direct inversion of the policy.
  It would cost older installations the ability to open Ohm's files, buy nothing
  (every reader here reads forward), and for the Altium and Allegro strings it
  would replace a fact about what the authoring tool writes with a guess.
- **Bump STEP to AP242 edition 2 or 3.** Edition 2 (`3 1 4`) is what ST-Developer
  writes today and gmsh reads it. But the policy asks for the *oldest* current
  stamp, and every AP242 edition is current in the sense that matters — the
  protocol is maintained. Edition 1 is the widest-compatibility member of the
  right protocol family, which is exactly what the policy selects for.
- **Leave STEP on AP214 because it still works.** This was very nearly right, and
  is what the previous loop chose. It fails on corollary one: "a reader still
  accepts it" is a statement about installed software, and the stamp is a claim
  about which protocol the file conforms to. A file claiming a protocol its
  standards body abandoned twelve years ago is stale in a way no reader will ever
  complain about, which is precisely why a policy has to catch it.
- **Give `.zen` a stamp without running `pcb`.** The version would have been
  `"0.4"` either way, and it would have been a decoration. Running the binary is
  what found that the export did not load — the stamp is worth having *because*
  of what obtaining it cost.
- **Emit `symbol=Symbol("<name>.kicad_sym")` so the `.zen` looks conventional.**
  `emit_zen` receives IR-LOW, which has no schematic geometry, and writes no
  `.kicad_sym` beside its output. The reference would resolve on no machine
  anywhere. `pin_defs` carries the same pin→pad map from data Ohm actually has.
- **Set `skip_bom=True` on parts with no manufacturer, to get a clean `pcb
  build`.** It would silence a true complaint. Ohm's own M001 makes the identical
  finding about the identical designs; the honest export hands the downstream tool
  the same gap rather than papering over it.
- **A per-format ADR.** Eleven of these stamps share one policy and one survey
  date. Splitting them would hide the only comparison that matters — which stamps
  have an oracle and which do not.


---

## Amendment, 2026-09-03 — four entries the survey above did not have

`ohm/formats.py` holds **nineteen** entries, not the fifteen this ADR surveyed.
Four were added afterwards, and none of them changed a byte Ohm writes — they are
stamps that were *already being written* as bare literals and had simply never
reached the registry:

| Entry | Owner | Why it was missing |
|---|---|---|
| `_PRO_PROJECT_VERSION` | `ohm/emit_kicad_pro.py` | the `.kicad_pro` document version |
| `_PRO_ERC_VERSION` | `ohm/emit_kicad_pro.py` | the `erc` section version |
| `_PRO_NET_SETTINGS_VERSION` | `ohm/emit_kicad_pro.py` | the `net_settings` section version |
| `_STEP_PROTOCOL` | `ohm/pkggen/step.py` | the DATA section's half of AP242's identity |

The first three are the interesting ones. `emit_kicad_pro` appeared in neither
the registry nor its owner gate, so a whole emitter — writing three KiCad schema
versions — was invisible to a round whose stated purpose was to abolish exactly
that. The gate is now discovery-based (`tests/test_formats.py::_candidate_owners`
globs `ohm/emit_*.py` and `ohm/pkggen/*.py` rather than comparing against a
hand-written list), so an unregistered emitter fails the build instead of being
absent from a table nobody diffs.

The fourth closes a stated-but-unenforced invariant: `ohm/pkggen/step.py` said
outright that the DATA section's protocol name "has to agree with FILE_SCHEMA
above or the file claims one protocol in the header and another in the data", and
nothing checked it. `test_the_step_protocol_agrees_with_the_file_schema` now
derives one from the other.

**The count is now pinned.** Three documents asserted "fifteen" — this ADR,
`ohm/formats.py`'s module docstring and `docs/ci/12_multi_eda_version_policy.md` —
for a whole round after it became nineteen. A number in prose is exactly the kind
of unnamed literal `ohm/formats.py` exists to abolish, and it had one of its own.
`tests/test_formats.py::test_the_registry_is_the_size_its_documentation_says`
fails if the registry changes size, and names these three documents in its
message.
