"""Material-phase clustering — grouping components into Building Campaigns.

Two architectural components belong to the same campaign ("Material Era")
only when all three conditions hold pairwise:

1. chemistry  — their chemical signatures (silica / calcium ratios) agree
                within a configurable relative variance threshold;
2. topology   — the validated stratigraphic graph contains no directed path
                between them in either direction (neither is provably
                earlier than the other);
3. chronology — their reconciled EPD..LPD windows overlap, so they could
                have been built concurrently.

Clustering is deterministic: components are visited in topological order
and joined to the first existing campaign with which every member is
pairwise compatible; otherwise they seed a new campaign. Components with
no chemical signature are reported as unclustered rather than guessed.
"""

from __future__ import annotations

from dataclasses import dataclass

from .chronology import PhaseChronology
from .stratigraphy import StratigraphicGraph, UnknownComponentError

DEFAULT_VARIANCE_THRESHOLD = 0.15


@dataclass(frozen=True)
class ChemicalSignature:
    """Bulk chemistry of one component, as XRF/XRD-style ratios [%]."""

    node_id: str
    silica_ratio_pct: float
    calcium_ratio_pct: float

    def __post_init__(self) -> None:
        for label, value in (
            ("silica_ratio_pct", self.silica_ratio_pct),
            ("calcium_ratio_pct", self.calcium_ratio_pct),
        ):
            if not 0.0 <= value <= 100.0:
                raise ValueError(f"{label} for '{self.node_id}' must be 0-100, got {value}")

    def distance(self, other: "ChemicalSignature") -> float:
        """Largest symmetric relative difference across signature channels.

        For each channel: |a - b| / mean(a, b); channels that are zero on
        both sides contribute 0, a zero against a non-zero contributes the
        maximum (2.0). Dimensionless, 0 = identical.
        """
        worst = 0.0
        for a, b in (
            (self.silica_ratio_pct, other.silica_ratio_pct),
            (self.calcium_ratio_pct, other.calcium_ratio_pct),
        ):
            mean = (a + b) / 2.0
            worst = max(worst, abs(a - b) / mean if mean > 0 else 0.0)
        return worst


@dataclass
class BuildingCampaign:
    """One synchronized group of components sharing material and time."""

    campaign_id: str                 # e.g. CAMPAIGN-01
    members: list[str]
    mean_silica_ratio_pct: float
    mean_calcium_ratio_pct: float
    max_signature_distance: float    # worst pairwise chemistry spread
    window_start: float | None       # latest member EPD (overlap start)
    window_end: float | None         # earliest member LPD (overlap end)


class CampaignClusterer:
    """Deterministic single-pass clustering over the validated graph."""

    def __init__(
        self,
        graph: StratigraphicGraph,
        signatures: list[ChemicalSignature],
        *,
        variance_threshold: float = DEFAULT_VARIANCE_THRESHOLD,
    ) -> None:
        if not 0.0 < variance_threshold <= 2.0:
            raise ValueError(
                f"variance_threshold must be in (0, 2], got {variance_threshold}"
            )
        known = set(graph.graph.nodes)
        undeclared = sorted({s.node_id for s in signatures} - known)
        if undeclared:
            raise UnknownComponentError(
                f"chemical signature(s) reference undeclared component(s): {undeclared}",
                detail={"undeclared": undeclared},
            )
        duplicates = sorted(
            {s.node_id for s in signatures
             if sum(1 for t in signatures if t.node_id == s.node_id) > 1}
        )
        if duplicates:
            raise ValueError(f"duplicate chemical signatures for: {duplicates}")

        self.graph = graph
        self.signatures = {s.node_id: s for s in signatures}
        self.threshold = variance_threshold

    def _compatible(
        self,
        a: str,
        b: str,
        windows: dict[str, tuple[float, float]] | None,
    ) -> bool:
        if self.signatures[a].distance(self.signatures[b]) > self.threshold:
            return False
        if self.graph.has_path_between(a, b):
            return False  # provably sequential, never concurrent
        if windows is not None:
            start = max(windows[a][0], windows[b][0])
            end = min(windows[a][1], windows[b][1])
            if start > end:
                return False  # date windows disjoint
        return True

    def cluster(
        self, phases: list[PhaseChronology] | None = None
    ) -> tuple[list[BuildingCampaign], list[str]]:
        """Group signed components into campaigns.

        phases (optional) supplies the reconciled EPD/LPD windows used for
        the concurrency test; without them only chemistry + topology apply.
        Returns (campaigns, unclustered_node_ids) where unclustered are the
        components that carry no chemical signature.
        """
        windows = (
            {p.node_id: (p.earliest_possible, p.latest_possible) for p in phases}
            if phases is not None
            else None
        )

        clusters: list[list[str]] = []
        for node in self.graph.topological_order():
            if node not in self.signatures:
                continue
            placed = False
            for members in clusters:
                if all(self._compatible(node, m, windows) for m in members):
                    members.append(node)
                    placed = True
                    break
            if not placed:
                clusters.append([node])

        campaigns = []
        for index, members in enumerate(clusters, start=1):
            sigs = [self.signatures[m] for m in members]
            spread = max(
                (a.distance(b) for i, a in enumerate(sigs) for b in sigs[i + 1:]),
                default=0.0,
            )
            window_start = window_end = None
            if windows is not None:
                window_start = max(windows[m][0] for m in members)
                window_end = min(windows[m][1] for m in members)
            campaigns.append(BuildingCampaign(
                campaign_id=f"CAMPAIGN-{index:02d}",
                members=sorted(members),
                mean_silica_ratio_pct=round(
                    sum(s.silica_ratio_pct for s in sigs) / len(sigs), 2
                ),
                mean_calcium_ratio_pct=round(
                    sum(s.calcium_ratio_pct for s in sigs) / len(sigs), 2
                ),
                max_signature_distance=round(spread, 4),
                window_start=window_start,
                window_end=window_end,
            ))

        unclustered = sorted(set(self.graph.graph.nodes) - set(self.signatures))
        return campaigns, unclustered
