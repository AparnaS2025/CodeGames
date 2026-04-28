---
name: capacity-add-source-connector
description: Use when adding a new observability source connector to the Capacity Intelligence MVP, such as Datadog, CloudWatch, Sumo Logic, or a future source. Updates specs first, defines raw-to-canonical mappings, cursor behavior, source health handling, and required tests.
---

# Capacity Add Source Connector

Use this skill when a new source system or new source metric family is added.

## Goal
Add the connector in a repeatable way without breaking normalization, freshness, or recommendation safety.

## Required Spec Areas
- `specs/002-architecture.md`
- `specs/003-domain-model.md`
- `specs/004-ingestion-and-normalization.md`
- `specs/008-security-and-operations.md`
- `specs/009-testing-and-edge-cases.md`

## Workflow
1. Define the new source purpose:
   - primary decision signal
   - supporting signal
   - narrative-only context
2. Define authentication and read-only constraints.
3. List the raw metrics/events to fetch.
4. Map each raw metric to a canonical metric or mark it unsupported.
5. Define cursor and idempotency behavior.
6. Define source-health and stale-data behavior.
7. Add edge cases and tests.

## Required Mapping Table
For each added metric, specify:
- raw source name
- canonical metric name
- unit conversion
- aggregation grain
- whether it is required for recommendation safety

## Guardrails
- Never let a new source silently override an existing canonical metric without a precedence rule.
- If source quality is lower than an existing primary source, keep it as supporting evidence.
- Do not treat logs as utilization truth unless explicitly justified.
