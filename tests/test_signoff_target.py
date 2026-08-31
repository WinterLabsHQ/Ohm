"""`ohm signoff --target-kicad`: the last gate must read the board's pad names.

The defect this pins. `ohm signoff` compiles the source and verifies a ROUTED
board against the resulting IR-LOW. It used to compile with NO target, so
`ohm/lower.py:_pad_translator` was the identity and the source model kept the
pad ids exactly as declared. A board laid out in KiCad 10 does not: KiCad 10
normalised shield and shell pads to "SH" across 163 footprints (ADR-021), so
`pin SHIELD: pad=11` on the source side faced a pad called "SH" on the board
side. `ohm/signoff.py`'s V001 loop skips a source pad the board does not carry —
correctly, because a pad-less stub footprint is not a mismatch — so the two
never met and the shield was not checked AT ALL. That is worse than a false
V001: a shield left genuinely unconnected by the layout sailed through the last
check before fabrication, silently.

So the test that matters is not "the flag is accepted". It is: a board with a
REAL defect on a renamed pad must be caught with the flag, and (today, without
it) must not be. The second half is the bug being demonstrated, not endorsed.

Everything here reads the pad table shipped inside `ohm.fpdata` and the boards
this repo emits — no KiCad, no network (`--no-geometry` keeps the V007 gate,
which shells out to kicad-cli, out of the way).
"""

from pathlib import Path

from ohm import fpdata
from ohm.cli import main
from ohm.driver import compile_source
from ohm.emit_kicad import emit_kicad_pcb
from tests.test_signoff import net_id, route

# Real cases from KiCad 10's shield normalisation. The pair actually used is
# picked out of the shipped table at run time (see `_renamed_case`) rather than
# fixed here, so a regenerated `ohm/fpdata/pads.json.gz` cannot leave this test
# asserting against a rename the table no longer contains.
CANDIDATES = (
    ("Connector_Card:microSD_HC_Hirose_DM3AT-SF-PEJM5", "11"),
    ("Connector_Card:microSD_HC_Molex_104031-0811", "11"),
    ("Connector_Card:SD_TE_2041021", "12"),
)

TARGET = "10"        # the KiCad that did the renaming
UNTARGETED = "7"     # what signoff resolves to with no flag and no ohm.toml


def _renamed_case() -> tuple[str, str, str]:
    """(footprint, declared pad, the pad KiCad 10 calls it) from `ohm.fpdata`.

    Also insists the pad is NOT renamed for the untargeted default — otherwise
    both halves of the end-to-end test would translate and the flag would prove
    nothing.
    """
    for fp, pad in CANDIDATES:
        if not fpdata.known(fp):
            continue
        new = fpdata.renamed_to(fp, pad, TARGET)
        if not new or new == pad:
            continue
        if pad not in (fpdata.pads(fp, TARGET) or ()) \
                and fpdata.renamed_to(fp, pad, UNTARGETED) is None:
            return fp, pad, new
    raise AssertionError(
        "no renamed pad left in ohm/fpdata: this test can no longer exercise the "
        f"translation `ohm signoff --target-kicad` exists for (tried {CANDIDATES})")


FOOTPRINT, DECLARED_PAD, TARGET_PAD = _renamed_case()

# Two pins on one net, one of them the pad KiCad renamed. A pin on no net never
# reaches the netlist, so the shield has to be wired to something for there to be
# an emitted pad to verify at all.
SRC = f"""component MicroSD:
    package = "{FOOTPRINT}"
    designator_prefix = "J"
    pin VSS: role=ground, pad=6
    pin SHIELD: role=ground, pad={DECLARED_PAD}

module Board:
    j1 = MicroSD()
    j1.SHIELD ~ j1.VSS
"""

NET = "J1_SHIELD"  # lower() names the net after the lexicographically first pin
ANCHOR = '    (property "ohm_path" "j1")\n'


def _pad(pad_id: str, x: str, net: str | None) -> str:
    """One KiCad pad inside the footprint block, optionally bound to a net."""
    bind = f' (net {net} "{NET}")' if net else ""
    return (f'    (pad "{pad_id}" smd roundrect (at {x} 0) (size 1 1)'
            f' (layers "F.Cu" "F.Paste" "F.Mask") (roundrect_rratio 0.25)'
            f'{bind})\n')


def _laid_out_on_kicad10(shield_connected: bool) -> str:
    """A board as KiCad 10 would have saved it after the user hydrated the stub.

    A `package = "Lib:Footprint"` part is emitted pad-LESS on purpose (the
    geometry comes from the user's installed library), so the pads a routed
    board carries have to be put there the way KiCad puts them there: named in
    KiCad 10's vocabulary, which for the shield means "SH" and not "11".
    """
    result = compile_source(SRC, "sd.ohm")
    assert result.ok, [d.machine() for d in result.diagnostics]
    pcb = emit_kicad_pcb(result.low, "sd.ohm")
    nid = net_id(pcb, NET)
    pads = (_pad(TARGET_PAD, "-2", nid if shield_connected else None)
            + _pad("6", "2", nid))
    pcb = pcb.replace(ANCHOR, ANCHOR + pads, 1)
    assert pads in pcb, "pad injection anchor moved; the board is not hydrated"
    if shield_connected:
        pcb = route(pcb, NET, [("j1", TARGET_PAD), ("j1", "6")])
    return pcb


