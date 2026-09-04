"""D-tier: the drawing gate, and the false positives that would have killed it.

The point of this tier is that a *geometric* defect gets caught the way a
connectivity defect already does. The first version reported thousands of
collisions on a clean board — library definitions colliding with each other, and
every part's own reference sitting over its own body — which is worse than no
check at all, because a check that cries wolf is a check nobody reads. These
cases pin both halves: it finds the real ones, and it stays quiet about the
things that are correct by construction.
"""

import pathlib

import pytest

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


# ---- the envelope is the BODY, per axis ----
TALL = HEAD.replace(
    '(rectangle (start -5.08 5.08) (end 5.08 -5.08)',
    '(rectangle (start -5.08 63.5) (end 5.08 -63.5)').replace(
    '(symbol "ohm:Part"', '(symbol "ohm:Tall"').replace(
    '"Part_0_1"', '"Tall_0_1"').replace('"Part_1_1"', '"Tall_1_1"').replace(
    '"Value" "Part"', '"Value" "Tall"')


def _tall(x: str, y: str, uid: str, rot: str = "0", ref: str = "U9") -> str:
    return (f'  (symbol (lib_id "ohm:Tall") (at {x} {y} {rot})'
            " (unit 1) (in_bom yes) (on_board yes) (dnp no)\n"
            f'    (uuid "{uid}")\n'
            f'    (property "Reference" "{ref}" (at {x} {y} 0)'
            " (effects (font (size 1.27 1.27))))\n"
            "  )")


def _tall_sheet(*body: str) -> str:
    return TALL + "\n".join(body) + "\n)\n"


def test_a_tall_narrow_symbol_does_not_claim_the_sheet_beside_it():
    """A 10x127mm part is not a 127x127mm square. Measured as a square it
    reported every label in the 60mm of empty sheet either side of a 100-pin
    MCU as printing over it — which was most of what D002 said on a big sheet,
    and none of it was the drawing."""
    sch = _tall_sheet(
        _tall("100", "100", "bbbbbbbb-0000-0000-0000-00000000000a"),
        # 30mm to the left: far outside the 5.08mm half-width, well inside
        # the 63.5mm half-height a square envelope would have claimed
        _label("SOME_NET", "70", "100", "cccccccc-0000-0000-0000-00000000000a"))
    assert "D002" not in _codes(sch)


def test_a_label_actually_over_the_tall_body_is_still_reported():
    """The narrower envelope must not blind the check: directly on the body
    still collides."""
    sch = _tall_sheet(
        _tall("100", "100", "bbbbbbbb-0000-0000-0000-00000000000b"),
        _label("SOME_NET", "98", "60", "cccccccc-0000-0000-0000-00000000000b"))
    assert "D002" in _codes(sch)


def test_a_turned_symbol_swaps_its_axes():
    """At 90 degrees the tall part is WIDE, so the same label 30mm to the side
    is now genuinely over it. A square envelope could never tell these apart."""
    sch = _tall_sheet(
        _tall("100", "100", "bbbbbbbb-0000-0000-0000-00000000000c", rot="90"),
        _label("SOME_NET", "70", "100", "cccccccc-0000-0000-0000-00000000000c"))
    assert "D002" in _codes(sch)
# ---------- the D tier reaches the diagnostic list, not just stderr ----------
# `check_drawing` needs the EMITTED schematic, so it cannot join `driver.CHECKERS`.
# It used to run after `_report` and after the `--strict` gate, printing straight
# to stderr: a D warning was therefore absent from `--json-diagnostics`, invisible
# to `--strict`, and could not affect the exit code. `ohm/cli.py` now renders every
# text artifact into memory first, so the drawing gate's findings join the same
# list as every other tier before anything is reported, gated or written.
EXAMPLE = "examples/io_expander_board.ohm"


@pytest.fixture
def one_drawing_warning(monkeypatch):
    """Make the drawing gate report exactly one D001, whatever the sheet says.

    These three cases are about the PIPELINE — that a D finding reaches
    `--json-diagnostics`, `--strict` and the exit code — not about any
    particular sheet's geometry. They used to get their warning from
    `examples/io_expander_board.ohm`, which really did overlap two pieces of
    text; when the emitter was fixed so that it does not, all three failed. A
    plumbing test that only passes while a shipped example is still defective
    is a test that punishes the fix, so the warning is injected instead. The
    geometry itself is proved by the sheet cases above and by the corpus-wide
    sweep in `test_lower_emit.py`.
    """
    from ohm.diagnostics import Diagnostic, Span

    def one(sch: str, path: str = "<string>"):
        return [Diagnostic("warning", "D001", "1 pair(s) of text overlap: 'A' / 'B'",
                           Span(1, 1, 1, 1), file=path, help="injected by a test")]

    monkeypatch.setattr("ohm.check_drawing.check_drawing", one)


