"""PackageModel + pin table → Eagle `.lbr` (XML library).

Eagle is the one non-KiCad target with a published, text-based library format, so it
is the only one Ohm can emit without reverse-engineering a binary container.

Two conventions differ from KiCad and are handled here, not by the caller:
  * Eagle's Y axis points UP; PackageModel uses KiCad's Y-down. Every y is negated.
  * Eagle has no rounded-rect primitive — pad corner rounding is `roundness` (percent),
    and a circular pad is simply roundness=100.

Units are millimetres, same as PackageModel, so no scaling is involved.

The symbol geometry comes from `symbol.symbol_layout`, the same function the KiCad
emitter uses, so the two symbols cannot drift apart.
"""

from __future__ import annotations

from decimal import Decimal

from ohm.pkggen.packages import PackageModel
from ohm.pkggen.symbol import BOX_HALF_W, symbol_layout

_EAGLE_VERSION = "6.3"  # the schema revision that introduced XML libraries

# Eagle layer numbers we emit on.
_L_TOP, _L_PLACE, _L_NAMES, _L_VALUES = 1, 21, 25, 27
_L_KEEPOUT, _L_SYMBOL, _L_SYM_NAMES, _L_SYM_VALUES = 39, 94, 95, 96

# Eagle only understands the layers its header declares, so ship exactly ours.
_LAYERS = (
    (_L_TOP, "Top", 4), (_L_PLACE, "tPlace", 7), (_L_NAMES, "tNames", 7),
    (_L_VALUES, "tValues", 7), (_L_KEEPOUT, "tKeepout", 4),
    (_L_SYMBOL, "Symbols", 4), (_L_SYM_NAMES, "Names", 7), (_L_SYM_VALUES, "Values", 7),
)

# Ohm role -> Eagle pin direction. Eagle's closed set is
# nc/in/out/io/oc/pwr/pas/hiz/sup; it has no open_drain (oc covers it) and no
# `free` (pas is the honest fallback — it disables no check that oc/in/out would).
_DIRECTION = {
    "input": "in", "output": "out", "bidirectional": "io",
    "power_in": "pwr", "power_out": "sup", "ground": "pwr",
    "passive": "pas", "nc": "nc",
    "open_collector": "oc", "open_drain": "oc", "free": "pas",
}

_SILK_W = Decimal("0.12")
_KEEPOUT_W = Decimal("0.05")
_TEXT_SIZE = Decimal("1.27")


