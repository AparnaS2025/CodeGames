from __future__ import annotations

from datetime import timezone
from typing import Any

from app.models import NormalizedMetricPoint, RawMetricPayload, Resource


CANONICAL_METRIC_MAP: dict[str, dict[str, tuple[str, str]]] = {
    "datadog": {
        "system.cpu.user": ("cpu_percent", "percent"),
        "system.mem.used.percent": ("memory_percent", "percent"),
        "service.request.rate": ("request_rate", "count"),
        "service.error.rate": ("error_rate", "percent"),
        "service.latency.p95.ms": ("latency_p95_ms", "milliseconds"),
        "container.restart.count": ("restart_count", "count"),
    },
    "cloudwatch": {
        "CPUUtilization": ("db_cpu_percent", "percent"),
        "DatabaseConnections": ("db_connections", "count"),
        "ReadLatency": ("db_read_latency_ms", "milliseconds"),
        "WriteLatency": ("db_write_latency_ms", "milliseconds"),
        "ReadWriteIOPS": ("db_iops", "count"),
        "StorageUsedGB": ("storage_used_gb", "gigabytes"),
    },
    "sumologic": {
        "app.error.rate": ("error_rate", "percent"),
        "app.error.count": ("app_error_count", "count"),
        "restart.count": ("restart_count", "count"),
        "db.slow_query.count": ("db_slow_query_count", "count"),
        "incident.count": ("incident_count", "count"),
        "sumo.ec2.cpu.utilization": ("ec2_cpu_percent", "percent"),
        "sumo.ec2.memory.utilization": ("memory_percent", "percent"),
        "sumo.ec2.available_memory.mbytes": ("available_memory_mbytes", "megabytes"),
        "sumo.ec2.network.in.bytes": ("network_in_bytes", "bytes"),
        "sumo.ec2.network.out.bytes": ("network_out_bytes", "bytes"),
        "sumo.ec2.ebs.read_ops": ("ebs_read_ops", "count"),
        "sumo.ec2.ebs.write_ops": ("ebs_write_ops", "count"),
        "sumo.alb.healthy_host_count": ("healthy_host_count", "count"),
        "sumo.alb.target_response_time.ms": ("target_response_time_ms", "milliseconds"),
        "sumo.rds.cpu.utilization": ("db_cpu_percent", "percent"),
        "sumo.rds.connections": ("db_connections", "count"),
        "sumo.rds.freeable_memory.bytes": ("db_freeable_memory_bytes", "bytes"),
        "sumo.rds.free_storage.bytes": ("db_free_storage_bytes", "bytes"),
        "sumo.rds.read_iops": ("db_read_iops", "count"),
        "sumo.rds.write_iops": ("db_write_iops", "count"),
        "sumo.rds.read_throughput.bytes_per_sec": ("db_read_throughput_bps", "bytes_per_second"),
        "sumo.rds.write_throughput.bytes_per_sec": ("db_write_throughput_bps", "bytes_per_second"),
        "sumo.rds.read_latency.ms": ("db_read_latency_ms", "milliseconds"),
        "sumo.rds.write_latency.ms": ("db_write_latency_ms", "milliseconds"),
        "sumo.http.call.count": ("request_count", "count"),
        "sumo.http.status.count": ("http_status_count", "count"),
        "sumo.http.request.size.bytes": ("http_request_size_bytes", "bytes"),
        "sumo.http.response.size.bytes": ("http_response_size_bytes", "bytes"),
        "sumo.http.duration.ms": ("http_duration_ms", "milliseconds"),
        "sumo.http.read_time.ms": ("http_read_time_ms", "milliseconds"),
        "sumo.http.write_time.ms": ("http_write_time_ms", "milliseconds"),
    },
}


def build_resource_resolution_index(resources: dict[str, Resource]) -> dict[str, str]:
    index: dict[str, str] = {}
    for resource in resources.values():
        index[resource.resource_id] = resource.resource_id
        index[resource.name] = resource.resource_id
        for source, alias in resource.metadata.get("source_aliases", {}).items():
            if alias:
                index[f"{source}:{alias}"] = resource.resource_id
    return index


def resolve_resource_id(payload: RawMetricPayload, resource_index: dict[str, str]) -> str | None:
    namespaced = f"{payload.source}:{payload.external_resource_id}"
    return resource_index.get(namespaced) or resource_index.get(payload.external_resource_id)


def _normalize_value(metric_name: str, value: float, unit: str) -> float:
    if unit == "fraction":
        return value * 100.0
    if unit == "ratio":
        return value * 100.0
    if unit.lower() == "seconds" and metric_name.endswith("_ms"):
        return value * 1000.0
    if "percent" in unit.lower():
        return max(min(value, 100.0), 0.0)
    if metric_name == "storage_used_gb" and unit.lower() == "bytes":
        return value / (1024.0**3)
    return value


def normalize_payloads(
    payloads: list[RawMetricPayload],
    resources: dict[str, Resource],
    resource_index: dict[str, str] | None = None,
) -> tuple[list[NormalizedMetricPoint], list[dict[str, Any]]]:
    normalized: list[NormalizedMetricPoint] = []
    issues: list[dict[str, Any]] = []
    effective_index = resource_index or build_resource_resolution_index(resources)

    for payload in payloads:
        mapped_metric = CANONICAL_METRIC_MAP.get(payload.source, {}).get(payload.metric_name)
        if mapped_metric is None:
            issues.append(
                {
                    "source": payload.source,
                    "external_resource_id": payload.external_resource_id,
                    "metric_name": payload.metric_name,
                    "timestamp_utc": payload.timestamp.astimezone(timezone.utc).isoformat(),
                    "issue_type": "unsupported_metric",
                    "details": {"unit": payload.unit},
                }
            )
            continue

        resource_id = resolve_resource_id(payload, effective_index)
        if resource_id is None:
            issues.append(
                {
                    "source": payload.source,
                    "external_resource_id": payload.external_resource_id,
                    "metric_name": payload.metric_name,
                    "timestamp_utc": payload.timestamp.astimezone(timezone.utc).isoformat(),
                    "issue_type": "unresolved_resource",
                    "details": {"dimensions": payload.dimensions},
                }
            )
            continue

        canonical_metric, canonical_unit = mapped_metric
        normalized.append(
            NormalizedMetricPoint(
                resource_id=resource_id,
                metric_name=canonical_metric,
                timestamp_utc=payload.timestamp.astimezone(timezone.utc),
                value=_normalize_value(canonical_metric, payload.value, payload.unit),
                unit=canonical_unit,
                source=payload.source,
                dimensions=payload.dimensions,
            )
        )

    return normalized, issues
