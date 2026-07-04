"""Hazard matrix engine — deterministic 0.0-10.0 degradation risk scoring.

Every score is a documented, reproducible function of physical stress
(pressure vs. the material's tensile capacity) and exposure statistics from
the microclimate series — no subjective weighting.

Scoring scheme (all factors clamped to [0, 1] before combining):

* Salt crystallization
    severity  = P_correns / tensile_strength
    frequency = cycles_per_year / 30        (30 cyc/yr ~ fully cyclic wall)
    uptake    = porosity / 25               (25 % ~ highly absorbent stone)
    score     = 10 * severity * sqrt(frequency) * sqrt(uptake)
  The governing salt is the registry entry with the highest score.

* Rust jacking
    score = 10 * (hoop_stress / tensile_strength)   capped at 10
  When no measured corrosion current is supplied, it is estimated from the
  ISO 9223 time-of-wetness fraction:  i_corr = 0.1 + 2.4 * TOW  [uA/cm^2]
  (0.1 ~ passive embedded iron, 2.5 ~ permanently wet active corrosion).

* Freeze-thaw
    intensity  = wet_cycles_per_year / 40   (40 cyc/yr ~ severe climate)
    saturation = porosity / 25
    severity   = |mean freeze minimum| / 10 (-10 C ~ full ice-pressure risk)
    score      = 10 * sqrt(intensity) * sqrt(saturation) * severity

States follow the score bands (>=7.5 CRITICAL, >=5 HIGH, >=2.5 MODERATE),
except that a computed stress exceeding the material tensile threshold
forces CRITICAL for the salt and rust hazards regardless of score.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .hazard_types import HazardState, state_from_score
from .microclimate import FreezeThawStats, MicroclimateSeries, SaltCycleStats
from .rust_jacking import RustJackingModel, RustJackingResult
from .salt_stress import SALT_REGISTRY, CrystallizationStressModel

SALT_CYCLES_PER_YEAR_REF = 30.0
POROSITY_REF_PCT = 25.0
FREEZE_CYCLES_PER_YEAR_REF = 40.0
FREEZE_SEVERITY_REF_C = 10.0
I_CORR_PASSIVE_UA_CM2 = 0.1
I_CORR_TOW_SLOPE_UA_CM2 = 2.4


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


@dataclass
class MaterialProfile:
    """Mechanical/petrophysical profile of the masonry under assessment.

    porosity_pct         : open porosity [%]
    tensile_strength_mpa : tensile capacity [MPa] (aged limestone ~3.0)
    """

    porosity_pct: float
    tensile_strength_mpa: float

    def __post_init__(self) -> None:
        if not 0.0 < self.porosity_pct <= 60.0:
            raise ValueError("porosity must lie within (0, 60] %")
        if not 0.0 < self.tensile_strength_mpa <= 50.0:
            raise ValueError("tensile strength must lie within (0, 50] MPa")


@dataclass
class SaltHazardAssessment:
    stats: SaltCycleStats
    pressure_mpa: float
    stress_ratio: float
    score: float
    state: HazardState


@dataclass
class FreezeThawAssessment:
    stats: FreezeThawStats
    score: float
    state: HazardState


@dataclass
class RustHazardAssessment:
    result: RustJackingResult
    i_corr_source: str  # "measured" | "estimated_from_time_of_wetness"
    time_of_wetness: float
    score: float
    state: HazardState


@dataclass
class HazardMatrixRow:
    hazard: str
    score: float
    state: HazardState
    governing_stress_mpa: float | None  # None where the model is cycle-based
    tensile_threshold_mpa: float


class HazardMatrixEngine:
    """Combines the physical stress models into one deterministic matrix."""

    def __init__(self, material: MaterialProfile, rust_model: RustJackingModel | None = None) -> None:
        self.material = material
        self.rust_model = rust_model or RustJackingModel()

    # ── Salt crystallization ────────────────────────────────────────────

    def assess_salt(self, series: MicroclimateSeries, salt_id: str) -> SaltHazardAssessment:
        salt = SALT_REGISTRY[salt_id]
        stats = series.salt_cycles(salt)
        model = CrystallizationStressModel(salt)
        pressure = model.pressure_mpa(
            stats.mean_event_temperature_c, stats.supersaturation_estimate
        )
        ratio = pressure / self.material.tensile_strength_mpa

        severity = _clamp01(ratio)
        frequency = _clamp01(stats.cycles_per_year / SALT_CYCLES_PER_YEAR_REF)
        uptake = _clamp01(self.material.porosity_pct / POROSITY_REF_PCT)
        score = round(10.0 * severity * math.sqrt(frequency) * math.sqrt(uptake), 1)

        state = HazardState.CRITICAL if ratio > 1.0 and stats.crystallization_events > 0 \
            else state_from_score(score)
        return SaltHazardAssessment(
            stats=stats,
            pressure_mpa=round(pressure, 3),
            stress_ratio=round(ratio, 3),
            score=score,
            state=state,
        )

    def assess_all_salts(self, series: MicroclimateSeries) -> dict[str, SaltHazardAssessment]:
        return {salt_id: self.assess_salt(series, salt_id) for salt_id in SALT_REGISTRY}

    # ── Rust jacking ────────────────────────────────────────────────────

    def assess_rust(
        self,
        series: MicroclimateSeries,
        diameter_mm: float,
        exposure_years: float,
        i_corr_ua_cm2: float | None = None,
        oxide: str = "Fe(OH)3",
        cover_mm: float = 50.0,
    ) -> RustHazardAssessment:
        tow = series.time_of_wetness()
        if i_corr_ua_cm2 is None:
            i_corr = I_CORR_PASSIVE_UA_CM2 + I_CORR_TOW_SLOPE_UA_CM2 * tow
            source = "estimated_from_time_of_wetness"
        else:
            i_corr = i_corr_ua_cm2
            source = "measured"

        result = self.rust_model.evaluate(
            diameter_mm=diameter_mm,
            i_corr_ua_cm2=round(i_corr, 4),
            exposure_years=exposure_years,
            oxide=oxide,
            cover_mm=cover_mm,
            tensile_threshold_mpa=self.material.tensile_strength_mpa,
        )
        score = round(10.0 * _clamp01(result.stress_ratio), 1)
        state = HazardState.CRITICAL if result.stress_ratio > 1.0 else state_from_score(score)
        return RustHazardAssessment(
            result=result,
            i_corr_source=source,
            time_of_wetness=round(tow, 3),
            score=score,
            state=state,
        )

    # ── Freeze-thaw ─────────────────────────────────────────────────────

    def assess_freeze_thaw(self, series: MicroclimateSeries) -> FreezeThawAssessment:
        stats = series.freeze_thaw_cycles()
        intensity = _clamp01(stats.wet_cycles_per_year / FREEZE_CYCLES_PER_YEAR_REF)
        saturation = _clamp01(self.material.porosity_pct / POROSITY_REF_PCT)
        severity = _clamp01(abs(min(stats.mean_freeze_minimum_c, 0.0)) / FREEZE_SEVERITY_REF_C)
        score = round(10.0 * math.sqrt(intensity) * math.sqrt(saturation) * severity, 1)
        return FreezeThawAssessment(stats=stats, score=score, state=state_from_score(score))

    # ── Matrix assembly ─────────────────────────────────────────────────

    @staticmethod
    def governing_salt(
        assessments: dict[str, SaltHazardAssessment]
    ) -> tuple[str, SaltHazardAssessment]:
        salt_id = max(assessments, key=lambda k: (assessments[k].score, assessments[k].stress_ratio))
        return salt_id, assessments[salt_id]

    def matrix(
        self,
        salt: SaltHazardAssessment,
        rust: RustHazardAssessment,
        freeze: FreezeThawAssessment,
    ) -> list[HazardMatrixRow]:
        tensile = self.material.tensile_strength_mpa
        return [
            HazardMatrixRow(
                hazard="salt_crystallization",
                score=salt.score,
                state=salt.state,
                governing_stress_mpa=salt.pressure_mpa,
                tensile_threshold_mpa=tensile,
            ),
            HazardMatrixRow(
                hazard="rust_jacking",
                score=rust.score,
                state=rust.state,
                governing_stress_mpa=rust.result.hoop_stress_mpa,
                tensile_threshold_mpa=tensile,
            ),
            HazardMatrixRow(
                hazard="freeze_thaw",
                score=freeze.score,
                state=freeze.state,
                governing_stress_mpa=None,
                tensile_threshold_mpa=tensile,
            ),
        ]
