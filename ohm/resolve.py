"""P2: P-SYMBOLS → P-RESOLVE → P-ELABORATE. AST → IR-HIGH (manual Part 4/5).

Pure: (Program) -> (Design, diagnostics). Never mutates the AST. Unresolved
names become diagnostics + poisoned bindings that suppress cascading errors.
"""

from __future__ import annotations

import difflib
import re
from decimal import Decimal

from ohm import ast_nodes as A
from ohm import ir
from ohm.diagnostics import Diagnostic, Span
from ohm import closedform as _cf
from shared.constraints_schema import CONSTRAINT_KINDS

# loop-index arithmetic (ADR-010 G1): `[i]`, `[i+1]`, `[i-2]` inside ref parts
_IDX_RE = re.compile(r"\[([A-Za-z_]\w*)([+-]\d+(?:\.\d+)?)?\]")


class _BadIndex(Exception):
    """A loop-index expression that cannot produce a valid element index (→ R021)."""


# open_collector/open_drain (wired-OR outputs: comparators, I2C, 1-Wire) and free (base
# default, connects to anything) added for kind-contract fidelity (ADR-008 research)
VALID_ROLES = {"power_in", "power_out", "input", "output", "bidirectional", "passive",
               "ground", "nc", "open_collector", "open_drain", "free"}
# closed v1 set of expects patterns (ADR-009); each is a predicate in check_bringup
EXPECT_PATTERNS = {"pullup", "decoupling", "loadcaps", "cap", "termination", "tie"}
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


def _dotted(expr: A.Expr | None, default: str = "") -> str:
    """The dotted name of a Ref expression, or `default` for anything else."""
    return expr.dotted() if isinstance(expr, A.Ref) else default


def _value(expr: A.Expr) -> ir.Value:
    """AST literal/ref → IR value (attrs, overrides, defaults).

    Unrepresentable expressions degrade to their canonical source text (a str):
    the name stays addressable and downstream evaluation treats it as opaque.
    """
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
            ptype = _dotted(expr.args[0]) if expr.args else ""
            kwargs = tuple((k, _value(v)) for k, v in expr.kwargs)
            return ir.GenerateSpec(ptype, kwargs)
        case _:
            return A.to_src(expr)


class _CfgEnv(_cf.Env):
    """Minimal Env for folding computed attr values at elaborate (ADR-010):
    resolves single-name refs against the enclosing module's literal configs."""

    def __init__(self, cfgs: dict[str, "ir.Value"]):  # noqa: super-init not needed
        self.cfgs = cfgs
        self.used: set[str] = set()  # configs the fold consumed (override guard)

    def resolve(self, ref: A.Ref):
        from ohm import closedform, units as U

        if len(ref.parts) == 1:
            v = self.cfgs.get(ref.parts[0])
            if v is None:
                return None
            self.used.add(ref.parts[0])
            if isinstance(v, ir.Quantity):
                v, _err_ = U.normalize(v)  # tol_raw -> tol_lo/hi (pre-annotation fold)
            return closedform.value_qty(v)
        return None


# canonical unit per dimension (the factor-1 entry of the unit table)
def _base_unit(dim: str) -> str | None:
    from decimal import Decimal as D

    from ohm import units as U

    for unit, (d, factor) in U.UNITS.items():
        if d == dim and factor == D(1):
            return unit
    return None


