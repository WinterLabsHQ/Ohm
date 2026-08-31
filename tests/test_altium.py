"""Native Altium `.PcbLib` / `.SchLib` emitters (ADR-020).

What is and is not claimed here, in the same spirit as test_eagle.py:

  * The **container** is verified by software Ohm did not write. `olefile` is an
    independent MS-CFB implementation; if it can list the storages and hand back every
    stream byte-for-byte, the compound file is real and not merely self-consistent.
  * The **record framing** is verified against constants read out of Altium-authored
    libraries — a pad's geometry block is 110 bytes, a track's 41, an arc's 45, and
    every primitive opens with `layer, 0x0c, 0x00` and a ten-byte 0xFF spacer. Those
    numbers are facts about the format, so a decoder that disagrees is broken even
    though both sides of the round-trip are ours.
  * The **geometry** is pinned to the `.kicad_mod`, which test_pkggen.py checks against
    KiCad's own reference footprints. That is the same transitive chain the Eagle
    emitter rides on: KiCad reference <- .kicad_mod <- PackageModel -> .PcbLib.

Anything requiring Altium Designer itself — that it opens, renders, or passes Altium's
DRC — is deliberately NOT claimed.
"""

import io
import re
import struct
import tempfile
from decimal import Decimal
from pathlib import Path

import olefile
import pytest

from ohm.driver import compile_source
from ohm.formats import (
    REGISTRY,
    _CFB_VERSION,
    _PCB_LIB_VERSION,
    _SCH_HEADER,
    _SCH_LIB_MINOR_VERSION,
)
from ohm.pkggen import cfb
from ohm.pkggen.altium import (
    _ETYPE,
    _pcb,
    SchSymbol,
    emit_pcblib,
    emit_schlib,
)
from ohm.pkggen.footprint import emit_footprint
from ohm.pkggen.packages import build
from ohm.resolve import VALID_ROLES

PINS = [("1", "VOUTA", "output"), ("2", "VINAN", "input"), ("3", "VINAP", "input"),
        ("4", "VSS", "ground"), ("5", "VINBP", "input"), ("6", "SDA", "open_drain"),
        ("7", "VOUTB", "output"), ("8", "VDD", "power_in")]

SOIC8 = build("SOIC", {"pins": Decimal(8)})

# Facts about the format, read out of an Altium-authored library. A change to any of
# these is a change to what Altium will accept, not a refactor.
PAD_BLOCK = 110
TRACK_BLOCK = 41
ARC_BLOCK = 45
COMMON = 13
SPACER = b"\xff" * 10
LAYER_TOP, LAYER_OVERLAY, LAYER_MECH15 = 1, 33, 71


def _streams(blob: bytes) -> dict[str, bytes]:
    ole = olefile.OleFileIO(io.BytesIO(blob))
    try:
        return {"/".join(e): ole.openstream("/".join(e)).read()
                for e in ole.listdir(streams=True)}
    finally:
        ole.close()


def _storages(blob: bytes) -> set[str]:
    ole = olefile.OleFileIO(io.BytesIO(blob))
    try:
        return {"/".join(e) for e in ole.listdir(streams=False, storages=True)}
    finally:
        ole.close()


def _records(data: bytes) -> list[tuple[int, list[bytes]]]:
    """Decode a footprint Data stream back into (record id, blocks)."""
    length = int.from_bytes(data[:4], "little")
    pos = 4 + length
    out = []
    while pos < len(data):
        rid = data[pos]
        pos += 1
        count = {2: 6}.get(rid, 1)  # only the pad record is multi-block
        blocks = []
        for _ in range(count):
            size = int.from_bytes(data[pos:pos + 4], "little")
            pos += 4
            blocks.append(data[pos:pos + size])
            pos += size
        out.append((rid, blocks))
    assert pos == len(data), "trailing bytes in the Data stream"
    return out


