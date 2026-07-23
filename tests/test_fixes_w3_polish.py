"""W3 polish fixes: deep connect refs (R003), ignored require/obligation args
(R013), negative-value percent tolerance, Diagnostic endpoints/related surface,
cli --json-diagnostics fields, --strict, LSP didClose."""

import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

from ohm.cli import main
from ohm.diagnostics import EXPLAIN, Diagnostic, Span
from ohm.ir import Quantity
from ohm.parse import parse
from ohm.resolve import resolve
from ohm.units import normalize

SRC = (
    "interface I2C:\n"
    "    sda: Signal, role=bidirectional\n"
    "    scl: Signal, role=bidirectional\n"
    '    require pair(sda, scl, foo="x")\n'
    "component R:\n"
    '    package = "R0603"\n'
    "    pin a: role=passive, pad=1\n"
    "    pin b: role=passive, pad=2\n"
    "module M:\n"
    "    bus = io(I2C)\n"
    "    r1 = R()\n"
    "    obligation ripple(bus.sda, 5V) < 50mV\n"
)


def _resolve(src):
    prog, pd = parse(src)
    assert pd == []
    return resolve(prog)


# ---------- 1: extra ref segments in connect context ----------
def test_connect_deep_component_ref_rejected():
    _, diags = _resolve(SRC.replace("r1 = R()\n", "r1 = R()\n    r1.a.bogus ~ bus.sda\n"))
    r003 = [d for d in diags if d.code == "R003"]
    assert len(r003) == 1 and "sub-members" in r003[0].message
    # and the connection is not silently wired
    design, _ = _resolve(SRC.replace("r1 = R()\n", "r1 = R()\n    r1.a.bogus ~ bus.sda\n"))
    assert design.modules[0].connections == ()


# ---------- 2: ignored require kwargs / obligation literals warn R013 ----------
def test_require_kwarg_and_obligation_literal_warn_r013():
    _, diags = _resolve(SRC)
    r013 = [d for d in diags if d.code == "R013"]
    assert len(r013) == 2
    assert all(d.severity == "warning" for d in r013)
    assert any("keyword 'foo'" in d.message for d in r013)
    assert any("'5V'" in d.message for d in r013)
    assert "R013" in EXPLAIN


# ---------- 3: negative value + percent tolerance ----------
def test_normalize_negative_value_percent_tolerance():
    q, err = normalize(Quantity(Decimal("-5"), "V", tol_raw=(Decimal("10"), None)))
    assert err is None
    assert q.tol_lo == Decimal("-5.5") and q.tol_hi == Decimal("-4.5")
    assert q.tol_lo < q.tol_hi


# ---------- 4: Diagnostic endpoints field + related rendering ----------
def test_diagnostic_endpoints_default_and_related_render():
    d = Diagnostic("error", "I003", "multiple drivers", Span(1, 1, 1, 2),
                   related=(Span(3, 5, 3, 8), Span(7, 1, 7, 2)))
    assert d.endpoints == ()
    out = d.human("line1\n")
    assert "related: line 3" in out and "related: line 7" in out


# ---------- 5 + 6: cli json fields and --strict ----------
WARN_SRC = SRC  # R013 warnings only, no errors


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text)
    return str(p)


def test_json_diagnostics_has_end_and_new_fields(tmp_path, capsys):
    f = _write(tmp_path, "w.ohm", WARN_SRC)
    assert main(["check", f, "--json-diagnostics"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload, "expected R013 warnings in payload"
    for d in payload:
        assert set(d) >= {"end_line", "end_col", "endpoints", "related"}
        assert isinstance(d["endpoints"], list) and isinstance(d["related"], list)


def test_check_strict_fails_on_warning(tmp_path):
    f = _write(tmp_path, "w.ohm", WARN_SRC)
    assert main(["check", f]) == 0
    assert main(["check", f, "--strict"]) == 1


def test_build_strict_emits_nothing_on_warning(tmp_path):
    f = _write(tmp_path, "w.ohm", WARN_SRC)
    out = tmp_path / "out"
    assert main(["build", f, "-o", str(out), "--strict"]) == 1
    assert not out.exists() or not list(out.iterdir())
    # without --strict the same source builds and emits
    assert main(["build", f, "-o", str(out)]) == 0
    assert (out / "w.net").exists()


# ---------- 7: LSP didClose drops doc + clears diagnostics ----------
def _lsp_session(requests):
    proc = subprocess.Popen([str(Path(sys.executable).parent / "ohm"), "lsp"],
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


def test_lsp_did_close_publishes_empty_diagnostics():
    bad = "module M:\n    x = Nope()\n"
    uri = "file:///t.ohm"
    messages = _lsp_session([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "textDocument/didOpen", "params": {
            "textDocument": {"uri": uri, "languageId": "ohm", "version": 1, "text": bad}}},
        {"jsonrpc": "2.0", "method": "textDocument/didClose", "params": {
            "textDocument": {"uri": uri}}},
        # didSave after close: doc gone from the map, must not crash or publish
        {"jsonrpc": "2.0", "method": "textDocument/didSave", "params": {
            "textDocument": {"uri": uri}}},
        {"jsonrpc": "2.0", "id": 2, "method": "shutdown", "params": {}},
        {"jsonrpc": "2.0", "method": "exit"},
    ])
    publishes = [m for m in messages if m.get("method") == "textDocument/publishDiagnostics"]
    assert len(publishes) == 2  # didOpen + didClose only (no publish for stale didSave)
    assert publishes[0]["params"]["diagnostics"]  # the R002 squiggle
    assert publishes[1]["params"]["uri"] == uri
    assert publishes[1]["params"]["diagnostics"] == []
