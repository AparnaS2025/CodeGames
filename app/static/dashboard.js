const state = {
  recommendations: [],
  resources: [],
  selectedId: null,
  selectedRecommendation: null,
};

const elements = {
  productionCount: document.querySelector("#productionCount"),
  totalCount: document.querySelector("#totalCount"),
  watchlistCount: document.querySelector("#watchlistCount"),
  holdCount: document.querySelector("#holdCount"),
  insufficientCount: document.querySelector("#insufficientCount"),
  portfolioSummary: document.querySelector("#portfolioSummary"),
  resultCount: document.querySelector("#resultCount"),
  actionItemCount: document.querySelector("#actionItemCount"),
  actionItems: document.querySelector("#actionItems"),
  rows: document.querySelector("#recommendationRows"),
  search: document.querySelector("#searchInput"),
  type: document.querySelector("#typeFilter"),
  risk: document.querySelector("#riskFilter"),
  confidence: document.querySelector("#confidenceFilter"),
  detailTitle: document.querySelector("#detailTitle"),
  detailStatus: document.querySelector("#detailStatus"),
  detailEvidence: document.querySelector("#detailEvidence"),
  detailGuardrails: document.querySelector("#detailGuardrails"),
  approvalPack: document.querySelector("#approvalPack"),
  reviewerInput: document.querySelector("#reviewerInput"),
  reviewComment: document.querySelector("#reviewComment"),
  workflowStatus: document.querySelector("#workflowStatus"),
  reviewButtons: document.querySelectorAll("[data-review-decision]"),
  agentStatus: document.querySelector("#agentStatus"),
  agentQuery: document.querySelector("#agentQuery"),
  agentAskButton: document.querySelector("#agentAskButton"),
  agentResponse: document.querySelector("#agentResponse"),
  agentTrace: document.querySelector("#agentTrace"),
  agentPrompts: document.querySelectorAll("[data-agent-prompt]"),
  agentModeInputs: document.querySelectorAll("input[name='agentMode']"),
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

function renderActionItems(actionItems) {
  const pendingCount = actionItems.filter((item) => item.status === "pending").length;
  elements.actionItemCount.textContent = `${pendingCount} pending`;
  if (!actionItems.length) {
    elements.actionItems.innerHTML = "<p>No capacity action items created yet.</p>";
    return;
  }

  elements.actionItems.innerHTML = actionItems
    .map((item) => {
      const payload = item.payload_json || {};
      const resourceName = payload.resource_name || item.recommendation_id;
      const target = payload.suggested_size ? `${payload.current_size} -> ${payload.suggested_size}` : payload.current_size || "n/a";
      return `
        <article class="action-card">
          <div>
            <h3>${resourceName}</h3>
            <p class="subtle">${formatLabel(item.action_type)} / ${formatLabel(item.status)}</p>
          </div>
          <div class="action-meta">
            <span>${target}</span>
            <span>Created by ${item.created_by}</span>
            <span>${new Date(item.created_at_utc).toLocaleString()}</span>
          </div>
        </article>
      `;
    })
    .join("");
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

function formatApprovalPack(detail) {
  const pack = detail.approval_pack;
  if (!pack) {
    return "Approval pack is unavailable for this recommendation.";
  }
  const approvers = pack.approval_policy?.required_approvers?.join(", ") || "platform_reviewer";
  const actionType = pack.action_preview?.action_type || "no follow-up action";
  return [
    pack.approval_summary,
    `Approval required: ${pack.approval_required ? "yes" : "acknowledgement only"}.`,
    `Required approvers: ${approvers}.`,
    `Action after approval: ${formatLabel(actionType)}.`,
  ].join("\n");
}

function formatWorkflowStatus(detail) {
  const latestReview = detail.review_history?.at(-1);
  const action = detail.action_item;
  if (action) {
    return `Action item ${action.action_id} is ${action.status} (${formatLabel(action.action_type)}).`;
  }
  if (latestReview) {
    return `Latest decision: ${formatLabel(latestReview.decision)} by ${latestReview.reviewer}.`;
  }
  return "No workflow decision recorded.";
}

async function selectRecommendation(id) {
  state.selectedId = id;
  const item = state.recommendations.find((candidate) => candidate.recommendation_id === id);
  if (!item) {
    return;
  }
  elements.detailTitle.textContent = item.resource_name;
  elements.detailStatus.textContent = `${formatLabel(item.recommendation_type)} / ${formatLabel(item.risk_level)} risk`;
  renderList(elements.detailEvidence, item.evidence_json || []);
  renderList(elements.detailGuardrails, item.guardrails_json || []);
  elements.approvalPack.textContent = "Preparing approval pack...";
  elements.workflowStatus.textContent = "Loading workflow state...";
  renderRows();

  try {
    const detail = await fetchJson(`/api/recommendations/${id}`);
    state.selectedRecommendation = detail;
    elements.detailStatus.textContent = `${formatLabel(detail.status)} / ${formatLabel(detail.risk_level)} risk`;
    renderList(elements.detailEvidence, detail.evidence_json || []);
    renderList(elements.detailGuardrails, detail.guardrails_json || []);
    elements.approvalPack.textContent = formatApprovalPack(detail);
    elements.workflowStatus.textContent = formatWorkflowStatus(detail);
  } catch (error) {
    elements.approvalPack.textContent = `Approval pack could not be loaded: ${error.message}`;
    elements.workflowStatus.textContent = "Workflow state unavailable.";
  }
}

async function fetchJson(url) {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`${url} returned ${response.status}`);
  }
  return response.json();
}

