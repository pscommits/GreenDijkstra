"""Turns a static Topology into a "live" carbon-weighted graph by attaching
current gCO2/kWh readings (per NESO region) to every edge.
"""

from __future__ import annotations

from dataclasses import dataclass

from .carbon_api import CarbonIntensityClient
from .topology import Node, Topology


@dataclass(frozen=True)
class WeightedEdge:
    to: str
    latency_ms: float
    carbon_gco2_per_kwh: float | None  # None only if carbon data was totally unavailable


@dataclass
class CarbonWeightedGraph:
    adjacency: dict[str, list[WeightedEdge]]
    nodes: dict[str, Node]
    data_source: str  # "live" | "cache" | "bundled_sample"
    carbon_available: bool

    def all_edges(self):
        for edges in self.adjacency.values():
            yield from edges


def build_carbon_weighted_graph(
    topology: Topology, client: CarbonIntensityClient | None = None
) -> CarbonWeightedGraph:
    """Attach live (or best-available fallback) carbon intensity to every edge.

    An edge's carbon figure is the average of its two endpoint regions'
    current gCO2/kWh - a deliberate, documented simplification: a backbone
    link physically spans two regions, and we don't have a finer-grained
    "per link" carbon signal, so we approximate it from the two nodes it
    connects.
    """
    client = client or CarbonIntensityClient()
    intensity_by_id, source = client.get_regional_intensity_by_id()

    adjacency: dict[str, list[WeightedEdge]] = {node_id: [] for node_id in topology.node_ids()}
    any_missing = False

    for edge in topology.edges:
        node_u = topology.get_node(edge.u)
        node_v = topology.get_node(edge.v)
        carbon = _edge_carbon(intensity_by_id, node_u, node_v)
        if carbon is None:
            any_missing = True

        adjacency[edge.u].append(
            WeightedEdge(to=edge.v, latency_ms=edge.latency_ms, carbon_gco2_per_kwh=carbon)
        )
        adjacency[edge.v].append(
            WeightedEdge(to=edge.u, latency_ms=edge.latency_ms, carbon_gco2_per_kwh=carbon)
        )

    return CarbonWeightedGraph(
        adjacency=adjacency,
        nodes=dict(topology.nodes),
        data_source=source,
        carbon_available=not any_missing,
    )


def _edge_carbon(intensity_by_id: dict[int, dict], node_u: Node, node_v: Node) -> float | None:
    reading_u = intensity_by_id.get(node_u.region_id)
    reading_v = intensity_by_id.get(node_v.region_id)
    if reading_u is None or reading_v is None:
        return None
    return (reading_u["gco2_per_kwh"] + reading_v["gco2_per_kwh"]) / 2
