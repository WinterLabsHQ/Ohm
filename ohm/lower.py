"""P-CONNECT + P-LOWER: hierarchical Design → flat IR-LOW.

One deterministic walk from the top module: flatten instances (uniquified as
`<module-path>.<local-name>`), expand `~` endpoints into signal slots, union
slots into nets. Interface-typed endpoints expand member-by-member matched by
name (type/role legality is Tier-I's job, P6 — lowering only wires).

Net naming (deterministic, order-independent): lexicographically smallest
port-member label on the net, else smallest pin label.
"""

from __future__ import annotations

from ohm import ir
from ohm.diagnostics import Diagnostic

_PIN, _PORT = "pin", "port"


def lower(design: ir.Design, path: str = "<string>") -> tuple[ir.IRLow, list[Diagnostic]]:
    diags: list[Diagnostic] = []
    comps = {c.name: c for c in design.components}
    mods = {m.name: m for m in design.modules}
    ifaces = {i.name: i for i in design.interfaces}

    parts: list[tuple[str, ir.ComponentDef]] = []  # (ref, def) in walk order
    slots: dict[str, tuple] = {}  # label -> (_PIN, part_ref, pin_name, pad) | (_PORT,)
    parent: dict[str, str] = {}  # union-find
    edges: list[tuple[str, object]] = []  # (label, connection Span) for diagnostics
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

    def expand(refparts: tuple[str, ...], module: ir.Module, prefix: str) -> list[str]:
        """Endpoint ref → slot labels (one per member for interface endpoints)."""
        base = refparts[0]
        inst = next((i for i in module.instances if i.name == base), None)
        if inst is not None and len(refparts) < 2:
            return []  # depth-1 instance ref (`a ~ b`): resolver's to reject; never crash
        if inst is not None and inst.def_ref in comps:
            comp = comps[inst.def_ref]
            pin_name = refparts[1]
            pin = next((p for p in comp.pins if p.name == pin_name), None)
            pad = pin.pad if pin else None
            return [slot(f"{prefix}{base}.{pin_name}", (_PIN, f"{prefix}{base}", pin_name, pad))]
        if inst is not None and inst.def_ref in mods:
            sub = mods[inst.def_ref]
            port = next((p for p in sub.ports if p.name == refparts[1]), None)
            if port is None:
                return []
            members = [refparts[2]] if len(refparts) > 2 else iface_members(port.interface)
            return [slot(f"{prefix}{base}.{port.name}.{m}", (_PORT,)) for m in members]
        port = next((p for p in module.ports if p.name == base), None)
        if port is not None:
            members = [refparts[1]] if len(refparts) > 1 else iface_members(port.interface)
            return [slot(f"{prefix}{base}.{m}", (_PORT,)) for m in members]
        return []  # config or unresolvable (already diagnosed by resolver)

    def walk(module: ir.Module, prefix: str, stack: frozenset[str]) -> None:
        for inst in module.instances:
            if inst.def_ref in comps:
                parts.append((f"{prefix}{inst.name}", comps[inst.def_ref], inst.overrides))
            elif inst.def_ref in mods and inst.def_ref not in stack:  # cycle guard
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
            labels = [lbl for parts_ in con.ref_args for lbl in expand(parts_, module, prefix)]
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
        # instance overrides win over component attr defaults (R9)
        merged = dict(comp.attrs) | dict(overrides)
        keys = [k for k, _ in comp.attrs] + [k for k, _ in overrides if k not in dict(comp.attrs)]
        attrs = tuple((k, merged[k]) for k in keys)
        parts_low.append(ir.PartLow(ref, des_of[ref], comp.name, comp.package, attrs))

    # group slots into nets
    groups: dict[str, list[str]] = {}
    for label in slots:
        groups.setdefault(find(label), []).append(label)
    nets = []
    net_of_label: dict[str, str] = {}
    for members in groups.values():
        members.sort()
        port_labels = tuple(l for l in members if slots[l][0] == _PORT)
        nodes = tuple(
            sorted(
                (ir.NetNode(info[1], des_of[info[1]], info[2], info[3])
                 for l in members if (info := slots[l])[0] == _PIN),
                key=lambda n: (n.part_ref, n.pin),
            )
        )
        name = port_labels[0] if port_labels else members[0]
        root = find(members[0])
        spans = {s for lbl, s in edges if find(lbl) == root}
        conn_spans = tuple(sorted(spans, key=lambda s: (s.line, s.col, s.end_line, s.end_col)))
        nets.append(ir.NetLow(name, nodes, port_labels, conn_spans))
        for lbl in members:
            net_of_label[lbl] = name
    nets.sort(key=lambda n: n.name)
    constraints = tuple(
        ir.ConstraintLow(kind, tuple(net_of_label[lbl] for lbl in labels if lbl in net_of_label), params)
        for kind, labels, params in raw_constraints
    )
    return ir.IRLow(tuple(parts_low), tuple(nets), constraints), diags
