# ADR 0003: Typed product lineage is part of the canonical record

## Status

Accepted.

## Decision

ProdKit represents intent-to-production provenance as an immutable, content-addressed,
tenant-scoped `LineageGraph`. Stage identities and relationships are typed rather than embedded
only in event payloads. The event ledger records assertion history and causality; the lineage graph
records the durable product semantics. Evidence bundles can carry both.

Production acceptance uses a fail-closed policy requiring an independently verifiable path from an
approved specification revision through generation, verification, build, authorization, controlled
action, deployment, observation, and reconciliation.

## Consequences

- Regenerated source and tests remain explainable through their historical digests and evidence.
- Git, CI, deployment systems, and observability platforms remain external witnesses.
- Missing or unsuccessful lineage stages are explicit policy failures rather than absent metadata.
- Adapters must translate external identities and attestations into canonical nodes and relations.
