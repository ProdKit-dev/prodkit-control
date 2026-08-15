# ADR 0001: Canonical append-only execution event ledger

- Status: Accepted
- Date: 2026-08-06

## Context

Provider traces and observability platforms are valuable but may be sampled, unavailable, mutable,
or coupled to one vendor. A production action record must survive provider replacement.

## Decision

Use an unsampled, provider-neutral, append-only `ControlEvent` sequence as the canonical record.
All query models and telemetry are projections. Corrections are new events. Event sequences are
hash-chained and production profiles add signed checkpoints and external anchors.

## Consequences

The system must operate storage, retention, migration, compatibility, and integrity verification.
This additional complexity is necessary for independent reconstruction.
