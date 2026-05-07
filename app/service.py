from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from threading import Lock
from typing import Any
from uuid import uuid4

from app.analysis import build_analysis_snapshot, build_portfolio_report, generate_recommendation
from app.config import Settings
from app.connectors import CloudWatchConnector, DatadogConnector, SourceConnector, SumoLogicConnector
from app.models import AnalysisSnapshot, CapacityActionItem, RawMetricPayload, ReportSnapshot, Resource, ReviewDecision
from app.normalization import build_resource_resolution_index, normalize_payloads, resolve_resource_id
from app.sample_data import default_window, sample_resources
from app.storage import Repository


@dataclass(slots=True)
class SourceRunResult:
    source: str
    status: str
    payload_count: int
    normalized_count: int
    issue_count: int


class CapacityIntelligenceService:
    def __init__(
        self,
        repository: Repository,
        settings: Settings | None = None,
        connectors: list[SourceConnector] | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self.settings.ensure_directories()
        self.repository = repository
        self.connectors = connectors or self._build_default_connectors()
        self._ingestion_lock = Lock()
        self._analysis_lock = Lock()

    def run_ingestion(
        self,
        window_days: int | None = None,
        now: datetime | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        if idempotency_key:
            existing_run = self.repository.get_capacity_run_by_idempotency_key("ingestion", idempotency_key)
            if existing_run:
                return self._capacity_run_response(existing_run, idempotent_replay=True)

        if not self._ingestion_lock.acquire(blocking=False):
            return {"status": "busy", "message": "An ingestion run is already in progress."}
        run_id = f"ingestion-{uuid4()}"
        request = {"window_days": window_days}
        run_started = False
        try:
            started_run = self._start_capacity_run(run_id, "ingestion", idempotency_key, request)
            if started_run["run_id"] != run_id:
                return self._capacity_run_response(started_run, idempotent_replay=True)
            run_started = True
            effective_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
            effective_window_days = window_days or self.settings.analysis_window_days
            window_start, window_end = default_window(effective_now, effective_window_days)
            resources = sample_resources() if self._should_seed_sample_resources() else []
            self.repository.upsert_resources(resources)
            resource_lookup = {resource.resource_id: resource for resource in resources}
            source_results: list[dict[str, Any]] = []
            resource_index = build_resource_resolution_index(resource_lookup)

            for connector in self.connectors:
                try:
                    fetch_result = connector.fetch(window_start, window_end)
                    inferred_resources = self._infer_resources_from_payloads(fetch_result.payloads, resource_lookup, resource_index)
                    if inferred_resources:
                        self.repository.upsert_resources(inferred_resources)
                        for resource in inferred_resources:
                            resource_lookup[resource.resource_id] = resource
                        resource_index = build_resource_resolution_index(resource_lookup)
                    normalized, normalization_issues = normalize_payloads(
                        fetch_result.payloads,
                        resource_lookup,
                        resource_index,
                    )
                    issues = [*fetch_result.issues, *normalization_issues]
                    written = self.repository.upsert_metrics([point.to_record() for point in normalized])
                    self.repository.record_ingestion_issues(issues)
                    health = {
                        **fetch_result.health,
                        "normalized_count": written,
                        "issue_count": len(issues),
                    }
                    self.repository.save_source_cursor(
                        connector.source_name,
                        window_start.isoformat(),
                        window_end.isoformat(),
                        effective_now.isoformat(),
                        health,
                    )
                    source_results.append(
                        asdict(
                            SourceRunResult(
                                source=connector.source_name,
                                status=health["status"],
                                payload_count=len(fetch_result.payloads),
                                normalized_count=written,
                                issue_count=len(issues),
                            )
                        )
                    )
                except Exception as exc:
                    source_results.append(
                        asdict(
                            SourceRunResult(
                                source=connector.source_name,
                                status="failed",
                                payload_count=0,
                                normalized_count=0,
                                issue_count=1,
                            )
                        )
                        | {"error": str(exc)}
                    )

            result = {
                "run_id": run_id,
                "run_status": "completed",
                "idempotency_key": idempotency_key,
                "window_start_utc": window_start.isoformat(),
                "window_end_utc": window_end.isoformat(),
                "window_days": effective_window_days,
                "source_run_status": source_results,
            }
            self.repository.complete_capacity_run(run_id, datetime.now(timezone.utc).isoformat(), result)
            return result
        except Exception as exc:
            if run_started:
                self.repository.fail_capacity_run(
                    run_id,
                    datetime.now(timezone.utc).isoformat(),
                    {
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                    },
                )
            raise
        finally:
            self._ingestion_lock.release()

    def _build_default_connectors(self) -> list[SourceConnector]:
        connectors: list[SourceConnector] = []
        if self.settings.datadog_enabled:
            connectors.append(DatadogConnector())
        if self.settings.cloudwatch_enabled:
            connectors.append(CloudWatchConnector())
        connectors.append(SumoLogicConnector(self.settings))
        return connectors

    def _should_seed_sample_resources(self) -> bool:
        return self.settings.datadog_enabled or self.settings.cloudwatch_enabled or self.settings.sumologic_use_sample_data

    def run_analysis(
        self,
        resource_ids: list[str] | None = None,
        source_categories: list[str] | None = None,
        window_days: int | None = None,
        now: datetime | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        if idempotency_key:
            existing_run = self.repository.get_capacity_run_by_idempotency_key("analysis", idempotency_key)
            if existing_run:
                return self._capacity_run_response(existing_run, idempotent_replay=True)

        if not self._analysis_lock.acquire(blocking=False):
            return {"status": "busy", "message": "An analysis run is already in progress."}
        run_id = f"analysis-{uuid4()}"
        request = {
            "resource_ids": resource_ids,
            "source_categories": source_categories,
            "window_days": window_days,
        }
        run_started = False
        try:
            started_run = self._start_capacity_run(run_id, "analysis", idempotency_key, request)
            if started_run["run_id"] != run_id:
                return self._capacity_run_response(started_run, idempotent_replay=True)
            run_started = True
            effective_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(second=0, microsecond=0)
            effective_window_days = window_days or self.settings.analysis_window_days
            window_start, window_end = default_window(effective_now, effective_window_days)
            resources = self.repository.list_resources(active_only=True)
            if resource_ids:
                resources = [resource for resource in resources if resource["resource_id"] in resource_ids]
            if source_categories:
                resource_ids_for_categories = self.repository.find_resource_ids_by_dimension(
                    source="sumologic",
                    dimension_key="source_category",
                    dimension_values=source_categories,
                    window_start=window_start.isoformat(),
                    window_end=window_end.isoformat(),
                )
                resources = [resource for resource in resources if resource["resource_id"] in resource_ids_for_categories]

            created_recommendations: list[dict[str, Any]] = []
            resource_ids_in_scope = [resource["resource_id"] for resource in resources]
            metrics_by_resource = self.repository.get_metrics_for_resources(
                resource_ids_in_scope,
                window_start.isoformat(),
                window_end.isoformat(),
            )

            for resource_dict in resources:
                resource = Resource(
                    resource_id=resource_dict["resource_id"],
                    name=resource_dict["name"],
                    resource_type=resource_dict["resource_type"],
                    environment=resource_dict["environment"],
                    current_size=resource_dict["current_size"],
                    metadata=resource_dict["metadata_json"],
                    active=resource_dict["active"],
                )
                metrics = metrics_by_resource.get(resource.resource_id, [])
                features, tags, freshness = build_analysis_snapshot(resource, metrics, window_start, window_end, self.settings)
                snapshot = AnalysisSnapshot(
                    snapshot_id=f"snapshot-{uuid4()}",
                    resource_id=resource.resource_id,
                    window_start_utc=window_start,
                    window_end_utc=window_end,
                    source_freshness=freshness,
                    computed_features=features,
                    pattern_candidates=tags,
                )
                self.repository.save_analysis_snapshot(snapshot)

                recommendation = generate_recommendation(resource, features, tags, self.settings)
                recommendation.recommendation_id = f"rec-{uuid4()}"
                recommendation.created_at_utc = effective_now
                self.repository.save_recommendation(recommendation)
                stored = self.repository.get_recommendation(recommendation.recommendation_id)
                if stored:
                    created_recommendations.append(stored)

            report_summary, report_details = build_portfolio_report(created_recommendations, effective_now)
            report = ReportSnapshot(
                report_id=f"report-{uuid4()}",
                report_type="weekly_portfolio",
                created_at_utc=effective_now,
                scope={
                    "environment": sorted({resource["environment"] for resource in resources}),
                    "resource_count": len(resources),
                    "resource_ids": resource_ids or [],
                    "source_categories": source_categories or [],
                    "window_days": effective_window_days,
                },
                summary_text=report_summary,
                details={
                    **report_details,
                    "recommendations": created_recommendations,
                },
            )
            self.repository.save_report(report)

            result = {
                "analysis_run_id": run_id,
                "run_id": run_id,
                "run_status": "completed",
                "idempotency_key": idempotency_key,
                "window_start_utc": window_start.isoformat(),
                "window_end_utc": window_end.isoformat(),
                "window_days": effective_window_days,
                "queued_resources": len(resources),
                "report_id": report.report_id,
            }
            self.repository.complete_capacity_run(run_id, datetime.now(timezone.utc).isoformat(), result)
            return result
        except Exception as exc:
            if run_started:
                self.repository.fail_capacity_run(
                    run_id,
                    datetime.now(timezone.utc).isoformat(),
                    {
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                    },
                )
            raise
        finally:
            self._analysis_lock.release()

    def list_resources(
        self,
        environment: str | None = None,
        resource_type: str | None = None,
        active_only: bool = False,
    ) -> list[dict[str, Any]]:
        return self.repository.list_resources(environment, resource_type, active_only)

    def get_resource_detail(self, resource_id: str) -> dict[str, Any] | None:
        resource = self.repository.get_resource(resource_id)
        if not resource:
            return None
        latest_recommendation = self.repository.get_latest_recommendation_for_resource(resource_id)
        review_history: list[dict[str, Any]] = []
        if latest_recommendation:
            review_history = self.repository.list_reviews_for_recommendation(latest_recommendation["recommendation_id"])
        return {
            **resource,
            "latest_recommendation_summary": latest_recommendation,
            "review_history": review_history,
        }

    def list_recommendations(
        self,
        recommendation_type: str | None = None,
        confidence: str | None = None,
        status: str | None = None,
        environment: str | None = None,
    ) -> list[dict[str, Any]]:
        return self.repository.list_recommendations(recommendation_type, confidence, status, environment)

    def get_recommendation_detail(self, recommendation_id: str) -> dict[str, Any] | None:
        recommendation = self.repository.get_recommendation(recommendation_id)
        if not recommendation:
            return None
        return {
            **recommendation,
            "review_history": self.repository.list_reviews_for_recommendation(recommendation_id),
            "approval_pack": self.prepare_approval_pack(recommendation_id),
            "action_item": self.repository.get_capacity_action_for_recommendation(recommendation_id),
        }

    def prepare_approval_pack(self, recommendation_id: str) -> dict[str, Any] | None:
        recommendation = self.repository.get_recommendation(recommendation_id)
        if not recommendation:
            return None
        policy = self._approval_policy_for(recommendation)
        action_type = self._action_type_for(recommendation)
        return {
            "recommendation_id": recommendation_id,
            "resource_name": recommendation["resource_name"],
            "recommendation_type": recommendation["recommendation_type"],
            "approval_required": policy["approval_required"],
            "approval_policy": policy,
            "approval_summary": self._approval_summary_for(recommendation, policy),
            "evidence": recommendation["evidence_json"][:5],
            "guardrails": recommendation["guardrails_json"][:5],
            "action_preview": {
                "action_type": action_type,
                "current_size": recommendation["current_size"],
                "suggested_size": recommendation["suggested_size"],
                "status_after_approval": "pending" if action_type else "no_action",
                "automatic_capacity_change": False,
            },
        }

    def review_recommendation(
        self,
        recommendation_id: str,
        decision: str,
        reviewer: str,
        comment: str | None,
    ) -> dict[str, Any] | None:
        recommendation = self.repository.get_recommendation(recommendation_id)
        if not recommendation:
            return None

        review = ReviewDecision(
            review_id=f"review-{uuid4()}",
            recommendation_id=recommendation_id,
            decision=decision,
            reviewer=reviewer,
            comment=comment,
            created_at_utc=datetime.now(timezone.utc),
        )
        persisted = self.repository.save_review_decision(review)
        action_item = None
        if decision == "approve":
            action_item = self._create_action_item_for_approval(recommendation, reviewer)
        return {
            "review_id": persisted.review_id,
            "recommendation_id": persisted.recommendation_id,
            "decision": persisted.decision,
            "reviewer": persisted.reviewer,
            "comment": persisted.comment,
            "created_at_utc": persisted.created_at_utc.isoformat(),
            "action_item": action_item,
        }

    def list_capacity_action_items(self, status: str | None = None) -> list[dict[str, Any]]:
        return self.repository.list_capacity_action_items(status=status)

    def latest_report(self) -> dict[str, Any] | None:
        return self.repository.get_latest_report()

    def get_capacity_run(self, run_id: str) -> dict[str, Any] | None:
        return self.repository.get_capacity_run(run_id)

    def list_capacity_runs(self, run_type: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        return self.repository.list_capacity_runs(run_type=run_type, limit=limit)

    def answer_review_question(self, recommendation_id: str, question: str) -> dict[str, Any] | None:
        recommendation = self.repository.get_recommendation(recommendation_id)
        if not recommendation:
            return None
        lowered = question.lower()
        if "risk" in lowered or "guardrail" in lowered:
            answer = " ".join(recommendation["guardrails_json"])
        elif "saving" in lowered or "cost" in lowered:
            amount = recommendation["estimated_monthly_savings"]
            answer = (
                f"Estimated monthly savings are ${amount:.2f}."
                if amount is not None
                else "Cost estimate unavailable for this recommendation."
            )
        else:
            answer = " ".join(
                [
                    recommendation["pattern_summary"],
                    "Evidence:",
                    "; ".join(recommendation["evidence_json"]),
                ]
            )
        return {
            "recommendation_id": recommendation_id,
            "question": question,
            "answer": answer,
            "source_of_truth": "stored_recommendation_state",
        }

    def _create_action_item_for_approval(self, recommendation: dict[str, Any], reviewer: str) -> dict[str, Any] | None:
        action_type = self._action_type_for(recommendation)
        if action_type is None:
            return None
        item = CapacityActionItem(
            action_id=f"action-{uuid4()}",
            recommendation_id=recommendation["recommendation_id"],
            action_type=action_type,
            status="pending",
            created_by=reviewer,
            created_at_utc=datetime.now(timezone.utc),
            payload={
                "resource_id": recommendation["resource_id"],
                "resource_name": recommendation["resource_name"],
                "resource_type": recommendation["resource_type"],
                "environment": recommendation["environment"],
                "recommendation_type": recommendation["recommendation_type"],
                "current_size": recommendation["current_size"],
                "suggested_size": recommendation["suggested_size"],
                "risk_level": recommendation["risk_level"],
                "confidence": recommendation["confidence"],
                "automatic_capacity_change": False,
            },
        )
        persisted = self.repository.save_capacity_action_item(item)
        return {
            "action_id": persisted.action_id,
            "recommendation_id": persisted.recommendation_id,
            "action_type": persisted.action_type,
            "status": persisted.status,
            "created_by": persisted.created_by,
            "created_at_utc": persisted.created_at_utc.isoformat(),
            "payload_json": persisted.payload,
        }

    @staticmethod
    def _action_type_for(recommendation: dict[str, Any]) -> str | None:
        recommendation_type = recommendation["recommendation_type"]
        if recommendation_type == "scale_up":
            return "capacity_scale_up"
        if recommendation_type == "scale_down":
            return "capacity_scale_down"
        if recommendation_type == "insufficient_data":
            return "telemetry_investigation"
        return None

    @staticmethod
    def _approval_policy_for(recommendation: dict[str, Any]) -> dict[str, Any]:
        recommendation_type = recommendation["recommendation_type"]
        risk = recommendation["risk_level"]
        if recommendation_type in {"scale_up", "scale_down"} and risk in {"high", "medium"}:
            approvers = ["application_owner", "platform_owner"]
        elif recommendation_type in {"scale_up", "scale_down"}:
            approvers = ["platform_owner"]
        elif recommendation_type == "insufficient_data":
            approvers = ["observability_owner"]
        else:
            approvers = ["platform_reviewer"]
        return {
            "approval_required": recommendation_type in {"scale_up", "scale_down", "insufficient_data"},
            "required_approvers": approvers,
            "risk_level": risk,
            "reason": "Capacity-changing and telemetry-remediation recommendations require an explicit recorded decision.",
        }

    @staticmethod
    def _approval_summary_for(recommendation: dict[str, Any], policy: dict[str, Any]) -> str:
        savings = recommendation.get("estimated_monthly_savings")
        savings_text = f" Estimated monthly savings: ${savings:.2f}." if savings is not None else ""
        return (
            f"{recommendation['resource_name']} has a {recommendation['recommendation_type']} recommendation "
            f"with {recommendation['confidence']} confidence and {recommendation['risk_level']} risk. "
            f"Current size is {recommendation['current_size']}; suggested size is "
            f"{recommendation.get('suggested_size') or 'not applicable'}.{savings_text} "
            f"Required approvers: {', '.join(policy['required_approvers'])}. "
            "Approval creates a pending action item only; no capacity change is applied automatically."
        )

    def source_health(self) -> list[dict[str, Any]]:
        issues = self.repository.list_ingestion_issues()
        grouped: dict[str, list[dict[str, Any]]] = {}
        for issue in issues:
            grouped.setdefault(issue["source"], []).append(issue)
        return [
            {
                "source": source,
                "issues": entries,
            }
            for source, entries in grouped.items()
        ]

    def export_state(self) -> str:
        return json.dumps(
            {
                "resources": self.list_resources(),
                "recommendations": self.list_recommendations(),
                "report": self.latest_report(),
            },
            indent=2,
            sort_keys=True,
        )

    def _start_capacity_run(
        self,
        run_id: str,
        run_type: str,
        idempotency_key: str | None,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            return self.repository.start_capacity_run(
                run_id=run_id,
                run_type=run_type,
                idempotency_key=idempotency_key,
                started_at_utc=datetime.now(timezone.utc).isoformat(),
                request=request,
            )
        except Exception:
            if idempotency_key:
                existing_run = self.repository.get_capacity_run_by_idempotency_key(run_type, idempotency_key)
                if existing_run:
                    return existing_run
            raise

    @staticmethod
    def _capacity_run_response(run: dict[str, Any], idempotent_replay: bool = False) -> dict[str, Any]:
        if run["status"] == "completed" and run["result_json"]:
            return {
                **run["result_json"],
                "run_status": run["status"],
                "idempotent_replay": idempotent_replay,
            }
        payload: dict[str, Any] = {
            "run_id": run["run_id"],
            "run_status": run["status"],
            "run_type": run["run_type"],
            "idempotency_key": run["idempotency_key"],
            "idempotent_replay": idempotent_replay,
        }
        if run["error_json"]:
            payload["error"] = run["error_json"]
        return payload

    def _infer_resources_from_payloads(
        self,
        payloads: list[RawMetricPayload],
        existing_resources: dict[str, Resource],
        resource_index: dict[str, str],
    ) -> list[Resource]:
        inferred: dict[str, Resource] = {}
        for payload in payloads:
            if resolve_resource_id(payload, resource_index) is not None:
                continue
            resource_type = self._infer_resource_type(payload)
            environment = self._infer_environment(payload)
            resource_id = str(payload.external_resource_id)
            inferred.setdefault(
                resource_id,
                Resource(
                    resource_id=resource_id,
                    name=resource_id,
                    resource_type=resource_type,
                    environment=environment,
                    current_size=self.settings.min_size_by_type[resource_type],
                    metadata={
                        "source_aliases": {payload.source: resource_id},
                        "discovered_from": payload.source,
                        "sample_inferred": True,
                    },
                ),
            )
        return list(inferred.values())

    @staticmethod
    def _infer_resource_type(payload: RawMetricPayload) -> str:
        db_markers = (
            "sumo.rds.",
            "db.",
            "DatabaseConnections",
            "ReadLatency",
            "WriteLatency",
            "ReadIOPS",
            "WriteIOPS",
            "FreeStorage",
            "FreeableMemory",
        )
        if payload.metric_name.startswith(db_markers) or payload.dimensions.get("Namespace") == "AWS/RDS":
            return "db_instance"
        return "app_service"

    @staticmethod
    def _infer_environment(payload: RawMetricPayload) -> str:
        for key in ("deployment.environment", "wk_environment_type", "wk_environment_name", "env"):
            value = payload.dimensions.get(key)
            if value:
                return str(value)
        source_category = payload.dimensions.get("source_category") or payload.dimensions.get("_sourceCategory")
        if isinstance(source_category, str) and source_category:
            return source_category.split("/", 1)[0]
        return "unknown"
