"""Where is KiCad? — one answer, for `ohm/` and for the test suite.

This resolution used to exist five times over, each copy hardcoding one Linux
path and each disagreeing with the others about which KiCad versions exist:
`ohm/cli.py` probed `KICAD9/8/7_FOOTPRINT_DIR` and `C:/Program Files/KiCad/{7,8,9}.0`,
while five test modules each wrote `/usr/share/kicad/...` inline. The consequence
was that `ohm doctor` reported "KiCad libraries not detected" on a machine running
the very version the README certifies, and the environment-verification tests
skipped on Windows and macOS even with KiCad installed — so a developer on this
project never ran them.

Two rules this module exists to keep:

- **Newest first.** The probe order is generated from `_MAJORS`, so supporting the
  next KiCad release costs one number rather than an edit in five files.
- **No I/O beyond `stat`.** `subprocess` is a forbidden import anywhere under
  `ohm/` (hermeticity N1, enforced by `tests/test_lower_emit.py`), and nothing
  here needs it: locating a directory is `is_dir()`, and locating a binary is
  `shutil.which`.

Nothing in this module is on the compile path. It answers "what is installed on
this machine", which is by definition an environment question, and is called by
`ohm doctor`, by `verify-pinouts` for its default library directory, and by the
test guards in `tests/conftest.py`.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

# Newest first: a machine with both 9 and 10 installed should be reported as the
# one KiCad will actually open the file with.
_MAJORS = ("10", "9", "8", "7")

# KiCad sets `KICAD<major>_FOOTPRINT_DIR` / `KICAD<major>_SYMBOL_DIR` in its own
# environment; the unversioned spellings are what a user sets by hand.
_ENV_TEMPLATES = tuple(f"KICAD{m}_{{kind}}_DIR" for m in _MAJORS) + ("KICAD_{kind}_DIR",)

_UNIX_PREFIXES = ("/usr/share/kicad", "/usr/local/share/kicad")
_MACOS_PREFIX = "/Applications/KiCad/KiCad.app/Contents/SharedSupport"

# directory name on disk -> the word KiCad uses in its environment variables
_ENV_WORD = {"footprints": "FOOTPRINT", "symbols": "SYMBOL"}


def _prefixes() -> list[str]:
    """Every directory that could be a KiCad share root, newest KiCad first."""
    out = [f"C:/Program Files/KiCad/{m}.0/share/kicad" for m in _MAJORS]
    out += [f"C:/Program Files (x86)/KiCad/{m}.0/share/kicad" for m in _MAJORS]
    out += list(_UNIX_PREFIXES)
    out.append(_MACOS_PREFIX)
    return out


def _lib_dir(kind: str) -> str | None:
    """Locate one library directory ("footprints" or "symbols").

    Environment first — an explicitly set variable is a statement about which
    KiCad this machine means, and must beat anything found by probing.
    """
    for template in _ENV_TEMPLATES:
        value = os.environ.get(template.format(kind=_ENV_WORD[kind]))
        if value and Path(value).is_dir():
            return value
    for prefix in _prefixes():
        candidate = Path(prefix) / kind
        if candidate.is_dir():
            return str(candidate)
    return None


def footprints_dir() -> str | None:
    """KiCad's installed `.pretty` footprint libraries, or None."""
    return _lib_dir("footprints")


def symbols_dir() -> str | None:
    """KiCad's installed `.kicad_sym` symbol libraries, or None."""
    return _lib_dir("symbols")


def kicad_cli() -> str | None:
    """The `kicad-cli` binary this machine should be tested against.

    `$OHM_KICAD_CLI` wins, so the SAME gate can be pointed at a KiCad that is not
    the one installed — a container wrapper, or a second version being certified.
    Compatibility with a KiCad release is then something measured rather than
    assumed from release notes, and certifying one does not disturb the
    developer's own toolchain.
    """
    override = os.environ.get("OHM_KICAD_CLI")
    if override:
        return override
    return shutil.which("kicad-cli")


def kicad_root() -> str | None:
    """Best-effort "is KiCad here at all", for `ohm doctor`.

    Reports the footprint libraries when they exist, since that is what an
    emitted `.kicad_pcb` needs to open, and falls back to the CLI so a
    libraries-less install is still reported as present rather than missing.
    """
    return footprints_dir() or kicad_cli()
