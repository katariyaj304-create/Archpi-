"""Tests for the Feature 2 degradation hazard engine.

Physical models are checked against hand-computed reference values
(Correns equation, Faraday penetration rate) and for the right qualitative
behaviour (monotonicity, saturation, determinism); the API is checked for
its validated contract.

Run from app/:  python -m pytest tests/test_hazard_engine.py -q
"""

import math

import numpy as np
import pytest
from fastapi.testclient import TestClient

from forensic_backend.hazard_engine import HazardMatrixEngine, MaterialProfile
from forensic_backend.hazard_types import HazardState
from forensic_backend.main import app
from forensic_backend.microclimate import MicroclimateSeries
from forensic_backend.rust_jacking import RustJackingModel
from forensic_backend.salt_stress import SALT_REGISTRY, CrystallizationStressModel
from forensic_backend.synthetic import generate_climate

client = TestClient(app)


# ── Salt crystallization (Correns) ───────────────────────────────────────

def test_correns_reference_value():
    # Halite, 20 C, S=2: P = R*293.15/2.702e-5 * ln 2 = 62.53 MPa
    model = CrystallizationStressModel.for_salt("halite")
    expected = 8.314462618 * 293.15 / 2.702e-5 * math.log(2.0) / 1e6
    assert model.pressure_mpa(20.0, 2.0) == pytest.approx(expected, rel=1e-9)
    assert expected == pytest.approx(62.5, abs=0.1)


def test_correns_undersaturated_is_zero():
    model = CrystallizationStressModel.for_salt("halite")
    assert model.pressure_mpa(20.0, 1.0) == 0.0
    assert model.pressure_mpa(20.0, 0.5) == 0.0


def test_correns_critical_flag_and_validation():
    model = CrystallizationStressModel.for_salt("halite")
    result = model.evaluate(20.0, 2.0, tensile_threshold_mpa=3.0)
    assert result.state is HazardState.CRITICAL          # 62.5 MPa >> 3 MPa
    assert model.evaluate(20.0, 1.0, 3.0).state is HazardState.NEGLIGIBLE
    with pytest.raises(ValueError, match="supersaturation"):
        model.pressure_mpa(20.0, -1.0)
    with pytest.raises(ValueError, match="absolute zero"):
        model.pressure_mpa(-300.0, 2.0)
    with pytest.raises(ValueError, match="unknown salt"):
        CrystallizationStressModel.for_salt("epsomite")


def test_correns_larger_molar_volume_gives_lower_pressure():
    halite = CrystallizationStressModel.for_salt("halite")
    mirabilite = CrystallizationStressModel.for_salt("mirabilite")
    assert mirabilite.pressure_mpa(20.0, 2.0) < halite.pressure_mpa(20.0, 2.0)


# ── Rust jacking (Faraday + Lamé) ────────────────────────────────────────

def test_faraday_penetration_constant():
    # Canonical rate for iron: 1 uA/cm^2 ~ 11.6 um/year
    loss_mm = RustJackingModel.section_loss_mm(1.0, 1.0)
    assert loss_mm * 1000.0 == pytest.approx(11.6, rel=0.02)


def test_rust_jacking_stress_growth_and_critical():
    model = RustJackingModel()
    # Passive corrosion for 2 years: rust still fits inside the porous
    # interfacial zone, so no pressure develops at all
    mild = model.evaluate(20.0, 0.05, 2.0)
    severe = model.evaluate(20.0, 2.0, 150.0)  # active, 150 years
    assert mild.hoop_stress_mpa == 0.0
    assert mild.state is HazardState.NEGLIGIBLE
    assert severe.hoop_stress_mpa > mild.hoop_stress_mpa
    assert severe.state is HazardState.CRITICAL
    assert severe.section_loss_mm == pytest.approx(2.0 * 11.6e-3 * 150.0, rel=0.02)


