"""Ecosystem X1 (ADR-012): real KiCad symbols → Ohm components.

`ohm import-symbol Device.kicad_sym R` generates a `component` block with the
symbol's REAL pins: pad = KiCad pin number, role = mapped from KiCad's
electrical type — no guessed pinouts, no TODO roles. The entire installed
KiCad symbol ecosystem becomes Ohm components one command at a time.

Author-time codegen (like `ohm gen-package`): text in → text out, never in the
build path — hermeticity (N1) holds. Reuses the signoff S-expression reader.
"""

from __future__ import annotations

import re

from ohm.signoff import Sexpr, _kid, _kids, parse_sexpr

# KiCad electrical type → Ohm role (VALID_ROLES in resolve.py)
_ROLE = {
    "input": "input", "output": "output", "bidirectional": "bidirectional",
    "passive": "passive", "power_in": "power_in", "power_out": "power_out",
    "open_collector": "open_collector", "open_emitter": "output",
    "tri_state": "output", "unspecified": "free", "free": "free",
    "no_connect": "nc",
}


class SymbolError(Exception):
    pass


def _symbols(lib_text: str) -> dict[str, Sexpr]:
    root = parse_sexpr(lib_text)
    lib = next((n for n in root if isinstance(n, list) and n
                and n[0] == "kicad_symbol_lib"), None)
    if lib is None:
        raise SymbolError("not a .kicad_sym library (no kicad_symbol_lib form)")
    return {str(s[1]): s for s in _kids(lib, "symbol")}

def list_symbols(lib_text: str) -> list[str]:
    return sorted(_symbols(lib_text))


def _prop(sym: Sexpr, name: str) -> str | None:
    for p in _kids(sym, "property"):
        if len(p) >= 3 and p[1] == name:
            v = str(p[2])
            return v if v not in ("", "~") else None
    return None


def _pins(sym: Sexpr) -> list[tuple[str, str, str]]:
    """(number, name, kicad_type) across ALL units — a component is the package."""
    out = []
    for unit in _kids(sym, "symbol"):  # unit sub-symbols "Name_1_1"
        for pin in _kids(unit, "pin"):
            num_n, name_n = _kid(pin, "number"), _kid(pin, "name")
            if num_n is None:
                continue
            out.append((str(num_n[1]),
                        str(name_n[1]) if name_n is not None else "~",
                        str(pin[1]) if len(pin) > 1 else "passive"))
    return out


def _pin_ohm_name(name: str, number: str, taken: set[str]) -> str:
    name = name.replace("+", "_P").replace("-", "_N")  # IN- -> IN_N, V+ -> V_P
    base = re.sub(r"\W", "_", name).strip("_")
    if not base or base == "_" or name == "~":
        base = f"P{number}"
    if not re.match(r"^[A-Za-z_]", base):
        base = f"P{base}"
    out = base
    k = 2
    while out in taken:
        out, k = f"{base}_{k}", k + 1
    return out


def import_symbol(lib_text: str, symbol: str, lib_name: str = "") -> str:
    """Generate an Ohm `component` block for one symbol in a .kicad_sym library."""
    syms = _symbols(lib_text)
    if symbol not in syms:
        near = [s for s in syms if symbol.lower() in s.lower()][:5]
        raise SymbolError(f"symbol '{symbol}' not in library"
                          + (f" (near: {', '.join(near)})" if near else ""))
    sym = syms[symbol]
    ext = _kid(sym, "extends")  # derived symbol: pins live on the base
    pin_src = sym
    if ext is not None:
        base = str(ext[1])
        if base not in syms:
            raise SymbolError(f"'{symbol}' extends '{base}' which is not in this library")
        pin_src = syms[base]

    comp = re.sub(r"\W", "_", symbol)
    src_label = f"{lib_name}:{symbol}" if lib_name else symbol
    out = [f"# imported from KiCad symbol {src_label} by `ohm import-symbol`",
           f"component {comp}:"]
    fp = _prop(sym, "Footprint") or (_prop(pin_src, "Footprint") if ext is not None else None)
    out.append(f'    package = "{fp}"' if fp else
               '    package = "TODO"  # symbol declares no footprint — pick one '
               "(ki_fp_filters: " + (_prop(sym, "ki_fp_filters") or "none") + ")")
    if (ds := _prop(sym, "Datasheet")):
        out.append(f'    datasheet = "{ds}"')

    pins = _pins(pin_src)
    if not pins:
        raise SymbolError(f"'{symbol}' has no pins (graphic-only symbol?)")
    seen_nums: dict[str, str] = {}
    taken: set[str] = set()
    for number, name, ktype in sorted(pins, key=lambda p: (len(p[0]), p[0])):
        if number in seen_nums:
            out.append(f"    # pad {number} repeated in symbol (kept first: {seen_nums[number]})")
            continue
        role = _ROLE.get(ktype, "free")
        ohm_name = _pin_ohm_name(name, number, taken)
        taken.add(ohm_name)
        seen_nums[number] = ohm_name
        note = "" if ktype in _ROLE else f"  # unmapped KiCad type '{ktype}'"
        out.append(f"    pin {ohm_name}: role={role}, pad={number}{note}")
    return "\n".join(out) + "\n"
