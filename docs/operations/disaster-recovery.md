# Disaster-recovery runbook

This runbook applies to the v0.7.0 supported enterprise warm-standby recovery profile. Provider-specific backup, replication and failover mechanisms remain adapters/infrastructure, but they must preserve the assurance invariants below.

## Continuous readiness

Keep the active `ReliabilityProfile` under governed configuration change. For the qualified reference profile, target RPO is 300 seconds and target RTO is 3600 seconds. Configure backup cadence no slower than the declared RPO and alert before `max_backup_age_seconds` is exceeded.

Each backup cycle must capture or coordinate ledger, lineage, configuration, artifact metadata, object store, idempotency ownership, execution attempts and governance state into one declared snapshot set. Record a `BackupManifest` only after every required component digest is known. Retain the signed checkpoint and trust-root material such that the trust root can be obtained independently of the failed site's restored backup.

Run restore exercises at the configured `restore_exercise_interval_seconds`; do not infer recoverability from successful backup creation alone.

## Disaster declaration

1. Stop automated promotion/retry activity that could create new external effects while ownership is uncertain.
2. Record the site-failure detection time. This becomes `failure_detected_at` and must not be backdated to shrink the recovery gap.
3. Select the latest usable backup whose age and recovery point comply with the active profile.
4. Open or reference the incident/change ticket that will bind break-glass authority and recovery evidence.
5. Have an authorized tenant approver issue a short-lived break-glass grant to a different recovery operator. Grant only capabilities needed for the exercise/incident.

Support elevation is not sufficient authority for recovery governance.

## Restore into isolation

Use the `RESTORE` capability to create the restore plan. Restore into an isolated recovery target before changing routing or production authority.

Recover all manifest components. Do not substitute a newer individual component into an older snapshot set merely to make a check pass. Provider restore tooling must report enough information to reproduce the component digest observations used by ProdKit Control.

## Integrity verification

Use the `INTEGRITY_SCAN` capability and verify, in order:

1. every required component is present and its observed digest equals the manifest digest;
2. the restored ledger chain tip equals `ledger_chain_tip_sha256`;
3. the exact `SignedCheckpoint` hashes to `trusted_checkpoint_sha256`;
4. the independently obtained `TrustRootPolicy` hashes to `trust_anchor_sha256`;
5. `OfflineAssuranceVerifier` validates the checkpoint signature, signer, key validity/revocation policy and trust-root constraints;
6. object-store content passes its component verification.

Any mismatch is a failed integrity scan. Do not promote and do not repair evidence by rewriting the backup manifest.

## Reconcile uncertain execution

Use the `RECONCILE` capability. The durable PostgreSQL recovery service enumerates all tenant-owned `execution_attempts` in terminal `UNCERTAIN` state. Reconcile every one through an independent provider observation using its action/provider identity.

Accept `MATCHED_SUCCESS` or `MATCHED_FAILURE` only with an evidence reference. `RECONCILE_REQUIRED` or `UNVERIFIABLE` blocks verified recovery. Do not reset, delete or rewrite the original uncertain execution journal entry and do not issue a blind retry.

## Reconcile the recovery-point gap

Inspect independent provider/audit sources for the full interval:

`(backup.recovery_point_at, restore.failure_detected_at]`

Record the sources queried, unexpected effects discovered, unresolved effects and a durable evidence reference in `RecoveryGapReconciliation`. The record is append-only and one canonical reconciliation exists per restore.

A gap with any unresolved effect blocks promotion. Discovery of an unexpected effect is not by itself a failure if it is independently reconciled and represented in the recovery evidence; silently ignoring it is a failure.

## Complete and promote

After integrity, uncertain-attempt reconciliation and recovery-gap reconciliation are complete, request restore completion. The service calculates actual RPO/RTO from authoritative recovery data/time.

Promotion requires a **fresh live use of the `FAILOVER` break-glass capability**. If the grant expired or was revoked after earlier recovery steps, obtain a new independently approved grant rather than extending authority implicitly.

Promote only when the resulting `RestoreResult` is `VERIFIED` and `promoted=true`. A degraded or failed restore remains isolated.

After promotion:

- revoke remaining break-glass authority;
- preserve recovery audit events and the restore/game-day evidence;
- reconcile routing, deployments and external provider state again through normal ProdKit reconciliation;
- investigate every unexpected effect or integrity finding;
- record actual RPO/RTO and compare them with the active profile.

## Game-day procedure

A release or production game day must exercise a simulated site failure, restore provider-neutral/production-equivalent backup bytes into an isolated target, independently obtain the trust root, verify the signed checkpoint, reconcile at least one durable uncertain execution scenario, reconcile the RPO gap, and prove zero blind replay.

For the v0.7.0 enterprise qualification, `GameDayExercise.passed` additionally requires the durable PostgreSQL recovery catalog to have been exercised. The in-memory/reference store can test semantics but cannot satisfy that enterprise release gate.

## Fail-closed conditions

Do not promote when any of these conditions is true:

- backup is missing, stale, outside RPO, or missing a required component;
- component, chain-tip, checkpoint or trust-root digest does not match;
- checkpoint signature/trust validation fails;
- object-store recovery does not verify;
- a durable uncertain attempt remains unresolved/unverifiable;
- the RPO gap has unresolved effects or lacks independent evidence sources;
- break-glass authority is missing, expired, revoked, belongs to another operator, or lacks the exact capability;
- actual RPO/RTO exceeds the active profile;
- PostgreSQL schema is not exactly compatible with the running control version.

## Backup and migration boundary

v0.7.0 advances the durable schema from 7 to 8. Before migration, take a provider-supported backup of schema-7 state. The supported upgrade path for this milestone is schema 7 -> 8. Downgrade is not claimed; use forward-fix or restore the pre-migration backup according to the v0.6 compatibility policy.

A database backup alone is not the whole DR set: object-store material, independently retained trust roots/checkpoints, provider evidence needed for uncertain/gap reconciliation, and deployment/routing recovery procedures are part of operational recoverability.
