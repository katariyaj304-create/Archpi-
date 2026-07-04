"""Tests for the Feature 6 radiocarbon calibration engine.

Covers the calibration curve, the Bayesian calibration math (HPD mass,
range ordering, monotonic curve behaviour), out-of-range handling and the
API contract.

Run from app/:  python -m pytest tests/test_radiocarbon_engine.py -q
"""

import pytest
from fastapi.testclient import TestClient

from forensic_backend.main import app
from forensic_backend.radiocarbon import (
    CalibrationOutOfRangeError,
    RadiocarbonCalibrator,
    calibration_curve,
)

client = TestClient(app)


# ── Calibration curve ────────────────────────────────────────────────────

def test_curve_is_one_year_resolution_and_ordered():
    curve = calibration_curve()
    assert curve.cal_years[0] == -700 and curve.cal_years[-1] == 1950
    assert curve.cal_years.size == 1950 - (-700) + 1
    # 14C age rises going back in time (older calendar => larger BP).
    assert curve.c14_bp[0] > curve.c14_bp[-1]


# ── Calibration math ─────────────────────────────────────────────────────

def test_demo_age_calibrates_to_gothic_window():
    result = RadiocarbonCalibrator().calibrate(760.0, 25.0)
    assert 1240 <= result.median_year <= 1275
    span68 = result.hpd_68
    assert span68[0].start_year >= 1220
    assert span68[-1].end_year <= 1300


def test_hpd_masses_are_correct():
    result = RadiocarbonCalibrator().calibrate(760.0, 25.0)
    mass68 = sum(r.probability for r in result.hpd_68)
    mass95 = sum(r.probability for r in result.hpd_95)
    assert mass68 == pytest.approx(0.682, abs=0.03)
    assert mass95 == pytest.approx(0.954, abs=0.03)
    # 2-sigma always contains 1-sigma, so it spans at least as wide.
    assert result.hpd_95[0].start_year <= result.hpd_68[0].start_year
    assert result.hpd_95[-1].end_year >= result.hpd_68[-1].end_year


def test_ranges_are_sorted_and_disjoint():
    result = RadiocarbonCalibrator().calibrate(1200.0, 40.0)
    for ranges in (result.hpd_68, result.hpd_95):
        for a, b in zip(ranges, ranges[1:]):
            assert a.end_year < b.start_year


def test_older_age_gives_older_calendar_date():
    cal = RadiocarbonCalibrator()
    young = cal.calibrate(500.0, 25.0).median_year
    old = cal.calibrate(1500.0, 25.0).median_year
    assert old < young       # larger BP => earlier (older) calendar year


def test_larger_uncertainty_widens_the_interval():
    cal = RadiocarbonCalibrator()
    tight = cal.calibrate(760.0, 15.0)
    loose = cal.calibrate(760.0, 60.0)
    tight_span = tight.hpd_95[-1].end_year - tight.hpd_95[0].start_year
    loose_span = loose.hpd_95[-1].end_year - loose.hpd_95[0].start_year
    assert loose_span > tight_span


def test_intercept_near_median_for_monotonic_segment():
    result = RadiocarbonCalibrator().calibrate(760.0, 25.0)
    assert result.intercepts
    assert min(abs(i - result.median_year) for i in result.intercepts) <= 15


def test_zero_uncertainty_rejected():
    with pytest.raises(ValueError, match="uncertainty must be positive"):
        RadiocarbonCalibrator().calibrate(760.0, 0.0)


def test_out_of_range_age_raises():
    with pytest.raises(CalibrationOutOfRangeError):
        RadiocarbonCalibrator().calibrate(40000.0, 30.0)


def test_density_normalised():
    result = RadiocarbonCalibrator().calibrate(760.0, 25.0)
    assert sum(result.density) == pytest.approx(1.0, abs=1e-6)
    assert len(result.density) == len(result.cal_years)


# ── API contract ─────────────────────────────────────────────────────────

def test_demo_round_trip():
    demo = client.get("/api/v1/radiocarbon/demo").json()
    resp = client.post("/api/v1/radiocarbon/calibrate", json=demo)
    assert resp.status_code == 200
    body = resp.json()
    assert 1240 <= body["median_year"] <= 1275
    assert body["hpd_68"] and body["hpd_95"]
    assert body["meta"]["lab_code"] == "AeL-2207"
    assert body["meta"]["curve_id"].startswith("AETHEL-INTCAL")


def test_out_of_range_maps_to_422():
    resp = client.post("/api/v1/radiocarbon/calibrate", json={
        "radiocarbon_age_bp": 40000, "uncertainty_bp": 30,
    })
    assert resp.status_code == 422
    assert "outside the calibration curve" in resp.json()["detail"]["message"]


def test_curve_endpoint():
    body = client.get("/api/v1/radiocarbon/curve").json()
    assert body["cal_start_ad"] == -700 and body["cal_end_ad"] == 1950
    assert len(body["cal_years"]) == len(body["c14_bp"])


def test_calibration_is_deterministic():
    demo = client.get("/api/v1/radiocarbon/demo").json()
    first = client.post("/api/v1/radiocarbon/calibrate", json=demo).json()
    second = client.post("/api/v1/radiocarbon/calibrate", json=demo).json()
    first["meta"].pop("processing_ms")
    second["meta"].pop("processing_ms")
    assert first == second
