from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
import math

from app.models import RawMetricPayload, Resource, ResourceType


def sample_resources() -> list[Resource]:
    return [
        Resource(
            resource_id="app-checkout",
            name="checkout-api",
            resource_type=ResourceType.APP_SERVICE.value,
            environment="prod",
            current_size="large",
            metadata={
                "cloud_account": "shared-prod",
                "region": "us-east-1",
                "service_tags": ["payments", "checkout"],
                "source_aliases": {
                    "datadog": "checkout-api",
                    "sumologic": "checkout-api",
                },
                "autoscaling_enabled": False,
            },
        ),
        Resource(
            resource_id="app-analytics",
            name="analytics-worker",
            resource_type=ResourceType.APP_SERVICE.value,
            environment="prod",
            current_size="medium",
            metadata={
                "cloud_account": "shared-prod",
                "region": "us-east-1",
                "service_tags": ["analytics", "batch"],
                "source_aliases": {
                    "datadog": "analytics-worker",
                    "sumologic": "analytics-worker",
                },
                "autoscaling_enabled": True,
            },
        ),
        Resource(
            resource_id="db-orders",
            name="orders-db",
            resource_type=ResourceType.DB_INSTANCE.value,
            environment="prod",
            current_size="large",
            metadata={
                "cloud_account": "shared-prod",
                "region": "us-east-1",
                "database_engine": "postgres",
                "source_aliases": {
                    "cloudwatch": "orders-db",
                    "sumologic": "orders-db",
                },
                "autoscaling_enabled": False,
            },
        ),
    ]


def _time_range(start: datetime, end: datetime, step: timedelta) -> Iterable[datetime]:
    current = start
    while current <= end:
        yield current
        current += step


def _business_load(hour: int, weekday: int) -> float:
    if weekday >= 5:
        return 0.35
    if 8 <= hour < 19:
        return 1.0
    if 6 <= hour < 8 or 19 <= hour < 22:
        return 0.65
    return 0.40


def generate_datadog_payloads(window_start: datetime, window_end: datetime) -> list[RawMetricPayload]:
    payloads: list[RawMetricPayload] = []
    for point in _time_range(window_start, window_end, timedelta(minutes=5)):
        weekday = point.weekday()
        hour = point.hour
        minute_fraction = point.minute / 60.0
        cycle = math.sin((hour + minute_fraction) / 24.0 * 2.0 * math.pi)
        business = _business_load(hour, weekday)

        checkout_cpu = 16.0 + business * 10.0 + cycle * 4.0
        checkout_mem = 38.0 + business * 8.0 + cycle * 3.0
        checkout_req = 210.0 + business * 120.0 + cycle * 18.0
        checkout_err = 0.32 + max(cycle, 0.0) * 0.08
        checkout_latency = 110.0 + business * 18.0 + max(cycle, 0.0) * 10.0

        analytics_cpu = 22.0 + business * 36.0 + cycle * 8.0
        analytics_mem = 46.0 + business * 20.0 + cycle * 4.0
        analytics_req = 75.0 + business * 40.0 + cycle * 9.0
        analytics_err = 0.50 + max(cycle, 0.0) * 0.18
        analytics_latency = 135.0 + business * 55.0 + max(cycle, 0.0) * 24.0

        for metric_name, value in (
            ("system.cpu.user", checkout_cpu),
            ("system.mem.used.percent", checkout_mem),
            ("service.request.rate", checkout_req),
            ("service.error.rate", checkout_err),
            ("service.latency.p95.ms", checkout_latency),
            ("container.restart.count", 0.0),
        ):
            payloads.append(
                RawMetricPayload(
                    source="datadog",
                    external_resource_id="checkout-api",
                    metric_name=metric_name,
                    timestamp=point,
                    value=max(value, 0.0),
                    unit="percent" if "percent" in metric_name or "cpu" in metric_name else "count",
                    dimensions={"env": "prod"},
                )
            )

        for metric_name, value in (
            ("system.cpu.user", analytics_cpu),
            ("system.mem.used.percent", analytics_mem),
            ("service.request.rate", analytics_req),
            ("service.error.rate", analytics_err),
            ("service.latency.p95.ms", analytics_latency),
            ("container.restart.count", 0.0 if weekday < 5 else 1.0),
        ):
            payloads.append(
                RawMetricPayload(
                    source="datadog",
                    external_resource_id="analytics-worker",
                    metric_name=metric_name,
                    timestamp=point,
                    value=max(value, 0.0),
                    unit="percent" if "percent" in metric_name or "cpu" in metric_name else "count",
                    dimensions={"env": "prod"},
                )
            )

    return payloads


