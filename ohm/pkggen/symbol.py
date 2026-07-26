"""P11: pin table → KiCad 7 `.kicad_sym`. Deterministic layout arithmetic:
first half of the pins down the left side, second half up the right."""

from __future__ import annotations

from decimal import Decimal

_SYM_VERSION = 20220914  # KiCad 7 symbol format

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
BOX_HALF_W = Decimal("7.62")
PIN_LEN = _GRID


def symbol_layout(pins: list[tuple[str, str, str]]):
    """Shared box-symbol geometry: first half down the left, second half up the right.

    Returns (box_half_h, placed) where placed is
    [(number, pin_name, role, connect_x, connect_y, side)] and side is "left"/"right".
    Both the KiCad and Eagle emitters read this, so the two symbols cannot drift.
    """
    half = (len(pins) + 1) // 2
    left, right = pins[:half], pins[half:]
    rows = max(len(left), len(right), 1)
    box_h = (_GRID * (rows + 1)) / 2  # half-height, keeps pins on the 2.54 grid
    stand_off = BOX_HALF_W + PIN_LEN

    placed = []
    for column, x, side in ((left, -stand_off, "left"), (right, stand_off, "right")):
        for i, (number, pin_name, role) in enumerate(column):
            placed.append((number, pin_name, role, x, box_h - _GRID * (i + 1), side))
    return box_h, placed


def emit_symbol(name: str, pins: list[tuple[str, str, str]],
                footprint: str | None = None,
                attrs: dict[str, str] | None = None) -> str:
    """pins: [(number, pin_name, role)] — one schematic unit, box style.

    `footprint` is the "<lib>:<name>" id written into the Footprint property. Without
    it the symbol arrives unassigned and cannot be pushed to a board, so callers that
    also emit a footprint should always pass it. `attrs` supplies BOM/sourcing
    properties (see _BOM_PROPS); unknown keys are ignored.
    """
    box_h, placed = symbol_layout(pins)
    prop_y = box_h + _GRID

    pin_lines = [
        f"      (pin {_ETYPE.get(role, 'passive')} line (at {x} {y}"
        f" {0 if side == 'left' else 180}) (length {PIN_LEN})"
        f' (name "{pin_name}" (effects (font (size 1.27 1.27))))'
        f' (number "{number}" (effects (font (size 1.27 1.27)))))'
        for number, pin_name, role, x, y, side in placed
    ]

    extra = [(label, str(attrs[key])) for key, label in _BOM_PROPS
             if attrs and attrs.get(key) not in (None, "")]

    out = [
        "(kicad_symbol_lib",
        f"  (version {_SYM_VERSION})",
        '  (generator "ohm")',
        f'  (symbol "{name}" (pin_names (offset 1.016)) (in_bom yes) (on_board yes)',
        f'    (property "Reference" "U" (at 0 {prop_y} 0)'
        " (effects (font (size 1.27 1.27))))",
        f'    (property "Value" "{name}" (at 0 -{prop_y} 0)'
        " (effects (font (size 1.27 1.27))))",
        f'    (property "Footprint" "{_escape(footprint or "")}" (at 0 0 0)'
        " (effects (font (size 1.27 1.27)) hide))",
        *[f'    (property "{label}" "{_escape(value)}" (at 0 0 0)'
          " (effects (font (size 1.27 1.27)) hide))" for label, value in extra],
        f'    (symbol "{name}_0_1"',
        f"      (rectangle (start -{BOX_HALF_W} {box_h}) (end {BOX_HALF_W} -{box_h})"
        " (stroke (width 0.254) (type default)) (fill (type background)))",
        "    )",
        f'    (symbol "{name}_1_1"',
        *pin_lines,
        "    )",
        "  )",
        ")",
    ]
    return "\n".join(out) + "\n"
