from __future__ import annotations

from datetime import datetime

from app.connectors.base import ConnectorFetchResult
from app.sample_data import generate_datadog_payloads


class DatadogConnector:
    source_name = "datadog"

    def fetch(self, window_start: datetime, window_end: datetime) -> ConnectorFetchResult:
        payloads = generate_datadog_payloads(window_start, window_end)
        return ConnectorFetchResult(
            payloads=payloads,
            health={
                "status": "healthy",
                "mode": "sample",
                "payload_count": len(payloads),
            },
        )

