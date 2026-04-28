# Testing And Edge Cases

## Testing Layers

### Unit Tests
- canonical metric mapping
- unit conversion
- UTC normalization
- percentile calculations
- anomaly detection helper logic
- recommendation validator rules
- savings calculation fallback behavior

### Integration Tests
- Datadog connector parsing
- CloudWatch connector parsing
- Sumo aggregate query parsing
- Sumo Logic sample-mode connector fallback
- ingestion persistence
- analysis snapshot creation
- end-to-end recommendation generation

### Contract Tests
- agent structured outputs conform to schema
- API response shape remains stable

### Golden Tests
- fixed input fixture produces fixed recommendation and report summary

## Required Edge Case Coverage

### Data Availability
- insufficient history
- one source fully stale
- all primary sources stale
- missing cost profile
- sparse metrics for new resource
- supporting-only Sumo Logic outage while primary sources remain healthy

### Identity / Mapping
- unresolved resource mapping
- resource renamed in one source
- deleted resource with old data still present

### Signal Quality
- low CPU, high memory
- low utilization but high latency at peak
- DB CPU low but connections high
- one-off anomaly in otherwise idle service
- maintenance window causing false idle behavior

### Agentic Failure
- malformed pattern agent output
- unsupported recommendation type from agent
- report agent timeout
- agent hallucinated metric names

### Review Flow
- duplicate approval request
- reject without comment
- snooze past report boundary

## MVP Acceptance Checklist
- specs reviewed and approved before implementation begins
- no production write actions exist in code
- deterministic fallback path exists for every agent-enabled step
- edge cases above have at least one test or explicit deferred note
- API implementation exposes the same contracts through FastAPI as through the reviewed spec surface
