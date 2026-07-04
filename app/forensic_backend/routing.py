"""Least-cost path routing over the historical friction surface.

The classified grid (terrain.CostSurface) becomes an undirected NetworkX
graph: nodes are cell indices, edges connect 8-neighbours. An edge costs

    cost = great_circle_km(u, v) * (friction(u) + friction(v)) / 2
           + TRANSSHIPMENT_PENALTY  (only when crossing water <-> land)

so a kilometre at sea is ~25x cheaper than overland and ~60x cheaper than
a mountain crossing, and every port call (cargo moving between ship and
land carriage) pays a fixed handling cost. The optimal route is found with
A* (networkx.astar_path); the heuristic — great-circle distance to the
target times the minimum friction (sea) — never overestimates the true
remaining cost, so the search stays admissible and exact.

The path is post-processed into contiguous single-mode legs (sea / river /
plain / mountain) with per-mode distance totals and a transshipment count,
and the polyline is returned in (lon, lat) order ready for GeoJSON.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import networkx as nx

from .isotopes import ProvenanceError
from .terrain import (
    TRANSSHIPMENT_PENALTY,
    WATER_MODES,
    CostSurface,
    GridCell,
    default_surface,
    haversine_km,
)

_NEIGHBOURS = ((1, 0), (0, 1), (1, 1), (1, -1))  # undirected: half the star


class RouteNotFoundError(ProvenanceError):
    """No navigable path exists between the requested endpoints."""


@dataclass
class RouteLeg:
    """One contiguous same-mode stretch of the optimal route."""

    mode: str                 # sea | river | plain | mountain
    distance_km: float
    start: tuple[float, float]   # (lon, lat)
    end: tuple[float, float]


@dataclass
class TradeRoute:
    """The least-cost path and its transport economics."""

    coordinates: list[tuple[float, float]]   # (lon, lat) polyline
    total_km: float
    total_cost: float                        # friction-weighted cost units
    km_by_mode: dict[str, float]
    legs: list[RouteLeg]
    transshipments: int                      # water<->land transfers
    source_snap_km: float                    # true point -> grid distance
    destination_snap_km: float


class TradeRouteEngine:
    """A* least-cost routing between arbitrary WGS84 points."""

    def __init__(self, surface: CostSurface | None = None) -> None:
        self.surface = surface if surface is not None else default_surface()
        self.graph = _build_graph(self.surface)

    def route(
        self,
        source_lat: float,
        source_lon: float,
        dest_lat: float,
        dest_lon: float,
    ) -> TradeRoute:
        """Least-cost trade route between two points inside the grid."""
        try:
            src = self.surface.snap(source_lat, source_lon)
            dst = self.surface.snap(dest_lat, dest_lon)
        except ValueError as exc:
            raise RouteNotFoundError(str(exc), detail={"reason": "outside_grid"}) from exc

        cells = self.surface.cells

        def heuristic(a: tuple[int, int], b: tuple[int, int]) -> float:
            ca, cb = cells[a], cells[b]
            return haversine_km(ca.lat, ca.lon, cb.lat, cb.lon)  # min friction = 1

        try:
            nodes = nx.astar_path(
                self.graph, (src.i, src.j), (dst.i, dst.j),
                heuristic=heuristic, weight="cost",
            )
        except nx.NetworkXNoPath as exc:  # unreachable on a full grid, kept for safety
            raise RouteNotFoundError(
                "no navigable path between the requested endpoints",
                detail={"reason": "disconnected"},
            ) from exc

        return _summarize(
            [cells[n] for n in nodes],
            source=(source_lon, source_lat),
            destination=(dest_lon, dest_lat),
            graph=self.graph,
        )


def _build_graph(surface: CostSurface) -> nx.Graph:
    graph = nx.Graph()
    cells = surface.cells
    graph.add_nodes_from(cells)
    for (i, j), cell in cells.items():
        for di, dj in _NEIGHBOURS:
            other = cells.get((i + di, j + dj))
            if other is None:
                continue
            km = haversine_km(cell.lat, cell.lon, other.lat, other.lon)
            cost = km * (cell.friction + other.friction) / 2.0
            if (cell.mode in WATER_MODES) != (other.mode in WATER_MODES):
                cost += TRANSSHIPMENT_PENALTY
            graph.add_edge((i, j), (other.i, other.j), cost=cost, km=km)
    return graph


def _summarize(
    path: list[GridCell],
    *,
    source: tuple[float, float],
    destination: tuple[float, float],
    graph: nx.Graph,
) -> TradeRoute:
    km_by_mode: dict[str, float] = {}
    total_km = total_cost = 0.0
    transshipments = 0
    legs: list[RouteLeg] = []

    for prev, curr in zip(path, path[1:]):
        edge = graph.edges[(prev.i, prev.j), (curr.i, curr.j)]
        total_km += edge["km"]
        total_cost += edge["cost"]
        # attribute the segment's km to the two cell modes half-and-half
        for cell in (prev, curr):
            km_by_mode[cell.mode] = km_by_mode.get(cell.mode, 0.0) + edge["km"] / 2.0
        if (prev.mode in WATER_MODES) != (curr.mode in WATER_MODES):
            transshipments += 1

        if legs and legs[-1].mode == curr.mode:
            legs[-1] = RouteLeg(
                mode=curr.mode,
                distance_km=legs[-1].distance_km + edge["km"],
                start=legs[-1].start,
                end=(curr.lon, curr.lat),
            )
        else:
            legs.append(RouteLeg(
                mode=curr.mode,
                distance_km=edge["km"],
                start=(prev.lon, prev.lat),
                end=(curr.lon, curr.lat),
            ))

    # Polyline through cell centres, closed onto the true endpoints.
    coordinates = [source] + [(c.lon, c.lat) for c in path] + [destination]

    return TradeRoute(
        coordinates=coordinates,
        total_km=round(total_km, 1),
        total_cost=round(total_cost, 1),
        km_by_mode={m: round(v, 1) for m, v in sorted(km_by_mode.items())},
        legs=[
            RouteLeg(l.mode, round(l.distance_km, 1), l.start, l.end)
            for l in legs
        ],
        transshipments=transshipments,
        source_snap_km=round(
            haversine_km(source[1], source[0], path[0].lat, path[0].lon), 1),
        destination_snap_km=round(
            haversine_km(destination[1], destination[0], path[-1].lat, path[-1].lon), 1),
    )


@lru_cache(maxsize=1)
def default_engine() -> TradeRouteEngine:
    """Process-wide singleton (grid + graph build once)."""
    return TradeRouteEngine()
