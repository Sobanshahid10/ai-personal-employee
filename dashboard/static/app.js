"use strict";

const API = "/api";
const TOKEN_KEY = "chiefmindApprovalToken";
const REFRESH_MS = 30_000;
const viewLabels = {
  dashboard: "Dashboard",
  pending_approval: "Pending Approval",
  needs_action: "Needs Action",
  done: "Done",
  plans: "Plans",
  activity: "Activity Log",
  failed: "Failed",
};
const state = { view: "dashboard", stats: null, busy: false };

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

async function apiFetch(path, options = {}, mayPrompt = true) {
  const headers = new Headers(options.headers || {});
  const token = sessionStorage.getItem(TOKEN_KEY);
  if (token) headers.set("X-Approval-Token", token);
  const response = await fetch(`${API}${path}`, { ...options, headers });

  if (response.status === 403 && mayPrompt) {
    sessionStorage.removeItem(TOKEN_KEY);
    const supplied = window.prompt("Approval token required. Enter it to continue:");
    if (supplied?.trim()) {
      sessionStorage.setItem(TOKEN_KEY, supplied.trim());
      return apiFetch(path, options, false);
    }
  }

  let payload = {};
  try { payload = await response.json(); } catch { /* handled below */ }
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status})`);
  return payload;
}

function showAlert(message, kind = "error") {
  const alert = $("#alert");
  alert.textContent = message;
  alert.classList.remove("hidden");
  alert.style.borderColor = kind === "success" ? "rgba(85,216,155,.35)" : "";
  window.clearTimeout(showAlert.timer);
  showAlert.timer = window.setTimeout(() => alert.classList.add("hidden"), 5000);
}

function formatDate(value) {
  const date = value ? new Date(value) : null;
  return date && !Number.isNaN(date.valueOf())
    ? new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date)
    : "Time unavailable";
}

function emptyState(title, message) {
  const box = document.createElement("div");
  box.className = "empty-state";
  const strong = document.createElement("strong");
  strong.textContent = title;
  box.append(strong, document.createTextNode(message));
  return box;
}

function renderKpis(stats) {
  const counts = stats.counts || {};
  const cards = [
    ["Pending Approval", counts.pending_approval || 0, "Human decision required", "#8b7cff", "✓"],
    ["Needs Action", counts.needs_action || 0, "Waiting for reasoning", "#47d7d0", "◇"],
    ["Done", counts.done || 0, "Successfully completed", "#55d89b", "●"],
    ["Failed", counts.failed || 0, "Requires investigation", "#ff7185", "!"],
    ["Plans", counts.plans || 0, "Generated action plans", "#ffc96b", "≡"],
    ["Total Items", stats.total_items || 0, "Across all workflows", "#75a7ff", "∑"],
  ];
  const grid = $("#kpi-grid");
  grid.replaceChildren(...cards.map(([label, value, note, color, icon]) => {
    const card = document.createElement("article");
    card.className = "kpi-card";
    card.style.setProperty("--accent", color);
    card.innerHTML = `<div class="kpi-top"><span></span><i class="kpi-icon"></i></div><div class="kpi-value"></div><div class="kpi-note"></div>`;
    $(".kpi-top span", card).textContent = label;
    $(".kpi-icon", card).textContent = icon;
    $(".kpi-value", card).textContent = value;
    $(".kpi-note", card).textContent = note;
    return card;
  }));
  $$('[data-count]').forEach(node => { node.textContent = counts[node.dataset.count] || 0; });
}

function itemCard(item, withActions = false) {
  const card = document.createElement("article");
  card.className = "item-card";
  const main = document.createElement("div");
  main.className = "item-main";
  main.tabIndex = 0;
  main.setAttribute("role", "button");
  const title = document.createElement("h3");
  title.textContent = item.metadata?.subject || item.metadata?.action_id || item.name;
  const detail = document.createElement("p");
  detail.textContent = `${item.metadata?.type || item.folder.replaceAll("_", " ")} · ${formatDate(item.modified_at)}`;
  main.append(title, detail);
  const open = () => openDetail(item.folder, item.name);
  main.addEventListener("click", open);
  main.addEventListener("keydown", event => { if (event.key === "Enter" || event.key === " ") open(); });
  card.append(main);
  if (withActions) {
    const actions = document.createElement("div");
    actions.className = "item-actions";
    [["✓ Approve", "approve", "approve"], ["✗ Reject", "reject", "reject"]].forEach(([label, action, klass]) => {
      const button = document.createElement("button");
      button.className = `action-button ${klass}`;
      button.textContent = label;
      button.addEventListener("click", () => decide(item.name, action, button));
      actions.append(button);
    });
    card.append(actions);
  }
  return card;
}

function renderItems(container, items, withActions = false) {
  container.replaceChildren(...(items.length
    ? items.map(item => itemCard(item, withActions))
    : [emptyState("Nothing here", "This workflow queue is currently clear.")]));
}

function timelineEvent(entry) {
  const node = document.createElement("article");
  node.className = "timeline-event";
  const title = document.createElement("h3");
  title.textContent = String(entry.status || entry.event || "Activity").replaceAll("_", " ");
  const detail = document.createElement("p");
  detail.textContent = [entry.agent, entry.action_id, entry.source_file].filter(Boolean).join(" · ") || "Workflow event";
  const time = document.createElement("time");
  time.textContent = formatDate(entry.timestamp);
  node.append(title, detail, time);
  return node;
}

function renderTimeline(container, entries) {
  const sorted = [...entries].sort((a, b) => new Date(b.timestamp || 0) - new Date(a.timestamp || 0));
  container.replaceChildren(...(sorted.length ? sorted.map(timelineEvent) : [emptyState("No activity yet", "Agent events will appear here.")]));
}

async function refreshStats({ quiet = false } = {}) {
  try {
    const stats = await apiFetch("/stats");
    state.stats = stats;
    renderKpis(stats);
    renderTimeline($("#recent-timeline"), (stats.recent_activity || []).slice(0, 6));
    $("#last-updated").textContent = `Updated ${formatDate(stats.generated_at)}`;
  } catch (error) {
    if (!quiet) showAlert(`Could not refresh dashboard: ${error.message}`);
  }
}

async function loadDashboard() {
  await refreshStats();
  try {
    const pending = await apiFetch("/folder/pending_approval");
    renderItems($("#attention-list"), (pending.items || []).slice(0, 5), true);
  } catch (error) { showAlert(error.message); }
}

async function loadFolder(key) {
  const list = $("#folder-list");
  list.replaceChildren(emptyState("Loading…", "Reading the live workflow folder."));
  try {
    const payload = await apiFetch(`/folder/${encodeURIComponent(key)}`);
    $("#folder-title").textContent = viewLabels[key];
    $("#folder-count").textContent = `${payload.count} item${payload.count === 1 ? "" : "s"}`;
    renderItems(list, payload.items || [], key === "pending_approval");
  } catch (error) { list.replaceChildren(emptyState("Could not load items", error.message)); }
}

async function loadActivity() {
  try {
    const payload = await apiFetch("/logs");
    $("#activity-count").textContent = `${payload.count} event${payload.count === 1 ? "" : "s"}`;
    renderTimeline($("#activity-timeline"), payload.entries || []);
    if (payload.errors?.length) showAlert(`${payload.errors.length} log file(s) could not be read.`);
  } catch (error) { showAlert(error.message); }
}

async function switchView(view) {
  state.view = view;
  $("#view-title").textContent = viewLabels[view];
  $$(".nav-item").forEach(button => button.classList.toggle("active", button.dataset.view === view));
  $$(".view").forEach(section => section.classList.remove("active"));
  $(view === "dashboard" ? "#view-dashboard" : view === "activity" ? "#view-activity" : "#view-folder").classList.add("active");
  $("#sidebar").classList.remove("open");
  $("#menu-button").setAttribute("aria-expanded", "false");
  if (view === "dashboard") await loadDashboard();
  else if (view === "activity") await loadActivity();
  else await loadFolder(view);
}

async function openDetail(folder, name) {
  try {
    const payload = await apiFetch(`/file/${encodeURIComponent(folder)}/${encodeURIComponent(name)}`);
    $("#modal-folder").textContent = folder.replaceAll("_", " ");
    $("#modal-title").textContent = name;
    const metadata = $("#modal-metadata");
    metadata.replaceChildren(...Object.entries(payload.metadata || {}).map(([key, value]) => {
      const pair = document.createElement("div"); pair.className = "metadata-pair";
      const term = document.createElement("dt"); term.textContent = key;
      const description = document.createElement("dd"); description.textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2);
      pair.append(term, description); return pair;
    }));
    if (!metadata.children.length) metadata.append(emptyState("No frontmatter", "This file contains Markdown only."));
    $("#modal-body").textContent = payload.body || "(Empty body)";
    $("#detail-modal").classList.remove("hidden");
    document.body.style.overflow = "hidden";
    $("#modal-close").focus();
  } catch (error) { showAlert(`Could not open file: ${error.message}`); }
}

function closeDetail() {
  $("#detail-modal").classList.add("hidden");
  document.body.style.overflow = "";
}

async function decide(name, action, button) {
  if (state.busy || !window.confirm(`${action === "approve" ? "Approve" : "Reject"} ${name}?`)) return;
  state.busy = true;
  const buttons = $$(".action-button"); buttons.forEach(node => { node.disabled = true; });
  try {
    await apiFetch(`/${action}/${encodeURIComponent(name)}`, { method: "POST" });
    showAlert(`${name} was ${action}d.`, "success");
    await Promise.all([refreshStats(), state.view === "dashboard" ? loadDashboard() : loadFolder("pending_approval")]);
  } catch (error) { showAlert(`${action} failed: ${error.message}`); }
  finally { state.busy = false; buttons.forEach(node => { node.disabled = false; }); }
}

function bindEvents() {
  $$(".nav-item").forEach(button => button.addEventListener("click", () => switchView(button.dataset.view)));
  $$('[data-go]').forEach(button => button.addEventListener("click", () => switchView(button.dataset.go)));
  $("#refresh-button").addEventListener("click", () => switchView(state.view));
  $("#menu-button").addEventListener("click", event => {
    const open = $("#sidebar").classList.toggle("open");
    event.currentTarget.setAttribute("aria-expanded", String(open));
  });
  $("#modal-close").addEventListener("click", closeDetail);
  $("#detail-modal").addEventListener("click", event => { if (event.target === event.currentTarget) closeDetail(); });
  document.addEventListener("keydown", event => { if (event.key === "Escape") closeDetail(); });
}

document.addEventListener("DOMContentLoaded", async () => {
  bindEvents();
  await loadDashboard();
  window.setInterval(() => refreshStats({ quiet: true }), REFRESH_MS);
});
