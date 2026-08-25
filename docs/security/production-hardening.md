# v0.8.0 Production Security Profile

This document defines the supported security baseline for ProdKit Control v0.8.0. It is a
control-plane profile, not a claim that the surrounding cloud, network, identity provider, or
executor environment is automatically secure.

## Control matrix

| Threat | Required control | Repository evidence | Production responsibility |
| --- | --- | --- | --- |
| Secret disclosure | Pass only `SecretReference`; pin provider version; bind tenant, purpose, and audience | `SecretReference`, `SecretReferenceGuard`, adversarial tests | Use Vault/cloud secret manager; materialize only inside the intended executor; rotate provider credentials |
| Workload impersonation/replay | HTTPS issuer, exact audience, subject/client allowlists, short assertion lifetime, `nbf`, one-time nonce claim | `WorkloadIdentityPolicy`, `WorkloadIdentityVerifier`, atomic replay race test | Use workload identity/OIDC or SPIFFE-equivalent; use a shared atomic replay store for multiple replicas |
| API abuse/DoS | Per-process bounded limiter plus trusted-ingress shared limit | `SecurityRateLimitMiddleware`, limiter tests | Enforce tenant/principal-aware limits at the ingress; do not trust arbitrary forwarded headers |
| Security evidence leakage | Structured security events and credential-key redaction | `SecurityAuditEvent`, `NDJSONSecurityAuditExporter`, redaction tests | Export to access-controlled append/stream/SIEM storage with retention policy |
| Artifact substitution | Exact subject SHA-256, SLSA predicate, trusted builder/build type, verified signature | `ArtifactProvenanceVerifier`, existing in-toto/SLSA/Sigstore contracts | Verify signatures against controlled trust roots before promotion; deploy immutable image digests |
| Dependency compromise | Locked Python/Node dependency graphs, `pip-audit`, CodeQL, secret scanning, exact-SHA workflow/action references | `uv.lock`, `pnpm-lock.yaml`, Security/CodeQL workflows, `check_security_policy.py` | Patch within the response targets in `SECURITY.md`; rotate compromised credentials/trust roots |
| Control-plane bypass | Existing broker/approval/policy/evidence boundaries; reconciliation | threat model, broker/reconciliation tests | Deny direct production mutation paths that bypass controlled executors and reconcile independently |
| Replica disruption | Non-root/read-only/seccomp/drop-capabilities, readiness/liveness, PDB, graceful drain | Kubernetes base manifests and HA lifecycle | Add NetworkPolicy/firewall rules, private database/storage endpoints, anti-affinity/topology policy appropriate to the cluster |

## Secret-management contract

The control plane stores and transmits **references**, never long-lived secret values. A production
secret reference MUST identify an approved provider and immutable provider version. The reference
is bound to a tenant and purpose and may be narrowed to one or more audiences. Secret value
resolution belongs inside the smallest trusted executor boundary and must produce short-lived
credentials wherever the provider supports them.

The reference guard deliberately does not implement a vendor SDK. HashiCorp Vault, AWS Secrets
Manager, GCP Secret Manager, Azure Key Vault, and other systems are provider integrations behind
the same contract, not architectural dependencies.

## Workload identity

`WorkloadIdentityVerifier` is for one-time credential-exchange assertions, not ordinary reusable
HTTP bearer tokens. It checks issuer, audience, subject prefix, optional authorized client,
assertion lifetime, activation time, expiry, and nonce replay. The in-memory replay store is a
standalone/test implementation. A multi-replica production deployment MUST inject a shared atomic
claim store such as PostgreSQL/Redis with equivalent claim-once semantics.

API bearer authentication separately enforces signature, issuer, audience, bounded lifetime,
`nbf`, and optional `azp` binding through `OIDCPrincipalResolver`.

## Abuse controls

The FastAPI package exposes `SecurityRateLimitMiddleware`. The reference limiter is bounded and
thread-safe and returns `429` with `Retry-After`. Health/readiness endpoints are exempt so an
attack cannot make orchestration probes self-deny.

For multi-replica deployments, the supported profile requires a shared limit at the trusted
reverse proxy/API gateway keyed by authenticated tenant/principal where possible. Socket-peer
address is the safe local fallback; forwarded headers are not trusted unless the deployment owns
and sanitizes that boundary.

## Audit export

Security events are canonical typed records. `NDJSONSecurityAuditExporter` is a provider-neutral
stream/file boundary suitable for an agent or shipper. Credential-like attribute keys are
redacted before export. Production sinks must provide access control, retention, integrity, and
alert routing appropriate to the deployment. Never place raw tokens, cookies, private keys, or
secret values in event attributes.

## Supply-chain policy

Every external GitHub Action/reusable workflow reference must use an exact 40-character commit
SHA. Python and Node dependency lockfiles are mandatory. Security runs dependency audit plus the
repository policy checker. Release artifacts remain subject to the existing signed
attestation/provenance and trust-root verification path.

The Kubernetes base uses a versioned image tag so examples never silently follow `latest`.
Production overlays MUST replace that tag with the exact verified image digest promoted by the
release pipeline.

## Kubernetes / infrastructure hardening

The checked baseline requires non-root execution, `RuntimeDefault` seccomp,
`allowPrivilegeEscalation: false`, read-only root filesystem, all Linux capabilities dropped,
service-account token automount disabled, service-link injection disabled, resource bounds,
health/readiness probes, graceful termination, and a PodDisruptionBudget.

Production operators must additionally provide environment-specific controls that cannot safely
be guessed in a general-purpose base manifest: default-deny network policy/firewalling with
explicit DNS/database/storage/identity egress; TLS/mTLS at trust boundaries; private database and
object-storage connectivity; encrypted backups; workload/pod isolation; immutable image digest;
and executor-specific sandboxing appropriate to the effect being performed.

## Evidence and release gate

`Security` runs dependency audit, the permanent security-policy checker, and the deterministic
incident exercise. CI carries adversarial unit/race tests. CodeQL remains an independent required
gate. A v0.8.0 release is eligible only when those exact-head gates are successful and there is no
known open critical security finding for the supported profile.