async function postJson(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`${url} returned ${response.status}: ${detail}`);
  }
  return response.json();
}

function runLabelFromQuery(query) {
  const normalized = query
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 40);
  const timestamp = new Date().toISOString().replace(/[-:]/g, "").slice(0, 12);
  return `${normalized || "dashboard-agent"}-${timestamp}`;
}

function selectedAgentMode() {
  return document.querySelector("input[name='agentMode']:checked")?.value || "llm";
}

function renderAgentTrace(toolCalls) {
  if (!toolCalls?.length) {
    elements.agentTrace.textContent = "No tool calls recorded.";
    elements.agentTrace.scrollTop = 0;
    return;
  }
  elements.agentTrace.textContent = toolCalls
    .map((call) => {
      const summary = call.result_summary || {};
      const detail = Object.entries(summary)
        .map(([key, value]) => `${key}=${value}`)
        .join(", ");
      return `${call.name}${detail ? `: ${detail}` : ""}`;
    })
    .join("\n");
  elements.agentTrace.scrollTop = 0;
}

async function askAgent() {
  const query = elements.agentQuery.value.trim();
  if (!query) {
    elements.agentStatus.textContent = "Add a question";
    elements.agentResponse.textContent = "Enter a request before asking the agent.";
    return;
  }

  elements.agentAskButton.disabled = true;
  elements.agentStatus.textContent = "Working";
  elements.agentResponse.textContent = "Running agent workflow...";
  elements.agentTrace.textContent = "";

  try {
    const answerMode = selectedAgentMode();
    const result = await postJson("/api/agent/ask", {
      query,
      run_label: runLabelFromQuery(query),
      answer_mode: answerMode,
    });
    const modeLabel = result.llm_enabled ? "LLM" : "Python";
    elements.agentStatus.textContent = `${formatLabel(result.intent || "complete")} - ${modeLabel}`;
    elements.agentResponse.textContent = result.answer || "The agent completed without a text response.";
    elements.agentResponse.scrollTop = 0;
    renderAgentTrace(result.tool_calls || []);
    await loadDashboard();
  } catch (error) {
    elements.agentStatus.textContent = "Error";
    elements.agentResponse.textContent = `Agent request failed: ${error.message}`;
    elements.agentTrace.textContent = "";
  } finally {
    elements.agentAskButton.disabled = false;
  }
}

async function submitReview(decision) {
  if (!state.selectedId) {
    elements.workflowStatus.textContent = "Select a recommendation before recording a decision.";
    return;
  }
  const reviewer = elements.reviewerInput.value.trim();
  if (!reviewer) {
    elements.workflowStatus.textContent = "Reviewer is required.";
    return;
  }

  elements.reviewButtons.forEach((button) => {
    button.disabled = true;
  });
  elements.workflowStatus.textContent = `Recording ${formatLabel(decision)} decision...`;
  try {
    const result = await postJson(`/api/recommendations/${state.selectedId}/review`, {
      decision,
      reviewer,
      comment: elements.reviewComment.value.trim() || null,
    });
    if (result.action_item) {
      elements.workflowStatus.textContent = `Decision recorded. Action item ${result.action_item.action_id} is pending.`;
    } else {
      elements.workflowStatus.textContent = `Decision recorded: ${formatLabel(result.decision)}.`;
    }
    elements.reviewComment.value = "";
    await loadDashboard();
    await selectRecommendation(state.selectedId);
  } catch (error) {
    elements.workflowStatus.textContent = `Decision failed: ${error.message}`;
  } finally {
    elements.reviewButtons.forEach((button) => {
      button.disabled = false;
    });
  }
}

async function loadDashboard() {
  try {
    const [report, recommendations, resources, actionItems] = await Promise.all([
      fetchJson("/api/reports/latest").catch(() => null),
      fetchJson("/api/recommendations"),
      fetchJson("/api/resources?active_only=true"),
      fetchJson("/api/action-items"),
    ]);
    state.recommendations = recommendations.recommendations || [];
    state.resources = resources.resources || [];
    renderSummary(report);
    renderRows();
    renderActionItems(actionItems.action_items || []);
    const selected = state.recommendations.find((item) => item.recommendation_id === state.selectedId);
    const firstPriority = state.recommendations.find((item) => item.environment === "production" && item.recommendation_type === "watchlist");
    const nextSelection = selected || firstPriority;
    if (nextSelection) {
      await selectRecommendation(nextSelection.recommendation_id);
    }
  } catch (error) {
    elements.portfolioSummary.textContent = `Dashboard data could not be loaded: ${error.message}`;
    elements.rows.innerHTML = '<tr><td colspan="5">Unable to load recommendations.</td></tr>';
  }
}

[elements.search, elements.type, elements.risk, elements.confidence].forEach((control) => {
  control.addEventListener("input", renderRows);
});

elements.agentAskButton.addEventListener("click", askAgent);
elements.agentQuery.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
    askAgent();
  }
});
elements.agentPrompts.forEach((button) => {
  button.addEventListener("click", () => {
    elements.agentQuery.value = button.dataset.agentPrompt;
    elements.agentQuery.focus();
  });
});
elements.agentModeInputs.forEach((input) => {
  input.addEventListener("change", () => {
    elements.agentStatus.textContent = input.value === "llm" ? "Ready - LLM" : "Ready - Python";
  });
});
elements.reviewButtons.forEach((button) => {
  button.addEventListener("click", () => submitReview(button.dataset.reviewDecision));
});

loadDashboard();
