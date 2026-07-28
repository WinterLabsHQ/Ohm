"""W2: `ohm init`/`create-project`, `ohm.toml` auto-discovery, `ohm doctor`,
`ohm refactor`. Scaffolding is strictly additive; discovery is framework-style
(bare commands inside a project); explicit file/`-o` always win."""

from ohm.cli import main


def test_init_scaffolds_full_project_that_compiles(tmp_path):
    proj = tmp_path / "myboard"
    assert main(["init", str(proj)]) == 0
    for rel in ("myboard.ohm", "ohm.toml", "README.md", "AGENTS.md",
                "docs/LANGUAGE.md", ".gitignore", "lib/.gitkeep"):
        assert (proj / rel).is_file(), rel
    assert (proj / "out").is_dir()
    # ohm.toml points at the entry
    assert 'entry = "myboard.ohm"' in (proj / "ohm.toml").read_text(encoding="utf-8")
    # the starter passes every check tier clean
    assert main(["check", str(proj / "myboard.ohm")]) == 0


def test_create_project_is_an_alias_for_init(tmp_path):
    proj = tmp_path / "aliased"
    assert main(["create-project", str(proj)]) == 0
    assert (proj / "aliased.ohm").is_file()
    assert (proj / "ohm.toml").is_file()


def test_scaffold_files_are_utf8_lf(tmp_path):
    proj = tmp_path / "enc"
    assert main(["init", str(proj)]) == 0
    for f in (proj / "ohm.toml", proj / "README.md", proj / "enc.ohm"):
        raw = f.read_bytes()
        f.read_text(encoding="utf-8")  # decodes as UTF-8 or errors
        assert b"\r\n" not in raw, f.name


def test_init_never_overwrites_existing(tmp_path):
    proj = tmp_path / "existing"
    proj.mkdir()
    (proj / "README.md").write_text("SENTINEL\n", encoding="utf-8")
    (proj / "ohm.toml").write_text("SENTINEL\n", encoding="utf-8")
    assert main(["init", str(proj)]) == 0
    assert (proj / "README.md").read_text(encoding="utf-8") == "SENTINEL\n"
    assert (proj / "ohm.toml").read_text(encoding="utf-8") == "SENTINEL\n"
    assert (proj / "AGENTS.md").is_file()  # the missing ones are still created


# ---------- ohm.toml auto-discovery ----------
def test_bare_check_uses_project_entry(tmp_path, monkeypatch):
    proj = tmp_path / "board"
    assert main(["init", str(proj)]) == 0
    monkeypatch.chdir(proj)
    assert main(["check"]) == 0                      # no file arg — finds ohm.toml


def test_bare_check_from_subdir_walks_up(tmp_path, monkeypatch):
    proj = tmp_path / "board"
    assert main(["init", str(proj)]) == 0
    monkeypatch.chdir(proj / "lib")
    assert main(["check"]) == 0


def test_bare_build_emits_to_project_out(tmp_path, monkeypatch):
    proj = tmp_path / "board"
    assert main(["init", str(proj)]) == 0
    monkeypatch.chdir(proj)
    assert main(["build"]) == 0
    outs = {p.suffix for p in (proj / "out").iterdir() if p.is_file()}
    assert ".net" in outs and ".kicad_sch" in outs


def test_bare_check_outside_project_errors(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["check"]) == 1
    assert "Ohm project" in capsys.readouterr().err


def test_ohm_toml_target_drives_build(tmp_path, monkeypatch, capsys):
    # ohm.toml's target is authoritative for a bare build: an unsupported target
    # fails honestly instead of being silently ignored.
    proj = tmp_path / "board"
    assert main(["init", str(proj)]) == 0
    toml = (proj / "ohm.toml").read_text(encoding="utf-8")
    (proj / "ohm.toml").write_text(toml.replace('target = "kicad7"', 'target = "kicad8"'),
                                   encoding="utf-8")
    monkeypatch.chdir(proj)
    assert main(["build"]) == 1
    assert "only KiCad 7" in capsys.readouterr().err
    # an explicit flag still wins over the project's target
    assert main(["build", "--target-kicad", "7", "-o", str(tmp_path / "o")]) == 0


def test_explicit_out_overrides_project(tmp_path, monkeypatch):
    proj = tmp_path / "board"
    assert main(["init", str(proj)]) == 0
    monkeypatch.chdir(proj)
    dest = tmp_path / "elsewhere"
    assert main(["build", "-o", str(dest)]) == 0
    assert dest.is_dir() and any(dest.iterdir())


# ---------- ohm doctor ----------
def test_doctor_reports_and_passes(capsys):
    assert main(["doctor"]) == 0                     # py3.12 + lark present here
    out = capsys.readouterr().out
    assert "ohm doctor" in out and "Python" in out and "[ OK ]" in out


# ---------- ohm refactor ----------
def test_refactor_dry_run_changes_nothing(tmp_path, capsys):
    d = tmp_path / "messy"
    d.mkdir()
    (d / "blink.ohm").write_text("module Blink:\n    pass\n", encoding="utf-8")
    assert main(["refactor", str(d)]) == 0
    assert "dry-run" in capsys.readouterr().out
    assert not (d / "ohm.toml").exists()             # nothing written
    assert not (d / "lib").exists()
    assert (d / "blink.ohm").exists()


def test_refactor_apply_structures_dir(tmp_path):
    d = tmp_path / "messy"
    d.mkdir()
    (d / "messy.ohm").write_text("module M:\n    pass\n", encoding="utf-8")
    assert main(["refactor", str(d), "--apply"]) == 0
    assert (d / "ohm.toml").is_file()
    assert (d / "AGENTS.md").is_file()
    assert (d / "docs" / "LANGUAGE.md").is_file()
    assert (d / "lib").is_dir()
    assert 'entry = "messy.ohm"' in (d / "ohm.toml").read_text(encoding="utf-8")


def test_refactor_moves_stray_ohm_to_lib(tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    (d / "top.ohm").write_text("module Top:\n    pass\n", encoding="utf-8")
    (d / "helper.ohm").write_text("module Helper:\n    pass\n", encoding="utf-8")
    assert main(["refactor", str(d), "--entry", "top.ohm", "--apply"]) == 0
    assert (d / "top.ohm").is_file()                 # entry stays at root
    assert (d / "lib" / "helper.ohm").is_file()      # the rest move to lib/
    assert not (d / "helper.ohm").exists()


def test_refactor_ambiguous_entry_asks(tmp_path, capsys):
    d = tmp_path / "ambig"
    d.mkdir()
    (d / "a.ohm").write_text("module A:\n    pass\n", encoding="utf-8")
    (d / "b.ohm").write_text("module B:\n    pass\n", encoding="utf-8")
    assert main(["refactor", str(d)]) == 1
    err = capsys.readouterr().err
    assert "--entry" in err and "a.ohm" in err and "b.ohm" in err


def test_refactor_apply_never_overwrites(tmp_path):
    d = tmp_path / "keep"
    d.mkdir()
    (d / "keep.ohm").write_text("module M:\n    pass\n", encoding="utf-8")
    (d / "README.md").write_text("SENTINEL\n", encoding="utf-8")
    assert main(["refactor", str(d), "--apply"]) == 0
    assert (d / "README.md").read_text(encoding="utf-8") == "SENTINEL\n"
