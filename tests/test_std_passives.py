"""std.passives: import cleanliness, instantiation, overrides + Q003 ratings,
on-disk footprint truth, and the divider_led example end-to-end."""

import re
from pathlib import Path

import ohm
from ohm.cli import main as cli_main
from ohm.driver import compile_source

from tests.conftest import requires_kicad_fp

STD_SRC = (Path(ohm.__file__).parent / "std" / "passives.ohm").read_text()
EXAMPLE = Path(__file__).parent / "examples" / "divider_led.ohm"

BLOCKS = [
    "Resistor", "Capacitor", "CapacitorPolarized", "Inductor", "FerriteBead",
    "Crystal2", "Crystal4", "Thermistor", "Varistor", "Fuse", "PTCResettable",
    "Potentiometer",
]


def compile_at(tmp_path, name, source):
    f = tmp_path / name
    f.write_text(source)
    return compile_source(source, str(f))


def test_use_std_passives_compiles_clean(tmp_path):
    result = compile_at(tmp_path, "m.ohm", "use std.passives\n")
    assert result.ok
    assert not result.diagnostics, [d.machine() for d in result.diagnostics]
    assert {c.name for c in result.design.components} == set(BLOCKS)


def test_every_block_instantiates_clean(tmp_path):
    lines = "".join(f"    x{i} = {b}()\n" for i, b in enumerate(BLOCKS))
    result = compile_at(tmp_path, "m.ohm", f"use std.passives\nmodule M:\n{lines}")
    assert result.ok
    assert not result.diagnostics, [d.machine() for d in result.diagnostics]


def test_resistor_override_value_package_pmax(tmp_path):
    src = (
        "use std.passives\nmodule M:\n"
        "    r = Resistor(value=10kohm +/- 1%, package=generate(CHIP0805), pmax=125mW)\n"
    )
    result = compile_at(tmp_path, "m.ohm", src)
    assert result.ok, [d.machine() for d in result.diagnostics]


def test_resistor_pmax_rating_fires_q003(tmp_path):
    src = (
        "use std.passives\n"
        "component Src:\n    package = \"X\"\n"
        "    pin OUT: role=power_out, voltage=12V, pad=1\n"
        "    pin GND: role=ground, pad=2\n"
        "module M:\n"
        "    s = Src()\n"
        "    r = Resistor(value=100ohm, pmax=100mW)\n"
        "    s.OUT ~ r.P1\n    r.P2 ~ s.GND\n"
    )
    result = compile_at(tmp_path, "m.ohm", src)
    assert any(d.code == "Q003" for d in result.diagnostics)  # 1.44W > 100mW


@requires_kicad_fp
def test_all_string_footprints_exist_on_disk():
    ids = re.findall(r'package = "([^"]+)"', STD_SRC)
    assert ids, "expected at least one string footprint in std.passives"
    for fid in ids:
        lib, name = fid.split(":")
        path = Path(f"/usr/share/kicad/footprints/{lib}.pretty/{name}.kicad_mod")
        assert path.is_file(), f"{fid} -> {path} missing"


def test_example_divider_led_builds_clean(tmp_path, capsys):
    rc = cli_main(["build", "--strict", "--emit", "netlist",
                   str(EXAMPLE), "-o", str(tmp_path)])
    assert rc == 0, capsys.readouterr().out
    net = (tmp_path / "divider_led.net").read_text()
    assert "(footprint" in net
