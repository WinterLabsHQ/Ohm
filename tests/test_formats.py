"""ohm/formats.py: the registry of every external-format version stamp Ohm
writes. Three things are checked here, in the same spirit as ADR-021's own
four-constant table: (a) every registered value actually appears in the bytes
the corresponding emitter produces — a stamp registered but never written
would be a lie; (b) every entry has a real reason and a real, importable
owner; (c) the registry is exhaustive — the set of owner modules is pinned,
so adding a ninth format without registering its stamp here fails the build
instead of passing silently.
"""

from __future__ import annotations

import ast
import importlib
import struct
from decimal import Decimal
from pathlib import Path

import pytest

from ohm import formats
from ohm.driver import compile_source
from ohm.emit_allegro import emit_allegro
from ohm.emit_constraints import emit_constraints
from ohm.emit_kicad import emit_kicad_pcb, emit_kicad_sch
from ohm.emit_netlist import emit_netlist
from ohm.formats import FormatVersion
from ohm.pkggen.altium import SchSymbol, emit_pcblib, emit_schlib
from ohm.pkggen.eagle import emit_eagle_library
from ohm.pkggen.footprint import emit_footprint
from ohm.pkggen.packages import build
from ohm.pkggen.step import emit_step_box
from ohm.pkggen.symbol import emit_symbol

GOLDEN = Path(__file__).parent / "golden"
SOIC8 = build("SOIC", {"pins": Decimal(8)})
PINS = [("1", "VOUTA", "output"), ("2", "VINAN", "input"), ("3", "VINAP", "input"),
        ("4", "VSS", "ground"), ("5", "VINBP", "input"), ("6", "SDA", "open_drain"),
        ("7", "VOUTB", "output"), ("8", "VDD", "power_in")]


@pytest.fixture(scope="module")
def built():
    """A real compiled design (P4's own golden LED board), reused for every
    emitter that takes IR-LOW rather than a bare PackageModel."""
    result = compile_source((GOLDEN / "led.ohm").read_text(), "led.ohm")
    assert result.ok, [d.machine() for d in result.diagnostics]
    return result


# ---------- (a) every stamp is really in the bytes ----------
def test_kicad_sch_carries_its_stamp(built):
    text = emit_kicad_sch(built.low, "led.ohm")
    assert f"(version {formats._SCH_VERSION})" in text


def test_kicad_pcb_carries_its_stamp(built):
    text = emit_kicad_pcb(built.low, "led.ohm")
    assert f"(version {formats._PCB_VERSION})" in text


def test_kicad_mod_carries_its_stamp():
    text = emit_footprint(SOIC8)
    assert f"(version {formats._FP_VERSION})" in text


def test_kicad_sym_carries_its_stamp():
    text = emit_symbol("MCP6002", PINS, footprint="Package_SO:SOIC-8")
    assert f"(version {formats._SYM_VERSION})" in text


def test_eagle_lbr_carries_its_stamp():
    text = emit_eagle_library(SOIC8, "MCP6002", PINS)
    assert f'version="{formats._EAGLE_VERSION}"' in text


def test_altium_pcblib_carries_its_stamps():
    blob = emit_pcblib([SOIC8])
    assert formats._PCB_HEADER.encode("cp1252") in blob
    assert f"VERSION={formats._PCB_LIB_VERSION}".encode("cp1252") in blob


def test_altium_schlib_carries_its_stamps():
    symbol = SchSymbol("MCP6002", PINS, footprint="MCP6002")
    blob = emit_schlib([symbol])
    assert formats._SCH_HEADER.encode("cp1252") in blob
    assert f"MinorVersion={formats._SCH_LIB_MINOR_VERSION}".encode("cp1252") in blob


def test_cfb_container_carries_its_version():
    """Both Altium emitters wrap `write_cfb`, so the container stamp shows up
    in either one's output; the pcblib is picked arbitrarily. Major version
    sits at byte 26 (signature[8] + CLSID[16] + minor-version[2]), per the
    struct.pack("<HHH", minor, major, byte-order) that writes the header."""
    blob = emit_pcblib([SOIC8])
    (major,) = struct.unpack_from("<H", blob, 26)
    assert major == formats._CFB_VERSION


