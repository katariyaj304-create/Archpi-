"""Topological stratigraphy — DAG engine for structural chronology.

Nodes are unique architectural components; directed edges are chronological
dependencies read as "target is later than source":

    A --built_after--> B   means B was built after A
    A --replaces-->    B   means B replaces (postdates) A
    A --cuts-->        B   means B cuts through A (so B postdates A)

All relation kinds share the same temporal semantics (source strictly
earlier than target); the kind is retained for provenance reporting.

A cycle in this graph is a physical impossibility — a component that is
both earlier and later than another — and raises a
StratigraphicParadoxException naming the exact conflicting loop.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import networkx as nx

RELATION_KINDS = ("built_after", "replaces", "cuts")


class PalimpsestError(Exception):
    """Base class for chronological-reconstruction errors."""

    def __init__(self, message: str, detail: dict | None = None) -> None:
        super().__init__(message)
        self.detail = detail or {}


class StratigraphicParadoxException(PalimpsestError):
    """A chronological contradiction (cycle) exists in the stratigraphy."""


class UnknownComponentError(PalimpsestError):
    """An edge or constraint references a component that was never declared."""


@dataclass(frozen=True)
class StratigraphicRelation:
    """One directed dependency: `earlier` strictly predates `later`."""

    earlier: str
    later: str
    kind: str  # one of RELATION_KINDS

    def __post_init__(self) -> None:
        if self.kind not in RELATION_KINDS:
            raise ValueError(f"unknown relation kind '{self.kind}'; known: {RELATION_KINDS}")
        if self.earlier == self.later:
            raise ValueError(f"component '{self.earlier}' cannot predate itself")


@dataclass
class StratigraphicSummary:
    node_count: int
    edge_count: int
    topological_order: list[str]
    depth_by_node: dict[str, int]      # longest path from any root, in edges
    roots: list[str]                    # no known predecessors
    terminals: list[str]                # no known successors
    isolated: list[str] = field(default_factory=list)  # no relations at all


class StratigraphicGraph:
    """Validated DAG of architectural components and their dependencies."""

    def __init__(self, components: list[str], relations: list[StratigraphicRelation]) -> None:
        if not components:
            raise ValueError("at least one architectural component is required")
        if len(set(components)) != len(components):
            dupes = sorted({c for c in components if components.count(c) > 1})
            raise ValueError(f"duplicate component ids: {dupes}")

        known = set(components)
        for rel in relations:
            missing = {rel.earlier, rel.later} - known
            if missing:
                raise UnknownComponentError(
                    f"relation {rel.earlier} -[{rel.kind}]-> {rel.later} references "
                    f"undeclared component(s): {sorted(missing)}",
                    detail={"undeclared": sorted(missing), "relation": rel.kind},
                )

        self.graph = nx.DiGraph()
        self.graph.add_nodes_from(components)
        for rel in relations:
            self.graph.add_edge(rel.earlier, rel.later, kind=rel.kind)
        self.relations = list(relations)
        self._assert_acyclic()

    def _assert_acyclic(self) -> None:
        """Cycle detection (Kahn-style via NetworkX); descriptive paradox."""
        if nx.is_directed_acyclic_graph(self.graph):
            return
        cycle_edges = nx.find_cycle(self.graph, orientation="original")
        loop_nodes = [edge[0] for edge in cycle_edges] + [cycle_edges[-1][1]]
        steps = [
            f"{u} is earlier than {v} (via '{self.graph.edges[u, v]['kind']}')"
            for u, v, _dir in cycle_edges
        ]
        raise StratigraphicParadoxException(
            "Chronological contradiction: "
            + "; ".join(steps)
            + f" — so '{loop_nodes[0]}' would have to predate itself.",
            detail={
                "cycle": loop_nodes,
                "conflicting_relations": [
                    {"earlier": u, "later": v, "kind": self.graph.edges[u, v]["kind"]}
                    for u, v, _dir in cycle_edges
                ],
            },
        )

    def topological_order(self) -> list[str]:
        """Deterministic Kahn topological sort (lexicographic tie-break)."""
        return list(nx.lexicographical_topological_sort(self.graph))

    def depth_by_node(self) -> dict[str, int]:
        """Longest chain of dependencies above each node (0 for roots)."""
        depth: dict[str, int] = {}
        for node in self.topological_order():
            preds = list(self.graph.predecessors(node))
            depth[node] = 1 + max(depth[p] for p in preds) if preds else 0
        return depth

    def has_path_between(self, a: str, b: str) -> bool:
        """True when a directed order exists in either direction."""
        return nx.has_path(self.graph, a, b) or nx.has_path(self.graph, b, a)

    def summary(self) -> StratigraphicSummary:
        order = self.topological_order()
        return StratigraphicSummary(
            node_count=self.graph.number_of_nodes(),
            edge_count=self.graph.number_of_edges(),
            topological_order=order,
            depth_by_node=self.depth_by_node(),
            roots=[n for n in order if self.graph.in_degree(n) == 0],
            terminals=[n for n in order if self.graph.out_degree(n) == 0],
            isolated=[n for n in order if self.graph.degree(n) == 0],
        )
