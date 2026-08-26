# Support

ProdKit Control is an open-source pre-1.0 project. Community support is provided on a best-effort basis; the public project does not promise response-time, uptime, or remediation service-level agreements except for the security-response targets explicitly documented in [`SECURITY.md`](SECURITY.md).

## Before opening a request

Please check:

1. [`README.md`](README.md) and [`docs/getting-started.md`](docs/getting-started.md);
2. the relevant architecture, security, and operations documents under [`docs/`](docs/);
3. existing GitHub Issues for a matching defect or proposal;
4. the release notes for the exact version you are running.

Run the supported local verification when possible:

```bash
make install
make check
```

For a minimal first-run reproduction:

```bash
uv sync --all-packages --group dev --locked
uv run prodkit-control demo --output .artifacts/demo
```

## Defects

Use GitHub Issues for reproducible defects. Include:

- exact ProdKit Control version or commit SHA;
- Python/Node versions as relevant;
- operating system/runtime environment;
- deployment profile (development/reference, standalone durable, production control, etc.);
- minimal reproduction steps;
- expected and observed behavior;
- relevant sanitized logs or evidence identifiers.

Do **not** include credentials, access tokens, private keys, customer data, or vulnerability details in a public issue.

## Feature and architecture proposals

Use GitHub Issues for concrete feature proposals. Changes to trust boundaries, canonical semantics, compatibility policy, authorization, evidence, tenancy, or deployment profiles may require an Architecture Decision Record as described in [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Usage questions

Start with the getting-started guide and examples. When GitHub Discussions is enabled for the repository, use Discussions for general usage/design questions. Otherwise, open a clearly scoped GitHub Issue and identify it as a usage question rather than a defect.

## Security

Suspected vulnerabilities, credential exposure, authorization bypasses, cross-tenant issues, integrity/provenance weaknesses, sandbox/executor escape concerns, and other security-sensitive reports must follow [`SECURITY.md`](SECURITY.md) and must **not** be opened as public issues.

## Supported release line

The latest v0.9.x patch is the supported public line until a later release explicitly supersedes it. Historical pre-1.0 releases are retained as immutable evidence but are not automatically maintained or backported.

For production-impacting deployments, pin an exact release/tag and follow the verification procedure in [`VERIFICATION.md`](VERIFICATION.md) instead of consuming a moving branch.
