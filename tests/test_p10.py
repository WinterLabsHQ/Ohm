"""P10 gate: R7 constraints emit the shared schema; a Tier-I error is surfaced
by the LSP (the red squiggle) before any build. Plus fmt + explain."""

import json
import subprocess
import sys
from pathlib import Path

from ohm.diagnostics import EXPLAIN
from ohm.driver import compile_source
from ohm.emit_constraints import emit_constraints
from ohm.emit_netlist import emit_netlist
from ohm.fmt import format_source

FIXTURES = Path(__file__).parent / "fixtures"
KNOWN_BAD = Path(__file__).parent / "known_bad"
CANONICAL = (FIXTURES / "canonical.ohm").read_text()

CONSTRAINED = (
    "interface USB2:\n"
    "    dp: Signal, role=bidirectional\n"
    "    dm: Signal, role=bidirectional\n"
    "    require pair(dp, dm)\n"
    "component Conn:\n"
    '    package = "USB_C"\n'
    "    pin DP: role=bidirectional, pad=3\n"
    "    pin DM: role=bidirectional, pad=2\n"
    "module Board:\n"
    "    usb = io(USB2)\n"
    "    j = Conn()\n"
    "    usb.dp ~ j.DP\n"
    "    usb.dm ~ j.DM\n"
    "    constraint impedance(usb.dp, 45ohm)\n"
    "    constraint diffpair(usb.dp, usb.dm)\n"
    "    constraint length(usb.dp, usb.dm, 0.5mm)\n"
    "    obligation ripple(usb.dp) < 50mV\n"
)


# ---------- gate: constraints emit the shared schema ----------
def test_constraints_emit_shared_schema():
    result = compile_source(CONSTRAINED, "usb.ohm")
    assert result.ok, [d.machine() for d in result.diagnostics]
    doc = json.loads(emit_constraints(result.low, "usb.ohm"))
    assert doc["version"] == "1"
    kinds = [c["kind"] for c in doc["constraints"]]
    assert kinds == ["impedance", "diffpair", "length"]
    imp = doc["constraints"][0]
    assert imp["nets"] == ["usb.dp"] and imp["params"] == ["45ohm"]
    dp = doc["constraints"][1]
    assert dp["nets"] == ["usb.dp", "usb.dm"] and dp["params"] == []
    assert doc["constraints"][2]["params"] == ["0.5mm"]
    (hole,) = doc["holes"]
    assert hole == {"kind": "ripple", "args": ["usb.dp"], "op": "<", "bound": "50mV"}


def test_unknown_constraint_kind_warns():
    src = CONSTRAINED.replace("impedance(usb.dp, 45ohm)", "wiggliness(usb.dp, 45ohm)")
    result = compile_source(src, "u.ohm")
    assert result.ok  # warning, not error
    assert any(d.code == "R009" for d in result.diagnostics)


def test_constraints_deterministic():
    a = emit_constraints(compile_source(CONSTRAINED, "x").low, "x")
    b = emit_constraints(compile_source(CONSTRAINED, "x").low, "x")
    assert a.encode() == b.encode()


# ---------- gate: Tier-I red squiggle via LSP before build ----------
def _lsp_session(requests):
    proc = subprocess.Popen([sys.executable, "-m", "ohm.lsp_main"] if False else
                            [str(Path(sys.executable).parent / "ohm"), "lsp"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    out = []
    try:
        for req in requests:
            body = json.dumps(req).encode()
            proc.stdin.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
        proc.stdin.flush()
        proc.stdin.close()
        raw = proc.stdout.read()
    finally:
        proc.wait(timeout=10)
    while b"Content-Length:" in raw:
        _, _, rest = raw.partition(b"\r\n\r\n")
        header = raw[: len(raw) - len(rest) - 4]
        length = int(header.split(b":")[1])
        out.append(json.loads(rest[:length]))
        raw = rest[length:]
    return out


def test_lsp_publishes_tier_i_error():
    swapped = (KNOWN_BAD / "i005_swapped_sda_scl.ohm").read_text()
    messages = _lsp_session([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "textDocument/didOpen", "params": {
            "textDocument": {"uri": "file:///t.ohm", "languageId": "ohm",
                             "version": 1, "text": swapped}}},
        {"jsonrpc": "2.0", "id": 2, "method": "shutdown", "params": {}},
        {"jsonrpc": "2.0", "method": "exit"},
    ])
    init = messages[0]
    assert init["result"]["capabilities"]["textDocumentSync"] == 1
    (publish,) = [m for m in messages if m.get("method") == "textDocument/publishDiagnostics"]
    i005 = [d for d in publish["params"]["diagnostics"] if d["code"] == "I005"]
    assert len(i005) == 2 and all(d["severity"] == 1 for d in i005)  # 1 = Error (red)
    assert i005[0]["range"]["start"]["line"] == 31  # 0-based line of the bad `~`


# ---------- fmt ----------
def test_fmt_idempotent_and_semantics_preserving():
    once, diags = format_source(CANONICAL, "c.ohm")
    assert diags == []
    twice, _ = format_source(once, "c.ohm")
    assert once == twice  # idempotent
    before = compile_source(CANONICAL, "c.ohm")
    after = compile_source(once, "c.ohm")
    assert emit_netlist(before.low, "x") == emit_netlist(after.low, "x")  # same design


def test_fmt_preserves_comments():
    src = "# top note\nmodule M:  # trailing\n    # inner note\n    a = X()\n"
    out, diags = format_source(src)
    assert [d for d in diags if d.severity == "error"] == []
    assert "# top note" in out and "# trailing" in out and "# inner note" in out


def test_fmt_normalizes_spacing():
    out, _ = format_source("module   M:\n    a   =   X(  )\n")
    assert "module M:" in out and "a = X()" in out


def test_fmt_leaves_broken_source_untouched():
    src = "module M\n    ???\n"
    out, diags = format_source(src)
    assert out == src and diags


# ---------- explain ----------
def test_every_emitted_code_has_an_explanation():
    import re

    ohm_pkg = Path(__file__).parent.parent / "ohm"
    emitted = set()
    for py in ohm_pkg.rglob("*.py"):
        emitted |= set(re.findall(r'"([PRUSICQZK]\d{3})"', py.read_text()))
    missing = emitted - set(EXPLAIN)
    assert not missing, f"codes without explain entries: {sorted(missing)}"


def test_explain_cli():
    from ohm.cli import main

    assert main(["explain", "I005"]) == 0
    assert main(["explain", "X999"]) == 1
