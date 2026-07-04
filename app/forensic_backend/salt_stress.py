"""Salt crystallization stress — Correns crystallization pressure model.

The pressure a growing crystal exerts against a confining pore wall follows
the Correns (1949) thermodynamic relation:

    P = (R * T / V_m) * ln(S)

    P    crystallization pressure          [Pa]
    R    universal gas constant            [J / (mol K)]
    T    absolute temperature              [K]
    V_m  molar volume of the solid salt    [m^3 / mol]
    S    supersaturation ratio C/C_sat     [dimensionless, >= 1]

This is the first-order isotropic form; hydrated-salt corrections (Steiger
2005) are absorbed into the per-salt typical supersaturation cap used by the
scoring engine. S <= 1 means the solution is undersaturated: no crystal
growth, zero pressure.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .hazard_types import HazardState

R_GAS_J_MOL_K = 8.314462618
ABSOLUTE_ZERO_C = -273.15


@dataclass(frozen=True)
class SaltProperties:
    """Thermophysical constants for one building salt.

    molar_volume_m3_mol : molar volume of the crystalline solid [m^3/mol]
    equilibrium_rh_pct  : deliquescence / equilibrium relative humidity at
                          ~20 C [%]; below it the salt crystallizes from
                          solution, above it crystals deliquesce
    typical_max_supersaturation : cap on the supersaturation ratio reachable
                          by evaporative drying in masonry pores (Steiger)
    """

    id: str
    name: str
    formula: str
    molar_volume_m3_mol: float
    equilibrium_rh_pct: float
    typical_max_supersaturation: float


SALT_REGISTRY: dict[str, SaltProperties] = {
    "halite": SaltProperties(
        id="halite", name="Halite", formula="NaCl",
        molar_volume_m3_mol=2.702e-5, equilibrium_rh_pct=75.3,
        typical_max_supersaturation=1.6,
    ),
    "mirabilite": SaltProperties(
        id="mirabilite", name="Mirabilite", formula="Na2SO4.10H2O",
        molar_volume_m3_mol=2.198e-4, equilibrium_rh_pct=95.6,
        typical_max_supersaturation=3.0,
    ),
    "thenardite": SaltProperties(
        id="thenardite", name="Thenardite", formula="Na2SO4",
        molar_volume_m3_mol=5.33e-5, equilibrium_rh_pct=84.4,
        typical_max_supersaturation=3.0,
    ),
}


@dataclass
class CrystallizationResult:
    salt_id: str
    temperature_c: float
    supersaturation: float
    pressure_mpa: float
    tensile_threshold_mpa: float
    stress_ratio: float           # pressure / tensile threshold
    state: HazardState


class CrystallizationStressModel:
    """Correns crystallization pressure for one salt in a porous medium."""

    def __init__(self, salt: SaltProperties) -> None:
        self.salt = salt

    @classmethod
    def for_salt(cls, salt_id: str) -> "CrystallizationStressModel":
        try:
            return cls(SALT_REGISTRY[salt_id])
        except KeyError:
            raise ValueError(
                f"unknown salt '{salt_id}'; known: {sorted(SALT_REGISTRY)}"
            ) from None

    def pressure_mpa(self, temperature_c: float, supersaturation: float) -> float:
        """Crystallization pressure [MPa] at the given state point.

        temperature_c   : ambient temperature [C]; must be above absolute zero
        supersaturation : ratio S = C/C_sat [-]; values <= 1 return 0.0
        """
        if temperature_c <= ABSOLUTE_ZERO_C:
            raise ValueError(f"temperature {temperature_c} C is at/below absolute zero")
        if supersaturation <= 0.0:
            raise ValueError("supersaturation ratio must be positive")
        if supersaturation <= 1.0:
            return 0.0
        t_kelvin = temperature_c - ABSOLUTE_ZERO_C
        pressure_pa = (
            R_GAS_J_MOL_K * t_kelvin / self.salt.molar_volume_m3_mol
            * math.log(supersaturation)
        )
        return pressure_pa / 1e6

    def evaluate(
        self,
        temperature_c: float,
        supersaturation: float,
        tensile_threshold_mpa: float = 3.0,
    ) -> CrystallizationResult:
        """Full assessment against a material tensile threshold [MPa].

        The hazard state is CRITICAL whenever the generated pressure exceeds
        the material's tensile capacity, HIGH above 2/3 of it, MODERATE above
        1/3, NEGLIGIBLE otherwise.
        """
        if tensile_threshold_mpa <= 0.0:
            raise ValueError("tensile threshold must be positive")
        pressure = self.pressure_mpa(temperature_c, supersaturation)
        ratio = pressure / tensile_threshold_mpa
        if ratio > 1.0:
            state = HazardState.CRITICAL
        elif ratio > 2.0 / 3.0:
            state = HazardState.HIGH
        elif ratio > 1.0 / 3.0:
            state = HazardState.MODERATE
        else:
            state = HazardState.NEGLIGIBLE
        return CrystallizationResult(
            salt_id=self.salt.id,
            temperature_c=temperature_c,
            supersaturation=supersaturation,
            pressure_mpa=round(pressure, 3),
            tensile_threshold_mpa=tensile_threshold_mpa,
            stress_ratio=round(ratio, 3),
            state=state,
        )