def test_strict_fails_on_a_drawing_warning_and_writes_nothing(
        tmp_path, capsys, one_drawing_warning):
    """`--strict` promises "exit 1 (and emit nothing) on any warning or worse".

    Measured before the fix: this exact command printed `warning[D001]` and
    exited 0, with every file written. Both halves of the promise were false for
    the whole D tier.
    """
    from ohm.cli import main

    out = tmp_path / "out"
    rc = main(["build", EXAMPLE, "--emit", "kicad", "-o", str(out), "--strict"])
    err = capsys.readouterr().err
    assert "D001" in err, err
    assert rc == 1
    assert not out.exists() or not list(out.iterdir()), "emitted despite --strict"


def test_json_diagnostics_is_one_document_and_carries_the_d_codes(
        tmp_path, capsys, one_drawing_warning):
    """AGENTS.md tells agents to iterate on `--json-diagnostics`; a D code that
    only ever reached stderr was unreachable from that loop.

    One document, not two: printing a second array after the emit would break
    the agent loop as thoroughly as printing nothing.
    """
    import json

    from ohm.cli import main

    out = tmp_path / "out"
    assert main(["build", EXAMPLE, "--emit", "kicad", "-o", str(out),
                 "--json-diagnostics"]) == 0
    doc = json.loads(capsys.readouterr().out)  # raises if it is not ONE document
    assert any(d["code"].startswith("D") for d in doc), [d["code"] for d in doc]


def test_a_plain_build_still_succeeds_with_a_drawing_warning(
        tmp_path, capsys, one_drawing_warning):
    """The non-strict contract does not change: D codes are advisory, they are
    reported, and they do not fail a build."""
    from ohm.cli import main

    out = tmp_path / "out"
    assert main(["build", EXAMPLE, "--emit", "kicad", "-o", str(out)]) == 0
    assert "D001" in capsys.readouterr().err
    assert (out / "io_expander_board.kicad_pcb").exists()


def test_a_drawing_diagnostic_is_not_quoted_against_the_ohm_source(
        tmp_path, capsys, one_drawing_warning):
    """A D code is about the emitted `.kicad_sch`, so nothing from the `.ohm`
    may be printed under it.

    `_report` renders one source text for every diagnostic in the list. When the
    D tier joined that list it started being rendered against the `.ohm` — so a
    finding whose location read `out/led.kicad_sch:1:1` carried an excerpt of
    line 1 of `led.ohm` beneath it, with a caret, pointing the reader at a file
    the finding is not about.
    """
    from ohm.cli import main

    out = tmp_path / "out"
    assert main(["build", EXAMPLE, "--emit", "kicad", "-o", str(out)]) == 0
    err = capsys.readouterr().err
    assert "D001" in err
    first_line = pathlib.Path(EXAMPLE).read_text(encoding="utf-8").splitlines()[0]
    assert first_line.strip() not in err, err


def test_nothing_is_written_until_every_artifact_has_rendered(tmp_path, monkeypatch):
    """The other half of rendering first: a failure part-way through emitting
    must not leave a partly-written output directory.

    This is what the cyclic-`near` crash did — `emit_kicad_pcb` raised after the
    .kicad_sch had already been written, leaving a schematic and no board.
    """
    import ohm.cli as cli

    def boom(*a, **k):
        raise RuntimeError("emitter exploded")

    monkeypatch.setattr(cli, "emit_kicad_pcb", boom)
    out = tmp_path / "out"
    try:
        cli.main(["build", EXAMPLE, "--emit", "kicad", "-o", str(out)])
    except RuntimeError:
        pass
    assert not out.exists() or not list(out.iterdir())


# ---------- the corpus draws clean, and stays that way ----------
def test_every_shipped_design_draws_clean():
    """No D finding on any design this repository ships.

    The D tier exists because "every geometric defect in this emitter's history
    was found by rendering a PDF and squinting at it". Having built the gate,
    the corpus still failed it in four places, all of them the same shape: text
    Ohm placed itself, in a column something else had already claimed.

      examples/divider_led.ohm            D002  'PWR_GND' over Capacitor
      examples/io_expander_board.ohm      D001  'VIN_V' / '1uF'
      tests/examples/led_panel.ohm        D001  'Decoupling' / 'PWR_V'
      tests/examples/sensor_node_bme280   D001  'PWR_V' / '1uF'

    Each was fixed at the place that chose the position — the rider offset that
    reserves a wire's label width, the ladder head that now records its rail
    name as placed text, and the symbol properties that now ask `_free_spot`
    where there is room, exactly as the frame titles already did. This is the
    gate that keeps the corpus at zero rather than at "four known".
    """
    from ohm.driver import compile_source
    from ohm.emit_kicad import emit_kicad_sheets

    designs = sorted(pathlib.Path("examples").glob("*.ohm"))
    designs += sorted(pathlib.Path("tests/examples").glob("*.ohm"))
    designs += sorted(pathlib.Path("tests/golden").glob("*.ohm"))
    assert len(designs) > 20, "corpus went missing"

    findings = []
    for design in designs:
        result = compile_source(design.read_text(encoding="utf-8"), design.name)
        assert result.ok, (design.name, [d.machine() for d in result.diagnostics])
        for fname, text in emit_kicad_sheets(result.low, design.name,
                                             result.design).items():
            findings += [f"{design.name} -> {fname}: {d.message}"
                         for d in check_drawing(text, fname)]
    assert not findings, "\n".join(findings)
