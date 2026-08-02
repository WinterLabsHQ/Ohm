"""The schematic is CONNECTED, not merely drawn.

Ohm used to emit a `.kicad_sch` containing text labels and global labels with an
empty `(lib_symbols)` — no symbols, no wires, nothing a human could review and
nothing KiCad could extract a net from. This gate proves the replacement is real
by making KiCad itself the oracle:

    Ohm source -> emit_kicad_sch -> `kicad-cli sch export netlist` -> net graph

and comparing that against Ohm's OWN netlist. If the two agree pin-for-pin, the
schematic is electrically the design. Nothing weaker is worth asserting: a
schematic can look plausible and be disconnected, which is exactly the failure a
generated diagram invites.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from ohm.driver import compile_source
from ohm.emit_kicad import _D, _snap, emit_kicad_sch, emit_kicad_sheets
from ohm.emit_netlist import emit_netlist
from ohm.pkggen.pindata_iface import DeclaredPinData
from ohm.pkggen.symbol import symbol_layout

EXAMPLES = Path(__file__).parent.parent / "examples"
KICAD_CLI = shutil.which("kicad-cli")
requires_kicad_cli = pytest.mark.skipif(KICAD_CLI is None, reason="kicad-cli not installed")

CASES = ["divider_led", "io_expander_board", "kind_demo_power_amp",
         "blinky_555", "led_bar"]


def _partition(nets: dict[str, set[tuple[str, str]]]) -> set[frozenset[tuple[str, str]]]:
    """The circuit itself: which pins share a net, names discarded.

    A net drawn as a plain wire carries no label, so KiCad names it
    `Net-(C2-P1)` where Ohm called it `c_ctrl.P1`. That is a naming difference,
    not a wiring one, and it is what a human schematic does with a local node
    too. Comparing the partition keeps every connectivity error detectable — a
    wire to the wrong pin still moves a pin between sets — while allowing an
    anonymous node to be anonymous. Names are asserted separately, on the nets
    that have one (see _named).
    """
    return {frozenset(pins) for pins in nets.values()}


def _named(nets: dict[str, set[tuple[str, str]]]) -> dict[str, set[tuple[str, str]]]:
    """Only the nets KiCad has a real name for — auto-names dropped."""
    return {n: p for n, p in nets.items() if not n.startswith("Net-(")}


def _nets(text: str) -> dict[str, set[tuple[str, str]]]:
    """(net name -> {(ref, pin)}) from a KiCad S-expression netlist.

    KiCad invents `unconnected-(U1-NC-Pad4)` nets for pins on no net; Ohm simply
    omits them. Those are the same statement, so they are excluded rather than
    counted as a difference.
    """
    out: dict[str, set[tuple[str, str]]] = {}
    # KiCad 9 inserts `(class "Default")` between the net name and its nodes;
    # KiCad 7 did not. Without tolerating it the node group matches zero times,
    # every net comes back with an empty pin set, and the comparison fails on
    # an empty partition rather than on a real difference.
    for m in re.finditer(r'\(net \(code "\d+"\) \(name "([^"]*)"\)'
                         r'(?:\s*\(class "[^"]*"\))?'
                         r'((?:\s*\(node [^\n]*)*)', text):
        # KiCad path-prefixes a sheet-local label ("/pwr/pwr.c1.P1"); Ohm names
        # are already globally unique, so the last segment is the name. Two
        # sheets colliding on one short name would merge here and fail loudly.
        name = m.group(1).rsplit("/", 1)[-1]
        if name.startswith("unconnected-"):
            continue
        out[name] = set(re.findall(r'\(node \(ref "([^"]+)"\) \(pin "([^"]+)"', m.group(2)))
    return out


def _assert_same_circuit(ours: dict, theirs: dict) -> None:
    """KiCad read the same circuit Ohm emitted — wiring first, then names."""
    assert _partition(theirs) == _partition(ours), (
        "KiCad read a different circuit from the schematic than Ohm emitted.\n"
        f"only in ohm:   {sorted(map(sorted, _partition(ours) - _partition(theirs)))}\n"
        f"only in kicad: {sorted(map(sorted, _partition(theirs) - _partition(ours)))}")
    named = _named(theirs)
    assert all(ours.get(n) == p for n, p in named.items()), (
        "a NAMED net differs — a label lands on the wrong pin.\n"
        f"{sorted(n for n, p in named.items() if ours.get(n) != p)}")


@requires_kicad_cli
@pytest.mark.parametrize("name", CASES)
def test_kicad_extracts_the_same_net_graph(name, tmp_path):
    path = EXAMPLES / f"{name}.ohm"
    result = compile_source(path.read_text(), str(path))
    assert result.low is not None, [d.machine() for d in result.diagnostics]

    sch = tmp_path / f"{name}.kicad_sch"
    sch.write_text(emit_kicad_sch(result.low, str(path), result.design),
                   encoding="utf-8", newline="\n")
    extracted = tmp_path / "kicad.net"
    proc = subprocess.run(
        [KICAD_CLI, "sch", "export", "netlist", str(sch), "-o", str(extracted)],
        capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stderr

    ours = _nets(emit_netlist(result.low, str(path)))
    theirs = _nets(extracted.read_text())
    assert ours, "the design under test has no nets"
    _assert_same_circuit(ours, theirs)


HIER_SRC = """use std.interfaces
use std.passives

