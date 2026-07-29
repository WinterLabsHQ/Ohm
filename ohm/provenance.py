"""Pinout provenance (ADR-014): where a component's pin↔pad map came from.

S003 proves a pin↔pad map is TOTAL. It cannot prove it is CORRECT — a transposed
pin passes every checker tier and arrives on copper. That is the one failure the
compiler structurally could not see, and it is the expensive one: a wrong pad on
a supply pin is a dead board, not a warning.

Provenance closes it without breaking hermeticity (N1):

  * `pinout_source` records the origin — "kicad:<Lib>:<Symbol>" for a map taken
    from a real KiCad symbol library, or "datasheet:<reference>" for a human
    attestation naming the document and table.
  * `pinout_digest` freezes the pin↔pad map at the moment it was taken from that
    source. `ohm check` recomputes the digest from the DECLARED PINS ONLY — no
    file read, no network, no clock — so a map that drifts after it was verified
    is an S008 error. Hermetic by construction, exactly like Tier-Q's ratings.
  * `ohm verify-pinouts` (author-time, alongside `signoff` / `import-symbol`)
    actually re-reads the library and diffs pad-for-pad. Never in the build path.

The digest covers pin name → pad ONLY. Roles, ratings, `must_connect` and every
other attr are the intent layer, which is *expected* to be edited after import
(that is what `enrich` steps are for). The pad map is not.
"""

from __future__ import annotations

import hashlib
import re

from ohm import ir

# Kinds of origin a `pinout_source` may name. "kicad" is machine-checkable by
# `ohm verify-pinouts`; "datasheet" is a human attestation that is recorded and
# surfaced, never silently trusted as equivalent.
SOURCE_KINDS = ("kicad", "datasheet")

_DIGEST_PREFIX = "sha256:"
_DIGEST_LEN = 16  # 64 bits of collision resistance over a pin table; short enough to read


def digest_pairs(pairs: list[tuple[str, str]]) -> str:
    """Stable digest of (pin name, pad) pairs.

    Canonical form is `name=pad` per pin, sorted by pin name, newline-joined —
    declaration order is deliberately NOT part of the identity, so reordering the
    pin block is a no-op while moving a pin to a different pad is not.

    Takes plain pairs rather than ir.Pin so `import-symbol` can stamp a digest
    while it is still emitting text, before any IR exists.
    """
    body = "\n".join(f"{name}={pad}" for name, pad in sorted(pairs))
    return _DIGEST_PREFIX + hashlib.sha256(body.encode()).hexdigest()[:_DIGEST_LEN]


def pinmap_digest(pins: tuple[ir.Pin, ...]) -> str:
    """Stable digest of a component's pin↔pad map (see `digest_pairs`)."""
    return digest_pairs([(p.name, p.pad) for p in pins if p.pad is not None])


def verify_against_libs(design: ir.Design, lib_dir: str) -> list[tuple[str, str, str, list[str]]]:
    """Author-time: re-read real KiCad libraries and diff every component's pad map.

    NEVER called from `ohm check` — this reads the filesystem outside the project
    and so lives with `signoff` / `import-symbol` on the author-time side of N1.

    Returns (component, status, detail, mismatches) per component, where status is
    "ok" | "mismatch" | "unverifiable". Components with an explicit
    `pinout_source = "kicad:..."` are diffed against exactly that symbol. Those
    with only an `mpn=` are searched for by name across the directory, so a design
    that never recorded provenance can still be audited — which is the case that
    matters, because it is the one that ships wrong.
    """
    from pathlib import Path

    from ohm.import_symbol import SymbolError, _pin_ohm_name, _pins, _symbols

    libs = sorted(Path(lib_dir).glob("*.kicad_sym"))
    cache: dict[Path, dict] = {}

    def symbols_in(lib: Path) -> dict:
        if lib not in cache:
            try:
                cache[lib] = _symbols(lib.read_text(encoding="utf-8", errors="replace"))
            except (SymbolError, OSError):
                cache[lib] = {}
        return cache[lib]

    def real_padmap(lib: Path, sym_name: str) -> dict[str, str] | None:
        syms = symbols_in(lib)
        sym = syms.get(sym_name)
        if sym is None:
            return None
        ext = _kid_local(sym)
        src = syms.get(ext, sym) if ext else sym
        out: dict[str, str] = {}
        taken: set[str] = set()
        for number, name, _ in sorted(_pins(src), key=lambda p: (len(p[0]), p[0])):
            if number in out:
                continue
            out[number] = _pin_ohm_name(name, number, taken)
            taken.add(out[number])
        return out or None

    def _kid_local(sym) -> str | None:
        for n in sym:
            if isinstance(n, list) and n and n[0] == "extends":
                return str(n[1])
        return None

    results: list[tuple[str, str, str, list[str]]] = []
    for comp in design.components:
        attrs = dict(comp.attrs)
        declared = {p.pad: p.name for p in comp.pins if p.pad is not None}
        if not declared:
            continue
        src = attrs.get("pinout_source")
        target: tuple[Path, str] | None = None
        if isinstance(src, str) and (parsed := parse_source(src)) and parsed[0] == "kicad":
            lib_name, _, sym_name = parsed[1].partition(":")
            path = Path(lib_dir) / f"{lib_name}.kicad_sym"
            if path.exists() and sym_name in symbols_in(path):
                target = (path, sym_name)
            else:
                results.append((comp.name, "unverifiable",
                                f"pinout_source names {parsed[1]}, not found in {lib_dir}", []))
                continue
        elif isinstance(attrs.get("mpn"), str):
            # Search by part number. Symbol names encode variant families
            # (STM32F427V_G-I_Tx covers F427VG/F427VI), so match on the longest
            # shared prefix and keep the best hit — a loose rule here would
            # "verify" a connector against Device:D and report a false ok.
            best = 0
            for lib in libs:
                for cand in symbols_in(lib):
                    n = _prefix_score(str(attrs["mpn"]), cand)
                    if n > best:
                        best, target = n, (lib, cand)
        else:
            continue  # no mpn: a generic shape block, not a claim about silicon
        if target is None:
            results.append((comp.name, "unverifiable", "no candidate symbol found", []))
            continue

        lib, sym_name = target
        real = real_padmap(lib, sym_name)
        if real is None:
            results.append((comp.name, "unverifiable", f"{sym_name} has no pins", []))
            continue
        ref = f"{lib.stem}:{sym_name}"
        # Disjoint pad namespaces mean the generated package FAMILY is wrong (a
        # grid-named LGA standing in for a perimeter-numbered part), not that N
        # individual pads moved. Reporting that as 2N line items buries it.
        if not (set(real) & set(declared)):
            results.append((comp.name, "mismatch", ref, [
                f"pad namespace is disjoint — library uses {_pad_span(real)}, "
                f"source uses {_pad_span(declared)}; the generated package family "
                f"does not match this part"]))
            continue
        bad = []
        for pad in sorted(set(real) | set(declared), key=lambda p: (len(p), p)):
            want, got = real.get(pad, "(absent)"), declared.get(pad, "(absent)")
            if want != got and not _same_pin(want, got):
                bad.append(f"pad {pad}: library says {want}, source says {got}")
        results.append((comp.name, "mismatch" if bad else "ok", ref, bad))
    return results


