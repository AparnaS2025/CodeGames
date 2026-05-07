from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
from typing import Any, Literal, Protocol, TypedDict
import warnings

try:
    from langchain_core._api.deprecation import LangChainPendingDeprecationWarning

    warnings.filterwarnings("ignore", category=LangChainPendingDeprecationWarning)
except Exception:
    pass

from langgraph.graph import END, StateGraph

from app.agent_model import build_agent_llm
from app.agent_prompts import CAPACITY_AGENT_SYSTEM_PROMPT
from app.agent_tools import CapacityAgentTools
from app.service import CapacityIntelligenceService


AgentIntent = Literal["execute_cycle", "run_status", "review"]
AnswerMode = Literal["llm", "deterministic"]


class CapacityAgentState(TypedDict, total=False):
    query: str
    run_label: str
    intent: AgentIntent
    answer: str
    use_llm: bool
    llm_used: bool
    tool_calls: list[dict[str, Any]]
    error: str | None
    scope: dict[str, Any]


class ChatModel(Protocol):
    def invoke(self, input: Any, **kwargs: Any) -> Any:
        ...


@dataclass
class CapacityAgent:
    service: CapacityIntelligenceService
    llm: ChatModel | None = None

    def __post_init__(self) -> None:
        self.tools = CapacityAgentTools(self.service)
        if self.llm is None:
            self.llm = build_agent_llm(self.service.settings)
        self.graph = self._build_graph()

    def ask(self, query: str, run_label: str | None = None, answer_mode: AnswerMode = "llm") -> dict[str, Any]:
        effective_label = run_label or _default_run_label(query)
        requested_mode = _normalize_answer_mode(answer_mode)
        final_state = self.graph.invoke(
            {
                "query": query,
                "run_label": effective_label,
                "use_llm": requested_mode == "llm",
                "llm_used": False,
                "tool_calls": [],
                "error": None,
            }
        )
        return {
            "answer": final_state.get("answer", ""),
            "intent": final_state.get("intent", "review"),
            "run_label": effective_label,
            "tool_calls": final_state.get("tool_calls", []),
            "source_of_truth": "capacity_langgraph_agent",
            "system_prompt_version": "capacity-agent-v1",
            "requested_answer_mode": requested_mode,
            "answer_mode": "llm" if final_state.get("llm_used", False) else "deterministic",
            "llm_enabled": bool(final_state.get("llm_used", False)),
            "llm_available": self.llm is not None,
        }

    def _build_graph(self):
        graph = StateGraph(CapacityAgentState)
        graph.add_node("route", self._route_node)
        graph.add_node("execute_cycle", self._execute_cycle_node)
        graph.add_node("run_status", self._run_status_node)
        graph.add_node("review", self._review_node)
        graph.set_entry_point("route")
        graph.add_conditional_edges(
            "route",
            lambda state: state["intent"],
            {
                "execute_cycle": "execute_cycle",
                "run_status": "run_status",
                "review": "review",
            },
        )
        graph.add_edge("execute_cycle", END)
        graph.add_edge("run_status", END)
        graph.add_edge("review", END)
        return graph.compile()

    def _route_node(self, state: CapacityAgentState) -> CapacityAgentState:
        query = state["query"].lower()
        if re.search(r"\b(ingestion|analysis)-[0-9a-f-]{32,36}\b", query):
            intent: AgentIntent = "run_status"
        elif any(token in query for token in ("run", "refresh", "trigger", "ingest", "pipeline", "capacity cycle")):
            intent = "execute_cycle"
        else:
            intent = "review"
        return {**state, "intent": intent}

    def _execute_cycle_node(self, state: CapacityAgentState) -> CapacityAgentState:
        tool_calls = list(state.get("tool_calls", []))
        scope = _parse_request_scope(state["query"], self.service.settings.analysis_window_days)
        stable_key = _stable_idempotency_key(scope)
        idempotency_key = _execution_idempotency_key(stable_key, scope, state["run_label"])

        ingestion = self.tools.run_ingestion(
            idempotency_key=f"ingestion|{idempotency_key}",
            window_days=scope["window_days"],
        )
        _record_tool_call(tool_calls, "run_ingestion", ingestion)

        resources = self.tools.list_resources(active_only=True)
        matched_resources = _match_resources(resources, scope.get("customer"))
        if scope.get("customer") and not matched_resources:
            _record_tool_call(tool_calls, "match_resources", {"customer": scope["customer"], "matched_resources": 0})
            deterministic_answer = (
                f"I could not find active resources matching `{scope['customer']}` after ingestion. "
                f"No analysis was run for this scoped request. Window requested: last {scope['window_days']} days. "
                "No capacity change was applied; this is advisory-only."
            )
            answer, llm_used = self._llm_summarize(
                state={**state, "scope": scope},
                intent="execute_cycle",
                deterministic_answer=deterministic_answer,
                facts={"scope": scope, "matched_resources": []},
            )
            return {**state, "scope": scope, "answer": answer, "llm_used": llm_used, "tool_calls": tool_calls}

        resource_ids = [resource["resource_id"] for resource in matched_resources] if scope.get("customer") else None
        _record_tool_call(
            tool_calls,
            "match_resources",
            {
                "customer": scope.get("customer") or "all",
                "matched_resources": len(matched_resources) if scope.get("customer") else len(resources),
            },
        )

        analysis = self.tools.run_analysis(
            idempotency_key=f"analysis|{idempotency_key}",
            resource_ids=resource_ids,
            window_days=scope["window_days"],
        )
        _record_tool_call(tool_calls, "run_analysis", analysis)

        if analysis.get("run_id"):
            analysis_status = self.tools.get_run_status(str(analysis["run_id"]))
            _record_tool_call(tool_calls, "get_run_status", analysis_status)

        report = self.tools.get_latest_report()
        _record_tool_call(tool_calls, "get_latest_report", report)

        report_recommendations = (report or {}).get("details_json", {}).get("recommendations", [])
        recommendations = _filter_recommendations_by_resources(report_recommendations, resource_ids)
        if not recommendations:
            recommendations = _filter_recommendations_by_resources(self.tools.list_recommendations(), resource_ids)
        _record_tool_call(
            tool_calls,
            "list_recommendations",
            {"count": len(recommendations), "scope": scope},
        )

        deterministic_answer = _summarize_capacity_cycle(ingestion, analysis, report, recommendations, scope=scope)
        answer, llm_used = self._llm_summarize(
            state={**state, "scope": scope},
            intent="execute_cycle",
            deterministic_answer=deterministic_answer,
            facts={
                "scope": scope,
                "ingestion": _result_summary(ingestion),
                "analysis": _result_summary(analysis),
                "report": _compact_report(report),
                "recommendation_summary": _recommendation_summary(recommendations),
            },
        )
        return {**state, "scope": scope, "answer": answer, "llm_used": llm_used, "tool_calls": tool_calls}

    def _run_status_node(self, state: CapacityAgentState) -> CapacityAgentState:
        tool_calls = list(state.get("tool_calls", []))
        run_id = _extract_run_id(state["query"])
        if not run_id:
            return {
                **state,
                "answer": "I could not find a run id in the request. Provide an ingestion-* or analysis-* run id.",
                "llm_used": False,
                "tool_calls": tool_calls,
            }
        run = self.tools.get_run_status(run_id)
        _record_tool_call(tool_calls, "get_run_status", run)
        if run is None:
            deterministic_answer = f"No persisted run was found for `{run_id}`."
        else:
            deterministic_answer = _summarize_run(run)
        answer, llm_used = self._llm_summarize(
            state=state,
            intent="run_status",
            deterministic_answer=deterministic_answer,
            facts={"run": _compact_run(run)},
        )
        return {**state, "answer": answer, "llm_used": llm_used, "tool_calls": tool_calls}

    def _review_node(self, state: CapacityAgentState) -> CapacityAgentState:
        tool_calls = list(state.get("tool_calls", []))
        recommendation_id = _extract_recommendation_id(state["query"])
        if recommendation_id:
            recommendation = self.tools.get_recommendation(recommendation_id)
            _record_tool_call(tool_calls, "get_recommendation", recommendation)
            deterministic_answer = _summarize_recommendation(recommendation, recommendation_id)
            answer, llm_used = self._llm_summarize(
                state=state,
                intent="review",
                deterministic_answer=deterministic_answer,
                facts={"recommendation": _compact_recommendation(recommendation)},
            )
            return {**state, "answer": answer, "llm_used": llm_used, "tool_calls": tool_calls}

        report = self.tools.get_latest_report()
        _record_tool_call(tool_calls, "get_latest_report", report)
        recommendations = self.tools.list_recommendations()
        _record_tool_call(tool_calls, "list_recommendations", {"count": len(recommendations)})
        deterministic_answer = _summarize_review(report, recommendations)
        answer, llm_used = self._llm_summarize(
            state=state,
            intent="review",
            deterministic_answer=deterministic_answer,
            facts={
                "report": _compact_report(report),
                "recommendation_summary": _recommendation_summary(recommendations),
            },
        )
        return {**state, "answer": answer, "llm_used": llm_used, "tool_calls": tool_calls}

    def _llm_summarize(
        self,
        state: CapacityAgentState,
        intent: AgentIntent,
        deterministic_answer: str,
        facts: dict[str, Any],
    ) -> tuple[str, bool]:
        if not state.get("use_llm", True) or self.llm is None:
            return deterministic_answer, False

        try:
            from langchain_core.messages import HumanMessage, SystemMessage

            prompt = {
                "user_query": state["query"],
                "intent": intent,
                "trusted_facts": facts,
                "deterministic_fallback_answer": deterministic_answer,
                "instructions": [
                    "Answer as a concise capacity engineering assistant.",
                    "Use only trusted_facts and deterministic_fallback_answer.",
                    "Do not invent metrics, timestamps, savings, recommendations, resources, or actions.",
                    "Do not claim capacity was changed, applied, deployed, approved, or resized.",
                    "Mention that the output is advisory-only.",
                ],
            }
            response = self.llm.invoke(
                [
                    SystemMessage(content=CAPACITY_AGENT_SYSTEM_PROMPT),
                    HumanMessage(content=json.dumps(prompt, sort_keys=True, default=str)),
                ]
            )
            content = getattr(response, "content", response)
            if isinstance(content, list):
                content = " ".join(str(part) for part in content)
            answer = str(content).strip()
            if not answer:
                return deterministic_answer, False
            return _ensure_advisory(answer), True
        except Exception:
            return deterministic_answer, False


