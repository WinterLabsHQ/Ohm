"""Ecosystem X0 (ADR-012): .zen as first-class input — check lints + convert
skeleton that compiles and preserves the imported net structure."""

from pathlib import Path

from ohm.cli import main
from ohm.driver import compile_source
from ohm.emit_ohm import emit_ohm
from ohm.imp_zen import flat_lints, import_zen

FIXTURES = Path(__file__).parent / "fixtures"
ZEN = (FIXTURES / "led_indicator.zen").read_text()


def endpoints(low):
    return {frozenset((n.part_ref, n.pin) for n in net.nodes)
            for net in low.nets if len(net.nodes) > 1}


def test_convert_skeleton_compiles_and_preserves_structure():
    zlow, zdiags = import_zen(ZEN, "led_indicator.zen")
    assert not [d for d in zdiags if d.severity == "error"]
    skeleton = emit_ohm(zlow, "led_indicator.zen")
    r = compile_source(skeleton, "converted.ohm")
    assert r.ok, [d.machine() for d in r.diagnostics]
    assert endpoints(r.low) == endpoints(zlow)  # same multi-endpoint net partition


def test_convert_is_deterministic():
    zlow, _ = import_zen(ZEN, "x.zen")
    assert emit_ohm(zlow, "x.zen") == emit_ohm(zlow, "x.zen")


def test_flat_lints_fire():
    src = (
        'Component(name="U1", footprint="X", pins={"EN": Net("EN")})\n'
        'Component(name="U2", footprint="X", pins={})\n')
    low, _ = import_zen(src, "l.zen")
    codes = [d.code for d in flat_lints(low, "l.zen")]
    assert "Z007" in codes  # single-endpoint net
    assert "Z008" in codes  # unconnected part
    assert "Z009" in codes  # honesty notice


def test_cli_check_accepts_zen(tmp_path, capsys):
    f = tmp_path / "d.zen"
    f.write_text(ZEN)
    assert main(["check", str(f)]) == 0
    assert "Z009" in capsys.readouterr().err


def test_cli_convert_writes_file(tmp_path, capsys):
    f = tmp_path / "d.zen"
    f.write_text(ZEN)
    out = tmp_path / "d.ohm"
    assert main(["convert", str(f), "-o", str(out)]) == 0
    assert "module Imported:" in out.read_text()
    r = compile_source(out.read_text(), str(out))
    assert r.ok


def test_component_name_collision_gets_distinct_decls():
    src = (
        'Component(name="A1", footprint="X", pins={"IN": Net("N1"), "OUT": Net("N1")})\n'
        'Component(name="B1", footprint="Y", pins={"L": Net("N2"), "R": Net("N2")})\n')
    low, _ = import_zen(src, "c.zen")
    text = emit_ohm(low, "c.zen")
    assert "component Component:" in text and "component Component_2:" in text
    r = compile_source(text, "c.ohm")
    assert r.ok, [d.machine() for d in r.diagnostics]
