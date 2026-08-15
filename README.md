# ProdKit Control

[![CI](https://github.com/prodkit-dev/prodkit-control/actions/workflows/ci.yml/badge.svg)](https://github.com/prodkit-dev/prodkit-control/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.12%2B-blue.svg)](pyproject.toml)
[![OpenTelemetry](https://img.shields.io/badge/observability-OpenTelemetry-purple.svg)](docs/architecture/observability.md)

A provider-neutral intent-to-production control and assurance plane for software changes.

> **ProdKit connects approved intent to verified production state through a continuous, independently verifiable lineage—without requiring ProdKit to own specification authoring, generation, CI, deployment, or observability.**

## Why this repository exists

An agent trace is not an audit trail, a source repository is not the complete product definition, and a tool-call log is not proof that the intended production change occurred. Code may be disposable as an implementation, but it cannot be anonymous as evidence.

```text
intent/spec/constraints/decisions
        -> generator identity + inputs
        -> source-tree digest
        -> tests/proofs + verification results
        -> build-artifact digest
        -> policy + approval + controlled actions
        -> deployment identity
        -> production-state digest
        -> reconciliation findings
```

ProdKit preserves the typed relationships between these identities. It treats models as untrusted proposers: models never receive implicit authority merely because they emitted a tool call, and every production effect must remain traceable to its approved specification revision.

## Project status

This repository is an **engineering foundation**, not a claim that production-grade guarantees are automatically enabled. The core contracts, typed lineage graph, production-lineage policy, deterministic hashing, in-memory ledger, approval binding, broker lifecycle, evidence bundles, HTTP API, CLI, PostgreSQL adapter, and representative adapters are implemented. Production deployment still requires hardened identity, durable lineage and event storage, key management, policy, executor isolation, external audit sources, and operational controls appropriate to your environment.

See [Guarantees and non-guarantees](docs/architecture/guarantees.md) before enabling production actions.

## Core guarantees

When every side effect is routed through ProdKit and the required deployment controls are enabled, the system is designed to answer:

- Who or what initiated the run?
- Which specification revision, constraints, and decision set authorized generation?
- Which generator configuration and inputs produced the exact source tree?
- Which requirements, tests, and proofs verified that tree?
- Which build produced the deployed artifact digest?
- Which provider, model, agent definition, prompt, policy, and schema versions were used?
- What exact action did the model propose?
- What policy decision and human approval authorized it?
- What executor actually ran it, with which identity and target?
- What did the target system return?
- What state existed before and after execution?
- Did the observed effect match the approved effect?
- Do Git, CI, cloud, Kubernetes, database, and deployment audit sources agree?
- Can the ordered history and artifacts be independently verified without the model vendor?
- Is the observed production state connected to the approved intent by a complete, typed lineage?

## Architecture

```text
GeneratorConfiguration --generated_from--> SpecificationRevision / DecisionSet
GeneratorConfiguration ----produced-----> SourceTree
SourceTree ----------------verified_by---> Verification
SourceTree ------------------built_as----> BuildArtifact
BuildArtifact ------------authorized_by--> Authorization
Authorization --------authorized_action--> AgentAction
AgentAction ----------------deployed_as---> Deployment
Deployment -----------------observed_as---> ProductionObservation
ProductionObservation ------compared_by--> Reconciliation
```

`LineageGraph` is the typed product lineage. The append-only `ControlEvent` ledger records how assertions and actions entered the system, including causality and evidence. Content-addressed artifacts preserve exact inputs and outputs; signed evidence bundles make the combined record independently portable. Query tables, Git history, traces, dashboards, and provider logs are projections or witnesses—not the canonical explanation of the system.

## Repository layout

```text
packages/
  python/
    prodkit-control-core/
      contracts/
      events/
      actions/
      approvals/
      integrity/
      verification/
      reconciliation/

    prodkit-control-runtime/
      coordinator/
      action-broker/
      projectors/
      evidence-bundles/

    prodkit-control-postgres/
    prodkit-control-fastapi/
    prodkit-control-cli/

    integrations/
      prodkit-agentgateway/
      prodkit-permit/
      prodkit-opa/
      prodkit-temporal/
      prodkit-otel/
      prodkit-e2b/
      prodkit-teleport/
      prodkit-sigstore/

    executors/
      prodkit-executor-shell/
      prodkit-executor-filesystem/
      prodkit-executor-git/
      prodkit-executor-github/
      prodkit-executor-http/
      prodkit-executor-database/
      prodkit-executor-kubernetes/
      prodkit-executor-deployment/

    providers/
      prodkit-provider-openai/
      prodkit-provider-anthropic/
      prodkit-provider-google/
      prodkit-provider-pydantic-ai/
      prodkit-provider-generic/

    reconcilers/
      prodkit-reconcile-git/
      prodkit-reconcile-github/
      prodkit-reconcile-ci/
      prodkit-reconcile-database/
      prodkit-reconcile-kubernetes/
      prodkit-reconcile-deployment/

  typescript/
    control/
    control-client/
    control-react/
    control-next/

schemas/
docs/
examples/
deploy/
```

Some adapter packages begin as explicit extension points. They contain stable package boundaries and capability contracts so implementations can be added without coupling the core to a vendor.

## Quick start

### Requirements

- Python 3.12 or newer
- `uv`
- Docker, only for the PostgreSQL example

### Install and verify

```bash
uv sync --all-packages --group dev
uv run pytest
uv run ruff check .
uv run mypy
```

### Run the local API

```bash
uv run prodkit-control-api
```

Open `http://127.0.0.1:8000/docs`.

### Run the deterministic demo

```bash
uv run prodkit-control demo
```

The demo creates a run, proposes a low-risk dry-run action, evaluates policy, executes through a controlled executor, verifies the result, exports an evidence bundle, and verifies its hash chain.

### PostgreSQL development stack

```bash
cp .env.example .env
docker compose up --build
```

## The action lifecycle

Every externally visible action follows this state machine:

```text
proposed
  -> policy_denied
  -> approval_required -> approval_denied
  -> approval_required -> approved
  -> authorized
  -> execution_started
  -> execution_failed
  -> execution_succeeded
  -> effect_verified
  -> effect_mismatched
  -> reconciled
```

The broker persists the proposal before execution. Approval is bound to the action digest, target digest, policy revision, tenant, environment, and expiration. Changing any of those invalidates the approval.

## Provider neutrality

Provider adapters normalize model interactions into canonical records. They do **not** execute tools. OpenAI, Anthropic, Google, local models, agent frameworks, MCP clients, and future providers can be integrated without changing:

- action contracts;
- policy and approval semantics;
- executor interfaces;
- the event ledger;
- the lineage graph and production completeness policy;
- verification and reconciliation;
- evidence bundle formats.

## Integration philosophy

ProdKit intentionally integrates with existing infrastructure instead of rebuilding everything:

- gateways: agentgateway or another MCP/agent gateway;
- policy: OPA, Permit, Cerbos, or a custom engine;
- durable orchestration: Temporal or another workflow engine;
- sandboxing: E2B, Daytona, Kubernetes Jobs, or isolated workers;
- privileged access: Teleport, StrongDM, cloud-native identity, or custom brokers;
- tracing: OpenTelemetry with Langfuse, Phoenix, or another backend;
- attestations: Sigstore and in-toto;
- external evidence: GitHub, CI, cloud, Kubernetes, databases, and deployment platforms.

These integrations are optional. The core contracts, lineage policy, ledger, and evidence bundle verifier can run standalone.

## Security model

The trust boundary assumes:

1. Models are untrusted proposers.
2. Provider traces are supplemental and may be unavailable.
3. Executors use short-lived, least-privilege workload identity.
4. Secrets are referenced, not embedded in events.
5. The authoritative ledger is unsampled and append-only.
6. OpenTelemetry is an operational projection, not the audit database.
7. External state is reconciled after execution.
8. Any production action that bypasses the broker is an audit failure.
9. Production acceptance fails closed when the intent-to-production lineage is incomplete or contains an unsuccessful required stage.

Read the [threat model](docs/security/threat-model.md), [secure deployment guide](docs/security/secure-deployment.md), and [responsible disclosure policy](SECURITY.md).

## Data retention

The core supports four content modes:

- `none`: retain metadata only;
- `hash_only`: retain fingerprints, not content;
- `redacted`: retain deterministic redacted content plus original fingerprint;
- `full`: retain encrypted full content through the configured artifact store.

`hash_only` proves integrity of known content but cannot reconstruct content that has been discarded. Production audit profiles normally require encrypted argument and result artifacts with field-level redaction.

## Standards and interoperability

The repository is designed around:

- W3C Trace Context identifiers;
- OpenTelemetry-compatible correlation;
- JSON Schema contracts;
- SHA-256 content addressing and event chaining;
- in-toto Statement-compatible evidence;
- SLSA-compatible provenance references;
- policy-engine-neutral decisions;
- SPIFFE-compatible workload identity references.

## Documentation

- [Architecture overview](docs/architecture/overview.md)
- [Event model](docs/architecture/event-model.md)
- [Product lineage model](docs/architecture/lineage.md)
- [Action and approval model](docs/architecture/action-approval.md)
- [Guarantees and non-guarantees](docs/architecture/guarantees.md)
- [Observability](docs/architecture/observability.md)
- [Threat model](docs/security/threat-model.md)
- [Secure deployment](docs/security/secure-deployment.md)
- [Operations runbook](docs/operations/runbook.md)
- [Roadmap](ROADMAP.md)

## Contributing

Contributions are welcome. Start with [CONTRIBUTING.md](CONTRIBUTING.md), follow the [Code of Conduct](CODE_OF_CONDUCT.md), and review [GOVERNANCE.md](GOVERNANCE.md).

## License

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
