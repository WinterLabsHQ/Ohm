"""Ohm — AI-native, physics-aware hardware description language."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("ohm-hdl")
except PackageNotFoundError:  # running from a checkout, not installed
    __version__ = "0.1.1"
