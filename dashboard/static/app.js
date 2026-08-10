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

const state = {
  view: "dashboard",
  stats: null,
  busy: false,
  searchQuery: "",
  categoryFilter: "all",
  currentFolderItems: [],
  currentModalItem: null
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

// Toast Notification System
function showToast(message, kind = "info", duration = 4500) {
  const container = $("#toast-container");
  if (!container) return;
  const toast = document.createElement("div");
  toast.className = `toast ${kind}`;
  
  const icon = kind === "success" ? "✓" : kind === "error" ? "⚠" : "ℹ";
  toast.innerHTML = `<span style="font-weight:700;">${icon}</span><span>${escapeHtml(message)}</span>`;
  
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = "0";
    toast.style.transform = "translateY(10px)";
    toast.style.transition = "all 0.3s ease";
    setTimeout(() => toast.remove(), 300);
  }, duration);
}

function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

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
  try { payload = await response.json(); } catch { /* json parse error */ }
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status})`);
  return payload;
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

// Monogram avatar generator
function getAvatarText(sender) {
  if (!sender) return "EM";
  const clean = sender.replace(/<.*?>/g, "").replace(/[^a-zA-Z0-9\s]/g, "").trim();
  const words = clean.split(/\s+/).filter(Boolean);
  if (words.length >= 2) return (words[0][0] + words[1][0]).toUpperCase();
  if (words.length === 1 && words[0].length >= 2) return words[0].substring(0, 2).toUpperCase();
  return "EM";
}

function parseTextToHtml(text) {
  if (!text) return "<p>(No readable content)</p>";
  
  // Escape base HTML
  let html = escapeHtml(text);

  // Convert URLs to clickable links
  const urlRegex = /(https?:\/\/[^\s<]+)/g;
  html = html.replace(urlRegex, '<a href="$1" target="_blank" rel="noopener noreferrer">$1</a>');

  // Convert code blocks ```
  html = html.replace(/```([\s\S]*?)```/g, '<pre><code>$1</code></pre>');

  // Convert single line quotes >
  html = html.replace(/^&gt;\s?(.*)$/gm, '<blockquote>$1</blockquote>');

  // Convert paragraphs
  const paragraphs = html.split(/\n\s*\n/).map(p => p.trim()).filter(Boolean);
  return paragraphs.map(p => {
    if (p.startsWith('<pre>') || p.startsWith('<blockquote>')) return p;
    return `<p>${p.replaceAll('\n', '<br>')}</p>`;
  }).join('');
}

