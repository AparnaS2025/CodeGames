from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
import json
from typing import Any


class ResourceType(str, Enum):
    APP_SERVICE = "app_service"
    DB_INSTANCE = "db_instance"


class RecommendationType(str, Enum):
    SCALE_DOWN = "scale_down"
    HOLD = "hold"
    WATCHLIST = "watchlist"
    SCALE_UP = "scale_up"
    INSUFFICIENT_DATA = "insufficient_data"


class RecommendationStatus(str, Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    REJECTED = "rejected"
    SNOOZED = "snoozed"


class ReviewDecisionType(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    SNOOZE = "snooze"


@dataclass(slots=True)
class Resource:
    resource_id: str
    name: str
    resource_type: str
    environment: str
    current_size: str
    metadata: dict[str, Any] = field(default_factory=dict)
    active: bool = True

    def to_record(self) -> dict[str, Any]:
        return {
            "resource_id": self.resource_id,
            "name": self.name,
            "resource_type": self.resource_type,
            "environment": self.environment,
            "current_size": self.current_size,
            "metadata_json": json.dumps(self.metadata, sort_keys=True),
            "active": int(self.active),
        }

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["metadata_json"] = payload.pop("metadata")
        return payload


@dataclass(slots=True)
class RawMetricPayload:
    source: str
    external_resource_id: str
    metric_name: str
    timestamp: datetime
    value: float
    unit: str
    dimensions: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class NormalizedMetricPoint:
    resource_id: str
    metric_name: str
    timestamp_utc: datetime
    value: float
    unit: str
    source: str
    dimensions: dict[str, Any] = field(default_factory=dict)

    def to_record(self) -> dict[str, Any]:
        return {
            "resource_id": self.resource_id,
            "metric_name": self.metric_name,
            "timestamp_utc": self.timestamp_utc.isoformat(),
            "value": self.value,
            "unit": self.unit,
            "source": self.source,
            "dimensions_json": json.dumps(self.dimensions, sort_keys=True),
        }


@dataclass(slots=True)
class AnalysisSnapshot:
    snapshot_id: str
    resource_id: str
    window_start_utc: datetime
    window_end_utc: datetime
    source_freshness: dict[str, Any]
    computed_features: dict[str, Any]
    pattern_candidates: list[str]

    def to_record(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "resource_id": self.resource_id,
            "window_start_utc": self.window_start_utc.isoformat(),
            "window_end_utc": self.window_end_utc.isoformat(),
            "source_freshness_json": json.dumps(self.source_freshness, sort_keys=True),
            "computed_features_json": json.dumps(self.computed_features, sort_keys=True),
            "pattern_candidates_json": json.dumps(self.pattern_candidates),
        }


@dataclass(slots=True)
class Recommendation:
    recommendation_id: str
    resource_id: str
    recommendation_type: str
    current_size: str
    suggested_size: str | None
    confidence: str
    risk_level: str
    estimated_monthly_savings: float | None
    evidence: list[str]
    guardrails: list[str]
    pattern_summary: str
    report_summary: str
    status: str = RecommendationStatus.DRAFT.value
    created_at_utc: datetime | None = None

    def to_record(self) -> dict[str, Any]:
        return {
            "recommendation_id": self.recommendation_id,
            "resource_id": self.resource_id,
            "recommendation_type": self.recommendation_type,
            "current_size": self.current_size,
            "suggested_size": self.suggested_size,
            "confidence": self.confidence,
            "risk_level": self.risk_level,
            "estimated_monthly_savings": self.estimated_monthly_savings,
            "evidence_json": json.dumps(self.evidence),
            "guardrails_json": json.dumps(self.guardrails),
            "pattern_summary": self.pattern_summary,
            "report_summary": self.report_summary,
            "status": self.status,
            "created_at_utc": (self.created_at_utc or datetime.utcnow()).isoformat(),
        }


@dataclass(slots=True)
class ReviewDecision:
    review_id: str
    recommendation_id: str
    decision: str
    reviewer: str
    comment: str | None
    created_at_utc: datetime

    def to_record(self) -> dict[str, Any]:
        return {
            "review_id": self.review_id,
            "recommendation_id": self.recommendation_id,
            "decision": self.decision,
            "reviewer": self.reviewer,
            "comment": self.comment,
            "created_at_utc": self.created_at_utc.isoformat(),
        }


@dataclass(slots=True)
class ReportSnapshot:
    report_id: str
    report_type: str
    created_at_utc: datetime
    scope: dict[str, Any]
    summary_text: str
    details: dict[str, Any]

    def to_record(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "report_type": self.report_type,
            "created_at_utc": self.created_at_utc.isoformat(),
            "scope_json": json.dumps(self.scope, sort_keys=True),
            "summary_text": self.summary_text,
            "details_json": json.dumps(self.details, sort_keys=True),
        }

