"""P5: Tier-S structural checker (manual Part 2, compiler design §4.2).

Pure structural comparison of things both present in the program (the
self-closing loop): pin↔pad maps vs the declared package. For generate(...)
packages the pad set is fully declared (the built package model), so the loop closes
completely; for external string footprints the geometry belongs to the user's
installed library, so the loop closes only as far as the pad IDS — which is what
S012 checks, against the table shipped in `ohm.fpdata` rather than against a disk
Ohm is not allowed to read (hermeticity N1).

Since R9 allows per-instance `package=` overrides (library blocks), the same
pad-loop check also runs for every instance whose override changes the package.

Never mutates IR; never halts; emits Diagnostics only.
"""

from __future__ import annotations

from decimal import Decimal

from ohm import ir
from ohm.diagnostics import Diagnostic, Span
from ohm.fpdata import kicad_majors, known, majors_lacking, pads, present_in, renamed_to, summarise
from ohm.provenance import parse_source, pinmap_digest

_NO_SPAN = Span(1, 1, 1, 1)


def _d(sev: str, code: str, msg: str, span: Span | None, path: str, help_: str | None = None) -> Diagnostic:
    return Diagnostic(sev, code, msg, span or _NO_SPAN, file=path, help=help_)


def _pad_key(pad: str) -> tuple[int, str]:
    """Total order over pad ids. BGA/LGA ids are alphanumeric (A1, B2), so int()
    is not available; length-then-lexical sorts 1..9,10 and never raises."""
    return (len(pad), pad)


def _join(pads: list[str], limit: int = 12) -> str:
    """Join a pad list for a diagnostic, truncated — a 256-ball BGA must not
    produce a multi-KB message."""
    if len(pads) <= limit:
        return ", ".join(pads)
    return ", ".join(pads[:limit]) + f", … ({len(pads) - limit} more)"


def _describe(valid: set[str]) -> str:
    """Describe a generated pad set for S002. Perimeter packages are 1..n;
    grids (A1, B2) and any gap are listed instead."""
    n = len(valid)
    if {str(i) for i in range(1, n + 1)} == valid:
        return f"pads 1..{n}"
    return f"{n} pads ({_join(sorted(valid, key=_pad_key), 4)})"


def _check_pad_loop(comp: ir.ComponentDef, package: ir.Value | None, owner: str,
                    span: Span | None, path: str, diags: list[Diagnostic]) -> None:
    """Validate comp's pin↔pad map against `package` (default or override)."""
    seen = {pin.pad: pin for pin in comp.pins if pin.pad is not None}
    if isinstance(package, ir.GenerateSpec):
        # S006: the generate(...) spec itself must be buildable (family/params)
        from ohm.pkggen.packages import PkgError, build_from_spec

        model = None
        try:
            model = build_from_spec(package)
        except PkgError as exc:
            diags.append(_d("error", "S006",
                            f"invalid generate(...) on {owner}: {exc}", span, path))
        # generated package: pad set comes from the built model (CHIP has pads
        # 1..2 with no pins kwarg) ⇒ close the pin↔pad loop fully
        if model is not None:
            valid = {p.id for p in model.pads}
        else:
            # ponytail: unbuildable spec (S006 above) — best-effort 1..pins so
            # pad-map errors still surface when an integral pins= was given
            pins_kw = dict(package.kwargs).get("pins")
            if not (isinstance(pins_kw, Decimal) and pins_kw == pins_kw.to_integral_value()):
                return
            valid = {str(i) for i in range(1, int(pins_kw) + 1)}
        produces = _describe(valid)
        for pin in comp.pins:
            if pin.pad is not None and pin.pad not in valid:
                diags.append(_d("error", "S002",
                                f"pin '{pin.name}' maps to pad '{pin.pad}' but the "
                                f"generated package of {owner} produces {produces}",
                                pin.span or span, path))
            elif pin.pad is None:
                diags.append(_d("error", "S003",
                                f"pin '{pin.name}' of {owner} has no pad= mapping "
                                f"to its generated package", pin.span or span, path))
        unmapped = sorted(valid - set(seen), key=_pad_key)
        if unmapped:
            diags.append(_d("error", "S003",
                            f"generated pads with no pin mapped on {owner}: "
                            f"{_join(unmapped)}", span, path))
    elif isinstance(package, str):
        # external footprint: only internal consistency is checkable; any pin
        # without pad= would ship its pin name into the netlist (unconnected in KiCad)
        missing = [p.name for p in comp.pins if p.pad is None]
        if missing:
            diags.append(_d("warning", "S004",
                            f"{owner} has pins with no pad= mapping: "
                            f"{', '.join(missing)}", span, path))
    elif package is not None:
        diags.append(_d("error", "S006",
                        f"package of {owner} must be a footprint string or generate(...)",
                        span, path))


# S012 closes the half of the loop S004 leaves open. For an external footprint the
# pad ids are the ONLY thing that travels — Ohm emits a pad-less stub and KiCad
# supplies the geometry from the library on that machine — so a pad id that library
# does not carry lands in the netlist and the pin arrives unconnected. KiCad 10
# renamed shield and shell pads to "SH" across 163 footprints, which is how a
# `pad=11` that was right for years becomes silently wrong on upgrade.


