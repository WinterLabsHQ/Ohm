"""P-CONNECT + P-LOWER: hierarchical Design → flat IR-LOW.

One deterministic walk from the top module: flatten instances (uniquified as
`<module-path>.<local-name>`), expand `~` endpoints into signal slots, union
slots into nets. Interface-typed endpoints expand member-by-member matched by
name (type/role legality is Tier-I's job, P6 — lowering only wires).

Net naming (deterministic, order-independent): shallowest (fewest dots), then
lexicographically smallest port-member label on the net, else pin label.
"""

from __future__ import annotations

import re

from ohm import ir
from ohm.diagnostics import Diagnostic, Span
from shared.constraints_schema import PLACEMENT_KINDS

_PIN, _PORT = "pin", "port"


def readable_net_name(name: str) -> str:
    """An Ohm instance path (`fmu.spi_fram.miso`) as a net name (`FMU_SPI_FRAM_MISO`).

    Exported because it is the ONE definition of the mapping. Any check that
    reasons about where a derived name came from — M010 asks whether a net is
    named after a part pin — has to ask in the same spelling the net now has,
    or it silently stops matching and reports nothing forever.

    An array subscript is SYNTAX, not a separator: `rs[1].P2` is the second
    resistor's P2, and the name a person writes for it is `RS1_P2`. Turning the
    brackets into separators too gives `RS_1__P2`, which reads like three
    fields. They are dropped; only the dots become underscores.
    """
    return re.sub(r"_+", "_", re.sub(r"\W", "_", name.replace("[", "")
                                     .replace("]", ""))).upper()


def first_by_name(items) -> dict:
    """name -> item, first occurrence wins (matches `next(... if x.name == n)`)."""
    d: dict = {}
    for it in items:
        d.setdefault(it.name, it)
    return d


def _pad_translator(target_kicad: str | None):
    """pin pad -> the pad id that KiCad `target_kicad` calls the same physical pad.

    A `package = "Lib:Footprint"` reference emits a pad-LESS stub, so the pad id
    is the ONLY thing that travels to the layout — and KiCad renames pads between
    releases. KiCad 10 normalised shield and shell pads to "SH" across 163
    footprints, which leaves `pad=11` bound to nothing at all on a KiCad 10 board.

    A rename is not a different pad; it is the same copper under a new name. So
    when the source says which KiCad it is for, the netlist is written in that
    KiCad's spelling. This is why `--target-kicad` exists: the FORMAT is always
    KiCad 7 (ohm/emit_kicad.py), and the LIBRARY VOCABULARY follows the target.

    Translation is deterministic and table-driven (`ohm/fpdata`, generated from
    real libraries by `tools/gen_fpdata.py`), so a build stays reproducible. It
    only ever renames a pad the target does not have into one it does; a pad the
    target already has is never touched, and a pad with no known rename is left
    exactly as declared for S012 to report.
    """
    if target_kicad is None:
        return lambda comp, pad: pad
    from ohm import fpdata

    if target_kicad not in fpdata.kicad_majors():
        # No data for this KiCad (7 has no published container to generate from),
        # so there is nothing to translate against. Leaving the pad as declared is
        # the only honest option: inventing a rename would be a guess on copper.
        return lambda comp, pad: pad

    def translate(comp: ir.ComponentDef, pad: str | None) -> str | None:
        if pad is None or not isinstance(comp.package, str):
            return pad
        have = fpdata.pads(comp.package, target_kicad)
        if have is None or pad in have:
            return pad
        return fpdata.renamed_to(comp.package, pad, target_kicad) or pad

    return translate