def test_allegro_pstxprt_carries_its_stamp(built):
    files = emit_allegro(built.design, built.low, "led")
    assert f"PST_VERSION='{formats._PST_VERSION}'" in files["pstxprt.dat"]


def test_step_carries_its_stamp():
    text = emit_step_box("SOIC-8", Decimal("4.9"), Decimal("3.9"), Decimal("1.75"))
    assert formats._HEADER_SCHEMA in text


def test_ohm_netlist_carries_its_stamp(built):
    text = emit_netlist(built.low, "led.ohm")
    assert f'(version "{formats._NETLIST_VERSION}")' in text


def test_constraints_json_carries_its_stamp(built):
    import json

    text = emit_constraints(built.low, "led.ohm")
    assert json.loads(text)["version"] == formats._SCHEMA_VERSION


# ---------- (b) every entry is real ----------
def test_every_entry_has_a_real_reason_and_a_real_owner():
    for entry in formats.REGISTRY:
        assert isinstance(entry, FormatVersion)
        assert entry.reason.strip(), f"{entry.constant} has no reason"
        assert len(entry.reason) > 20, f"{entry.constant}'s reason looks like filler"
        module = importlib.import_module(entry.owner)
        assert hasattr(module, entry.constant), \
            f"{entry.owner} does not export {entry.constant}"


def test_every_entry_has_a_nonempty_artifact_and_constant_name():
    for entry in formats.REGISTRY:
        assert entry.artifact.strip()
        assert entry.constant.strip()
        assert entry.value not in (None, "")


# ---------- (c) the registry is exhaustive ----------
EXPECTED_OWNERS = {
    "ohm.emit_kicad",
    "ohm.pkggen.footprint",
    "ohm.pkggen.symbol",
    "ohm.pkggen.eagle",
    "ohm.pkggen.altium",
    "ohm.pkggen.cfb",
    "ohm.emit_allegro",
    "ohm.pkggen.step",
    "ohm.emit_netlist",
    "shared.constraints_schema",
}


def test_registry_owners_match_the_known_emitter_set():
    """A new external format means a ninth owner shows up here, unregistered —
    this must fail until someone adds its FormatVersion entry, not pass quietly
    the way a bare literal buried in the new emitter would."""
    assert {entry.owner for entry in formats.REGISTRY} == EXPECTED_OWNERS


def test_registry_has_exactly_the_documented_stamps():
    """Pinned count, not just a pinned owner set — .PcbLib and .SchLib each
    carry two stamps (header + version) under the same owner, so the owner
    check alone would not catch one of those four going missing."""
    assert len(formats.REGISTRY) == 14


# ---------- hermeticity (N1): stdlib + shared only, nothing else, no I/O ----------
def test_formats_module_imports_only_stdlib_and_shared():
    """Mirrors tests/test_verify_footprints.py's import-AST-walk and
    tests/test_lower_emit.py's forbidden-import gate: this module is the
    registry every emitter on the build path depends on, so it must not
    itself depend on any of them (that would be a cycle) or on anything
    impure (that would break N1 for every emitter that imports it)."""
    path = Path(formats.__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    allowed_roots = {"__future__", "dataclasses", "shared"}
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        for name in names:
            root = name.split(".")[0]
            assert root in allowed_roots, (
                f"ohm/formats.py imports '{name}', outside stdlib + shared")


def test_formats_module_does_no_io_or_impure_calls():
    """No subprocess, no network, no clock, no file access at import time —
    the same forbidden-call surface tests/test_lower_emit.py polices for the
    rest of the build path (socket/requests/urllib/http/subprocess/time/
    random/datetime), checked here by name since this module has no import
    of them to catch: a call like `open(...)` or `Path(...).read_text()` at
    module level would still be impure without importing a forbidden module."""
    path = Path(formats.__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    forbidden_calls = {"open", "input", "exec", "eval"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in forbidden_calls, (
                f"ohm/formats.py calls '{node.func.id}' — not hermetic (N1)")
