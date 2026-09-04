"""P7: .zen export — IR-LOW → Starlark using the current `Component()` surface.

Deterministic: nets in IR-LOW order (sorted), pins sorted by name, sanitized
variable names uniquified stably. Interop only — never a build dependency.

WHAT THIS EMITS, AND WHY IT CHANGED (ADR-002 revision, 2026-08-31)
-----------------------------------------------------------------
The old export wrote `Component(name=, footprint=, prefix=, pins={...})` and
called it "the universal Component() form". It was not universal; it did not
load. Run against diodeinc/pcb 0.4.41 — the first time a real `pcb` binary was
available to this project, which is what ADR-002 section 5 had been waiting for —
`pcbc build` rejects it twice over:

    Either `pin_defs` or a Symbol value for `symbol` must be provided
    [bom.unspecified] Component 'U1' is included in the BOM but is missing part
    information. Specify `part=Part(...)`.

So two things had to change, and both are recorded here rather than in a commit
message because the reasoning is the interesting part:

1. `pin_defs`. Zener needs a pin->pad map before it will accept a Component, and
   normally that map arrives with a `Symbol(library="X.kicad_sym")`. **Ohm has no
   symbol object to give**: `emit_zen` is handed IR-LOW, which carries parts,
   nets, pins and pads and no schematic geometry at all, and pointing `symbol=`
   at a `.kicad_sym` path that this export does not write would be a reference
   that resolves on no machine anywhere. `pin_defs` is the other half of the
   tool's own error message — the symbol-free way to declare the same map — and
   Ohm has exactly the data it wants: `ir.NetNode.pad`. Where a pin has no mapped
   pad the pin's own name is used as the pad, which is the same fallback
   `emit_netlist.py` already applies ("netlist pin number = pad if mapped, else
   pin name"), so the two exports agree about what a pad is.

   `pin_defs` is not in the published spec (docs/pages/spec.mdx, checked
   2026-08-31, documents `symbol` as required and never mentions `pin_defs`). It
   is what the implementation asks for by name in its own diagnostic, and
   `tests/test_zen_pcb.py` builds the emitted file with the real binary, so this
   is a checked fact rather than a read one.

2. `part=Part(...)` over the legacy `mpn=`/`manufacturer=` kwargs. 0.4.41 marks
   the bare kwargs deprecated ("`mpn=...` is deprecated; pass
   `part=Part(mpn=..., manufacturer=...)`") and `Part` requires BOTH fields
   non-empty. Ohm's components carry `mpn` far more often than `manufacturer`, so
   the pair is only sometimes formable; where only one is known the legacy kwarg
   is written for that one. That leaves the file with a real `bom.underspecified`
   error from `pcbc` — deliberately. It is the same gap Ohm's own M001 reports
   ("parts with no mpn and no dnp — the BOM line is not orderable"), so the
   honest export is the one that hands the downstream tool the same complaint,
   not one that invents a manufacturer or sets `skip_bom` to quiet a warning that
   is telling the truth about the design.

Nothing here is a build dependency: `tests/test_zen.py` asserts `driver.py` and
`lower.py` mention neither this module nor `imp_zen` (D1/N5).
"""

from __future__ import annotations

import re

from ohm import ir
from ohm.emit_common import _source_id
from ohm.formats import _ZEN_PCB_VERSION

_IDENT = re.compile(r"[^A-Za-z0-9_]")


def _varname(net_name: str, taken: dict[str, str]) -> str:
    base = _IDENT.sub("_", net_name).upper() or "N"
    if base[0].isdigit():
        base = "N_" + base
    var, i = base, 1
    while var in taken and taken[var] != net_name:
        i += 1
        var = f"{base}_{i}"
    taken[var] = net_name
    return var


def _refname(ref: str, taken: dict[str, str]) -> str:
    """An IR-LOW part ref as a legal Zener instance name.

    Ohm refs are hierarchical paths — `mcu.u`, `sens.s` — and 0.4.41 refuses them
    outright: `Component name cannot contain invalid characters ".". Got: "mcu.u"`.
    The dots become underscores, and a collision between `mcu.u` and a literal
    `mcu_u` is broken the same stable way `_varname` breaks net-name collisions, so
    the export stays deterministic and never silently merges two parts into one.
    """
    base = _IDENT.sub("_", ref) or "U"
    if base[0].isdigit():
        base = "U_" + base
    name, i = base, 1
    while name in taken and taken[name] != ref:
        i += 1
        name = f"{base}_{i}"
    taken[name] = ref
    return name


