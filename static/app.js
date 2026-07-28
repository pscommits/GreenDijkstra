// Green Dijkstra — live demo frontend. No framework, no build step: fetch
// /api/graph, /api/regions and /api/route as JSON, and render.

const SVG_NS = "http://www.w3.org/2000/svg";
const PADDING = 60;

const svg = document.getElementById("topology-svg");
const fromSelect = document.getElementById("from-select");
const toSelect = document.getElementById("to-select");
const alphaSlider = document.getElementById("alpha-slider");
const alphaLabel = document.getElementById("alpha-label");
const statusLine = document.getElementById("status-line");
const results = document.getElementById("results");
const dataSourceBadge = document.getElementById("data-source-badge");

const INDEX_COLORS = {
  "very low": "#00b894",
  "low": "#55efc4",
  "moderate": "#fdcb6e",
  "high": "#e17055",
  "very high": "#d63031",
};

let topology = null; // { nodes: [...], edges: [...] }
let debounceTimer = null;

init();

async function init() {
  try {
    topology = await fetchJson("/api/graph");
  } catch (err) {
    statusLine.textContent = "Could not load network topology. Refresh to retry.";
    return;
  }

  drawTopology(topology);
  populateNodeSelects(topology.nodes);
  loadRegions();

  alphaSlider.addEventListener("input", () => {
    updateAlphaLabel();
    scheduleRoute();
  });
  fromSelect.addEventListener("change", scheduleRoute);
  toSelect.addEventListener("change", scheduleRoute);

  updateAlphaLabel();
  scheduleRoute();
}

function fetchJson(url) {
  return fetch(url).then((res) => {
    if (!res.ok) throw new Error(`${url} -> ${res.status}`);
    return res.json();
  });
}

// ---- Topology drawing --------------------------------------------------

function drawTopology(topo) {
  const xs = topo.nodes.map((n) => n.x);
  const ys = topo.nodes.map((n) => n.y);
  const minX = Math.min(...xs), maxX = Math.max(...xs);
  const minY = Math.min(...ys), maxY = Math.max(...ys);

  const viewW = 700 - PADDING * 2;
  const viewH = 620 - PADDING * 2;

  const project = (x, y) => ({
    px: PADDING + ((x - minX) / (maxX - minX || 1)) * viewW,
    // flip Y so higher latitude-ish values render nearer the top
    py: PADDING + (1 - (y - minY) / (maxY - minY || 1)) * viewH,
  });

  const positions = {};
  topo.nodes.forEach((n) => (positions[n.id] = project(n.x, n.y)));

  svg.innerHTML = "";

  topo.edges.forEach((edge) => {
    const a = positions[edge.from];
    const b = positions[edge.to];
    const line = document.createElementNS(SVG_NS, "line");
    line.setAttribute("x1", a.px);
    line.setAttribute("y1", a.py);
    line.setAttribute("x2", b.px);
    line.setAttribute("y2", b.py);
    line.setAttribute("class", "edge-line");
    line.dataset.from = edge.from;
    line.dataset.to = edge.to;
    svg.appendChild(line);
  });

  topo.nodes.forEach((n) => {
    const { px, py } = positions[n.id];
    const circle = document.createElementNS(SVG_NS, "circle");
    circle.setAttribute("cx", px);
    circle.setAttribute("cy", py);
    circle.setAttribute("r", 22);
    circle.setAttribute("class", "node-circle");
    svg.appendChild(circle);

    const label = document.createElementNS(SVG_NS, "text");
    label.setAttribute("x", px);
    label.setAttribute("y", py);
    label.setAttribute("class", "node-label");
    label.textContent = n.id;
    svg.appendChild(label);
  });
}

function highlightPaths(baselinePath, greenPath) {
  svg.querySelectorAll(".edge-line").forEach((line) => {
    line.classList.remove("baseline", "green");
  });
  paintPath(baselinePath, "baseline");
  if (greenPath) paintPath(greenPath, "green");
}

function paintPath(path, className) {
  if (!path) return;
  for (let i = 0; i < path.length - 1; i++) {
    const u = path[i], v = path[i + 1];
    const line = svg.querySelector(
      `.edge-line[data-from="${u}"][data-to="${v}"], .edge-line[data-from="${v}"][data-to="${u}"]`
    );
    if (line) line.classList.add(className);
  }
}

// ---- Controls ------------------------------------------------------------

