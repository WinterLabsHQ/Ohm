"""Ohm CLI driver. Commands: build, check, fmt (later phases add more)."""

import argparse
import sys

from ohm import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ohm", description="Ohm HDL compiler")
    parser.add_argument("--version", action="version", version=f"ohm {__version__}")
    sub = parser.add_subparsers(dest="command")
    # ponytail: subcommands (build/check/fmt) wired in as their phases land
    parser.parse_args(argv)
    if len(sys.argv) <= 1 and argv is None:
        parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
