"""P5: Tier-S structural checker (manual Part 2, compiler design §4.2).

Pure structural comparison of things both present in the program (the
self-closing loop): pin↔pad maps vs the declared package. For generate(...)
packages the pad set is fully declared (the built package model), so the loop closes
completely; for external string footprints only program-internal consistency is
checkable (no footprint data at build time — hermeticity N1).

Since R9 allows per-instance `package=` overrides (library blocks), the same
pad-loop check also runs for every instance whose override changes the package.

Never mutates IR; never halts; emits Diagnostics only.
"""

from __future__ import annotations

from decimal import Decimal

from ohm import ir
from ohm.diagnostics import Diagnostic, Span

_NO_SPAN = Span(1, 1, 1, 1)


def _d(sev: str, code: str, msg: str, span: Span | None, path: str, help_: str | None = None) -> Diagnostic:
    return Diagnostic(sev, code, msg, span or _NO_SPAN, file=path, help=help_)


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
        n = len(valid)
        for pin in comp.pins:
            if pin.pad is not None and pin.pad not in valid:
                diags.append(_d("error", "S002",
                                f"pin '{pin.name}' maps to pad '{pin.pad}' but the "
                                f"generated package of {owner} produces pads 1..{n}",
                                pin.span or span, path))
            elif pin.pad is None:
                diags.append(_d("error", "S003",
                                f"pin '{pin.name}' of {owner} has no pad= mapping "
                                f"to its generated package", pin.span or span, path))
        unmapped = sorted(valid - set(seen), key=int)
        if unmapped:
            diags.append(_d("error", "S003",
                            f"generated pads with no pin mapped on {owner}: "
                            f"{', '.join(unmapped)}", span, path))
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


def check_structural(design: ir.Design, path: str = "<string>") -> list[Diagnostic]:
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

    # per-instance package overrides (R9): re-close the pin↔pad loop against the
    # effective package, anchored at the instantiation site
    comps = {c.name: c for c in design.components}
    for module in design.modules:
        for inst in module.instances:
            target = comps.get(inst.def_ref)
            override = dict(inst.overrides).get("package")
            if target is None or override is None:
                continue
            owner = f"'{module.name}.{inst.name}' ({target.name})"
            _check_pad_loop(target, override, owner, inst.span, path, diags)
    return diags
