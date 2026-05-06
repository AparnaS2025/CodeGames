from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.agent_graph import CapacityAgent
from app.service import CapacityIntelligenceService


STATIC_DIR = Path(__file__).resolve().parent / "static"


class IngestionRunRequest(BaseModel):
    window_days: int | None = Field(default=None, ge=1, le=90)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)


class AnalysisRunRequest(BaseModel):
    resource_ids: list[str] | None = None
    source_categories: list[str] | None = None
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)


class RecommendationReviewRequest(BaseModel):
    decision: Literal["approve", "reject", "snooze"]
    reviewer: str = Field(min_length=1)
    comment: str | None = None


class ReviewAssistantAskRequest(BaseModel):
    recommendation_id: str = Field(min_length=1)
    question: str = Field(min_length=1)


class AgentAskRequest(BaseModel):
    query: str = Field(min_length=1)
    run_label: str | None = Field(default=None, min_length=1, max_length=128)


def create_app(service: CapacityIntelligenceService) -> FastAPI:
    app = FastAPI(
        title="Capacity Intelligence Agentic MVP",
        version="0.1.0",
        description="Advisory-only capacity analysis API with deterministic analytics and supporting observability connectors.",
    )
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    def root() -> dict[str, object]:
        return {
            "name": "Capacity Intelligence Agentic MVP",
            "status": "ok",
            "message": "Use the API routes below to run ingestion, analysis, and review flows.",
            "routes": [
                "POST /api/ingestion/run",
                "POST /api/analysis/run",
                "GET /api/resources",
                "GET /api/recommendations",
                "GET /api/reports/latest",
                "GET /api/runs",
                "GET /api/runs/{run_id}",
                "POST /api/agent/ask",
                "POST /api/review-assistant/ask",
                "GET /dashboard",
                "GET /docs",
            ],
        }

    @app.get("/dashboard", response_class=HTMLResponse)
    def dashboard() -> HTMLResponse:
        return HTMLResponse((STATIC_DIR / "dashboard.html").read_text(encoding="utf-8"))

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/ingestion/run", status_code=202)
    def run_ingestion(payload: IngestionRunRequest) -> dict[str, object]:
        return service.run_ingestion(
            window_days=payload.window_days,
            idempotency_key=payload.idempotency_key,
        )

    @app.post("/api/analysis/run", status_code=202)
    def run_analysis(payload: AnalysisRunRequest) -> dict[str, object]:
        return service.run_analysis(
            resource_ids=payload.resource_ids,
            source_categories=payload.source_categories,
            idempotency_key=payload.idempotency_key,
        )

    @app.get("/api/runs")
    def list_runs(
        run_type: str | None = Query(default=None),
        limit: int = Query(default=20, ge=1, le=100),
    ) -> dict[str, object]:
        return {"runs": service.list_capacity_runs(run_type=run_type, limit=limit)}

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, object]:
        run = service.get_capacity_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run_not_found")
        return run

    @app.get("/api/resources")
    def list_resources(
        environment: str | None = Query(default=None),
        resource_type: str | None = Query(default=None),
        active_only: bool = Query(default=False),
    ) -> dict[str, object]:
        return {
            "resources": service.list_resources(
                environment=environment,
                resource_type=resource_type,
                active_only=active_only,
            )
        }

    @app.get("/api/resources/{resource_id}")
    def get_resource(resource_id: str) -> dict[str, object]:
        resource = service.get_resource_detail(resource_id)
        if resource is None:
            raise HTTPException(status_code=404, detail="resource_not_found")
        return resource

    @app.get("/api/recommendations")
    def list_recommendations(
        recommendation_type: str | None = Query(default=None),
        confidence: str | None = Query(default=None),
        status: str | None = Query(default=None),
        environment: str | None = Query(default=None),
    ) -> dict[str, object]:
        return {
            "recommendations": service.list_recommendations(
                recommendation_type=recommendation_type,
                confidence=confidence,
                status=status,
                environment=environment,
            )
        }

    @app.get("/api/recommendations/{recommendation_id}")
    def get_recommendation(recommendation_id: str) -> dict[str, object]:
        recommendation = service.get_recommendation_detail(recommendation_id)
        if recommendation is None:
            raise HTTPException(status_code=404, detail="recommendation_not_found")
        return recommendation

    @app.post("/api/recommendations/{recommendation_id}/review")
    def review_recommendation(
        recommendation_id: str,
        payload: RecommendationReviewRequest,
    ) -> dict[str, object]:
        review = service.review_recommendation(
            recommendation_id=recommendation_id,
            decision=payload.decision,
            reviewer=payload.reviewer,
            comment=payload.comment,
        )
        if review is None:
            raise HTTPException(status_code=404, detail="recommendation_not_found")
        return review

    @app.get("/api/reports/latest")
    def latest_report() -> dict[str, object]:
        report = service.latest_report()
        if report is None:
            raise HTTPException(status_code=404, detail="report_not_found")
        return report

    @app.post("/api/review-assistant/ask")
    def ask_review_assistant(payload: ReviewAssistantAskRequest) -> dict[str, object]:
        answer = service.answer_review_question(
            recommendation_id=payload.recommendation_id,
            question=payload.question,
        )
        if answer is None:
            raise HTTPException(status_code=404, detail="recommendation_not_found")
        return answer

    @app.post("/api/agent/ask")
    def ask_capacity_agent(payload: AgentAskRequest) -> dict[str, object]:
        agent = CapacityAgent(service)
        return agent.ask(query=payload.query, run_label=payload.run_label)

    return app
