"""Project scaffolding shared by `ohm init` / `ohm create-project` and
`ohm refactor`.

Templates live in ohm/templates/ and ship as package data, so scaffolding works
identically from a source checkout and from a global `pipx install`. Everything
here is *strictly additive*: it builds a plan; the caller decides what to write
and never overwrites an existing file."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

# Files that make a directory a well-formed Ohm project — everything except the
# top-level .ohm entry (which init always writes and refactor writes only when
# the directory has no entry yet).
_SUPPORT: dict[str, str] = {
    "ohm.toml": "ohm.toml.tmpl",
    "README.md": "readme.md.tmpl",
    "AGENTS.md": "agents.md.tmpl",
    ".gitignore": "gitignore.tmpl",
    "docs/LANGUAGE.md": "language.md",
    "lib/.gitkeep": "",
}
_DIRS = ("lib", "out", "docs")


def module_name(project: str) -> str:
    """A valid Ohm module identifier ([A-Za-z_][A-Za-z0-9_]*) from a project
    name: split on non-alphanumerics, PascalCase the parts."""
    parts = [p for p in "".join(c if c.isalnum() else " " for c in project).split() if p]
    ident = "".join(p[:1].upper() + p[1:] for p in parts) or "Board"
    if not (ident[0].isalpha() or ident[0] == "_"):
        ident = "Board" + ident
    return ident


def safe_stem(project: str) -> str:
    """A safe filename stem for the entry .ohm file from a project name."""
    stem = "".join(c if (c.isalnum() or c in "._-") else "_" for c in project)
    return stem or "board"


def _template(name: str) -> str:
    return (files("ohm") / "templates" / name).read_text(encoding="utf-8")


def _render(name: str, *, project: str, module: str, stem: str) -> str:
    text = _template(name)
    return (text.replace("{{PROJECT}}", project)
                .replace("{{MODULE}}", module)
                .replace("{{STEM}}", stem))


def starter_module(project: str, module: str, stem: str) -> str:
    """The starter .ohm entry — a design that runs clean through `ohm check`."""
    return _render("starter.ohm.tmpl", project=project, module=module, stem=stem)


def build_plan(target: Path, *, entry_file: str,
               include_starter: bool) -> tuple[list[Path], dict[Path, str]]:
    """Return (dirs_to_ensure, {path: content}) for scaffolding `target`.

    `entry_file` is the entry basename ohm.toml points at (e.g. "board.ohm").
    `include_starter` writes a starter design at that path (init, or refactor of
    a directory with no .ohm yet)."""
    project = target.resolve().name
    module = module_name(project)
    stem = Path(entry_file).stem

    dirs = [target, *(target / d for d in _DIRS)]
    plan: dict[Path, str] = {}
    for rel, tmpl in _SUPPORT.items():
        content = "" if tmpl == "" else (
            _template(tmpl) if not tmpl.endswith(".tmpl")
            else _render(tmpl, project=project, module=module, stem=stem))
        plan[target / rel] = content
    if include_starter:
        plan[target / entry_file] = starter_module(project, module, stem)
    return dirs, plan


def resolve_entry(target: Path, explicit: str | None) -> tuple[str | None, str | None]:
    """Decide which .ohm file is the project entry for `ohm refactor`.

    Returns (entry_file, None) or (None, error). Deterministic and never guesses
    when it is ambiguous — it asks for --entry instead."""
    ohm_files = sorted(p.name for p in target.glob("*.ohm"))
    if explicit:
        if explicit not in ohm_files and not (target / explicit).is_file():
            return None, f"--entry '{explicit}' is not a .ohm file in {target}"
        return explicit, None
    named = f"{safe_stem(target.resolve().name)}.ohm"
    if named in ohm_files:
        return named, None
    if len(ohm_files) == 1:
        return ohm_files[0], None
    if len(ohm_files) == 0:
        return named, None  # caller will create a starter
    return None, ("multiple top-level .ohm files — pass --entry NAME to pick the "
                  "top-level module")


def plan_moves(target: Path, entry_file: str) -> list[tuple[Path, Path]]:
    """Root-level .ohm files other than the entry are reusable blocks; plan to
    move them under lib/. Additive: skip any whose destination already exists."""
    lib = target / "lib"
    moves: list[tuple[Path, Path]] = []
    for p in sorted(target.glob("*.ohm")):
        if p.name == entry_file:
            continue
        dest = lib / p.name
        if not dest.exists():
            moves.append((p, dest))
    return moves
