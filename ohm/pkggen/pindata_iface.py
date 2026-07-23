"""P11: pin-data contract (mirrors ratings_iface; manual Part 11, ADR-006).

The default provider reads the pin table already declared in the source —
always hermetic. Datasheet/parts-API/LLM acquisition happens at AUTHORING time
and is frozen into the source before any build; no provider on the build path
may fetch (N1). Generation is exact; acquisition is best-effort and must be
author-verified (N3).
"""

from __future__ import annotations

from typing import Protocol

from ohm import ir


class PinDataProvider(Protocol):
    def pin_table(self, component: ir.ComponentDef) -> list[tuple[str, str, str]]:
        """[(pad number, pin name, role)] in pad order."""
        ...


class DeclaredPinData:
    """Baseline provider: the component's own declared pins + padmap."""

    def pin_table(self, component: ir.ComponentDef) -> list[tuple[str, str, str]]:
        rows = [(p.pad or p.name, p.name, p.role) for p in component.pins]
        return sorted(rows, key=lambda r: (len(r[0]), r[0]))  # numeric-ish pad order
