"""Signoff must read a board KiCad wrote, not only the board Ohm wrote.

`ohm signoff` is the last gate before fabrication, and by construction its input
is a file **KiCad has opened and re-saved** (`AGENTS.md` item 5: route, then sign
off). Every test in `tests/test_signoff.py` nevertheless feeds it
`emit_kicad_pcb()` output — Ohm reading bytes Ohm itself has just written. That
proves the checker's logic and nothing at all about the format it will actually
be handed.

The exposure is concrete. `ohm/signoff.py:read_board` identifies every part by a
custom property, `(property "ohm_path" "…")`, and takes the value as the third
element of that form. KiCad 8 rewrote footprint text properties into full field
forms carrying `(at …)`, `(layer …)`, `(uuid …)` and `(effects …)` children; the
value stayed in position 3, so the reader survives — today. If a future version
moves, renames or drops a property it does not recognise, signoff stops
identifying parts, and the failure mode is not a crash: it is a board that looks
foreign to Ohm (a wall of V001), or worse, an empty board that looks clean.
Silence in a fabrication go/no-go is the whole risk.

`kicad-cli pcb upgrade` is that re-save with no GUI and no `pcbnew` — KiCad reads
the file and writes it back out in its own native format, which is exactly what
the router leg of the workflow does:

    emit_kicad_pcb()  ->  kicad-cli pcb upgrade  ->  ohm.signoff.read_board()
                          (KiCad rewrites it)       same parts, pads, verdict?

Both an unrouted and a routed board go through it, because the two answers
signoff can give — "incomplete net" and "clean" — must each survive: a re-save
that turned a connected net into an incomplete one, or an incomplete one into a
clean board, is a defect either way.

That gate needs a KiCad, and so can only ever test the KiCads that exist. The
second half of this file (section 6) needs none: it takes a board Ohm emitted,
breaks ONE shape `read_board` stands on, and asserts Ohm answers "I could not
read this file" (V008) rather than a verdict about the layout. Hand-written
fixture text is the point — the release that breaks the next assumption has not
shipped yet, and all three that broke the earlier ones were found by a human
running a new KiCad rather than by a test.

Deliberately NOT asserted here: that KiCad prints coordinates the way Ohm does.
Pad and footprint identity is compared by path/pad-id/net, never by re-read
number formatting, because `1.0` for `1` is a serialiser's business while a net
binding moving is a real defect. Positions are compared separately, to the
reader's own 0.001 mm quantum, where a difference means a part actually moved.
"""

import os
import re
import shutil
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest

from ohm.signoff import read_board, signoff

from tests.conftest import KICAD_CLI, NOT_APPLICABLE, requires_kicad_cli
from tests.test_signoff import build, route

BOARD_FILE = "d.kicad_pcb"  # the same name for both readings: it appears in every diagnostic
QUANTUM = Decimal("0.001")  # ohm.signoff's own coordinate quantum, mm

# MEASURED, 2026-08-26, not taken from the docs: `kicad-cli pcb upgrade` exists
# only in KiCad **10** — 9.0.9 and 8.0.9 answer `kicad-cli pcb` with drc, export
# and render and nothing else. In 10.0.5 it takes `--force` and rewrites the file
# IN PLACE; there is no `--output`. The forms below are therefore tried in order
# and fall back only when the CLI rejects the ARGUMENTS — never when it rejects
# the board, which must surface as a failure. When no form works at all, the
# gate re-saves through `pcbnew` instead (see `_resave_via_pcbnew`), which every
# supported version ships.
_FORMS: tuple[tuple[str, ...], ...] = (
    ("--force", "--output", "{out}", "{src}"),
    ("--output", "{out}", "{src}"),
    ("--force", "{src}"),
    ("{src}",),
)

_USAGE_SHAPED = ("unknown command", "invalid command", "unrecognized", "unrecognised",
                 "no such command", "usage:", "unknown argument", "unknown option",
                 "invalid option", "not a valid")


def _usage_error(blob: str) -> bool:
    """Did kicad-cli reject the ARGUMENTS rather than the board?"""
    low = blob.lower()
    return any(m in low for m in _USAGE_SHAPED)


