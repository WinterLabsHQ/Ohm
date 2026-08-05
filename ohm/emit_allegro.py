"""IR-LOW → Cadence Allegro third-party netlist (ADR-020).

Allegro's library formats (`.dra`, `.psm`, `.pad`) are undocumented binary and only
Cadence's own tools write them, so Ohm does not pretend to. What Allegro *does*
document and accept from any third-party front end is the logic import: a triplet of
plain-text files that carries the part list, the pin-level connectivity and the
footprint binding. `File > Import > Logic > Other` reads them directly, and that is
the whole design as far as the board is concerned.

  * `pstchip.dat`  — one `primitive` per component type: its pins, their numbers,
                     their electrical use, and the footprint (`JEDEC_TYPE`) to place.
  * `pstxprt.dat`  — the part list: designator to component type.
  * `pstxnet.dat`  — every net and the nodes on it.

Syntax and the canonical-path shape are taken from a real OrCAD-written triplet, not
from prose. The paths are synthetic here — Ohm has no OrCAD occurrence ids — but they
are deterministic and unique, which is all Allegro uses them for.

Emission is byte-identical run to run: parts follow IR-LOW's walk order and nets are
already name-sorted by P-LOWER.
"""

from __future__ import annotations

from ohm import __version__, ir

# Ohm role -> Allegro PINUSE. Allegro's closed set is
# IN/OUT/BI/TRI/OCA/OCL/POWER/GROUND/UNSPEC/NC. As with the KiCad and Eagle maps,
# every role in resolve.VALID_ROLES must appear: a role falling through to UNSPEC
# silently disables the checks Allegro would otherwise run on the net.
#
# Allegro splits open collector into OCA (with an active pull-up) and OCL (without).
# Ohm's `open_collector`/`open_drain` roles do not say which, and inventing a pull-up
# that the source never declared would be a lie, so both map to the neutral OCL.
PINUSE = {
    "input": "IN", "output": "OUT", "bidirectional": "BI",
    "power_in": "POWER", "power_out": "POWER", "ground": "GROUND",
    "passive": "UNSPEC", "free": "UNSPEC", "nc": "NC",
    "open_collector": "OCL", "open_drain": "OCL",
}

_FOOTPRINT_UNSET = ""


def _q(text: str) -> str:
    """Allegro quotes with `'`; a literal quote is doubled."""
    return "'" + str(text).replace("'", "''") + "'"


def _ident(name: str) -> str:
    """Allegro identifiers are case-insensitive and dislike spaces."""
    return "".join(c if c.isalnum() or c in "_-" else "_" for c in name).upper()


def _footprint(package: ir.Value | None) -> str:
    if isinstance(package, str):
        return package
    if isinstance(package, ir.GenerateSpec):
        from ohm.pkggen.packages import spec_name

        return spec_name(package)
    return _FOOTPRINT_UNSET


def _value(part: ir.PartLow) -> str:
    attrs = dict(part.attrs)
    raw = attrs.get("value")
    if raw is None:
        raw = attrs.get("mpn", part.component)
    if isinstance(raw, ir.Quantity):
        return f"{raw.value}{raw.unit}"
    return str(raw)


def _seg(text: str) -> str:
    r"""Escape one segment of a lowercase canonical path.

    Allegro's own writer wraps any segment carrying a character outside `[a-z0-9_]`
    in backslashes — `\+3.3v\`, `\boostxl-cc2650ma\` — because `.`, `-` and `(` are
    structural in a path. Without it a net named `r1.P1` parses as a hierarchy
    descent and silently loses its nodes.

    Net names no longer reach here with a dot in them: derived names go through
    `readable_net_name` and authored ones are grammar NAMEs, so both are already
    `[A-Za-z0-9_]`. This is kept anyway — it also escapes PART and PIN segments,
    and the failure it prevents is silent node loss in a file nobody re-reads.
    `tests/test_allegro.py` covers it directly rather than through a net name.
    """
    lowered = text.lower()
    plain = all(c.isascii() and (c.isalnum() or c == "_") for c in lowered)
    return lowered if plain else f"\\{lowered}\\"


def _path(root: str, part: ir.PartLow, index: int) -> str:
    """The canonical occurrence path Allegro keys a part by.

    Real OrCAD writes `@<root>.<root>(SCH_1):INS<n>@<lib>.<part>.NORMAL(CHIPS)`. Ohm
    has no occurrence numbering of its own, so the part's index in IR-LOW stands in —
    stable for a given source, which is the property Allegro needs.
    """
    return (f"@{root}.{root}(SCH_1):INS{index}@OHM."
            f"{_ident(part.component)}.NORMAL(CHIPS)")


