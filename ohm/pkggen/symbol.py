"""P11: pin table → KiCad 7 `.kicad_sym`. Deterministic layout arithmetic:
first half of the pins down the left side, second half up the right."""

from __future__ import annotations

from decimal import ROUND_CEILING, Decimal

from ohm.formats import _SYM_VERSION

# role -> KiCad pin electrical type. ERC is only as good as this map, so every
# role in ohm.resolve.VALID_ROLES must appear: anything falling through to
# "passive" silently disables the checks the reviewer expects (e.g. two open-drain
# outputs on a net with no pull-up). KiCad has no open_drain type — open_collector
# is its equivalent and is what the stock libraries use.
_ETYPE = {
    "input": "input", "output": "output", "bidirectional": "bidirectional",
    "power_in": "power_in", "power_out": "power_out", "passive": "passive",
    "ground": "power_in", "nc": "no_connect",
    "open_collector": "open_collector", "open_drain": "open_collector",
    "free": "free",
}
_GRID = Decimal("2.54")

# Ohm-generated footprints are addressed as "<lib>:<footprint>". The nickname the
# user registers the .pretty under is theirs to choose; this is the documented
# default so the symbol arrives already linked instead of unassigned.
DEFAULT_FOOTPRINT_LIB = "ohm"

# component attr -> KiCad symbol property. Attrs are open-ended in the language,
# so these are picked up if present and skipped if not.
#
# The Sim.* group is what KiCad's simulator integration reads. Ohm never invents a
# model — these only carry through a model the caller already had, which is why
# they are plain attrs rather than anything the compiler derives.
_BOM_PROPS = (("datasheet", "Datasheet"), ("mpn", "MPN"),
              ("manufacturer", "Manufacturer"), ("description", "Description"),
              ("sim_device", "Sim.Device"), ("sim_type", "Sim.Type"),
              ("sim_library", "Sim.Library"), ("sim_name", "Sim.Name"),
              ("sim_pins", "Sim.Pins"), ("sim_params", "Sim.Params"))


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


# box half-width; pins stand off by one grid step beyond it
BOX_HALF_W = Decimal("7.62")  # minimum; widened per part by box_half_w()
PIN_LEN = _GRID
# Pin names are drawn INSIDE the box, left names growing right and right names
# growing left. At the 1.27mm text size KiCad renders roughly 0.85mm per glyph,
# so a fixed-width box lets a long pair collide in the middle: MPU-6000 rendered
# "AD0_MISO"/"SCL_SCLK" as "ADO_SCL_SCLK". Width now follows the longest pair.
# Measured off a KiCad render at the 1.27mm text size: 8 glyphs span ~10.8mm,
# i.e. ~1.35mm each including inter-character spacing.
_CHAR_W = Decimal("1.4")
_NAME_GAP = _GRID * 2  # clear space between the two columns of names


def box_half_w(pins: list[tuple[str, str, str]]) -> Decimal:
    """Half-width that fits the widest left name plus the widest right name."""
    half = (len(pins) + 1) // 2
    left, right = pins[:half], pins[half:]
    widest = max((len(n) for _, n, _ in left), default=0) + \
        max((len(n) for _, n, _ in right), default=0)
    need = (Decimal(widest) * _CHAR_W + _NAME_GAP) / 2
    if need <= BOX_HALF_W:
        return BOX_HALF_W
    return (need / _GRID).quantize(Decimal(1), rounding=ROUND_CEILING) * _GRID


# ---------- two-terminal glyphs ----------
# A resistor is not a box. Drawing every part as a labelled rectangle is what
# makes a generated sheet read as a netlist dump: a 2-pin passive takes 20mm and
# carries "P1"/"P2" text nobody needs, where the conventional glyph takes 7.62mm
# and says what the part IS at a glance. Geometry is KiCad's own Device library,
# transposed to horizontal (pins left/right) so it drops into the same placement
# and wiring the box symbols use. Kind -> glyph, most specific kind winning.
GLYPH_STAND = Decimal("3.81")  # pin connect point, ±x
_GLYPH_HALF_H = Decimal("2.54")  # for reference/value text placement
_GLYPH_ORDER = ("LED", "Zener", "Schottky", "TVS", "Diode", "Resistor",
                "Capacitor", "Inductor", "Crystal", "Fuse")
