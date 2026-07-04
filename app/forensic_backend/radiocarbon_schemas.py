"""Pydantic request/response schemas for the radiocarbon calibration API."""

from __future__ import annotations

from pydantic import BaseModel, Field


# ── Requests ─────────────────────────────────────────────────────────────

class CalibrationRequest(BaseModel):
    radiocarbon_age_bp: float = Field(
        ..., ge=-500, le=55000, description="conventional 14C age [years BP]"
    )
    uncertainty_bp: float = Field(
        ..., gt=0, le=2000, description="1-sigma measurement error [years BP]"
    )
    sample_id: str = Field("UNLABELLED", max_length=64)
    lab_code: str = Field("", max_length=32, description="e.g. OxA-12345")


# ── Responses ────────────────────────────────────────────────────────────

class YearRangeOut(BaseModel):
    start_year: int
    end_year: int
    probability: float = Field(..., description="posterior mass within this range")


class CalibrationMetaOut(BaseModel):
    sample_id: str
    lab_code: str
    radiocarbon_age_bp: float
    uncertainty_bp: float
    curve_id: str
    processing_ms: float


class CalibrationResponse(BaseModel):
    meta: CalibrationMetaOut
    median_year: int = Field(..., description="calendar year, negative = BC")
    hpd_68: list[YearRangeOut] = Field(..., description="1-sigma (68.2%) ranges")
    hpd_95: list[YearRangeOut] = Field(..., description="2-sigma (95.4%) ranges")
    intercepts: list[int]
    cal_years: list[int] = Field(..., description="posterior x-axis, for plotting")
    density: list[float] = Field(..., description="normalised posterior per year")
