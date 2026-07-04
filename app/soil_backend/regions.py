"""Regional lookup for the Diagnostic Hub (supports Task 1 + Task 4).

Primary path:  PostGIS (Supabase) via SQLAlchemy/GeoAlchemy2 — set DATABASE_URL
               (e.g. postgresql+asyncpg://...supabase.co:5432/postgres) and run
               schema.sql first.
Fallback path: the same seed polygons compiled in, matched with a ray-casting
               point-in-polygon test, so the API works with zero infrastructure.

Every region also carries typical topsoil composition so the API can degrade
gracefully when ISRIC SoilGrids is unreachable.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger("soil_backend.regions")

# (lon, lat) rings — identical to the seed polygons in schema.sql.
# Listed most-specific first; the first polygon containing the point wins.
BUILTIN_REGIONS: list[dict[str, Any]] = [
    {
        "region_name": "Northeast Hills",
        "seismic_risk_score": 0.90,
        "seismic_zone": "V",
        "dominant_soil": "Residual lateritic / alluvial",
        "polygon": [(89.5, 22.0), (97.5, 21.5), (97.5, 28.5), (89.5, 27.0)],
        "typical_soil": {"clay_pct": 28.0, "sand_pct": 38.0, "silt_pct": 34.0, "bulk_density_g_cm3": 1.25, "ph": 5.4},
    },
    {
        "region_name": "Himalayan Seismic Belt",
        "seismic_risk_score": 0.92,
        "seismic_zone": "V",
        "dominant_soil": "Colluvial / fractured rock",
        "polygon": [(73.0, 35.5), (78.5, 32.5), (84.0, 29.5), (89.0, 28.0), (95.0, 28.5),
                    (97.5, 28.8), (97.5, 30.5), (90.0, 30.5), (80.0, 34.0), (74.5, 37.0)],
        "typical_soil": {"clay_pct": 18.0, "sand_pct": 48.0, "silt_pct": 34.0, "bulk_density_g_cm3": 1.35, "ph": 6.2},
    },
    {
        "region_name": "Thar Desert Region",
        "seismic_risk_score": 0.45,
        "seismic_zone": "III",
        "dominant_soil": "Aeolian sand",
        "polygon": [(68.5, 23.8), (75.0, 24.5), (75.5, 29.5), (69.5, 28.5)],
        "typical_soil": {"clay_pct": 8.0, "sand_pct": 78.0, "silt_pct": 14.0, "bulk_density_g_cm3": 1.55, "ph": 8.1},
    },
    {
        "region_name": "Deccan Trap (Black Cotton Belt)",
        "seismic_risk_score": 0.35,
        "seismic_zone": "III",
        "dominant_soil": "Expansive black cotton clay",
        "polygon": [(73.8, 15.8), (80.0, 15.5), (80.5, 22.5), (74.5, 23.5), (73.6, 19.0)],
        "typical_soil": {"clay_pct": 48.0, "sand_pct": 22.0, "silt_pct": 30.0, "bulk_density_g_cm3": 1.30, "ph": 7.8},
    },
    {
        "region_name": "Western Coastal Zone",
        "seismic_risk_score": 0.60,
        "seismic_zone": "III",
        "dominant_soil": "Marine clay / lateritic",
        "polygon": [(72.0, 21.0), (73.6, 21.0), (76.4, 8.0), (74.6, 8.0)],
        "typical_soil": {"clay_pct": 38.0, "sand_pct": 32.0, "silt_pct": 30.0, "bulk_density_g_cm3": 1.28, "ph": 6.4},
    },
    {
        "region_name": "Eastern Coastal Zone",
        "seismic_risk_score": 0.55,
        "seismic_zone": "III",
        "dominant_soil": "Deltaic alluvium / marine clay",
        "polygon": [(79.8, 7.9), (81.6, 7.9), (87.5, 21.5), (85.4, 22.3), (80.0, 13.0)],
        "typical_soil": {"clay_pct": 34.0, "sand_pct": 36.0, "silt_pct": 30.0, "bulk_density_g_cm3": 1.32, "ph": 6.8},
    },
    {
        "region_name": "Indo-Gangetic Alluvial Plain",
        "seismic_risk_score": 0.70,
        "seismic_zone": "IV",
        "dominant_soil": "Deep alluvium (silty sand / clay)",
        "polygon": [(72.5, 30.5), (75.5, 28.5), (80.0, 25.5), (86.0, 23.8), (91.5, 23.5),
                    (92.0, 25.5), (89.0, 26.2), (84.0, 27.5), (78.0, 30.5), (73.5, 32.5)],
        "typical_soil": {"clay_pct": 22.0, "sand_pct": 42.0, "silt_pct": 36.0, "bulk_density_g_cm3": 1.42, "ph": 7.4},
    },
    {
        "region_name": "South Peninsular Craton",
        "seismic_risk_score": 0.25,
        "seismic_zone": "II",
        "dominant_soil": "Residual red soil over gneiss",
        "polygon": [(74.6, 8.2), (79.6, 8.2), (80.2, 15.0), (78.0, 17.0), (74.2, 14.0)],
        "typical_soil": {"clay_pct": 26.0, "sand_pct": 46.0, "silt_pct": 28.0, "bulk_density_g_cm3": 1.45, "ph": 6.6},
    },
]

DEFAULT_REGION: dict[str, Any] = {
    "region_name": "Unindexed Region",
    "seismic_risk_score": 0.30,
    "seismic_zone": "II",
    "dominant_soil": "Unclassified",
    "typical_soil": {"clay_pct": 25.0, "sand_pct": 45.0, "silt_pct": 30.0, "bulk_density_g_cm3": 1.40, "ph": 6.8},
}


def _point_in_polygon(lon: float, lat: float, ring: list[tuple[float, float]]) -> bool:
    """Ray-casting point-in-polygon test (ring need not be closed)."""
    inside = False
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        if (y1 > lat) != (y2 > lat):
            x_cross = x1 + (lat - y1) * (x2 - x1) / (y2 - y1)
            if lon < x_cross:
                inside = not inside
    return inside


def _builtin_lookup(lat: float, lon: float) -> dict[str, Any]:
    for region in BUILTIN_REGIONS:
        if _point_in_polygon(lon, lat, region["polygon"]):
            result = {k: v for k, v in region.items() if k != "polygon"}
            result["source"] = "builtin_polygons"
            return result
    result = dict(DEFAULT_REGION)
    result["source"] = "builtin_polygons"
    return result


# --- PostGIS path (SQLAlchemy / GeoAlchemy2), used when DATABASE_URL is set ---

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        from sqlalchemy.ext.asyncio import create_async_engine  # lazy: optional dependency
        url = os.environ["DATABASE_URL"]
        if url.startswith("postgresql://"):  # Supabase dashboard format -> async driver
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        _engine = create_async_engine(url, pool_pre_ping=True)
    return _engine


async def _postgis_lookup(lat: float, lon: float) -> Optional[dict[str, Any]]:
    from sqlalchemy import text

    engine = _get_engine()
    stmt = text(
        """
        SELECT region_name, seismic_risk_score, seismic_zone, dominant_soil
        FROM regional_diagnostics
        WHERE ST_Contains(boundary, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326))
        ORDER BY ST_Area(boundary) ASC
        LIMIT 1
        """
    )
    async with engine.connect() as conn:
        row = (await conn.execute(stmt, {"lat": lat, "lon": lon})).mappings().first()
    if row is None:
        return None
    typical = next(
        (r["typical_soil"] for r in BUILTIN_REGIONS if r["region_name"] == row["region_name"]),
        DEFAULT_REGION["typical_soil"],
    )
    return {**dict(row), "typical_soil": typical, "source": "postgis"}


async def lookup_region(lat: float, lon: float) -> dict[str, Any]:
    """Resolve the geotechnical region for a coordinate.

    Tries PostGIS when DATABASE_URL is configured; on any failure (or no
    match) falls back to the compiled-in polygons so the endpoint never 500s
    because of missing infrastructure.
    """
    if os.environ.get("DATABASE_URL"):
        try:
            match = await _postgis_lookup(lat, lon)
            if match is not None:
                return match
        except Exception as exc:  # missing driver, network, bad credentials...
            logger.warning("PostGIS lookup failed, using builtin polygons: %s", exc)
    return _builtin_lookup(lat, lon)
