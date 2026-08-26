# ProdKit Control documentation

This directory is the public documentation entry point for ProdKit Control. Start with the end-user path, then move into architecture, deployment, security, and operations as your use case requires.

## Start here

- [Getting started](getting-started.md) — install from an exact source checkout or verified release artifacts, run the end-to-end demo, verify evidence, and understand the development/reference container profile.
- [Architecture](architecture/README.md) — canonical architecture, language-neutral authority, runtime/action flow, lineage, trust boundaries, deployment profiles, failure/recovery, and guarantees.
- [Release history](releases/README.md) — immutable release boundaries, versioning, and release-evidence expectations.
- [Roadmap](../ROADMAP.md) — maturity-gated milestones. The roadmap is not a statement that future guarantees already exist.

## API discovery

The FastAPI surface exposes interactive OpenAPI documentation when the API is running:

- `/docs` — Swagger UI;
- `/redoc` — ReDoc;
- `/openapi.json` — machine-readable OpenAPI document.

The default API bootstrap is a development/reference profile and intentionally fails readiness when production authentication is not configured. See [Getting started](getting-started.md) before treating any local API process or container as a durable or production deployment.

## Security and deployment

- [Threat model](security/threat-model.md)
- [Secure deployment](security/secure-deployment.md)
- [Production hardening](security/production-hardening.md)
- [Deployment architecture](architecture/deployment.md)
- [Multi-tenancy and isolation](architecture/multi-tenancy.md)
- [Guarantees and non-guarantees](architecture/guarantees.md)

Production use requires deployment-supplied authenticated identity, durable storage, isolated executors, short-lived credentials, explicit policy/approval, independent reconciliation, and the controls required by the selected assurance profile. A local demo, in-memory API, or package import is not equivalent to that profile.

## Operations

- [Operations runbook](operations/runbook.md)
- [Capacity and overload envelope](operations/capacity.md)
- [Security incident response](operations/security-incident-response.md)

## Extending and embedding

Provider, executor, policy, workflow, storage, framework, and reconciliation integrations are replaceable adapters around the canonical core. Start with [Extension architecture](architecture/extensions.md) and the package-local README for the package you consume.

The language-neutral specifications, schemas, protocols, canonicalization profiles, and conformance vectors are authoritative for portable semantics. Python and TypeScript are native implementations; neither runtime is the normative source of meaning.

## Support and contribution

- [Support](../SUPPORT.md)
- [Security reporting](../SECURITY.md)
- [Contributing](../CONTRIBUTING.md)
- [Governance](../GOVERNANCE.md)
- [Code of Conduct](../CODE_OF_CONDUCT.md)

When documentation and executable behavior disagree, treat that as a release-blocking defect for current-facing claims rather than silently relying on the documentation.
