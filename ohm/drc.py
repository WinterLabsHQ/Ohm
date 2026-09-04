"""V007: the geometry gate — KiCad's own DRC engine, driven by Ohm.

ADR-011 makes `ohm signoff` verify a routed board against the SOURCE, and keeps
`ohm/signoff.py` pure (no I/O, no subprocess, N1). Geometry — clearance, silk
over pads, courtyard overlap, board outline — is a property of the board alone,
so signoff delegated it. It delegated it to `kicad-cli pcb drc`, a subcommand
that **does not exist in KiCad 7**, still the oldest KiCad Ohm supports
(`ohm.cli.SUPPORTED_KICAD`): the advice was unrunnable there, so in practice
nothing checked geometry at all.

**Correction, measured 2026-08-31.** This module then went the other way and
called `pcbnew.WriteDRCReport` in-process, arguing that one code path served
every supported version, needed no binary on `PATH`, and handed the report back
to the caller rather than to a file. The first ground is false, and it is the
one the other two were paying for. That exact call, against a real Ohm-emitted
board, using each image's own system Python:

    KiCad 7.0.11  import ok, LoadBoard ok, WriteDRCReport ok    (report written)
    KiCad 8.0.9   import ok, LoadBoard ok, WriteDRCReport ok    (report written)
    KiCad 9.0.9   import ok, LoadBoard ok, WriteDRCReport ok    (report written)
    KiCad 10.0.5  import ok, LoadBoard ok, WriteDRCReport **SIGSEGV** (exit 139)
    KiCad 10.0.6  import ok, LoadBoard ok, WriteDRCReport **SIGSEGV** (exit 139)

On 7, 8 and 9 the call prints a benign wx assertion (`create wxApp before
calling this`) and works. On KiCad 10 it prints `assert "m_choices.GetCount() >
0" failed in PROPERTY_ENUM()`, then `assert "process" failed in Pgm()`, then
dies with SIGSEGV — inside the process running `ohm signoff`.

That is worse than a failing gate. `ohm/cli.py` catches `DrcUnavailable` and
downgrades it to `warning: geometry gate skipped`, which is the right design and
was completely inapplicable: **a segfault cannot be caught.** The V001-V006
source-fidelity diagnostics are pure, and had already been computed, and died
with the interpreter. The last check before fabrication printed nothing at all.

So two rules now, and the first does not depend on the second:

1. **The engine runs OUT OF PROCESS, always.** A crash in a delegated engine
   costs the geometry answer; it must not cost the answer Ohm already had. A
   child that dies on a signal becomes `DrcUnavailable`, which the CLI handles.
2. **`kicad-cli pcb drc` first, `pcbnew` in a child interpreter second.**
   `kicad-cli pcb drc` is KiCad's own supported headless route, it takes the
   same flags in 8, 9 and 10 (measured), and it is the one that works on the
   newest version. It does not exist in KiCad 7 (`kicad-cli pcb` there offers
   `export` and nothing else), where the `pcbnew` route does work — and a child
   interpreter is also the answer for a venv install with no binary on `PATH`,
   which is why that route stays rather than being deleted.

Both routes write the SAME report format, which is what makes this cheap:
`parse_drc_report` is unchanged in structure and stays pure, so it is still
testable with no KiCad installed.

This module lives outside signoff.py because it is impure by nature: it runs
KiCad and writes a temp file. That is the same seam `ohm verify-pinouts` and
`ohm verify-footprints` already use to read real KiCad libraries. It is off the
parse->emit build path — only `ohm/cli.py`'s signoff command may import it, which
`tests/test_drc_gate.py` proves, and which is why `subprocess` here is exempted
by name in the N1 import gate rather than the gate being loosened.

Unrouted boards are the normal case for Ohm today (there is no router — that is
Meridian's scope), and an unrouted board's unconnected pads are not a defect, so
`unconnected_items` is reported as info, not error.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import NoReturn

from ohm.diagnostics import Diagnostic, Span
from ohm.kicad_env import kicad_cli

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

# A DRC on a real board is seconds; anything approaching this is a hung child,
# and a hung child in a fabrication gate must end as a stated reason rather than
# as a command that never returns.
_TIMEOUT = 300

# The child's way of saying "this interpreter has no pcbnew" rather than "KiCad
# fell over": the first is an environment fact worth naming to the user, the
# second is the bug this module exists to survive.
_NO_PCBNEW = 3
_NO_UNITS = 4

_RULE_RE = re.compile(r"^\[([a-z_]+)\]: (.*)$")
_AT_RE = re.compile(r"^\s+@\((.*?)\): (.*)$")
# The severity line under a finding, in BOTH spellings KiCad has used:
#
#   KiCad 7      "    Local override; Severity: warning"
#   KiCad 8/9/10 "    Local override; warning"
#
# Measured 2026-08-31 on 7.0.11, 8.0.9, 9.0.9 and 10.0.5, over both routes: the
# `Severity: ` prefix is a KiCad 7 spelling that KiCad 8 dropped, and it is not a
# difference between `kicad-cli` and `pcbnew` (each version writes the same text
# either way). Matching only the KiCad 7 spelling — which is what this module did
# until now, having been written against a KiCad 7 report — means every finding
# on every KiCad from 8 onwards falls back to the default "warning", so a
# courtyard overlap or a clearance violation could never fail the gate. A gate
# that cannot return "error" is not a gate.
_SEVERITY_RE = re.compile(r";\s*(?:Severity:\s*)?(error|warning|info|exclusion|ignore)\s*$")

# What the child interpreter runs. It re-implements, in the child, the sys.path
# fix this module used to do in-process: KiCad ships `pcbnew` into the SYSTEM
# interpreter, not into a venv, so a venv install of Ohm cannot import it by name
# alone. Looking where the distro puts it is the difference between the gate
# running and the gate being permanently unavailable to anyone using a virtualenv.
_CHILD_SCRIPT = f"""\
import sys
sys.path.extend([p for p in {list(_DIST_PACKAGES)!r} if p not in sys.path])
try:
    import pcbnew
