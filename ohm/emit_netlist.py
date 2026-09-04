"""Own netlist emitter — KiCad-style s-expression export format (manual P4).

Deterministic: components ordered by designator's part order in IR-LOW (already
walk-ordered), nets sorted by name (done in P-LOWER). Byte-identical across runs.
"""

from __future__ import annotations

from ohm import ir
from ohm import __version__
from ohm.emit_common import BOM_FIELDS, _source_id, _value_text
from ohm.formats import _NETLIST_VERSION

__all__ = ["BOM_FIELDS", "emit_netlist"]


def _q(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def emit_netlist(low: ir.IRLow, source: str = "<string>") -> str:
    # The file NAME, never the path the caller typed. `_source_id` was written
    # for the schematic uuids and applied only there, so this artifact still
    # carried whatever the caller typed — two checkouts at different paths
    # produced different bytes, breaking the determinism invariant, and an
    # absolute path (drive letter and user name included) travelled to whoever
    # the file was handed to.
    source = _source_id(source)
    out = [f'(export (version "{_NETLIST_VERSION}")']
    out.append(f"  (design (source {_q(source)}) (tool {_q('ohm ' + __version__)}))")
    out.append("  (components")
    for p in low.parts:
        if isinstance(p.package, str):
            fp = p.package
        elif isinstance(p.package, ir.GenerateSpec):
            from ohm.pkggen.packages import spec_name

            fp = f"ohm:{spec_name(p.package)}"  # sibling generated .kicad_mod (P11)
        else:
            fp = ""
        attrs = dict(p.attrs)
        raw = attrs.get("value")
        value = _value_text(raw) if raw is not None else str(attrs.get("mpn", p.component))
        # A netlist whose parts carry only "100nF" is not orderable: two 0402
        # 100nF caps differ by dielectric, voltage and tolerance, and the design
        # already said which one it meant. Carry the sourcing attrs through under
        # the field names KiCad's own BOM tooling reads.
        fields = "".join(
            f" (field (name {_q(BOM_FIELDS[k])}) {_q(_value_text(attrs[k]))})"
            for k in BOM_FIELDS if attrs.get(k) is not None
        )
        out.append(
            f"    (comp (ref {_q(p.designator)}) (value {_q(value)})"
            f" (footprint {_q(fp)}) (fields (field (name \"ohm_ref\")"
            f" {_q(p.ref)}){fields}))"
        )
    out.append("  )")
    out.append("  (nets")
    for code, net in enumerate(low.nets, start=1):
        nodes = "".join(
            f" (node (ref {_q(n.designator)}) (pin {_q(n.pad or n.pin)}))" for n in net.nodes
        )
        out.append(f"    (net (code {_q(str(code))}) (name {_q(net.name)}){nodes})")
    out.append("  )")
    out.append(")")
    return "\n".join(out) + "\n"
