"""Pydantic request/response schemas for the XRD analysis API."""

from __future__ import annotations

import math

from pydantic import BaseModel, Field, field_validator, model_validator


# ── Request ──────────────────────────────────────────────────────────────

class ConditioningOptions(BaseModel):
    als_lam: float = Field(1e5, gt=0, description="ALS smoothness penalty")
    als_p: float = Field(0.01, gt=0, lt=0.5, description="ALS asymmetry weight")
    als_niter: int = Field(10, ge=1, le=50)
    savgol_window: int | None = Field(None, ge=5, description="odd; auto if omitted")
    savgol_polyorder: int = Field(3, ge=1, le=5)


class PeakOptions(BaseModel):
    prominence_sigma: float = Field(5.0, gt=0, description="noise sigmas for dynamic prominence")
    min_rel_height: float = Field(0.02, ge=0, le=1)
    min_separation_deg: float = Field(0.15, gt=0)
    max_fitted_peaks: int = Field(8, ge=1, le=30)


class MatchOptions(BaseModel):
    tolerance_deg: float = Field(0.25, gt=0, le=1.0)
    min_confidence_pct: float = Field(25.0, ge=0, le=100)
    instrumental_fwhm_deg: float = Field(0.05, ge=0, le=0.5)


class XRDAnalysisRequest(BaseModel):
    two_theta: list[float] = Field(min_length=50, description="diffraction angles, degrees 2-theta")
    intensity: list[float] = Field(min_length=50, description="counts per angle")
    sample_id: str = Field("UNLABELLED", max_length=64)
    conditioning: ConditioningOptions = ConditioningOptions()
    peaks: PeakOptions = PeakOptions()
    matching: MatchOptions = MatchOptions()

    @field_validator("two_theta", "intensity")
    @classmethod
    def finite_values(cls, v: list[float]) -> list[float]:
        if not all(math.isfinite(x) for x in v):
            raise ValueError("array contains NaN or infinite values")
        return v

    @model_validator(mode="after")
    def equal_lengths(self) -> "XRDAnalysisRequest":
        if len(self.two_theta) != len(self.intensity):
            raise ValueError(
                f"two_theta ({len(self.two_theta)}) and intensity "
                f"({len(self.intensity)}) must have equal length"
            )
        return self


# ── Response ─────────────────────────────────────────────────────────────

class ConditionedArrays(BaseModel):
    two_theta: list[float]
    raw: list[float]
    baseline: list[float]
    processed: list[float]
    noise_sigma: float
    step_deg: float


class PeakOut(BaseModel):
    two_theta: float
    height: float
    prominence: float
    fwhm_deg: float
    left_base_deg: float
    right_base_deg: float
    area: float


class FittedPeakOut(BaseModel):
    two_theta: float
    model: str
    amplitude: float
    fwhm_deg: float
    area: float
    r_squared: float


class LineMatchOut(BaseModel):
    ref_two_theta: float
    ref_rel_intensity: float
    hkl: str
    observed_two_theta: float
    delta_deg: float
    observed_height: float


class PhaseMatchOut(BaseModel):
    phase_id: str
    name: str
    formula: str
    rir: float
    confidence_pct: float
    matched_lines: list[LineMatchOut]


class CrystalliteOut(BaseModel):
    two_theta_deg: float
    theta_deg: float
    beta_obs_deg: float
    beta_sample_deg: float
    size_nm: float


class PhaseWeightOut(BaseModel):
    phase_id: str
    name: str
    formula: str
    rir: float
    integrated_intensity: float
    weight_pct: float


class QuantitativeOut(BaseModel):
    crystallite_estimates: list[CrystalliteOut]
    avg_crystallite_size_nm: float | None
    avg_crystallite_size_um: float | None
    phase_weights: list[PhaseWeightOut]
    silica_ratio_pct: float
    calcium_content_pct: float
    crystallinity_pct: float


class AnalysisMeta(BaseModel):
    sample_id: str
    n_points: int
    two_theta_min: float
    two_theta_max: float
    n_peaks_detected: int
    n_peaks_fitted: int
    processing_ms: float
    scherrer_k: float
    wavelength_nm: float


class XRDAnalysisResponse(BaseModel):
    meta: AnalysisMeta
    conditioned: ConditionedArrays
    detected_peaks: list[PeakOut]
    fitted_peaks: list[FittedPeakOut]
    phase_matches: list[PhaseMatchOut]
    quantitative: QuantitativeOut
