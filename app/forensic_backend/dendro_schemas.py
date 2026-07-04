"""Pydantic request/response schemas for the dendrochronology API."""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


# ── Requests ─────────────────────────────────────────────────────────────

class DendroRequest(BaseModel):
    ring_widths: list[float] = Field(
        ..., min_length=20, max_length=2000,
        description="annual ring widths [mm], oldest first",
    )
    sample_id: str = Field("UNLABELLED", max_length=64)
    species: str = Field("Quercus robur", max_length=64)
    bark_edge: bool = Field(
        False, description="waney/bark edge present -> exact felling year"
    )
    sapwood_rings: int | None = Field(
        None, ge=0, le=200,
        description="number of sapwood rings measured (None if none preserved)",
    )
    sapwood_complete: bool = Field(
        False, description="sapwood measured to the heartwood boundary but no bark"
    )
    sapwood_min: int = Field(9, ge=0, le=100, description="regional sapwood estimate, min")
    sapwood_max: int = Field(41, ge=1, le=200, description="regional sapwood estimate, max")
    detrend_window: int = Field(7, ge=3, le=51, description="detrend moving-average window [yr]")
    top_k: int = Field(5, ge=1, le=20)

    @field_validator("ring_widths")
    @classmethod
    def positive_finite(cls, v: list[float]) -> list[float]:
        if not all(math.isfinite(x) and x > 0 for x in v):
            raise ValueError("ring widths must all be positive, finite numbers")
        return v

    @model_validator(mode="after")
    def sapwood_bounds(self) -> "DendroRequest":
        if self.sapwood_max < self.sapwood_min:
            raise ValueError("sapwood_max must be >= sapwood_min")
        return self


# ── Responses ────────────────────────────────────────────────────────────

class DateCandidateOut(BaseModel):
    end_year: int
    start_year: int
    correlation: float
    t_value: float
    glk_pct: float
    overlap_n: int


class FellingEstimateOut(BaseModel):
    terminus: Literal["exact", "range", "terminus_post_quem"]
    earliest: int
    latest: int | None
    note: str


class DendroMetaOut(BaseModel):
    sample_id: str
    species: str
    ring_count: int
    reference_id: str
    reference_span: str
    processing_ms: float


class DendroResponse(BaseModel):
    meta: DendroMetaOut
    dated: bool
    significant: bool
    best: DateCandidateOut
    felling: FellingEstimateOut
    t_margin: float = Field(..., description="best t-value minus runner-up t-value")
    widest_ring_year: int | None
    mean_ring_width_mm: float
    candidates: list[DateCandidateOut]
    detrended_sample: list[float]
    detrended_reference: list[float]
