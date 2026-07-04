"""Hourly microclimate time-series analysis for degradation scoring.

Detects the phase-transition events that drive the physical stress models:

* salt dissolution/crystallization cycles — hysteresis state machine on
  relative humidity around each salt's equilibrium RH;
* wet freeze-thaw cycles — hysteresis state machine on temperature around
  0 C, counting only freezes that start with moisture available;
* time of wetness — fraction of hours with RH >= 80 % and T > 0 C
  (ISO 9223), used to estimate corrosion activity when no measured
  corrosion current is supplied.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .salt_stress import SaltProperties

HOURS_PER_YEAR = 8766.0  # Julian


@dataclass
class SaltCycleStats:
    salt_id: str
    crystallization_events: int
    cycles_per_year: float
    supersaturation_estimate: float  # mean event S, capped per salt [-]
    mean_event_temperature_c: float


@dataclass
class FreezeThawStats:
    total_cycles: int
    wet_cycles: int
    wet_cycles_per_year: float
    mean_freeze_minimum_c: float  # mean of per-cycle minimum temperatures


class MicroclimateSeries:
    """Validated hourly (relative humidity, temperature) series.

    relative_humidity_pct : hourly RH values [%], 0-100
    temperature_c         : hourly ambient temperature [C]
    hours_per_sample      : sampling interval [h] (1.0 for hourly data)
    """

    def __init__(
        self,
        relative_humidity_pct: list[float] | np.ndarray,
        temperature_c: list[float] | np.ndarray,
        hours_per_sample: float = 1.0,
    ) -> None:
        rh = np.asarray(relative_humidity_pct, dtype=np.float64)
        temp = np.asarray(temperature_c, dtype=np.float64)
        if rh.ndim != 1 or temp.ndim != 1:
            raise ValueError("sensor series must be one-dimensional")
        if rh.size != temp.size:
            raise ValueError(
                f"length mismatch: {rh.size} humidity vs {temp.size} temperature samples"
            )
        if rh.size < 48:
            raise ValueError("at least 48 hourly samples are required")
        if not (np.isfinite(rh).all() and np.isfinite(temp).all()):
            raise ValueError("sensor series must contain only finite values")
        if ((rh < 0) | (rh > 100)).any():
            raise ValueError("relative humidity must lie within 0-100 %")
        if ((temp < -60) | (temp > 60)).any():
            raise ValueError("ambient temperature must lie within -60..60 C")
        if hours_per_sample <= 0:
            raise ValueError("sampling interval must be positive")
        self.rh = rh
        self.temp = temp
        self.hours_per_sample = hours_per_sample

    @property
    def duration_years(self) -> float:
        return self.rh.size * self.hours_per_sample / HOURS_PER_YEAR

    @property
    def mean_rh_pct(self) -> float:
        return float(self.rh.mean())

    @property
    def mean_temperature_c(self) -> float:
        return float(self.temp.mean())

    def time_of_wetness(self) -> float:
        """ISO 9223 time-of-wetness fraction: RH >= 80 % while T > 0 C."""
        return float(((self.rh >= 80.0) & (self.temp > 0.0)).mean())

    def salt_cycles(self, salt: SaltProperties, hysteresis_pct: float = 2.0) -> SaltCycleStats:
        """Count dissolution -> crystallization transitions for one salt.

        The salt is 'dissolved' once RH rises above its equilibrium RH plus
        the hysteresis band, and 'crystallized' once RH falls below minus the
        band; each dissolved->crystallized transition is one crystallization
        event. The supersaturation reached in an event is approximated by
        RH_eq / RH_min over the following crystallized spell (first-order
        evaporative-concentration estimate), capped at the salt's typical
        maximum.
        """
        upper = salt.equilibrium_rh_pct + hysteresis_pct
        lower = salt.equilibrium_rh_pct - hysteresis_pct
        state = ""
        events: list[int] = []
        spell_minima: list[float] = []
        event_temps: list[float] = []
        for i, rh in enumerate(self.rh):
            if rh >= upper:
                state = "dissolved"
            elif rh <= lower:
                if state == "dissolved":
                    events.append(i)
                    spell_minima.append(rh)
                    event_temps.append(float(self.temp[i]))
                elif spell_minima:
                    spell_minima[-1] = min(spell_minima[-1], rh)
                state = "crystallized"

        if events:
            s_values = [
                min(salt.equilibrium_rh_pct / max(m, 5.0), salt.typical_max_supersaturation)
                for m in spell_minima
            ]
            s_estimate = float(np.mean(s_values))
            mean_temp = float(np.mean(event_temps))
        else:
            s_estimate = 1.0
            mean_temp = self.mean_temperature_c

        return SaltCycleStats(
            salt_id=salt.id,
            crystallization_events=len(events),
            cycles_per_year=round(len(events) / self.duration_years, 2),
            supersaturation_estimate=round(s_estimate, 3),
            mean_event_temperature_c=round(mean_temp, 2),
        )

    def freeze_thaw_cycles(
        self,
        freeze_below_c: float = -1.0,
        thaw_above_c: float = 1.0,
        wet_rh_pct: float = 75.0,
    ) -> FreezeThawStats:
        """Count freeze->thaw cycles with a hysteresis band around 0 C.

        A cycle is 'wet' — capable of hydraulic/ice-lens damage — when RH at
        freeze onset is at least wet_rh_pct (pore moisture proxy).
        """
        if freeze_below_c >= thaw_above_c:
            raise ValueError("freeze threshold must be below thaw threshold")
        frozen = False
        wet = False
        current_min = 0.0
        total = 0
        wet_total = 0
        minima: list[float] = []
        for rh, t in zip(self.rh, self.temp):
            if not frozen:
                if t <= freeze_below_c:
                    frozen = True
                    wet = rh >= wet_rh_pct
                    current_min = t
            else:
                current_min = min(current_min, float(t))
                if t >= thaw_above_c:
                    frozen = False
                    total += 1
                    wet_total += int(wet)
                    minima.append(current_min)

        return FreezeThawStats(
            total_cycles=total,
            wet_cycles=wet_total,
            wet_cycles_per_year=round(wet_total / self.duration_years, 2),
            mean_freeze_minimum_c=round(float(np.mean(minima)), 2) if minima else 0.0,
        )
