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

VERSION="${OHM_KICAD_VERSION:-10.0.5}"

# The official images start at 8.0.4 — there is no 7.x tag and there never was.
# Asking for one used to fail as an opaque "manifest unknown" from `docker pull`
# midway through a test run, which reads as a broken harness rather than as the
# fact it is. KiCad 7 can only be certified against a local install.
case "$VERSION" in
  7|7.*)
    echo "kicad_in_docker: no official kicad/kicad image exists for KiCad ${VERSION}" >&2
    echo "  (the published tags begin at 8.0.4). Certify KiCad 7 against a local" >&2
    echo "  install instead: OHM_KICAD_CLI=/path/to/kicad-cli pytest ..." >&2
    exit 2
    ;;
esac

IMAGE="kicad/kicad:${VERSION}"

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