def _resave_via_pcbnew(src: Path, dst: Path, attempts: list[str]) -> str:
    """Re-save `src` through KiCad's own `pcbnew`, the way the GUI would.

    This is the same round trip `pcb upgrade` performs — KiCad parses the board
    into its data model and serialises it back — and it is available on every
    KiCad that ships Python bindings, which `pcb upgrade` is not.

    `$OHM_KICAD_PYTHON` names the interpreter to use; otherwise the system
    `python3`, since KiCad installs pcbnew there rather than into a virtualenv.
    """
    py = os.environ.get("OHM_KICAD_PYTHON") or shutil.which("python3")
    script = ("import pcbnew, sys\n"
              "b = pcbnew.LoadBoard(sys.argv[1])\n"
              "pcbnew.SaveBoard(sys.argv[2], b)\n")
    if py is None:
        pytest.skip(f"{NOT_APPLICABLE} no kicad-cli 'pcb upgrade' (KiCad 10+ only) and no "
                    f"python3 to reach pcbnew:\n" + "\n".join(attempts))
    proc = subprocess.run([py, "-c", script, str(src), str(dst)],
                          capture_output=True, text=True, timeout=300)
    if proc.returncode != 0 or not dst.exists() or dst.stat().st_size == 0:
        blob = (proc.stderr + proc.stdout).strip()
        if "No module named" in blob or "ImportError" in blob:
            pytest.skip(f"{NOT_APPLICABLE} no kicad-cli 'pcb upgrade' (KiCad 10+ only) and "
                        f"pcbnew is not importable by {py}:\n{blob[:300]}")
        pytest.fail(f"pcbnew failed to re-save Ohm's board:\n{blob[:500]}")
    return dst.read_text(encoding="utf-8")


def _upgrade(text: str, work: Path, stem: str) -> str:
    """Hand `text` to KiCad, let it re-save, and return what KiCad wrote.

    Skips (with a KiCad-shaped reason, so `OHM_REQUIRE_KICAD` turns it into a
    failure) when this kicad-cli has no usable `pcb upgrade` at all, and when the
    re-saved bytes are identical to Ohm's own. The second case matters: byte
    equality means no re-save happened, and every assertion below it would then
    be comparing Ohm's output with itself and passing for free. A test that
    cannot fail is worse than no test.
    """
    if KICAD_CLI is None:  # belt and braces; the marker should have skipped first
        pytest.skip("kicad-cli not installed")
    src = work / f"{stem}.kicad_pcb"
    src.write_text(text, encoding="utf-8", newline="\n")

    attempts = []
    resaved = None
    for form in _FORMS:
        out = src if "{out}" not in form else work / f"{stem}.resaved.kicad_pcb"
        if out != src and out.exists():
            out.unlink()
        args = [a.format(src=str(src), out=str(out)) for a in form]
        proc = subprocess.run([KICAD_CLI, "pcb", "upgrade", *args],
                              capture_output=True, text=True, timeout=300)
        blob = (proc.stderr + proc.stdout).strip()
        if proc.returncode == 0 and out.exists() and out.stat().st_size > 0:
            resaved = out.read_text(encoding="utf-8")
            break
        attempts.append(f"{' '.join(args)} -> exit {proc.returncode}: {blob[:200]}")
        if proc.returncode != 0 and not _usage_error(blob):
            pytest.fail("kicad-cli pcb upgrade failed on Ohm's board:\n" + "\n".join(attempts))

    if resaved is None:
        # `pcb upgrade` is KiCad 10+ — measured, not assumed: KiCad 9.0.9 and
        # 8.0.9 answer `kicad-cli pcb` with drc/export/render and nothing else.
        # `pcbnew` can re-save on every supported version, so the gate falls back
        # to it rather than going dark on two thirds of the matrix. It runs in
        # the SYSTEM interpreter because KiCad builds pcbnew against the distro's
        # Python, which is not the one running pytest.
        resaved = _resave_via_pcbnew(src, work / f"{stem}.pcbnew.kicad_pcb", attempts)

    if resaved == text:
        pytest.skip("kicad-cli left the board byte-identical — no re-save happened, so this "
                    "gate would be comparing Ohm's output with itself")
    return resaved


