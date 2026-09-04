"""V007: `ohm signoff` runs the geometry gate, and reports it honestly.

The gate exists because signoff used to tell the user to run `kicad-cli pcb
drc`, which does not exist in KiCad 7 — the version Ohm pins. Nothing checked
geometry at all. These cases pin the two judgements the gate makes: what counts
as a defect, and what an unrouted Ohm board is allowed to look like.

Report parsing is tested against real KiCad 7 report text rather than a live
run, so the gate is covered on machines without KiCad.

Since 2026-08-31 they also pin the judgement the gate makes about ITSELF. KiCad
10's `pcbnew.WriteDRCReport` segfaults (measured on 10.0.5 and 10.0.6), and while
it ran in-process that took `ohm signoff` down with it — exit 139, no verdict,
not even the V001-V006 source-fidelity diagnostics that were already computed and
sitting in a list. A delegated engine may fail; it may not take the answer Ohm
already had. So the engine runs in a child process, and the cases below drive a
child that dies on purpose: no KiCad needed to prove that signoff survives it.
"""

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ohm.drc import DrcUnavailable, _exit_reason, _run, parse_drc_report, run_geometry_drc

from tests.conftest import requires_kicad_cli

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


@requires_kicad_cli
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


# ---------- the severity spelling KiCad 8 changed under us ----------
# The SAME report in the spelling every KiCad from 8 onwards writes. Measured
# 2026-08-31 on 8.0.9, 9.0.9 and 10.0.5, over BOTH routes (`kicad-cli pcb drc`
# and `pcbnew.WriteDRCReport` produce identical text on a given version): KiCad 7
# writes "Local override; Severity: warning", KiCad 8 dropped the "Severity: "
# and writes "Local override; warning".
#
# `ohm/drc.py` was written against a KiCad 7 report and matched only the KiCad 7
# spelling, so from KiCad 8 onwards every finding fell back to the default
# "warning" — a courtyard overlap could not fail the gate, and nothing said so.
REPORT_MODERN = REPORT.replace("; Severity: ", "; ")

# A real excerpt, verbatim, from `kicad-cli pcb drc` on KiCad 10.0.5 against
# Ohm's own examples/divider_led.ohm board — so the spelling above is quoted
# from the tool rather than assumed.
REPORT_KICAD10 = """** Drc report for b.kicad_pcb **
** Created on 2026-08-31T18:09:20 **
** Report includes: Errors, Warnings, Exclusions **

** Found 4 DRC violations **
[lib_footprint_issues]: The current configuration does not include the footprint library 'ohm'
    Local override; warning
    @(13.0000 mm, 26.0000 mm): Footprint R1

** Found 5 unconnected pads **
[unconnected_items]: Missing connection between items
    Local override; error
    @(13.7200 mm, 22.0000 mm): Pad 2 [PWR_GND] of C1 on F.Cu

** End of Report **
"""


def test_severity_survived_kicad_8_dropping_the_word_severity():
    """The same board, the same verdict, whichever spelling KiCad used."""
    old = {d.message: d.severity for d in parse_drc_report(REPORT, "b.kicad_pcb")}
    new = {d.message: d.severity for d in parse_drc_report(REPORT_MODERN, "b.kicad_pcb")}
    assert old == new
    assert _by_rule(parse_drc_report(REPORT_MODERN, "b.kicad_pcb"))[
        "courtyards_overlap"].severity == "error"


def test_a_real_kicad_10_report_parses():
    by_rule = _by_rule(parse_drc_report(REPORT_KICAD10, "b.kicad_pcb"))
    assert set(by_rule) == {"lib_footprint_issues", "unconnected_items"}
    # both are info for Ohm's flow — but they were READ as warning and error
    assert all(d.severity == "info" for d in by_rule.values())


# ---------- a dead engine must not be a dead signoff ----------
def test_exit_reason_names_the_signal_and_the_number_the_shell_prints():
    """-11 here and 139 in the terminal are the same death; say both, or the
    user cannot match this message to what they saw."""
    reason = _exit_reason(-11)
    assert "SIGSEGV" in reason and "11" in reason and "139" in reason
    assert _exit_reason(3) == "exited 3"


