"""ADR-008 kind retrofit of std.passives / std.discrete / std.protection.

Each std file must still compile with ZERO diagnostics now that kind conformance
is checked, every retrofitted block must instantiate clean, and a couple of
blocks must actually carry their declared kind.
"""

import pytest

from ohm.driver import compile_source

# blocks that were retrofitted with a (Kind) header, per file
RETROFITTED = {
    "passives": [
        "CapacitorPolarized", "FerriteBead", "Crystal2", "Crystal4",
        "Thermistor", "Varistor", "Fuse", "PTCResettable", "Potentiometer",
        # already-retrofitted in the base file, re-checked here for good measure
        "Resistor", "Capacitor", "Inductor",
    ],
    "discrete": [
        "Diode", "Schottky", "Zener", "LED", "TVS_Uni", "TVS_Bi",
        "BJT_NPN", "BJT_PNP", "DigitalTransistor_NPN", "DigitalTransistor_PNP",
        "NMOS", "PMOS", "JFET_N", "BridgeRectifier",
    ],
    "protection": ["ESDDiode", "PolyFuse"],
}

# expected kind for a representative sample
EXPECTED_KIND = {
    "LED": ("LED",),
    "NMOS": ("MOSFET",),
    "BJT_NPN": ("BJT",),
    "BridgeRectifier": ("BridgeRectifier",),
    "Potentiometer": ("Potentiometer",),
    "PolyFuse": ("Fuse",),
    "Crystal2": ("Crystal",),
}


@pytest.mark.parametrize("mod", ["passives", "discrete", "protection"])
def test_std_file_compiles_zero_diagnostics(mod):
    result = compile_source(f"use std.{mod}\n", "m.ohm")
    assert result.ok and not result.diagnostics, [d.machine() for d in result.diagnostics]


@pytest.mark.parametrize(
    "mod,block",
    [(m, b) for m, bs in RETROFITTED.items() for b in bs],
)
def test_retrofitted_block_instantiates_clean(mod, block):
    src = f"use std.{mod}\nmodule M:\n    x = {block}()\n"
    result = compile_source(src, "m.ohm")
    assert result.ok and not result.diagnostics, [d.machine() for d in result.diagnostics]


@pytest.mark.parametrize("block,kind", list(EXPECTED_KIND.items()))
def test_block_carries_declared_kind(block, kind):
    mod = next(m for m, bs in RETROFITTED.items() if block in bs)
    result = compile_source(f"use std.{mod}\n", "m.ohm")
    (comp,) = [c for c in result.design.components if c.name == block]
    assert comp.kinds == kind


def test_tvsarray4_left_unkinded():
    # multi-channel ESD array has no clean single kind — base Component.
    result = compile_source("use std.protection\n", "m.ohm")
    (comp,) = [c for c in result.design.components if c.name == "TVSArray4"]
    assert comp.kinds == ()
