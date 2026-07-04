"""Quantitative XRD calculations: Scherrer crystallite size and RIR weights.

Scherrer:  tau = K * lambda / (beta * cos(theta))
  * K = 0.94 (spherical crystallites, cubic-symmetry shape factor)
  * lambda = 0.15418 nm (Cu K-alpha)
  * beta = sample FWHM in radians of 2-theta, after quadrature subtraction
    of the instrumental broadening
  * theta = Bragg angle (half the fitted 2-theta), in radians

RIR (Reference Intensity Ratio / matrix-flushing):
  W_i = (I_i / RIR_i) / sum_j (I_j / RIR_j)
using the integrated area of each matched phase's strongest observed line.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .conditioning import ConditionedPattern
from .peaks import FittedPeak
from .phases import PhaseMatch

SCHERRER_K = 0.94
CU_K_ALPHA_NM = 0.15418


@dataclass
class CrystalliteEstimate:
    two_theta_deg: float
    theta_deg: float
    beta_obs_deg: float       # fitted FWHM before instrumental correction
    beta_sample_deg: float    # after quadrature subtraction
    size_nm: float


@dataclass
class PhaseWeight:
    phase_id: str
    name: str
    formula: str
    rir: float
    integrated_intensity: float
    weight_pct: float


def scherrer_size_nm(beta_deg: float, two_theta_deg: float) -> float:
    beta_rad = math.radians(beta_deg)
    theta_rad = math.radians(two_theta_deg / 2.0)
    return SCHERRER_K * CU_K_ALPHA_NM / (beta_rad * math.cos(theta_rad))


def crystallite_sizes(
    fitted: list[FittedPeak],
    *,
    instrumental_fwhm_deg: float = 0.05,
    min_r_squared: float = 0.8,
) -> list[CrystalliteEstimate]:
    estimates = []
    for peak in fitted:
        if peak.r_squared < min_r_squared:
            continue
        beta_obs = peak.fwhm_deg
        # Quadrature subtraction of instrumental broadening; skip peaks
        # narrower than the instrument profile — Scherrer is meaningless there.
        squared = beta_obs**2 - instrumental_fwhm_deg**2
        if squared <= 0:
            continue
        beta_sample = math.sqrt(squared)
        estimates.append(CrystalliteEstimate(
            two_theta_deg=peak.two_theta,
            theta_deg=peak.two_theta / 2.0,
            beta_obs_deg=beta_obs,
            beta_sample_deg=beta_sample,
            size_nm=scherrer_size_nm(beta_sample, peak.two_theta),
        ))
    return estimates


def average_crystallite_size_nm(estimates: list[CrystalliteEstimate]) -> float | None:
    if not estimates:
        return None
    return sum(e.size_nm for e in estimates) / len(estimates)


def rir_weight_fractions(
    matches: list[PhaseMatch],
    *,
    min_confidence_pct: float = 25.0,
) -> list[PhaseWeight]:
    accepted = [
        m for m in matches
        if m.confidence_pct >= min_confidence_pct and m.strongest_area > 0
    ]
    denominator = sum(m.strongest_area / m.rir for m in accepted)
    if denominator <= 0:
        return []
    weights = []
    for m in accepted:
        scaled = m.strongest_area / m.rir
        weights.append(PhaseWeight(
            phase_id=m.phase_id,
            name=m.name,
            formula=m.formula,
            rir=m.rir,
            integrated_intensity=round(m.strongest_area, 2),
            weight_pct=round(100.0 * scaled / denominator, 1),
        ))
    weights.sort(key=lambda w: -w.weight_pct)
    return weights


def silica_ratio_pct(weights: list[PhaseWeight], matches: list[PhaseMatch]) -> float:
    silica_ids = {m.phase_id for m in matches if m.is_silica}
    return round(sum(w.weight_pct for w in weights if w.phase_id in silica_ids), 1)


def crystallinity_pct(pattern: ConditionedPattern) -> float:
    """Degree of crystallinity: sharp-peak area over sharp + diffuse area.

    The ALS baseline carries both the flat air-scatter floor and the broad
    amorphous hump; subtracting the floor (its minimum) leaves the hump as
    the diffuse/amorphous contribution.
    """
    crystalline = float(np.trapezoid(pattern.processed, pattern.two_theta))
    hump = pattern.baseline - pattern.baseline.min()
    diffuse = float(np.trapezoid(hump, pattern.two_theta))
    total = crystalline + diffuse
    if total <= 0:
        return 0.0
    return round(100.0 * crystalline / total, 1)


def calcium_content_pct(weights: list[PhaseWeight], matches: list[PhaseMatch]) -> float:
    """Elemental Ca in the crystalline fraction, from each phase's
    stoichiometric Ca mass fraction (calcite 40.0%, gypsum 23.3%)."""
    ca_by_id = {m.phase_id: m.ca_mass_fraction for m in matches}
    return round(
        sum(w.weight_pct * ca_by_id.get(w.phase_id, 0.0) for w in weights), 1
    )