@pytest.fixture(scope="module")
def unrouted(tmp_path_factory):
    """(low, Ohm's board, the same board after KiCad re-saved it)."""
    low, pcb = build()
    return low, pcb, _upgrade(pcb, tmp_path_factory.mktemp("unrouted"), "divider")


@pytest.fixture(scope="module")
def routed(tmp_path_factory):
    """As `unrouted`, but with the one unrouted net star-connected on F.Cu."""
    low, pcb = build()
    pcb = route(pcb, "R1_P2", [("r1", "2"), ("r2", "1")])
    return low, pcb, _upgrade(pcb, tmp_path_factory.mktemp("routed"), "divider")


BOARDS = ["unrouted", "routed"]


def _pair(request, which):
    return request.getfixturevalue(which)


def _bindings(board):
    """(footprint path, pad id) -> net name. Identity and wiring; no coordinates."""
    return {(p.fp_path, p.pad_id): p.net for p in board.pads}


def _verdict(low, text):
    """The signoff answer as comparable lines.

    Sorted: the ORDER of findings follows the order items appear in the board,
    which is KiCad's to choose when it re-serialises. The verdict is the set of
    findings and their count, and that is what must not change.
    """
    return sorted(d.machine() for d in signoff(low, text, BOARD_FILE))


# ---------- 1. the re-save happened, and produced a board ----------
@requires_kicad_cli
@pytest.mark.parametrize("which", BOARDS)
def test_kicad_rewrites_the_board(request, which):
    """`pcb upgrade` exits 0 and leaves a non-empty file KiCad's own parser wrote.

    `_upgrade` asserts exit status and non-emptiness (and skips if the bytes did
    not change at all). What is left to prove here is that the result is still a
    board rather than a truncated or foreign file.
    """
    _low, original, resaved = _pair(request, which)
    assert resaved.lstrip().startswith("(kicad_pcb")
    assert read_board(resaved).footprints, "KiCad's re-save has no footprints Ohm can see"
    assert resaved != original  # a real rewrite, not a copy


# ---------- 2. ohm_path survives: the assertion this file exists for ----------
@requires_kicad_cli
@pytest.mark.parametrize("which", BOARDS)
def test_ohm_path_property_survives_the_resave(request, which):
    """Every part Ohm emitted is still identified by `ohm_path` after KiCad saves.

    This is the F3 gap. If KiCad ever moves the custom property out of position 3
    of the form, drops it, or rewrites it into a shape `read_board` does not
    match, this comparison goes from {r1, r2} to {} and signoff's identification
    of the board silently collapses.
    """
    _low, original, resaved = _pair(request, which)
    before = set(read_board(original).footprints)
    assert before == {"r1", "r2"}, before  # not vacuous: the baseline is named, not derived
    assert set(read_board(resaved).footprints) == before


# ---------- 3. pad -> net bindings survive ----------
@requires_kicad_cli
@pytest.mark.parametrize("which", BOARDS)
def test_pad_net_bindings_survive_the_resave(request, which):
    """Which pad is on which net — compared by identity, not by coordinates."""
    _low, original, resaved = _pair(request, which)
    before = _bindings(read_board(original))
    assert len(before) == 4, before  # two resistors, two pads each; the map is not empty
    assert _bindings(read_board(resaved)) == before


@requires_kicad_cli
@pytest.mark.parametrize("which", BOARDS)
def test_net_names_carried_by_copper_survive_the_resave(request, which):
    """The net NAMES the board's copper actually references are unchanged.

    Deliberately not asserted through the top-level net table: KiCad 7-9 wrote
    one and referenced it by number, KiCad 10 dropped it and names every net
    inline, so a test written against the table asserts a serialisation habit
    rather than the circuit. (That is how this test was first written, and
    KiCad 10 failed it while the board was perfectly intact.)

    Net *numbers* are KiCad's to reassign and are not compared. A name appearing
    or vanishing is the real defect: it is what pad bindings and V001 compare
    against.
    """
    _low, original, resaved = _pair(request, which)

    def names(text: str) -> set[str]:
        board = read_board(text)
        return ({p.net for p in board.pads if p.net}
                | {s[5] for s in board.segs if s[5]}
                | {v[2] for v in board.vias if v[2]})

    before = names(original)
    assert {"PWR_V", "R1_P2", "PWR_GND"} <= before, before
    assert names(resaved) == before


