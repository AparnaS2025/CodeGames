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
from app.models import AnalysisSnapshot, RawMetricPayload, ReportSnapshot, Resource, ReviewDecision
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
        self.connectors = connectors or [
            DatadogConnector(),
            CloudWatchConnector(),
            SumoLogicConnector(self.settings),
        ]
        self._ingestion_lock = Lock()
        self._analysis_lock = Lock()

    def run_ingestion(self, window_days: int | None = None, now: datetime | None = None) -> dict[str, Any]:
        if not self._ingestion_lock.acquire(blocking=False):
            return {"status": "busy", "message": "An ingestion run is already in progress."}
        try:
            effective_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
            window_start, window_end = default_window(effective_now, window_days or self.settings.analysis_window_days)
            resources = sample_resources()
            self.repository.upsert_resources(resources)
            resource_lookup = {resource.resource_id: resource for resource in resources}
            run_id = f"ingestion-{uuid4()}"
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

            return {
                "run_id": run_id,
                "window_start_utc": window_start.isoformat(),
                "window_end_utc": window_end.isoformat(),
                "source_run_status": source_results,
            }
        finally:
            self._ingestion_lock.release()

    def run_analysis(
        self,
        resource_ids: list[str] | None = None,
        source_categories: list[str] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        if not self._analysis_lock.acquire(blocking=False):
            return {"status": "busy", "message": "An analysis run is already in progress."}
        try:
            effective_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(second=0, microsecond=0)
            window_start, window_end = default_window(effective_now, self.settings.analysis_window_days)
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

            run_id = f"analysis-{uuid4()}"
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
                    "source_categories": source_categories or [],
                },
                summary_text=report_summary,
                details={
                    **report_details,
                    "recommendations": created_recommendations,
                },
            )
            self.repository.save_report(report)

            return {
                "analysis_run_id": run_id,
                "queued_resources": len(resources),
                "report_id": report.report_id,
            }
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
        return {
            "review_id": persisted.review_id,
            "recommendation_id": persisted.recommendation_id,
            "decision": persisted.decision,
            "reviewer": persisted.reviewer,
            "comment": persisted.comment,
            "created_at_utc": persisted.created_at_utc.isoformat(),
        }

    def latest_report(self) -> dict[str, Any] | None:
        return self.repository.get_latest_report()

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