def _record_tool_call(tool_calls: list[dict[str, Any]], name: str, result: Any) -> None:
    tool_calls.append(
        {
            "name": name,
            "result_summary": _result_summary(result),
        }
    )


def _result_summary(result: Any) -> dict[str, Any]:
    if result is None:
        return {"status": "not_found"}
    if isinstance(result, list):
        return {"count": len(result)}
    if isinstance(result, dict):
        summary_keys = (
            "run_id",
            "analysis_run_id",
            "run_status",
            "status",
            "idempotency_key",
            "report_id",
            "queued_resources",
            "idempotent_replay",
            "recommendation_id",
            "recommendation_type",
            "created_at_utc",
            "window_days",
            "matched_resources",
            "customer",
            "scope",
        )
        return {key: result[key] for key in summary_keys if key in result}
    return {"value": str(result)}


def _summarize_capacity_cycle(
    ingestion: dict[str, Any],
    analysis: dict[str, Any],
    report: dict[str, Any] | None,
    recommendations: list[dict[str, Any]],
    scope: dict[str, Any] | None = None,
) -> str:
    scope = scope or {}
    counts = Counter(item["recommendation_type"] for item in recommendations)
    total = len(recommendations)
    insufficient = counts.get("insufficient_data", 0)
    data_warning = ""
    if total and insufficient / total > 0.30:
        data_warning = f"Data quality warning: {insufficient} of {total} recommendations have insufficient data. "

    urgent = [
        item["resource_name"]
        for item in recommendations
        if item["recommendation_type"] == "scale_up" and item["confidence"] == "high"
    ]
    review_priority = [
        item["resource_name"]
        for item in recommendations
        if item["recommendation_type"] == "scale_up" and item["confidence"] != "high"
    ]
    scale_down = [
        item["resource_name"]
        for item in recommendations
        if item["recommendation_type"] == "scale_down"
    ][:3]

    report_id = report["report_id"] if report else "unavailable"
    report_time = report["created_at_utc"] if report else "unavailable"
    scope_text = (
        f" Scope: {scope.get('customer') or 'all resources'}, last {scope.get('window_days') or 'configured'} days."
    )
    if scope.get("force_refresh"):
        scope_text += " Cache bypass requested."
    parts = [
        data_warning
        + f"Capacity cycle completed. Ingestion run `{ingestion.get('run_id', 'unavailable')}` and analysis run "
        + f"`{analysis.get('run_id') or analysis.get('analysis_run_id', 'unavailable')}` produced report `{report_id}` "
        + f"at {report_time}.{scope_text}",
        f"Recommendation counts: scale_up={counts.get('scale_up', 0)}, scale_down={counts.get('scale_down', 0)}, "
        + f"watchlist={counts.get('watchlist', 0)}, hold={counts.get('hold', 0)}, "
        + f"insufficient_data={insufficient}.",
    ]
    insufficient_reason_summary = _insufficient_reason_summary(recommendations)
    if insufficient_reason_summary:
        parts.append(f"Insufficient-data reasons: {insufficient_reason_summary}.")
    if urgent:
        parts.append(f"URGENT scale-up resources: {', '.join(urgent)}.")
    if review_priority:
        parts.append(f"Review-priority scale-up resources: {', '.join(review_priority)}.")
    if scale_down:
        parts.append(f"Top scale-down candidates: {', '.join(scale_down)}.")
    parts.append("No capacity change was applied; this is advisory-only.")
    return " ".join(parts)


