"""std.electromech + std.connectivity + std.sensors: import cleanliness,
instantiation, on-disk footprint truth (with pad-id spot checks read from the
actual .kicad_mod files), and the rs485_node example end-to-end."""

import re
from pathlib import Path

import ohm
from ohm.cli import main as cli_main
from ohm.driver import compile_source

STD = Path(ohm.__file__).parent / "std"
ELECTROMECH_SRC = (STD / "electromech.ohm").read_text()
CONNECTIVITY_SRC = (STD / "connectivity.ohm").read_text()
SENSORS_SRC = (STD / "sensors.ohm").read_text()
EXAMPLE = Path(__file__).parent / "examples" / "rs485_node.ohm"
KICAD_FP = Path("/usr/share/kicad/footprints")

ELECTROMECH_BLOCKS = [
    "Header_1x02", "Header_1x03", "Header_1x04", "Header_1x05", "Header_1x06",
    "Header_1x08", "Header_1x10", "Header_2x03", "Header_2x05",
    "TactSwitch4", "SlideSwitch", "Relay_SPDT", "Jumper",
    "DIPSwitch_4", "RotaryEncoder_EC11", "Buzzer",  # expansion additions
]
CONNECTIVITY_BLOCKS = [
    "CANTransceiver_SOIC8", "RS485Transceiver_SOIC8",
    "USBConnector_MicroB", "USBConnector_C16",
]


def compile_at(tmp_path, name, source):
    f = tmp_path / name
    f.write_text(source)
    return compile_source(source, str(f))


def pad_ids(lib, name):
    """All pad ids read from the real .kicad_mod on disk."""
    mod = (KICAD_FP / f"{lib}.pretty" / f"{name}.kicad_mod").read_text()
    return re.findall(r'\(pad "([^"]*)"', mod)


# ---------- each file compiles clean standalone ----------
def test_use_std_electromech_compiles_clean(tmp_path):
    result = compile_at(tmp_path, "m.ohm", "use std.electromech\n")
    assert result.ok
    assert not result.diagnostics, [d.machine() for d in result.diagnostics]
    assert {c.name for c in result.design.components} == set(ELECTROMECH_BLOCKS)


def test_use_std_connectivity_compiles_clean(tmp_path):
    result = compile_at(tmp_path, "m.ohm", "use std.connectivity\n")
    assert result.ok
    assert not result.diagnostics, [d.machine() for d in result.diagnostics]
    assert {c.name for c in result.design.components} == set(CONNECTIVITY_BLOCKS)


def test_use_std_sensors_is_documented_emptiness(tmp_path):
    # sensors.ohm is the P12 adaptation pattern: comments only, no decls
    result = compile_at(tmp_path, "m.ohm", "use std.sensors\n")
    assert result.ok
    assert not result.diagnostics, [d.machine() for d in result.diagnostics]
    assert result.design.components == ()
    assert result.design.interfaces == ()
    assert "TEMPLATE (adapt to your datasheet)" in SENSORS_SRC


# ---------- every declared block instantiates clean ----------
def test_every_electromech_block_instantiates_clean(tmp_path):
    lines = "".join(f"    x{i} = {b}()\n" for i, b in enumerate(ELECTROMECH_BLOCKS))
    result = compile_at(tmp_path, "m.ohm", f"use std.electromech\nmodule M:\n{lines}")
    assert result.ok
    assert not result.diagnostics, [d.machine() for d in result.diagnostics]


def test_every_connectivity_block_instantiates_clean(tmp_path):
    lines = "".join(f"    x{i} = {b}()\n" for i, b in enumerate(CONNECTIVITY_BLOCKS))
    result = compile_at(tmp_path, "m.ohm", f"use std.connectivity\nmodule M:\n{lines}")
    assert result.ok
    assert not result.diagnostics, [d.machine() for d in result.diagnostics]


# ---------- footprints on disk + pad-id truth ----------
def test_all_string_footprints_exist_on_disk():
    ids = re.findall(r'package = "([^"]+)"', ELECTROMECH_SRC + CONNECTIVITY_SRC)
    assert len(ids) >= 15  # 13 electromech + 2 connector footprints
    for fid in ids:
        lib, name = fid.split(":")
        path = KICAD_FP / f"{lib}.pretty" / f"{name}.kicad_mod"
        assert path.is_file(), f"{fid} -> {path} missing"


def test_declared_pads_exist_in_footprints():
    # every pad= declared on a string-footprint component exists in its .kicad_mod
    for src in (ELECTROMECH_SRC, CONNECTIVITY_SRC):
        for block in re.split(r"\ncomponent ", src)[1:]:
            m = re.search(r'package = "([^"]+)"', block)
            if not m:  # generate(...) blocks are checked by the compiler itself
                continue
            lib, name = m.group(1).split(":")
            on_disk = set(pad_ids(lib, name))
            declared = re.findall(r"pad=(\w+)", block)
            for pad in declared:
                assert pad in on_disk, f"{name}: declared pad {pad} not in {sorted(on_disk)}"


def test_microb_pad_ids_spot_check():
    pads = pad_ids("Connector_USB", "USB_Micro-B_Amphenol_10118193-0001LF_Horizontal")
    assert {"1", "2", "3", "4", "5", "6"} <= set(pads)  # signals 1..5 + shield 6
    assert pads.count("6") > 1  # the shield is multiple physical pads, all id 6


def test_relay_pad_ids_spot_check():
    pads = pad_ids("Relay_THT", "Relay_SPDT_SANYOU_SRD_Series_Form_C")
    assert sorted(pads) == ["1", "2", "3", "4", "5"]  # exactly the 5 mapped pads


def test_usbc_pad_ids_spot_check():
    pads = set(pad_ids("Connector_USB",
                       "USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal"))
    assert {"A1", "A4", "A5", "A6", "A7", "A8", "A9", "A12",
            "B1", "B4", "B5", "B6", "B7", "B8", "B9", "B12", "S1"} <= pads


# ---------- example ----------
def test_example_rs485_node_builds_clean(tmp_path, capsys):
    rc = cli_main(["build", "--strict", "--emit", "netlist",
                   str(EXAMPLE), "-o", str(tmp_path)])
    assert rc == 0, capsys.readouterr().out
    net = (tmp_path / "rs485_node.net").read_text()
    assert "(footprint" in net


def test_example_rs485_node_zero_diagnostics():
    src = EXAMPLE.read_text()
    result = compile_source(src, str(EXAMPLE))
    assert result.ok and not result.diagnostics, \
        [d.machine() for d in result.diagnostics]
    assert result.design.top == "Rs485Node"
