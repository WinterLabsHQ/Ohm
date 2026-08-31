"""`ohm/kicad_env.py`: where is KiCad, answered once.

This is the resolver `ohm doctor` and every environment-gated test now share. It
is tested with synthetic directory trees rather than the machine's real KiCad,
so the answers are the same on a CI runner, a developer's Windows box and a
container — which is the property the old five-copies-of-one-Linux-path
arrangement did not have.

The bug being gated: the probe knew about KiCad 7, 8 and 9 and stopped. On a
machine running KiCad 10 — the version the README certifies — `ohm doctor`
reported "KiCad libraries not detected", and every library test skipped.
"""

from __future__ import annotations

import os

import pytest

from ohm import kicad_env
from ohm.cli import SUPPORTED_KICAD, main


def _tree(root, prefix: str, kinds=("footprints", "symbols")):
    """Build a fake KiCad share tree and return its share root."""
    share = root / prefix
    for kind in kinds:
        (share / kind).mkdir(parents=True, exist_ok=True)
    return share


@pytest.fixture(autouse=True)
def _no_ambient_kicad(monkeypatch):
    """Every KICAD* variable cleared, so a developer's real KiCad cannot make a
    test pass that would fail on a bare runner."""
    for key in list(os.environ):
        if key.startswith("KICAD") or key == "OHM_KICAD_CLI":
            monkeypatch.delenv(key, raising=False)


# ---------- environment variables ----------
def test_versioned_env_var_is_honoured(tmp_path, monkeypatch):
    share = _tree(tmp_path, "kicad10")
    monkeypatch.setenv("KICAD10_FOOTPRINT_DIR", str(share / "footprints"))
    assert kicad_env.footprints_dir() == str(share / "footprints")


def test_every_supported_major_has_an_env_var(tmp_path, monkeypatch):
    """KiCad sets KICAD<major>_FOOTPRINT_DIR itself; missing one silently drops
    that version's users back to path probing."""
    for major in SUPPORTED_KICAD:
        share = _tree(tmp_path, f"k{major}")
        monkeypatch.setenv(f"KICAD{major}_FOOTPRINT_DIR", str(share / "footprints"))
        assert kicad_env.footprints_dir() == str(share / "footprints"), \
            f"KICAD{major}_FOOTPRINT_DIR ignored"
        monkeypatch.delenv(f"KICAD{major}_FOOTPRINT_DIR")


def test_unversioned_env_var_is_honoured(tmp_path, monkeypatch):
    share = _tree(tmp_path, "hand-set")
    monkeypatch.setenv("KICAD_FOOTPRINT_DIR", str(share / "footprints"))
    assert kicad_env.footprints_dir() == str(share / "footprints")


def test_newer_kicad_wins_over_older(tmp_path, monkeypatch):
    """Two KiCads installed: report the one that will actually open the file."""
    new = _tree(tmp_path, "k10")
    old = _tree(tmp_path, "k9")
    monkeypatch.setenv("KICAD9_FOOTPRINT_DIR", str(old / "footprints"))
    monkeypatch.setenv("KICAD10_FOOTPRINT_DIR", str(new / "footprints"))
    assert kicad_env.footprints_dir() == str(new / "footprints")


def test_env_var_pointing_nowhere_is_ignored(tmp_path, monkeypatch):
    """A stale variable from an uninstalled KiCad must not shadow a real one."""
    real = _tree(tmp_path, "real")
    monkeypatch.setenv("KICAD10_FOOTPRINT_DIR", str(tmp_path / "gone"))
    monkeypatch.setenv("KICAD9_FOOTPRINT_DIR", str(real / "footprints"))
    assert kicad_env.footprints_dir() == str(real / "footprints")


def test_symbols_and_footprints_are_resolved_separately(tmp_path, monkeypatch):
    share = _tree(tmp_path, "k10")
    monkeypatch.setenv("KICAD10_SYMBOL_DIR", str(share / "symbols"))
    monkeypatch.setenv("KICAD10_FOOTPRINT_DIR", str(share / "footprints"))
    assert kicad_env.symbols_dir() == str(share / "symbols")
    assert kicad_env.footprints_dir() == str(share / "footprints")


