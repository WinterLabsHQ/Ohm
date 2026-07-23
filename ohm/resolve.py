"""P2: P-SYMBOLS → P-RESOLVE → P-ELABORATE. AST → IR-HIGH (manual Part 4/5).

Pure: (Program) -> (Design, diagnostics). Never mutates the AST. Unresolved
names become diagnostics + poisoned bindings that suppress cascading errors.
"""

from __future__ import annotations

import difflib

from ohm import ast_nodes as A
from ohm import ir
from ohm.diagnostics import Diagnostic, Span
from shared.constraints_schema import CONSTRAINT_KINDS

VALID_ROLES = {"power_in", "power_out", "input", "output", "bidirectional", "passive", "ground", "nc"}
_BUILTIN_CTORS = {"io", "config"}


def _err(code: str, msg: str, span: Span, path: str, help_: str | None = None) -> Diagnostic:
    return Diagnostic("error", code, msg, span, file=path, help=help_)


def _suggest(name: str, candidates) -> str | None:
    close = difflib.get_close_matches(name, list(candidates), n=1)
    return f"did you mean '{close[0]}'?" if close else None


def _edit_distance(a: str, b: str) -> int:
    """Levenshtein distance, capped early when only <=2 matters."""
    if abs(len(a) - len(b)) > 2:
        return 3
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[-1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _value(expr: A.Expr) -> ir.Value | None:
    """AST literal/ref → IR value (attrs, overrides, defaults)."""
    match expr:
        case A.Str():
            return expr.value
        case A.Num():
            return expr.value
        case A.QuantityLit():
            tol = (expr.tol.value, expr.tol.unit) if expr.tol else None
            return ir.Quantity(expr.value, expr.unit, tol_raw=tol)
        case A.Ref():
            return expr.dotted()
        case A.Call() if expr.callee.dotted() == "generate":
            ptype = expr.args[0].dotted() if expr.args else ""
            kwargs = tuple((k, _value(v)) for k, v in expr.kwargs)
            return ir.GenerateSpec(ptype, kwargs)
        case _:
            return None


class _Resolver:
    def __init__(self, program: A.Program, path: str):
        self.program = program
        self.path = path
        self.diags: list[Diagnostic] = []
        self.symbols: dict[str, A.Decl] = {}
        self._current_overrides: dict[str, set[str]] = {}  # per-module: inst -> override keys

    # ---------- P-SYMBOLS ----------
    def build_symbols(self) -> None:
        for decl in self.program.decls:
            if decl.name in self.symbols:
                self.diags.append(
                    _err("R001", f"duplicate top-level name '{decl.name}'", decl.span, self.path)
                )
            else:
                self.symbols[decl.name] = decl

    # ---------- P-ELABORATE (includes P-RESOLVE ref checks, needs scope) ----------
    def run(self) -> ir.Design:
        self.build_symbols()
        interfaces, components, modules = [], [], []
        for decl in self.program.decls:
            if self.symbols.get(decl.name) is not decl:
                continue  # duplicate; first wins
            if isinstance(decl, A.InterfaceDecl):
                interfaces.append(self._interface(decl))
            elif isinstance(decl, A.ComponentDecl):
                components.append(self._component(decl))
            else:
                modules.append(self._module(decl))
        top = modules[-1].name if modules else None
        return ir.Design(tuple(interfaces), tuple(components), tuple(modules), top)

    def _role_attr(self, attrs: tuple[A.Attr, ...], owner: str, span: Span, required: bool) -> str | None:
        amap = {a.name: a for a in attrs}
        role_attr = amap.get("role")
        if role_attr is None:
            if required:
                self.diags.append(_err("R006", f"{owner} is missing required 'role'", span, self.path))
            return None
        role = role_attr.value.dotted() if isinstance(role_attr.value, A.Ref) else str(role_attr.value)
        if role not in VALID_ROLES:
            self.diags.append(
                _err("R007", f"invalid role '{role}' on {owner}", role_attr.span, self.path,
                     _suggest(role, VALID_ROLES))
            )
            return None
        return role

    def _interface(self, decl: A.InterfaceDecl) -> ir.InterfaceDef:
        members = tuple(
            (m.name, m.signal_type, self._role_attr(m.attrs, f"member '{m.name}'", m.span, required=False))
            for m in decl.members
        )
        member_names = {m.name for m in decl.members}
        requires = []
        for req in decl.requires:
            args = []
            for arg in req.relation.args:
                name = arg.dotted() if isinstance(arg, A.Ref) else str(arg)
                if name not in member_names:
                    self.diags.append(
                        _err("R008", f"require relation names unknown member '{name}'", req.span,
                             self.path, _suggest(name, member_names))
                    )
                else:
                    args.append(name)
            for k, _ in req.relation.kwargs:
                self.diags.append(Diagnostic(
                    "warning", "R013",
                    f"argument ignored: keyword '{k}' in require relation", req.span,
                    file=self.path))
            requires.append(ir.Relation(req.relation.callee.dotted(), tuple(args)))
        return ir.InterfaceDef(decl.name, members, tuple(requires), decl.span)

    def _component(self, decl: A.ComponentDecl) -> ir.ComponentDef:
        package = None
        attrs = []
        for a in decl.attrs:
            v = _value(a.value)
            if a.name == "package":
                package = v
            else:
                attrs.append((a.name, v))
        pins, padmap = [], []
        for p in decl.pins:
            amap = {a.name: a.value for a in p.attrs}
            role = self._role_attr(p.attrs, f"pin '{p.name}'", p.span, required=True)
            pad = None
            if "pad" in amap:
                pv = amap["pad"]
                pad = pv.dotted() if isinstance(pv, A.Ref) else str(_value(pv))
                padmap.append((p.name, pad))

            def qty(key: str) -> ir.Quantity | None:
                v = amap.get(key)
                return _value(v) if isinstance(v, A.QuantityLit) else None

            pins.append(ir.Pin(p.name, role or "passive", pad, qty("vmax"), qty("imax"),
                               qty("pmax"), qty("voltage"), qty("current"), p.span))
        return ir.ComponentDef(decl.name, package, tuple(pins), tuple(attrs), tuple(padmap), decl.span)

    def _module(self, decl: A.ModuleDecl) -> ir.Module:
        path = self.path
        configs, ports, instances, connections, asserts, obligations, constraints = \
            [], [], [], [], [], [], []
        scope: dict[str, str] = {}  # binding name -> def name ("" = poisoned/config)
        poisoned: set[str] = set()
        self._current_overrides = {}

        def bind(name: str, target: str, span: Span) -> bool:
            if name in self.symbols:
                g = self.symbols[name]
                kind = ("interface" if isinstance(g, A.InterfaceDecl)
                        else "component" if isinstance(g, A.ComponentDecl) else "module")
                self.diags.append(
                    _err("R010", f"binding '{name}' shadows the global {kind} '{name}'", span, path)
                )
                poisoned.add(name)  # suppress cascades from later refs to this binding
                return False
            if name in scope:
                self.diags.append(_err("R004", f"duplicate binding '{name}' in module '{decl.name}'", span, path))
                return False
            scope[name] = target
            return True

        for item in decl.items:
            if isinstance(item, A.Binding):
                callee = item.call.callee.dotted()
                if callee == "io":
                    iface = item.call.args[0].dotted() if item.call.args else ""
                    if not isinstance(self.symbols.get(iface), A.InterfaceDecl):
                        self.diags.append(
                            _err("R005", f"io(...) needs an interface type, got '{iface}'", item.span,
                                 path, _suggest(iface, [n for n, d in self.symbols.items()
                                                        if isinstance(d, A.InterfaceDecl)]))
                        )
                        poisoned.add(item.name)
                        bind(item.name, "", item.span)
                        continue
                    if bind(item.name, iface, item.span):
                        ports.append(ir.Port(item.name, iface, item.span))
                elif callee == "config":
                    type_name = item.call.args[0].dotted() if item.call.args else ""
                    default = None
                    for k, v in item.call.kwargs:
                        if k == "default":
                            if isinstance(v, (A.QuantityLit, A.Num, A.Str)):
                                default = _value(v)
                            else:
                                self.diags.append(_err(
                                    "R002", "config default must be a literal value",
                                    item.span, path))
                    if bind(item.name, "", item.span):
                        configs.append(ir.Config(item.name, type_name, default, item.span))
                else:
                    target = self.symbols.get(callee)
                    if not isinstance(target, (A.ComponentDecl, A.ModuleDecl)):
                        self.diags.append(
                            _err("R002", f"unknown component or module '{callee}'", item.span, path,
                                 _suggest(callee, self.symbols))
                        )
                        poisoned.add(item.name)
                        bind(item.name, "", item.span)
                        continue
                    self._check_overrides(item, target)
                    overrides = tuple((k, _value(v)) for k, v in item.call.kwargs)
                    if bind(item.name, callee, item.span):
                        instances.append(ir.Instance(item.name, callee, overrides, item.span))
                        self._current_overrides[item.name] = {k for k, _ in item.call.kwargs}
            elif isinstance(item, A.Connect):
                lo = self._check_ref(item.left, scope, poisoned, decl.name, connect=True)
                ro = self._check_ref(item.right, scope, poisoned, decl.name, connect=True)
                if lo and ro:
                    connections.append(ir.Connection(item.left.parts, item.right.parts, item.span))
            elif isinstance(item, A.AssertStmt):
                self._check_expr_refs(item.expr, scope, poisoned, decl.name)
                asserts.append(ir.AssertRec(item.expr, item.span))
            elif isinstance(item, A.ConstraintStmt):
                kind = item.call.callee.dotted()
                if kind not in CONSTRAINT_KINDS:
                    self.diags.append(Diagnostic(
                        "warning", "R009", f"unknown constraint kind '{kind}'", item.span,
                        file=path, help=f"known kinds: {', '.join(sorted(CONSTRAINT_KINDS))}"))
                ref_args, params = [], []
                for arg in item.call.args:
                    if isinstance(arg, A.Ref):
                        if self._check_ref(arg, scope, poisoned, decl.name):
                            ref_args.append(arg.parts)
                    else:
                        params.append(A.to_src(arg))
                constraints.append(ir.Constraint(kind, tuple(ref_args), tuple(params), item.span))
            elif isinstance(item, A.ObligationStmt):
                args = []
                for arg in item.call.args:
                    if isinstance(arg, A.Ref):
                        self._check_ref(arg, scope, poisoned, decl.name)
                        args.append(arg.parts)
                    else:
                        self.diags.append(Diagnostic(
                            "warning", "R013",
                            f"argument ignored: '{A.to_src(arg)}' in obligation "
                            f"'{item.call.callee.dotted()}' is not a reference",
                            item.span, file=path))
                obligations.append(
                    ir.ObligationRec(item.call.callee.dotted(), tuple(args), item.op, item.bound, item.span)
                )
        return ir.Module(decl.name, tuple(configs), tuple(ports), tuple(instances),
                         tuple(connections), tuple(asserts), tuple(obligations),
                         tuple(constraints), decl.span)

    def _check_overrides(self, item: A.Binding, target: A.ComponentDecl | A.ModuleDecl) -> None:
        """R012: unknown instance-override kwargs. Modules: strict (declared configs
        only). Components: new attrs are legal (divider.ohm introduces 'value'),
        but a name within edit-distance 2 of a declared attr/pin is a typo."""
        if isinstance(target, A.ModuleDecl):
            declared = {b.name for b in target.items
                        if isinstance(b, A.Binding) and b.call.callee.dotted() == "config"}
            for k, _ in item.call.kwargs:
                if k not in declared:
                    self.diags.append(_err(
                        "R012", f"unknown config '{k}' on module '{target.name}'",
                        item.span, self.path, _suggest(k, declared)))
        else:
            declared = {a.name for a in target.attrs} | {p.name for p in target.pins}
            for k, _ in item.call.kwargs:
                if k in declared:
                    continue
                close = min(declared, key=lambda n: _edit_distance(k, n), default=None)
                if close is not None and _edit_distance(k, close) <= 2:
                    self.diags.append(_err(
                        "R012", f"unknown attr '{k}' on component '{target.name}'",
                        item.span, self.path, f"did you mean '{close}'?"))

    def _check_ref(self, ref: A.Ref, scope: dict, poisoned: set, module: str,
                   connect: bool = False) -> bool:
        """Validate base binding and (when depth>=2) the pin/member name."""
        base = ref.parts[0]
        if base in poisoned:
            return False  # already reported; suppress cascade
        if base not in scope:
            self.diags.append(
                _err("R002", f"unknown name '{base}' in module '{module}'", ref.span, self.path,
                     _suggest(base, scope))
            )
            return False
        target = self.symbols.get(scope[base])
        if len(ref.parts) < 2:
            if connect:
                if isinstance(target, (A.ComponentDecl, A.ModuleDecl)):
                    self.diags.append(
                        _err("R011", f"cannot connect '{base}' — connect a pin, port, or "
                             "interface member", ref.span, self.path)
                    )
                    return False
                if scope[base] == "":  # config binding (poisoned handled above)
                    self.diags.append(
                        _err("R011", f"cannot connect config '{base}'", ref.span, self.path)
                    )
                    return False
            return True
        member = ref.parts[1]
        if isinstance(target, A.ComponentDecl):
            # pins, declared attrs, and per-instance overrides are all addressable
            pin_names = {p.name for p in target.pins}
            names = pin_names | {a.name for a in target.attrs}
            names |= self._current_overrides.get(base, set())
            kind = f"pin or attr on component '{target.name}'"
            if connect and member in names and member not in pin_names:
                self.diags.append(_err(
                    "R003", f"'{member}' is an attr on component '{target.name}', not a pin"
                    " — connect endpoints must be pins", ref.span, self.path,
                    _suggest(member, pin_names)))
                return False
        elif isinstance(target, A.InterfaceDecl):
            names = {m.name for m in target.members}
            kind = f"member on interface '{target.name}'"
        elif isinstance(target, A.ModuleDecl):
            names = {i.name for i in target.items if isinstance(i, A.Binding)}
            kind = f"port on module '{target.name}'"
        else:
            return True  # config or poisoned target: nothing to validate
        if member not in names:
            self.diags.append(
                _err("R003", f"unknown {kind}: '{member}'", ref.span, self.path, _suggest(member, names))
            )
            return False
        if connect and isinstance(target, A.ComponentDecl) and len(ref.parts) > 2:
            self.diags.append(_err(
                "R003", f"component pins have no sub-members: '{ref.dotted()}'",
                ref.span, self.path))
            return False
        if isinstance(target, A.ModuleDecl) and len(ref.parts) >= 3:
            # inst.port.member: resolve the port's io(Interface) and validate the member
            sub = next(i for i in target.items if isinstance(i, A.Binding) and i.name == member)
            if sub.call.callee.dotted() == "io":
                iface = self.symbols.get(sub.call.args[0].dotted() if sub.call.args else "")
                if isinstance(iface, A.InterfaceDecl):
                    mnames = {m.name for m in iface.members}
                    if ref.parts[2] not in mnames:
                        self.diags.append(
                            _err("R003", f"unknown member on interface '{iface.name}': "
                                 f"'{ref.parts[2]}'", ref.span, self.path,
                                 _suggest(ref.parts[2], mnames))
                        )
                        return False
        return True

    def _check_expr_refs(self, expr: A.Expr, scope, poisoned, module) -> None:
        match expr:
            case A.Ref():
                self._check_ref(expr, scope, poisoned, module)
            case A.BinOp():
                self._check_expr_refs(expr.left, scope, poisoned, module)
                self._check_expr_refs(expr.right, scope, poisoned, module)
            case A.Call():
                for a in expr.args:
                    self._check_expr_refs(a, scope, poisoned, module)
                for _, v in expr.kwargs:
                    self._check_expr_refs(v, scope, poisoned, module)
            case _:
                pass


def resolve(program: A.Program, path: str = "<string>") -> tuple[ir.Design, list[Diagnostic]]:
    r = _Resolver(program, path)
    design = r.run()
    return design, r.diags
