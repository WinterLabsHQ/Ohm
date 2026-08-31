"""V007: the geometry gate — KiCad's own DRC engine, driven by Ohm.

ADR-011 makes `ohm signoff` verify a routed board against the SOURCE, and keeps
`ohm/signoff.py` pure (no I/O, no subprocess, N1). Geometry — clearance, silk
over pads, courtyard overlap, board outline — is a property of the board alone,
so signoff delegated it. It delegated it to `kicad-cli pcb drc`, a subcommand
that **does not exist in KiCad 7**, still the oldest KiCad Ohm supports
(`ohm.cli.SUPPORTED_KICAD`): the advice was unrunnable there, so in practice
nothing checked geometry at all.

Every KiCad from 8 onwards does ship `kicad-cli pcb drc`, but this module stays
on the Python route deliberately. `pcbnew.WriteDRCReport` has existed since
KiCad 7, so one code path serves every supported version; it needs no binary on
`PATH`, which is the common case inside a virtualenv; and it returns the report
to the caller rather than to a file the caller must then find. A second
implementation gated on the user's KiCad version would be two things to keep
correct in exchange for nothing.

This module drives that engine and folds the result back into Ohm diagnostics,
so one command gives one verdict. It lives outside signoff.py because it is
impure by nature: it imports KiCad and writes a temp file. That is the same seam
`ohm verify-pinouts` already uses to read real KiCad symbol libraries.

Unrouted boards are the normal case for Ohm today (there is no router — that is
Meridian's scope), and an unrouted board's unconnected pads are not a defect, so
`unconnected_items` is reported as info, not error.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from ohm.diagnostics import Diagnostic, Span

# KiCad rule ids whose violations are NOT board defects in Ohm's flow.
#
# A rule id is a VERSION-SENSITIVE key. KiCad renames them between releases —
# KiCad 10 renamed the ERC rule `label_dangling` to `isolated_pin_label`, which
# is why `ohm/emit_kicad_pro.py` now writes both spellings — and a renamed id
# drops out of this set in silence. The day that happens, a finding Ohm has
# already judged harmless starts failing the geometry gate as a warning or an
# error, and nothing in the output points at the rename as the cause.
#
# So this set is a UNION over every KiCad Ohm supports (`ohm.cli.SUPPORTED_KICAD`
# — 7, 8, 9, 10), not a snapshot of the one that happened to be installed, and
# each entry records the versions it was OBSERVED under rather than the versions
# it is assumed to hold for. A union is the only classification that can be
# right here: a DRC report carries no version stamp (`WriteDRCReport` writes a
# date and a violation count, nothing more), so at classification time Ohm does
# not know which KiCad wrote the text it was handed and cannot pick a spelling.
# `tests/test_kicad_rule_ids.py` fails if a spelling is dropped.

# Observed: KiCad 7 (the report text in tests/test_drc_gate.py is a real KiCad 7
# run), and unchanged through 8, 9 and 10.
# "unconnected" on a board Ohm emitted means "not routed yet", which is the
# expected state until a router runs; reporting it as an error would make the
# gate cry wolf on every board.
_INFO_RULES = {"unconnected_items"}
# Observed: KiCad 7 (same report).
# Ohm embeds full footprint geometry in the board; KiCad still wants a
# fp-lib-table to resolve the ORIGINAL library, which a generated board has no
# reason to carry. Not a defect in the board.
_INFO_RULES |= {"lib_footprint_issues"}
# Observed: KiCad 9 and 10, where KiCad 10 reports 39 MORE of these than KiCad 9
# on the same FMUv2 board (recorded in CHANGELOG.md and PROGRESS.md under the
# KiCad 10.0.5 certification, and previously nowhere in code). Info for exactly
# the reason `lib_footprint_issues` is info, and the changelog's own reading of
# those 39 is that reason stated: "library-resolution and rule-naming
# differences in an environment with no footprint tables configured, not
# findings about the design". A footprint that cannot be linked back to the
# library it came from is not a geometry defect — the geometry is already in the
# board file, and V007 gates geometry. Leaving it unclassified would mean the
# same board passes the gate on KiCad 9 and fails it on KiCad 10 over a library
# table neither KiCad was given.
#
# NOT established, and deliberately not asserted anywhere: whether KiCad 10
# RENAMED `lib_footprint_issues` to this or whether the two are distinct rules
# that both fire in this environment. The recorded evidence is a count on one
# board, not a mapping, and no KiCad is installed on the machine this was
# written on to settle it. It does not change the verdict — both spellings are
# info, so the union is correct either way — but it would matter the day either
# one is promoted to a defect, and that is the day to go and find out.
_INFO_RULES |= {"footprint_link_issues"}

# where distros put KiCad's Python bindings for the system interpreter
_DIST_PACKAGES = (
    "/usr/lib/python3/dist-packages",
    "/usr/local/lib/python3/dist-packages",
)

_RULE_RE = re.compile(r"^\[([a-z_]+)\]: (.*)$")
_AT_RE = re.compile(r"^\s+@\((.*?)\): (.*)$")


class DrcUnavailable(RuntimeError):
    """KiCad's Python module is not importable in this environment."""


