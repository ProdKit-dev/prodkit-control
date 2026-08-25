# Security Incident Response

ProdKit Control treats an incident as any event that may compromise authorization, provenance,
confidentiality, integrity, evidence completeness, or availability of the control plane or its
controlled executors.

## Ownership and severity

| Class | Default severity | Primary owner | Initial containment target |
| --- | --- | --- | --- |
| Credential or signing-key compromise | Critical | Security/on-call | 30 minutes |
| Workload identity replay or impersonation | High | Security + platform | 60 minutes |
| Control-plane bypass / unauthorized production effect | Critical | Security + production owner | 30 minutes |
| Supply-chain/provenance compromise | Critical | Security + release owner | 30 minutes |
| Evidence/data-integrity loss | High | Platform + data owner | 4 hours |
| Abuse / denial of service | High | Platform/on-call | 60 minutes |
| Replica/service availability failure | Medium unless broad impact | Platform/on-call | 4 hours |

Deployments may tighten these targets. They must not silently downgrade a Critical incident merely
because business impact is not yet fully known.

## Response sequence

1. **Detect and declare.** Preserve the triggering security event, alert, exact commit/release,
   affected tenant/executor, and first-known timestamp. Assign an incident owner.
2. **Contain.** Revoke/rotate affected credentials or trust roots, disable compromised workload
   identities/builders, stop unsafe promotion/execution, tighten rate limits, or isolate affected
   replicas/executors. Prefer fail-closed containment over continued mutation.
3. **Establish scope.** Correlate control events, security-audit exports, provider logs,
   reconciliation observations, release provenance, and external production state. Do not rely on
   the control-plane ledger alone for suspected bypasses.
4. **Eradicate.** Patch the vulnerable component/configuration, rotate secrets/keys, remove
   malicious artifacts, and invalidate affected assertions/caches.
5. **Recover.** Restore from verified artifacts/backups, re-enable narrowly, reconcile production
   state, and monitor for recurrence before removing temporary controls.
6. **Close with evidence.** Record timeline, root cause, impacted scope, control failures,
   remediation, tests, and follow-up owners. Update threat model/runbooks when assumptions changed.

## Vulnerability patch targets

- Critical exploitable issue in the supported profile: containment immediately on confirmation;
  patched release target within 72 hours where a software patch is the remediation.
- High: patched release target within 7 days.
- Medium: target within 30 days.
- Low: target within 90 days or next planned maintenance release.

If safe remediation requires more time, the incident record must name the compensating control,
owner, and expiry. A known uncontained Critical finding blocks release promotion.

## Mandatory evidence

Keep: alert/security event ID, relevant immutable digests, exact deployed/release commit, affected
identity/tenant references, trust-root revision, containment actions, reconciliation result, and
post-fix test/gate evidence. Do not copy raw credentials or personal data into the incident record.

## Deterministic exercise

`scripts/ci_security_exercise.py` is the repository-level incident-response exercise. Security CI
must prove that a replayed workload assertion, an over-limit request burst, and provenance from an
untrusted builder are blocked. This is intentionally deterministic and non-destructive; production
operators should additionally rehearse provider-specific revocation, restore, and communications
procedures.

Run locally after installing the locked workspace:

```bash
uv run --python 3.13 --no-sync python scripts/ci_security_exercise.py
```

Any failed exercise is a release blocker.