def _pads(blob: bytes, footprint: str) -> dict[str, tuple[int, int, int, int, int]]:
    """pad id -> (x, y, w, h, shape) in Altium units, straight from the bytes."""
    found = {}
    for rid, blocks in _records(_streams(blob)[f"{footprint}/Data"]):
        if rid != 2:
            continue
        pad_id = blocks[0][1:1 + blocks[0][0]].decode()
        geom = blocks[4]
        assert len(geom) == PAD_BLOCK
        x, y = struct.unpack_from("<ii", geom, COMMON)
        w, h = struct.unpack_from("<ii", geom, COMMON + 8)
        found[pad_id] = (x, y, w, h, geom[49])
    return found


def _sch_records(data: bytes) -> list[tuple[int, bytes]]:
    """Decode a symbol Data stream into (record type, payload)."""
    out = []
    pos = 0
    while pos < len(data):
        size, zero, kind = struct.unpack_from("<HBB", data, pos)
        assert zero == 0
        pos += 4
        out.append((kind, data[pos:pos + size]))
        pos += size
    assert pos == len(data)
    return out


# ------------------------------------------------------------------ container


def test_container_is_a_real_compound_file_an_independent_reader_can_open():
    blob = emit_pcblib([SOIC8])
    assert blob[:8] == bytes.fromhex("D0CF11E0A1B11AE1")
    assert olefile.isOleFile(io.BytesIO(blob))
    assert _storages(blob) == {"Library", "FileVersionInfo", "SOIC-8"}


def test_ms_cfb_container_version_field_is_3_read_by_olefile_not_by_ohm():
    """[MS-CFB] 2.2 puts Minor Version at header offset 24 and Major Version
    ("Major Version" is what the spec calls the field this repo calls _CFB_VERSION)
    at offset 26, both little-endian u16. olefile — an independent implementation,
    per the module docstring and pyproject.toml:31-33 on why a self-written reader
    would only agree with a self-written writer about a broken file — parses the
    header itself and exposes the major-version field as `OleFileIO.dll_version`
    (the name comes from olefile's own history, not from the spec). Using it here,
    rather than re-deriving the field from raw bytes, is exactly the "independent
    reader" verification the rest of this module already relies on for streams and
    storages.

    olefile does not expose a spec-named "minor version" accessor either — it keeps
    it as `minor_version`, an internal-looking attribute name we'd rather not lean
    on for an assertion — so the minor version (0x003E, the value Altium's own
    writer uses and not one this registry names) is instead pinned by reading the
    header bytes directly, per the spec offsets above.
    """
    blob = emit_pcblib([SOIC8])
    ole = olefile.OleFileIO(io.BytesIO(blob))
    try:
        assert ole.dll_version == _CFB_VERSION == 3
        # version 3 implies 512-byte sectors; olefile derives sector_size from the
        # sector-shift field independently and would refuse to open the file if the
        # two disagreed, so this is corroborating, not the same fact twice.
        assert ole.sector_size == 512
    finally:
        ole.close()
    minor, major = struct.unpack_from("<HH", blob, 24)
    assert major == _CFB_VERSION == 3
    assert minor == 0x003E  # Altium's own minor-version value; not a registry entry


def test_every_stream_survives_the_round_trip_through_another_implementation():
    """The writer is ours; the reader is not. Agreement means the file is real."""
    payloads = {
        "FileHeader": b"\x1b\x00\x00\x00\x1bPCB 6.0 Binary Library File",
        "Library/Data": bytes(range(256)) * 80,  # >4096: lives in the FAT
        "Library/Header": b"\x01\x00\x00\x00",  # <4096: lives in the mini stream
        "Deep/Nested/Stream": b"",  # empty stream, two levels down
    }
    assert _streams(cfb.write_cfb(payloads)) == payloads


def test_a_name_too_long_for_the_container_is_refused_not_truncated():
    """A truncated name is a part the netlist can no longer find by name."""
    with pytest.raises(cfb.CfbError, match="exceeds"):
        cfb.write_cfb({"x" * (cfb.MAX_NAME + 1): b"data"})


