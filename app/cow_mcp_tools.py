from __future__ import annotations

from typing import Any

from app.cow_api_client import CapacityApiClient


class CapacityMcpTools:
    def __init__(self, api_client: CapacityApiClient) -> None:
        self.api_client = api_client

    async def run_ingestion(
        self,
        window_days: int | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Trigger metric ingestion for the configured observability sources."""
        payload = {
            "window_days": window_days,
            "idempotency_key": idempotency_key,
        }
        return await self.api_client.post("/api/ingestion/run", self.api_client._clean(payload))

    async def run_analysis(
        self,
        resource_ids: list[str] | None = None,
        source_categories: list[str] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Run deterministic capacity analysis for all active resources or a requested subset."""
        payload = {
            "resource_ids": resource_ids,
            "source_categories": source_categories,
            "idempotency_key": idempotency_key,
        }
        return await self.api_client.post("/api/analysis/run", self.api_client._clean(payload))

    async def list_runs(self, run_type: str | None = None, limit: int = 20) -> dict[str, Any]:
        """List recent ingestion and analysis run records."""
        return await self.api_client.get(
            "/api/runs",
            {
                "run_type": run_type,
                "limit": limit,
            },
        )

    async def get_run_status(self, run_id: str) -> dict[str, Any]:
        """Get a durable ingestion or analysis run record by id."""
        return await self.api_client.get(f"/api/runs/{run_id}")

    async def list_resources(
        self,
        environment: str | None = None,
        resource_type: str | None = None,
        active_only: bool = False,
    ) -> dict[str, Any]:
        """List known resources with optional environment, type, and active-only filters."""
        return await self.api_client.get(
            "/api/resources",
            {
                "environment": environment,
                "resource_type": resource_type,
                "active_only": active_only,
            },
        )

    async def get_resource(self, resource_id: str) -> dict[str, Any]:
        """Get resource detail and the latest recommendation summary."""
        return await self.api_client.get(f"/api/resources/{resource_id}")

    async def list_recommendations(
        self,
        recommendation_type: str | None = None,
        confidence: str | None = None,
        status: str | None = None,
        environment: str | None = None,
    ) -> dict[str, Any]:
        """List persisted recommendations with optional recommendation filters."""
        return await self.api_client.get(
            "/api/recommendations",
            {
                "recommendation_type": recommendation_type,
                "confidence": confidence,
                "status": status,
                "environment": environment,
            },
        )

    async def get_recommendation(self, recommendation_id: str) -> dict[str, Any]:
        """Get full recommendation detail, evidence, guardrails, and review history."""
        return await self.api_client.get(f"/api/recommendations/{recommendation_id}")

    async def get_latest_report(self) -> dict[str, Any]:
        """Retrieve the latest persisted capacity analysis report."""
        return await self.api_client.get("/api/reports/latest")

    async def ask_review_assistant(self, recommendation_id: str, question: str) -> dict[str, Any]:
        """Ask the existing deterministic review assistant about a saved recommendation."""
        return await self.api_client.post(
            "/api/review-assistant/ask",
            {
                "recommendation_id": recommendation_id,
                "question": question,
            },
        )
