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

from tests.conftest import requires_kicad_fp

STD_SRC = (Path(ohm.__file__).parent / "std" / "connectors.ohm").read_text()
EXAMPLE = Path(__file__).parent / "examples" / "power_input.ohm"
FP_ROOT = Path("/usr/share/kicad/footprints")

BLOCKS = [
    "TerminalBlock_2", "TerminalBlock_3", "DCBarrelJack", "JST_XH_2", "JST_XH_3",
    "RJ45", "MicroSD", "AudioJack_3p5", "SMAConnector", "UFL", "CoinCellHolder",
    "Battery2", "ICSocket_DIP8",
]


def _design():
    return compile_source("use std.connectors\n", "m.ohm")


def _fp_path(fid):
    lib, name = fid.split(":")
    return FP_ROOT / f"{lib}.pretty" / f"{name}.kicad_mod"


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
    """Exhaustive: every pad= on every block is a real pad id in the .kicad_mod."""
    comps = {c.name: c for c in _design().design.components}
    for name in BLOCKS:
        c = comps[name]
        disk = _pads_on_disk(c.package)
        for pin in c.pins:
            assert pin.pad in disk, (
                f"{name}.{pin.name} maps to pad '{pin.pad}' not present in "
                f"{c.package} (footprint has {sorted(disk)})"
            )


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
    assert rj_pads <= _pads_on_disk(comps["RJ45"].package)

    # MicroSD: SD-mode contacts + card-detect + shell "11"
    sd_pads = {p.pad for p in comps["MicroSD"].pins}
    assert {"1", "8", "9", "10", "11"} <= sd_pads
    assert sd_pads <= _pads_on_disk(comps["MicroSD"].package)

    # CoinCellHolder: two-terminal + / -
    cc_pads = {p.pad for p in comps["CoinCellHolder"].pins}
    assert cc_pads == {"1", "2"}
    assert cc_pads <= _pads_on_disk(comps["CoinCellHolder"].package)


def test_example_power_input_builds_clean(tmp_path, capsys):
    rc = cli_main(["build", "--strict", "--emit", "netlist",
                   str(EXAMPLE), "-o", str(tmp_path)])
    assert rc == 0, capsys.readouterr().out
    net = (tmp_path / "power_input.net").read_text()
    assert "(footprint" in net