def test_pcblib_has_the_streams_altium_looks_for():
    got = _streams(emit_pcblib([SOIC8], {"SOIC-8": "test part"}))
    assert set(got) >= {"FileHeader", "Library/Data", "Library/Header",
                        "Library/EmbeddedFonts", "FileVersionInfo/Header",
                        "FileVersionInfo/Data", "SOIC-8/Header", "SOIC-8/Data",
                        "SOIC-8/Parameters", "SOIC-8/WideStrings"}
    assert got["FileHeader"].endswith(b"PCB 6.0 Binary Library File")
    assert b"|PATTERN=SOIC-8" in got["SOIC-8/Parameters"]
    assert b"|DESCRIPTION=test part" in got["SOIC-8/Parameters"]
    # the Header stream is the primitive count, and it has to agree with the Data
    assert int.from_bytes(got["SOIC-8/Header"], "little") == \
        len(_records(got["SOIC-8/Data"]))


def test_pcb_library_version_is_the_value_altium_itself_writes():
    """VERSION=3.00 sits right next to KIND=Protel_Advanced_PCB_Library in the same
    head string (ohm/pkggen/altium.py:247) but was never itself asserted — the
    existing KIND check in test_pcblib_has_the_streams_altium_looks_for stops one
    field short of it. Like PCB_HEADER, this is not an Altium Designer release
    number: it is the VERSION= Altium Designer itself writes into the
    library-parameters block of a PCB library it authored, read off a real one
    rather than chosen (ohm/formats.py:63-71)."""
    data = _streams(emit_pcblib([SOIC8]))["Library/Data"]
    assert f"VERSION={_PCB_LIB_VERSION}".encode() in data
    assert _PCB_LIB_VERSION == "3.00"


def test_library_data_lists_every_footprint_by_name():
    chip = build("CHIP0603", {})
    blob = emit_pcblib([SOIC8, chip])
    data = _streams(blob)["Library/Data"]
    params = int.from_bytes(data[:4], "little")
    assert data[4:4 + params].endswith(b"\x00")
    assert b"KIND=Protel_Advanced_PCB_Library" in data
    pos = 4 + params
    assert int.from_bytes(data[pos:pos + 4], "little") == 2
    names, pos = [], pos + 4
    for _ in range(2):
        size = int.from_bytes(data[pos:pos + 4], "little")
        names.append(data[pos + 5:pos + 4 + size].decode())
        pos += 4 + size
    # sorted, so the same design always produces the same bytes
    assert names == sorted([SOIC8.name, chip.name])


# ------------------------------------------------------------------ pcb records


def test_every_primitive_opens_with_the_13_byte_common_header():
    blob = emit_pcblib([SOIC8])
    for rid, blocks in _records(_streams(blob)["SOIC-8/Data"]):
        body = blocks[4] if rid == 2 else blocks[0]
        assert body[1:3] == b"\x0c\x00", f"record {rid} flags"
        assert body[3:COMMON] == SPACER, f"record {rid} spacer"
        assert body[0] in (LAYER_TOP, LAYER_OVERLAY, LAYER_MECH15)


def test_record_block_lengths_are_the_ones_altium_writes():
    sizes = {}
    for rid, blocks in _records(_streams(emit_pcblib([SOIC8]))["SOIC-8/Data"]):
        sizes.setdefault(rid, set()).add(len(blocks[4] if rid == 2 else blocks[0]))
    assert sizes[2] == {PAD_BLOCK}
    assert sizes[4] == {TRACK_BLOCK}
    assert sizes[1] == {ARC_BLOCK}