class _Resolver:
    def __init__(self, program: A.Program, path: str):
        self.program = program
        self.path = path
        self._cur_path = path  # per-decl file (ADR-007); falls back to the entry path
        self.diags: list[Diagnostic] = []
        self.symbols: dict[str, A.Decl] = {}
        self.kinds_ns: dict[str, A.KindDecl] = {}  # kinds are a separate namespace (ADR-008)
        self._current_overrides: dict[str, set[str]] = {}  # per-module: inst -> override keys
        self._current_arrays: dict[str, int] = {}  # per-module: array name -> size (ADR-010)
        # ADR-010 override guard: module -> configs consumed by computed-value folds,
        # and every module-instantiation site that overrides configs (checked at end
        # of run() so declaration order doesn't matter)
        self._folded_cfg_uses: dict[str, set[str]] = {}
        self._override_sites: list[tuple[str, set[str], Span, str]] = []

    # ---------- P-SYMBOLS ----------
    def build_symbols(self) -> None:
        for decl in self.program.decls:
            if isinstance(decl, A.UseDecl):
                continue  # stripped by the loader; tolerated on parse-only paths
            if isinstance(decl, A.KindDecl):  # kinds are a separate namespace (ADR-008)
                if decl.name in self.kinds_ns:
                    self._cur_path = decl.file or self.path
                    self.diags.append(_err("R001", f"duplicate kind '{decl.name}'",
                                           decl.span, self._cur_path))
                else:
                    self.kinds_ns[decl.name] = decl
            elif decl.name in self.symbols:
                self._cur_path = decl.file or self.path
                self.diags.append(_err("R001", f"duplicate top-level name '{decl.name}'",
                                       decl.span, self._cur_path))
            else:
                self.symbols[decl.name] = decl

    # ---------- P-ELABORATE (includes P-RESOLVE ref checks, needs scope) ----------
    def run(self) -> ir.Design:
        self.build_symbols()
        interfaces, components, modules, kinds = [], [], [], []
        for decl in self.program.decls:
            if isinstance(decl, A.UseDecl):
                continue
            registered = self.kinds_ns.get(decl.name) if isinstance(decl, A.KindDecl) \
                else self.symbols.get(decl.name)
            if registered is not decl:
                continue  # duplicate; first wins
            self._cur_path = decl.file or self.path  # diagnostics point at the decl's own file
            if isinstance(decl, A.InterfaceDecl):
                interfaces.append(self._interface(decl))
            elif isinstance(decl, A.ComponentDecl):
                components.append(self._component(decl))
            elif isinstance(decl, A.KindDecl):
                kinds.append(self._kind(decl))
            else:
                modules.append(self._module(decl))
        self._cur_path = self.path
        # ADR-010 override guard: a folded computed value baked the config's default
        # in at elaborate — an instantiation override would be silently ignored, so
        # it is an error (v1; per-instance re-folding would need lower-time context)
        for callee, keys, span, site_path in self._override_sites:
            for cfg in sorted(keys & self._folded_cfg_uses.get(callee, set())):
                self.diags.append(_err(
                    "R022", f"config '{cfg}' of '{callee}' feeds computed values; "
                    "overriding it is not supported (v1)", span, site_path,
                    "computed values fold against the declared default at compile "
                    "time — pass the final value directly instead"))
        top = modules[-1].name if modules else None
        return ir.Design(tuple(interfaces), tuple(components), tuple(modules), top, tuple(kinds))

    def _kind(self, decl: A.KindDecl) -> ir.KindDef:
        for role_name, members in (("required", decl.requires), ("optional", decl.optional)):
            for m in members:
                if m.role not in VALID_ROLES:
                    self.diags.append(_err(
                        "R007", f"invalid role '{m.role}' on kind member '{m.name}'",
                        m.span, self._cur_path, _suggest(m.role, VALID_ROLES)))
        from ohm import units
        valid_dims = {d for d, _ in units.UNITS.values()} | {units.DIMENSIONLESS}
        for p in decl.params:
            if p.dimension not in valid_dims:
                self.diags.append(_err(
                    "R019", f"unknown dimension '{p.dimension}' on kind param '{p.name}'",
                    p.span, self._cur_path, _suggest(p.dimension, valid_dims)))
        for parent in decl.parents:
            if parent not in self.kinds_ns:
                self.diags.append(_err(
                    "R017", f"kind '{decl.name}' refines unknown kind '{parent}'",
                    decl.span, self._cur_path, _suggest(parent, self.kinds_ns)))
            elif self._kind_cycles(decl.name):
                self.diags.append(_err(
                    "R018", f"kind refinement cycle through '{decl.name}'", decl.span, self._cur_path))
                break
        kind_members = ({m.name for m in decl.requires} | {m.name for m in decl.optional})
        expects = self._expectations(decl.expects, kind_members, f"kind '{decl.name}'")
        return ir.KindDef(
            decl.name, decl.parents,
            tuple((m.name, m.role) for m in decl.requires),
            tuple((m.name, m.role) for m in decl.optional),
            tuple((p.name, p.dimension) for p in decl.params), decl.span, expects)

    def _kind_cycles(self, start: str) -> bool:
        seen: set[str] = set()
        stack = [start]
        while stack:
            k = stack.pop()
            if k == start and seen:
                return True
            if k in seen:
                continue
            seen.add(k)
            kd = self.kinds_ns.get(k)
            if kd is not None:
                stack.extend(kd.parents)
        return False

    def _role_attr(self, attrs: tuple[A.Attr, ...], owner: str, span: Span, required: bool) -> str | None:
        amap = {a.name: a for a in attrs}
        role_attr = amap.get("role")
        if role_attr is None:
            if required:
                self.diags.append(_err("R006", f"{owner} is missing required 'role'", span, self._cur_path))
            return None
        role = role_attr.value.dotted() if isinstance(role_attr.value, A.Ref) else str(role_attr.value)
        if role not in VALID_ROLES:
            self.diags.append(
                _err("R007", f"invalid role '{role}' on {owner}", role_attr.span, self._cur_path,
                     _suggest(role, VALID_ROLES))
            )
            return None
        return role

    def _fold_value(self, expr: A.Expr, cfgs: dict[str, ir.Value],
                    span: Span, module: str | None = None) -> ir.Value | None:
        """Fold a computed attr/override expression (ADR-010) to a concrete value.

        Reuses the closed-form engine; must resolve entirely at compile time
        (literals + this module's literal-default configs) — else R022."""
        from ohm import closedform, units as U

        if not isinstance(expr, A.BinOp):
            return _value(expr)
        if expr.op not in ("+", "-", "*", "/"):
            self.diags.append(_err(
                "R022", "attr expressions must be arithmetic, not comparisons",
                span, self._cur_path))
            return None
        env = _CfgEnv(cfgs)
        qty = closedform.eval_expr(expr, env)
        if module is not None and env.used:  # folds bake the default in — guard overrides
            self._folded_cfg_uses.setdefault(module, set()).update(env.used)
        if qty is None:
            self.diags.append(_err(
                "R022", f"attr expression does not fold at compile time: {A.to_src(expr)}",
                span, self._cur_path,
                "computed values may use literals, this module's configs with literal "
                "defaults, and closed-form arithmetic"))
            return None
        from decimal import Decimal as D

        def clean(d: D) -> D:  # 1.3E+2 -> 130 (no exponent leak into outputs)
            d = d.normalize()
            exp = d.as_tuple().exponent
            return d.quantize(D(1)) if d == d.to_integral_value() and isinstance(exp, int) and exp > 0 else d

        if qty.dim == U.DIMENSIONLESS:
            v = qty.iv.lo if qty.iv.lo == qty.iv.hi else (qty.iv.lo + qty.iv.hi) / 2
            return clean(v)
        unit = _base_unit(qty.dim)
        if unit is None:
            self.diags.append(_err(
                "R022", f"attr expression folds to unexpressible dimension '{qty.dim}'",
                span, self._cur_path))
            return None
        if qty.iv.lo == qty.iv.hi:
            return ir.Quantity(clean(qty.iv.lo), unit)
        mid = clean((qty.iv.lo + qty.iv.hi) / 2)
        return ir.Quantity(mid, unit, clean(qty.iv.lo), clean(qty.iv.hi))

    def _expectations(self, expects: tuple[A.Expectation, ...],
                      valid_members: set[str], owner: str) -> tuple[ir.ExpectationDef, ...]:
        """Validate + lower `expects` declarations (ADR-009) — R020 on bad pattern/member."""
        out = []
        for e in expects:
            if e.pattern not in EXPECT_PATTERNS:
                self.diags.append(_err(
                    "R020", f"unknown expects pattern '{e.pattern}' on {owner}",
                    e.span, self._cur_path, _suggest(e.pattern, EXPECT_PATTERNS)))
                continue
            bad = [m for m in e.members if m not in valid_members]
            if bad:
                self.diags.append(_err(
                    "R020", f"expects on {owner} names unknown member(s): {', '.join(bad)}",
                    e.span, self._cur_path, _suggest(bad[0], valid_members)))
                continue
            out.append(ir.ExpectationDef(
                e.pattern, tuple(_value(a) for a in e.args), e.members, e.when, e.span))
        return tuple(out)

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
                             self._cur_path, _suggest(name, member_names))
                    )
                else:
                    args.append(name)
            for k, _ in req.relation.kwargs:
                self.diags.append(Diagnostic(
                    "warning", "R013",
                    f"argument ignored: keyword '{k}' in require relation", req.span,
                    file=self._cur_path))
            requires.append(ir.Relation(req.relation.callee.dotted(), tuple(args)))
        expects = self._expectations(decl.expects, member_names, f"interface '{decl.name}'")
        return ir.InterfaceDef(decl.name, members, tuple(requires), decl.span, expects)

    def _component(self, decl: A.ComponentDecl) -> ir.ComponentDef:
        package = None
        attrs = []
        for a in decl.attrs:
            v = self._fold_value(a.value, {}, a.span)  # computed attrs fold (ADR-010)
            if v is None:
                continue
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
                if not isinstance(v, A.QuantityLit):
                    return None
                val = _value(v)
                return val if isinstance(val, ir.Quantity) else None

            satisfies = None  # `is=<member>` binds this pin to a kind member (ADR-008)
            if "is" in amap:
                iv = amap["is"]
                satisfies = iv.dotted() if isinstance(iv, A.Ref) else str(_value(iv))

            pins.append(ir.Pin(p.name, role or "passive", pad, qty("vmax"), qty("imax"),
                               qty("pmax"), qty("voltage"), qty("current"), satisfies, p.span,
                               must_connect="must_connect" in amap,
                               tolerant_to=qty("tolerant_to")))
        # validate declared kinds exist (R017); check_kinds does conformance
        for kname in decl.kinds:
            if kname not in self.kinds_ns:
                self.diags.append(_err(
                    "R017", f"component '{decl.name}' declares unknown kind '{kname}'",
                    decl.span, self._cur_path, _suggest(kname, self.kinds_ns)))
        return ir.ComponentDef(decl.name, package, tuple(pins), tuple(attrs), tuple(padmap),
                               decl.kinds, decl.span)

    def _module(self, decl: A.ModuleDecl) -> ir.Module:
        path = self._cur_path
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
            if "[" not in name and name in self._current_arrays:  # plain `rs` vs array `rs` (ADR-010)
                self.diags.append(_err(
                    "R004", f"duplicate binding '{name}' in module '{decl.name}': "
                    f"'{name}' is also an array of {self._current_arrays[name]}", span, path))
                return False
            scope[name] = target
            return True

        # ---- ADR-010 pre-expansion: unroll arrays and bounded for-loops so the
        # main loop (and every checker after it) sees only plain items ----
        self._current_arrays = {}

        def subst_part(p: str, var: str, i: int) -> str:
            # `[i]` / `[i+1]` / `[i-1]` → evaluated literal index (ADR-010 G1)
            def rep(m: "re.Match[str]") -> str:
                if m.group(1) != var:
                    return m.group(0)
                off = Decimal(m.group(2)) if m.group(2) else Decimal(0)
                if off != off.to_integral_value():
                    raise _BadIndex(f"index offset must be an integer in '{p}'")
                n = i + int(off)
                if n < 0:
                    raise _BadIndex(
                        f"index {var}{m.group(2)} is {n} at {var}={i} in '{p}'")
                return f"[{n}]"

            return _IDX_RE.sub(rep, p)

        def subst_ref(ref: A.Ref, var: str, i: int) -> A.Ref:
            import dataclasses as _dc

            return _dc.replace(ref, parts=tuple(
                subst_part(p, var, i) for p in ref.parts))

        def subst_expr(e: A.Expr, var: str, i: int) -> A.Expr:
            import dataclasses as _dc

            match e:
                case A.Ref():
                    return subst_ref(e, var, i)
                case A.BinOp():
                    return _dc.replace(e, left=subst_expr(e.left, var, i),
                                       right=subst_expr(e.right, var, i))
                case A.Call():
                    return _dc.replace(
                        e, args=tuple(subst_expr(a, var, i) for a in e.args),
                        kwargs=tuple((k, subst_expr(v, var, i)) for k, v in e.kwargs))
                case _:
                    return e

        def subst_item(item: A.ModuleItem, var: str, i: int) -> A.ModuleItem:
            import dataclasses as _dc

            match item:
                case A.Connect():
                    return _dc.replace(item, left=subst_ref(item.left, var, i),
                                       right=subst_ref(item.right, var, i))
                case A.AssertStmt():
                    return _dc.replace(item, expr=subst_expr(item.expr, var, i))
                case A.ConstraintStmt():
                    new_call = subst_expr(item.call, var, i)
                    assert isinstance(new_call, A.Call)
                    return _dc.replace(item, call=new_call)
                case _:
                    return item

        expanded: list[A.ModuleItem] = []
        for item in decl.items:
            if isinstance(item, A.ArrayBinding):
                if item.call.callee.dotted() in ("io", "config"):
                    self.diags.append(_err(
                        "R021", f"arrays instantiate components or modules; "
                        f"{item.call.callee.dotted()}() cannot be arrayed",
                        item.span, path))
                    continue
                n = item.count
                if n != n.to_integral_value() or not (1 <= int(n) <= 1024):
                    self.diags.append(_err(
                        "R021", f"array count must be an integer 1..1024, got {n}",
                        item.span, path))
                    continue
                self._current_arrays[item.name] = int(n)
                for i in range(int(n)):
                    expanded.append(A.Binding(f"{item.name}[{i}]", item.call, item.span))
            elif isinstance(item, A.ForStmt):
                lo, hi = item.lo, item.hi
                if (lo != lo.to_integral_value() or hi != hi.to_integral_value()
                        or lo > hi or hi - lo + 1 > 1024):
                    self.diags.append(_err(
                        "R021", f"for range must be integer lo..hi with at most 1024 "
                        f"iterations, got {lo}..{hi}", item.span, path))
                    continue
                try:
                    unrolled = [subst_item(inner, item.var, i)
                                for i in range(int(lo), int(hi) + 1)
                                for inner in item.items]
                except _BadIndex as bad:  # one diagnostic per loop; drop the whole loop
                    self.diags.append(_err("R021", str(bad), item.span, path))
                    continue
                expanded.extend(unrolled)
            else:
                expanded.append(item)

        for item in expanded:
            if isinstance(item, A.Binding):
                callee = item.call.callee.dotted()
                if callee == "io":
                    iface = _dotted(item.call.args[0]) if item.call.args else ""
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
                    type_name = _dotted(item.call.args[0]) if item.call.args else ""
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
                    # `waive="B005,pullup"` suppresses expectation findings (ADR-009);
                    # it is metadata, not an attr — never merged into part attrs
                    waivers: tuple[str, ...] = ()
                    for k, v in item.call.kwargs:
                        if k == "waive":
                            wv = _value(v)
                            waivers = tuple(w.strip() for w in str(wv).split(",") if w.strip())
                    # computed overrides fold against configs declared so far (ADR-010)
                    cfg_map = {c.name: c.default for c in configs if c.default is not None}
                    folded = [(k, self._fold_value(v, cfg_map, item.span, decl.name))
                              for k, v in item.call.kwargs if k != "waive"]
                    overrides = tuple((k, v) for k, v in folded if v is not None)
                    if isinstance(target, A.ModuleDecl) and overrides:
                        self._override_sites.append(
                            (callee, {k for k, _ in overrides}, item.span, path))
                    if bind(item.name, callee, item.span):
                        instances.append(ir.Instance(item.name, callee, overrides, item.span,
                                                     waivers=waivers))
                        self._current_overrides[item.name] = {k for k, _ in item.call.kwargs}
            elif isinstance(item, A.Connect):
                okl = self._check_ref(item.left, scope, poisoned, decl.name, connect=True)
                okr = self._check_ref(item.right, scope, poisoned, decl.name, connect=True)
                if okl and okr:
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
            declared.add("waive")  # expectation waivers are universal metadata (ADR-009)
            for k, _ in item.call.kwargs:
                if k not in declared:
                    self.diags.append(_err(
                        "R012", f"unknown config '{k}' on module '{target.name}'",
                        item.span, self._cur_path, _suggest(k, declared)))
        else:
            declared = {a.name for a in target.attrs} | {p.name for p in target.pins}
            declared.add("package")  # R9: library parts' package is overridable per instance
            declared.add("waive")  # expectation waivers (ADR-009)
            for k, _ in item.call.kwargs:
                if k in declared:
                    continue
                close = min(declared, key=lambda n: _edit_distance(k, n), default=None)
                if close is not None and _edit_distance(k, close) <= 2:
                    self.diags.append(_err(
                        "R012", f"unknown attr '{k}' on component '{target.name}'",
                        item.span, self._cur_path, f"did you mean '{close}'?"))

    def _check_ref(self, ref: A.Ref, scope: dict, poisoned: set, module: str,
                   connect: bool = False) -> bool:
        """Validate base binding and (when depth>=2) the pin/member name."""
        base = ref.parts[0]
        if base in poisoned:
            return False  # already reported; suppress cascade
        if base not in scope:
            # ADR-010 array diagnostics before the generic unknown-name error
            if "[" in base:
                stem, _, idx = base.partition("[")
                idx = idx.rstrip("]")
                if not idx.isdigit():
                    msg = (f"array index must be an integer, got '{idx}' in '{base}'"
                           if re.fullmatch(r"[\d.]+", idx) else
                           f"loop variable '{idx}' used outside its loop in '{base}'")
                    self.diags.append(_err("R021", msg, ref.span, self._cur_path))
                    return False
                if stem in self._current_arrays:
                    self.diags.append(_err(
                        "R021", f"index {idx} out of range for array '{stem}' "
                        f"(size {self._current_arrays[stem]})", ref.span, self._cur_path))
                    return False
            elif base in self._current_arrays:
                self.diags.append(_err(
                    "R021", f"'{base}' is an array of {self._current_arrays[base]} — "
                    f"index it, e.g. {base}[0]", ref.span, self._cur_path))
                return False
            self.diags.append(
                _err("R002", f"unknown name '{base}' in module '{module}'", ref.span, self._cur_path,
                     _suggest(base, scope))
            )
            return False
        target = self.symbols.get(scope[base])
        if len(ref.parts) < 2:
            if connect:
                if isinstance(target, (A.ComponentDecl, A.ModuleDecl)):
                    self.diags.append(
                        _err("R011", f"cannot connect '{base}' — connect a pin, port, or "
                             "interface member", ref.span, self._cur_path)
                    )
                    return False
                if scope[base] == "":  # config binding (poisoned handled above)
                    self.diags.append(
                        _err("R011", f"cannot connect config '{base}'", ref.span, self._cur_path)
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
                    " — connect endpoints must be pins", ref.span, self._cur_path,
                    _suggest(member, pin_names)))
                return False
        elif isinstance(target, A.InterfaceDecl):
            names = {m.name for m in target.members}
            kind = f"member on interface '{target.name}'"
        elif isinstance(target, A.ModuleDecl):
            port_names = {i.name for i in target.items if isinstance(i, A.Binding)
                          and i.call.callee.dotted() == "io"}
            names = {i.name for i in target.items if isinstance(i, A.Binding)}
            kind = f"port on module '{target.name}'"
            if connect and member in names and member not in port_names:
                self.diags.append(_err(
                    "R003", f"'{member}' is internal to module '{target.name}', not a port"
                    " — connect endpoints must be io() ports", ref.span, self._cur_path,
                    _suggest(member, port_names)))
                return False
        else:
            return True  # config or poisoned target: nothing to validate
        if member not in names:
            self.diags.append(
                _err("R003", f"unknown {kind}: '{member}'", ref.span, self._cur_path, _suggest(member, names))
            )
            return False
        if connect and isinstance(target, A.ComponentDecl) and len(ref.parts) > 2:
            self.diags.append(_err(
                "R003", f"component pins have no sub-members: '{ref.dotted()}'",
                ref.span, self._cur_path))
            return False
        if isinstance(target, A.ModuleDecl) and len(ref.parts) >= 3:
            # inst.port.member: resolve the port's io(Interface) and validate the member
            sub = next(i for i in target.items if isinstance(i, A.Binding) and i.name == member)
            if sub.call.callee.dotted() == "io":
                iface = self.symbols.get(_dotted(sub.call.args[0]) if sub.call.args else "")
                if isinstance(iface, A.InterfaceDecl):
                    mnames = {m.name for m in iface.members}
                    if ref.parts[2] not in mnames:
                        self.diags.append(
                            _err("R003", f"unknown member on interface '{iface.name}': "
                                 f"'{ref.parts[2]}'", ref.span, self._cur_path,
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
