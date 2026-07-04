"""Tests for the Feature 4 Provenance Map Engine.

Covers the isotopic matcher (hand-checked sigma distances, partial panels,
implausible-sample flagging), the terrain cost surface (known-mode cells,
grid bounds), the least-cost router (maritime dominance, Nile corridor,
endpoint closure) and the API contract including the GeoJSON payload.

Run from app/:  python -m pytest tests/test_provenance_engine.py -q
"""

import math

import pytest
from fastapi.testclient import TestClient

from forensic_backend.isotopes import (
    QUARRY_REGISTRY,
    IsotopeMatcher,
    UnknownQuarryError,
)
from forensic_backend.main import app
from forensic_backend.routing import default_engine
from forensic_backend.terrain import BBOX, FRICTION, default_surface, haversine_km

client = TestClient(app)

ROME = {"lat": 41.89, "lon": 12.49, "name": "Rome"}


# ── Isotopic matching ────────────────────────────────────────────────────

def test_registry_is_sane():
    lon_min, lat_min, lon_max, lat_max = BBOX
    for q in QUARRY_REGISTRY.values():
        assert lat_min <= q.lat <= lat_max and lon_min <= q.lon <= lon_max
        assert q.delta13c_sd > 0 and q.delta18o_sd > 0 and q.sr8786_sd > 0
        assert 0.70 < q.sr8786 < 0.72


def test_exact_centroid_matches_with_zero_distance():
    q = QUARRY_REGISTRY["carrara"]
    best = IsotopeMatcher().match(q.delta13c, q.delta18o, q.sr8786)[0]
    assert best.quarry_id == "carrara"
    assert best.distance_sigma == 0.0
    assert best.confidence_pct > 90
    assert best.plausible


def test_one_sigma_offset_distance_is_hand_checkable():
    # Sample displaced +1 sd on every axis: d = sqrt(1+1+1) = sqrt(3).
    q = QUARRY_REGISTRY["paros"]
    best = IsotopeMatcher().match(
        q.delta13c + q.delta13c_sd,
        q.delta18o + q.delta18o_sd,
        q.sr8786 + q.sr8786_sd,
    )[0]
    assert best.quarry_id == "paros"
    assert best.distance_sigma == pytest.approx(math.sqrt(3.0), abs=1e-3)


def test_partial_panel_matches_on_two_axes():
    q = QUARRY_REGISTRY["pentelicon"]
    best = IsotopeMatcher().match(q.delta13c, q.delta18o, None)[0]
    assert best.quarry_id == "pentelicon"
    assert best.axes_used == ["delta13c", "delta18o"]
    assert "sr8786" not in best.z_scores


def test_single_axis_is_rejected():
    with pytest.raises(ValueError, match="two isotope systems"):
        IsotopeMatcher().match(2.0, None, None)


def test_alien_sample_is_flagged_implausible():
    best = IsotopeMatcher().match(15.0, 8.0, 0.7300)[0]
    assert not best.plausible
    assert best.consistency_p < 0.01


def test_matches_are_ranked_and_sum_to_one():
    matches = IsotopeMatcher().match(2.1, -2.0, 0.70785, top_k=10)
    assert [m.confidence_pct for m in matches] == sorted(
        (m.confidence_pct for m in matches), reverse=True
    )
    assert sum(m.confidence_pct for m in matches) == pytest.approx(100.0, abs=0.5)


def test_unknown_quarry_lookup_raises():
    with pytest.raises(UnknownQuarryError):
        IsotopeMatcher().get("atlantis")


# ── Terrain cost surface ─────────────────────────────────────────────────

def test_known_cells_classify_correctly():
    surface = default_surface()
    for lat, lon, want in [
        (36.5, 18.0, "sea"),        # Ionian
        (40.0, 12.0, "sea"),        # Tyrrhenian
        (46.0, 10.0, "mountain"),   # Alps
        (45.0, 9.5, "plain"),       # Po plain
        (25.5, 32.7, "river"),      # Nile valley
        (26.0, 10.0, "plain"),      # Sahara interior
    ]:
        assert surface.snap(lat, lon).mode == want, (lat, lon)


def test_friction_ordering_maritime_cheapest():
    assert FRICTION["sea"] < FRICTION["river"] < FRICTION["plain"] < FRICTION["mountain"]


def test_snap_outside_grid_raises():
    with pytest.raises(ValueError, match="outside the modelled"):
        default_surface().snap(55.0, -30.0)


def test_haversine_reference_value():
    # One degree of longitude at the equator: 2*pi*R/360 = 111.19 km.
    assert haversine_km(0.0, 0.0, 0.0, 1.0) == pytest.approx(111.19, abs=0.05)


# ── Least-cost routing ───────────────────────────────────────────────────

def test_carrara_to_rome_is_predominantly_maritime():
    route = default_engine().route(44.08, 10.10, ROME["lat"], ROME["lon"])
    land_km = route.km_by_mode.get("plain", 0) + route.km_by_mode.get("mountain", 0)
    assert route.km_by_mode["sea"] > land_km        # ship down the Tyrrhenian
    assert 300 < route.total_km < 900
    assert route.transshipments >= 1
    # The polyline is closed onto the true endpoints.
    assert route.coordinates[0] == (10.10, 44.08)
    assert route.coordinates[-1] == (ROME["lon"], ROME["lat"])


