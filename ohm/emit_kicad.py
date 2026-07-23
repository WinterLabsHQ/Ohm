"""KiCad emitters — minimal valid .kicad_sch / .kicad_pcb, format pinned to KiCad 7.

P4 scope (honest): the schematic carries the design as global labels + text notes;
the board carries the net declarations. Symbol/footprint geometry arrives with
P11 package generation. Files must open clean in KiCad 7.

Deterministic: uuid5 from a fixed namespace + stable path strings — no clock,
no randomness (hermeticity N1).
"""

from __future__ import annotations

import uuid

from ohm import ir

_NS = uuid.UUID("00000000-0000-0000-0000-00000000006f")  # fixed Ohm namespace
_SCH_VERSION = 20230121  # KiCad 7 schematic format
_PCB_VERSION = 20221018  # KiCad 7 board format


def _uid(*parts: str) -> str:
    return str(uuid.uuid5(_NS, "/".join(parts)))


def _q(s: str) -> str:
    """Escape for interpolation inside a double-quoted s-expression string."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def emit_kicad_sch(low: ir.IRLow, source: str = "<string>") -> str:
    out = [
        "(kicad_sch",
        f"  (version {_SCH_VERSION})",
        '  (generator "ohm")',
        f'  (uuid "{_uid(source, "sch")}")',
        '  (paper "A4")',
        "  (lib_symbols)",
    ]
    y = 20
    for p in low.parts:
        if isinstance(p.package, str):
            fp = p.package
        elif isinstance(p.package, ir.GenerateSpec):
            from ohm.pkggen.packages import spec_name

            fp = f"ohm:{spec_name(p.package)}"  # sibling generated .kicad_mod (P11)
        else:
            fp = ""
        out.append(
            f'  (text "{_q(p.designator)}: {_q(p.component)} ({_q(fp)}) [{_q(p.ref)}]"'
            f' (at 20 {y} 0) (effects (font (size 2 2)))'
            f' (uuid "{_uid(source, "part", p.ref)}"))'
        )
        y += 10
    for net in low.nets:
        out.append(
            f'  (global_label "{_q(net.name)}" (shape passive) (at 150 {y} 0)'
            f' (effects (font (size 1.27 1.27)))'
            f' (uuid "{_uid(source, "net", net.name)}"))'
        )
        y += 10
    out.append('  (sheet_instances (path "/" (page "1")))')
    out.append(")")
    return "\n".join(out) + "\n"


def emit_kicad_pcb(low: ir.IRLow, source: str = "<string>") -> str:
    out = [
        "(kicad_pcb",
        f"  (version {_PCB_VERSION})",
        '  (generator "ohm")',
        "  (general (thickness 1.6))",
        '  (paper "A4")',
        "  (layers",
        '    (0 "F.Cu" signal)',
        '    (31 "B.Cu" signal)',
        '    (36 "B.SilkS" user "B.Silkscreen")',
        '    (37 "F.SilkS" user "F.Silkscreen")',
        '    (44 "Edge.Cuts" user)',
        "  )",
        '  (net 0 "")',
    ]
    for code, net in enumerate(low.nets, start=1):
        out.append(f'  (net {code} "{_q(net.name)}")')
    out.append(")")
    return "\n".join(out) + "\n"