def _summarize_run(run: dict[str, Any]) -> str:
    result = run.get("result_json") or {}
    return (
        f"Run `{run['run_id']}` is `{run['status']}`. "
        f"Type: {run['run_type']}. Started: {run['started_at_utc']}. "
        f"Completed: {run.get('completed_at_utc') or 'not completed'}. "
        f"Report: {result.get('report_id', 'n/a')}. "
        "No capacity change was applied."
    )


def _summarize_recommendation(recommendation: dict[str, Any] | None, recommendation_id: str) -> str:
    if recommendation is None:
        return f"No persisted recommendation was found for `{recommendation_id}`."
    evidence = "; ".join(recommendation.get("evidence_json", [])[:3])
    guardrails = "; ".join(recommendation.get("guardrails_json", [])[:2])
    savings = recommendation.get("estimated_monthly_savings")
    savings_text = f" Estimated monthly savings: ${savings:.2f}." if savings is not None else ""
    return (
        f"Recommendation `{recommendation_id}` for {recommendation['resource_name']} is "
        f"`{recommendation['recommendation_type']}` with {recommendation['confidence']} confidence and "
        f"{recommendation['risk_level']} risk. Current size is `{recommendation['current_size']}`; "
        f"suggested size is `{recommendation.get('suggested_size') or 'none'}`.{savings_text} "
        f"Evidence: {evidence}. Guardrails: {guardrails}. "
        f"Created at {recommendation['created_at_utc']}. No capacity change was applied."
    )