def test_aswan_to_rome_uses_the_nile_corridor():
    route = default_engine().route(24.09, 32.90, ROME["lat"], ROME["lon"])
    assert route.km_by_mode.get("river", 0) > 500   # barge down the Nile
    assert route.km_by_mode["sea"] > 1500           # then ship across the sea


def test_open_sea_crossing_avoids_land():
    # Athens -> Alexandria: an almost pure sea passage.
    route = default_engine().route(37.98, 23.73, 31.20, 29.92)
    assert route.km_by_mode["sea"] / route.total_km > 0.85


def test_route_cost_is_friction_weighted_not_shortest():
    # Carrara -> Rome overland is ~360 km direct, but the engine must pay
    # ~25x land friction, so the chosen route's cost stays far below a
    # hypothetical direct overland march.
    route = default_engine().route(44.08, 10.10, ROME["lat"], ROME["lon"])
    direct_km = haversine_km(44.08, 10.10, ROME["lat"], ROME["lon"])
    overland_cost = direct_km * FRICTION["plain"]
    assert route.total_cost < overland_cost
    assert route.total_km > direct_km               # detour accepted for cheap water


# ── API contract ─────────────────────────────────────────────────────────

def test_demo_round_trip_returns_valid_geojson():
    demo = client.get("/api/v1/provenance/demo").json()
    resp = client.post("/api/v1/provenance/analyze", json=demo)
    assert resp.status_code == 200
    body = resp.json()

    assert body["meta"]["best_quarry_id"] == "carrara"
    assert body["meta"]["plausible"] is True

    fc = body["geojson"]
    assert fc["type"] == "FeatureCollection"
    roles = [f["properties"]["role"] for f in fc["features"]]
    assert roles == ["origin_quarry", "destination", "trade_route"]

    origin, destination, trade = fc["features"]
    assert origin["geometry"]["type"] == "Point"
    assert origin["geometry"]["coordinates"] == [10.10, 44.08]
    assert origin["properties"]["confidence_pct"] > 50
    assert destination["geometry"]["coordinates"] == [12.49, 41.89]
    assert trade["geometry"]["type"] == "LineString"
    coords = trade["geometry"]["coordinates"]
    assert len(coords) >= 3
    assert coords[0] == [10.10, 44.08] and coords[-1] == [12.49, 41.89]
    assert trade["properties"]["total_km"] == body["route"]["total_km"]


def test_match_endpoint_contract():
    resp = client.post("/api/v1/provenance/match", json={
        "sample_id": "S1", "delta13c": 4.5, "delta18o": -3.0, "sr8786": 0.70855,
    })
    assert resp.status_code == 200
    assert resp.json()["matches"][0]["quarry_id"] == "paros"

    resp = client.post("/api/v1/provenance/match", json={"delta13c": 2.0})
    assert resp.status_code == 422


def test_route_endpoint_by_quarry_id_and_errors():
    resp = client.post("/api/v1/provenance/route", json={
        "source_quarry_id": "proconnesos", "destination": ROME,
    })
    assert resp.status_code == 200
    assert resp.json()["route"]["km_by_mode"]["sea"] > 1000  # Marmara -> Rome

    resp = client.post("/api/v1/provenance/route", json={
        "source_quarry_id": "atlantis", "destination": ROME,
    })
    assert resp.status_code == 422
    assert "atlantis" in resp.json()["detail"]["message"]


def test_destination_outside_grid_maps_to_422():
    demo = client.get("/api/v1/provenance/demo").json()
    demo["destination"] = {"lat": 55.0, "lon": -30.0, "name": "Atlantic"}
    resp = client.post("/api/v1/provenance/analyze", json=demo)
    assert resp.status_code == 422
    assert "outside the modelled" in resp.json()["detail"]["message"]


def test_terrain_endpoint_serves_router_geography():
    resp = client.get("/api/v1/provenance/terrain")
    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "FeatureCollection"
    assert body["bbox"] == list(BBOX)
    kinds = {f["properties"]["kind"] for f in body["features"]}
    assert kinds == {"land", "mountain", "river"}
    names = {f["properties"]["name"] for f in body["features"]}
    assert {"europe", "sicily", "alps", "nile"} <= names
    for f in body["features"]:
        ring = f["geometry"]["coordinates"][0]
        assert len(ring) >= 4 and ring[0] == ring[-1]  # closed valid rings


def test_quarry_registry_endpoint():
    resp = client.get("/api/v1/provenance/quarries")
    assert resp.status_code == 200
    ids = {q["quarry_id"] for q in resp.json()["quarries"]}
    assert {"carrara", "paros", "vesuvius", "aswan"} <= ids


def test_analysis_is_deterministic():
    demo = client.get("/api/v1/provenance/demo").json()
    first = client.post("/api/v1/provenance/analyze", json=demo).json()
    second = client.post("/api/v1/provenance/analyze", json=demo).json()
    first["meta"].pop("processing_ms")
    second["meta"].pop("processing_ms")
    assert first == second
