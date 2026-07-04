"""Tests for the Feature 3 Chronological Palimpsest engine.

Covers the stratigraphic DAG (cycle paradox reporting, deterministic
topological order), the Bayesian chronology (constraint narrowing in both
directions, order-vs-evidence conflicts), campaign clustering (all three
compatibility rules) and the API contract including the 409 mapping.

Run from app/:  python -m pytest tests/test_palimpsest_engine.py -q
"""

import pytest
from fastapi.testclient import TestClient

from forensic_backend.campaigns import CampaignClusterer, ChemicalSignature
from forensic_backend.chronology import (
    ChronologicalConflictError,
    ChronologyOptimizer,
    DateConstraint,
    DateInterval,
)
from forensic_backend.main import app
from forensic_backend.stratigraphy import (
    StratigraphicGraph,
    StratigraphicParadoxException,
    StratigraphicRelation,
    UnknownComponentError,
)

client = TestClient(app)


def rel(earlier: str, later: str, kind: str = "built_after") -> StratigraphicRelation:
    return StratigraphicRelation(earlier=earlier, later=later, kind=kind)


def constraint(node: str, start: float, end: float, kind: str = "inscription") -> DateConstraint:
    return DateConstraint(node_id=node, intervals=(DateInterval(start, end),), kind=kind)


# ── Stratigraphic graph ─────────────────────────────────────────────────

def test_relation_validation():
    with pytest.raises(ValueError, match="unknown relation kind"):
        rel("A", "B", kind="above")
    with pytest.raises(ValueError, match="predate itself"):
        rel("A", "A")


def test_undeclared_component_rejected():
    with pytest.raises(UnknownComponentError) as exc_info:
        StratigraphicGraph(["A"], [rel("A", "Ghost")])
    assert exc_info.value.detail["undeclared"] == ["Ghost"]


def test_duplicate_components_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        StratigraphicGraph(["A", "A", "B"], [])


def test_paradox_names_the_conflicting_loop():
    with pytest.raises(StratigraphicParadoxException) as exc_info:
        StratigraphicGraph(
            ["A", "B", "C"],
            [rel("A", "B"), rel("B", "C", "cuts"), rel("C", "A", "replaces")],
        )
    exc = exc_info.value
    assert "predate itself" in str(exc)
    assert set(exc.detail["cycle"]) == {"A", "B", "C"}
    kinds = {(r["earlier"], r["later"]): r["kind"] for r in exc.detail["conflicting_relations"]}
    assert kinds[("B", "C")] == "cuts"
    assert kinds[("C", "A")] == "replaces"


def test_topological_order_and_depth():
    graph = StratigraphicGraph(
        ["Foundation", "Wall", "Arch", "Repair"],
        [rel("Foundation", "Wall"), rel("Wall", "Arch"), rel("Arch", "Repair", "replaces")],
    )
    assert graph.topological_order() == ["Foundation", "Wall", "Arch", "Repair"]
    assert graph.depth_by_node() == {"Foundation": 0, "Wall": 1, "Arch": 2, "Repair": 3}
    summary = graph.summary()
    assert summary.roots == ["Foundation"]
    assert summary.terminals == ["Repair"]
    assert summary.isolated == []


def test_topological_order_is_deterministic():
    graph = StratigraphicGraph(["Z", "M", "A"], [])  # no relations: lexicographic
    assert graph.topological_order() == ["A", "M", "Z"]


# ── Bayesian chronology ─────────────────────────────────────────────────

def test_constrained_node_respects_its_own_evidence():
    graph = StratigraphicGraph(["A"], [])
    phases = ChronologyOptimizer(graph, [constraint("A", 120, 150)]).phases()
    (phase,) = phases
    assert phase.earliest_possible >= 119
    assert phase.latest_possible <= 151
    assert 120 <= phase.median_year <= 150
    assert phase.constrained and phase.constraint_kinds == ["inscription"]


def test_successor_evidence_narrows_predecessor_lpd():
    # B is unconstrained but must predate C (dated 1240-1280) and postdate
    # A (dated 120-150): its reconciled window must land strictly between.
    graph = StratigraphicGraph(["A", "B", "C"], [rel("A", "B"), rel("B", "C")])
    phases = ChronologyOptimizer(
        graph,
        [constraint("A", 120, 150), constraint("C", 1240, 1280, "radiocarbon_2sigma")],
    ).phases()
    by_id = {p.node_id: p for p in phases}
    assert by_id["B"].earliest_possible > by_id["A"].earliest_possible
    assert by_id["B"].latest_possible < 1280
    assert by_id["B"].earliest_possible > 120
    assert not by_id["B"].constrained


