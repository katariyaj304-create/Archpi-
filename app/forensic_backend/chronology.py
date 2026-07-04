"""Bayesian chronological optimization over a stratigraphic DAG.

A lightweight, deterministic analogue of an OxCal sequence model. Each
component's date is a discrete probability array over a shared year grid:

* prior     — uniform over the union of the component's absolute constraint
              intervals (inscription ranges, calibrated 2-sigma radiocarbon
              intervals, each with an optional weight); components without
              constraints start uniform over the whole modelled span;
* forward   — in topological order, a component's density at year t is
              multiplied by the probability that every predecessor dates
              strictly before t (its cumulative distribution through t-1);
* backward  — in reverse order, multiplied by the probability that every
              successor dates strictly after t (its survival beyond t).

The passes zero out any year incompatible with the graph order, narrowing
overlapping ranges. The support of the posterior gives the reconciled
Earliest Possible Date (EPD) and Latest Possible Date (LPD); a 2-sigma
(95.4 %) highest-density interval is reported alongside. An empty posterior
means the absolute constraints contradict the stratigraphy and raises
ChronologicalConflictError naming the offending component.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .stratigraphy import PalimpsestError, StratigraphicGraph, UnknownComponentError

MAX_GRID_POINTS = 4000
SUPPORT_EPS = 1e-12
TWO_SIGMA = 0.954


class ChronologicalConflictError(PalimpsestError):
    """Absolute date constraints are irreconcilable with the graph order."""


@dataclass(frozen=True)
class DateInterval:
    """One absolute constraint interval in calendar years (negative = BC)."""

    start_year: float
    end_year: float
    weight: float = 1.0  # relative mass, e.g. 2-sigma segment probability

    def __post_init__(self) -> None:
        if self.end_year < self.start_year:
            raise ValueError(
                f"interval end {self.end_year} precedes start {self.start_year}"
            )
        if self.weight <= 0:
            raise ValueError("interval weight must be positive")


@dataclass(frozen=True)
class DateConstraint:
    """Absolute dating evidence attached to one component.

    kind is provenance only ("inscription", "radiocarbon_2sigma",
    "archival", ...); the mathematics treats all kinds identically.
    """

    node_id: str
    intervals: tuple[DateInterval, ...]
    kind: str = "inscription"

    def __post_init__(self) -> None:
        if not self.intervals:
            raise ValueError(f"constraint on '{self.node_id}' has no intervals")


@dataclass
class PhaseChronology:
    node_id: str
    phase_index: int              # position in topological order
    depth: int                    # longest dependency chain above the node
    earliest_possible: float      # EPD, calendar year
    latest_possible: float        # LPD, calendar year
    hpd_start: float              # 95.4 % highest-density interval
    hpd_end: float
    median_year: float
    constrained: bool             # had direct absolute evidence
    constraint_kinds: list[str]


class ChronologyOptimizer:
    """Constraint propagation + probability narrowing over the DAG."""

    def __init__(
        self,
        graph: StratigraphicGraph,
        constraints: list[DateConstraint],
        *,
        padding_years: float = 50.0,
    ) -> None:
        known = set(graph.graph.nodes)
        by_node: dict[str, list[DateConstraint]] = {}
        for c in constraints:
            if c.node_id not in known:
                raise UnknownComponentError(
                    f"date constraint references undeclared component '{c.node_id}'",
                    detail={"undeclared": [c.node_id]},
                )
            by_node.setdefault(c.node_id, []).append(c)
        if not by_node:
            raise ValueError("at least one absolute date constraint is required")

        self.graph = graph
        self.constraints_by_node = by_node

        all_intervals = [iv for cs in by_node.values() for c in cs for iv in c.intervals]
        lo = min(iv.start_year for iv in all_intervals) - padding_years
        hi = max(iv.end_year for iv in all_intervals) + padding_years
        # Widen so every node needs at most |V| years of strict ordering slack
        slack = float(graph.graph.number_of_nodes())
        lo -= slack
        hi += slack
        step = max(1.0, (hi - lo) / MAX_GRID_POINTS)
        self.years = np.arange(lo, hi + step, step)
        self.step = step

    # ── Priors ──────────────────────────────────────────────────────────

    def _prior(self, node_id: str) -> np.ndarray:
        constraints = self.constraints_by_node.get(node_id)
        if not constraints:
            return np.full(self.years.size, 1.0 / self.years.size)
        density = np.zeros(self.years.size)
        for c in constraints:
            for iv in c.intervals:
                mask = (self.years >= iv.start_year) & (self.years <= iv.end_year)
                if not mask.any():  # sub-step interval: nearest grid year
                    mask = np.abs(self.years - iv.start_year) <= self.step / 2.0
                density[mask] += iv.weight / max(mask.sum(), 1)
        total = density.sum()
        if total <= 0:
            raise ChronologicalConflictError(
                f"constraints on '{node_id}' cover no part of the modelled span",
                detail={"node": node_id},
            )
        return density / total

    # ── Message passing ─────────────────────────────────────────────────

    @staticmethod
    def _before_probability(density: np.ndarray) -> np.ndarray:
        """P(date <= t - 1 step) for each grid year t."""
        cdf = np.cumsum(density)
        return np.concatenate(([0.0], cdf[:-1]))

    @staticmethod
    def _after_probability(density: np.ndarray) -> np.ndarray:
        """P(date >= t + 1 step) for each grid year t."""
        reverse_cdf = np.cumsum(density[::-1])[::-1]
        return np.concatenate((reverse_cdf[1:], [0.0]))

    def _conflict(self, node: str, stage: str) -> ChronologicalConflictError:
        preds = sorted(self.graph.graph.predecessors(node))
        succs = sorted(self.graph.graph.successors(node))
        return ChronologicalConflictError(
            f"component '{node}' has no possible date: its absolute constraints "
            f"are incompatible with the stratigraphic order during the {stage} pass "
            f"(predecessors: {preds or 'none'}; successors: {succs or 'none'}).",
            detail={"node": node, "stage": stage, "predecessors": preds, "successors": succs},
        )

    def solve(self) -> dict[str, np.ndarray]:
        """Posterior date density per component, keyed by node id."""
        order = self.graph.topological_order()
        posterior: dict[str, np.ndarray] = {}

        for node in order:  # forward: enforce "after all predecessors"
            density = self._prior(node)
            for pred in self.graph.graph.predecessors(node):
                density = density * self._before_probability(posterior[pred])
            total = density.sum()
            if total <= SUPPORT_EPS:
                raise self._conflict(node, "forward")
            posterior[node] = density / total

        for node in reversed(order):  # backward: enforce "before all successors"
            density = posterior[node]
            for succ in self.graph.graph.successors(node):
                density = density * self._after_probability(posterior[succ])
            total = density.sum()
            if total <= SUPPORT_EPS:
                raise self._conflict(node, "backward")
            posterior[node] = density / total

        return posterior

    # ── Summaries ───────────────────────────────────────────────────────

    def _hpd_interval(self, density: np.ndarray) -> tuple[float, float]:
        """Shortest set of highest-density years holding 95.4 % of mass."""
        order = np.argsort(density)[::-1]
        cumulative = np.cumsum(density[order])
        keep = order[: int(np.searchsorted(cumulative, TWO_SIGMA)) + 1]
        return float(self.years[keep.min()]), float(self.years[keep.max()])

    def phases(self) -> list[PhaseChronology]:
        posterior = self.solve()
        order = self.graph.topological_order()
        depth = self.graph.depth_by_node()

        phases = []
        for index, node in enumerate(order):
            density = posterior[node]
            support = np.flatnonzero(density > SUPPORT_EPS)
            cdf = np.cumsum(density)
            hpd_start, hpd_end = self._hpd_interval(density)
            constraints = self.constraints_by_node.get(node, [])
            phases.append(PhaseChronology(
                node_id=node,
                phase_index=index,
                depth=depth[node],
                earliest_possible=float(self.years[support[0]]),
                latest_possible=float(self.years[support[-1]]),
                hpd_start=hpd_start,
                hpd_end=hpd_end,
                median_year=float(self.years[int(np.searchsorted(cdf, 0.5))]),
                constrained=bool(constraints),
                constraint_kinds=sorted({c.kind for c in constraints}),
            ))
        return phases
