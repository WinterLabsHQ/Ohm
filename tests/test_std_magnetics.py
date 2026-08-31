"""std.magnetics + std.rf: both libs import clean standalone, every declared
block instantiates clean, TEMPLATE blocks stay comments (do not parse as
components), string footprints exist on disk, and the USB EMC front-end example
builds and emits a netlist."""

import re
from pathlib import Path

import ohm
from ohm.cli import main as cli_main
from ohm.driver import compile_source

from tests.conftest import footprint, requires_kicad_fp

MAG_SRC = (Path(ohm.__file__).parent / "std" / "magnetics.ohm").read_text()
RF_SRC = (Path(ohm.__file__).parent / "std" / "rf.ohm").read_text()
EXAMPLE = Path(__file__).parent / "examples" / "usb_emc_frontend.ohm"

MAG_BLOCKS = ["CommonModeChoke", "Transformer4", "CoupledInductor4"]
RF_BLOCKS = ["ChipAntenna", "Balun"]
# SAWFilter / RFModule are TEMPLATE comment blocks — must NOT parse as components.
RF_TEMPLATES = ["SAWFilter", "RFModule"]


def compile_at(tmp_path, name, source):
    f = tmp_path / name
    f.write_text(source)
    return compile_source(source, str(f))


def test_use_std_magnetics_compiles_clean(tmp_path):
    result = compile_at(tmp_path, "m.ohm", "use std.magnetics\n")
    assert result.ok
    assert not result.diagnostics, [d.machine() for d in result.diagnostics]
    assert {c.name for c in result.design.components} == set(MAG_BLOCKS)


def test_use_std_rf_compiles_clean(tmp_path):
    result = compile_at(tmp_path, "m.ohm", "use std.rf\n")
    assert result.ok
    assert not result.diagnostics, [d.machine() for d in result.diagnostics]
    names = {c.name for c in result.design.components}
    assert names == set(RF_BLOCKS)
    # TEMPLATE blocks are comments, not code
    for t in RF_TEMPLATES:
        assert t not in names


def test_every_magnetics_block_instantiates_clean(tmp_path):
    lines = "".join(f"    x{i} = {b}()\n" for i, b in enumerate(MAG_BLOCKS))
    result = compile_at(tmp_path, "m.ohm", f"use std.magnetics\nmodule M:\n{lines}")
    assert result.ok
    assert not result.diagnostics, [d.machine() for d in result.diagnostics]


def test_every_rf_block_instantiates_clean(tmp_path):
    lines = "".join(f"    x{i} = {b}()\n" for i, b in enumerate(RF_BLOCKS))
    result = compile_at(tmp_path, "m.ohm", f"use std.rf\nmodule M:\n{lines}")
    assert result.ok
    assert not result.diagnostics, [d.machine() for d in result.diagnostics]


@requires_kicad_fp
def test_all_string_footprints_exist_on_disk():
    ids = re.findall(r'package = "([^"]+)"', MAG_SRC) + re.findall(r'package = "([^"]+)"', RF_SRC)
    # keep only concrete "Lib:Name" ids; TEMPLATE comment blocks carry
    # placeholder "..." packages that are not real footprints.
    ids = [i for i in ids if ":" in i and "..." not in i]
    assert ids, "expected string footprints in std.magnetics + std.rf"
    for fid in ids:
        lib, name = fid.split(":")
        path = footprint(f"{lib}.pretty/{name}.kicad_mod")
        assert path.is_file(), f"{fid} -> {path} missing"


def test_example_usb_emc_frontend_builds_clean(tmp_path, capsys):
    rc = cli_main(["build", "--strict", "--emit", "netlist",
                   str(EXAMPLE), "-o", str(tmp_path)])
    assert rc == 0, capsys.readouterr().out
    net = (tmp_path / "usb_emc_frontend.net").read_text()
    assert "(footprint" in net
