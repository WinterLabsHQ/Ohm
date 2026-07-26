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


def _module_name(project: str) -> str:
    """Derive a valid Ohm module identifier ([A-Za-z_][A-Za-z0-9_]*) from a
    project name: split on non-alphanumerics, PascalCase the parts."""
    parts = [p for p in "".join(c if c.isalnum() else " " for c in project).split() if p]
    ident = "".join(p[:1].upper() + p[1:] for p in parts) or "Board"
    if not (ident[0].isalpha() or ident[0] == "_"):
        ident = "Board" + ident
    return ident


def _cmd_init(args) -> int:
    """Scaffold a new Ohm project directory with a starter module that
    compiles clean, plus lib/, out/, .gitignore and a README."""
    target = Path(args.name) if args.name else Path.cwd()
    project = target.resolve().name
    module = _module_name(project)
    stem = "".join(c if (c.isalnum() or c in "._-") else "_" for c in project) or "board"

    entry = target / f"{stem}.ohm"
    if entry.exists():
        print(f"error: '{entry}' already exists — refusing to overwrite", file=sys.stderr)
        return 1

    starter = f"""\
# {project} — a starter Ohm design. Runs clean through `ohm check`.
# Edit freely; see docs/STYLE.md for the full authoring guide.

use std.interfaces      # Power
use std.passives        # Resistor, Capacitor
use std.discrete        # LED

module {module}:
    pwr = io(Power)
    vcc = config(Voltage, default=5.0V)

    # current-limiting resistor for a ~2V-forward LED at ~9mA on a 5V rail
    r_led = Resistor(value=330ohm +/- 5%)
    d1    = LED()
    c_dec = Capacitor(value=100nF)         # rail decoupling

    pwr.v    ~ r_led.P1
    r_led.P2 ~ d1.A
    d1.K     ~ pwr.gnd
    pwr.v    ~ c_dec.P1
    pwr.gnd  ~ c_dec.P2

    # keep the LED current sane at the nominal rail (approx 2V LED drop)
    assert (vcc - 2.0V) / r_led.value <= 15mA

    constraint netclass(pwr.v, "power")
"""

    gitignore = "# Ohm build output\n/out/\n\n# Python\n.venv/\n__pycache__/\n*.pyc\n"

    readme = f"""\
# {project}

An [Ohm](https://) hardware design.

## Build

```sh
ohm check {stem}.ohm            # all checks, no output files (fast inner loop)
ohm build {stem}.ohm -o out/    # emit netlist + KiCad project + zen + constraints
ohm explain <CODE>              # explain any diagnostic, e.g. ohm explain C001
```

## Layout

- `{stem}.ohm` — the top-level design (the module you build)
- `lib/`       — your own reusable component blocks: `use "./lib/parts.ohm"`
- `out/`       — build artifacts (git-ignored)
"""

    try:
        target.mkdir(parents=True, exist_ok=True)
        (target / "lib").mkdir(exist_ok=True)
        (target / "out").mkdir(exist_ok=True)
        (target / "lib" / ".gitkeep").write_text("", encoding="utf-8", newline="\n")
        entry.write_text(starter, encoding="utf-8", newline="\n")
        (target / ".gitignore").write_text(gitignore, encoding="utf-8", newline="\n")
        (target / "README.md").write_text(readme, encoding="utf-8", newline="\n")
    except OSError as exc:
        print(f"error: cannot scaffold '{target}': {exc.strerror or exc}", file=sys.stderr)
        return 1

    where = "." if args.name is None else str(target)
    print(f"created Ohm project '{project}' in {where}")
    print("next:")
    if args.name is not None:
        print(f"  cd {args.name}")
    print(f"  ohm check {stem}.ohm")
    return 0


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

    p_init = sub.add_parser("init", help="scaffold a new Ohm project directory")
    p_init.add_argument("name", nargs="?",
                        help="project directory to create (default: current directory)")

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

    p_conv = sub.add_parser("convert",
                            help="convert a .zen design to annotatable Ohm source")
    p_conv.add_argument("file", help="the .zen file")
    p_conv.add_argument("-o", "--out", help="write to a file instead of stdout")

    p_imp = sub.add_parser("import-symbol",
                           help="generate an Ohm component from a KiCad .kicad_sym symbol")
    p_imp.add_argument("file", help="the .kicad_sym library file")
    p_imp.add_argument("symbol", nargs="?", help="symbol name (omit to list the library)")
    p_imp.add_argument("-o", "--out", help="write/append to a file instead of stdout")

    p_sign = sub.add_parser("signoff",
                            help="verify a ROUTED .kicad_pcb against the Ohm source (ADR-011)")
    p_sign.add_argument("file", help="the Ohm source")
    p_sign.add_argument("board", help="the routed .kicad_pcb")
    p_sign.add_argument("--json-diagnostics", action="store_true")

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

    if args.command == "init":
        return _cmd_init(args)

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
    if args.command == "import-symbol":
        from ohm.import_symbol import SymbolError, import_symbol, list_symbols

        lib_name = Path(args.file).stem
        try:
            if args.symbol is None:
                for name in list_symbols(source):
                    print(name)
                return 0
            text = import_symbol(source, args.symbol, lib_name)
        except SymbolError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        if args.out:
            existing = Path(args.out).exists() and Path(args.out).stat().st_size > 0
            with open(args.out, "a") as fh:
                fh.write(("\n" if existing else "") + text)
            print(f"appended component to {args.out}", file=sys.stderr)
        else:
            print(text, end="")
        return 0

    # ---- .zen ecosystem paths (ADR-012 X0) ----
    if args.command == "convert" or (args.command == "check" and args.file.endswith(".zen")):
        from ohm.imp_zen import flat_lints, import_zen

        zlow, zdiags = import_zen(source, args.file)
        diags = zdiags + flat_lints(zlow, args.file)
        if args.command == "convert":
            from ohm.emit_ohm import emit_ohm

            text = emit_ohm(zlow, args.file)
            for dg in zdiags:
                print(dg.human(source), file=sys.stderr)
            if args.out:
                Path(args.out).write_text(text)
                print(f"wrote {args.out} — fill the TODOs (or hand them to your agent) "
                      "to enable the checking tiers", file=sys.stderr)
            else:
                print(text, end="")
        elif getattr(args, "json_diagnostics", False):
            print(json.dumps([{
                "severity": dg.severity, "code": dg.code, "message": dg.message,
                "file": dg.file, "help": dg.help} for dg in diags]))
        else:
            for dg in diags:
                print(dg.human(source), file=sys.stderr)
        return 1 if any(dg.severity == "error" for dg in diags) else 0

    if args.command == "signoff":
        from ohm.signoff import signoff

        result = compile_source(source, args.file, emit=True)
        _report(result, source, args.json_diagnostics)
        if not result.ok:
            return 1
        assert result.low is not None
        try:
            board_text = Path(args.board).read_text()
        except OSError as exc:
            print(f"error: cannot read '{args.board}': {exc.strerror or exc}", file=sys.stderr)
            return 1
        diags = signoff(result.low, board_text, args.board)
        if args.json_diagnostics:
            print(json.dumps([{
                "severity": d.severity, "code": d.code, "message": d.message,
                "file": d.file, "help": d.help} for d in diags]))
        else:
            for d in diags:
                print(d.machine(), file=sys.stderr)
        return 1 if any(d.severity == "error" for d in diags) else 0

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
