"""P4 gate: emitted netlist matches golden for >=5 designs; KiCad imports the
output without error. Plus the structural invariants: determinism (double-build
byte-identity) and hermeticity (forbidden-import grep over the build path)."""

import shutil
import subprocess
from pathlib import Path

import pytest

from ohm import ir
from ohm.driver import compile_source
from ohm.emit_kicad import emit_kicad_pcb, emit_kicad_sch
from ohm.emit_netlist import emit_netlist

GOLDEN = Path(__file__).parent / "golden"
DESIGNS = sorted(GOLDEN.glob("*.ohm"))
OHM_PKG = Path(__file__).parent.parent / "ohm"


def compile_ok(path: Path):
    result = compile_source(path.read_text(), str(path))
    assert result.ok, [d.machine() for d in result.diagnostics]
    return result


# ---------- gate: golden corpus ----------
@pytest.mark.parametrize("design", DESIGNS, ids=lambda p: p.stem)
def test_netlist_matches_golden(design):
    result = compile_ok(design)
    emitted = emit_netlist(result.low, f"tests/golden/{design.name}")
    assert emitted == (GOLDEN / f"{design.stem}.net").read_text()


def test_at_least_five_golden_designs():
    assert len(DESIGNS) >= 5


# ---------- gate: KiCad imports without error ----------
@pytest.mark.skipif(shutil.which("kicad-cli") is None, reason="kicad-cli not installed")
@pytest.mark.parametrize("design", DESIGNS, ids=lambda p: p.stem)
def test_kicad_opens_emitted_files(design, tmp_path):
    result = compile_ok(design)
    sch = tmp_path / "out.kicad_sch"
    pcb = tmp_path / "out.kicad_pcb"
    sch.write_text(emit_kicad_sch(result.low, design.name))
    pcb.write_text(emit_kicad_pcb(result.low, design.name))
    r1 = subprocess.run(
        ["kicad-cli", "sch", "export", "netlist", "-o", str(tmp_path / "k.net"), str(sch)],
        capture_output=True, text=True)
    assert r1.returncode == 0, r1.stderr
    r2 = subprocess.run(
        ["kicad-cli", "pcb", "export", "pos", "-o", str(tmp_path / "k.pos"), str(pcb)],
        capture_output=True, text=True)
    assert r2.returncode == 0, r2.stderr


# ---------- determinism (design invariant 6) ----------
def test_double_build_byte_identical():
    for design in DESIGNS:
        a = compile_ok(design)
        b = compile_ok(design)
        assert ir.to_json(a.low).encode() == ir.to_json(b.low).encode()
        assert emit_netlist(a.low, "x").encode() == emit_netlist(b.low, "x").encode()
        assert emit_kicad_sch(a.low, "x").encode() == emit_kicad_sch(b.low, "x").encode()
        assert emit_kicad_pcb(a.low, "x").encode() == emit_kicad_pcb(b.low, "x").encode()


# ---------- hermeticity (non-negotiable N1) ----------
FORBIDDEN = ["socket", "requests", "urllib", "http.client", "subprocess",
             "time", "random", "datetime"]


def test_no_forbidden_imports_in_build_path():
    for py in OHM_PKG.rglob("*.py"):
        text = py.read_text()
        for mod in FORBIDDEN:
            assert f"import {mod}" not in text and f"from {mod}" not in text, \
                f"{py.name} imports forbidden module '{mod}' (breaks hermeticity N1)"


# ---------- no output from an errored design (design invariant 4) ----------
def test_errored_design_emits_nothing(tmp_path):
    from ohm.cli import main

    bad = tmp_path / "bad.ohm"
    bad.write_text("module M:\n    u = Ghost()\n")
    out = tmp_path / "out"
    rc = main(["build", str(bad), "-o", str(out)])
    assert rc == 1
    assert not out.exists() or list(out.iterdir()) == []


# ---------- lowering shape checks ----------
def test_hierarchy_flattening_and_designators():
    result = compile_ok(GOLDEN / "i2c_bus.ohm")
    assert [(p.ref, p.designator) for p in result.low.parts] == [
        ("mcu.u", "U1"), ("sens.s", "U2")]
    scl = next(n for n in result.low.nets if n.name == "mcu.bus.scl")
    assert [(n.designator, n.pad) for n in scl.nodes] == [("U1", "5"), ("U2", "1")]
    assert "sens.bus.scl" in scl.ports


def test_shared_net_across_ports():
    result = compile_ok(GOLDEN / "divider.ohm")
    gnd = next(n for n in result.low.nets if n.name == "vin.gnd")
    assert set(gnd.ports) == {"vin.gnd", "vout.gnd"}
    assert [(n.designator, n.pin) for n in gnd.nodes] == [("R2", "P2")]
