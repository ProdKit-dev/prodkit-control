# Security and Operational SLOs

These are reference objectives for the supported v0.8.0 production profile. Operators should tune
absolute targets to their architecture and traffic while preserving the control semantics and
alert classes.

## Reference objectives

| SLI | Objective | Window | Page condition | Ticket condition |
| --- | ---: | ---: | --- | --- |
| Authenticated control API success | 99.9% | 30 days | burn rate >= 14.4 | burn rate >= 6.0 |
| Authorized action admission decision success | 99.95% | 30 days | burn rate >= 14.4 | burn rate >= 6.0 |
| Security-audit export acceptance | 99.99% | 30 days | any sustained loss plus burn threshold | burn rate >= 6.0 |
| Reconciliation freshness | 99.9% inside configured freshness objective | 30 days | stale production scope crosses safety threshold | repeated stale samples |
| Release provenance verification | 100% of promoted artifacts | per release | any unverified promoted artifact | any verification-system degradation |

An SLO is not permission to consume the entire error budget on security-critical integrity checks.
Signature/provenance verification, tenant authorization, replay protection, and required approval
checks remain fail-closed regardless of availability pressure.

## Burn-rate calculation

`evaluate_slo` computes the observed bad-event fraction divided by the permitted error-budget
fraction (`1 - target_ratio`). `OperationalSLO` stores paging and ticket thresholds. The reference
14.4/6.0 thresholds are suitable starting points for multi-window alerting; operators should feed
real telemetry windows into their monitoring stack rather than using this helper as a metrics
backend.

## Minimum dashboard

A supported production dashboard should expose at least:

- request volume, latency and 2xx/4xx/5xx rates by route class;
- authentication failures, authorization denials, rate-limit denials and workload replay events;
- broker action admission/outcome counts and approval wait/failure rates;
- executor failures, uncertainty/recovery state and reconciliation age;
- event/audit export backlog or sink failures;
- release/provenance verification failures and trust-root revision;
- database/storage error rates, connection saturation and queue depth;
- replica readiness, drain state, restarts, CPU/memory and PDB availability.

Do not put tenant identifiers or secret-bearing labels into unbounded metric dimensions.

## Alert routing

- Page immediately for Critical incident classes, provenance bypass, cross-tenant authorization
  evidence, replay-control failure, or sustained fast burn.
- Page the platform on-call for availability/abuse fast burn that threatens safe operation.
- Open a ticket for slow burn, recurring dependency-policy violations, security-export degradation,
  reconciliation freshness erosion, or capacity approaching the validated envelope.
- Link each alert to the security incident runbook and include only sanitized correlation IDs.

## Readiness and safe degradation

Liveness answers only whether the process is alive. Readiness must fail when the runtime is
draining or required authentication is not configured. Security-critical dependencies should fail
closed: inability to verify identity/provenance or establish required replay/authorization state is
not converted into a successful mutation merely to preserve availability.
