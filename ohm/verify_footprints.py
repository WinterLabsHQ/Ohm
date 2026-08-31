"""Author-time: diff every `package = "Lib:Footprint"` against the REAL library.

`ohm verify-pinouts` does this for symbols (ADR-014); this is the same move for
footprints, and it closes the other half of the same hole.

A string package makes Ohm emit a pad-LESS stub — the geometry comes from the
user's installed KiCad library at layout time, and the only thing that travels
from the source is the pad id, in the netlist. So `pin SHIELD: pad=11` is a
claim about a file Ohm never reads, and it is correct only for as long as the
library on that machine still calls that pad 11. KiCad 10 renamed shield and
shell pads to `"SH"` across 163 footprints; every board naming an old id kept
compiling and shipped with the shield unconnected.

`ohm check` answers that hermetically (N1) from the frozen table in
`ohm/fpdata`. This module is the impure half: it reads the user's disk, so it
lives OUTSIDE the parse->emit path with `ohm/provenance.py`'s verify half and
`ohm/drc.py`, and nothing on that path may import it.

**Where it abstains.** Abstention is the whole reason a tool like this gets
trusted, so it is deliberate in three places:

- A `generate(...)` package is not applicable — Ohm emits that geometry itself,
  and `tests/test_pkggen.py` already diffs it against KiCad's own reference.
- A library that is not under `--lib-dir` is *unverifiable*, not wrong: users
  reference project-local and vendor `.pretty` directories that a KiCad share
  root has never heard of.
- A footprint missing from a library that IS installed is a real defect, and is
  reported as a mismatch. That one KiCad cannot resolve either.
"""

from __future__ import annotations

import re
from pathlib import Path

from ohm import ir

# (component or module.instance, "ok"|"mismatch"|"unverifiable", detail, findings)
Row = tuple[str, str, str, list[str]]

# `(pad` then an id, which KiCad 7 leaves bare and KiCad 9/10 quote. Both
# spellings appear in the wild in the same installation once a user has
# upgraded, and an id-only reader has no reason to care which it is reading.
# `\s` after the id keeps `(padstack ...)` — KiCad 9's per-layer pad stack —
# from being read as a pad named "tack".
_PAD = re.compile(r'\(pad\s+(?:"([^"]*)"|([^\s")]+))\s')

_LIMIT = 24  # pads listed before the tail is summarised, in one finding line


def pad_ids(text: str) -> set[str]:
    """Every pad id a `.kicad_mod` defines.

    Nameless pads (`(pad "" np_thru_hole ...)`, i.e. mounting holes) are dropped:
    they carry no net, so they can never be the pad a pin is asking for.
    """
    return {quoted or bare for quoted, bare in _PAD.findall(text) if quoted or bare}


def _sorted_pads(pads: set[str]) -> list[str]:
    """Numeric pads before alphanumeric ones, the way a datasheet lists them."""
    return sorted(pads, key=lambda p: (len(p), p))


def _summarise(pads: set[str], limit: int = _LIMIT) -> str:
    ordered = _sorted_pads(pads)
    if not ordered:
        return "(none)"
    shown = ", ".join(ordered[:limit])
    return shown if len(ordered) <= limit else f"{shown}, … ({len(ordered)} pads)"


def _near(name: str, candidates: list[str], limit: int = 5) -> list[str]:
    """Footprints in the same library whose names share a long prefix with `name`.

    A missing footprint is nearly always a rename or a variant suffix
    (`..._Horizontal` vs `..._Vertical`), so the actionable half of the report is
    what the library calls it now.
    """
    scored = []
    for cand in candidates:
        shared = 0
        for a, b in zip(name.lower(), cand.lower()):
            if a != b:
                break
            shared += 1
        if shared >= 6:
            scored.append((-shared, cand))
    return [cand for _, cand in sorted(scored)[:limit]]


