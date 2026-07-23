"""Milestone L, wave L2: live-agent usability. A fresh LLM given only STYLE.md +
the stdlib built tests/examples/sensor_node_bme280.ohm — a 3.3V I2C sensor node
using std.interfaces/passives/power + its own BME280 component from the datasheet
(generated TSSOP-8). Zero-shot invented the component-declaration syntax (a guide
gap); one round of machine diagnostics produced this clean design.

Frozen here: the design compiles (the lone I006 is the honest strapped-input
warning — CSB pulled high through 10k has no active driver), all outputs emit."""

from pathlib import Path

from ohm.driver import compile_source

FIXTURE = Path(__file__).parent / "examples" / "sensor_node_bme280.ohm"


def _result():
    return compile_source(FIXTURE.read_text(), str(FIXTURE))


def test_agent_sensor_node_builds():
    result = _result()
    assert result.ok, [d.machine() for d in result.diagnostics]
    assert result.low is not None


def test_only_diagnostic_is_the_honest_strap_warning():
    result = _result()
    # no errors; the single warning is I006 on the CSB pull-up strap
    assert not [d for d in result.diagnostics if d.severity == "error"]
    warns = [d for d in result.diagnostics if d.severity == "warning"]
    assert all(d.code == "I006" for d in warns)


def test_uses_stdlib_blocks_and_own_component():
    result = _result()
    names = {c.name for c in result.design.components}
    assert "BME280" in names  # authored from the datasheet
    assert "LDO_SOT23_5" in names and "Resistor" in names  # from the stdlib
    assert result.design.top == "sensor_node"


def test_full_emit(tmp_path):
    from ohm.cli import main

    out = tmp_path / "out"
    assert main(["build", str(FIXTURE), "-o", str(out), "--emit", "all"]) == 0
    names = {p.name for p in out.iterdir()}
    # spec-named shared generated footprints + per-component symbols + netlist
    assert {"TSSOP-8.kicad_mod", "SOT23-5.kicad_mod", "CHIP0603.kicad_mod"} <= names
    assert "BME280.kicad_sym" in names
    assert (out / "sensor_node_bme280.net").exists()
    assert (out / "sensor_node_bme280.constraints.json").exists()
