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
from ohm.emit_allegro import PINUSE, _seg, emit_allegro
from ohm.emit_netlist import emit_netlist
from ohm.formats import REGISTRY, _PST_VERSION
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


def test_pst_version_is_the_string_a_real_orcad_triplet_carries(files):
    """PST_VERSION is the one format-version token in this triplet, and it is the
    directive Allegro's own logic importer string-matches against — so unlike
    everything else this emitter generates, it is not derived from Ohm's IR at
    all. It is copied verbatim from a real OrCAD-written pstxprt.dat
    (ohm/emit_allegro.py:1-21, ohm/formats.py:88-93); inventing a plausible-looking
    value here would not fail loudly, it would just fail to import."""
    assert f" PST_VERSION='{_PST_VERSION}';" in files["pstxprt.dat"]
    assert _PST_VERSION == "PST_HDL_CENTRIC_VERSION_0"


def test_file_type_header_lines_are_exact_including_the_one_that_looks_like_a_typo(files):
    """All three FILE_TYPE lines, pinned to their exact text rather than merely
    their prefix (the existing .startswith checks in
    test_the_three_files_allegro_asks_for already cover the prefix). pstxnet.dat's
    line carries spaces around the `=` — 'FILE_TYPE = EXPANDEDNETLIST;' — while
    pstchip.dat's and pstxprt.dat's do not. That asymmetry looks like a typo but is
    copied verbatim from a real OrCAD-written triplet (ohm/emit_allegro.py:1-21)
    and is deliberate: normalizing it to match the other two would produce a file
    that no longer matches what real Allegro-adjacent tooling writes."""
    assert files["pstchip.dat"].splitlines()[0] == "FILE_TYPE=LIBRARY_PARTS;"
    assert files["pstxprt.dat"].splitlines()[0] == "FILE_TYPE=EXPANDEDPARTLIST;"
    assert files["pstxnet.dat"].splitlines()[0] == "FILE_TYPE = EXPANDEDNETLIST;"


def test_allegro_version_stamp_matches_the_formats_registry(files):
    """ohm/formats.py is the one place PST_VERSION is supposed to be decided, and
    ohm/emit_allegro.py imports it rather than defining its own literal. This pins
    the emitted directive to that registry entry so the two cannot drift apart."""
    entry = next(f for f in REGISTRY if f.constant == "_PST_VERSION")
    assert entry.artifact == "Allegro pstxprt.dat" and entry.owner == "ohm.emit_allegro"
    assert f" PST_VERSION='{entry.value}';" in files["pstxprt.dat"]


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
    r"""A segment like `r1.P1` must reach Allegro as `\r1.p1\`, or the dot reads
    as a hierarchy descent and the net silently loses its nodes.

    Driven directly rather than through a net name: net names are now always
    `[A-Za-z0-9_]` (derived ones via `readable_net_name`, authored ones via the
    grammar's NAME), so no fixture can produce a net that needs escaping. The
    escape still guards part and pin segments, so it is still tested — and the
    emitted signal names are checked to be clean rather than merely unescaped.
    """
    for raw, want in [("r1.P1", "\\r1.p1\\"),
                      ("+3.3V", "\\+3.3v\\"),
                      ("BOOSTXL-CC2650MA", "\\boostxl-cc2650ma\\"),
                      ("VDD_5V", "vdd_5v"),
                      ("FMU_SPI_FRAM_MISO", "fmu_spi_fram_miso")]:
        assert _seg(raw) == want, raw

    result = compile_source(SRC, "demo.ohm")
    files = emit_allegro(result.design, result.low, "demo")
    signals = re.findall(r"C_SIGNAL='([^']*)'", files["pstxnet.dat"])
    assert signals
    for signal in signals:
        tail = signal.split(":", 1)[1]
        assert tail == tail.lower(), "the canonical form is lower-case"
        # every net name is now structurally safe, so none should be wrapped
        assert not any(c in tail for c in ".-+()"), tail
        assert not tail.startswith("\\"), tail


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