@requires_kicad_cli
@pytest.mark.parametrize("which", BOARDS)
def test_parts_did_not_move_in_the_resave(request, which):
    """Placement is preserved to the reader's own 0.001 mm quantum.

    Separate from the identity checks on purpose: here a difference is not a
    formatting artefact but a part that actually moved, which would break V006
    (`constraint fixed`) and any placement a human approved. The tolerance is one
    quantum — the smallest difference `read_board` can represent at all — so a
    re-serialised `1` as `1.0` passes and a 1 µm shift is the smallest thing that
    can fail.
    """
    _low, original, resaved = _pair(request, which)
    before, after = read_board(original).footprints, read_board(resaved).footprints
    assert before and set(after) == set(before)
    for path, (x, y, rot) in before.items():
        ax, ay, arot = after[path]
        assert abs(ax - x) <= QUANTUM and abs(ay - y) <= QUANTUM, (path, (x, y), (ax, ay))
        assert (arot - rot) % 360 == 0, (path, rot, arot)


# ---------- 4. the end-to-end property: the same verdict ----------
@requires_kicad_cli
@pytest.mark.parametrize("which", BOARDS)
def test_signoff_verdict_is_unchanged_by_the_resave(request, which):
    """Same diagnostics, same codes, same count — the user's workflow answer.

    Everything above is a component of this. This is the property the workflow
    actually depends on: the file KiCad hands back gets the same go/no-go.
    """
    low, original, resaved = _pair(request, which)
    before = _verdict(low, original)
    assert _verdict(low, resaved) == before


# ---------- 5. connectivity: each verdict survives in its own direction ----------
@requires_kicad_cli
def test_unrouted_board_still_fires_v002_after_the_resave(unrouted):
    """A re-save must not HIDE an incomplete net.

    The dangerous direction: if the re-saved board became unreadable to signoff
    (no footprints, no pads), there would be no incomplete net left to report and
    the board would sign off clean with nothing routed.
    """
    low, original, resaved = unrouted
    assert [d for d in signoff(low, original, BOARD_FILE) if d.code == "V002"]
    assert [d for d in signoff(low, resaved, BOARD_FILE) if d.code == "V002"]


@requires_kicad_cli
def test_routed_board_is_still_clean_after_the_resave(routed):
    """A re-save must not turn a connected net into an incomplete one.

    Mirror of `test_fully_routed_board_is_clean`: the same copper, after KiCad
    has written it out, must still join the net's pads — proving signoff reads
    KiCad's segments, not merely Ohm's.
    """
    low, original, resaved = routed
    assert not [d for d in signoff(low, original, BOARD_FILE)
                if d.severity in ("error", "warning")]
    diags = signoff(low, resaved, BOARD_FILE)
    assert not [d for d in diags if d.severity in ("error", "warning")], \
        [d.machine() for d in diags]
    assert not [d for d in diags if d.code == "V002"]


# ---------- 6. the shapes the reader STANDS ON, each one broken on purpose ----------
#
# Everything above needs a real KiCad. These do not, and that is the point: the
# KiCad that breaks the next assumption does not exist yet, so the only way to
# test the failure mode is to write the broken file by hand. Each case below
# takes a board Ohm emitted, removes ONE shape `read_board` depends on, and
# asserts Ohm answers "I could not read this file" (V008) instead of a verdict
# about the user's layout — because a board carrying no `ohm_path` reads exactly
# like a board whose every part is missing (V001), and a truncated file reads
# exactly like an empty layout that signs off clean.
#
# The three regressions in ADR-021's history were each found by a human running
# a new KiCad. These are the same failures, found by a test.

def _diags(low, text):
    return signoff(low, text, BOARD_FILE)


