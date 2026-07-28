"""`ohm.toml` discovery + parsing.

Lets `ohm build` / `ohm check` run with no file argument from anywhere inside a
project (framework-style), the same way `next dev` finds `next.config`. An
explicit file argument on the command line always wins over discovery."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Project:
    root: Path        # directory holding ohm.toml
    name: str
    entry: str        # entry file, relative to root
    out: str          # output directory, relative to root
    target: str       # emit target (v1: "kicad7")


def find_root(start: Path | None = None) -> Path | None:
    """Walk up from `start` (default: cwd) to the filesystem root, returning the
    first directory that contains an ohm.toml, or None."""
    base = (start or Path.cwd()).resolve()
    for d in (base, *base.parents):
        if (d / "ohm.toml").is_file():
            return d
    return None


def load(root: Path) -> tuple[Project | None, str | None]:
    """Parse root/ohm.toml. Returns (project, None) or (None, error message)."""
    path = root / "ohm.toml"
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError) as exc:
        return None, f"cannot read {path}: {getattr(exc, 'strerror', None) or exc}"
    except tomllib.TOMLDecodeError as exc:
        return None, f"invalid ohm.toml ({path}): {exc}"
    proj = data.get("project", {})
    if not isinstance(proj, dict):
        return None, f"invalid ohm.toml ({path}): [project] must be a table"
    name = str(proj.get("name") or root.name)
    entry = str(proj.get("entry") or f"{name}.ohm")
    out = str(proj.get("out") or "out")
    target = str(proj.get("target") or "kicad7")
    return Project(root, name, entry, out, target), None


def discover(start: Path | None = None) -> tuple[Project | None, str | None]:
    """Find and load the enclosing project. Returns (None, None) when there is
    no ohm.toml above `start`, or (None, error) when one exists but is broken."""
    root = find_root(start)
    if root is None:
        return None, None
    return load(root)
