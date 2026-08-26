# Getting started with ProdKit Control

ProdKit Control is a provider-neutral control and assurance plane for software changes. It treats models and agents as untrusted proposers, binds authorization to exact actions and context, executes through explicit capability boundaries, and records evidence that can be reconciled with independently observed production state.

This guide covers the supported first-run experience for the v0.9.x line. It intentionally starts with the local/reference profile. Production use requires the additional controls documented in the secure-deployment and operations guides.

## Choose a distribution path

The v0.9.1 public release supports two reproducible ways to evaluate and run the project:

1. **Exact source release / source checkout** — recommended for evaluation, development, and building a deployment. The root lockfiles reproduce the tested Python and TypeScript workspace.
2. **GitHub Release artifacts** — every first-party Python distribution and TypeScript package is built, inspected, clean-installed, smoke-tested, checksummed, and published with the exact source archive and SPDX SBOM.

The project does not require PyPI, npm, a container registry, another ProdKit product, or a model vendor as a semantic dependency. Public package-registry publication is not claimed by v0.9.1; do not assume an identically named third-party registry package is an official ProdKit artifact.

## Requirements

For the Python control plane and CLI:

- Python 3.12–3.14;
- [`uv`](https://docs.astral.sh/uv/).

For the TypeScript packages and cross-runtime development checks:

- Node.js 22 or newer;
- Corepack / pnpm 10.

Docker is optional and is only required for the containerized reference profile and container builds.

## Run the verified local demo

From an exact v0.9.1 source tree:

```bash
uv sync --all-packages --group dev --locked
uv run prodkit-control demo --output .artifacts/demo
```

The demo does more than print a placeholder. It:

1. creates a tenant-scoped run;
2. proposes a low-risk action through the action broker;
3. evaluates policy;
4. executes through the explicit dry-run executor;
5. records the action result and observation;
6. builds a complete intent-to-observation lineage;
7. enforces the production-lineage contract against that graph;
8. exports an evidence bundle; and
9. independently verifies the bundle's event ordering, hash chain, tenant scope, and lineage manifest before returning success.

A successful run prints the evidence-bundle path, final event hash, and lineage counts. You can verify a bundle again with:

```bash
uv run prodkit-control verify-bundle PATH_TO_BUNDLE
```

## Run the programmatic example

```bash
uv run python examples/basic_dry_run.py
```

The example shows the public Python contracts directly: create an authenticated actor/run, construct an exact `ActionSpec`, route it through policy + broker + executor boundaries, and inspect the resulting verification and observation.

See [`examples/README.md`](../examples/README.md) for the example boundary and production cautions.

## Run the API locally

The FastAPI surface fails closed unless a principal resolver is configured. For local development only, you may explicitly opt into the insecure header-based development resolver:

```bash
PRODKIT_ALLOW_INSECURE_HEADER_AUTH=true uv run prodkit-control-api
```

The local CLI binds to `127.0.0.1` by default. Then use:

- `http://127.0.0.1:8000/docs` for Swagger UI;
- `http://127.0.0.1:8000/redoc` for ReDoc;
- `http://127.0.0.1:8000/openapi.json` for the machine-readable OpenAPI document.

Development requests to protected routes must provide the documented ProdKit tenant/actor headers. **Never enable `PRODKIT_ALLOW_INSECURE_HEADER_AUTH` in a production deployment.** Production must inject authenticated identity from the surrounding platform and use least-privilege execution credentials.

## Start the containerized reference profile

```bash
cp .env.example .env
docker compose up --build
```

The Compose profile intentionally runs the same in-memory development/reference API as the local bootstrap. It does **not** silently wire PostgreSQL, durable artifacts, production authentication, or an enterprise execution plane. This is deliberate: a public quickstart must not imply durability that is not actually active.

The image explicitly binds the API to `0.0.0.0` inside the container while Compose exposes it only on host loopback (`127.0.0.1:8000`). Without an authenticated principal resolver the default image remains healthy but not ready, failing `/readyz` closed with HTTP 503.

For durable and production deployment, read [`docs/architecture/deployment.md`](architecture/deployment.md), [`docs/security/secure-deployment.md`](security/secure-deployment.md), and [`docs/operations/runbook.md`](operations/runbook.md) before connecting ProdKit Control to production systems. The PostgreSQL package provides durable storage primitives; selecting and wiring a durable service graph is an explicit deployment responsibility rather than an implicit side effect of `docker compose up`.

## Run the full developer verification

Install both workspaces with the committed locks:

```bash
make install
make check
```

`make check` verifies release/version consistency, package completeness, language-neutral contract authority, cross-runtime conformance, public-readiness documentation/metadata, Python lint/types/tests/schema drift, TypeScript types/build/conformance, and the local first-run smoke path.

The canonical CI additionally runs the supported Python and Node matrices, PostgreSQL integration checks, container build/startup checks, Security, and CodeQL. Pull requests from public forks use GitHub-hosted runners; same-repository release qualification may use the configured trusted runner.

The release build adds a separate consumer gate: it installs the exact built wheels into a clean virtual environment, runs the installed CLI and API import path outside the monorepo, installs the exact npm tarballs into a clean project, imports all public TypeScript packages, and verifies required public legal/documentation files are actually present in the distributions.

## What is language-neutral

Portable ProdKit Control semantics do not derive their meaning from Python or TypeScript implementation code. Normative specifications and conformance vectors live under [`contracts/`](../contracts/). Python and TypeScript are independent native implementations of those profiles and CI requires both to agree on the shared vectors.

External policy engines, model providers, workflow engines, telemetry systems, privileged-access systems, and sandbox providers remain adapters. They can be replaced without becoming the semantic source of truth for ProdKit Control.

## Deployment profiles

Use the profile names precisely:

| Profile | Intended use |
| --- | --- |
| Development/reference | Local evaluation, contract testing, explicit development-only auth and in-memory components. |
| Standalone durable | A durable deployment that does not require another ProdKit product. |
| Production control | Production effects with authenticated principals, isolated executors, short-lived credentials, durable idempotency, and independent reconciliation. |
| Enterprise assurance | The higher-assurance target with HA/DR, tenant-isolation verification, governance/retention, SLOs, and independent review gates. |

v0.9.1 is the public-readiness patch for the v0.9 cumulative-completeness milestone. It is **not** the v0.10.0 Production Candidate and does not claim the v1.0.0 enterprise production-assurance gate.

## Security and support

Before enabling privileged actions, read:

- [`SECURITY.md`](../SECURITY.md) for vulnerability reporting and supported-version policy;
- [`docs/security/threat-model.md`](security/threat-model.md);
- [`docs/security/secure-deployment.md`](security/secure-deployment.md);
- [`docs/architecture/guarantees.md`](architecture/guarantees.md);
- [`SUPPORT.md`](../SUPPORT.md) for community support boundaries.

Never include production credentials, private keys, access tokens, or sensitive customer data in public issues, examples, or test fixtures.

## Verify a published release

A published release is bound to one immutable source commit. The release lifecycle verifies the exact tag, metadata, asset set, checksums, source archive, SBOM, and independent release-verification result before cleanup.

See [`VERIFICATION.md`](../VERIFICATION.md) and [`docs/releases/README.md`](releases/README.md) for the verification and versioning contracts.
