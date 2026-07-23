"""Tier-Q ratings contract (manual Part 7/8; ADR-004).

The open contract: a provider returns the ratings for a (component, pin).
Default provider (d) reads the typed fields frozen in the source — always
hermetic. Richer providers (curated DB, authoring-time-frozen parts APIs) plug
in behind this interface; the open compiler never depends on any of them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ohm import ir


@dataclass(frozen=True)
class Ratings:
    vmax: ir.Quantity | None
    imax: ir.Quantity | None
    pmax: ir.Quantity | None


class RatingsProvider(Protocol):
    def ratings(self, component: ir.ComponentDef, pin: ir.Pin) -> Ratings: ...


class DeclaredRatings:
    """Provider (d): ratings are the pin's own declared typed fields."""

    def ratings(self, component: ir.ComponentDef, pin: ir.Pin) -> Ratings:
        return Ratings(pin.vmax, pin.imax, pin.pmax)