def _sourcing(attrs: dict[str, ir.Value]) -> list[str]:
    """The BOM identity lines for one Component, in the current spec's order.

    `Part(...)` when both halves are known, the deprecated bare kwargs when only
    one is — see the module docstring on why a half-known part is exported half
    known rather than completed with a guess.
    """
    mpn = attrs.get("mpn")
    manufacturer = attrs.get("manufacturer")
    both = isinstance(mpn, str) and mpn and isinstance(manufacturer, str) and manufacturer
    if both:
        return [f'    part=Part(mpn="{mpn}", manufacturer="{manufacturer}"),']
    lines = []
    if isinstance(mpn, str) and mpn:
        lines.append(f'    mpn="{mpn}",')
    if isinstance(manufacturer, str) and manufacturer:
        lines.append(f'    manufacturer="{manufacturer}",')
    return lines


def emit_zen(low: ir.IRLow, source: str = "<string>") -> str:
    # The file NAME, never the path the caller typed. `_source_id` was written
    # for the schematic uuids and applied only there, so this artifact still
    # carried whatever the caller typed — two checkouts at different paths
    # produced different bytes, breaking the determinism invariant, and an
    # absolute path (drive letter and user name included) travelled to whoever
    # the file was handed to.
    source = _source_id(source)
    # The lane goes in the file, not in a manifest: `emit_zen` produces one `.zen`
    # and no workspace around it, so a reader who finds this file on its own can
    # still tell which `Component()` surface it was written against. `pcb.toml`'s
    # own field name is quoted so the line reads as the setting it corresponds to.
    out = [f"# generated by ohm from {source}",
           f'# targets the diodeinc/pcb toolchain lane pcb-version = "'
           f'{_ZEN_PCB_VERSION}"', ""]
    taken: dict[str, str] = {}
    refs: dict[str, str] = {}
    net_var: dict[str, str] = {}
    for net in low.nets:
        var = _varname(net.name, taken)
        net_var[net.name] = var
        out.append(f'{var} = Net("{net.name}")')
    out.append("")
    # (pin, net variable, pad) per part, in the IR's net order; sorted per part below
    pin_net: dict[str, list[tuple[str, str, str]]] = {}
    for net in low.nets:
        for node in net.nodes:
            pin_net.setdefault(node.part_ref, []).append(
                (node.pin, net_var[net.name], node.pad or node.pin))
    for part in low.parts:
        attrs = dict(part.attrs)
        prefix = "".join(c for c in part.designator if c.isalpha()) or "U"
        wired = sorted(pin_net.get(part.ref, []))
        # Every pin named in `pin_defs` must also be connected in `pins` — 0.4.41
        # errors "Unconnected pin(s): ..." otherwise — so both dicts are built from
        # the same wired-endpoint list and cannot drift apart.
        defs = ", ".join(f'"{pin}": "{pad}"' for pin, _, pad in wired)
        pins = ", ".join(f'"{pin}": {var}' for pin, var, _ in wired)
        lines = [f'    name="{_refname(part.ref, refs)}",']
        if isinstance(part.package, str) and part.package:
            lines.append(f'    footprint="{part.package}",')
        lines.append(f'    prefix="{prefix}",')
        lines.append(f"    pin_defs={{{defs}}},")
        lines.append(f"    pins={{{pins}}},")
        lines.extend(_sourcing(attrs))
        if isinstance(datasheet := attrs.get("datasheet"), str) and datasheet:
            lines.append(f'    datasheet="{datasheet}",')
        if attrs.get("dnp") == "true":  # Ohm lowers boolean attrs to "true"/"false"
            lines.append("    dnp=True,")
        out.append("Component(")
        out.extend(lines)
        out.append(")")
    return "\n".join(out) + "\n"