def test_a_child_that_crashes_comes_back_as_a_status():
    """The KiCad 10 shape, without KiCad: a child that dies mid-call returns a
    status to this process instead of ending it. If containment ever broke, this
    test would not fail — the test runner would."""
    status, _ = _run([sys.executable, "-c", "import ctypes; ctypes.string_at(0)"])
    assert status != 0
    assert _exit_reason(status)  # whatever it was, it has a name


def test_run_geometry_drc_names_every_route_it_tried(tmp_path, monkeypatch):
    monkeypatch.setenv("OHM_KICAD_CLI", str(tmp_path / "no-such-kicad-cli"))
    monkeypatch.setenv("OHM_KICAD_PYTHON", sys.executable)
    with pytest.raises(DrcUnavailable) as exc:
        run_geometry_drc(str(tmp_path / "b.kicad_pcb"))
    message = str(exc.value)
    assert "no-such-kicad-cli" in message          # the kicad-cli route, and why not
    assert "pcbnew" in message                     # the child-interpreter route, and why not


# A `pcbnew` that dies the way KiCad 10's does, for a child that has no KiCad.
_CRASHING_PCBNEW = "import ctypes\nctypes.string_at(0)\n"


def _signoff(tmp_path, source: Path, board: Path):
    """`ohm signoff` with both geometry routes pointed at things that fail: the
    binary does not exist, and the child interpreter finds a `pcbnew` that
    segfaults on import."""
    fake = tmp_path / "fakelib"
    fake.mkdir(exist_ok=True)
    (fake / "pcbnew.py").write_text(_CRASHING_PCBNEW, encoding="utf-8")
    env = dict(os.environ,
               OHM_KICAD_CLI=str(tmp_path / "no-such-kicad-cli"),
               OHM_KICAD_PYTHON=sys.executable,
               PYTHONPATH=str(fake))
    return subprocess.run([sys.executable, "-m", "ohm.cli", "signoff",
                           str(source), str(board)],
                          capture_output=True, text=True, timeout=300, env=env)


def test_signoff_still_reports_when_the_geometry_engine_dies(tmp_path):
    """The regression that matters. `ohm signoff` must exit 0 or 1 and say
    something — never hand the shell a signal.

    Before the gate moved out of process this was exit 139 and NO output at all
    on any machine with KiCad 10's pcbnew importable.
    """
    src = Path(__file__).parent.parent / "examples" / "divider_led.ohm"
    out = tmp_path / "out"
    build = subprocess.run(
        [sys.executable, "-m", "ohm.cli", "build", str(src), "-o", str(out),
         "--emit", "kicad"], capture_output=True, text=True, timeout=300)
    assert build.returncode == 0, build.stderr

    proc = _signoff(tmp_path, src, out / "divider_led.kicad_pcb")
    combined = proc.stdout + proc.stderr
    assert proc.returncode in (0, 1), f"exit {proc.returncode}\n{combined}"
    assert "geometry gate skipped" in combined, combined
    # the skip message names the code it stands in for, but no V007 FINDING may
    # appear: a gate that did not run must not report a clean board
    assert "[V007]" not in combined, combined


def test_source_fidelity_survives_a_dead_geometry_engine(tmp_path):
    """V001-V006 are pure and were computed BEFORE the gate ran. A crash in the
    geometry engine cost them, which is the whole reason this moved out of
    process: signing a board off against the wrong source must still say so."""
    ours = Path(__file__).parent.parent / "examples" / "divider_led.ohm"
    other = Path(__file__).parent.parent / "examples" / "led_bar.ohm"
    out = tmp_path / "out"
    for design in (ours, other):
        build = subprocess.run(
            [sys.executable, "-m", "ohm.cli", "build", str(design), "-o", str(out),
             "--emit", "kicad"], capture_output=True, text=True, timeout=300)
        assert build.returncode == 0, build.stderr

    proc = _signoff(tmp_path, ours, out / "led_bar.kicad_pcb")
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 1, f"exit {proc.returncode}\n{combined}"
    assert "V001" in combined, combined
    assert "geometry gate skipped" in combined, combined