def _v008(low, text):
    return [d for d in _diags(low, text) if d.code == "V008"]


def _pad_lines(pcb: str, fn):
    """Rewrite only the `(pad …)` lines of a board — `(size …)` and `(layers …)`
    also appear inside fonts and footprint bodies, and this file is about breaking
    ONE shape at a time."""
    out = [fn(ln) if ln.lstrip().startswith('(pad "') or ln.lstrip().startswith("(pad ")
           else ln for ln in pcb.splitlines(keepends=True)]
    return "".join(out)


def test_a_board_ohm_wrote_reports_no_read_failure():
    """The control, and the guard against noise: robustness must not become
    chatter. Neither an unrouted nor a routed board Ohm emitted may produce a
    single V008 — if one of the structural checks fires here, it is wrong."""
    low, pcb = build()
    assert not _v008(low, pcb)
    assert not _v008(low, route(pcb, "R1_P2", [("r1", "2"), ("r2", "1")]))
    assert not read_board(pcb).unreadable


def test_v008_has_an_explanation():
    from ohm.diagnostics import EXPLAIN

    assert "V008" in EXPLAIN and "ohm_path" in EXPLAIN["V008"]


# ---- the highest-value assumption: parts are identified by one property ----
def test_a_renamed_ohm_path_property_is_a_read_failure_not_a_missing_board():
    """AGENTS.md item 5: signoff identifies every part by `(property "ohm_path")`.

    If a KiCad release renamed or dropped it, every footprint would go
    unidentified and signoff would report the whole board as parts missing from
    the layout — a design verdict on a file it could not read, in the last check
    before fabrication. It must say which shape it did not find.
    """
    low, pcb = build()
    renamed = pcb.replace('"ohm_path"', '"ohm_path_v2"')
    assert renamed != pcb
    found = _v008(low, renamed)
    assert found, [d.machine() for d in _diags(low, renamed)]
    assert all(d.severity == "error" for d in found)
    assert any("ohm_path" in d.message and "2 footprints" in d.message for d in found), \
        [d.message for d in found]


def test_an_ohm_path_property_with_no_value_is_a_read_failure():
    """The same collapse one step later: the property is there, the VALUE is not
    (a release that moved it out of position 2 looks like this)."""
    low, pcb = build()
    stripped = re.sub(r'\(property "ohm_path" "[^"]*"\)', '(property "ohm_path")', pcb)
    assert stripped != pcb and '(property "ohm_path")' in stripped
    assert _v008(low, stripped)
    assert not read_board(stripped).footprints  # the failure this is protecting


def test_one_unidentified_footprint_is_not_a_read_failure():
    """Tolerance, deliberately: a board may legitimately carry footprints Ohm did
    not place (a mounting hole, a logo, a part the user added in KiCad). Only a
    board where NOTHING is identified is unreadable — one part missing from the
    layout is a real V001 and must stay one."""
    low, pcb = build()
    one = pcb.replace('(property "ohm_path" "r2")', '(property "ohm_path_v2" "r2")')
    assert one != pcb
    assert not _v008(low, one)
    assert any(d.code == "V001" and "r2" in d.message for d in _diags(low, one))


# ---- geometry: positional forms whose absence would fake a routing defect ----
def test_a_footprint_at_without_coordinates_is_a_read_failure():
    low, pcb = build()
    broken, n = re.subn(r"\(at [\d.]+ [\d.]+\)\n", "(at)\n", pcb)
    assert n == 2, n  # exactly the two footprint placements, not pads or fp_text
    found = _v008(low, broken)
    assert found and any("(at x y" in d.message for d in found), \
        [d.message for d in found]


def test_a_pad_size_that_is_not_a_number_is_a_read_failure():
    """A pad whose size cannot be read cannot catch a track, so its net would be
    reported as an incomplete net (V002) — the router blamed for a parse."""
    low, pcb = build()
    broken = _pad_lines(pcb, lambda ln: re.sub(r"\(size [\d.]+ [\d.]+\)", "(size)", ln))
    assert broken != pcb
    found = _v008(low, broken)
    assert found and any("size w h" in d.message for d in found), [d.message for d in found]


