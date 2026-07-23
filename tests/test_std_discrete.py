"""std.discrete / std.protection / std.opto: standalone compile, instantiation,
footprint-on-disk verification, and the mosfet_switch example."""

import re
from pathlib import Path

import pytest

from ohm.driver import compile_source
from ohm.emit_netlist import emit_netlist

STD = Path(__file__).parent.parent / "ohm" / "std"
EXAMPLE = Path(__file__).parent / "examples" / "mosfet_switch.ohm"
KICAD_FP = Path("/usr/share/kicad/footprints")

STD_FILES = ["discrete.ohm", "protection.ohm", "opto.ohm"]

BLOCKS = {
    "discrete": ["Diode", "Schottky", "Zener", "LED", "BJT_NPN", "BJT_PNP",
                 "NMOS", "PMOS", "TVS_Uni", "TVS_Bi"],
    "protection": ["ESDDiode", "TVSArray4", "PolyFuse"],
    "opto": ["Optocoupler_4", "Optocoupler_6", "PhotoDiode", "PhotoTransistor"],
}


@pytest.mark.parametrize("name", STD_FILES)
def test_std_file_compiles_clean_standalone(name):
    path = STD / name
    result = compile_source(path.read_text(), str(path))
    assert result.ok and not result.diagnostics, [d.machine() for d in result.diagnostics]


@pytest.mark.parametrize("mod,block", [(m, b) for m, bs in BLOCKS.items() for b in bs])
def test_every_block_instantiates_clean(tmp_path, mod, block):
    src = f"use std.{mod}\nmodule M:\n    x = {block}()\n"
    f = tmp_path / "m.ohm"
    f.write_text(src)
    result = compile_source(src, str(f))
    assert result.ok and not result.diagnostics, [d.machine() for d in result.diagnostics]


def test_all_string_footprints_exist_on_disk():
    pat = re.compile(r'package\s*=\s*"([^":]+):([^"]+)"')
    checked = 0
    for name in STD_FILES:
        for lib, fp in pat.findall((STD / name).read_text()):
            mod = KICAD_FP / f"{lib}.pretty" / f"{fp}.kicad_mod"
            assert mod.is_file(), f"{name}: missing footprint {mod}"
            # pad ids used in std files are 1 and 2 — both must exist in the mod
            text = mod.read_text()
            for pad in ("1", "2"):
                assert re.search(rf'\(pad "?{pad}"? ', text), f"{mod} lacks pad {pad}"
            checked += 1
    assert checked >= 8  # every string-footprint block covered


def test_mosfet_switch_example_builds_clean_and_emits():
    result = compile_source(EXAMPLE.read_text(), str(EXAMPLE))
    assert result.ok and not result.diagnostics, [d.machine() for d in result.diagnostics]
    net = emit_netlist(result.low, "mosfet_switch")
    assert '(footprint "LED_SMD:LED_0603_1608Metric")' in net
    assert '(footprint "ohm:SOT23-3")' in net or "SOT23" in net
