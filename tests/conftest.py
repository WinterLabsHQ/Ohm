"""Shared KiCad guards for the test suite — and the switch that makes them mean
something in CI.

Environment-verification tests (Ohm's output read by real KiCad, stdlib pinouts
against the installed libraries) cannot run where KiCad is absent, so they skip.
That is right on a developer's laptop and **wrong in CI**: a job whose whole
purpose is to certify a KiCad release reports green when the container failed to
start, the PPA 404'd, or a path moved — certifying nothing while looking exactly
like a job that certified everything.

`OHM_REQUIRE_KICAD=1` closes that hole from two directions:

1. **Before collection**, a missing KiCad aborts the run with one clear message,
   rather than 40 individually skipped tests nobody reads.
2. **After each test**, any test that skipped for a KiCad-shaped reason is
   converted into a failure — including `pytest.skip()` calls inside test bodies,
   which no decorator can see. `tests/test_pkggen.py` has one of those, and it is
   exactly the kind of silent hole this is for.

The KiCad CI jobs set the variable; nothing else does. A skipped test is not a
passed test, and this file is where that distinction is enforced.

Every path comes from `ohm.kicad_env`, so the suite and `ohm doctor` agree about
where KiCad is. They used to disagree: five test modules hardcoded
`/usr/share/kicad/...`, which skipped on Windows and macOS even with KiCad
installed, so the people writing this project never ran these tests.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ohm.kicad_env import footprints_dir, kicad_cli, symbols_dir

REQUIRE_KICAD = os.environ.get("OHM_REQUIRE_KICAD", "") not in ("", "0", "false")

KICAD_CLI = kicad_cli()
KICAD_FOOTPRINTS = Path(footprints_dir()) if footprints_dir() else None
KICAD_SYMBOLS = Path(symbols_dir()) if symbols_dir() else None

# Skip reasons that mean "the environment was missing KiCad". Under
# OHM_REQUIRE_KICAD these become failures, so the marker text is load-bearing:
# every guard in the suite must produce a reason containing one of these.
_KICAD_REASONS = ("kicad", "pcbnew")

# ...with one exemption. Some gates test a KiCad CAPABILITY that a supported
# version genuinely lacks — `kicad-cli pcb upgrade` is KiCad 10+, so the
# read-back gate has nothing to drive on 8 and 9. That is a fact about KiCad,
# not a broken certification environment, and failing on it would train people
# to ignore the switch. A skip whose reason carries this sentinel is allowed to
# stay a skip; it still prints, so it can never become invisible.
NOT_APPLICABLE = "n/a at this KiCad version:"


# ---------- the guards ----------
requires_kicad_cli = pytest.mark.skipif(
    KICAD_CLI is None, reason="kicad-cli not installed")
requires_kicad_fp = pytest.mark.skipif(
    KICAD_FOOTPRINTS is None, reason="KiCad footprint libraries not installed")
requires_kicad_sym = pytest.mark.skipif(
    KICAD_SYMBOLS is None, reason="KiCad symbol libraries not installed")


def footprint(rel: str) -> Path | None:
    """A path inside the installed footprint libraries, or None if absent.

    e.g. `footprint("Package_SO.pretty/SOIC-8_3.9x4.9mm_P1.27mm.kicad_mod")`.
    """
    return (KICAD_FOOTPRINTS / rel) if KICAD_FOOTPRINTS else None


def symbol_lib(name: str) -> Path | None:
    """A `.kicad_sym` inside the installed symbol libraries, or None if absent."""
    return (KICAD_SYMBOLS / name) if KICAD_SYMBOLS else None


def first_footprint(*rels: str) -> Path | None:
    """The first of `rels` that exists locally, or None.

    KiCad renames its own reference footprints between releases — the BGA-49 land
    pattern is `..._P0.8mm_Ball0.5mm_Pad0.4mm` in KiCad 8 and `..._P0.8mm` in 9
    and 10. A test pinned to one spelling does not fail on the others, it
    SKIPS, which is the same as not existing. Naming every spelling Ohm supports
    keeps the comparison running across the whole range.
    """
    for rel in rels:
        path = footprint(rel)
        if path is not None and path.exists():
            return path
    return None


def first_symbol_lib(*names: str) -> Path | None:
    """The first of `names` that exists locally, or None. See `first_footprint`."""
    for name in names:
        path = symbol_lib(name)
        if path is not None and path.exists():
            return path
    return None


def requires_footprint(*rels: str):
    """Guard a test on at least one of these footprint spellings existing."""
    return pytest.mark.skipif(
        first_footprint(*rels) is None,
        reason=f"KiCad footprint library lacks all of {', '.join(rels)}")


def requires_symbol_lib(name: str):
    """Guard a test on one specific symbol library existing locally."""
    path = symbol_lib(name)
    return pytest.mark.skipif(
        path is None or not path.exists(),
        reason=f"KiCad symbol library {name} not installed")


# ---------- OHM_REQUIRE_KICAD: a skip is not a pass ----------
def pytest_configure(config) -> None:
    """Refuse to start a certification run that cannot certify anything."""
    if not REQUIRE_KICAD:
        return
    missing = [name for name, value in (
        ("kicad-cli", KICAD_CLI),
        ("footprint libraries", KICAD_FOOTPRINTS),
        ("symbol libraries", KICAD_SYMBOLS),
    ) if value is None]
    if missing:
        raise pytest.UsageError(
            "OHM_REQUIRE_KICAD is set, but this environment is missing: "
            + ", ".join(missing)
            + ".\nThat variable means 'this run is certifying a KiCad release',"
              " so a skipped\nKiCad test would be a green run that proved nothing."
              " Either install KiCad\n(or point $OHM_KICAD_CLI and"
              " $KICAD*_FOOTPRINT_DIR at it), or unset the variable.")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Under OHM_REQUIRE_KICAD, a KiCad-shaped skip becomes a failure.

    Catches the skips a decorator cannot: `pytest.skip()` called from inside a
    test body once it has seen what the local KiCad actually supports.
    """
    outcome = yield
    if not REQUIRE_KICAD:
        return
    report = outcome.get_result()
    # "setup" as well as "call": a `skipif` mark fires during setup, so checking
    # only the call phase caught `pytest.skip()` inside a test body and let every
    # decorator-guarded skip through — which is most of them. Found by running a
    # certification against KiCad 10 and noticing one test still skipped.
    if report.when not in ("setup", "call") or not report.skipped:
        return
    raw = str(report.longrepr[2] if isinstance(report.longrepr, tuple)
              else report.longrepr)
    if NOT_APPLICABLE in raw:
        return
    reason = raw.lower()
    if any(word in reason for word in _KICAD_REASONS):
        report.outcome = "failed"
        report.longrepr = (
            f"OHM_REQUIRE_KICAD is set and this test skipped for a KiCad reason: "
            f"{reason}\nA certification run may not skip KiCad tests.")


def pytest_report_header(config) -> list[str]:
    """Name the KiCad this run was measured against, in the header.

    A test report that does not say which KiCad it ran against cannot be used as
    evidence afterwards, which is the entire point of the exercise.
    """
    mode = "REQUIRED (a KiCad skip fails)" if REQUIRE_KICAD else "optional (skips)"
    return [
        f"kicad-cli:  {KICAD_CLI or '(not found)'}",
        f"footprints: {KICAD_FOOTPRINTS or '(not found)'}",
        f"symbols:    {KICAD_SYMBOLS or '(not found)'}",
        f"kicad mode: {mode}",
    ]