except ImportError as exc:
    print(exc, file=sys.stderr)
    raise SystemExit({_NO_PCBNEW})
board = pcbnew.LoadBoard(sys.argv[1])
# KiCad 9 renamed the millimetre unit enum (EDA_UNITS_MILLIMETRES -> EDA_UNITS_MM).
# Ask for whichever this KiCad has rather than pinning a version.
mm = getattr(pcbnew, "EDA_UNITS_MM", None)
if mm is None:
    mm = getattr(pcbnew, "EDA_UNITS_MILLIMETRES", None)
if mm is None:
    print("no millimetre unit constant (EDA_UNITS_MM / EDA_UNITS_MILLIMETRES)",
          file=sys.stderr)
    raise SystemExit({_NO_UNITS})
pcbnew.WriteDRCReport(board, sys.argv[2], mm, True)
"""


class DrcUnavailable(RuntimeError):
    """KiCad's DRC engine could not be run in this environment.

    Raised for every way the delegated engine can fail to produce a report,
    including the engine dying: `ohm/cli.py` turns this into `warning: geometry
    gate skipped` and still prints the source-fidelity verdict.
    """


def _tail(blob: str, limit: int = 300) -> str:
    """The last of a child's output, on one line, for a diagnostic message."""
    flat = " ".join(blob.split())
    return flat[-limit:] if len(flat) > limit else flat


def _exit_reason(returncode: int) -> str:
    """Say what happened to a child, in the words the user will see elsewhere.

    `subprocess` reports a POSIX child killed by a signal as a NEGATIVE status,
    while the shell that ran `ohm signoff` reports the same death as 128 + the
    signal — so a KiCad 10 segfault is -11 here and 139 there, and naming both
    is what lets someone match this message against what their terminal said.
    Windows has no signals and reports a structured exception as a large
    unsigned code (0xC0000005 is an access violation), so print those in hex.
    """
    if returncode < 0:
        sig = -returncode
        try:
            name = signal.Signals(sig).name
        except ValueError:  # pragma: no cover - a signal number Python has no name for
            name = f"signal {sig}"
        return f"died on {name} (signal {sig}; a shell reports this as exit {128 + sig})"
    if returncode > 0xFF:
        return f"exited {returncode} (0x{returncode:08X})"
    return f"exited {returncode}"


def _run(argv: list[str]) -> tuple[int, str]:
    """Run a child to completion and capture BOTH streams.

    Capturing is not tidiness. KiCad prints wx assertions to stderr on every
    version and progress lines to stdout from 8 onwards, and `ohm signoff
    --json-diagnostics` writes JSON to stdout — an inherited stream would put
    "Saved DRC Report to ..." in the middle of a machine-readable verdict.

    Returns (status, output). A child that CRASHED returns like any other: this
    function raises only when the child could not be started or would not stop.
    """
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              errors="replace", timeout=_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise DrcUnavailable(
            f"{Path(argv[0]).name} did not finish within {_TIMEOUT}s") from None
    except OSError as exc:
        raise DrcUnavailable(f"cannot run {argv[0]}: {exc}") from None
    return proc.returncode, ((proc.stderr or "") + (proc.stdout or "")).strip()