def _pad_span(padmap: dict[str, str]) -> str:
    pads = sorted(padmap, key=lambda p: (len(p), p))
    return f"{pads[0]}..{pads[-1]} ({len(pads)})" if pads else "(none)"


def _prefix_score(mpn: str, symbol: str) -> int:
    """Length of the shared alphanumeric prefix, or 0 if it is too weak to trust.

    Needs >= 6 shared characters AND >= 60% of the shorter name, so MS5611
    matches MS5611-01BA while DF13-6P-1.25DSA does not match Device:D.
    """
    a = "".join(ch for ch in mpn.upper() if ch.isalnum())
    b = "".join(ch for ch in symbol.upper() if ch.isalnum())
    if not a or not b:
        return 0
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n if n >= 6 and n >= 0.6 * min(len(a), len(b)) else 0


# Libraries and datasheets disagree on supply-pin spelling far more often than
# they disagree on position. Folding the known aliases is what keeps this tool
# reporting the 4 real transpositions instead of 8 findings half of which are
# spelling — a checker people learn to ignore protects nothing.
_ALIAS = {"VI": "IN", "VIN": "IN", "VO": "OUT", "VOUT": "OUT",
          "VCC": "VDD", "VSS": "GND", "GND": "GND"}


def _canon(name: str) -> str:
    # Strip the trailing disambiguator libraries and datasheets use for "the other
    # pin of the same function": KiCad writes I_O2_2 / VDD_2, part authors write
    # IO2_B. Both mean the same node, so neither is a position error.
    n = re.sub(r"_[0-9A-Z]$", "", name.upper())
    n = "".join(ch for ch in n if ch.isalnum())
    return _ALIAS.get(n, n)


def _same_pin(a: str, b: str) -> bool:
    """Tolerate naming style, never position.

    `VDD_2`/`VDD`, `PB3_SWO`/`PB3`, `SCL_SPC`/`SPC`, `V_I`/`IN` and `I_O1`/`IO1_A`
    are the same pin. `PD6`/`VSS` and `CS`/`AUX_CL` are not.
    """
    na, nb = a.upper().strip("_"), b.upper().strip("_")
    if na == nb or "(ABSENT)" in (na, nb) and na == nb:
        return True
    ca, cb = _canon(a), _canon(b)
    if ca == cb or ca.startswith(cb) or cb.startswith(ca):
        return True
    pa, pb = set(na.split("_")), set(nb.split("_"))
    return bool(pa & pb) and not (na[:1].isdigit() or nb[:1].isdigit())


def parse_source(value: str) -> tuple[str, str] | None:
    """"kicad:Sensor_Motion:MPU-6000" -> ("kicad", "Sensor_Motion:MPU-6000").

    Returns None if the string does not name one of SOURCE_KINDS, so the caller
    can report a bad declaration rather than silently accepting it.
    """
    kind, sep, rest = value.partition(":")
    if not sep or kind not in SOURCE_KINDS or not rest.strip():
        return None
    return kind, rest.strip()