def _cpath(root: str, part: ir.PartLow, index: int) -> str:
    """The same occurrence, in the escaped lowercase form `C_PATH` takes."""
    return (f"@{_seg(root)}.{_seg(root)}(sch_1):ins{index}@ohm."
            f"{_seg(_ident(part.component) + '.NORMAL')}(chips)")


def _primitive(part: ir.PartLow) -> str:
    """The pstchip.dat primitive name: one per (component, footprint, value)."""
    return _ident(f"{part.component}_{_footprint(part.package)}_{_value(part)}")


def emit_pstchip(design: ir.Design, low: ir.IRLow, stamp: str) -> str:
    """Library-parts file: pin tables and footprint bindings, one per distinct part."""
    from ohm.pkggen.pindata_iface import DeclaredPinData

    comps = {c.name: c for c in design.components}
    provider = DeclaredPinData()
    out = ["FILE_TYPE=LIBRARY_PARTS;", stamp]
    emitted: set[str] = set()
    for part in low.parts:
        name = _primitive(part)
        if name in emitted:
            continue
        emitted.add(name)
        out.append(f"primitive {_q(name)};")
        out.append("  pin")
        for pad, pin_name, role in provider.pin_table(comps[part.component]):
            out.append(f"    {_q(pin_name)}:")
            out.append(f"      PIN_NUMBER='({pad})';")
            out.append(f"      PINUSE={_q(PINUSE.get(role, 'UNSPEC'))};")
        out.append("  end_pin;")
        out.append("  body")
        out.append(f"    PART_NAME={_q(_ident(part.component))};")
        if footprint := _footprint(part.package):
            out.append(f"    JEDEC_TYPE={_q(footprint)};")
        out.append(f"    VALUE={_q(_value(part))};")
        out.append("  end_body;")
        out.append("end_primitive;")
    out.append("END.")
    return "\n".join(out) + "\n"


def emit_pstxprt(low: ir.IRLow, root: str, stamp: str) -> str:
    """Part list: designator -> primitive, with the occurrence path Allegro binds by."""
    out = ["FILE_TYPE=EXPANDEDPARTLIST;", stamp,
           "DIRECTIVES",
           " PST_VERSION='PST_HDL_CENTRIC_VERSION_0';",
           f" ROOT_DRAWING={_q(root)};",
           f" SOURCE_TOOL={_q('ohm ' + __version__)};",
           "END_DIRECTIVES;", ""]
    for index, part in enumerate(low.parts):
        path = _path(root, part, index)
        out.append("PART_NAME")
        out.append(f" {part.designator} {_q(_primitive(part))}:;")
        out.append("")
        out.append("SECTION_NUMBER 1")
        out.append(f" {_q(path)}:")
        out.append(f" C_PATH={_q(_cpath(root, part, index))},")
        out.append(" PRIM_FILE='.\\pstchip.dat',")
        out.append(" SECTION='';")
        out.append("")
    out.append("END.")
    return "\n".join(out) + "\n"


def emit_pstxnet(low: ir.IRLow, root: str, stamp: str) -> str:
    """Connectivity: every net, every node on it, by designator and pin number."""
    paths = {part.designator: _path(root, part, index)
             for index, part in enumerate(low.parts)}
    out = ["FILE_TYPE = EXPANDEDNETLIST;", stamp]
    for net in low.nets:
        out.append("NET_NAME")
        out.append(f"{_q(net.name)}")
        out.append(f" {_q(f'@{root}.{root}(SCH_1):{net.name}')}:")
        out.append(f" C_SIGNAL="
                   f"{_q(f'@{_seg(root)}.{_seg(root)}(sch_1):{_seg(net.name)}')};")
        for node in net.nodes:
            pin = node.pad or node.pin
            out.append(f"NODE_NAME\t{node.designator} {pin}")
            out.append(f" {_q(paths.get(node.designator, node.designator))}:")
            out.append(f" {_q(pin)}:;")
    out.append("END.")
    return "\n".join(out) + "\n"


def emit_allegro(design: ir.Design, low: ir.IRLow, source: str = "design") -> dict[str, str]:
    """The three files Allegro's third-party logic import reads, keyed by filename."""
    root = _ident(design.board.name if design.board is not None else source)
    stamp = f"{{ Using ohm {__version__} }}"
    return {
        "pstchip.dat": emit_pstchip(design, low, stamp),
        "pstxprt.dat": emit_pstxprt(low, root, stamp),
        "pstxnet.dat": emit_pstxnet(low, root, stamp),
    }
