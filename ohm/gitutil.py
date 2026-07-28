"""Git bootstrap for `ohm init --git` — the ONLY module in the package that
shells out (subprocess), and it is never imported on the build path.

Hermeticity (invariant N1) constrains the *compiler* — parse → resolve → check →
lower → emit must do no I/O, network, clock, or subprocess. Scaffolding a new
project and offering to `git init` it is a setup command, not compilation, so it
is allowed to run git. This module is quarantined here (and exempted by name in
the N1 import gate) precisely so the forbidden-import scan stays strict for every
other file, including cli.py's build orchestration."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def git_init(target: Path) -> tuple[bool, str]:
    """`git init` the scaffolded project. Idempotent and non-fatal: a missing git
    binary or an init failure is reported to the caller, never raised. Returns
    (ok, human message)."""
    if shutil.which("git") is None:
        return False, "git not found on PATH — skipped repository init"
    if (target / ".git").exists():
        return True, "git repository already present"
    try:
        subprocess.run(["git", "init", "-q"], cwd=str(target), check=True,
                       capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        return False, f"git init failed: {str(getattr(exc, 'stderr', '') or exc).strip()}"
    return True, "initialized git repository (.git/)"
