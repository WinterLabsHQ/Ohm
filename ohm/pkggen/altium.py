"""PackageModel + pin table → native Altium `.PcbLib` and `.SchLib` (ADR-020).

Altium is the second target after KiCad that Ohm emits *natively*: not a neutral
interchange the user has to import and repair, but the binary library format Altium
Designer itself writes. Both files are MS-CFB containers (see `cfb.py`); inside, the
PCB side is packed binary records and the schematic side is pipe-delimited parameter
strings with the pins — and only the pins — in binary.

Nothing here was guessed. Every field offset, block length and magic constant below
was read out of Altium-authored libraries (an IPC-7352 chip-capacitor `.PcbLib` and a
general-purpose `.SchLib`) and cross-checked against the two published descriptions of
the format, KiCad's importer notes and pyAltiumLib. Constants whose meaning is still
unknown are copied from those files verbatim and named `_OBSERVED_*` rather than
invented: an unknown field set to zero is a guess, an unknown field set to what Altium
writes is a fact.

Geometry is shared with the KiCad emitter, not reimplemented: pads come from the same
`PackageModel`, silkscreen from `footprint.silk_segments`, symbol layout from
`symbol.symbol_layout`. The Altium output therefore cannot drift from the `.kicad_mod`
that `tests/test_pkggen.py` validates against KiCad's own reference footprints.

Two axis conventions differ from KiCad and are handled here, not by the caller:
  * Altium's PCB Y axis points UP; PackageModel is KiCad's Y-down, so every pad and
    line y is negated. The schematic side is Y-up in both, so it is not.
  * An Altium schematic pin is anchored at the *body* end and grows outward, where
    `symbol_layout` reports the connection point. The half-box width is subtracted back
    off, which is why pin length never appears in the emitted x.

Deliberate simplifications, each of which loses nothing electrical:
  * roundrect pads are emitted as Altium rectangular pads. Corner rounding lives in a
    596-byte per-layer array Altium only writes for shape 9; the land pattern — the
    thing a fabricator and a DRC care about — is identical, and Altium's own
    IPC-generated libraries use plain rectangles.
  * two-terminal glyphs (the resistor zig-zag and friends the KiCad symbol draws) are
    not carried over; every part is the labelled box. The glyph bodies sit on a finer
    grid than Altium's integer schematic unit, and a box with the right pins is
    correct where a mis-gridded glyph would be pretty and wrong.
"""

from __future__ import annotations

import hashlib
import struct
from collections.abc import Mapping, Sequence
from decimal import ROUND_HALF_UP, Decimal
from typing import NamedTuple

from ohm.pkggen.cfb import write_cfb
from ohm.pkggen.footprint import silk_pin1, silk_segments
from ohm.pkggen.packages import PackageModel
from ohm.pkggen.symbol import box_half_w, symbol_layout

# ---------------------------------------------------------------- units

# PCB coordinates are signed 32-bit counts of 1/10000 mil.
_PCB_PER_MM = Decimal(10000) / Decimal("0.0254")
# Schematic coordinates are signed 16-bit counts of 10 mil, which is exactly the
# 2.54 mm/10 grid `symbol.py` already lays symbols out on.
_SCH_MM = Decimal("0.254")