def test_pcblib_and_kicad_mod_describe_the_same_land_pattern():
    """The .kicad_mod is validated against KiCad's own reference in test_pkggen;
    this pins the .PcbLib to it, so the Altium output inherits that verification."""
    mod = emit_footprint(SOIC8)
    kicad = {m[0]: (Decimal(m[1]), Decimal(m[2]), Decimal(m[3]), Decimal(m[4]))
             for m in re.findall(
                 r'\(pad "?(\w+)"? smd \w+ \(at ([-\d.]+) ([-\d.]+)\)'
                 r' \(size ([\d.]+) ([\d.]+)\)', mod)}
    assert kicad, "could not parse the .kicad_mod"
    altium = _pads(emit_pcblib([SOIC8]), "SOIC-8")
    assert set(altium) == set(kicad)
    for pid, (kx, ky, kw, kh) in kicad.items():
        ax, ay, aw, ah, _shape = altium[pid]
        assert (ax, aw, ah) == (_pcb(kx), _pcb(kw), _pcb(kh)), pid
        assert ay == _pcb(-ky), f"pad {pid} Y not mirrored into Altium's Y-up space"


def test_the_unit_conversion_is_altiums_1_over_10000_mil():
    """Pinned separately so the land-pattern test above compares geometry, not
    arithmetic: 0.5 mm is 19.685 mil is 196850 Altium units."""
    assert _pcb(Decimal("0.5")) == 196850
    assert _pcb(Decimal("0.6")) == 236220
    assert _pcb(Decimal("2.54")) == 1000000


def test_the_silkscreen_is_the_same_drawing_as_the_kicad_footprint():
    """Both emitters call silk_segments/silk_pin1, so the same stubs and the same
    pin-1 marker must come out. A count or a corner that differs means one of them
    stopped sharing the geometry."""
    mod = emit_footprint(SOIC8)
    lines = re.findall(r"\(fp_line \(start ([-\d.]+) ([-\d.]+)\)"
                       r" \(end ([-\d.]+) ([-\d.]+)\)", mod)
    circle = re.search(r"\(fp_circle \(center ([-\d.]+) ([-\d.]+)\)", mod)
    assert lines and circle, "the KiCad footprint has no silkscreen to compare against"

    records = _records(_streams(emit_pcblib([SOIC8]))["SOIC-8/Data"])
    silk = [blocks[0] for rid, blocks in records
            if rid == 4 and blocks[0][0] == LAYER_OVERLAY]
    assert len(silk) == len(lines)
    assert {(struct.unpack_from("<ii", b, COMMON),
             struct.unpack_from("<ii", b, COMMON + 8)) for b in silk} == \
        {((_pcb(Decimal(x1)), _pcb(-Decimal(y1))),
          (_pcb(Decimal(x2)), _pcb(-Decimal(y2)))) for x1, y1, x2, y2 in lines}

    arcs = [blocks[0] for rid, blocks in records if rid == 1]
    assert len(arcs) == 1
    assert struct.unpack_from("<ii", arcs[0], COMMON) == \
        (_pcb(Decimal(circle.group(1))), _pcb(-Decimal(circle.group(2))))


def test_silk_goes_to_top_overlay_and_the_courtyard_to_mechanical_15():
    """Altium has no F.CrtYd. Put the courtyard on the copper silk layer and the
    board silkscreen carries lines the fabricator will actually print."""
    layers = {}
    for rid, blocks in _records(_streams(emit_pcblib([SOIC8]))["SOIC-8/Data"]):
        if rid in (1, 4):
            layers.setdefault(blocks[0][0], 0)
            layers[blocks[0][0]] += 1
    assert layers[LAYER_MECH15] == 4  # a closed courtyard rectangle
    assert layers[LAYER_OVERLAY] >= 1  # silk stubs and/or the pin-1 marker


def test_bga_balls_stay_round():
    from ohm.ir import Quantity

    bga = build("BGA", {"rows": Decimal(4), "cols": Decimal(4),
                        "pitch": Quantity(Decimal("0.8"), "mm"),
                        "ball": Quantity(Decimal("0.4"), "mm")})
    pads = _pads(emit_pcblib([bga]), bga.name)
    assert len(pads) == 16
    assert {shape for *_rest, shape in pads.values()} == {1}  # 1 == Round


# ------------------------------------------------------------------ schlib


def _symbol(**kw) -> bytes:
    return emit_schlib([SchSymbol("MCP6002", PINS, "SOIC-8", {"mpn": "MCP6002"},
                                  "Dual op-amp", **kw)])


