# Operations runbook

This runbook defines operational response patterns for ProdKit Control. It is a baseline for production operators; environment-specific deployments should add concrete service names, dashboards, escalation contacts, credential systems, and recovery commands without weakening these semantics.

## Operational priorities

During an incident, prioritize in this order:

1. prevent additional unauthorized/unsafe production effects;
2. preserve canonical and independent evidence;
3. classify in-flight/uncertain actions without blind retry;
4. restore integrity/authentication/policy/durability controls;
5. reconcile external production state;
6. resume privileged actions only when the required assurance controls are trustworthy;
7. append incident/correction evidence rather than rewriting history.

## Incident severity guidance

### Critical

Examples:

- confirmed broker bypass with production effect;
- cross-tenant action/data access;
- canonical integrity/checkpoint failure suggesting tampering;
- signing/workload credential compromise;
- uncontrolled duplicate destructive action;
- policy/approval fail-open behavior;
- widespread inability to durably record production actions while effects continue.

### High

Examples:

- reconciliation mismatch on high-risk production target;
- unresolved uncertain production action past defined threshold;
- unexpected external action with unknown actor;
- executor isolation failure;
- restore/DR inconsistency in canonical evidence.

### Medium/operational

Examples:

- reconciliation lag within bounded safety policy;
- executor/provider outage with fail-closed behavior working correctly;
- artifact export failure without canonical data loss;
- approval backlog or worker saturation.

Severity should be adapted to tenant/risk/environment policy.

## Integrity failure

Symptoms:

- event hash/sequence mismatch;
- artifact digest mismatch;
- signed checkpoint/archive digest does not match;
- lineage identity/relation validation fails unexpectedly.

Response:

1. Stop new production actions for the affected tenant/run/scope when integrity is required for authorization.
2. Preserve database, object-store, signing, service logs, and external audit evidence.
3. Verify the latest independently trusted checkpoint/archive digest.
4. Locate the first mismatching event/artifact/reference.
5. Determine whether the cause is corruption, application defect, key compromise, operator error, or unauthorized mutation.
6. Compare with independent Git/CI/cloud/database/deployment evidence.
7. Record incident/correction events; never rewrite the original sequence to hide the mismatch.
8. Rotate/revoke compromised signing or service credentials if applicable.
9. Re-establish and verify a trusted checkpoint before restoring the affected assurance profile.

## Ambiguous executor outcome

Symptoms:

- executor timeout/network reset after execution started;
- worker crash during a target operation;
- local exception but external operation may have been accepted;
- response lost before durable result persistence.

Response:

1. Do **not** automatically retry a non-idempotent action.
2. Confirm the durable action digest, idempotency key, execution-attempt identity, and target.
3. Query the target using external operation/request/deployment/transaction identities where available.
4. Inspect independent audit/reconciliation sources.
5. Compare expected and observed state.
6. Mark the action verified, failed, mismatched, or unverifiable according to evidence.
7. Require policy/human decision before retry or compensating action when uncertainty remains.
8. Treat retry/rollback as new controlled activity with complete evidence.

## Unexpected external action

Symptoms:

- Git/cloud/database/Kubernetes/deployment audit activity has no corresponding authorized ProdKit action;
- external actor/credential is not an expected executor identity.

Response:

1. Treat unmatched privileged production activity as a high-severity finding until explained.
2. Preserve the external audit record and relevant canonical state.
3. Identify the human/workload credential and origin path.
4. Revoke or restrict bypass credentials/network routes where safe.
5. Determine whether the production state is acceptable, must be rolled back, or requires emergency authorization/response.
6. Do not retroactively fabricate an approval to make the event look compliant.
7. Record the finding and any corrective/compensating actions.
8. Add controls/reconciliation coverage preventing recurrence.

## Policy or approval service outage

Expected behavior: fail closed for action classes requiring the unavailable service.

Response:

1. Confirm the runtime is denying/defering rather than default-allowing.
2. Assess whether already-authorized actions remain valid/fresh under policy.
3. Stop new high-risk actions if freshness cannot be established.
4. Restore the policy/approval dependency.
5. Verify policy revision/identity after restoration.
6. Re-run authorization for actions whose decisions expired or whose context changed.
7. Investigate any action executed during the outage contrary to the fail-closed policy.

Do not manually bypass the broker merely to restore availability.

## Authentication/identity outage

1. Fail closed for operations requiring authenticated identity.
2. Do not accept arbitrary headers/client actor IDs as a temporary production substitute.
3. Preserve existing sessions/tokens only according to documented validity and revocation policy.
4. Restore identity service or switch to a pre-approved documented failover identity mechanism.
5. Verify tenant/role claims before resuming privileged operations.

## Canonical database outage

If required canonical durability is unavailable:

1. Stop/defer new production effects that cannot be durably recorded first.
2. Do not fall back to in-memory-only production execution.
3. Preserve health/diagnostic read paths if safe.
4. Restore database availability/consistency.
5. Verify migrations, event sequence integrity, idempotency state, and recent checkpoints.
6. Reconcile any external effects suspected during the outage.
7. Resume only after durable ownership semantics are trustworthy.

## Artifact/object-store outage

Behavior depends on active retention/assurance profile.

1. Determine whether the action requires artifact persistence before execution.
2. Fail closed when required evidence cannot be durably stored.
3. Do not silently downgrade `full`/`redacted` retention to `hash_only` unless policy explicitly allows it and records the downgrade.
4. Restore the store and verify content digests/references.
5. Re-run incomplete exports/checkpoints from canonical data where possible.

## Reconciliation outage or lag