function renderMarkdown(text) {
  if (!text) return "<p>(No content)</p>";
  const lines = text.split("\n");
  const chunks = [];
  let paragraph = [];
  let tableRows = [];

  const inline = value => escapeHtml(value)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");

  const flushParagraph = () => {
    if (!paragraph.length) return;
    chunks.push(`<p>${inline(paragraph.join(" "))}</p>`);
    paragraph = [];
  };

  const flushTable = () => {
    if (tableRows.length < 2) return;
    const [header, , ...rows] = tableRows;
    const headerCells = header.split("|").slice(1, -1).map(cell => cell.trim());
    const body = rows.map(row => {
      const cells = row.split("|").slice(1, -1).map(cell => cell.trim());
      return `<tr>${cells.map(cell => `<td>${escapeHtml(cell.replace(/`/g, ""))}</td>`).join("")}</tr>`;
    }).join("");
    chunks.push(
      `<table class="plan-table"><thead><tr>${headerCells.map(cell => `<th>${escapeHtml(cell.replace(/`/g, ""))}</th>`).join("")}</tr></thead><tbody>${body}</tbody></table>`
    );
    tableRows = [];
  };

  for (const rawLine of lines) {
    const line = rawLine.trimEnd();
    if (line.startsWith("|")) {
      flushParagraph();
      tableRows.push(line);
      continue;
    }
    if (tableRows.length) flushTable();

    if (!line.trim()) {
      flushParagraph();
      continue;
    }
    if (line.startsWith("# ")) {
      flushParagraph();
      chunks.push(`<h1 class="md-h1">${escapeHtml(line.slice(2))}</h1>`);
      continue;
    }
    if (line.startsWith("## ")) {
      flushParagraph();
      chunks.push(`<h2 class="md-h2">${escapeHtml(line.slice(3))}</h2>`);
      continue;
    }
    if (line.startsWith("### ")) {
      flushParagraph();
      chunks.push(`<h3 class="md-h3">${escapeHtml(line.slice(4))}</h3>`);
      continue;
    }
    if (/^\d+\.\s/.test(line)) {
      flushParagraph();
      const number = line.match(/^(\d+)\./)?.[1] || "•";
      chunks.push(`<div class="md-step"><span>${number}</span><p>${inline(line.replace(/^\d+\.\s/, ""))}</p></div>`);
      continue;
    }
    if (line.startsWith("- ")) {
      flushParagraph();
      chunks.push(`<div class="md-bullet"><span>✓</span><p>${inline(line.slice(2))}</p></div>`);
      continue;
    }
    paragraph.push(line);
  }
  flushParagraph();
  flushTable();
  return chunks.join("") || "<p>(No readable content)</p>";
}

function renderDoneSummary(summary) {
  $("#done-headline").textContent = summary.headline || "Completed work is summarized here.";
  const cards = [
    ["Total complete", summary.total || 0, "◈"],
    ["Auto-handled", summary.by_resolution?.auto_handled || 0, "✦"],
    ["Approval-routed", summary.by_resolution?.pending_approval || 0, "◇"],
  ];
  $("#done-stats-grid").replaceChildren(...cards.map(([label, value, icon]) => {
    const card = document.createElement("article");
    card.className = "done-stat-card";
    card.innerHTML = `<span>${icon}</span><strong>${value}</strong><small>${escapeHtml(label)}</small>`;
    return card;
  }));
  const recent = (summary.recent || []).map(item => ({
    status: item.resolution || "completed",
    agent: item.autonomy_mode || "workflow",
    source_file: item.subject || "Completed item",
    timestamp: item.resolved_at,
  }));
  renderTimeline($("#done-recent-list"), recent.slice(0, 6));
}

function getItemType(item) {
  const meta = item.metadata || {};
  const name = item.name || "";
  if (meta.type === "email" || meta.from || name.startsWith("email_")) return "email";
  if (meta.type === "linkedin_post" || meta.post_body || name.includes("linkedin")) return "linkedin";
  if (meta.type === "plan" || name.startsWith("Plan_")) return "plan";
  return "manual";
}

function renderKpis(stats) {
  const counts = stats.counts || {};
  const cards = [
    ["Pending Approval", counts.pending_approval || 0, "Human decision required", "var(--purple)", "✓"],
    ["Auto-Handled Today", stats.digest_today || 0, "Summarized without approval", "var(--green)", "◆"],
    ["Needs Action", counts.needs_action || 0, "Unprocessed intake only", "var(--cyan)", "◇"],
    ["Done", counts.done || 0, "Successfully completed", "var(--green)", "●"],
    ["Failed", counts.failed || 0, "Requires investigation", "var(--red)", "!"],
    ["Action Plans", counts.plans || 0, "Generated execution plans", "var(--amber)", "≡"],
    ["Total Items", stats.total_items || 0, "Across all workflows", "#38bdf8", "∑"],
  ];
  
  const grid = $("#kpi-grid");
  grid.replaceChildren(...cards.map(([label, value, note, color, icon]) => {
    const card = document.createElement("article");
    card.className = "kpi-card";
    card.style.setProperty("--accent", color);
    card.innerHTML = `
      <div class="kpi-top">
        <span>${escapeHtml(label)}</span>
        <i class="kpi-icon">${icon}</i>
      </div>
      <div class="kpi-value">${value}</div>
      <div class="kpi-note">${escapeHtml(note)}</div>
    `;
    return card;
  }));

  $$('[data-count]').forEach(node => {
    node.textContent = counts[node.dataset.count] || 0;
  });
}

function itemCard(item, withActions = false) {
  const card = document.createElement("article");
  card.className = "item-card";
  const itemType = getItemType(item);

  const main = document.createElement("div");
  main.className = "item-main";
  main.tabIndex = 0;
  main.setAttribute("role", "button");

  const titleRow = document.createElement("div");
  titleRow.className = "item-title-row";

  const typeTag = document.createElement("span");
  typeTag.className = `item-type-tag ${itemType}`;
  typeTag.textContent = itemType.toUpperCase();

  const title = document.createElement("h3");
  title.textContent = item.metadata?.subject || item.metadata?.summary || item.metadata?.action_id || item.name;

  titleRow.append(typeTag, title);

  const detail = document.createElement("p");
  const senderText = item.metadata?.from ? `From: ${item.metadata.from} · ` : "";
  detail.textContent = `${senderText}${formatDate(item.modified_at)}`;

  main.append(titleRow, detail);

  const open = () => openDetail(item.folder, item.name);
  main.addEventListener("click", open);
  main.addEventListener("keydown", event => { if (event.key === "Enter" || event.key === " ") open(); });

  card.append(main);

  if (withActions) {
    const actions = document.createElement("div");
    actions.className = "item-actions";
    
    const approveBtn = document.createElement("button");
    approveBtn.className = "action-button approve";
    approveBtn.textContent = "✓ Approve";
    approveBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      decide(item.name, "approve");
    });

    const rejectBtn = document.createElement("button");
    rejectBtn.className = "action-button reject";
    rejectBtn.textContent = "✗ Reject";
    rejectBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      decide(item.name, "reject");
    });

    actions.append(approveBtn, rejectBtn);
    card.append(actions);
  }
  return card;
}

