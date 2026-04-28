const state = {
  recommendations: [],
  resources: [],
  selectedId: null,
};

const elements = {
  runMeta: document.querySelector("#runMeta"),
  productionCount: document.querySelector("#productionCount"),
  totalCount: document.querySelector("#totalCount"),
  watchlistCount: document.querySelector("#watchlistCount"),
  holdCount: document.querySelector("#holdCount"),
  insufficientCount: document.querySelector("#insufficientCount"),
  portfolioSummary: document.querySelector("#portfolioSummary"),
  resultCount: document.querySelector("#resultCount"),
  rows: document.querySelector("#recommendationRows"),
  search: document.querySelector("#searchInput"),
  type: document.querySelector("#typeFilter"),
  risk: document.querySelector("#riskFilter"),
  confidence: document.querySelector("#confidenceFilter"),
  detailTitle: document.querySelector("#detailTitle"),
  detailStatus: document.querySelector("#detailStatus"),
  detailEvidence: document.querySelector("#detailEvidence"),
  detailGuardrails: document.querySelector("#detailGuardrails"),
};

function countBy(items, key) {
  return items.reduce((acc, item) => {
    const value = item[key] || "unknown";
    acc[value] = (acc[value] || 0) + 1;
    return acc;
  }, {});
}

function formatLabel(value) {
  return String(value || "").replaceAll("_", " ");
}

function pill(value) {
  const className = `pill pill-${value || "unknown"}`;
  return `<span class="${className}">${formatLabel(value)}</span>`;
}

function evidencePreview(item) {
  return (item.evidence_json || []).slice(0, 2).join(" ");
}

function matchesFilters(item) {
  const search = elements.search.value.trim().toLowerCase();
  const haystack = [
    item.resource_name,
    item.resource_id,
    item.recommendation_type,
    item.risk_level,
    item.confidence,
    ...(item.evidence_json || []),
  ]
    .join(" ")
    .toLowerCase();

  return (
    (!search || haystack.includes(search)) &&
    (!elements.type.value || item.recommendation_type === elements.type.value) &&
    (!elements.risk.value || item.risk_level === elements.risk.value) &&
    (!elements.confidence.value || item.confidence === elements.confidence.value)
  );
}

function renderSummary(report) {
  const production = state.recommendations.filter((item) => item.environment === "production");
  const allResources = state.resources.length;
  const productionResources = state.resources.filter((item) => item.environment === "production").length;
  const counts = countBy(production, "recommendation_type");

  elements.productionCount.textContent = productionResources || production.length;
  elements.totalCount.textContent = `${allResources || state.recommendations.length} total analyzed`;
  elements.watchlistCount.textContent = counts.watchlist || 0;
  elements.holdCount.textContent = counts.hold || 0;
  elements.insufficientCount.textContent = counts.insufficient_data || 0;
  elements.portfolioSummary.textContent = report?.summary_text || "No scored report has been generated yet.";

  const generatedAt = report?.created_at_utc ? new Date(report.created_at_utc).toLocaleString() : "not available";
  elements.runMeta.textContent = `Latest report: ${generatedAt}`;
}

function renderRows() {
  const filtered = state.recommendations.filter((item) => item.environment === "production").filter(matchesFilters);
  elements.resultCount.textContent = `${filtered.length} shown`;
  if (!filtered.length) {
    elements.rows.innerHTML = '<tr><td colspan="5">No recommendations match the current filters.</td></tr>';
    return;
  }

  elements.rows.innerHTML = filtered
    .map(
      (item) => `
        <tr data-id="${item.recommendation_id}" class="${item.recommendation_id === state.selectedId ? "selected" : ""}">
          <td>
            <span class="resource-name">${item.resource_name}</span>
            <span class="subtle">${item.resource_type} / ${item.environment}</span>
          </td>
          <td>${pill(item.recommendation_type)}</td>
          <td>${pill(item.risk_level)}</td>
          <td>${formatLabel(item.confidence)}</td>
          <td>${evidencePreview(item)}</td>
        </tr>
      `
    )
    .join("");

  elements.rows.querySelectorAll("tr[data-id]").forEach((row) => {
    row.addEventListener("click", () => selectRecommendation(row.dataset.id));
  });
}

function renderList(target, items) {
  target.innerHTML = "";
  if (!items.length) {
    const item = document.createElement("li");
    item.textContent = "No entries available.";
    target.appendChild(item);
    return;
  }
  items.forEach((text) => {
    const item = document.createElement("li");
    item.textContent = text;
    target.appendChild(item);
  });
}

function selectRecommendation(id) {
  state.selectedId = id;
  const item = state.recommendations.find((candidate) => candidate.recommendation_id === id);
  if (!item) {
    return;
  }
  elements.detailTitle.textContent = item.resource_name;
  elements.detailStatus.textContent = `${formatLabel(item.recommendation_type)} / ${formatLabel(item.risk_level)} risk`;
  renderList(elements.detailEvidence, item.evidence_json || []);
  renderList(elements.detailGuardrails, item.guardrails_json || []);
  renderRows();
}

async function fetchJson(url) {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`${url} returned ${response.status}`);
  }
  return response.json();
}

async function loadDashboard() {
  try {
    const [report, recommendations, resources] = await Promise.all([
      fetchJson("/api/reports/latest").catch(() => null),
      fetchJson("/api/recommendations"),
      fetchJson("/api/resources?active_only=true"),
    ]);
    state.recommendations = recommendations.recommendations || [];
    state.resources = resources.resources || [];
    renderSummary(report);
    renderRows();
    const firstPriority = state.recommendations.find((item) => item.environment === "production" && item.recommendation_type === "watchlist");
    if (firstPriority) {
      selectRecommendation(firstPriority.recommendation_id);
    }
  } catch (error) {
    elements.portfolioSummary.textContent = `Dashboard data could not be loaded: ${error.message}`;
    elements.rows.innerHTML = '<tr><td colspan="5">Unable to load recommendations.</td></tr>';
  }
}

[elements.search, elements.type, elements.risk, elements.confidence].forEach((control) => {
  control.addEventListener("input", renderRows);
});

loadDashboard();