def _pcb(mm: Decimal) -> int:
    return int((Decimal(mm) * _PCB_PER_MM).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _sch(mm: Decimal) -> int:
    """Schematic units. Exact by construction — `symbol_layout` works on the 0.254 grid."""
    units = Decimal(mm) / _SCH_MM
    if units != units.to_integral_value():
        raise ValueError(f"{mm} mm is not on Altium's 10-mil schematic grid")
    return int(units)


# ---------------------------------------------------------------- layers

_L_TOP = 1
_L_TOP_OVERLAY = 33  # silkscreen
_L_MECH15 = 71  # where Altium libraries conventionally keep the courtyard

_PAD_ROUND, _PAD_RECT = 1, 2

# Altium's own layer table, in id order. The library-level parameter block has to
# describe all 82 or Altium opens the file with an empty stack.
_LAYER_NAMES = (
    ["Top Layer"] + [f"Mid-Layer {i}" for i in range(1, 31)] + ["Bottom Layer"]
    + ["Top Overlay", "Bottom Overlay", "Top Paste", "Bottom Paste",
       "Top Solder", "Bottom Solder"]
    + [f"Internal Plane {i}" for i in range(1, 17)]
    + ["Drill Guide", "Keep-Out Layer"]
    + [f"Mechanical {i}" for i in range(1, 17)]
    + ["Drill Drawing", "Multi-Layer", "Connections", "Background",
       "DRC Error Markers", "Selections", "Visible Grid 1", "Visible Grid 2",
       "Pad Holes", "Via Holes"]
)
assert len(_LAYER_NAMES) == 82

# ---------------------------------------------------------------- primitives

_SILK_W = Decimal("0.12")
_COURTYARD_W = Decimal("0.05")
_PIN1_R = Decimal("0.1")  # same radius emit_footprint draws the KiCad marker at

# Every PCB record opens with the same 13 bytes: layer, two flag bytes, then a
# ten-byte 0xFF spacer Altium checks for. 0x0c = the "unlocked" flags a normal
# library primitive carries.
_FLAGS = b"\x0c\x00"
_SPACER = b"\xff" * 10


def _common(layer: int) -> bytes:
    return bytes([layer]) + _FLAGS + _SPACER


def _blk(payload: bytes) -> bytes:
    """A length-prefixed block: how every PCB record is delimited."""
    return struct.pack("<I", len(payload)) + payload


def _cp1252(text: str) -> bytes:
    """Altium's 8-bit string encoding.

    Text outside it needs the `%UTF8%` property variant, which Ohm does not emit —
    so say which string is the problem rather than surfacing a codec traceback from
    four frames down.
    """
    try:
        return text.encode("windows-1252")
    except UnicodeEncodeError as exc:
        raise ValueError(
            f"{text!r} cannot be written to an Altium library: "
            f"{text[exc.start:exc.end]!r} is outside Windows-1252") from None


def _strblk(text: str) -> bytes:
    raw = _cp1252(text)
    if len(raw) > 255:
        raise ValueError(f"string too long for an Altium block: {text!r}")
    return bytes([len(raw)]) + raw


# The pad geometry block ends with a run of fields whose meaning is not documented
# anywhere public. These are the exact values an Altium-authored library carries for
# an ordinary SMD pad (rule-driven clearances, 4 mil solder-mask expansion), copied
# rather than zeroed so the pad behaves in Altium the way that library's pads do.
_OBSERVED_PAD_TAIL = (
    struct.pack("<I", 0)
    + struct.pack("<I", 100000)  # 10 mil
    + struct.pack("<H", 4)
    + struct.pack("<I", 100000)
    + struct.pack("<I", 200000)  # 20 mil
    + struct.pack("<I", 200000)
    + struct.pack("<i", 0)  # paste-mask expansion
    + struct.pack("<i", 40000)  # solder-mask expansion, 4 mil
    + b"\x00" * 7
    + b"\x01\x01"  # paste/solder expansion follow the rule, not a manual override
    + b"\x00" * 7
)
_OBSERVED_PAD_MARKER = "|&|0"  # blk2 of every pad in the reference library


def _pad_record(pad_id: str, x: int, y: int, w: int, h: int, shape: int) -> bytes:
    geometry = (
        _common(_L_TOP)
        + struct.pack("<ii", x, y)
        + struct.pack("<ii", w, h) * 3  # top / middle / bottom stack, all the same
        + struct.pack("<i", 0)  # no hole: everything PackageModel builds is SMD
        + bytes([shape, shape, shape])
        + struct.pack("<d", 0.0)  # rotation
        + b"\x01"  # plated
        + b"\x00"
        + b"\x00"  # simple stack mode
        + b"\x00"
        + _OBSERVED_PAD_TAIL
    )
    assert len(geometry) == 110, len(geometry)
    return (
        b"\x02"
        + _blk(_strblk(pad_id))
        + _blk(b"\x00")
        + _blk(_strblk(_OBSERVED_PAD_MARKER))
        + _blk(b"\x00")
        + _blk(geometry)
        + _blk(b"")  # no shape-9 corner-radius block; see the module docstring
    )


def _track_record(layer: int, x1: int, y1: int, x2: int, y2: int, width: int) -> bytes:
    body = (_common(layer) + struct.pack("<iiii", x1, y1, x2, y2)
            + struct.pack("<i", width) + b"\x00" * 8)
    assert len(body) == 41, len(body)
    return b"\x04" + _blk(body)


def _arc_record(layer: int, cx: int, cy: int, radius: int, width: int) -> bytes:
    body = (_common(layer) + struct.pack("<ii", cx, cy) + struct.pack("<i", radius)
            + struct.pack("<dd", 0.0, 360.0) + struct.pack("<i", width))
    assert len(body) == 45, len(body)
    return b"\x01" + _blk(body)


def footprint_records(model: PackageModel) -> list[bytes]:
    """Every primitive in one footprint, in emission order."""
    out: list[bytes] = []
    for pad in model.pads:
        shape = _PAD_ROUND if pad.shape == "circle" else _PAD_RECT
        # y negated: PackageModel is KiCad Y-down, Altium's PCB space is Y-up
        out.append(_pad_record(pad.id, _pcb(pad.x), _pcb(-pad.y),
                               _pcb(pad.w), _pcb(pad.h), shape))
    silk_w = _pcb(_SILK_W)
    for x1, y1, x2, y2 in silk_segments(model):
        out.append(_track_record(_L_TOP_OVERLAY, _pcb(x1), _pcb(-y1),
                                 _pcb(x2), _pcb(-y2), silk_w))
    if (corner := silk_pin1(model)) is not None:
        out.append(_arc_record(_L_TOP_OVERLAY, _pcb(corner[0]), _pcb(-corner[1]),
                               _pcb(_PIN1_R), silk_w))
    hw, hh = model.courtyard_w / 2, model.courtyard_h / 2
    court = _pcb(_COURTYARD_W)
    box = ((-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh))
    for (ax, ay), (bx, by) in zip(box, box[1:] + box[:1]):
        out.append(_track_record(_L_MECH15, _pcb(ax), _pcb(-ay), _pcb(bx), _pcb(-by),
                                 court))
    return out


# ---------------------------------------------------------------- .PcbLib

_PCB_HEADER = "PCB 6.0 Binary Library File"


def _param_block(text: str) -> bytes:
    """A parameter collection: length-prefixed, NUL-terminated."""
    return _blk(_cp1252(text) + b"\x00")


def _library_parameters() -> str:
    """The library-level board/stack description Altium expects before any footprint.

    Altium writes it as \\r-separated sub-blocks of five layers each; the split is
    cosmetic but reproducing it keeps a diff against a real library readable.
    """
    def layer(i: int) -> str:
        nxt = 32 if i == 1 else 0  # top's next in the stack is bottom
        return (f"|LAYER{i}NAME={_LAYER_NAMES[i - 1]}|LAYER{i}PREV=0|LAYER{i}NEXT={nxt}"
                f"|LAYER{i}MECHENABLED=FALSE|LAYER{i}SHEETLINKED=FALSE"
                f"|LAYER{i}COPTHICK=1.4mil|LAYER{i}DIELTYPE=0|LAYER{i}DIELCONST=4.800"
                f"|LAYER{i}DIELHEIGHT=12.6mil|LAYER{i}DIELMATERIAL=FR-4")

    head = ("|KIND=Protel_Advanced_PCB_Library|VERSION=3.00"
            "|TOPTYPE=3|TOPCONST=3.500|TOPHEIGHT=0.4mil|TOPMATERIAL=Solder Resist"
            "|BOTTOMTYPE=3|BOTTOMCONST=3.500|BOTTOMHEIGHT=0.4mil"
            "|BOTTOMMATERIAL=Solder Resist|LAYERSTACKSTYLE=0"
            "|SHOWTOPDIELECTRIC=FALSE|SHOWBOTTOMDIELECTRIC=FALSE"
            + "".join(layer(i) for i in range(1, 6)))
    blocks = [head]
    for start in range(6, 83, 5):
        blocks.append("|RECORD=Board"
                      + "".join(layer(i) for i in range(start, min(start + 5, 83))))
    blocks.append("|RECORD=Board|BIGVISIBLEGRIDSIZE=1000000.000"
                  "|VISIBLEGRIDSIZE=50000.000|SNAPGRIDSIZE=50000.000000"
                  "|SNAPGRIDSIZEX=50000.000000|SNAPGRIDSIZEY=50000.000000"
                  "|ELECTRICALGRIDRANGE=8mil|ELECTRICALGRIDENABLED=TRUE"
                  "|DOTGRID=FALSE|DISPLAYUNIT=0|LAYERSETSCOUNT=0")
    return "\r".join(blocks)


def emit_pcblib(models: Sequence[PackageModel],
                descriptions: Mapping[str, str] | None = None) -> bytes:
    """One `.PcbLib` holding every generated package.

    Altium libraries are many-part files — one per package family is how the stock
    libraries ship — so unlike the Eagle emitter this takes the whole set at once.
    """
    if not models:
        raise ValueError("a .PcbLib needs at least one footprint")
    by_name = {m.name: m for m in models}
    if len(by_name) != len(models):
        raise ValueError("duplicate footprint names in one .PcbLib")

    entries: dict[str, bytes] = {
        "FileHeader": (struct.pack("<I", len(_PCB_HEADER)) + _strblk(_PCB_HEADER)),
        "FileVersionInfo/Header": struct.pack("<I", 1),
        "FileVersionInfo/Data": _param_block("|COUNT=0"),
        "Library/Header": struct.pack("<I", 1),
        "Library/EmbeddedFonts": struct.pack("<I", 0),
        "Library/Data": (_param_block(_library_parameters())
                         + struct.pack("<I", len(by_name))
                         + b"".join(_blk(_strblk(n)) for n in sorted(by_name))),
    }
    for name in sorted(by_name):
        model = by_name[name]
        records = footprint_records(model)
        height = model.body[2] if model.body is not None else Decimal(0)
        params = f"|PATTERN={name}|HEIGHT={height}mm"
        if descriptions and (desc := descriptions.get(name)):
            params += f"|DESCRIPTION={desc}"
        entries[f"{name}/Header"] = struct.pack("<I", len(records))
        entries[f"{name}/Data"] = _blk(_strblk(name)) + b"".join(records)
        entries[f"{name}/Parameters"] = _param_block(params)
        entries[f"{name}/WideStrings"] = _blk(b"\x00")
    return write_cfb(entries)


# ---------------------------------------------------------------- .SchLib

# role -> Altium pin electrical type. Same contract as the KiCad and Eagle maps: every
# role in resolve.VALID_ROLES must appear, because a role silently falling through to
# Passive disables the ERC the reviewer is relying on. Altium's set is
# Input/IO/Output/OpenCollector/Passive/HiZ/OpenEmitter/Power — it has no
# no-connect type, so `nc` maps to Passive and the NC intent travels in the pin name.
_ETYPE = {
    "input": 0, "bidirectional": 1, "output": 2,
    "open_collector": 3, "open_drain": 3,
    "passive": 4, "free": 4, "nc": 4,
    "power_in": 7, "power_out": 7, "ground": 7,
}

_PIN_BASE_FLAGS = 0x20  # set on every pin in Altium-authored libraries
_PIN_FLIPPED = 0x02  # pin grows in -x: the left-hand column of the box
_PIN_SHOW_NAME = 0x08
_PIN_SHOW_DESIGNATOR = 0x10

_SCH_HEADER = ("Protel for Windows - Schematic Library Editor Binary File "
               "Version 5.0")


class SchSymbol(NamedTuple):
    name: str
    pins: Sequence[tuple[str, str, str]]  # (pad number, pin name, role)
    footprint: str | None = None  # the .PcbLib pattern this symbol binds to
    attrs: Mapping[str, str] | None = None
    description: str = ""


def _unique_id(*parts: str) -> str:
    """Altium's 8-letter object id. Derived from the content so runs are identical."""
    digest = hashlib.sha256("\x00".join(parts).encode("utf-8")).digest()
    return "".join(chr(ord("A") + b % 26) for b in digest[:8])


def _sch_param(text: str) -> bytes:
    payload = _cp1252(text) + b"\x00"
    return struct.pack("<HBB", len(payload), 0, 0) + payload


def _sch_pin(pad: str, name: str, role: str, x: int, y: int, length: int,
             flipped: bool) -> bytes:
    flags = _PIN_BASE_FLAGS | _PIN_SHOW_NAME | _PIN_SHOW_DESIGNATOR
    if flipped:
        flags |= _PIN_FLIPPED
    payload = (
        struct.pack("<I", 2)  # record id, in the binary record's own header
        + b"\x00"
        + struct.pack("<h", 1)  # OwnerPartId
        + b"\x00" * 5  # display mode + the four pin-symbol decorations, all none
        + b"\x00\x01"  # empty description, then the byte Altium always sets to 1
        + bytes([_ETYPE.get(role, 4), flags])
        + struct.pack("<hhh", length, x, y)
        + struct.pack("<I", 0)  # colour: inherit
        + _strblk(name)
        + _strblk(pad)
        + b"\x00" + _strblk("") + b"\x00"
    )
    return struct.pack("<HBB", len(payload), 0, 1) + payload


def _symbol_records(symbol: SchSymbol) -> list[bytes]:
    pins = list(symbol.pins)
    box_h, placed = symbol_layout(pins)
    half_w = box_half_w(pins)
    hw, hh = _sch(half_w), _sch(box_h)
    uid = _unique_id(symbol.name)

    records = [_sch_param(
        f"|RECORD=1|LibReference={symbol.name}"
        f"|ComponentDescription={symbol.description}"
        # PartCount is Altium's count-plus-one; a single-unit symbol says 2
        "|PartCount=2|DisplayModeCount=1|IndexInSheet=-1|OwnerPartId=-1"
        "|CurrentPartId=1|LibraryPath=*|SourceLibraryName=*|SheetPartFileName=*"
        f"|TargetFileName=*|UniqueID={uid}|AreaColor=11599871|Color=128"
        f"|PartIDLocked=F|DesignItemId={symbol.name}|AllPinCount={len(pins)}")]

    for index, (key, value) in enumerate(sorted((symbol.attrs or {}).items())):
        if not str(value):
            continue
        records.append(_sch_param(
            f"|RECORD=41|IndexInSheet={index}|OwnerPartId=-1|Location.X=0"
            f"|Location.Y={-hh - 5}|Color=8388608|FontID=1|IsHidden=T"
            f"|Text={value}|Name={key}|UniqueID={_unique_id(symbol.name, key)}"))

    records.append(_sch_param(
        f"|RECORD=14|IsNotAccesible=T|IndexInSheet={len(records)}|OwnerPartId=1"
        f"|Location.X={-hw}|Location.Y={-hh}|Corner.X={hw}|Corner.Y={hh}"
        f"|Color=128|AreaColor=11599871|IsSolid=T"
        f"|UniqueID={_unique_id(symbol.name, 'body')}"))

    for pad, pin_name, role, _x, y, side in placed:
        # symbol_layout hands back the connection point; Altium anchors the pin at the
        # body and grows it outward, so the anchor is the box edge itself
        flipped = side == "left"
        records.append(_sch_pin(pad, pin_name, role,
                                -hw if flipped else hw, _sch(y),
                                _sch(Decimal("2.54")), flipped))

    records.append(_sch_param(
        f"|RECORD=34|IndexInSheet=-1|OwnerPartId=-1|Location.X={-hw}"
        f"|Location.Y={hh + 2}|Color=8388608|FontID=1|Text=U?|Name=Designator"
        f"|ReadOnlyState=1|UniqueID={_unique_id(symbol.name, 'designator')}"))

    if symbol.footprint:
        # the symbol->footprint binding: a list record, the model inside it, and the
        # datafile record inside that, each pointing at its owner by stream index
        list_index = len(records)
        records.append(_sch_param("|RECORD=44"))
        records.append(_sch_param(
            f"|RECORD=45|OwnerIndex={list_index}|IndexInSheet=-1"
            f"|ModelName={symbol.footprint}|ModelType=PCBLIB|DatafileCount=1"
            f"|ModelDatafileEntity0={symbol.footprint}|ModelDatafileKind0=PCBLib"
            f"|IsCurrent=T|UniqueID={_unique_id(symbol.name, 'model')}"))
        records.append(_sch_param(f"|RECORD=46|OwnerIndex={list_index + 1}"))
    return records


def emit_schlib(symbols: Sequence[SchSymbol]) -> bytes:
    """One `.SchLib` holding every component's symbol."""
    if not symbols:
        raise ValueError("a .SchLib needs at least one symbol")
    by_name = {s.name: s for s in symbols}
    if len(by_name) != len(symbols):
        raise ValueError("duplicate symbol names in one .SchLib")

    bodies = {name: _symbol_records(by_name[name]) for name in sorted(by_name)}
    weight = sum(len(r) for r in bodies.values())

    header = (f"|HEADER={_SCH_HEADER}|Weight={weight}|MinorVersion=2"
              f"|UniqueID={_unique_id(*sorted(by_name))}"
              "|FontIdCount=1|Size1=10|FontName1=Times New Roman"
              "|UseMBCS=T|IsBOC=T|SheetStyle=9|BorderOn=T|SheetNumberSpaceSize=4"
              "|AreaColor=16317695|SnapGridOn=T|SnapGridSize=10|VisibleGridOn=T"
              "|VisibleGridSize=10|CustomX=1000|CustomY=800|UseCustomSheet=F"
              "|ReferenceZonesOn=T|Display_Unit=4"
              f"|CompCount={len(by_name)}")
    for index, name in enumerate(sorted(by_name)):
        header += (f"|LibRef{index}={name}"
                   f"|CompDescr{index}={by_name[name].description}"
                   f"|PartCount{index}=2")

    entries: dict[str, bytes] = {
        "FileHeader": _param_block(header),
        "Storage": _param_block("|HEADER=Icon storage"),
    }
    for name, records in bodies.items():
        entries[f"{name}/Data"] = b"".join(records)
    return write_cfb(entries)
