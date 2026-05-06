from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import os


def _default_db_path() -> Path:
    configured = os.getenv("CAPACITY_DB_PATH")
    if configured:
        return Path(configured)
    return Path.cwd() / "data" / "capacity_intelligence.db"


@dataclass(slots=True)
class Settings:
    db_path: Path | str = field(default_factory=_default_db_path)
    analysis_window_days: int = field(default_factory=lambda: int(os.getenv("ANALYSIS_WINDOW_DAYS", "30")))
    metric_resolution_minutes: int = field(default_factory=lambda: int(os.getenv("METRIC_RESOLUTION_MINUTES", "5")))
    minimum_history_days: int = field(default_factory=lambda: int(os.getenv("MINIMUM_HISTORY_DAYS", "7")))
    stale_after_hours: int = field(default_factory=lambda: int(os.getenv("STALE_AFTER_HOURS", "6")))
    sumologic_api_url: str = field(default_factory=lambda: os.getenv("SUMOLOGIC_API_URL", "").rstrip("/"))
    sumologic_access_id: str = field(default_factory=lambda: os.getenv("SUMOLOGIC_ACCESS_ID", ""))
    sumologic_access_key: str = field(default_factory=lambda: os.getenv("SUMOLOGIC_ACCESS_KEY", ""))
    sumologic_query_window_minutes: int = field(
        default_factory=lambda: int(os.getenv("SUMOLOGIC_QUERY_WINDOW_MINUTES", "30"))
    )
    sumologic_max_query_window_days: int = field(
        default_factory=lambda: int(os.getenv("SUMOLOGIC_MAX_QUERY_WINDOW_DAYS", "1"))
    )
    sumologic_metrics_source_category: str = field(
        default_factory=lambda: os.getenv("SUMOLOGIC_METRICS_SOURCE_CATEGORY", "production/cloudwatch/metrics")
    )
    sumologic_metrics_application_name: str = field(
        default_factory=lambda: os.getenv("SUMOLOGIC_METRICS_APPLICATION_NAME", "enablon vision platform v9")
    )
    sumologic_metrics_autoscaling_group_pattern: str = field(
        default_factory=lambda: os.getenv("SUMOLOGIC_METRICS_AUTOSCALING_GROUP_PATTERN", "*pv*")
    )
    sumologic_http_logs_source_category_pattern: str = field(
        default_factory=lambda: os.getenv("SUMOLOGIC_HTTP_LOGS_SOURCE_CATEGORY_PATTERN", "*permitvision/http")
    )
    sumologic_enable_http_log_queries: bool = field(
        default_factory=lambda: os.getenv("SUMOLOGIC_ENABLE_HTTP_LOG_QUERIES", "false").lower() == "true"
    )
    sumologic_max_workers: int = field(default_factory=lambda: int(os.getenv("SUMOLOGIC_MAX_WORKERS", "4")))
    sumologic_use_sample_data: bool = field(
        default_factory=lambda: os.getenv("SUMOLOGIC_USE_SAMPLE_DATA", "true").lower() != "false"
    )
    agent_enable_llm: bool = field(default_factory=lambda: os.getenv("AGENT_ENABLE_LLM", "false").lower() == "true")
    agent_max_tool_iterations: int = field(default_factory=lambda: int(os.getenv("AGENT_MAX_TOOL_ITERATIONS", "6")))
    azure_openai_endpoint: str = field(default_factory=lambda: os.getenv("AZURE_OPENAI_ENDPOINT", "").rstrip("/"))
    azure_openai_api_key: str = field(default_factory=lambda: os.getenv("AZURE_OPENAI_API_KEY", ""))
    azure_openai_api_version: str = field(default_factory=lambda: os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21"))
    azure_openai_deployment: str = field(default_factory=lambda: os.getenv("AZURE_OPENAI_DEPLOYMENT", ""))
    min_size_by_type: dict[str, str] = field(
        default_factory=lambda: {
            "app_service": "medium",
            "db_instance": "large",
        }
    )
    cost_profile: dict[str, dict[str, float]] = field(
        default_factory=lambda: {
            "app_service": {
                "small": 120.0,
                "medium": 220.0,
                "large": 360.0,
                "xlarge": 610.0,
            },
            "db_instance": {
                "medium": 520.0,
                "large": 840.0,
                "xlarge": 1320.0,
            },
        }
    )
    size_order: tuple[str, ...] = ("small", "medium", "large", "xlarge")

    def ensure_directories(self) -> None:
        if isinstance(self.db_path, Path):
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
