"""P10: stdlib-only LSP server (`ohm lsp`) — JSON-RPC over stdio.

Surfaces the full checker pipeline (S/I/Q/U/C + parse/resolve) as
publishDiagnostics on didOpen/didChange/didSave, so a Tier-I miswire is a red
squiggle before any build. No dependencies beyond the stdlib (ADR-005).
"""

from __future__ import annotations

import json
import os
import re
import sys

from ohm.driver import compile_source

_SEVERITY = {"error": 1, "warning": 2, "info": 3}

_PCT_RUN = re.compile(r"((?:%[0-9A-Fa-f]{2})+)")
_DRIVE = re.compile(r"^/([A-Za-z])[:|](?=/|$)")


def _unquote(text: str) -> str:
    # percent-decoding as urllib.parse.unquote does it: a run of %XX escapes is
    # one UTF-8 byte string, so `%C3%A9` is one character, not two
    parts = _PCT_RUN.split(text)
    return "".join(
        bytes.fromhex(part.replace("%", "")).decode("utf-8", "replace") if i % 2 else part
        for i, part in enumerate(parts))


def _uri_path(uri: str, windows: bool | None = None) -> str:
    """A `file://` URI as a local path, the way `urllib.request.url2pathname` reads it.

    Decoded by hand to keep the hermeticity grep strict (no `urllib` on the build
    path). On Windows the path part of `file:///D:/x/a.ohm` is `/D:/x/a.ohm`, and a
    path that starts with `/` and names no drive is relative to the *current
    drive's* root, so the drive letter's leading slash must go: `D:/x/a.ohm`.
    Editors also send the colon escaped (`file:///d%3A/x`) and the old `|` form.
    A non-local authority is a UNC share there (`//host/share/a.ohm`). On POSIX
    the path part is already the path.
    """
    if not uri.startswith("file://"):
        return uri
    if windows is None:
        windows = os.name == "nt"
    authority, slash, path = uri[len("file://"):].partition("/")
    path = _unquote(slash + path)
    if authority and authority.lower() != "localhost":
        return "//" + _unquote(authority) + path
    if windows:
        m = _DRIVE.match(path)
        if m:
            path = m.group(1).upper() + ":" + (path[m.end():] or "/")
    return path


def _lsp_diagnostics(text: str, uri: str) -> list[dict]:
    result = compile_source(text, _uri_path(uri), emit=False)
    out = []
    for d in result.diagnostics:
        entry = {
            "range": {
                "start": {"line": d.span.line - 1, "character": d.span.col - 1},
                "end": {"line": d.span.end_line - 1, "character": max(d.span.end_col - 1, d.span.col)},
            },
            "severity": _SEVERITY[d.severity],
            "code": d.code,
            "source": "ohm",
            "message": d.message + (f"\nhelp: {d.help}" if d.help else ""),
        }
        if d.related:
            entry["relatedInformation"] = [{
                "location": {"uri": uri, "range": {
                    "start": {"line": r.line - 1, "character": r.col - 1},
                    "end": {"line": r.end_line - 1, "character": max(r.end_col - 1, r.col)},
                }},
                "message": "related",
            } for r in d.related]
        if d.endpoints:
            entry["data"] = {"endpoints": list(d.endpoints)}
        out.append(entry)
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
        elif method == "textDocument/didClose":
            uri = params["textDocument"]["uri"]
            docs.pop(uri, None)
            _write_message(stdout, {
                "jsonrpc": "2.0",
                "method": "textDocument/publishDiagnostics",
                "params": {"uri": uri, "diagnostics": []},
            })
        elif method == "textDocument/didSave":
            uri = params["textDocument"]["uri"]
            if uri in docs:
                publish(uri)
        elif msg_id is not None:  # unknown request: empty result keeps clients happy
            _write_message(stdout, {"jsonrpc": "2.0", "id": msg_id, "result": None})
