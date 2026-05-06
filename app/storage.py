from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from typing import Any

from app.models import AnalysisSnapshot, Recommendation, ReportSnapshot, Resource, ReviewDecision


class Repository:
    def __init__(self, db_path: Path | str) -> None:
        self.db_path = db_path
        if isinstance(db_path, Path):
            db_path.parent.mkdir(parents=True, exist_ok=True)
            connection_target = str(db_path)
            use_uri = False
        else:
            connection_target = db_path
            use_uri = db_path.startswith("file:")
        self._connection = sqlite3.connect(connection_target, check_same_thread=False, uri=use_uri)
        self._connection.row_factory = sqlite3.Row
        self._configure_connection(connection_target)
        self._initialize()

    @contextmanager
    def connect(self) -> Any:
        try:
            yield self._connection
            self._connection.commit()
        finally:
            pass

    def _initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS resources (
                    resource_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    resource_type TEXT NOT NULL,
                    environment TEXT NOT NULL,
                    current_size TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS source_cursors (
                    source TEXT PRIMARY KEY,
                    last_window_start_utc TEXT NOT NULL,
                    last_window_end_utc TEXT NOT NULL,
                    last_success_at_utc TEXT NOT NULL,
                    health_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS normalized_metrics (
                    metric_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    resource_id TEXT NOT NULL,
                    metric_name TEXT NOT NULL,
                    timestamp_utc TEXT NOT NULL,
                    value REAL NOT NULL,
                    unit TEXT NOT NULL,
                    source TEXT NOT NULL,
                    dimensions_json TEXT NOT NULL,
                    UNIQUE(resource_id, metric_name, timestamp_utc, source)
                );
                CREATE INDEX IF NOT EXISTS idx_normalized_metrics_resource_window
                    ON normalized_metrics(resource_id, timestamp_utc);

                CREATE TABLE IF NOT EXISTS ingestion_issues (
                    issue_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    external_resource_id TEXT NOT NULL,
                    metric_name TEXT NOT NULL,
                    timestamp_utc TEXT NOT NULL,
                    issue_type TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS analysis_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    resource_id TEXT NOT NULL,
                    window_start_utc TEXT NOT NULL,
                    window_end_utc TEXT NOT NULL,
                    source_freshness_json TEXT NOT NULL,
                    computed_features_json TEXT NOT NULL,
                    pattern_candidates_json TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS recommendations (
                    recommendation_id TEXT PRIMARY KEY,
                    resource_id TEXT NOT NULL,
                    recommendation_type TEXT NOT NULL,
                    current_size TEXT NOT NULL,
                    suggested_size TEXT,
                    confidence TEXT NOT NULL,
                    risk_level TEXT NOT NULL,
                    estimated_monthly_savings REAL,
                    evidence_json TEXT NOT NULL,
                    guardrails_json TEXT NOT NULL,
                    pattern_summary TEXT NOT NULL,
                    report_summary TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS review_decisions (
                    review_id TEXT PRIMARY KEY,
                    recommendation_id TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    reviewer TEXT NOT NULL,
                    comment TEXT,
                    created_at_utc TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS report_snapshots (
                    report_id TEXT PRIMARY KEY,
                    report_type TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    scope_json TEXT NOT NULL,
                    summary_text TEXT NOT NULL,
                    details_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS capacity_runs (
                    run_id TEXT PRIMARY KEY,
                    run_type TEXT NOT NULL,
                    idempotency_key TEXT,
                    status TEXT NOT NULL,
                    started_at_utc TEXT NOT NULL,
                    completed_at_utc TEXT,
                    request_json TEXT NOT NULL,
                    result_json TEXT,
                    error_json TEXT
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_capacity_runs_type_idempotency
                    ON capacity_runs(run_type, idempotency_key)
                    WHERE idempotency_key IS NOT NULL;
                """
            )

    def _configure_connection(self, connection_target: str) -> None:
        with self.connect() as connection:
            connection.execute("PRAGMA busy_timeout = 5000;")
            if connection_target != ":memory:" and "mode=ro" not in connection_target:
                # The local workspace rejects SQLite's disk rollback/WAL journal path. MEMORY journaling
                # avoids that filesystem edge case without taking an exclusive process-wide DB lock.
                connection.execute("PRAGMA journal_mode = MEMORY;")

    def upsert_resources(self, resources: list[Resource]) -> None:
        with self.connect() as connection:
            connection.executemany(
                """
                INSERT INTO resources (
                    resource_id, name, resource_type, environment, current_size, metadata_json, active
                )
                VALUES (
                    :resource_id, :name, :resource_type, :environment, :current_size, :metadata_json, :active
                )
                ON CONFLICT(resource_id) DO UPDATE SET
                    name = excluded.name,
                    resource_type = excluded.resource_type,
                    environment = excluded.environment,
                    current_size = excluded.current_size,
                    metadata_json = excluded.metadata_json,
                    active = excluded.active,
                    updated_at_utc = CURRENT_TIMESTAMP;
                """,
                [resource.to_record() for resource in resources],
            )

    def upsert_metrics(self, points: list[dict[str, Any]]) -> int:
        with self.connect() as connection:
            before = connection.total_changes
            connection.executemany(
                """
                INSERT OR IGNORE INTO normalized_metrics (
                    resource_id, metric_name, timestamp_utc, value, unit, source, dimensions_json
                )
                VALUES (
                    :resource_id, :metric_name, :timestamp_utc, :value, :unit, :source, :dimensions_json
                );
                """,
                points,
            )
            return connection.total_changes - before

    def record_ingestion_issues(self, issues: list[dict[str, Any]]) -> None:
        if not issues:
            return
        with self.connect() as connection:
            connection.executemany(
                """
                INSERT INTO ingestion_issues (
                    source, external_resource_id, metric_name, timestamp_utc, issue_type, details_json
                )
                VALUES (
                    :source, :external_resource_id, :metric_name, :timestamp_utc, :issue_type, :details_json
                );
                """,
                [
                    {
                        **issue,
                        "details_json": json.dumps(issue["details"], sort_keys=True),
                    }
                    for issue in issues
                ],
            )

    def save_source_cursor(
        self,
        source: str,
        window_start_utc: str,
        window_end_utc: str,
        last_success_at_utc: str,
        health: dict[str, Any],
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO source_cursors (
                    source, last_window_start_utc, last_window_end_utc, last_success_at_utc, health_json
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source) DO UPDATE SET
                    last_window_start_utc = excluded.last_window_start_utc,
                    last_window_end_utc = excluded.last_window_end_utc,
                    last_success_at_utc = excluded.last_success_at_utc,
                    health_json = excluded.health_json;
                """,
                (
                    source,
                    window_start_utc,
                    window_end_utc,
                    last_success_at_utc,
                    json.dumps(health, sort_keys=True),
                ),
            )

    def start_capacity_run(
        self,
        run_id: str,
        run_type: str,
        idempotency_key: str | None,
        started_at_utc: str,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO capacity_runs (
                    run_id, run_type, idempotency_key, status, started_at_utc, request_json
                )
                VALUES (?, ?, ?, ?, ?, ?);
                """,
                (
                    run_id,
                    run_type,
                    idempotency_key,
                    "running",
                    started_at_utc,
                    json.dumps(request, sort_keys=True),
                ),
            )
        run = self.get_capacity_run(run_id)
        if run is None:
            raise RuntimeError(f"capacity run {run_id} was not persisted")
        return run

    def complete_capacity_run(
        self,
        run_id: str,
        completed_at_utc: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE capacity_runs
                SET status = ?, completed_at_utc = ?, result_json = ?, error_json = NULL
                WHERE run_id = ?;
                """,
                ("completed", completed_at_utc, json.dumps(result, sort_keys=True), run_id),
            )
        run = self.get_capacity_run(run_id)
        if run is None:
            raise RuntimeError(f"capacity run {run_id} was not found after completion")
        return run

    def fail_capacity_run(
        self,
        run_id: str,
        completed_at_utc: str,
        error: dict[str, Any],
    ) -> dict[str, Any]:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE capacity_runs
                SET status = ?, completed_at_utc = ?, error_json = ?
                WHERE run_id = ?;
                """,
                ("failed", completed_at_utc, json.dumps(error, sort_keys=True), run_id),
            )
        run = self.get_capacity_run(run_id)
        if run is None:
            raise RuntimeError(f"capacity run {run_id} was not found after failure")
        return run

    def get_capacity_run(self, run_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT run_id, run_type, idempotency_key, status, started_at_utc, completed_at_utc,
                       request_json, result_json, error_json
                FROM capacity_runs
                WHERE run_id = ?;
                """,
                (run_id,),
            ).fetchone()
        return self._capacity_run_row_to_dict(row) if row else None

    def get_capacity_run_by_idempotency_key(self, run_type: str, idempotency_key: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT run_id, run_type, idempotency_key, status, started_at_utc, completed_at_utc,
                       request_json, result_json, error_json
                FROM capacity_runs
                WHERE run_type = ?
                  AND idempotency_key = ?;
                """,
                (run_type, idempotency_key),
            ).fetchone()
        return self._capacity_run_row_to_dict(row) if row else None

    def list_capacity_runs(self, run_type: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        if run_type:
            query = """
                SELECT run_id, run_type, idempotency_key, status, started_at_utc, completed_at_utc,
                       request_json, result_json, error_json
                FROM capacity_runs
                WHERE run_type = ?
                ORDER BY started_at_utc DESC
                LIMIT ?;
            """
            params: tuple[Any, ...] = (run_type, limit)
        else:
            query = """
                SELECT run_id, run_type, idempotency_key, status, started_at_utc, completed_at_utc,
                       request_json, result_json, error_json
                FROM capacity_runs
                ORDER BY started_at_utc DESC
                LIMIT ?;
            """
            params = (limit,)
        with self.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._capacity_run_row_to_dict(row) for row in rows]

    def list_resources(
        self,
        environment: str | None = None,
        resource_type: str | None = None,
        active_only: bool = False,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if environment:
            clauses.append("environment = ?")
            params.append(environment)
        if resource_type:
            clauses.append("resource_type = ?")
            params.append(resource_type)
        if active_only:
            clauses.append("active = 1")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT resource_id, name, resource_type, environment, current_size, metadata_json, active
                FROM resources
                {where}
                ORDER BY environment, name;
                """,
                params,
            ).fetchall()
        return [self._resource_row_to_dict(row) for row in rows]

    def get_resource(self, resource_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT resource_id, name, resource_type, environment, current_size, metadata_json, active
                FROM resources
                WHERE resource_id = ?;
                """,
                (resource_id,),
            ).fetchone()
        return self._resource_row_to_dict(row) if row else None

    def get_metrics(self, resource_id: str, window_start: str, window_end: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT resource_id, metric_name, timestamp_utc, value, unit, source, dimensions_json
                FROM normalized_metrics
                WHERE resource_id = ?
                  AND timestamp_utc BETWEEN ? AND ?
                ORDER BY timestamp_utc ASC;
                """,
                (resource_id, window_start, window_end),
            ).fetchall()
        return [
            {
                "resource_id": row["resource_id"],
                "metric_name": row["metric_name"],
                "timestamp_utc": row["timestamp_utc"],
                "value": row["value"],
                "unit": row["unit"],
                "source": row["source"],
                "dimensions_json": json.loads(row["dimensions_json"]),
            }
            for row in rows
        ]

    def get_metrics_for_resources(
        self,
        resource_ids: list[str],
        window_start: str,
        window_end: str,
        chunk_size: int = 400,
    ) -> dict[str, list[dict[str, Any]]]:
        if not resource_ids:
            return {}

        grouped: dict[str, list[dict[str, Any]]] = {}
        with self.connect() as connection:
            for chunk_start in range(0, len(resource_ids), chunk_size):
                chunk = resource_ids[chunk_start : chunk_start + chunk_size]
                placeholders = ",".join("?" for _ in chunk)
                rows = connection.execute(
                    f"""
                    SELECT resource_id, metric_name, timestamp_utc, value, unit, source, dimensions_json
                    FROM normalized_metrics
                    WHERE resource_id IN ({placeholders})
                      AND timestamp_utc BETWEEN ? AND ?
                    ORDER BY resource_id ASC, timestamp_utc ASC;
                    """,
                    [*chunk, window_start, window_end],
                ).fetchall()
                for row in rows:
                    grouped.setdefault(str(row["resource_id"]), []).append(
                        {
                            "resource_id": row["resource_id"],
                            "metric_name": row["metric_name"],
                            "timestamp_utc": row["timestamp_utc"],
                            "value": row["value"],
                            "unit": row["unit"],
                            "source": row["source"],
                            "dimensions_json": json.loads(row["dimensions_json"]),
                        }
                    )
        return grouped

    def find_resource_ids_by_dimension(
        self,
        source: str,
        dimension_key: str,
        dimension_values: list[str],
        window_start: str,
        window_end: str,
    ) -> set[str]:
        requested = set(dimension_values)
        if not requested:
            return set()
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT resource_id, dimensions_json
                FROM normalized_metrics
                WHERE source = ?
                  AND timestamp_utc BETWEEN ? AND ?;
                """,
                (source, window_start, window_end),
            ).fetchall()
        matched: set[str] = set()
        for row in rows:
            dimensions = json.loads(row["dimensions_json"])
            if dimensions.get(dimension_key) in requested:
                matched.add(str(row["resource_id"]))
        return matched

    def save_analysis_snapshot(self, snapshot: AnalysisSnapshot) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO analysis_snapshots (
                    snapshot_id, resource_id, window_start_utc, window_end_utc, source_freshness_json,
                    computed_features_json, pattern_candidates_json
                )
                VALUES (
                    :snapshot_id, :resource_id, :window_start_utc, :window_end_utc, :source_freshness_json,
                    :computed_features_json, :pattern_candidates_json
                );
                """,
                snapshot.to_record(),
            )

    def save_recommendation(self, recommendation: Recommendation) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO recommendations (
                    recommendation_id, resource_id, recommendation_type, current_size, suggested_size,
                    confidence, risk_level, estimated_monthly_savings, evidence_json, guardrails_json,
                    pattern_summary, report_summary, status, created_at_utc
                )
                VALUES (
                    :recommendation_id, :resource_id, :recommendation_type, :current_size, :suggested_size,
                    :confidence, :risk_level, :estimated_monthly_savings, :evidence_json, :guardrails_json,
                    :pattern_summary, :report_summary, :status, :created_at_utc
                );
                """,
                recommendation.to_record(),
            )

    def list_recommendations(
        self,
        recommendation_type: str | None = None,
        confidence: str | None = None,
        status: str | None = None,
        environment: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if recommendation_type:
            clauses.append("r.recommendation_type = ?")
            params.append(recommendation_type)
        if confidence:
            clauses.append("r.confidence = ?")
            params.append(confidence)
        if status:
            clauses.append("r.status = ?")
            params.append(status)
        if environment:
            clauses.append("resource.environment = ?")
            params.append(environment)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT r.*, resource.name, resource.resource_type, resource.environment
                FROM recommendations r
                JOIN resources resource ON resource.resource_id = r.resource_id
                {where}
                ORDER BY r.created_at_utc DESC, resource.name ASC;
                """,
                params,
            ).fetchall()
        return [self._recommendation_row_to_dict(row) for row in rows]

    def get_recommendation(self, recommendation_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT r.*, resource.name, resource.resource_type, resource.environment
                FROM recommendations r
                JOIN resources resource ON resource.resource_id = r.resource_id
                WHERE r.recommendation_id = ?;
                """,
                (recommendation_id,),
            ).fetchone()
        return self._recommendation_row_to_dict(row) if row else None

    def get_latest_recommendation_for_resource(self, resource_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT r.*, resource.name, resource.resource_type, resource.environment
                FROM recommendations r
                JOIN resources resource ON resource.resource_id = r.resource_id
                WHERE r.resource_id = ?
                ORDER BY r.created_at_utc DESC
                LIMIT 1;
                """,
                (resource_id,),
            ).fetchone()
        return self._recommendation_row_to_dict(row) if row else None

    def save_review_decision(self, review: ReviewDecision) -> ReviewDecision:
        latest = self.get_latest_review_for_recommendation(review.recommendation_id)
        if latest and latest["decision"] == review.decision and latest["reviewer"] == review.reviewer and latest["comment"] == review.comment:
            return ReviewDecision(
                review_id=latest["review_id"],
                recommendation_id=latest["recommendation_id"],
                decision=latest["decision"],
                reviewer=latest["reviewer"],
                comment=latest["comment"],
                created_at_utc=datetime.fromisoformat(latest["created_at_utc"]),
            )

        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO review_decisions (
                    review_id, recommendation_id, decision, reviewer, comment, created_at_utc
                )
                VALUES (
                    :review_id, :recommendation_id, :decision, :reviewer, :comment, :created_at_utc
                );
                """,
                review.to_record(),
            )
            connection.execute(
                """
                UPDATE recommendations
                SET status = ?
                WHERE recommendation_id = ?;
                """,
                (self._status_for_decision(review.decision), review.recommendation_id),
            )
        return review

    def list_reviews_for_recommendation(self, recommendation_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT review_id, recommendation_id, decision, reviewer, comment, created_at_utc
                FROM review_decisions
                WHERE recommendation_id = ?
                ORDER BY created_at_utc ASC;
                """,
                (recommendation_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_latest_review_for_recommendation(self, recommendation_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT review_id, recommendation_id, decision, reviewer, comment, created_at_utc
                FROM review_decisions
                WHERE recommendation_id = ?
                ORDER BY created_at_utc DESC
                LIMIT 1;
                """,
                (recommendation_id,),
            ).fetchone()
        return dict(row) if row else None

    def save_report(self, report: ReportSnapshot) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO report_snapshots (
                    report_id, report_type, created_at_utc, scope_json, summary_text, details_json
                )
                VALUES (
                    :report_id, :report_type, :created_at_utc, :scope_json, :summary_text, :details_json
                );
                """,
                report.to_record(),
            )

    def get_latest_report(self) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT report_id, report_type, created_at_utc, scope_json, summary_text, details_json
                FROM report_snapshots
                ORDER BY created_at_utc DESC
                LIMIT 1;
                """
            ).fetchone()
        if not row:
            return None
        return {
            "report_id": row["report_id"],
            "report_type": row["report_type"],
            "created_at_utc": row["created_at_utc"],
            "scope_json": json.loads(row["scope_json"]),
            "summary_text": row["summary_text"],
            "details_json": json.loads(row["details_json"]),
        }

    def count_metrics(self) -> int:
        with self.connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS metric_count FROM normalized_metrics;").fetchone()
        return int(row["metric_count"])

    def list_ingestion_issues(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT issue_id, source, external_resource_id, metric_name, timestamp_utc, issue_type, details_json
                FROM ingestion_issues
                ORDER BY created_at_utc DESC;
                """
            ).fetchall()
        return [
            {
                "issue_id": row["issue_id"],
                "source": row["source"],
                "external_resource_id": row["external_resource_id"],
                "metric_name": row["metric_name"],
                "timestamp_utc": row["timestamp_utc"],
                "issue_type": row["issue_type"],
                "details_json": json.loads(row["details_json"]),
            }
            for row in rows
        ]

    def close(self) -> None:
        self._connection.close()

    @staticmethod
    def _status_for_decision(decision: str) -> str:
        return {
            "approve": "approved",
            "reject": "rejected",
            "snooze": "snoozed",
        }[decision]

    @staticmethod
    def _resource_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "resource_id": row["resource_id"],
            "name": row["name"],
            "resource_type": row["resource_type"],
            "environment": row["environment"],
            "current_size": row["current_size"],
            "metadata_json": json.loads(row["metadata_json"]),
            "active": bool(row["active"]),
        }

    @staticmethod
    def _recommendation_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "recommendation_id": row["recommendation_id"],
            "resource_id": row["resource_id"],
            "resource_name": row["name"],
            "resource_type": row["resource_type"],
            "environment": row["environment"],
            "recommendation_type": row["recommendation_type"],
            "current_size": row["current_size"],
            "suggested_size": row["suggested_size"],
            "confidence": row["confidence"],
            "risk_level": row["risk_level"],
            "estimated_monthly_savings": row["estimated_monthly_savings"],
            "evidence_json": json.loads(row["evidence_json"]),
            "guardrails_json": json.loads(row["guardrails_json"]),
            "pattern_summary": row["pattern_summary"],
            "report_summary": row["report_summary"],
            "status": row["status"],
            "created_at_utc": row["created_at_utc"],
        }

    @staticmethod
    def _capacity_run_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "run_id": row["run_id"],
            "run_type": row["run_type"],
            "idempotency_key": row["idempotency_key"],
            "status": row["status"],
            "started_at_utc": row["started_at_utc"],
            "completed_at_utc": row["completed_at_utc"],
            "request_json": json.loads(row["request_json"]),
            "result_json": json.loads(row["result_json"]) if row["result_json"] else None,
            "error_json": json.loads(row["error_json"]) if row["error_json"] else None,
        }
