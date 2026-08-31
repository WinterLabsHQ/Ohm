"""P7: .zen import — bounded Starlark subset → IR-LOW (ADR-002).

Starlark is syntactically a subset of Python, so the stdlib `ast` parses .zen
files directly. Interop only (D1/N5): never called from `ohm build`.

STRUCTURAL, NOT VERSION-ANCHORED. This module reads files other people wrote, and
the argument ADR-021 makes for Ohm's KiCad readers applies unchanged: a reader
that recognises exactly one spelling of a construct is a reader that breaks on the
next release, so the accepted vocabulary is a superset and anything outside it is
skipped with a diagnostic rather than treated as a parse failure. Concretely, both
Component forms are accepted side by side:

  * LEGACY  — `Component(name=, footprint=, prefix=, pins={...})`, which is what
    Ohm itself exported until 2026-08-31 and what any `.zen` written against the
    older surface still looks like.
  * CURRENT — `Component(name=, symbol=Symbol(...) | pin_defs={...}, pins={...},
    part=Part(mpn=, manufacturer=), prefix=, footprint=, datasheet=, ...)` as
    diodeinc/pcb 0.4.41 defines it.

Two of the current-surface fields carry information the old walker threw away:

  * `pin_defs` is a pin -> pad map, and `ir.NetNode` has a `pad` slot that this
    importer used to leave `None` on every node. It is now filled in from
    `pin_defs` where the file supplies one. That is why `emit_ohm.py` no longer
    has to invent a pad from the shape of the pin's NAME for these parts.
  * `part=Part(mpn=..., manufacturer=...)` replaced the bare `mpn=` kwarg, which
    0.4.41 still accepts but reports as deprecated. Both spellings are read, and
    a `Part(...)` bound to a variable first (the form the published spec's own
    example uses) resolves through that variable.

`symbol=` is accepted and deliberately NOT recorded: the pin->pad map it points at
lives in a `.kicad_sym` this importer does not open, so there is nothing behind the
reference for Ohm to carry, and inventing an attr out of the library path would
put a filename where a pinout belongs.
"""

from __future__ import annotations

import ast

from ohm import ir
from ohm.diagnostics import Diagnostic, Span

_NET_CTORS = {"Net", "Power", "Ground", "NotConnected"}
_SKIP_CTORS = {"interface", "config", "load", "Part", "Symbol"}
# String-valued Component kwargs carried straight through to IR-LOW attrs.
# `manufacturer` and `type` are new with the current surface; `mpn` stays because
# 0.4.41 still accepts it (deprecated) and every older .zen in the wild uses it.
_COMPONENT_ATTRS = ("prefix", "mpn", "manufacturer", "type", "datasheet")


def _span(node: ast.AST | None) -> Span:
    if node is None:
        return Span(1, 1, 1, 1)
    return Span(getattr(node, "lineno", 1), getattr(node, "col_offset", 0) + 1,
                getattr(node, "end_lineno", 1), getattr(node, "end_col_offset", 0) + 1)


def _call_name(call: ast.Call) -> str | None:
    return call.func.id if isinstance(call.func, ast.Name) else None


