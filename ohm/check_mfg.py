"""DFM/DFT lint (Milestone F, Part C) — manufacturability/testability notes.

  M001  parts with no mpn and no dnp — incomplete BOM (ONE summary warning)
  M002  one mpn spanning different value/package (one warning per mpn)
  M003  R/C/L-lineage passives with no value (ONE summary warning)
  M004  power rail with no test point or connector access (per-net warning)
  M005  0402 chip passives — fine-pitch assembly note (ONE summary info)
  M006  mixed SMT + through-hole — dual-process assembly (ONE info)
  M007  SMT parts but no fiducial (ONE info)
  M008  fine-pitch (<=0.65mm) generated packages — process summary (ONE info)

Never error severity: these are advisory, they must not block a build.

Design-skip heuristic (two gates, both required):
  1. WIRED designs only — if no module in the design has any connection, the
     whole pass returns [] (part-catalog files, std instantiation tests).
  2. BOM-stage only — at least one part must declare mpn= or dnp=. A design
     that has not started assigning orderable parts is schematic-stage; DFM
     lint there is pure noise (and the frozen example corpus asserts zero
     diagnostics on such designs). Once one MPN appears, the whole pass runs.

Part-identity heuristic (documented per plan): std.pcb features (TestPoint,
Fiducial, MountingHole...) are PLAIN components WITHOUT kinds, so they are
recognized by COMPONENT NAME; categories that do declare kinds (Connector,
and the Resistor/Capacitor/Inductor lineage) are recognized through the
transitive kind contract of the part's component. User components reusing a
std.pcb feature name inherit its exemption — acceptable: the names are the
std vocabulary for exactly those features.
"""

from __future__ import annotations

from decimal import Decimal

from ohm import ir, units
from ohm.diagnostics import Diagnostic, Span
from ohm.lower import lower
from ohm.pkggen.packages import _GULLWING  # in-repo family table (default pitch)

_NO_SPAN = Span(1, 1, 1, 1)
# std.pcb mechanical/link features: no MPN needed on the BOM (matched by name —
# these components declare no kinds).
_PCB_FEATURES = {"TestPoint", "Fiducial", "MountingHole", "MountingHolePlated",
                 "NetTie2", "ZeroOhm", "Jumper"}
_PASSIVE_KINDS = {"Resistor", "Capacitor", "Inductor"}
_FINE_PITCH_MM = Decimal("0.65")
# ponytail: substring heuristic on footprint lib ids; extend markers if a new
# THT library family shows up in std.
_THT_MARKERS = ("THT", "PinHeader", "TerminalBlock")


def _refs(refs: list[str], cap: int = 10) -> str:
    shown = ", ".join(refs[:cap])
    extra = len(refs) - cap
    return shown + (f" and {extra} more" if extra > 0 else "")


def _lineage(comp: ir.ComponentDef, kinds: dict[str, ir.KindDef]) -> frozenset[str]:
    out: set[str] = set()
    todo = list(comp.kinds)
    while todo:
        name = todo.pop()
        if name in out:
            continue
        out.add(name)
        kd = kinds.get(name)
        if kd is not None:
            todo.extend(kd.parents)
    return frozenset(out)


def _pitch_mm(spec: ir.GenerateSpec) -> Decimal | None:
    """Effective lead pitch of a generated gullwing package, in mm."""
    fam = _GULLWING.get(spec.package_type)
    if fam is None:
        return None  # chip families: fixed 2-pad geometry, no lead pitch
    for key, val in spec.kwargs:
        if key == "pitch":
            if isinstance(val, Decimal):
                return val  # plain number = mm (pkggen convention)
            if isinstance(val, ir.Quantity):
                return units.convert(val.value, val.unit, "mm")
            return None
    return fam[0]  # family default (SSOP/TSSOP 0.65, SOT23 0.95, SOIC 1.27)


