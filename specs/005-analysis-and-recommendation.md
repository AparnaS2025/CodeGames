# Analysis And Recommendation

## Deterministic Feature Set
Compute at minimum:
- p50, p95, p99 CPU
- p50, p95 memory
- p95 latency
- p95 error rate
- weekday vs weekend utilization difference
- hour-of-week baseline
- number of sustained high-utilization windows
- number of sustained low-utilization windows
- latest source freshness age
- insufficient-data flag

For databases also compute:
- p95 connections
- p95 read latency
- p95 write latency
- p95 IOPS
- storage growth trend

## Pattern Tags
Possible tags:
- `steady_underutilized`
- `steady_saturated`
- `business_hours_peak`
- `weekend_idle`
- `monthly_peak_pattern`
- `short_lived_anomaly`
- `deploy_correlated_spike`
- `stale_source_data`
- `insufficient_data`
- `conflicting_signals`

## Recommendation Types

### scale_down
Use only when:
- low utilization is sustained
- latency and error behavior remain healthy during peaks
- no contradictory bottleneck signal exists
- data freshness is acceptable
- recommendation does not violate minimum size constraint

### hold
Use when:
- current size appears appropriate
- or confidence is not sufficient for a change

### watchlist
Use when:
- there are early signs of future over- or under-provisioning
- or burst behavior needs a longer observation window

### scale_up
Use when:
- saturation is sustained
- business-hour peaks cause latency or error pressure
- or database pressure indicates current size is insufficient

### insufficient_data
Use when:
- primary metrics are missing or stale
- history window is too short
- or resource mapping is unresolved

## Recommendation Policy Rules
- never recommend below configured minimum size
- database scale-down requires stricter evidence than app-service scale-down
- any stale primary source downgrades confidence at least one level
- conflicting signals must prevent high-confidence scale-down
- recent severe anomaly windows should bias toward `hold` or `watchlist`

## Confidence Levels
- `high`: strong, consistent multi-signal evidence
- `medium`: acceptable evidence with some uncertainty
- `low`: incomplete, conflicting, or stale evidence

## Risk Levels
- `low`: action is conservative and guarded
- `medium`: action needs monitoring after change
- `high`: recommendation exists but risk of regression is meaningful

## Evidence Requirements
Every recommendation must include:
- at least three evidence points
- at least one explicit risk or guardrail statement
- at least one explicit reason for why more aggressive action was not chosen

## Estimated Savings
Savings input may come from:
- static size-to-cost map in config
- manually maintained cost profile table

If cost data is missing:
- recommendation still allowed
- savings field becomes `null`
- report must state `cost estimate unavailable`

## Edge Cases
- low CPU but high memory
- low average usage but narrow high peak causing latency
- database low CPU but high connection pressure
- autoscaled service where current size changes often
- burstable instance credits masking sustained load
- maintenance windows creating misleading idle periods
- one-off incident dominating p99 values

## Acceptance Criteria
- recommendation validator can reject an agent draft
- final recommendation remains explainable without requiring model reasoning text
