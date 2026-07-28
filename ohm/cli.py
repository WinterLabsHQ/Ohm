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

# The real console encoding at process start, captured before main() forces the
# streams to UTF-8. `ohm doctor` reports this so a non-UTF-8 console is visible.
_CONSOLE_ENCODING = getattr(sys.stdout, "encoding", None)


def _emit_generated_packages(result, outdir: Path, fmt: str = "kicad") -> None:
    """P-GEN-PACKAGE: write one .kicad_mod per distinct generated package
    (spec-named, shared across parts) and one .kicad_sym per component.

    fmt="eagle" writes one self-contained .lbr per component instead — Eagle keeps
    package, symbol and their pin<->pad connects in a single library file."""
    from ohm.ir import GenerateSpec
    from ohm.pkggen.footprint import emit_footprint
    from ohm.pkggen.packages import build_from_spec, spec_name
    from ohm.pkggen.pindata_iface import DeclaredPinData
    from ohm.pkggen.symbol import DEFAULT_FOOTPRINT_LIB, emit_symbol

    comps = {c.name: c for c in result.design.components}
    footprints_done: set[str] = set()
    symbols_done: set[str] = set()
    for part in result.low.parts:
        spec = part.package  # effective package (R9 override already applied)
        if not isinstance(spec, GenerateSpec):
            continue
        fp_name = spec_name(spec)
        comp = comps[part.component]
        if fmt == "eagle":
            if comp.name in symbols_done:
                continue
            symbols_done.add(comp.name)
            from ohm.pkggen.eagle import emit_eagle_library

            model = build_from_spec(spec, fp_name)
            (outdir / f"{comp.name}.lbr").write_text(emit_eagle_library(
                model, comp.name, DeclaredPinData().pin_table(comp),
                {k: str(v) for k, v in comp.attrs}),
                encoding="utf-8", newline="\n")
            continue
        if fp_name not in footprints_done:
            footprints_done.add(fp_name)
            model = build_from_spec(spec, fp_name)  # S006 already gated errors
            (outdir / f"{fp_name}.kicad_mod").write_text(
                emit_footprint(model), encoding="utf-8", newline="\n")
            if model.body is not None:
                from ohm.pkggen.step import emit_step_box

                (outdir / f"{fp_name}.step").write_text(
                    emit_step_box(fp_name, *model.body),
                    encoding="utf-8", newline="\n")
        if comp.name not in symbols_done:
            symbols_done.add(comp.name)
            pins = DeclaredPinData().pin_table(comp)
            # link the symbol to the footprint we just wrote — without this the
            # part lands in the schematic unassigned and cannot go to a board
            (outdir / f"{comp.name}.kicad_sym").write_text(
                emit_symbol(comp.name, pins,
                            footprint=f"{DEFAULT_FOOTPRINT_LIB}:{fp_name}",
                            attrs={k: str(v) for k, v in comp.attrs}),
                encoding="utf-8", newline="\n")


def _target_major(target: str) -> str:
    """Normalize an ohm.toml `target` ("kicad7") or a --target-kicad value ("7")
    to the bare KiCad major version string the build path validates ("7")."""
    t = target.strip().lower()
    return t[len("kicad"):] if t.startswith("kicad") else t


