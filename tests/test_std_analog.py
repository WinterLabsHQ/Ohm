"""std.analog / std.power / std.logic — every std file compiles clean, every
declared block instantiates clean, string footprints (and their pads) exist in
the on-disk KiCad library, and the LDO example builds clean."""

import re
from pathlib import Path

import pytest

from ohm.driver import compile_source

STD_DIR = Path(__file__).resolve().parent.parent / "ohm" / "std"
KICAD_FP = Path("/usr/share/kicad/footprints")
STD_FILES = ["analog.ohm", "power.ohm", "logic.ohm"]

EXPECTED = {
    "analog.ohm": {"OpAmp_SOT23_5", "OpAmp_Dual_SOIC8", "Comparator_SOT23_5"},
    "power.ohm": {"LDO_SOT23_5", "LDO_SOT223"},
    "logic.ohm": {"SingleGate_SOT23_5", "Inverter_SOT23_5"},
}


def declared_components(text):
    return re.findall(r"^component (\w+):", text, flags=re.M)


@pytest.mark.parametrize("fname", STD_FILES)
def test_std_file_compiles_clean_standalone(fname):
    path = STD_DIR / fname
    result = compile_source(path.read_text(), str(path))
    assert result.ok
    assert result.diagnostics == [], [d.machine() for d in result.diagnostics]


@pytest.mark.parametrize("fname", STD_FILES)
def test_declared_blocks_match_spec_and_templates_stay_comments(fname):
    text = (STD_DIR / fname).read_text()
    assert set(declared_components(text)) == EXPECTED[fname]
    # TEMPLATE blocks must be comments: no uncommented 'component' beyond EXPECTED
    # (covered by the equality above) and every template header is present.
    assert "# TEMPLATE (adapt to your datasheet):" in text


@pytest.mark.parametrize("fname", STD_FILES)
def test_every_declared_block_instantiates_clean(fname, tmp_path):
    text = (STD_DIR / fname).read_text()
    names = declared_components(text)
    assert names
    src = f"use std.{fname.removesuffix('.ohm')}\nmodule T:\n"
    src += "".join(f"    u{i} = {n}()\n" for i, n in enumerate(names))
    f = tmp_path / "t.ohm"
    f.write_text(src)
    result = compile_source(src, str(f))
    assert result.ok
    assert result.diagnostics == [], [d.machine() for d in result.diagnostics]


@pytest.mark.skipif(not KICAD_FP.is_dir(), reason="KiCad footprint library not installed")
def test_string_footprints_exist_on_disk_with_declared_pads():
    checked = 0
    for fname in STD_FILES:
        text = (STD_DIR / fname).read_text()
        for block in re.split(r"^component ", text, flags=re.M)[1:]:
            m = re.search(r'package = "([^"]+):([^"]+)"', block)
            if m is None:
                continue  # generate(...) packages are compiler-verified
            lib, name = m.groups()
            mod = KICAD_FP / f"{lib}.pretty" / f"{name}.kicad_mod"
            assert mod.is_file(), mod
            disk_pads = set(re.findall(r'\(pad "([^"]+)"', mod.read_text()))
            # only real pin lines — commented TEMPLATE pads don't count
            declared = set(re.findall(r"^    pin .*?pad=(\d+)", block, flags=re.M))
            assert declared <= disk_pads, (mod, declared, disk_pads)
            checked += 1
    assert checked >= 1  # at least LDO_SOT223


def test_ldo_supply_example_builds_clean():
    path = Path(__file__).resolve().parent / "examples" / "ldo_supply.ohm"
    result = compile_source(path.read_text(), str(path))
    assert result.ok
    assert result.diagnostics == [], [d.machine() for d in result.diagnostics]
    assert result.design.top == "LdoSupply"
    assert result.low is not None  # emitted (clean build)
