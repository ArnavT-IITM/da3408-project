"use strict";

// config.js is written by the container from AIRCAST_API_URL.
const API = (window.AIRCAST_API_URL || "/api").replace(/\/$/, "");
const $ = (id) => document.getElementById(id);
const state = { view: "forecast", catalog: null, status: {}, simToday: null, daysLeft: null, city: null, token: 0, forecastKey: null, busy: false, lastReplay: null };
const CATEGORIES = [[50, "Good", "good"], [100, "Satisfactory", "satisfactory"], [200, "Moderate", "moderate"], [300, "Poor", "poor"], [400, "Very poor", "very-poor"], [Infinity, "Severe", "severe"]];
const SERVICES = { api: "API", frontend: "Frontend", "model-server": "Model server", mlflow: "MLflow", prometheus: "Prometheus", grafana: "Grafana", "node-exporter": "Node exporter" };

const categoryOf = (value) => CATEGORIES.find(([upper]) => Math.round(value) <= upper);
// Light category colours need dark text.
const textOn = (key) => ["satisfactory", "moderate", "poor"].includes(key) ? "#14202b" : "#ffffff";
const number = (value, digits = 0) => value == null ? "—" : Number(value).toLocaleString(undefined, { maximumFractionDigits: digits });
const compact = (value) => value == null ? "—" : Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 }).format(value);
const day = (iso) => new Date(`${iso}T12:00:00Z`).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
const clock = (unix) => new Date(unix * 1000).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
const addDays = (iso, days) => new Date(Date.parse(`${iso}T00:00:00Z`) + days * 86400000).toISOString().slice(0, 10);
const daysBetween = (from, to) => Math.round((Date.parse(to) - Date.parse(from)) / 86400000);

function element(tag, attributes = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attributes)) {
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else node.setAttribute(key, value);
  }
  node.append(...children);
  return node;
}

function svg(tag, attributes = {}, text = "") {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
  node.textContent = text;
  return node;
}

function showError(message) {
  $("toast-text").textContent = message;
  $("toast").hidden = false;
}

