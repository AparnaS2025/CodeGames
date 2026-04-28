---
name: capacity-add-analysis-rule
description: Use when adding or changing a pattern-detection rule, confidence rule, or rightsizing recommendation rule in the Capacity Intelligence MVP. Updates specs first, captures contradictory signals and safety guardrails, and requires deterministic tests before agent prompt changes.
---

# Capacity Add Analysis Rule

Use this skill when changing how the system detects patterns or produces final recommendations.

## Goal
Keep recommendation logic safe, explainable, and deterministic even when agents are involved.

## Required Spec Areas
- `specs/005-analysis-and-recommendation.md`
- `specs/006-agentic-workflow.md`
- `specs/009-testing-and-edge-cases.md`

## Workflow
1. State the business reason for the new rule.
2. Define exact inputs used by the rule.
3. Define exact outputs changed by the rule.
4. Document contradictory-signal behavior.
5. Add or revise confidence and risk effects.
6. Add guardrails and fallback behavior.
7. Add tests before implementation.

## Required Questions
- Is this rule deterministic or agent-assisted?
- Can this rule create a false-safe scale-down?
- Does it behave differently for app services vs databases?
- What happens if one required metric is stale or missing?
- What evidence should appear in the final recommendation?

## Guardrails
- Recommendation safety rules must remain in deterministic code.
- Agent prompts may explain a rule, but must not replace the rule.
- If the rule increases automation aggressiveness, add new negative tests.
