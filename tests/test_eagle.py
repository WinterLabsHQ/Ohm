"""Eagle .lbr emitter.

No Eagle install exists here and kicad-cli has no Eagle importer, so nothing can
open the file and confirm it renders. What IS verifiable is that the .lbr describes
the *same land pattern* as the .kicad_mod — and the .kicad_mod is itself checked
against KiCad's own reference footprints in test_pkggen.py. That transitive chain
(KiCad reference <- .kicad_mod <- PackageModel -> .lbr) is what these tests assert.
Anything requiring Eagle itself is deliberately NOT claimed.
"""

import re
import tempfile
import xml.etree.ElementTree as ET
from decimal import Decimal
from pathlib import Path

import pytest

from ohm.driver import compile_source
from ohm.formats import REGISTRY, _EAGLE_VERSION
from ohm.pkggen.eagle import _DIRECTION, emit_eagle_library
from ohm.pkggen.footprint import emit_footprint
from ohm.pkggen.packages import build
from ohm.resolve import VALID_ROLES

PINS = [("1", "VOUTA", "output"), ("2", "VINAN", "input"), ("3", "VINAP", "input"),
        ("4", "VSS", "ground"), ("5", "VINBP", "input"), ("6", "SDA", "open_drain"),
        ("7", "VOUTB", "output"), ("8", "VDD", "power_in")]

SOIC8 = build("SOIC", {"pins": Decimal(8)})


def _tree(xml: str) -> ET.Element:
    return ET.fromstring(xml)


def test_eagle_version_stamp_is_the_xml_library_schema_revision():
    """6.3 is not an Eagle *release* — it is the schema revision that introduced
    XML libraries (Eagle moved off a binary .lbr at 6.0), so it is the oldest
    spelling any XML-reading Eagle understands and there is no older XML value to
    fall back to (ohm/formats.py:57-61). Autodesk EAGLE itself reached end of life
    on 7 June 2026 — Autodesk stopped selling and supporting it and shut down the
    licence servers, so no version of the application runs at all any more, and
    the successor reader is Autodesk Fusion Electronics, which still opens .lbr.
    None of that is a reason to bump the stamp: a newer number only costs an older
    Eagle install the ability to open the file, and buys nothing back, per the
    forward-read policy this module states once for all eight formats."""
    xml = emit_eagle_library(SOIC8, "MCP6002", PINS)
    assert '<!DOCTYPE eagle SYSTEM "eagle.dtd">' in xml
    assert f'<eagle version="{_EAGLE_VERSION}">' in xml
    assert _EAGLE_VERSION == "6.3"  # pin the literal too, so a registry-only edit is caught


def test_eagle_version_matches_the_formats_registry():
    """ohm/formats.py is the one place this value is supposed to be decided, and
    ohm/pkggen/eagle.py imports it rather than defining its own literal. This test
    exists so a future edit to either side — the registry entry or the emitted
    XML — that stops agreeing with the other fails loudly here, in the module a
    person maintaining the Eagle emitter would actually look at."""
    entry = next(f for f in REGISTRY if f.constant == "_EAGLE_VERSION")
    assert entry.artifact == ".lbr" and entry.owner == "ohm.pkggen.eagle"
    xml = emit_eagle_library(SOIC8, "MCP6002", PINS)
    assert f'<eagle version="{entry.value}">' in xml


def test_library_is_well_formed_and_has_the_three_sections():
    root = _tree(emit_eagle_library(SOIC8, "MCP6002", PINS))
    assert root.tag == "eagle"
    lib = root.find("./drawing/library")
    assert lib is not None
    assert lib.find("./packages/package").get("name") == "SOIC-8"
    assert lib.find("./symbols/symbol").get("name") == "MCP6002"
    assert lib.find("./devicesets/deviceset").get("name") == "MCP6002"


def test_pads_match_the_package_model_with_eagle_y_axis():
    """Eagle's Y points up, KiCad's down — every pad must be the mirror."""
    root = _tree(emit_eagle_library(SOIC8, "MCP6002", PINS))
    smds = {s.get("name"): s for s in root.iter("smd")}
    assert len(smds) == len(SOIC8.pads)
    for pad in SOIC8.pads:
        s = smds[pad.id]
        assert Decimal(s.get("x")) == pad.x
        assert Decimal(s.get("y")) == -pad.y, f"pad {pad.id} Y not mirrored"
        assert Decimal(s.get("dx")) == pad.w and Decimal(s.get("dy")) == pad.h
        assert s.get("layer") == "1"


