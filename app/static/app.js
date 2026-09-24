const state = {
  data: null,
  selectedCandidateId: null,
  compareAnchorId: null,
  editingCandidateId: null,
  hoverCandidateId: null,
  externalMapsEnabled: false,
};

// Sensitive filters and selections live only in this tab's memory.
let sessionUi = {};
function loadPersistedUi() { return { ...sessionUi }; }
function savePersistedUi() {
  sessionUi = {
    search: $("#candidateSearch")?.value || "",
    status: $("#statusFilter")?.value || "all",
    area: $("#areaFilter")?.value || "all",
    sort: $("#candidateSort")?.value || "default",
    selected: state.selectedCandidateId,
    compareAnchor: state.compareAnchorId,
  };
}

const leafletMapState = {
  map: null,
  markerLayer: null,
  markers: new Map(),
  lastVisibleSignature: "",
  selectedMarkerId: null,
};

const defaultCategoryLabels = {};

const defaultRiskLabels = {};

// Labels and areas come from the profile (state.data); these getters keep call sites simple.
const categoryLabels = new Proxy({}, {
  get: (_, key) => state.data?.category_labels?.[key] ?? defaultCategoryLabels[key],
});
const riskLabels = new Proxy({}, {
  get: (_, key) => state.data?.flag_labels?.[key] ?? defaultRiskLabels[key],
});
const areaLabels = new Proxy({}, {
  get: (_, key) => (key === "other" ? "Other" : (state.data?.areas || []).find((a) => a.id === key)?.label),
});
function areaOrder() {
  return [...(state.data?.areas || []).map((a) => a.id), "other"];
}

const statusLabels = {
  baseline: "Baseline",
  active: "Active",
  watchlist: "Watchlist",
  tour: "Tour",
  negotiate: "Negotiate",
  rejected: "Rejected",
  archived: "Archived",
};

const rowRiskBadges = [
  { test: (text) => /construction|pile driving|demolition/.test(text), label: "CONSTR", tone: "reject" },
  { test: (text) => /flood|ground[- ]level|below[- ]grade/.test(text), label: "FLOOD", tone: "reject" },
  { test: (text) => /studio_unit|studio unit/.test(text), label: "STUDIO", tone: "reject" },
  { test: (text) => /traffic|loud|exhaust|loading/.test(text), label: "NOISE", tone: "warn" },
];

function $(selector) {
  return document.querySelector(selector);
}

function safeUrl(value) {
  try {
    const url = new URL(String(value || ""), window.location.origin);
    return ["http:", "https:"].includes(url.protocol) ? url.href : "#";
  } catch (error) {
    return "#";
  }
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatMoney(value) {
  if (value === null || value === undefined || value === "") return "-";
  const number = Number(value);
  if (Number.isNaN(number)) return "-";
  return number.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  });
}

function formatMoneyPending(value) {
  return value === null || value === undefined || value === "" ? "Pending" : formatMoney(value);
}

function numericValue(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function formatMoneyDelta(value, baselineValue) {
  const number = numericValue(value);
  const baseline = numericValue(baselineValue);
  if (number === null || baseline === null) return "Pending";
  const delta = number - baseline;
  if (delta === 0) return "Same as baseline";
  const sign = delta > 0 ? "+" : "-";
  return `${sign}${formatMoney(Math.abs(delta))} vs baseline`;
}

function formatNumber(value) {
  const number = numericValue(value);
  return number === null ? "-" : number.toLocaleString("en-US");
}

function formatMinutes(value) {
  const number = numericValue(value);
  return number === null ? "Unknown" : `${number.toLocaleString("en-US")} min`;
}

function formatRange(min, max, formatter = formatNumber) {
  const low = numericValue(min);
  const high = numericValue(max);
  if (low === null && high === null) return "-";
  if (high === null || low === high) return formatter(low);
  if (low === null) return formatter(high);
  return `${formatter(low)}-${formatter(high)}`;
}

function formatSqftRange(min, max) {
  const range = formatRange(min, max, formatNumber);
  return range === "-" ? range : `${range} sq ft`;
}

function formatMoneyRange(min, max) {
  return formatRange(min, max, formatMoney);
}

function normalizeAvailability(value) {
  // Availability is a listing fact, never a projection of the preferred move window.
  return String(value ?? "").trim() || "Unknown";
}

function formatFeeValue(value) {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "number") return formatMoney(value);
  return escapeHtml(value);
}

