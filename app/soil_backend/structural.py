"""Structural engineering models for the Environmental Dynamics feature (Task 2).

Converts raw meteorology into the metrics a structural engineer acts on:
dynamic wind pressure, concrete carbonation risk, 24-hour thermal loading,
wet-bulb exposure and monsoon-cycle vulnerability.
"""

from __future__ import annotations

import math
from typing import Any, Optional, Sequence

AIR_DENSITY_KG_M3 = 1.225          # ISA sea-level standard
CONCRETE_ALPHA_PER_C = 12e-6       # coefficient of thermal expansion, /degC
FRESH_CONCRETE_PH = 12.6
FULLY_CARBONATED_PH = 8.3


def calculate_wind_shear(wind_speed_kmh: float) -> dict[str, Any]:
    """Dynamic wind pressure q = 1/2 * rho * v^2, reported in kN/m2.

    This is the stagnation pressure acting on a windward facade — the input
    to IS 875-3 / ASCE 7 design wind load factors.
    """
    v_ms = wind_speed_kmh / 3.6
    q_kn_m2 = 0.5 * AIR_DENSITY_KG_M3 * v_ms ** 2 / 1000.0

    if q_kn_m2 < 0.05:
        exposure = "NEGLIGIBLE"
    elif q_kn_m2 < 0.25:
        exposure = "SERVICE LEVEL"
    elif q_kn_m2 < 0.60:
        exposure = "ELEVATED"
    else:
        exposure = "DESIGN-GOVERNING"

    return {
        "wind_speed_kmh": round(wind_speed_kmh, 1),
        "wind_speed_ms": round(v_ms, 2),
        "dynamic_pressure_kn_m2": round(q_kn_m2, 4),
        "exposure_class": exposure,
        "assumptions": {"air_density_kg_m3": AIR_DENSITY_KG_M3, "formula": "q = 0.5 * rho * v^2"},
    }


def evaluate_carbonation_risk(temp_c: float, humidity_pct: float) -> dict[str, Any]:
    """Concrete carbonation risk matrix.

    CO2 ingress peaks at 50-70% RH (pores moist enough to react, dry enough
    to stay diffusion-open) and accelerates with temperature. Below ~40% RH
    the reaction starves for water; above ~85% saturated pores block CO2
    diffusion.
    """
    in_band = 50.0 <= humidity_pct <= 70.0
    near_band = 40.0 <= humidity_pct < 50.0 or 70.0 < humidity_pct <= 80.0

    if in_band and temp_c > 32.0:
        label, multiplier = "Critical - Accelerated Leaching", 2.6
    elif in_band and temp_c > 25.0:
        label, multiplier = "Critical - Accelerated Leaching", 2.2
    elif in_band:
        label, multiplier = "High", 1.6
    elif near_band:
        label, multiplier = "Moderate", 1.2
    elif humidity_pct > 85.0:
        label, multiplier = "Low - Pores Saturated (CO2 Blocked)", 0.5
    else:
        label, multiplier = "Low - Insufficient Pore Moisture", 0.6

    # Indicative pH of the carbonation front for the dashboard readout:
    # fresh cover ~12.6, fully carbonated ~8.3
    indicative_ph = round(
        FRESH_CONCRETE_PH - (FRESH_CONCRETE_PH - FULLY_CARBONATED_PH) * min(multiplier / 2.6, 1.0), 2
    )
    steel_passivity = (
        "COMPROMISED" if multiplier >= 2.2 else "AT RISK" if multiplier >= 1.6 else "STABLE"
    )
    calcium_leaching = (
        "SEVERE" if multiplier >= 2.2 else "MODERATE" if multiplier >= 1.2 else "LOW"
    )

    return {
        "risk_label": label,
        "ph_degradation_multiplier": multiplier,
        "indicative_front_ph": indicative_ph,
        "steel_passivity": steel_passivity,
        "calcium_leaching": calcium_leaching,
        "inputs": {"temperature_c": temp_c, "relative_humidity_pct": humidity_pct},
    }


def thermal_loading(hourly_temps_c: Sequence[float]) -> Optional[dict[str, Any]]:
    """24-hour thermal loading cycle and the expansion it drives.

    Expansion is reported per 10 m of unrestrained concrete member:
    dL = alpha * L * dT.
    """
    temps = [t for t in hourly_temps_c if t is not None]
    if not temps:
        return None
    t_min, t_max = min(temps), max(temps)
    swing = t_max - t_min
    expansion_mm_per_10m = CONCRETE_ALPHA_PER_C * 10_000.0 * swing  # 10 m in mm
    return {
        "min_c": round(t_min, 1),
        "max_c": round(t_max, 1),
        "avg_c": round(sum(temps) / len(temps), 1),
        "swing_c": round(swing, 1),
        "expansion_mm_per_10m": round(expansion_mm_per_10m, 2),
        "cycle_class": "SEVERE" if swing >= 15 else "MODERATE" if swing >= 8 else "MILD",
    }


def wet_bulb_c(temp_c: float, humidity_pct: float) -> float:
    """Stull (2011) wet-bulb approximation — human/curing heat stress metric."""
    rh = max(humidity_pct, 0.1)
    tw = (
        temp_c * math.atan(0.151977 * math.sqrt(rh + 8.313659))
        + math.atan(temp_c + rh)
        - math.atan(rh - 1.676331)
        + 0.00391838 * rh ** 1.5 * math.atan(0.023101 * rh)
        - 4.686035
    )
    return round(tw, 1)


def monsoon_outlook(daily_precip_mm: Sequence[float]) -> dict[str, Any]:
    """7-day precipitation load vs structural vulnerability classification."""
    precip = [p for p in daily_precip_mm if p is not None]
    total = sum(precip)
    peak = max(precip) if precip else 0.0

    if total >= 200 or peak >= 100:
        label, note = "SEVERE SURGE", "Flash-flood scour and hydrostatic uplift checks required."
    elif total >= 100 or peak >= 60:
        label, note = "ACTIVE SURGE", "Monitor foundation drainage and slope stability."
    elif total >= 30:
        label, note = "ACTIVE", "Normal monsoon loading; inspect water-side joints."
    else:
        label, note = "QUIESCENT", "No significant hydrological loading forecast."

    return {
        "total_7d_mm": round(total, 1),
        "peak_daily_mm": round(peak, 1),
        "label": label,
        "note": note,
    }
