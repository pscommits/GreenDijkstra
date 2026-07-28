"""Two routing engines over the same CarbonWeightedGraph:

- baseline_dijkstra: classic Dijkstra, edge weight = latency only.
- green_dijkstra:    weighted-sum carbon-aware Dijkstra,
                      edge weight = alpha * norm(latency) + beta * norm(carbon),
                      alpha + beta == 1.

Both share one generic `_dijkstra` implementation (heapq-based,
O((V+E) log V)) parameterised by a `weight_fn`.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass

from .graph_builder import CarbonWeightedGraph, WeightedEdge


class NoPathError(ValueError):
    """Raised when source and target are not connected."""


class CarbonDataRequiredError(ValueError):
    """Raised if green_dijkstra is called on a graph with no carbon data."""


@dataclass
class RouteResult:
    path: list[str]
    total_latency_ms: float
    total_carbon_gco2_per_kwh: float
    alpha: float


def _dijkstra(graph: CarbonWeightedGraph, source: str, target: str, weight_fn) -> list[str]:
    if source not in graph.nodes or target not in graph.nodes:
        raise KeyError(f"Unknown node(s): {source!r}, {target!r}")

    dist: dict[str, float] = {node_id: float("inf") for node_id in graph.nodes}
    dist[source] = 0.0
    prev: dict[str, str] = {}
    visited: set[str] = set()
    heap: list[tuple[float, str]] = [(0.0, source)]

    while heap:
        d, u = heapq.heappop(heap)
        if u in visited:
            continue
        visited.add(u)
        if u == target:
            break
        for edge in graph.adjacency[u]:
            if edge.to in visited:
                continue
            nd = d + weight_fn(edge)
            if nd < dist[edge.to]:
                dist[edge.to] = nd
                prev[edge.to] = u
                heapq.heappush(heap, (nd, edge.to))

    if dist[target] == float("inf"):
        raise NoPathError(f"No path from {source!r} to {target!r}")

    path = [target]
    while path[-1] != source:
        path.append(prev[path[-1]])
    path.reverse()
    return path


def _totals(graph: CarbonWeightedGraph, path: list[str]) -> tuple[float, float]:
    latency = 0.0
    carbon = 0.0
    for u, v in zip(path, path[1:]):
        edge = next(e for e in graph.adjacency[u] if e.to == v)
        latency += edge.latency_ms
        carbon += edge.carbon_gco2_per_kwh or 0.0
    return latency, carbon


def baseline_dijkstra(graph: CarbonWeightedGraph, source: str, target: str) -> RouteResult:
    path = _dijkstra(graph, source, target, weight_fn=lambda e: e.latency_ms)
    total_latency, total_carbon = _totals(graph, path)
    return RouteResult(path=path, total_latency_ms=total_latency,
                        total_carbon_gco2_per_kwh=total_carbon, alpha=1.0)


def green_dijkstra(
    graph: CarbonWeightedGraph, source: str, target: str, alpha: float = 0.5
) -> RouteResult:
    if not 0.0 <= alpha <= 1.0:
        raise ValueError(f"alpha must be in [0, 1], got {alpha}")
    if not graph.carbon_available:
        raise CarbonDataRequiredError(
            "No carbon data available for this graph - use baseline_dijkstra() instead."
        )

    beta = 1.0 - alpha
    max_latency = max(e.latency_ms for e in graph.all_edges())
    max_carbon = max(e.carbon_gco2_per_kwh or 0.0 for e in graph.all_edges()) or 1.0

    def weight_fn(edge: WeightedEdge) -> float:
        norm_latency = edge.latency_ms / max_latency
        norm_carbon = (edge.carbon_gco2_per_kwh or 0.0) / max_carbon
        return alpha * norm_latency + beta * norm_carbon

    path = _dijkstra(graph, source, target, weight_fn)
    total_latency, total_carbon = _totals(graph, path)
    return RouteResult(path=path, total_latency_ms=total_latency,
                        total_carbon_gco2_per_kwh=total_carbon, alpha=alpha)
