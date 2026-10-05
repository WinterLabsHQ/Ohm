"""`ohm signoff --physics-report` — the physics return channel, read (V009).

Each blocking condition is made to fire by a test of its own: a gate that has
never been observed to fire is a gate nobody has tested. The report fixtures are
built here, against the design compiled here, so a test states exactly which one
field it breaks.
"""

import hashlib
import json
import re

import pytest

from ohm import __version__
from ohm.cli import main
from ohm.driver import compile_source
from ohm.emit_constraints import emit_constraints
from ohm.emit_kicad import emit_kicad_pcb
from ohm.physics_report import check_physics_report, hole_ref, identify
from ohm.signoff import read_board

SRC = """
use std.interfaces
use std.passives

module Divider:
    pwr = io(Power)
    r1 = Resistor(value=10kohm)
    r2 = Resistor(value=10kohm)
    pwr.v ~ r1.P1
    r1.P2 ~ r2.P1
    r2.P2 ~ pwr.gnd
    obligation impedance(r1.P2) == 50ohm +/- 10%
    obligation return_path(r1.P2) <= 40
    obligation ripple(pwr.v) < 50mV
"""

COMMIT = "0" * 40


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _routed(pcb: str) -> str:
    """The emitted board with R1_P2 routed, as tests/test_signoff.py routes it."""
    pads = {(p.fp_path, p.pad_id): (p.x, p.y) for p in read_board(pcb).pads}
    (x1, y1), (x2, y2) = pads[("r1", "2")], pads[("r2", "1")]
    nid = re.search(r'\(net (\d+) "R1_P2"\)', pcb).group(1)
    seg = (f'  (segment (start {x1} {y1}) (end {x2} {y2}) (width 0.25) (layer "F.Cu") '
           f'(net {nid}))\n')
    return pcb.rstrip()[:-1] + seg + ")\n"


@pytest.fixture(scope="module")
def design():
    result = compile_source(SRC, "d.ohm", emit=True)
    assert result.ok, [d.machine() for d in result.diagnostics]
    board = _routed(emit_kicad_pcb(result.low, "d.ohm")).encode("utf-8")
    return result.low, SRC.encode("utf-8"), board


def _report(low, source, board, **override):
    verdicts = []
    for ref, kind, args, op, bound in identify(low.holes):
        if kind == "impedance":
            verdict, ev = "proven", {"verified": "microstrip 49.1 ohm", "estimated": None}
        elif kind == "return_path":
            verdict, ev = "proven", {"verified": None, "estimated": "soft 30.0 vs 40"}
        else:
            verdict, ev = "unproven", {"verified": None, "estimated": None,
                                       "reason": "no engine for 'ripple'"}
        verdicts.append({"hole_ref": ref, "kind": kind, "args": list(args), "op": op,
                         "bound": bound, "verdict": verdict, "evidence": ev})
    doc = {
        "schema": "ohm.physics_report", "version": "1",
        "ohm_commit": COMMIT, "ohm_version": f"ohm {__version__}",
        "ohm_source_sha256": _sha(source),
        "constraints_hash": _sha(emit_constraints(low).encode("utf-8")),
        "kicad_pcb_hash": _sha(board),
        "unsupported_vocabulary": ["ripple"],
        "declared_obligations": len(low.holes),
        "verdicts": verdicts,
    }
    doc.update(override)
    return doc


def _check(design, doc):
    low, source, board = design
    raw = doc if isinstance(doc, bytes) else json.dumps(doc).encode("utf-8")
    return check_physics_report(low, raw, "physics_report.json", source, board)


def _codes(diags, severity):
    return [d.message for d in diags if d.severity == severity]


def test_a_complete_fresh_report_is_clean_and_labels_both_kinds_of_number(design):
    diags = _check(design, _report(*design))
    assert all(d.code == "V009" for d in diags)
    assert not _codes(diags, "error")
    assert _codes(diags, "warning") == [
        "obligation 'ripple(pwr.v) < 50mV' is unproven: no engine for 'ripple'"]
    infos = _codes(diags, "info")
    assert any("proven by an ESTIMATE" in m and "return_path" in m for m in infos)
    assert infos[-1] == ("physics report: 3 obligation(s); 2 proven (1 verified, 1 estimated), "
                         "0 violated, 1 unproven, 0 waived, 0 without a verdict")


