"""std.electromech + std.connectivity + std.sensors: import cleanliness,
instantiation, on-disk footprint truth (with pad-id spot checks read from the
actual .kicad_mod files), and the rs485_node example end-to-end."""

import re
from pathlib import Path

import ohm
from ohm.cli import main as cli_main
from ohm.driver import compile_source

from tests.conftest import KICAD_FOOTPRINTS, requires_kicad_fp

STD = Path(ohm.__file__).parent / "std"
ELECTROMECH_SRC = (STD / "electromech.ohm").read_text()
CONNECTIVITY_SRC = (STD / "connectivity.ohm").read_text()
SENSORS_SRC = (STD / "sensors.ohm").read_text()
EXAMPLE = Path(__file__).parent / "examples" / "rs485_node.ohm"

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
    mod = (KICAD_FOOTPRINTS / f"{lib}.pretty" / f"{name}.kicad_mod").read_text()
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
@requires_kicad_fp
def test_all_string_footprints_exist_on_disk():
    ids = re.findall(r'package = "([^"]+)"', ELECTROMECH_SRC + CONNECTIVITY_SRC)
    assert len(ids) >= 15  # 13 electromech + 2 connector footprints
    for fid in ids:
        lib, name = fid.split(":")
        path = KICAD_FOOTPRINTS / f"{lib}.pretty" / f"{name}.kicad_mod"
        assert path.is_file(), f"{fid} -> {path} missing"


@requires_kicad_fp
def test_declared_pads_reach_a_real_pad_in_the_installed_library():
    """Every declared pad lands on copper in the KiCad that is actually here.

    Not "the declared id is present verbatim": KiCad 10 renamed shield and shell
    pads to "SH" across 163 footprints, and the std library declares the id KiCad
    8 and 9 use. `ohm.lower` writes the target's spelling into the netlist
    (ADR-021), so what must hold is that the declared pad EITHER exists in this
    library OR is one the table knows was renamed into a pad that does. Asserting
    the literal id would force the std library to be wrong on some supported
    KiCad in order to be right on this one.
    """
    from ohm import fpdata

    for src in (ELECTROMECH_SRC, CONNECTIVITY_SRC):
        for block in re.split(r"\ncomponent ", src)[1:]:
            m = re.search(r'package = "([^"]+)"', block)
            if not m:  # generate(...) blocks are checked by the compiler itself
                continue
            fp = m.group(1)
            lib, name = fp.split(":")
            on_disk = set(pad_ids(lib, name))
            for pad in re.findall(r"pad=(\w+)", block):
                if pad in on_disk:
                    continue
                renamed = next((fpdata.renamed_to(fp, pad, major)
                                for major in fpdata.kicad_majors()
                                if fpdata.renamed_to(fp, pad, major) in on_disk), None)
                assert renamed is not None, (
                    f"{name}: declared pad {pad} is not in {sorted(on_disk)}, and no "
                    f"rename in ohm/fpdata reaches one that is")


@requires_kicad_fp
def test_microb_pad_ids_spot_check():
    pads = pad_ids("Connector_USB", "USB_Micro-B_Amphenol_10118193-0001LF_Horizontal")
    assert {"1", "2", "3", "4", "5"} <= set(pads)  # the five signals, always numeric
    # The shell is several physical pads sharing ONE id — "6" through KiCad 9,
    # "SH" from KiCad 10. Which spelling is present depends on the library
    # installed here, so assert the shape (one shared id, repeated) rather than
    # the name; ohm/fpdata is what maps between them.
    shell = "SH" if "SH" in pads else "6"
    assert shell in pads, f"neither shell id present: {sorted(set(pads))}"
    assert pads.count(shell) > 1


@requires_kicad_fp
def test_relay_pad_ids_spot_check():
    pads = pad_ids("Relay_THT", "Relay_SPDT_SANYOU_SRD_Series_Form_C")
    assert sorted(pads) == ["1", "2", "3", "4", "5"]  # exactly the 5 mapped pads


@requires_kicad_fp
def test_usbc_pad_ids_spot_check():
    pads = set(pad_ids("Connector_USB",
                       "USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal"))
    assert {"A1", "A4", "A5", "A6", "A7", "A8", "A9", "A12",
            "B1", "B4", "B5", "B6", "B7", "B8", "B9", "B12"} <= pads
    # the shell: "S1" through KiCad 9, "SH" from KiCad 10
    assert pads & {"S1", "SH"}, f"no shell pad present: {sorted(pads)}"


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
