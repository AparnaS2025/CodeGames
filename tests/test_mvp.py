from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
import unittest

import httpx
from fastapi.testclient import TestClient

from app.api import create_app
from app.agent_graph import CapacityAgent
from app.agent_model import build_agent_llm
from app.config import Settings
from app.connectors.base import ConnectorFetchResult
from app.connectors.sumologic import SUMOLOGIC_HTTP_LOG_PARSE_EXPRESSION, SumoLogicConnector
from app.cow_api_client import CapacityApiClient, CapacityApiConfig
from app.cow_mcp_tools import CapacityMcpTools
from app.analysis import build_analysis_snapshot, generate_recommendation
from app.models import NormalizedMetricPoint, RawMetricPayload, RecommendationType, Resource
from app.normalization import build_resource_resolution_index, normalize_payloads
from app.service import CapacityIntelligenceService
from app.storage import Repository


FIXED_NOW = datetime(2026, 4, 18, 9, 0, tzinfo=timezone.utc)


class FakeAgentLlm:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls = []

    def invoke(self, messages, **kwargs):
        self.calls.append(messages)

        class Response:
            content = self.content

        return Response()


class CapacityMvpTests(unittest.TestCase):
    def setUp(self) -> None:
        settings = Settings()
        settings.db_path = ":memory:"
        settings.analysis_window_days = 7
        settings.sumologic_use_sample_data = True
        self.settings = settings
        self.repository = Repository(settings.db_path)
        self.service = CapacityIntelligenceService(self.repository, settings)
        self.client = TestClient(create_app(self.service))

    def tearDown(self) -> None:
        self.client.close()
        self.repository.close()

    def test_ingestion_is_idempotent_for_overlapping_windows(self) -> None:
        self.service.run_ingestion(now=FIXED_NOW)
        first_count = self.service.repository.count_metrics()
        self.service.run_ingestion(now=FIXED_NOW)
        second_count = self.service.repository.count_metrics()

        self.assertEqual(first_count, second_count)
        self.assertEqual(len(self.service.list_resources(active_only=True)), 3)
        self.assertGreater(first_count, 0)

    def test_ingestion_can_run_as_pure_sumologic_without_seeded_sample_resources(self) -> None:
        class FakeSumologicConnector:
            source_name = "sumologic"

            def fetch(self, window_start: datetime, window_end: datetime) -> ConnectorFetchResult:
                return ConnectorFetchResult(
                    payloads=[
                        RawMetricPayload(
                            source="sumologic",
                            external_resource_id="sumo-only-service",
                            metric_name="sumo.ec2.cpu.utilization",
                            timestamp=window_end - timedelta(minutes=5),
                            value=42.0,
                            unit="percent",
                            dimensions={"wk_environment_type": "production"},
                        )
                    ],
                    health={"status": "healthy", "mode": "api", "payload_count": 1},
                )

        settings = Settings()
        settings.db_path = ":memory:"
        settings.datadog_enabled = False
        settings.cloudwatch_enabled = False
        settings.sumologic_use_sample_data = False
        repository = Repository(settings.db_path)
        service = CapacityIntelligenceService(repository, settings, connectors=[FakeSumologicConnector()])
        try:
            result = service.run_ingestion(now=FIXED_NOW)
            resources = service.list_resources(active_only=True)
        finally:
            repository.close()

        self.assertEqual([item["source"] for item in result["source_run_status"]], ["sumologic"])
        self.assertNotIn("checkout-api", {item["name"] for item in resources})
        self.assertNotIn("analytics-worker", {item["name"] for item in resources})
        self.assertNotIn("orders-db", {item["name"] for item in resources})
        self.assertEqual({item["name"] for item in resources}, {"sumo-only-service"})

    def test_analysis_generates_recommendations_and_report(self) -> None:
        self.service.run_ingestion(now=FIXED_NOW)
        result = self.service.run_analysis(now=FIXED_NOW)
        recommendations = self.service.list_recommendations()
        recommendation_types = {item["recommendation_type"] for item in recommendations}
        report = self.service.latest_report()

        self.assertEqual(result["queued_resources"], 3)
        self.assertIn("scale_down", recommendation_types)
        self.assertTrue({"watchlist", "hold"} & recommendation_types)
        self.assertTrue(report)
        for item in recommendations:
            self.assertGreaterEqual(len(item["evidence_json"]), 3)
            self.assertGreaterEqual(len(item["guardrails_json"]), 1)

    def test_stale_primary_data_becomes_insufficient_data(self) -> None:
        self.service.run_ingestion(now=FIXED_NOW)
        self.service.run_analysis(now=FIXED_NOW + timedelta(days=3))
        recommendations = self.service.list_recommendations()

        self.assertTrue(recommendations)
        self.assertTrue(all(item["recommendation_type"] == "insufficient_data" for item in recommendations[:3]))
        self.assertTrue(
            all(
                any("Insufficient data reason(s):" in evidence for evidence in item["evidence_json"])
                for item in recommendations[:3]
            )
        )

    def test_analysis_uses_coverage_ratio_for_history_boundary(self) -> None:
        self.settings.minimum_history_days = 7
        self.settings.minimum_history_coverage_ratio = 0.90
        resource = Resource(
            resource_id="app-near-window",
            name="near-window",
            resource_type="app_service",
            environment="prod",
            current_size="large",
            metadata={"source_aliases": {"sumologic": "near-window"}},
        )
        window_start = FIXED_NOW - timedelta(days=7)
        window_end = FIXED_NOW
        metrics = [
            NormalizedMetricPoint(
                resource_id=resource.resource_id,
                metric_name="ec2_cpu_percent",
                timestamp_utc=timestamp,
                value=42.0,
                unit="percent",
                source="sumologic",
                dimensions={},
            ).to_record()
            for timestamp in (window_start + timedelta(hours=1, minutes=40), window_end - timedelta(minutes=1))
        ]

        features, tags, _ = build_analysis_snapshot(resource, metrics, window_start, window_end, self.settings)

        self.assertAlmostEqual(features["history_days"], 6.93, places=2)
        self.assertAlmostEqual(features["cpu_coverage_ratio"], 0.99, places=2)
        self.assertNotIn("short history", features["insufficient_data_reasons"])
        self.assertNotIn("insufficient_data", tags)
        recommendation = generate_recommendation(resource, features, tags, self.settings)
        self.assertTrue(any("CPU telemetry coverage is 99.0%" in item for item in recommendation.evidence))

    def test_analysis_flags_short_history_when_cpu_coverage_is_low(self) -> None:
        self.settings.minimum_history_days = 7
        self.settings.minimum_history_coverage_ratio = 0.90
        resource = Resource(
            resource_id="app-short-history",
            name="short-history",
            resource_type="app_service",
            environment="prod",
            current_size="large",
            metadata={"source_aliases": {"sumologic": "short-history"}},
        )
        window_start = FIXED_NOW - timedelta(days=7)
        window_end = FIXED_NOW
        metrics = [
            NormalizedMetricPoint(
                resource_id=resource.resource_id,
                metric_name="ec2_cpu_percent",
                timestamp_utc=timestamp,
                value=42.0,
                unit="percent",
                source="sumologic",
                dimensions={},
            ).to_record()
            for timestamp in (window_end - timedelta(days=1), window_end - timedelta(minutes=1))
        ]

        features, tags, _ = build_analysis_snapshot(resource, metrics, window_start, window_end, self.settings)
        recommendation = generate_recommendation(resource, features, tags, self.settings)

        self.assertLess(features["cpu_coverage_ratio"], 0.90)
        self.assertIn("short history", features["insufficient_data_reasons"])
        self.assertIn("insufficient_data", tags)
        self.assertTrue(any("CPU telemetry coverage is" in item and "insufficient" in item for item in recommendation.evidence))

    def test_analysis_separates_missing_cpu_from_short_history(self) -> None:
        self.settings.minimum_history_days = 7
        self.settings.minimum_history_coverage_ratio = 0.90
        resource = Resource(
            resource_id="app-alb-only",
            name="alb-only",
            resource_type="app_service",
            environment="prod",
            current_size="large",
            metadata={"source_aliases": {"sumologic": "alb-only"}},
        )
        window_start = FIXED_NOW - timedelta(days=7)
        window_end = FIXED_NOW
        metrics = [
            NormalizedMetricPoint(
                resource_id=resource.resource_id,
                metric_name="target_response_time_ms",
                timestamp_utc=timestamp,
                value=120.0,
                unit="milliseconds",
                source="sumologic",
                dimensions={},
            ).to_record()
            for timestamp in (window_start + timedelta(hours=1), window_end - timedelta(minutes=1))
        ]

        features, tags, _ = build_analysis_snapshot(resource, metrics, window_start, window_end, self.settings)
        recommendation = generate_recommendation(resource, features, tags, self.settings)

        self.assertIn("missing CPU", features["insufficient_data_reasons"])
        self.assertNotIn("short history", features["insufficient_data_reasons"])
        self.assertIn("insufficient_data", tags)
        self.assertTrue(any("Telemetry coverage is" in item for item in recommendation.evidence))

    def test_duplicate_review_action_is_idempotent(self) -> None:
        self.service.run_ingestion(now=FIXED_NOW)
        self.service.run_analysis(now=FIXED_NOW)
        recommendation = next(
            item for item in self.service.list_recommendations() if item["recommendation_type"] == "scale_down"
        )

        first = self.service.review_recommendation(recommendation["recommendation_id"], "approve", "aparna", "safe to trial")
        second = self.service.review_recommendation(recommendation["recommendation_id"], "approve", "aparna", "safe to trial")
        detail = self.service.get_recommendation_detail(recommendation["recommendation_id"])

        self.assertEqual(first["review_id"], second["review_id"])
        self.assertEqual(first["action_item"]["action_id"], second["action_item"]["action_id"])
        self.assertEqual(len(detail["review_history"]), 1)
        self.assertEqual(detail["review_history"][0]["decision"], "approve")
        self.assertEqual(detail["status"], "action_created")
        self.assertEqual(detail["action_item"]["status"], "pending")

    def test_approval_pack_and_action_item_flow_are_exposed_by_api(self) -> None:
        self.service.run_ingestion(now=FIXED_NOW)
        self.service.run_analysis(now=FIXED_NOW)
        recommendation = next(
            item for item in self.service.list_recommendations() if item["recommendation_type"] == "scale_down"
        )

        pack = self.client.get(f"/api/recommendations/{recommendation['recommendation_id']}/approval-pack")
        review = self.client.post(
            f"/api/recommendations/{recommendation['recommendation_id']}/review",
            json={"decision": "approve", "reviewer": "platform.owner", "comment": "approved for action tracking"},
        )
        actions = self.client.get("/api/action-items")

        self.assertEqual(pack.status_code, 200)
        self.assertTrue(pack.json()["approval_required"])
        self.assertEqual(pack.json()["action_preview"]["action_type"], "capacity_scale_down")
        self.assertEqual(review.status_code, 200)
        self.assertEqual(review.json()["action_item"]["status"], "pending")
        self.assertEqual(actions.status_code, 200)
        self.assertEqual(actions.json()["action_items"][0]["recommendation_id"], recommendation["recommendation_id"])

    def test_api_exposes_execution_flow(self) -> None:
        ingestion = self.client.post("/api/ingestion/run", json={})
        self.assertEqual(ingestion.status_code, 202)
        self.assertIn("run_id", ingestion.json())

        analysis = self.client.post("/api/analysis/run", json={})
        self.assertEqual(analysis.status_code, 202)
        self.assertEqual(analysis.json()["queued_resources"], 3)

        recommendations = self.client.get("/api/recommendations")
        self.assertEqual(recommendations.status_code, 200)
        self.assertEqual(len(recommendations.json()["recommendations"]), 3)

        recommendation_id = recommendations.json()["recommendations"][0]["recommendation_id"]
        answer = self.client.post(
            "/api/review-assistant/ask",
            json={"recommendation_id": recommendation_id, "question": "What are the risks?"},
        )
        self.assertEqual(answer.status_code, 200)
        self.assertIn("answer", answer.json())

    def test_run_status_endpoints_show_completed_runs(self) -> None:
        ingestion = self.client.post("/api/ingestion/run", json={"idempotency_key": "ingest-api-1"})
        analysis = self.client.post("/api/analysis/run", json={"idempotency_key": "analysis-api-1"})

        self.assertEqual(ingestion.status_code, 202)
        self.assertEqual(analysis.status_code, 202)

        run = self.client.get(f"/api/runs/{analysis.json()['run_id']}")
        runs = self.client.get("/api/runs", params={"run_type": "analysis"})

        self.assertEqual(run.status_code, 200)
        self.assertEqual(run.json()["status"], "completed")
        self.assertEqual(run.json()["idempotency_key"], "analysis-api-1")
        self.assertEqual(run.json()["result_json"]["report_id"], analysis.json()["report_id"])
        self.assertEqual(runs.status_code, 200)
        self.assertEqual(runs.json()["runs"][0]["run_type"], "analysis")

    def test_analysis_idempotency_key_prevents_duplicate_recommendations_and_reports(self) -> None:
        self.service.run_ingestion(now=FIXED_NOW)
        first = self.service.run_analysis(now=FIXED_NOW, idempotency_key="analysis-once")
        recommendation_count = len(self.service.list_recommendations())
        report_id = self.service.latest_report()["report_id"]

        second = self.service.run_analysis(now=FIXED_NOW, idempotency_key="analysis-once")

        self.assertEqual(second["analysis_run_id"], first["analysis_run_id"])
        self.assertEqual(second["report_id"], first["report_id"])
        self.assertTrue(second["idempotent_replay"])
        self.assertEqual(len(self.service.list_recommendations()), recommendation_count)
        self.assertEqual(self.service.latest_report()["report_id"], report_id)

    def test_langgraph_agent_runs_capacity_cycle_with_idempotency(self) -> None:
        first = self.client.post(
            "/api/agent/ask",
            json={"query": "Run the full capacity cycle", "run_label": "agent-cycle-once"},
        )
        recommendation_count = len(self.service.list_recommendations())
        second = self.client.post(
            "/api/agent/ask",
            json={"query": "Run the full capacity cycle", "run_label": "agent-cycle-once"},
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["intent"], "execute_cycle")
        self.assertEqual(first.json()["answer_mode"], "deterministic")
        self.assertFalse(first.json()["llm_enabled"])
        self.assertIn("Capacity cycle completed", first.json()["answer"])
        self.assertIn("No capacity change was applied", first.json()["answer"])
        self.assertEqual(
            [call["name"] for call in first.json()["tool_calls"]],
            [
                "run_ingestion",
                "match_resources",
                "run_analysis",
                "get_run_status",
                "get_latest_report",
                "list_recommendations",
            ],
        )
        self.assertEqual(second.status_code, 200)
        self.assertEqual(len(self.service.list_recommendations()), recommendation_count)
        analysis_replay = second.json()["tool_calls"][2]["result_summary"]
        self.assertTrue(analysis_replay["idempotent_replay"])

    def test_langgraph_agent_scopes_customer_window_and_idempotency_key(self) -> None:
        first = self.client.post(
            "/api/agent/ask",
            json={"query": "Can you run the cycle for checkout for last 7 days?", "run_label": "scoped-cycle-a"},
        )
        second = self.client.post(
            "/api/agent/ask",
            json={"query": "Can you run the cycle for checkout for last 7 days?", "run_label": "scoped-cycle-b"},
        )

        self.assertEqual(first.status_code, 200)
        tool_summaries = {call["name"]: call["result_summary"] for call in first.json()["tool_calls"]}
        self.assertEqual(tool_summaries["run_ingestion"]["window_days"], 7)
        self.assertEqual(tool_summaries["match_resources"]["customer"], "checkout")
        self.assertEqual(tool_summaries["match_resources"]["matched_resources"], 1)
        self.assertEqual(tool_summaries["run_analysis"]["queued_resources"], 1)
        self.assertEqual(tool_summaries["run_analysis"]["window_days"], 7)
        self.assertEqual(
            tool_summaries["run_analysis"]["idempotency_key"],
            "analysis|customer=checkout|window=7d|analysis_version=v1",
        )
        self.assertIn("Scope: checkout, last 7 days", first.json()["answer"])

        self.assertEqual(second.status_code, 200)
        replay_summaries = {call["name"]: call["result_summary"] for call in second.json()["tool_calls"]}
        self.assertTrue(replay_summaries["run_ingestion"]["idempotent_replay"])
        self.assertTrue(replay_summaries["run_analysis"]["idempotent_replay"])

    def test_langgraph_agent_force_refresh_bypasses_stable_cache(self) -> None:
        first = self.client.post(
            "/api/agent/ask",
            json={"query": "Run the cycle for checkout for last 7 days", "run_label": "cache-cycle-a"},
        )
        replay = self.client.post(
            "/api/agent/ask",
            json={"query": "Run the cycle for checkout for last 7 days", "run_label": "cache-cycle-b"},
        )
        forced = self.client.post(
            "/api/agent/ask",
            json={"query": "Force refresh checkout for last 7 days with no cache", "run_label": "force-cycle-a"},
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(forced.status_code, 200)
        replay_summaries = {call["name"]: call["result_summary"] for call in replay.json()["tool_calls"]}
        force_summaries = {call["name"]: call["result_summary"] for call in forced.json()["tool_calls"]}
        self.assertTrue(replay_summaries["run_ingestion"]["idempotent_replay"])
        self.assertTrue(replay_summaries["run_analysis"]["idempotent_replay"])
        self.assertNotIn("idempotent_replay", force_summaries["run_ingestion"])
        self.assertNotIn("idempotent_replay", force_summaries["run_analysis"])
        self.assertEqual(
            force_summaries["run_analysis"]["idempotency_key"],
            "analysis|customer=checkout|window=7d|analysis_version=v1|refresh=force-cycle-a",
        )
        self.assertTrue(force_summaries["list_recommendations"]["scope"]["force_refresh"])
        self.assertIn("Cache bypass requested", forced.json()["answer"])

    def test_langgraph_agent_does_not_treat_window_as_customer(self) -> None:
        response = self.client.post(
            "/api/agent/ask",
            json={"query": "Run the capacity cycle for last 7 days"},
        )

        self.assertEqual(response.status_code, 200)
        match_summary = next(call["result_summary"] for call in response.json()["tool_calls"] if call["name"] == "match_resources")
        analysis_summary = next(call["result_summary"] for call in response.json()["tool_calls"] if call["name"] == "run_analysis")
        self.assertEqual(match_summary["customer"], "all")
        self.assertEqual(match_summary["matched_resources"], 3)
        self.assertEqual(analysis_summary["queued_resources"], 3)
        self.assertEqual(analysis_summary["window_days"], 7)

    def test_langgraph_agent_reviews_latest_report(self) -> None:
        self.service.run_ingestion(now=FIXED_NOW)
        self.service.run_analysis(now=FIXED_NOW)

        response = self.client.post(
            "/api/agent/ask",
            json={"query": "Show me the latest report"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["intent"], "review")
        self.assertEqual(response.json()["answer_mode"], "deterministic")
        self.assertIn("Latest report", response.json()["answer"])
        self.assertIn("No capacity change was applied", response.json()["answer"])
        self.assertEqual(
            [call["name"] for call in response.json()["tool_calls"]],
            ["get_latest_report", "list_recommendations"],
        )

    def test_langgraph_agent_can_use_guarded_llm_summary(self) -> None:
        self.service.run_ingestion(now=FIXED_NOW)
        self.service.run_analysis(now=FIXED_NOW)
        llm = FakeAgentLlm("LLM summary grounded in stored recommendations.")
        agent = CapacityAgent(self.service, llm=llm)

        response = agent.ask("Show me the latest report", run_label="fake-llm")

        self.assertTrue(response["llm_enabled"])
        self.assertEqual(response["requested_answer_mode"], "llm")
        self.assertEqual(response["answer_mode"], "llm")
        self.assertEqual(len(llm.calls), 1)
        self.assertIn("LLM summary grounded", response["answer"])
        self.assertIn("advisory-only", response["answer"])
        self.assertEqual(response["tool_calls"][0]["name"], "get_latest_report")

    def test_langgraph_agent_falls_back_when_llm_fails(self) -> None:
        class FailingLlm:
            def invoke(self, messages, **kwargs):
                raise RuntimeError("model unavailable")

        self.service.run_ingestion(now=FIXED_NOW)
        self.service.run_analysis(now=FIXED_NOW)
        agent = CapacityAgent(self.service, llm=FailingLlm())

        response = agent.ask("Show me the latest report", run_label="failing-llm")

        self.assertIn("Latest report", response["answer"])
        self.assertIn("No capacity change was applied", response["answer"])
        self.assertFalse(response["llm_enabled"])
        self.assertEqual(response["answer_mode"], "deterministic")

    def test_langgraph_agent_can_force_deterministic_answer_mode(self) -> None:
        self.service.run_ingestion(now=FIXED_NOW)
        self.service.run_analysis(now=FIXED_NOW)
        llm = FakeAgentLlm("This LLM response should not be used.")
        agent = CapacityAgent(self.service, llm=llm)

        response = agent.ask("Show me the latest report", run_label="python-mode", answer_mode="deterministic")

        self.assertFalse(response["llm_enabled"])
        self.assertTrue(response["llm_available"])
        self.assertEqual(response["requested_answer_mode"], "deterministic")
        self.assertEqual(response["answer_mode"], "deterministic")
        self.assertEqual(len(llm.calls), 0)
        self.assertIn("Latest report", response["answer"])

    def test_agent_llm_builder_ignores_placeholder_env_values(self) -> None:
        settings = Settings()
        settings.agent_enable_llm = True
        settings.azure_openai_endpoint = "https://capacitymcpfoundry.openai.azure.com/"
        settings.azure_openai_api_key = "<your-rotated-key>"
        settings.azure_openai_deployment = "gpt-4.1"
        settings.azure_openai_api_version = "2024-10-21"

        self.assertIsNone(build_agent_llm(settings))

    def test_settings_defaults_use_sixty_day_analysis_and_forty_eight_hour_staleness(self) -> None:
        original_analysis = os.environ.get("ANALYSIS_WINDOW_DAYS")
        original_stale = os.environ.get("STALE_AFTER_HOURS")
        original_coverage = os.environ.get("MINIMUM_HISTORY_COVERAGE_RATIO")
        try:
            os.environ.pop("ANALYSIS_WINDOW_DAYS", None)
            os.environ.pop("STALE_AFTER_HOURS", None)
            os.environ.pop("MINIMUM_HISTORY_COVERAGE_RATIO", None)
            defaults = Settings()

            self.assertEqual(defaults.analysis_window_days, 60)
            self.assertEqual(defaults.stale_after_hours, 48)
            self.assertEqual(defaults.minimum_history_coverage_ratio, 0.90)
        finally:
            if original_analysis is None:
                os.environ.pop("ANALYSIS_WINDOW_DAYS", None)
            else:
                os.environ["ANALYSIS_WINDOW_DAYS"] = original_analysis
            if original_stale is None:
                os.environ.pop("STALE_AFTER_HOURS", None)
            else:
                os.environ["STALE_AFTER_HOURS"] = original_stale
            if original_coverage is None:
                os.environ.pop("MINIMUM_HISTORY_COVERAGE_RATIO", None)
            else:
                os.environ["MINIMUM_HISTORY_COVERAGE_RATIO"] = original_coverage

    def test_root_and_health_routes_are_available(self) -> None:
        root = self.client.get("/")
        self.assertEqual(root.status_code, 200)
        self.assertEqual(root.json()["status"], "ok")
        self.assertIn("POST /api/ingestion/run", root.json()["routes"])
        self.assertIn("GET /dashboard", root.json()["routes"])

        health = self.client.get("/health")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["status"], "ok")

    def test_dashboard_page_and_assets_are_available(self) -> None:
        dashboard = self.client.get("/dashboard")
        stylesheet = self.client.get("/static/dashboard.css")
        script = self.client.get("/static/dashboard.js")

        self.assertEqual(dashboard.status_code, 200)
        self.assertIn("Wolters Kluwer", dashboard.text)
        self.assertIn("/static/assets/wolters-kluwer-logo.png", dashboard.text)
        self.assertIn("/static/assets/codegames-agentic-edition-2026.png", dashboard.text)
        self.assertIn("Agentic CoW Capacity Intelligence", dashboard.text)
        self.assertIn("Recommendation Summary", dashboard.text)
        self.assertIn("Agent Assistant", dashboard.text)
        self.assertIn("name=\"agentMode\"", dashboard.text)
        self.assertIn("Force refresh Chevron for last 7 days with no cache", dashboard.text)
        self.assertIn("insufficientReasons", dashboard.text)
        self.assertIn("Missing CPU", dashboard.text)
        self.assertIn("Short history", dashboard.text)
        self.assertIn("Stale source", dashboard.text)
        self.assertIn("No primary source", dashboard.text)
        self.assertIn("Approval Pack", dashboard.text)
        self.assertIn("Capacity action items", dashboard.text)
        self.assertEqual(stylesheet.status_code, 200)
        self.assertIn("wk-logo", stylesheet.text)
        self.assertIn("codegames-logo", stylesheet.text)
        self.assertIn("agent-panel", stylesheet.text)
        self.assertIn("reason-breakdown", stylesheet.text)
        self.assertIn("workspace detail agent", stylesheet.text)
        self.assertIn("actions-panel", stylesheet.text)
        self.assertEqual(script.status_code, 200)
        self.assertIn("/api/agent/ask", script.text)
        self.assertIn("/api/action-items", script.text)
        self.assertIn("answer_mode", script.text)
        self.assertIn("insufficientReasonCounts", script.text)
        self.assertIn("data-review-decision", dashboard.text)

    def test_sumologic_connector_uses_sample_mode_by_default(self) -> None:
        connector = SumoLogicConnector(self.settings)
        window_end = FIXED_NOW
        window_start = FIXED_NOW - timedelta(days=1)

        result = connector.fetch(window_start, window_end)

        self.assertEqual(result.health["mode"], "sample")
        self.assertEqual(result.health["status"], "healthy")
        self.assertGreater(len(result.payloads), 0)

    def test_sumologic_query_window_is_clamped_to_one_day(self) -> None:
        connector = SumoLogicConnector(self.settings)
        window_end = FIXED_NOW
        window_start = FIXED_NOW - timedelta(days=30)

        effective_start, effective_end = connector._effective_window(window_start, window_end)

        self.assertEqual(effective_end, window_end)
        self.assertEqual(effective_start, window_end - timedelta(days=1))

    def test_sumologic_builds_production_metric_jobs_by_default(self) -> None:
        connector = SumoLogicConnector(self.settings)

        jobs = connector._build_query_jobs()
        metric_names = {job.metric_name for job in jobs}

        self.assertIn("sumo.ec2.cpu.utilization", metric_names)
        self.assertIn("sumo.rds.cpu.utilization", metric_names)
        self.assertIn("sumo.alb.target_response_time.ms", metric_names)
        self.assertNotIn("sumo.http.call.count", metric_names)

    def test_sumologic_http_log_queries_can_be_enabled(self) -> None:
        self.settings.sumologic_enable_http_log_queries = True
        connector = SumoLogicConnector(self.settings)

        jobs = connector._build_query_jobs()
        http_jobs = {job.metric_name: job for job in jobs if job.metric_name.startswith("sumo.http.")}

        self.assertIn("sumo.http.call.count", http_jobs)
        self.assertIn("sumo.http.request.size.bytes", http_jobs)
        self.assertIn("sumo.http.duration.ms", http_jobs)
        self.assertIn("request_size_token", http_jobs["sumo.http.request.size.bytes"].query)
        self.assertIn(SUMOLOGIC_HTTP_LOG_PARSE_EXPRESSION, http_jobs["sumo.http.duration.ms"].query)

    def test_sumologic_latency_metrics_normalize_seconds_to_milliseconds(self) -> None:
        resource = Resource(
            resource_id="db-orders",
            name="orders-db",
            resource_type="db_instance",
            environment="prod",
            current_size="large",
            metadata={"source_aliases": {"sumologic": "orders-db"}},
        )
        normalized, issues = normalize_payloads(
            [
                RawMetricPayload(
                    source="sumologic",
                    external_resource_id="orders-db",
                    metric_name="sumo.rds.read_latency.ms",
                    timestamp=FIXED_NOW,
                    value=0.012,
                    unit="seconds",
                    dimensions={"db_instance_identifier": "orders-db"},
                )
            ],
            {"db-orders": resource},
        )

        self.assertFalse(issues)
        self.assertEqual(normalized[0].metric_name, "db_read_latency_ms")
        self.assertAlmostEqual(normalized[0].value, 12.0, places=3)

    def test_settings_can_read_sumologic_window_overrides_from_environment(self) -> None:
        original_days = os.environ.get("SUMOLOGIC_MAX_QUERY_WINDOW_DAYS")
        original_minutes = os.environ.get("SUMOLOGIC_QUERY_WINDOW_MINUTES")
        original_workers = os.environ.get("SUMOLOGIC_MAX_WORKERS")
        try:
            os.environ["SUMOLOGIC_MAX_QUERY_WINDOW_DAYS"] = "30"
            os.environ["SUMOLOGIC_QUERY_WINDOW_MINUTES"] = "15"
            os.environ["SUMOLOGIC_MAX_WORKERS"] = "6"
            overridden = Settings()
            self.assertEqual(overridden.sumologic_max_query_window_days, 30)
            self.assertEqual(overridden.sumologic_query_window_minutes, 15)
            self.assertEqual(overridden.sumologic_max_workers, 6)
        finally:
            if original_days is None:
                os.environ.pop("SUMOLOGIC_MAX_QUERY_WINDOW_DAYS", None)
            else:
                os.environ["SUMOLOGIC_MAX_QUERY_WINDOW_DAYS"] = original_days
            if original_minutes is None:
                os.environ.pop("SUMOLOGIC_QUERY_WINDOW_MINUTES", None)
            else:
                os.environ["SUMOLOGIC_QUERY_WINDOW_MINUTES"] = original_minutes
            if original_workers is None:
                os.environ.pop("SUMOLOGIC_MAX_WORKERS", None)
            else:
                os.environ["SUMOLOGIC_MAX_WORKERS"] = original_workers

    def test_service_can_infer_sumologic_resources_from_live_payload_shape(self) -> None:
        payload = RawMetricPayload(
            source="sumologic",
            external_resource_id="customer-a-ovp-blue-pv",
            metric_name="sumo.ec2.cpu.utilization",
            timestamp=FIXED_NOW,
            value=42.0,
            unit="percent",
            dimensions={"wk_environment_type": "production", "Region": "eu-west-1"},
        )

        inferred = self.service._infer_resources_from_payloads([payload], {}, {})

        self.assertEqual(len(inferred), 1)
        self.assertEqual(inferred[0].resource_id, "customer-a-ovp-blue-pv")
        self.assertEqual(inferred[0].resource_type, "app_service")
        self.assertEqual(inferred[0].environment, "production")

    def test_resource_resolution_index_uses_source_aliases(self) -> None:
        resource = Resource(
            resource_id="app-checkout",
            name="checkout-api",
            resource_type="app_service",
            environment="prod",
            current_size="medium",
            metadata={"source_aliases": {"sumologic": "checkout-asg", "cloudwatch": "i-123"}},
        )

        index = build_resource_resolution_index({"app-checkout": resource})

        self.assertEqual(index["app-checkout"], "app-checkout")
        self.assertEqual(index["checkout-api"], "app-checkout")
        self.assertEqual(index["sumologic:checkout-asg"], "app-checkout")
        self.assertEqual(index["cloudwatch:i-123"], "app-checkout")

    def test_repository_can_batch_load_metrics_for_analysis(self) -> None:
        resource_a = Resource(
            resource_id="app-a",
            name="app-a",
            resource_type="app_service",
            environment="prod",
            current_size="medium",
            metadata={},
        )
        resource_b = Resource(
            resource_id="app-b",
            name="app-b",
            resource_type="app_service",
            environment="prod",
            current_size="medium",
            metadata={},
        )
        self.repository.upsert_resources([resource_a, resource_b])
        self.repository.upsert_metrics(
            [
                NormalizedMetricPoint(
                    resource_id="app-a",
                    metric_name="cpu_percent",
                    timestamp_utc=FIXED_NOW - timedelta(hours=1),
                    value=15.0,
                    unit="percent",
                    source="datadog",
                    dimensions={"service": "app-a"},
                ).to_record(),
                NormalizedMetricPoint(
                    resource_id="app-b",
                    metric_name="db_connections",
                    timestamp_utc=FIXED_NOW - timedelta(hours=1),
                    value=22.0,
                    unit="count",
                    source="sumologic",
                    dimensions={"db_instance_identifier": "app-b"},
                ).to_record(),
            ]
        )

        grouped = self.repository.get_metrics_for_resources(
            ["app-a", "app-b"],
            (FIXED_NOW - timedelta(days=1)).isoformat(),
            FIXED_NOW.isoformat(),
        )

        self.assertEqual(set(grouped), {"app-a", "app-b"})
        self.assertEqual(grouped["app-a"][0]["metric_name"], "cpu_percent")
        self.assertEqual(grouped["app-b"][0]["metric_name"], "db_connections")

    def test_sumologic_max_workers_are_bounded_by_job_count(self) -> None:
        self.settings.sumologic_max_workers = 6
        connector = SumoLogicConnector(self.settings)

        self.assertEqual(connector._max_workers_for_jobs(0), 1)
        self.assertEqual(connector._max_workers_for_jobs(3), 3)
        self.assertEqual(connector._max_workers_for_jobs(10), 6)

    def test_analysis_can_filter_by_sumologic_source_category(self) -> None:
        resource = Resource(
            resource_id="tenant/deploy/permitvision",
            name="permitvision",
            resource_type="app_service",
            environment="prod",
            current_size="medium",
            metadata={"source_aliases": {"sumologic": "tenant/deploy/permitvision"}},
        )
        self.repository.upsert_resources([resource])
        self.repository.upsert_metrics(
            [
                NormalizedMetricPoint(
                    resource_id="tenant/deploy/permitvision",
                    metric_name="app_error_count",
                    timestamp_utc=FIXED_NOW - timedelta(hours=2),
                    value=3.0,
                    unit="count",
                    source="sumologic",
                    dimensions={"query_mode": "api", "source_category": "prod/tenant/deploy/permitvision/errors"},
                ).to_record()
            ]
        )

        response = self.client.post(
            "/api/analysis/run",
            json={"source_categories": ["prod/tenant/deploy/permitvision/errors"], "window_days": 30},
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["queued_resources"], 1)

    def test_mcp_tools_wrap_current_fastapi_contract(self) -> None:
        async def exercise_tools() -> None:
            transport = httpx.ASGITransport(app=create_app(self.service))
            api_client = CapacityApiClient(
                CapacityApiConfig(
                    base_url="http://testserver",
                    transport=transport,
                )
            )
            tools = CapacityMcpTools(api_client)
            try:
                ingestion = await tools.run_ingestion(idempotency_key="mcp-ingestion")
                analysis = await tools.run_analysis(idempotency_key="mcp-analysis")
                run_status = await tools.get_run_status(analysis["run_id"])
                recommendations = await tools.list_recommendations(recommendation_type="scale_down")
                latest_report = await tools.get_latest_report()
                resources = await tools.list_resources(active_only=True)
            finally:
                await transport.aclose()

            self.assertIn("run_id", ingestion)
            self.assertEqual(analysis["queued_resources"], 3)
            self.assertEqual(run_status["status"], "completed")
            self.assertEqual(run_status["idempotency_key"], "mcp-analysis")
            self.assertIn("recommendations", recommendations)
            self.assertTrue(
                all(item["recommendation_type"] == "scale_down" for item in recommendations["recommendations"])
            )
            self.assertIn("report_id", latest_report)
            self.assertEqual(len(resources["resources"]), 3)

        asyncio.run(exercise_tools())

    def test_capacity_api_client_returns_structured_http_errors(self) -> None:
        async def exercise_error() -> None:
            transport = httpx.ASGITransport(app=create_app(self.service))
            api_client = CapacityApiClient(
                CapacityApiConfig(
                    base_url="http://testserver",
                    transport=transport,
                )
            )
            try:
                response = await api_client.get("/api/reports/latest")
            finally:
                await transport.aclose()

            self.assertEqual(response["status"], "error")
            self.assertEqual(response["error_type"], "http_status")
            self.assertEqual(response["status_code"], 404)
            self.assertEqual(response["detail"]["detail"], "report_not_found")

        asyncio.run(exercise_error())

    def test_cloudwatch_can_act_as_primary_source_for_app_service_analysis(self) -> None:
        self.settings.analysis_window_days = 1
        self.settings.minimum_history_days = 1
        resource = Resource(
            resource_id="ec2:us-east-2:i-1234567890abcdef0",
            name="checkout-ec2",
            resource_type="app_service",
            environment="prod",
            current_size="large",
            metadata={"source_aliases": {"cloudwatch": "i-1234567890abcdef0"}},
        )
        self.repository.upsert_resources([resource])
        self.repository.upsert_metrics(
            [
                NormalizedMetricPoint(
                    resource_id=resource.resource_id,
                    metric_name="ec2_cpu_percent",
                    timestamp_utc=FIXED_NOW - timedelta(hours=offset),
                    value=18.0,
                    unit="percent",
                    source="cloudwatch",
                    dimensions={"region": "us-east-2"},
                ).to_record()
                for offset in range(24, -1, -1)
            ]
        )

        result = self.service.run_analysis(now=FIXED_NOW)
        recommendations = self.service.list_recommendations()

        self.assertEqual(result["queued_resources"], 1)
        self.assertEqual(len(recommendations), 1)
        self.assertEqual(recommendations[0]["recommendation_type"], "scale_down")

    def test_sumologic_is_preferred_as_primary_source_when_present(self) -> None:
        self.settings.analysis_window_days = 1
        self.settings.minimum_history_days = 1
        resource = Resource(
            resource_id="app-checkout",
            name="checkout-api",
            resource_type="app_service",
            environment="prod",
            current_size="large",
            metadata={"source_aliases": {"sumologic": "checkout-api", "datadog": "checkout-api"}},
        )
        self.repository.upsert_resources([resource])
        points = []
        for offset in range(24, -1, -1):
            timestamp = FIXED_NOW - timedelta(hours=offset)
            points.append(
                NormalizedMetricPoint(
                    resource_id=resource.resource_id,
                    metric_name="ec2_cpu_percent",
                    timestamp_utc=timestamp,
                    value=22.0,
                    unit="percent",
                    source="sumologic",
                    dimensions={"autoscaling_group": "checkout-api"},
                ).to_record()
            )
            points.append(
                NormalizedMetricPoint(
                    resource_id=resource.resource_id,
                    metric_name="cpu_percent",
                    timestamp_utc=timestamp,
                    value=35.0,
                    unit="percent",
                    source="datadog",
                    dimensions={"service": "checkout-api"},
                ).to_record()
            )
        self.repository.upsert_metrics(points)

        self.service.run_analysis(now=FIXED_NOW)
        with self.repository.connect() as connection:
            snapshot_row = connection.execute(
                "SELECT computed_features_json FROM analysis_snapshots ORDER BY created_at_utc DESC LIMIT 1;"
            ).fetchone()
        self.assertTrue(snapshot_row)
        features = json.loads(snapshot_row["computed_features_json"])
        self.assertEqual(features["primary_source"], "sumologic")

    def test_composite_pressure_score_can_drive_scale_up_recommendation(self) -> None:
        self.settings.analysis_window_days = 1
        self.settings.minimum_history_days = 1
        resource = Resource(
            resource_id="app-pressure",
            name="permitvision-pressure",
            resource_type="app_service",
            environment="production",
            current_size="large",
            metadata={"source_aliases": {"sumologic": "permitvision-pressure"}},
        )
        metrics = []
        for offset in range(24, -1, -1):
            timestamp = FIXED_NOW - timedelta(hours=offset)
            metrics.extend(
                [
                    NormalizedMetricPoint(
                        resource_id=resource.resource_id,
                        metric_name="ec2_cpu_percent",
                        timestamp_utc=timestamp,
                        value=82.0,
                        unit="percent",
                        source="sumologic",
                        dimensions={},
                    ).to_record(),
                    NormalizedMetricPoint(
                        resource_id=resource.resource_id,
                        metric_name="memory_percent",
                        timestamp_utc=timestamp,
                        value=86.0,
                        unit="percent",
                        source="sumologic",
                        dimensions={},
                    ).to_record(),
                    NormalizedMetricPoint(
                        resource_id=resource.resource_id,
                        metric_name="target_response_time_ms",
                        timestamp_utc=timestamp,
                        value=245.0,
                        unit="milliseconds",
                        source="sumologic",
                        dimensions={},
                    ).to_record(),
                ]
            )

        features, tags, _ = build_analysis_snapshot(
            resource,
            metrics,
            FIXED_NOW - timedelta(days=1),
            FIXED_NOW,
            self.settings,
        )
        recommendation = generate_recommendation(resource, features, tags, self.settings)

        self.assertGreaterEqual(features["pressure_score"], 55.0)
        self.assertIn(features["pressure_band"], {"elevated", "high"})
        self.assertTrue({"elevated_composite_pressure", "high_composite_pressure"} & set(tags))
        self.assertEqual(recommendation.recommendation_type, RecommendationType.SCALE_UP.value)

    def test_ewma_cusum_flags_short_lived_anomaly(self) -> None:
        self.settings.analysis_window_days = 1
        self.settings.minimum_history_days = 1
        resource = Resource(
            resource_id="app-anomaly",
            name="permitvision-anomaly",
            resource_type="app_service",
            environment="production",
            current_size="large",
            metadata={"source_aliases": {"sumologic": "permitvision-anomaly"}},
        )
        metrics = []
        for offset in range(29, -1, -1):
            timestamp = FIXED_NOW - timedelta(hours=offset)
            response_time = 100.0 if offset >= 10 else 1000.0
            metrics.extend(
                [
                    NormalizedMetricPoint(
                        resource_id=resource.resource_id,
                        metric_name="ec2_cpu_percent",
                        timestamp_utc=timestamp,
                        value=30.0,
                        unit="percent",
                        source="sumologic",
                        dimensions={},
                    ).to_record(),
                    NormalizedMetricPoint(
                        resource_id=resource.resource_id,
                        metric_name="target_response_time_ms",
                        timestamp_utc=timestamp,
                        value=response_time,
                        unit="milliseconds",
                        source="sumologic",
                        dimensions={},
                    ).to_record(),
                ]
            )

        features, tags, _ = build_analysis_snapshot(
            resource,
            metrics,
            FIXED_NOW - timedelta(days=1),
            FIXED_NOW,
            self.settings,
        )
        recommendation = generate_recommendation(resource, features, tags, self.settings)

        self.assertGreater(features["anomaly_count"], 0)
        self.assertIn("short_lived_anomaly", tags)
        self.assertNotEqual(recommendation.recommendation_type, RecommendationType.SCALE_DOWN.value)


if __name__ == "__main__":
    unittest.main()
