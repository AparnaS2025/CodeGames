from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from app.config import Settings
from app.models import Resource
from app.service import CapacityIntelligenceService
from app.storage import Repository


SOURCE_DB = Path("C:/capacity-intelligence-agentic/data/sumo_live_30d_run.db")
ANALYSIS_DB = Path("C:/capacity-intelligence-agentic/data/sumo_live_scored_report_30d.db")
REPORT_PATH = Path("C:/capacity-intelligence-agentic/data/capacity_report_sumo_scored_30d.md")


def _load_resources(connection: sqlite3.Connection) -> list[Resource]:
    rows = connection.execute(
        """
        SELECT resource_id, name, resource_type, environment, current_size, metadata_json, active
        FROM resources
        WHERE active = 1;
        """
    ).fetchall()
    return [
        Resource(
            resource_id=row["resource_id"],
            name=row["name"],
            resource_type=row["resource_type"],
            environment=row["environment"],
            current_size=row["current_size"],
            metadata=json.loads(row["metadata_json"]),
            active=bool(row["active"]),
        )
        for row in rows
    ]


def _load_metrics(connection: sqlite3.Connection) -> list[dict[str, object]]:
    rows = connection.execute(
        """
        SELECT resource_id, metric_name, timestamp_utc, value, unit, source, dimensions_json
        FROM normalized_metrics
        ORDER BY resource_id, timestamp_utc;
        """
    ).fetchall()
    return [dict(row) for row in rows]


def _write_report(
    service: CapacityIntelligenceService,
    analysis: dict[str, object],
    source_metric_count: int,
    now: datetime,
) -> dict[str, object]:
    report = service.latest_report()
    recommendations = service.list_recommendations(environment="production")
    all_recommendations = service.list_recommendations()

    counts: dict[str, int] = {}
    risk_counts: dict[str, int] = {}
    for item in recommendations:
        counts[item["recommendation_type"]] = counts.get(item["recommendation_type"], 0) + 1
        risk_counts[item["risk_level"]] = risk_counts.get(item["risk_level"], 0) + 1

    watch_items = [item for item in recommendations if item["recommendation_type"] in {"watchlist", "scale_up"}]
    scale_down = [item for item in recommendations if item["recommendation_type"] == "scale_down"]
    insufficient = [item for item in recommendations if item["recommendation_type"] == "insufficient_data"]

    lines = [
        "# Sumo Production Capacity Report",
        "",
        f"Generated UTC: {now.isoformat()}",
        f"Source database: {SOURCE_DB}",
        f"Analysis database: {ANALYSIS_DB}",
        "",
        "## Run Summary",
        f"- Source metrics loaded: {source_metric_count}",
        f"- Analysis queued resources: {analysis['queued_resources']}",
        "",
        "## Production Recommendation Mix",
    ]
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
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")

    return {
        "source_db": str(SOURCE_DB),
        "analysis_db": str(ANALYSIS_DB),
        "report_path": str(REPORT_PATH),
        "source_metric_count": source_metric_count,
        "analysis": analysis,
        "production_recommendation_counts": counts,
        "production_risk_counts": risk_counts,
        "production_recommendation_count": len(recommendations),
        "all_recommendation_count": len(all_recommendations),
        "latest_report_summary": report["summary_text"] if report else None,
        "top_risk_hotspots": [item["resource_name"] for item in watch_items[:5]],
        "top_scale_down": [item["resource_name"] for item in scale_down[:5]],
        "top_insufficient": [item["resource_name"] for item in insufficient[:5]],
    }


def main() -> None:
    settings = Settings(db_path=ANALYSIS_DB)
    settings.analysis_window_days = 30
    source = sqlite3.connect(f"file:{SOURCE_DB}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    repository = Repository(ANALYSIS_DB)
    try:
        resources = _load_resources(source)
        metrics = _load_metrics(source)
        repository.upsert_resources(resources)
        repository.upsert_metrics(metrics)
        service = CapacityIntelligenceService(repository, settings, connectors=[])
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        analysis = service.run_analysis(now=now)
        result = _write_report(service, analysis, len(metrics), now)
        print(json.dumps(result, indent=2, sort_keys=True))
    finally:
        repository.close()
        source.close()


if __name__ == "__main__":
    main()
