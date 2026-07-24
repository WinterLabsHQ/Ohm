"""Milestone F Part D gate: placement intent + routing-friendly KiCad handoff.

fixed pose + locked; deterministic non-overlapping auto-placement; groups;
ohm_path properties; pads with net bindings; diffpair rename in kicad files
only; .kicad_pro net classes; kicad-cli parse; byte-identity; near adjacency.
"""

import json
import re
import shutil
import subprocess
from decimal import Decimal

import pytest

from ohm.driver import compile_source
from ohm.emit_constraints import emit_constraints
from ohm.emit_kicad import _CELL_GAP, _ceil_grid, emit_kicad_pcb, emit_kicad_sch
from ohm.emit_kicad_pro import emit_kicad_pro
from ohm.emit_netlist import emit_netlist
from ohm.pkggen.packages import build

SRC = (
    "interface Power:\n"
    "    v: Signal\n"
    "    gnd: Signal\n"
    "component R:\n"
    "    package = generate(CHIP0603)\n"
    '    designator_prefix = "R"\n'
    "    pin P1: role=passive, pad=1\n"
    "    pin P2: role=passive, pad=2\n"
    "component Conn:\n"
    '    package = "Connector:USB_C"\n'
    "    pin DP: role=bidirectional, pad=3\n"
    "    pin DM: role=bidirectional, pad=2\n"
    "module Sub:\n"
    "    pwr = io(Power)\n"
    "    r1 = R()\n"
    "    r2 = R()\n"
    "    pwr.v ~ r1.P1\n"
    "    r1.P2 ~ r2.P1\n"
    "    pwr.gnd ~ r2.P2\n"
    "module Board:\n"
    "    pwr = io(Power)\n"
    "    j = Conn()\n"
    "    ra = R()\n"
    "    rb = R()\n"
    "    sub = Sub()\n"
    "    pwr.v ~ sub.pwr.v\n"
    "    pwr.gnd ~ sub.pwr.gnd\n"
    "    j.DP ~ ra.P1\n"
    "    j.DM ~ rb.P1\n"
    "    ra.P2 ~ pwr.gnd\n"
    "    rb.P2 ~ pwr.gnd\n"
    "    constraint fixed(j, 50mm, 40mm, 90)\n"
    "    constraint near(rb, ra)\n"
    "    constraint diffpair(j.DP, j.DM)\n"
    '    constraint netclass(pwr.v, "power")\n'
    "    constraint keepout(pwr.v, 10mm, 10mm, 20mm, 5mm)\n"
)


@pytest.fixture(scope="module")
def low():
    result = compile_source(SRC, "board.ohm")
    assert result.ok, [d.machine() for d in result.diagnostics]
    return result.low


@pytest.fixture(scope="module")
def pcb(low):
    return emit_kicad_pcb(low, "board.ohm")


def footprints(pcb_text):
    """ohm_path -> (footprint header line, full block text)."""
    out = {}
    for m in re.finditer(r'  \(footprint [^\n]*\n(?:    [^\n]*\n)*  \)', pcb_text):
        block = m.group(0)
        path = re.search(r'\(property "ohm_path" "([^"]+)"\)', block).group(1)
        out[path] = block
    return out


def pose(block):
    m = re.search(r'\(at (-?[\d.]+) (-?[\d.]+)(?: (-?[\d.]+))?\)', block)
    return Decimal(m.group(1)), Decimal(m.group(2)), Decimal(m.group(3) or 0)


# ---------- fixed: exact pose + locked ----------
def test_fixed_part_exact_pose_and_locked(pcb):
    fps = footprints(pcb)
    block = fps["j"]
    assert " locked " in block.splitlines()[0]
    assert pose(block) == (Decimal(50), Decimal(40), Decimal(90))
    others = [b for p, b in fps.items() if p != "j"]
    assert all(" locked " not in b.splitlines()[0] for b in others)


# ---------- auto placement: distinct, non-overlapping ----------
def test_unfixed_parts_distinct_and_non_overlapping(pcb):
    fps = footprints(pcb)
    poses = {p: pose(b) for p, b in fps.items() if p != "j"}
    assert len(set(poses.values())) == len(poses)  # all distinct
    # spot-check: two CHIP0603 courtyards (2.94 x 1.40) don't intersect
    cw, ch = build("CHIP0603", {}).courtyard_w, build("CHIP0603", {}).courtyard_h
    (xa, ya, _), (xb, yb, _) = poses["sub.r1"], poses["sub.r2"]
    assert abs(xa - xb) >= cw or abs(ya - yb) >= ch


# ---------- near: adjacent in the same row ----------
def test_near_places_part_adjacent(pcb):
    fps = footprints(pcb)
    (xa, ya, _), (xb, yb, _) = pose(fps["ra"]), pose(fps["rb"])
    assert ya == yb  # same row
    pitch = _ceil_grid(build("CHIP0603", {}).courtyard_w + _CELL_GAP)
    assert xb - xa == pitch  # immediately adjacent cell


