"""ADR-011: `ohm signoff` — routed-board verification against the source.

The clean-route tests synthesize tracks at the parser's own computed pad
positions; the pcbnew test (skipped if the module is absent) validates that
geometry against KiCad's own connectivity engine."""


import re
import shutil
import subprocess

import pytest

from ohm.driver import compile_source
from ohm.emit_kicad import emit_kicad_pcb
from ohm.signoff import read_board, signoff

DIVIDER = """
use std.interfaces
use std.passives

module Divider:
    pwr = io(Power)
    r1 = Resistor(value=10kohm)
    r2 = Resistor(value=10kohm)
    pwr.v ~ r1.P1
    r1.P2 ~ r2.P1
    r2.P2 ~ pwr.gnd
"""


def build(src=DIVIDER, name="d.ohm"):
    r = compile_source(src, name)
    assert r.ok, [d.machine() for d in r.diagnostics]
    return r.low, emit_kicad_pcb(r.low, name)


def net_id(pcb: str, name: str) -> str:
    m = re.search(r'\(net (\d+) "%s"\)' % re.escape(name), pcb)
    assert m, f"net {name} not in board"
    return m.group(1)


def route(pcb: str, net_name: str, pads: list[tuple[str, str]]) -> str:
    """Append F.Cu segments star-connecting the named pads of one net."""
    board = read_board(pcb)
    pos = {(p.fp_path, p.pad_id): (p.x, p.y) for p in board.pads}
    pts = [pos[k] for k in pads]
    nid = net_id(pcb, net_name)
    segs = "".join(
        f'  (segment (start {pts[0][0]} {pts[0][1]}) (end {x} {y})'
        f' (width 0.25) (layer "F.Cu") (net {nid}))\n'
        for x, y in pts[1:])
    return pcb.rstrip()[:-1] + segs + ")\n"  # splice before the final paren


def by_code(diags):
    out = {}
    for d in diags:
        out.setdefault(d.code, []).append(d)
    return out


# ---------- V002: unrouted / routed ----------
def test_unrouted_board_fires_v002():
    low, pcb = build()
    codes = by_code(signoff(low, pcb, "d.kicad_pcb"))
    assert "V002" in codes  # r1.P2~r2.P1 net has 2 pads, no copper
    assert not any(c in codes for c in ("V001", "V003", "V004", "V006"))


def test_fully_routed_board_is_clean():
    low, pcb = build()
    pcb = route(pcb, "r1.P2", [("r1", "2"), ("r2", "1")])
    diags = signoff(low, pcb, "d.kicad_pcb")
    assert not [d for d in diags if d.severity in ("error", "warning")], \
        [d.machine() for d in diags]


def test_multihop_route_chains_endpoints():
    # two segments meeting at an intermediate point still complete the net
    low, pcb = build()
    board = read_board(pcb)
    pos = {(p.fp_path, p.pad_id): (p.x, p.y) for p in board.pads}
    (ax, ay), (bx, by) = pos[("r1", "2")], pos[("r2", "1")]
    nid = net_id(pcb, "r1.P2")
    mid = f"{(ax + bx) / 2} 77.7"
    segs = (f'  (segment (start {ax} {ay}) (end {mid}) (width 0.25) (layer "F.Cu") (net {nid}))\n'
            f'  (segment (start {mid}) (end {bx} {by}) (width 0.25) (layer "F.Cu") (net {nid}))\n')
    routed = pcb.rstrip()[:-1] + segs + ")\n"
    diags = signoff(low, routed, "d.kicad_pcb")
    assert not [d for d in diags if d.code == "V002"]


# ---------- V001: board/source drift ----------
def test_missing_part_fires_v001():
    low, pcb = build()
    start = pcb.index('(property "ohm_path" "r2")')
    fp_start = pcb.rindex("(footprint", 0, start)
    depth, i = 0, fp_start
    while True:  # excise the whole footprint block
        if pcb[i] == "(":
            depth += 1
        elif pcb[i] == ")":
            depth -= 1
            if depth == 0:
                break
        i += 1
    codes = by_code(signoff(low, pcb[:fp_start] + pcb[i + 1:], "d.kicad_pcb"))
    assert any("r2" in d.message for d in codes.get("V001", []))


def test_pad_on_wrong_net_fires_v001():
    low, pcb = build()
    # rebind a PAD (occurrences after the first footprint), not the net table
    cut = pcb.index("(footprint")
    wrong = pcb[:cut] + pcb[cut:].replace(
        f'(net {net_id(pcb, "r1.P2")} "r1.P2")',
        f'(net {net_id(pcb, "pwr.gnd")} "pwr.gnd")', 1)
    assert wrong != pcb
    assert "V001" in by_code(signoff(low, wrong, "d.kicad_pcb"))


