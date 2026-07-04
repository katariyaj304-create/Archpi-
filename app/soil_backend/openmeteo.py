"""Open-Meteo client for the Environmental Dynamics feature (Task 1).

Three fetchers, all free / keyless:
- fetch_current_weather: current conditions + past-24h hourlies + 7-day
  daily forecast for one coordinate (10-minute TTL cache).
- fetch_wind_grid: one multi-location call returning wind vectors for the
  global schematic.
- fetch_monthly_normal: 30-year (1991-2020) ERA5 mean temperature for the
  current calendar month, used to compute live-vs-historical anomalies
  (heavy call; cached for the process lifetime).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

import httpx

logger = logging.getLogger("soil_backend.openmeteo")

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
TIMEOUT_S = 20.0
USER_AGENT = "ArchPi-Diagnostics/1.0 (structural health monitoring; contact: archpi)"

CURRENT_VARS = (
    "temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m,"
    "wind_gusts_10m,surface_pressure,precipitation,apparent_temperature"
)

_weather_cache: dict[tuple[float, float], tuple[float, dict]] = {}
_WEATHER_TTL_S = 600.0
_normal_cache: dict[tuple[float, float, int], float] = {}


class OpenMeteoError(RuntimeError):
    """Raised when Open-Meteo cannot provide data for a coordinate."""


async def fetch_current_weather(
    lat: float,
    lon: float,
    client: Optional[httpx.AsyncClient] = None,
) -> dict[str, Any]:
    """Current conditions, past-24h hourlies and a 7-day daily forecast."""
    cache_key = (round(lat, 2), round(lon, 2))
    cached = _weather_cache.get(cache_key)
    if cached and time.monotonic() - cached[0] < _WEATHER_TTL_S:
        return dict(cached[1])

    params = {
        "latitude": lat,
        "longitude": lon,
        "current": CURRENT_VARS,
        "hourly": "temperature_2m,relative_humidity_2m,wind_speed_10m",
        "daily": "precipitation_sum,temperature_2m_max,temperature_2m_min,wind_gusts_10m_max",
        "past_days": 1,
        "forecast_days": 7,
        "timezone": "auto",
    }
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient()
    try:
        response = await client.get(
            FORECAST_URL, params=params,
            headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_S,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise OpenMeteoError(f"Open-Meteo request failed: {exc}") from exc
    finally:
        if owns_client:
            await client.aclose()

    current = payload.get("current") or {}
    hourly = payload.get("hourly") or {}
    daily = payload.get("daily") or {}
    if current.get("temperature_2m") is None:
        raise OpenMeteoError("Open-Meteo returned no current conditions for this location.")

    # past_days=1 + forecast_days=7 -> hour 0 is 24h ago; keep the trailing
    # 25 samples up to "now" for the 24-hour structural loading window
    now_iso = current.get("time", "")
    times = hourly.get("time", [])
    now_idx = times.index(now_iso) if now_iso in times else 24

    def window(key: str) -> list[float]:
        vals = hourly.get(key, [])
        return [v for v in vals[max(0, now_idx - 24): now_idx + 1] if v is not None]

    result = {
        "temperature_c": current.get("temperature_2m"),
        "apparent_temperature_c": current.get("apparent_temperature"),
        "relative_humidity_pct": current.get("relative_humidity_2m"),
        "wind_speed_kmh": current.get("wind_speed_10m"),
        "wind_direction_deg": current.get("wind_direction_10m"),
        "wind_gusts_kmh": current.get("wind_gusts_10m"),
        "surface_pressure_hpa": current.get("surface_pressure"),
        "precipitation_mm": current.get("precipitation"),
        "observed_at": now_iso,
        "hourly_24h": {
            "temperature_c": window("temperature_2m"),
            "relative_humidity_pct": window("relative_humidity_2m"),
            "wind_speed_kmh": window("wind_speed_10m"),
        },
        "daily_7d": {
            "date": daily.get("time", []),
            "precipitation_sum_mm": daily.get("precipitation_sum", []),
            "temperature_max_c": daily.get("temperature_2m_max", []),
            "temperature_min_c": daily.get("temperature_2m_min", []),
            "wind_gusts_max_kmh": daily.get("wind_gusts_10m_max", []),
        },
        "source": "open_meteo_forecast",
    }
    _weather_cache[cache_key] = (time.monotonic(), dict(result))
    return result


async def fetch_wind_grid(
    stations: Sequence[dict[str, Any]],
    client: Optional[httpx.AsyncClient] = None,
) -> list[dict[str, Any]]:
    """Current wind vectors for many stations in a single multi-location call.

    stations: [{"name": ..., "lat": ..., "lon": ...}, ...]
    """
    params = {
        "latitude": ",".join(str(s["lat"]) for s in stations),
        "longitude": ",".join(str(s["lon"]) for s in stations),
        "current": CURRENT_VARS,
        "timezone": "UTC",
    }
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient()
    try:
        response = await client.get(
            FORECAST_URL, params=params,
            headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_S,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise OpenMeteoError(f"Open-Meteo grid request failed: {exc}") from exc
    finally:
        if owns_client:
            await client.aclose()

    results = payload if isinstance(payload, list) else [payload]
    vectors = []
    for station, entry in zip(stations, results):
        current = entry.get("current") or {}
        vectors.append({
            "name": station["name"],
            "lat": station["lat"],
            "lon": station["lon"],
            "temperature_c": current.get("temperature_2m"),
            "relative_humidity_pct": current.get("relative_humidity_2m"),
            "wind_speed_kmh": current.get("wind_speed_10m"),
            "wind_direction_deg": current.get("wind_direction_10m"),
            "wind_gusts_kmh": current.get("wind_gusts_10m"),
            "surface_pressure_hpa": current.get("surface_pressure"),
        })
    return vectors


async def fetch_monthly_normal(
    lat: float,
    lon: float,
    client: Optional[httpx.AsyncClient] = None,
) -> Optional[float]:
    """30-year (1991-2020) ERA5 mean temperature for the current month.

    Best effort: returns None on failure so anomaly cards degrade gracefully.
    Cached for the process lifetime — the normal only changes month to month.
    """
    month = datetime.now(timezone.utc).month
    cache_key = (round(lat, 1), round(lon, 1), month)
    if cache_key in _normal_cache:
        return _normal_cache[cache_key]

    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": "1991-01-01",
        "end_date": "2020-12-31",
        "daily": "temperature_2m_mean",
        "timezone": "UTC",
    }
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient()
    try:
        response = await client.get(
            ARCHIVE_URL, params=params,
            headers={"User-Agent": USER_AGENT}, timeout=45.0,
        )
        response.raise_for_status()
        daily = response.json().get("daily") or {}
        dates = daily.get("time", [])
        temps = daily.get("temperature_2m_mean", [])
        month_vals = [
            t for d, t in zip(dates, temps)
            if t is not None and int(d[5:7]) == month
        ]
        normal = round(sum(month_vals) / len(month_vals), 2) if month_vals else None
    except (httpx.HTTPError, ValueError, ZeroDivisionError) as exc:
        logger.warning("Climate normal fetch failed for (%s, %s): %s", lat, lon, exc)
        normal = None
    finally:
        if owns_client:
            await client.aclose()

    if normal is not None:
        _normal_cache[cache_key] = normal
    return normal
