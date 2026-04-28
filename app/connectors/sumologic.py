from __future__ import annotations

from base64 import b64encode
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.cookiejar import CookieJar
import json
from threading import local
import time
from typing import Any
from urllib import error, request

from app.config import Settings
from app.connectors.base import ConnectorFetchResult
from app.models import RawMetricPayload
from app.sample_data import generate_sumologic_payloads


SUMOLOGIC_HTTP_LOG_PARSE_EXPRESSION = (
    r"^(?<event_date>\S+)\s+(?<event_time>\S+)\s+(?<server_ip>\S+)\s+(?<application_name>.+?)\s+-\s+"
    r"(?<client_ip>\S+)\s+(?<server_port>\d+)\s+(?<http_method>GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+"
    r"(?<request_path>\S+)\s+(?<query_token>\S+)\s+(?<request_id>\S+)\s+(?<status_code>\d{3})\s+"
    r"(?<user_agent>\S+)\s+(?<request_content_type>\S+)\s+(?<request_size_token>\S+)\s+"
    r"(?<response_content_type>\S+)\s+(?<response_encoding>\S+)\s+(?<response_size>\d+)\s+"
    r"(?<time_taken_ms>\d+)\s+(?<read_time_ms>\d+)\s+(?<write_time_ms>\d+)$"
)


@dataclass(frozen=True, slots=True)
class SumoQueryJob:
    name: str
    metric_name: str
    unit: str
    query_kind: str
    query: str
    resource_field: str


