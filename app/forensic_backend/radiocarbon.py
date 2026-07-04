"""Radiocarbon calibration — conventional 14C age to calendar date ranges.

A laboratory reports a *conventional radiocarbon age* in years BP (before
1950) with a 1-sigma measurement error. Because atmospheric 14C has varied,
that age must be calibrated against a curve mapping calendar year -> 14C age
before it means a calendar date. This module implements the standard
probabilistic (Bayesian) calibration used by OxCal/CALIB:

    for each calendar year t on the curve (mean mu(t), curve error s(t)):
        P(t) proportional to exp( -(mu(t) - age)^2 / (2 * (sigma^2 + s(t)^2)) )
                            / sqrt(sigma^2 + s(t)^2)

The posterior P(t) is normalised over the modelled span. Highest-Posterior-
Density (HPD) regions at 68.2 % (1-sigma) and 95.4 % (2-sigma) are extracted
as the shortest set of calendar years holding that mass — these are genuinely
multi-modal where the curve wiggles, so each confidence level returns a list
of disjoint year ranges. The median and the curve intercepts are reported too.

The bundled calibration curve is a compact, smooth IntCal-style approximation
over 700 BC - AD 1950 (documented anchor points, linearly interpolated). It
is adequate for demonstrating and validating the calibration mathematics; it
is NOT IntCal20 and should not be used for publishable dates.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from .stratigraphy import PalimpsestError

ONE_SIGMA = 0.682
TWO_SIGMA = 0.954
CURVE_START_AD = -700
CURVE_END_AD = 1950


class RadiocarbonError(PalimpsestError):
    """Base class for radiocarbon-calibration errors."""


class CalibrationOutOfRangeError(RadiocarbonError):
    """The measured age falls outside the bundled calibration curve."""


# Anchor points (calendar year AD : conventional 14C age BP) of a smooth
# IntCal-style curve. 14C age rises going back in time; the late-Holocene
# medieval window is anchored to reproduce realistic wiggles. Curve sigma is
# a typical per-knot IntCal uncertainty.
_ANCHORS_AD_BP: tuple[tuple[int, float], ...] = (
    (1950, 0), (1900, 60), (1850, 110), (1800, 150), (1700, 210),
    (1600, 330), (1500, 400), (1450, 480), (1400, 560), (1350, 630),
    (1300, 700), (1270, 745), (1250, 770), (1220, 815), (1200, 840),
    (1150, 900), (1100, 930), (1050, 990), (1000, 1050), (900, 1130),
    (800, 1205), (700, 1275), (600, 1400), (500, 1500), (400, 1600),
    (300, 1710), (200, 1800), (100, 1900), (1, 1990), (-100, 2100),
    (-200, 2200), (-300, 2260), (-400, 2350), (-500, 2450), (-600, 2550),
    (-700, 2650),
)
CURVE_SIGMA_BP = 14.0


@dataclass(frozen=True)
class CalibrationCurve:
    """Calendar year -> 14C age lookup at 1-year resolution."""

    cal_years: np.ndarray     # ascending calendar years AD (negative = BC)
    c14_bp: np.ndarray        # conventional 14C age at each year
    sigma_bp: np.ndarray      # curve uncertainty at each year

    @property
    def min_bp(self) -> float:
        return float(self.c14_bp.min())

    @property
    def max_bp(self) -> float:
        return float(self.c14_bp.max())


@lru_cache(maxsize=1)
def calibration_curve() -> CalibrationCurve:
    """The bundled IntCal-style curve, interpolated to 1-year steps."""
    anchors = sorted(_ANCHORS_AD_BP)
    ax = np.array([a for a, _ in anchors], dtype=float)
    ay = np.array([b for _, b in anchors], dtype=float)
    years = np.arange(CURVE_START_AD, CURVE_END_AD + 1)
    c14 = np.interp(years, ax, ay)
    return CalibrationCurve(
        cal_years=years,
        c14_bp=c14,
        sigma_bp=np.full(years.size, CURVE_SIGMA_BP),
    )


@dataclass
class YearRange:
    """One contiguous calendar-year interval with its probability mass."""

    start_year: int
    end_year: int
    probability: float        # posterior mass inside this range


@dataclass
class CalibrationResult:
    radiocarbon_age_bp: float
    uncertainty_bp: float
    hpd_68: list[YearRange]   # 1-sigma disjoint ranges
    hpd_95: list[YearRange]   # 2-sigma disjoint ranges
    median_year: int
    intercepts: list[int]     # calendar years where the curve crosses the age
    cal_years: list[int]      # posterior support x-axis (for plotting)
    density: list[float]      # normalised posterior at each cal year


def _hpd_ranges(
    years: np.ndarray, density: np.ndarray, mass: float
) -> list[YearRange]:
    """Shortest set of highest-density years holding `mass`, merged to ranges."""
    order = np.argsort(density)[::-1]
    cumulative = np.cumsum(density[order])
    cutoff = int(np.searchsorted(cumulative, mass)) + 1
    keep = np.sort(order[:cutoff])

    ranges: list[YearRange] = []
    run_start = keep[0]
    prev = keep[0]
    for idx in keep[1:]:
        if idx == prev + 1:
            prev = idx
            continue
        ranges.append(_range(years, density, run_start, prev))
        run_start = prev = idx
    ranges.append(_range(years, density, run_start, prev))
    ranges.sort(key=lambda r: r.start_year)
    return ranges


def _range(years, density, lo, hi) -> YearRange:
    return YearRange(
        start_year=int(years[lo]),
        end_year=int(years[hi]),
        probability=round(float(density[lo:hi + 1].sum()), 4),
    )


class RadiocarbonCalibrator:
    """Probabilistic calibration of a conventional 14C age."""

    def __init__(self, curve: CalibrationCurve | None = None) -> None:
        self.curve = curve if curve is not None else calibration_curve()

    def calibrate(
        self, radiocarbon_age_bp: float, uncertainty_bp: float
    ) -> CalibrationResult:
        """Calibrate one age (BP +/- 1 sigma) to calendar-year HPD ranges."""
        if uncertainty_bp <= 0:
            raise ValueError("measurement uncertainty must be positive")
        curve = self.curve
        # Reject ages the curve cannot reach within ~4 combined sigma.
        margin = 4.0 * (uncertainty_bp + CURVE_SIGMA_BP)
        if (radiocarbon_age_bp < curve.min_bp - margin or
                radiocarbon_age_bp > curve.max_bp + margin):
            raise CalibrationOutOfRangeError(
                f"{radiocarbon_age_bp:.0f} BP is outside the calibration curve "
                f"({curve.min_bp:.0f}-{curve.max_bp:.0f} BP; "
                f"{CURVE_START_AD} to {CURVE_END_AD} cal AD)",
                detail={
                    "age_bp": radiocarbon_age_bp,
                    "curve_bp_range": [curve.min_bp, curve.max_bp],
                },
            )

        combined_var = uncertainty_bp ** 2 + curve.sigma_bp ** 2
        resid = curve.c14_bp - radiocarbon_age_bp
        density = np.exp(-0.5 * resid ** 2 / combined_var) / np.sqrt(combined_var)
        total = density.sum()
        if total <= 0:
            raise CalibrationOutOfRangeError(
                f"{radiocarbon_age_bp:.0f} BP yields no calendar support",
                detail={"age_bp": radiocarbon_age_bp},
            )
        density = density / total

        years = curve.cal_years
        cdf = np.cumsum(density)
        median_year = int(years[int(np.searchsorted(cdf, 0.5))])

        # Intercepts: where (curve - age) crosses zero, i.e. the curve mean
        # meets the measured age. The "<= 0" flip catches exact-zero knots too.
        crossings = np.flatnonzero((resid[:-1] <= 0) != (resid[1:] <= 0))
        intercepts = [int(years[i]) for i in crossings]

        return CalibrationResult(
            radiocarbon_age_bp=radiocarbon_age_bp,
            uncertainty_bp=uncertainty_bp,
            hpd_68=_hpd_ranges(years, density, ONE_SIGMA),
            hpd_95=_hpd_ranges(years, density, TWO_SIGMA),
            median_year=median_year,
            intercepts=intercepts,
            cal_years=years.tolist(),
            density=[round(float(d), 8) for d in density],
        )
