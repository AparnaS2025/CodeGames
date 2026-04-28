# Ingestion And Normalization

## Sources

### Datadog
Primary source for:
- CPU
- memory
- latency
- error rate
- app/container restarts

### AWS CloudWatch
Primary source for:
- EC2 or container-level utilization
- RDS CPU
- RDS connections
- read/write latency
- IOPS
- storage growth

### Sumo Logic
Primary source for this implementation:
- production CloudWatch-backed EC2 metrics for PermitVision autoscaling groups
- production CloudWatch-backed RDS metrics for PermitVision customer databases
- production CloudWatch-backed ALB health and response-time metrics

Supporting source for:
- incident context
- error count aggregates
- restart spike evidence
- slow-query event aggregates
- optionally derived HTTP metrics from PermitVision access logs when HTTP log queries are enabled

Sumo Logic is not the primary source of utilization decisions in the MVP.
The connector runs in `sample` mode by default for local execution and can switch to `api` mode when read-only credentials are supplied.

## Connector Requirements
- read-only credentials only
- each connector must support a requested time window
- each connector must record the last successful fetch window
- each connector must be retry-safe
- each connector must expose source health status
- Sumo Logic API mode must degrade safely to supporting-signal loss instead of blocking primary-source ingestion

## Sumo Logic Mapping Table

| raw source name | canonical metric name | unit conversion | aggregation grain | required for recommendation safety |
| --- | --- | --- | --- | --- |
| `app.error.rate` | `error_rate` | preserve percent or count-derived rate | 30 minutes | no |
| `db.slow_query.count` | `db_slow_query_count` | preserve count | 30 minutes | no |
| `sumo.ec2.cpu.utilization` | `ec2_cpu_percent` | preserve percent | source rollup | yes for Sumo-only app analysis |
| `sumo.ec2.memory.utilization` | `memory_percent` | preserve percent | source rollup | yes for Sumo-only app analysis |
| `sumo.ec2.available_memory.mbytes` | `available_memory_mbytes` | preserve megabytes | source rollup | no |
| `sumo.ec2.network.in.bytes` | `network_in_bytes` | preserve bytes | source rollup | no |
| `sumo.ec2.network.out.bytes` | `network_out_bytes` | preserve bytes | source rollup | no |
| `sumo.ec2.ebs.read_ops` | `ebs_read_ops` | preserve count | source rollup | no |
| `sumo.ec2.ebs.write_ops` | `ebs_write_ops` | preserve count | source rollup | no |
| `sumo.alb.healthy_host_count` | `healthy_host_count` | preserve count | source rollup | no |
| `sumo.alb.target_response_time.ms` | `target_response_time_ms` | convert seconds to milliseconds if needed | source rollup | no |
| `sumo.rds.cpu.utilization` | `db_cpu_percent` | preserve percent | source rollup | yes |
| `sumo.rds.connections` | `db_connections` | preserve count | source rollup | yes |
| `sumo.rds.freeable_memory.bytes` | `db_freeable_memory_bytes` | preserve bytes | source rollup | no |
| `sumo.rds.free_storage.bytes` | `db_free_storage_bytes` | preserve bytes | source rollup | no |
| `sumo.rds.read_iops` | `db_read_iops` | preserve count | source rollup | no |
| `sumo.rds.write_iops` | `db_write_iops` | preserve count | source rollup | no |
| `sumo.rds.read_throughput.bytes_per_sec` | `db_read_throughput_bps` | preserve bytes/s | source rollup | no |
| `sumo.rds.write_throughput.bytes_per_sec` | `db_write_throughput_bps` | preserve bytes/s | source rollup | no |
| `sumo.rds.read_latency.ms` | `db_read_latency_ms` | preserve ms | source rollup | yes |
| `sumo.rds.write_latency.ms` | `db_write_latency_ms` | preserve ms | source rollup | yes |
| `sumo.http.call.count` | `request_count` | preserve count | 30 minutes | no |
| `sumo.http.status.count` | `http_status_count` | preserve count | 30 minutes | no |
| `sumo.http.request.size.bytes` | `http_request_size_bytes` | preserve bytes | 30 minutes | no |
| `sumo.http.response.size.bytes` | `http_response_size_bytes` | preserve bytes | 30 minutes | no |
| `sumo.http.duration.ms` | `http_duration_ms` | preserve ms | 30 minutes | no |
| `sumo.http.read_time.ms` | `http_read_time_ms` | preserve ms | 30 minutes | no |
| `sumo.http.write_time.ms` | `http_write_time_ms` | preserve ms | 30 minutes | no |

Precedence rule:
- Sumo Logic is the primary source for PermitVision production recommendation inputs in this implementation.
- Native app telemetry should still take precedence over log-derived HTTP rollups when production PermitVision app metrics become available end to end.
- HTTP log-derived metrics are supporting signals until production PermitVision HTTP streams are validated end to end.

## Ingestion Job Behavior
- ingestion job runs on a configurable interval
- each source fetches independently
- a source failure must not block successful sources
- repeated job execution over the same window must be idempotent

## Normalization Rules
- all timestamps converted to UTC
- all percentages normalized to `0-100`
- all byte-based values normalized consistently
- metric names mapped to canonical names
- resource identity resolved before persistence
- no silent zero-filling for missing data

## Resource Mapping
Resource mapping priority:
1. explicit configured mapping
2. deterministic tag match
3. source-specific alias table
4. unresolved state requiring review

If a metric cannot be mapped to a resource:
- do not discard silently
- store as unresolved ingestion issue
- exclude from analysis until resolved

## Data Freshness Rules
- data freshness tracked per source and resource
- analysis must mark source stale if latest point exceeds threshold
- stale source data lowers recommendation confidence
- if all primary sources are stale, return `insufficient_data`

## Edge Cases
- API rate limit
- empty result set for valid query window
- partial source outage
- credentials expired mid-run
- Sumo Logic API configured but unreachable
- Sumo Logic rows missing resource alias fields
- resource renamed in one source only
- duplicate metric points returned on overlapping fetches
- units differ between environments
- new resource with less than 7 days of history
- deleted resource still present in old source data

## Acceptance Criteria
- overlapping ingestion windows do not create duplicate normalized points
- unresolved resources are visible in source health output
- source staleness is queryable and reviewable