def _str_const(node: ast.AST | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


class _ZenImporter:
    def __init__(self, path: str):
        self.path = path
        self.diags: list[Diagnostic] = []
        self.net_vars: dict[str, str] = {}  # var -> net name
        self.config_vars: set[str] = set()  # vars bound to config(...) — not nets
        self.nonnet_vars: set[str] = set()  # NotConnected()/part-bound vars — never nets
        self.module_vars: dict[str, str] = {}  # var -> generic path
        self.part_vars: dict[str, list[tuple[str, str]]] = {}  # var -> Part(...) attrs
        self.nets: dict[str, list[tuple[str, str]]] = {}  # net name -> [(ref, pin)]
        self.pads: dict[tuple[str, str], str] = {}  # (ref, pin) -> pad, from pin_defs
        self.parts: list[ir.PartLow] = []
        self.anon = 0

    def _net_of(self, node: ast.AST) -> str | None:
        """Resolve an expression used as a pin value to a net name (or None)."""
        if isinstance(node, ast.Name):
            if node.id in self.net_vars:
                return self.net_vars[node.id]
            if node.id not in self.config_vars and node.id not in self.nonnet_vars:
                self.diags.append(Diagnostic(
                    "warning", "Z002", f"unknown net variable '{node.id}'", _span(node), file=self.path))
            return None
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            base = self.net_vars.get(node.value.id)
            if base is None or node.attr == "NET":  # X.NET is the base net itself
                return base
            return f"{base}.{node.attr}"  # bundle member → its own net
        if isinstance(node, ast.Call):
            name = _call_name(node)
            if name == "NotConnected":
                return None
            if name in _NET_CTORS:
                return self._net_from_ctor(node, var=None)
        return None

    def _net_from_ctor(self, call: ast.Call, var: str | None) -> str:
        name = _str_const(call.args[0]) if call.args else None
        if name is None:
            fn = _call_name(call)
            if fn == "Ground":
                name = "GND"
            elif var is not None:
                name = var
            else:
                self.anon += 1
                name = f"N${self.anon}"
        self.nets.setdefault(name, [])
        return name

    def _part_attrs(self, node: ast.AST | None) -> list[tuple[str, str]]:
        """Read `part=` into (mpn, manufacturer, datasheet) attrs.

        Two spellings, because the current spec uses both: `part=Part(...)` written
        inline, and `p = Part(...)` bound to a variable and passed by name (which is
        what the spec's own Component example does with its symbol). Positional
        arguments are read too — `Part`'s signature is `Part(mpn, manufacturer,
        qualifications=[], datasheet=None)`, so the first two positions are those
        two fields and nothing else is positional.
        """
        if isinstance(node, ast.Name):
            return list(self.part_vars.get(node.id, ()))
        if not (isinstance(node, ast.Call) and _call_name(node) == "Part"):
            return []
        found: dict[str, str] = {}
        for key, arg in zip(("mpn", "manufacturer"), node.args):
            if (v := _str_const(arg)) is not None:
                found[key] = v
        for k in node.keywords:
            if k.arg in ("mpn", "manufacturer", "datasheet"):
                if (v := _str_const(k.value)) is not None:
                    found[k.arg] = v
        return [(k, found[k]) for k in ("mpn", "manufacturer", "datasheet") if k in found]

    def _add_part(self, ref: str, component: str, package: str | None,
                  attrs: list[tuple[str, str]], pins: list[tuple[str, str | None]],
                  node: ast.AST, pads: dict[str, str] | None = None) -> None:
        if any(p.ref == ref for p in self.parts):
            self.diags.append(Diagnostic(
                "error", "Z005", f"duplicate instance name '{ref}'", _span(node), file=self.path))
            return  # keep first, skip duplicate
        designator = ref  # .zen instance names ARE designators (R1, U1, ...)
        self.parts.append(ir.PartLow(ref, designator, component, package, tuple(attrs)))
        for pin, net in pins:
            if net is not None:
                self.nets.setdefault(net, []).append((ref, pin))
        # Pads are recorded HERE, past the duplicate check, and not by the caller:
        # `(ref, pin)` is the key, so a rejected duplicate that reused a name would
        # otherwise overwrite the kept part's pads with the skipped part's — "keep
        # first" has to mean the whole of the first part, pinout included.
        for pin, pad in (pads or {}).items():
            if any(p == pin for p, _ in pins):
                self.pads[(ref, pin)] = pad

    def _component_call(self, call: ast.Call) -> None:
        kw = {k.arg: k.value for k in call.keywords if k.arg}
        ref = _str_const(kw.get("name")) or f"U${len(self.parts) + 1}"
        package = _str_const(kw.get("footprint"))
        attrs: dict[str, str] = {}
        for key in _COMPONENT_ATTRS:
            if (v := _str_const(kw.get(key))) is not None:
                attrs[key] = v
        # `part=Part(...)` wins over a bare `mpn=` on the same call: the bare kwarg is
        # the deprecated spelling of the same field, so if a file carries both, the
        # one the tool would honour is the one Ohm records.
        attrs.update(self._part_attrs(kw.get("part")))
        # `pin_defs` is a pin -> pad map. It only ever adds knowledge — a pin absent
        # from it (or a file that uses `symbol=` instead) keeps pad=None, exactly as
        # before — so it is read straight, with no diagnostic for a partial map.
        pads: dict[str, str] = {}
        if isinstance(defs_node := kw.get("pin_defs"), ast.Dict):
            for knode, vnode in zip(defs_node.keys, defs_node.values):
                pin, pad = _str_const(knode), _str_const(vnode)
                if pin is not None and pad is not None:
                    pads[pin] = pad
        pins: dict[str, str | None] = {}
        pins_node = kw.get("pins")
        if isinstance(pins_node, ast.Dict):
            for knode, vnode in zip(pins_node.keys, pins_node.values):
                if (pin := _str_const(knode)) is not None:
                    if pin in pins:
                        self.diags.append(Diagnostic(
                            "warning", "Z006", f"pin '{pin}' bound more than once",
                            _span(knode), file=self.path))
                    pins[pin] = self._net_of(vnode)  # last binding wins
        else:
            self.diags.append(Diagnostic(
                "warning", "Z004",
                f"component '{ref}' has no statically-readable pins dict",
                _span(call), file=self.path))
        self._add_part(ref, "Component", package, list(attrs.items()),
                       list(pins.items()), call, pads)

    def _generic_call(self, call: ast.Call, generic: str) -> None:
        kw = {k.arg: k.value for k in call.keywords if k.arg}
        ref = _str_const(kw.get("name")) or f"U${len(self.parts) + 1}"
        component = generic.rsplit("/", 1)[-1].removesuffix(".zen")
        package = _str_const(kw.get("package")) or _str_const(kw.get("footprint"))
        attrs: list[tuple[str, str]] = []
        pins: list[tuple[str, str | None]] = []
        for key, node in kw.items():
            if key in ("name", "package", "footprint"):
                continue
            if isinstance(node, ast.Name) and node.id in self.config_vars:
                continue  # config-valued attr: not statically known, not a pin
            net = self._net_of(node) if isinstance(node, (ast.Name, ast.Attribute, ast.Call)) else None
            if net is not None:
                pins.append((key, net))
            elif (s := _str_const(node)) is not None:
                attrs.append((key, s))
        self._add_part(ref, component, package, attrs, pins, call)

    def run(self, source: str) -> ir.IRLow:
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            self.diags.append(Diagnostic(
                "error", "Z001", f"invalid .zen syntax: {exc.msg}",
                Span(exc.lineno or 1, exc.offset or 1, exc.lineno or 1, exc.offset or 1),
                file=self.path))
            return ir.IRLow((), ())
        for stmt in tree.body:
            if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 \
                    and isinstance(stmt.targets[0], ast.Name) and isinstance(stmt.value, ast.Call):
                var = stmt.targets[0].id
                fn = _call_name(stmt.value)
                if fn == "Module":
                    self.module_vars[var] = (_str_const(stmt.value.args[0]) or var) \
                        if stmt.value.args else var
                elif fn == "NotConnected":  # no-connect var: pins wired to it are dropped
                    self.nonnet_vars.add(var)
                elif fn in _NET_CTORS:
                    self.net_vars[var] = self._net_from_ctor(stmt.value, var)
                elif fn == "io":  # boundary bundle → treat as a named net (ADR-002)
                    self.net_vars[var] = var
                    self.nets.setdefault(var, [])
                elif fn == "config":
                    self.config_vars.add(var)
                elif fn == "Part":  # `p = Part(...)` then `Component(..., part=p)`
                    self.part_vars[var] = self._part_attrs(stmt.value)
                    self.nonnet_vars.add(var)
                elif fn == "Symbol":  # a symbol handle: never a net, never an attr
                    self.nonnet_vars.add(var)
                elif fn == "Component":  # assignment-form part: import it, bind var as non-net
                    self._component_call(stmt.value)
                    self.nonnet_vars.add(var)
                elif fn in self.module_vars:
                    self._generic_call(stmt.value, self.module_vars[fn])
                    self.nonnet_vars.add(var)
                # interface/etc: no electrical content for flat interop
            elif isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                fn = _call_name(stmt.value)
                if fn == "Component":
                    self._component_call(stmt.value)
                elif fn in self.module_vars:
                    self._generic_call(stmt.value, self.module_vars[fn])
                elif fn not in _SKIP_CTORS and fn is not None:
                    self.diags.append(Diagnostic(
                        "info", "Z003", f"skipped unsupported construct '{fn}(...)'",
                        _span(stmt), file=self.path))
        nets = tuple(sorted(
            (ir.NetLow(name, tuple(ir.NetNode(ref, ref, pin, self.pads.get((ref, pin)))
                                   for ref, pin in sorted(endpoints)), ())
             for name, endpoints in self.nets.items() if endpoints),
            key=lambda n: n.name))
        return ir.IRLow(tuple(self.parts), nets)


def import_zen(source: str, path: str = "<string>") -> tuple[ir.IRLow, list[Diagnostic]]:
    imp = _ZenImporter(path)
    return imp.run(source), imp.diags


def flat_lints(low: ir.IRLow, path: str = "<string>") -> list[Diagnostic]:
    """What is honestly checkable on a FLAT imported netlist (no declared intent):
    Z007 single-endpoint nets, Z008 fully-unconnected parts. Intent-level tiers
    need annotation — `ohm convert` emits the skeleton for that (ecosystem X0)."""
    diags: list[Diagnostic] = []
    span = Span(1, 1, 1, 1)
    connected = {n.part_ref for net in low.nets for n in net.nodes}
    for net in sorted(low.nets, key=lambda n: n.name):
        if len(net.nodes) == 1:
            node = net.nodes[0]
            diags.append(Diagnostic(
                "info", "Z007",
                f"net '{net.name}' has a single endpoint ({node.part_ref}.{node.pin})",
                span, path))
    for p in sorted(low.parts, key=lambda p: p.ref):
        if p.ref not in connected:
            diags.append(Diagnostic(
                "warning", "Z008", f"part '{p.ref}' has no connections at all", span, path))
    if low.parts:
        diags.append(Diagnostic(
            "info", "Z009",
            f"flat netlist: {len(low.parts)} parts, {len(low.nets)} nets — intent tiers "
            "(kinds, bring-up, quantities) need annotation; run `ohm convert` for the skeleton",
            span, path))
    return diags
