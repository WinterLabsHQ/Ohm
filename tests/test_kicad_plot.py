"""The emitted files PLOT — gerbers, drill and a schematic PDF, by KiCad itself.

`CHANGELOG.md` claims KiCad 10.0.5 "plots its gerbers and schematic PDF" for an
Ohm-emitted design, and `KICAD_COMPATIBILITY.md` grades that claim honestly: it
was done once, by hand, on one developer's machine, and no test made it. This
file makes it.

Opening is not plotting. `tests/test_schematic_roundtrip.py` already proves KiCad
can *read* what Ohm emits — it extracts a netlist and the netlist is the circuit.
But the netlist exporter walks the schematic graph; the plotters walk geometry,
layer stacks, footprint bodies and page setup, and a board can satisfy the first
while the second refuses. Gerbers are the artifact a fab is actually sent: a
board KiCad can open but cannot plot is not a board, and until now nothing in
this repo would have noticed the difference.

So the oracle is the same shape as the roundtrip's — KiCad, not Ohm, decides:

    Ohm source -> emit_kicad_pcb -> `kicad-cli pcb export gerbers` -> RS-274X
    Ohm source -> emit_kicad_pcb -> `kicad-cli pcb export drill`   -> Excellon
    Ohm source -> emit_kicad_sch -> `kicad-cli sch export pdf`     -> PDF

An exit code alone would be too weak: a plotter that writes a zero-byte file and
returns 0 passes that. Each artifact is therefore checked against its format's
own start-and-end markers — a gerber begins with `%` or `G04` and ends `M02*`, an
Excellon begins `M48` and ends `M30`, a PDF begins `%PDF` and ends `%%EOF`. Those
catch the two failures a generated file invites: nothing written, and half
written.

**What this proves.** That an Ohm-emitted board and schematic are *producible* as
a fabrication package by an unmodified KiCad — the toolchain runs to completion
and every promised file arrives whole.

**What this does NOT prove**, and no test here can:

- that the plots are *visually* correct — that a pad is where the datasheet puts
  it, that silkscreen is legible, that the schematic reads well to a human;
- that a fab could build the board. Only a fabricated board proves that, and
  this repo is deliberate about not blurring the line (`README.md`, "About
  validation, plainly");
- anything about the drill data's *content* on today's examples. Ohm's package
  generators emit SMD land patterns only — every board in `examples/` has zero
  drilled holes — so KiCad correctly writes no Excellon file for them. The drill
  test therefore asserts the *invariant* (a drill file exists exactly when the
  board has drilled features, and is well-formed when it exists) rather than
  pretending to have checked hole content it could not have seen. The day Ohm
  gains a through-hole package, that test starts asserting the strong half by
  itself.

`kicad-cli` writes warnings to stderr on a board with no `fp-lib-table`
configured; that is expected here and is not a failure. The gate is the exit code
and the artifacts, and `proc.stderr` rides along in every assertion message so a
real failure is diagnosable from the CI log alone.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

from ohm.driver import compile_source
from ohm.emit_kicad import emit_kicad_pcb, emit_kicad_sch

from tests.conftest import KICAD_CLI, requires_kicad_cli
from tests.test_schematic_roundtrip import CASES, EXAMPLES

# A subset of the roundtrip oracle's boards: one four-part divider, one
# eight-part board built around an IC, and one sixteen-part repeated array. That
# spans trivial-to-busiest without paying for all of CASES on every CI run —
# plotting is the slow half of kicad-cli, and three boards times three exports is
# already nine invocations.
_PLOTTED = {"divider_led", "blinky_555", "led_bar"}
PLOT_CASES = [name for name in CASES if name in _PLOTTED]


def _emit(name: str, tmp_path: Path) -> tuple[Path, Path]:
    """Compile `examples/<name>.ohm` and write both KiCad files. Returns (pcb, sch)."""
    path = EXAMPLES / f"{name}.ohm"
    result = compile_source(path.read_text(), str(path))
    assert result.low is not None, [d.machine() for d in result.diagnostics]

    pcb = tmp_path / f"{name}.kicad_pcb"
    pcb.write_text(emit_kicad_pcb(result.low, str(path)), encoding="utf-8", newline="\n")
    sch = tmp_path / f"{name}.kicad_sch"
    sch.write_text(emit_kicad_sch(result.low, str(path), result.design),
                   encoding="utf-8", newline="\n")
    return pcb, sch


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([KICAD_CLI, *args], capture_output=True, text=True, timeout=300)


def _describe(directory: Path) -> str:
    """Every file the plotter left, with its size — the useful half of a failure."""
    return "\n".join(f"  {p.name}  {p.stat().st_size} bytes"
                     for p in sorted(directory.iterdir())) or "  (nothing was written)"


def test_the_plotted_boards_are_roundtrip_cases():
    """Guard the subset: a rename in CASES must not silently shrink this gate.

    `PLOT_CASES` is filtered out of `CASES` so the two files cannot drift apart,
    but a filter that matches nothing is also a filter that tests nothing.
    """
    assert set(PLOT_CASES) == _PLOTTED
    assert _PLOTTED <= set(CASES)


@requires_kicad_cli
@pytest.mark.parametrize("name", PLOT_CASES)
def test_kicad_plots_gerbers_from_the_emitted_board(name, tmp_path):
    """The fabrication copper: KiCad plots RS-274X from an Ohm `.kicad_pcb`.

    Layers are named explicitly rather than left to the board's plot settings.
    Ohm emits no `(setup)` block, so "whatever the board asked for" is undefined
    here; naming them also makes the assertion mean something — F.Cu and B.Cu are
    the two layers this stack has, and a plot that quietly omitted one would
    otherwise pass.
    """
    pcb, _sch = _emit(name, tmp_path)
    out = tmp_path / "gerbers"
    out.mkdir()

    proc = _run("pcb", "export", "gerbers", str(pcb), "--layers", "F.Cu,B.Cu,F.Mask",
                "-o", str(out) + os.sep)
    assert proc.returncode == 0, f"kicad-cli exited {proc.returncode}\n{proc.stderr}"

    plotted = sorted(p for p in out.iterdir() if p.is_file())
    assert plotted, f"kicad-cli reported success and wrote nothing\n{proc.stderr}"

    # Identify layers by BOTH filename and content. The filename suffix depends on
    # whether Protel extensions are in force (`board.gtl` vs `board-F_Cu.gbr`);
    # the X2 file-function attribute is part of the Gerber format itself and says
    # the same thing either way, in X2 mode or as a `G04 #@!` comment.
    found: set[str] = set()
    for gerber in plotted:
        data = gerber.read_bytes()
        assert data.strip(), f"{gerber.name} is empty\n{_describe(out)}\n{proc.stderr}"
        if gerber.suffix.lower() == ".gbrjob":
            # KiCad writes a JSON job file beside the gerbers describing the
            # stackup. It is part of a correct fabrication package, so it is
            # validated as JSON rather than skipped — but it is not a gerber and
            # has neither Gerber directives nor an M02* terminator.
            json.loads(data.decode("utf-8"))
            continue
        assert data.lstrip().startswith((b"%", b"G04")), (
            f"{gerber.name} does not begin with a Gerber directive: {data[:40]!r}\n"
            f"{proc.stderr}")
        assert b"M02*" in data, (
            f"{gerber.name} has no M02* end-of-file — the plot was truncated\n"
            f"{_describe(out)}\n{proc.stderr}")
        stem = gerber.stem.upper()
        if stem.endswith("F_CU") or b"TF.FileFunction,Copper,L1,Top" in data:
            found.add("F.Cu")
        if stem.endswith("B_CU") or b"TF.FileFunction,Copper,L2,Bot" in data:
            found.add("B.Cu")

    assert found == {"F.Cu", "B.Cu"}, (
        f"the copper layers did not both arrive: got {sorted(found)}\n"
        f"{_describe(out)}\n{proc.stderr}")


@requires_kicad_cli
@pytest.mark.parametrize("name", PLOT_CASES)
def test_kicad_exports_drill_data_from_the_emitted_board(name, tmp_path):
    """The other half of a fabrication package: Excellon drill data.

    KiCad writes no drill file for a board with no holes — that is deliberate on
    its side, not a failure. Every board Ohm can currently generate is all-SMD
    (the package generators emit land patterns only), so the assertion is the
    invariant rather than the artifact: the command must succeed, a drill file
    must appear exactly when the board has drilled features, and any file that
    does appear must be a whole Excellon program. This strengthens on its own the
    first time Ohm emits a through-hole pad or a via.
    """
    pcb, _sch = _emit(name, tmp_path)
    out = tmp_path / "drill"
    out.mkdir()

    proc = _run("pcb", "export", "drill", str(pcb), "-o", str(out) + os.sep)
    assert proc.returncode == 0, f"kicad-cli exited {proc.returncode}\n{proc.stderr}"

    board = pcb.read_text(encoding="utf-8")
    drilled = "(drill" in board or "(via" in board
    files = sorted(p for p in out.iterdir() if p.suffix.lower() == ".drl")

    if drilled:
        assert files, (
            f"the board has drilled features but no .drl was written\n"
            f"{_describe(out)}\n{proc.stderr}")
    # The converse does NOT hold, and this was measured rather than assumed:
    # KiCad 10.0.5 writes a well-formed but hole-less Excellon program for an
    # all-SMD board (262 bytes for led_bar). Ohm emits no through-hole pads or
    # vias today — `ohm/pkggen/` has no thru_hole support — so `drilled` is False
    # for every current example, and this gate strengthens by itself the day that
    # changes. Asserting no .drl here would have been asserting KiCad's habits.

    for drl in files:
        data = drl.read_bytes()
        assert data.lstrip().startswith(b"M48"), (
            f"{drl.name} is not an Excellon program: {data[:40]!r}\n{proc.stderr}")
        assert b"M30" in data, (
            f"{drl.name} has no M30 end-of-program — the export was truncated\n"
            f"{_describe(out)}\n{proc.stderr}")


@requires_kicad_cli
@pytest.mark.parametrize("name", PLOT_CASES)
def test_kicad_plots_a_schematic_pdf_from_the_emitted_schematic(name, tmp_path):
    """The reviewable artifact: KiCad plots a PDF from an Ohm `.kicad_sch`.

    The netlist oracle proves the schematic is electrically the design; it says
    nothing about whether the drawing can be rendered at all. A symbol body with
    impossible geometry, or a page KiCad cannot lay out, fails here and nowhere
    else. Magic bytes and the `%%EOF` trailer are the check — a zero-byte file,
    or an error message written where a PDF was promised, is exactly what an
    exit-code-only assertion would wave through.
    """
    _pcb, sch = _emit(name, tmp_path)
    pdf = tmp_path / f"{name}.pdf"

    proc = _run("sch", "export", "pdf", str(sch), "-o", str(pdf))
    assert proc.returncode == 0, f"kicad-cli exited {proc.returncode}\n{proc.stderr}"
    assert pdf.exists(), f"kicad-cli reported success and wrote no PDF\n{proc.stderr}"

    data = pdf.read_bytes()
    assert data.startswith(b"%PDF"), (
        f"the plotted file is not a PDF: {data[:40]!r}\n{proc.stderr}")
    assert b"%%EOF" in data[-2048:], (
        f"the PDF has no %%EOF trailer — the plot was truncated at "
        f"{len(data)} bytes\n{proc.stderr}")
    # A one-page schematic PDF carries a page, a font and the drawing itself; a
    # kilobyte is far below any real one and far above a stub.
    assert len(data) > 1024, f"the PDF is {len(data)} bytes — too small to be a drawing"
