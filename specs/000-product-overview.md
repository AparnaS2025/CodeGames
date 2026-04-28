# Product Overview

## Name
CoW Capacity Intelligence

## Summary
CoW Capacity Intelligence is an advisory-only, agent-assisted service that analyzes infrastructure and application telemetry across Datadog, AWS CloudWatch, Sumo Logic, and internal CoW metrics to identify underutilized and overutilized services and databases. The MVP produces explainable scale-up, scale-down, hold, or watchlist recommendations with confidence, evidence, estimated savings, and rollback guardrails.

## Primary Users
- platform engineering
- cloud operations
- SRE / service owners
- engineering managers reviewing cost-saving opportunities

## Core User Problem
Operators can see dashboards and alerts, but still spend significant time answering:
- Is low utilization sustained enough to safely scale down?
- Is a spike structural business demand or a temporary anomaly?
- Is a database lightly used on CPU but constrained elsewhere?
- What is the likely cost impact of doing nothing vs rightsizing?

## Product Goal
Turn fragmented observability signals into actionable, reviewable capacity recommendations while keeping the MVP read-only and low-risk.

## Non-Goals
- automatic scaling
- direct write access to AWS, Datadog, or Sumo Logic
- long-horizon forecasting beyond light heuristics
- tenant-by-tenant cost allocation
- deep Kubernetes scheduling optimization

## Guiding Principles
- deterministic analytics decide; agents interpret and explain
- advisory only for MVP
- every recommendation must be auditable
- conservative behavior under uncertainty
- stale or incomplete data should reduce confidence, not be hidden
