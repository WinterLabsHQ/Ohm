# 20 · Cross-platform determinism — the invariant nothing measured

Loop 22 · 2026-08-31 · commit `00980e9`

---

## What it was

Page 8 of the architecture document states determinism as an invariant, and is
specific about the mechanisms:

> Every collection is insertion-ordered or sorted by a stable key. Name
> uniquification uses a fixed scheme; net naming is order-independent.
> Arithmetic is `Decimal`, never float — the single square root in the package
> geometry is rounded to 0.01 mm before it re-enters `Decimal` world. IR dumps
> serialise with sorted keys; files are written UTF-8 with LF newlines on every
> platform.

And page 7 turns it into a promise: *"the same source is the same bytes on any
machine, any day."*

The suite checked the **within-machine** half. `test_double_build_byte_identical`
rebuilds a design and compares the two runs — on whatever machine happens to be
running. The v0.1.1 agent run went further and hashed, deleted `out/`, rebuilt and
compared; also one machine.

Nothing checked the **across-machine** half, which is the half the invariant is
actually about. Every mechanism that paragraph names — a stray `float`, an
unsorted `set`, `os.linesep`, a locale-dependent encode, a `WindowsPath` rendering
backslashes — breaks *across* machines while every single machine stays
self-consistent and green.

---

## What changed

**`tools/emit_manifest.py`** produces a comparable manifest:

```
<sha256>  <emit-kind>/<design>/<relative path, forward slashes>
```

Two decisions in it are load-bearing rather than tidiness:

- **Every emit kind is run explicitly.** `--emit all` covers only `netlist`,
  `kicad`, `zen` and `constraints`; `package` (which carries the STEP body),
  `eagle`, `altium` and `allegro` are opt-in. A determinism gate that silently
  skipped the two binary formats — the MS-CFB container and the packed Altium
  records, which are by far the most likely to carry a platform-dependent
  `struct` or padding bug — would be checking the easy half. The kind list is
  written out rather than scraped from argparse, so a ninth format is a visible
  edit here and not a silent narrowing of coverage.
- **Paths are POSIX-normalised.** A `WindowsPath` renders backslashes, and the
  manifest would then differ for a reason that is not about artifact bytes.

**The CI job** runs on `ubuntu-latest`, `windows-latest` and `macos-latest`, then
a compare step that names the differing artifact and both hashes — *"the
manifests differ"* is not actionable, *"divider.kicad_sch differs between ubuntu
and windows"* is. It needs no KiCad and pulls no image, so it runs on every pull
request: this is the gate most likely to catch a determinism regression at the
moment it is introduced rather than at the next scheduled certification.

---

## What it measured

**Windows 11 / CPython 3.12.13 against Debian / CPython 3.12.14 in a container:
175 artifacts, byte-identical, across all eight emitted formats.**

```
IDENTICAL  manifest-linux.txt == manifest-win.txt  (175 artifacts)
```

The invariant holds. It is now evidence rather than a claim — which is the whole
point, and it is worth saying plainly that the expected outcome of a new gate is
that it passes. The value is that it will keep being run.

---

## The defect getting there found

The first comparison reported **every `.kicad_sch`, `.kicad_pcb` and `.kicad_pro`
differing** between the two platforms. Chasing it produced a genuine bug, and
not the one it looked like.

The files were not platform-dependent at all: byte-for-byte identical when built
the same way on both. What differed was the **invocation**.
`ohm/emit_kicad.py:81`:

```python
def _uid(*parts: str) -> str:
    return str(uuid.uuid5(_NS, "/".join(parts)))
```

called as `_uid(source, "sch")`, where `source` is **the path string the caller
passed on the command line**. So:

```
ohm build tests/golden/divider.ohm            -> uuid 5d81bef0-7da1-57ac-…
ohm build D:/…/tests/golden/divider.ohm       -> uuid 7998f527-d7fb-5a58-…
```

The module docstring at `:34` says *"Deterministic: uuid5 from a fixed namespace
+ stable path strings"*. The path string is not stable; it is whatever was typed.

**Two developers whose checkouts sit at different paths get different bytes for
the same source.** And it matters beyond byte equality: KiCad identifies a sheet
by its uuid, so rebuilding from a different directory reads to KiCad as a
*different sheet*, which is how per-sheet state gets silently orphaned.

The harness now invokes with a repo-relative path and `cwd=root`, so it measures
the platform rather than the invocation — otherwise this one defect would make
every artifact differ and drown anything real. The uuid defect is tracked and
tested separately rather than being allowed to hide inside this gate.

---

## Still not proven

- macOS is in the CI matrix but was not run locally — no macOS machine here. The
  ubuntu/windows pair is measured; the macos leg is wired.
- The manifest compares **emitted artifacts**, not the diagnostics text. Two
  platforms could in principle report the same design differently; nothing here
  would see it.
- The corpus is the golden designs plus `examples/`. A construct exercised by
  neither is not covered by this gate.
