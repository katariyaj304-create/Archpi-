"""Tests for the Forensic Lab XRD engine.

Round-trips synthetic diffractograms (built from the same COD line matrix)
through the full pipeline and checks phase identification, Scherrer size
recovery, RIR weights, and the FastAPI contract.

Run from app/:  python -m pytest tests/test_xrd_engine.py -q
"""

import math

import numpy as np
import pytest
from fastapi.testclient import TestClient

from forensic_backend.conditioning import condition, ingest
from forensic_backend.main import app, run_analysis
from forensic_backend.peaks import detect_peaks, fit_peaks
from forensic_backend.phases import match_phases
from forensic_backend.quant import (
    average_crystallite_size_nm,
    crystallite_sizes,
    rir_weight_fractions,
    scherrer_size_nm,
    silica_ratio_pct,
)
from forensic_backend.schemas import XRDAnalysisRequest
from forensic_backend.synthetic import generate_pattern


@pytest.fixture(scope="module")
def mortar_pattern():
    return generate_pattern(
        {"quartz": 0.5, "calcite": 0.4, "gypsum": 0.1},
        crystallite_nm=40.0,
        seed=7,
    )


@pytest.fixture(scope="module")
def conditioned(mortar_pattern):
    x, y = mortar_pattern
    return condition(x, y)


# ── Ingestion & conditioning ─────────────────────────────────────────────

def test_ingest_rejects_bad_input():
    with pytest.raises(ValueError, match="length mismatch"):
        ingest([1.0] * 60, [1.0] * 59)
    with pytest.raises(ValueError, match="at least 50"):
        ingest([1.0, 2.0], [1.0, 2.0])
    bad = list(np.linspace(5, 70, 60))
    with pytest.raises(ValueError, match="finite"):
        ingest(bad, [float("nan")] * 60)


def test_ingest_sorts_descending_scans():
    x = list(np.linspace(70, 5, 100))
    y = list(range(100))
    xs, ys = ingest(x, y)
    assert xs[0] < xs[-1]
    assert ys[0] == 99  # intensity reordered together with angles


def test_baseline_removed_and_peaks_preserved(conditioned):
    # Background (~500 counts here) must be substantially removed in valleys:
    # ALS hugs the lower noise envelope, so a small positive residual remains
    valley = (conditioned.two_theta > 15) & (conditioned.two_theta < 17)
    residual = np.median(conditioned.processed[valley])
    original = np.median(conditioned.raw[valley])
    assert residual < 0.1 * original
    # Savitzky-Golay must not crush the quartz 26.64 peak (<10% loss vs raw-baseline)
    near_quartz = np.abs(conditioned.two_theta - 26.64) < 0.2
    assert conditioned.processed[near_quartz].max() >= 0.9 * conditioned.corrected[near_quartz].max()


# ── Peak detection & fitting ─────────────────────────────────────────────

def test_detects_all_primary_reflections(conditioned):
    peaks = detect_peaks(conditioned)
    found = [p.two_theta for p in peaks]
    for expected in (26.64, 29.41, 11.63, 20.86, 20.72):
        assert any(abs(f - expected) < 0.15 for f in found), f"missed line at {expected}"


def test_fit_recovers_bragg_angle_and_fwhm(conditioned):
    peaks = detect_peaks(conditioned)
    fitted = fit_peaks(conditioned, peaks)
    assert fitted, "no peaks were fitted"
    quartz = min(fitted, key=lambda f: abs(f.two_theta - 26.64))
    assert abs(quartz.two_theta - 26.64) < 0.05
    assert quartz.r_squared > 0.95
    # Synthetic FWHM at 26.64 deg for 40 nm crystallites: ~0.213 deg
    theta = math.radians(26.64 / 2)
    expected_fwhm = math.degrees(0.94 * 0.15418 / (40.0 * math.cos(theta)))
    assert abs(quartz.fwhm_deg - expected_fwhm) < 0.05


# ── Phase matching ───────────────────────────────────────────────────────

def test_matches_expected_phases(conditioned):
    peaks = detect_peaks(conditioned)
    fitted = fit_peaks(conditioned, peaks)
    matches = {m.phase_id: m for m in match_phases(peaks, fitted)}
    assert matches["quartz"].confidence_pct > 60
    assert matches["calcite"].confidence_pct > 60
    assert matches["gypsum"].confidence_pct > 40