def test_rust_oxide_expansion_ordering():
    model = RustJackingModel()
    fe_oh3 = model.evaluate(20.0, 1.0, 100.0, oxide="Fe(OH)3")
    fe3_o4 = model.evaluate(20.0, 1.0, 100.0, oxide="Fe3O4")
    assert fe_oh3.expansion_ratio == 4.2 and fe3_o4.expansion_ratio == 2.1
    assert fe_oh3.hoop_stress_mpa > fe3_o4.hoop_stress_mpa


def test_rust_jacking_extreme_inputs():
    model = RustJackingModel()
    # Full section consumption is capped at the bar radius, not unbounded
    total = model.evaluate(10.0, 100.0, 500.0)
    assert total.section_loss_mm == pytest.approx(5.0)
    with pytest.raises(ValueError, match="diameter"):
        model.evaluate(-5.0, 1.0, 10.0)
    with pytest.raises(ValueError, match="unknown oxide"):
        model.evaluate(20.0, 1.0, 10.0, oxide="FeO")
    assert model.evaluate(20.0, 0.0, 100.0).hoop_stress_mpa == 0.0


# ── Microclimate series ──────────────────────────────────────────────────

def test_salt_cycle_counting_square_wave():
    # RH alternates 3 days wet (85 %) / 3 days dry (60 %) across halite's
    # 75.3 % equilibrium: 5 dissolution->crystallization transitions
    rh = ([85.0] * 72 + [60.0] * 72) * 5
    temp = [15.0] * len(rh)
    series = MicroclimateSeries(rh, temp)
    stats = series.salt_cycles(SALT_REGISTRY["halite"])
    assert stats.crystallization_events == 5
    # S capped at halite's typical max (75.3/60 = 1.255 < 1.6 cap)
    assert stats.supersaturation_estimate == pytest.approx(75.3 / 60.0, rel=1e-3)


def test_freeze_thaw_counting_with_wetness():
    # Daily wave dipping to -4 C; RH 90 % (wet) for 10 days then 50 % (dry)
    hours = np.arange(20 * 24)
    temp = -4.0 * np.maximum(0.0, np.sin(2 * np.pi * hours / 24.0)) + 2.0 * (
        np.sin(2 * np.pi * hours / 24.0) <= 0
    )
    rh = np.where(hours < 10 * 24, 90.0, 50.0)
    series = MicroclimateSeries(rh.tolist(), temp.tolist())
    stats = series.freeze_thaw_cycles()
    assert stats.total_cycles == pytest.approx(20, abs=1)
    assert stats.wet_cycles == pytest.approx(10, abs=1)
    assert stats.mean_freeze_minimum_c == pytest.approx(-4.0, abs=0.3)


def test_series_validation():
    with pytest.raises(ValueError, match="length mismatch"):
        MicroclimateSeries([50.0] * 48, [10.0] * 47)
    with pytest.raises(ValueError, match="0-100"):
        MicroclimateSeries([120.0] * 48, [10.0] * 48)
    with pytest.raises(ValueError, match="-60..60"):
        MicroclimateSeries([50.0] * 48, [999.0] * 48)
    with pytest.raises(ValueError, match="48"):
        MicroclimateSeries([50.0] * 10, [10.0] * 10)


# ── Scoring engine ───────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def year_series():
    rh, temp = generate_climate(days=365, seed=42)
    return MicroclimateSeries(rh, temp)


@pytest.fixture(scope="module")
def engine():
    return HazardMatrixEngine(MaterialProfile(porosity_pct=22.0, tensile_strength_mpa=3.0))


def test_scores_bounded_and_states_consistent(year_series, engine):
    salts = engine.assess_all_salts(year_series)
    _, governing = engine.governing_salt(salts)
    rust = engine.assess_rust(year_series, diameter_mm=20.0, exposure_years=150.0)
    freeze = engine.assess_freeze_thaw(year_series)
    for row in engine.matrix(governing, rust, freeze):
        assert 0.0 <= row.score <= 10.0
        assert isinstance(row.state, HazardState)


