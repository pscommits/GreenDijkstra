"""Static network topology model: nodes (mapped to GB carbon-intensity
regions) and edges (backbone links with a base latency).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

# lib/topology.py -> parent = lib/, parents[1] = green-dijkstra-web/
# (one level shallower than the main package, since there's no src/ here)
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TOPOLOGY_PATH = _PROJECT_ROOT / "data" / "topology.json"


@dataclass(frozen=True)
class Node:
    id: str
    name: str
    region_id: int
    x: float
    y: float


@dataclass(frozen=True)
class Edge:
    u: str
    v: str
    latency_ms: float


class Topology:
    """A simple undirected graph of Nodes and Edges, loaded from JSON."""

    def __init__(self, nodes: list[Node], edges: list[Edge]) -> None:
        self.nodes: dict[str, Node] = {n.id: n for n in nodes}
        self.edges: list[Edge] = edges
        self._adjacency: dict[str, list[str]] = {n.id: [] for n in nodes}
        for e in edges:
            self._adjacency[e.u].append(e.v)
            self._adjacency[e.v].append(e.u)

    @classmethod
    def from_json(cls, path: Path | str = DEFAULT_TOPOLOGY_PATH) -> "Topology":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        nodes = [Node(**n) for n in raw["nodes"]]
        edges = [
            Edge(u=e["from"], v=e["to"], latency_ms=e["latency_ms"]) for e in raw["edges"]
        ]
        return cls(nodes, edges)

    def node_ids(self) -> list[str]:
        return list(self.nodes.keys())

    def get_node(self, node_id: str) -> Node:
        try:
            return self.nodes[node_id]
        except KeyError as exc:
            raise KeyError(f"Unknown node id {node_id!r}. Known ids: {self.node_ids()}") from exc

    def neighbors(self, node_id: str) -> list[str]:
        return self._adjacency[node_id]
