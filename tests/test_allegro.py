"""Cadence Allegro third-party netlist emitter (ADR-020).

What is claimed: the three files carry the same connectivity, part list and footprint
binding as Ohm's own netlist, in the syntax Allegro's logic import reads — keywords,
block structure, quoting and canonical-path escaping all taken from an
OrCAD-written triplet rather than from prose.

What is NOT claimed: that Allegro opens them. No Cadence tool exists on this machine
and none of the format is published by Cadence, so the assertions below are about
agreement with a real triplet's grammar and with Ohm's own netlist — never about
Allegro's behaviour.
"""

import re
import tempfile
from pathlib import Path

import pytest

from ohm.driver import compile_source
from ohm.emit_allegro import PINUSE, emit_allegro
from ohm.emit_netlist import emit_netlist
from ohm.resolve import VALID_ROLES

SRC = """board demo:
    title = "Demo"

component MCP6002:
    package = generate(SOIC, pins=8, pitch=1.27mm)
    mpn = "MCP6002T-I/SN"
    pin VOUTA: role=output, pad=1
    pin VINAN: role=input, pad=2
    pin VINAP: role=input, pad=3
    pin VSS: role=ground, pad=4
    pin VINBP: role=input, pad=5
    pin SDA: role=open_drain, pad=6
    pin VOUTB: role=output, pad=7
    pin VDD: role=power_in, pad=8

component R0603:
    package = generate(CHIP0603)
    value = 10kohm
    pin P1: role=passive, pad=1
    pin P2: role=passive, pad=2

module Top:
    u1 = MCP6002()
    r1 = R0603()
    net OUT = u1.VOUTA
    u1.VOUTA ~ r1.P1
    r1.P2 ~ u1.VINAN
"""


@pytest.fixture(scope="module")
def built():
    result = compile_source(SRC, "demo.ohm")
    assert result.ok, [d.machine() for d in result.diagnostics]
    return result


@pytest.fixture(scope="module")
def files(built):
    return emit_allegro(built.design, built.low, "demo")


def test_the_three_files_allegro_asks_for(files):
    assert set(files) == {"pstchip.dat", "pstxprt.dat", "pstxnet.dat"}
    assert files["pstchip.dat"].startswith("FILE_TYPE=LIBRARY_PARTS;")
    assert files["pstxprt.dat"].startswith("FILE_TYPE=EXPANDEDPARTLIST;")
    assert files["pstxnet.dat"].startswith("FILE_TYPE = EXPANDEDNETLIST;")
    assert all(text.rstrip().endswith("END.") for text in files.values())


def test_every_primitive_block_is_closed(files):
    chip = files["pstchip.dat"]
    assert chip.count("primitive ") == chip.count("end_primitive;")
    assert chip.count("\n  pin\n") == chip.count("  end_pin;")
    assert chip.count("\n  body\n") == chip.count("  end_body;")


def test_every_role_maps_to_a_real_allegro_pinuse():
    """Same contract as the KiCad, Eagle and Altium maps — a role falling through to
    UNSPEC silently disables the checks Allegro would otherwise run on the net."""
    assert set(PINUSE) >= VALID_ROLES, VALID_ROLES - set(PINUSE)
    assert set(PINUSE.values()) <= {"IN", "OUT", "BI", "TRI", "OCA", "OCL",
                                    "POWER", "GROUND", "UNSPEC", "NC"}
    assert PINUSE["open_drain"] == PINUSE["open_collector"] == "OCL"


def test_pin_table_matches_the_declared_component(files):
    chip = files["pstchip.dat"]
    block = chip[chip.index("primitive 'MCP6002"):chip.index("  end_pin;")]
    pairs = dict(re.findall(r"'(\w+)':\n      PIN_NUMBER='\((\w+)\)';", block))
    assert pairs == {"VOUTA": "1", "VINAN": "2", "VINAP": "3", "VSS": "4",
                     "VINBP": "5", "SDA": "6", "VOUTB": "7", "VDD": "8"}
    assert "PINUSE='OCL';" in block  # the open-drain SDA pin
    assert "PINUSE='GROUND';" in block


def test_footprint_travels_with_the_part(built, files):
    """JEDEC_TYPE is how Allegro knows which .psm to place; without it the part
    imports with no footprint and the board cannot be laid out. It has to be the
    same name the .PcbLib/.kicad_mod is written under, or it resolves to nothing."""
    from ohm.pkggen.packages import spec_name

    assert set(re.findall(r"JEDEC_TYPE='([^']*)';", files["pstchip.dat"])) == \
        {spec_name(part.package) for part in built.low.parts}


def test_partlist_names_every_designator_once(files):
    designators = re.findall(r"^ (\w+) '", files["pstxprt.dat"], re.M)
    assert sorted(designators) == ["U1", "U2"]
    assert files["pstxprt.dat"].count("SECTION_NUMBER 1") == len(designators)
    assert "PRIM_FILE='.\\pstchip.dat'," in files["pstxprt.dat"]


def test_connectivity_is_the_same_as_ohms_own_netlist(built, files):
    """The one assertion that matters: Allegro must receive the netlist Ohm proved,
    not a re-derivation of it."""
    reference = {
        (net, ref, pin)
        for net, nodes in re.findall(r'\(net \(code "\d+"\) \(name "([^"]+)"\)(.*)\)',
                                     emit_netlist(built.low, "demo.ohm"))
        for ref, pin in re.findall(r'\(node \(ref "([^"]+)"\) \(pin "([^"]+)"\)\)',
                                   nodes)
    }
    assert reference, "could not parse the reference netlist"

    got = set()
    net = None
    for line in files["pstxnet.dat"].splitlines():
        if line.startswith("'") and net is None:
            net = line.strip().strip("'")
        elif line.startswith("NET_NAME"):
            net = None
        elif line.startswith("NODE_NAME"):
            ref, pin = line.split("\t")[1].split()
            got.add((net, ref, pin))
    assert got == reference


def test_canonical_paths_escape_the_characters_allegro_treats_as_structure():
    r"""A net named `r1.P1` must reach Allegro as `\r1.p1\`, or the dot reads as a
    hierarchy descent and the net silently loses its nodes."""
    result = compile_source(SRC, "demo.ohm")
    files = emit_allegro(result.design, result.low, "demo")
    signals = re.findall(r"C_SIGNAL='([^']*)'", files["pstxnet.dat"])
    assert signals
    escaped = 0
    for signal in signals:
        tail = signal.split(":", 1)[1]
        assert tail == tail.lower(), "the canonical form is lower-case"
        if any(c in tail for c in ".-+()"):
            assert tail.startswith("\\") and tail.endswith("\\"), tail
            escaped += 1
    assert escaped, "no net name in the fixture needed escaping — test proves nothing"


def test_paths_are_unique_per_part(files):
    paths = re.findall(r"C_PATH='([^']*)'", files["pstxprt.dat"])
    assert len(paths) == len(set(paths)) == 2


def test_emission_is_deterministic(built):
    a = emit_allegro(built.design, built.low, "demo")
    b = emit_allegro(built.design, built.low, "demo")
    assert a == b


def test_cli_writes_the_triplet():
    from ohm.cli import main

    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "demo.ohm"
        src.write_text(SRC, encoding="utf-8")
        out = Path(td) / "out"
        assert main(["build", str(src), "--emit", "allegro", "-o", str(out)]) == 0
        assert sorted(p.name for p in out.iterdir()) == [
            "pstchip.dat", "pstxnet.dat", "pstxprt.dat"]