def _project(tmp_path: Path, shield_connected: bool,
             target: str | None = None) -> tuple[str, str]:
    src = tmp_path / "sd.ohm"
    src.write_text(SRC, encoding="utf-8")
    board = tmp_path / "sd.kicad_pcb"
    board.write_text(_laid_out_on_kicad10(shield_connected), encoding="utf-8")
    if target is not None:
        (tmp_path / "ohm.toml").write_text(
            f'[project]\nname = "sd"\nentry = "sd.ohm"\ntarget = "{target}"\n',
            encoding="utf-8")
    return str(src), str(board)


# ---------- the defect ----------
def test_the_renamed_pad_is_verified_only_when_the_target_is_named(tmp_path, capsys):
    """The whole point: a shield the layout left unconnected on a KiCad 10 board.

    With `--target-kicad 10` the source model says "SH", meets the board's "SH",
    finds it bound to no net and fires V001. Without the target the model still
    says "11", the board has no such pad, and V001 skips it as if it were a stub
    — a clean verdict on a board whose shield is not connected. Remove the flag
    from the CLI, or stop threading it into compile_source, and the first
    assertion below fails.
    """
    src, board = _project(tmp_path, shield_connected=False)

    assert main(["signoff", src, board, "--no-geometry",
                 "--target-kicad", TARGET]) == 1
    err = capsys.readouterr().err
    assert "V001" in err and f"j1.{TARGET_PAD}" in err, err

    assert main(["signoff", src, board, "--no-geometry"]) == 0
    err = capsys.readouterr().err
    assert "V001" not in err, err


def test_a_good_kicad10_board_signs_off_clean_with_the_target(tmp_path, capsys):
    """The other side of the same coin — the flag must not manufacture a V001.

    The shield IS bound and routed here, in KiCad 10's spelling, so naming the
    target has to reach a clean verdict; if translation went the wrong way (or
    renamed into a pad the target does not have) this is where it shows.
    """
    src, board = _project(tmp_path, shield_connected=True)
    assert main(["signoff", src, board, "--no-geometry",
                 "--target-kicad", TARGET]) == 0
    assert "V001" not in capsys.readouterr().err


def test_signoff_needs_no_change_to_consume_the_translated_pads(tmp_path):
    """`ohm/signoff.py` reads IR-LOW and nothing else, so threading the target
    through the driver is the entire fix — this asserts that directly, at the
    level signoff actually works on, rather than trusting the CLI exit code."""
    from ohm.signoff import signoff

    low = compile_source(SRC, "sd.ohm", target_kicad=TARGET).low
    assert {n.pad for net in low.nets for n in net.nodes} == {TARGET_PAD, "6"}
    board = _laid_out_on_kicad10(shield_connected=False)
    assert [d for d in signoff(low, board, "sd.kicad_pcb") if d.code == "V001"]

    untargeted = compile_source(SRC, "sd.ohm", target_kicad=None).low
    assert {n.pad for net in untargeted.nets for n in net.nodes} == {DECLARED_PAD, "6"}
    assert not [d for d in signoff(untargeted, board, "sd.kicad_pcb")
                if d.code == "V001"]


# ---------- CLI surface ----------
def test_signoff_accepts_the_whole_supported_range(tmp_path, capsys):
    from ohm.cli import SUPPORTED_KICAD

    src, board = _project(tmp_path, shield_connected=True)
    for major in SUPPORTED_KICAD:
        # only the KiCad that renamed the pad reads this board as fully bound;
        # the older majors look for the old id and skip it, which is not an error
        rc = main(["signoff", src, board, "--no-geometry", "--target-kicad", major])
        assert rc == 0, f"target {major}: {capsys.readouterr().err}"
        capsys.readouterr()


def test_signoff_refuses_an_unknown_target_the_way_build_does(capsys):
    """Same validation, same words — `signoff` must not grow its own dialect."""
    assert main(["signoff", "nope.ohm", "nope.kicad_pcb",
                 "--target-kicad", "99"]) == 1
    err = capsys.readouterr().err
    assert "unknown KiCad target '99'" in err and "7, 8, 9, 10" in err
    # and it fails on the TARGET, before ever trying to read the (absent) source
    assert "cannot read" not in err


def test_signoff_honors_the_project_target(tmp_path, capsys):
    """`target = "kicad10"` in ohm.toml is how a team records which KiCad the
    boards are laid out in; signoff has to obey it or the project file is a lie
    at exactly the moment it matters most."""
    src, board = _project(tmp_path, shield_connected=False, target=f"kicad{TARGET}")
    assert main(["signoff", src, board, "--no-geometry"]) == 1
    assert "V001" in capsys.readouterr().err


def test_an_explicit_target_beats_the_project_file(tmp_path, capsys):
    """Precedence, stated as a test: the flag wins, ohm.toml is the fallback."""
    src, board = _project(tmp_path, shield_connected=False, target=f"kicad{TARGET}")
    assert main(["signoff", src, board, "--no-geometry", "--target-kicad", "9"]) == 0
    assert "V001" not in capsys.readouterr().err