def lower(design: ir.Design, path: str = "<string>",
          target_kicad: str | None = None) -> tuple[ir.IRLow, list[Diagnostic]]:
    diags: list[Diagnostic] = []
    pad_for = _pad_translator(target_kicad)
    comps = {c.name: c for c in design.components}
    mods = {m.name: m for m in design.modules}
    ifaces = {i.name: i for i in design.interfaces}
    inst_of = {m.name: first_by_name(m.instances) for m in design.modules}
    pins_of = {c.name: first_by_name(c.pins) for c in design.components}

    # (ref, def, overrides) in walk order
    parts: list[tuple[str, ir.ComponentDef, tuple[tuple[str, ir.Value], ...]]] = []
    slots: dict[str, tuple] = {}  # label -> (_PIN, part_ref, pin_name, pad) | (_PORT,)
    parent: dict[str, str] = {}  # union-find
    edges: list[tuple[str, Span]] = []  # (label, connection Span) for diagnostics
    raw_constraints: list[tuple[str, list[str], tuple[str, ...]]] = []  # (kind, labels, params)

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    def slot(label: str, info: tuple) -> str:
        if label not in slots:
            slots[label] = info
            parent[label] = label
        return label

    def iface_members(name: str) -> list[str]:
        i = ifaces.get(name)
        return [m[0] for m in i.members] if i else []

    def expand(
        refparts: tuple[str, ...], module: ir.Module, prefix: str, create: bool = True
    ) -> list[str]:
        """Endpoint ref → slot labels (one per member for interface endpoints).

        create=False (constraint refs): look up existing slots only — never
        fabricate a slot (a phantom single-node net) for an unconnected pin.
        """

        def mk(label: str, info: tuple) -> str | None:
            if create or label in slots:
                return slot(label, info)
            return None

        base = refparts[0]
        inst = inst_of[module.name].get(base)
        if inst is not None and len(refparts) < 2:
            return []  # depth-1 instance ref (`a ~ b`): resolver's to reject; never crash
        if inst is not None and inst.def_ref in comps:
            comp = comps[inst.def_ref]
            pin_name = refparts[1]
            pin = pins_of[comp.name].get(pin_name)
            pad = pad_for(comp, pin.pad) if pin else None
            lbl = mk(f"{prefix}{base}.{pin_name}", (_PIN, f"{prefix}{base}", pin_name, pad))
            return [lbl] if lbl is not None else []
        if inst is not None and inst.def_ref in mods:
            sub = mods[inst.def_ref]
            port = next((p for p in sub.ports if p.name == refparts[1]), None)
            if port is None:
                return []
            members = [refparts[2]] if len(refparts) > 2 else iface_members(port.interface)
            got = (mk(f"{prefix}{base}.{port.name}.{m}", (_PORT,)) for m in members)
            return [lbl for lbl in got if lbl is not None]
        port = next((p for p in module.ports if p.name == base), None)
        if port is not None:
            members = [refparts[1]] if len(refparts) > 1 else iface_members(port.interface)
            got = (mk(f"{prefix}{base}.{m}", (_PORT,)) for m in members)
            return [lbl for lbl in got if lbl is not None]
        return []  # config or unresolvable (already diagnosed by resolver)

    declared: dict[str, tuple[str, Span | None]] = {}  # label -> (net name, span)

    def walk(module: ir.Module, prefix: str, stack: frozenset[str]) -> None:
        for nn in module.net_names:  # `net NAME = label` (explicit naming)
            declared[f"{prefix}{'.'.join(nn.target)}"] = (nn.name, nn.span)
        for inst in module.instances:
            if inst.def_ref in comps:
                parts.append((f"{prefix}{inst.name}", comps[inst.def_ref], inst.overrides))
            elif inst.def_ref in mods:
                if inst.def_ref in stack:  # cycle: unrealizable hierarchy, not just a skip
                    diags.append(Diagnostic(
                        "error", "R014",
                        f"recursive module instantiation: '{inst.def_ref}' instantiates"
                        f" itself (via '{prefix}{inst.name}')",
                        inst.span or Span(1, 1, 1, 1), file=path,
                    ))
                else:
                    walk(mods[inst.def_ref], f"{prefix}{inst.name}.", stack | {inst.def_ref})
        for conn in module.connections:
            left = expand(conn.left, module, prefix)
            right = expand(conn.right, module, prefix)
            if conn.span is not None and left:
                edges.append((left[0], conn.span))
            if len(left) == 1 and len(right) == 1:
                union(left[0], right[0])
            else:  # interface expansion: pair by member name (last label segment)
                rmap = {lbl.rsplit(".", 1)[1]: lbl for lbl in right}
                for lbl in left:
                    member = lbl.rsplit(".", 1)[1]
                    if member in rmap:
                        union(lbl, rmap[member])
        for con in module.constraints:
            labels: list[str] = []
            for parts_ in con.ref_args:
                if con.kind in PLACEMENT_KINDS:
                    # placement kinds reference INSTANCES: resolve the depth-1
                    # ref to its flat part ref (stored in ConstraintLow.nets,
                    # which holds part refs — not net names — for these kinds)
                    target = inst_of[module.name].get(parts_[0])
                    if len(parts_) == 1 and target is not None and target.def_ref in comps:
                        labels.append(f"{prefix}{parts_[0]}")
                    else:
                        diags.append(Diagnostic(
                            "warning", "I007",
                            f"placement constraint '{con.kind}' references"
                            f" '{'.'.join(parts_)}' which is not a component instance",
                            con.span or Span(1, 1, 1, 1), file=path,
                        ))
                    continue
                got = expand(parts_, module, prefix, create=False)
                if not got:
                    diags.append(Diagnostic(
                        "warning", "I007",
                        f"constraint references '{'.'.join(parts_)}' which is not"
                        " connected to any net",
                        con.span or Span(1, 1, 1, 1), file=path,
                    ))
                labels.extend(got)
            raw_constraints.append((con.kind, labels, con.params))

    top = mods.get(design.top) if design.top else None
    if top is None:
        return ir.IRLow((), ()), diags
    walk(top, "", frozenset({top.name}))

    # designators: walk order, one counter per prefix
    counters: dict[str, int] = {}
    des_of: dict[str, str] = {}
    parts_low = []
    for ref, comp, overrides in parts:
        prefix = str(dict(comp.attrs).get("designator_prefix", "U"))
        counters[prefix] = counters.get(prefix, 0) + 1
        des_of[ref] = f"{prefix}{counters[prefix]}"
        # instance overrides win over component attr defaults (R9) — including
        # the package itself ("Library parts carry default package/footprint;
        # override via attrs")
        merged = dict(comp.attrs) | dict(overrides)
        package = merged.pop("package", comp.package)
        keys = [k for k, _ in comp.attrs] + [k for k, _ in overrides if k not in dict(comp.attrs)]
        attrs = tuple((k, merged[k]) for k in keys if k != "package")
        parts_low.append(ir.PartLow(ref, des_of[ref], comp.name, package, attrs))

    # group slots into nets
    groups: dict[str, list[str]] = {}
    for label in slots:
        groups.setdefault(find(label), []).append(label)
    pending = []  # (name, explicit, members, port_labels, nodes, conn_spans)
    for members in groups.values():
        members.sort()
        port_labels = tuple(lbl for lbl in members if slots[lbl][0] == _PORT)
        nodes = tuple(
            sorted(
                (ir.NetNode(info[1], des_of[info[1]], info[2], info[3])
                 for lbl in members if (info := slots[lbl])[0] == _PIN),
                key=lambda n: (n.part_ref, n.pin),
            )
        )
        # An explicit `net NAME = ...` always wins: naming a signal is a
        # statement about what it IS, and no election over labels can beat it.
        # Two names on one net is a contradiction, so it is reported and the
        # alphabetically first is used to keep the emit deterministic.
        named = sorted({declared[lbl] for lbl in members if lbl in declared})
        if named:
            name = named[0][0]
            if len(named) > 1:
                diags.append(Diagnostic(
                    "error", "R028",
                    "one net, two names: "
                    + ", ".join(f"'{n}'" for n, _s in named)
                    + " all name the same net",
                    named[0][1] or Span(1, 1, 1, 1), file=path,
                    help="the labels are already connected, so they are one "
                         "signal — keep a single `net` declaration for it"))
        else:
            # shallowest label wins: a top-level `vin.v` must beat a deep `a.pwr.v`
            name = min(port_labels or members, key=lambda lbl: (lbl.count("."), lbl))
        root = find(members[0])
        spans = {sp for lbl, sp in edges if find(lbl) == root}
        conn_spans = tuple(sorted(spans, key=lambda sp: (sp.line, sp.col,
                                                         sp.end_line, sp.end_col)))
        # Two different questions, so two flags. `no_collapse` gates the
        # member-stripping below (only a port label may lose its member).
        # `authored` is the narrower fact — the author wrote `net NAME =` — and
        # is the only thing that may veto the readable-name rewrite.
        pending.append((name, bool(named) or name not in port_labels,
                        members, port_labels, nodes, conn_spans, bool(named)))

    # An interface MEMBER name is not part of the signal's identity: `VDD_3V3.v`
    # and `GND.gnd` carry an Ohm implementation detail onto a drawing where the
    # reader wants the rail's name. Drop the trailing member when the port
    # instance identifies the net on its own — exactly one net hangs off that
    # prefix and nothing else claims the bare name. Anything ambiguous (a Power
    # port whose .v and .gnd are distinct nets) keeps its suffix, because there
    # the member IS what distinguishes them.
    #
    # Only PORT labels are collapsed. A name that came from a pin (`r1.P2`) is
    # not a port-and-member at all; stripping it would rename the net after the
    # PART, which is worse than the pin. Those are M010's business instead.
    taken = {n for n, *_ in pending}
    stem_count: dict[str, int] = {}
    for name, explicit, *_ in pending:
        if not explicit and "." in name:
            stem_count[name.rsplit(".", 1)[0]] = \
                stem_count.get(name.rsplit(".", 1)[0], 0) + 1
    collapse: dict[str, str] = {}
    for name, explicit, *_ in pending:
        if explicit or "." not in name:
            continue
        stem = name.rsplit(".", 1)[0]
        if stem_count.get(stem) == 1 and stem not in taken and stem not in collapse.values():
            collapse[name] = stem

    # A derived name is an Ohm INSTANCE PATH; a net name is read by a human on a
    # drawing. `fmu.spi_fram.miso` and `SPI_FRAM_MISO` are the same signal, but
    # only one of them looks like something an engineer chose. Uppercase and
    # underscore the derived ones — the path is kept whole, never shortened,
    # because `accel.spi.cs` and `gyro.spi.cs` are different chip selects and
    # dropping the instance would short them into one `SPI_CS`.
    #
    # Explicit `net NAME = ...` is left exactly as authored: the author already
    # said what this signal is called, and case is part of that.
    # Never merge two nets by renaming. Connectivity in the emitted schematic is
    # carried BY THE NAME — two nets drawn with one label are one net in KiCad —
    # so a rename that lands on a name another net already holds is a short, not
    # a cosmetic clash. The taken set therefore has to hold every name that will
    # survive: the authored ones, and every derived name as it currently stands,
    # because a derived name that is already in readable form (`A_B`) is never
    # itself renamed and so would never appear in `renamed.values()`. Checking
    # only those two was enough to let `a.b` rename onto a sibling `A_B`.
    taken_names = {collapse.get(n, n) for n, *_ in pending}
    renamed: dict[str, str] = {}
    for name, _nc, _m, _p, _nd, _cs, authored in pending:
        name = collapse.get(name, name)
        if authored or name in renamed:
            continue
        cand = readable_net_name(name)
        # A collision means the readable form is ambiguous, so that net keeps
        # the path it can be told apart by. Uglier and correct beats pretty and
        # shorted.
        if cand != name and cand not in taken_names:
            renamed[name] = cand
            taken_names.add(cand)

    nets = []
    net_of_label: dict[str, str] = {}
    for name, _nc, members, port_labels, nodes, conn_spans, _authored in pending:
        name = collapse.get(name, name)
        name = renamed.get(name, name)
        nets.append(ir.NetLow(name, nodes, port_labels, conn_spans))
        for lbl in members:
            net_of_label[lbl] = name
    nets.sort(key=lambda n: n.name)
    constraints = tuple(
        ir.ConstraintLow(
            kind,
            tuple(labels) if kind in PLACEMENT_KINDS  # already flat part refs
            else tuple(net_of_label[lbl] for lbl in labels if lbl in net_of_label),
            params)
        for kind, labels, params in raw_constraints
    )
    return ir.IRLow(tuple(parts_low), tuple(nets), constraints), diags
