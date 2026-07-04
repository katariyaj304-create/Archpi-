"""ISRIC SoilGrids v2.0 client (Task 2).

Fetches mean topsoil properties (0-5cm and 5-15cm) for a WGS84 coordinate:
clay / sand / silt (texture fractions), bdod (bulk density) and phh2o (pH),
then returns a cleaned dictionary in engineering units.

SoilGrids reports scaled integers; each layer carries a `d_factor` divisor
(e.g. clay in g/kg with d_factor 10 -> percent). We always honour the
d_factor from the response instead of hardcoding conversions.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

import httpx

logger = logging.getLogger("soil_backend.soilgrids")

# Circuit breaker: after a total failure, skip ISRIC for a cool-down window so
# the dashboard stays snappy on its regional fallback instead of re-timing-out.
CIRCUIT_COOLDOWN_S = 60.0
_circuit_open_until = 0.0

SOILGRIDS_URL = "https://rest.isric.org/soilgrids/v2.0/properties/query"
CLASSIFICATION_URL = "https://rest.isric.org/soilgrids/v2.0/classification/query"
PROPERTIES = ("clay", "sand", "silt", "bdod", "phh2o")
DEPTHS = ("0-5cm", "5-15cm")
REQUEST_TIMEOUT_S = 15.0   # ISRIC regularly needs ~10s per query when degraded
MAX_ATTEMPTS = 2
USER_AGENT = "ArchPi-Diagnostics/1.0 (geotechnical screening; contact: archpi)"

# SoilGrids masks built-up / water pixels (mean = null). Dense city centres —
# exactly where users drop pings — hit the mask, so we sample rings of nearby
# points and use the closest unmasked pixel. The inner ring (~9 km) covers
# towns; the outer ring (~27 km, incl. diagonals) escapes megacity footprints.
NEIGHBOR_RINGS_DEG = (
    ((0.08, 0.0), (-0.08, 0.0), (0.0, 0.08), (0.0, -0.08)),
    ((0.25, 0.0), (-0.25, 0.0), (0.0, 0.25), (0.0, -0.25),
     (0.18, 0.18), (0.18, -0.18), (-0.18, 0.18), (-0.18, -0.18)),
)


class SoilGridsError(RuntimeError):
    """Raised when SoilGrids cannot provide usable data for a coordinate."""


# Soil composition is static, so successful lookups are cached for the process
# lifetime — repeat clicks on the same ping return instantly instead of
# re-walking the sampling rings (~15 s worst case for megacity centres).
_cache: dict[tuple[float, float], dict[str, Any]] = {}
_wrb_cache: dict[tuple[float, float], Optional[str]] = {}
_CACHE_MAX = 256

# WRB reference soil groups -> the Indian soil-survey names engineers know.
WRB_INDIAN_NAMES = {
    "Vertisols": "Black Cotton",
    "Fluvisols": "Alluvial",
    "Cambisols": "Alluvial / Brown",
    "Luvisols": "Red / Brown",
    "Lixisols": "Red",
    "Nitisols": "Red Loam",
    "Acrisols": "Red-Yellow Podzolic",
    "Ferralsols": "Laterite",
    "Plinthosols": "Laterite",
    "Arenosols": "Desert Sand",
    "Calcisols": "Calcareous",
    "Solonchaks": "Saline",
    "Solonetz": "Sodic (Usar)",
    "Leptosols": "Mountain / Skeletal",
    "Regosols": "Immature",
    "Gleysols": "Waterlogged",
    "Histosols": "Peaty / Organic",
    "Andosols": "Volcanic",
    "Podzols": "Podzolic",
    "Kastanozems": "Chestnut",
    "Chernozems": "Black Earth",
    "Phaeozems": "Prairie",
    "Cryosols": "Permafrost",
    "Durisols": "Duripan",
    "Gypsisols": "Gypsic",
    "Planosols": "Bleached Loam",
    "Stagnosols": "Perched-Water",
    "Umbrisols": "Dark Acid Loam",
    "Alisols": "Acid Clay",
    "Retisols": "Interlayered Loam",
    "Anthrosols": "Man-Modified",
    "Technosols": "Urban Fill",
}


async def fetch_wrb_class(
    lat: float,
    lon: float,
    client: Optional[httpx.AsyncClient] = None,
) -> Optional[str]:
    """Most-probable WRB reference soil group at (lat, lon), or None.

    Best-effort enrichment: never raises, single attempt, cached. Pass the
    nearest-pixel coordinates from fetch_soil_properties for masked cities.
    """
    cache_key = (round(lat, 3), round(lon, 3))
    if cache_key in _wrb_cache:
        return _wrb_cache[cache_key]
    if time.monotonic() < _circuit_open_until:
        return None

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient()
    try:
        response = await client.get(
            CLASSIFICATION_URL,
            params={"lat": lat, "lon": lon, "number_classes": 1},
            headers={"User-Agent": USER_AGENT},
            timeout=15.0,  # classification queries run slower than property queries
        )
        response.raise_for_status()
        name = response.json().get("wrb_class_name") or None
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("SoilGrids classification failed: %s", exc)
        name = None
    finally:
        if owns_client:
            await client.aclose()

    if name is not None:  # never cache failures — retry on the next query
        if len(_wrb_cache) >= _CACHE_MAX:
            _wrb_cache.pop(next(iter(_wrb_cache)))
        _wrb_cache[cache_key] = name
    return name


def _parse_layers(payload: dict[str, Any]) -> dict[str, Any]:
    """Flatten the SoilGrids layer/depth structure into engineering units."""
    per_depth: dict[str, dict[str, float]] = {}
    averaged: dict[str, float] = {}

    for layer in payload.get("properties", {}).get("layers", []):
        name = layer.get("name")
        if name not in PROPERTIES:
            continue
        d_factor = (layer.get("unit_measure") or {}).get("d_factor") or 1
        values = []
        for depth in layer.get("depths", []):
            label = depth.get("label", "?")
            mean = (depth.get("values") or {}).get("mean")
            if mean is None:  # ocean / no-data pixel
                continue
            scaled = mean / d_factor
            per_depth.setdefault(label, {})[name] = round(scaled, 2)
            values.append(scaled)
        if values:
            averaged[name] = round(sum(values) / len(values), 2)

    if not averaged:
        raise SoilGridsError("SoilGrids returned no data for this location (ocean or no-data pixel).")
    return {"averaged": averaged, "per_depth": per_depth}


async def _query_point(
    client: httpx.AsyncClient,
    lat: float,
    lon: float,
    max_attempts: int = MAX_ATTEMPTS,
    trip_circuit: bool = True,
) -> dict[str, Any]:
    """One SoilGrids query with retries; raises SoilGridsError on failure.

    A parse-level "no data" (masked pixel) propagates immediately without
    tripping the circuit breaker — the service itself is healthy.
    """
    global _circuit_open_until
    params: list[tuple[str, Any]] = [("lat", lat), ("lon", lon), ("value", "mean")]
    params += [("property", p) for p in PROPERTIES]
    params += [("depth", d) for d in DEPTHS]

    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = await client.get(
                SOILGRIDS_URL,
                params=params,
                headers={"User-Agent": USER_AGENT},
                timeout=REQUEST_TIMEOUT_S,
            )
            response.raise_for_status()
            return _parse_layers(response.json())
        except (httpx.HTTPError, ValueError) as exc:
            last_error = exc
            logger.warning("SoilGrids attempt %d/%d failed: %s", attempt, max_attempts, exc)
            if attempt < max_attempts:
                await asyncio.sleep(1.5 * attempt)  # ISRIC rate-limits aggressive retries
    if trip_circuit:
        _circuit_open_until = time.monotonic() + CIRCUIT_COOLDOWN_S
    raise SoilGridsError(f"SoilGrids unreachable after {max_attempts} attempts: {last_error}")


async def fetch_soil_properties(
    lat: float,
    lon: float,
    client: Optional[httpx.AsyncClient] = None,
) -> dict[str, Any]:
    """Query SoilGrids for topsoil composition at (lat, lon).

    Returns a dict with texture percentages, bulk density (g/cm3), pH and
    the raw per-depth breakdown. If the exact pixel is masked (built-up or
    water), samples a ring of nearby points and uses the closest unmasked
    pixel. Raises SoilGridsError on failure so the caller can decide
    whether to fall back to regional estimates.
    """
    cache_key = (round(lat, 3), round(lon, 3))
    if cache_key in _cache:
        return dict(_cache[cache_key])

    if time.monotonic() < _circuit_open_until:
        raise SoilGridsError("SoilGrids circuit open (recent outage); using fallback.")

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient()

    source = "isric_soilgrids_v2"
    sampled_at: Optional[dict[str, float]] = None
    try:
        try:
            parsed = await _query_point(client, lat, lon)
        except SoilGridsError as exc:
            if "no data" not in str(exc):
                raise  # transport failure — circuit already handled in _query_point
            # Masked (urban/water) pixel: probe rings of nearby points
            # concurrently, nearest ring first
            logger.info("SoilGrids pixel masked at (%s, %s); sampling nearby rings", lat, lon)
            parsed = None
            for ring in NEIGHBOR_RINGS_DEG:
                results = await asyncio.gather(
                    *(
                        _query_point(client, lat + dlat, lon + dlon, max_attempts=1, trip_circuit=False)
                        for dlat, dlon in ring
                    ),
                    return_exceptions=True,
                )
                for (dlat, dlon), result in zip(ring, results):
                    if isinstance(result, dict):
                        parsed = result
                        sampled_at = {"lat": round(lat + dlat, 4), "lon": round(lon + dlon, 4)}
                        source = "isric_soilgrids_v2_nearest_pixel"
                        break
                if parsed is not None:
                    break
            if parsed is None:
                raise SoilGridsError(
                    "SoilGrids has no data at or near this location (urban/water mask)."
                )
    finally:
        if owns_client:
            await client.aclose()

    averaged = parsed["averaged"]
    result = {
        "clay_pct": averaged.get("clay"),
        "sand_pct": averaged.get("sand"),
        "silt_pct": averaged.get("silt"),
        "bulk_density_g_cm3": averaged.get("bdod"),
        "ph": averaged.get("phh2o"),
        "per_depth": parsed["per_depth"],
        "sampled_at": sampled_at,
        "source": source,
    }
    if len(_cache) >= _CACHE_MAX:
        _cache.pop(next(iter(_cache)))  # drop oldest insertion
    _cache[cache_key] = dict(result)
    return result
