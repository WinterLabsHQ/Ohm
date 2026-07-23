"""Ohm CLI driver: `ohm build`, `ohm check` (P4); fmt/explain/gen-package later."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ohm import __version__, ir
from ohm.driver import compile_source
from ohm.emit_kicad import emit_kicad_pcb, emit_kicad_sch
from ohm.emit_netlist import emit_netlist


def _report(result, source: str, as_json: bool) -> None:
    if as_json:
        print(json.dumps([{
            "severity": d.severity, "code": d.code, "message": d.message,
            "file": d.file, "line": d.span.line, "col": d.span.col, "help": d.help,
        } for d in result.diagnostics]))
    else:
        for d in result.diagnostics:
            print(d.human(source), file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ohm", description="Ohm HDL compiler")
    parser.add_argument("--version", action="version", version=f"ohm {__version__}")
    sub = parser.add_subparsers(dest="command")

    p_build = sub.add_parser("build", help="compile and emit outputs")
    p_build.add_argument("file")
    p_build.add_argument("-o", "--out", default=".", help="output directory")
    p_build.add_argument("--emit", choices=["netlist", "kicad", "zen", "constraints", "all"],
                         default="all")
    p_build.add_argument("--dump-ir", choices=["high", "low"])
    p_build.add_argument("--json-diagnostics", action="store_true")

    p_check = sub.add_parser("check", help="run all checks, no emit (fast inner loop)")
    p_check.add_argument("file")
    p_check.add_argument("--json-diagnostics", action="store_true")

    p_fmt = sub.add_parser("fmt", help="canonical reformat (deterministic)")
    p_fmt.add_argument("file")
    p_fmt.add_argument("-w", "--write", action="store_true", help="rewrite the file in place")

    p_explain = sub.add_parser("explain", help="explain a diagnostic code")
    p_explain.add_argument("code")

    sub.add_parser("lsp", help="LSP server over stdio (editor integration)")

    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0

    if args.command == "explain":
        from ohm.diagnostics import EXPLAIN

        text = EXPLAIN.get(args.code.upper())
        if text is None:
            print(f"unknown diagnostic code '{args.code}'", file=sys.stderr)
            return 1
        print(f"{args.code.upper()}: {text}")
        return 0

    if args.command == "lsp":
        from ohm.lsp import serve

        return serve()

    source = Path(args.file).read_text()

    if args.command == "fmt":
        from ohm.fmt import format_source

        formatted, diags = format_source(source, args.file)
        for d in diags:
            print(d.human(source), file=sys.stderr)
        if diags:
            return 1
        if args.write:
            Path(args.file).write_text(formatted)
        else:
            print(formatted, end="")
        return 0
    result = compile_source(source, args.file, emit=(args.command == "build"))
    _report(result, source, args.json_diagnostics)
    if not result.ok:
        return 1

    if args.command == "build":
        if args.dump_ir:
            node = result.design if args.dump_ir == "high" else result.low
            print(ir.to_json(node))
            return 0
        stem = Path(args.file).stem
        outdir = Path(args.out)
        outdir.mkdir(parents=True, exist_ok=True)
        if args.emit in ("netlist", "all"):
            (outdir / f"{stem}.net").write_text(emit_netlist(result.low, args.file))
        if args.emit in ("kicad", "all"):
            (outdir / f"{stem}.kicad_sch").write_text(emit_kicad_sch(result.low, args.file))
            (outdir / f"{stem}.kicad_pcb").write_text(emit_kicad_pcb(result.low, args.file))
        if args.emit in ("zen", "all"):
            from ohm.emit_zen import emit_zen  # interop path only (ADR-002)

            (outdir / f"{stem}.zen").write_text(emit_zen(result.low, args.file))
        if args.emit in ("constraints", "all"):
            from ohm.emit_constraints import emit_constraints

            (outdir / f"{stem}.constraints.json").write_text(
                emit_constraints(result.low, args.file))
    return 0


if __name__ == "__main__":
    sys.exit(main())