def _fpdata_target(target_kicad: str | None) -> str | None:
    """The major to check against, or None to check against every known major.

    A target the shipped table has no data for — KiCad 7, which has no official
    container image to generate a table from — is not clean, it is unknown, and
    unknown has to fall back to reporting across the majors that ARE covered.
    """
    return target_kicad if target_kicad in kicad_majors() else None


def _no_target_help(target_kicad: str | None) -> str:
    """Say why the answer is a list of versions rather than one verdict."""
    covered = ", ".join(kicad_majors())
    why = ("no --target-kicad was given" if target_kicad is None
           else f"the shipped table has no data for KiCad {target_kicad}")
    return (f"{why}, so the pad is read against every KiCad the table does cover "
            f"({covered}). Name one of those as --target-kicad to get a single verdict.")


def _fate(footprint: str, pad: str, majors: tuple[str, ...]) -> str:
    """What became of `pad` in each major whose copy of `footprint` lacks it."""
    return "; ".join(
        f"KiCad {m} calls it '{new}'" if (new := renamed_to(footprint, pad, m)) is not None
        else f"KiCad {m} has no such pad" for m in majors)


def _check_footprint_pads(comp: ir.ComponentDef, package: ir.Value | None, owner: str,
                          span: Span | None, path: str, target_kicad: str | None,
                          diags: list[Diagnostic]) -> None:
    """S012 — do the pads this component names exist in the footprint it names?

    Silent on anything the shipped table does not cover: a footprint from the
    user's own or a third party's library is unknown, not wrong.
    """
    if not isinstance(package, str) or not known(package):
        return
    target = _fpdata_target(target_kicad)
    mapped = [(pin, pin.pad) for pin in comp.pins if pin.pad is not None]
    if target is not None:
        if not present_in(package, target):
            diags.append(_d("error", "S012",
                            f"{owner} uses footprint '{package}', which KiCad {target} "
                            f"no longer ships", span, path,
                            "KiCad removed this footprint from its own libraries: name the "
                            "part's current footprint, or keep the old one in a library of "
                            "your own and reference it from there"))
            return
        have = pads(package, target)
        if have is None:
            return
        for pin, pad in mapped:
            if pad in have:
                continue
            if renamed_to(package, pad, target) is not None:
                # KiCad renamed this pad; the copper is the same and
                # `ohm.lower._pad_translator` writes the target's spelling into
                # the netlist. Nothing for the author to fix, so nothing is said.
                continue
            else:
                diags.append(_d("error", "S012",
                                f"pin '{pin.name}' of {owner} maps to pad '{pad}', which "
                                f"'{package}' does not have in KiCad {target}",
                                pin.span or span, path,
                                f"KiCad {target} gives that footprint: "
                                f"{summarise(package, target)}"))
        return
    for pin, pad in mapped:
        # A major that merely RENAMED the pad is handled at lowering time, so it
        # is not a finding here either. Reporting it would put a warning on every
        # board using a connector whose shield KiCad 10 renamed — 163 footprints
        # — for something Ohm resolves on its own.
        lacking = tuple(m for m in majors_lacking(package, pad)
                        if renamed_to(package, pad, m) is None)
        if lacking:
            diags.append(_d("warning", "S012",
                            f"pin '{pin.name}' of {owner} maps to pad '{pad}' of "
                            f"'{package}': {_fate(package, pad, lacking)}",
                            pin.span or span, path,
                            _no_target_help(target_kicad)))


# A pin↔pad map is only worth attesting on a part specific enough to order: a
# generic stdlib shape block (no mpn=) is a template, not a claim about silicon.
# Gating on mpn= mirrors the M-tier's own activation rule, so S009 stays silent
# on the standard library and speaks up on exactly the parts PinForge mints.
_PROVENANCE_MIN_PINS = 4