interface Ana:
    s: Signal, role=bidirectional

component Buf:
    package = generate(SOIC, pins=8)
    pin IN:  role=bidirectional, pad=1
    pin OUT: role=bidirectional, pad=2
    pin VCC: role=power_in, pad=8
    pin GND: role=ground, pad=4
    pin NC1: role=nc, pad=3
    pin NC2: role=nc, pad=5
    pin NC3: role=nc, pad=6
    pin NC4: role=nc, pad=7

module Sense:
    pwr = io(Power)
    sig = io(Ana)
    u = Buf()
    r_top = Resistor(value=10kohm)
    c_dec = Capacitor(value=100nF)
    pwr.v ~ u.VCC
    pwr.gnd ~ u.GND
    pwr.v ~ r_top.P1
    r_top.P2 ~ u.IN
    u.OUT ~ sig.s
    pwr.v ~ c_dec.P1
    pwr.gnd ~ c_dec.P2

module Drive:
    pwr = io(Power)
    sig = io(Ana)
    u = Buf()
    c_flt = Capacitor(value=1nF)
    pwr.v ~ u.VCC
    pwr.gnd ~ u.GND
    sig.s ~ u.IN
    u.OUT ~ c_flt.P1
    pwr.gnd ~ c_flt.P2

module Board:
    pwr = io(Power)
    sense = Sense()
    drive = Drive()
    pwr ~ sense.pwr
    pwr ~ drive.pwr
    sense.sig ~ drive.sig
"""


@requires_kicad_cli
def test_kicad_extracts_the_same_net_graph_across_hierarchical_sheets(tmp_path):
    """The oracle, on the hierarchy: N sheet files must read as ONE circuit.

    A hierarchy is where a generated schematic silently lies — a cross-sheet
    signal with no matching sheet pin, or a rail drawn as a power symbol on one
    sheet and a plain (sheet-local) label on another, both leave the drawing
    looking right and the netlist split in two. Only KiCad reading back the
    whole hierarchy proves otherwise.
    """
    result = compile_source(HIER_SRC, "hier.ohm")
    assert result.low is not None, [d.machine() for d in result.diagnostics]

    # force=True: this design is small enough to fit one A3, and `auto` would
    # correctly keep it on one sheet — the hierarchy is what is under test here
    sheets = emit_kicad_sheets(result.low, "hier.ohm", result.design, force=True)
    assert len(sheets) >= 3, sorted(sheets)  # root + one per module
    for fname, text in sheets.items():
        (tmp_path / fname).write_text(text, encoding="utf-8", newline="\n")

    extracted = tmp_path / "kicad.net"
    proc = subprocess.run(
        [KICAD_CLI, "sch", "export", "netlist", str(tmp_path / "hier.kicad_sch"),
         "-o", str(extracted)], capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stderr

    ours = _nets(emit_netlist(result.low, "hier.ohm"))
    theirs = _nets(extracted.read_text())
    assert ours
    _assert_same_circuit(ours, theirs)


def test_hierarchy_pins_match_hierarchical_labels():
    """Every signal leaving a sheet has a pin on that sheet's symbol, and vice
    versa. KiCad's netlist survives a mismatch here; ERC and a reader do not."""
    result = compile_source(HIER_SRC, "hier.ohm")
    sheets = emit_kicad_sheets(result.low, "hier.ohm", result.design, force=True)
    root = sheets["hier.kicad_sch"]
    for fname, text in sheets.items():
        if fname == "hier.kicad_sch" or fname.endswith("_overview.kicad_sch"):
            continue  # the overview is a second RENDERING, not a sheet of the
            # hierarchy: it holds every block flat and so has no sheet pins
        labels = set(re.findall(r'\(hierarchical_label "([^"]+)"', text))
        assert labels, f"{fname} has no cross-sheet signal"
        for name in labels:
            assert f'(pin "{name}" bidirectional' in root, (name, fname)
    # and the root's pins are all accounted for by some sheet
    all_labels = {n for f, t in sheets.items()
                  if f != "hier.kicad_sch" and not f.endswith("_overview.kicad_sch")
                  for n in re.findall(r'\(hierarchical_label "([^"]+)"', t)}
    assert set(re.findall(r'\(pin "([^"]+)" bidirectional', root)) == all_labels