function filterItems(items) {
  let filtered = [...items];

  // Category filter
  if (state.categoryFilter !== "all") {
    filtered = filtered.filter(item => getItemType(item) === state.categoryFilter);
  }

  // Search filter
  if (state.searchQuery.trim()) {
    const q = state.searchQuery.toLowerCase();
    filtered = filtered.filter(item => {
      const name = (item.name || "").toLowerCase();
      const subject = (item.metadata?.subject || "").toLowerCase();
      const from = (item.metadata?.from || "").toLowerCase();
      const summary = (item.metadata?.summary || "").toLowerCase();
      const actionId = (item.metadata?.action_id || "").toLowerCase();
      const preview = (item.body_preview || "").toLowerCase();
      return name.includes(q) || subject.includes(q) || from.includes(q) || summary.includes(q) || actionId.includes(q) || preview.includes(q);
    });
  }

  return filtered;
}

function renderItems(container, items, withActions = false) {
  const filtered = filterItems(items);
  container.replaceChildren(...(filtered.length
    ? filtered.map(item => itemCard(item, withActions))
    : [emptyState("No matching items", "No workflow items match your search or filter criteria.")]));
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

function digestEvent(entry) {
  const node = document.createElement("article");
  node.className = "timeline-event digest-event";

  const title = document.createElement("h3");
  title.textContent = entry.subject || entry.summary || entry.action_id || "Auto-handled item";

  const detail = document.createElement("p");
  const parts = [
    entry.from,
    entry.summary,
    entry.autonomy_mode?.replaceAll("_", " "),
  ].filter(Boolean);
  detail.textContent = parts.join(" · ");

  const time = document.createElement("time");
  time.textContent = formatDate(entry.timestamp);

  node.append(title, detail, time);
  return node;
}

function renderDigest(container, entries, totalCount, batchSummary) {
  const count = totalCount ?? entries.length;
  if (!entries.length) {
    container.replaceChildren(
      emptyState(
        "Nothing auto-handled yet",
        "Routine logs, notifications, and low-impact mail are summarized here without interrupting you."
      )
    );
    return;
  }
  const sorted = [...entries].sort(
    (a, b) => new Date(b.timestamp || 0) - new Date(a.timestamp || 0)
  );
  const header = document.createElement("p");
  header.className = "digest-batch-header";
  header.textContent = batchSummary || `Handled ${count} item${count === 1 ? "" : "s"} automatically today`;
  container.replaceChildren(
    header,
    ...sorted.slice(0, 12).map(digestEvent)
  );
}

async function refreshStats({ quiet = false } = {}) {
  const refreshBtn = $("#refresh-button");
  if (refreshBtn) refreshBtn.classList.add("spinning");
  try {
    const stats = await apiFetch("/stats");
    state.stats = stats;
    renderKpis(stats);
    renderTimeline($("#recent-timeline"), (stats.recent_activity || []).slice(0, 6));
    $("#last-updated").textContent = `Updated ${formatDate(stats.generated_at)}`;
  } catch (error) {
    if (!quiet) showToast(`Could not refresh dashboard: ${error.message}`, "error");
  } finally {
    if (refreshBtn) refreshBtn.classList.remove("spinning");
  }
}

async function loadDashboard() {
  await refreshStats();
  try {
    const [pending, digest, doneSummary] = await Promise.all([
      apiFetch("/folder/pending_approval"),
      apiFetch("/digest"),
      apiFetch("/done-summary"),
    ]);
    renderItems($("#attention-list"), (pending.items || []).slice(0, 6), true);
    renderDigest($("#digest-list"), digest.entries || [], digest.count, digest.batch_summary);
    renderDoneSummary(doneSummary);
  } catch (error) { showToast(error.message, "error"); }
}

async function loadFolder(key) {
  const list = $("#folder-list");
  list.replaceChildren(emptyState("Loading…", "Reading workflow queue..."));
  try {
    const payload = await apiFetch(`/folder/${encodeURIComponent(key)}`);
    state.currentFolderItems = payload.items || [];
    $("#folder-title").textContent = viewLabels[key] || key;
    $("#folder-count").textContent = `${payload.count} item${payload.count === 1 ? "" : "s"}`;
    renderItems(list, state.currentFolderItems, key === "pending_approval");
  } catch (error) {
    list.replaceChildren(emptyState("Could not load items", error.message));
  }
}

async function loadActivity() {
  try {
    const payload = await apiFetch("/logs");
    $("#activity-count").textContent = `${payload.count} event${payload.count === 1 ? "" : "s"}`;
    renderTimeline($("#activity-timeline"), payload.entries || []);
    if (payload.errors?.length) showToast(`${payload.errors.length} log file(s) could not be read.`, "error");
  } catch (error) { showToast(error.message, "error"); }
}

async function switchView(view) {
  state.view = view;
  $("#view-title").textContent = viewLabels[view] || view;
  $$(".nav-item").forEach(button => button.classList.toggle("active", button.dataset.view === view));
  $$(".view").forEach(section => section.classList.remove("active"));
  $(view === "dashboard" ? "#view-dashboard" : view === "activity" ? "#view-activity" : "#view-folder").classList.add("active");
  $("#sidebar").classList.remove("open");
  $("#menu-button").setAttribute("aria-expanded", "false");

  if (view === "dashboard") await loadDashboard();
  else if (view === "activity") await loadActivity();
  else await loadFolder(view);
}

// Modal Tabs Switcher
function switchModalTab(tabName) {
  $$(".tab-btn").forEach(btn => btn.classList.toggle("active", btn.dataset.tab === tabName));
  $$(".tab-pane").forEach(pane => pane.classList.toggle("hidden", pane.id !== `pane-${tabName}`));
}

async function openDetail(folder, name) {
  try {
    const payload = await apiFetch(`/file/${encodeURIComponent(folder)}/${encodeURIComponent(name)}`);
    state.currentModalItem = { folder, name, ...payload };
    const meta = payload.metadata || {};
    const itemType = getItemType(state.currentModalItem);

    // Set modal headers & badges
    $("#modal-subtitle").textContent = `File: ${name} · Folder: ${folder}`;
    $("#modal-type-badge").textContent = itemType.toUpperCase();
    $("#modal-status-badge").textContent = (meta.status || folder).toUpperCase();
    $("#modal-title").textContent = meta.subject || meta.summary || meta.action_id || name;

    // Check if item has draft reply / response
    const hasDraft = Boolean(meta.draft_body || meta.post_body || meta.proposed_action);
    const draftTabBtn = $("#tab-btn-draft");
    if (draftTabBtn) {
      draftTabBtn.classList.toggle("hidden", !hasDraft);
    }

    // Populate Draft Pane if available
    if (hasDraft) {
      $("#draft-target").textContent = meta.draft_recipient ? `Recipient: ${meta.draft_recipient}` : `Action ID: ${meta.action_id || name}`;
      $("#draft-subject-val").textContent = meta.draft_subject || meta.subject || "Re: Response Draft";
      $("#draft-text-val").textContent = meta.draft_body || meta.post_body || JSON.stringify(meta.proposed_action || {}, null, 2);
    }

    // Populate Email Reader View vs Standard Reader
    const emailViewer = $("#email-viewer");
    const standardReader = $("#standard-reader");

    if (itemType === "email" || meta.from) {
      emailViewer.classList.remove("hidden");
      standardReader.classList.add("hidden");

      $("#email-avatar").textContent = getAvatarText(meta.from);
      $("#email-from").textContent = meta.from || "(Unknown sender)";
      
      const toRow = $("#email-to-row");
      if (meta.to) {
        toRow.classList.remove("hidden");
        $("#email-to").textContent = meta.to;
      } else {
        toRow.classList.add("hidden");
      }

      $("#email-date").textContent = formatDate(meta.received_at || meta.date);
      $("#email-subject").textContent = meta.subject || "(No subject)";
      $("#email-body-parsed").innerHTML = parseTextToHtml(payload.body);
    } else {
      emailViewer.classList.add("hidden");
      standardReader.classList.remove("hidden");
      $("#standard-body-parsed").innerHTML = parseTextToHtml(payload.body);
    }

    const planTab = $("#tab-btn-plan");
    const isPlan = folder === "plans" || /^#\s+Action Plan/m.test(payload.body || "");
    planTab?.classList.toggle("hidden", !isPlan);
    if (isPlan) $("#plan-body-parsed").innerHTML = renderMarkdown(payload.body);

    // Populate Metadata Grid
    const metadataGrid = $("#modal-metadata");
    metadataGrid.replaceChildren(...Object.entries(meta).map(([key, value]) => {
      const pair = document.createElement("div");
      pair.className = "metadata-pair";
      const term = document.createElement("dt");
      term.textContent = key;
      const desc = document.createElement("dd");
      desc.textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2);
      pair.append(term, desc);
      return pair;
    }));
    if (!metadataGrid.children.length) {
      metadataGrid.append(emptyState("No Frontmatter", "This file contains plain Markdown body only."));
    }

    // Configure Modal Action Buttons
    const isPending = folder === "pending_approval";
    const approveBtn = $("#modal-btn-approve");
    const rejectBtn = $("#modal-btn-reject");

    if (approveBtn && rejectBtn) {
      approveBtn.classList.toggle("hidden", !isPending);
      rejectBtn.classList.toggle("hidden", !isPending);
    }

    switchModalTab("reader");
    $("#detail-modal").classList.remove("hidden");
    document.body.style.overflow = "hidden";
  } catch (error) {
    showToast(`Could not open file: ${error.message}`, "error");
  }
}

