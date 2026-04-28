# Agentic Workflow

## Agentic Design Principle
Agents propose and explain.
Deterministic services calculate and validate.

The MVP must not permit an agent to directly issue a final scaling action.

## Agents In Scope

### Pattern Agent
Input:
- computed features
- time-window summary
- anomaly markers

Output:
- pattern tags
- explanation of likely workload shape
- explicit uncertainty notes

### Recommendation Agent
Input:
- deterministic features
- pattern agent output
- current size
- minimum size
- available savings estimate

Output:
- draft recommendation type
- suggested size
- rationale bullets
- initial guardrails

### Report Agent
Input:
- final validated recommendation
- evidence
- risk
- savings

Output:
- operator-facing summary
- weekly portfolio summary

### Review Agent
Input:
- saved recommendation
- saved evidence
- report summary

Output:
- answers to operator questions
- no new facts beyond stored state unless explicitly asked to re-run analysis

## Supervisor Workflow
1. gather analysis snapshot
2. invoke pattern agent
3. invoke recommendation agent
4. run policy validator
5. invoke report agent on final validated output
6. persist recommendation and report

## Structured Output Requirement
All agents must return structured output validated by schema.

Free-form text alone is not acceptable.

## Fallback Behavior
- if an agent fails, analysis still completes using deterministic fallback text
- if schema validation fails, retry once with error feedback
- if retry fails, mark agent output unavailable and continue conservatively

## Edge Cases
- agent returns unsupported recommendation type
- agent hallucinated a metric not present in snapshot
- agent ignores insufficient-data flag
- contradictory agent outputs
- token/time budget exceeded on report generation

## Acceptance Criteria
- system remains usable with agents disabled
- policy validation output is the source of truth
