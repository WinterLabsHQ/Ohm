"""D-tier: the drawing gate, and the false positives that would have killed it.

The point of this tier is that a *geometric* defect gets caught the way a
connectivity defect already does. The first version reported thousands of
collisions on a clean board — library definitions colliding with each other, and
every part's own reference sitting over its own body — which is worse than no
check at all, because a check that cries wolf is a check nobody reads. These
cases pin both halves: it finds the real ones, and it stays quiet about the
things that are correct by construction.
"""

from ohm.check_drawing import check_drawing

HEAD = '''(kicad_sch
  (version 20230121)
  (generator "ohm")
  (uuid "aaaaaaaa-0000-0000-0000-000000000001")
  (paper "A4")
  (lib_symbols
    (symbol "ohm:Part" (pin_names (offset 1.016)) (in_bom yes) (on_board yes)
      (property "Reference" "U" (at 0 5.08 0) (effects (font (size 1.27 1.27))))
      (property "Value" "Part" (at 0 -5.08 0) (effects (font (size 1.27 1.27))))
      (symbol "Part_0_1"
        (rectangle (start -5.08 5.08) (end 5.08 -5.08) (stroke (width 0.254) (type default)) (fill (type background)))
      )
      (symbol "Part_1_1"
        (pin passive line (at -10.16 0 0) (length 2.54) (name "A" (effects (font (size 1.27 1.27)))) (number "1" (effects (font (size 1.27 1.27)))))
      )
    )
  )
'''


def _sheet(*body: str) -> str:
    return HEAD + "\n".join(body) + "\n)\n"


def _part(x: str, y: str, uid: str, ref: str = "U1") -> str:
    return (f'  (symbol (lib_id "ohm:Part") (at {x} {y} 0)'
            " (unit 1) (in_bom yes) (on_board yes) (dnp no)\n"
            f'    (uuid "{uid}")\n'
            f'    (property "Reference" "{ref}" (at {x} {y} 0)'
            " (effects (font (size 1.27 1.27))))\n"
            "  )")


def _label(text: str, x: str, y: str, uid: str, just: str = "left") -> str:
    return (f'  (label "{text}" (at {x} {y} 0)'
            f" (effects (font (size 1.27 1.27)) (justify {just} bottom))"
            f' (uuid "{uid}"))')


def _codes(sch: str) -> set[str]:
    return {d.code for d in check_drawing(sch)}


# ---------- it stays quiet when the drawing is fine ----------
def test_a_clean_sheet_reports_nothing():
    sch = _sheet(_part("50", "50", "bbbbbbbb-0000-0000-0000-000000000001"),
                 _label("NET_A", "100", "100", "cccccccc-0000-0000-0000-000000000001"))
    assert check_drawing(sch) == []


def test_library_definitions_are_not_placed_geometry():
    """Every lib entry declares a reference of "U" at its own origin. Reading
    those as drawn text made every library collide with every other."""
    sch = _sheet(_part("50", "50", "bbbbbbbb-0000-0000-0000-000000000002"))
    assert "D001" not in _codes(sch)


def test_a_parts_own_reference_does_not_collide_with_it():
    """Reference and value sit against the body on purpose."""
    sch = _sheet(_part("50", "50", "bbbbbbbb-0000-0000-0000-000000000003"))
    assert "D002" not in _codes(sch)


def test_a_part_touching_another_pin_is_not_an_overlap():
    """A rider sits against its host's pin, and a series chain sits against the
    part before it. Measuring symbols by their PINS rather than their bodies
    made every one of those read as a collision."""
    sch = _sheet(_part("50", "50", "bbbbbbbb-0000-0000-0000-000000000004"),
                 _part("64", "50", "bbbbbbbb-0000-0000-0000-000000000005", "U2"))
    assert "D003" not in _codes(sch)


# ---------- it finds the real ones ----------
def test_overlapping_text_is_reported():
    sch = _sheet(_label("VDD_3V3", "100", "100", "cccccccc-0000-0000-0000-000000000002"),
                 _label("Decoupling", "102", "100", "cccccccc-0000-0000-0000-000000000003"))
    assert "D001" in _codes(sch)


def test_text_over_a_symbol_body_is_reported():
    sch = _sheet(_part("50", "50", "bbbbbbbb-0000-0000-0000-000000000006"),
                 _label("VDD_5V_PERIPH", "48", "50", "cccccccc-0000-0000-0000-000000000004"))
    assert "D002" in _codes(sch)


def test_overlapping_symbols_are_reported():
    sch = _sheet(_part("50", "50", "bbbbbbbb-0000-0000-0000-000000000007"),
                 _part("52", "50", "bbbbbbbb-0000-0000-0000-000000000008", "U2"))
    assert "D003" in _codes(sch)


def test_duplicate_uuids_are_reported():
    same = "bbbbbbbb-0000-0000-0000-000000000009"
    sch = _sheet(_part("20", "20", same), _part("80", "80", same, "U2"))
    assert "D004" in _codes(sch)


def test_findings_are_advisory_and_never_block():
    """The netlist is proved exactly and separately; an ugly drawing is ugly,
    not wrong. A gate that refuses to emit over a text collision would stop
    people using the gate."""
    sch = _sheet(_label("A_LONG_NET_NAME", "100", "100", "cccccccc-0000-0000-0000-000000000005"),
                 _label("ANOTHER_ONE", "101", "100", "cccccccc-0000-0000-0000-000000000006"))
    found = check_drawing(sch)
    assert found and all(d.severity == "warning" for d in found)
