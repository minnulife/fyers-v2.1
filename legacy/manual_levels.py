"""Validated manual price-level strategies for index/futures signals.

This module does not place orders. It validates a trader-authored plan and emits
CE/PE intent when the configured entry is crossed. The Engine/API layer can then
apply its normal risk checks before execution.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class Direction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True)
class ManualLevelPlan:
    name: str
    instrument: str
    direction: Direction
    entry: float
    targets: List[float]
    stop_loss: float
    final_risk_levels: List[float] = field(default_factory=list)
    correction_level: Optional[float] = None
    risk_profile: str = "LOW"
    enabled: bool = True

    def validate(self) -> List[str]:
        errors: List[str] = []
        if self.entry <= 0 or self.stop_loss <= 0:
            errors.append("Entry and stop-loss must be positive.")
        if not self.targets:
            errors.append("At least one target is required.")
        if any(x <= 0 for x in self.targets + self.final_risk_levels):
            errors.append("Targets and risk levels must be positive.")

        if self.direction == Direction.SELL:
            if self.stop_loss <= self.entry:
                errors.append("For SELL, stop-loss must be above entry.")
            if any(t >= self.entry for t in self.targets):
                errors.append("For SELL, every target must be below entry.")
            if any(r >= self.entry for r in self.final_risk_levels):
                errors.append("For SELL, final-risk levels must be below entry.")
        else:
            if self.stop_loss >= self.entry:
                errors.append("For BUY, stop-loss must be below entry.")
            if any(t <= self.entry for t in self.targets):
                errors.append("For BUY, every target must be above entry.")
            if any(r <= self.entry for r in self.final_risk_levels):
                errors.append("For BUY, final-risk levels must be above entry.")

        max_distance = max(5000.0, self.entry * 0.20)
        if abs(self.stop_loss - self.entry) > max_distance:
            errors.append("Stop-loss is unusually far from entry; check for a typing error.")
        return errors

    def signal(self, previous_price: float, current_price: float) -> Optional[str]:
        if not self.enabled or self.validate():
            return None
        if self.direction == Direction.SELL:
            crossed = previous_price > self.entry >= current_price
            return "PE" if crossed else None
        crossed = previous_price < self.entry <= current_price
        return "CE" if crossed else None


EXAMPLE_NIFTY_JULY_SHORT = ManualLevelPlan(
    name="Nifty July Future Short",
    instrument="NIFTY JUL FUT",
    direction=Direction.SELL,
    entry=23800,
    targets=[23722, 23636],
    final_risk_levels=[23542, 23456],
    stop_loss=23850,
    correction_level=23770,
    risk_profile="LOW",
)
