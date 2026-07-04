"""ArchPi Geotechnical Diagnostic Hub — FastAPI service (Task 4).

Run from the app/ directory:

    python -m uvicorn soil_backend.main:app --port 8000 --reload

GET /api/v1/diagnostics/?lat=19.076&lon=72.877 returns the compiled payload:
region (PostGIS point-in-polygon), ISRIC SoilGrids topsoil composition, the
Terzaghi Safe Bearing Capacity estimate, and derived hazard indices.
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from .openmeteo import (
    OpenMeteoError,
    fetch_current_weather,
    fetch_monthly_normal,
    fetch_wind_grid,
)
from .regions import lookup_region
from .sbc import calculate_sbc, classify_texture, liquefaction_index
from .soilgrids import WRB_INDIAN_NAMES, SoilGridsError, fetch_soil_properties, fetch_wrb_class
from .structural import (
    calculate_wind_shear,
    evaluate_carbonation_risk,
    monsoon_outlook,
    thermal_loading,
    wet_bulb_c,
)

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("soil_backend")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # One shared connection pool for all SoilGrids calls
    app.state.http = httpx.AsyncClient(timeout=30.0)
    yield
    await app.state.http.aclose()


app = FastAPI(
    title="ArchPi Geotechnical Diagnostic Hub",
    version="1.0.0",
    lifespan=lifespan,
)

# Dev CORS: the UI is served by the Node server on :3000
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "geotechnical-diagnostic-hub"}


# Supabase free-plan database ceiling — used by the usage dashboard to show
# how much storage headroom remains before the project needs a paid plan
SUPABASE_FREE_LIMIT_MB = 500.0


@app.get("/api/v1/database/stats")
async def database_stats() -> dict:
    """Live Supabase/PostGIS storage report: total size vs the free-plan
    limit, plus per-table row counts and sizes so the dashboard can show
    exactly what is stored."""
    import os

    if not os.environ.get("DATABASE_URL"):
        return {"configured": False, "reason": "DATABASE_URL not set"}
    try:
        from sqlalchemy import text

        from .regions import _get_engine

        engine = _get_engine()
        async with engine.connect() as conn:
            size_row = (await conn.execute(text(
                "SELECT pg_database_size(current_database()) AS bytes, current_database() AS name"
            ))).mappings().first()
            tables = (await conn.execute(text(
                """
                SELECT c.relname AS table_name,
                       COALESCE(s.n_live_tup, 0) AS row_count,
                       pg_total_relation_size(c.oid) AS total_bytes
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid
                WHERE n.nspname = 'public' AND c.relkind = 'r'
                ORDER BY pg_total_relation_size(c.oid) DESC
                """
            ))).mappings().all()
            postgis = (await conn.execute(text(
                "SELECT extversion FROM pg_extension WHERE extname = 'postgis'"
            ))).scalar()

        used_mb = round(size_row["bytes"] / (1024 * 1024), 2)
        return {
            "configured": True,
            "database": size_row["name"],
            "postgis_version": postgis,
            "used_mb": used_mb,
            "limit_mb": SUPABASE_FREE_LIMIT_MB,
            "remaining_mb": round(max(0.0, SUPABASE_FREE_LIMIT_MB - used_mb), 2),
            "percent_used": round(min(100.0, used_mb / SUPABASE_FREE_LIMIT_MB * 100), 1),
            "tables": [
                {
                    "name": t["table_name"],
                    "rows": int(t["row_count"]),
                    "size_kb": round(t["total_bytes"] / 1024, 1),
                }
                for t in tables
            ],
        }
    except Exception as exc:  # bad credentials, network, pooler asleep...
        logger.warning("database stats failed: %s", exc)
        return {"configured": True, "error": str(exc)[:200]}


async def _diagnose_point(lat: float, lon: float) -> dict:
    started = time.perf_counter()

    # 1. Which indexed region does this coordinate fall inside? (ST_Contains)
    region = await lookup_region(lat, lon)

    # 2. Real-world topsoil composition from ISRIC SoilGrids, with graceful
    #    degradation to the region's typical profile if ISRIC is down.
    try:
        soil = await fetch_soil_properties(lat, lon, client=app.state.http)
    except SoilGridsError as exc:
        logger.warning("SoilGrids unavailable (%s); using regional typical profile", exc)
        soil = {**region["typical_soil"], "per_depth": {}, "source": "regional_typical_fallback"}

    clay = soil.get("clay_pct")
    sand = soil.get("sand_pct")
    bdod = soil.get("bulk_density_g_cm3")
    if clay is None or sand is None or bdod is None:
        raise HTTPException(
            status_code=422,
            detail="No soil composition available for this coordinate (likely open water).",
        )

    # 2b. WRB soil classification (best effort); query the nearest unmasked
    #     pixel when the exact point was urban-masked
    wrb_lat, wrb_lon = lat, lon
    if soil.get("sampled_at"):
        wrb_lat, wrb_lon = soil["sampled_at"]["lat"], soil["sampled_at"]["lon"]
    wrb_class = await fetch_wrb_class(wrb_lat, wrb_lon, client=app.state.http)

    # 3. Safe Bearing Capacity via Terzaghi (strip footing, B=1m, D=1m, FS=3)
    bearing = calculate_sbc(clay_pct=clay, sand_pct=sand, bulk_density=bdod)

    # 4. Derived diagnostics for the UI widgets
    silt = soil.get("silt_pct") or max(0.0, 100.0 - clay - sand)
    liquefaction = liquefaction_index(sand_pct=sand, seismic_risk_score=region["seismic_risk_score"])

    return {
        "query": {"lat": lat, "lon": lon},
        "region": {
            "name": region["region_name"],
            "seismic_risk_score": region["seismic_risk_score"],
            "seismic_zone": region["seismic_zone"],
            "dominant_soil": region.get("dominant_soil"),
            "source": region["source"],
        },
        "soil_profile": {
            "clay_pct": clay,
            "sand_pct": sand,
            "silt_pct": silt,
            "bulk_density_g_cm3": bdod,
            "ph": soil.get("ph"),
            "texture_class": classify_texture(clay, sand, silt),
            "wrb_class": wrb_class,
            "wrb_common_name": WRB_INDIAN_NAMES.get(wrb_class) if wrb_class else None,
            "per_depth": soil.get("per_depth", {}),
            "sampled_at": soil.get("sampled_at"),
            "source": soil["source"],
        },
        "bearing_capacity": bearing,
        "hazards": {
            "liquefaction_index": liquefaction["index"],
            "liquefaction_label": liquefaction["label"],
        },
        "meta": {
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "disclaimer": "Screening-level estimate; not a substitute for site investigation.",
        },
    }


@app.get("/api/v1/diagnostics/")
async def diagnostics(
    lat: float = Query(..., ge=-90.0, le=90.0, description="Latitude (WGS84)"),
    lon: float = Query(..., ge=-180.0, le=180.0, description="Longitude (WGS84)"),
) -> dict:
    return await _diagnose_point(lat, lon)


AREA_GRID_N = 3  # 3x3 sample grid across the selected bounding box


@app.get("/api/v1/diagnostics/area/")
async def area_diagnostics(
    min_lat: float = Query(..., ge=-90.0, le=90.0),
    min_lon: float = Query(..., ge=-180.0, le=180.0),
    max_lat: float = Query(..., ge=-90.0, le=90.0),
    max_lon: float = Query(..., ge=-180.0, le=180.0),
) -> dict:
    """Aggregate diagnostics for a drag-selected bounding box.

    Samples an evenly spaced grid inside the box concurrently, then reports
    the regions covered, mean topsoil composition, the SBC computed from the
    mean profile (plus the min-max range across samples) and the worst-case
    seismic / liquefaction exposure anywhere in the selection.
    """
    started = time.perf_counter()
    if max_lat < min_lat or max_lon < min_lon:
        raise HTTPException(status_code=400, detail="max_lat/max_lon must be >= min_lat/min_lon.")

    def spread(lo: float, hi: float) -> list[float]:
        if hi <= lo:
            return [lo]
        return [lo + (hi - lo) * i / (AREA_GRID_N - 1) for i in range(AREA_GRID_N)]

    points = [(la, lo) for la in spread(min_lat, max_lat) for lo in spread(min_lon, max_lon)]
    results = await asyncio.gather(
        *(_diagnose_point(la, lo) for la, lo in points), return_exceptions=True
    )
    samples = [r for r in results if isinstance(r, dict)]
    if not samples:
        raise HTTPException(
            status_code=422,
            detail="No usable soil data anywhere in the selected area (likely open water).",
        )

    def mean_of(key: str) -> float | None:
        vals = [s["soil_profile"][key] for s in samples if s["soil_profile"].get(key) is not None]
        return round(sum(vals) / len(vals), 2) if vals else None

    clay, sand, silt = mean_of("clay_pct"), mean_of("sand_pct"), mean_of("silt_pct")
    bdod, ph = mean_of("bulk_density_g_cm3"), mean_of("ph")

    bearing = calculate_sbc(clay_pct=clay, sand_pct=sand, bulk_density=bdod)
    sbcs = [s["bearing_capacity"]["safe_bearing_capacity_kn_m2"] for s in samples]
    bearing["range_kn_m2"] = [min(sbcs), max(sbcs)]

    wrbs = [s["soil_profile"]["wrb_class"] for s in samples if s["soil_profile"].get("wrb_class")]
    wrb_class = max(set(wrbs), key=wrbs.count) if wrbs else None
    wrb_distinct = list(dict.fromkeys(wrbs))

    names = [s["region"]["name"] for s in samples]
    distinct = list(dict.fromkeys(names))
    primary = max(set(names), key=names.count)
    worst_region = max(samples, key=lambda s: s["region"]["seismic_risk_score"])["region"]
    worst_hazards = max(samples, key=lambda s: s["hazards"]["liquefaction_index"])["hazards"]
    live = sum(1 for s in samples if s["soil_profile"]["source"].startswith("isric"))

    return {
        "query": {
            "bbox": {"min_lat": min_lat, "min_lon": min_lon, "max_lat": max_lat, "max_lon": max_lon},
            "samples_requested": len(points),
            "samples_ok": len(samples),
            "regions_covered": distinct,
        },
        "region": {
            "name": primary if len(distinct) == 1 else f"{primary} (+{len(distinct) - 1} more)",
            "seismic_risk_score": worst_region["seismic_risk_score"],
            "seismic_zone": worst_region["seismic_zone"],
            "dominant_soil": worst_region.get("dominant_soil"),
            "source": f"aggregate/{worst_region['source']}",
        },
        "soil_profile": {
            "clay_pct": clay,
            "sand_pct": sand,
            "silt_pct": silt,
            "bulk_density_g_cm3": bdod,
            "ph": ph,
            "texture_class": classify_texture(clay, sand, silt or 0.0),
            "wrb_class": wrb_class if len(wrb_distinct) <= 1 else f"{wrb_class} (+{len(wrb_distinct) - 1} more)",
            "wrb_common_name": WRB_INDIAN_NAMES.get(wrb_class) if wrb_class else None,
            "per_depth": {},
            "sampled_at": None,
            "source": f"mean of {len(samples)} grid samples ({live} live SoilGrids)",
        },
        "bearing_capacity": bearing,
        "hazards": {
            "liquefaction_index": worst_hazards["liquefaction_index"],
            "liquefaction_label": worst_hazards["liquefaction_label"],
        },
        "meta": {
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "disclaimer": "Worst-case seismic/liquefaction across the selection; composition is the grid mean.",
        },
    }


# =============================================================================
# Environmental Dynamics (weather page)
# =============================================================================

GLOBAL_STATIONS = [
    {"name": "NEW DELHI", "lat": 28.61, "lon": 77.21},
    {"name": "MUMBAI", "lat": 19.08, "lon": 72.88},
    {"name": "CHENNAI", "lat": 13.08, "lon": 80.27},
    {"name": "KOLKATA", "lat": 22.57, "lon": 88.36},
    {"name": "GUWAHATI", "lat": 26.14, "lon": 91.74},
    {"name": "BENGALURU", "lat": 12.97, "lon": 77.59},
    {"name": "DUBAI", "lat": 25.20, "lon": 55.27},
    {"name": "SINGAPORE", "lat": 1.35, "lon": 103.82},
    {"name": "TOKYO", "lat": 35.68, "lon": 139.69},
    {"name": "LONDON", "lat": 51.51, "lon": -0.13},
    {"name": "NEW YORK", "lat": 40.71, "lon": -74.01},
    {"name": "SAO PAULO", "lat": -23.55, "lon": -46.63},
    {"name": "SYDNEY", "lat": -33.87, "lon": 151.21},
    {"name": "CAPE TOWN", "lat": -33.92, "lon": 18.42},
    {"name": "MOSCOW", "lat": 55.76, "lon": 37.62},
    {"name": "ANCHORAGE", "lat": 61.22, "lon": -149.90},
]
INDIA_STATIONS = GLOBAL_STATIONS[:6]
# Anomaly cards compare against 30-year ERA5 normals — one heavy archive call
# per station (cached for the process lifetime), so keep this subset small.
ANOMALY_STATIONS = [s for s in GLOBAL_STATIONS
                    if s["name"] in ("NEW DELHI", "MUMBAI", "LONDON", "TOKYO", "NEW YORK", "SINGAPORE")]


async def _log_weather_event(lat: float, lon: float, weather: dict, shear: dict,
                             carbonation: dict, thermal: dict | None) -> None:
    """Append to the Supabase time-series ledger; silently no-ops without it."""
    import os
    if not os.environ.get("DATABASE_URL"):
        return
    try:
        from sqlalchemy import text
        from .regions import _get_engine
        stmt = text(
            "INSERT INTO weather_events (lat, lon, temperature_c, relative_humidity_pct,"
            " wind_speed_kmh, wind_shear_kn_m2, carbonation_label, carbonation_multiplier,"
            " thermal_swing_c) VALUES (:lat, :lon, :t, :rh, :ws, :q, :cl, :cm, :sw)"
        )
        async with _get_engine().begin() as conn:
            await conn.execute(stmt, {
                "lat": lat, "lon": lon,
                "t": weather.get("temperature_c"),
                "rh": weather.get("relative_humidity_pct"),
                "ws": weather.get("wind_speed_kmh"),
                "q": shear["dynamic_pressure_kn_m2"],
                "cl": carbonation["risk_label"],
                "cm": carbonation["ph_degradation_multiplier"],
                "sw": thermal["swing_c"] if thermal else None,
            })
    except Exception as exc:
        logger.warning("weather ledger insert failed: %s", exc)


@app.get("/api/v1/environment/stress/")
async def environment_stress(
    lat: float = Query(..., ge=-90.0, le=90.0, description="Latitude (WGS84)"),
    lon: float = Query(..., ge=-180.0, le=180.0, description="Longitude (WGS84)"),
) -> dict:
    """Task 3: live meteorology -> structural stress diagnostics for a site."""
    started = time.perf_counter()
    try:
        weather = await fetch_current_weather(lat, lon, client=app.state.http)
    except OpenMeteoError as exc:
        raise HTTPException(status_code=502, detail=f"Weather provider unavailable: {exc}")

    shear = calculate_wind_shear(weather["wind_speed_kmh"] or 0.0)
    carbonation = evaluate_carbonation_risk(
        weather["temperature_c"], weather["relative_humidity_pct"]
    )
    thermal = thermal_loading(weather["hourly_24h"]["temperature_c"])
    monsoon = monsoon_outlook(weather["daily_7d"]["precipitation_sum_mm"])
    wet_bulb = wet_bulb_c(weather["temperature_c"], weather["relative_humidity_pct"])

    # Live-vs-30-year anomaly (best effort; None until the archive call lands)
    normal = await fetch_monthly_normal(lat, lon, client=app.state.http)
    anomaly = round(weather["temperature_c"] - normal, 2) if normal is not None else None

    # Hourly wind -> dynamic pressure series for the analytics bar chart
    pressure_series = [
        calculate_wind_shear(w)["dynamic_pressure_kn_m2"]
        for w in weather["hourly_24h"]["wind_speed_kmh"]
    ]

    await _log_weather_event(lat, lon, weather, shear, carbonation, thermal)

    return {
        "query": {"lat": lat, "lon": lon},
        "meteorology": {
            "temperature_c": weather["temperature_c"],
            "relative_humidity_pct": weather["relative_humidity_pct"],
            "wind_speed_kmh": weather["wind_speed_kmh"],
            "wind_direction_deg": weather["wind_direction_deg"],
            "wind_gusts_kmh": weather["wind_gusts_kmh"],
            "surface_pressure_hpa": weather["surface_pressure_hpa"],
            "observed_at": weather["observed_at"],
            "source": weather["source"],
        },
        "structural": {
            "wind_shear": shear,
            "wind_pressure_series_24h_kn_m2": pressure_series,
            "carbonation": carbonation,
            "thermal_loading_24h": thermal,
            "wet_bulb_c": wet_bulb,
            "monsoon_outlook_7d": monsoon,
            "temperature_anomaly_c": anomaly,
            "monthly_normal_c": normal,
        },
        "meta": {
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "disclaimer": "Screening-level estimate; not a substitute for site-specific wind tunnel / durability studies.",
        },
    }


@app.get("/api/v1/environment/stress/area/")
async def environment_stress_area(
    min_lat: float = Query(..., ge=-90.0, le=90.0),
    min_lon: float = Query(..., ge=-180.0, le=180.0),
    max_lat: float = Query(..., ge=-90.0, le=90.0),
    max_lon: float = Query(..., ge=-180.0, le=180.0),
) -> dict:
    """Aggregate environmental stress for a drag-selected bounding box.

    Samples a 3x3 grid concurrently. Means describe the climate of the area;
    the structural metrics are deliberately worst-case across the samples —
    the governing values for design.
    """
    started = time.perf_counter()
    if max_lat < min_lat or max_lon < min_lon:
        raise HTTPException(status_code=400, detail="max_lat/max_lon must be >= min_lat/min_lon.")

    def spread(lo: float, hi: float) -> list[float]:
        if hi <= lo:
            return [lo]
        return [lo + (hi - lo) * i / (AREA_GRID_N - 1) for i in range(AREA_GRID_N)]

    points = [(la, lo) for la in spread(min_lat, max_lat) for lo in spread(min_lon, max_lon)]
    raw = await asyncio.gather(
        *(fetch_current_weather(la, lo, client=app.state.http) for la, lo in points),
        return_exceptions=True,
    )
    samples = [w for w in raw if isinstance(w, dict)]
    if not samples:
        raise HTTPException(status_code=502, detail="Weather provider unavailable for this area.")

    def mean(key: str) -> float | None:
        vals = [s[key] for s in samples if s.get(key) is not None]
        return round(sum(vals) / len(vals), 1) if vals else None

    temps = [s["temperature_c"] for s in samples]
    windiest = max(samples, key=lambda s: s["wind_speed_kmh"] or 0.0)
    shear = calculate_wind_shear(windiest["wind_speed_kmh"] or 0.0)
    carbonations = [
        evaluate_carbonation_risk(s["temperature_c"], s["relative_humidity_pct"])
        for s in samples
    ]
    worst_carb = max(carbonations, key=lambda c: c["ph_degradation_multiplier"])
    thermals = [t for t in (thermal_loading(s["hourly_24h"]["temperature_c"]) for s in samples) if t]
    worst_thermal = max(thermals, key=lambda t: t["swing_c"]) if thermals else None
    monsoons = [monsoon_outlook(s["daily_7d"]["precipitation_sum_mm"]) for s in samples]
    worst_monsoon = max(monsoons, key=lambda m: m["total_7d_mm"])
    wet_bulbs = [wet_bulb_c(s["temperature_c"], s["relative_humidity_pct"]) for s in samples]
    pressure_series = [
        calculate_wind_shear(w)["dynamic_pressure_kn_m2"]
        for w in windiest["hourly_24h"]["wind_speed_kmh"]
    ]

    return {
        "query": {
            "bbox": {"min_lat": min_lat, "min_lon": min_lon, "max_lat": max_lat, "max_lon": max_lon},
            "samples_requested": len(points),
            "samples_ok": len(samples),
        },
        "meteorology": {
            "temperature_c": mean("temperature_c"),
            "temperature_range_c": [round(min(temps), 1), round(max(temps), 1)],
            "relative_humidity_pct": mean("relative_humidity_pct"),
            "wind_speed_kmh": windiest["wind_speed_kmh"],       # governing (max) wind
            "wind_direction_deg": windiest["wind_direction_deg"],
            "wind_gusts_kmh": windiest["wind_gusts_kmh"],
            "surface_pressure_hpa": mean("surface_pressure_hpa"),
            "observed_at": windiest["observed_at"],
            "source": f"open_meteo_forecast (mean of {len(samples)} grid samples; wind is area max)",
        },
        "structural": {
            "wind_shear": shear,
            "wind_pressure_series_24h_kn_m2": pressure_series,
            "carbonation": worst_carb,
            "thermal_loading_24h": worst_thermal,
            "wet_bulb_c": max(wet_bulbs),
            "monsoon_outlook_7d": worst_monsoon,
            "temperature_anomaly_c": None,
            "monthly_normal_c": None,
        },
        "meta": {
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "disclaimer": "Worst-case structural metrics across the selection; meteorology is the grid mean.",
        },
    }


@app.get("/api/v1/environment/global/")
async def environment_global() -> dict:
    """Aggregation feed for the weather dashboard: metric cards, the wind
    vector grid for the global schematic, India monsoon diagnostics and
    derived catastrophic-event monitoring."""
    started = time.perf_counter()
    try:
        vectors = await fetch_wind_grid(GLOBAL_STATIONS, client=app.state.http)
    except OpenMeteoError as exc:
        raise HTTPException(status_code=502, detail=f"Weather provider unavailable: {exc}")

    # India regional detail (7-day dailies) + 30-year anomaly normals, concurrently
    india_task = asyncio.gather(
        *(fetch_current_weather(s["lat"], s["lon"], client=app.state.http) for s in INDIA_STATIONS),
        return_exceptions=True,
    )
    normals_task = asyncio.gather(
        *(fetch_monthly_normal(s["lat"], s["lon"], client=app.state.http) for s in ANOMALY_STATIONS),
        return_exceptions=True,
    )
    india_raw, normals_raw = await asyncio.gather(india_task, normals_task)

    # --- Card 1: global average deviation from the 30-year monthly normal
    deltas = []
    for station, normal in zip(ANOMALY_STATIONS, normals_raw):
        if isinstance(normal, (int, float)):
            vec = next((v for v in vectors if v["name"] == station["name"]), None)
            if vec and vec["temperature_c"] is not None:
                deltas.append(vec["temperature_c"] - normal)
    avg_delta_t = round(sum(deltas) / len(deltas), 2) if deltas else None

    # --- Card 2: share of monitored stations inside the 50-70% RH
    #     carbonation acceleration band
    rh_vals = [v["relative_humidity_pct"] for v in vectors if v["relative_humidity_pct"] is not None]
    in_band = sum(1 for rh in rh_vals if 50.0 <= rh <= 70.0)
    humidity_stress = round(100.0 * in_band / len(rh_vals), 1) if rh_vals else None

    # --- India regional diagnostics + card 3 (worst wet bulb) + events
    india = []
    events = []
    for station, w in zip(INDIA_STATIONS, india_raw):
        if not isinstance(w, dict):
            continue
        monsoon = monsoon_outlook(w["daily_7d"]["precipitation_sum_mm"])
        thermal = thermal_loading(w["hourly_24h"]["temperature_c"])
        wb = wet_bulb_c(w["temperature_c"], w["relative_humidity_pct"])
        india.append({
            "city": station["name"],
            "temperature_c": w["temperature_c"],
            "relative_humidity_pct": w["relative_humidity_pct"],
            "wet_bulb_c": wb,
            "monsoon": monsoon,
            "thermal_loading_24h": thermal,
            "daily_precip_7d_mm": w["daily_7d"]["precipitation_sum_mm"],
        })
        gust_peak = max((g for g in w["daily_7d"]["wind_gusts_max_kmh"] if g is not None), default=0.0)
        if gust_peak >= 90:
            events.append({"title": f"High Wind Watch — {station['name'].title()}",
                           "severity": "Lvl 3 Gusts", "level": "error"})
        if monsoon["label"] == "SEVERE SURGE":
            events.append({"title": f"Monsoon Surge — {station['name'].title()}",
                           "severity": "Severe Precip", "level": "error"})
        elif monsoon["label"] == "ACTIVE SURGE":
            events.append({"title": f"Monsoon Surge — {station['name'].title()}",
                           "severity": "Active Monitoring", "level": "warn"})
        if wb >= 31.0:
            events.append({"title": f"Wet Bulb Emergency — {station['name'].title()}",
                           "severity": f"{wb}°C WBT", "level": "error"})
        elif w["temperature_c"] >= 43.0:
            events.append({"title": f"Extreme Heat — {station['name'].title()}",
                           "severity": f"{w['temperature_c']}°C", "level": "warn"})

    wet_bulb_max = max((c["wet_bulb_c"] for c in india), default=None)

    pressures = [v["surface_pressure_hpa"] for v in vectors if v["surface_pressure_hpa"] is not None]
    winds = [v["wind_speed_kmh"] for v in vectors if v["wind_speed_kmh"] is not None]
    temps = [v["temperature_c"] for v in vectors if v["temperature_c"] is not None]

    return {
        "cards": {
            "avg_delta_t_c": avg_delta_t,
            "humidity_stress_index": humidity_stress,
            "wet_bulb_max_india_c": wet_bulb_max,
            "active_events": len(events),
        },
        "vectors": vectors,
        "legend": {
            "mean_wind_ms": round(sum(winds) / len(winds) / 3.6, 1) if winds else None,
            "mean_temperature_c": round(sum(temps) / len(temps), 1) if temps else None,
            "mean_pressure_hpa": round(sum(pressures) / len(pressures), 1) if pressures else None,
        },
        "india": india,
        "events": events,
        "meta": {
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "stations": len(vectors),
            "source": "open_meteo",
        },
    }
