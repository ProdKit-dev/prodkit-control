# ADR 0002: Models propose; controlled executors act

- Status: Accepted
- Date: 2026-08-06

## Decision

Provider adapters may return tool proposals but never execute side effects. The action broker owns
policy, approval, idempotency, executor selection, observation, verification, and event recording.

## Consequences

Agent frameworks must route tools through the broker. Direct credentials or hidden tool execution
break the completeness guarantee and are treated as deployment defects.
