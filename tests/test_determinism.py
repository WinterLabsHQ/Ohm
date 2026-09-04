"""The determinism invariant, in the direction a single machine cannot see.

Page 8 of the architecture document promises that a build is "reproducible
forever ... the same source is the same bytes on any machine, any day". The
suite has always checked the WITHIN-machine half — `test_double_build_byte_identical`
rebuilds and compares on whatever machine is running. This file covers the
inputs that make a build differ WITHOUT the machine changing, which is how the
cross-platform gate (`tools/emit_manifest.py`, and the `determinism` job in
.github/workflows/ci.yml) found the defect below.
"""

import inspect
from pathlib import Path

import pytest

import ohm
from ohm.driver import compile_source
from ohm.emit_allegro import emit_allegro
from ohm.emit_constraints import emit_constraints
from ohm.emit_kicad import _source_id, emit_kicad_pcb, emit_kicad_sch
from ohm.emit_kicad_pro import emit_kicad_pro
from ohm.emit_netlist import emit_netlist
from ohm.emit_ohm import emit_ohm
from ohm.emit_zen import emit_zen

GOLDEN = Path(ohm.__file__).parent.parent / "tests" / "golden"

# Every emitter that takes a `source`, and therefore every way the caller's cwd
# can reach the emitted bytes. Kept as a table rather than as two hand-picked
# assertions because two hand-picked assertions is what this file used to be:
# `emit_kicad_sch` and `emit_kicad_pcb` were covered and fixed, and `.kicad_pro`,
# `.net` and `.zen` went on embedding the path as typed for another round.
PATH_TAKING_EMITTERS = {
    "emit_kicad_sch": lambda low, src, design: emit_kicad_sch(low, src, design),
    "emit_kicad_pcb": lambda low, src, design: emit_kicad_pcb(low, src),
    "emit_kicad_pro": lambda low, src, design: emit_kicad_pro(low, src, design),
    "emit_netlist": lambda low, src, design: emit_netlist(low, src),
    "emit_zen": lambda low, src, design: emit_zen(low, src),
    "emit_constraints": lambda low, src, design: emit_constraints(low, src),
    "emit_ohm": lambda low, src, design: emit_ohm(low, src),
    "emit_allegro": lambda low, src, design: "".join(
        t for _n, t in sorted(emit_allegro(design, low, src).items())),
}

# Public functions that take a `source` and are NOT emitters. Each states why,
# so "not in the table" is a decision rather than an omission.
NOT_AN_EMITTER = {
    "emit_kicad_sheets": "returns {filename: text}; its uuids come from the same "
                         "_source_id call emit_kicad_sch makes, and the sheet "
                         "list is gated by test_the_project_lists_only_sheets_"
                         "that_were_written",
    "sheet_plan": "returns the sheet decision, not bytes",
    "title_block": "builds a fragment of the schematic, not a file; normalises "
                   "`source` itself and is covered through emit_kicad_sch",
}


def _low(path: Path):
    result = compile_source(path.read_text(encoding="utf-8"), str(path))
    assert result.ok, [d.code for d in result.diagnostics]
    return result


def test_kicad_uuids_do_not_depend_on_the_typed_path():
    """The uuid that identifies a KiCad sheet must not come from the caller's cwd.

    Every uuid and tstamp in the schematic and board is uuid5 over the source
    string, and that string used to be the path as typed. So:

        ohm build tests/golden/divider.ohm         -> uuid 5d81bef0-...
        ohm build D:/repo/tests/golden/divider.ohm -> uuid 7998f527-...

    Two developers whose checkouts sit at different paths got different bytes for
    one source, and — the serious half — KiCad identifies a sheet BY its uuid, so
    rebuilding from another directory reads to KiCad as a DIFFERENT SHEET.

    Found by the cross-platform manifest gate, which initially reported every
    .kicad_sch, .kicad_pcb and .kicad_pro differing between Windows and Linux;
    the files were byte-identical when named the same way, and it was the
    invocation that differed, not the platform.
    """
    design = GOLDEN / "divider.ohm"
    result = _low(design)

    relative = "tests/golden/divider.ohm"
    absolute = str(design.resolve())
    windowsy = absolute.replace("/", "\\")

    for named in (absolute, windowsy, "./tests/golden/divider.ohm"):
        assert emit_kicad_sch(result.low, named) == emit_kicad_sch(result.low, relative), (
            f"naming the design {named!r} instead of {relative!r} changed the schematic")
        assert emit_kicad_pcb(result.low, named) == emit_kicad_pcb(result.low, relative), (
            f"naming the design {named!r} instead of {relative!r} changed the board")


def test_source_id_keeps_only_the_file_name():
    """Both separators, because a Windows caller types one and POSIX the other."""
    assert _source_id("tests/golden/divider.ohm") == "divider.ohm"
    assert _source_id(r"D:\repo\tests\golden\divider.ohm") == "divider.ohm"
    assert _source_id("divider.ohm") == "divider.ohm"
    assert _source_id("<string>") == "<string>"          # the default, unharmed


