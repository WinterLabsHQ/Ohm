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

from ohm.provenance import digest_pairs
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
    """Every named symbol in a .kicad_sym library.

    Like `signoff.read_board`, this reads a file the USER's KiCad wrote — 7.0
    through 10.0 — so it walks to named forms and never anchors on layout
    (ADR-021). Where a shape it depends on is absent it says so: an empty result
    would mean "this library has no symbols", which is a claim about the library
    rather than about Ohm's ability to read it, and a wrong pad map imported from
    a misread library ends up on copper.
    """
    root = parse_sexpr(lib_text)
    lib = next((n for n in root if isinstance(n, list) and n
                and n[0] == "kicad_symbol_lib"), None)
    if lib is None:
        raise SymbolError("not a .kicad_sym library (no kicad_symbol_lib form)")
    forms = _kids(lib, "symbol")
    # The name is positional — `(symbol "Device:R" …)`. A form that does not
    # carry one there is not a symbol this reader can name, and a library where
    # NONE of them do is a library Ohm cannot read at all, which must not present
    # as an empty library.
    out = {str(s[1]): s for s in forms if len(s) > 1 and isinstance(s[1], str)}
    if forms and not out:
        raise SymbolError(
            f'{len(forms)} `(symbol …)` forms, none naming the symbol as a bare '
            'string in the first field (`(symbol "Name" …)`) — this library does '
            "not have the shape Ohm reads; please report the KiCad version that "
            "wrote it")
    return out

def list_symbols(lib_text: str) -> list[str]:
    return sorted(_symbols(lib_text))


def _prop(sym: Sexpr, name: str) -> str | None:
    """A symbol property's value, or None.

    Tolerant by design: `Footprint` and `Datasheet` are optional, and a symbol
    that declares neither is imported with a `package = "TODO"` line the author
    has to fill in. Nothing here is guessed, and nothing silently wrong follows
    from a miss — unlike a pad number.
    """
    for p in _kids(sym, "property"):
        if len(p) >= 3 and p[1] == name and isinstance(p[2], str):
            v = str(p[2])
            return v if v not in ("", "~") else None
    return None


def _pin_forms(sym: Sexpr) -> list[Sexpr]:
    """Every `(pin …)` form of a symbol, wherever the library keeps it.

    KiCad 6-10 put every pin inside a unit sub-symbol (`Name_1_1`), so the old
    reader looked only there. That is a layout assumption in the same family as
    the ones ADR-021 is about: a library that declares pins directly on the
    symbol came back with NO pins, and `import_symbol` then told the user the
    part was "graphic-only" — a confident claim about the SYMBOL produced by a
    failure to read the FILE. Collecting both shapes costs one recursive call and
    cannot mis-read either.
    """
    out = list(_kids(sym, "pin"))
    for unit in _kids(sym, "symbol"):  # unit sub-symbols "Name_1_1"
        out.extend(_pin_forms(unit))
    return out


def _pins(sym: Sexpr) -> list[tuple[str, str, str]]:
    """(number, name, kicad_type) across ALL units — a component is the package.

    A pin with no readable `(number "N")` is skipped rather than guessed at: the
    number IS the pad, and inventing one is how a transposition reaches copper.
    Callers distinguish "no pins" from "pins Ohm could not read" through
    `_pin_forms` — see `import_symbol`.
    """
    out = []
    for pin in _pin_forms(sym):
        num_n, name_n = _kid(pin, "number"), _kid(pin, "name")
        if num_n is None or len(num_n) < 2 or not isinstance(num_n[1], str):
            continue
        # The electrical type is positional — `(pin power_in line …)`. Anything
        # else in that slot is left unmapped on purpose: it lands in the emitted
        # source as `role=free` with an "unmapped KiCad type" comment, which the
        # author sees, rather than being silently read as `passive`.
        ktype = str(pin[1]) if len(pin) > 1 and isinstance(pin[1], str) else "(unreadable)"
        name = (str(name_n[1]) if name_n is not None and len(name_n) > 1
                and isinstance(name_n[1], str) else "~")
        out.append((str(num_n[1]), name, ktype))
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
        if len(ext) < 2 or not isinstance(ext[1], str):
            raise SymbolError(f"'{symbol}' declares `(extends …)` with no base symbol "
                              "name Ohm can read, so its pins cannot be found")
        base = str(ext[1])
        if base not in syms:
            raise SymbolError(f"'{symbol}' extends '{base}' which is not in this library")
        pin_src = syms[base]

    comp = re.sub(r"\W", "_", symbol)
    if not re.match(r"^[A-Za-z_]", comp):
        comp = f"P{comp}"  # 2N7002 -> P2N7002: identifiers may not start with a digit
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
        # "No pins" and "pins in a shape Ohm does not read" are opposite
        # statements — the first is about the symbol, the second about this
        # reader — and telling the user the first when the second is true is how
        # a format change becomes a wrong answer about a part.
        forms = len(_pin_forms(pin_src))
        if forms:
            raise SymbolError(
                f"'{symbol}' has {forms} `(pin …)` forms, none carrying a readable "
                '`(number "N")` — Ohm cannot tell which pad each pin is on, and '
                "will not guess; please report the KiCad version that wrote this "
                "library")
        raise SymbolError(f"'{symbol}' has no pins (graphic-only symbol?)")
    seen_nums: dict[str, str] = {}
    taken: set[str] = set()
    pin_lines: list[str] = []
    for number, name, ktype in sorted(pins, key=lambda p: (len(p[0]), p[0])):
        if number in seen_nums:
            pin_lines.append(f"    # pad {number} repeated in symbol "
                             f"(kept first: {seen_nums[number]})")
            continue
        role = _ROLE.get(ktype, "free")
        ohm_name = _pin_ohm_name(name, number, taken)
        taken.add(ohm_name)
        seen_nums[number] = ohm_name
        note = "" if ktype in _ROLE else f"  # unmapped KiCad type '{ktype}'"
        pin_lines.append(f"    pin {ohm_name}: role={role}, pad={number}{note}")

    # Provenance (ADR-014): this map came from a real library, so say so and freeze
    # it. `ohm check` recomputes the digest from the declared pins alone, so any
    # later edit to a pad is an S008 error instead of a silent transposition.
    if lib_name:
        out.append(f'    pinout_source = "kicad:{src_label}"')
        out.append(f'    pinout_digest = "{digest_pairs([(n, p) for p, n in seen_nums.items()])}"')
    out.extend(pin_lines)
    return "\n".join(out) + "\n"
