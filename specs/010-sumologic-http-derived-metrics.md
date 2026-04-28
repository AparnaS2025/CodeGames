# Sumo Logic HTTP-Derived Metrics

## Goal

Derive PermitVision HTTP request signals from Sumo Logic access logs when native production app telemetry is missing or incomplete.

The current implementation exposes:
- a shared Sumo parse expression for PermitVision-style HTTP access logs
- log-query templates that aggregate counts, status counts, request size, response size, and timing fields
- canonical metric mappings for the normalized metrics store

## Parse Expression

Use the same regular expression as the runtime connector:

```text
^(?<event_date>\S+)\s+(?<event_time>\S+)\s+(?<server_ip>\S+)\s+(?<application_name>.+?)\s+-\s+(?<client_ip>\S+)\s+(?<server_port>\d+)\s+(?<http_method>GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+(?<request_path>\S+)\s+(?<query_token>\S+)\s+(?<request_id>\S+)\s+(?<status_code>\d{3})\s+(?<user_agent>\S+)\s+(?<request_content_type>\S+)\s+(?<request_size_token>\S+)\s+(?<response_content_type>\S+)\s+(?<response_encoding>\S+)\s+(?<response_size>\d+)\s+(?<time_taken_ms>\d+)\s+(?<read_time_ms>\d+)\s+(?<write_time_ms>\d+)$
```

## Parsed Fields

Fields that are safe to use immediately:
- `_sourceHost`
- `application_name`
- `http_method`
- `request_path`
- `status_code`
- `request_size_token`
- `response_size`
- `time_taken_ms`
- `read_time_ms`
- `write_time_ms`

Fields intentionally not mapped yet:
- concurrent users
- time-to-first-byte variants

Those require either a confirmed log-format contract or a production OTel stream that already publishes the semantic values.

## Query Templates

Assume `_sourceCategory=*permitvision/http`.

### Calls Per Machine

```text
_sourceCategory=*permitvision/http
| parse regex field=_raw "<PARSE_EXPRESSION>"
| timeslice 30m
| count as metric_value by _timeslice, _sourceHost, status_code, http_method, request_path, _sourceCategory
```

### Calls Per Status

```text
_sourceCategory=*permitvision/http
| parse regex field=_raw "<PARSE_EXPRESSION>"
| timeslice 30m
| count as metric_value by _timeslice, _sourceHost, status_code, _sourceCategory
```

### Average Request Size

```text
_sourceCategory=*permitvision/http
| parse regex field=_raw "<PARSE_EXPRESSION>"
| if(request_size_token = "-", 0, num(request_size_token)) as request_size_bytes
| timeslice 30m
| avg(request_size_bytes) as metric_value by _timeslice, _sourceHost, http_method, request_path, _sourceCategory
```

### Average Response Size

```text
_sourceCategory=*permitvision/http
| parse regex field=_raw "<PARSE_EXPRESSION>"
| num(response_size) as response_size_bytes
| timeslice 30m
| avg(response_size_bytes) as metric_value by _timeslice, _sourceHost, http_method, request_path, _sourceCategory
```

### Average Time Taken

```text
_sourceCategory=*permitvision/http
| parse regex field=_raw "<PARSE_EXPRESSION>"
| num(time_taken_ms) as time_taken_ms
| timeslice 30m
| avg(time_taken_ms) as metric_value by _timeslice, _sourceHost, http_method, request_path, _sourceCategory
```

### Average Read Time

```text
_sourceCategory=*permitvision/http
| parse regex field=_raw "<PARSE_EXPRESSION>"
| num(read_time_ms) as read_time_ms
| timeslice 30m
| avg(read_time_ms) as metric_value by _timeslice, _sourceHost, http_method, request_path, _sourceCategory
```

### Average Write Time

```text
_sourceCategory=*permitvision/http
| parse regex field=_raw "<PARSE_EXPRESSION>"
| num(write_time_ms) as write_time_ms
| timeslice 30m
| avg(write_time_ms) as metric_value by _timeslice, _sourceHost, http_method, request_path, _sourceCategory
```

## Canonical Metric Mapping

- `sumo.http.call.count` -> `request_count`
- `sumo.http.status.count` -> `http_status_count`
- `sumo.http.request.size.bytes` -> `http_request_size_bytes`
- `sumo.http.response.size.bytes` -> `http_response_size_bytes`
- `sumo.http.duration.ms` -> `http_duration_ms`
- `sumo.http.read_time.ms` -> `http_read_time_ms`
- `sumo.http.write_time.ms` -> `http_write_time_ms`

## Operational Notes

- These derived metrics are off by default in runtime configuration.
- Enable them only when the target environment has matching PermitVision HTTP log streams.
- Production PermitVision HTTP telemetry should still prefer native app metrics when they become available.
