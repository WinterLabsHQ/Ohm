"""Kind conformance checker (ADR-008) — the component-type system.

Nominal claim, structural check: the author DECLARES a component's kinds
(`component TLV9061(OpAmp)`); this pass verifies the pins/params actually
satisfy every declared kind's contract, over the transitive union of declared
kinds + their ancestors. A component with no declared kinds is the base
`Component` — unchecked (open-world: absence of a kind is "unasserted").

Cheap, total, O(pins) — deterministic, no solver. Never mutates IR.

  K001  two declared kinds require the same member with different role (conflict)
  K002  a required member has no pin bound to it
  K003  a pin's `is=` binds a member but its role != the required role
  K004  a required param is absent, or present with the wrong dimension
  K005  a pin binds `is=<member>` that no declared kind defines
"""

from __future__ import annotations

from ohm import ir, units
from ohm.diagnostics import Diagnostic, Span

_NO_SPAN = Span(1, 1, 1, 1)


def _d(code: str, msg: str, span: Span | None, path: str, sev: str = "error",
       help_: str | None = None) -> Diagnostic:
    return Diagnostic(sev, code, msg, span or _NO_SPAN, file=path, help=help_)


def _closest(name: str, options) -> str | None:
    import difflib

    m = difflib.get_close_matches(name, list(options), n=1)
    return f"did you mean '{m[0]}'?" if m else None


def check_kinds(design: ir.Design, path: str = "<string>") -> list[Diagnostic]:
    diags: list[Diagnostic] = []
    kinds = {k.name: k for k in design.kinds}

    def ancestry(name: str, seen: set[str]) -> list[ir.KindDef]:
        """A kind + its transitive parents (cycle-guarded; resolver already flags cycles)."""
        kd = kinds.get(name)
        if kd is None or name in seen:
            return []
        seen.add(name)
        out = [kd]
        for parent in kd.parents:
            out.extend(ancestry(parent, seen))
        return out

    for comp in design.components:
        if not comp.kinds:
            continue  # base Component — unchecked (open-world escape hatch)
        collected: list[ir.KindDef] = []
        for kname in comp.kinds:
            collected.extend(ancestry(kname, set()))
        # dedup by kind name, keep first-seen order
        seen_k: set[str] = set()
        contract: list[ir.KindDef] = []
        for kd in collected:
            if kd.name not in seen_k:
                seen_k.add(kd.name)
                contract.append(kd)

        # --- required members merged across the whole contract (K001 conflict) ---
        req_role: dict[str, str] = {}  # member -> role
        req_owner: dict[str, str] = {}  # member -> first kind that required it
        for kd in contract:
            for member, role in kd.required:
                if member in req_role and req_role[member] != role:
                    diags.append(_d(
                        "K001",
                        f"component '{comp.name}': kinds '{req_owner[member]}' and '{kd.name}' "
                        f"both require member '{member}' with different roles "
                        f"({req_role[member]} vs {role})",
                        comp.span, path,
                        help_="rename one kind's member, or align the roles — a single pin can "
                              "satisfy both only when the role matches"))
                else:
                    req_role.setdefault(member, role)
                    req_owner.setdefault(member, kd.name)

        # all member names the contract knows (required + optional) — for K005
        known_members = {m for kd in contract for m, _ in (*kd.required, *kd.optional)}

        # --- pin bindings: role match (K003) + stray binding (K005) ---
        bound: dict[str, ir.Pin] = {}
        for pin in comp.pins:
            if pin.satisfies is None:
                continue
            m = pin.satisfies
            if m not in known_members:
                diags.append(_d(
                    "K005", f"pin '{pin.name}' of '{comp.name}' binds is='{m}', which no "
                    f"declared kind defines", pin.span, path, help_=_closest(m, known_members)))
                continue
            if m in req_role and pin.role != req_role[m]:
                diags.append(_d(
                    "K003", f"pin '{pin.name}' binds is='{m}' but its role is '{pin.role}'; "
                    f"kind requires '{m}' to be '{req_role[m]}'", pin.span, path))
            bound[m] = pin

        # --- required members must all be bound (K002) ---
        for member, role in req_role.items():
            if member not in bound:
                diags.append(_d(
                    "K002", f"component '{comp.name}' declares kind '{req_owner[member]}' but no "
                    f"pin binds required member '{member}' ({role})", comp.span, path,
                    help_=f"add is={member} to the pin that is the '{member}'"))

        # --- required params present with correct dimension (K004) ---
        attrs = dict(comp.attrs)
        for kd in contract:
            for pname, dim in kd.params:
                val = attrs.get(pname)
                if val is None:
                    diags.append(_d(
                        "K004", f"component '{comp.name}' declares kind '{kd.name}' but is "
                        f"missing required param '{pname}' ({dim})", comp.span, path))
                elif isinstance(val, ir.Quantity):
                    have = units.dim(val.unit)
                    if have != dim and not (dim == units.DIMENSIONLESS and have is None):
                        diags.append(_d(
                            "K004", f"param '{pname}' of '{comp.name}' has dimension "
                            f"'{have or val.unit}' but kind '{kd.name}' requires '{dim}'",
                            comp.span, path))
                elif dim != units.DIMENSIONLESS:
                    diags.append(_d(
                        "K004", f"param '{pname}' of '{comp.name}' must be a {dim} quantity",
                        comp.span, path))
    return diags
