"""P11: pin table → KiCad 7 `.kicad_sym`. Deterministic layout arithmetic:
first half of the pins down the left side, second half up the right."""

from __future__ import annotations

from decimal import Decimal

_SYM_VERSION = 20220914  # KiCad 7 symbol format

_ETYPE = {
    "input": "input", "output": "output", "bidirectional": "bidirectional",
    "power_in": "power_in", "power_out": "power_out", "passive": "passive",
    "ground": "power_in", "nc": "no_connect",
}
_GRID = Decimal("2.54")


def emit_symbol(name: str, pins: list[tuple[str, str, str]]) -> str:
    """pins: [(number, pin_name, role)] — one schematic unit, box style."""
    half = (len(pins) + 1) // 2
    left, right = pins[:half], pins[half:]
    rows = max(len(left), len(right), 1)
    box_h = (_GRID * (rows + 1)) / 2  # half-height, keeps pins on the 2.54 grid
    prop_y = box_h + _GRID

    def pin_lines(column, x, angle):
        lines = []
        for i, (number, pin_name, role) in enumerate(column):
            y = box_h - _GRID * (i + 1)
            etype = _ETYPE.get(role, "passive")
            lines.append(
                f"      (pin {etype} line (at {x} {y} {angle}) (length 2.54)"
                f' (name "{pin_name}" (effects (font (size 1.27 1.27))))'
                f' (number "{number}" (effects (font (size 1.27 1.27)))))'
            )
        return lines

    out = [
        "(kicad_symbol_lib",
        f"  (version {_SYM_VERSION})",
        '  (generator "ohm")',
        f'  (symbol "{name}" (pin_names (offset 1.016)) (in_bom yes) (on_board yes)',
        f'    (property "Reference" "U" (at 0 {prop_y} 0)'
        " (effects (font (size 1.27 1.27))))",
        f'    (property "Value" "{name}" (at 0 -{prop_y} 0)'
        " (effects (font (size 1.27 1.27))))",
        f'    (property "Footprint" "" (at 0 0 0)'
        " (effects (font (size 1.27 1.27)) hide))",
        f'    (symbol "{name}_0_1"',
        f"      (rectangle (start -7.62 {box_h}) (end 7.62 -{box_h})"
        " (stroke (width 0.254) (type default)) (fill (type background)))",
        "    )",
        f'    (symbol "{name}_1_1"',
        *pin_lines(left, Decimal("-10.16"), 0),
        *pin_lines(right, Decimal("10.16"), 180),
        "    )",
        "  )",
        ")",
    ]
    return "\n".join(out) + "\n"
