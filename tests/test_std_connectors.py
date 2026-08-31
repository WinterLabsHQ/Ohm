"""std.connectors: import cleanliness, instantiation, on-disk footprint truth,
declared-pad-ids-exist-in-the-footprint, and the power_input example end-to-end.

The N3 promise for this library is that every pad id was read off a real
.kicad_mod — so these tests read the same files and prove each declared pad=
actually exists in the footprint on disk."""

import re
from pathlib import Path

import ohm
from ohm.cli import main as cli_main
from ohm.driver import compile_source

from tests.conftest import KICAD_FOOTPRINTS, requires_kicad_fp

STD_SRC = (Path(ohm.__file__).parent / "std" / "connectors.ohm").read_text()
EXAMPLE = Path(__file__).parent / "examples" / "power_input.ohm"

BLOCKS = [
    "TerminalBlock_2", "TerminalBlock_3", "DCBarrelJack", "JST_XH_2", "JST_XH_3",
    "RJ45", "MicroSD", "AudioJack_3p5", "SMAConnector", "UFL", "CoinCellHolder",
    "Battery2", "ICSocket_DIP8",
]


def _design():
    return compile_source("use std.connectors\n", "m.ohm")


def _fp_path(fid):
    lib, name = fid.split(":")
    return KICAD_FOOTPRINTS / f"{lib}.pretty" / f"{name}.kicad_mod"


def _pads_on_disk(fid):
    """The set of pad ids actually present in the footprint file."""
    text = _fp_path(fid).read_text()
    return set(re.findall(r'\(pad\s+"([^"]*)"', text))


def compile_at(tmp_path, name, source):
    f = tmp_path / name
    f.write_text(source)
    return compile_source(source, str(f))


def test_use_std_connectors_compiles_clean(tmp_path):
    result = compile_at(tmp_path, "m.ohm", "use std.connectors\n")
    assert result.ok
    assert not result.diagnostics, [d.machine() for d in result.diagnostics]
    assert {c.name for c in result.design.components} == set(BLOCKS)


def test_every_block_instantiates_clean(tmp_path):
    lines = "".join(f"    x{i} = {b}()\n" for i, b in enumerate(BLOCKS))
    result = compile_at(tmp_path, "m.ohm", f"use std.connectors\nmodule M:\n{lines}")
    assert result.ok
    assert not result.diagnostics, [d.machine() for d in result.diagnostics]


@requires_kicad_fp
def test_all_string_footprints_exist_on_disk():
    ids = re.findall(r'package = "([^"]+)"', STD_SRC)
    assert len(ids) == len(BLOCKS), "every connector block uses a string footprint"
    for fid in ids:
        assert _fp_path(fid).is_file(), f"{fid} -> {_fp_path(fid)} missing"


@requires_kicad_fp
def test_every_declared_pad_exists_in_its_footprint():
    """Exhaustive: every pad= reaches real copper in the library installed here.

    Via a rename where KiCad has one — the std library declares the id KiCad 8
    and 9 use, and `ohm.lower` writes KiCad 10's spelling into the netlist
    (ADR-021). Demanding the literal id would force the std library to be wrong
    on two supported majors in order to be right on the third.
    """
    from ohm import fpdata

    comps = {c.name: c for c in _design().design.components}
    for name in BLOCKS:
        c = comps[name]
        disk = _pads_on_disk(c.package)
        for pin in c.pins:
            if pin.pad in disk:
                continue
            reached = any(fpdata.renamed_to(c.package, pin.pad, major) in disk
                          for major in fpdata.kicad_majors())
            assert reached, (
                f"{name}.{pin.name} maps to pad '{pin.pad}', which is not in "
                f"{c.package} (footprint has {sorted(disk)}) and no rename in "
                f"ohm/fpdata reaches one that is")



def _assert_pads_reach_copper(name: str, declared: set, package: str) -> None:
    """Every declared pad exists in the installed library, or was renamed into
    one that does. The std library declares the id KiCad 8 and 9 use; `ohm.lower`
    writes the target's spelling into the netlist (ADR-021), so demanding the
    literal id here would force the std library to be wrong on two supported
    majors in order to be right on the third."""
    from ohm import fpdata

    disk = _pads_on_disk(package)
    for pad in sorted(declared):
        if pad in disk:
            continue
        assert any(fpdata.renamed_to(package, pad, major) in disk
                   for major in fpdata.kicad_majors()), (
            f"{name}: pad '{pad}' is not in {package} (has {sorted(disk)}) and no "
            f"rename in ohm/fpdata reaches one that is")


