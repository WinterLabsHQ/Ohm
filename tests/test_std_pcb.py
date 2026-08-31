"""std.pcb + std.clock: import cleanliness, instantiation (including the 0-pin
mechanical parts, which must still build and emit), on-disk footprint truth, and
the blinky_555 example end-to-end."""

import re
from pathlib import Path

import ohm
from ohm.cli import main as cli_main
from ohm.driver import compile_source

from tests.conftest import footprint, requires_kicad_fp

STD_DIR = Path(ohm.__file__).parent / "std"
PCB_SRC = (STD_DIR / "pcb.ohm").read_text()
CLOCK_SRC = (STD_DIR / "clock.ohm").read_text()
EXAMPLE = Path(__file__).parent / "examples" / "blinky_555.ohm"

PCB_BLOCKS = [
    "MountingHole", "MountingHolePlated", "Fiducial", "TestPoint",
    "NetTie2", "ZeroOhm",
]
CLOCK_BLOCKS = ["Oscillator_XO_4", "CeramicResonator3", "Timer555"]

# parts with no numbered pad → zero declared pins (still legal, still emit)
ZERO_PIN = {"MountingHole", "Fiducial"}


def compile_at(tmp_path, name, source):
    f = tmp_path / name
    f.write_text(source)
    return compile_source(source, str(f))


def test_use_std_pcb_compiles_clean(tmp_path):
    result = compile_at(tmp_path, "m.ohm", "use std.pcb\n")
    assert result.ok
    assert not result.diagnostics, [d.machine() for d in result.diagnostics]
    assert {c.name for c in result.design.components} == set(PCB_BLOCKS)


def test_use_std_clock_compiles_clean(tmp_path):
    result = compile_at(tmp_path, "m.ohm", "use std.clock\n")
    assert result.ok
    assert not result.diagnostics, [d.machine() for d in result.diagnostics]
    assert {c.name for c in result.design.components} == set(CLOCK_BLOCKS)


def test_every_block_instantiates_clean(tmp_path):
    blocks = PCB_BLOCKS + CLOCK_BLOCKS
    lines = "".join(f"    x{i} = {b}()\n" for i, b in enumerate(blocks))
    src = f"use std.pcb\nuse std.clock\nmodule M:\n{lines}"
    result = compile_at(tmp_path, "m.ohm", src)
    assert result.ok
    assert not result.diagnostics, [d.machine() for d in result.diagnostics]


def test_zero_pin_parts_have_no_pins(tmp_path):
    result = compile_at(tmp_path, "m.ohm", "use std.pcb\n")
    for c in result.design.components:
        if c.name in ZERO_PIN:
            assert len(c.pins) == 0, f"{c.name} should declare no pins"


def test_zero_pin_parts_build_and_emit(tmp_path):
    """A 0-pin mechanical feature must still make it into the emitted netlist."""
    src = (
        "use std.pcb\n"
        "module M:\n"
        "    h = MountingHole()\n"
        "    fid = Fiducial()\n"
    )
    (tmp_path / "z.ohm").write_text(src)
    out = tmp_path / "out"
    out.mkdir()
    rc = cli_main(["build", "--strict", "--emit", "netlist",
                   str(tmp_path / "z.ohm"), "-o", str(out)])
    assert rc == 0
    net = (out / "z.net").read_text()
    assert "MountingHole" in net and "Fiducial" in net


@requires_kicad_fp
def test_all_string_footprints_exist_on_disk():
    ids = re.findall(r'package = "([^"]+)"', PCB_SRC + "\n" + CLOCK_SRC)
    assert ids, "expected string footprints in std.pcb / std.clock"
    for fid in ids:
        lib, name = fid.split(":")
        path = footprint(f"{lib}.pretty/{name}.kicad_mod")
        assert path.is_file(), f"{fid} -> {path} missing"


def test_example_blinky_555_builds_clean(tmp_path, capsys):
    rc = cli_main(["build", "--strict", "--emit", "netlist",
                   str(EXAMPLE), "-o", str(tmp_path)])
    assert rc == 0, capsys.readouterr().out
    net = (tmp_path / "blinky_555.net").read_text()
    assert "(footprint" in net
    assert "SOIC-8" in net  # the 555 emitted with its generated footprint