def _check(owner: str, package: ir.Value, pins: tuple[ir.Pin, ...],
           root: Path, cache: dict[Path, set[str]]) -> Row | None:
    """One (owner, effective package) pair. None means "nothing to say"."""
    if not isinstance(package, str):
        return None  # generate(...) carries its own geometry; see the module docstring
    lib, sep, name = package.partition(":")
    if not sep or not name.strip():
        return (owner, "unverifiable",
                f"package '{package}' does not name a library footprint "
                "(expected \"Lib:Footprint\")", [])

    lib_dir = root / f"{lib}.pretty"
    ref = f"{lib}:{name}"
    if not lib_dir.is_dir():
        return (owner, "unverifiable",
                f"no library '{lib}.pretty' under {root} — a project or vendor library?", [])

    path = lib_dir / f"{name}.kicad_mod"
    if not path.is_file():
        have = sorted(p.stem for p in lib_dir.glob("*.kicad_mod"))
        findings = [f"{lib}.pretty has no footprint '{name}' "
                    f"({len(have)} footprints in that library)"]
        if near := _near(name, have):
            findings.append("did you mean: " + ", ".join(near))
        return (owner, "mismatch", ref, findings)

    if path not in cache:
        try:
            cache[path] = pad_ids(path.read_text(encoding="utf-8", errors="replace"))
        except OSError as exc:
            return (owner, "unverifiable", f"cannot read {path}: {exc.strerror or exc}", [])
    real = cache[path]
    if not real:
        return (owner, "unverifiable", f"{ref} defines no named pads", [])

    declared = [(p.pad, p.name) for p in pins if p.pad is not None]
    if not declared:
        return (owner, "ok", f"{ref} — exists, no pads declared", [])

    missing = [(pad, pin) for pad, pin in declared if pad not in real]
    if not missing:
        return (owner, "ok", ref, [])
    findings = [f"pad {pad} (pin {pin}) is not in this footprint"
                for pad, pin in sorted(missing, key=lambda d: (len(d[0]), d[0]))]
    findings.append(f"the footprint has: {_summarise(real)}")
    return (owner, "mismatch", ref, findings)


def verify_against_footprints(design: ir.Design, lib_dir: str) -> list[Row]:
    """Author-time: re-read the installed `.pretty` libraries and diff every pad.

    NEVER called from `ohm check` — this reads the filesystem outside the project
    and so lives with `signoff` / `verify-pinouts` on the author-time side of N1.

    Per-instance `package=` overrides (R9) are checked at the instantiation site
    as well as the component, because the override is the package that will
    actually be placed — `check_structural` re-closes the pin↔pad loop the same
    way, and a tool that checked only the definition would pass a board whose
    every instance names a footprint that does not exist.
    """
    root = Path(lib_dir)
    cache: dict[Path, set[str]] = {}
    rows: list[Row] = []
    for comp in design.components:
        if comp.package is not None:
            row = _check(comp.name, comp.package, comp.pins, root, cache)
            if row is not None:
                rows.append(row)

    comps = {c.name: c for c in design.components}
    for module in design.modules:
        for inst in module.instances:
            target = comps.get(inst.def_ref)
            override = dict(inst.overrides).get("package")
            if target is None or override is None:
                continue
            row = _check(f"{module.name}.{inst.name} ({target.name})",
                         override, target.pins, root, cache)
            if row is not None:
                rows.append(row)
    return rows


def generated_packages(design: ir.Design) -> int:
    """How many packages this design generates rather than references.

    Reported as a count, not as a row apiece: a design of 40 resistors would
    otherwise print 40 lines saying nothing, and a verification tool people
    scroll past is a verification tool nobody reads.
    """
    n = sum(1 for c in design.components if isinstance(c.package, ir.GenerateSpec))
    for module in design.modules:
        for inst in module.instances:
            if isinstance(dict(inst.overrides).get("package"), ir.GenerateSpec):
                n += 1
    return n