def test_a_dropped_obligation_fails(design):
    doc = _report(*design)
    doc["verdicts"] = doc["verdicts"][1:]
    errors = _codes(_check(design, doc), "error")
    assert any("impedance(r1.P2) == 50ohm +/- 10%' has no verdict" in m for m in errors)


@pytest.mark.parametrize("field", ["kicad_pcb_hash", "constraints_hash",
                                   "ohm_source_sha256", "ohm_version"])
def test_a_stale_report_is_refused_and_no_verdict_is_read(design, field):
    doc = _report(*design, **{field: "f" * 64})
    diags = _check(design, doc)
    errors = _codes(diags, "error")
    assert any(f"stale and was refused: its {field}" in m for m in errors)
    assert errors[-1] == ("3 obligation(s) have no verdict: the only report given was "
                          "refused as stale")
    assert not _codes(diags, "info") and not _codes(diags, "warning")


def test_an_ohm_commit_that_is_not_a_commit_is_refused(design):
    errors = _codes(_check(design, _report(*design, ohm_commit="HEAD")), "error")
    assert any("its ohm_commit is 'HEAD'" in m for m in errors)


def test_violated_is_an_error_and_says_which_label_its_number_has(design):
    doc = _report(*design)
    doc["verdicts"][0]["verdict"] = "violated"
    errors = _codes(_check(design, doc), "error")
    assert errors == ["obligation 'impedance(r1.P2) == 50ohm +/- 10%' is violated "
                      "(verified): microstrip 49.1 ohm"]


def test_estimated_and_verified_are_never_mixed_in_one_verdict(design):
    doc = _report(*design)
    doc["verdicts"][0]["evidence"]["estimated"] = "51 ohm"
    errors = _codes(_check(design, doc), "error")
    assert any("both a verified and an estimated number" in m for m in errors)
    assert any("has no verdict" in m for m in errors)  # the rejected entry discharges nothing


def test_a_result_with_no_number_is_refused(design):
    doc = _report(*design)
    doc["verdicts"][0]["evidence"] = {"verified": None, "estimated": None}
    errors = _codes(_check(design, doc), "error")
    assert any("'proven' with no measurement" in m for m in errors)


def test_two_verdicts_for_one_obligation_are_both_refused(design):
    doc = _report(*design)
    doc["verdicts"].append(dict(doc["verdicts"][0]))
    errors = _codes(_check(design, doc), "error")
    assert any("has 2 verdicts" in m for m in errors)


def test_a_verdict_for_an_obligation_the_source_does_not_declare(design):
    doc = _report(*design)
    extra = dict(doc["verdicts"][0], hole_ref=hole_ref("impedance", ("r9.P1",), "==", 0),
                 args=["r9.P1"])
    doc["verdicts"].append(extra)
    errors = _codes(_check(design, doc), "error")
    assert any("the source does not declare" in m for m in errors)


def test_a_verdict_whose_fields_do_not_match_its_identity(design):
    doc = _report(*design)
    doc["verdicts"][0]["bound"] = "45ohm +/- 10%"
    errors = _codes(_check(design, doc), "error")
    assert any("is filed under" in m for m in errors)


def test_an_unsupported_kind_cannot_be_proven(design):
    doc = _report(*design)
    doc["verdicts"][2].update(verdict="proven", evidence={"estimated": "12 mV"})
    errors = _codes(_check(design, doc), "error")
    assert any("vocabulary it has no engine for" in m for m in errors)


def test_the_declared_count_must_reconcile(design):
    errors = _codes(_check(design, _report(*design, declared_obligations=2)), "error")
    assert any("does not reconcile" in m for m in errors)


@pytest.mark.parametrize("raw", [b"{", b"[]", json.dumps({"schema": "x"}).encode()])
def test_an_unreadable_report_is_an_error(design, raw):
    errors = _codes(_check(design, raw), "error")
    assert len(errors) == 1 and "could not be read" in errors[0]


