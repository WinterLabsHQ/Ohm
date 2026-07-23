"""P-CONNECT + P-LOWER: hierarchical Design → flat IR-LOW.

One deterministic walk from the top module: flatten instances (uniquified as
`<module-path>.<local-name>`), expand `~` endpoints into signal slots, union
slots into nets. Interface-typed endpoints expand member-by-member matched by
name (type/role legality is Tier-I's job, P6 — lowering only wires).

Net naming (deterministic, order-independent): shallowest (fewest dots), then
lexicographically smallest port-member label on the net, else pin label.
"""

from __future__ import annotations

from ohm import ir
from ohm.diagnostics import Diagnostic, Span

_PIN, _PORT = "pin", "port"


def first_by_name(items) -> dict:
    """name -> item, first occurrence wins (matches `next(... if x.name == n)`)."""
    d: dict = {}
    for it in items:
        d.setdefault(it.name, it)
    return d


def lower(design: ir.Design, path: str = "<string>") -> tuple[ir.IRLow, list[Diagnostic]]:
    diags: list[Diagnostic] = []
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
            pad = pin.pad if pin else None
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

    def walk(module: ir.Module, prefix: str, stack: frozenset[str]) -> None:
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
        port_labels = tuple(lbl for lbl in members if slots[lbl][0] == _PORT)
        nodes = tuple(
            sorted(
                (ir.NetNode(info[1], des_of[info[1]], info[2], info[3])
                 for lbl in members if (info := slots[lbl])[0] == _PIN),
                key=lambda n: (n.part_ref, n.pin),
            )
        )
        # shallowest label wins: a top-level `vin.v` must beat a deep `a.pwr.v`
        name = min(port_labels or members, key=lambda lbl: (lbl.count("."), lbl))
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
