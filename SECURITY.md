# Security Policy

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. Send a private report to
`security@prodkit.dev` with:

- affected version or commit;
- deployment assumptions;
- reproduction steps;
- impact and possible mitigations;
- whether the issue is already public.

Do not send production credentials, private keys, access tokens, or personal data. Maintainers
should acknowledge actionable reports promptly, establish severity/ownership, and preserve a
private incident record until disclosure is safe.

## Supported versions

The project is pre-1.0. Security fixes are applied to the latest release line. Earlier snapshots
may not receive backports unless a maintainer explicitly designates a supported maintenance line.

## Vulnerability response targets

For the supported production profile:

- **Critical:** immediate containment on confirmation; patched release target within 72 hours when
  a code patch is the remediation.
- **High:** patched release target within 7 days.
- **Medium:** target within 30 days.
- **Low:** target within 90 days or the next planned maintenance release.

If remediation cannot safely meet the target, the incident record must name a compensating
control, owner, and expiry. A known uncontained Critical finding blocks release promotion.

## Dependency and provenance policy

Dependency graphs are lockfile-controlled and audited in Security CI. External GitHub Actions and
reusable workflows must use exact commit SHAs. Release artifacts must pass the repository's
existing provenance/signature verification policy before production promotion. Production images
must be deployed by verified immutable digest even when the reference Kubernetes base shows the
human-readable release tag.

## Security boundary

This software cannot provide a complete audit record for actions that bypass its broker or use
credentials outside controlled executors. Treat bypass prevention, independent reconciliation,
provider-side audit logs, network isolation, and external security-event retention as mandatory
production controls.

For the v0.8.0 supported profile and control matrix, see
`docs/security/production-hardening.md`. Incident ownership and response procedure are defined in
`docs/operations/security-incident-response.md`.
