from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from app.config import Settings
from app.connectors.sumologic import SumoLogicConnector
from app.service import CapacityIntelligenceService
from app.storage import Repository


def main() -> None:
    settings = Settings()
    settings.ensure_directories()
    repository = Repository(settings.db_path)
    try:
        service = CapacityIntelligenceService(repository, settings, connectors=[SumoLogicConnector(settings)])
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        ingestion = service.run_ingestion(window_days=settings.analysis_window_days, now=now)
        analysis = service.run_analysis(now=now)
        report = service.latest_report()
        recommendations = service.list_recommendations(environment="production")
        all_recommendations = service.list_recommendations()

        counts: dict[str, int] = {}
        risk_counts: dict[str, int] = {}
        for item in recommendations:
            counts[item["recommendation_type"]] = counts.get(item["recommendation_type"], 0) + 1
            risk_counts[item["risk_level"]] = risk_counts.get(item["risk_level"], 0) + 1

        watch_items = [
            item for item in recommendations if item["recommendation_type"] in {"watchlist", "scale_up"}
        ]
        scale_down = [item for item in recommendations if item["recommendation_type"] == "scale_down"]
        insufficient = [item for item in recommendations if item["recommendation_type"] == "insufficient_data"]

        report_path = Path("C:/capacity-intelligence-agentic/data/capacity_report_sumo_scored_30d.md")
        lines = [
            "# Sumo Production Capacity Report",
            "",
            f"Generated UTC: {now.isoformat()}",
            f"Database: {settings.db_path}",
            "",
            "## Run Summary",
            f"- Ingestion window: {ingestion['window_start_utc']} to {ingestion['window_end_utc']}",
            f"- Analysis queued resources: {analysis['queued_resources']}",
        ]
        for source in ingestion["source_run_status"]:
            lines.append(
                "- Source "
                f"{source['source']}: status={source['status']}, payloads={source['payload_count']}, "
                f"normalized={source['normalized_count']}, issues={source['issue_count']}"
            )

        lines.extend(["", "## Production Recommendation Mix"])
        for key in sorted(counts):
            lines.append(f"- {key}: {counts[key]}")
        lines.append(f"- total production recommendations: {len(recommendations)}")

        lines.extend(["", "## Production Risk Mix"])
        for key in sorted(risk_counts):
            lines.append(f"- {key}: {risk_counts[key]}")

        lines.extend(["", "## Portfolio Summary", report["summary_text"] if report else "No report generated."])

        lines.extend(["", "## Top Risk Hotspots"])
        for item in watch_items[:15]:
            lines.append(
                f"- {item['resource_name']}: {item['recommendation_type']}, "
                f"confidence={item['confidence']}, risk={item['risk_level']}"
            )
            lines.append(f"  Evidence: {'; '.join(item['evidence_json'][:3])}")

        lines.extend(["", "## Scale Down Candidates"])
        for item in scale_down[:15]:
            savings = item["estimated_monthly_savings"] if item["estimated_monthly_savings"] is not None else "n/a"
            lines.append(
                f"- {item['resource_name']}: {item['current_size']} -> {item['suggested_size']}, "
                f"savings={savings}, confidence={item['confidence']}"
            )
            lines.append(f"  Evidence: {'; '.join(item['evidence_json'][:3])}")

        lines.extend(["", "## Insufficient Data Examples"])
        for item in insufficient[:15]:
            lines.append(f"- {item['resource_name']}: {item['report_summary']}")
        report_path.write_text("\n".join(lines), encoding="utf-8")

        print(
            json.dumps(
                {
                    "db_path": str(settings.db_path),
                    "report_path": str(report_path),
                    "ingestion": ingestion,
                    "analysis": analysis,
                    "production_recommendation_counts": counts,
                    "production_risk_counts": risk_counts,
                    "production_recommendation_count": len(recommendations),
                    "all_recommendation_count": len(all_recommendations),
                    "latest_report_summary": report["summary_text"] if report else None,
                    "top_risk_hotspots": [item["resource_name"] for item in watch_items[:5]],
                    "top_scale_down": [item["resource_name"] for item in scale_down[:5]],
                    "top_insufficient": [item["resource_name"] for item in insufficient[:5]],
                },
                indent=2,
                sort_keys=True,
            )
        )
    finally:
        repository.close()


if __name__ == "__main__":
    main()