def test_every_role_maps_to_a_real_altium_electrical_type():
    """Same reasoning as the KiCad and Eagle maps: a role falling through to a
    default silently weakens the checks the reviewer relies on."""
    assert set(_ETYPE) >= VALID_ROLES, VALID_ROLES - set(_ETYPE)
    assert set(_ETYPE.values()) <= set(range(8))  # Altium's closed 0..7 set
    assert _ETYPE["open_drain"] == _ETYPE["open_collector"] == 3
    assert _ETYPE["ground"] == _ETYPE["power_in"] == 7


def test_schlib_pins_sit_on_the_box_edge_and_grow_outward():
    """Altium anchors a pin at the body and extends it away; symbol_layout reports
    the far end. Getting this backwards puts every pin inside the rectangle."""
    data = _streams(_symbol())["MCP6002/Data"]
    rect = next(p for kind, p in _sch_records(data) if kind == 0 and b"RECORD=14" in p)
    corner_x = int(re.search(rb"\|Corner\.X=(-?\d+)", rect).group(1))
    pins = [p for kind, p in _sch_records(data) if kind == 1]
    assert len(pins) == len(PINS)
    for payload in pins:
        length, x, _y = struct.unpack_from("<hhh", payload, 16)
        flipped = bool(payload[15] & 0x02)
        assert abs(x) == corner_x, "pin is not anchored on the rectangle's edge"
        assert (x < 0) == flipped, "left-hand pins must be flipped to grow outward"
        assert length == 10  # 10 x 10 mil = the 2.54 mm the KiCad symbol uses


def test_schlib_pin_names_and_numbers_match_the_declared_pins():
    pins = [p for kind, p in _sch_records(_streams(_symbol())["MCP6002/Data"])
            if kind == 1]
    got = set()
    for payload in pins:
        pos = 26
        name = payload[pos + 1:pos + 1 + payload[pos]].decode()
        pos += 1 + payload[pos]
        pad = payload[pos + 1:pos + 1 + payload[pos]].decode()
        got.add((pad, name, payload[14]))
    assert got == {(pad, name, _ETYPE[role]) for pad, name, role in PINS}


def test_schlib_binds_the_symbol_to_its_footprint():
    """Without the implementation records the symbol lands unassigned and cannot go
    to a board — the same failure the KiCad Footprint property exists to prevent."""
    records = _sch_records(_streams(_symbol())["MCP6002/Data"])
    payloads = [p for kind, p in records if kind == 0]
    model = next(p for p in payloads if b"RECORD=45" in p)
    assert b"|ModelName=SOIC-8" in model and b"|ModelType=PCBLIB" in model
    list_index = next(i for i, (_k, p) in enumerate(records) if b"RECORD=44" in p)
    assert f"|OwnerIndex={list_index}".encode() in model
    datafile = next(p for p in payloads if b"RECORD=46" in p)
    assert f"|OwnerIndex={list_index + 1}".encode() in datafile


def test_schlib_header_carries_the_file_kind_string_altium_itself_writes():
    """Same rule as the .PcbLib's FileHeader, unasserted until now: "Protel for
    Windows - Schematic Library Editor Binary File Version 5.0" is not an Altium
    Designer release string — it is read verbatim off a real Altium-authored
    .SchLib's header record, the same "unknown field set to what Altium writes is
    a fact" rule ohm/pkggen/altium.py:14-15 states for every constant in this file
    (ohm/formats.py:73-76)."""
    header = _streams(_symbol())["FileHeader"]
    assert f"|HEADER={_SCH_HEADER}".encode() in header


def test_schlib_minor_version_is_the_value_altium_itself_writes():
    """MinorVersion=2 in the same header record — again read off a real
    Altium-authored .SchLib, not chosen (ohm/formats.py:77)."""
    header = _streams(_symbol())["FileHeader"]
    assert f"|MinorVersion={_SCH_LIB_MINOR_VERSION}".encode() in header
    assert _SCH_LIB_MINOR_VERSION == 2