def test_pads_that_name_no_copper_layer_are_a_read_failure():
    """`(layers …)` is matched by the `.Cu` suffix. Rename the copper layers and
    no track can be matched to any pad — every net looks unrouted, and every one
    of those verdicts is wrong."""
    low, pcb = build()
    routed = route(pcb, "R1_P2", [("r1", "2"), ("r2", "1")])
    broken = _pad_lines(routed, lambda ln: ln.replace('"F.Cu"', '"F.Copper"'))
    assert broken != routed
    found = _v008(low, broken)
    assert found and any("copper layer" in d.message for d in found), \
        [d.message for d in found]


def test_a_segment_without_coordinates_is_a_read_failure():
    """Copper that exists but cannot be placed is copper signoff cannot see, and
    an unseen track is an incomplete net."""
    low, pcb = build()
    routed = route(pcb, "R1_P2", [("r1", "2"), ("r2", "1")])
    broken = re.sub(r"\(start [-\d.]+ [-\d.]+\) \(end", "(start) (end", routed)
    assert broken != routed
    found = _v008(low, broken)
    assert found and any("segment" in d.message for d in found), [d.message for d in found]


# ---- the file itself ----
def test_a_truncated_board_is_a_read_failure_not_an_empty_layout():
    low, pcb = build()
    cut = pcb[:pcb.index('(property "ohm_path" "r2")')]
    found = _v008(low, cut)
    assert found and any("unclosed" in d.message for d in found), [d.message for d in found]


def test_text_that_is_not_a_board_is_a_read_failure():
    low, _pcb = build()
    for text in ("", "not a board at all", '(kicad_sch (version 20231120))'):
        found = _v008(low, text)
        assert found and any("kicad_pcb" in d.message for d in found), (text, found)


def test_stray_closing_parens_are_reported_and_never_raise():
    """The reader is handed a file another program wrote, so it must not raise:
    a traceback out of the last gate before fabrication is not a verdict."""
    low, pcb = build()
    damaged = pcb + ")))\n"
    found = _v008(low, damaged)
    assert found and any("closing nothing" in d.message for d in found)
    # and what WAS readable is still read, so the finding is the only difference
    assert set(read_board(damaged).footprints) == {"r1", "r2"}


def test_read_failures_are_deterministic():
    low, pcb = build()
    broken = pcb.replace('"ohm_path"', '"ohm_path_v2"')
    assert ([d.machine() for d in _diags(low, broken)]
            == [d.machine() for d in _diags(low, broken)])


# ---- what stays tolerant, and why ----
def test_an_absent_or_unreadable_rotation_is_not_a_read_failure():
    """`(at x y [rot])`: the angle is absent on every unrotated part, and KiCad
    prints trailing keywords in these forms elsewhere (`(at x y 90 unlocked)` on
    footprint text). Locally recoverable — "not rotated" is the truth — so it
    must not become noise. Before this change it was an InvalidOperation."""
    low, pcb = build()
    keyworded = re.sub(r"\(at ([\d.]+) ([\d.]+)\)\n", r"(at \1 \2 unlocked)\n", pcb)
    assert keyworded != pcb
    assert not _v008(low, keyworded)
    assert read_board(keyworded).footprints == read_board(pcb).footprints


def test_an_unnamed_pad_is_not_read_as_a_pad_called_smd():
    """KiCad omits the name of an unnamed mechanical pad — `(pad smd circle …)`.
    Taking the field positionally yields a pad called "smd", which matches no
    source pad and reads as board/source drift."""
    board = read_board(
        '(kicad_pcb (version 20221018)\n'
        '  (footprint "ohm:X" (layer "F.Cu") (at 10 10)\n'
        '    (property "ohm_path" "h1")\n'
        '    (pad smd circle (at 0 0) (size 1 1) (layers "F.Cu"))\n'
        '    (pad "1" smd circle (at 2 0) (size 1 1) (layers "F.Cu"))))\n')
    assert not board.unreadable
    assert sorted(p.pad_id for p in board.pads) == ["", "1"]
