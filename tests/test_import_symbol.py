"""Ecosystem X1 (ADR-012): KiCad .kicad_sym → Ohm component blocks.

An embedded fixture keeps CI self-contained; the system-library tests (skipped
when KiCad isn't installed) prove the real ecosystem imports."""

from pathlib import Path

import pytest

from ohm.driver import compile_source
from ohm.import_symbol import SymbolError, import_symbol, list_symbols

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

SYSLIB = Path("/usr/share/kicad/symbols/Device.kicad_sym")


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


@pytest.mark.skipif(not SYSLIB.exists(), reason="system KiCad symbols not installed")
def test_real_device_resistor():
    text = import_symbol(SYSLIB.read_text(), "R", "Device")
    assert "pin P1: role=passive, pad=1" in text and "pin P2: role=passive, pad=2" in text


@pytest.mark.skipif(not SYSLIB.exists(), reason="system KiCad symbols not installed")
def test_every_role_emitted_is_valid():
    from ohm.resolve import VALID_ROLES

    text = import_symbol(SYSLIB.read_text(), "Q_NMOS_GDS", "Device")
    for line in text.splitlines():
        if line.strip().startswith("pin "):
            role = line.split("role=")[1].split(",")[0]
            assert role in VALID_ROLES, line
