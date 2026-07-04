"""Structure registry — selectable buildings that drive every engine.

The forensic dossier page can be pointed at any one of several real-world-
flavoured structures. Each entry here bundles a *complete, coherent* set of
inputs for all six analytical engines (XRD, degradation hazards,
chronological palimpsest, provenance, dendrochronology, radiocarbon) so that
selecting a building re-runs the whole page against one internally consistent
specimen.

Every structure is placed inside the 850-1300 AD window covered by the
dendro master chronology, so its timber crossdates confidently; its
radiocarbon age, stratigraphy and quarry are chosen to tell the same story.

`site_bundle(site_id)` returns request bodies ready to POST unchanged to the
matching analyze endpoints — the frontend is a dumb dispatcher.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .dendro import generate_core_sample
from .synthetic import generate_climate, generate_pattern


@dataclass(frozen=True)
class StructureProfile:
    """One selectable building and its per-engine input parameters."""

    site_id: str
    name: str
    location: str
    period: str
    blurb: str

    # XRD synthetic diffractogram
    xrd_sample_id: str
    xrd_phases: dict[str, float]
    xrd_crystallite_nm: float
    xrd_seed: int

    # Degradation hazards — synthetic microclimate + material/tie profile
    haz_site_id: str
    haz_mean_rh_pct: float
    haz_mean_temperature_c: float
    haz_seed: int
    haz_porosity_pct: float
    haz_tensile_strength_mpa: float
    haz_tie_diameter_mm: float
    haz_tie_exposure_years: float
    haz_tie_cover_mm: float

    # Chronological palimpsest — full sequence payload
    palimpsest: dict[str, Any]

    # Provenance — isotope panel + destination
    prov_sample_id: str
    prov_delta13c: float
    prov_delta18o: float
    prov_sr8786: float
    prov_dest_lat: float
    prov_dest_lon: float
    prov_dest_name: str

    # Dendrochronology — synthetic oak core
    dendro_sample_id: str
    dendro_species: str
    dendro_end_year: int
    dendro_ring_count: int
    dendro_seed: int

    # Radiocarbon — conventional age
    c14_sample_id: str
    c14_lab_code: str
    c14_age_bp: float
    c14_uncertainty_bp: float

    def summary(self) -> dict[str, Any]:
        return {
            "site_id": self.site_id,
            "name": self.name,
            "location": self.location,
            "period": self.period,
            "blurb": self.blurb,
            # The construction site is the building's geographic location —
            # used by the soil and weather pages to run their analyses there.
            "lat": self.prov_dest_lat,
            "lon": self.prov_dest_lon,
        }


def _bundle(p: StructureProfile) -> dict[str, Any]:
    """Expand a profile into ready-to-POST bodies for every engine."""
    two_theta, intensity = generate_pattern(
        p.xrd_phases, crystallite_nm=p.xrd_crystallite_nm, seed=p.xrd_seed
    )
    rh, temp = generate_climate(
        days=365,
        mean_rh_pct=p.haz_mean_rh_pct,
        mean_temperature_c=p.haz_mean_temperature_c,
        seed=p.haz_seed,
    )
    ring_widths = generate_core_sample(
        end_year=p.dendro_end_year,
        ring_count=p.dendro_ring_count,
        seed=p.dendro_seed,
    ).tolist()

    return {
        "xrd": {
            "sample_id": p.xrd_sample_id,
            "two_theta": two_theta,
            "intensity": intensity,
        },
        "hazards": {
            "site_id": p.haz_site_id,
            "relative_humidity_pct": rh,
            "temperature_c": temp,
            "material": {
                "porosity_pct": p.haz_porosity_pct,
                "tensile_strength_mpa": p.haz_tensile_strength_mpa,
            },
            "rust_tie": {
                "diameter_mm": p.haz_tie_diameter_mm,
                "exposure_years": p.haz_tie_exposure_years,
                "oxide": "Fe(OH)3",
                "cover_mm": p.haz_tie_cover_mm,
            },
        },
        "palimpsest": p.palimpsest,
        "provenance": {
            "sample_id": p.prov_sample_id,
            "delta13c": p.prov_delta13c,
            "delta18o": p.prov_delta18o,
            "sr8786": p.prov_sr8786,
            "destination": {
                "lat": p.prov_dest_lat,
                "lon": p.prov_dest_lon,
                "name": p.prov_dest_name,
            },
            "top_k": 3,
        },
        "dendro": {
            "sample_id": p.dendro_sample_id,
            "species": p.dendro_species,
            "ring_widths": ring_widths,
            "bark_edge": False,
            "sapwood_rings": None,
            "sapwood_complete": False,
        },
        "radiocarbon": {
            "sample_id": p.c14_sample_id,
            "lab_code": p.c14_lab_code,
            "radiocarbon_age_bp": p.c14_age_bp,
            "uncertainty_bp": p.c14_uncertainty_bp,
        },
    }


# ── The registry ─────────────────────────────────────────────────────────

_AETHEL_DOSSIER = {
    "site_id": "AETHEL-DOSSIER",
    "components": [
        "Foundation_01", "Roman_Floor", "Hypocaust_Column",
        "West_Arch", "Nave_Vault", "Chapel_Apse", "West_Arch_Repair",
    ],
    "relations": [
        {"earlier": "Foundation_01", "later": "Roman_Floor", "kind": "built_after"},
        {"earlier": "Foundation_01", "later": "Hypocaust_Column", "kind": "built_after"},
        {"earlier": "Roman_Floor", "later": "West_Arch", "kind": "cuts"},
        {"earlier": "West_Arch", "later": "Nave_Vault", "kind": "built_after"},
        {"earlier": "West_Arch", "later": "Chapel_Apse", "kind": "built_after"},
        {"earlier": "West_Arch", "later": "West_Arch_Repair", "kind": "replaces"},
    ],
    "constraints": [
        {"node_id": "Foundation_01", "kind": "inscription",
         "intervals": [{"start_year": 120, "end_year": 150, "weight": 1.0}]},
        {"node_id": "West_Arch", "kind": "radiocarbon_2sigma",
         "intervals": [
             {"start_year": 1235, "end_year": 1240, "weight": 0.136},
             {"start_year": 1240, "end_year": 1265, "weight": 0.682},
             {"start_year": 1265, "end_year": 1280, "weight": 0.136},
         ]},
        {"node_id": "West_Arch_Repair", "kind": "archival",
         "intervals": [{"start_year": 1982, "end_year": 1998, "weight": 1.0}]},
    ],
    "signatures": [
        {"node_id": "Foundation_01", "silica_ratio_pct": 67.0, "calcium_ratio_pct": 23.0},
        {"node_id": "Roman_Floor", "silica_ratio_pct": 66.0, "calcium_ratio_pct": 24.0},
        {"node_id": "Hypocaust_Column", "silica_ratio_pct": 65.0, "calcium_ratio_pct": 25.0},
        {"node_id": "West_Arch", "silica_ratio_pct": 28.0, "calcium_ratio_pct": 60.0},
        {"node_id": "Nave_Vault", "silica_ratio_pct": 30.0, "calcium_ratio_pct": 58.0},
        {"node_id": "Chapel_Apse", "silica_ratio_pct": 29.0, "calcium_ratio_pct": 59.0},
        {"node_id": "West_Arch_Repair", "silica_ratio_pct": 45.0, "calcium_ratio_pct": 40.0},
    ],
    "clustering": {"variance_threshold": 0.15},
    "padding_years": 50.0,
}

_SAN_NICOLO_DOSSIER = {
    "site_id": "SAN-NICOLO",
    "components": [
        "Timber_Piles", "Brick_Foundation", "Campanile_Shaft",
        "Belfry_Gothic", "SE_Buttress",
    ],
    "relations": [
        {"earlier": "Timber_Piles", "later": "Brick_Foundation", "kind": "built_after"},
        {"earlier": "Brick_Foundation", "later": "Campanile_Shaft", "kind": "built_after"},
        {"earlier": "Campanile_Shaft", "later": "Belfry_Gothic", "kind": "built_after"},
        {"earlier": "Campanile_Shaft", "later": "SE_Buttress", "kind": "cuts"},
    ],
    "constraints": [
        {"node_id": "Timber_Piles", "kind": "radiocarbon_2sigma",
         "intervals": [
             {"start_year": 1150, "end_year": 1160, "weight": 0.16},
             {"start_year": 1160, "end_year": 1180, "weight": 0.68},
             {"start_year": 1180, "end_year": 1190, "weight": 0.16},
         ]},
        {"node_id": "Belfry_Gothic", "kind": "archival",
         "intervals": [{"start_year": 1440, "end_year": 1470, "weight": 1.0}]},
        {"node_id": "SE_Buttress", "kind": "archival",
         "intervals": [{"start_year": 1550, "end_year": 1580, "weight": 1.0}]},
    ],
    "signatures": [
        {"node_id": "Timber_Piles", "silica_ratio_pct": 40.0, "calcium_ratio_pct": 44.0},
        {"node_id": "Brick_Foundation", "silica_ratio_pct": 43.0, "calcium_ratio_pct": 41.0},
        {"node_id": "Campanile_Shaft", "silica_ratio_pct": 45.0, "calcium_ratio_pct": 39.0},
        {"node_id": "Belfry_Gothic", "silica_ratio_pct": 30.0, "calcium_ratio_pct": 58.0},
        {"node_id": "SE_Buttress", "silica_ratio_pct": 28.0, "calcium_ratio_pct": 60.0},
    ],
    "clustering": {"variance_threshold": 0.15},
    "padding_years": 50.0,
}

_BYZ_NARTHEX_DOSSIER = {
    "site_id": "BYZ-NARTHEX",
    "components": [
        "Justinianic_Spolia", "Middle_Byz_Wall", "Narthex_Vault",
        "Marble_Revetment", "Ottoman_Repair",
    ],
    "relations": [
        {"earlier": "Justinianic_Spolia", "later": "Middle_Byz_Wall", "kind": "built_after"},
        {"earlier": "Middle_Byz_Wall", "later": "Narthex_Vault", "kind": "built_after"},
        {"earlier": "Middle_Byz_Wall", "later": "Marble_Revetment", "kind": "built_after"},
        {"earlier": "Narthex_Vault", "later": "Ottoman_Repair", "kind": "replaces"},
    ],
    "constraints": [
        {"node_id": "Justinianic_Spolia", "kind": "inscription",
         "intervals": [{"start_year": 530, "end_year": 560, "weight": 1.0}]},
        {"node_id": "Narthex_Vault", "kind": "radiocarbon_2sigma",
         "intervals": [
             {"start_year": 950, "end_year": 965, "weight": 0.16},
             {"start_year": 965, "end_year": 1020, "weight": 0.68},
             {"start_year": 1020, "end_year": 1035, "weight": 0.16},
         ]},
        {"node_id": "Ottoman_Repair", "kind": "archival",
         "intervals": [{"start_year": 1490, "end_year": 1520, "weight": 1.0}]},
    ],
    "signatures": [
        {"node_id": "Justinianic_Spolia", "silica_ratio_pct": 34.0, "calcium_ratio_pct": 55.0},
        {"node_id": "Middle_Byz_Wall", "silica_ratio_pct": 36.0, "calcium_ratio_pct": 54.0},
        {"node_id": "Narthex_Vault", "silica_ratio_pct": 35.0, "calcium_ratio_pct": 55.0},
        {"node_id": "Marble_Revetment", "silica_ratio_pct": 33.0, "calcium_ratio_pct": 57.0},
        {"node_id": "Ottoman_Repair", "silica_ratio_pct": 50.0, "calcium_ratio_pct": 38.0},
    ],
    "clustering": {"variance_threshold": 0.15},
    "padding_years": 50.0,
}


STRUCTURE_REGISTRY: dict[str, StructureProfile] = {
    s.site_id: s
    for s in (
        StructureProfile(
            site_id="aethel-cathedral",
            name="Aethel Cathedral — North Nave (Site 14-B)",
            location="Latium, Rome",
            period="Roman core, Gothic vault · c. 120–1280 AD",
            blurb="Comprehensive petrographic and stratigraphic analysis of the "
                  "North Nave structural assembly: a Roman foundation overbuilt by "
                  "a 13th-century Gothic nave, with Carrara marble columns.",
            xrd_sample_id="ATH-22-NNA (Roman-phase mortar)",
            xrd_phases={"quartz": 0.55, "calcite": 0.25, "gypsum": 0.20},
            xrd_crystallite_nm=35.0,
            xrd_seed=22,
            haz_site_id="Aethel-NorthNave-Facade",
            haz_mean_rh_pct=74.0,
            haz_mean_temperature_c=11.0,
            haz_seed=42,
            haz_porosity_pct=22.0,
            haz_tensile_strength_mpa=3.0,
            haz_tie_diameter_mm=20.0,
            haz_tie_exposure_years=150.0,
            haz_tie_cover_mm=50.0,
            palimpsest=_AETHEL_DOSSIER,
            prov_sample_id="AETHEL-COLUMN-03",
            prov_delta13c=2.1,
            prov_delta18o=-2.0,
            prov_sr8786=0.70785,
            prov_dest_lat=41.89,
            prov_dest_lon=12.49,
            prov_dest_name="Basilica Site, Rome",
            dendro_sample_id="CORE-012",
            dendro_species="Quercus robur",
            dendro_end_year=1242,
            dendro_ring_count=142,
            dendro_seed=77,
            c14_sample_id="VAULT-CHARCOAL-07",
            c14_lab_code="AeL-2207",
            c14_age_bp=760.0,
            c14_uncertainty_bp=25.0,
        ),
        StructureProfile(
            site_id="san-nicolo",
            name="San Niccolò Campanile — Lagoon Belltower",
            location="Venetian Lagoon",
            period="Romanesque shaft, Gothic belfry · c. 1160–1580 AD",
            blurb="A lagoon-edge campanile founded on oak piles, its brick shaft "
                  "raised in the late 12th century and crowned by a Gothic belfry, "
                  "under relentless marine-salt loading.",
            xrd_sample_id="SNL-07 Campanile Mortar",
            xrd_phases={"quartz": 0.42, "calcite": 0.40, "gypsum": 0.18},
            xrd_crystallite_nm=44.0,
            xrd_seed=19,
            haz_site_id="SanNicolo-Campanile-Base",
            haz_mean_rh_pct=86.0,
            haz_mean_temperature_c=14.0,
            haz_seed=7,
            haz_porosity_pct=30.0,
            haz_tensile_strength_mpa=2.0,
            haz_tie_diameter_mm=24.0,
            haz_tie_exposure_years=210.0,
            haz_tie_cover_mm=40.0,
            palimpsest=_SAN_NICOLO_DOSSIER,
            prov_sample_id="SNL-COLUMN-02",
            prov_delta13c=2.55,
            prov_delta18o=-2.4,
            prov_sr8786=0.70820,
            prov_dest_lat=45.43,
            prov_dest_lon=12.34,
            prov_dest_name="San Niccolò, Venice",
            dendro_sample_id="SNL-PILE-04",
            dendro_species="Quercus robur",
            dendro_end_year=1172,
            dendro_ring_count=128,
            dendro_seed=41,
            c14_sample_id="SNL-PILE-CHARCOAL",
            c14_lab_code="AeL-1180",
            c14_age_bp=830.0,
            c14_uncertainty_bp=30.0,
        ),
        StructureProfile(
            site_id="byzantine-narthex",
            name="Byzantine Narthex — Middle-Byzantine Church",
            location="Constantinople",
            period="Justinianic spolia, Middle-Byzantine rebuild · c. 540–1520 AD",
            blurb="A middle-Byzantine church narthex raised on reused Justinianic "
                  "ashlar, roofed with oak tie-beams and faced in Proconnesian "
                  "marble, exposed to hard continental freeze-thaw.",
            xrd_sample_id="BYZ-03 Narthex Mortar",
            xrd_phases={"quartz": 0.34, "calcite": 0.60, "gypsum": 0.06},
            xrd_crystallite_nm=52.0,
            xrd_seed=11,
            haz_site_id="Byz-Narthex-North",
            haz_mean_rh_pct=66.0,
            haz_mean_temperature_c=7.0,
            haz_seed=3,
            haz_porosity_pct=26.0,
            haz_tensile_strength_mpa=2.5,
            haz_tie_diameter_mm=20.0,
            haz_tie_exposure_years=230.0,
            haz_tie_cover_mm=55.0,
            palimpsest=_BYZ_NARTHEX_DOSSIER,
            prov_sample_id="BYZ-REVET-01",
            prov_delta13c=2.55,
            prov_delta18o=-2.4,
            prov_sr8786=0.70820,
            prov_dest_lat=41.01,
            prov_dest_lon=28.98,
            prov_dest_name="Narthex, Constantinople",
            dendro_sample_id="BYZ-BEAM-02",
            dendro_species="Quercus robur",
            dendro_end_year=1012,
            dendro_ring_count=118,
            dendro_seed=23,
            c14_sample_id="BYZ-MORTAR-CHARCOAL",
            c14_lab_code="AeL-1000",
            c14_age_bp=1040.0,
            c14_uncertainty_bp=30.0,
        ),
    )
}


def list_structures() -> list[dict[str, str]]:
    """Selector metadata for every registered structure, in display order."""
    return [s.summary() for s in STRUCTURE_REGISTRY.values()]


def site_bundle(site_id: str) -> dict[str, Any]:
    """Full per-engine request bodies for one structure.

    Raises KeyError if the id is unknown.
    """
    profile = STRUCTURE_REGISTRY[site_id]
    return {**profile.summary(), "bundles": _bundle(profile)}
