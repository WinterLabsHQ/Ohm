"""The determinism invariant, in the direction a single machine cannot see.

Page 8 of the architecture document promises that a build is "reproducible
forever ... the same source is the same bytes on any machine, any day". The
suite has always checked the WITHIN-machine half — `test_double_build_byte_identical`
rebuilds and compares on whatever machine is running. This file covers the
inputs that make a build differ WITHOUT the machine changing, which is how the
cross-platform gate (`tools/emit_manifest.py`, and the `determinism` job in
.github/workflows/ci.yml) found the defect below.
"""

from pathlib import Path

import ohm
from ohm.driver import compile_source
from ohm.emit_kicad import _source_id, emit_kicad_pcb, emit_kicad_sch

GOLDEN = Path(ohm.__file__).parent.parent / "tests" / "golden"


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