def _read_report(report: Path) -> str | None:
    """The report text, or None if the engine did not write a usable one."""
    if not report.exists() or report.stat().st_size == 0:
        return None
    return report.read_text(encoding="utf-8", errors="replace")


def _drc_via_kicad_cli(cli: str, board_path: str, report: Path) -> str:
    """KiCad's own headless DRC. Flags measured identical on 8.0.9/9.0.9/10.0.5.

    `--units mm` is passed rather than relied on (it is the default today)
    because the coordinates in the report are quoted verbatim into V007
    diagnostics, and a diagnostic whose units depend on a KiCad default is not a
    stable record. `--all-track-errors` matches the `True` this module passes to
    `WriteDRCReport` on the other route, so the two report the same counts.
    `--exit-code-violations` is deliberately NOT passed: violations are the
    answer, not a failure to get one, and Ohm grades them itself.
    """
    status, blob = _run([cli, "pcb", "drc", "--output", str(report),
                         "--format", "report", "--units", "mm",
                         "--severity-all", "--all-track-errors", board_path])
    text = _read_report(report)
    if status == 0 and text is not None:
        return text
    # KiCad 7 lands here every time: `kicad-cli pcb` has only `export`, so it
    # prints usage and exits 1. That is a fall-through to the pcbnew route, not
    # a failure — hence the reason is collected rather than raised at the user.
    detail = f": {_tail(blob)}" if blob else ""
    raise DrcUnavailable(f"`kicad-cli pcb drc` ({cli}) {_exit_reason(status)}{detail}")


def _child_interpreters() -> list[str]:
    """Which interpreter should be asked to import `pcbnew`, best first.

    NOT `sys.executable` first. KiCad installs `pcbnew` into the SYSTEM
    interpreter, and it is a compiled extension: it can only be imported by the
    Python it was built for. Ohm's own interpreter is routinely neither — the
    container matrix runs Ohm on a uv-managed standalone 3.12 while KiCad 10's
    `pcbnew` is built for the image's 3.13 — so asking `sys.executable` first
    would spend the first attempt on the candidate least likely to work.

    `$OHM_KICAD_PYTHON` wins outright and is then the ONLY candidate, the way
    `$OHM_KICAD_CLI` does for the binary (`ohm.kicad_env.kicad_cli`): an
    explicitly set variable is a statement about which KiCad this machine means,
    and a fallback that quietly used a different one would defeat it. That is
    also what lets a test point the gate at an interpreter that fails on purpose.
    """
    override = os.environ.get("OHM_KICAD_PYTHON")
    if override:
        return [override]
    out: list[str] = []
    for cand in (shutil.which("python3"), "/usr/bin/python3", sys.executable):
        if cand and cand not in out and (cand == sys.executable or Path(cand).exists()):
            out.append(cand)
    return out


def _drc_via_pcbnew_child(board_path: str, report: Path) -> str:
    """KiCad's DRC through `pcbnew`, in a CHILD interpreter — never this one.

    The child is the whole point. `WriteDRCReport` segfaults on KiCad 10 (see the
    module docstring), and a segfault cannot be caught; out here it is a status
    code and a sentence. A crashed child's report is also refused even when the
    file exists, because KiCad 10 dies *inside* the write: a truncated report
    parses perfectly and under-reports violations, which in a fabrication gate is
    the worst of the three outcomes.
    """
    attempts = []
    for py in _child_interpreters():
        if report.exists():
            report.unlink()
        status, blob = _run([py, "-c", _CHILD_SCRIPT, board_path, str(report)])
        text = _read_report(report)
        if status == 0 and text is not None:
            return text
        if status == _NO_PCBNEW:
            attempts.append(f"{py}: pcbnew is not importable")
            continue
        if status == 0:
            attempts.append(f"{py}: pcbnew wrote no report")
            continue
        detail = f": {_tail(blob)}" if blob else ""
        attempts.append(f"{py}: pcbnew {_exit_reason(status)}{detail}")
    if not attempts:  # pragma: no cover - sys.executable always exists
        attempts.append("no Python interpreter to try")
    _unavailable("KiCad's pcbnew could not produce a report", attempts)


def _unavailable(headline: str, reasons: list[str]) -> NoReturn:
    """Every way out of this module that is not a report, in one shape."""
    raise DrcUnavailable(f"{headline} — " + "; ".join(reasons))


