"""ADR-002 section 5, closed: Ohm's emitted `.zen` read by the real `pcb` binary.

ADR-002 shipped in July 2026 with its last clause deferred — "the optional `pcb`
toolchain cross-check is deferred until the `pcb` binary is available locally" —
and it stayed deferred for a year, which is how a `.zen` export that *no `pcb` had
ever loaded* went on being described as "the universal Component() form". It was
not universal. diodeinc/pcb 0.4.41 refuses it outright:

    Either `pin_defs` or a Symbol value for `symbol` must be provided

That is the whole argument for this file. A round-trip test against Ohm's own
importer proves the two halves of `ohm/` agree with each other; only the vendor's
own compiler can say whether either half agrees with Zener. Everything in
`ohm/emit_zen.py` that changed on 2026-08-31 changed because of a message printed
by the binary this module drives.

Skipping honestly, and what "honestly" means here:

* No binary -> skip. Ordinary, and the same shape as `requires_kicad_cli` in
  conftest.py.
* A binary that cannot run in this environment -> skip, with the tool's own words
  in the reason so it prints. Windows without Developer Mode is the live example:
  `pcbc` materialises its stdlib through a symlink and dies with "A required
  privilege is not held by the client. (os error 1314)" before it reads a line of
  Zener. That is a fact about the host, not about Ohm's output, and a red gate
  there would teach people to ignore this file. The probe that decides is a build
  of a hand-written component known to be valid — so a skip means "the toolchain
  could not run", never "Ohm's output was not checked".
* Anything else -> a failure. In particular the BOM-completeness diagnostics are
  suppressed with `-S bom` and NOTHING else is: see the comment on BOM_SCOPE.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from ohm.driver import compile_source
from ohm.emit_zen import emit_zen
from ohm.formats import _ZEN_PCB_VERSION

GOLDEN = Path(__file__).parent / "golden"

# `pcb` is the CLI's name once installed; `pcbc` is what the release assets are
# called (pcbc-x86_64-pc-windows-msvc.exe and friends). OHM_PCB names a binary
# directly, for a checkout that has one without putting it on PATH.
PCB = (os.environ.get("OHM_PCB")
       or shutil.which("pcb") or shutil.which("pcbc"))

# The manifest `pcbc` requires in order to see a directory as a workspace at all.
# `pcb-version` is the toolchain lane, and it is the SAME string the registry pins
# and `emit_zen` writes into the file's header comment — one value, three places
# that would disagree loudly if anyone moved it.
PCB_TOML = f'[workspace]\nname = "ohm-interop"\npcb-version = "{_ZEN_PCB_VERSION}"\n'

# The one diagnostic family this gate suppresses, and why it is not a weakened test.
# `bom.unspecified` / `bom.underspecified` fire when a Component has no
# `part=Part(mpn=..., manufacturer=...)`. That is a statement about how completely
# the *design* is sourced, not about whether the *file* is valid Zener: Ohm's own
# M001 makes the identical complaint about the identical designs, so gating on it
# here would only assert that Ohm's golden fixtures are not order-ready, which is
# already known and is not what this file is for. Everything else — syntax,
# unknown kwargs, missing pin maps, illegal instance names, unconnected pins
# declared in `pin_defs` — stays fatal.
BOM_SCOPE = ["-S", "bom"]

# A component that is valid Zener by construction, used only to decide whether the
# toolchain works on this machine. `skip_bom` keeps it out of the BOM entirely, so
# it needs no suppression and no sourcing data: if THIS does not build, nothing
# about Ohm is being measured.
PROBE_ZEN = (
    'V = Net("V")\n'
    'Component(\n'
    '    name="U1",\n'
    '    footprint="SOT-23-5",\n'
    '    prefix="U",\n'
    '    pin_defs={"VCC": "1"},\n'
    '    pins={"VCC": V},\n'
    '    skip_bom=True,\n'
    ')\n')


def _build(source: str, *extra: str) -> subprocess.CompletedProcess:
    """Write `source` into a throwaway workspace and build it with `pcb`."""
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        (work / "pcb.toml").write_text(PCB_TOML, encoding="utf-8", newline="\n")
        (work / "d.zen").write_text(source, encoding="utf-8", newline="\n")
        return subprocess.run([PCB, "build", *extra, "d.zen"], cwd=work,
                              capture_output=True, text=True, timeout=300)


def _unusable() -> str | None:
    """None if `pcb` can build a known-good file here; else why it cannot."""
    if PCB is None:
        return "pcb (diodeinc/pcb) toolchain not installed"
    try:
        probe = _build(PROBE_ZEN)
    except OSError as exc:                      # not executable on this platform
        return f"pcb present but not runnable: {exc}"
    if probe.returncode != 0:
        detail = (probe.stderr or probe.stdout).strip().splitlines()
        return ("pcb present but cannot build in this environment: "
                + (detail[0] if detail else f"exit {probe.returncode}"))
    return None


UNUSABLE = _unusable()
needs_pcb = pytest.mark.skipif(UNUSABLE is not None, reason=UNUSABLE or "")


@needs_pcb
@pytest.mark.parametrize("design", sorted(p.name for p in GOLDEN.glob("*.ohm")))
def test_every_golden_design_exports_a_zen_that_pcb_builds(design):
    """The gate itself: compile an Ohm design, export it, and hand the file to the
    vendor's compiler. It elaborates the netlist, so a pass means the Components
    resolved, the pin maps were accepted and every net found its endpoints."""
    result = compile_source((GOLDEN / design).read_text(), design)
    assert result.ok, [d.machine() for d in result.diagnostics]
    built = _build(emit_zen(result.low, design), *BOM_SCOPE)
    report = built.stdout + built.stderr  # 0.4.41 prints the "✓ ... (N components)"
    assert built.returncode == 0, report   # summary line on stderr, not stdout
    assert f"({len(result.low.parts)} components)" in report, report


@needs_pcb
def test_a_fully_sourced_design_needs_no_suppression_at_all():
    """With both halves of the sourcing data present, `emit_zen` writes
    `part=Part(...)` and the file builds clean — no `-S`, nothing suppressed. This
    is the test that says the BOM_SCOPE above is scoping and not hiding."""
    src = ('component NCP:\n'
           '    package = "TSOT-23-5"\n'
           '    mpn = "NCP1529ASNT1G"\n'
           '    manufacturer = "onsemi"\n'
           '    pin VIN: role=power_in, pad=1\n'
           '    pin GND: role=ground, pad=2\n'
           'module M:\n'
           '    a = NCP()\n'
           '    b = NCP()\n'
           '    a.VIN ~ b.VIN\n'
           '    a.GND ~ b.GND\n')
    result = compile_source(src, "m.ohm")
    assert result.ok, [d.machine() for d in result.diagnostics]
    built = _build(emit_zen(result.low, "m.ohm"))
    assert built.returncode == 0, built.stdout + built.stderr


@needs_pcb
def test_the_form_ohm_used_to_emit_is_the_form_pcb_rejects():
    """The finding that justified rewriting the exporter, kept executable.

    If a future `pcb` starts accepting a bare `Component(name=, footprint=,
    prefix=, pins=)` again, this test fails and someone gets to reconsider whether
    `pin_defs` is still the right thing to write. Until then it is the evidence
    that the old export was broken rather than merely old-fashioned."""
    legacy = ('V = Net("V")\n'
              'Component(name="U1", footprint="SOT-23-5", prefix="U",'
              ' pins={"VCC": V})\n')
    built = _build(legacy, *BOM_SCOPE)
    assert built.returncode != 0
    assert "pin_defs" in built.stdout + built.stderr


@needs_pcb
def test_the_emitted_file_is_already_pcb_fmt_clean():
    """`pcb fmt` is the ecosystem's own formatter, and it disagrees with the
    published spec's examples: the spec writes `name = "U1"`, the formatter wants
    `name="U1"`. `emit_zen` follows the formatter, so a `.zen` Ohm produced does
    not show up as a diff the moment somebody runs `pcb fmt` over the repo it
    landed in."""
    result = compile_source((GOLDEN / "led.ohm").read_text(), "led.ohm")
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        (work / "pcb.toml").write_text(PCB_TOML, encoding="utf-8", newline="\n")
        (work / "d.zen").write_text(emit_zen(result.low, "led.ohm"),
                                    encoding="utf-8", newline="\n")
        out = subprocess.run([PCB, "fmt", "--check", "d.zen"], cwd=work,
                             capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, out.stdout + out.stderr
