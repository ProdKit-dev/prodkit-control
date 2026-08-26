# Security Policy

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. Send a private report to
`security@prodkit.dev` with:

- affected version or exact commit SHA;
- deployment assumptions/profile;
- reproduction steps or proof of concept;
- security impact and possible mitigations;
- whether the issue or exploit is already public.

Do not send production credentials, private keys, access tokens, or personal/customer data. If a
reproduction requires sensitive material, first establish a safe exchange method with the
maintainers.

Maintainers should acknowledge actionable reports promptly, establish severity/ownership, preserve
a private incident record, and coordinate disclosure only after affected users have a reasonable
remediation path.

## Supported versions

ProdKit Control is pre-1.0. **v0.9.1 is the current supported public release**. Security fixes are
applied to the latest supported release line. Earlier immutable snapshots may not receive backports
unless a maintainer explicitly designates a maintenance line.

The v0.9.1 public-readiness release inherits the v0.9 cumulative-completeness and
language-neutral-authority boundary. It is not the v0.10.0 Production Candidate and does not claim
the v1.0.0 enterprise production-assurance gate.

## Vulnerability response targets

For the supported production-control profile:

- **Critical:** immediate containment on confirmation; patched release target within 72 hours when
  a code patch is the remediation.
- **High:** patched release target within 7 days.
- **Medium:** target within 30 days.
- **Low:** target within 90 days or the next planned maintenance release.

These are project remediation targets, not commercial uptime/support SLAs. If remediation cannot
safely meet a target, the private incident record must name a compensating control, owner, and
expiry. A known uncontained Critical finding blocks release promotion.

## Dependency and provenance policy

Dependency graphs are lockfile-controlled and audited in Security CI. External GitHub Actions and
reusable workflows use exact commit SHAs. Release artifacts must pass the repository's exact-source
proof, artifact inspection, checksum/SBOM sealing, publication binding, and independent release
verification before the release is considered closed.

Production images and other deployment artifacts must be selected by immutable digest/identity
where the selected production profile requires it; a human-readable tag alone is not sufficient
proof of exact content.

## Security boundary

ProdKit Control treats models and agents as untrusted proposers. A model does not gain authority
because it generated a tool call. Production authority must come from authenticated identity,
policy/approval, exact-action binding, explicit executor capabilities, short-lived credentials, and
independent evidence appropriate to the deployment profile.

This software cannot provide an organization-wide audit record for actions that bypass its broker
or use credentials outside controlled executors. Treat bypass prevention/detection, independent
reconciliation, provider-side audit logs, network isolation, and external security-event retention
as required production controls.

ProdKit Control also does not claim that application-level policy can make arbitrary hostile code
incapable of escaping a compromised container, VM, microVM, kernel, hypervisor, or runtime. When
untrusted code is executed, the surrounding isolation substrate must independently enforce the
required process, filesystem, network, credential, resource, and privilege boundaries.

## Production security guidance

Before enabling privileged effects, read:

- [`docs/security/threat-model.md`](docs/security/threat-model.md);
- [`docs/security/secure-deployment.md`](docs/security/secure-deployment.md);
- [`docs/security/production-hardening.md`](docs/security/production-hardening.md);
- [`docs/operations/security-incident-response.md`](docs/operations/security-incident-response.md);
- [`docs/architecture/guarantees.md`](docs/architecture/guarantees.md).

The current security/operational hardening foundation was established in v0.8.0 and is inherited by
v0.9.1; the supported release boundary is v0.9.1, not v0.8.0.
