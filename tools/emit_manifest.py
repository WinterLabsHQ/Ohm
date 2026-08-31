#!/usr/bin/env python3
"""Hash every artifact Ohm can emit, for the cross-platform determinism gate.

Page 8 of the architecture document states, as an invariant:

    Arithmetic is Decimal, never float — the single square root in the package
    geometry is rounded to 0.01 mm before it re-enters Decimal world. IR dumps
    serialise with sorted keys; files are written UTF-8 with LF newlines on
    every platform.

The point of that paragraph is a promise: **a Windows build and a Linux build of
one source are the same bytes.** The suite has always checked the within-machine
half — `test_double_build_byte_identical` rebuilds and compares on whatever
machine is running — and has never checked the across-machine half, which is the
half the invariant is actually about and the half a `float` or an `os.linesep`
can break without any single machine noticing.

This writes a manifest that can be compared between machines:

    <sha256>  <emit-kind>/<design>/<relative path, forward slashes>

Sorted, so the manifest itself is order-independent. Paths are POSIX-normalised
because `WindowsPath` renders backslashes and the manifest would then differ for
a reason that is not about the artifact bytes.

`--emit all` covers only netlist, kicad, zen and constraints; the library formats
(package, eagle, altium, allegro) are opt-in, and the STEP body arrives with
`package`. So every kind is run explicitly — a determinism gate that silently
skipped the binary formats would be checking the easy half.

Author-time tooling: this shells out to `ohm`, which is why it lives in tools/
and never on the build path (invariant N1). Stdlib only.

    python tools/emit_manifest.py --out manifest.txt
    python tools/emit_manifest.py --check a.txt b.txt c.txt
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Every emit kind the CLI accepts, minus the "all" alias. Named explicitly rather
# than scraped from argparse so that adding a ninth format is a visible edit here
# and not a silent widening of what this gate covers.
EMIT_KINDS = ("netlist", "kicad", "zen", "constraints",
              "package", "eagle", "altium", "allegro")

# Not every design exercises every emitter equally, so the corpus is the golden
# set (which pins bytes elsewhere and is therefore already stable) plus the
# examples (which are richer: hierarchy, diffpairs, placement, netclasses).
def corpus(root: Path) -> list[Path]:
    designs = sorted((root / "tests" / "golden").glob("*.ohm"))
    designs += sorted((root / "examples").glob("*.ohm"))
    return designs


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_one(python: str, root: Path, design: Path, kind: str,
              outdir: Path) -> list[str]:
    """Emit `design` under `kind` and return one manifest line per artifact.

    The design is named by a path RELATIVE to the repo root, with `cwd=root`, and
    that is load-bearing rather than tidiness. `ohm/emit_kicad.py:81` derives every
    schematic and board uuid as `uuid5(namespace, source_path + role)`, where
    `source_path` is the string the caller passed — so naming the same file as
    `tests/golden/divider.ohm` and as `D:/.../tests/golden/divider.ohm` produces
    different uuids and therefore different bytes. Passing absolute paths here
    would make every artifact differ between a Windows checkout and a Linux one,
    and the manifest would report a platform difference that is really a harness
    difference, drowning any real finding.

    That the uuid depends on the typed path at all is its own defect against the
    determinism invariant, and it has its own test — see
    `test_kicad_uuids_do_not_depend_on_the_typed_path`.
    """
    rel = design.relative_to(root).as_posix()
    cmd = [python, "-m", "ohm", "build", rel, "-o", str(outdir), "--emit", kind]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=root)
    if r.returncode != 0:
        # A design that does not compile is a real failure of this gate: the
        # manifest would silently shrink and the comparison would still pass.
        raise SystemExit(f"error: `ohm build --emit {kind} {rel}` exited "
                         f"{r.returncode}\n{r.stdout}\n{r.stderr}")
    lines = []
    for artifact in sorted(p for p in outdir.rglob("*") if p.is_file()):
        rel = artifact.relative_to(outdir).as_posix()
        lines.append(f"{sha256(artifact)}  {kind}/{design.stem}/{rel}")
    return lines


def generate(root: Path, python: str) -> list[str]:
    designs = corpus(root)
    if not designs:
        raise SystemExit(f"error: no designs found under {root}")
    lines: list[str] = []
    for design in designs:
        for kind in EMIT_KINDS:
            tmp = Path(tempfile.mkdtemp(prefix="ohm-manifest-"))
            try:
                lines.extend(build_one(python, root, design, kind, tmp))
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
    return sorted(lines)


def check(paths: list[Path]) -> int:
    """Compare manifests from several machines. Non-zero if any byte differs."""
    if len(paths) < 2:
        raise SystemExit("error: --check needs at least two manifests")
    read = {p: p.read_text(encoding="utf-8").splitlines() for p in paths}
    first, baseline = next(iter(read.items()))
    ok = True
    for path, lines in list(read.items())[1:]:
        if lines == baseline:
            print(f"IDENTICAL  {path.name} == {first.name}  ({len(lines)} artifacts)")
            continue
        ok = False
        print(f"DIFFERENT  {path.name} != {first.name}")
        b = {ln.split("  ", 1)[1]: ln.split("  ", 1)[0] for ln in baseline}
        o = {ln.split("  ", 1)[1]: ln.split("  ", 1)[0] for ln in lines}
        for name in sorted(set(b) | set(o)):
            if b.get(name) != o.get(name):
                print(f"    {name}\n      {first.name}: {b.get(name, '(absent)')}"
                      f"\n      {path.name}: {o.get(name, '(absent)')}")
    if not ok:
        print("\nA differing hash means one platform wrote different bytes for the "
              "same source. Look first for float arithmetic, an unsorted "
              "collection, a native newline, or a locale-dependent encode — the "
              "four things the determinism invariant exists to forbid.")
    return 0 if ok else 1


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, help="write the manifest here")
    ap.add_argument("--check", nargs="*", type=Path, default=None,
                    help="compare two or more manifests instead of generating one")
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    ap.add_argument("--python", default=sys.executable)
    args = ap.parse_args(argv[1:])

    if args.check is not None:
        return check(args.check)

    lines = generate(args.root, args.python)
    text = "\n".join(lines) + "\n"
    if args.out:
        args.out.write_text(text, encoding="utf-8", newline="\n")
        print(f"{len(lines)} artifacts hashed -> {args.out}")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