def generate_cloudwatch_payloads(window_start: datetime, window_end: datetime) -> list[RawMetricPayload]:
    payloads: list[RawMetricPayload] = []
    growth_days = max((window_end - window_start).days, 1)

    for point in _time_range(window_start, window_end, timedelta(minutes=5)):
        weekday = point.weekday()
        hour = point.hour
        business = _business_load(hour, weekday)
        cycle = math.sin(hour / 24.0 * 2.0 * math.pi)
        days_from_start = (point - window_start).total_seconds() / 86400.0

        db_cpu = 16.0 + business * 7.0 + cycle * 2.0
        db_connections = 62.0 + business * 18.0 + max(cycle, 0.0) * 6.0
        read_latency = 8.0 + business * 5.0 + max(cycle, 0.0) * 2.0
        write_latency = 9.0 + business * 5.5 + max(cycle, 0.0) * 2.0
        iops = 420.0 + business * 210.0 + max(cycle, 0.0) * 55.0
        storage_used = 640.0 + days_from_start * (22.0 / growth_days)

        for metric_name, value, unit in (
            ("CPUUtilization", db_cpu, "percent"),
            ("DatabaseConnections", db_connections, "count"),
            ("ReadLatency", read_latency, "milliseconds"),
            ("WriteLatency", write_latency, "milliseconds"),
            ("ReadWriteIOPS", iops, "count"),
            ("StorageUsedGB", storage_used, "gigabytes"),
        ):
            payloads.append(
                RawMetricPayload(
                    source="cloudwatch",
                    external_resource_id="orders-db",
                    metric_name=metric_name,
                    timestamp=point,
                    value=max(value, 0.0),
                    unit=unit,
                    dimensions={"env": "prod"},
                )
            )

    return payloads


