"""The schematic is CONNECTED, not merely drawn.

Ohm used to emit a `.kicad_sch` containing text labels and global labels with an
empty `(lib_symbols)` — no symbols, no wires, nothing a human could review and
nothing KiCad could extract a net from. This gate proves the replacement is real
by making KiCad itself the oracle:

    Ohm source -> emit_kicad_sch -> `kicad-cli sch export netlist` -> net graph

and comparing that against Ohm's OWN netlist. If the two agree pin-for-pin, the
schematic is electrically the design. Nothing weaker is worth asserting: a
schematic can look plausible and be disconnected, which is exactly the failure a
generated diagram invites.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from ohm.driver import compile_source
from ohm.emit_kicad import emit_kicad_sch
from ohm.emit_netlist import emit_netlist

EXAMPLES = Path(__file__).parent.parent / "examples"
KICAD_CLI = shutil.which("kicad-cli")
requires_kicad_cli = pytest.mark.skipif(KICAD_CLI is None, reason="kicad-cli not installed")

CASES = ["divider_led", "io_expander_board", "kind_demo_power_amp",
         "blinky_555", "led_bar"]


def _nets(text: str) -> dict[str, set[tuple[str, str]]]:
    """(net name -> {(ref, pin)}) from a KiCad S-expression netlist.

    KiCad invents `unconnected-(U1-NC-Pad4)` nets for pins on no net; Ohm simply
    omits them. Those are the same statement, so they are excluded rather than
    counted as a difference.
    """
    out: dict[str, set[tuple[str, str]]] = {}
    for m in re.finditer(r'\(net \(code "\d+"\) \(name "([^"]*)"\)'
                         r'((?:\s*\(node [^\n]*)*)', text):
        name = m.group(1).lstrip("/")  # KiCad prefixes sheet-local labels
        if name.startswith("unconnected-"):
            continue
        out[name] = set(re.findall(r'\(node \(ref "([^"]+)"\) \(pin "([^"]+)"', m.group(2)))
    return out


@requires_kicad_cli
@pytest.mark.parametrize("name", CASES)
def test_kicad_extracts_the_same_net_graph(name, tmp_path):
    path = EXAMPLES / f"{name}.ohm"
    result = compile_source(path.read_text(), str(path))
    assert result.low is not None, [d.machine() for d in result.diagnostics]

    sch = tmp_path / f"{name}.kicad_sch"
    sch.write_text(emit_kicad_sch(result.low, str(path), result.design),
                   encoding="utf-8", newline="\n")
    extracted = tmp_path / "kicad.net"
    proc = subprocess.run(
        [KICAD_CLI, "sch", "export", "netlist", str(sch), "-o", str(extracted)],
        capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stderr

    ours = _nets(emit_netlist(result.low, str(path)))
    theirs = _nets(extracted.read_text())
    assert ours, "the design under test has no nets"
    assert theirs == ours, (
        f"KiCad read a different circuit from the schematic than Ohm emitted.\n"
        f"only in ohm:   {sorted(set(ours) - set(theirs))}\n"
        f"only in kicad: {sorted(set(theirs) - set(ours))}\n"
        f"differing:     {sorted(n for n in set(ours) & set(theirs) if ours[n] != theirs[n])}")


def test_schematic_has_symbols_and_wires():
    """Guard the regression directly: the old emitter produced neither."""
    path = EXAMPLES / "divider_led.ohm"
    result = compile_source(path.read_text(), str(path))
    sch = emit_kicad_sch(result.low, str(path), result.design)
    assert "(lib_symbols\n" in sch and "(symbol \"ohm:" in sch
    assert sch.count("(wire ") >= 8  # one stub per connected pin
    assert sch.count("(label ") >= 8
    assert "(rectangle" in sch  # symbol bodies, not just text


def test_schematic_is_deterministic():
    path = EXAMPLES / "led_bar.ohm"
    a = compile_source(path.read_text(), str(path))
    b = compile_source(path.read_text(), str(path))
    assert emit_kicad_sch(a.low, "x", a.design) == emit_kicad_sch(b.low, "x", b.design)


def test_emit_without_design_still_works():
    """Back-compat: older callers pass only `low` and must still get a schematic."""
    path = EXAMPLES / "divider_led.ohm"
    result = compile_source(path.read_text(), str(path))
    sch = emit_kicad_sch(result.low, str(path))
    assert "(kicad_sch" in sch and "(label " in sch


def test_pin_named_like_another_pads_id_does_not_shadow_it():
    """Regression: a pin *named* C1 must not claim the pad *called* C1.

    Real case (LSM303D): pin `C1` sits on pad `E1` while pad `C1` carries `INT2`.
    Keying the net lookup by pin name as well as pad hung `C1`'s label on the
    unconnected INT2 pin, silently shorting it onto that net in the schematic
    while Ohm's own netlist stayed correct.
    """
    src = """component Part:
    package = generate(LGA, rows=5, cols=5, pitch=0.5mm, pad=0.3mm, border=1)
    pin C1:   role=passive, pad=E1
    pin INT2: role=passive, pad=C1
    pin VDD:  role=power_in, pad=A1
    pin GND:  role=ground, pad=E5
    pin A2:   role=passive, pad=A2
    pin A3:   role=passive, pad=A3
    pin A4:   role=passive, pad=A4
    pin A5:   role=passive, pad=A5
    pin B1:   role=passive, pad=B1
    pin B5:   role=passive, pad=B5
    pin C5:   role=passive, pad=C5
    pin D1:   role=passive, pad=D1
    pin D5:   role=passive, pad=D5
    pin E2:   role=passive, pad=E2
    pin E3:   role=passive, pad=E3
    pin E4:   role=passive, pad=E4

component Cap:
    package = generate(CHIP0402)
    pin P1: role=passive, pad=1
    pin P2: role=passive, pad=2

module Top:
    u = Part()
    c = Cap()
    u.C1 ~ c.P1
"""
    result = compile_source(src, "t.ohm")
    assert result.low is not None, [d.machine() for d in result.diagnostics]
    sch = emit_kicad_sch(result.low, "t.ohm", result.design)
    # exactly two labels on that net: the cap pin and pad E1 — never pad C1
    net = next(n.name for n in result.low.nets
               if {(x.part_ref, x.pin) for x in n.nodes} >= {("u", "C1"), ("c", "P1")})
    assert sch.count(f'(label "{net}"') == 2
