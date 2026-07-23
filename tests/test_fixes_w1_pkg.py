"""Regression tests for W1 audit fixes: pins-kwarg type safety, CHIP pad sets,
unknown generate() kwargs, and all-unmapped external footprints (S004)."""

from ohm.driver import compile_source


def s_codes(result):
    return [d.code for d in result.diagnostics if d.code.startswith("S")]


def test_quantity_pins_is_s006_not_crash():
    src = (
        "component X:\n"
        "    package = generate(SOIC, pins=8mm)\n"
        "    pin A: role=input, pad=1\n"
    )
    result = compile_source(src, "q.ohm")
    hits = [d for d in result.diagnostics if d.code == "S006"]
    assert hits and "pins" in hits[0].message


def test_chip0603_pads_1_2_clean():
    src = (
        "component R:\n"
        "    package = generate(CHIP0603)\n"
        "    pin P1: role=passive, pad=1\n"
        "    pin P2: role=passive, pad=2\n"
    )
    result = compile_source(src, "chip.ohm")
    assert result.ok, [d.machine() for d in result.diagnostics]
    assert s_codes(result) == []


def test_unknown_generate_kwarg_is_s006():
    src = (
        "component X:\n"
        "    package = generate(SOIC, pins=4, bogus=7, pitchh=2mm)\n"
        "    pin A: role=input, pad=1\n"
        "    pin B: role=input, pad=2\n"
        "    pin C: role=output, pad=3\n"
        "    pin D: role=ground, pad=4\n"
    )
    result = compile_source(src, "typo.ohm")
    hits = [d for d in result.diagnostics if d.code == "S006"]
    assert hits and "bogus" in hits[0].message and "pitchh" in hits[0].message


def test_chip_rejects_pins_kwarg():
    src = (
        "component R:\n"
        "    package = generate(CHIP0603, pins=2)\n"
        "    pin P1: role=passive, pad=1\n"
        "    pin P2: role=passive, pad=2\n"
    )
    result = compile_source(src, "chippins.ohm")
    hits = [d for d in result.diagnostics if d.code == "S006"]
    assert hits and "pins" in hits[0].message and "density" in hits[0].message


def test_all_unmapped_external_footprint_warns_s004():
    src = (
        "component U:\n"
        '    package = "SOT-23-5"\n'
        "    pin VIN: role=power_in\n"
        "    pin GND: role=ground\n"
    )
    result = compile_source(src, "u.ohm")
    hits = [d for d in result.diagnostics if d.code == "S004"]
    assert len(hits) == 1 and hits[0].severity == "warning"
    assert "VIN" in hits[0].message and "GND" in hits[0].message
    assert result.ok  # warning never blocks emission


def test_soic8_valid_design_still_clean():
    src = (
        "component Op8:\n"
        "    package = generate(SOIC, pins=8, pitch=1.27mm)\n"
        + "".join(f"    pin P{i}: role=passive, pad={i}\n" for i in range(1, 9))
    )
    result = compile_source(src, "op8.ohm")
    assert result.ok, [d.machine() for d in result.diagnostics]
    assert s_codes(result) == []