1. Identify affected sources, tenants, targets, and freshness windows.
2. Stop converting pending evidence into `matched` after the maximum allowed freshness expires.
3. Alert according to the selected profile/risk class.
4. Preserve reconciliation cursors/last-success identity.
5. Restore source access and resume from durable cursor/checkpoint.
6. Reconcile the full gap window.
7. Escalate any unexpected/mismatched activity discovered after recovery.

## Signing/trust-anchor failure

If the selected assurance profile requires signing/checkpoint anchoring:

1. Fail closed for operations that require a fresh trusted anchor when the trust service is unavailable or invalid.
2. Distinguish temporary signer outage from suspected key compromise.
3. Preserve unsigned canonical records if the profile permits deferred signing, explicitly marking trust state.
4. On compromise, revoke/rotate key material and document the trust transition.
5. Re-verify historical checkpoints according to key validity/revocation policy.
6. Publish/record the new trusted anchor chain before resuming the affected claim level.

## Suspected key/credential compromise

1. Stop use of the suspected credential/key.
2. Revoke/rotate it through the authoritative identity/KMS/secret system.
3. Preserve access/audit logs and affected canonical actions.
4. Identify every executor/action/tenant/target reachable with the compromised credential.
5. Reconcile external activity across the compromise window.
6. Rotate downstream credentials if exposure permits lateral movement.
7. Record incident and trust/key transition evidence.

## Cross-tenant isolation incident

1. Stop the affected endpoint/worker/path if leakage or cross-tenant effect is ongoing.
2. Preserve request, principal, repository/query, task, cache, executor, and audit evidence.
3. Identify whether the breach was read, write, action execution, artifact access, policy/approval, or administrative access.
4. Determine affected tenants/resources/time window.
5. Revoke unsafe sessions/credentials/caches.
6. Fix the isolation path and add a regression/negative test.
7. Reconcile any cross-tenant external effects.
8. Follow contractual/legal notification obligations through the organization's incident process.
9. Require security review before restoring the affected enterprise isolation claim.

## Executor compromise or isolation failure

1. Remove the executor instance/image/credential from service.
2. Block its ability to acquire new work.
3. Preserve runtime image/config/logs and action inputs.
4. Enumerate actions executed by the instance during the affected window.
5. Reconcile each high-risk action against independent target/audit evidence.
6. Rotate credentials available to the executor.
7. Validate replacement executor image/config/capabilities before returning to service.
8. Record findings and strengthen sandbox/capability controls.

## Stale-worker / duplicate-execution incident

1. Stop both competing workers if duplicate effect risk continues.
2. Inspect durable lease/fencing/idempotency state.
3. Determine whether one or both target operations occurred.
4. Reconcile external state before compensation/retry.
5. Fix ownership/fencing semantics; do not solve only by increasing timeout.
6. Add a concurrency/failover regression test.

## Failed deployment or migration

1. Stop rollout if the new version is producing incompatible canonical state.
2. Determine whether rollback is safe given schema/event versions already written.
3. Prefer forward fix when an older binary cannot safely read new durable state.
4. Preserve migration logs and canonical integrity evidence.
5. Verify event/lineage/idempotency state after recovery.
6. Reconcile in-flight external actions affected during rollout.
7. Record the operational change/incident through normal change controls.

## Backup restore

After restoring a backup:

1. Verify database schema/application compatibility.
2. Verify event sequence/hash integrity.
3. Verify artifact references/content digests for sampled or required sets.
4. Verify the latest trusted external checkpoint/archive digest.
5. Restore idempotency/execution-attempt state.
6. Identify actions that were in-flight/uncertain at the backup/recovery boundary.
7. Reconcile those actions before retry.
8. Verify tenant/policy/approval/security configuration.
9. Resume read-only services first where practical.
10. Resume privileged execution only after assurance controls are healthy.

## Disaster recovery

A DR event should follow the same validation as backup restore plus:

- validate configured RPO/RTO outcome;
- verify DNS/ingress/identity/policy/workload credential dependencies;
- verify executor isolation/target routing in the recovery environment;
- verify external reconciliation source access;
- verify trust-anchor/signing configuration;
- document any evidence gap introduced by the disaster window.

## Break-glass action

Break-glass is an explicit controlled profile, not an invisible bypass.

1. Authenticate the emergency operator strongly.
2. Record incident/reason and exact requested capability.
3. Limit scope/time/target.
4. Apply required emergency policy/approval path.
5. Execute through an auditable controlled mechanism where technically possible.
6. Capture/reconcile external evidence.
7. Revoke emergency elevation after use.
8. Conduct post-action review.

## Resuming production actions

Before resuming a paused assurance scope, confirm:

- authenticated principal resolution healthy;
- canonical durable storage healthy and integrity verified;
- policy/approval healthy and current;
- executor isolation/credentials healthy;
- idempotency ownership trustworthy;
- reconciliation sources caught up to required freshness;
- trust anchors/signing healthy when required;
- no unresolved critical tenant/security compromise;
- incident-specific exit criteria completed.

## Post-incident requirements

For significant incidents:

- preserve evidence according to retention/legal requirements;
- append incident/correction findings to the appropriate audit record;
- document root cause and contributing control gaps;
- add deterministic regression/adversarial tests;
- update threat model/runbook/architecture when assumptions changed;
- update roadmap/release gates when a systemic missing control is identified.

## Current implementation boundary

`v0.0.1` provides canonical integrity, action lifecycle, uncertainty, evidence, and fail-closed contract foundations. Environment-specific production automation, SLOs, HA/DR exercises, enterprise escalation ownership, and complete reconciler coverage are roadmap-gated and must be added by the supported production profile.
