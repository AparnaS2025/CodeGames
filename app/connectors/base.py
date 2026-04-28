from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from app.models import RawMetricPayload


@dataclass(slots=True)
class ConnectorFetchResult:
    payloads: list[RawMetricPayload]
    health: dict[str, Any]
    issues: list[dict[str, Any]] = field(default_factory=list)


class SourceConnector(Protocol):
    source_name: str

    def fetch(self, window_start: datetime, window_end: datetime) -> ConnectorFetchResult:
        ...