def _summarize_review(report: dict[str, Any] | None, recommendations: list[dict[str, Any]]) -> str:
    if report is None:
        return "No report is available yet. Run ingestion and analysis before asking for recommendation review."
    counts = Counter(item["recommendation_type"] for item in recommendations)
    return (
        f"Latest report `{report['report_id']}` was created at {report['created_at_utc']}. "
        f"{report['summary_text']} Recommendation counts: "
        f"scale_up={counts.get('scale_up', 0)}, scale_down={counts.get('scale_down', 0)}, "
        f"watchlist={counts.get('watchlist', 0)}, hold={counts.get('hold', 0)}, "
        f"insufficient_data={counts.get('insufficient_data', 0)}. "
        "No capacity change was applied."
    )


def _ensure_advisory(answer: str) -> str:
    lowered = answer.lower()
    if "advisory" in lowered or "no capacity change was applied" in lowered:
        return answer
    return f"{answer} No capacity change was applied; this is advisory-only."


def _normalize_answer_mode(answer_mode: str) -> AnswerMode:
    if answer_mode == "deterministic":
        return "deterministic"
    return "llm"


def _compact_report(report: dict[str, Any] | None) -> dict[str, Any] | None:
    if report is None:
        return None
    return {
        "report_id": report.get("report_id"),
        "created_at_utc": report.get("created_at_utc"),
        "summary_text": report.get("summary_text"),
        "scope_json": report.get("scope_json"),
        "details_json": report.get("details_json"),
    }