def _rel(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def _write_scaffold(dirs: list[Path],
                    plan: dict[Path, str]) -> tuple[list[Path], list[Path]]:
    """Create `dirs` and write every file in `plan` that does not already exist.
    Strictly additive — existing files are kept, never clobbered. Returns
    (created, kept)."""
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    kept: list[Path] = []
    for path, content in plan.items():
        if path.exists():
            kept.append(path)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        created.append(path)
    return created, kept


def _ask(prompt: str, default: str = "") -> str:
    try:
        resp = input(prompt).strip()
    except EOFError:
        return default
    return resp or default


def _ask_yes_no(prompt: str, *, default: bool) -> bool:
    resp = _ask(f"{prompt} [{'Y/n' if default else 'y/N'}]: ")
    return default if not resp else resp[:1].lower() == "y"


def _want_git(args, interactive: bool) -> bool:
    """Resolve whether to `git init`: --no-git wins, then --git, then (only on a
    terminal) an interactive prompt; non-interactive with no flag means no git."""
    if getattr(args, "no_git", False):
        return False
    if getattr(args, "git", False):
        return True
    return interactive and _ask_yes_no("  Initialize a git repository?", default=True)


def _cmd_init(args) -> int:
    """Scaffold a full Ohm project directory: a starter module that compiles
    clean, plus ohm.toml, README, AGENTS.md, docs/LANGUAGE.md, lib/, out/ and a
    .gitignore. Strictly additive — never overwrites an existing file.

    On a terminal with no arguments this runs an interactive wizard (project
    directory, git); it is fully scriptable with a name argument plus
    --git/--no-git/--yes."""
    from ohm import scaffold

    interactive = sys.stdin.isatty() and not args.yes
    name = args.name
    if name is None and interactive:
        print("Creating a new Ohm project.")
        name = _ask("  Project directory (blank = current directory): ") or None

    target = Path(name) if name else Path.cwd()
    project = target.resolve().name
    entry_file = f"{scaffold.safe_stem(project)}.ohm"
    do_git = _want_git(args, interactive)

    dirs, plan = scaffold.build_plan(target, entry_file=entry_file, include_starter=True)
    try:
        created, kept = _write_scaffold(dirs, plan)
    except OSError as exc:
        print(f"error: cannot scaffold '{target}': {exc.strerror or exc}", file=sys.stderr)
        return 1

    git_status = None
    if do_git:
        from ohm.gitutil import git_init  # setup-only; never on the build path

        git_status = git_init(target)

    where = "." if name is None else str(target)
    if created:
        print(f"initialized Ohm project '{project}' in {where}:")
        for p in sorted(created):
            print(f"  + {_rel(p, target)}")
    else:
        print(f"nothing to create — '{where}' already has all scaffold files")
    if kept:
        names = ", ".join(sorted(_rel(p, target) for p in kept))
        print(f"kept (already present, not overwritten): {names}", file=sys.stderr)
    if git_status is not None:
        ok, msg = git_status
        print(f"  {msg}", file=sys.stdout if ok else sys.stderr)
    print("next:")
    if name is not None:
        print(f"  cd {name}")
    print("  ohm check          # or: ohm check " + entry_file)
    print("  ohm build          # emits to out/")
    return 0


def _find_kicad() -> str | None:
    """Best-effort locate KiCad's installed footprint libraries (optional — only
    needed to open an emitted .kicad_pcb, not to compile)."""
    import os
    import shutil

    for var in ("KICAD9_FOOTPRINT_DIR", "KICAD8_FOOTPRINT_DIR",
                "KICAD7_FOOTPRINT_DIR", "KICAD_FOOTPRINT_DIR"):
        v = os.environ.get(var)
        if v and Path(v).is_dir():
            return v
    candidates = [Path("/usr/share/kicad/footprints"),
                  Path("/usr/local/share/kicad/footprints")]
    candidates += [Path(f"C:/Program Files/KiCad/{maj}.0/share/kicad/footprints")
                   for maj in ("7", "8", "9")]
    for p in candidates:
        if p.is_dir():
            return str(p)
    cli = shutil.which("kicad-cli")
    return cli or None


def _cmd_doctor(args) -> int:
    """Diagnose the Ohm install and the current project environment."""
    import platform
    import shutil

    from ohm import project

    rows: list[tuple[str, str, str]] = []  # (status, label, detail)
    py_ok = sys.version_info >= (3, 12)
    rows.append(("ok" if py_ok else "fail", "Python",
                 platform.python_version() + f"  ({sys.executable})"
                 + ("" if py_ok else "  — Ohm needs 3.12+")))
    rows.append(("ok", "Ohm version", __version__))
    rows.append(("ok", "Ohm package", str(Path(__file__).resolve().parent)))

    exe = shutil.which("ohm")
    rows.append(("ok", "`ohm` on PATH", exe) if exe else
                ("warn", "`ohm` on PATH",
                 "not found — activate Ohm's venv, or `pipx install .` for a global command"))

    try:
        import lark
        rows.append(("ok", "lark", getattr(lark, "__version__", "installed")))
    except ImportError:
        rows.append(("fail", "lark", "missing — pip install 'lark>=1.1'"))

    enc = (_CONSOLE_ENCODING or "unknown")
    enc_ok = "utf-8" in enc.lower() or "utf8" in enc.lower()
    rows.append(("ok" if enc_ok else "warn", "Console encoding",
                 enc + ("" if enc_ok else
                        "  — non-UTF-8; Ohm forces UTF-8 on its own output, but "
                        "set PYTHONUTF8=1 so other tools agree")))

    kicad = _find_kicad()
    rows.append(("ok", "KiCad libraries", kicad) if kicad else
                ("warn", "KiCad libraries",
                 "not detected (optional) — needed only to open emitted .kicad_pcb"))

    proj, perr = project.discover()
    if perr:
        rows.append(("warn", "Project", perr))
    elif proj:
        rows.append(("ok", "Project",
                     f"{proj.name}  (root: {proj.root}, entry: {proj.entry})"))
    else:
        rows.append(("ok", "Project", "not inside one — run `ohm init <name>` to start"))

    mark = {"ok": "[ OK ]", "warn": "[WARN]", "fail": "[FAIL]"}
    print("ohm doctor")
    for status, label, detail in rows:
        print(f"  {mark[status]}  {label:<16} {detail}")
    fails = sum(1 for r in rows if r[0] == "fail")
    warns = sum(1 for r in rows if r[0] == "warn")
    print()
    if fails:
        print(f"{fails} problem(s) must be fixed for Ohm to run.")
        return 1
    print(f"ready — {warns} optional warning(s) above." if warns else "all good.")
    return 0


def _cmd_refactor(args) -> int:
    """Restructure an existing folder into a standard Ohm project. Dry-run by
    default (prints the plan, changes nothing); --apply executes it. Strictly
    additive — never overwrites a file; moves only stray root .ohm files whose
    destination is free."""
    from ohm import scaffold

    target = Path(args.dir)
    if not target.is_dir():
        print(f"error: '{target}' is not a directory", file=sys.stderr)
        return 1

    entry_file, err = scaffold.resolve_entry(target, args.entry)
    if err:
        print(f"error: {err}", file=sys.stderr)
        cands = sorted(p.name for p in target.glob("*.ohm"))
        if cands:
            print("       candidates: " + ", ".join(cands), file=sys.stderr)
        return 1
    assert entry_file is not None
    include_starter = not (target / entry_file).exists()
    dirs, plan = scaffold.build_plan(target, entry_file=entry_file,
                                     include_starter=include_starter)
    moves = scaffold.plan_moves(target, entry_file)
    would_create = sorted(p for p in plan if not p.exists())
    already = sorted(p for p in plan if p.exists())

    if not args.apply:
        print(f"ohm refactor (dry-run) — {target.resolve()}")
        note = "  (will be created)" if include_starter else "  (existing)"
        print(f"  entry: {entry_file}{note}")
        for p in would_create:
            print(f"  would create  {_rel(p, target)}")
        for src, dest in moves:
            print(f"  would move    {_rel(src, target)} -> {_rel(dest, target)}")
        if already:
            print("  keep as-is    " + ", ".join(_rel(p, target) for p in already))
        if not would_create and not moves:
            print("  already a well-formed Ohm project — nothing to do")
        else:
            print("\nre-run with --apply to execute (nothing was changed).")
        return 0

    try:
        created, kept = _write_scaffold(dirs, plan)
        done: list[tuple[Path, Path]] = []
        for src, dest in moves:
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():  # additive: never clobber
                continue
            src.rename(dest)
            done.append((src, dest))
    except OSError as exc:
        print(f"error: refactor failed: {exc.strerror or exc}", file=sys.stderr)
        return 1

    print(f"refactored {target.resolve()} into a standard Ohm project:")
    for p in sorted(created):
        print(f"  + {_rel(p, target)}")
    for src, dest in done:
        print(f"  moved {_rel(src, target)} -> {_rel(dest, target)}")
    if not created and not done:
        print("  (already well-formed — no changes)")
    if kept:
        print("  kept " + ", ".join(sorted(_rel(p, target) for p in kept)), file=sys.stderr)
    print("next: ohm check")
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
    # Ohm's output is UTF-8 on every platform; force it so a non-UTF-8 console
    # (Windows cp1252, Git Bash) never crashes on a diagnostic like Q003's "ΔV=".
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass  # captured/replaced stream (e.g. pytest) — nothing to reconfigure

    parser = argparse.ArgumentParser(prog="ohm", description="Ohm HDL compiler")
    parser.add_argument("--version", action="version", version=f"ohm {__version__}")
    sub = parser.add_subparsers(dest="command")

    p_init = sub.add_parser("init", aliases=["create-project"],
                            help="scaffold a new Ohm project directory (interactive wizard "
                                 "with no args on a terminal)")
    p_init.add_argument("name", nargs="?",
                        help="project directory to create (default: current directory)")
    p_init.add_argument("--git", action="store_true",
                        help="initialize a git repository in the project")
    p_init.add_argument("--no-git", action="store_true",
                        help="do not initialize a git repository")
    p_init.add_argument("-y", "--yes", action="store_true",
                        help="non-interactive: accept defaults, never prompt")

    sub.add_parser("doctor", help="diagnose the Ohm install & project environment")

    p_ref = sub.add_parser("refactor",
                           help="restructure an existing folder into a standard Ohm project")
    p_ref.add_argument("dir", nargs="?", default=".",
                       help="the directory to restructure (default: current directory)")
    p_ref.add_argument("--entry", help="the top-level .ohm file (when several exist)")
    p_ref.add_argument("--apply", action="store_true",
                       help="execute the plan (default: dry-run, changes nothing)")

    p_build = sub.add_parser("build", help="compile and emit outputs")
    p_build.add_argument("file", nargs="?",
                         help="the .ohm file (omit to use the project entry from ohm.toml)")
    p_build.add_argument("-o", "--out", default=None,
                         help="output directory (default: ohm.toml 'out', else '.')")
    p_build.add_argument("--emit",
                         choices=["netlist", "kicad", "zen", "constraints", "package",
                                  "eagle", "all"],
                         default="all")
    p_build.add_argument("--dump-ir", choices=["high", "typed", "low"])
    p_build.add_argument("--json-diagnostics", action="store_true")
    p_build.add_argument("--strict", action="store_true",
                         help="exit 1 (and emit nothing) on any warning or worse")
    p_build.add_argument("--debug-passes", action="store_true",
                         help="verify checkers do not mutate the IR (asserts on violation)")
    p_build.add_argument("--target-kicad", default=None,
                         help="KiCad major version to target (default: ohm.toml 'target', "
                              "else 7; v1 supports: 7)")
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

    if args.command in ("init", "create-project"):
        return _cmd_init(args)

    if args.command == "doctor":
        return _cmd_doctor(args)

    if args.command == "refactor":
        return _cmd_refactor(args)

    # framework-style discovery: bare `ohm build`/`ohm check` inside a project
    # use ohm.toml's entry/out/target. An explicit file, -o, or --target-kicad
    # always wins, and stays independent of ohm.toml when all are given.
    if args.command in ("build", "check"):
        from ohm import project

        need_entry = args.file is None
        need_out = args.command == "build" and args.out is None
        if need_entry or need_out:
            proj, perr = project.discover()
            if perr:
                print(f"error: {perr}", file=sys.stderr)
                return 1
            if need_entry:
                if proj is None:
                    print(f"error: `ohm {args.command}` needs a FILE, or run it inside "
                          "an Ohm project (a directory with ohm.toml — create one with "
                          "`ohm init`)", file=sys.stderr)
                    if args.command == "check":
                        print("       (or explain a code: ohm check --explain CODE)",
                              file=sys.stderr)
                    return 1
                args.file = str(proj.root / proj.entry)
            if need_out:
                args.out = str(proj.root / proj.out) if proj else "."
            # honor ohm.toml's target only when already relying on the project
            if args.command == "build" and args.target_kicad is None and proj is not None:
                args.target_kicad = _target_major(proj.target)

    if args.command == "build":
        if args.target_kicad is None:
            args.target_kicad = "7"
        if args.target_kicad != "7":
            print("error: only KiCad 7 is supported in v1", file=sys.stderr)
            return 1

    if args.command == "lsp":
        from ohm.lsp import serve

        return serve()

    if args.command == "gen-package":
        from decimal import Decimal

        from ohm.pkggen.footprint import emit_footprint
        from ohm.pkggen.packages import PkgError, build
        from ohm.pkggen.symbol import DEFAULT_FOOTPRINT_LIB, emit_symbol

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
        (outdir / f"{model.name}.kicad_mod").write_text(
            emit_footprint(model), encoding="utf-8", newline="\n")
        pins = [(p.id, f"P{p.id}", "passive") for p in model.pads]
        (outdir / f"{model.name}.kicad_sym").write_text(
            emit_symbol(model.name, pins,
                        footprint=f"{DEFAULT_FOOTPRINT_LIB}:{model.name}"),
            encoding="utf-8", newline="\n")
        print(f"generated {model.name}.kicad_mod + {model.name}.kicad_sym in {outdir}")
        return 0

    try:
        # utf-8-sig tolerates an editor-inserted BOM; sources are UTF-8 on every
        # platform (never the OS locale codepage, which crashes on non-ASCII)
        source = Path(args.file).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        detail = getattr(exc, "strerror", None) or exc
        print(f"error: cannot read '{args.file}': {detail}", file=sys.stderr)
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
                Path(args.file).write_text(formatted, encoding="utf-8", newline="\n")
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
            with open(args.out, "a", encoding="utf-8", newline="\n") as fh:
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
                Path(args.out).write_text(text, encoding="utf-8", newline="\n")
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
            board_text = Path(args.board).read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError) as exc:
            detail = getattr(exc, "strerror", None) or exc
            print(f"error: cannot read '{args.board}': {detail}", file=sys.stderr)
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
                (outdir / f"{stem}.net").write_text(
                    emit_netlist(low, args.file), encoding="utf-8", newline="\n")
            if args.emit in ("kicad", "all"):
                from ohm.emit_kicad_pro import emit_kicad_pro

                (outdir / f"{stem}.kicad_sch").write_text(
                    emit_kicad_sch(low, args.file), encoding="utf-8", newline="\n")
                (outdir / f"{stem}.kicad_pcb").write_text(
                    emit_kicad_pcb(low, args.file), encoding="utf-8", newline="\n")
                (outdir / f"{stem}.kicad_pro").write_text(
                    emit_kicad_pro(low, args.file), encoding="utf-8", newline="\n")
                _emit_generated_packages(result, outdir)
            if args.emit in ("package",):  # only the generated .kicad_mod/.kicad_sym
                _emit_generated_packages(result, outdir)
            if args.emit in ("eagle",):  # the same part as an Eagle .lbr library
                _emit_generated_packages(result, outdir, fmt="eagle")
            if args.emit in ("zen", "all"):
                from ohm.emit_zen import emit_zen  # interop path only (ADR-002)

                (outdir / f"{stem}.zen").write_text(
                    emit_zen(low, args.file), encoding="utf-8", newline="\n")
            if args.emit in ("constraints", "all"):
                from ohm.emit_constraints import emit_constraints

                (outdir / f"{stem}.constraints.json").write_text(
                    emit_constraints(low, args.file), encoding="utf-8", newline="\n")
        except OSError as exc:
            print(f"error: cannot write outputs to '{outdir}': {exc.strerror or exc}",
                  file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