function closeDetail() {
  $("#detail-modal").classList.add("hidden");
  document.body.style.overflow = "";
  state.currentModalItem = null;
}

async function decide(name, action) {
  if (state.busy) return;
  if (!window.confirm(`Are you sure you want to ${action === "approve" ? "APPROVE" : "REJECT"} "${name}"?`)) return;

  state.busy = true;
  try {
    await apiFetch(`/${action}/${encodeURIComponent(name)}`, { method: "POST" });
    showToast(`Action "${name}" was successfully ${action}d!`, "success");
    closeDetail();
    await Promise.all([
      refreshStats(),
      state.view === "dashboard" ? loadDashboard() : loadFolder(state.view)
    ]);
  } catch (error) {
    showToast(`${action} failed: ${error.message}`, "error");
  } finally {
    state.busy = false;
  }
}

// Profile Badge Overlay Functions
function toggleProfileCard() {
  const popover = $("#profile-popover");
  if (!popover) return;
  const isHidden = popover.classList.contains("hidden");
  popover.classList.toggle("hidden", !isHidden);
  $("#profile-trigger")?.setAttribute("aria-expanded", String(isHidden));
}

function closeProfileCard() {
  const popover = $("#profile-popover");
  if (!popover) return;
  popover.classList.add("hidden");
  $("#profile-trigger")?.setAttribute("aria-expanded", "false");
}

