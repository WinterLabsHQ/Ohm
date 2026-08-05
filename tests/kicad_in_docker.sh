#!/usr/bin/env bash
# A `kicad-cli` that is some OTHER KiCad than the one installed.
#
# Point the roundtrip gate at it to certify a KiCad release without changing the
# machine's own toolchain:
#
#   OHM_KICAD_VERSION=10.0.5 \
#   OHM_KICAD_CLI=tests/kicad_in_docker.sh \
#     .venv/bin/python -m pytest tests/test_schematic_roundtrip.py -q
#
# Why this exists: "compatible with KiCad N" is a claim about a program Ohm does
# not ship and cannot inspect. The only honest way to make it is to run KiCad N
# over Ohm's output and compare — and doing that by upgrading the developer's
# KiCad trades one unverified version for another.
#
# The gate hands absolute host paths on the command line, so the host filesystem
# is mounted at the same paths inside the container and the arguments need no
# rewriting. Read-write: the gate writes its output next to its input in a
# pytest tmp_path.
set -euo pipefail

IMAGE="kicad/kicad:${OHM_KICAD_VERSION:-10.0.5}"

# Mount the directories the arguments actually name — not $HOME — so a stray
# path outside them fails loudly in the container instead of silently resolving
# to something else. /tmp covers pytest's tmp_path; the repo covers examples/.
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MOUNTS=(-v "/tmp:/tmp" -v "${REPO}:${REPO}")
[ -n "${TMPDIR:-}" ] && [ "${TMPDIR}" != "/tmp" ] && MOUNTS+=(-v "${TMPDIR}:${TMPDIR}")

exec docker run --rm \
  --user "$(id -u):$(id -g)" \
  -e HOME=/tmp \
  "${MOUNTS[@]}" \
  -w "$(pwd)" \
  "${IMAGE}" \
  kicad-cli "$@"