function populateNodeSelects(nodes) {
  const sorted = [...nodes].sort((a, b) => a.name.localeCompare(b.name));
  for (const select of [fromSelect, toSelect]) {
    select.innerHTML = "";
    sorted.forEach((n) => {
      const opt = document.createElement("option");
      opt.value = n.id;
      opt.textContent = `${n.name} (${n.id})`;
      select.appendChild(opt);
    });
  }
  // Pick two distinct defaults so the page shows something meaningful immediately.
  fromSelect.value = sorted[0].id;
  toSelect.value = sorted[Math.min(3, sorted.length - 1)].id;
}

function updateAlphaLabel() {
  const alpha = parseFloat(alphaSlider.value);
  let text;
  if (alpha >= 0.95) text = "latency only";
  else if (alpha <= 0.05) text = "carbon only";
  else if (alpha > 0.55) text = "latency-leaning";
  else if (alpha < 0.45) text = "carbon-leaning";
  else text = "balanced";
  alphaLabel.textContent = `${text} (α=${alpha.toFixed(2)})`;
}

function scheduleRoute() {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(loadRoute, 250);
}

// ---- Route fetching + rendering ------------------------------------------

async function loadRoute() {
  const from = fromSelect.value;
  const to = toSelect.value;
  const alpha = alphaSlider.value;

  if (!from || !to) return;
  if (from === to) {
    statusLine.textContent = "Pick two different nodes.";
    results.hidden = true;
    highlightPaths(null, null);
    return;
  }

  statusLine.textContent = "Computing route…";

  let data;
  try {
    data = await fetchJson(`/api/route?from=${from}&to=${to}&alpha=${alpha}`);
  } catch (err) {
    statusLine.textContent = "Could not compute a route. Try again.";
    return;
  }

  renderRoute(data);
}

function renderRoute(data) {
  statusLine.textContent = data.warning || "";
  setDataSourceBadge(data.data_source);

  document.getElementById("baseline-path").textContent = data.baseline.path.join(" → ");
  document.getElementById("baseline-latency").textContent = data.baseline.latency_ms.toFixed(1);
  document.getElementById("baseline-carbon").textContent = data.baseline.carbon_gco2.toFixed(1);

  const greenCard = document.querySelector(".result-card.green");
  const summaryCard = document.getElementById("comparison-summary");

  if (!data.carbon_available) {
    greenCard.hidden = true;
    summaryCard.textContent = "Carbon data is unavailable right now — showing the baseline route only.";
    highlightPaths(data.baseline.path, null);
  } else {
    greenCard.hidden = false;
    document.getElementById("green-path").textContent = data.green.path.join(" → ");
    document.getElementById("green-latency").textContent = data.green.latency_ms.toFixed(1);
    document.getElementById("green-carbon").textContent = data.green.carbon_gco2.toFixed(1);

    const c = data.comparison;
    if (data.baseline.path.join(",") === data.green.path.join(",")) {
      summaryCard.textContent = "At this priority setting, the green route is identical to the baseline — no alternative path was worth taking.";
    } else {
      summaryCard.innerHTML =
        `<strong>${c.pct_carbon_saved >= 0 ? "-" : "+"}${Math.abs(c.pct_carbon_saved).toFixed(1)}% carbon</strong>` +
        ` for <strong>${c.latency_added_ms >= 0 ? "+" : ""}${c.latency_added_ms.toFixed(1)}ms` +
        ` (${c.pct_latency_added >= 0 ? "+" : ""}${c.pct_latency_added.toFixed(1)}%) latency</strong>.`;
    }
    highlightPaths(data.baseline.path, data.green.path);
  }

  results.hidden = false;
  statusLine.textContent = data.warning || "";
}

function setDataSourceBadge(source) {
  if (!source) {
    dataSourceBadge.hidden = true;
    return;
  }
  const labels = { live: "live data", cache: "cached data", bundled_sample: "sample data" };
  dataSourceBadge.textContent = labels[source] || source;
  dataSourceBadge.hidden = false;
}

// ---- Region ranking --------------------------------------------------------

async function loadRegions() {
  const list = document.getElementById("regions-list");
  try {
    const data = await fetchJson("/api/regions");
    list.innerHTML = "";
    data.regions.forEach((r) => {
      const li = document.createElement("li");
      const color = INDEX_COLORS[r.index] || "#b2bec3";
      li.innerHTML =
        `<span class="region-name"><i class="index-dot" style="background:${color}"></i>${r.shortname}</span>` +
        `<span class="region-value">${r.gco2_per_kwh} gCO2/kWh</span>`;
      list.appendChild(li);
    });
  } catch (err) {
    list.innerHTML = "<li>Could not load region data.</li>";
  }
}