def _compact_run(run: dict[str, Any] | None) -> dict[str, Any] | None:
    if run is None:
        return None
    return {
        "run_id": run.get("run_id"),
        "run_type": run.get("run_type"),
        "status": run.get("status"),
        "idempotency_key": run.get("idempotency_key"),
        "started_at_utc": run.get("started_at_utc"),
        "completed_at_utc": run.get("completed_at_utc"),
        "result_json": run.get("result_json"),
        "error_json": run.get("error_json"),
    }


def _compact_recommendation(recommendation: dict[str, Any] | None) -> dict[str, Any] | None:
    if recommendation is None:
        return None
    keys = (
        "recommendation_id",
        "resource_id",
        "resource_name",
        "recommendation_type",
        "current_size",
        "suggested_size",
        "confidence",
        "risk_level",
        "estimated_monthly_savings",
        "evidence_json",
        "guardrails_json",
        "pattern_summary",
        "report_summary",
        "status",
        "created_at_utc",
        "review_history",
    )
    return {key: recommendation.get(key) for key in keys if key in recommendation}


def _recommendation_summary(recommendations: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(item["recommendation_type"] for item in recommendations)
    return {
        "total": len(recommendations),
        "counts_by_type": dict(counts),
        "scale_up": [_compact_recommendation(item) for item in recommendations if item["recommendation_type"] == "scale_up"][:5],
        "scale_down": [_compact_recommendation(item) for item in recommendations if item["recommendation_type"] == "scale_down"][:5],
        "watchlist": [_compact_recommendation(item) for item in recommendations if item["recommendation_type"] == "watchlist"][:5],
        "insufficient_data_count": counts.get("insufficient_data", 0),
        "insufficient_data_reasons": dict(_count_insufficient_reasons(recommendations)),
    }


def _parse_request_scope(query: str, default_window_days: int) -> dict[str, Any]:
    window_days = _extract_window_days(query) or default_window_days
    customer = _extract_customer_or_resource(query)
    return {
        "customer": customer,
        "window_days": window_days,
        "analysis_version": "v1",
        "force_refresh": _is_force_refresh_request(query),
    }


def _extract_window_days(query: str) -> int | None:
    match = re.search(r"\b(?:last|past)\s+(\d{1,2})\s+days?\b", query, flags=re.IGNORECASE)
    if not match:
        return None
    return max(1, min(int(match.group(1)), 90))


def _extract_customer_or_resource(query: str) -> str | None:
    patterns = (
        r"\bfor\s+(?!(?:the\s+)?(?:last|past)\s+\d{1,2}\s+days?\b)(.+?)(?:\s+for\s+(?:the\s+)?(?:last|past)\b|\s+(?:last|past)\s+\d{1,2}\s+days?\b|\s+and\b|$)",
        r"\b(?:force refresh|force rerun|fresh run|live refresh)\s+(?!the\s+cycle\b)(.+?)(?:\s+(?:for\s+)?(?:last|past)\s+\d{1,2}\s+days?\b|\s+with\s+no\s+cache\b|$)",
        r"\b(?:customer|client|tenant|resource)\s+([A-Za-z0-9][A-Za-z0-9 _.-]{1,80})",
    )
    for pattern in patterns:
        match = re.search(pattern, query, flags=re.IGNORECASE)
        if match:
            candidate = _clean_scope_term(match.group(1))
            if candidate:
                return candidate
    return None


def _clean_scope_term(value: str) -> str | None:
    cleaned = re.sub(
        r"\b(the|a|an|all|full|capacity|cycle|resources?|recommendations?|analysis|pipeline)\b",
        " ",
        value,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"[^A-Za-z0-9_. -]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -_.")
    return cleaned or None


def _stable_idempotency_key(scope: dict[str, Any]) -> str:
    customer = _slug(scope.get("customer") or "all")
    window_days = int(scope.get("window_days") or 60)
    version = _slug(scope.get("analysis_version") or "v1")
    return f"customer={customer}|window={window_days}d|analysis_version={version}"


def _execution_idempotency_key(stable_key: str, scope: dict[str, Any], run_label: str) -> str:
    if not scope.get("force_refresh"):
        return stable_key
    return f"{stable_key}|refresh={_slug(run_label)}"


def _is_force_refresh_request(query: str) -> bool:
    return bool(
        re.search(
            r"\b(force refresh|force rerun|fresh run|no cache|bypass cache|ignore cache|do not use cache|live refresh)\b",
            query,
            flags=re.IGNORECASE,
        )
    )


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return normalized or "all"


def _match_resources(resources: list[dict[str, Any]], customer: str | None) -> list[dict[str, Any]]:
    if not customer:
        return resources
    needle = customer.lower()
    return [resource for resource in resources if needle in _resource_search_text(resource)]


def _resource_search_text(resource: dict[str, Any]) -> str:
    metadata = resource.get("metadata_json") or {}
    metadata_text = " ".join(str(value) for value in metadata.values())
    return " ".join(
        [
            str(resource.get("resource_id", "")),
            str(resource.get("resource_name", "")),
            str(resource.get("name", "")),
            str(resource.get("resource_type", "")),
            str(resource.get("environment", "")),
            metadata_text,
        ]
    ).lower()


def _filter_recommendations_by_resources(
    recommendations: list[dict[str, Any]],
    resource_ids: list[str] | None,
) -> list[dict[str, Any]]:
    if not resource_ids:
        return recommendations
    allowed = set(resource_ids)
    return [item for item in recommendations if item.get("resource_id") in allowed]


def _count_insufficient_reasons(recommendations: list[dict[str, Any]]) -> Counter:
    reasons: Counter = Counter()
    for item in recommendations:
        if item.get("recommendation_type") != "insufficient_data":
            continue
        for evidence in item.get("evidence_json", []):
            if evidence.lower().startswith("insufficient data reason"):
                reason_text = evidence.split(":", 1)[-1].strip(" .")
                for reason in reason_text.split(","):
                    cleaned = reason.strip()
                    if cleaned:
                        reasons[cleaned] += 1
    return reasons


def _insufficient_reason_summary(recommendations: list[dict[str, Any]]) -> str:
    counts = _count_insufficient_reasons(recommendations)
    return ", ".join(f"{reason}={count}" for reason, count in counts.most_common())


def _extract_recommendation_id(query: str) -> str | None:
    match = re.search(r"\brec-[0-9a-f-]{32,36}\b", query, flags=re.IGNORECASE)
    return match.group(0) if match else None


def _extract_run_id(query: str) -> str | None:
    match = re.search(r"\b(?:analysis|ingestion)-[0-9a-f-]{32,36}\b", query, flags=re.IGNORECASE)
    return match.group(0) if match else None


def _default_run_label(query: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-")
    normalized = normalized[:40].strip("-") or "capacity-agent-run"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
    return f"{normalized}-{timestamp}"


__all__ = ["CAPACITY_AGENT_SYSTEM_PROMPT", "CapacityAgent"]
