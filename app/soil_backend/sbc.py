"""Safe Bearing Capacity engineering model (Task 3).

Estimates shear-strength parameters from SoilGrids texture data, then applies
Terzaghi's bearing capacity equation for a shallow continuous (strip) footing:

    q_u = c*Nc + gamma*D*Nq + 0.5*gamma*B*Ngamma

with B = 1 m, D = 1 m and a Factor of Safety of 3.

The texture -> strength correlations are deliberately simple, documented
estimations (this is a diagnostic screening tool, not a substitute for a
site-specific geotechnical investigation):

  * internal friction angle  phi = 18 deg + 0.18 * sand_pct   (18..36 deg)
  * cohesion                 c   = 1.2 kPa * clay_pct         (0..~60 kPa)
  * unit weight              gamma = bulk_density [g/cm3] * 9.81 kN/m3
"""

from __future__ import annotations

import math
from typing import Any

GRAVITY_KN_PER_G_CM3 = 9.81   # 1 g/cm3 == 9.81 kN/m3
FOOTING_WIDTH_B_M = 1.0
FOOTING_DEPTH_D_M = 1.0
FACTOR_OF_SAFETY = 3.0


def estimate_strength_parameters(clay_pct: float, sand_pct: float) -> tuple[float, float]:
    """Return (phi_degrees, cohesion_kpa) estimated from texture fractions."""
    clay_pct = max(0.0, min(100.0, clay_pct))
    sand_pct = max(0.0, min(100.0, sand_pct))
    phi_deg = 18.0 + 0.18 * sand_pct          # sandier soil -> higher friction angle
    cohesion_kpa = 1.2 * clay_pct             # clayier soil -> higher cohesion
    return round(phi_deg, 2), round(cohesion_kpa, 2)


def terzaghi_factors(phi_deg: float) -> dict[str, float]:
    """Terzaghi bearing capacity factors Nc, Nq, Ngamma for a given phi."""
    phi_rad = math.radians(phi_deg)
    if phi_deg < 0.5:  # undrained / phi ~ 0 case
        return {"Nc": 5.7, "Nq": 1.0, "Ngamma": 0.0}
    nq = math.exp(math.pi * math.tan(phi_rad)) * math.tan(math.radians(45.0) + phi_rad / 2.0) ** 2
    nc = (nq - 1.0) / math.tan(phi_rad)
    ngamma = 1.8 * (nq - 1.0) * math.tan(phi_rad)  # Terzaghi approximation
    return {"Nc": round(nc, 2), "Nq": round(nq, 2), "Ngamma": round(ngamma, 2)}


def calculate_sbc(clay_pct: float, sand_pct: float, bulk_density: float) -> dict[str, Any]:
    """Estimate Safe Bearing Capacity (kN/m2) for a strip footing.

    bulk_density is in g/cm3 (as delivered by SoilGrids `bdod`).
    Returns the allowable capacity plus every intermediate quantity so the
    frontend terminal can display the full derivation.
    """
    phi_deg, cohesion_kpa = estimate_strength_parameters(clay_pct, sand_pct)
    gamma = bulk_density * GRAVITY_KN_PER_G_CM3
    factors = terzaghi_factors(phi_deg)

    q_ultimate = (
        cohesion_kpa * factors["Nc"]
        + gamma * FOOTING_DEPTH_D_M * factors["Nq"]
        + 0.5 * gamma * FOOTING_WIDTH_B_M * factors["Ngamma"]
    )
    q_allowable = q_ultimate / FACTOR_OF_SAFETY

    return {
        "safe_bearing_capacity_kn_m2": round(q_allowable, 1),
        "ultimate_bearing_capacity_kn_m2": round(q_ultimate, 1),
        "factor_of_safety": FACTOR_OF_SAFETY,
        "parameters": {
            "friction_angle_deg": phi_deg,
            "cohesion_kpa": cohesion_kpa,
            "unit_weight_kn_m3": round(gamma, 2),
            "bearing_factors": factors,
        },
        "assumptions": {
            "footing_type": "shallow continuous (strip)",
            "footing_width_b_m": FOOTING_WIDTH_B_M,
            "footing_depth_d_m": FOOTING_DEPTH_D_M,
            "method": "Terzaghi general shear",
            "note": "Screening estimate from remote-sensed texture; verify with in-situ SPT/CPT.",
        },
    }


def classify_texture(clay_pct: float, sand_pct: float, silt_pct: float) -> str:
    """Coarse USDA-style texture class for the telemetry panel."""
    if clay_pct >= 40:
        return "CLAY (CH/CL)"
    if sand_pct >= 70:
        return "SAND (SP/SW)"
    if silt_pct >= 60:
        return "SILT (ML)"
    if clay_pct >= 27 and sand_pct >= 20:
        return "CLAY LOAM"
    if sand_pct >= 45:
        return "SANDY LOAM"
    return "LOAM (MIXED)"


def liquefaction_index(sand_pct: float, seismic_risk_score: float) -> dict[str, Any]:
    """Susceptibility heuristic: loose saturated sands + seismicity drive risk.

    index = seismic_risk * sand susceptibility, mapped to a 0..1 scale.
    """
    susceptibility = max(0.0, min(1.0, (sand_pct - 20.0) / 60.0))  # <20% sand ~ none, >80% ~ full
    index = round(seismic_risk_score * susceptibility, 2)
    if index >= 0.6:
        label = "CRITICAL"
    elif index >= 0.4:
        label = "HIGH"
    elif index >= 0.2:
        label = "MODERATE"
    else:
        label = "LOW"
    return {"index": index, "label": label}