def test_porosity_increases_salt_risk(year_series):
    dense = HazardMatrixEngine(MaterialProfile(porosity_pct=5.0, tensile_strength_mpa=3.0))
    porous = HazardMatrixEngine(MaterialProfile(porosity_pct=30.0, tensile_strength_mpa=3.0))
    _, low = dense.governing_salt(dense.assess_all_salts(year_series))
    _, high = porous.governing_salt(porous.assess_all_salts(year_series))
    assert high.score >= low.score


def test_measured_i_corr_takes_priority(year_series, engine):
    measured = engine.assess_rust(year_series, 20.0, 150.0, i_corr_ua_cm2=5.0)
    estimated = engine.assess_rust(year_series, 20.0, 150.0, i_corr_ua_cm2=None)
    assert measured.i_corr_source == "measured"
    assert measured.result.i_corr_ua_cm2 == 5.0
    assert estimated.i_corr_source == "estimated_from_time_of_wetness"
    assert 0.1 <= estimated.result.i_corr_ua_cm2 <= 2.5


def test_engine_is_deterministic(year_series, engine):
    a = engine.assess_all_salts(year_series)
    b = engine.assess_all_salts(year_series)
    assert {k: v.score for k, v in a.items()} == {k: v.score for k, v in b.items()}


# ── API contract ─────────────────────────────────────────────────────────

def test_evaluate_endpoint_round_trip():
    demo = client.get("/api/v1/hazards/demo", params={"days": 365}).json()
    res = client.post("/api/v1/hazards/evaluate", json=demo)
    assert res.status_code == 200
    body = res.json()
    assert body["meta"]["samples"] == 365 * 24
    hazards = {row["hazard"]: row for row in body["hazard_matrix"]}
    assert set(hazards) == {"salt_crystallization", "rust_jacking", "freeze_thaw"}
    for row in hazards.values():
        assert 0.0 <= row["score"] <= 10.0
        assert row["state"] in {"NEGLIGIBLE", "MODERATE", "HIGH", "CRITICAL"}
    assert len(body["salt_assessments"]) == len(SALT_REGISTRY)
    assert body["meta"]["governing_salt"] in SALT_REGISTRY
    # Deterministic: same request, same matrix
    res2 = client.post("/api/v1/hazards/evaluate", json=demo)
    assert res2.json()["hazard_matrix"] == body["hazard_matrix"]


def test_evaluate_endpoint_validation():
    base = {"material": {"porosity_pct": 22.0, "tensile_strength_mpa": 3.0}}
    res = client.post("/api/v1/hazards/evaluate", json={
        **base, "relative_humidity_pct": [50.0] * 48, "temperature_c": [10.0] * 47,
    })
    assert res.status_code == 422  # length mismatch
    res = client.post("/api/v1/hazards/evaluate", json={
        **base, "relative_humidity_pct": [150.0] * 48, "temperature_c": [10.0] * 48,
    })
    assert res.status_code == 422  # RH out of physical range


def test_crystallization_pressure_endpoint():
    res = client.post("/api/v1/hazards/crystallization-pressure", json={
        "salt_id": "halite", "temperature_c": 20.0, "supersaturation": 2.0,
        "tensile_threshold_mpa": 3.0,
    })
    assert res.status_code == 200
    body = res.json()
    assert body["pressure_mpa"] == pytest.approx(62.5, abs=0.1)
    assert body["state"] == "CRITICAL"


def test_rust_jacking_endpoint():
    res = client.post("/api/v1/hazards/rust-jacking", json={
        "diameter_mm": 20.0, "i_corr_ua_cm2": 2.0, "exposure_years": 150.0,
    })
    assert res.status_code == 200
    body = res.json()
    assert body["section_loss_mm"] == pytest.approx(3.48, rel=0.02)
    assert body["state"] == "CRITICAL"
    res = client.post("/api/v1/hazards/rust-jacking", json={
        "diameter_mm": 20.0, "i_corr_ua_cm2": -1.0, "exposure_years": 150.0,
    })
    assert res.status_code == 422