# ---------- quarantine: impure, and off the build path ----------
def test_only_the_cli_reaches_the_geometry_gate():
    """This is what pays for `subprocess` being exempted by name for drc.py in
    the N1 import gate (tests/test_lower_emit.py). The module runs KiCad in a
    child process; nothing on the parse→emit path may import it, so only the
    `ohm signoff` command in cli.py may name it."""
    pkg = Path(__file__).resolve().parent.parent / "ohm"
    for py in pkg.rglob("*.py"):
        if py.name in ("cli.py", "drc.py"):
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            assert not any(n.split(".")[-1] == "drc" for n in names), \
                f"{py.name} imports ohm.drc, which runs KiCad in a subprocess"
# ---------- a rule class reports the WORST thing in it ----------
# `--severity-all` is passed on purpose (ohm/drc.py `_drc_via_kicad_cli`), which
# is what makes exclusions appear in the report at all — so a rule class really
# can hold an `error` and an `exclusion` at once. This is that report.
REPORT_MIXED = """** Drc report for b.kicad_pcb **
** Report includes: Errors, Warnings, Exclusions **

** Found 3 DRC violations **
[courtyards_overlap]: Courtyards overlap
    Rule: board setup constraints courtyard; Severity: error
    @(4.0000 mm, 8.0000 mm): Footprint J1
[courtyards_overlap]: Courtyards overlap
    Rule: board setup constraints courtyard; Severity: error
    @(3.5000 mm, 46.5000 mm): Footprint H3
[courtyards_overlap]: Courtyards overlap
    Local override; Severity: exclusion
    @(9.0000 mm, 9.0000 mm): Footprint H9
** End of Report **
"""

REPORT_MIXED_MODERN = REPORT_MIXED.replace("; Severity: ", "; ")


@pytest.mark.parametrize("report", [REPORT_MIXED, REPORT_MIXED_MODERN],
                         ids=["kicad7", "kicad8+"])
def test_a_mixed_severity_class_reports_its_worst(report):
    """Two real courtyard overlaps and one the author excluded.

    Severity used to be stored once per RULE with the last write winning, while
    KiCad prints one severity line per VIOLATION — so this exact report came back
    as a single `warning | 3x courtyards_overlap`, and the geometry gate passed a
    board with two genuine overlaps on it, exit 0. The regex fix that taught this
    module KiCad 8's spelling did not touch it: the demotion happens in the
    aggregation, so it survived on every version.

    The count was wrong too. `3x` included the item the author had deliberately
    excluded, which is the one thing in the class that is NOT a finding.
    """
    diags = parse_drc_report(report, "b.kicad_pcb")
    worst = [d for d in diags if d.severity == "error"]
    assert worst, [(d.severity, d.message) for d in diags]
    assert worst[0].message.startswith("2x courtyards_overlap"), worst[0].message
    # the exclusion is recorded, not counted as a defect and not silently dropped
    excluded = [d for d in diags if d.severity == "info"]
    assert excluded and excluded[0].message.startswith("1x courtyards_overlap")


@pytest.mark.parametrize("report", [REPORT_MIXED, REPORT_MIXED_MODERN],
                         ids=["kicad7", "kicad8+"])
def test_the_reported_location_belongs_to_the_reported_severity(report):
    """`message` and `where` came from the FIRST violation of a rule while
    `severity` came from the LAST, so one diagnostic could describe two different
    items — an error pointing a reviewer at the coordinates of an exclusion."""
    diags = parse_drc_report(report, "b.kicad_pcb")
    (err,) = [d for d in diags if d.severity == "error"]
    (exc,) = [d for d in diags if d.severity == "info"]
    assert "Footprint J1" in err.message and "Footprint H9" not in err.message
    assert "Footprint H9" in exc.message


def test_kicad_info_is_not_promoted_to_a_warning():
    """The same defect in the other direction: `_severity` mapped everything
    that was not exactly "error" to "warning", so KiCad's own `info` came back
    louder than KiCad wrote it."""
    report = REPORT_MIXED.replace("Severity: exclusion", "Severity: info")
    diags = parse_drc_report(report, "b.kicad_pcb")
    assert [d.severity for d in diags] == ["error", "info"]


def test_the_worst_finding_is_reported_first():
    """A reviewer reads the errors before the notes, and the order is a pure
    function of the report either way."""
    diags = parse_drc_report(REPORT, "b.kicad_pcb")
    rank = {"error": 0, "warning": 1, "info": 2}
    assert [rank[d.severity] for d in diags] == sorted(rank[d.severity] for d in diags)
