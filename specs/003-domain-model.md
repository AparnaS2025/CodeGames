# Domain Model

## Resource
Represents a capacity-managed entity.

Required fields:
- `resource_id`
- `name`
- `resource_type`
- `environment`
- `current_size`
- `metadata_json`

Allowed `resource_type` values for MVP:
- `app_service`
- `db_instance`

Example metadata:
- cloud account
- region
- service tags
- database engine
- autoscaling enabled flag

## Raw Metric Payload
Temporary source-shaped object returned by a connector before normalization.

Fields:
- `source`
- `external_resource_id`
- `metric_name`
- `timestamp`
- `value`
- `unit`
- `dimensions`

Connector notes for MVP:
- Datadog and CloudWatch provide primary sample-backed payloads in local development
- Sumo Logic uses a dedicated connector module and may run in `sample` or `api` mode
- connector health is persisted through `source_cursors.health_json`

## Normalized Metric Point
Canonical metric point stored after normalization.

Fields:
- `resource_id`
- `metric_name`
- `timestamp_utc`
- `value`
- `unit`
- `source`
- `dimensions_json`

Canonical metric names for MVP:
- `cpu_percent`
- `memory_percent`
- `request_rate`
- `error_rate`
- `latency_p95_ms`
- `restart_count`
- `db_cpu_percent`
- `db_connections`
- `db_read_latency_ms`
- `db_write_latency_ms`
- `db_iops`
- `storage_used_gb`

## Analysis Snapshot
Frozen summary of features used in one recommendation run.

Fields:
- `snapshot_id`
- `resource_id`
- `window_start_utc`
- `window_end_utc`
- `source_freshness_json`
- `computed_features_json`
- `pattern_candidates_json`

## Recommendation
Final validated advisory output.

Fields:
- `recommendation_id`
- `resource_id`
- `recommendation_type`
- `current_size`
- `suggested_size`
- `confidence`
- `risk_level`
- `estimated_monthly_savings`
- `evidence_json`
- `guardrails_json`
- `pattern_summary`
- `report_summary`
- `status`

Allowed `status` values:
- `draft`
- `approved`
- `rejected`
- `snoozed`

## Review Decision
Human review attached to a recommendation.

Fields:
- `review_id`
- `recommendation_id`
- `decision`
- `reviewer`
- `comment`
- `created_at_utc`

## Report Snapshot
Point-in-time portfolio summary.

Fields:
- `report_id`
- `report_type`
- `created_at_utc`
- `scope_json`
- `summary_text`
- `details_json`