def _esc(value: str) -> str:
    return (value.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _roundness(shape: str) -> int:
    return {"circle": 100, "roundrect": 25}.get(shape, 0)


def _rect(x1, y1, x2, y2, layer, width) -> list[str]:
    """Eagle has no footprint rectangle primitive — four wires."""
    corners = ((x1, y1), (x2, y1), (x2, y2), (x1, y2))
    return [f'<wire x1="{a[0]}" y1="{a[1]}" x2="{b[0]}" y2="{b[1]}"'
            f' width="{width}" layer="{layer}"/>'
            for a, b in zip(corners, corners[1:] + corners[:1])]


def _package(model: PackageModel) -> list[str]:
    hw = model.courtyard_w / 2
    hh = model.courtyard_h / 2
    body = [f'<package name="{_esc(model.name)}">']
    # courtyard -> tKeepout, which is what Eagle's DRC uses for placement clearance
    body += _rect(-hw, -hh, hw, hh, _L_KEEPOUT, _KEEPOUT_W)
    body += _rect(-hw + _SILK_W, -hh + _SILK_W, hw - _SILK_W, hh - _SILK_W,
                  _L_PLACE, _SILK_W)
    for pad in model.pads:
        r = _roundness(pad.shape)
        extra = f' roundness="{r}"' if r else ""
        # y negated: PackageModel is KiCad Y-down, Eagle is Y-up
        body.append(f'<smd name="{_esc(pad.id)}" x="{pad.x}" y="{-pad.y}"'
                    f' dx="{pad.w}" dy="{pad.h}" layer="{_L_TOP}"{extra}/>')
    body.append(f'<text x="{-hw}" y="{hh + _TEXT_SIZE / 2}" size="{_TEXT_SIZE}"'
                f' layer="{_L_NAMES}">&gt;NAME</text>')
    body.append(f'<text x="{-hw}" y="{-hh - _TEXT_SIZE}" size="{_TEXT_SIZE}"'
                f' layer="{_L_VALUES}">&gt;VALUE</text>')
    body.append("</package>")
    return body


def _symbol(name: str, pins: list[tuple[str, str, str]]) -> list[str]:
    box_h, placed = symbol_layout(pins)
    body = [f'<symbol name="{_esc(name)}">']
    body += _rect(-BOX_HALF_W, -box_h, BOX_HALF_W, box_h, _L_SYMBOL, Decimal("0.254"))
    for number, pin_name, role, x, y, side in placed:
        # (x, y) is the connection point; rotation 0 extends the pin toward +x and
        # 180 toward -x, so each pin runs from its connect point back to the box edge
        angle = 0 if side == "left" else 180
        body.append(f'<pin name="{_esc(pin_name)}" x="{x}" y="{y}" length="short"'
                    f' direction="{_DIRECTION.get(role, "pas")}" rot="R{angle}"/>')
    body.append(f'<text x="{-BOX_HALF_W}" y="{box_h + _TEXT_SIZE / 2}"'
                f' size="{_TEXT_SIZE}" layer="{_L_SYM_NAMES}">&gt;NAME</text>')
    body.append(f'<text x="{-BOX_HALF_W}" y="{-box_h - _TEXT_SIZE}"'
                f' size="{_TEXT_SIZE}" layer="{_L_SYM_VALUES}">&gt;VALUE</text>')
    body.append("</symbol>")
    return body


def _deviceset(name: str, package: str, pins: list[tuple[str, str, str]],
               attrs: dict[str, str] | None) -> list[str]:
    connects = [f'<connect gate="G$1" pin="{_esc(pin_name)}" pad="{_esc(number)}"/>'
                for number, pin_name, _role in pins]
    props = [f'<attribute name="{_esc(k.upper())}" value="{_esc(str(v))}" constant="no"/>'
             for k, v in sorted((attrs or {}).items()) if str(v)]
    tech = (["<technology name=\"\">", *props, "</technology>"] if props
            else ['<technology name=""/>'])
    return [
        f'<deviceset name="{_esc(name)}" prefix="U">',
        "<gates>",
        f'<gate name="G$1" symbol="{_esc(name)}" x="0" y="0"/>',
        "</gates>",
        "<devices>",
        f'<device name="" package="{_esc(package)}">',
        "<connects>", *connects, "</connects>",
        "<technologies>", *tech, "</technologies>",
        "</device>",
        "</devices>",
        "</deviceset>",
    ]


def emit_eagle_library(model: PackageModel, name: str,
                       pins: list[tuple[str, str, str]],
                       attrs: dict[str, str] | None = None) -> str:
    """pins: [(pad_number, pin_name, role)] — the same table emit_symbol takes."""
    layers = [f'<layer number="{n}" name="{nm}" color="{c}" fill="1"'
              ' visible="yes" active="yes"/>' for n, nm, c in _LAYERS]
    out = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<!DOCTYPE eagle SYSTEM "eagle.dtd">',
        f'<eagle version="{_EAGLE_VERSION}">',
        "<drawing>",
        '<settings><setting alwaysvectorfont="no"/></settings>',
        '<grid distance="2.54" unitdist="mm" unit="mm" style="lines"'
        ' multiple="1" display="no"/>',
        "<layers>", *layers, "</layers>",
        "<library>",
        "<packages>", *_package(model), "</packages>",
        "<symbols>", *_symbol(name, pins), "</symbols>",
        "<devicesets>", *_deviceset(name, model.name, pins, attrs), "</devicesets>",
        "</library>",
        "</drawing>",
        "</eagle>",
    ]
    return "\n".join(out) + "\n"
