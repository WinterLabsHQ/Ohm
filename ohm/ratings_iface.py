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
    def ratings(self, component: ir.ComponentDef, pin: ir.Pin,
                part_attrs: dict[str, ir.Value] | None = None) -> Ratings: ...


class DeclaredRatings:
    """Provider (d): the pin's declared typed fields, falling back to part-level
    attrs (which already include instance overrides) — so a library block rated
    per instance, `Resistor(value=…, pmax=250mW)`, is fully Tier-Q-checkable."""

    def ratings(self, component: ir.ComponentDef, pin: ir.Pin,
                part_attrs: dict[str, ir.Value] | None = None) -> Ratings:
        attrs = part_attrs or {}

        def pick(pin_field: ir.Quantity | None, key: str) -> ir.Quantity | None:
            if pin_field is not None:
                return pin_field
            fallback = attrs.get(key)
            return fallback if isinstance(fallback, ir.Quantity) else None

        return Ratings(pick(pin.vmax, "vmax"), pick(pin.imax, "imax"),
                       pick(pin.pmax, "pmax"))
