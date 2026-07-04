"""Pydantic request/response schemas for the degradation hazard API."""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .hazard_types import HazardState


# ── Requests ─────────────────────────────────────────────────────────────

class MaterialProfileIn(BaseModel):
    porosity_pct: float = Field(..., gt=0, le=60, description="open porosity [%]")
    tensile_strength_mpa: float = Field(
        3.0, gt=0, le=50, description="tensile capacity [MPa]; aged limestone ~3.0"
    )


class RustTieProfileIn(BaseModel):
    diameter_mm: float = Field(20.0, ge=1, le=500, description="original tie diameter [mm]")
    i_corr_ua_cm2: float | None = Field(
        None, ge=0, le=1000,
        description="measured corrosion current density [uA/cm^2]; "
                    "estimated from time of wetness when omitted",
    )
    exposure_years: float = Field(100.0, gt=0, le=2000, description="corrosion duration [years]")
    oxide: Literal["Fe(OH)3", "Fe3O4"] = Field(
        "Fe(OH)3", description="governing corrosion product"
    )
    cover_mm: float = Field(50.0, ge=5, le=1000, description="masonry sleeve thickness [mm]")


class HazardEvaluationRequest(BaseModel):
    relative_humidity_pct: list[float] = Field(
        ..., min_length=48, description="hourly relative humidity [%]"
    )
    temperature_c: list[float] = Field(
        ..., min_length=48, description="hourly ambient temperature [C]"
    )
    material: MaterialProfileIn
    rust_tie: RustTieProfileIn = RustTieProfileIn()
    site_id: str = Field("UNLABELLED", max_length=64)

    @field_validator("relative_humidity_pct", "temperature_c")
    @classmethod
    def finite_values(cls, v: list[float]) -> list[float]:
        if not all(math.isfinite(x) for x in v):
            raise ValueError("sensor series contains NaN or infinite values")
        return v

    @model_validator(mode="after")
    def equal_lengths(self) -> "HazardEvaluationRequest":
        if len(self.relative_humidity_pct) != len(self.temperature_c):
            raise ValueError(
                f"humidity ({len(self.relative_humidity_pct)}) and temperature "
                f"({len(self.temperature_c)}) series must have equal length"
            )
        return self


class CrystallizationPressureRequest(BaseModel):
    salt_id: Literal["halite", "mirabilite", "thenardite"] = "halite"
    temperature_c: float = Field(..., ge=-60, le=60, description="ambient temperature [C]")
    supersaturation: float = Field(
        ..., gt=0, le=1000, description="supersaturation ratio S = C/C_sat [-]"
    )
    tensile_threshold_mpa: float = Field(3.0, gt=0, le=50)


class RustJackingRequest(BaseModel):
    diameter_mm: float = Field(..., ge=1, le=500)
    i_corr_ua_cm2: float = Field(..., ge=0, le=1000)
    exposure_years: float = Field(..., gt=0, le=2000)
    oxide: Literal["Fe(OH)3", "Fe3O4"] = "Fe(OH)3"
    cover_mm: float = Field(50.0, ge=5, le=1000)
    tensile_threshold_mpa: float = Field(3.0, gt=0, le=50)


# ── Responses ────────────────────────────────────────────────────────────

class CrystallizationPressureOut(BaseModel):
    salt_id: str
    temperature_c: float
    supersaturation: float
    pressure_mpa: float
    tensile_threshold_mpa: float
    stress_ratio: float
    state: HazardState


class RustJackingOut(BaseModel):
    diameter_mm: float
    i_corr_ua_cm2: float
    exposure_years: float
    oxide: str
    expansion_ratio: float
    section_loss_mm: float
    iron_mass_loss_kg_m: float
    free_expansion_mm: float
    effective_expansion_mm: float
    interface_pressure_mpa: float
    hoop_stress_mpa: float
    tensile_threshold_mpa: float
    stress_ratio: float
    state: HazardState


class SaltCycleStatsOut(BaseModel):
    crystallization_events: int
    cycles_per_year: float
    supersaturation_estimate: float
    mean_event_temperature_c: float


class SaltAssessmentOut(BaseModel):
    salt_id: str
    name: str
    formula: str
    equilibrium_rh_pct: float
    stats: SaltCycleStatsOut
    pressure_mpa: float
    stress_ratio: float
    score: float
    state: HazardState


class RustAssessmentOut(BaseModel):
    detail: RustJackingOut
    i_corr_source: Literal["measured", "estimated_from_time_of_wetness"]
    time_of_wetness: float
    score: float
    state: HazardState


class FreezeThawAssessmentOut(BaseModel):
    total_cycles: int
    wet_cycles: int
    wet_cycles_per_year: float
    mean_freeze_minimum_c: float
    score: float
    state: HazardState


class HazardMatrixRowOut(BaseModel):
    hazard: Literal["salt_crystallization", "rust_jacking", "freeze_thaw"]
    score: float = Field(..., ge=0, le=10)
    state: HazardState
    governing_stress_mpa: float | None
    tensile_threshold_mpa: float


class HazardMetaOut(BaseModel):
    site_id: str
    samples: int
    duration_years: float
    mean_rh_pct: float
    mean_temperature_c: float
    time_of_wetness: float
    governing_salt: str
    processing_ms: float


class HazardEvaluationResponse(BaseModel):
    meta: HazardMetaOut
    salt_assessments: list[SaltAssessmentOut]
    rust_jacking: RustAssessmentOut
    freeze_thaw: FreezeThawAssessmentOut
    hazard_matrix: list[HazardMatrixRowOut]
