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


# ---------- the library shapes this reader stands on (ADR-021) ----------
#
# `import_symbol` reads a file the USER's KiCad wrote, so the shapes it assumes
# are the exposure. Each fixture below breaks exactly one of them and asserts the
# error NAMES it. The failure being designed out is the confident wrong answer:
# every one of these used to come back as "graphic-only symbol?" or an
# IndexError, which are statements about the part, or about nothing at all.

DIRECT_PINS = """(kicad_symbol_lib (version 20220914) (generator test)
  (symbol "Direct"
    (property "Footprint" "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm" (at 0 0 0))
    (pin passive line (at 0 0 0) (length 1) (name "A" ) (number "1" ))
    (pin passive line (at 0 0 0) (length 1) (name "B" ) (number "2" ))))"""

NO_NUMBERS = """(kicad_symbol_lib (version 20220914) (generator test)
  (symbol "NoNum"
    (symbol "NoNum_1_1"
      (pin passive line (at 0 0 0) (length 1) (name "A" ))
      (pin passive line (at 0 0 0) (length 1) (name "B" )))))"""

NO_SYMBOL_NAMES = """(kicad_symbol_lib (version 20220914) (generator test)
  (symbol (property "Reference" "R" (at 0 0 0))))"""

EXTENDS_NOTHING = """(kicad_symbol_lib (version 20220914) (generator test)
  (symbol "Base" (symbol "Base_1_1"
      (pin passive line (at 0 0 0) (length 1) (name "A" ) (number "1" ))))
  (symbol "Der" (extends)))"""


def test_pins_declared_directly_on_the_symbol_are_imported():
    """KiCad 6-10 keep every pin in a unit sub-symbol (`Name_1_1`), and reading
    only that shape is a layout assumption: a library that declares pins on the
    symbol itself came back with no pins at all, and the user was told the part
    was graphic-only — a claim about the SYMBOL produced by a failure to read the
    FILE. Both shapes are now collected."""
    text = import_symbol(DIRECT_PINS, "Direct", "Test")
    assert "pin A: role=passive, pad=1" in text
    assert "pin B: role=passive, pad=2" in text


def test_pins_without_a_readable_number_are_not_called_graphic_only():
    """The number IS the pad. A pin Ohm cannot number is not a pin it may guess
    at — and 'this symbol has no pins' is the wrong thing to say about a symbol
    whose pins simply were not in the shape the reader knows."""
    with pytest.raises(SymbolError, match=r'number'):
        import_symbol(NO_NUMBERS, "NoNum", "Test")
    with pytest.raises(SymbolError) as exc:
        import_symbol(NO_NUMBERS, "NoNum", "Test")
    assert "graphic-only" not in str(exc.value)
    assert "2 `(pin" in str(exc.value)


def test_a_library_whose_symbols_are_not_named_says_which_shape_is_missing():
    """`(symbol "Name" …)` is positional. A library where no form carries a name
    there is a library Ohm cannot read, which must not present as an empty
    library (`list_symbols` returning [] is a fact about the ecosystem)."""
    with pytest.raises(SymbolError, match=r"none naming the symbol"):
        list_symbols(NO_SYMBOL_NAMES)


def test_extends_with_no_base_name_is_named_not_an_indexerror():
    with pytest.raises(SymbolError, match=r"extends"):
        import_symbol(EXTENDS_NOTHING, "Der", "Test")


def test_a_well_formed_library_is_unaffected():
    """The guard against over-tightening: every shape KiCad 7-10 actually writes
    still imports, and imports the same text as before."""
    assert list_symbols(FIXTURE) == ["Derived", "OpAmpish"]
    assert import_symbol(FIXTURE, "OpAmpish", "Test").count("pin ") == 5
