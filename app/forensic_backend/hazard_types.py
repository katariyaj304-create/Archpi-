"""Shared hazard classification types for the degradation engine."""

from __future__ import annotations

from enum import Enum


class HazardState(str, Enum):
    NEGLIGIBLE = "NEGLIGIBLE"
    MODERATE = "MODERATE"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


def state_from_score(score: float) -> HazardState:
    """Map a normalized 0.0-10.0 risk score onto a hazard state band."""
    if score >= 7.5:
        return HazardState.CRITICAL
    if score >= 5.0:
        return HazardState.HIGH
    if score >= 2.5:
        return HazardState.MODERATE
    return HazardState.NEGLIGIBLE
