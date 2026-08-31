"""Build `ohm/fpdata/pads.json.gz` — the pad ids KiCad's own footprints carry.

AUTHOR-TIME ACQUISITION, never the build path (the same rule
`ohm/pkggen/pindata_iface.py` follows). Ohm's compile must stay hermetic, so the
answer to "does this footprint have a pad called 11?" ships as data rather than
being looked up on the user's disk at check time.

Why this exists. Ohm emits a pad-LESS stub for a `package = "Lib:Footprint"`
reference: the geometry comes from the user's installed library at layout time,
and only the pad id travels, in the netlist. So the netlist is correct only if
Ohm's declared pad id matches the library the user actually has — and KiCad
renames pads between releases. KiCad 10 normalised shield/shell pads to "SH"
across 163 footprints (`0`, `S1`, `6`, `5`, `11`, `9` -> `SH`), which silently
leaves the shield unconnected on any board that names the old id. Nothing in Ohm
could see that, because Ohm never reads a footprint.

Usage:

    # inside a KiCad container, once per version
    python3 tools/gen_fpdata.py dump 10 /tmp/pads-10.json

    # then, anywhere
    python tools/gen_fpdata.py build ohm/fpdata/pads.json.gz /tmp/pads-*.json

`dump` reads /usr/share/kicad/footprints and must run with that KiCad installed;
it deliberately uses nothing but the standard library so it runs on the
container's own interpreter with no venv. `build` merges the dumps into the
shipped table.
"""

from __future__ import annotations

import gzip
import json
import pathlib
import re
import sys

_PAD_QUOTED = re.compile(r'\(pad\s+"([^"]*)"')
_PAD_BARE = re.compile(r"\(pad\s+([^\s\"()]+)\s")


def dump(root: pathlib.Path, out: pathlib.Path) -> int:
    """Every `Lib:Footprint` -> sorted pad ids, from an installed library tree."""
    table: dict[str, list[str]] = {}
    for pretty in sorted(root.glob("*.pretty")):
        lib = pretty.name[: -len(".pretty")]
        for mod in sorted(pretty.glob("*.kicad_mod")):
            text = mod.read_text(encoding="utf-8", errors="replace")
            pads = set(_PAD_QUOTED.findall(text)) | set(_PAD_BARE.findall(text))
            pads.discard("")  # an unnamed pad is a mechanical feature, not a connection
            if pads:
                table[f"{lib}:{mod.stem}"] = sorted(pads)
    out.write_text(json.dumps(table, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    return len(table)


def _encode(pads: list[str]) -> int | list[str]:
    """Contiguous 1..N -> N. 84% of KiCad's footprints are plain numbered pads,
    and storing them as one integer is what keeps the shipped table small."""
    try:
        nums = sorted(int(p) for p in pads)
    except ValueError:
        return sorted(pads)
    return len(nums) if nums == list(range(1, len(nums) + 1)) else sorted(pads)


def _renames(old: list[str], new: list[str]) -> dict[str, str]:
    """Pads that vanished -> the pad they became, when that is unambiguous.

    Only the old->new direction is derived, and only when exactly one pad was
    added: that covers every real case (KiCad's shield normalisation, including
    the ten footprints where two shield pads MERGE into one "SH"), and refuses
    to guess when several pads appear at once. new->old is not derived at all,
    because a merge makes it genuinely ambiguous.
    """
    gone, added = sorted(set(old) - set(new)), sorted(set(new) - set(old))
    if not gone or len(added) != 1:
        return {}
    return {g: added[0] for g in gone}


def build(out: pathlib.Path, dumps: dict[str, pathlib.Path]) -> dict:
    """Merge per-version dumps into the shipped table."""
    per: dict[str, dict[str, list[str]]] = {
        major: json.loads(path.read_text(encoding="utf-8")) for major, path in dumps.items()
    }
    majors = sorted(per, key=int)
    newest = majors[-1]
    every = sorted(set().union(*(set(t) for t in per.values())))

    base: dict[str, int | list[str]] = {}
    overrides: dict[str, dict[str, int | list[str]]] = {}
    absent: dict[str, list[str]] = {m: [] for m in majors}
    renames: dict[str, dict[str, dict[str, str]]] = {}

    for fp in every:
        here = [m for m in majors if fp in per[m]]
        reference = per[newest][fp] if fp in per[newest] else per[here[-1]][fp]
        base[fp] = _encode(reference)
        for major in majors:
            if fp not in per[major]:
                absent[major].append(fp)
                continue
            encoded = _encode(per[major][fp])
            if encoded != base[fp]:
                overrides.setdefault(fp, {})[major] = encoded
        # A pad renamed between two adjacent versions is recorded against the
        # LATER one: "if you are targeting this KiCad, the old id is now this".
        for older, newer in zip(here, here[1:]):
            mapping = _renames(per[older][fp], per[newer][fp])
            if mapping:
                renames.setdefault(fp, {})[newer] = mapping

    table = {
        "kicad_majors": majors,
        "base_is": newest,
        "base": base,
        "overrides": overrides,
        "absent": absent,
        "renames": renames,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    # mtime=0 because gzip stamps the current time by default, which would make
    # the shipped artifact differ on every run and turn the CI "regenerate and
    # diff" check into permanent noise. Everything upstream of here is already
    # sorted, so the bytes are a pure function of the input libraries.
    out.write_bytes(gzip.compress(
        json.dumps(table, separators=(",", ":"), sort_keys=True).encode("utf-8"), 9, mtime=0))
    return table


def main(argv: list[str]) -> int:
    if len(argv) >= 4 and argv[1] == "dump":
        n = dump(pathlib.Path("/usr/share/kicad/footprints"), pathlib.Path(argv[3]))
        print(f"KiCad {argv[2]}: {n} footprints -> {argv[3]}")
        return 0
    if len(argv) >= 4 and argv[1] == "build":
        dumps = {}
        for path in argv[3:]:
            m = re.search(r"pads-(\d+)\.json$", path)
            if not m:
                print(f"cannot read a KiCad major from {path!r} "
                      "(expected .../pads-<major>.json)", file=sys.stderr)
                return 2
            dumps[m.group(1)] = pathlib.Path(path)
        t = build(pathlib.Path(argv[2]), dumps)
        size = pathlib.Path(argv[2]).stat().st_size
        print(f"majors={t['kicad_majors']} footprints={len(t['base'])} "
              f"overrides={len(t['overrides'])} renames={len(t['renames'])} "
              f"-> {argv[2]} ({size / 1024:.0f} KB)")
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
