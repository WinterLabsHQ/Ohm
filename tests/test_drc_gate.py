"""V007: `ohm signoff` runs the geometry gate, and reports it honestly.

The gate exists because signoff used to tell the user to run `kicad-cli pcb
drc`, which does not exist in KiCad 7 — the version Ohm pins. Nothing checked
geometry at all. These cases pin the two judgements the gate makes: what counts
as a defect, and what an unrouted Ohm board is allowed to look like.

Report parsing is tested against real KiCad 7 report text rather than a live
run, so the gate is covered on machines without KiCad.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from ohm.drc import parse_drc_report

REPORT = """** Drc report for out/fmuv2.kicad_pcb **
** Created on 2026-07-29 20:53:48 **

** Found 198 DRC violations **
[silk_over_copper]: Silkscreen clipped by solder mask
    Local override; Severity: warning
    @(316.2000 mm, 19.1500 mm): Rect on F.Silkscreen
[courtyards_overlap]: Courtyards overlap
    Rule: board setup constraints courtyard; Severity: error
    @(4.0000 mm, 8.0000 mm): Footprint J1
    @(3.5000 mm, 3.5000 mm): Footprint H1
[courtyards_overlap]: Courtyards overlap
    Rule: board setup constraints courtyard; Severity: error
    @(3.5000 mm, 46.5000 mm): Footprint H3
[unconnected_items]: Missing connection between items
    Local override; Severity: error
    @(100.4800 mm, 20.0000 mm): Pad 2 [GND.gnd] of C16 on F.Cu
[lib_footprint_issues]: Footprint 'D_SMA' not found in library 'Diode_SMD'.
    Local override; Severity: warning
    @(382.0000 mm, 20.0000 mm): Footprint D1

** Found 99 unconnected pads **
** End of Report **
"""


def _by_rule(diags):
    return {d.message.split(": ", 1)[0].split("x ", 1)[1]: d for d in diags}


def test_one_line_per_rule_class_with_a_count():
    """198 violations is five findings, not 198 lines of the same three."""
    by_rule = _by_rule(parse_drc_report(REPORT, "b.kicad_pcb"))
    assert set(by_rule) == {"silk_over_copper", "courtyards_overlap",
                            "unconnected_items", "lib_footprint_issues"}
    assert by_rule["courtyards_overlap"].message.startswith("2x ")
    assert "Footprint J1 @ (4.0000 mm, 8.0000 mm)" in by_rule["courtyards_overlap"].message


def test_an_unrouted_board_is_not_a_failed_board():
    """Ohm has no router yet, so every net is unconnected on every board it
    emits. Reporting that as an error would make the gate cry wolf and get
    ignored — which is worse than not having it."""
    by_rule = _by_rule(parse_drc_report(REPORT, "b.kicad_pcb"))
    assert by_rule["unconnected_items"].severity == "info"
    # Ohm embeds footprint geometry inline; the original library is absent by
    # design, not by mistake
    assert by_rule["lib_footprint_issues"].severity == "info"


def test_real_geometry_defects_are_errors_and_warnings():
    by_rule = _by_rule(parse_drc_report(REPORT, "b.kicad_pcb"))
    assert by_rule["courtyards_overlap"].severity == "error"  # parts on top of each other
    assert by_rule["silk_over_copper"].severity == "warning"
    assert all(d.code == "V007" for d in parse_drc_report(REPORT, "b.kicad_pcb"))


def test_empty_report_is_a_clean_gate():
    assert parse_drc_report("** Found 0 DRC violations **\n", "b.kicad_pcb") == []


@pytest.mark.skipif(shutil.which("kicad-cli") is None, reason="KiCad not installed")
def test_signoff_runs_the_gate_end_to_end(tmp_path):
    """The command wires source-fidelity and geometry into ONE verdict."""
    src = Path(__file__).parent.parent / "examples" / "divider_led.ohm"
    out = tmp_path / "out"
    build = subprocess.run(
        [sys.executable, "-m", "ohm.cli", "build", str(src), "-o", str(out),
         "--emit", "kicad"], capture_output=True, text=True, timeout=300)
    assert build.returncode == 0, build.stderr

    proc = subprocess.run(
        [sys.executable, "-m", "ohm.cli", "signoff", str(src),
         str(out / "divider_led.kicad_pcb")],
        capture_output=True, text=True, timeout=300)
    combined = proc.stdout + proc.stderr
    # either the gate ran (V007 lines) or it said plainly why it could not
    assert "V007" in combined or "geometry gate skipped" in combined, combined
