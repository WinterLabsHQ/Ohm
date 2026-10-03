"""The std-module parse memo (loader._parse_std): a hit only on unchanged text, a
miss on any edit, never an entry for the user's own files, and no output a
memoised load could produce that a cold one would not."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from ohm import loader
from ohm.driver import compile_source
from ohm.emit_netlist import emit_netlist
from ohm.lsp import _lsp_diagnostics

ROOT = Path(__file__).parent.parent
DESIGNS = [p for p in [ROOT / "tests" / "fixtures" / "canonical.ohm",
                       *sorted((ROOT / "examples").glob("*.ohm"))]
           if "use std." in p.read_text(encoding="utf-8-sig")]


@pytest.fixture
def parses(monkeypatch):
    """Clear the memo, and record every real `parse` the loader makes, by file."""
    monkeypatch.setattr(loader, "_STD_PARSE_MEMO", {})
    seen: list[str] = []
    real = loader.parse

    def counting(text, path="<string>"):
        seen.append(path)
        return real(text, path)

    monkeypatch.setattr(loader, "parse", counting)
    return seen


def _std_parses(seen: list[str]) -> list[str]:
    return [p for p in seen if Path(p).parent == loader._STD_DIR_RESOLVED]


def test_unchanged_std_text_is_parsed_once_per_process(parses):
    source = "use std.passives\nuse std.pcb\n"
    first, d1 = loader.load(source)
    cold = list(parses)
    second, d2 = loader.load(source)
    # std.passives itself uses std.kinds, so three std files, each parsed once
    assert sorted(Path(p).stem for p in _std_parses(cold)) == ["kinds", "passives", "pcb"]
    assert _std_parses(parses[len(cold):]) == [], "the second load re-parsed a std module"
    assert first == second and d1 == d2
    assert {label for label, _ in loader._STD_PARSE_MEMO} == set(_std_parses(cold))


def test_an_edited_std_file_is_a_miss_not_a_stale_hit(parses, monkeypatch, tmp_path):
    std = tmp_path / "std"
    std.mkdir()
    lib = std / "lib.ohm"
    monkeypatch.setattr(loader, "_STD_DIR", std)
    monkeypatch.setattr(loader, "_STD_DIR_RESOLVED", std.resolve())
    lib.write_text("component A:\n    pin P1: role=passive, pad=1\n", encoding="utf-8")
    before, _ = loader.load("use std.lib\n")
    lib.write_text("component B:\n    pin P1: role=passive, pad=1\n", encoding="utf-8")
    after, _ = loader.load("use std.lib\n")
    assert [d.name for d in before.decls] == ["A"]
    assert [d.name for d in after.decls] == ["B"]
    assert len(_std_parses(parses)) == 2
    assert len(loader._STD_PARSE_MEMO) == 2  # keyed by content: both texts, one path


def test_the_entry_and_user_imports_are_never_memoised(parses, tmp_path):
    (tmp_path / "lib.ohm").write_text("component A:\n    pin P1: role=passive, pad=1\n",
                                      encoding="utf-8")
    entry = tmp_path / "top.ohm"
    source = 'use "./lib.ohm"\nuse std.passives\n'
    for _ in range(2):
        loader.load(source, str(entry))
    user = [p for p in parses if Path(p).parent != loader._STD_DIR_RESOLVED]
    assert user == [str(entry), str((tmp_path / "lib.ohm").resolve())] * 2
    assert all(Path(label).parent == loader._STD_DIR_RESOLVED for label, _ in loader._STD_PARSE_MEMO)


def test_the_memo_is_bounded(monkeypatch):
    monkeypatch.setattr(loader, "_STD_PARSE_MEMO", {})
    for i in range(loader._STD_PARSE_MEMO_MAX * 2):
        loader._parse_std(f"# {i}\n", "std.ohm")
        assert len(loader._STD_PARSE_MEMO) <= loader._STD_PARSE_MEMO_MAX


def test_a_hit_hands_back_a_fresh_diagnostics_list(monkeypatch):
    monkeypatch.setattr(loader, "_STD_PARSE_MEMO", {})
    _, first = loader._parse_std("component\n", "std.ohm")
    first.append("caller's own")
    _, second = loader._parse_std("component\n", "std.ohm")
    assert first[:-1] == second and "caller's own" not in second


def _walk_mutables(node, path="program"):
    if isinstance(node, (list, dict, set, bytearray)):
        yield path
    if dataclasses.is_dataclass(node):
        assert type(node).__dataclass_params__.frozen, f"{path}: {type(node).__name__} is not frozen"
        for f in dataclasses.fields(node):
            yield from _walk_mutables(getattr(node, f.name), f"{path}.{f.name}")
    elif isinstance(node, (tuple, list)):
        for i, item in enumerate(node):
            yield from _walk_mutables(item, f"{path}[{i}]")


@pytest.mark.parametrize("module", loader.std_modules())
def test_what_the_memo_holds_cannot_be_mutated(module, monkeypatch):
    """The soundness premise: a memoised AST is frozen dataclasses and tuples all
    the way down, so no caller can change what the next keystroke is handed."""
    monkeypatch.setattr(loader, "_STD_PARSE_MEMO", {})
    path = loader._STD_DIR_RESOLVED / f"{module}.ohm"
    program, diags = loader._parse_std(path.read_text(encoding="utf-8-sig"), str(path))
    assert list(_walk_mutables(program)) == []
    assert list(_walk_mutables(tuple(diags), "diags")) == []


@pytest.mark.parametrize("design", DESIGNS, ids=lambda p: p.stem)
def test_a_warm_memo_compiles_byte_identically_to_a_cold_one(design, monkeypatch):
    source, path = design.read_text(encoding="utf-8-sig"), str(design)
    monkeypatch.setattr(loader, "_STD_PARSE_MEMO", {})
    cold = compile_source(source, path)
    cold_lsp = _lsp_diagnostics(source, design.as_uri())
    warm = compile_source(source, path)
    warm_lsp = _lsp_diagnostics(source, design.as_uri())
    monkeypatch.setattr(loader, "_parse_std", loader.parse)  # the memo taken out entirely
    bare = compile_source(source, path)
    assert ([d.machine() for d in cold.diagnostics] == [d.machine() for d in warm.diagnostics]
            == [d.machine() for d in bare.diagnostics])
    assert cold.diagnostics == warm.diagnostics == bare.diagnostics
    assert cold_lsp == warm_lsp == _lsp_diagnostics(source, design.as_uri())
    if cold.ok:
        assert emit_netlist(cold.low, path) == emit_netlist(warm.low, path) == emit_netlist(bare.low, path)
