"""ADR-008 retrofit: std analog/power/logic/clock blocks declare their component
kinds. Each file still compiles clean standalone with kind checking on, every
retrofitted block instantiates clean, and the declared kinds are what we expect.
Un-kinded blocks (OpAmp_Dual_SOIC8, Timer555) stay base Component by design."""

from pathlib import Path

import pytest

from ohm.driver import compile_source

STD_DIR = Path(__file__).resolve().parent.parent / "ohm" / "std"

# block -> declared kinds (empty tuple = intentionally un-kinded base Component)
EXPECTED_KINDS = {
    "analog.ohm": {
        "OpAmp_SOT23_5": ("OpAmp",),
        "OpAmp_Dual_SOIC8": (),          # two op-amps in one package — single OpAmp kind doesn't fit
        "Comparator_SOT23_5": ("Comparator",),
    },
    "power.ohm": {
        "LDO_SOT23_5": ("LDO",),
        "LDO_SOT223": ("LDO",),
    },
    "logic.ohm": {
        "SingleGate_SOT23_5": ("LogicGate",),
        "Inverter_SOT23_5": ("Buffer",),
    },
    "clock.ohm": {
        "Oscillator_XO_4": ("Oscillator",),
        "CeramicResonator3": ("Resonator",),
        "Timer555": (),                  # 8 special-function pins — no clean kind
    },
}


@pytest.mark.parametrize("fname", EXPECTED_KINDS)
def test_std_file_compiles_clean_standalone_with_kinds(fname):
    path = STD_DIR / fname
    result = compile_source(path.read_text(), str(path))
    assert result.ok
    assert result.diagnostics == [], [d.machine() for d in result.diagnostics]


@pytest.mark.parametrize("fname", EXPECTED_KINDS)
def test_retrofitted_blocks_declare_expected_kinds(fname, tmp_path):
    names = list(EXPECTED_KINDS[fname])
    src = f"use std.{fname.removesuffix('.ohm')}\nmodule T:\n"
    src += "".join(f"    u{i} = {n}()\n" for i, n in enumerate(names))
    f = tmp_path / "t.ohm"
    f.write_text(src)
    result = compile_source(src, str(f))
    assert result.ok
    assert result.diagnostics == [], [d.machine() for d in result.diagnostics]
    by_name = {c.name: c for c in result.design.components}
    for name, kinds in EXPECTED_KINDS[fname].items():
        assert by_name[name].kinds == kinds, name