def generate_sumologic_payloads(window_start: datetime, window_end: datetime) -> list[RawMetricPayload]:
    payloads: list[RawMetricPayload] = []
    for point in _time_range(window_start, window_end, timedelta(minutes=30)):
        weekday = point.weekday()
        hour = point.hour
        business = _business_load(hour, weekday)
        cycle = math.sin(hour / 24.0 * 2.0 * math.pi)
        payloads.extend(
            [
                RawMetricPayload(
                    source="sumologic",
                    external_resource_id="checkout-api",
                    metric_name="app.error.rate",
                    timestamp=point,
                    value=0.30 + business * 0.06,
                    unit="percent",
                    dimensions={"env": "prod"},
                ),
                RawMetricPayload(
                    source="sumologic",
                    external_resource_id="analytics-worker",
                    metric_name="app.error.rate",
                    timestamp=point,
                    value=0.45 + business * 0.12,
                    unit="percent",
                    dimensions={"env": "prod"},
                ),
                RawMetricPayload(
                    source="sumologic",
                    external_resource_id="orders-db",
                    metric_name="db.slow_query.count",
                    timestamp=point,
                    value=2.0 + business * 2.0,
                    unit="count",
                    dimensions={"env": "prod"},
                ),
                RawMetricPayload(
                    source="sumologic",
                    external_resource_id="checkout-api",
                    metric_name="sumo.ec2.cpu.utilization",
                    timestamp=point,
                    value=18.0 + business * 11.0 + cycle * 3.0,
                    unit="percent",
                    dimensions={"env": "prod", "autoscaling_group": "checkout-api"},
                ),
                RawMetricPayload(
                    source="sumologic",
                    external_resource_id="analytics-worker",
                    metric_name="sumo.ec2.cpu.utilization",
                    timestamp=point,
                    value=24.0 + business * 22.0 + cycle * 4.0,
                    unit="percent",
                    dimensions={"env": "prod", "autoscaling_group": "analytics-worker"},
                ),
                RawMetricPayload(
                    source="sumologic",
                    external_resource_id="checkout-api",
                    metric_name="sumo.ec2.memory.utilization",
                    timestamp=point,
                    value=40.0 + business * 10.0 + max(cycle, 0.0) * 3.0,
                    unit="percent",
                    dimensions={"env": "prod", "autoscaling_group": "checkout-api"},
                ),
                RawMetricPayload(
                    source="sumologic",
                    external_resource_id="analytics-worker",
                    metric_name="sumo.ec2.memory.utilization",
                    timestamp=point,
                    value=49.0 + business * 12.0 + max(cycle, 0.0) * 4.0,
                    unit="percent",
                    dimensions={"env": "prod", "autoscaling_group": "analytics-worker"},
                ),
                RawMetricPayload(
                    source="sumologic",
                    external_resource_id="checkout-api",
                    metric_name="sumo.http.call.count",
                    timestamp=point,
                    value=260.0 + business * 95.0 + max(cycle, 0.0) * 15.0,
                    unit="count",
                    dimensions={"env": "prod", "status_code": "200"},
                ),
                RawMetricPayload(
                    source="sumologic",
                    external_resource_id="checkout-api",
                    metric_name="sumo.http.status.count",
                    timestamp=point,
                    value=5.0 + max(cycle, 0.0) * 3.0,
                    unit="count",
                    dimensions={"env": "prod", "status_code": "500"},
                ),
                RawMetricPayload(
                    source="sumologic",
                    external_resource_id="checkout-api",
                    metric_name="sumo.http.request.size.bytes",
                    timestamp=point,
                    value=620.0 + business * 140.0,
                    unit="bytes",
                    dimensions={"env": "prod", "http_method": "POST"},
                ),
                RawMetricPayload(
                    source="sumologic",
                    external_resource_id="checkout-api",
                    metric_name="sumo.http.response.size.bytes",
                    timestamp=point,
                    value=1840.0 + business * 420.0,
                    unit="bytes",
                    dimensions={"env": "prod", "http_method": "POST"},
                ),
                RawMetricPayload(
                    source="sumologic",
                    external_resource_id="checkout-api",
                    metric_name="sumo.http.duration.ms",
                    timestamp=point,
                    value=122.0 + business * 14.0 + max(cycle, 0.0) * 8.0,
                    unit="milliseconds",
                    dimensions={"env": "prod", "http_method": "POST"},
                ),
                RawMetricPayload(
                    source="sumologic",
                    external_resource_id="checkout-api",
                    metric_name="sumo.http.read_time.ms",
                    timestamp=point,
                    value=12.0 + business * 2.0,
                    unit="milliseconds",
                    dimensions={"env": "prod", "http_method": "POST"},
                ),
                RawMetricPayload(
                    source="sumologic",
                    external_resource_id="checkout-api",
                    metric_name="sumo.http.write_time.ms",
                    timestamp=point,
                    value=10.0 + business * 2.0,
                    unit="milliseconds",
                    dimensions={"env": "prod", "http_method": "POST"},
                ),
                RawMetricPayload(
                    source="sumologic",
                    external_resource_id="orders-db",
                    metric_name="sumo.rds.cpu.utilization",
                    timestamp=point,
                    value=17.0 + business * 8.0 + cycle * 2.0,
                    unit="percent",
                    dimensions={"env": "prod", "db_instance_identifier": "orders-db"},
                ),
                RawMetricPayload(
                    source="sumologic",
                    external_resource_id="orders-db",
                    metric_name="sumo.rds.connections",
                    timestamp=point,
                    value=56.0 + business * 20.0 + max(cycle, 0.0) * 5.0,
                    unit="count",
                    dimensions={"env": "prod", "db_instance_identifier": "orders-db"},
                ),
                RawMetricPayload(
                    source="sumologic",
                    external_resource_id="orders-db",
                    metric_name="sumo.rds.freeable_memory.bytes",
                    timestamp=point,
                    value=(9.0 - business * 1.2) * 1024.0**3,
                    unit="bytes",
                    dimensions={"env": "prod", "db_instance_identifier": "orders-db"},
                ),
                RawMetricPayload(
                    source="sumologic",
                    external_resource_id="orders-db",
                    metric_name="sumo.rds.read_iops",
                    timestamp=point,
                    value=390.0 + business * 180.0,
                    unit="count",
                    dimensions={"env": "prod", "db_instance_identifier": "orders-db"},
                ),
                RawMetricPayload(
                    source="sumologic",
                    external_resource_id="orders-db",
                    metric_name="sumo.rds.write_iops",
                    timestamp=point,
                    value=280.0 + business * 155.0,
                    unit="count",
                    dimensions={"env": "prod", "db_instance_identifier": "orders-db"},
                ),
                RawMetricPayload(
                    source="sumologic",
                    external_resource_id="orders-db",
                    metric_name="sumo.rds.read_throughput.bytes_per_sec",
                    timestamp=point,
                    value=2.8e6 + business * 1.2e6,
                    unit="bytes_per_second",
                    dimensions={"env": "prod", "db_instance_identifier": "orders-db"},
                ),
                RawMetricPayload(
                    source="sumologic",
                    external_resource_id="orders-db",
                    metric_name="sumo.rds.write_throughput.bytes_per_sec",
                    timestamp=point,
                    value=1.9e6 + business * 9.5e5,
                    unit="bytes_per_second",
                    dimensions={"env": "prod", "db_instance_identifier": "orders-db"},
                ),
                RawMetricPayload(
                    source="sumologic",
                    external_resource_id="orders-db",
                    metric_name="sumo.rds.read_latency.ms",
                    timestamp=point,
                    value=8.0 + business * 4.0 + max(cycle, 0.0) * 2.0,
                    unit="milliseconds",
                    dimensions={"env": "prod", "db_instance_identifier": "orders-db"},
                ),
                RawMetricPayload(
                    source="sumologic",
                    external_resource_id="orders-db",
                    metric_name="sumo.rds.write_latency.ms",
                    timestamp=point,
                    value=9.0 + business * 4.5 + max(cycle, 0.0) * 2.0,
                    unit="milliseconds",
                    dimensions={"env": "prod", "db_instance_identifier": "orders-db"},
                ),
            ]
        )

    return payloads


def default_window(now: datetime, window_days: int) -> tuple[datetime, datetime]:
    end = now.astimezone(timezone.utc).replace(second=0, microsecond=0)
    start = end - timedelta(days=window_days)
    return start, end
