# MVP Scope

## In Scope
- separate Python repository
- read-only connectors for Datadog, CloudWatch, and Sumo Logic
- normalized internal time-series model
- analysis of up to 5 resources in 1 environment
- support for two resource classes:
  - application service / app server
  - database instance
- trailing 30-day default analysis window
- recommendation types:
  - `scale_down`
  - `hold`
  - `watchlist`
  - `scale_up`
  - `insufficient_data`
- human review flow:
  - approve
  - reject
  - snooze
- weekly portfolio report
- agent-assisted summaries and operator Q&A

## Out Of Scope
- production-grade autoscaling
- automatic ticket creation
- cross-account cloud action execution
- budget ownership workflows
- horizontal pod autoscaling policy generation
- multi-region forecasting and simulation

## Success Criteria
- ingest at least 30 days of sample or real metric history for at least 3 resources
- produce at least one realistic `scale_down` candidate and one `hold` or `watchlist` result
- each recommendation contains:
  - evidence
  - confidence
  - guardrails
  - estimated monthly savings or explicit `not available`
- analysis run completes successfully with partial source failure
- no infrastructure changes are executed by the app

## Assumptions
- platform team can obtain read-only access to source systems
- a small manually maintained cost profile is acceptable for MVP
- the initial review audience is internal operations, not end customers