# ---------- V003: floating / foreign copper ----------
def test_net0_copper_fires_v003():
    low, pcb = build()
    stray = pcb.rstrip()[:-1] + (
        '  (segment (start 1 1) (end 2 2) (width 0.25) (layer "F.Cu") (net 0))\n)\n')
    assert "V003" in by_code(signoff(low, stray, "d.kicad_pcb"))


def test_floating_trace_fires_v003():
    low, pcb = build()
    pcb = route(pcb, "r1.P2", [("r1", "2"), ("r2", "1")])  # net complete...
    nid = net_id(pcb, "r1.P2")
    floating = pcb.rstrip()[:-1] + (
        f'  (segment (start 90 90) (end 95 95) (width 0.25) (layer "F.Cu") (net {nid}))\n)\n')
    codes = by_code(signoff(low, floating, "d.kicad_pcb"))
    assert "V003" in codes and "V002" not in codes  # floating, but net itself complete


# ---------- V004: topology shorts ----------
def test_cross_net_point_fires_v004():
    low, pcb = build()
    n1, n2 = net_id(pcb, "r1.P2"), net_id(pcb, "pwr.gnd")
    shorted = pcb.rstrip()[:-1] + (
        f'  (segment (start 50 50) (end 60 60) (width 0.25) (layer "F.Cu") (net {n1}))\n'
        f'  (segment (start 60 60) (end 70 70) (width 0.25) (layer "F.Cu") (net {n2}))\n)\n')
    assert "V004" in by_code(signoff(low, shorted, "d.kicad_pcb"))


def test_wrong_net_copper_into_pad_fires_v004():
    low, pcb = build()
    board = read_board(pcb)
    px, py = next((p.x, p.y) for p in board.pads if (p.fp_path, p.pad_id) == ("r2", "1"))
    nid = net_id(pcb, "pwr.gnd")
    bad = pcb.rstrip()[:-1] + (
        f'  (segment (start {px} {py}) (end 90 90) (width 0.25) (layer "F.Cu") (net {nid}))\n)\n')
    assert "V004" in by_code(signoff(low, bad, "d.kicad_pcb"))


# ---------- V006: fixed placement is a promise ----------
FIXED = DIVIDER + "    constraint fixed(r1, 30mm, 30mm, 0)\n"


def test_fixed_part_moved_fires_v006():
    low, pcb = build(FIXED)
    moved = pcb.replace("(at 30 30)", "(at 31 30)", 1)
    assert moved != pcb
    assert "V006" in by_code(signoff(low, moved, "d.kicad_pcb"))
    assert "V006" not in by_code(signoff(low, pcb, "d.kicad_pcb"))


# ---------- determinism ----------
def test_signoff_deterministic():
    low, pcb = build()
    a = [d.machine() for d in signoff(low, pcb, "d.kicad_pcb")]
    b = [d.machine() for d in signoff(low, pcb, "d.kicad_pcb")]
    assert a == b


# ---------- ground truth: KiCad's connectivity engine agrees with ours ----------
def _pcbnew_ok() -> bool:
    py = shutil.which("python3")
    return py is not None and subprocess.run(
        [py, "-c", "import pcbnew"], capture_output=True).returncode == 0


@pytest.mark.skipif(not _pcbnew_ok(), reason="system pcbnew module not installed")
def test_pcbnew_agrees_with_signoff(tmp_path):
    low, pcb = build()
    routed = route(pcb, "r1.P2", [("r1", "2"), ("r2", "1")])
    (tmp_path / "un.kicad_pcb").write_text(pcb)
    (tmp_path / "ro.kicad_pcb").write_text(routed)
    script = (
        "import pcbnew, sys\n"
        "for p in sys.argv[1:]:\n"
        "    b = pcbnew.LoadBoard(p)\n"
        "    b.BuildConnectivity()\n"
        "    print(b.GetConnectivity().GetUnconnectedCount(False))\n")
    r = subprocess.run(
        ["python3", "-c", script, str(tmp_path / "un.kicad_pcb"),
         str(tmp_path / "ro.kicad_pcb")], capture_output=True, text=True, check=True)
    unrouted_gaps, routed_gaps = (int(x) for x in r.stdout.split())
    assert unrouted_gaps == 1 and routed_gaps == 0  # matches V002-then-clean above
