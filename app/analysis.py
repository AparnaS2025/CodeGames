from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import math
from typing import Any

from app.config import Settings
from app.models import Recommendation, RecommendationType, Resource


PRIMARY_SOURCES_BY_TYPE = {
    "app_service": ("sumologic", "datadog", "cloudwatch"),
    "db_instance": ("sumologic", "cloudwatch"),
}


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * p
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[int(rank)]
    fraction = rank - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _average(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _clamp(value: float, lower: float = 0.0, upper: float = 100.0) -> float:
    return max(lower, min(value, upper))


def _score_high_pressure(value: float | None, warning: float, critical: float) -> float | None:
    if value is None:
        return None
    if critical <= warning:
        return None
    return round(_clamp(((value - warning) / (critical - warning)) * 100.0), 2)


def _score_low_pressure(value: float | None, warning: float, critical: float) -> float | None:
    if value is None:
        return None
    if warning <= critical:
        return None
    return round(_clamp(((warning - value) / (warning - critical)) * 100.0), 2)


def _weighted_score(components: dict[str, dict[str, float | None]]) -> float | None:
    weighted_total = 0.0
    total_weight = 0.0
    for component in components.values():
        score = component.get("score")
        weight = component.get("weight")
        if score is None or weight is None or weight <= 0:
            continue
        weighted_total += float(score) * float(weight)
        total_weight += float(weight)
    if total_weight == 0:
        return None
    return round(weighted_total / total_weight, 2)


def _pressure_band(score: float | None) -> str:
    if score is None:
        return "unknown"
    if score >= 75.0:
        return "high"
    if score >= 55.0:
        return "elevated"
    if score <= 25.0:
        return "low"
    return "moderate"


def _first_available(items: dict[str, Any], ordered_keys: tuple[str, ...]) -> tuple[str | None, Any]:
    for key in ordered_keys:
        if key in items:
            return key, items[key]
    return None, None


def _metric_entries(grouped: dict[str, list[dict[str, Any]]], metric_names: tuple[str, ...]) -> list[dict[str, Any]]:
    for metric_name in metric_names:
        entries = grouped.get(metric_name, [])
        if entries:
            return entries
    return []


def _count_sustained(values: list[float], predicate: Any, minimum_streak: int = 12) -> int:
    streak = 0
    count = 0
    for value in values:
        if predicate(value):
            streak += 1
        else:
            if streak >= minimum_streak:
                count += 1
            streak = 0
    if streak >= minimum_streak:
        count += 1
    return count


def _ewma_cusum_summary(entries: list[dict[str, Any]], alpha: float = 0.25) -> dict[str, Any] | None:
    ordered = sorted(entries, key=lambda item: item["timestamp_utc"])
    values = [float(entry["value"]) for entry in ordered]
    if len(values) < 20:
        return None

    ewma = values[0]
    residuals: list[float] = []
    ewma_values: list[float] = []
    for value in values[1:]:
        residuals.append(value - ewma)
        ewma = (alpha * value) + ((1.0 - alpha) * ewma)
        ewma_values.append(ewma)

    baseline_count = max(10, int(len(residuals) * 0.70))
    baseline_values = values[: baseline_count + 1]
    baseline_value_mean = _average(baseline_values) or 0.0
    baseline_residuals = residuals[:baseline_count]
    residual_mean = _average(baseline_residuals) or 0.0
    variance = sum((value - residual_mean) ** 2 for value in baseline_residuals) / max(len(baseline_residuals) - 1, 1)
    residual_stddev = math.sqrt(variance)
    noise_floor = max(abs(baseline_value_mean) * 0.05, 1.0)
    effective_stddev = max(residual_stddev, noise_floor)
    allowance = 0.5 * effective_stddev
    threshold = 5.0 * effective_stddev

    positive_cusum = 0.0
    negative_cusum = 0.0
    max_positive_cusum = 0.0
    max_negative_cusum = 0.0
    total_breach_count = 0
    recent_breach_count = 0
    recent_start = max(0, len(residuals) - max(5, int(len(residuals) * 0.10)))

    for index, residual in enumerate(residuals):
        centered = residual - residual_mean
        positive_cusum = max(0.0, positive_cusum + centered - allowance)
        negative_cusum = min(0.0, negative_cusum + centered + allowance)
        max_positive_cusum = max(max_positive_cusum, positive_cusum)
        max_negative_cusum = min(max_negative_cusum, negative_cusum)
        if positive_cusum >= threshold or abs(negative_cusum) >= threshold:
            total_breach_count += 1
            if index >= recent_start:
                recent_breach_count += 1
            positive_cusum = 0.0
            negative_cusum = 0.0

    max_cusum = max(max_positive_cusum, abs(max_negative_cusum))
    latest_delta = values[-1] - ewma_values[-1]
    latest_delta_percent = (latest_delta / abs(ewma)) * 100.0 if ewma else 0.0
    latest_baseline_delta = values[-1] - baseline_value_mean
    latest_baseline_delta_percent = (
        (latest_baseline_delta / abs(baseline_value_mean)) * 100.0 if baseline_value_mean else 0.0
    )
    active_anomaly = (
        (total_breach_count > 0 and abs(latest_baseline_delta_percent) >= 50.0)
        or (recent_breach_count > 0 and abs(latest_delta_percent) >= 20.0)
    )
    return {
        "point_count": len(values),
        "mean": round(_average(values) or 0.0, 2),
        "baseline_mean": round(baseline_value_mean, 2),
        "stddev": round(effective_stddev, 2),
        "latest_ewma": round(ewma_values[-1], 2),
        "latest_delta_percent": round(latest_delta_percent, 2),
        "latest_baseline_delta_percent": round(latest_baseline_delta_percent, 2),
        "active_anomaly": active_anomaly,
        "cusum_breach_count": recent_breach_count,
        "total_cusum_breach_count": total_breach_count,
        "cusum_severity": round(_clamp((max_cusum / threshold) * 100.0), 2),
    }


def _build_app_pressure_components(features: dict[str, Any]) -> dict[str, dict[str, float | None]]:
    latency_value = max(features.get("latency_p95") or 0.0, features.get("target_response_time_p95") or 0.0)
    healthy_host_min = features.get("healthy_host_count_min")
    return {
        "cpu": {"score": _score_high_pressure(features.get("cpu_p95"), 65.0, 85.0), "weight": 0.30},
        "memory": {"score": _score_high_pressure(features.get("memory_p95"), 75.0, 90.0), "weight": 0.15},
        "latency": {"score": _score_high_pressure(latency_value or None, 160.0, 260.0), "weight": 0.30},
        "errors": {"score": _score_high_pressure(features.get("error_rate_p95"), 1.0, 5.0), "weight": 0.15},
        "healthy_hosts": {"score": _score_low_pressure(healthy_host_min, 2.0, 1.0), "weight": 0.10},
    }


def _build_db_pressure_components(features: dict[str, Any]) -> dict[str, dict[str, float | None]]:
    max_latency = max(features.get("db_read_latency_p95") or 0.0, features.get("db_write_latency_p95") or 0.0)
    total_iops = features.get("db_iops_p95")
    freeable_memory = features.get("db_freeable_memory_min_gb")
    return {
        "cpu": {"score": _score_high_pressure(features.get("cpu_p95"), 65.0, 90.0), "weight": 0.25},
        "connections": {"score": _score_high_pressure(features.get("db_connections_p95"), 70.0, 120.0), "weight": 0.20},
        "latency": {"score": _score_high_pressure(max_latency or None, 12.0, 80.0), "weight": 0.30},
        "iops": {"score": _score_high_pressure(total_iops, 3000.0, 10000.0), "weight": 0.10},
        "freeable_memory": {"score": _score_low_pressure(freeable_memory, 2.0, 0.5), "weight": 0.15},
    }


def _build_anomaly_summary(
    grouped: dict[str, list[dict[str, Any]]],
    metric_names: tuple[str, ...],
) -> dict[str, Any]:
    summaries: dict[str, Any] = {}
    max_score = 0.0
    breach_count = 0
    active_anomaly_count = 0
    for metric_name in metric_names:
        summary = _ewma_cusum_summary(grouped.get(metric_name, []))
        if summary is None:
            continue
        summaries[metric_name] = summary
        max_score = max(max_score, float(summary["cusum_severity"]))
        breach_count += int(summary["cusum_breach_count"])
        if summary["active_anomaly"]:
            active_anomaly_count += 1
    return {
        "metrics": summaries,
        "max_cusum_severity": round(max_score, 2),
        "total_cusum_breaches": breach_count,
        "active_anomaly_count": active_anomaly_count,
    }


def _size_step(current_size: str, size_order: tuple[str, ...], direction: int) -> str | None:
    try:
        current_index = size_order.index(current_size)
    except ValueError:
        return None
    next_index = current_index + direction
    if next_index < 0 or next_index >= len(size_order):
        return None
    return size_order[next_index]


def _estimate_savings(resource_type: str, current_size: str, suggested_size: str | None, settings: Settings) -> float | None:
    if suggested_size is None:
        return None
    costs = settings.cost_profile.get(resource_type, {})
    current = costs.get(current_size)
    target = costs.get(suggested_size)
    if current is None or target is None:
        return None
    return round(current - target, 2)


def build_analysis_snapshot(
    resource: Resource,
    metrics: list[dict[str, Any]],
    window_start: datetime,
    window_end: datetime,
    settings: Settings,
) -> tuple[dict[str, Any], list[str], dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    freshness_by_source: dict[str, datetime] = {}

    for metric in metrics:
        grouped[metric["metric_name"]].append(metric)
        timestamp = datetime.fromisoformat(metric["timestamp_utc"])
        source = metric["source"]
        freshness_by_source[source] = max(timestamp, freshness_by_source.get(source, timestamp))

    feature_values: dict[str, Any] = {}
    cpu_metric_candidates = ("cpu_percent", "ec2_cpu_percent") if resource.resource_type == "app_service" else ("db_cpu_percent",)
    cpu_entries = _metric_entries(grouped, cpu_metric_candidates)
    cpu_values = [entry["value"] for entry in cpu_entries]
    memory_values = [entry["value"] for entry in grouped.get("memory_percent", [])]
    latency_values = [entry["value"] for entry in grouped.get("latency_p95_ms", [])]
    error_values = [entry["value"] for entry in grouped.get("error_rate", [])]
    request_count_values = [entry["value"] for entry in grouped.get("request_count", [])]
    target_response_values = [entry["value"] for entry in grouped.get("target_response_time_ms", [])]
    healthy_host_values = [entry["value"] for entry in grouped.get("healthy_host_count", [])]
    connection_values = [entry["value"] for entry in grouped.get("db_connections", [])]
    read_latency_values = [entry["value"] for entry in grouped.get("db_read_latency_ms", [])]
    write_latency_values = [entry["value"] for entry in grouped.get("db_write_latency_ms", [])]
    iops_values = [entry["value"] for entry in grouped.get("db_iops", [])]
    read_iops_values = [entry["value"] for entry in grouped.get("db_read_iops", [])]
    write_iops_values = [entry["value"] for entry in grouped.get("db_write_iops", [])]
    freeable_memory_values = [entry["value"] for entry in grouped.get("db_freeable_memory_bytes", [])]
    storage_values = [entry["value"] for entry in grouped.get("storage_used_gb", [])]

    feature_values["cpu_p50"] = percentile(cpu_values, 0.50)
    feature_values["cpu_p95"] = percentile(cpu_values, 0.95)
    feature_values["cpu_p99"] = percentile(cpu_values, 0.99)
    feature_values["memory_p50"] = percentile(memory_values, 0.50)
    feature_values["memory_p95"] = percentile(memory_values, 0.95)
    feature_values["latency_p95"] = percentile(latency_values, 0.95)
    feature_values["error_rate_p95"] = percentile(error_values, 0.95)
    feature_values["request_count_p95"] = percentile(request_count_values, 0.95)
    feature_values["target_response_time_p95"] = percentile(target_response_values, 0.95)
    feature_values["healthy_host_count_min"] = min(healthy_host_values) if healthy_host_values else None
    feature_values["db_connections_p95"] = percentile(connection_values, 0.95)
    feature_values["db_read_latency_p95"] = percentile(read_latency_values, 0.95)
    feature_values["db_write_latency_p95"] = percentile(write_latency_values, 0.95)
    combined_iops_values = iops_values or [read + write for read, write in zip(read_iops_values, write_iops_values, strict=False)]
    feature_values["db_iops_p95"] = percentile(combined_iops_values, 0.95)
    feature_values["db_read_iops_p95"] = percentile(read_iops_values, 0.95)
    feature_values["db_write_iops_p95"] = percentile(write_iops_values, 0.95)
    feature_values["db_freeable_memory_min_gb"] = (
        round(min(freeable_memory_values) / (1024.0**3), 2) if freeable_memory_values else None
    )
    feature_values["storage_growth_gb"] = round(storage_values[-1] - storage_values[0], 2) if len(storage_values) > 1 else None
    if resource.resource_type == "app_service":
        pressure_components = _build_app_pressure_components(feature_values)
        anomaly_summary = _build_anomaly_summary(
            grouped,
            ("cpu_percent", "ec2_cpu_percent", "latency_p95_ms", "target_response_time_ms", "error_rate"),
        )
    else:
        pressure_components = _build_db_pressure_components(feature_values)
        anomaly_summary = _build_anomaly_summary(
            grouped,
            ("db_cpu_percent", "db_connections", "db_read_latency_ms", "db_write_latency_ms"),
        )
    pressure_score = _weighted_score(pressure_components)
    feature_values["pressure_components"] = pressure_components
    feature_values["pressure_score"] = pressure_score
    feature_values["pressure_band"] = _pressure_band(pressure_score)
    feature_values["anomaly_summary"] = anomaly_summary
    feature_values["anomaly_score"] = anomaly_summary["max_cusum_severity"]
    feature_values["anomaly_count"] = anomaly_summary["active_anomaly_count"]

    cpu_weekday = []
    cpu_weekend = []
    business_hour_cpu = []
    by_hour_of_week: dict[int, list[float]] = defaultdict(list)

    for entry in cpu_entries:
        timestamp = datetime.fromisoformat(entry["timestamp_utc"])
        by_hour_of_week[timestamp.weekday() * 24 + timestamp.hour].append(entry["value"])
        if timestamp.weekday() >= 5:
            cpu_weekend.append(entry["value"])
        else:
            cpu_weekday.append(entry["value"])
            if 8 <= timestamp.hour < 19:
                business_hour_cpu.append(entry["value"])

    feature_values["weekday_vs_weekend_utilization_diff"] = round(
        (_average(cpu_weekday) or 0.0) - (_average(cpu_weekend) or 0.0), 2
    )
    baseline_summary = {
        "peak_hour_of_week": max(
            ((hour, _average(values) or 0.0) for hour, values in by_hour_of_week.items()),
            key=lambda item: item[1],
            default=(None, 0.0),
        )[0],
        "peak_hour_cpu_avg": round(
            max((_average(values) or 0.0 for values in by_hour_of_week.values()), default=0.0),
            2,
        ),
    }
    feature_values["hour_of_week_baseline"] = baseline_summary
    feature_values["sustained_high_utilization_windows"] = _count_sustained(cpu_values, lambda value: value >= 75.0)
    feature_values["sustained_low_utilization_windows"] = _count_sustained(cpu_values, lambda value: value <= 35.0)

    preferred_primary_sources = PRIMARY_SOURCES_BY_TYPE[resource.resource_type]
    source_freshness = {
        source: {
            "latest_point_utc": latest.isoformat(),
            "freshness_hours": round((window_end - latest).total_seconds() / 3600.0, 2),
            "is_stale": (window_end - latest) > timedelta(hours=settings.stale_after_hours),
        }
        for source, latest in freshness_by_source.items()
    }
    primary_source, primary_freshness = _first_available(source_freshness, preferred_primary_sources)
    if primary_source is None and source_freshness:
        primary_source = next(iter(source_freshness))
        primary_freshness = source_freshness[primary_source]

    history_days = 0.0
    if cpu_values:
        first = datetime.fromisoformat(cpu_entries[0]["timestamp_utc"])
        last = datetime.fromisoformat(cpu_entries[-1]["timestamp_utc"])
        history_days = round((last - first).total_seconds() / 86400.0, 2)
    insufficient_data = (
        not cpu_values
        or history_days < settings.minimum_history_days
        or primary_freshness is None
        or primary_freshness["is_stale"]
    )
    feature_values["latest_source_freshness_hours"] = primary_freshness["freshness_hours"] if primary_freshness else None
    feature_values["history_days"] = history_days
    feature_values["insufficient_data"] = insufficient_data
    feature_values["business_hour_cpu_p95"] = percentile(business_hour_cpu, 0.95)
    feature_values["primary_source"] = primary_source

    tags: list[str] = []
    if insufficient_data:
        tags.append("insufficient_data")
    if primary_freshness and primary_freshness["is_stale"]:
        tags.append("stale_source_data")
    if (feature_values["cpu_p95"] or 0.0) <= 35.0 and (feature_values["memory_p95"] or 0.0) <= 70.0:
        tags.append("steady_underutilized")
    if (feature_values["cpu_p95"] or 0.0) >= 80.0:
        tags.append("steady_saturated")
    if feature_values["weekday_vs_weekend_utilization_diff"] >= 10.0:
        tags.append("weekend_idle")
    if (feature_values["business_hour_cpu_p95"] or 0.0) - (_average(cpu_weekend) or 0.0) >= 18.0:
        tags.append("business_hours_peak")
    if feature_values["pressure_band"] == "high":
        tags.append("high_composite_pressure")
    elif feature_values["pressure_band"] == "elevated":
        tags.append("elevated_composite_pressure")
    elif feature_values["pressure_band"] == "low":
        tags.append("low_composite_pressure")
    if feature_values["anomaly_count"] > 0:
        tags.append("short_lived_anomaly")
    if feature_values["anomaly_count"] >= 2 and (feature_values["pressure_score"] or 0.0) >= 55.0:
        tags.append("sustained_pressure_anomaly")

    conflicting = False
    if resource.resource_type == "app_service":
        conflicting = (feature_values["cpu_p95"] or 0.0) <= 35.0 and max(
            feature_values["latency_p95"] or 0.0,
            feature_values["target_response_time_p95"] or 0.0,
        ) >= 200.0
    else:
        conflicting = (feature_values["cpu_p95"] or 0.0) <= 30.0 and (feature_values["db_connections_p95"] or 0.0) >= 70.0
    if conflicting:
        tags.append("conflicting_signals")

    return feature_values, tags, source_freshness


def generate_recommendation(
    resource: Resource,
    features: dict[str, Any],
    tags: list[str],
    settings: Settings,
) -> Recommendation:
    min_size = settings.min_size_by_type[resource.resource_type]
    smaller_size = _size_step(resource.current_size, settings.size_order, -1)
    larger_size = _size_step(resource.current_size, settings.size_order, 1)
    can_scale_down = smaller_size is not None and settings.size_order.index(smaller_size) >= settings.size_order.index(min_size)

    evidence: list[str] = []
    guardrails: list[str] = []

    def add_common_evidence() -> None:
        evidence.append(f"CPU p95 is {round(features.get('cpu_p95') or 0.0, 1)}%.")
        if features.get("pressure_score") is not None:
            evidence.append(
                f"Composite pressure score is {round(features['pressure_score'], 1)} ({features.get('pressure_band', 'unknown')})."
            )
        if features.get("memory_p95") is not None:
            evidence.append(f"Memory p95 is {round(features['memory_p95'], 1)}%.")
        if features.get("latency_p95") is not None:
            evidence.append(f"Latency p95 is {round(features['latency_p95'], 1)} ms.")
        if features.get("target_response_time_p95") is not None:
            evidence.append(f"ALB target response time p95 is {round(features['target_response_time_p95'], 1)} ms.")
        if features.get("request_count_p95") is not None:
            evidence.append(f"Request-count p95 is {round(features['request_count_p95'], 1)}.")
        if features.get("healthy_host_count_min") is not None:
            evidence.append(f"Minimum healthy host count is {round(features['healthy_host_count_min'], 1)}.")
        if features.get("db_connections_p95") is not None:
            evidence.append(f"Database connection p95 is {round(features['db_connections_p95'], 1)}.")
        if features.get("db_freeable_memory_min_gb") is not None:
            evidence.append(f"Minimum freeable DB memory is {round(features['db_freeable_memory_min_gb'], 1)} GB.")
        if features.get("anomaly_count"):
            evidence.append(
                f"EWMA/CUSUM detected {features['anomaly_count']} anomaly window(s) with severity {round(features.get('anomaly_score') or 0.0, 1)}."
            )
        freshness = features.get("latest_source_freshness_hours")
        if freshness is not None:
            evidence.append(f"Primary source freshness is {round(freshness, 1)} hours.")

    add_common_evidence()

    if features["insufficient_data"]:
        guardrails.extend(
            [
                "Do not change capacity until fresh primary-source data is available.",
                "Re-run ingestion before the next review cycle.",
            ]
        )
        evidence.append("Recommendation is conservative because primary metrics are stale or history is too short.")
        return Recommendation(
            recommendation_id="",
            resource_id=resource.resource_id,
            recommendation_type=RecommendationType.INSUFFICIENT_DATA.value,
            current_size=resource.current_size,
            suggested_size=None,
            confidence="low",
            risk_level="high",
            estimated_monthly_savings=None,
            evidence=evidence[:4],
            guardrails=guardrails,
            pattern_summary="Insufficient fresh telemetry prevents a safe recommendation.",
            report_summary=f"{resource.name} needs fresh data before capacity advice can be trusted.",
        )

    if resource.resource_type == "app_service":
        underutilized = (
            "steady_underutilized" in tags
            and "conflicting_signals" not in tags
            and "short_lived_anomaly" not in tags
            and (features.get("pressure_score") is None or features.get("pressure_score") <= 35.0)
        )
        sustained_pressure = (
            (features.get("cpu_p95") or 0.0) >= 70.0
            or (features.get("latency_p95") or 0.0) >= 210.0
            or (features.get("target_response_time_p95") or 0.0) >= 200.0
            or (features.get("pressure_score") or 0.0) >= 70.0
            or "sustained_pressure_anomaly" in tags
        )

        if underutilized and can_scale_down and (features.get("latency_p95") or 0.0) < 160.0 and (features.get("error_rate_p95") or 0.0) < 1.5:
            suggested_size = smaller_size
            guardrails.extend(
                [
                    "Roll out during a low-traffic window and monitor latency, error rate, and restarts for 24 hours.",
                    "Do not go below the configured minimum service size.",
                ]
            )
            evidence.append("A more aggressive reduction was not chosen because the MVP only allows one size step per review.")
            return Recommendation(
                recommendation_id="",
                resource_id=resource.resource_id,
                recommendation_type=RecommendationType.SCALE_DOWN.value,
                current_size=resource.current_size,
                suggested_size=suggested_size,
                confidence="high" if "weekend_idle" in tags else "medium",
                risk_level="low",
                estimated_monthly_savings=_estimate_savings(resource.resource_type, resource.current_size, suggested_size, settings),
                evidence=evidence[:5],
                guardrails=guardrails,
                pattern_summary="Sustained low utilization with healthy latency indicates excess headroom.",
                report_summary=f"{resource.name} is a scale-down candidate from {resource.current_size} to {suggested_size}.",
            )

        if sustained_pressure and larger_size is not None and max(
            features.get("latency_p95") or 0.0,
            features.get("target_response_time_p95") or 0.0,
        ) >= 230.0:
            guardrails.extend(
                [
                    "Validate the peak against deployment and incident timelines before changing size.",
                    "Confirm autoscaling signals are not already absorbing the load.",
                ]
            )
            evidence.append("A watchlist recommendation was not chosen because latency pressure is already material.")
            return Recommendation(
                recommendation_id="",
                resource_id=resource.resource_id,
                recommendation_type=RecommendationType.SCALE_UP.value,
                current_size=resource.current_size,
                suggested_size=larger_size,
                confidence="medium",
                risk_level="medium",
                estimated_monthly_savings=None,
                evidence=evidence[:5],
                guardrails=guardrails,
                pattern_summary="Business-hour pressure suggests current headroom may be insufficient.",
                report_summary=f"{resource.name} may need a one-step scale-up if current peak pressure persists.",
            )

        guardrails.extend(
            [
                "Continue observing business-hour peaks before changing steady-state capacity.",
                "Review autoscaling behavior and deployment timing before taking action.",
            ]
        )
        if "short_lived_anomaly" in tags:
            evidence.append("A capacity change was avoided because anomaly detection found unstable recent behavior.")
        else:
            evidence.append("A more aggressive action was not chosen because burst behavior is still bounded to business hours.")
        return Recommendation(
            recommendation_id="",
            resource_id=resource.resource_id,
            recommendation_type=(
                RecommendationType.WATCHLIST.value
                if {"business_hours_peak", "elevated_composite_pressure", "high_composite_pressure", "short_lived_anomaly"} & set(tags)
                else RecommendationType.HOLD.value
            ),
            current_size=resource.current_size,
            suggested_size=None,
            confidence="medium",
            risk_level="medium",
            estimated_monthly_savings=None,
            evidence=evidence[:5],
            guardrails=guardrails,
            pattern_summary="The workload has notable peaks, but not enough sustained evidence for a safe resize.",
            report_summary=f"{resource.name} should remain under observation rather than be resized immediately.",
        )

    low_cpu = (features.get("cpu_p95") or 0.0) <= 30.0
    low_connection_pressure = (features.get("db_connections_p95") or 0.0) <= 45.0
    healthy_db_latency = max(features.get("db_read_latency_p95") or 0.0, features.get("db_write_latency_p95") or 0.0) <= 12.0
    conflicting = "conflicting_signals" in tags

    if (
        low_cpu
        and low_connection_pressure
        and healthy_db_latency
        and can_scale_down
        and "short_lived_anomaly" not in tags
        and (features.get("pressure_score") is None or features.get("pressure_score") <= 35.0)
    ):
        suggested_size = smaller_size
        guardrails.extend(
            [
                "Review connection saturation, storage growth, and replica lag after any resize.",
                "Database scale-down should be staged more conservatively than app-service changes.",
            ]
        )
        evidence.append("A more aggressive reduction was not chosen because database downsizing uses stricter one-step safety rules.")
        return Recommendation(
            recommendation_id="",
            resource_id=resource.resource_id,
            recommendation_type=RecommendationType.SCALE_DOWN.value,
            current_size=resource.current_size,
            suggested_size=suggested_size,
            confidence="medium",
            risk_level="medium",
            estimated_monthly_savings=_estimate_savings(resource.resource_type, resource.current_size, suggested_size, settings),
            evidence=evidence[:5],
            guardrails=guardrails,
            pattern_summary="Database resource looks lightly utilized across CPU, connections, and latency.",
            report_summary=f"{resource.name} may support a cautious one-step scale-down.",
        )

    guardrails.extend(
        [
            "Do not scale down while connection pressure or query latency remains elevated.",
            "Re-check storage growth and slow-query context before the next review.",
        ]
    )
    evidence.append("A scale-down was rejected because non-CPU database signals still show operational pressure.")
    recommendation_type = (
        RecommendationType.WATCHLIST.value
        if (
            conflicting
            or {"elevated_composite_pressure", "high_composite_pressure", "short_lived_anomaly"} & set(tags)
        )
        else RecommendationType.HOLD.value
    )
    return Recommendation(
        recommendation_id="",
        resource_id=resource.resource_id,
        recommendation_type=recommendation_type,
        current_size=resource.current_size,
        suggested_size=None,
        confidence="medium",
        risk_level="medium" if conflicting else "low",
        estimated_monthly_savings=None,
        evidence=evidence[:5],
        guardrails=guardrails,
        pattern_summary="CPU looks light, but connection and latency signals make a resize unsafe right now.",
        report_summary=f"{resource.name} should stay at its current size until database pressure signals ease.",
    )


def build_portfolio_report(recommendations: list[dict[str, Any]], generated_at: datetime) -> tuple[str, dict[str, Any]]:
    scale_down = [item for item in recommendations if item["recommendation_type"] == RecommendationType.SCALE_DOWN.value]
    watch_items = [
        item
        for item in recommendations
        if item["recommendation_type"] in {RecommendationType.WATCHLIST.value, RecommendationType.SCALE_UP.value}
    ]
    insufficient = [item for item in recommendations if item["recommendation_type"] == RecommendationType.INSUFFICIENT_DATA.value]
    total_savings = round(sum(item["estimated_monthly_savings"] or 0.0 for item in scale_down), 2)
    summary = (
        f"Weekly capacity review generated {len(recommendations)} recommendations on {generated_at.date().isoformat()}. "
        f"{len(scale_down)} scale-down candidate(s) represent about ${total_savings:.2f} in monthly savings, "
        f"{len(watch_items)} workload(s) remain on watch, and {len(insufficient)} workload(s) need fresher data."
    )
    details = {
        "top_cost_saving_opportunities": [item["resource_name"] for item in scale_down[:3]],
        "top_risk_hotspots": [item["resource_name"] for item in watch_items[:3]],
        "unresolved_data_issues": [item["resource_name"] for item in insufficient[:3]],
        "overall_potential_savings": total_savings if scale_down else "cost estimate unavailable",
    }
    return summary, details