@requires_kicad_cli
def test_the_overview_is_a_second_rendering_of_the_same_circuit(tmp_path):
    """Hierarchical sheets are what you EDIT; the overview is what you REVIEW.

    Same netlist, two renderings — cheap only because Ohm owns the source
    format. It must be a standalone sheet: not referenced by the root, so KiCad
    never loads it twice into one hierarchy and double-counts every part.
    """
    result = compile_source(HIER_SRC, "hier.ohm")
    sheets = emit_kicad_sheets(result.low, "hier.ohm", result.design, force=True)
    overview = sheets["hier_overview.kicad_sch"]
    assert "OVERVIEW" in overview
    assert "hier_overview" not in sheets["hier.kicad_sch"]  # never in the hierarchy

    # and it is the whole circuit on its own
    path = tmp_path / "ov.kicad_sch"
    path.write_text(overview, encoding="utf-8", newline="\n")
    extracted = tmp_path / "ov.net"
    proc = subprocess.run(
        [KICAD_CLI, "sch", "export", "netlist", str(path), "-o", str(extracted)],
        capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stderr
    _assert_same_circuit(_nets(emit_netlist(result.low, "hier.ohm")),
                         _nets(extracted.read_text()))


def test_flat_design_stays_a_single_sheet():
    """One module is not a hierarchy; a root sheet pointing at one box is pure
    indirection. Nor is a multi-module design that already fits a reviewable
    page — sheet count follows FIT, not module count."""
    path = EXAMPLES / "divider_led.ohm"
    result = compile_source(path.read_text(), str(path))
    sheets = emit_kicad_sheets(result.low, str(path), result.design)
    assert list(sheets) == ["divider_led.kicad_sch"]
    assert "(sheet (at" not in sheets["divider_led.kicad_sch"]


def test_schematic_has_symbols_and_wires():
    """Guard the regression directly: the old emitter produced neither.

    This used to assert "at least one stub per connected pin", which encoded the
    all-labels model — the moment pins started being joined by real wires the
    count fell and the test failed for the right change. What matters is that
    the drawing carries bodies, copper and names at all.
    """
    path = EXAMPLES / "divider_led.ohm"
    result = compile_source(path.read_text(), str(path))
    sch = emit_kicad_sch(result.low, str(path), result.design)
    assert "(lib_symbols\n" in sch and "(symbol \"ohm:" in sch
    assert sch.count("(wire ") >= 4
    assert sch.count("(label ") >= 4
    assert "(rectangle" in sch or "(polyline" in sch  # bodies, not just text


@pytest.mark.parametrize("name", CASES)
def test_every_uuid_is_unique(name):
    """Two elements sharing a uuid is a corrupt file, not a cosmetic issue.

    It happened as soon as wires stopped being one-per-pin: the uuid was derived
    from the net name alone, so a net drawn as several segments emitted the same
    id for each. KiCad's own behaviour on duplicates is undefined.
    """
    path = EXAMPLES / f"{name}.ohm"
    result = compile_source(path.read_text(), str(path))
    sch = emit_kicad_sch(result.low, str(path), result.design)
    ids = re.findall(r'\(uuid "([0-9a-f-]{36})"\)', sch)
    dupes = {u for u in ids if ids.count(u) > 1}
    assert not dupes, sorted(dupes)


@pytest.mark.parametrize("name", CASES)
def test_every_part_reaches_the_sheet(name):
    """No part may go missing from the drawing.

    Placement has several paths — flow layout, riders against a host pin — and a
    part that falls between them vanishes silently: the schematic still looks
    plausible, and only its absence from a net gives it away. That happened: a
    rider whose host was itself a rider could be reached before its host had a
    position and was dropped. Assert the whole BOM is on the page.
    """
    path = EXAMPLES / f"{name}.ohm"
    result = compile_source(path.read_text(), str(path))
    sch = emit_kicad_sch(result.low, str(path), result.design)
    drawn = set(re.findall(r'\(property "Ohm" "([^"]+)"', sch))
    assert drawn == {p.ref for p in result.low.parts}


def test_schematic_is_deterministic():
    path = EXAMPLES / "led_bar.ohm"
    a = compile_source(path.read_text(), str(path))
    b = compile_source(path.read_text(), str(path))
    assert emit_kicad_sch(a.low, "x", a.design) == emit_kicad_sch(b.low, "x", b.design)


def test_emit_without_design_still_works():
    """Back-compat: older callers pass only `low` and must still get a schematic."""
    path = EXAMPLES / "divider_led.ohm"
    result = compile_source(path.read_text(), str(path))
    sch = emit_kicad_sch(result.low, str(path))
    assert "(kicad_sch" in sch and "(label " in sch


def test_pin_named_like_another_pads_id_does_not_shadow_it():
    """Regression: a pin *named* C1 must not claim the pad *called* C1.

    Real case (LSM303D): pin `C1` sits on pad `E1` while pad `C1` carries `INT2`.
    Keying the net lookup by pin name as well as pad hung `C1`'s label on the
    unconnected INT2 pin, silently shorting it onto that net in the schematic
    while Ohm's own netlist stayed correct.
    """
    src = """component Part:
    package = generate(LGA, rows=5, cols=5, pitch=0.5mm, pad=0.3mm, border=1)
    pin C1:   role=passive, pad=E1
    pin INT2: role=passive, pad=C1
    pin VDD:  role=power_in, pad=A1
    pin GND:  role=ground, pad=E5
    pin A2:   role=passive, pad=A2
    pin A3:   role=passive, pad=A3
    pin A4:   role=passive, pad=A4
    pin A5:   role=passive, pad=A5
    pin B1:   role=passive, pad=B1
    pin B5:   role=passive, pad=B5
    pin C5:   role=passive, pad=C5
    pin D1:   role=passive, pad=D1
    pin D5:   role=passive, pad=D5
    pin E2:   role=passive, pad=E2
    pin E3:   role=passive, pad=E3
    pin E4:   role=passive, pad=E4

component Cap:
    package = generate(CHIP0402)
    pin P1: role=passive, pad=1
    pin P2: role=passive, pad=2

module Top:
    u = Part()
    c = Cap()
    u.C1 ~ c.P1
"""
    result = compile_source(src, "t.ohm")
    assert result.low is not None, [d.machine() for d in result.diagnostics]
    sch = emit_kicad_sch(result.low, "t.ohm", result.design)

    # The net is now DRAWN — the cap rides pad E1 and is wired to it — so assert
    # where the copper goes rather than where a label went: the wire must land on
    # pad E1, and nothing at all may touch pad C1 (INT2, unconnected).
    at = re.search(r'\(symbol \(lib_id "ohm:Part"\) \(at (\S+) (\S+) 0\)', sch)
    assert at, sch[:400]
    px, py = _D(at.group(1)), _D(at.group(2))
    comp = next(c for c in result.design.components if c.name == "Part")
    _h, geom = symbol_layout(DeclaredPinData().pin_table(comp))
    pad = {num: (_snap(px + lx), _snap(py - ly)) for num, _n, _r, lx, ly, _s in geom}
    assert f"(xy {pad['E1'][0]} {pad['E1'][1]})" in sch, "nothing wired to pad E1"
    assert f"(xy {pad['C1'][0]} {pad['C1'][1]})" not in sch, "pad C1 was wired"
