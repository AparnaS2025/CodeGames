---
name: capacity-spec-evolution
description: Use when updating or extending the Capacity Intelligence MVP specs before implementation. Applies spec-driven development rules, keeps cross-spec consistency, updates acceptance criteria and edge cases, and identifies whether a change belongs in scope, out of scope, or deferred.
---

# Capacity Spec Evolution

Use this skill whenever a request changes product behavior, scope, domain entities, API shape, agent responsibilities, or edge-case handling.

## Goal
Update specs first and keep the whole spec pack internally consistent before any implementation work begins.

## Files To Review First
- `specs/000-product-overview.md`
- `specs/001-mvp-scope.md`
- `specs/002-architecture.md`
- `specs/003-domain-model.md`
- `specs/004-ingestion-and-normalization.md`
- `specs/005-analysis-and-recommendation.md`
- `specs/006-agentic-workflow.md`
- `specs/007-api-and-ui.md`
- `specs/008-security-and-operations.md`
- `specs/009-testing-and-edge-cases.md`

## Workflow
1. Identify the exact behavior or constraint being changed.
2. Find all affected specs, not just the most obvious one.
3. Update scope, architecture, domain model, and acceptance criteria together.
4. Add or revise explicit edge cases.
5. Mark decisions as one of:
   - in scope now
   - deferred
   - out of scope
6. Keep wording implementation-facing and testable.

## Required Output Shape
When you finish, make sure the spec change clearly answers:
- what changed
- why it changed
- which files were updated
- what acceptance criteria changed
- what new edge cases were added

## Guardrails
- Do not start code implementation while requirements are still ambiguous.
- Do not hide uncertainty; capture it as an assumption or deferred decision.
- Prefer explicit acceptance criteria over vague prose.