def test_order_enforced_between_two_dated_nodes():
    # Both dated 1240-1280 but A must strictly predate B: the posteriors
    # must shift apart (A earlier, B later) instead of staying identical.
    graph = StratigraphicGraph(["A", "B"], [rel("A", "B")])
    phases = ChronologyOptimizer(
        graph,
        [constraint("A", 1240, 1280), constraint("B", 1240, 1280)],
    ).phases()
    by_id = {p.node_id: p for p in phases}
    assert by_id["A"].median_year < by_id["B"].median_year
    assert by_id["A"].latest_possible < 1280  # must leave room for B after it


def test_conflicting_evidence_raises_named_conflict():
    # Graph says A predates B, but evidence dates A centuries after B.
    graph = StratigraphicGraph(["A", "B"], [rel("A", "B")])
    with pytest.raises(ChronologicalConflictError) as exc_info:
        ChronologyOptimizer(
            graph, [constraint("A", 1900, 1950), constraint("B", 120, 150)]
        ).phases()
    assert exc_info.value.detail["node"] in {"A", "B"}
    assert "incompatible with the stratigraphic order" in str(exc_info.value)


def test_multi_interval_radiocarbon_and_weights():
    graph = StratigraphicGraph(["Arch"], [])
    c = DateConstraint(
        node_id="Arch",
        kind="radiocarbon_2sigma",
        intervals=(
            DateInterval(1235, 1240, 0.136),
            DateInterval(1240, 1265, 0.682),
            DateInterval(1265, 1280, 0.136),
        ),
    )
    (phase,) = ChronologyOptimizer(graph, [c]).phases()
    assert phase.earliest_possible >= 1234
    assert phase.latest_possible <= 1281
    assert 1240 <= phase.median_year <= 1265  # mass concentrates in the 1-sigma core
    assert phase.hpd_start >= phase.earliest_possible
    assert phase.hpd_end <= phase.latest_possible


def test_constraint_on_unknown_node_rejected():
    graph = StratigraphicGraph(["A"], [])
    with pytest.raises(UnknownComponentError):
        ChronologyOptimizer(graph, [constraint("Ghost", 120, 150)])


def test_at_least_one_constraint_required():
    graph = StratigraphicGraph(["A"], [])
    with pytest.raises(ValueError, match="at least one"):
        ChronologyOptimizer(graph, [])


# ── Campaign clustering ─────────────────────────────────────────────────

def sig(node: str, silica: float, calcium: float) -> ChemicalSignature:
    return ChemicalSignature(node_id=node, silica_ratio_pct=silica, calcium_ratio_pct=calcium)


def test_concurrent_matching_signatures_cluster_together():
    # Floor and Column are siblings (no path) with near-identical chemistry.
    graph = StratigraphicGraph(
        ["Foundation", "Floor", "Column"],
        [rel("Foundation", "Floor"), rel("Foundation", "Column")],
    )
    campaigns, unclustered = CampaignClusterer(
        graph, [sig("Foundation", 67, 23), sig("Floor", 66, 24), sig("Column", 65, 25)]
    ).cluster()
    grouped = {tuple(c.members) for c in campaigns}
    assert ("Column", "Floor") in grouped     # concurrent + matching -> together
    assert ("Foundation",) in grouped          # ordered before both -> alone
    assert unclustered == []


def test_sequential_components_never_cluster_despite_chemistry():
    graph = StratigraphicGraph(["A", "B"], [rel("A", "B")])
    campaigns, _ = CampaignClusterer(graph, [sig("A", 30, 60), sig("B", 30, 60)]).cluster()
    assert all(len(c.members) == 1 for c in campaigns)


def test_divergent_chemistry_never_clusters():
    graph = StratigraphicGraph(["A", "B"], [])  # fully concurrent
    campaigns, _ = CampaignClusterer(graph, [sig("A", 67, 23), sig("B", 28, 60)]).cluster()
    assert all(len(c.members) == 1 for c in campaigns)


def test_disjoint_date_windows_block_clustering():
    graph = StratigraphicGraph(["A", "B"], [])  # no path, same chemistry
    optimizer = ChronologyOptimizer(
        graph, [constraint("A", 120, 150), constraint("B", 1900, 1950)]
    )
    campaigns, _ = CampaignClusterer(
        graph, [sig("A", 30, 60), sig("B", 30, 60)]
    ).cluster(optimizer.phases())
    assert all(len(c.members) == 1 for c in campaigns)


