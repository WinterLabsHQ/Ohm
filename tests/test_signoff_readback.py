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

Deliberately NOT asserted here: that KiCad prints coordinates the way Ohm does.
Pad and footprint identity is compared by path/pad-id/net, never by re-read
number formatting, because `1.0` for `1` is a serialiser's business while a net
binding moving is a real defect. Positions are compared separately, to the
reader's own 0.001 mm quantum, where a difference means a part actually moved.
"""

import os
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