def test_pure_quartz_rejects_other_phases():
    x, y = generate_pattern({"quartz": 1.0}, crystallite_nm=40.0, seed=3)
    pattern = condition(x, y)
    peaks = detect_peaks(pattern)
    fitted = fit_peaks(pattern, peaks)
    matches = {m.phase_id: m for m in match_phases(peaks, fitted)}
    assert matches["quartz"].confidence_pct > 70
    for other in ("calcite", "gypsum"):
        if other in matches:
            assert matches[other].confidence_pct < 25


# ── Quantitative calculations ────────────────────────────────────────────

def test_scherrer_equation_reference_value():
    # beta = 0.2 deg at 2-theta = 26.64: tau = K*lambda/(beta*cos(theta))
    beta_rad = math.radians(0.2)
    expected = 0.94 * 0.15418 / (beta_rad * math.cos(math.radians(13.32)))
    assert scherrer_size_nm(0.2, 26.64) == pytest.approx(expected)


def test_crystallite_size_recovered(conditioned):
    peaks = detect_peaks(conditioned)
    fitted = fit_peaks(conditioned, peaks)
    estimates = crystallite_sizes(fitted, instrumental_fwhm_deg=0.0)
    avg = average_crystallite_size_nm(estimates)
    assert avg is not None
    assert avg == pytest.approx(40.0, rel=0.15)  # ground truth of the fixture


def test_rir_weights_ordering_and_sum(conditioned):
    peaks = detect_peaks(conditioned)
    fitted = fit_peaks(conditioned, peaks)
    matches = match_phases(peaks, fitted)
    weights = rir_weight_fractions(matches)
    assert weights
    assert sum(w.weight_pct for w in weights) == pytest.approx(100.0, abs=0.5)
    by_id = {w.phase_id: w.weight_pct for w in weights}
    # 50/40/10 generation with RIR correction: quartz and calcite dominate
    assert by_id["quartz"] > by_id["gypsum"]
    assert by_id["calcite"] > by_id["gypsum"]
    assert silica_ratio_pct(weights, matches) == by_id["quartz"]


# ── API contract ─────────────────────────────────────────────────────────

client = TestClient(app)


def test_analyze_endpoint_full_payload(mortar_pattern):
    x, y = mortar_pattern
    res = client.post(
        "/api/v1/xrd/analyze",
        json={"two_theta": x, "intensity": y, "sample_id": "ATH-22-NNA"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["meta"]["sample_id"] == "ATH-22-NNA"
    assert len(body["conditioned"]["processed"]) == len(x)
    assert body["meta"]["n_peaks_detected"] >= 5
    top = body["phase_matches"][0]
    assert top["phase_id"] in ("quartz", "calcite")
    quant = body["quantitative"]
    assert quant["avg_crystallite_size_nm"] is not None
    assert 0 <= quant["silica_ratio_pct"] <= 100
    assert 0 <= quant["calcium_content_pct"] <= 100
    # The synthetic hump dominates by area; crystallinity lands mid-range
    assert 10 < quant["crystallinity_pct"] < 90


def test_analyze_endpoint_validates_input():
    res = client.post(
        "/api/v1/xrd/analyze",
        json={"two_theta": [1, 2, 3], "intensity": [1, 2, 3]},
    )
    assert res.status_code == 422  # below 50-point minimum
    res = client.post(
        "/api/v1/xrd/analyze",
        json={"two_theta": list(range(60)), "intensity": list(range(59))},
    )
    assert res.status_code == 422  # length mismatch


def test_demo_endpoint_round_trip():
    res = client.get("/api/v1/xrd/demo", params={"quartz": 0.7, "calcite": 0.3, "gypsum": 0.0})
    assert res.status_code == 200
    demo = res.json()
    res2 = client.post(
        "/api/v1/xrd/analyze",
        json={"two_theta": demo["two_theta"], "intensity": demo["intensity"]},
    )
    assert res2.status_code == 200
    top = res2.json()["phase_matches"][0]
    assert top["phase_id"] == "quartz"


def test_request_schema_defaults():
    req = XRDAnalysisRequest(two_theta=list(np.linspace(5, 70, 100)), intensity=[1.0] * 100)
    assert req.matching.tolerance_deg == 0.25
    assert req.peaks.max_fitted_peaks == 8
