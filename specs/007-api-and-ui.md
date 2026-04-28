# API And UI

## API Endpoints

### POST /api/ingestion/run
Triggers ingestion for configured sources.

Response:
- accepted run id
- source run status

### POST /api/analysis/run
Triggers analysis for all active resources or a requested subset.

Response:
- analysis run id
- number of resources queued

### GET /api/resources
List known resources.

Filters:
- environment
- resource_type
- active_only

### GET /api/resources/{resource_id}
Get resource detail and latest recommendation summary.

### GET /api/recommendations
List recommendations with filtering.

Filters:
- recommendation_type
- confidence
- status
- environment

### GET /api/recommendations/{recommendation_id}
Detailed recommendation view.

### POST /api/recommendations/{recommendation_id}/review
Persist approve / reject / snooze decision and comment.

### GET /api/reports/latest
Get latest weekly portfolio report.

### POST /api/review-assistant/ask
Ask follow-up questions about a saved recommendation.

## UI Views

### Portfolio Overview
Shows:
- resource
- type
- current size
- recommendation
- confidence
- estimated savings
- last analyzed at
- data freshness

### Resource Detail
Shows:
- 30-day trend cards
- pattern tags
- evidence list
- guardrails
- review history

### Weekly Report
Shows:
- top cost-saving opportunities
- top risk hotspots
- unresolved data issues
- overall potential savings

### Review Queue
Shows:
- all draft recommendations
- action buttons
- reviewer comment field

## UI Edge Cases
- empty portfolio
- stale data warning
- source outage banner
- missing cost estimate
- insufficient data recommendation

## Acceptance Criteria
- every UI recommendation row links to full evidence
- operator can review without using a chat flow