def test_altium_version_stamps_match_the_formats_registry():
    """ohm/formats.py is the one place these five values are supposed to be
    decided, and ohm/pkggen/altium.py + ohm/pkggen/cfb.py import them rather than
    defining their own literals. This pins every Altium-owned registry entry to
    the bytes actually emitted, so a future edit to either side that stops
    agreeing with the other fails here rather than silently drifting."""
    by_constant = {f.constant: f for f in REGISTRY}
    for name in ("_PCB_HEADER", "_PCB_LIB_VERSION", "_SCH_HEADER",
                "_SCH_LIB_MINOR_VERSION", "_CFB_VERSION"):
        assert by_constant[name].owner in ("ohm.pkggen.altium", "ohm.pkggen.cfb")

    pcb_data = _streams(emit_pcblib([SOIC8]))
    assert pcb_data["FileHeader"].endswith(str(by_constant["_PCB_HEADER"].value).encode())
    assert f"VERSION={by_constant['_PCB_LIB_VERSION'].value}".encode() \
        in pcb_data["Library/Data"]

    sch_header = _streams(_symbol())["FileHeader"]
    assert f"|HEADER={by_constant['_SCH_HEADER'].value}".encode() in sch_header
    assert f"|MinorVersion={by_constant['_SCH_LIB_MINOR_VERSION'].value}".encode() \
        in sch_header

    ole = olefile.OleFileIO(io.BytesIO(emit_pcblib([SOIC8])))
    try:
        assert ole.dll_version == by_constant["_CFB_VERSION"].value
    finally:
        ole.close()


def test_schlib_header_indexes_every_symbol():
    blob = emit_schlib([SchSymbol("AAA", PINS), SchSymbol("BBB", PINS)])
    header = _streams(blob)["FileHeader"]
    assert b"|CompCount=2" in header
    assert b"|LibRef0=AAA" in header and b"|LibRef1=BBB" in header
    assert set(_storages(blob)) == {"AAA", "BBB"}


def test_bom_attrs_become_hidden_schematic_parameters():
    blob = emit_schlib([SchSymbol("U", PINS, "SOIC-8",
                                  {"mpn": "MCP6002", "manufacturer": "Microchip"})])
    params = [p for kind, p in _sch_records(_streams(blob)["U/Data"])
              if kind == 0 and b"RECORD=41" in p]
    assert {re.search(rb"\|Name=([^|\x00]+)", p).group(1) for p in params} == \
        {b"manufacturer", b"mpn"}
    assert all(b"|IsHidden=T" in p for p in params)


# ------------------------------------------------------------------ hygiene


def test_emission_is_deterministic():
    assert emit_pcblib([SOIC8]) == emit_pcblib([SOIC8])
    assert _symbol() == _symbol()


def test_duplicate_names_are_refused():
    with pytest.raises(ValueError, match="duplicate"):
        emit_pcblib([SOIC8, SOIC8])
    with pytest.raises(ValueError, match="duplicate"):
        emit_schlib([SchSymbol("U", PINS), SchSymbol("U", PINS)])


def test_cli_emits_one_pcblib_and_one_schlib():
    src = ("component U1:\n"
           "    package = generate(SOIC, pins=8, pitch=1.27mm)\n"
           + "".join(f"    pin P{i}: role=passive, pad={i}\n" for i in range(1, 9))
           + "module M:\n    u = U1()\n")
    result = compile_source(src, "u1.ohm")
    assert result.ok
    from ohm.cli import _emit_generated_packages

    with tempfile.TemporaryDirectory() as td:
        out = Path(td)
        _emit_generated_packages(result, out, fmt="altium", stem="u1")
        written = sorted(p.name for p in out.iterdir())
        assert written == ["u1.PcbLib", "u1.SchLib"]
        assert olefile.isOleFile(str(out / "u1.PcbLib"))
        assert olefile.isOleFile(str(out / "u1.SchLib"))