def test_lbr_and_kicad_mod_describe_the_same_land_pattern():
    """The .kicad_mod is validated against KiCad's own reference in test_pkggen;
    this pins the .lbr to it, so the Eagle output inherits that verification."""
    mod = emit_footprint(SOIC8)
    kicad = {m[0]: (Decimal(m[1]), Decimal(m[2]), Decimal(m[3]), Decimal(m[4]))
             for m in re.findall(
                 r'\(pad "?(\w+)"? smd \w+ \(at ([-\d.]+) ([-\d.]+)\)'
                 r' \(size ([\d.]+) ([\d.]+)\)', mod)}
    assert kicad, "could not parse the .kicad_mod"
    root = _tree(emit_eagle_library(SOIC8, "MCP6002", PINS))
    eagle = {s.get("name"): (Decimal(s.get("x")), Decimal(s.get("y")),
                             Decimal(s.get("dx")), Decimal(s.get("dy")))
             for s in root.iter("smd")}
    assert set(eagle) == set(kicad)
    for pid, (kx, ky, kw, kh) in kicad.items():
        ex, ey, ew, eh = eagle[pid]
        assert (ex, ew, eh) == (kx, kw, kh)
        assert ey == -ky


def test_every_pin_is_connected_to_its_pad():
    root = _tree(emit_eagle_library(SOIC8, "MCP6002", PINS))
    connects = {(c.get("pin"), c.get("pad")) for c in root.iter("connect")}
    assert connects == {(name, num) for num, name, _ in PINS}
    pads = {s.get("name") for s in root.iter("smd")}
    assert {pad for _pin, pad in connects} <= pads  # no connect to a nonexistent pad


def test_every_role_maps_to_a_real_eagle_direction():
    """Same reasoning as the KiCad electrical types: a role falling through to a
    default silently weakens the checks the reviewer relies on."""
    assert set(_DIRECTION) >= VALID_ROLES, VALID_ROLES - set(_DIRECTION)
    assert set(_DIRECTION.values()) <= {"nc", "in", "out", "io", "oc",
                                        "pwr", "pas", "hiz", "sup"}
    assert _DIRECTION["open_drain"] == "oc" and _DIRECTION["open_collector"] == "oc"


def test_open_drain_pin_is_oc_in_eagle():
    root = _tree(emit_eagle_library(SOIC8, "MCP6002", PINS))
    by_name = {p.get("name"): p for p in root.iter("pin")}
    assert by_name["SDA"].get("direction") == "oc"
    assert by_name["VSS"].get("direction") == "pwr"
    assert by_name["VOUTA"].get("direction") == "out"


def test_courtyard_goes_to_tkeepout():
    """Eagle's placement DRC reads tKeepout (39) — the courtyard has to land there
    or the designer gets no collision check."""
    root = _tree(emit_eagle_library(SOIC8, "MCP6002", PINS))
    keepout = [w for w in root.iter("wire") if w.get("layer") == "39"]
    assert len(keepout) == 4  # a closed rectangle
    xs = [abs(Decimal(w.get("x1"))) for w in keepout]
    assert max(xs) == SOIC8.courtyard_w / 2


def test_bga_circular_pads_become_roundness_100():
    from ohm.ir import Quantity

    bga = build("BGA", {"rows": Decimal(4), "cols": Decimal(4),
                        "pitch": Quantity(Decimal("0.8"), "mm"),
                        "ball": Quantity(Decimal("0.4"), "mm")})
    pins = [(p.id, f"P{p.id}", "passive") for p in bga.pads]
    root = _tree(emit_eagle_library(bga, "BGA16", pins))
    smds = list(root.iter("smd"))
    assert len(smds) == 16
    assert all(s.get("roundness") == "100" for s in smds)


def test_bom_attrs_become_technology_attributes():
    xml = emit_eagle_library(SOIC8, "MCP6002", PINS,
                             {"mpn": "MCP6002", "manufacturer": "Microchip"})
    root = _tree(xml)
    attrs = {a.get("name"): a.get("value") for a in root.iter("attribute")}
    assert attrs == {"MPN": "MCP6002", "MANUFACTURER": "Microchip"}


def test_emission_is_deterministic():
    a = emit_eagle_library(SOIC8, "MCP6002", PINS, {"mpn": "X"})
    b = emit_eagle_library(SOIC8, "MCP6002", PINS, {"mpn": "X"})
    assert a.encode() == b.encode()


def test_xml_special_characters_are_escaped():
    xml = emit_eagle_library(SOIC8, "A&B", PINS, {"mpn": '<"x">'})
    _tree(xml)  # must still parse
    assert "&amp;" in xml and "&quot;" in xml


@pytest.mark.parametrize("emit,suffix", [("eagle", ".lbr"), ("package", ".kicad_mod")])
def test_cli_emits_the_requested_format(emit, suffix):
    src = ("component U1:\n"
           "    package = generate(SOIC, pins=8, pitch=1.27mm)\n"
           + "".join(f"    pin P{i}: role=passive, pad={i}\n" for i in range(1, 9))
           + "module M:\n    u = U1()\n")
    result = compile_source(src, "u1.ohm")
    assert result.ok
    from ohm.cli import _emit_generated_packages

    with tempfile.TemporaryDirectory() as td:
        out = Path(td)
        _emit_generated_packages(result, out, fmt=emit)
        written = sorted(p.name for p in out.iterdir())
    assert any(n.endswith(suffix) for n in written), written
    if emit == "eagle":
        assert written == ["U1.lbr"]  # one self-contained library, nothing else