def test_a_waiver_is_info(design):
    doc = _report(*design)
    doc["verdicts"][2].update(verdict="waived", evidence={"reason": "bench-measured, ECO-12"})
    diags = _check(design, doc)
    assert not _codes(diags, "error") and not _codes(diags, "warning")
    assert any("is waived: bench-measured, ECO-12" in m for m in _codes(diags, "info"))


def test_identity_is_scoped_to_the_duplicate_group():
    """An unrelated obligation does not move another's ordinal; a duplicate does."""
    assert hole_ref("ripple", ("a.v",), "<", 0) == hole_ref("ripple", ("a.v",), "<", 0)
    assert hole_ref("ripple", ("a.v",), "<", 0) != hole_ref("ripple", ("a.v",), "<", 1)
    assert hole_ref("k", (), "<", 0) != hole_ref("k", ("",), "<", 0)
    assert hole_ref("k", ("a.v",), "<", 0).startswith("h:")
    assert len(hole_ref("k", ("a.v",), "<", 0)) == 34


# ---------- the CLI: absent flag changes nothing; present flag only appends ----------
def _signoff(tmp_path, capsys, *extra):
    code = main(["signoff", str(tmp_path / "d.ohm"), str(tmp_path / "d.kicad_pcb"),
                 "--no-geometry", "--json-diagnostics", *extra])
    out = capsys.readouterr().out.strip().splitlines()
    return code, [json.loads(line) for line in out]


def test_cli_without_the_flag_reports_nothing_about_obligations(tmp_path, capsys, design):
    low, source, board = design
    (tmp_path / "d.ohm").write_bytes(source)
    (tmp_path / "d.kicad_pcb").write_bytes(board)
    _code, docs = _signoff(tmp_path, capsys)
    assert not [d for d in docs[-1] if d["code"] == "V009"]


def test_cli_with_the_flag_only_appends_v009(tmp_path, capsys, design):
    low, source, board = design
    (tmp_path / "d.ohm").write_bytes(source)
    (tmp_path / "d.kicad_pcb").write_bytes(board)
    (tmp_path / "r.json").write_text(json.dumps(_report(low, source, board)), encoding="utf-8")
    base_code, base = _signoff(tmp_path, capsys)
    code, docs = _signoff(tmp_path, capsys, "--physics-report", str(tmp_path / "r.json"))
    assert docs[0] == base[0]
    assert docs[1][:len(base[1])] == base[1]
    assert {d["code"] for d in docs[1][len(base[1]):]} == {"V009"}
    assert base_code == code == 0  # a routed board and a clean report: signoff passes


def test_cli_a_dropped_obligation_turns_the_exit_status(tmp_path, capsys, design):
    low, source, board = design
    (tmp_path / "d.ohm").write_bytes(source)
    (tmp_path / "d.kicad_pcb").write_bytes(board)
    doc = _report(low, source, board)
    doc["verdicts"] = doc["verdicts"][:-1]
    (tmp_path / "r.json").write_text(json.dumps(doc), encoding="utf-8")
    code, docs = _signoff(tmp_path, capsys, "--physics-report", str(tmp_path / "r.json"))
    assert code == 1
    assert any(d["code"] == "V009" and d["severity"] == "error" and "no verdict" in d["message"]
               for d in docs[1])


def test_cli_a_missing_report_file_is_v009(tmp_path, capsys, design):
    low, source, board = design
    (tmp_path / "d.ohm").write_bytes(source)
    (tmp_path / "d.kicad_pcb").write_bytes(board)
    code, docs = _signoff(tmp_path, capsys, "--physics-report", str(tmp_path / "nope.json"))
    assert code == 1
    assert [d["code"] for d in docs[1] if d["severity"] == "error"][-1] == "V009"


def test_the_verdict_list_must_reconcile_with_the_declared_count(design):
    doc = _report(*design)
    doc["verdicts"] = doc["verdicts"][:-1]
    errors = _codes(_check(design, doc), "error")
    assert any("carries 2 verdict(s)" in m and "does not reconcile" in m for m in errors)
    assert any("has no verdict" in m for m in errors)