def _import_pcbnew():
    """KiCad ships `pcbnew` into the SYSTEM interpreter, not into a venv, so a
    venv install of Ohm cannot import it by name alone. Look where the distro
    puts it before giving up — that is the difference between the gate running
    and the gate being permanently unavailable to anyone using a virtualenv."""
    import importlib
    import sys

    try:
        return importlib.import_module("pcbnew")
    except ImportError:
        pass
    for path in _DIST_PACKAGES:
        if (Path(path) / "pcbnew.py").exists() and path not in sys.path:
            sys.path.append(path)
            try:
                return importlib.import_module("pcbnew")
            except ImportError:  # pragma: no cover - broken install
                break
    raise DrcUnavailable(
        "KiCad's Python module (pcbnew) is not importable, so the geometry gate "
        "cannot run. Install KiCad, or run DRC from the KiCad GUI "
        "(Inspect -> Design Rules Checker).")


def _severity(rule: str, kicad_severity: str) -> str:
    if rule in _INFO_RULES:
        return "info"
    return "error" if kicad_severity == "error" else "warning"


def run_geometry_drc(board_path: str) -> list[Diagnostic]:
    """Run KiCad's DRC on a board and return it as Ohm diagnostics (V007).

    One diagnostic per rule class, carrying the count and the first location —
    a 116-line wall of the same finding tells a reviewer nothing a count does
    not. Raises DrcUnavailable when KiCad's Python module is missing.
    """
    pcbnew = _import_pcbnew()

    board = pcbnew.LoadBoard(board_path)
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "drc.rpt"
        # KiCad 9 renamed the millimetre unit enum (EDA_UNITS_MILLIMETRES ->
        # EDA_UNITS_MM). Ask for whichever this KiCad has rather than pinning a
        # version: signoff's geometry gate is the last check before a board is
        # fabricated, and it failing with an AttributeError on a KiCad upgrade
        # is exactly when it must not.
        mm = getattr(pcbnew, "EDA_UNITS_MM", None)
        if mm is None:
            mm = getattr(pcbnew, "EDA_UNITS_MILLIMETRES", None)
        if mm is None:
            raise DrcUnavailable(
                "this KiCad's pcbnew exposes no millimetre unit constant "
                "(looked for EDA_UNITS_MM and EDA_UNITS_MILLIMETRES)")
        pcbnew.WriteDRCReport(board, str(report), mm, True)
        text = report.read_text(encoding="utf-8", errors="replace")
    return parse_drc_report(text, board_path)


def parse_drc_report(text: str, board_path: str) -> list[Diagnostic]:
    """KiCad's DRC report text -> one Ohm diagnostic per rule class.

    Split out from the run so it is testable without KiCad installed.
    """
    found: dict[str, dict] = {}
    rule = None
    for line in text.splitlines():
        m = _RULE_RE.match(line)
        if m:
            rule, message = m.group(1), m.group(2)
            entry = found.setdefault(rule, {"n": 0, "message": message,
                                            "severity": "warning", "where": None})
            entry["n"] += 1
            continue
        if rule is None:
            continue
        if "Severity: " in line:
            found[rule]["severity"] = line.rsplit("Severity: ", 1)[1].strip()
        elif (m := _AT_RE.match(line)) and found[rule]["where"] is None:
            found[rule]["where"] = f"{m.group(2)} @ ({m.group(1)})"

    span = Span(1, 1, 1, 1)
    out = []
    for rule in sorted(found):
        entry = found[rule]
        where = f" first: {entry['where']}" if entry["where"] else ""
        out.append(Diagnostic(
            _severity(rule, entry["severity"]), "V007",
            f"{entry['n']}x {rule}: {entry['message']}{where}",
            span, file=board_path,
            help="KiCad DRC, run by `ohm signoff`; geometry is a property of "
                 "the board, so the board's own engine is the oracle"))
    return out