# ---------- groups ----------
def test_groups_present_with_correct_members(pcb):
    fps = footprints(pcb)
    (group,) = re.findall(r'  \(group "([^"]+)" \(id "[^"]+"\) \(members ([^)]*)\)\)', pcb)
    assert group[0] == "sub"
    member_ids = set(re.findall(r'"([0-9a-f-]+)"', group[1]))
    expected = {re.search(r'\(tstamp "([0-9a-f-]+)"\)', fps[p]).group(1)
                for p in ("sub.r1", "sub.r2")}
    assert member_ids == expected


# ---------- ohm_path + pads with net bindings ----------
def test_ohm_path_and_pad_net_bindings(pcb, low):
    fps = footprints(pcb)
    assert set(fps) == {p.ref for p in low.parts}
    ra = fps["ra"]
    assert '(pad "1" smd roundrect' in ra and '(pad "2" smd roundrect' in ra
    assert re.search(r'\(pad "2" [^\n]*\(net \d+ "pwr.gnd"\)', ra)
    assert "(pad" not in fps["j"]  # string-footprint stub: no pads
    assert fps["j"].splitlines()[0].startswith('  (footprint "Connector:USB_C"')


# ---------- diffpair rename: kicad files only ----------
def test_diffpair_renamed_only_in_kicad_files(pcb, low):
    sch = emit_kicad_sch(low, "board.ohm")
    for text in (pcb, sch):
        assert '"j.DP_P"' in text and '"j.DP_N"' in text
        assert '"j.DP"' not in text and '"j.DM"' not in text
    net = emit_netlist(low, "board.ohm")
    assert '"j.DP"' in net and '"j.DM"' in net and "_P" not in net
    doc = json.loads(emit_constraints(low, "board.ohm"))
    dp = next(c for c in doc["constraints"] if c["kind"] == "diffpair")
    assert dp["nets"] == ["j.DP", "j.DM"]  # canonical names in the seam


# ---------- constraints JSON: placement + groups ----------
def test_constraints_json_placement_and_groups(low):
    doc = json.loads(emit_constraints(low, "board.ohm"))
    assert doc["placement"] == [
        {"ref": "j", "x_mm": "50", "y_mm": "40", "rot": "90", "side": "top",
         "fixed": True}]
    assert {g["name"]: g["members"] for g in doc["groups"]} == {
        "root": ["j", "ra", "rb"], "sub": ["sub.r1", "sub.r2"]}
    near = next(c for c in doc["constraints"] if c["kind"] == "near")
    assert near["nets"] == ["rb", "ra"]  # part refs ride the constraints list


# ---------- .kicad_pro: netclass + diffpair classes ----------
def test_kicad_pro_classes(low):
    doc = json.loads(emit_kicad_pro(low, "board.ohm"))
    classes = {c["name"]: c for c in doc["net_settings"]["classes"]}
    assert set(classes) == {"Default", "DiffPair", "power"}
    assert classes["DiffPair"]["track_width"] == 0.4
    assert classes["DiffPair"]["diff_pair_gap"] == 0.4
    patterns = {(p["netclass"], p["pattern"])
                for p in doc["net_settings"]["netclass_patterns"]}
    assert ("power", "pwr.v") in patterns
    assert ("DiffPair", "j.DP_P") in patterns and ("DiffPair", "j.DP_N") in patterns


# ---------- keepout zone with geometry ----------
def test_keepout_zone_emitted(pcb):
    assert "(keepout (tracks not_allowed) (vias not_allowed)" in pcb
    assert "(xy 10 10) (xy 30 10) (xy 30 15) (xy 10 15)" in pcb


# ---------- kicad-cli parses the board ----------
@pytest.mark.skipif(shutil.which("kicad-cli") is None, reason="kicad-cli not installed")
def test_kicad_cli_parses_board(pcb, tmp_path):
    board = tmp_path / "board.kicad_pcb"
    board.write_text(pcb)
    r = subprocess.run(
        ["kicad-cli", "pcb", "export", "pos", "-o", str(tmp_path / "b.pos"), str(board)],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


# ---------- determinism ----------
def test_double_build_byte_identical():
    a = compile_source(SRC, "board.ohm").low
    b = compile_source(SRC, "board.ohm").low
    assert emit_kicad_pcb(a, "x").encode() == emit_kicad_pcb(b, "x").encode()
    assert emit_kicad_pro(a, "x").encode() == emit_kicad_pro(b, "x").encode()


# ---------- lowering: placement refs are part refs; bad refs warn I007 ----------
def test_placement_constraint_lowers_to_part_refs(low):
    fixed = next(c for c in low.constraints if c.kind == "fixed")
    assert fixed.nets == ("j",) and fixed.params == ("50mm", "40mm", "90")


def test_placement_ref_must_be_component_instance():
    bad = SRC.replace("constraint near(rb, ra)", "constraint near(rb, sub)")
    result = compile_source(bad, "board.ohm")
    assert any(d.code == "I007" and "component instance" in d.message
               for d in result.diagnostics)


# ---------- CLI writes the .kicad_pro ----------
def test_cli_emits_kicad_pro(tmp_path):
    from ohm.cli import main

    src = tmp_path / "board.ohm"
    src.write_text(SRC)
    out = tmp_path / "out"
    assert main(["build", str(src), "-o", str(out), "--emit", "kicad"]) == 0
    pro = json.loads((out / "board.kicad_pro").read_text())
    assert pro["meta"]["filename"] == "board.kicad_pro"
    assert (out / "board.kicad_pcb").exists()
