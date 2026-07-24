"""Ohm CLI driver: `ohm build|check|fmt|explain|lsp|gen-package`."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ohm import __version__, ir
from ohm.driver import compile_source
from ohm.emit_kicad import emit_kicad_pcb, emit_kicad_sch
from ohm.emit_netlist import emit_netlist


def _emit_generated_packages(result, outdir: Path) -> None:
    """P-GEN-PACKAGE: write one .kicad_mod per distinct generated package
    (spec-named, shared across parts) and one .kicad_sym per component."""
    from ohm.ir import GenerateSpec
    from ohm.pkggen.footprint import emit_footprint
    from ohm.pkggen.packages import build_from_spec, spec_name
    from ohm.pkggen.pindata_iface import DeclaredPinData
    from ohm.pkggen.symbol import emit_symbol

    comps = {c.name: c for c in result.design.components}
    footprints_done: set[str] = set()
    symbols_done: set[str] = set()
    for part in result.low.parts:
        spec = part.package  # effective package (R9 override already applied)
        if not isinstance(spec, GenerateSpec):
            continue
        fp_name = spec_name(spec)
        if fp_name not in footprints_done:
            footprints_done.add(fp_name)
            model = build_from_spec(spec, fp_name)  # S006 already gated errors
            (outdir / f"{fp_name}.kicad_mod").write_text(emit_footprint(model))
        comp = comps[part.component]
        if comp.name not in symbols_done:
            symbols_done.add(comp.name)
            pins = DeclaredPinData().pin_table(comp)
            (outdir / f"{comp.name}.kicad_sym").write_text(emit_symbol(comp.name, pins))


def _report(result, source: str, as_json: bool) -> None:
    if as_json:
        print(json.dumps([{
            "severity": d.severity, "code": d.code, "message": d.message,
            "file": d.file, "line": d.span.line, "col": d.span.col,
            "end_line": d.span.end_line, "end_col": d.span.end_col,
            "endpoints": list(d.endpoints),
            "related": [{"line": r.line, "col": r.col} for r in d.related],
            "help": d.help,
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
    p_build.add_argument("--emit",
                         choices=["netlist", "kicad", "zen", "constraints", "package", "all"],
                         default="all")
    p_build.add_argument("--dump-ir", choices=["high", "typed", "low"])
    p_build.add_argument("--json-diagnostics", action="store_true")
    p_build.add_argument("--strict", action="store_true",
                         help="exit 1 (and emit nothing) on any warning or worse")
    p_build.add_argument("--debug-passes", action="store_true",
                         help="verify checkers do not mutate the IR (asserts on violation)")
    p_build.add_argument("--target-kicad", default="7",
                         help="KiCad major version to target (v1 supports: 7)")
    # ponytail: single 'declared' provider today; the flag is the provider seam
    p_build.add_argument("--ratings", choices=["declared"], default="declared",
                         help="pin-ratings provider")
    p_build.add_argument("--pindata", choices=["declared"], default="declared",
                         help="pin-data provider for generated symbols")

    p_check = sub.add_parser("check", help="run all checks, no emit (fast inner loop)")
    p_check.add_argument("file", nargs="?")
    p_check.add_argument("--json-diagnostics", action="store_true")
    p_check.add_argument("--strict", action="store_true",
                         help="exit 1 on any warning or worse")
    p_check.add_argument("--debug-passes", action="store_true",
                         help="verify checkers do not mutate the IR (asserts on violation)")
    p_check.add_argument("--explain", metavar="CODE",
                         help="print the explanation for a diagnostic code and exit")

    p_fmt = sub.add_parser("fmt", help="canonical reformat (deterministic)")
    p_fmt.add_argument("file")
    p_fmt.add_argument("-w", "--write", action="store_true", help="rewrite the file in place")

    p_explain = sub.add_parser("explain", help="explain a diagnostic code")
    p_explain.add_argument("code")

    sub.add_parser("lsp", help="LSP server over stdio (editor integration)")

    p_gen = sub.add_parser("gen-package", help="generate .kicad_mod/.kicad_sym standalone")
    p_gen.add_argument("type", help="package type, e.g. SOIC, TSSOP, SOT23, CHIP0603")
    p_gen.add_argument("--pins", type=int, default=0)
    p_gen.add_argument("--pitch", help="pitch in mm, e.g. 1.27")
    p_gen.add_argument("--density", choices=["least", "nominal", "most"], default="nominal")
    p_gen.add_argument("--name", help="output name (default <TYPE>-<pins>)")
    p_gen.add_argument("--out", default=".")

    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0

    if args.command == "explain" or (args.command == "check" and args.explain):
        from ohm.diagnostics import EXPLAIN

        code = args.code if args.command == "explain" else args.explain
        text = EXPLAIN.get(code.upper())
        if text is None:
            print(f"unknown diagnostic code '{code}'", file=sys.stderr)
            return 1
        print(f"{code.upper()}: {text}")
        return 0

    if args.command == "check" and args.file is None:
        print("error: ohm check needs a file (or --explain CODE)", file=sys.stderr)
        return 1

    if args.command == "build" and args.target_kicad != "7":
        print("error: only KiCad 7 is supported in v1", file=sys.stderr)
        return 1

    if args.command == "lsp":
        from ohm.lsp import serve

        return serve()

    if args.command == "gen-package":
        from decimal import Decimal

        from ohm.pkggen.footprint import emit_footprint
        from ohm.pkggen.packages import PkgError, build
        from ohm.pkggen.symbol import emit_symbol

        kwargs: dict[str, ir.Value] = {}
        if args.pins:
            kwargs["pins"] = Decimal(args.pins)
        if args.pitch:
            from decimal import InvalidOperation

            from ohm.ir import Quantity

            try:
                kwargs["pitch"] = Quantity(Decimal(args.pitch), "mm")
            except InvalidOperation:
                print(f"error: invalid --pitch '{args.pitch}' (expected mm, e.g. 1.27)",
                      file=sys.stderr)
                return 1
        kwargs["density"] = args.density
        try:
            model = build(args.type, kwargs, args.name)
        except PkgError as exc:
            print(f"error[S006]: {exc}", file=sys.stderr)
            return 1
        outdir = Path(args.out)
        outdir.mkdir(parents=True, exist_ok=True)
        (outdir / f"{model.name}.kicad_mod").write_text(emit_footprint(model))
        pins = [(p.id, f"P{p.id}", "passive") for p in model.pads]
        (outdir / f"{model.name}.kicad_sym").write_text(emit_symbol(model.name, pins))
        print(f"generated {model.name}.kicad_mod + {model.name}.kicad_sym in {outdir}")
        return 0

    try:
        source = Path(args.file).read_text()
    except OSError as exc:
        print(f"error: cannot read '{args.file}': {exc.strerror or exc}", file=sys.stderr)
        return 1

    if args.command == "fmt":
        from ohm.fmt import format_source

        formatted, diags = format_source(source, args.file)
        for d in diags:
            print(d.human(source), file=sys.stderr)
        if diags:
            return 1
        if args.write:
            try:
                Path(args.file).write_text(formatted)
            except OSError as exc:
                print(f"error: cannot write '{args.file}': {exc.strerror or exc}",
                      file=sys.stderr)
                return 1
        else:
            print(formatted, end="")
        return 0
    emit = args.command == "build"
    result = compile_source(source, args.file, emit=emit,
                            debug_passes=getattr(args, "debug_passes", False))
    _report(result, source, args.json_diagnostics)
    if not result.ok:
        return 1
    # --strict: fail (before any emit) on warnings too
    if getattr(args, "strict", False) and any(
            d.severity in ("error", "warning") for d in result.diagnostics):
        return 1

    if args.command == "build":
        if args.dump_ir:
            node = {"high": result.design_high, "typed": result.design,
                    "low": result.low}[args.dump_ir]
            assert node is not None  # result.ok gated above; all stages populated
            print(ir.to_json(node))
            return 0
        low = result.low
        assert low is not None  # result.ok gated above; build path always lowers
        stem = Path(args.file).stem
        outdir = Path(args.out)
        try:
            outdir.mkdir(parents=True, exist_ok=True)
            if args.emit in ("netlist", "all"):
                (outdir / f"{stem}.net").write_text(emit_netlist(low, args.file))
            if args.emit in ("kicad", "all"):
                from ohm.emit_kicad_pro import emit_kicad_pro

                (outdir / f"{stem}.kicad_sch").write_text(emit_kicad_sch(low, args.file))
                (outdir / f"{stem}.kicad_pcb").write_text(emit_kicad_pcb(low, args.file))
                (outdir / f"{stem}.kicad_pro").write_text(emit_kicad_pro(low, args.file))
                _emit_generated_packages(result, outdir)
            if args.emit in ("package",):  # only the generated .kicad_mod/.kicad_sym
                _emit_generated_packages(result, outdir)
            if args.emit in ("zen", "all"):
                from ohm.emit_zen import emit_zen  # interop path only (ADR-002)

                (outdir / f"{stem}.zen").write_text(emit_zen(low, args.file))
            if args.emit in ("constraints", "all"):
                from ohm.emit_constraints import emit_constraints

                (outdir / f"{stem}.constraints.json").write_text(
                    emit_constraints(low, args.file))
        except OSError as exc:
            print(f"error: cannot write outputs to '{outdir}': {exc.strerror or exc}",
                  file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