def test_source_id_is_idempotent():
    """emit_kicad_sch calls itself for hierarchical sheets, passing `source` on."""
    once = _source_id("D:/repo/tests/golden/divider.ohm")
    assert _source_id(once) == once


def test_the_table_covers_every_emitter_that_takes_a_source():
    """A new emitter with a `source` parameter is a new way for the caller's cwd
    to reach the bytes, so it must arrive in the table above by failing here.

    Found by inspecting signatures rather than by memory — remembering is what
    left `.kicad_pro`, `.net` and `.zen` out of this file the first time.
    """
    import importlib

    root = Path(ohm.__file__).parent
    found = set()
    for mod_path in sorted(root.glob("emit_*.py")):
        mod = importlib.import_module(f"ohm.{mod_path.stem}")
        for name, fn in vars(mod).items():
            if name.startswith("_") or not callable(fn):
                continue
            if getattr(fn, "__module__", None) != mod.__name__:
                continue
            try:
                params = inspect.signature(fn).parameters
            except (TypeError, ValueError):  # pragma: no cover - builtins
                continue
            if "source" in params:
                found.add(name)

    found -= set(NOT_AN_EMITTER)
    assert found == set(PATH_TAKING_EMITTERS), (
        f"emitters taking a `source` but not covered here: "
        f"{sorted(found - set(PATH_TAKING_EMITTERS))}; "
        f"listed here but gone: {sorted(set(PATH_TAKING_EMITTERS) - found)}")


@pytest.mark.parametrize("emitter", sorted(PATH_TAKING_EMITTERS))
@pytest.mark.parametrize("design", ["divider.ohm", "i2c_bus.ohm"])
def test_no_emitter_lets_the_typed_path_reach_the_bytes(emitter, design):
    """The same source, named five ways, must produce one set of bytes.

    `i2c_bus.ohm` is in the corpus on purpose: it is the only design in the repo
    with more than one top-level module, so it is the only one whose `.kicad_pro`
    carries a `sheets` list at all. Every other design emits `[]` there, which is
    why a path-derived sheet uuid survived every existing gate.
    """
    result = _low(GOLDEN / design)
    call = PATH_TAKING_EMITTERS[emitter]

    relative = f"tests/golden/{design}"
    absolute = str((GOLDEN / design).resolve())
    spellings = [absolute, absolute.replace("/", "\\"), f"./{relative}",
                 design, f"/somewhere/else/{design}"]

    want = call(result.low, relative, result.design)
    for named in spellings:
        assert call(result.low, named, result.design) == want, (
            f"{emitter}: naming the design {named!r} instead of {relative!r} "
            f"changed the emitted bytes")


def test_no_emitted_artifact_carries_a_directory_from_the_caller():
    """The other half of the same defect: what LEAKS, not just what varies.

    A `.net` handed to a fab carried `C:/Users/<name>/checkout/...` because the
    netlist stamps its source path verbatim. Byte-identity would be satisfied by
    every machine leaking the same path; this asserts the path is not there.
    """
    result = _low(GOLDEN / "i2c_bus.ohm")
    marker = "/private/checkout-of-somebody/tests/golden/i2c_bus.ohm"
    for name, call in sorted(PATH_TAKING_EMITTERS.items()):
        text = call(result.low, marker, result.design)
        assert "checkout-of-somebody" not in text, (
            f"{name} emitted a directory from the caller's path")
        assert "/private/" not in text, f"{name} emitted the caller's path"


def test_the_project_lists_only_sheets_that_were_written():
    """A `.kicad_pro` must not name a `.kicad_sch` that does not exist.

    The sheet decision used to be made twice, by two different predicates:
    `emit_kicad_sheets` decides by FIT (a drawing that fits one reviewable page
    stays on one page), and `emit_kicad_pro` asked the cheaper
    `len(sheet_groups(low)) > 1`. On `i2c_bus.ohm` — the only multi-module
    design in the repo, and one that fits — the project listed a root and two
    child sheets, named `i2c_bus_mcu.kicad_sch` and `i2c_bus_sens.kicad_sch`
    which were never emitted, and not one of the three uuids appeared in the
    single sheet that was. `emit_kicad_pro`'s own comment says "KiCad will not
    open a hierarchy it cannot find in the project".
    """
    import json

    from ohm.emit_kicad import emit_kicad_sheets, sheet_plan

    design = GOLDEN / "i2c_bus.ohm"
    result = _low(design)
    src = "tests/golden/i2c_bus.ohm"

    for force in (False, True):
        written = emit_kicad_sheets(result.low, src, result.design, force=force)
        groups = sheet_plan(result.low, src, result.design, force=force)
        pro = json.loads(emit_kicad_pro(result.low, src, result.design, groups))
        sheets = pro.get("sheets") or []

        all_text = "".join(written.values())
        for uuid, name in sheets:
            assert uuid in all_text, (
                f"force={force}: the project names sheet {name or '(root)'} "
                f"with a uuid that appears in no emitted schematic")
        # a hierarchy in the project means a hierarchy on disk, and vice versa
        assert bool(sheets) == (len(written) > 1), (
            f"force={force}: {len(written)} sheet file(s) written, "
            f"{len(sheets)} listed in the project")
