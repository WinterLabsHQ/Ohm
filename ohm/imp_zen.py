"""P7: .zen import — bounded Starlark subset → IR-LOW (ADR-002).

Starlark is syntactically a subset of Python, so the stdlib `ast` parses .zen
files directly. Interop only (D1/N5): never called from `ohm build`.
"""

from __future__ import annotations

import ast

from ohm import ir
from ohm.diagnostics import Diagnostic, Span

_NET_CTORS = {"Net", "Power", "Ground", "NotConnected"}
_SKIP_CTORS = {"interface", "config", "load", "Part", "Symbol"}


def _span(node: ast.AST) -> Span:
    return Span(getattr(node, "lineno", 1), getattr(node, "col_offset", 0) + 1,
                getattr(node, "end_lineno", 1), getattr(node, "end_col_offset", 0) + 1)


def _call_name(call: ast.Call) -> str | None:
    return call.func.id if isinstance(call.func, ast.Name) else None


def _str_const(node: ast.AST) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


class _ZenImporter:
    def __init__(self, path: str):
        self.path = path
        self.diags: list[Diagnostic] = []
        self.net_vars: dict[str, str] = {}  # var -> net name
        self.config_vars: set[str] = set()  # vars bound to config(...) — not nets
        self.module_vars: dict[str, str] = {}  # var -> generic path
        self.nets: dict[str, list[tuple[str, str]]] = {}  # net name -> [(ref, pin)]
        self.parts: list[ir.PartLow] = []
        self.anon = 0

    def _net_of(self, node: ast.AST) -> str | None:
        """Resolve an expression used as a pin value to a net name (or None)."""
        if isinstance(node, ast.Name):
            if node.id in self.net_vars:
                return self.net_vars[node.id]
            if node.id not in self.config_vars:
                self.diags.append(Diagnostic(
                    "warning", "Z002", f"unknown net variable '{node.id}'", _span(node), file=self.path))
            return None
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            return self.net_vars.get(node.value.id)  # e.g. VCC.NET
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

    def _add_part(self, ref: str, component: str, package: str | None,
                  attrs: list[tuple[str, str]], pins: list[tuple[str, str | None]]) -> None:
        designator = ref  # .zen instance names ARE designators (R1, U1, ...)
        self.parts.append(ir.PartLow(ref, designator, component, package, tuple(attrs)))
        for pin, net in pins:
            if net is not None:
                self.nets.setdefault(net, []).append((ref, pin))

    def _component_call(self, call: ast.Call) -> None:
        kw = {k.arg: k.value for k in call.keywords if k.arg}
        ref = _str_const(kw.get("name")) or f"U${len(self.parts) + 1}"
        package = _str_const(kw.get("footprint"))
        attrs = []
        for key in ("prefix", "mpn", "datasheet"):
            if (v := _str_const(kw.get(key))) is not None:
                attrs.append((key, v))
        pins: list[tuple[str, str | None]] = []
        pins_node = kw.get("pins")
        if isinstance(pins_node, ast.Dict):
            for k, v in zip(pins_node.keys, pins_node.values):
                if (pin := _str_const(k)) is not None:
                    pins.append((pin, self._net_of(v)))
        self._add_part(ref, "Component", package, attrs, pins)

    def _generic_call(self, call: ast.Call, generic: str) -> None:
        kw = {k.arg: k.value for k in call.keywords if k.arg}
        ref = _str_const(kw.get("name")) or f"U${len(self.parts) + 1}"
        component = generic.rsplit("/", 1)[-1].removesuffix(".zen")
        package = _str_const(kw.get("package")) or _str_const(kw.get("footprint"))
        attrs, pins = [], []
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
        self._add_part(ref, component, package, attrs, pins)

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
                    self.module_vars[var] = _str_const(stmt.value.args[0]) if stmt.value.args else var
                elif fn in _NET_CTORS:
                    self.net_vars[var] = self._net_from_ctor(stmt.value, var)
                elif fn == "io":  # boundary bundle → treat as a named net (ADR-002)
                    self.net_vars[var] = var
                    self.nets.setdefault(var, [])
                elif fn == "config":
                    self.config_vars.add(var)
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
            (ir.NetLow(name, tuple(ir.NetNode(ref, ref, pin, None)
                                   for ref, pin in sorted(endpoints)), ())
             for name, endpoints in self.nets.items() if endpoints),
            key=lambda n: n.name))
        return ir.IRLow(tuple(self.parts), nets)


def import_zen(source: str, path: str = "<string>") -> tuple[ir.IRLow, list[Diagnostic]]:
    imp = _ZenImporter(path)
    return imp.run(source), imp.diags
