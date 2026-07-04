"""Tests for the Feature 5 dendrochronology crossdating engine.

Covers the detrending high-pass filter, the crossdating statistics
(known end-year recovery, t-value ordering, GLK), the sapwood felling
logic (bark edge / partial / heartwood-only) and the API contract.

Run from app/:  python -m pytest tests/test_dendro_engine.py -q
"""

import numpy as np
import pytest
from fastapi.testclient import TestClient

from forensic_backend.dendro import (
    OAK_SAPWOOD_MAX,
    OAK_SAPWOOD_MIN,
    T_THRESHOLD,
    analyze_sample,
    build_reference_chronology,
    detrend,
    estimate_felling,
    generate_core_sample,
    gleichlaeufigkeit,
)
from forensic_backend.main import app

client = TestClient(app)


# ── Detrending & statistics ──────────────────────────────────────────────

def test_detrend_removes_low_frequency_trend():
    # A pure exponential growth trend should flatten to ~1.0 after detrend.
    trend = 3.0 * np.exp(-np.arange(80) / 40.0) + 0.5
    idx = detrend(trend, window=7)
    assert np.allclose(idx, 1.0, atol=0.05)


def test_gleichlaeufigkeit_bounds():
    a = np.array([1.0, 2.0, 1.0, 2.0, 1.0])
    b = np.array([2.0, 1.0, 2.0, 1.0, 2.0])            # every move opposed
    assert gleichlaeufigkeit(a, a) == 100.0            # identical moves
    assert gleichlaeufigkeit(a, b) == 0.0              # anti-phase


# ── Crossdating ──────────────────────────────────────────────────────────

def test_demo_sample_crossdates_to_known_year():
    ref = build_reference_chronology()
    result = analyze_sample(generate_core_sample(), ref, sample_id="CORE-012")
    assert result.best.end_year == 1242
    assert result.best.start_year == 1101
    assert result.ring_count == 142
    assert result.dated and result.significant
    assert result.best.t_value >= 10.0                 # strong, unambiguous match
    assert result.best.glk_pct > 65.0
    assert result.t_margin > 3.0                        # clear of the runner-up


def test_candidates_ranked_by_t_value():
    ref = build_reference_chronology()
    result = analyze_sample(generate_core_sample(), ref, top_k=8)
    ts = [c.t_value for c in result.candidates]
    assert ts == sorted(ts, reverse=True)
    assert result.candidates[0].end_year == 1242


def test_reference_slice_correlates_perfectly():
    ref = build_reference_chronology()
    # An exact slice of the master (no noise) must date to its true year.
    exact = ref.widths[200:200 + 120]
    result = analyze_sample(exact, ref)
    assert result.best.end_year == ref.start_year + 200 + 120 - 1
    assert result.best.correlation > 0.99


def test_short_series_rejected():
    ref = build_reference_chronology()
    with pytest.raises(ValueError, match="at least"):
        analyze_sample(np.full(10, 2.0), ref)


def test_negative_widths_rejected():
    ref = build_reference_chronology()
    w = generate_core_sample()
    w[5] = -1.0
    with pytest.raises(ValueError, match="positive"):
        analyze_sample(w, ref)


# ── Felling-date logic ───────────────────────────────────────────────────

def test_felling_exact_with_bark_edge():
    f = estimate_felling(1242, bark_edge=True, sapwood_rings=None, sapwood_complete=False)
    assert f.terminus == "exact"
    assert f.earliest == f.latest == 1242


def test_felling_range_with_partial_sapwood():
    f = estimate_felling(1242, bark_edge=False, sapwood_rings=5, sapwood_complete=False)
    assert f.terminus == "range"
    assert f.earliest == 1242 + (OAK_SAPWOOD_MIN - 5)
    assert f.latest == 1242 + (OAK_SAPWOOD_MAX - 5)


def test_felling_terminus_post_quem_heartwood_only():
    f = estimate_felling(1242, bark_edge=False, sapwood_rings=None, sapwood_complete=False)
    assert f.terminus == "terminus_post_quem"
    assert f.earliest == 1242 + OAK_SAPWOOD_MIN
    assert f.latest is None


def test_felling_range_with_complete_sapwood():
    f = estimate_felling(1242, bark_edge=False, sapwood_rings=30, sapwood_complete=True)
    assert f.terminus == "range"
    assert f.earliest == 1242


# ── API contract ─────────────────────────────────────────────────────────

def test_demo_round_trip():
    demo = client.get("/api/v1/dendro/demo").json()
    resp = client.post("/api/v1/dendro/analyze", json=demo)
    assert resp.status_code == 200
    body = resp.json()
    assert body["dated"] is True
    assert body["best"]["end_year"] == 1242
    assert body["felling"]["terminus"] == "terminus_post_quem"
    assert body["felling"]["earliest"] == 1251
    assert len(body["detrended_sample"]) == body["meta"]["ring_count"]
    assert len(body["detrended_reference"]) == body["meta"]["ring_count"]
    assert body["meta"]["reference_span"] == "850-1300 AD"


def test_analyze_with_bark_edge_endpoint():
    demo = client.get("/api/v1/dendro/demo").json()
    demo["bark_edge"] = True
    body = client.post("/api/v1/dendro/analyze", json=demo).json()
    assert body["felling"]["terminus"] == "exact"
    assert body["felling"]["earliest"] == body["best"]["end_year"] == 1242


def test_short_series_maps_to_422():
    resp = client.post("/api/v1/dendro/analyze", json={"ring_widths": [2.0] * 10})
    assert resp.status_code == 422


def test_reference_endpoint():
    body = client.get("/api/v1/dendro/reference").json()
    assert body["start_year"] == 850 and body["end_year"] == 1300
    assert len(body["widths"]) == 451


def test_analysis_is_deterministic():
    demo = client.get("/api/v1/dendro/demo").json()
    first = client.post("/api/v1/dendro/analyze", json=demo).json()
    second = client.post("/api/v1/dendro/analyze", json=demo).json()
    first["meta"].pop("processing_ms")
    second["meta"].pop("processing_ms")
    assert first == second