# KiCad's severity words, mapped to Ohm's three. Explicit rather than
# "error, else warning": that shape silently PROMOTED KiCad's own `info` to a
# warning, and silently DEMOTED an `exclusion` — a violation the board's author
# deliberately excluded — into the same bucket as a real finding.
_SEVERITY_MAP = {"error": "error", "warning": "warning", "info": "info",
                 "exclusion": "info", "ignore": "info"}
# Worst-first, so a rule class reports the worst thing in it.
_SEVERITY_RANK = {"error": 3, "warning": 2, "info": 1}


def _severity(rule: str, kicad_severity: str) -> str:
    if rule in _INFO_RULES:
        return "info"
    return _SEVERITY_MAP.get(kicad_severity, "warning")


def run_geometry_drc(board_path: str) -> list[Diagnostic]:
    """Run KiCad's DRC on a board and return it as Ohm diagnostics (V007).

    One diagnostic per rule class, carrying the count and the first location —
    a 116-line wall of the same finding tells a reviewer nothing a count does
    not. Raises DrcUnavailable when no route could produce a report, naming
    every route it tried and what each did; `ohm/cli.py` prints that as
    `warning: geometry gate skipped` and keeps the source-fidelity verdict.
    """
    reasons = []
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "drc.rpt"
        cli = kicad_cli()
        if cli is None:
            reasons.append("no kicad-cli found ($OHM_KICAD_CLI overrides the search)")
        else:
            try:
                return parse_drc_report(_drc_via_kicad_cli(cli, board_path, report),
                                        board_path)
            except DrcUnavailable as exc:
                reasons.append(str(exc))
        try:
            return parse_drc_report(_drc_via_pcbnew_child(board_path, report), board_path)
        except DrcUnavailable as exc:
            reasons.append(str(exc))
    _unavailable("the geometry gate (V007) could not run", reasons)


def parse_drc_report(text: str, board_path: str) -> list[Diagnostic]:
    """KiCad's DRC report text -> one Ohm diagnostic per rule class.

    Pure, and split out from the run so it is testable without KiCad installed —
    which is also what lets one parser serve both routes: `kicad-cli pcb drc`
    and `pcbnew.WriteDRCReport` write the same text on the same KiCad (measured
    2026-08-31 on 8.0.9, 9.0.9 and 10.0.5).
    """
    # One entry per VIOLATION, grouped afterwards. KiCad prints a severity line
    # under each violation, and this used to keep one severity per RULE with the
    # last write winning — so a class holding two real `error` overlaps and one
    # `exclusion` reported `warning`, and the gate passed a board with genuine
    # clearance violations on it. `--severity-all` is passed on purpose (it is
    # what makes exclusions visible at all), which is what put the third line
    # there. The message and location came from the FIRST violation while the
    # severity came from the LAST, so one diagnostic could describe two items.
    #
    # Grouping by (rule, severity) fixes all three at once: a bucket's count,
    # message and location now all belong to the same severity.
    violations: list[dict] = []
    for line in text.splitlines():
        m = _RULE_RE.match(line)
        if m:
            violations.append({"rule": m.group(1), "message": m.group(2),
                               "severity": "warning", "where": None})
            continue
        if not violations:
            continue
        if (m := _SEVERITY_RE.search(line)):
            violations[-1]["severity"] = m.group(1)
        elif (m := _AT_RE.match(line)) and violations[-1]["where"] is None:
            violations[-1]["where"] = f"{m.group(2)} @ ({m.group(1)})"

    groups: dict[tuple[str, str], dict] = {}
    for v in violations:
        key = (v["rule"], _severity(v["rule"], v["severity"]))
        entry = groups.setdefault(key, {"n": 0, "message": v["message"],
                                        "where": None})
        entry["n"] += 1
        if entry["where"] is None:
            entry["where"] = v["where"]

    span = Span(1, 1, 1, 1)
    out = []
    # Worst first, then by rule: a reviewer reads the errors before the notes,
    # and the order is a pure function of the report either way.
    for (rule, severity) in sorted(
            groups, key=lambda k: (-_SEVERITY_RANK.get(k[1], 0), k[0])):
        entry = groups[(rule, severity)]
        where = f" first: {entry['where']}" if entry["where"] else ""
        out.append(Diagnostic(
            severity, "V007",
            f"{entry['n']}x {rule}: {entry['message']}{where}",
            span, file=board_path,
            help="KiCad DRC, run by `ohm signoff`; geometry is a property of "
                 "the board, so the board's own engine is the oracle"))
    return out
