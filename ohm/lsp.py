"""P10: stdlib-only LSP server (`ohm lsp`) — JSON-RPC over stdio.

Surfaces the full checker pipeline (S/I/Q/U/C + parse/resolve) as
publishDiagnostics on didOpen/didChange/didSave, so a Tier-I miswire is a red
squiggle before any build. No dependencies beyond the stdlib (ADR-005).
"""

from __future__ import annotations

import json
import re
import sys

from ohm.driver import compile_source

_SEVERITY = {"error": 1, "warning": 2, "info": 3}


def _uri_path(uri: str) -> str:
    # minimal file:// decoding by hand — keeps the hermeticity grep strict
    if not uri.startswith("file://"):
        return uri
    path = uri[len("file://"):]
    return re.sub(r"%([0-9A-Fa-f]{2})", lambda m: chr(int(m.group(1), 16)), path)


def _lsp_diagnostics(text: str, uri: str) -> list[dict]:
    result = compile_source(text, _uri_path(uri), emit=False)
    out = []
    for d in result.diagnostics:
        out.append({
            "range": {
                "start": {"line": d.span.line - 1, "character": d.span.col - 1},
                "end": {"line": d.span.end_line - 1, "character": max(d.span.end_col - 1, d.span.col)},
            },
            "severity": _SEVERITY[d.severity],
            "code": d.code,
            "source": "ohm",
            "message": d.message + (f"\nhelp: {d.help}" if d.help else ""),
        })
    return out


def _read_message(stream) -> dict | None:
    length = None
    while True:
        line = stream.readline()
        if not line:
            return None
        line = line.strip()
        if line.startswith(b"Content-Length:"):
            length = int(line.split(b":")[1])
        elif not line:
            break
    if length is None:
        return None
    return json.loads(stream.read(length))


def _write_message(stream, payload: dict) -> None:
    body = json.dumps(payload).encode()
    stream.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
    stream.flush()


def serve(stdin=None, stdout=None) -> int:
    stdin = stdin or sys.stdin.buffer
    stdout = stdout or sys.stdout.buffer
    docs: dict[str, str] = {}

    def publish(uri: str) -> None:
        try:
            diagnostics = _lsp_diagnostics(docs[uri], uri)
        except Exception as exc:  # compiler bug must not kill the server
            diagnostics = [{
                "range": {"start": {"line": 0, "character": 0},
                          "end": {"line": 0, "character": 0}},
                "severity": 1,
                "code": "X000",
                "source": "ohm",
                "message": f"internal error: {exc}",
            }]
        _write_message(stdout, {
            "jsonrpc": "2.0",
            "method": "textDocument/publishDiagnostics",
            "params": {"uri": uri, "diagnostics": diagnostics},
        })

    while True:
        msg = _read_message(stdin)
        if msg is None:
            return 0
        method, msg_id = msg.get("method"), msg.get("id")
        params = msg.get("params") or {}
        if method == "initialize":
            _write_message(stdout, {"jsonrpc": "2.0", "id": msg_id, "result": {
                "capabilities": {"textDocumentSync": 1},  # full-document sync
                "serverInfo": {"name": "ohm-lsp"},
            }})
        elif method == "shutdown":
            _write_message(stdout, {"jsonrpc": "2.0", "id": msg_id, "result": None})
        elif method == "exit":
            return 0
        elif method == "textDocument/didOpen":
            doc = params["textDocument"]
            docs[doc["uri"]] = doc["text"]
            publish(doc["uri"])
        elif method == "textDocument/didChange":
            uri = params["textDocument"]["uri"]
            docs[uri] = params["contentChanges"][-1]["text"]
            publish(uri)
        elif method == "textDocument/didSave":
            uri = params["textDocument"]["uri"]
            if uri in docs:
                publish(uri)
        elif msg_id is not None:  # unknown request: empty result keeps clients happy
            _write_message(stdout, {"jsonrpc": "2.0", "id": msg_id, "result": None})
