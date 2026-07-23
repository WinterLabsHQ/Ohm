"""W1 audit regressions: crash inputs must produce diagnostics, never tracebacks."""

import io
import json
import subprocess
import sys
from pathlib import Path

from ohm.driver import compile_source
from ohm.parse import parse

DEPTH1_CONNECT = (
    "component C:\n"
    '    package = "X"\n'
    "    pin P: role=passive\n"
    "module M:\n"
    "    a = C()\n"
    "    b = C()\n"
    "    a ~ b\n"
)


# ---------- finding 1: depth-1 connect endpoint must not IndexError ----------
def test_depth1_component_connect_does_not_crash():
    result = compile_source(DEPTH1_CONNECT, "d1.ohm", emit=False)  # was IndexError
    assert isinstance(result.diagnostics, list)


# ---------- finding 2: DedentError caught, P001 emitted ----------
def test_dedent_error_becomes_p001():
    src = 'component A:\n        package = "X"\n    mpn = "Y"\n'  # was raw DedentError
    program, diags = parse(src, "bad.ohm")
    assert any(d.code == "P001" for d in diags)
    assert all(d.span.line >= 1 and d.span.col >= 1 for d in diags)


# ---------- finding 3: self-instantiating module must not recurse forever ----------
def test_recursive_module_is_r014_error():
    result = compile_source("module M:\n    m = M()\n", "rec.ohm")  # was RecursionError
    assert any(d.code == "R014" for d in result.diagnostics)
    assert not result.ok and result.low is None  # unrealizable design emits nothing


def test_mutually_recursive_modules_are_r014_error():
    src = "module A:\n    b = B()\nmodule B:\n    a = A()\n"
    result = compile_source(src, "mutual.ohm")
    assert any(d.code == "R014" for d in result.diagnostics)
    assert not result.ok


# ---------- finding 4: LSP server survives a compile_source exception ----------
def _frame(req: dict) -> bytes:
    body = json.dumps(req).encode()
    return f"Content-Length: {len(body)}\r\n\r\n".encode() + body


def _parse_frames(raw: bytes) -> list[dict]:
    out = []
    while b"Content-Length:" in raw:
        _, _, rest = raw.partition(b"\r\n\r\n")
        header = raw[: len(raw) - len(rest) - 4]
        length = int(header.split(b":")[1])
        out.append(json.loads(rest[:length]))
        raw = rest[length:]
    return out


def test_lsp_survives_crash_input(monkeypatch):
    # over a pipe, like test_p10: server must answer shutdown after the crash input
    reqs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "textDocument/didOpen", "params": {
            "textDocument": {"uri": "file:///t.ohm", "languageId": "ohm",
                             "version": 1, "text": DEPTH1_CONNECT}}},
        {"jsonrpc": "2.0", "id": 2, "method": "shutdown", "params": {}},
        {"jsonrpc": "2.0", "method": "exit"},
    ]
    proc = subprocess.Popen([str(Path(sys.executable).parent / "ohm"), "lsp"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    try:
        proc.stdin.write(b"".join(_frame(r) for r in reqs))
        proc.stdin.flush()
        proc.stdin.close()
        raw = proc.stdout.read()
    finally:
        proc.wait(timeout=10)
    messages = _parse_frames(raw)
    assert any(m.get("id") == 2 for m in messages)  # shutdown answered: server survived

    # and if the compiler DOES raise, the server publishes X000 and keeps serving
    import ohm.lsp as lsp

    def boom(text, uri):
        raise RuntimeError("boom")

    monkeypatch.setattr(lsp, "_lsp_diagnostics", boom)
    stdout = io.BytesIO()
    assert lsp.serve(io.BytesIO(b"".join(_frame(r) for r in reqs)), stdout) == 0
    messages = _parse_frames(stdout.getvalue())
    (publish,) = [m for m in messages if m.get("method") == "textDocument/publishDiagnostics"]
    (diag,) = publish["params"]["diagnostics"]
    assert diag["code"] == "X000" and diag["severity"] == 1
    assert diag["message"].startswith("internal error: boom")
    assert any(m.get("id") == 2 for m in messages)  # still answered shutdown
