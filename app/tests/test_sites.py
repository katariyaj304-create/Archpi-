"""Tests for the structure selector — one specimen driving every engine.

Confirms the registry lists structures and that each structure's bundle is a
set of request bodies that every analyze endpoint accepts and dates
coherently.

Run from app/:  python -m pytest tests/test_sites.py -q
"""

import pytest
from fastapi.testclient import TestClient

from forensic_backend.main import app

client = TestClient(app)


def _sites():
    return client.get("/api/v1/sites").json()["sites"]


def test_lists_registered_structures():
    sites = _sites()
    ids = {s["site_id"] for s in sites}
    assert {"aethel-cathedral", "san-nicolo", "byzantine-narthex"} <= ids
    for s in sites:
        assert s["name"] and s["location"] and s["period"] and s["blurb"]


def test_unknown_structure_is_404():
    assert client.get("/api/v1/sites/not-a-building").status_code == 404


@pytest.mark.parametrize("site_id", ["aethel-cathedral", "san-nicolo", "byzantine-narthex"])
def test_bundle_drives_every_engine(site_id):
    """Each structure's bundle POSTs cleanly through all six engines."""
    bundles = client.get(f"/api/v1/sites/{site_id}").json()["bundles"]

    assert client.post("/api/v1/xrd/analyze", json=bundles["xrd"]).status_code == 200
    assert client.post("/api/v1/hazards/evaluate", json=bundles["hazards"]).status_code == 200
    assert client.post("/api/v1/palimpsest/sequence", json=bundles["palimpsest"]).status_code == 200
    assert client.post("/api/v1/provenance/analyze", json=bundles["provenance"]).status_code == 200
    assert client.post("/api/v1/dendro/analyze", json=bundles["dendro"]).status_code == 200
    assert client.post("/api/v1/radiocarbon/calibrate", json=bundles["radiocarbon"]).status_code == 200


@pytest.mark.parametrize("site_id", ["aethel-cathedral", "san-nicolo", "byzantine-narthex"])
def test_timber_crossdates_and_brackets_radiocarbon(site_id):
    """Every building's oak crossdates, and its felling sits inside (or at)
    the calibrated radiocarbon 2-sigma window — the coherence guarantee."""
    bundles = client.get(f"/api/v1/sites/{site_id}").json()["bundles"]

    dendro = client.post("/api/v1/dendro/analyze", json=bundles["dendro"]).json()
    assert dendro["dated"] is True
    assert dendro["best"]["t_value"] >= 3.5

    c14 = client.post("/api/v1/radiocarbon/calibrate", json=bundles["radiocarbon"]).json()
    felling = dendro["felling"]["earliest"]
    lo = c14["hpd_95"][0]["start_year"]
    hi = c14["hpd_95"][-1]["end_year"]
    # Felling (terminus post quem) should not predate the radiocarbon window.
    assert lo - 30 <= felling <= hi + 60
