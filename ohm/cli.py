"""Ohm CLI driver: `ohm build|check|fmt|explain|lsp|gen-package`."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ohm import __version__, ir
from ohm.driver import compile_source
from ohm.emit_kicad import emit_kicad_pcb, emit_kicad_sch, emit_kicad_sheets
from ohm.emit_netlist import emit_netlist

# The real console encoding at process start, captured before main() forces the
# streams to UTF-8. `ohm doctor` reports this so a non-UTF-8 console is visible.
_CONSOLE_ENCODING = getattr(sys.stdout, "encoding", None)


def _emit_generated_packages(result, outdir: Path, fmt: str = "kicad",
                             stem: str = "ohm") -> None:
    """P-GEN-PACKAGE: write one .kicad_mod per distinct generated package
    (spec-named, shared across parts) and one .kicad_sym per component.

    fmt="eagle" writes one self-contained .lbr per component instead — Eagle keeps
    package, symbol and their pin<->pad connects in a single library file.

    fmt="altium" writes one multi-part `<stem>.PcbLib` and one `<stem>.SchLib`, which
    is how Altium libraries ship — a file per part would be unusable there."""
    from ohm.ir import GenerateSpec
    from ohm.pkggen.footprint import emit_footprint
    from ohm.pkggen.packages import build_from_spec, spec_name
    from ohm.pkggen.pindata_iface import DeclaredPinData
    from ohm.pkggen.altium import SchSymbol
    from ohm.pkggen.packages import PackageModel
    from ohm.pkggen.symbol import DEFAULT_FOOTPRINT_LIB, emit_symbol

    comps = {c.name: c for c in result.design.components}
    footprints_done: set[str] = set()
    symbols_done: set[str] = set()
    altium_models: dict[str, PackageModel] = {}
    altium_symbols: list[SchSymbol] = []
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
        if fmt == "altium":
            attrs = {k: str(v) for k, v in comp.attrs}
            altium_models.setdefault(fp_name, build_from_spec(spec, fp_name))
            if comp.name not in symbols_done:
                symbols_done.add(comp.name)
                altium_symbols.append(SchSymbol(
                    comp.name, DeclaredPinData().pin_table(comp), fp_name, attrs,
                    attrs.get("description", "")))
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

    if fmt == "altium" and altium_models:
        from ohm.pkggen.altium import emit_pcblib, emit_schlib

        (outdir / f"{stem}.PcbLib").write_bytes(
            emit_pcblib(list(altium_models.values())))
        (outdir / f"{stem}.SchLib").write_bytes(emit_schlib(altium_symbols))


# The KiCad versions `--target-kicad` accepts. They all emit the SAME bytes:
# Ohm writes the KiCad 7 file format and relies on KiCad reading forward, which
# is the direction KiCad guarantees, so a newer stamp would cost backward
# compatibility for no gain. The flag names the KiCad you are targeting, not a
# different serialisation — refusing 10 here told a user on the version the
# README certifies that their KiCad was unsupported.
SUPPORTED_KICAD = ("7", "8", "9", "10")


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
    name, git); it is fully scriptable with a name argument plus
    --git/--no-git/--yes."""
    from ohm import scaffold

    interactive = sys.stdin.isatty() and not args.yes
    name = args.name
    if name is None and interactive:
        print("Creating a new Ohm project.")
        # default to a NEW named subfolder (create-app style), never the current
        # directory — that surprised users into scaffolding their parent folder.
        # Use "." explicitly to scaffold into the current directory.
        entered = _ask('  Project name [my-board] (use "." for the current dir): ',
                       "my-board")
        name = None if entered == "." else entered

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
    needed to open an emitted .kicad_pcb, not to compile).

    The probe itself lives in `ohm/kicad_env.py`, which the test suite shares, so
    "where is KiCad" has one definition. This version knew nothing about KiCad 10
    and reported it as absent on exactly the version the README certifies.
    """
    from ohm.kicad_env import kicad_root

    return kicad_root()


def _default_symbol_dir() -> str:
    """`--lib-dir`'s default: this machine's KiCad symbols if they can be found.

    Falls back to the Linux path so `--help` still prints something concrete and
    the error, when it comes, names a path the user can recognise.
    """
    from ohm.kicad_env import symbols_dir

    return symbols_dir() or "/usr/share/kicad/symbols"


def _default_footprint_dir() -> str | None:
    """`verify-footprints --lib-dir`'s default: this machine's KiCad footprints.

    None where KiCad is absent, and NOT a fallback path as `_default_symbol_dir`
    uses: "no libraries here" is the answer the footprint command has to be able
    to give, because reporting every footprint in the design as missing from a
    directory that does not exist would be a page of confident nonsense.
    """
    from ohm.kicad_env import footprints_dir

    return footprints_dir()


def _print_verify_rows(rows: list[tuple[str, str, str, list[str]]]) -> tuple[int, int]:
    """Render `verify-pinouts` / `verify-footprints` rows; returns (bad, unverifiable).

    Shared so the two commands cannot drift apart in format: they are one habit
    with two subjects, and a user who has read one report should not have to
    re-learn how to read the other.
    """
    bad = unver = 0
    for name, status, detail, mismatches in rows:
        if status == "ok":
            print(f"ok           {name}  ({detail})")
        elif status == "unverifiable":
            unver += 1
            print(f"UNVERIFIABLE {name}  — {detail}")
        else:
            bad += 1
            print(f"MISMATCH     {name}  vs {detail}  "
                  f"({len(mismatches)} finding{'s' if len(mismatches) != 1 else ''})")
            for line in mismatches[:12]:
                print(f"                 {line}")
            if len(mismatches) > 12:
                print(f"                 … {len(mismatches) - 12} more")
    return bad, unver


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
                                  "eagle", "altium", "allegro", "all"],
                         default="all")
    p_build.add_argument("--sheets", choices=["auto", "flat", "hier"], default="auto",
                         help="schematic sheets: 'auto' (default) keeps one sheet "
                              "when the drawing fits A3 and splits into one A3 "
                              "sheet per module when it does not; 'flat' forces a "
                              "single sheet at whatever size it takes; 'hier' "
                              "always splits")
    p_build.add_argument("--dump-ir", choices=["high", "typed", "low"])
    p_build.add_argument("--json-diagnostics", action="store_true")
    p_build.add_argument("--strict", action="store_true",
                         help="exit 1 (and emit nothing) on any warning or worse")
    p_build.add_argument("--debug-passes", action="store_true",
                         help="verify checkers do not mutate the IR (asserts on violation)")
    p_build.add_argument("--target-kicad", default=None,
                         help="KiCad major version to target (default: ohm.toml 'target', "
                              "else 7; accepts 7-10 — all emit the KiCad 7 format, "
                              "which every one of them reads)")
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
    # S012 reads pad ids against the KiCad the user is actually building for; with
    # no target it reports across every major the shipped table covers
    p_check.add_argument("--target-kicad", default=None,
                         help="KiCad major version to target (default: ohm.toml 'target', "
                              "else 7), which decides how footprint pad ids are checked")
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

    p_ver = sub.add_parser("verify-pinouts",
                           help="diff every pin↔pad map against real KiCad symbol libraries")
    p_ver.add_argument("file", help="the Ohm source")
    # resolved rather than hardcoded: the old Linux-only default meant
    # `verify-pinouts` had no usable default on Windows or macOS at all.
    p_ver.add_argument("--lib-dir", default=_default_symbol_dir(),
                       help="directory of .kicad_sym libraries (default: %(default)s)")

    p_vfp = sub.add_parser("verify-footprints",
                           help="diff every declared pad against real KiCad footprint libraries")
    p_vfp.add_argument("file", help="the Ohm source")
    fp_default = _default_footprint_dir()
    p_vfp.add_argument("--lib-dir", default=fp_default,
                       help="directory of .pretty libraries (default: "
                            + (fp_default or "this machine's installed KiCad, if any") + ")")

    p_sign = sub.add_parser("signoff",
                            help="verify a ROUTED .kicad_pcb against the Ohm source (ADR-011)")
    p_sign.add_argument("file", help="the Ohm source")
    p_sign.add_argument("board", help="the routed .kicad_pcb")
    p_sign.add_argument("--no-geometry", action="store_true",
                        help="skip the V007 geometry gate (KiCad's DRC engine)")
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
            if args.target_kicad is None and proj is not None:
                args.target_kicad = _target_major(proj.target)

    if args.command in ("build", "check"):
        if args.target_kicad is None:
            args.target_kicad = "7"
        if args.target_kicad not in SUPPORTED_KICAD:
            print(f"error: unknown KiCad target '{args.target_kicad}' — "
                  f"supported: {', '.join(SUPPORTED_KICAD)}", file=sys.stderr)
            return 1
        # Ohm writes ONE format for every target on purpose (see SUPPORTED_KICAD),
        # so say so rather than letting a user believe `--target-kicad 10` bought
        # them different bytes. Only worth saying when they asked for a target
        # that is not the format's own version, and only when bytes are written.
        if args.command == "build" and args.target_kicad != "7":
            print(f"note: Ohm writes the KiCad 7 file format; KiCad "
                  f"{args.target_kicad} reads it forward. Output is identical "
                  f"for every supported target.", file=sys.stderr)

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

    if args.command == "verify-pinouts":
        from ohm.loader import load
        from ohm.provenance import verify_against_libs
        from ohm.resolve import resolve

        program, load_diags = load(source, args.file)
        design, res_diags = resolve(program, args.file)
        if any(d.severity == "error" for d in load_diags + res_diags):
            for d in load_diags + res_diags:
                if d.severity == "error":
                    print(d.machine(), file=sys.stderr)
            return 1
        rows = verify_against_libs(design, args.lib_dir)
        bad, unver = _print_verify_rows(rows)
        print(f"\n{len(rows)} attested components: {len(rows) - bad - unver} verified, "
              f"{bad} MISMATCHED, {unver} unverifiable")
        if unver:
            print("unverifiable is not the same as correct — attest those by hand "
                  'with pinout_source = "datasheet:<ref>"')
        return 1 if bad else 0

    if args.command == "verify-footprints":
        from ohm.loader import load
        from ohm.resolve import resolve
        from ohm.verify_footprints import generated_packages, verify_against_footprints

        program, load_diags = load(source, args.file)
        design, res_diags = resolve(program, args.file)
        if any(d.severity == "error" for d in load_diags + res_diags):
            for d in load_diags + res_diags:
                if d.severity == "error":
                    print(d.machine(), file=sys.stderr)
            return 1
        # Abstaining is a 0. This command reads the REAL libraries, so with none
        # on the machine there is nothing to read — which says nothing whatever
        # about the design, and must not be reported as if it did. `ohm check`'s
        # S012 is the half that still answers here, from the shipped table.
        if args.lib_dir is None or not Path(args.lib_dir).is_dir():
            where = (f"'{args.lib_dir}' is not a directory" if args.lib_dir else
                     "no installed KiCad footprint libraries were found on this machine")
            print(f"cannot check: {where}.\n"
                  "`ohm verify-footprints` re-reads the footprint libraries KiCad "
                  "actually has, so\nwith none installed there is nothing to compare "
                  "against — this is not a finding about\nthe design. Install KiCad, or "
                  "point --lib-dir (or $KICAD_FOOTPRINT_DIR) at a directory\nof .pretty "
                  "libraries. `ohm check` still checks pad ids against Ohm's shipped table.")
            return 0
        rows = verify_against_footprints(design, args.lib_dir)
        bad, unver = _print_verify_rows(rows)
        print(f"\n{len(rows)} library footprints: {len(rows) - bad - unver} verified, "
              f"{bad} MISMATCHED, {unver} unverifiable")
        if generated := generated_packages(design):
            print(f"{generated} generate(...) package{'s' if generated != 1 else ''} "
                  "not applicable — Ohm emits that geometry itself")
        if unver:
            print("unverifiable is not the same as correct — those footprints are not "
                  f"in {args.lib_dir}, so nothing read them")
        return 1 if bad else 0

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
        if not args.no_geometry:
            # source-fidelity (V001-V006) and geometry (V007) are one verdict:
            # a board can match its source exactly and still be unmanufacturable
            from ohm.drc import DrcUnavailable, run_geometry_drc

            try:
                diags = list(diags) + run_geometry_drc(args.board)
            except DrcUnavailable as exc:
                print(f"warning: geometry gate skipped: {exc}", file=sys.stderr)
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
                            debug_passes=getattr(args, "debug_passes", False),
                            target_kicad=getattr(args, "target_kicad", None))
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

                mode = getattr(args, "sheets", "auto")
                sheets = ({f"{stem}.kicad_sch":
                           emit_kicad_sch(low, args.file, result.design)}
                          if mode == "flat"
                          else emit_kicad_sheets(low, args.file, result.design,
                                                 force=mode == "hier"))
                for fname, text in sheets.items():
                    (outdir / fname).write_text(text, encoding="utf-8", newline="\n")
                # The drawing gate. Connectivity is proved by the netlist; this
                # is the other half — that the sheet can be READ. Advisory, and
                # reported at emit time because that is when the geometry exists.
                from ohm.check_drawing import check_drawing

                for fname, text in sorted(sheets.items()):
                    for d in check_drawing(text, str(outdir / fname)):
                        print(d.machine(), file=sys.stderr)
                (outdir / f"{stem}.kicad_pcb").write_text(
                    emit_kicad_pcb(low, args.file), encoding="utf-8", newline="\n")
                (outdir / f"{stem}.kicad_pro").write_text(
                    emit_kicad_pro(low, args.file, result.design),
                    encoding="utf-8", newline="\n")
                _emit_generated_packages(result, outdir)
            if args.emit in ("package",):  # only the generated .kicad_mod/.kicad_sym
                _emit_generated_packages(result, outdir)
            if args.emit in ("eagle",):  # the same part as an Eagle .lbr library
                _emit_generated_packages(result, outdir, fmt="eagle")
            if args.emit in ("altium",):  # native .PcbLib + .SchLib (ADR-020)
                _emit_generated_packages(result, outdir, fmt="altium", stem=stem)
            if args.emit in ("allegro",):  # Allegro third-party logic import
                from ohm.emit_allegro import emit_allegro

                assert result.design is not None  # result.ok gated above
                for fname, text in emit_allegro(result.design, low, stem).items():
                    (outdir / fname).write_text(text, encoding="utf-8", newline="\n")
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
