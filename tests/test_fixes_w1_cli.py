"""W1 audit-fix gate: CLI flags, clean errors, --dump-ir typed, --emit package."""

import json
from pathlib import Path

from ohm.cli import main

FIXTURES = Path(__file__).parent / "fixtures"
CANONICAL = str(FIXTURES / "canonical.ohm")
GENERATED = str(FIXTURES / "agent_mcp9808.ohm")


def test_target_kicad_8_errors_cleanly(capsys):
    assert main(["build", CANONICAL, "--target-kicad", "8"]) == 1
    assert "only KiCad 7 is supported in v1" in capsys.readouterr().err


def test_target_kicad_7_and_provider_flags_accepted(tmp_path):
    assert main(["build", CANONICAL, "-o", str(tmp_path), "--target-kicad", "7",
                 "--ratings", "declared", "--pindata", "declared"]) == 0


def test_dump_ir_typed_has_tol_lo(capsys):
    assert main(["build", CANONICAL, "--dump-ir", "typed"]) == 0
    out = capsys.readouterr().out
    assert "tol_lo" in out
    json.loads(out)  # valid JSON


def test_dump_ir_high_is_pre_annotation(capsys):
    assert main(["build", CANONICAL, "--dump-ir", "high"]) == 0
    json.loads(capsys.readouterr().out)


def test_check_explain_without_file(capsys):
    assert main(["check", "--explain", "I005"]) == 0
    assert "I005" in capsys.readouterr().out


def test_check_neither_file_nor_explain(capsys):
    assert main(["check"]) == 1
    assert "error" in capsys.readouterr().err


def test_missing_file_no_traceback(capsys):
    assert main(["check", "does_not_exist.ohm"]) == 1
    err = capsys.readouterr().err
    assert "cannot read" in err and "Traceback" not in err


def test_gen_package_bad_pitch(capsys):
    assert main(["gen-package", "TSSOP", "--pins", "8", "--pitch", "abc"]) == 1
    assert "invalid --pitch" in capsys.readouterr().err


def test_emit_package_writes_only_mod_and_sym(tmp_path):
    assert main(["build", GENERATED, "-o", str(tmp_path), "--emit", "package"]) == 0
    suffixes = {p.suffix for p in tmp_path.iterdir()}
    assert suffixes == {".kicad_mod", ".kicad_sym"}
