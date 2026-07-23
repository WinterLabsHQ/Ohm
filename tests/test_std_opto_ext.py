"""std.opto extension: opto.ohm still compiles clean standalone, every new
declared display/emitter/IR block instantiates clean, the referenced
footprints (+ their pad ids) exist on disk, and the led_panel example builds
and emits the real WS2812B footprint."""

import re
from pathlib import Path

import pytest

from ohm.driver import compile_source
from ohm.emit_netlist import emit_netlist

OPTO = Path(__file__).parent.parent / "ohm" / "std" / "opto.ohm"
EXAMPLE = Path(__file__).parent / "examples" / "led_panel.ohm"
KICAD_FP = Path("/usr/share/kicad/footprints")

NEW_BLOCKS = [
    "AddressableLED", "RGBLED_CommonAnode", "RGBLED_CommonCathode",
    "SevenSegment", "SevenSegment_CA", "IREmitter", "IRReceiver",
]


def test_opto_compiles_clean_standalone():
    r = compile_source(OPTO.read_text(), str(OPTO))
    assert r.ok and not r.diagnostics, [d.machine() for d in r.diagnostics]


@pytest.mark.parametrize("block", NEW_BLOCKS)
def test_new_block_instantiates_clean(tmp_path, block):
    src = f"use std.opto\nmodule M:\n    x = {block}()\n"
    f = tmp_path / "m.ohm"
    f.write_text(src)
    r = compile_source(src, str(f))
    assert r.ok and not r.diagnostics, [d.machine() for d in r.diagnostics]


# (block footprint, pad ids that block maps) — verified on disk.
@pytest.mark.parametrize("lib,fp,pads", [
    ("LED_SMD", "LED_WS2812B_PLCC4_5.0x5.0mm_P3.2mm", ["1", "2", "3", "4"]),
    ("Display_7Segment", "CC56-12GWA",
     [str(n) for n in range(1, 13)]),
])
def test_footprint_and_pads_exist_on_disk(lib, fp, pads):
    mod = KICAD_FP / f"{lib}.pretty" / f"{fp}.kicad_mod"
    assert mod.is_file(), f"missing footprint {mod}"
    text = mod.read_text()
    for pad in pads:
        assert re.search(rf'\(pad "?{pad}"? ', text), f"{mod} lacks pad {pad}"


def test_led_panel_example_builds_and_emits():
    r = compile_source(EXAMPLE.read_text(), str(EXAMPLE))
    # Honest: the panel data input (px1.DIN) has no driver in this fragment
    # (no MCU) -> one expected I006 warning; build still succeeds.
    assert r.ok, [d.machine() for d in r.diagnostics]
    codes = {d.code for d in r.diagnostics}
    assert codes <= {"I006"}, [d.machine() for d in r.diagnostics]
    net = emit_netlist(r.low, "led_panel")
    assert '(footprint "LED_SMD:LED_WS2812B_PLCC4_5.0x5.0mm_P3.2mm")' in net
