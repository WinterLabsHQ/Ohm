"""Loader — resolves `use` imports into one merged Program (ADR-007).

Parse-stage I/O only (the design doc's "parse pass reading source"): local file
reads, no network, deterministic given the installed compiler version. Imports
are deduplicated by absolute path; cycles are diagnosed (R016), not followed.

Merge order is post-order (imports before their importer), so the entry file's
declarations come last — `design.top` stays the entry file's final module.
Every declaration is stamped with the file it came from, so diagnostics inside
an imported file point at that file with its own line numbers.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from ohm import ast_nodes as A
from ohm.diagnostics import Diagnostic
from ohm.parse import parse

_STD_DIR = Path(__file__).parent / "std"


def std_modules() -> list[str]:
    """Available `use std.<name>` targets (for diagnostics and docs)."""
    return sorted(p.stem for p in _STD_DIR.glob("*.ohm"))


def _resolve_target(use: A.UseDecl, importer_dir: Path) -> Path | None:
    if use.is_path:
        return (importer_dir / use.target).resolve()
    parts = use.target.split(".")
    if len(parts) == 2 and parts[0] == "std":
        return (_STD_DIR / f"{parts[1]}.ohm").resolve()
    return None  # unknown dotted namespace


def load(source: str, path: str = "<string>") -> tuple[A.Program, list[Diagnostic]]:
    """Entry source + its `use` closure → one merged Program + diagnostics."""
    diags: list[Diagnostic] = []
    decls_of: dict[str, tuple[A.Decl, ...]] = {}  # abspath -> stamped decls
    order: list[str] = []  # post-order emission
    stack: list[str] = []  # cycle detection

    def process(text: str, file_label: str, key: str, importer_dir: Path) -> None:
        program, pdiags = parse(text, file_label)
        diags.extend(pdiags)
        kept: list[A.Decl] = []
        for decl in program.decls:
            if not isinstance(decl, A.UseDecl):
                kept.append(dataclasses.replace(decl, file=file_label))
                continue
            target = _resolve_target(decl, importer_dir)
            if target is None or not target.is_file():
                known = ", ".join(f"std.{m}" for m in std_modules()) or "none installed"
                diags.append(Diagnostic(
                    "error", "R015", f"cannot find import '{decl.target}'",
                    decl.span, file=file_label,
                    help=f"available std modules: {known}; local files use the "
                         f'quoted form: use "./lib.ohm"'))
                continue
            tkey = str(target)
            if tkey in stack:
                diags.append(Diagnostic(
                    "error", "R016", f"import cycle: '{decl.target}' is already "
                    "being imported", decl.span, file=file_label))
                continue
            if tkey in decls_of:
                continue  # already loaded once; shared imports are fine
            try:
                ttext = target.read_text()
            except OSError as exc:
                diags.append(Diagnostic(
                    "error", "R015", f"cannot read import '{decl.target}': "
                    f"{exc.strerror or exc}", decl.span, file=file_label))
                continue
            stack.append(tkey)
            process(ttext, str(target), tkey, target.parent)
            stack.pop()
        decls_of[key] = tuple(kept)
        order.append(key)

    is_real_file = path != "<string>"
    entry_key = str(Path(path).resolve()) if is_real_file else "<entry>"
    entry_dir = Path(path).resolve().parent if is_real_file else Path.cwd()
    stack.append(entry_key)
    process(source, path, entry_key, entry_dir)
    stack.pop()
    merged = tuple(d for key in order for d in decls_of[key])
    return A.Program(merged), diags
