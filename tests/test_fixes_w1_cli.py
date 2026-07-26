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


def test_init_scaffolds_a_compiling_project(tmp_path):
    proj = tmp_path / "myboard"
    assert main(["init", str(proj)]) == 0
    entry = proj / "myboard.ohm"
    assert entry.is_file()
    assert (proj / ".gitignore").is_file()
    assert (proj / "README.md").is_file()
    assert (proj / "lib" / ".gitkeep").is_file()
    assert (proj / "out").is_dir()
    # the generated starter must pass every check tier clean
    assert main(["check", str(entry)]) == 0


def test_build_outputs_are_utf8_with_lf_newlines(tmp_path):
    # hermetic guarantee: emitted files are UTF-8 with LF regardless of the OS
    # locale codepage / newline convention (no cp1252, no CRLF on Windows).
    assert main(["build", CANONICAL, "-o", str(tmp_path), "--emit", "all"]) == 0
    for f in tmp_path.iterdir():
        if f.is_file():
            raw = f.read_bytes()
            f.read_text(encoding="utf-8")  # decodes as UTF-8 or the test errors
            assert b"\r\n" not in raw, f"{f.name} has CRLF"


def test_init_never_overwrites_existing_files(tmp_path):
    # regression: bare `ohm init` in an existing dir must NOT clobber a real
    # .gitignore / README.md — the scaffolder is strictly additive.
    proj = tmp_path / "existing"
    proj.mkdir()
    (proj / ".gitignore").write_text("SENTINEL-GITIGNORE\n", encoding="utf-8")
    (proj / "README.md").write_text("SENTINEL-README\n", encoding="utf-8")
    assert main(["init", str(proj)]) == 0
    assert (proj / ".gitignore").read_text(encoding="utf-8") == "SENTINEL-GITIGNORE\n"
    assert (proj / "README.md").read_text(encoding="utf-8") == "SENTINEL-README\n"
    assert (proj / "existing.ohm").is_file()  # the missing entry is still created
