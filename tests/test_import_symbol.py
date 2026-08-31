"""Ecosystem X1 (ADR-012): KiCad .kicad_sym → Ohm component blocks.

An embedded fixture keeps CI self-contained; the system-library tests (skipped
when KiCad isn't installed) prove the real ecosystem imports."""

import pytest

from ohm.driver import compile_source
from ohm.import_symbol import SymbolError, import_symbol, list_symbols

from tests.conftest import first_symbol_lib, requires_symbol_lib, symbol_lib

FIXTURE = """(kicad_symbol_lib (version 20220914) (generator test)
  (symbol "OpAmpish" (in_bom yes)
    (property "Reference" "U" (at 0 0 0))
    (property "Footprint" "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm" (at 0 0 0))
    (property "Datasheet" "https://example.com/ds.pdf" (at 0 0 0))
    (symbol "OpAmpish_1_1"
      (pin output line (at 0 0 0) (length 1) (name "OUT" ) (number "1" ))
      (pin input line (at 0 0 0) (length 1) (name "IN-" ) (number "2" ))
      (pin input line (at 0 0 0) (length 1) (name "IN+" ) (number "3" )))
    (symbol "OpAmpish_2_1"
      (pin power_in line (at 0 0 0) (length 1) (name "V+" ) (number "8" ))
      (pin power_in line (at 0 0 0) (length 1) (name "GND" ) (number "4" ))))
  (symbol "Derived" (extends "OpAmpish")
    (property "Reference" "U" (at 0 0 0)))
)"""

SYSLIB = symbol_lib("Device.kicad_sym")
# KiCad 9 moved the discrete FETs out of Device into Transistor_FET, so where
# Q_NMOS_GDS lives depends on the version installed. Ask for the symbol, not for
# one library's name: pinned to Transistor_FET this failed on KiCad 8, where that
# library exists but does not contain the part.
FET_LIBS = ("Transistor_FET.kicad_sym", "Device.kicad_sym")


def _lib_holding(symbol: str, *libs: str):
    """The first installed library that actually CONTAINS `symbol`.

    KiCad moves parts between libraries across releases (the discrete FETs went
    Device -> Transistor_FET at KiCad 9), so asking for a library name and
    assuming the part is in it fails on whichever half of the range is wrong.
    """
    from ohm.import_symbol import list_symbols

    for name in libs:
        path = symbol_lib(name)
        if path is not None and path.exists() and symbol in list_symbols(
                path.read_text(encoding="utf-8")):
            return path
    pytest.skip(f"no installed KiCad library holds {symbol} (looked in {', '.join(libs)})")



def test_import_generates_compilable_component():
    text = import_symbol(FIXTURE, "OpAmpish", "Test")
    assert 'package = "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"' in text
    assert "pin OUT: role=output, pad=1" in text
    assert "pin GND: role=power_in, pad=4" in text
    src = text + (
        "\nmodule M:\n    u = OpAmpish()\n    u.OUT ~ u.IN_N\n")
    r = compile_source(src, "imp.ohm")
    assert r.ok, [d.machine() for d in r.diagnostics]


def test_units_merge_into_one_package():
    text = import_symbol(FIXTURE, "OpAmpish")
    assert text.count("pin ") == 5  # both units + power


def test_extends_resolves_base_pins():
    text = import_symbol(FIXTURE, "Derived")
    assert "component Derived:" in text and text.count("pin ") == 5


def test_unknown_symbol_suggests():
    with pytest.raises(SymbolError, match="OpAmpish"):
        import_symbol(FIXTURE, "opamp")


def test_list_symbols():
    assert list_symbols(FIXTURE) == ["Derived", "OpAmpish"]


def test_deterministic():
    assert import_symbol(FIXTURE, "OpAmpish") == import_symbol(FIXTURE, "OpAmpish")


@requires_symbol_lib("Device.kicad_sym")
def test_real_device_resistor():
    text = import_symbol(SYSLIB.read_text(), "R", "Device")
    assert "pin P1: role=passive, pad=1" in text and "pin P2: role=passive, pad=2" in text


# guarded on Transistor_FET, not Device: that is the library this one reads
@pytest.mark.skipif(first_symbol_lib(*FET_LIBS) is None,
                    reason="no KiCad symbol library installed for the FETs")
def test_every_role_emitted_is_valid():
    from ohm.resolve import VALID_ROLES

    lib = _lib_holding("Q_NMOS_GDS", *FET_LIBS)
    text = import_symbol(lib.read_text(encoding="utf-8"), "Q_NMOS_GDS", lib.stem)
    for line in text.splitlines():
        if line.strip().startswith("pin "):
            role = line.split("role=")[1].split(",")[0]
            assert role in VALID_ROLES, line
