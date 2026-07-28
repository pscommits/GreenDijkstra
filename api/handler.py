"""The single entrypoint for the entire app - static files AND the JSON API.

Why one file handles everything
-------------------------------
Vercel's Python runtime requires an explicit entrypoint declaration
(`[tool.vercel] entrypoint` in pyproject.toml) whenever the handler file
isn't one of its hardcoded default names. Declaring that entrypoint also
puts the deployment into "whole-app" mode: *every* incoming request is
routed to this function, including `/`. There is no separate static-file
builder running alongside it.

So rather than fight that, this handler embraces it and serves both:

    GET /                          -> static/index.html
    GET /style.css, /app.js, ...   -> the matching file in static/
    GET /api/graph                 -> topology JSON
    GET /api/regions               -> live GB regional carbon intensity, ranked
    GET /api/route?from=&to=&alpha= -> baseline vs green route + comparison

One routing model, one place to reason about it. The app is a single page,
so this stays small.
"""

import json
import mimetypes
import os
import sys
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from lib.carbon_api import CarbonDataUnavailableError, CarbonIntensityClient  # noqa: E402
from lib.graph_builder import build_carbon_weighted_graph  # noqa: E402
from lib.metrics import compare_routes  # noqa: E402
from lib.routing import (  # noqa: E402
    CarbonDataRequiredError,
    NoPathError,
    baseline_dijkstra,
    green_dijkstra,
)
from lib.topology import Topology  # noqa: E402

STATIC_DIR = os.path.join(BASE_DIR, "static")

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".png": "image/png",
}


# ---------------------------------------------------------------------------
# API: /api/graph
# ---------------------------------------------------------------------------

def api_graph(_query):
    topology = Topology.from_json()
    return 200, {
        "nodes": [
            {"id": n.id, "name": n.name, "region_id": n.region_id, "x": n.x, "y": n.y}
            for n in topology.nodes.values()
        ],
        "edges": [
            {"from": e.u, "to": e.v, "latency_ms": e.latency_ms} for e in topology.edges
        ],
    }


# ---------------------------------------------------------------------------
# API: /api/regions
# ---------------------------------------------------------------------------

def api_regions(_query):
    topology = Topology.from_json()
    node_by_region = {n.region_id: n.id for n in topology.nodes.values()}

    try:
        by_id, source = CarbonIntensityClient().get_regional_intensity_by_id()
    except CarbonDataUnavailableError as exc:
        return 503, {"error": str(exc)}

    regions = [
        {
            "region_id": region_id,
            "node_id": node_by_region[region_id],
            "shortname": reading["shortname"],
            "gco2_per_kwh": reading["gco2_per_kwh"],
            "index": reading["index"],
        }
        for region_id, reading in by_id.items()
        if region_id in node_by_region  # only the 13 regions actually on the topology
    ]
    regions.sort(key=lambda r: r["gco2_per_kwh"])
    return 200, {"source": source, "regions": regions}


# ---------------------------------------------------------------------------
# API: /api/route?from=&to=&alpha=
# ---------------------------------------------------------------------------

def _route_json(result):
    return {
        "path": result.path,
        "latency_ms": result.total_latency_ms,
        "carbon_gco2": result.total_carbon_gco2_per_kwh,
        "alpha": result.alpha,
    }


def api_route(query):
    source = query.get("from", [None])[0]
    target = query.get("to", [None])[0]
    alpha_raw = query.get("alpha", [None])[0]

    if not source or not target:
        return 400, {"error": "Both 'from' and 'to' query parameters are required."}

    try:
        alpha = float(alpha_raw) if alpha_raw is not None else 0.5
    except ValueError:
        return 400, {"error": f"'alpha' must be a number, got {alpha_raw!r}."}

    try:
        graph = build_carbon_weighted_graph(Topology.from_json())
    except CarbonDataUnavailableError as exc:
        return 503, {"error": str(exc)}

    try:
        baseline = baseline_dijkstra(graph, source, target)
    except KeyError as exc:
        return 400, {"error": str(exc)}
    except NoPathError as exc:
        return 404, {"error": str(exc)}

    if not graph.carbon_available:
        return 200, {
            "data_source": graph.data_source,
            "carbon_available": False,
            "baseline": _route_json(baseline),
            "green": None,
            "comparison": None,
            "warning": "Carbon data unavailable for one or more regions - showing baseline only.",
        }

    try:
        green = green_dijkstra(graph, source, target, alpha=alpha)
    except (ValueError, CarbonDataRequiredError) as exc:
        return 400, {"error": str(exc)}

    comparison = compare_routes(source, target, baseline, green)
    return 200, {
        "data_source": graph.data_source,
        "carbon_available": True,
        "baseline": _route_json(baseline),
        "green": _route_json(green),
        "comparison": {
            "carbon_saved_gco2": comparison.carbon_saved_gco2,
            "pct_carbon_saved": comparison.pct_carbon_saved,
            "latency_added_ms": comparison.latency_added_ms,
            "pct_latency_added": comparison.pct_latency_added,
        },
    }


API_ROUTES = {
    "/api/graph": api_graph,
    "/api/regions": api_regions,
    "/api/route": api_route,
}


# ---------------------------------------------------------------------------
# Static file resolution
# ---------------------------------------------------------------------------

def resolve_static(path: str):
    """Map a URL path to a file inside static/, or None if there isn't one.

    Rejects anything that escapes static/ (e.g. '/../lib/carbon_api.py') by
    resolving the real path and confirming it's still inside the directory.
    """
    relative = "index.html" if path in ("", "/") else path.lstrip("/")
    candidate = os.path.normpath(os.path.join(STATIC_DIR, relative))

    static_root = os.path.realpath(STATIC_DIR)
    resolved = os.path.realpath(candidate)
    if resolved != static_root and not resolved.startswith(static_root + os.sep):
        return None
    if not os.path.isfile(resolved):
        return None
    return resolved


def content_type_for(file_path: str) -> str:
    extension = os.path.splitext(file_path)[1].lower()
    if extension in CONTENT_TYPES:
        return CONTENT_TYPES[extension]
    guessed, _ = mimetypes.guess_type(file_path)
    return guessed or "application/octet-stream"


# ---------------------------------------------------------------------------
# The handler Vercel invokes (declared in pyproject.toml as api.handler:handler)
# ---------------------------------------------------------------------------

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        api_fn = API_ROUTES.get(path)
        if api_fn is not None:
            status, payload = api_fn(parse_qs(parsed.query))
            self._send_json(status, payload)
            return

        if path.startswith("/api"):
            self._send_json(404, {
                "error": f"Unknown API route {path!r}.",
                "available": sorted(API_ROUTES),
            })
            return

        file_path = resolve_static(parsed.path)
        if file_path is None:
            self._send_json(404, {"error": f"Not found: {parsed.path}"})
            return
        self._send_file(file_path)

    # -- response helpers ----------------------------------------------------

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, file_path: str) -> None:
        with open(file_path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type_for(file_path))
        self.send_header("Content-Length", str(len(body)))
        # The page itself must not be cached hard, or a redeploy leaves users
        # on a stale build; the API sets its own no-store above.
        self.send_header("Cache-Control", "public, max-age=0, must-revalidate")
        self.end_headers()
        self.wfile.write(body)