def _check_provenance(comp: ir.ComponentDef, path: str, diags: list[Diagnostic]) -> None:
    """S007/S008/S009 — where this pin↔pad map came from, and whether it moved.

    Hermetic (N1): recomputes the digest from the declared pins. Reading the
    library the source names is `ohm verify-pinouts`, never the build path.
    """
    attrs = dict(comp.attrs)
    source = attrs.get("pinout_source")
    digest = attrs.get("pinout_digest")
    mapped = [p for p in comp.pins if p.pad is not None]

    if source is not None:
        parsed = parse_source(source) if isinstance(source, str) else None
        if parsed is None:
            diags.append(_d("error", "S007",
                            f"'{comp.name}' has an unreadable pinout_source; expected "
                            f'"kicad:<Lib>:<Symbol>" or "datasheet:<reference>"',
                            comp.span, path,
                            'e.g. pinout_source = "kicad:Sensor_Motion:MPU-6000"'))
            return
        if digest is None:
            diags.append(_d("info", "S009",
                            f"'{comp.name}' names a pinout_source but records no "
                            f"pinout_digest, so drift in the pad map cannot be detected",
                            comp.span, path,
                            f'add pinout_digest = "{pinmap_digest(comp.pins)}" '
                            "(or re-run `ohm import-symbol`, which stamps it)"))
            return
        actual = pinmap_digest(comp.pins)
        if digest != actual:
            kind, ref = parsed
            diags.append(_d("error", "S008",
                            f"'{comp.name}' pin↔pad map no longer matches the one taken "
                            f"from {kind} '{ref}': recorded {digest}, now {actual}",
                            comp.span, path,
                            "a pad was moved, added or removed after this pinout was "
                            "verified — re-verify against the source, then update "
                            "pinout_digest. Roles and ratings are not covered by the "
                            "digest and are free to change."))
        return

    if len(mapped) >= _PROVENANCE_MIN_PINS and "mpn" in attrs:
        diags.append(_d("info", "S009",
                        f"'{comp.name}' declares an mpn and {len(mapped)} mapped pads but "
                        f"no pinout_source — S003 proves this map is total, never correct",
                        comp.span, path,
                        "record where the pinout came from: "
                        '`ohm import-symbol <lib>.kicad_sym <Symbol>` stamps it '
                        'automatically, or write pinout_source = "datasheet:<ref>" '
                        "to attest it by hand"))


def _check_package_family(comp: ir.ComponentDef, path: str,
                          diags: list[Diagnostic]) -> None:
    """S010 — a grid family standing in for a perimeter-numbered part.

    Ohm's LGA/BGA are GRID arrays: JEDEC alphanumeric pads, and `border=` keeps the
    outer rings. Datasheets also say "LGA" for leadless PERIMETER packages whose
    pads are numbered 1..N counterclockwise — those are QFN/DFN-shaped here.

    Reaching for LGA on such a part produced, on the board that motivated this, a
    5/3/3/5 ring for a device that is 4 pads per side, with the pin->pad map
    zigzagging between opposite edges. Nothing caught it: the map was total, so
    S003 passed. Detect the tell — a border ring whose sides are unequal, on a
    component whose pins are otherwise sequentially named.
    """
    pkg = comp.package
    if not isinstance(pkg, ir.GenerateSpec) or pkg.package_type not in ("LGA", "BGA"):
        return
    kw = dict(pkg.kwargs)
    if not kw.get("border"):
        return
    try:
        rows, cols = int(str(kw.get("rows", 0))), int(str(kw.get("cols", 0)))
    except ValueError:
        return
    if rows < 2 or cols < 2 or rows == cols and rows % 2 == 0:
        return
    per_side = {rows, cols, rows - 2, cols - 2}
    if len(per_side) <= 1:
        return
    diags.append(_d("info", "S010",
                    f"'{comp.name}' uses generate({pkg.package_type}, border=...) with "
                    f"rows={rows}, cols={cols}, giving a perimeter ring of unequal sides "
                    f"({cols}/{rows - 2}/{rows - 2}/{cols}) and JEDEC grid pad names",
                    comp.span, path,
                    "if the datasheet numbers this part's pads 1..N around the outline "
                    "rather than as a letter/number grid, it is a QFN/DFN here: "
                    f"generate(QFN, pins={2 * rows + 2 * cols - 4}, pitch=..., body=...)"))


def check_structural(design: ir.Design, path: str = "<string>", *,
                     target_kicad: str | None = None) -> list[Diagnostic]:
    diags: list[Diagnostic] = []
    for comp in design.components:
        # S005: a component must declare a package
        if comp.package is None:
            diags.append(_d("error", "S005", f"component '{comp.name}' has no package",
                            comp.span, path, 'add package = "<footprint-id>" or package = generate(...)'))
        # S001: two pins on the same pad
        seen: dict[str, ir.Pin] = {}
        for pin in comp.pins:
            if pin.pad is None:
                continue
            if pin.pad in seen:
                diags.append(_d("error", "S001",
                                f"pins '{seen[pin.pad].name}' and '{pin.name}' of "
                                f"'{comp.name}' both map to pad '{pin.pad}'", pin.span, path))
            else:
                seen[pin.pad] = pin
        _check_pad_loop(comp, comp.package, f"'{comp.name}'", comp.span, path, diags)
        _check_footprint_pads(comp, comp.package, f"'{comp.name}'", comp.span, path,
                              target_kicad, diags)
        _check_provenance(comp, path, diags)
        _check_package_family(comp, path, diags)

    # per-instance package overrides (R9): re-close the pin↔pad loop against the
    # effective package, anchored at the instantiation site
    comps = {c.name: c for c in design.components}
    for module in design.modules:
        for inst in module.instances:
            target_def = comps.get(inst.def_ref)
            override = dict(inst.overrides).get("package")
            if target_def is None or override is None:
                continue
            owner = f"'{module.name}.{inst.name}' ({target_def.name})"
            _check_pad_loop(target_def, override, owner, inst.span, path, diags)
            _check_footprint_pads(target_def, override, owner, inst.span, path,
                                  target_kicad, diags)
    return diags