function bindEvents() {
  // Navigation
  $$(".nav-item").forEach(button => button.addEventListener("click", () => switchView(button.dataset.view)));
  $$('[data-go]').forEach(button => button.addEventListener("click", () => switchView(button.dataset.go)));
  
  // Refresh Button
  $("#refresh-button")?.addEventListener("click", () => switchView(state.view));
  
  // Mobile Sidebar Menu
  $("#menu-button")?.addEventListener("click", event => {
    const open = $("#sidebar").classList.toggle("open");
    event.currentTarget.setAttribute("aria-expanded", String(open));
  });

  // Top Right Profile Card Toggle
  $("#profile-trigger")?.addEventListener("click", (e) => {
    e.stopPropagation();
    toggleProfileCard();
  });
  $("#profile-close")?.addEventListener("click", (e) => {
    e.stopPropagation();
    closeProfileCard();
  });
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".profile-trigger-wrapper")) {
      closeProfileCard();
    }
  });

  // Search Input Handler
  const searchInput = $("#search-input");
  const searchClear = $("#search-clear");
  if (searchInput) {
    searchInput.addEventListener("input", (e) => {
      state.searchQuery = e.target.value;
      if (searchClear) searchClear.classList.toggle("hidden", !state.searchQuery);
      if (state.view === "dashboard") {
        const attentionList = $("#attention-list");
        if (state.stats) loadDashboard();
      } else {
        renderItems($("#folder-list"), state.currentFolderItems, state.view === "pending_approval");
      }
    });
  }
  if (searchClear) {
    searchClear.addEventListener("click", () => {
      searchInput.value = "";
      state.searchQuery = "";
      searchClear.classList.add("hidden");
      if (state.view === "dashboard") loadDashboard();
      else renderItems($("#folder-list"), state.currentFolderItems, state.view === "pending_approval");
    });
  }

  // Category Filter Pills
  $$(".filter-pill").forEach(pill => {
    pill.addEventListener("click", () => {
      $$(".filter-pill").forEach(p => p.classList.remove("active"));
      pill.classList.add("active");
      state.categoryFilter = pill.dataset.filter;
      renderItems($("#folder-list"), state.currentFolderItems, state.view === "pending_approval");
    });
  });

  // Modal Tabs
  $$(".tab-btn").forEach(btn => {
    btn.addEventListener("click", () => switchModalTab(btn.dataset.tab));
  });

  // Modal Action Buttons
  $("#modal-close")?.addEventListener("click", closeDetail);
  $("#modal-btn-close")?.addEventListener("click", closeDetail);
  
  $("#modal-btn-approve")?.addEventListener("click", () => {
    if (state.currentModalItem) decide(state.currentModalItem.name, "approve");
  });
  $("#modal-btn-reject")?.addEventListener("click", () => {
    if (state.currentModalItem) decide(state.currentModalItem.name, "reject");
  });

  $("#modal-btn-copy")?.addEventListener("click", () => {
    if (state.currentModalItem?.body) {
      navigator.clipboard.writeText(state.currentModalItem.body);
      showToast("Item body copied to clipboard!", "success");
    }
  });

  $("#detail-modal")?.addEventListener("click", event => {
    if (event.target === event.currentTarget) closeDetail();
  });

  document.addEventListener("keydown", event => {
    if (event.key === "Escape") {
      closeDetail();
      closeProfileCard();
    }
  });
}

document.addEventListener("DOMContentLoaded", async () => {
  bindEvents();
  await loadDashboard();
  window.setInterval(async () => {
    if (state.view === "dashboard") await loadDashboard();
    else await refreshStats({ quiet: true });
  }, REFRESH_MS);
});