def test_nothing_installed_reports_nothing(monkeypatch):
    """Absent KiCad is None, not a path that does not exist — a caller that
    stats the result must not be handed a lie."""
    monkeypatch.setattr(kicad_env, "_prefixes", lambda: [])
    monkeypatch.setattr(kicad_env.shutil, "which", lambda _: None)
    assert kicad_env.footprints_dir() is None
    assert kicad_env.symbols_dir() is None
    assert kicad_env.kicad_root() is None


# ---------- the probe list ----------
def test_probe_list_covers_every_supported_major(monkeypatch):
    """Adding a KiCad release must cost one number, not an edit in five files."""
    prefixes = " ".join(kicad_env._prefixes())
    for major in SUPPORTED_KICAD:
        assert f"KiCad/{major}.0" in prefixes, f"no Windows probe for KiCad {major}"


def test_probe_list_covers_all_three_platforms():
    prefixes = kicad_env._prefixes()
    assert any("Program Files" in p for p in prefixes), "no Windows path"
    assert any(p.startswith("/usr/share") for p in prefixes), "no Linux path"
    assert any("KiCad.app" in p for p in prefixes), "no macOS path"


def test_probe_prefers_newer_windows_install(tmp_path, monkeypatch):
    """Probing, not just the env vars, must be newest-first."""
    roots = {m: _tree(tmp_path, f"pf/KiCad/{m}.0/share/kicad") for m in ("9", "10")}
    monkeypatch.setattr(kicad_env, "_prefixes",
                        lambda: [str(tmp_path / "pf/KiCad/10.0/share/kicad"),
                                 str(tmp_path / "pf/KiCad/9.0/share/kicad")])
    assert kicad_env.footprints_dir() == str(roots["10"] / "footprints")


# ---------- kicad-cli ----------
def test_ohm_kicad_cli_overrides_the_installed_binary(monkeypatch):
    """The seam that lets one gate certify a KiCad that is not installed."""
    monkeypatch.setenv("OHM_KICAD_CLI", "/opt/kicad-10/bin/kicad-cli")
    assert kicad_env.kicad_cli() == "/opt/kicad-10/bin/kicad-cli"


def test_kicad_cli_falls_back_to_path(monkeypatch):
    monkeypatch.setattr(kicad_env.shutil, "which",
                        lambda name: "/usr/bin/kicad-cli" if name == "kicad-cli" else None)
    assert kicad_env.kicad_cli() == "/usr/bin/kicad-cli"


def test_kicad_root_prefers_libraries_over_the_binary(tmp_path, monkeypatch):
    """`ohm doctor` reports what an emitted .kicad_pcb needs to OPEN, which is
    the libraries; the binary alone is the weaker answer, not the better one."""
    share = _tree(tmp_path, "k10")
    monkeypatch.setenv("KICAD10_FOOTPRINT_DIR", str(share / "footprints"))
    monkeypatch.setattr(kicad_env.shutil, "which", lambda _: "/usr/bin/kicad-cli")
    assert kicad_env.kicad_root() == str(share / "footprints")


def test_kicad_root_falls_back_to_the_binary(monkeypatch):
    """A libraries-less install is present, not missing."""
    monkeypatch.setattr(kicad_env, "_prefixes", lambda: [])
    monkeypatch.setattr(kicad_env.shutil, "which", lambda _: "/usr/bin/kicad-cli")
    assert kicad_env.kicad_root() == "/usr/bin/kicad-cli"


# ---------- the CLI actually uses it ----------
def test_doctor_reports_a_kicad_10_only_machine_as_present(tmp_path, monkeypatch, capsys):
    """The regression this module exists for: KiCad 10 installed and nothing
    else, and `ohm doctor` said "not detected"."""
    share = _tree(tmp_path, "kicad10")
    monkeypatch.setenv("KICAD10_FOOTPRINT_DIR", str(share / "footprints"))
    assert main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "KiCad libraries" in out
    assert "not detected" not in out


def test_verify_pinouts_lib_dir_default_is_resolved(monkeypatch):
    """`--lib-dir` defaulted to a Linux path, so the flag had no usable default
    on Windows or macOS at all."""
    from ohm.cli import _default_symbol_dir

    monkeypatch.setattr(kicad_env, "_prefixes", lambda: [])
    assert _default_symbol_dir() == "/usr/share/kicad/symbols"