async function request(path, body) {
  let response;
  try {
    response = await fetch(API + path, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  } catch {
    throw new Error("Cannot reach AirCast. Check that the services are running.");
  }
  const data = await response.json().catch(() => ({}));
  if (response.ok) return data;
  const error = new Error(Array.isArray(data.detail) ? data.detail.map((item) => item.msg).join("; ") : data.detail || `Request failed (${response.status}).`);
  error.status = response.status;
  throw error;
}

// Run one button action with every button disabled meanwhile.
async function act(action) {
  state.busy = true;
  updateButtons();
  try { await action(); } catch (error) { showError(error.message); }
  state.busy = false;
  await refresh();
}

function showView(name) {
  state.view = ["forecast", "accuracy", "operations"].includes(name) ? name : "forecast";
  for (const tab of document.querySelectorAll("[role=tab]")) {
    const selected = tab.dataset.view === state.view;
    tab.setAttribute("aria-selected", String(selected));
    $(tab.getAttribute("aria-controls")).hidden = !selected;
  }
  history.replaceState(null, "", `#${state.view}`);
  $("clockbar").hidden = state.view === "operations";
  refresh();
}

// ---- Forecast: tomorrow for every city, from the simulated today -------------------------------

// These are the same requests the replay makes for that day, and the API stores each one once.
async function loadForecasts() {
  const today = state.simToday, cities = state.catalog?.cities;
  if (!today || !cities) return;
  state.forecastKey = `${today}|${state.status.model?.version}`;
  const token = ++state.token;
  if (!state.daysLeft) {
    $("fc-date").textContent = "This is the end of the recorded data. Press Start over to replay it again.";
    $("fc-grid").replaceChildren();
    return;
  }
  $("fc-date").textContent = `Forecast for ${day(addDays(today, 1))}, made from the measurements of ${day(today)}.`;
  $("fc-grid").setAttribute("aria-busy", "true");
  const results = await Promise.allSettled(cities.map((city) => request("/predict", { city, date: today })));
  if (token !== state.token) return;
  $("fc-grid").replaceChildren(...results.map((result, index) => forecastCard(cities[index], result)));
  $("fc-grid").setAttribute("aria-busy", "false");
}

function forecastCard(city, result) {
  const card = element("article", { class: "fc" }, element("h2", { text: city }));
  if (result.status === "rejected") {
    const code = result.reason.status;
    const text = code === 422 ? "No measurements today, so no forecast." : code === 503 ? "The model is reloading. This updates in a few seconds." : result.reason.message;
    card.append(element("p", { class: "fc-missing", text }));
    return card;
  }
  const { predicted_aqi: aqi, today_aqi: today, guidance } = result.value;
  const [, name, key] = categoryOf(aqi);
  const change = aqi - today;
  card.style.setProperty("--cat", `var(--${key})`);
  const label = element("span", { class: "fc-cat", text: name });
  label.style.color = textOn(key);
  const compared = Math.abs(change) < 5 ? "About the same as" : change > 0 ? `▲ ${number(change)} worse than` : `▼ ${number(-change)} better than`;
  card.append(
    element("div", { class: "fc-value" }, element("span", { class: "fc-aqi", text: number(aqi) }), element("span", { class: "unit", text: "AQI" })),
    label,
    element("p", { class: "fc-change", text: `${compared} today (${number(today)})` }),
    element("p", { class: "fc-guide", text: guidance }),
  );
  return card;
}

// ---- Accuracy and the shared simulated clock -----------------------------------------------------

async function syncClock() {
  await loadAccuracy();
  if (state.simToday) $("goto-date").value = addDays(state.simToday, Math.min(7, Math.max(state.daysLeft - 1, 0)));
  await loadForecasts();
}

async function loadAccuracy() {
  let data;
  try {
    data = await request(`/accuracy?city=${encodeURIComponent(state.city)}&days=60`);
  } catch (error) { showError(error.message); return; }
  state.simToday = data.simulated_today;
  state.daysLeft = Math.max(0, daysBetween(data.simulated_today, data.last_day) + 1);
  $("goto-date").min = state.catalog.dates.min;
  $("goto-date").max = data.last_day;
  $("sim-date").textContent = day(data.simulated_today);
  $("sim-left").textContent = state.daysLeft ? `${number(state.daysLeft)} days of data left` : "End of the recorded data";
  $("city-scores").replaceChildren(...data.by_city.map((row) => {
    const button = element("button", { class: "city-score", type: "button", "aria-pressed": String(row.city === state.city) },
      element("span", { class: "name", text: row.city }),
      element("span", { class: "big", text: row.mae == null ? "—" : `± ${number(row.mae)}` }),
      element("span", { class: "sub", text: row.scored ? `average error over ${number(row.scored)} forecasts` : "not scored yet" }));
    button.addEventListener("click", () => { state.city = row.city; loadAccuracy(); });
    return button;
  }));
  $("chart-city").textContent = state.city;
  renderChart(data.points);
  const latest = [...data.points].reverse().slice(0, 8);
  $("scored").replaceChildren(...(latest.length
    ? latest.map((p) => element("tr", {}, element("td", { text: day(p.forecast_date) }), ...[p.predicted_aqi, p.actual_aqi, p.error].map((v) => element("td", { class: "num", text: number(v) }))))
    : [element("tr", {}, element("td", { colspan: "4", class: "empty", text: "Nothing checked yet for this city." }))]));
  updateButtons();
}

function renderChart(points) {
  if (points.length < 2) {
    $("chart").replaceChildren(element("p", { class: "empty", text: "Press Next day a few times to see results here." }));
    return;
  }
  const width = 900, height = 260, left = 44, right = 12, top = 12, bottom = 28;
  const ceiling = Math.ceil(Math.max(50, ...points.flatMap((p) => [p.predicted_aqi, p.actual_aqi])) / 50) * 50;
  const x = (i) => left + (i / (points.length - 1)) * (width - left - right);
  const y = (value) => top + (1 - value / ceiling) * (height - top - bottom);
  const chart = svg("svg", { viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": `Forecast and actual AQI for ${state.city}` });
  for (const value of [0, ceiling / 2, ceiling]) {
    chart.append(svg("line", { x1: left, x2: width - right, y1: y(value), y2: y(value), class: "grid" }),
      svg("text", { x: left - 8, y: y(value) + 4, "text-anchor": "end", class: "axis" }, number(value)));
  }
  const path = (key) => points.map((p, i) => `${i ? "L" : "M"}${x(i)},${y(p[key])}`).join(" ");
  chart.append(svg("path", { d: path("actual_aqi"), class: "actual" }), svg("path", { d: path("predicted_aqi"), class: "forecast" }));
  // Invisible columns give each day a hover tooltip.
  const slot = (width - left - right) / points.length;
  points.forEach((p, i) => {
    const hit = svg("rect", { x: x(i) - slot / 2, y: top, width: slot, height: height - top - bottom, class: "hit" });
    hit.append(svg("title", {}, `${day(p.forecast_date)}: forecast ${number(p.predicted_aqi)}, actual ${number(p.actual_aqi)}`));
    chart.append(hit);
  });
  for (const i of [0, points.length - 1]) {
    chart.append(svg("text", { x: x(i), y: height - 6, "text-anchor": i ? "end" : "start", class: "axis" }, day(points[i].forecast_date)));
  }
  $("chart").replaceChildren(chart);
}

function renderReplay(replay) {
  const running = Boolean(replay.running), total = replay.total_days || 0, done = replay.completed_days || 0;
  $("progress").hidden = !running;
  $("progress-bar").style.width = total ? `${Math.round((done / total) * 100)}%` : "0%";
  $("sim-status").textContent = replay.error ? `The last run stopped: ${replay.error}`
    : running ? `Simulating day ${number(Math.min(done + 1, total))} of ${number(total)}…`
    : replay.status === "success" ? `Moved forward ${number(done)} ${done === 1 ? "day" : "days"}. Forecasts for the days that passed were checked against what really happened.`
    : "AirCast replays real data from 2020. Press Next day to move time forward.";
  if (state.lastReplay === "running" && !running) syncClock();
  state.lastReplay = running ? "running" : replay.status;
}

function advance(days) {
  // Long jumps run faster per day so even the whole period takes about 20 seconds.
  return act(async () => {
    await request("/replay", { days, delay: days > 30 ? 0.1 : 0.3 });
    state.lastReplay = "running";
  });
}

// Going back means starting over and replaying forward, so every day on the way is scored.
async function goTo(event) {
  event.preventDefault();
  const target = $("goto-date").value;
  if (!target || !$("goto-date").checkValidity()) return showError(`Choose a date between ${day($("goto-date").min)} and ${day($("goto-date").max)}.`);
  let from = state.simToday;
  if (target === from) return;
  if (target < from) {
    if (!confirm(`Going back to ${day(target)} replays the data from the start up to that date. Continue?`)) return;
    try { from = (await request("/replay/reset", {})).simulated_today; } catch (error) { return showError(error.message); }
  }
  if (target > from) await advance(daysBetween(from, target));
  else await syncClock();
}

async function resetSimulation() {
  if (!confirm("Go back to the start of the data? The accuracy scores start again from zero.")) return;
  await act(async () => {
    await request("/replay/reset", {});
    await syncClock();
  });
  $("sim-status").textContent = "Back at the start. Press Next day to move forward.";
}

// ---- Operations -----------------------------------------------------------------------------------

function renderStatus(data) {
  state.status = data;
  $("conn").querySelector(".dot").className = "dot up";
  $("conn-text").textContent = "Connected";
  const { version, status, previous_version: previous } = data.model;
  $("model-current").textContent = version ? `Version ${version}${status === "ready" ? "" : ` (${status})`}` : "None yet";
  $("model-previous").textContent = previous ? `Version ${previous}` : "None";
  $("switch-model").textContent = previous ? `Switch to version ${previous}` : "Switch to previous";
  const links = { "model-run": "mlflow_run", "link-grafana": "grafana", "link-mlflow": "mlflow", "link-prometheus": "prometheus", "link-api": "api_docs" };
  for (const [id, key] of Object.entries(links)) {
    $(id).hidden = !data.links[key];
    $(id).href = data.links[key] || "";
  }
  const services = Object.entries(data.services);
  if (services.length) {
    $("services").replaceChildren(...services.map(([name, up]) => element("li", {}, element("span", { text: SERVICES[name] || name }),
      element("span", { class: `pill ${up ? "success" : "failed"}`, text: up ? "Healthy" : "Down" }))));
  }
  renderReplay(data.replay);
  updateButtons();
}

// Draw `dvc dag --mermaid` left to right, each node in the column of its longest path from the data.
function renderDag(text, stages) {
  const nodes = new Map(), edges = [];
  for (const line of text.split("\n")) {
    const node = line.match(/^\s*(\w+)\["(.+)"\]\s*$/), edge = line.match(/^\s*(\w+)-->(\w+)\s*$/);
    if (node) nodes.set(node[1], node[2]);
    if (edge) edges.push({ from: edge[1], to: edge[2] });
  }
  const evaluate = [...nodes].find(([, label]) => label === "evaluate");
  if (!evaluate) return;
  nodes.set("deploy", "deploy");
  edges.push({ from: evaluate[0], to: "deploy", dashed: true });
  const level = new Map([...nodes.keys()].map((id) => [id, 0]));
  for (let pass = 0; pass < nodes.size; pass += 1) edges.forEach(({ from, to }) => level.set(to, Math.max(level.get(to), level.get(from) + 1)));
  const rows = new Map(), row = new Map();
  for (const id of nodes.keys()) {
    row.set(id, rows.get(level.get(id)) || 0);
    rows.set(level.get(id), row.get(id) + 1);
  }
  const boxWidth = 122, boxHeight = 52, gap = 146, rowGap = 80, top = 70;
  const x = (id) => 10 + level.get(id) * gap, y = (id) => top + row.get(id) * rowGap;
  const width = 20 + (Math.max(...level.values()) + 1) * gap, height = top + Math.max(...rows.values()) * rowGap;
  const graph = svg("svg", { viewBox: `0 0 ${width} ${height}`, width, height, role: "img", "aria-label": "Pipeline graph from dvc dag" });
  const marker = svg("marker", { id: "arrow", viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: "auto" });
  marker.append(svg("path", { d: "M0,0 L10,5 L0,10 z" }));
  graph.append(marker);
  let arcs = 0;
  for (const { from, to, dashed } of edges) {
    let d;
    // A stage that reads an output from more than one column back gets an arc over the boxes in between.
    if (level.get(to) - level.get(from) > 1 && row.get(from) === row.get(to)) {
      arcs += 1;
      const x1 = x(from) + 28 + arcs * 14, x2 = x(to) + boxWidth - 28 - arcs * 14, lift = y(from) - 12 - arcs * 14;
      d = `M${x1},${y(from)} C${x1},${lift} ${x2},${lift} ${x2},${y(to) - 2}`;
    } else {
      const x1 = x(from) + boxWidth, y1 = y(from) + boxHeight / 2, x2 = x(to) - 2, y2 = y(to) + boxHeight / 2;
      d = `M${x1},${y1} C${x1 + 16},${y1} ${x2 - 16},${y2} ${x2},${y2}`;
    }
    graph.append(svg("path", { d, class: dashed ? "edge dashed" : "edge", "marker-end": "url(#arrow)" }));
  }
  const status = Object.fromEntries(stages.map((stage) => [stage.name, stage.status]));
  nodes.forEach((label, id) => {
    const isData = label.endsWith(".dvc"), nodeStatus = isData ? "data" : status[label] || "pending";
    const group = svg("g", { class: `node ${nodeStatus}` });
    group.append(svg("rect", { x: x(id), y: y(id), width: boxWidth, height: boxHeight, rx: 10 }),
      svg("text", { x: x(id) + 12, y: y(id) + 22 }, isData ? "Raw data" : label[0].toUpperCase() + label.slice(1)),
      svg("text", { x: x(id) + 12, y: y(id) + 40, class: "status" }, isData ? "tracked by DVC" : nodeStatus));
    graph.append(group);
  });
  $("dag").replaceChildren(graph);
}

function mlflowLink(record) {
  if (!record?.run_id || !record.experiment_id) return "";
  return element("a", { href: `${state.status.links.mlflow}/#/experiments/${record.experiment_id}/runs/${record.run_id}`, target: "_blank", rel: "noopener", text: "MLflow run ↗" });
}

function renderPipeline(data) {
  state.jobRunning = data.running;
  const run = data.runs.find((item) => item.kind === "pipeline");
  $("pipeline-state").className = `pill ${data.running ? "running" : run?.status || ""}`;
  $("pipeline-state").textContent = data.running ? "Job running" : run ? `Last run: ${run.status}` : "Not run yet";
  renderDag(data.dag, data.stages);
  $("stages").replaceChildren(...data.stages.map((stage) => element("tr", {},
    element("td", { text: stage.name[0].toUpperCase() + stage.name.slice(1) }),
    element("td", {}, element("span", { class: `pill ${stage.status}`, text: stage.status })),
    element("td", { class: "num", text: stage.duration_seconds == null ? "—" : `${number(stage.duration_seconds, 1)} s` }),
    element("td", { class: "num", text: compact(stage.rows) }),
    element("td", { class: "num", text: compact(stage.rows_per_second) }),
    element("td", {}, stage.error ? element("span", { class: "small", text: stage.error.split("\n")[0] }) : mlflowLink(stage)))));
  // Replays belong to the clock, not here. An opened error stays open across refreshes.
  const open = new Set([...document.querySelectorAll("#runs details[open]")].map((node) => node.dataset.id));
  const runs = data.runs.filter((item) => item.kind !== "replay").slice(0, 8);
  $("runs").replaceChildren(...(runs.length ? runs.map((item) => {
    let detail;
    if (item.error) {
      const [first, ...rest] = item.error.split("\n");
      detail = element("details", { "data-id": item.id }, element("summary", { text: first }), element("pre", { text: rest.join("\n").trim() || first }));
      detail.open = open.has(item.id);
    } else {
      detail = (item.kind === "pipeline" && mlflowLink(item.result)) || element("span", { class: "small", text: item.result?.version ? `Now serving version ${item.result.version}` : "" });
    }
    return element("li", {}, element("span", { class: "when", text: clock(item.started_at) }),
      element("div", {}, element("strong", { text: `${item.kind[0].toUpperCase()}${item.kind.slice(1)} ` }), element("span", { class: `pill ${item.status}`, text: item.status }), element("div", {}, detail)),
      element("span", { class: "small", text: item.ended_at ? `${number(item.ended_at - item.started_at, 1)} s` : "running" }));
  }) : [element("li", { class: "empty", text: "No runs yet." })]));
  $("log").textContent = data.log_tail || "No output yet.";
}

function switchModel() {
  const target = state.status.model.previous_version;
  if (!confirm(`Switch serving to model version ${target}? Forecasts pause for a few seconds while it loads.`)) return;
  return act(() => request("/models/rollback", {}));
}

function updateButtons() {
  const blocked = state.busy || state.jobRunning || state.status.replay?.running;
  const ready = state.status.model?.status === "ready";
  document.querySelectorAll("[data-advance]").forEach((button) => { button.disabled = blocked || !ready || state.daysLeft === 0; });
  $("reset").disabled = blocked;
  $("goto").disabled = blocked || !ready;
  $("run-pipeline").disabled = blocked;
  $("switch-model").disabled = blocked || !state.status.model?.previous_version;
}

// Poll every 3 seconds; each view loads only what it shows.
async function refresh() {
  if (document.hidden) return;
  try {
    const [status, pipeline] = await Promise.all([request("/status"), request("/pipeline")]);
    state.jobRunning = pipeline.running;
    renderStatus(status);
    if (state.view === "operations") renderPipeline(pipeline);
    else if (state.status.replay.running) {
      await loadAccuracy();
      $("fc-grid").setAttribute("aria-busy", "true");
    } else if (state.forecastKey !== `${state.simToday}|${state.status.model.version}`) await loadForecasts();
  } catch {
    $("conn").querySelector(".dot").className = "dot down";
    $("conn-text").textContent = "Offline";
  }
  updateButtons();
}

document.querySelectorAll("[role=tab]").forEach((tab) => tab.addEventListener("click", () => showView(tab.dataset.view)));
document.querySelectorAll("[data-advance]").forEach((button) => button.addEventListener("click", () => advance(Number(button.dataset.advance))));
$("reset").addEventListener("click", resetSimulation);
$("goto-form").addEventListener("submit", goTo);
$("run-pipeline").addEventListener("click", () => act(() => request("/pipeline/run", {})));
$("switch-model").addEventListener("click", switchModel);
$("toast-close").addEventListener("click", () => { $("toast").hidden = true; });
$("help-open").addEventListener("click", () => $("help").showModal());
$("help-close").addEventListener("click", () => $("help").close());
document.addEventListener("visibilitychange", refresh);

(async () => {
  try {
    state.catalog = await request("/catalog");
  } catch (error) {
    $("fc-date").textContent = `Forecasts are unavailable. ${error.message}`;
    return;
  }
  state.city = state.catalog.cities[0];
  try { renderStatus(await request("/status")); } catch { /* the next refresh shows it as offline */ }
  await syncClock();
  showView(location.hash.slice(1));
  setInterval(refresh, 3000);
})();
