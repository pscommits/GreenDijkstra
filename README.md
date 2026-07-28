# Green Dijkstra 

A single-page, interactive app: pick a sender and receiver on a live network topology, drag a latency↔carbon priority slider, and watch a carbon-aware route update in real time against real UK grid carbon data.

Classic Dijkstra finds the _fastest_ path through a network. This app finds the path that best balances speed against **carbon emitted per unit of electricity used**, using live UK National Energy System Operator (NESO) grid data — the greenest route between two points genuinely changes throughout the day as the generation mix shifts (more wind at night, more solar at midday, gas plants covering the gaps).

**Live at:** https://green-dijkstra-web.vercel.app/

---

## What this is (and isn't)

- **Carbon data is 100% real and live**, pulled from [NESO's public Carbon Intensity API](https://carbon-intensity.github.io/api-definitions/) for Great Britain — no API key required.
- **The network topology is a designed simulation** (13 nodes, one per real GB carbon-intensity region, connected by a plausible backbone mesh) — not any real operator's actual infrastructure, which isn't public.
- **The cost function is a weighted-sum trade-off, not carbon-only.** Optimising for carbon alone can produce a route with unacceptable added latency for latency-sensitive traffic. The app always shows both numbers, never hides the cost of going green.

---

## Architecture

```
Every request  ->  api/handler.py  (the single Vercel entrypoint)
                        |
        +---------------+----------------+
        |                                |
    /  /style.css  /app.js          /api/graph
    -> served from static/          /api/regions
                                    /api/route
                                         |
                                         v
                    lib/  -  topology, carbon client, graph builder,
                             both Dijkstra variants, metrics
                                         |
                                         | requests.get()
                                         v
                    NESO Carbon Intensity API (live)
                      -> /tmp cache -> data/samples/ bundled fallback
```

**One function serves everything — static files and JSON alike.** That's deliberate, and it's the part worth understanding if you ever modify this:

Vercel's Python runtime needs an explicit entrypoint declaration (`[tool.vercel] entrypoint = "api.handler:handler"` in `pyproject.toml`) whenever the handler file isn't one of its hardcoded default names. Declaring that entrypoint also switches the deployment into **whole-app mode**: _every_ incoming request routes to that function, including `/`. There's no separate static-file builder running alongside it — so if the function doesn't serve `index.html` itself, hitting the site root returns whatever the function says instead of the page.

Rather than fight that, `api/handler.py` embraces it: it inspects the request path, serves files out of `static/` for normal paths and JSON for `/api/*`. One routing model, one place to reason about it.

- **Frontend**: plain HTML/CSS/JS in `static/`, no build step. Fetches the topology once on load to draw it as SVG, the region ranking once to populate the sidebar list, and the route comparison on every sender/receiver/alpha change (debounced) to recompute and highlight both routes.
- **Backend**: `api/handler.py`, a minimal `http.server.BaseHTTPRequestHandler` — no Flask/FastAPI, just the stdlib plus `requests`.
- **Dependencies** live in `pyproject.toml`'s `[project]` table, not a `requirements.txt`. `uv` (which Vercel's Python builder uses) requires a valid `[project]` table in any `pyproject.toml` it finds, so the two sections have to coexist in that one file.

## The cost function

```
cost(edge) = alpha * (latency_ms / max_latency) + beta * (carbon_gco2 / max_carbon),   beta = 1 - alpha
```

- `alpha = 1.0` (slider fully right) → pure latency routing, identical to classic Dijkstra.
- `alpha = 0.0` (slider fully left) → pure carbon routing.
- Latency and carbon are min-max normalised against the graph's own edge values before combining — they're on wildly different numeric scales (milliseconds vs gCO2/kWh), so combining them raw would make the slider meaningless.
- An edge's carbon figure is the average of its two endpoint regions' live intensity — a link physically spans two regions and there's no finer-grained "per-link" carbon signal available publicly, so this is a documented simplification, not a claimed measurement.

Both the baseline and green routes share one generic heapq-based Dijkstra implementation (`O((V+E) log V)`) — the search never changes, only the cost function fed into it does.

---

## Project structure

```
green-dijkstra-web/
├── api/
│   └── handler.py           # THE entrypoint - routes everything: static files + all three API routes
│
├── static/                   # served by handler.py (not by a Vercel static builder - see Architecture)
│   ├── index.html             # the single page - header, topology SVG, controls, results, region list
│   ├── style.css               # all styling (light/dark via prefers-color-scheme, no framework)
│   └── app.js                   # all frontend logic - fetch, SVG drawing, event handling
│
├── lib/                         # the routing engine
│   ├── topology.py                # Node/Edge/Topology - loads data/topology.json
│   ├── carbon_api.py               # NESO API client + live -> cache -> bundled-sample fallback chain
│   ├── graph_builder.py             # attaches live carbon intensity onto topology edges
│   ├── routing.py                    # baseline_dijkstra() and green_dijkstra(), one shared Dijkstra core
│   └── metrics.py                     # comparison math (% carbon saved, latency added)
│
├── data/
│   ├── topology.json                  # the 13-node backbone topology definition
│   └── samples/regional_intensity.json  # bundled fallback snapshot (real data, just not necessarily current)
│
├── pyproject.toml                        # the one dependency (requests) + the explicit Vercel entrypoint declaration
├── vercel.json                           # bundles lib/, data/ and static/ into the function at build time
├── README.md                                # this file
```

This folder is fully self-contained — everything needed to run and deploy it lives inside it. You can zip this directory on its own and hand it to someone else; it doesn't depend on anything outside it.

---

## Local development

```bash
npm i -g vercel        # if you don't already have it
vercel dev
```

`vercel dev` runs the exact same Python function and static files locally, on the same runtime Vercel deploys to production — the most accurate way to test before pushing. It will ask you to log in and link the folder to a Vercel project the first time; see [INSTRUCTIONS.md](INSTRUCTIONS.md) for the full walkthrough.

---

## API reference

| Request                                  | Returns                                                                                                   |
| ---------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| `GET /api/graph`                         | `{ nodes: [...], edges: [...] }` — static topology structure                                              |
| `GET /api/regions`                       | `{ source, regions: [{region_id, node_id, shortname, gco2_per_kwh, index}, ...] }`, sorted greenest-first |
| `GET /api/route?from=ID&to=ID&alpha=0.5` | `{ data_source, carbon_available, baseline: {...}, green: {...}, comparison: {...} }`                     |

All respond with JSON and standard HTTP status codes (`400` for bad input, `404` for no path, `503` if carbon data is entirely unavailable).

## Data reliability: the fallback chain

Every carbon-data read in `lib/carbon_api.py` goes through the same three-step chain:

1. **Live API call** — cached to `/tmp` on success (Vercel's deployed source tree is read-only; `/tmp` is the one writable location, and this cache is a nice-to-have warm-instance optimisation, not load-bearing).
2. **Last known-good cache**, however stale, if the live call fails.
3. **Bundled sample snapshot** (`data/samples/`, committed to the repo) if there's no cache either — genuine fetched data, just not necessarily current.

If even the bundled sample is missing, the route endpoint returns a `503` with a clear error rather than crashing, and the frontend falls back to showing the baseline (latency-only) route alone.
