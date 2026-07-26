"""Shared test guards: environment-verification tests (stdlib pinouts vs the
installed KiCad libraries) skip honestly where KiCad isn't present (e.g. CI).
The full gate including these runs on any machine with KiCad installed."""

from pathlib import Path

import pytest

requires_kicad_fp = pytest.mark.skipif(
    not Path("/usr/share/kicad/footprints").is_dir(),
    reason="KiCad footprint libraries not installed")