def check_mfg(design: ir.Design, path: str = "<string>",
              low: ir.IRLow | None = None) -> list[Diagnostic]:
    if not any(m.connections for m in design.modules):
        return []  # unwired design: catalog/test context, stay silent
    if low is None:  # standalone use; the driver passes its single lower() result
        low, _ = lower(design, path)
    comps = {c.name: c for c in design.components}
    kinds = {k.name: k for k in design.kinds}
    parts = [p for p in low.parts if p.component in comps]
    part_comp = {p.ref: comps[p.component] for p in parts}
    attrs_of = {p.ref: dict(p.attrs) for p in parts}  # incl. instance overrides
    if not any("mpn" in a or "dnp" in a for a in attrs_of.values()):
        return []  # pre-BOM design: no DFM lint yet (gate 2, see docstring)
    lineage_of = {c.name: _lineage(c, kinds) for c in design.components}
    pins_of = {c.name: {pin.name: pin for pin in c.pins} for c in design.components}
    diags: list[Diagnostic] = []

    # ---- M001: BOM lines with no mpn and no dnp (summary, PCB features exempt) ----
    no_mpn = [p.ref for p in parts
              if part_comp[p.ref].name not in _PCB_FEATURES
              and "mpn" not in attrs_of[p.ref] and "dnp" not in attrs_of[p.ref]]
    if no_mpn:
        diags.append(Diagnostic(
            "warning", "M001",
            f"{len(no_mpn)} part(s) have no mpn (and no dnp): {_refs(no_mpn)}"
            " — the BOM cannot be ordered", _NO_SPAN, file=path))

    # ---- M002: one mpn grouping parts with different value or package ----
    by_mpn: dict[str, list[ir.PartLow]] = {}
    for p in parts:
        mpn = attrs_of[p.ref].get("mpn")
        if isinstance(mpn, str):
            by_mpn.setdefault(mpn, []).append(p)
    for mpn in sorted(by_mpn):
        group = by_mpn[mpn]
        if len({(attrs_of[p.ref].get("value"), p.package) for p in group}) > 1:
            diags.append(Diagnostic(
                "warning", "M002",
                f"mpn '{mpn}' is shared by parts with different value or package: "
                f"{_refs([p.ref for p in group])} — one orderable part is one "
                "value+footprint", _NO_SPAN, file=path))

    # ---- M003: R/C/L-lineage passives with no value (summary) ----
    no_value = [p.ref for p in parts
                if part_comp[p.ref].name not in _PCB_FEATURES
                and (part_comp[p.ref].name in _PASSIVE_KINDS
                     or lineage_of[part_comp[p.ref].name] & _PASSIVE_KINDS)
                and "value" not in attrs_of[p.ref]]
    if no_value:
        diags.append(Diagnostic(
            "warning", "M003",
            f"{len(no_value)} passive part(s) have no value: {_refs(no_value)}"
            " — incomplete BOM line", _NO_SPAN, file=path))

    # ---- M004: power rail with no test point / connector access (per net) ----
    for net in low.nets:
        pins = [pin for node in net.nodes
                if node.part_ref in part_comp
                and (pin := pins_of[part_comp[node.part_ref].name].get(node.pin))]
        if any(pin.role == "ground" for pin in pins):
            continue  # ground: probe anywhere (mounting hole, pour, chassis)
        rail = any(pin.role == "power_out" for pin in pins) or any(
            pin.role == "bidirectional" and pin.voltage is not None for pin in pins)
        if not rail:
            continue
        on_net = {part_comp[node.part_ref].name for node in net.nodes
                  if node.part_ref in part_comp}
        if any(name == "TestPoint" or "Connector" in lineage_of[name]
               for name in on_net):
            continue
        span = net.conn_spans[0] if net.conn_spans else _NO_SPAN
        diags.append(Diagnostic(
            "warning", "M004",
            f"power rail '{net.name}' has no test point and no connector — "
            "unreachable for bring-up and ICT probing", span, file=path))

    # ---- M005: 0402 chip passives — fine-pitch assembly note (summary) ----
    chip0402 = [p.ref for p in parts
                if len(part_comp[p.ref].pins) == 2 and "value" in attrs_of[p.ref]
                and isinstance(p.package, ir.GenerateSpec)
                and p.package.package_type == "CHIP0402"]
    if chip0402:
        diags.append(Diagnostic(
            "info", "M005",
            f"{len(chip0402)} passive(s) use 0402 chip packages: {_refs(chip0402)}"
            " — fine-pitch assembly (placement accuracy, rework difficulty)",
            _NO_SPAN, file=path))

    # ---- M006/M007: process mix + fiducials ----
    def fp(p: ir.PartLow) -> str:
        return p.package if isinstance(p.package, str) else ""

    tht = [p.ref for p in parts if any(m in fp(p) for m in _THT_MARKERS)]
    smt = [p.ref for p in parts
           if isinstance(p.package, ir.GenerateSpec) or "SMD" in fp(p)]
    if tht and smt:
        diags.append(Diagnostic(
            "info", "M006",
            f"board mixes SMT and through-hole parts (THT: {_refs(tht)}) — "
            "dual-process assembly (reflow + wave/selective solder)",
            _NO_SPAN, file=path))
    if smt and not any(part_comp[p.ref].name == "Fiducial" for p in parts):
        diags.append(Diagnostic(
            "info", "M007",
            "board has SMT parts but no Fiducial — pick-and-place optical "
            "registration needs fiducial marks", _NO_SPAN, file=path))

    # ---- M008: fine-pitch generated packages — process summary (ONE info) ----
    fine = []
    for p in parts:
        if isinstance(p.package, ir.GenerateSpec):
            pitch = _pitch_mm(p.package)
            if pitch is not None and pitch <= _FINE_PITCH_MM:
                fine.append(f"{p.ref} ({p.package.package_type} {pitch}mm)")
    if fine:
        diags.append(Diagnostic(
            "info", "M008",
            f"fine-pitch (<= {_FINE_PITCH_MM}mm) packages on board: "
            f"{_refs(fine)} — stencil/paste/inspection process care",
            _NO_SPAN, file=path))
    return diags
