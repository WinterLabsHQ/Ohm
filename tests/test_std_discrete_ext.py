"""Extension coverage for the appended std.discrete / std.electromech blocks
(BridgeRectifier, pre-biased & JFET transistors, DIP switch, rotary encoder,
buzzer) and the ac_and_ui example. Guards that the two files still compile
clean standalone and that every new string footprint + declared pad is real."""

import re
from pathlib import Path

import pytest

from ohm.driver import compile_source
from ohm.emit_netlist import emit_netlist

from tests.conftest import KICAD_FOOTPRINTS, requires_kicad_fp

STD = Path(__file__).parent.parent / "ohm" / "std"
EXAMPLE = Path(__file__).parent / "examples" / "ac_and_ui.ohm"

NEW_BLOCKS = {
    "discrete": ["BridgeRectifier", "DigitalTransistor_NPN",
                 "DigitalTransistor_PNP", "JFET_N"],
    "electromech": ["DIPSwitch_4", "RotaryEncoder_EC11", "Buzzer"],
}

# string footprints introduced by the appended blocks -> pad ids we declared
NEW_FOOTPRINTS = {
    "Diode_SMD:Diode_Bridge_Diotec_ABS": {"1", "2", "3", "4"},
    "Button_Switch_SMD:SW_DIP_SPSTx04_Slide_9.78x12.34mm_W8.61mm_P2.54mm":
        {"1", "2", "3", "4", "5", "6", "7", "8"},
    "Rotary_Encoder:RotaryEncoder_Alps_EC11E-Switch_Vertical_H20mm":
        {"A", "B", "C", "S1", "S2", "MP"},
    "Buzzer_Beeper:MagneticBuzzer_CUI_CST-931RP-A": {"1", "2"},
}


def pad_ids(fid):
    lib, name = fid.split(":")
    mod = (KICAD_FOOTPRINTS / f"{lib}.pretty" / f"{name}.kicad_mod").read_text()
    return set(re.findall(r'\(pad "([^"]*)"', mod))


@pytest.mark.parametrize("name", ["discrete.ohm", "electromech.ohm"])
def test_file_still_compiles_clean_standalone(name):
    path = STD / name
    result = compile_source(path.read_text(), str(path))
    assert result.ok and not result.diagnostics, \
        [d.machine() for d in result.diagnostics]


@pytest.mark.parametrize(
    "mod,block",
    [(m, b) for m, bs in NEW_BLOCKS.items() for b in bs],
)
def test_new_block_instantiates_clean(tmp_path, mod, block):
    src = f"use std.{mod}\nmodule M:\n    x = {block}()\n"
    f = tmp_path / "m.ohm"
    f.write_text(src)
    result = compile_source(src, str(f))
    assert result.ok and not result.diagnostics, \
        [d.machine() for d in result.diagnostics]


@requires_kicad_fp
@pytest.mark.parametrize("fid,declared", NEW_FOOTPRINTS.items())
def test_new_footprint_and_declared_pads_exist_on_disk(fid, declared):
    lib, name = fid.split(":")
    assert (KICAD_FOOTPRINTS / f"{lib}.pretty" / f"{name}.kicad_mod").is_file(), fid
    on_disk = pad_ids(fid)
    missing = declared - on_disk
    assert not missing, f"{fid}: declared pads {missing} not in {sorted(on_disk)}"


def test_ac_and_ui_example_builds_and_emits():
    result = compile_source(EXAMPLE.read_text(), str(EXAMPLE))
    assert result.ok and not result.diagnostics, \
        [d.machine() for d in result.diagnostics]
    assert result.design.top == "AcAndUi"
    net = emit_netlist(result.low, "ac_and_ui")
    assert "(footprint" in net
    assert "Diode_Bridge_Diotec_ABS" in net
