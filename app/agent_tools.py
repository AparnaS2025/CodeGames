from __future__ import annotations

from typing import Any

from app.service import CapacityIntelligenceService


class CapacityAgentTools:
    def __init__(self, service: CapacityIntelligenceService) -> None:
        self.service = service

    def run_ingestion(self, idempotency_key: str, window_days: int | None = None) -> dict[str, Any]:
        return self.service.run_ingestion(
            window_days=window_days,
            idempotency_key=idempotency_key,
        )

    def run_analysis(
        self,
        idempotency_key: str,
        resource_ids: list[str] | None = None,
        source_categories: list[str] | None = None,
        window_days: int | None = None,
    ) -> dict[str, Any]:
        return self.service.run_analysis(
            resource_ids=resource_ids,
            source_categories=source_categories,
            window_days=window_days,
            idempotency_key=idempotency_key,
        )

    def list_resources(self, active_only: bool = True) -> list[dict[str, Any]]:
        return self.service.list_resources(active_only=active_only)

    def get_latest_report(self) -> dict[str, Any] | None:
        return self.service.latest_report()

    def list_recommendations(
        self,
        recommendation_type: str | None = None,
        confidence: str | None = None,
        status: str | None = None,
        environment: str | None = None,
    ) -> list[dict[str, Any]]:
        return self.service.list_recommendations(
            recommendation_type=recommendation_type,
            confidence=confidence,
            status=status,
            environment=environment,
        )

    def get_recommendation(self, recommendation_id: str) -> dict[str, Any] | None:
        return self.service.get_recommendation_detail(recommendation_id)

    def list_runs(self, run_type: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        return self.service.list_capacity_runs(run_type=run_type, limit=limit)

    def get_run_status(self, run_id: str) -> dict[str, Any] | None:
        return self.service.get_capacity_run(run_id)