class SumoLogicConnector:
    source_name = "sumologic"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._thread_local = local()

    def fetch(self, window_start: datetime, window_end: datetime) -> ConnectorFetchResult:
        if self._use_sample_mode():
            payloads = generate_sumologic_payloads(window_start, window_end)
            return ConnectorFetchResult(
                payloads=payloads,
                health={
                    "status": "healthy",
                    "mode": "sample",
                    "payload_count": len(payloads),
                    "notes": "Configured to use deterministic sample Sumo Logic aggregates.",
                },
            )

        effective_start, effective_end = self._effective_window(window_start, window_end)
        query_jobs = self._build_query_jobs()
        payloads: list[RawMetricPayload] = []
        issues: list[dict[str, Any]] = []
        max_workers = self._max_workers_for_jobs(len(query_jobs))

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self._execute_query_job, job, effective_start, effective_end): job for job in query_jobs
            }
            for future in as_completed(futures):
                job = futures[future]
                try:
                    payloads.extend(future.result())
                except Exception as exc:
                    issues.append(
                        {
                            "source": self.source_name,
                            "external_resource_id": job.resource_field,
                            "metric_name": job.metric_name,
                            "timestamp_utc": effective_end.isoformat(),
                            "issue_type": "sumologic_query_failure",
                            "details": {
                                "query_name": job.name,
                                "query_kind": job.query_kind,
                                "error": str(exc),
                            },
                        }
                    )

        status = "healthy" if not issues else ("degraded" if payloads else "failed")
        return ConnectorFetchResult(
            payloads=payloads,
            health={
                "status": status,
                "mode": "api",
                "payload_count": len(payloads),
                "query_count": len(query_jobs),
                "issue_count": len(issues),
                "max_workers": max_workers,
                "window_start_utc": effective_start.isoformat(),
                "window_end_utc": effective_end.isoformat(),
                "http_log_queries_enabled": self.settings.sumologic_enable_http_log_queries,
            },
            issues=issues,
        )

    def _use_sample_mode(self) -> bool:
        if self.settings.sumologic_use_sample_data:
            return True
        return not (
            self.settings.sumologic_api_url
            and self.settings.sumologic_access_id
            and self.settings.sumologic_access_key
        )

    def _effective_window(self, window_start: datetime, window_end: datetime) -> tuple[datetime, datetime]:
        end = window_end.astimezone(timezone.utc)
        start = max(
            window_start.astimezone(timezone.utc),
            end - timedelta(days=self.settings.sumologic_max_query_window_days),
        )
        return start, end

    def _build_query_jobs(self) -> list[SumoQueryJob]:
        jobs = [*self._build_metric_query_jobs()]
        if self.settings.sumologic_enable_http_log_queries:
            jobs.extend(self._build_http_log_query_jobs())
        return jobs

    def _build_metric_query_jobs(self) -> list[SumoQueryJob]:
        metric_scope = f"_sourceCategory={self.settings.sumologic_metrics_source_category}"
        autoscaling_scope = (
            f"{metric_scope} Namespace=AWS/EC2 "
            f"AutoScalingGroupName={self.settings.sumologic_metrics_autoscaling_group_pattern}"
        )
        database_scope = f"{metric_scope} Namespace=AWS/RDS wk_application_name={self._wildcard_value(self.settings.sumologic_metrics_application_name)}"
        alb_scope = f"{metric_scope} Namespace=AWS/ApplicationELB wk_application_name={self._wildcard_value(self.settings.sumologic_metrics_application_name)}"

        return [
            SumoQueryJob(
                name="ec2_cpu_utilization",
                metric_name="sumo.ec2.cpu.utilization",
                unit="percent",
                query_kind="metrics",
                query=f'{autoscaling_scope} metric=CPUUtilization | avg by AutoScalingGroupName, Region, AccountId, _sourceCategory',
                resource_field="AutoScalingGroupName",
            ),
            SumoQueryJob(
                name="ec2_memory_utilization",
                metric_name="sumo.ec2.memory.utilization",
                unit="percent",
                query_kind="metrics",
                query=f'{autoscaling_scope} metric="Memory % Committed Bytes In Use" | avg by AutoScalingGroupName, Region, AccountId, _sourceCategory',
                resource_field="AutoScalingGroupName",
            ),
            SumoQueryJob(
                name="ec2_available_memory",
                metric_name="sumo.ec2.available_memory.mbytes",
                unit="megabytes",
                query_kind="metrics",
                query=f'{autoscaling_scope} metric="Memory Available MBytes" | avg by AutoScalingGroupName, Region, AccountId, _sourceCategory',
                resource_field="AutoScalingGroupName",
            ),
            SumoQueryJob(
                name="ec2_network_in",
                metric_name="sumo.ec2.network.in.bytes",
                unit="bytes",
                query_kind="metrics",
                query=f'{autoscaling_scope} metric=NetworkIn | avg by AutoScalingGroupName, Region, AccountId, _sourceCategory',
                resource_field="AutoScalingGroupName",
            ),
            SumoQueryJob(
                name="ec2_network_out",
                metric_name="sumo.ec2.network.out.bytes",
                unit="bytes",
                query_kind="metrics",
                query=f'{autoscaling_scope} metric=NetworkOut | avg by AutoScalingGroupName, Region, AccountId, _sourceCategory',
                resource_field="AutoScalingGroupName",
            ),
            SumoQueryJob(
                name="ec2_ebs_read_ops",
                metric_name="sumo.ec2.ebs.read_ops",
                unit="count",
                query_kind="metrics",
                query=f'{autoscaling_scope} metric=EBSReadOps | avg by AutoScalingGroupName, Region, AccountId, _sourceCategory',
                resource_field="AutoScalingGroupName",
            ),
            SumoQueryJob(
                name="ec2_ebs_write_ops",
                metric_name="sumo.ec2.ebs.write_ops",
                unit="count",
                query_kind="metrics",
                query=f'{autoscaling_scope} metric=EBSWriteOps | avg by AutoScalingGroupName, Region, AccountId, _sourceCategory',
                resource_field="AutoScalingGroupName",
            ),
            SumoQueryJob(
                name="alb_healthy_host_count",
                metric_name="sumo.alb.healthy_host_count",
                unit="count",
                query_kind="metrics",
                query=f'{alb_scope} metric=HealthyHostCount | avg by LoadBalancer, Region, AccountId, _sourceCategory',
                resource_field="LoadBalancer",
            ),
            SumoQueryJob(
                name="alb_target_response_time",
                metric_name="sumo.alb.target_response_time.ms",
                unit="seconds",
                query_kind="metrics",
                query=f'{alb_scope} metric=TargetResponseTime | avg by LoadBalancer, Region, AccountId, _sourceCategory',
                resource_field="LoadBalancer",
            ),
            SumoQueryJob(
                name="rds_cpu_utilization",
                metric_name="sumo.rds.cpu.utilization",
                unit="percent",
                query_kind="metrics",
                query=f'{database_scope} metric=CPUUtilization | avg by DBInstanceIdentifier, Region, AccountId, _sourceCategory, "enablon:client"',
                resource_field="DBInstanceIdentifier",
            ),
            SumoQueryJob(
                name="rds_database_connections",
                metric_name="sumo.rds.connections",
                unit="count",
                query_kind="metrics",
                query=f'{database_scope} metric=DatabaseConnections | avg by DBInstanceIdentifier, Region, AccountId, _sourceCategory, "enablon:client"',
                resource_field="DBInstanceIdentifier",
            ),
            SumoQueryJob(
                name="rds_freeable_memory",
                metric_name="sumo.rds.freeable_memory.bytes",
                unit="bytes",
                query_kind="metrics",
                query=f'{database_scope} metric=FreeableMemory | avg by DBInstanceIdentifier, Region, AccountId, _sourceCategory, "enablon:client"',
                resource_field="DBInstanceIdentifier",
            ),
            SumoQueryJob(
                name="rds_free_storage",
                metric_name="sumo.rds.free_storage.bytes",
                unit="bytes",
                query_kind="metrics",
                query=f'{database_scope} metric=FreeStorageSpace | avg by DBInstanceIdentifier, Region, AccountId, _sourceCategory, "enablon:client"',
                resource_field="DBInstanceIdentifier",
            ),
            SumoQueryJob(
                name="rds_read_iops",
                metric_name="sumo.rds.read_iops",
                unit="count",
                query_kind="metrics",
                query=f'{database_scope} metric=ReadIOPS | avg by DBInstanceIdentifier, Region, AccountId, _sourceCategory, "enablon:client"',
                resource_field="DBInstanceIdentifier",
            ),
            SumoQueryJob(
                name="rds_write_iops",
                metric_name="sumo.rds.write_iops",
                unit="count",
                query_kind="metrics",
                query=f'{database_scope} metric=WriteIOPS | avg by DBInstanceIdentifier, Region, AccountId, _sourceCategory, "enablon:client"',
                resource_field="DBInstanceIdentifier",
            ),
            SumoQueryJob(
                name="rds_read_throughput",
                metric_name="sumo.rds.read_throughput.bytes_per_sec",
                unit="bytes_per_second",
                query_kind="metrics",
                query=f'{database_scope} metric=ReadThroughput | avg by DBInstanceIdentifier, Region, AccountId, _sourceCategory, "enablon:client"',
                resource_field="DBInstanceIdentifier",
            ),
            SumoQueryJob(
                name="rds_write_throughput",
                metric_name="sumo.rds.write_throughput.bytes_per_sec",
                unit="bytes_per_second",
                query_kind="metrics",
                query=f'{database_scope} metric=WriteThroughput | avg by DBInstanceIdentifier, Region, AccountId, _sourceCategory, "enablon:client"',
                resource_field="DBInstanceIdentifier",
            ),
            SumoQueryJob(
                name="rds_read_latency",
                metric_name="sumo.rds.read_latency.ms",
                unit="seconds",
                query_kind="metrics",
                query=f'{database_scope} metric=ReadLatency | avg by DBInstanceIdentifier, Region, AccountId, _sourceCategory, "enablon:client"',
                resource_field="DBInstanceIdentifier",
            ),
            SumoQueryJob(
                name="rds_write_latency",
                metric_name="sumo.rds.write_latency.ms",
                unit="seconds",
                query_kind="metrics",
                query=f'{database_scope} metric=WriteLatency | avg by DBInstanceIdentifier, Region, AccountId, _sourceCategory, "enablon:client"',
                resource_field="DBInstanceIdentifier",
            ),
        ]

    def _build_http_log_query_jobs(self) -> list[SumoQueryJob]:
        base_scope = f"_sourceCategory={self.settings.sumologic_http_logs_source_category_pattern}"
        parsed_scope = (
            f"{base_scope} | parse regex field=_raw "
            f'"{SUMOLOGIC_HTTP_LOG_PARSE_EXPRESSION}" '
            '| if(request_size_token = "-", 0, num(request_size_token)) as request_size_bytes '
            "| num(response_size) as response_size_bytes "
            "| num(time_taken_ms) as time_taken_ms "
            "| num(read_time_ms) as read_time_ms "
            "| num(write_time_ms) as write_time_ms "
        )
        slice_window = f"{self.settings.sumologic_query_window_minutes}m"

        return [
            SumoQueryJob(
                name="permitvision_http_call_count",
                metric_name="sumo.http.call.count",
                unit="count",
                query_kind="logs",
                query=(
                    f"{parsed_scope}| timeslice {slice_window} "
                    "| count as metric_value by _timeslice, _sourceHost, status_code, http_method, request_path, _sourceCategory"
                ),
                resource_field="_sourceHost",
            ),
            SumoQueryJob(
                name="permitvision_http_status_count",
                metric_name="sumo.http.status.count",
                unit="count",
                query_kind="logs",
                query=(
                    f"{parsed_scope}| timeslice {slice_window} "
                    "| count as metric_value by _timeslice, _sourceHost, status_code, _sourceCategory"
                ),
                resource_field="_sourceHost",
            ),
            SumoQueryJob(
                name="permitvision_http_request_size",
                metric_name="sumo.http.request.size.bytes",
                unit="bytes",
                query_kind="logs",
                query=(
                    f"{parsed_scope}| timeslice {slice_window} "
                    "| avg(request_size_bytes) as metric_value by _timeslice, _sourceHost, http_method, request_path, _sourceCategory"
                ),
                resource_field="_sourceHost",
            ),
            SumoQueryJob(
                name="permitvision_http_response_size",
                metric_name="sumo.http.response.size.bytes",
                unit="bytes",
                query_kind="logs",
                query=(
                    f"{parsed_scope}| timeslice {slice_window} "
                    "| avg(response_size_bytes) as metric_value by _timeslice, _sourceHost, http_method, request_path, _sourceCategory"
                ),
                resource_field="_sourceHost",
            ),
            SumoQueryJob(
                name="permitvision_http_time_taken",
                metric_name="sumo.http.duration.ms",
                unit="milliseconds",
                query_kind="logs",
                query=(
                    f"{parsed_scope}| timeslice {slice_window} "
                    "| avg(time_taken_ms) as metric_value by _timeslice, _sourceHost, http_method, request_path, _sourceCategory"
                ),
                resource_field="_sourceHost",
            ),
            SumoQueryJob(
                name="permitvision_http_read_time",
                metric_name="sumo.http.read_time.ms",
                unit="milliseconds",
                query_kind="logs",
                query=(
                    f"{parsed_scope}| timeslice {slice_window} "
                    "| avg(read_time_ms) as metric_value by _timeslice, _sourceHost, http_method, request_path, _sourceCategory"
                ),
                resource_field="_sourceHost",
            ),
            SumoQueryJob(
                name="permitvision_http_write_time",
                metric_name="sumo.http.write_time.ms",
                unit="milliseconds",
                query_kind="logs",
                query=(
                    f"{parsed_scope}| timeslice {slice_window} "
                    "| avg(write_time_ms) as metric_value by _timeslice, _sourceHost, http_method, request_path, _sourceCategory"
                ),
                resource_field="_sourceHost",
            ),
            SumoQueryJob(
                name="permitvision_app_errors",
                metric_name="app.error.count",
                unit="count",
                query_kind="logs",
                query=(
                    "_sourceCategory=*permitvision* and (_sourceCategory=*diagnostics or _sourceCategory=*errors) "
                    '| where loglevel matches "Error" '
                    'or toLowerCase(_raw) matches "*exception*" '
                    'or toLowerCase(_raw) matches "*fail*" '
                    "| parse regex field=_sourceCategory "
                    '"(?<environment>[^/]+)/(?<tenant>[^/]+)/(?<deployment>[^/]+)/(?<component>[^/]+)/(?<stream>[^/]+)" '
                    '| format("%s/%s/%s", tenant, deployment, component) as resource_id '
                    f"| timeslice {slice_window} "
                    "| count as metric_value by _timeslice, resource_id, _sourceCategory"
                ),
                resource_field="resource_id",
            ),
            SumoQueryJob(
                name="restart_spikes",
                metric_name="restart.count",
                unit="count",
                query_kind="logs",
                query=(
                    '("CrashLoopBackOff" or "OOMKilled" or "restarting failed container" or restart) '
                    '| parse regex field=_sourceCategory "(?<environment>[^/]+)/(?<resource_path>.*)" '
                    f"| timeslice {slice_window} "
                    "| count as metric_value by _timeslice, resource_path, _sourceCategory"
                ),
                resource_field="resource_path",
            ),
            SumoQueryJob(
                name="slow_query_aggregates",
                metric_name="db.slow_query.count",
                unit="count",
                query_kind="logs",
                query=(
                    '("slow query" or "Query_time=" or deadlock or "lock wait") '
                    '| parse regex field=_sourceCategory "(?<environment>[^/]+)/(?<resource_path>.*)" '
                    f"| timeslice {slice_window} "
                    "| count as metric_value by _timeslice, resource_path, _sourceCategory"
                ),
                resource_field="resource_path",
            ),
            SumoQueryJob(
                name="incident_context_summaries",
                metric_name="incident.count",
                unit="count",
                query_kind="logs",
                query=(
                    "(incident or alert or pagerduty or sentry) "
                    '| parse regex field=_sourceCategory "(?<environment>[^/]+)/(?<resource_path>.*)" '
                    "| timeslice 1h "
                    "| count as metric_value by _timeslice, resource_path, _sourceCategory"
                ),
                resource_field="resource_path",
            ),
        ]

    def _execute_query_job(
        self,
        job: SumoQueryJob,
        window_start: datetime,
        window_end: datetime,
    ) -> list[RawMetricPayload]:
        if job.query_kind == "metrics":
            rows = self._execute_metrics_query(job.query, window_start, window_end)
            return self._metric_rows_to_payloads(job, rows)
        rows = self._execute_log_query(job.query, window_start, window_end)
        return self._log_rows_to_payloads(job, rows)

    def _max_workers_for_jobs(self, job_count: int) -> int:
        configured = max(1, self.settings.sumologic_max_workers)
        return max(1, min(job_count or 1, configured))

    def _execute_log_query(self, query: str, window_start: datetime, window_end: datetime) -> list[dict[str, Any]]:
        job_id = self._create_search_job(query, window_start, window_end)
        try:
            self._wait_for_job(job_id)
            return self._fetch_records(job_id)
        finally:
            self._delete_job(job_id)

    def _execute_metrics_query(self, query: str, window_start: datetime, window_end: datetime) -> list[dict[str, Any]]:
        payload = json.dumps(
            {
                "query": [{"rowId": "A", "query": query}],
                "startTime": int(window_start.timestamp() * 1000),
                "endTime": int(window_end.timestamp() * 1000),
                "maxDataPoints": 1000,
            }
        ).encode("utf-8")
        url = f"{self.settings.sumologic_api_url}/metrics/results"
        response_payload = self._request_json(url, method="POST", data=payload)
        rows = response_payload.get("response", [])
        if not rows:
            if response_payload.get("error"):
                raise RuntimeError(f"Sumo Logic metrics query failed: {response_payload.get('keyedErrors') or response_payload}")
            return []
        return rows[0].get("results", [])

    def _create_search_job(self, query: str, window_start: datetime, window_end: datetime) -> str:
        payload = json.dumps(
            {
                "query": query,
                "from": window_start.strftime("%Y-%m-%dT%H:%M:%S"),
                "to": window_end.strftime("%Y-%m-%dT%H:%M:%S"),
                "timeZone": "UTC",
            }
        ).encode("utf-8")
        url = f"{self.settings.sumologic_api_url}/search/jobs"
        response_payload = self._request_json(url, method="POST", data=payload)
        job_id = response_payload.get("id")
        if not job_id:
            raise RuntimeError("Sumo Logic search job did not return an id.")
        return str(job_id)

    def _wait_for_job(self, job_id: str) -> None:
        url = f"{self.settings.sumologic_api_url}/search/jobs/{job_id}"
        last_state = ""
        for _ in range(30):
            response_payload = self._request_json(url, method="GET")
            last_state = str(response_payload.get("state", ""))
            if response_payload.get("recordCount", 0) > 0 or last_state in {
                "DONE GATHERING RESULTS",
                "DONE GATHERING HISTOGRAM",
            }:
                return
            time.sleep(2)
        raise RuntimeError(f"Timed out waiting for Sumo Logic search job. Last state: {last_state}")

    def _fetch_records(self, job_id: str) -> list[dict[str, Any]]:
        url = f"{self.settings.sumologic_api_url}/search/jobs/{job_id}/records?offset=0&limit=1000"
        response_payload = self._request_json(url, method="GET")
        return response_payload.get("records", [])

    def _delete_job(self, job_id: str) -> None:
        url = f"{self.settings.sumologic_api_url}/search/jobs/{job_id}"
        try:
            self._request_json(url, method="DELETE")
        except RuntimeError:
            return

    def _request_json(self, url: str, method: str, data: bytes | None = None) -> dict[str, Any]:
        token = b64encode(
            f"{self.settings.sumologic_access_id}:{self.settings.sumologic_access_key}".encode("utf-8")
        ).decode("ascii")
        http_request = request.Request(
            url,
            data=data,
            headers={
                "Authorization": f"Basic {token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            method=method,
        )
        try:
            with self._get_opener().open(http_request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"Sumo Logic API error ({exc.code}): {error_body}") from exc
        except error.URLError as exc:
            raise RuntimeError("Unable to reach Sumo Logic API.") from exc

    def _get_opener(self) -> request.OpenerDirector:
        opener = getattr(self._thread_local, "opener", None)
        if opener is None:
            opener = request.build_opener(request.HTTPCookieProcessor(CookieJar()))
            self._thread_local.opener = opener
        return opener

    def _log_rows_to_payloads(self, job: SumoQueryJob, rows: list[dict[str, Any]]) -> list[RawMetricPayload]:
        payloads: list[RawMetricPayload] = []
        for row in rows:
            row_map = row.get("map", row)
            external_resource_id = row_map.get(job.resource_field)
            timestamp = row_map.get("_timeslice")
            value = row_map.get("metric_value")
            source_category = row_map.get("_sourcecategory") or row_map.get("_sourceCategory")
            if external_resource_id in {None, ""} or timestamp is None or value is None:
                continue
            payloads.append(
                RawMetricPayload(
                    source=self.source_name,
                    external_resource_id=str(external_resource_id),
                    metric_name=job.metric_name,
                    timestamp=self._parse_timestamp(timestamp),
                    value=float(value),
                    unit=job.unit,
                    dimensions={
                        "query_mode": "logs",
                        "source_category": source_category,
                        **self._sanitized_dimensions(row_map, excluded={job.resource_field, "_timeslice", "metric_value"}),
                    },
                )
            )
        return payloads

    def _metric_rows_to_payloads(self, job: SumoQueryJob, rows: list[dict[str, Any]]) -> list[RawMetricPayload]:
        payloads: list[RawMetricPayload] = []
        for row in rows:
            metric_def = row.get("metric", {})
            datapoints = row.get("datapoints", {})
            dimensions = {
                item["key"]: item["value"]
                for item in metric_def.get("dimensions", [])
                if "key" in item and "value" in item and item["key"] != "metric"
            }
            external_resource_id = dimensions.get(job.resource_field)
            timestamps = datapoints.get("timestamp", [])
            values = datapoints.get("value", [])
            if external_resource_id in {None, ""}:
                continue
            for timestamp, value in zip(timestamps, values, strict=False):
                if value is None:
                    continue
                payloads.append(
                    RawMetricPayload(
                        source=self.source_name,
                        external_resource_id=str(external_resource_id),
                        metric_name=job.metric_name,
                        timestamp=self._parse_timestamp(timestamp),
                        value=float(value),
                        unit=job.unit,
                        dimensions={
                            "query_mode": "metrics",
                            **self._sanitized_dimensions(dimensions, excluded={job.resource_field}),
                        },
                    )
                )
        return payloads

    @staticmethod
    def _sanitized_dimensions(row_map: dict[str, Any], excluded: set[str]) -> dict[str, Any]:
        return {
            str(key): value
            for key, value in row_map.items()
            if key not in excluded and value not in {None, ""}
        }

    @staticmethod
    def _wildcard_value(value: str) -> str:
        if "*" in value or "?" in value:
            return value
        compact = "*".join(part for part in value.split() if part)
        return f"*{compact}*" if compact else "*"

    @staticmethod
    def _parse_timestamp(value: Any) -> datetime:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc)
        if isinstance(value, str) and value.isdigit():
            return datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc)
        if isinstance(value, str):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        raise ValueError(f"Unsupported timestamp value: {value!r}")
