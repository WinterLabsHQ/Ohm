# 13 · STEP AP214 → AP242

Loop 15 · 2026-08-31 · commit `a0eb5d1`

---

## What it was

`ohm/pkggen/step.py` emits a package body as a `MANIFOLD_SOLID_BREP` — six planar
`ADVANCED_FACE`s over a `FACETED_BREP` prism — for placement, height and
enclosure-fit checks to read: a *dimensional envelope* built from the datasheet
body size, never a vendor-exact model of the plastic. Before this loop
`_HEADER_SCHEMA` was `AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }` — AP214, first
edition — with the matching `APPLICATION_CONTEXT('core data for automotive
mechanical design processes')` and `APPLICATION_PROTOCOL_DEFINITION(...,
'automotive_design', 2000, ...)`.

`tests/test_step.py` meshes the emitted file with gmsh (which embeds
OpenCASCADE) and checks the resulting solid's volume to `rel=1e-9` — an oracle
that fails loudly on a schema a reader rejects, because then there is no solid to
have a volume. It is the only reader in the whole suite that opens an Ohm-emitted
file with an implementation Ohm did not write.

**That oracle had never actually run.** `gmsh` was not in the dev dependencies at
all. `_gmsh_cmd()` located it with `shutil.which`, which finds nothing when the
venv's `Scripts/` directory is off `PATH`. Once it does find something, Windows
prefers `gmsh.BAT` — a shim that launches a bare `python`, which resolves to the
*system* interpreter, not the venv, and dies with `ModuleNotFoundError: No module
named 'gmsh'`, printing nothing; `_mesh` then fails on an empty stdout rather than
on the geometry. Two layers of broken, and the three gmsh-marked tests
(`test_step_is_a_watertight_solid_of_the_right_volume`,
`test_body_sits_on_the_board_and_is_centred`, `test_a_generated_package_body_meshes`)
sat quietly in the skip count. **This is how the AP214 schema went unexamined**:
nothing had ever actually meshed Ohm's STEP output.

`gmsh>=4.13` is now in `pyproject.toml`'s `dev` extras beside `olefile==0.47`, with
the reason recorded at `pyproject.toml:34-38`: it is "the ONLY reader in the suite
that opens a file Ohm emits with an implementation Ohm did not write," and its
absence was "exactly how the AP214 schema went unexamined until 2026-08-31."
`tests/test_step.py`'s `_gmsh_cmd()` now runs the venv's gmsh script directly with
`sys.executable`, bypassing the broken `.bat` shim.

## What changed

`ohm/pkggen/step.py` and `ohm/formats.py`:

- `_HEADER_SCHEMA` → `"AP242_MANAGED_MODEL_BASED_3D_ENGINEERING_MIM_LF { 1 0 10303
  442 1 1 4 }"` — AP242 **edition 1**, not edition 2's `{ 3 1 4 }` or edition 3's
  `{ 4 1 4 }`. The same oldest-that-works rule, applied *inside* the new protocol:
  every AP242 edition is current in the sense that matters (the protocol is
  maintained), so the policy selects the widest-compatibility member of the family.
- `APPLICATION_CONTEXT('Managed model based 3d engineering')`
- `APPLICATION_PROTOCOL_DEFINITION('international standard',
  'ap242_managed_model_based_3d_engineering', 2013, #ctx)`

All three are copied verbatim from OpenCASCADE's own AP242 write lane
(`STEPConstruct_ContextTool.cxx`, `AddAPD()`, `mySchema == 5`), cross-read against
`StepAP214_Protocol.cxx`. This is not a new habit: the AP214 triple these lines
replace was already OCC's `mySchema == 4` lane verbatim, right down to the year
2000.

Two things look like mistakes and are not, both commented at the call site
(`ohm/pkggen/step.py:140-159`):

- The `APPLICATION_PROTOCOL_DEFINITION` schema name is
  `ap242_managed_model_based_3d_engineering` — **no `_mim_lf` suffix**, unlike the
  `FILE_SCHEMA` name directly above it. OCC's `IsAP242()` lower-cases that field
  and string-matches exactly that spelling; "fixing" the asymmetry would make OCC
  stop recognising the file as AP242 on read-back.
- The year is `2013` — OCC's DIS-era date — not ISO's `2014` publication year.
  Nothing reads it; it is descriptive. Agreeing with the one AP242 writer Ohm's
  output can actually be diffed against beats a silent divergence from it.

Why the bump shipped when six other stamps in the same survey did not (ADR-022
§3): the AP214 → AP242 move is decided by ISO/TC 184/SC 4 stopping work on AP203
and AP214 when AP242 published in 2014 — "current" is a property of the
*protocol*, not of what still opens. AP214 was never unreadable; it stopped being
current twelve years ago in a way no reader would ever complain about.

## What proves it

The SOIC-8 body (`4.9 × 3.9 × 1.75` mm) meshes to volume `33.4425` mm³ against
`pytest.approx(4.9 * 3.9 * 1.75, rel=1e-9)` (`33.442499999999995` in float), **one
solid**, no meshing errors — identical to the AP214 baseline, at the untouched
`rel=1e-9` tolerance. `test_header_is_iso_10303_21_ap242` and
`test_the_ap242_triple_agrees_with_the_file_schema` pin the exact strings,
including asserting `"AUTOMOTIVE_DESIGN" not in text` and `"automotive_design"
not in text`, so a partial revert would fail loudly rather than silently mixing
schemas.

The epoch-frozen `FILE_NAME('...', '1970-01-01T00:00:00', ...)` timestamp — what
keeps output byte-identical across machines and days — is untouched, and
`test_emission_is_deterministic` still asserts it.

## Still not proven

- **That any *other* STEP reader accepts the file.** The evidence is
  gmsh/OpenCASCADE — the same library the three literal strings were copied out
  of. It is one implementation agreeing with itself in two roles (source of the
  entities, and reader of the result), not independent confirmation. Say that
  plainly rather than letting "gmsh accepts it" read as "STEP readers accept it."
- **The body stays a dimensional envelope, not a vendor model.** Six planar faces
  over a rectangular prism; no chamfers, no leads, no vendor STEP import. That was
  true before this loop and is unchanged by it.
