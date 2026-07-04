"""Electrochemical rust jacking — Faraday mass loss + Lamé sleeve stress.

Chain of models:

1. Faraday's law converts a measured corrosion current density into an iron
   section loss depth:

       x = (i_corr * t * M_Fe) / (n * F * rho_Fe)

       x       radial section loss                  [m]
       i_corr  corrosion current density            [A/m^2]
       t       exposure duration                    [s]
       M_Fe    molar mass of iron  = 0.055845       [kg/mol]
       n       electrons per Fe atom oxidised = 2   [-]
       F       Faraday constant    = 96485.33       [C/mol]
       rho_Fe  density of iron     = 7870           [kg/m^3]

   (Numerically: 1 uA/cm^2 ~ 11.6 um/year of uniform penetration.)

2. The consumed iron annulus converts to oxide of larger specific volume
   (Fe3O4 x2.1, Fe(OH)3 x4.2). The unrestrained rust front displacement is
   the growth of the oxide annulus beyond the original bar radius, less a
   porous interfacial zone that absorbs the first microns of product.

3. The masonry sleeve is treated as a thick-walled cylinder (Lamé). The
   interface pressure required to accommodate the residual displacement u at
   the bore r0 of a cylinder with outer radius R is

       P = u * E / ( r0 * ( (R^2 + r0^2) / (R^2 - r0^2) + nu ) )

   and the resulting tensile hoop stress at the bore, which is what actually
   cracks the masonry, is

       sigma_theta = P * (R^2 + r0^2) / (R^2 - r0^2)

   The oxide layer is treated as rigid relative to historic masonry
   (E_rust >> E_masonry applies for confined, dense rust); this yields a
   conservative upper-bound pressure.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .hazard_types import HazardState

FARADAY_C_MOL = 96485.33212
M_FE_KG_MOL = 0.055845
RHO_FE_KG_M3 = 7870.0
N_ELECTRONS = 2
SECONDS_PER_YEAR = 3.15576e7  # Julian year


@dataclass(frozen=True)
class OxideProduct:
    """Corrosion product with its volumetric expansion over parent iron."""

    formula: str
    expansion_ratio: float  # V_oxide / V_Fe consumed [-]


OXIDE_REGISTRY: dict[str, OxideProduct] = {
    "Fe3O4": OxideProduct(formula="Fe3O4", expansion_ratio=2.1),
    "Fe(OH)3": OxideProduct(formula="Fe(OH)3", expansion_ratio=4.2),
}


@dataclass
class RustJackingResult:
    diameter_mm: float
    i_corr_ua_cm2: float
    exposure_years: float
    oxide: str
    expansion_ratio: float
    section_loss_mm: float        # radial depth of iron consumed
    iron_mass_loss_kg_m: float    # per metre of tie length
    free_expansion_mm: float      # unrestrained radial rust-front growth
    effective_expansion_mm: float # after the porous zone is filled
    interface_pressure_mpa: float # radial stress on the masonry bore
    hoop_stress_mpa: float        # tensile stress cracking the sleeve
    tensile_threshold_mpa: float
    stress_ratio: float           # hoop stress / tensile threshold
    state: HazardState


class RustJackingModel:
    """Expansive corrosion stress on a masonry sleeve around an iron tie.

    masonry_e_gpa     : Young's modulus of the surrounding masonry [GPa]
                        (historic brick/limestone typically 5-20 GPa)
    masonry_poisson   : Poisson's ratio of the masonry [-]
    porous_zone_um    : thickness of the interfacial voids that absorb rust
                        before pressure builds [micrometres]
    """

    def __init__(
        self,
        masonry_e_gpa: float = 15.0,
        masonry_poisson: float = 0.2,
        porous_zone_um: float = 12.5,
    ) -> None:
        if masonry_e_gpa <= 0:
            raise ValueError("masonry Young's modulus must be positive")
        if not 0.0 <= masonry_poisson < 0.5:
            raise ValueError("Poisson's ratio must be in [0, 0.5)")
        if porous_zone_um < 0:
            raise ValueError("porous zone thickness cannot be negative")
        self.masonry_e_mpa = masonry_e_gpa * 1000.0
        self.masonry_poisson = masonry_poisson
        self.porous_zone_mm = porous_zone_um / 1000.0

    @staticmethod
    def section_loss_mm(i_corr_ua_cm2: float, exposure_years: float) -> float:
        """Uniform radial iron loss [mm] from Faraday's law.

        i_corr_ua_cm2  : corrosion current density [uA/cm^2]
        exposure_years : duration of active corrosion [years]
        """
        if i_corr_ua_cm2 < 0:
            raise ValueError("corrosion current density cannot be negative")
        if exposure_years < 0:
            raise ValueError("exposure duration cannot be negative")
        i_a_m2 = i_corr_ua_cm2 * 1e-2          # uA/cm^2 -> A/m^2
        t_s = exposure_years * SECONDS_PER_YEAR
        mass_flux_kg_m2 = i_a_m2 * t_s * M_FE_KG_MOL / (N_ELECTRONS * FARADAY_C_MOL)
        return mass_flux_kg_m2 / RHO_FE_KG_M3 * 1000.0  # m -> mm

    def evaluate(
        self,
        diameter_mm: float,
        i_corr_ua_cm2: float,
        exposure_years: float,
        oxide: str = "Fe(OH)3",
        cover_mm: float = 50.0,
        tensile_threshold_mpa: float = 3.0,
    ) -> RustJackingResult:
        """Radial pressure and hoop stress on the masonry sleeve.

        diameter_mm           : original tie diameter [mm]
        i_corr_ua_cm2         : corrosion current density [uA/cm^2]
        exposure_years        : corrosion duration [years]
        oxide                 : governing corrosion product (OXIDE_REGISTRY)
        cover_mm              : masonry sleeve thickness around the tie [mm]
        tensile_threshold_mpa : masonry tensile capacity [MPa]
        """
        if diameter_mm <= 0:
            raise ValueError("tie diameter must be positive")
        if cover_mm <= 0:
            raise ValueError("masonry cover must be positive")
        if tensile_threshold_mpa <= 0:
            raise ValueError("tensile threshold must be positive")
        if oxide not in OXIDE_REGISTRY:
            raise ValueError(f"unknown oxide '{oxide}'; known: {sorted(OXIDE_REGISTRY)}")

        product = OXIDE_REGISTRY[oxide]
        r0 = diameter_mm / 2.0
        loss = min(self.section_loss_mm(i_corr_ua_cm2, exposure_years), r0)
        r_core = r0 - loss

        # Consumed iron annulus area [mm^2/m of length] and its mass
        consumed_area_mm2 = math.pi * (r0**2 - r_core**2)
        iron_mass_kg_m = consumed_area_mm2 * 1e-6 * RHO_FE_KG_M3

        # Rust occupies the consumed annulus scaled by the expansion ratio;
        # the oxide front therefore advances beyond the original surface.
        r_rust = math.sqrt(r_core**2 + product.expansion_ratio * (r0**2 - r_core**2))
        free_expansion = r_rust - r0
        effective = max(0.0, free_expansion - self.porous_zone_mm)

        r_outer = r0 + cover_mm
        geometry = (r_outer**2 + r0**2) / (r_outer**2 - r0**2)
        pressure = (
            effective * self.masonry_e_mpa
            / (r0 * (geometry + self.masonry_poisson))
        )
        hoop = pressure * geometry
        ratio = hoop / tensile_threshold_mpa

        if ratio > 1.0:
            state = HazardState.CRITICAL
        elif ratio > 2.0 / 3.0:
            state = HazardState.HIGH
        elif ratio > 1.0 / 3.0:
            state = HazardState.MODERATE
        else:
            state = HazardState.NEGLIGIBLE

        return RustJackingResult(
            diameter_mm=diameter_mm,
            i_corr_ua_cm2=i_corr_ua_cm2,
            exposure_years=exposure_years,
            oxide=product.formula,
            expansion_ratio=product.expansion_ratio,
            section_loss_mm=round(loss, 4),
            iron_mass_loss_kg_m=round(iron_mass_kg_m, 4),
            free_expansion_mm=round(free_expansion, 4),
            effective_expansion_mm=round(effective, 4),
            interface_pressure_mpa=round(pressure, 3),
            hoop_stress_mpa=round(hoop, 3),
            tensile_threshold_mpa=tensile_threshold_mpa,
            stress_ratio=round(ratio, 3),
            state=state,
        )