function titleCase(value) {
  return String(value || "")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function candidateArea(candidate) {
  const haystack = [candidate.name, candidate.address, candidate.neighborhood].join(" ").toLowerCase();
  const neighborhood = String(candidate.neighborhood || "").toLowerCase();
  const areas = state.data?.areas || [];
  const byNeighborhood = areas.find((a) => (a.match || []).some((m) => neighborhood.includes(String(m).toLowerCase())));
  if (byNeighborhood) return byNeighborhood.id;
  const byText = areas.find((a) => (a.match || []).some((m) => haystack.includes(String(m).toLowerCase())));
  return byText ? byText.id : "other";
}

function scoreValue(candidate) {
  return Number(candidate.score_summary?.weighted_score || 0);
}

function rentValue(candidate) {
  const rent = numericValue(candidate.all_in_monthly_cost) ?? numericValue(candidate.base_rent);
  return rent ?? Number.POSITIVE_INFINITY;
}

function candidateRankSort(left, right) {
  const defaultLeft = state.data.candidates.findIndex((candidate) => candidate.id === left.id);
  const defaultRight = state.data.candidates.findIndex((candidate) => candidate.id === right.id);
  return defaultLeft - defaultRight;
}

function sortCandidates(candidates) {
  const sortMode = $("#candidateSort")?.value || "default";
  const nextCandidates = [...candidates];
  if (sortMode === "area") {
    return nextCandidates.sort((left, right) => (
      areaOrder().indexOf(candidateArea(left)) - areaOrder().indexOf(candidateArea(right))
      || candidateRankSort(left, right)
    ));
  }
  if (sortMode === "score") {
    return nextCandidates.sort((left, right) => scoreValue(right) - scoreValue(left) || candidateRankSort(left, right));
  }
  if (sortMode === "rent") {
    return nextCandidates.sort((left, right) => rentValue(left) - rentValue(right) || candidateRankSort(left, right));
  }
  if (sortMode === "name") {
    return nextCandidates.sort((left, right) => left.name.localeCompare(right.name) || candidateRankSort(left, right));
  }
  return nextCandidates;
}

async function api(path, options = {}) {
  const token = document.querySelector('meta[name="housing-token"]')?.content || "";
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", "X-Housing-Token": token, ...(options.headers || {}) },
  });
  if (!response.ok) {
    const body = await response.text();
    let message = body;
    try { message = JSON.parse(body).error || body; } catch (error) { /* keep raw text */ }
    const error = new Error(message || `Request failed: ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return response.json();
}

async function loadState() {
  state.data = await api("/api/state");
  const selectedStillExists = state.data.candidates.some((candidate) => candidate.id === state.selectedCandidateId);
  if (!state.selectedCandidateId || !selectedStillExists) {
    state.selectedCandidateId = defaultSelectedCandidateId();
  }
  render();
}

function candidateRejected(candidate) {
  return candidate.evaluation?.rejected === true || candidate.status === "rejected"
    || candidate.score_summary?.recommendation_flag === "reject_unless_exceptional";
}

function eligibleCandidate(candidate) {
  return !candidate.is_baseline && !["baseline", "archived", "rejected"].includes(candidate.status)
    && !candidateRejected(candidate);
}

function defaultSelectedCandidateId() {
  const liveCandidates = (state.data?.candidates || [])
    .filter(eligibleCandidate);
  const best = liveCandidates.reduce((currentBest, candidate) => {
    const score = Number(candidate.score_summary?.weighted_score || 0);
    const bestScore = Number(currentBest?.score_summary?.weighted_score || 0);
    return score > bestScore ? candidate : currentBest;
  }, liveCandidates[0]);
  return best?.id || state.data?.candidates?.[0]?.id || null;
}

function render() {
  renderMetrics();
  renderAreaFilter();
  renderActiveFilters();
  renderScoreInputs();
  renderCustomNoteInputs();
  renderRiskFlags();
  renderProfile();
  renderCandidates();
  renderMap();
  renderLedger();
  renderSourceRegistry();
  renderChart();
  renderCandidateDetail();
  renderPreferences();
  syncDetailToolButtons();
  savePersistedUi();
}

function syncDetailToolButtons() {
  const anchorBtn = $("#setAnchorBtn");
  if (anchorBtn) {
    const isPinned = state.compareAnchorId && state.compareAnchorId === state.selectedCandidateId;
    anchorBtn.textContent = isPinned ? "Unpin compare anchor" : "Pin as compare anchor";
    anchorBtn.classList.toggle("active", Boolean(state.compareAnchorId));
  }
}

function renderSelectionViews({ scrollList = false } = {}) {
  renderCandidates();
  if (scrollList && state.selectedCandidateId) {
    const selectedRow = [...document.querySelectorAll("[data-candidate-row]")]
      .find((row) => row.dataset.candidateRow === state.selectedCandidateId);
    selectedRow?.scrollIntoView({ block: "nearest" });
  }
  renderMap();
  renderChart();
  renderCandidateDetail();
}

function renderMetrics() {
  const metrics = state.data.metrics;
  $("#metricTotal").textContent = metrics.total_candidates;
  $("#metricLive").textContent = metrics.live_candidates;
  $("#metricBestScore").textContent = metrics.best_score === null ? "-" : metrics.best_score.toFixed(2);
  $("#metricBudget").textContent = state.data.profile.soft_ceiling;
  const staleEl = $("#metricStale");
  if (staleEl) staleEl.textContent = metrics.stale_candidates ?? 0;
}

function renderProfile() {
  const profile = state.data.profile;
  const rows = [
    ["Current home", profile.current_home],
    ["Current rent", profile.current_rent ? `${formatMoney(profile.current_rent)} base` : "-"],
    ["Lease end", profile.lease_end || "-"],
    ["Ideal band", profile.ideal_band],
    ["Hard review", profile.hard_review_threshold],
    ...(profile.facts || []).map((fact) => [fact.label, fact.value]),
  ];
  $("#profileFacts").innerHTML = rows
    .map(([label, value]) => `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`)
    .join("");
  $("#baselineTitle").textContent = profile.current_home_name;
  document.title = `${profile.name} — Housing Decision Dashboard`;
  $("#readOnlyBanner").hidden = !state.data.read_only;
  $("#editSelectedBtn").disabled = Boolean(state.data.read_only);
  document.querySelectorAll("#candidateForm input, #candidateForm select, #candidateForm textarea, #candidateForm button")
    .forEach((el) => { el.disabled = Boolean(state.data.read_only); });
  document.querySelectorAll(".anchor-label").forEach((el) => { el.textContent = profile.anchor_label; });
}

function renderScoreInputs() {
  const container = $("#scoreInputs");
  if (container.dataset.ready === "true") return;
  container.innerHTML = state.data.score_categories
    .map((category) => `
      <div class="score-control">
        <div class="score-label">
          <label for="score-${escapeHtml(category)}">${escapeHtml(categoryLabels[category] || titleCase(category))}</label>
          <strong id="score-value-${escapeHtml(category)}">5</strong>
        </div>
        <input id="score-${escapeHtml(category)}" name="${escapeHtml(category)}" type="range" min="0" max="10" value="5" step="0.5">
      </div>
    `)
    .join("");
  state.data.score_categories.forEach((category) => {
    const input = document.getElementById(`score-${category}`);
    const label = document.getElementById(`score-value-${category}`);
    input.addEventListener("input", () => {
      label.textContent = Number(input.value).toFixed(1);
    });
  });
  container.dataset.ready = "true";
}

function renderRiskFlags() {
  const container = $("#riskFlags");
  if (container.dataset.ready === "true") return;
  container.innerHTML = state.data.risk_flags
    .map((flag) => `
      <label class="risk-option">
        <input type="checkbox" name="risk_flags" value="${escapeHtml(flag)}">
        <span>${escapeHtml(riskLabels[flag] || titleCase(flag))}</span>
      </label>
    `)
    .join("");
  container.dataset.ready = "true";
}

function renderAreaFilter() {
  const select = $("#areaFilter");
  const selected = select.value || "all";
  const counts = new Map();
  (state.data.candidates || []).forEach((candidate) => {
    const area = candidateArea(candidate);
    counts.set(area, (counts.get(area) || 0) + 1);
  });
  const totalCount = state.data.candidates?.length || 0;
  const options = areaOrder()
    .filter((area) => counts.has(area))
    .map((area) => {
      const label = `${areaLabels[area] || titleCase(area)} (${counts.get(area)})`;
      return `<option value="${escapeHtml(area)}">${escapeHtml(label)}</option>`;
    })
    .join("");
  select.innerHTML = `<option value="all">All areas (${totalCount})</option>${options}`;
  select.value = counts.has(selected) ? selected : "all";
}

function renderActiveFilters() {
  const container = $("#activeFilters");
  if (!container) return;
  const status = $("#statusFilter").value;
  const area = $("#areaFilter").value;
  const sort = $("#candidateSort").value;
  const search = $("#candidateSearch").value.trim();
  const chips = [];
  if (search) chips.push({ key: "search", label: `Search: "${search}"` });
  if (status !== "all") chips.push({ key: "status", label: `Status: ${statusLabels[status] || status}` });
  if (area !== "all") chips.push({ key: "area", label: `Area: ${areaLabels[area] || area}` });
  if (sort !== "default") chips.push({ key: "sort", label: `Sort: ${sort}` });
  if (state.compareAnchorId) {
    const anchor = state.data.candidates.find((c) => c.id === state.compareAnchorId);
    if (anchor) chips.push({ key: "anchor", label: `Compare anchor: ${anchor.name}` });
  }
  if (!chips.length) {
    container.hidden = true;
    container.innerHTML = "";
    return;
  }
  container.hidden = false;
  container.innerHTML = chips
    .map((chip) => `<button type="button" class="filter-chip" data-clear-filter="${chip.key}">${escapeHtml(chip.label)} <span class="chip-x">x</span></button>`)
    .join("") + ' <button type="button" class="filter-chip-clear" data-clear-filter="all">Clear all</button>';
  container.querySelectorAll("[data-clear-filter]").forEach((button) => {
    button.addEventListener("click", () => clearFilter(button.dataset.clearFilter));
  });
}

function clearFilter(key) {
  if (key === "search" || key === "all") $("#candidateSearch").value = "";
  if (key === "status" || key === "all") $("#statusFilter").value = "all";
  if (key === "area" || key === "all") $("#areaFilter").value = "all";
  if (key === "sort" || key === "all") $("#candidateSort").value = "default";
  if (key === "anchor" || key === "all") state.compareAnchorId = null;
  savePersistedUi();
  render();
}

function visibleCandidates() {
  const status = $("#statusFilter").value;
  const area = $("#areaFilter").value;
  const query = $("#candidateSearch").value.trim().toLowerCase();
  const filtered = state.data.candidates.filter((candidate) => {
    const matchesStatus = status === "all" || candidate.status === status;
    const matchesArea = area === "all" || candidateArea(candidate) === area;
    const haystack = [
      candidate.name,
      candidate.address,
      candidate.neighborhood,
      candidate.kind,
      candidate.status,
      candidate.verdict,
    ].join(" ").toLowerCase();
    return matchesStatus && matchesArea && (!query || haystack.includes(query));
  });
  return sortCandidates(filtered);
}

function candidateLocation(candidate) {
  const location = candidate.map_location || {};
  const lat = numericValue(location.lat);
  const lng = numericValue(location.lng);
  if (lat === null || lng === null) return null;
  return { lat, lng };
}

function syncSelectedCandidate(candidates) {
  if (!candidates.length) return;
  const selectedIsVisible = candidates.some((candidate) => candidate.id === state.selectedCandidateId);
  if (!selectedIsVisible) {
    state.selectedCandidateId = candidates[0].id;
  }
}

function selectCandidate(candidateId, options = {}) {
  state.selectedCandidateId = candidateId;
  renderSelectionViews(options);
}

function renderCandidates() {
  const candidates = visibleCandidates();
  const container = $("#candidateList");
  if (!candidates.length) {
    state.selectedCandidateId = null;
    container.innerHTML = '<div class="empty-state">No matching candidates. <button type="button" class="clear-filters-link" data-clear-filter="all">Clear all filters</button></div>';
    container.querySelector("[data-clear-filter]")?.addEventListener("click", () => clearFilter("all"));
    return;
  }
  syncSelectedCandidate(candidates);
  const sortMode = $("#candidateSort")?.value || "default";
  if (sortMode === "area") {
    const groups = new Map();
    candidates.forEach((candidate) => {
      const area = candidateArea(candidate);
      if (!groups.has(area)) groups.set(area, []);
      groups.get(area).push(candidate);
    });
    container.innerHTML = areaOrder()
      .filter((area) => groups.has(area))
      .map((area) => {
        const list = groups.get(area);
        const header = `<h4 class="area-group-header">${escapeHtml(areaLabels[area] || area)} <span class="count">${list.length}</span></h4>`;
        return header + list.map(candidateRow).join("");
      })
      .join("");
  } else {
    container.innerHTML = candidates.map(candidateRow).join("");
  }
  attachCandidateRowHandlers(container);
}

function attachCandidateRowHandlers(container) {
  container.querySelectorAll("[data-select-candidate]").forEach((button) => {
    button.addEventListener("click", () => selectCandidate(button.dataset.selectCandidate));
  });
  container.querySelectorAll("[data-delete-candidate]").forEach((button) => {
    button.addEventListener("click", async (event) => {
      event.stopPropagation();
      const candidateId = button.dataset.deleteCandidate;
      if (candidateId === state.data.profile.baseline_id) return;
      const candidate = state.data.candidates.find((c) => c.id === candidateId);
      const name = candidate?.name || "this candidate";
      if (!window.confirm(`Archive ${name}? It stays in your profile with status "archived".`)) return;
      await api(`/api/candidates?id=${encodeURIComponent(candidateId)}`, { method: "DELETE" });
      await loadState();
    });
  });
  container.querySelectorAll("[data-row-status]").forEach((select) => {
    select.addEventListener("click", (event) => event.stopPropagation());
    select.addEventListener("change", async (event) => {
      event.stopPropagation();
      const candidateId = select.dataset.rowStatus;
      const candidate = state.data.candidates.find((c) => c.id === candidateId);
      if (!candidate) return;
      const updated = { id: candidate.id, version: candidate.version, status: select.value };
      try {
        const result = await api("/api/candidates", { method: "POST", body: JSON.stringify(updated) });
        state.data = result.state;
        render();
      } catch (error) {
        window.alert(`Status update failed: ${error.message}`);
      }
    });
  });
  container.querySelectorAll("[data-candidate-row]").forEach((row) => {
    const id = row.dataset.candidateRow;
    row.addEventListener("mouseenter", () => setHoveredCandidate(id));
    row.addEventListener("mouseleave", () => setHoveredCandidate(null));
  });
}

function setHoveredCandidate(id) {
  state.hoverCandidateId = id;
  document.querySelectorAll("[data-candidate-row]").forEach((row) => {
    row.classList.toggle("row-hover", row.dataset.candidateRow === id);
  });
  if (leafletMapState.markers) {
    leafletMapState.markers.forEach((marker, key) => {
      const el = marker.getElement?.()?.querySelector(".leaflet-map-marker");
      if (el) el.classList.toggle("hovered", key === id);
    });
  }
}

function candidateRow(candidate) {
  const summary = candidate.score_summary || {};
  const score = numericValue(summary.weighted_score);
  const isBaseline = candidate.is_baseline || candidate.status === "baseline";
  const isRejected = candidateRejected(candidate);
  const statusClass = isRejected ? "reject" : "";
  const bandClass = isBaseline ? "band-baseline" : isRejected ? "band-low" : "band-neutral";
  const selectedClass = candidate.id === state.selectedCandidateId ? "selected" : "";
  const rentSignal = candidate.all_in_monthly_cost || candidate.base_rent;
  const baselineRent = numericValue(baselineCandidate()?.base_rent) ?? numericValue(state.data?.profile?.current_rent);
  let deltaHtml = "";
  const rentNum = numericValue(rentSignal);
  if (!isBaseline && rentNum !== null && baselineRent) {
    const delta = rentNum - baselineRent;
    const cls = delta > 0 ? "up" : delta < 0 ? "down" : "flat";
    const sign = delta > 0 ? "+" : delta < 0 ? "-" : "";
    deltaHtml = `<span class="delta ${cls}">${sign}${formatMoney(Math.abs(delta))}</span>`;
  }
  const flagText = [
    ...(candidate.risk_flags || []),
    ...(summary.risk_cap_notes || []),
    candidate.risk_notes || "",
  ].join(" ").toLowerCase();
  const staleBadge = (candidate.stale_facts || []).length
    ? `<span class="row-badge warn" title="Stale: ${escapeHtml(candidate.stale_facts.join(", "))}">STALE</span>`
    : "";
  const badges = rowRiskBadges
    .filter((rule) => rule.test(flagText))
    .map((rule) => {
      const studioRejected = (state.data.profile.reject_flags || []).includes("studio_unit")
        || state.data.profile.unit?.allow_studio === false || isRejected;
      const tone = rule.label === "STUDIO" && !studioRejected ? "neutral" : rule.tone;
      return `<span class="row-badge ${tone === "reject" ? "" : tone}">${rule.label}</span>`;
    })
    .join("") + staleBadge;
  const statusOptions = Object.entries(statusLabels)
    .map(([value, label]) => `<option value="${value}" ${value === candidate.status ? "selected" : ""}>${escapeHtml(label)}</option>`)
    .join("");
  const statusSelect = isBaseline || state.data.read_only
    ? `<span class="mini-status ${statusClass}">${escapeHtml(candidate.status || "active")}</span>`
    : `<select class="row-status-select" data-row-status="${escapeHtml(candidate.id)}" aria-label="Change status of ${escapeHtml(candidate.name)}">${statusOptions}</select>`;
  const deleteBtn = isBaseline || state.data.read_only
    ? ""
    : `<button type="button" class="danger icon-button" data-delete-candidate="${escapeHtml(candidate.id)}" aria-label="Archive ${escapeHtml(candidate.name)}" title="Archive">&times;</button>`;
  return `
    <article class="candidate-row ${bandClass} ${selectedClass}" data-candidate-row="${escapeHtml(candidate.id)}">
      <button type="button" class="candidate-select" data-select-candidate="${escapeHtml(candidate.id)}" aria-label="Inspect ${escapeHtml(candidate.name)}">
        <span class="candidate-title">${escapeHtml(candidate.name)}</span>
        <span class="candidate-meta">${escapeHtml(candidate.neighborhood || "Neighborhood unknown")} / ${formatMoney(rentSignal)} ${deltaHtml}</span>
        ${badges ? `<span class="row-badges">${badges}</span>` : ""}
      </button>
      ${statusSelect}
      <span class="score">${score !== null ? score.toFixed(2) : "-"}</span>
      <div class="actions">${deleteBtn}</div>
    </article>
  `;
}

function setExternalMapsEnabled(enabled) {
  state.externalMapsEnabled = Boolean(enabled);
  if (!state.externalMapsEnabled) resetLeafletMap();
  const toggle = $("#externalMapsToggle");
  if (toggle) toggle.checked = state.externalMapsEnabled;
  renderMap();
}

function renderMap() {
  const container = $("#candidateMap");
  const selection = $("#mapSelection");
  if (!container || !selection) return;
  if (!state.externalMapsEnabled) {
    resetLeafletMap();
    container.innerHTML = '<div class="empty-state">External maps are off. Enable OpenStreetMap above to load tiles.</div>';
    renderMapSelection(selectedCandidate());
    return;
  }

  const candidates = visibleCandidates();
  if (!candidates.length) {
    state.selectedCandidateId = null;
    resetLeafletMap();
    container.innerHTML = '<div class="empty-state">No matching locations yet.</div>';
    selection.innerHTML = '<div class="empty-state">Filter less tightly to see candidate locations.</div>';
    return;
  }

  syncSelectedCandidate(candidates);
  const selected = selectedCandidate();
  renderMapSelection(selected);
  try {
    renderLeafletMap(candidates, container);
  } catch (error) {
    resetLeafletMap();
    container.innerHTML = `<div class="empty-state">Map unavailable: ${escapeHtml(error.message)}</div>`;
  }
}

function renderMapSelection(selected) {
  const selection = $("#mapSelection");
  if (!selection) return;
  const selectedSummary = selected?.score_summary || {};
  selection.innerHTML = selected ? `
    <div class="map-selection-card">
      <p class="eyebrow">Selected</p>
      <h3>${escapeHtml(selected.name)}</h3>
      <p>${escapeHtml(selected.neighborhood || "Neighborhood unknown")}</p>
      <p>${escapeHtml(selected.address || "Address unknown")}</p>
      <div class="map-stat-grid">
        <div><span>Score</span><strong>${selectedSummary.weighted_score?.toFixed ? selectedSummary.weighted_score.toFixed(2) : "-"}</strong></div>
        <div><span>Rent signal</span><strong>${formatMoney(selected.all_in_monthly_cost || selected.base_rent)}</strong></div>
        <div><span>Status</span><strong>${escapeHtml(selected.status || "active")}</strong></div>
      </div>
    </div>
    <div class="map-legend">
      <span><i class="legend-dot"></i> candidate</span>
      <span><i class="legend-dot reject"></i> rejected by status or preferences</span>
    </div>
  ` : '<div class="empty-state">Select a candidate to inspect.</div>';
}

function renderLeafletMap(candidates, container) {
  const mapCandidates = candidates
    .map((candidate, index) => ({ candidate, index, location: candidateLocation(candidate) }))
    .filter((item) => item.location);

  if (!mapCandidates.length) {
    resetLeafletMap();
    container.innerHTML = '<div class="empty-state">No mapped locations for the current filters yet.</div>';
    return;
  }

  if (!window.L) {
    throw new Error("Leaflet map library failed to load.");
  }

  if (!leafletMapState.map) {
    container.innerHTML = "";
    leafletMapState.map = L.map(container, {
      center: state.data.profile.map_center || [mapCandidates[0].location.lat, mapCandidates[0].location.lng],
      zoom: 12,
      scrollWheelZoom: false,
    });
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    }).addTo(leafletMapState.map);
    leafletMapState.markerLayer = window.L.markerClusterGroup
      ? L.markerClusterGroup({ maxClusterRadius: 36, spiderfyOnMaxZoom: true, showCoverageOnHover: false })
      : L.layerGroup();
    leafletMapState.markerLayer.addTo(leafletMapState.map);
  }

  leafletMapState.markerLayer.clearLayers();
  leafletMapState.markers.clear();
  mapCandidates.forEach(({ candidate, index, location }) => {
    const marker = createLeafletMarker(candidate, index, location);
    marker.addTo(leafletMapState.markerLayer);
    leafletMapState.markers.set(candidate.id, marker);
  });

  syncLeafletMapViewport(mapCandidates);
}

function resetLeafletMap() {
  if (leafletMapState.map) {
    leafletMapState.map.remove();
  }
  leafletMapState.map = null;
  leafletMapState.markerLayer = null;
  leafletMapState.markers.clear();
  leafletMapState.lastVisibleSignature = "";
  leafletMapState.selectedMarkerId = null;
}

function createLeafletMarker(candidate, index, location) {
  const markerTitle = candidate.map_label || candidate.name;
  const score = scoreValue(candidate);
  const isRejected = candidateRejected(candidate);
  const markerTone = isRejected ? "reject" : "";
  const selectedClass = candidate.id === state.selectedCandidateId ? "selected" : "";
  const icon = L.divIcon({
    className: "",
    html: `<span class="leaflet-map-marker ${markerTone} ${selectedClass}">${index + 1}</span>`,
    iconSize: selectedClass ? [34, 34] : [26, 26],
    iconAnchor: selectedClass ? [17, 17] : [13, 13],
  });

  const marker = L.marker([location.lat, location.lng], {
    title: markerTitle,
    keyboard: true,
    icon,
    zIndexOffset: candidate.id === state.selectedCandidateId ? 1000 : 0,
  });
  marker.on("click", () => selectCandidate(candidate.id, { scrollList: true }));
  return marker;
}

function syncLeafletMapViewport(mapCandidates) {
  const visibleSignature = mapCandidates.map(({ candidate }) => candidate.id).join("|");
  const selectedItem = mapCandidates.find(({ candidate }) => candidate.id === state.selectedCandidateId);
  const visibleChanged = visibleSignature !== leafletMapState.lastVisibleSignature;
  const selectionChanged = state.selectedCandidateId !== leafletMapState.selectedMarkerId;

  if (visibleChanged) {
    if (mapCandidates.length === 1) {
      leafletMapState.map.setView([mapCandidates[0].location.lat, mapCandidates[0].location.lng], 14);
    } else {
      const bounds = L.latLngBounds(mapCandidates.map(({ location }) => [location.lat, location.lng]));
      leafletMapState.map.fitBounds(bounds, { padding: [48, 48] });
    }
  } else if (selectionChanged && selectedItem) {
    leafletMapState.map.panTo([selectedItem.location.lat, selectedItem.location.lng]);
  }

  leafletMapState.lastVisibleSignature = visibleSignature;
  leafletMapState.selectedMarkerId = state.selectedCandidateId;
}

function renderLedger() {
  const ledger = [...state.data.ledger].reverse();
  $("#ledgerList").innerHTML = ledger.length
    ? ledger.map((item) => `
      <div class="ledger-item">
        <strong>${escapeHtml(item.title)}</strong>
        <div class="small">${escapeHtml(item.date)} / ${escapeHtml(item.status)}</div>
        <p>${escapeHtml(item.summary)}</p>
      </div>
    `).join("")
    : '<div class="empty-state">No decision entries yet.</div>';
}

function selectedCandidate() {
  if (!state.selectedCandidateId) return null;
  return state.data.candidates.find((item) => item.id === state.selectedCandidateId) || null;
}

function baselineCandidate() {
  if (state.compareAnchorId) {
    const pinned = state.data.candidates.find((item) => item.id === state.compareAnchorId);
    if (pinned) return pinned;
  }
  return state.data.candidates.find((item) => item.is_baseline)
    || state.data.candidates.find((item) => item.status === "baseline");
}

function renderComparisonBlock(candidate, summary) {
  const baseline = baselineCandidate();
  const baselineRent = baseline?.base_rent ?? state.data.profile.current_rent;
  const baselineUnit = Array.isArray(baseline?.unit_options) ? baseline.unit_options[0] : null;
  const baselineSize = baselineUnit ? formatSqftRange(baselineUnit.sqft_min, baselineUnit.sqft_max) : "";
  const candidateBaseRent = candidate.id === baseline?.id ? baselineRent : candidate.base_rent;
  const baseDelta = candidate.id === baseline?.id
    ? "Comparison anchor"
    : formatMoneyDelta(candidateBaseRent, baselineRent);
  const allInDelta = candidate.id === baseline?.id
    ? "Comparison anchor"
    : formatMoneyDelta(candidate.all_in_monthly_cost, baselineRent);
  const pricingNotes = candidate.pricing_notes
    || "No unit pricing snapshot saved yet. Verify current availability, parking, fees, and all-in cost before making this a real contender.";

  return `
    <section class="comparison-card" aria-label="Baseline comparison">
      <div class="comparison-head">
        <div>
          <span>Vs baseline</span>
          <strong>${formatMoney(baselineRent)} base at ${escapeHtml(baseline?.name || state.data.profile.current_home_name)}${baselineSize ? ` / ${escapeHtml(baselineSize)}` : ""}</strong>
        </div>
        <strong class="detail-score">${summary.weighted_score?.toFixed ? summary.weighted_score.toFixed(2) : "-"}</strong>
      </div>
      <dl class="comparison-grid">
        <div>
          <dt>Candidate base</dt>
          <dd>${formatMoney(candidateBaseRent)} <span>${escapeHtml(baseDelta)}</span></dd>
        </div>
        <div>
          <dt>Candidate all-in</dt>
          <dd>${formatMoneyPending(candidate.all_in_monthly_cost)} <span>${escapeHtml(allInDelta)}</span></dd>
        </div>
        <div>
          <dt>Unit target</dt>
          <dd>${escapeHtml(candidate.unit_type || "Unknown")}</dd>
        </div>
        <div>
          <dt>Move-in cost</dt>
          <dd>${formatMoneyPending(candidate.move_in_cost)}</dd>
        </div>
      </dl>
      <p class="pricing-note">${escapeHtml(pricingNotes)}</p>
    </section>
  `;
}

function renderUnitOptions(candidate) {
  const options = Array.isArray(candidate.unit_options) ? candidate.unit_options : [];
  const rows = options.length
    ? options.map((option) => `
      <tr>
        <td><strong>${escapeHtml(option.label || "Unit option")}</strong></td>
        <td>${escapeHtml(`${option.bedrooms ?? "-"}BR / ${option.bathrooms ?? "-"}BA`)}</td>
        <td>${escapeHtml(formatSqftRange(option.sqft_min, option.sqft_max))}</td>
        <td>${escapeHtml(formatMoneyRange(option.base_rent_min, option.base_rent_max))}</td>
        <td>${escapeHtml(formatMoneyRange(option.effective_rent_min, option.effective_rent_max))}</td>
        <td>${escapeHtml(normalizeAvailability(option.availability || option.notes || "Verify"))}</td>
      </tr>
    `).join("")
    : `
      <tr>
        <td colspan="6">No unit square-footage comparison saved yet.</td>
      </tr>
    `;

  return `
    <section class="detail-section">
      <h3>Unit Cross-Comparison</h3>
      <div class="detail-table-wrap">
        <table class="detail-table">
          <thead>
            <tr>
              <th>Plan</th>
              <th>Type</th>
              <th>Size</th>
              <th>Base</th>
              <th>Net</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    </section>
  `;
}

function renderFinancialSnapshot(candidate) {
  const fee = candidate.fee_snapshot || {};
  const concession = candidate.concession || {};
  const concessionSummary = concession.summary || "No saved concession signal.";
  const concessionStatus = concession.verified_status || "unknown";
  const concessionMath = [
    concession.months_free ? `${concession.months_free} month(s) free` : "",
    concession.one_time_credit ? `${formatMoney(concession.one_time_credit)} one-time credit` : "",
    concession.expires ? `expires ${concession.expires}` : "",
  ].filter(Boolean).join(" / ") || "No concession math saved.";
  const feeRows = [
    ["Application", fee.application_fee],
    ["Admin", fee.admin_fee],
    ["Deposit", fee.security_deposit],
    ["Monthly fees", fee.required_monthly_fees],
    ["Parking", fee.parking_monthly],
    ["Other move-in", fee.other_move_in_fees],
  ];

  return `
    <section class="detail-section">
      <h3>Fees & Promotions</h3>
      <div class="fee-grid">
        ${feeRows.map(([label, value]) => `
          <div>
            <span>${escapeHtml(label)}</span>
            <strong>${formatFeeValue(value)}</strong>
          </div>
        `).join("")}
      </div>
      <div class="promo-card">
        <span class="mini-status">${escapeHtml(concessionStatus)}</span>
        <strong>${escapeHtml(concessionSummary)}</strong>
        <p>${escapeHtml(concessionMath)}</p>
        ${concession.terms ? `<p>${escapeHtml(concession.terms)}</p>` : ""}
        ${fee.notes ? `<p>${escapeHtml(fee.notes)}</p>` : ""}
      </div>
    </section>
  `;
}

function renderMovingEstimate(candidate) {
  const moving = candidate.moving_estimate || {};
  const hasEstimate = moving.low || moving.high || moving.estimated_drive_miles;
  if (!hasEstimate) {
    return `
      <section class="detail-section">
        <h3>Moving Estimate</h3>
        <p class="pricing-note">No moving estimate saved yet.</p>
      </section>
    `;
  }

  return `
    <section class="detail-section">
      <h3>Moving Estimate</h3>
      <div class="moving-card">
        <div>
          <span>Estimated mover cost</span>
          <strong>${escapeHtml(formatMoneyRange(moving.low, moving.high))}</strong>
        </div>
        <div>
          <span>Distance</span>
          <strong>${escapeHtml(formatRange(moving.estimated_drive_miles, moving.estimated_drive_miles, formatNumber))} mi</strong>
        </div>
        <div>
          <span>Move size basis</span>
          <strong>${escapeHtml(formatSqftRange(moving.source_sqft_estimate, moving.source_sqft_estimate))}</strong>
        </div>
        <div>
          <span>Confidence</span>
          <strong>${escapeHtml(moving.confidence || "Verify")}</strong>
        </div>
      </div>
      <p class="pricing-note">${escapeHtml(moving.basis || "Quote with building elevator/loading dock rules before deciding.")}</p>
    </section>
  `;
}

function renderOfficeCommute(candidate, summary) {
  const commute = candidate.commute_estimate || {};
  const adjustment = summary.office_commute_adjustment || {};
  const best = adjustment.best_minutes === null || adjustment.best_minutes === undefined
    ? "Unknown"
    : `${formatMinutes(adjustment.best_minutes)} by ${escapeHtml(adjustment.best_mode || "best mode")}`;
  const scoreAdjustment = numericValue(adjustment.score_adjustment);
  const adjustmentText = scoreAdjustment === null
    ? "No adjustment"
    : `${scoreAdjustment >= 0 ? "+" : ""}${scoreAdjustment.toFixed(2)}`;

  return `
    <section class="detail-section">
      <h3>${escapeHtml(state.data.profile.anchor_label)} Commute Fit</h3>
      <div class="moving-card">
        <div>
          <span>Walk</span>
          <strong>${escapeHtml(formatMinutes(commute.walk_minutes))}</strong>
        </div>
        <div>
          <span>Drive</span>
          <strong>${escapeHtml(formatMinutes(commute.drive_minutes))}</strong>
        </div>
        <div>
          <span>Best</span>
          <strong>${best}</strong>
        </div>
        <div>
          <span>Score effect</span>
          <strong>${escapeHtml(adjustmentText)}</strong>
        </div>
      </div>
      <p class="pricing-note">${escapeHtml((adjustment.notes || [])[0] || `Add walk or drive minutes to apply the ${state.data.profile.anchor_label.toLowerCase()} commute preference.`)}</p>
    </section>
  `;
}

function renderCandidateDetail() {
  const candidate = selectedCandidate();
  if (!candidate) {
    $("#candidateDetail").innerHTML = '<div class="empty-state">Select a candidate to inspect.</div>';
    return;
  }
  const summary = candidate.score_summary || {};
  const capNotes = (summary.risk_cap_notes || []).length
    ? summary.risk_cap_notes.map((note) => `<li>${escapeHtml(note)}</li>`).join("")
    : "<li>No score caps applied.</li>";
  const rejectFlags = (summary.reject_flags || []).length
    ? summary.reject_flags.map((flag) => `<li>${escapeHtml(riskLabels[flag] || flag)}</li>`).join("")
    : "<li>No reject flags.</li>";
  const sourceLinks = (candidate.source_urls || []).length
    ? candidate.source_urls.map((url) => `<li><a href="${escapeHtml(safeUrl(url))}" target="_blank" rel="noreferrer">${escapeHtml(url)}</a></li>`).join("")
    : "<li>No source links saved.</li>";
  $("#candidateDetail").innerHTML = `
    <div class="candidate-detail-head">
      <div>
        <h3>${escapeHtml(candidate.name)}</h3>
        <p>${escapeHtml(candidate.verdict || "No verdict entered yet.")}</p>
      </div>
    </div>
    ${renderComparisonBlock(candidate, summary)}
    ${renderUnitOptions(candidate)}
    ${renderFinancialSnapshot(candidate)}
    ${renderMovingEstimate(candidate)}
    ${renderOfficeCommute(candidate, summary)}
    <div class="detail-grid">
      <div><strong>Base rent signal</strong><br>${formatMoney(candidate.base_rent)}</div>
      <div><strong>All-in monthly</strong><br>${formatMoney(candidate.all_in_monthly_cost)}</div>
      <div><strong>Annualized spend</strong><br>${formatMoney(candidate.annualized_housing_spend)}</div>
      <div><strong>Address</strong><br>${escapeHtml(candidate.address || "Unknown")}</div>
      <div><strong>View / floor</strong><br>${escapeHtml(candidate.view || "Unknown")} / ${escapeHtml(candidate.floor || "Unknown")}</div>
      <div><strong>Parking</strong><br>${escapeHtml(candidate.parking || "Unknown")}</div>
      ${customNoteFields().map((f) => `<div><strong>${escapeHtml(f.label || titleCase(f.id))}</strong><br>${escapeHtml((candidate.notes || {})[f.id] || "Unknown")}</div>`).join("")}
      <div><strong>Commute</strong><br>${escapeHtml(candidate.commute_notes || "Unknown")}</div>
    </div>
    <h3>Source Links</h3>
    <ul class="source-links">${sourceLinks}</ul>
    <h3>Risk Caps</h3>
    <ul>${capNotes}</ul>
    <h3>Reject Flags</h3>
    <ul>${rejectFlags}</ul>
  `;
}

function themeColor(name, fallback) {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

function renderChart() {
  const canvas = $("#scoreChart");
  const ctx = canvas.getContext("2d");
  const candidate = selectedCandidate();
  const panel = themeColor("--panel", "#ffffff");
  const ink = themeColor("--ink", "#111312");
  const muted = themeColor("--muted", "#646b66");
  const trackBg = themeColor("--soft", "#eef4ef");
  const good = themeColor("--green", "#4f8a5f");
  const ok = themeColor("--gold", "#c79824");
  const low = themeColor("--coral", "#d9574a");

  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = panel;
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  if (!candidate) {
    ctx.fillStyle = muted;
    ctx.font = "18px system-ui";
    ctx.fillText("Add a candidate to see score bars.", 24, 48);
    return;
  }

  const scores = candidate.score_summary?.risk_adjusted_scores || candidate.scores || {};
  const categories = state.data.score_categories;
  const left = 180;
  const top = 34;
  const barHeight = 26;
  const gap = 18;
  const maxWidth = canvas.width - left - 60;

  ctx.fillStyle = ink;
  ctx.font = "bold 18px system-ui";
  ctx.fillText(candidate.name, 24, 24);

  categories.forEach((category, index) => {
    const y = top + index * (barHeight + gap);
    const value = Number(scores[category] || 0);
    const width = Math.max(0, Math.min(10, value)) / 10 * maxWidth;
    ctx.fillStyle = muted;
    ctx.font = "13px system-ui";
    ctx.fillText(categoryLabels[category] || titleCase(category), 24, y + 18);
    ctx.fillStyle = trackBg;
    ctx.fillRect(left, y, maxWidth, barHeight);
    ctx.fillStyle = good;
    ctx.fillRect(left, y, width, barHeight);
    ctx.fillStyle = ink;
    ctx.font = "bold 13px system-ui";
    ctx.fillText(value.toFixed(1), left + maxWidth + 12, y + 18);
  });
}

function extractSourceCards(yamlText) {
  const lines = yamlText.split(/\r?\n/);
  const cards = [];
  let current = null;
  for (const line of lines) {
    const nameMatch = line.match(/^\s*-\s+name:\s+"?([^"]+)"?\s*$/);
    const urlMatch = line.match(/^\s+url:\s+"?([^"]+)"?\s*$/);
    const useMatch = line.match(/^\s+use:\s+"?([^"]+)"?\s*$/);
    if (nameMatch) {
      current = { name: nameMatch[1], url: "", use: "" };
      cards.push(current);
    } else if (current && urlMatch) {
      current.url = urlMatch[1];
    } else if (current && useMatch) {
      current.use = useMatch[1];
    }
  }
  return cards.slice(0, 18);
}

function renderSourceRegistry() {
  const cards = extractSourceCards(state.data.research_sources_yaml || "");
  $("#sourceRegistry").innerHTML = cards.map((source) => `
    <div class="source-item">
      <a href="${escapeHtml(safeUrl(source.url))}" target="_blank" rel="noreferrer">${escapeHtml(source.name)}</a>
      <p>${escapeHtml(source.use)}</p>
    </div>
  `).join("");
}

function collectFormCandidate(form) {
  const data = new FormData(form);
  const scores = {};
  state.data.score_categories.forEach((category) => {
    scores[category] = Number(data.get(category) || 5);
  });
  return {
    name: data.get("name"),
    kind: data.get("kind"),
    status: data.get("status"),
    address: data.get("address"),
    neighborhood: data.get("neighborhood"),
    unit_type: data.get("unit_type"),
    base_rent: data.get("base_rent"),
    all_in_monthly_cost: data.get("all_in_monthly_cost"),
    pricing_notes: data.get("pricing_notes"),
    move_in_cost: data.get("move_in_cost"),
    floor: data.get("floor"),
    view: data.get("view"),
    parking: data.get("parking"),
    ...Object.fromEntries(customNoteFields().map((f) => [f.id, data.get(`note:${f.id}`) || ""])),
    commute_notes: data.get("commute_notes"),
    risk_notes: data.get("risk_notes"),
    verdict: data.get("verdict"),
    source_urls: String(data.get("source_urls") || "")
      .split(",")
      .map((url) => url.trim())
      .filter(Boolean),
    walk_minutes: data.get("walk_minutes"),
    drive_minutes: data.get("drive_minutes"),
    scores,
    risk_flags: data.getAll("risk_flags"),
  };
}

function wireEvents() {
  $("#externalMapsToggle")?.addEventListener("change", (event) => setExternalMapsEnabled(event.target.checked));
  const rerenderCandidateSelection = () => {
    savePersistedUi();
    render();
  };

  $("#candidateSearch").addEventListener("input", rerenderCandidateSelection);
  $("#statusFilter").addEventListener("change", rerenderCandidateSelection);
  $("#areaFilter").addEventListener("change", rerenderCandidateSelection);
  $("#candidateSort").addEventListener("change", rerenderCandidateSelection);

  // Keyboard nav for candidate list
  $("#candidateList").addEventListener("keydown", (event) => {
    if (event.key !== "ArrowUp" && event.key !== "ArrowDown" && event.key !== "Enter") return;
    const candidates = visibleCandidates();
    if (!candidates.length) return;
    const currentIndex = candidates.findIndex((c) => c.id === state.selectedCandidateId);
    if (event.key === "Enter") {
      $("#editSelectedBtn")?.click();
      return;
    }
    event.preventDefault();
    const nextIndex = event.key === "ArrowDown"
      ? Math.min(candidates.length - 1, currentIndex + 1)
      : Math.max(0, currentIndex - 1);
    selectCandidate(candidates[nextIndex].id, { scrollList: true });
  });

  // Edit selected -> prefill intake form, expand
  $("#editSelectedBtn")?.addEventListener("click", () => {
    const candidate = selectedCandidate();
    if (!candidate || state.data.read_only) return;
    prefillIntakeForm(candidate);
    const intake = $("#intakeWrap");
    if (intake) intake.open = true;
    intake?.scrollIntoView({ behavior: "smooth", block: "start" });
  });

  $("#cancelEditBtn")?.addEventListener("click", () => {
    state.editingCandidateId = null;
    $("#candidateForm").reset();
    resetScoreSliders();
    $("#cancelEditBtn").hidden = true;
    $("#formMessage").textContent = "";
  });

  // Pin compare anchor
  $("#setAnchorBtn")?.addEventListener("click", () => {
    if (!state.selectedCandidateId) return;
    state.compareAnchorId = state.compareAnchorId === state.selectedCandidateId
      ? null
      : state.selectedCandidateId;
    savePersistedUi();
    render();
  });

  // Rent warning when user types above soft ceiling
  const rentWarn = document.createElement("p");
  rentWarn.id = "rentWarning";
  rentWarn.className = "rent-warning";
  rentWarn.textContent = "Above soft ceiling. Strong justification required.";
  const rentInput = document.querySelector('input[name="all_in_monthly_cost"]');
  rentInput?.parentElement?.appendChild(rentWarn);
  const updateRentWarning = () => {
    const value = Number(rentInput?.value || 0);
    const ceiling = Number(state.data?.profile?.soft_ceiling_value || 0);
    rentWarn.textContent = `Above soft ceiling (${formatMoney(ceiling)}). Strong justification required.`;
    rentWarn.classList.toggle("show", ceiling > 0 && value > ceiling);
  };
  rentInput?.addEventListener("input", updateRentWarning);

  $("#candidateForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const message = $("#formMessage");
    if (state.data.read_only) { message.textContent = "This example is read-only."; return; }
    message.textContent = "Saving...";
    try {
      const payload = collectFormCandidate(form);
      if (state.editingCandidateId) {
        payload.id = state.editingCandidateId;
        payload.version = state.data.candidates.find((c) => c.id === state.editingCandidateId)?.version;
      }
      const result = await api("/api/candidates", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      state.data = result.state;
      state.selectedCandidateId = result.candidate.id;
      state.editingCandidateId = null;
      $("#cancelEditBtn").hidden = true;
      form.reset();
      resetScoreSliders();
      message.textContent = "Saved.";
      render();
    } catch (error) {
      message.textContent = error.message;
    }
  });

  $("#briefForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const response = await api("/api/research-brief", {
      method: "POST",
      body: JSON.stringify({
        target: data.get("target"),
        question: data.get("question"),
      }),
    });
    $("#briefOutput").textContent = response.brief;
  });
}

function customNoteFields() {
  return (state.data?.note_fields || []).filter((f) => f && /^[a-z0-9_]+$/.test(f.id || ""));
}

function renderCustomNoteInputs() {
  const host = document.getElementById("customNoteFields");
  if (!host) return;
  host.innerHTML = customNoteFields()
    .map((f) => `<label>${escapeHtml(f.label || titleCase(f.id))}
      <textarea name="note:${escapeHtml(f.id)}" rows="3" placeholder="${escapeHtml(f.placeholder || "")}"></textarea>
    </label>`)
    .join("");
}

function resetScoreSliders() {
  document.querySelectorAll(".score-control input").forEach((input) => {
    input.value = 5;
    const label = document.getElementById(`score-value-${input.name}`);
    if (label) label.textContent = "5.0";
  });
}

function prefillIntakeForm(candidate) {
  const form = $("#candidateForm");
  if (!form) return;
  state.editingCandidateId = candidate.id;
  const setField = (name, value) => {
    const field = form.elements.namedItem(name);
    if (!field) return;
    field.value = value === null || value === undefined ? "" : value;
  };
  setField("name", candidate.name);
  setField("kind", candidate.kind || "building");
  setField("status", candidate.status || "active");
  setField("address", candidate.address);
  setField("neighborhood", candidate.neighborhood);
  setField("unit_type", candidate.unit_type);
  setField("base_rent", candidate.base_rent);
  setField("all_in_monthly_cost", candidate.all_in_monthly_cost);
  setField("pricing_notes", candidate.pricing_notes);
  setField("move_in_cost", candidate.move_in_cost);
  setField("floor", candidate.floor);
  setField("view", candidate.view);
  setField("source_urls", (candidate.source_urls || []).join(", "));
  setField("walk_minutes", candidate.commute_estimate?.walk_minutes);
  setField("drive_minutes", candidate.commute_estimate?.drive_minutes);
  setField("parking", candidate.parking);
  customNoteFields().forEach((f) => setField(`note:${f.id}`, (candidate.notes || {})[f.id]));
  setField("risk_notes", candidate.risk_notes);
  setField("commute_notes", candidate.commute_notes);
  setField("verdict", candidate.verdict);
  const scores = candidate.scores || candidate.score_summary?.scores || {};
  Object.entries(scores).forEach(([key, value]) => {
    const slider = form.elements.namedItem(key);
    if (slider) {
      slider.value = value;
      const label = document.getElementById(`score-value-${key}`);
      if (label) label.textContent = Number(value).toFixed(1);
    }
  });
  form.querySelectorAll('input[name="risk_flags"]').forEach((box) => {
    box.checked = (candidate.risk_flags || []).includes(box.value);
  });
  $("#cancelEditBtn").hidden = false;
  $("#formMessage").textContent = `Editing ${candidate.name}. Save to update.`;
}

function applyPersistedUi() {
  const persisted = loadPersistedUi();
  if (persisted.search !== undefined) $("#candidateSearch").value = persisted.search;
  if (persisted.status) $("#statusFilter").value = persisted.status;
  if (persisted.area) $("#areaFilter").value = persisted.area;
  if (persisted.sort) $("#candidateSort").value = persisted.sort;
  if (persisted.compareAnchor) state.compareAnchorId = persisted.compareAnchor;
  if (persisted.selected) state.selectedCandidateId = persisted.selected;
}

// --- Preferences editor ---------------------------------------------------------------------
const prefs = { profile: null, draft: null, dirty: false };

async function loadPrefsProfile() {
  prefs.profile = await api("/api/profile");
  resetPrefsDraft();
}

function resetPrefsDraft() {
  if (!prefs.profile) return;
  const p = prefs.profile;
  const flags = Array.from(new Set([...Object.keys(p.risk_caps || {}), ...(p.reject_flags || [])])).sort();
  prefs.draft = {
    weights: Object.fromEntries((p.categories || []).map((c) => [c.id, Math.round(Number(c.weight) * 1000) / 10])),
    budget: { ...(p.budget || {}) },
    rejectFlags: new Set(p.reject_flags || []),
    allFlags: flags,
  };
  prefs.dirty = false;
  renderPrefsForm();
}

function prefsTotal() {
  return Object.values(prefs.draft?.weights || {}).reduce((s, v) => s + Number(v || 0), 0);
}

function prefsTotalOk() {
  return Math.abs(prefsTotal() - 100) < 0.05;
}

function flagLabel(flag) {
  return (prefs.profile?.flag_labels || {})[flag] || state.data?.flag_labels?.[flag] || flag;
}

function renderPrefsForm() {
  const form = $("#prefsForm");
  if (!form || !prefs.draft || !prefs.profile) return;
  const { weights, budget, rejectFlags, allFlags } = prefs.draft;
  $("#prefsWeights").innerHTML = prefs.profile.categories.map((c) => `
    <label class="prefs-weight">
      <span>${escapeHtml(c.label || c.id)}</span>
      <input type="range" min="0" max="100" step="0.5" data-cat="${escapeHtml(c.id)}" value="${escapeHtml(weights[c.id])}">
      <output id="prefs-w-${escapeHtml(c.id)}">${escapeHtml(Number(weights[c.id]).toFixed(1))}%</output>
    </label>`).join("");
  ["ideal_min", "ideal_max", "soft_ceiling", "hard_ceiling"].forEach((k) => {
    const input = form.querySelector(`[name="${k}"]`);
    if (input) input.value = budget[k] ?? "";
  });
  $("#prefsRejectFlags").innerHTML = allFlags.length ? allFlags.map((f) => `
    <label class="check"><input type="checkbox" value="${escapeHtml(f)}" ${rejectFlags.has(f) ? "checked" : ""}>
    ${escapeHtml(flagLabel(f))}</label>`).join("") : '<p class="muted">No flags defined.</p>';
  updatePrefsTotal();
}

function updatePrefsTotal() {
  const total = prefsTotal();
  const el = $("#prefsTotal");
  if (el) {
    el.textContent = `${total.toFixed(1)}%`;
    el.classList.toggle("bad", !prefsTotalOk());
  }
  const readOnly = Boolean(state.data?.read_only);
  const propose = $("#prefsPropose");
  if (propose) propose.disabled = readOnly || !prefsTotalOk();
  const preview = $("#prefsPreview");
  if (preview) preview.disabled = !prefsTotalOk();
}

function buildPrefsPatch() {
  const p = prefs.profile;
  const d = prefs.draft;
  const patch = {};
  const cats = p.categories.map((c) => ({ ...c, weight: Math.round(Number(d.weights[c.id] || 0) * 10) / 1000 }));
  // absorb rounding drift into the largest weight so the sum is exactly 1.0
  const drift = Math.round((1 - cats.reduce((s, c) => s + c.weight, 0)) * 1000) / 1000;
  if (drift && cats.length) {
    const big = cats.reduce((a, c) => (c.weight > a.weight ? c : a), cats[0]);
    big.weight = Math.round((big.weight + drift) * 1000) / 1000;
  }
  if (cats.some((c, i) => c.weight !== p.categories[i].weight)) patch.categories = cats;
  const budget = {};
  ["ideal_min", "ideal_max", "soft_ceiling", "hard_ceiling"].forEach((k) => {
    const v = numericValue(d.budget[k]);
    if (v !== null && v !== undefined && v !== (p.budget || {})[k]) budget[k] = v;
  });
  if (Object.keys(budget).length) patch.budget = budget;
  const rejects = Array.from(d.rejectFlags).sort();
  if (JSON.stringify(rejects) !== JSON.stringify([...(p.reject_flags || [])].sort())) patch.reject_flags = rejects;
  return patch;
}

function mergeProfilePatch(before, patch) {
  if (!patch || typeof patch !== "object" || Array.isArray(patch)) return patch;
  const out = { ...(before || {}) };
  for (const [key, value] of Object.entries(patch)) {
    Object.defineProperty(out, key, { value: value && typeof value === "object" && !Array.isArray(value)
      ? mergeProfilePatch(before?.[key], value) : value, enumerable: true, configurable: true });
  }
  return out;
}

function renderProposalDiff(proposal) {
  const before = Object.hasOwn(proposal, "before") ? proposal.before : prefs.profile;
  if (!before) return '<p>Load preferences to review the exact changes before applying.</p>';
  const after = proposal.after || mergeProfilePatch(before, proposal.patch);
  const keys = [...new Set([...Object.keys(before), ...Object.keys(after)])]
    .filter((key) => JSON.stringify(before[key]) !== JSON.stringify(after[key]));
  const value = (obj, key) => Object.hasOwn(obj, key) ? JSON.stringify(obj[key], null, 2) : "(absent)";
  return keys.length ? `<table class="prefs-table proposal-diff"><thead><tr><th>Field</th><th>Before</th><th>After</th></tr></thead>
    <tbody>${keys.map((key) => `<tr><th>${escapeHtml(key)}</th><td><pre>${escapeHtml(value(before, key))}</pre></td><td><pre>${escapeHtml(value(after, key))}</pre></td></tr>`).join("")}</tbody></table>`
    : '<p>No changes: this proposal already matches the current profile.</p>';
}

function renderPrefsPreview(result) {
  const out = $("#prefsPreviewOut");
  if (!out) return;
  if (!result) { out.innerHTML = ""; return; }
  const row = (r) => {
    let move = '<span class="muted">new</span>';
    if (r.moved > 0) move = `<span class="up">▲ ${r.moved}</span>`;
    else if (r.moved < 0) move = `<span class="down">▼ ${-r.moved}</span>`;
    else if (r.moved === 0) move = '<span class="muted">=</span>';
    const prev = r.previous_score === null || r.previous_score === undefined ? "-" : Number(r.previous_score).toFixed(2);
    return `<tr><td>${r.rank}</td><td>${escapeHtml(r.name)}</td><td>${prev}</td><td>${Number(r.final_score).toFixed(2)}</td><td>${move}</td></tr>`;
  };
  out.innerHTML = result.after.length ? `<table class="prefs-table"><thead><tr><th>#</th><th>Candidate</th><th>Before</th><th>After</th><th>Move</th></tr></thead>
    <tbody>${result.after.map(row).join("")}</tbody></table>` : '<p class="muted">No scored candidates to rank.</p>';
}

function renderPrefsProposals() {
  const box = $("#prefsProposals");
  if (!box) return;
  const list = state.data?.pending_proposals || [];
  const readOnly = Boolean(state.data?.read_only);
  box.innerHTML = list.length ? list.map((p) => {
    const llm = String(p.actor || "").startsWith("llm:");
    return `<article class="prefs-proposal${llm ? " from-llm" : ""}">
      <header>${llm ? '<span class="badge llm">from LLM</span> ' : ""}<strong>${escapeHtml(p.actor)}</strong>
        <span class="muted">${escapeHtml((p.ts || "").slice(0, 16))}</span></header>
      <p>${escapeHtml(p.reason || "")}</p>
      ${renderProposalDiff(p)}
      ${p.stale ? '<p role="status">Profile changed since this proposal. Reject it and create a new proposal after reviewing current preferences.</p>' : ""}
      <div class="prefs-actions">
        <button type="button" data-prop-apply="${escapeHtml(p.id)}" ${readOnly || p.stale || (!p.before && !prefs.profile) ? "disabled" : ""}>Apply</button>
        <button type="button" data-prop-reject="${escapeHtml(p.id)}" ${readOnly ? "disabled" : ""}>Reject</button>
      </div></article>`;
  }).join("") : '<p class="muted">No pending proposals.</p>';
}

function renderPreferences() {
  const readOnly = Boolean(state.data?.read_only);
  const notice = $("#prefsReadOnly");
  if (notice) notice.hidden = !readOnly;
  const fields = $("#prefsFields");
  // Example profiles may explore previews, but cannot propose or apply changes.
  if (fields) fields.disabled = false;
  if (!prefs.draft && prefs.profile) resetPrefsDraft();
  updatePrefsTotal();
  renderPrefsProposals();
}

async function resolveProposal(id, verb) {
  const msg = $("#prefsMessage");
  let reason = "";
  if (verb === "reject") {
    reason = window.prompt("Reason for rejecting?") || "";
  }
  try {
    const result = await api(`/api/profile/proposals/${encodeURIComponent(id)}/${verb}`, {
      method: "POST", body: JSON.stringify({ reason, profile_revision: state.data.profile_revision }),
    });
    state.data = result.state;
    if (verb === "apply") await loadPrefsProfile();
    renderPrefsPreview(null);
    msg.textContent = verb === "apply" ? "Proposal applied; scores recomputed." : "Proposal rejected.";
    render();
  } catch (error) {
    if (error.status === 409) {
      await loadPrefsProfile();
      await loadState();
      renderPrefsPreview(null);
    }
    msg.textContent = error.message;
  }
}

function wirePreferences() {
  const form = $("#prefsForm");
  if (!form) return;
  form.addEventListener("input", (event) => {
    const t = event.target;
    if (!prefs.draft) return;
    if (t.dataset.cat) {
      prefs.draft.weights[t.dataset.cat] = Number(t.value);
      const out = document.getElementById(`prefs-w-${t.dataset.cat}`);
      if (out) out.textContent = `${Number(t.value).toFixed(1)}%`;
    } else if (["ideal_min", "ideal_max", "soft_ceiling", "hard_ceiling"].includes(t.name)) {
      prefs.draft.budget[t.name] = t.value === "" ? null : Number(t.value);
    }
    updatePrefsTotal();
  });
  $("#prefsRejectFlags").addEventListener("change", (event) => {
    const t = event.target;
    if (!prefs.draft || t.type !== "checkbox") return;
    if (t.checked) prefs.draft.rejectFlags.add(t.value); else prefs.draft.rejectFlags.delete(t.value);
  });
  $("#prefsNormalize").addEventListener("click", () => {
    const w = prefs.draft?.weights;
    if (!w) return;
    const total = prefsTotal();
    const ids = Object.keys(w);
    if (!total) ids.forEach((id) => { w[id] = 100 / ids.length; });
    else ids.forEach((id) => { w[id] = Math.round((w[id] / total) * 1000) / 10; });
    const drift = Math.round((100 - prefsTotal()) * 10) / 10;
    if (drift && ids.length) {
      const big = ids.reduce((a, id) => (w[id] > w[a] ? id : a), ids[0]);
      w[big] = Math.round((w[big] + drift) * 10) / 10;
    }
    renderPrefsForm();
  });
  $("#prefsReset").addEventListener("click", () => { resetPrefsDraft(); renderPrefsPreview(null); $("#prefsMessage").textContent = ""; });
  $("#prefsPreview").addEventListener("click", async () => {
    const msg = $("#prefsMessage");
    const patch = buildPrefsPatch();
    if (!Object.keys(patch).length) { msg.textContent = "No changes to preview."; return; }
    try {
      renderPrefsPreview(await api("/api/profile/preview", { method: "POST", body: JSON.stringify({ patch }) }));
      msg.textContent = "Preview only — nothing saved.";
    } catch (error) { msg.textContent = error.message; }
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const msg = $("#prefsMessage");
    if (!prefsTotalOk()) { msg.textContent = "Weights must total 100%."; return; }
    const patch = buildPrefsPatch();
    if (!Object.keys(patch).length) { msg.textContent = "No changes to propose."; return; }
    const reason = form.querySelector('[name="reason"]').value.trim();
    try {
      const result = await api("/api/profile/proposals", { method: "POST", body: JSON.stringify({ patch, reason, profile_revision: prefs.profile.profile_revision }) });
      state.data = result.state;
      msg.textContent = result.id ? `Proposal ${result.id} created. Review the full before/after diff below before applying.` : "No preference changes to apply.";
      render();
    } catch (error) {
      if (error.status === 409) { await loadPrefsProfile(); await loadState(); renderPrefsPreview(null); }
      msg.textContent = error.message;
    }
  });
  $("#prefsProposals").addEventListener("click", (event) => {
    const t = event.target.closest("button");
    if (!t) return;
    if (t.dataset.propApply) resolveProposal(t.dataset.propApply, "apply");
    if (t.dataset.propReject) resolveProposal(t.dataset.propReject, "reject");
  });
}

wirePreferences();
loadPrefsProfile().then(() => renderPreferences()).catch((error) => {
  const msg = document.querySelector("#prefsMessage");
  if (msg) msg.textContent = `Could not load preferences: ${error.message}`;
});

wireEvents();
loadState()
  .then(() => {
    applyPersistedUi();
    render();
  })
  .catch((error) => {
    document.body.innerHTML = `<main class="shell"><section class="panel"><h1>Dashboard failed to load</h1><p>${escapeHtml(error.message)}</p></section></main>`;
  });