_GLYPH_OF_KIND = {
    "LED": "led", "Zener": "zener", "Schottky": "schottky", "TVS": "diode",
    "Diode": "diode", "Resistor": "resistor", "Capacitor": "capacitor",
    "Inductor": "inductor", "Crystal": "crystal", "Fuse": "fuse",
}
_S = "(stroke (width {w}) (type default)) (fill (type {f}))"
_GLYPH_BODY: dict[str, tuple[Decimal, list[str]]] = {
    # glyph -> (pin length, body s-expressions)
    "resistor": (Decimal("1.27"), [
        f"(rectangle (start -2.54 -1.016) (end 2.54 1.016) {_S.format(w='0.254', f='none')})"]),
    "capacitor": (Decimal("3.048"), [
        f"(polyline (pts (xy -0.762 -2.032) (xy -0.762 2.032)) {_S.format(w='0.508', f='none')})",
        f"(polyline (pts (xy 0.762 -2.032) (xy 0.762 2.032)) {_S.format(w='0.508', f='none')})"]),
    "capacitor_polarized": (Decimal("3.048"), [
        f"(rectangle (start -1.016 -2.286) (end -0.508 2.286) {_S.format(w='0', f='outline')})",
        f"(polyline (pts (xy 0.762 -2.032) (xy 0.762 2.032)) {_S.format(w='0.508', f='none')})",
        f"(polyline (pts (xy -2.286 1.778) (xy -2.286 0.762)) {_S.format(w='0', f='none')})",
        f"(polyline (pts (xy -2.794 1.27) (xy -1.778 1.27)) {_S.format(w='0', f='none')})"]),
    "inductor": (Decimal("1.27"), [
        f"(arc (start -2.54 0) (mid -1.905 0.6323) (end -1.27 0) {_S.format(w='0', f='none')})",
        f"(arc (start -1.27 0) (mid -0.635 0.6323) (end 0 0) {_S.format(w='0', f='none')})",
        f"(arc (start 0 0) (mid 0.635 0.6323) (end 1.27 0) {_S.format(w='0', f='none')})",
        f"(arc (start 1.27 0) (mid 1.905 0.6323) (end 2.54 0) {_S.format(w='0', f='none')})"]),
    "crystal": (Decimal("1.27"), [
        f"(rectangle (start -1.143 2.54) (end 1.143 -2.54) {_S.format(w='0.3048', f='none')})",
        f"(polyline (pts (xy -1.905 -1.27) (xy -1.905 1.27)) {_S.format(w='0.508', f='none')})",
        f"(polyline (pts (xy 1.905 -1.27) (xy 1.905 1.27)) {_S.format(w='0.508', f='none')})",
        f"(polyline (pts (xy -2.54 0) (xy -1.905 0)) {_S.format(w='0', f='none')})",
        f"(polyline (pts (xy 2.54 0) (xy 1.905 0)) {_S.format(w='0', f='none')})"]),
    "diode": (Decimal("2.54"), [
        f"(polyline (pts (xy 1.27 1.27) (xy 1.27 -1.27)) {_S.format(w='0.254', f='none')})",
        f"(polyline (pts (xy 1.27 0) (xy -1.27 0)) {_S.format(w='0', f='none')})",
        "(polyline (pts (xy -1.27 1.27) (xy -1.27 -1.27) (xy 1.27 0) (xy -1.27 1.27)) "
        f"{_S.format(w='0.254', f='none')})"]),
    "zener": (Decimal("2.54"), [
        f"(polyline (pts (xy 1.27 0) (xy -1.27 0)) {_S.format(w='0', f='none')})",
        "(polyline (pts (xy 1.27 -1.27) (xy 1.27 1.27) (xy 0.762 1.27)) "
        f"{_S.format(w='0.254', f='none')})",
        "(polyline (pts (xy -1.27 -1.27) (xy -1.27 1.27) (xy 1.27 0) (xy -1.27 -1.27)) "
        f"{_S.format(w='0.254', f='none')})"]),
    "schottky": (Decimal("2.54"), [
        f"(polyline (pts (xy 1.27 0) (xy -1.27 0)) {_S.format(w='0', f='none')})",
        "(polyline (pts (xy -1.27 1.27) (xy -1.27 -1.27) (xy 1.27 0) (xy -1.27 1.27)) "
        f"{_S.format(w='0.254', f='none')})",
        "(polyline (pts (xy 1.905 0.635) (xy 1.905 1.27) (xy 1.27 1.27) (xy 1.27 -1.27)"
        f" (xy 0.635 -1.27) (xy 0.635 -0.635)) {_S.format(w='0.254', f='none')})"]),
    "led": (Decimal("2.54"), [
        f"(polyline (pts (xy 1.27 -1.27) (xy 1.27 1.27)) {_S.format(w='0.254', f='none')})",
        f"(polyline (pts (xy 1.27 0) (xy -1.27 0)) {_S.format(w='0', f='none')})",
        "(polyline (pts (xy -1.27 1.27) (xy -1.27 -1.27) (xy 1.27 0) (xy -1.27 1.27)) "
        f"{_S.format(w='0.254', f='none')})",
        "(polyline (pts (xy 0.762 -3.048) (xy 2.286 -4.572) (xy 1.27 -4.318)"
        f" (xy 2.286 -4.572) (xy 2.032 -3.556)) {_S.format(w='0', f='none')})",
        "(polyline (pts (xy -0.762 -3.048) (xy 0.762 -4.572) (xy -0.254 -4.318)"
        f" (xy 0.762 -4.572) (xy 0.508 -3.556)) {_S.format(w='0', f='none')})"]),
    "fuse": (Decimal("1.27"), [
        f"(rectangle (start -2.54 -0.762) (end 2.54 0.762) {_S.format(w='0.254', f='none')})",
        f"(polyline (pts (xy -2.54 0) (xy 2.54 0)) {_S.format(w='0', f='none')})"]),
}
# glyphs whose ends are not interchangeable: the named pin goes on the LEFT, so
# a diode's triangle points at its cathode the way every schematic draws it
_GLYPH_ANODE_LEFT = {"diode", "led", "zener", "schottky", "capacitor_polarized"}


