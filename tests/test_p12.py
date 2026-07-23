"""P12 gate: an LLM given only docs/STYLE.md + a datasheet excerpt authored
tests/fixtures/agent_mcp9808.ohm (MCP9808 on a generated TSSOP-8 — a package
variant missing from KiCad's library). Executed live on 2026-07-23 with a fresh
agent: zero-shot output had one error (R005, undeclared Power interface — a
guide gap); one round of machine diagnostics produced this clean file.

This test freezes that outcome: the fixture compiles clean, the deliberately
swapped variant is caught by `ohm check`, and the build emits the generated
package. It also pins the detectability improvement P12 uncovered: a require
pair crossed onto one instance's like-named pins is I005."""

from pathlib import Path

from ohm.driver import compile_source

FIXTURE = (Path(__file__).parent / "fixtures" / "agent_mcp9808.ohm").read_text()


def test_agent_authored_design_is_clean():
    result = compile_source(FIXTURE, "agent_mcp9808.ohm")
    assert result.ok, [d.machine() for d in result.diagnostics]
    assert [d for d in result.diagnostics if d.severity == "error"] == []


def test_component_is_faithful_to_datasheet():
    result = compile_source(FIXTURE, "agent_mcp9808.ohm")
    (comp,) = [c for c in result.design.components if c.name == "MCP9808"]
    assert comp.package.package_type == "TSSOP"
    assert dict(comp.package.kwargs)["pins"] == 8
    assert [(p.name, p.pad) for p in comp.pins] == [
        ("SDA", "1"), ("SCL", "2"), ("Alert", "3"), ("GND", "4"),
        ("A2", "5"), ("A1", "6"), ("A0", "7"), ("VDD", "8")]
    vdd = comp.pins[-1]
    assert vdd.role == "power_in" and str(vdd.vmax.value) == "6.0"
    assert vdd.current is not None  # declared 200uA draw


def test_swapped_member_is_caught():
    swapped = FIXTURE.replace("bus.sda ~ sensor.SDA", "bus.sda ~ sensor.SCL") \
                     .replace("bus.scl ~ sensor.SCL", "bus.scl ~ sensor.SDA")
    result = compile_source(swapped, "swapped.ohm")
    i005 = [d for d in result.diagnostics if d.code == "I005"]
    assert len(i005) == 2 and not result.ok
    # and the diagnostics point at the two offending `~` lines
    assert {d.span.line for d in i005} == {28, 29}


def test_generated_package_emitted_for_library_missing_part(tmp_path):
    from ohm.cli import main

    src = tmp_path / "board.ohm"
    src.write_text(FIXTURE)
    assert main(["build", str(src), "-o", str(tmp_path / "out"), "--emit", "kicad"]) == 0
    mod = (tmp_path / "out" / "TSSOP-8.kicad_mod").read_text()  # spec-named footprint
    sym = (tmp_path / "out" / "MCP9808.kicad_sym").read_text()
    assert mod.count("(pad ") == 8
    assert '"SDA"' in sym and '"VDD"' in sym  # datasheet pin names in the symbol


def test_style_guide_is_self_contained():
    style = (Path(__file__).parent.parent / "docs" / "STYLE.md").read_text()
    for needed in ("interface", "component", "module", "generate(", "require pair",
                   "role", "vmax", "assert", "obligation", "constraint",
                   "ohm check", "ohm explain"):
        assert needed in style, f"STYLE.md is missing '{needed}'"