def test_campaign_reports_window_and_spread():
    graph = StratigraphicGraph(["A", "B"], [])
    optimizer = ChronologyOptimizer(
        graph, [constraint("A", 1200, 1300), constraint("B", 1250, 1350)]
    )
    campaigns, _ = CampaignClusterer(
        graph, [sig("A", 30, 60), sig("B", 31, 59)]
    ).cluster(optimizer.phases())
    (campaign,) = campaigns
    assert campaign.members == ["A", "B"]
    assert campaign.campaign_id == "CAMPAIGN-01"
    assert campaign.window_start == pytest.approx(1250, abs=2)  # max of EPDs
    assert campaign.window_end == pytest.approx(1300, abs=2)    # min of LPDs
    assert 0 < campaign.max_signature_distance <= 0.15


def test_signature_for_unknown_node_rejected():
    graph = StratigraphicGraph(["A"], [])
    with pytest.raises(UnknownComponentError):
        CampaignClusterer(graph, [sig("Ghost", 30, 60)])


# ── API contract ────────────────────────────────────────────────────────

def test_validate_endpoint_accepts_dag():
    resp = client.post("/api/v1/palimpsest/validate", json={
        "site_id": "T1",
        "components": ["A", "B"],
        "relations": [{"earlier": "A", "later": "B", "kind": "built_after"}],
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is True
    assert body["graph"]["topological_order"] == ["A", "B"]


def test_validate_endpoint_maps_paradox_to_409():
    resp = client.post("/api/v1/palimpsest/validate", json={
        "components": ["A", "B"],
        "relations": [
            {"earlier": "A", "later": "B", "kind": "built_after"},
            {"earlier": "B", "later": "A", "kind": "cuts"},
        ],
    })
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert "predate itself" in detail["message"]
    assert set(detail["cycle"]) == {"A", "B"}
    assert len(detail["conflicting_relations"]) == 2


def test_validate_endpoint_maps_unknown_component_to_422():
    resp = client.post("/api/v1/palimpsest/validate", json={
        "components": ["A"],
        "relations": [{"earlier": "A", "later": "Ghost", "kind": "cuts"}],
    })
    assert resp.status_code == 422
    assert resp.json()["detail"]["undeclared"] == ["Ghost"]


def test_demo_round_trip_sequences_the_dossier():
    demo = client.get("/api/v1/palimpsest/demo").json()
    resp = client.post("/api/v1/palimpsest/sequence", json=demo)
    assert resp.status_code == 200
    body = resp.json()

    phases = {p["node_id"]: p for p in body["phases"]}
    assert phases["Foundation_01"]["phase_index"] == 0
    # Roman era stays 2nd century, Gothic 13th, restoration late 20th.
    assert phases["Foundation_01"]["latest_possible"] <= 151
    assert 1234 <= phases["West_Arch"]["earliest_possible"]
    assert phases["West_Arch"]["latest_possible"] <= 1281
    assert phases["West_Arch_Repair"]["earliest_possible"] >= 1982
    # Unconstrained Roman_Floor is squeezed between foundation and arch.
    assert phases["Roman_Floor"]["earliest_possible"] > 120
    assert phases["Roman_Floor"]["latest_possible"] < 1281

    members = {c["campaign_id"]: c["members"] for c in body["campaigns"]}
    grouped = {tuple(m) for m in members.values()}
    assert ("Hypocaust_Column", "Roman_Floor") in grouped   # Roman era
    assert ("Chapel_Apse", "Nave_Vault") in grouped          # Gothic era
    assert body["unclustered"] == []


def test_sequence_endpoint_maps_conflict_to_409():
    resp = client.post("/api/v1/palimpsest/sequence", json={
        "components": ["A", "B"],
        "relations": [{"earlier": "A", "later": "B", "kind": "built_after"}],
        "constraints": [
            {"node_id": "A", "intervals": [{"start_year": 1900, "end_year": 1950}]},
            {"node_id": "B", "intervals": [{"start_year": 120, "end_year": 150}]},
        ],
    })
    assert resp.status_code == 409
    assert "incompatible with the stratigraphic order" in resp.json()["detail"]["message"]


def test_sequence_is_deterministic():
    demo = client.get("/api/v1/palimpsest/demo").json()
    first = client.post("/api/v1/palimpsest/sequence", json=demo).json()
    second = client.post("/api/v1/palimpsest/sequence", json=demo).json()
    first["meta"].pop("processing_ms")
    second["meta"].pop("processing_ms")
    assert first == second
