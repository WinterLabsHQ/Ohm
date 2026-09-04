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
from ohm.emit_kicad_pro import emit_kicad_pro
from ohm.emit_netlist import emit_netlist
from ohm.emit_zen import emit_zen
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


def test_zen_carries_its_stamp(built):
    """`.zen` is the one artifact with no manifest of its own to carry a version,
    so the lane is written into the generated file's header comment — which is what
    makes this entry checkable the same way as the other fourteen."""
    text = emit_zen(built.low, "led.ohm")
    assert f'pcb-version = "{formats._ZEN_PCB_VERSION}"' in text


def test_ohm_netlist_carries_its_stamp(built):
    text = emit_netlist(built.low, "led.ohm")
    assert f'(version "{formats._NETLIST_VERSION}")' in text


def test_constraints_json_carries_its_stamp(built):
    import json

    text = emit_constraints(built.low, "led.ohm")
    assert json.loads(text)["version"] == formats._SCHEMA_VERSION


def test_kicad_pro_carries_its_stamps(built):
    """The project file's three section versions.

    They were bare literals in `ohm/emit_kicad_pro.py`, and the module was in
    neither this registry nor its owner gate — so ADR-022's table never mentioned
    `.kicad_pro` at all, and any of the three could have been changed with
    nothing noticing.
    """
    import json

    doc = json.loads(emit_kicad_pro(built.low, "led.ohm", built.design))
    assert doc["meta"]["version"] == formats._PRO_PROJECT_VERSION
    assert doc["erc"]["meta"]["version"] == formats._PRO_ERC_VERSION
    assert doc["net_settings"]["meta"]["version"] == formats._PRO_NET_SETTINGS_VERSION


def test_step_carries_its_protocol_name():
    text = emit_step_box("SOIC-8", Decimal("4.9"), Decimal("3.9"),
                         Decimal("1.75"))
    assert f"'{formats._STEP_PROTOCOL}'" in text
    assert str(formats._STEP_PROTOCOL_YEAR) in text
    assert f"APPLICATION_CONTEXT('{formats._STEP_APP_CONTEXT}')" in text


def test_the_step_protocol_agrees_with_the_file_schema():
    """`ohm/pkggen/step.py` states that the DATA section's protocol name "has to
    agree with FILE_SCHEMA above or the file claims one protocol in the header
    and another in the data" — and nothing enforced it.

    Derived rather than a second copy, so the two cannot be half-updated:
    OpenCASCADE's `IsAP242()` lower-cases the APD name and compares it, and the
    header name is that same string plus the `_mim_lf` suffix and the object
    identifier. Changing `_HEADER_SCHEMA` back to AP214 would leave the DATA
    section still claiming AP242, and this is what says so.
    """
    head = formats._HEADER_SCHEMA.split(" ", 1)[0].lower()
    assert head == formats._STEP_PROTOCOL + "_mim_lf", (
        f"FILE_SCHEMA says {head!r}, APPLICATION_PROTOCOL_DEFINITION says "
        f"{formats._STEP_PROTOCOL!r}")


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
# Modules that COULD carry a stamp and deliberately do not. Each states why, so
# "absent from the registry" is a recorded decision rather than an oversight.
NO_STAMP = {
    "ohm.emit_common": "not an emitter — the source identity, value text and BOM "
                       "field names every emitter shares, and no file of its own",
    "ohm.emit_constraints": "the stamp belongs to shared.constraints_schema, "
                            "which owns the seam's schema and is registered there",
    "ohm.emit_ohm": "emits Ohm's own source language, which carries no version",
    "ohm.pkggen.packages": "computes land patterns and writes no file; IPC-7351B "
                           "is the current published revision (ADR-022 sec 6)",
    "ohm.pkggen.pindata_iface": "a provider interface, not an emitter",
}


def _candidate_owners():
    """Every module that writes an external format, discovered rather than listed.

    The gate this replaces compared the registry's owners against a hand-written
    set, so it could only catch a REGISTERED entry with an unknown owner — never
    an emitter that never registered at all. That is not a hypothetical: it is
    how `ohm/emit_kicad_pro.py` wrote three KiCad schema versions as bare
    literals, unmentioned by the registry and by ADR-022's table, through a whole
    round whose stated purpose was to abolish exactly that.
    """
    root = Path(formats.__file__).parent
    mods = {f"ohm.{p.stem}" for p in root.glob("emit_*.py")}
    mods |= {f"ohm.pkggen.{p.stem}" for p in (root / "pkggen").glob("*.py")
             if not p.stem.startswith("__")}
    mods |= {"shared.constraints_schema"}
    return mods


def test_every_emitter_is_either_registered_or_exempt_with_a_reason():
    """An emitter is registered, or it is exempt and says why. No third state."""
    registered = {entry.owner for entry in formats.REGISTRY}
    candidates = _candidate_owners()

    unregistered = candidates - registered - set(NO_STAMP)
    assert not unregistered, (
        f"emitter(s) with no FormatVersion entry and no stated reason: "
        f"{sorted(unregistered)} — register the stamp in ohm/formats.py, or add "
        f"the module to NO_STAMP with the reason it carries none")

    # ...and the exemption list may not rot either: a module that no longer
    # exists, or one that has since been registered, must not sit here excusing
    # something.
    stale = set(NO_STAMP) - candidates
    assert not stale, f"NO_STAMP names modules that do not exist: {sorted(stale)}"
    both = set(NO_STAMP) & registered
    assert not both, f"NO_STAMP excuses modules that ARE registered: {sorted(both)}"
    assert all(reason.strip() for reason in NO_STAMP.values())


def test_registry_owners_are_all_real_modules():
    """No entry may name an owner that does not exist."""
    assert {entry.owner for entry in formats.REGISTRY} <= _candidate_owners()


def test_registry_has_exactly_the_documented_stamps():
    """Pinned count, not just a pinned owner set — .PcbLib and .SchLib each
    carry two stamps (header + version) under the same owner, so the owner
    check alone would not catch one of those four going missing. 14 -> 15 when
    `.zen` gained a version to record (ADR-002 sec 5 closed, ADR-022); 15 -> 19
    when `.kicad_pro`'s three section versions and the STEP DATA-section
    protocol name stopped being bare literals."""
    assert len(formats.REGISTRY) == 19


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


# ---------- (d) the count in the prose is the count in the code ----------
# Every document that describes this registry states how big it is, and three of
# them said "fifteen" for a whole round after it became nineteen: `ohm/formats.py`'s
# own docstring, ADR-022 ("of the fifteen registry entries") and
# `docs/ci/12_multi_eda_version_policy.md`. Nothing noticed, because a number in
# prose is exactly the kind of literal this module exists to abolish — and the
# module had one of its own. Pinning it here is the smallest thing that makes
# "the docs disagree with the code" a build failure rather than a reading.
REGISTRY_SIZE = 19


def test_the_registry_is_the_size_its_documentation_says():
    assert len(formats.REGISTRY) == REGISTRY_SIZE, (
        f"the registry now holds {len(formats.REGISTRY)} entries, not "
        f"{REGISTRY_SIZE}. That number is quoted in prose in three places — "
        f"ohm/formats.py's module docstring, docs/adr/ADR_022_emitted_format_"
        f"versions.md and docs/ci/12_multi_eda_version_policy.md. Update them "
        f"with this constant, or the next reader gets a count that was true once")