@requires_kicad_fp
def test_pad_ids_spot_checks():
    """Explicit checks for the tricky footprints called out in the task:
    numeric, named, and repeated-id shell pads."""
    comps = {c.name: c for c in _design().design.components}

    # DCBarrelJack: numeric tip/sleeve/switch
    assert {p.pad for p in comps["DCBarrelJack"].pins} == {"1", "2", "3"}
    assert _pads_on_disk(comps["DCBarrelJack"].package) >= {"1", "2", "3"}

    # RJ45: signal 1..8 + LEDs 9..12 + repeated shell id "SH"
    rj_pads = {p.pad for p in comps["RJ45"].pins}
    assert "SH" in rj_pads and {"1", "8", "9", "12"} <= rj_pads
    _assert_pads_reach_copper("RJ45", rj_pads, comps["RJ45"].package)

    # MicroSD: SD-mode contacts + card-detect + the shell, which the std library
    # declares as "11" (KiCad 8/9) and KiCad 10 calls "SH". Compared through the
    # rename table for the same reason as the exhaustive check above — the
    # declaration must REACH copper here, not match this library's spelling.
    sd_pads = {p.pad for p in comps["MicroSD"].pins}
    assert {"1", "8", "9", "10", "11"} <= sd_pads
    _assert_pads_reach_copper("MicroSD", sd_pads, comps["MicroSD"].package)

    # CoinCellHolder: two-terminal + / -
    cc_pads = {p.pad for p in comps["CoinCellHolder"].pins}
    assert cc_pads == {"1", "2"}
    assert cc_pads <= _pads_on_disk(comps["CoinCellHolder"].package)


def test_every_stdlib_footprint_exists_in_every_supported_kicad():
    """A stdlib footprint is a GENERIC placeholder, so it has to exist in every
    KiCad Ohm supports — otherwise a standard part binds to a footprint the
    user's library does not have, and the pin arrives unconnected.

    This gate did not exist, and two things had already slipped through it. The
    first was caught by hand: `TerminalBlock_bornier-*` was renamed by KiCad 9
    and fixed one part at a time. The second was invisible until KiCad 7 pad data
    existed — `TerminalBlock_CUI:*` was added to KiCad's libraries AFTER 7, so on
    the DEFAULT target both screw-terminal parts named a footprint the user did
    not have, with every gate green. Fixing the two parts again would have left
    the third instance to be found by whoever hits it; this is the rule instead.

    Silent on `generate(...)` packages, which Ohm builds itself, and on any
    library the shipped table does not know — a third-party footprint is
    unknown, not wrong (ADR-021 section 3).
    """
    from ohm import fpdata

    majors = fpdata.kicad_majors()
    assert majors, "the shipped pad table must cover at least one KiCad"

    offenders = []
    for src in sorted((Path(ohm.__file__).parent / "std").glob("*.ohm")):
        for lineno, line in enumerate(src.read_text(encoding="utf-8").splitlines(), 1):
            m = re.search(r'package\s*=\s*"([^"]+)"', line)
            if not m:
                continue
            fp = m.group(1)
            if ":" not in fp or not fpdata.known(fp):
                continue
            missing = tuple(x for x in majors if not fpdata.present_in(fp, x))
            if missing:
                why = ", ".join(f"KiCad {x} ({fpdata.absence(fp, x)})" for x in missing)
                offenders.append(f"{src.name}:{lineno} {fp} -> missing in {why}")

    assert not offenders, (
        "stdlib footprints must exist in every supported KiCad:\n  "
        + "\n  ".join(offenders))


def test_example_power_input_builds_clean(tmp_path, capsys):
    rc = cli_main(["build", "--strict", "--emit", "netlist",
                   str(EXAMPLE), "-o", str(tmp_path)])
    assert rc == 0, capsys.readouterr().out
    net = (tmp_path / "power_input.net").read_text()
    assert "(footprint" in net
