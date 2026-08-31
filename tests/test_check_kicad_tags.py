"""The tag-drift checker's parsing, without touching the network.

`tools/check_kicad_tags.py` exists because the KiCad matrix pin is a MEASUREMENT
and is therefore only as honest as it is current — and because the one time
anyone checked the tag list by eye, they read page one of a listing ordered by
`last_updated` and concluded KiCad 7 was not containerised. It is. That mistake
survived four documents, an ADR, the README and a shell guard.

`fetch_tags()` is the only part that needs the registry, and it is deliberately
the only part with no logic in it. Everything that decides an answer is pure and
is tested here, so the checker itself cannot rot silently while watching for rot.
"""

import importlib.util
from pathlib import Path

TOOL = Path(__file__).parent.parent / "tools" / "check_kicad_tags.py"
_spec = importlib.util.spec_from_file_location("check_kicad_tags", TOOL)
assert _spec and _spec.loader
ckt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ckt)

# The real listing, as it stood 2026-08-31: 116 tags, of which these are the
# release-shaped ones. Frozen here on purpose — a fixture that tracked the live
# registry would agree with any bug that also read the live registry.
REAL_RELEASE_TAGS = [
    "7.0.8", "7.0.9", "7.0.10", "7.0.11",
    "8.0.0", "8.0.1", "8.0.2", "8.0.3", "8.0.4", "8.0.5", "8.0.6", "8.0.7",
    "8.0.8", "8.0.9",
    "9.0.1", "9.0.2", "9.0.3", "9.0.4", "9.0.5", "9.0.6", "9.0.7", "9.0.8", "9.0.9",
    "10.0.0", "10.0.1", "10.0.2", "10.0.4", "10.0.5",
]
NOISE = ["7.0", "9.0", "10.0", "latest", "nightly", "nightly-full",
         "10.0.5-full", "10.0.5-amd64", "10.0.5-amd64-full", "9.0.9-full"]


def test_only_bare_release_tags_count():
    """A rolling tag cannot be a certification pin.

    `10.0`, `latest` and `nightly` all move: the bytes behind them change without
    the pin changing, which is the whole failure this tool exists to stop. The
    `-full` and `-amd64` variants are the same release under another name and
    would make `newest` ambiguous for no gain.
    """
    newest = ckt.newest_per_major(REAL_RELEASE_TAGS + NOISE)
    assert set(newest) == {"7", "8", "9", "10"}


def test_newest_is_by_version_order_not_string_order():
    """`10.0.5` beats `9.0.9`, and `8.0.10` would beat `8.0.9`.

    String comparison gets both of these wrong, and the second is the one that
    will actually happen: KiCad 8 reached 8.0.9, so the next patch is 8.0.10.
    """
    newest = ckt.newest_per_major(REAL_RELEASE_TAGS + ["8.0.10"])
    assert newest["8"] == (8, 0, 10)
    assert newest["9"] == (9, 0, 9)
    assert newest["10"] == (10, 0, 5)


def test_a_gap_in_the_patch_series_is_not_a_problem():
    """10.0.3 has no image; 10.0.4 and 10.0.5 do. The newest is still 10.0.5."""
    assert "10.0.3" not in REAL_RELEASE_TAGS
    assert ckt.newest_per_major(REAL_RELEASE_TAGS)["10"] == (10, 0, 5)


def test_the_pins_this_repo_actually_uses_were_current_on_the_measured_day():
    """The four tags `.github/workflows/ci.yml` names, against the frozen listing.

    KiCad 10.0.6 was released in August 2026 and `kicad/kicad:10.0.6` returns 404
    — the stable release and the newest containerised release are different
    numbers, and only the second one can be a gate. So 10.0.5 is not a stale pin,
    it is the ceiling of what is provable.
    """
    newest = ckt.newest_per_major(REAL_RELEASE_TAGS)
    for pin in ("7.0.11", "8.0.9", "9.0.9", "10.0.5"):
        major = pin.split(".", 1)[0]
        assert ckt._fmt(newest[major]) == pin


def test_drift_is_detected_when_a_newer_image_appears():
    """The case the tool is for: KiCad ships 10.0.6 AND an image for it."""
    newest = ckt.newest_per_major(REAL_RELEASE_TAGS + ["10.0.6"])
    assert ckt._fmt(newest["10"]) == "10.0.6"
    assert ckt._fmt(newest["10"]) != "10.0.5", "the pin has drifted and must be reported"