def glyph_for(kinds, pins: list[tuple[str, str, str]]) -> str | None:
    """The conventional two-terminal glyph a component's kinds call for, if any."""
    if len(pins) != 2:
        return None
    for kind in _GLYPH_ORDER:
        if kind in kinds:
            glyph = _GLYPH_OF_KIND[kind]
            if glyph == "capacitor" and "Polarized" in kinds:
                return "capacitor_polarized"
            return glyph
    return None


def symbol_layout(pins: list[tuple[str, str, str]], glyph: str | None = None):
    """Shared box-symbol geometry: first half down the left, second half up the right.

    Returns (box_half_h, placed) where placed is
    [(number, pin_name, role, connect_x, connect_y, side)] and side is "left"/"right".
    Both the KiCad and Eagle emitters read this, so the two symbols cannot drift.

    With `glyph` the part is a two-terminal symbol instead: one pin each side at
    ±GLYPH_STAND, so it places and wires exactly like a box, only far smaller.
    """
    if glyph is not None and len(pins) == 2:
        first, second = pins
        if glyph in _GLYPH_ANODE_LEFT and second[1][:1] in ("A", "+"):
            first, second = second, first
        return _GLYPH_HALF_H, [
            (first[0], first[1], first[2], -GLYPH_STAND, Decimal(0), "left"),
            (second[0], second[1], second[2], GLYPH_STAND, Decimal(0), "right")]
    half = (len(pins) + 1) // 2
    left, right = pins[:half], pins[half:]
    rows = max(len(left), len(right), 1)
    box_h = (_GRID * (rows + 1)) / 2  # half-height, keeps pins on the 2.54 grid
    stand_off = box_half_w(pins) + PIN_LEN

    placed = []
    for column, x, side in ((left, -stand_off, "left"), (right, stand_off, "right")):
        for i, (number, pin_name, role) in enumerate(column):
            placed.append((number, pin_name, role, x, box_h - _GRID * (i + 1), side))
    return box_h, placed


def emit_symbol(name: str, pins: list[tuple[str, str, str]],
                footprint: str | None = None,
                attrs: dict[str, str] | None = None,
                glyph: str | None = None) -> str:
    """pins: [(number, pin_name, role)] — one schematic unit, box style.

    `footprint` is the "<lib>:<name>" id written into the Footprint property. Without
    it the symbol arrives unassigned and cannot be pushed to a board, so callers that
    also emit a footprint should always pass it. `attrs` supplies BOM/sourcing
    properties (see _BOM_PROPS); unknown keys are ignored.
    """
    glyph = glyph if glyph in _GLYPH_BODY else None
    box_h, placed = symbol_layout(pins, glyph)
    half_w = box_half_w(pins)
    prop_y = box_h + _GRID
    pin_len, body = (_GLYPH_BODY[glyph] if glyph
                     else (PIN_LEN, [f"(rectangle (start -{half_w} {box_h})"
                                     f" (end {half_w} -{box_h})"
                                     " (stroke (width 0.254) (type default))"
                                     " (fill (type background)))"]))

    pin_lines = [
        f"      (pin {_ETYPE.get(role, 'passive')} line (at {x} {y}"
        f" {0 if side == 'left' else 180}) (length {pin_len})"
        f' (name "{pin_name}" (effects (font (size 1.27 1.27))))'
        f' (number "{number}" (effects (font (size 1.27 1.27)))))'
        for number, pin_name, role, x, y, side in placed
    ]
    # A glyph says what the part is; "P1"/"P2" and pad numbers on a 0402 are
    # noise, and they are what made every passive 20mm wide.
    hide = " (pin_numbers hide)" if glyph else ""
    names = "(pin_names (offset 0) hide)" if glyph else "(pin_names (offset 1.016))"

    extra = [(label, str(attrs[key])) for key, label in _BOM_PROPS
             if attrs and attrs.get(key) not in (None, "")]

    out = [
        "(kicad_symbol_lib",
        f"  (version {_SYM_VERSION})",
        '  (generator "ohm")',
        f'  (symbol "{name}"{hide} {names} (in_bom yes) (on_board yes)',
        f'    (property "Reference" "U" (at 0 {prop_y} 0)'
        " (effects (font (size 1.27 1.27))))",
        f'    (property "Value" "{name}" (at 0 -{prop_y} 0)'
        " (effects (font (size 1.27 1.27))))",
        f'    (property "Footprint" "{_escape(footprint or "")}" (at 0 0 0)'
        " (effects (font (size 1.27 1.27)) hide))",
        *[f'    (property "{label}" "{_escape(value)}" (at 0 0 0)'
          " (effects (font (size 1.27 1.27)) hide))" for label, value in extra],
        f'    (symbol "{name}_0_1"',
        *[f"      {shape}" for shape in body],
        "    )",
        f'    (symbol "{name}_1_1"',
        *pin_lines,
        "    )",
        "  )",
        ")",
    ]
    return "\n".join(out) + "\n"
