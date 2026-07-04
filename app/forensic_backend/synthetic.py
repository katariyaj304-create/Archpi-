"""Synthetic diffractogram generator.

Builds a physically plausible mortar/concrete pattern from the same COD
reference matrix the matcher uses: Gaussian reflections whose FWHM follows
the Scherrer relation for a requested crystallite size, an exponentially
decaying amorphous background, and Poisson counting noise. Used by the
demo endpoint and the test suite.
"""

from __future__ import annotations

import math

import numpy as np

from .phases import load_reference_db
from .quant import CU_K_ALPHA_NM, SCHERRER_K


def generate_pattern(
    phase_weights: dict[str, float],
    *,
    two_theta_min: float = 5.0,
    two_theta_max: float = 70.0,
    step_deg: float = 0.02,
    crystallite_nm: float = 40.0,
    peak_scale: float = 4000.0,
    background_scale: float = 300.0,
    seed: int | None = 42,
) -> tuple[list[float], list[float]]:
    db = load_reference_db()
    by_id = {p["id"]: p for p in db["phases"]}
    unknown = set(phase_weights) - set(by_id)
    if unknown:
        raise ValueError(f"unknown phase ids: {sorted(unknown)}")

    x = np.arange(two_theta_min, two_theta_max + step_deg / 2, step_deg)
    y = np.zeros_like(x)

    for phase_id, weight in phase_weights.items():
        if weight <= 0:
            continue
        phase = by_id[phase_id]
        for line in phase["lines"]:
            center = line["two_theta"]
            if not (two_theta_min < center < two_theta_max):
                continue
            theta_rad = math.radians(center / 2.0)
            beta_rad = SCHERRER_K * CU_K_ALPHA_NM / (crystallite_nm * math.cos(theta_rad))
            fwhm_deg = math.degrees(beta_rad)
            sigma = fwhm_deg / (2.0 * math.sqrt(2.0 * math.log(2.0)))
            amp = peak_scale * weight * line["rel_intensity"] / 100.0
            y += amp * np.exp(-((x - center) ** 2) / (2.0 * sigma**2))

    # Amorphous hump + air-scatter decay typical of historic binders
    y += background_scale * (1.0 + 2.0 * np.exp(-x / 15.0))

    if seed is not None:
        rng = np.random.default_rng(seed)
        y = rng.poisson(np.clip(y, 0, None)).astype(np.float64)

    return x.tolist(), y.tolist()


def generate_climate(
    *,
    days: int = 365,
    mean_rh_pct: float = 74.0,
    rh_annual_amplitude: float = 10.0,
    rh_daily_amplitude: float = 9.0,
    mean_temperature_c: float = 11.0,
    temp_annual_amplitude: float = 10.0,
    temp_daily_amplitude: float = 5.0,
    seed: int | None = 42,
) -> tuple[list[float], list[float]]:
    """Synthetic hourly microclimate series (RH %, temperature C).

    Annual + diurnal sinusoids with Gaussian sensor noise: RH peaks in
    winter nights and dips on summer afternoons (anti-phase with
    temperature), which is what drives salt and freeze-thaw cycling in a
    real facade. Deterministic for a fixed seed.
    """
    hours = np.arange(days * 24, dtype=np.float64)
    annual = 2.0 * np.pi * hours / (365.0 * 24.0)
    daily = 2.0 * np.pi * hours / 24.0

    rng = np.random.default_rng(seed)
    temp = (
        mean_temperature_c
        - temp_annual_amplitude * np.cos(annual)
        + temp_daily_amplitude * np.sin(daily - np.pi / 2.0)
        + rng.normal(0.0, 0.8, hours.size)
    )
    rh = (
        mean_rh_pct
        + rh_annual_amplitude * np.cos(annual)
        - rh_daily_amplitude * np.sin(daily - np.pi / 2.0)
        + rng.normal(0.0, 2.5, hours.size)
    )
    rh = np.clip(rh, 5.0, 100.0)
    temp = np.clip(temp, -50.0, 55.0)
    return rh.round(2).tolist(), temp.round(2).tolist()
