# 02 · The version registry — `ohm/formats.py`

Loop 2 · 2026-08-31 · commit `d299c9a`

Ohm emits eight external file formats. Every one carries a version stamp. Before
this loop, **five of them were bare inline literals**.

---

## What it was

| Format | Stamp | Named? |
|---|---|---|
| `.kicad_sch` | `20230121` | yes — `_SCH_VERSION` |
| `.kicad_pcb` | `20221018` | yes — `_PCB_VERSION` |
| `.kicad_mod` | `20221018` | yes — `_FP_VERSION` |
| `.kicad_sym` | `20220914` | yes — `_SYM_VERSION` |
| Eagle `.lbr` | `6.3` | yes — `_EAGLE_VERSION` |
| Altium `.PcbLib` | `PCB 6.0 Binary Library File` | yes — `_PCB_HEADER` |
| Altium `.PcbLib` | `VERSION=3.00` | **no — inline** |
| Altium `.SchLib` | `…Version 5.0` | yes — `_SCH_HEADER` |
| Altium `.SchLib` | `MinorVersion=2` | **no — inline** |
| MS-CFB container | version `3` | **no — inline in a `struct.pack`** |
| Allegro | `PST_HDL_CENTRIC_VERSION_0` | **no — inline** |
| STEP | `AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }` | yes — `_HEADER_SCHEMA` |
| Ohm netlist | `(export (version "E"))` | **no — inline** |
| `constraints.json` | `"1"` | yes — `SCHEMA_VERSION` |

Only the four KiCad constants had a written policy behind them
(`docs/adr/ADR_021_kicad_version_policy.md`, which tabulates them by name). The
other ten had none — no test asserted any value, and none recorded *why that
value rather than a newer one*.

A literal nobody named is a literal anybody can change in one place with nothing
noticing. That is not hypothetical: the KiCad four spent most of their life with
their policy living in two commit messages, which is precisely how ADR-021 came
to be written.

---

## What changed

`ohm/formats.py` — fourteen `FormatVersion(artifact, constant, value, owner,
reason)` entries at the time of this loop. (Fifteen after `.zen` gained a version
in loop 16; **nineteen** once `.kicad_pro`'s three section versions and the STEP
DATA-section protocol name were found still sitting as bare literals — see the
addendum at the foot of this file.) Each emitter imports its constant from the registry instead of
defining it locally; the five inline literals were promoted to named constants
and are used at the emit site.

The module docstring states the policy once instead of nine times:

> **Write the oldest stamp a current reader accepts; bump only when a reader
> proves the new one is needed.**

A newer stamp never buys correctness — every reader in this table reads forward —
and it always costs something: an older installation's ability to open the file,
or, for the Altium and MS-CFB constants, agreement with what the authoring tool
itself writes, which is the only ground truth a reverse-engineered binary format
has. ADR-022 (loop 14) generalises this into a decision record; the registry is
where the policy is *executed*.

**No value changed.** This loop is a pure refactor; the loops that do change a
value (STEP → AP242, Zener → v0.4.x) come later and do so under the written
policy rather than ahead of it.

### The seam keeps its direction

`shared/constraints_schema.py` is the one open ↔ proprietary boundary
(`shared/constraints_schema.py:1-5`), consumed by Meridian and the convergence
solver, which are separate products. If `shared/` imported from `ohm/`, the seam
would depend on the compiler and stop being a seam. So `SCHEMA_VERSION` stays
defined in `shared/` and the **registry imports it**, recorded with
`owner="shared.constraints_schema"` so the direction is visible in the table
rather than something a reader has to infer. Verified against existing
precedent: `ohm/emit_constraints.py`, `ohm/lower.py` and `ohm/resolve.py` all
import *from* `shared`, never the reverse.

---

## What proves it

`tests/test_formats.py`, 18 tests at the time of this loop (23 now):

- Every registered `value` appears in the bytes the named emitter actually
  produces — the registry cannot drift from reality without failing.
- Every entry has a non-empty `reason` and an `owner` module that imports.
- The set of owner modules matches an explicit list, so **a ninth emitted format
  cannot be added without a decision** — it fails until someone registers it.
  (This turned out to be the weak half: an explicit list can only catch a
  REGISTERED entry naming an unknown owner, never an emitter that never
  registered. See the addendum.)
- An AST walk proves `ohm/formats.py` imports nothing outside the stdlib and
  `shared`, mirroring the hermeticity walks in `tests/test_lower_emit.py` and
  `tests/test_verify_footprints.py:241` (invariant N1).

The proof that this was a refactor and not a change: `tests/golden/*.net` and
`tests/golden/canonical.ir.json` pin output bytes and are **untouched**, and
`test_double_build_byte_identical` passes unmodified.

**907 passed, 83 skipped** (889 baseline + 18), ruff and mypy clean.

---

## Still not proven here

- The registry asserts each stamp is *written*. It does not assert any external
  tool *accepts* it — that is what the KiCad matrix does for KiCad, and what no
  job does for Eagle, Altium or Allegro, because no headless version of those
  tools exists to run. See `23_not_proven.md`.

---

## Addendum, 2026-09-02 — the gate could not see an emitter that never registered

Two emitters were still writing bare literals when this loop declared the job
done, and the gate above could not have found either.

- **`ohm/emit_kicad_pro.py`** writes three KiCad schema versions —
  `meta.version` for the project document, the `erc` section and the
  `net_settings` section. The module appeared in neither `REGISTRY` nor
  `EXPECTED_OWNERS`, so ADR-022's table never mentioned `.kicad_pro` at all.
- **`ohm/pkggen/step.py`** writes AP242's identity a SECOND time in the DATA
  section. Its own comment says the two "ha[ve] to agree with FILE_SCHEMA above
  or the file claims one protocol in the header and another in the data", and
  nothing enforced that.

The cause is the shape of the check, not the diligence of the loop:
`{entry.owner for entry in REGISTRY} == EXPECTED_OWNERS` compares the registry
against a hand-written list, and an emitter that never registered appears in
neither side. It is the same blind-allowlist shape as the diagnostic-code gate
that omitted first `V` and then `X`.

`tests/test_formats.py` now DISCOVERS the candidates — every `ohm/emit_*.py`,
every `ohm/pkggen/*.py`, and `shared/` — and requires each to be either
registered or listed in `NO_STAMP` **with the reason it carries none**. The
exemption list is checked for rot in both directions. Registry: 15 → 19.
The STEP halves are now tied together by derivation rather than by a second
copy: the header name is the protocol name plus `_mim_lf`, and a test asserts it.
