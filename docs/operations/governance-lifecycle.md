# Governance, retention, and lifecycle operations

This runbook covers the v0.6.0 governance profile. It assumes v0.5 tenant isolation is already configured and the PostgreSQL production profile is at schema 7.

## High-risk configuration changes

For retention, trust-root, legal-hold release, compatibility policy, deprecation policy, and other high/critical production configuration changes:

1. Resolve the exact current object and compute its canonical SHA-256 digest where applicable.
2. Construct the proposed typed object and compute its canonical digest.
3. Create a `GovernanceChangeRequest` with target, proposed digest, expected current digest, risk, reason, and ticket/reference.
4. For high/critical changes, obtain approval from an actor distinct from the proposer.
5. Apply only the payload whose digest and expected-current digest match the approved request.
6. Preserve the resulting governance audit event. Never edit historical approvals or policy revisions.

If the current digest changed between proposal and application, fail the change and start a new request. Do not silently rebase an approval onto new configuration.

## Retention operation

Run retention in two phases when destructive effects are significant:

- **Evaluate/report:** call the retention evaluator and review `retain`/`delete` decisions, policy revision, deletion-not-before time, and legal-hold IDs.
- **Execute:** call the governed execution path with an idempotent `RetentionDeletionAdapter`.

The execution path re-evaluates under the tenant governance lock. A legal hold committed first prevents deletion. The adapter must use an idempotency mechanism or provider-native conditional delete where available; retrying an ambiguous destructive effect without reconciliation is not allowed.

Resource classes that cannot be safely or lawfully deleted should use `deletion_allowed=false` or indefinite retention. Append-only governance audit/migration evidence is intentionally not a normal retention-deletion target.

## Legal holds

Place a hold with a case/reference, reason, actor, and optional resource-type/resource-ID scope. Empty scope means tenant-wide for governance retention.

To release a hold:

1. Create the canonical legal-hold release intent.
2. Submit it as a critical governance change.
3. Obtain independent approval.
4. Release the hold using that exact approved request.
5. Confirm the release and audit evidence before resuming retention execution.

Any active governance hold blocks tenant deletion in schema 7. Do not bypass this database guard.

## Trust-root rotation

Normal rotation procedure:

1. Prepare the new signing key in the approved KMS/HSM/provider; private key material does not belong in ProdKit governance records.
2. Build a new `TrustRootPolicy` revision with the intended signer/key constraints.
3. Propose the exact new policy digest with `expected_current_digest` equal to the current governed trust-root digest.
4. Obtain independent approval.
5. Choose activation and overlap/retirement times. Keep the overlap long enough for in-flight signatures and verification propagation.
6. Apply `KeyRotationPlan` with sequential revision `N -> N+1`.
7. Verify at least one pre-rotation checkpoint with the historical root and one post-rotation checkpoint with the new root.
8. Confirm audit evidence records before/after digests and the overlap boundary.

Emergency rotation may use an activation time already reached, but it still requires an exact approved change. Key compromise may require revocation semantics in the underlying `TrustRootPolicy`; never rewrite historical root records.

## Evidence export and import

For export, build/verify the portable evidence package first, compute its package digest and canonical evidence-bundle manifest digest, then create the `EvidenceTransferManifest` with source control/schema version and applicable trust-root revision.

For import:

1. Verify the package SHA-256 against the transfer manifest.
2. Verify the portable package offline with an independently supplied trust policy or trust-root digest.
3. Verify the package tenant matches the transfer tenant.
4. Verify the canonical evidence-bundle manifest digest.
5. Check the source schema against the supported compatibility policy.
6. Persist an import receipt/audit event only after these checks.
7. Preserve applicable legal-hold and retention obligations after import.

Embedded trust metadata alone is not a trust anchor.

## Database upgrades to v0.6

The direct supported source schemas are 5 and 6. Before migration:

- take and identify a recoverable database backup;
- stop incompatible writers or place the service in a controlled maintenance/drain state;
- record current runtime and schema version;
- validate enough free storage for migration/index work.

Apply migrations sequentially. Never skip 6 when upgrading 5 -> 7. After migration:

- confirm `prodkit_schema_metadata.version = 7`;
- run schema compatibility check;
- verify representative pre-upgrade runs/events/tenant ownership;
- verify governance tables and triggers;
- record migration evidence including source/target schema, control version, backup reference, and time;
- run CI-equivalent retention/legal-hold/key-rotation checks before re-enabling destructive governance operations.

v0.6 does not promise an in-place rollback from schema 7. Restore from the pre-upgrade backup if rollback is required. Backup/restore qualification itself is a v0.7 release gate.

## Failure handling

- **Approval digest mismatch:** reject application; create a new proposal.
- **Legal hold appears before deletion authorization:** retain.
- **Delete adapter returns ambiguous outcome:** stop automatic retry and reconcile provider state.
- **Trust-root history cannot select one root:** fail verification; investigate time/key overlap configuration.
- **Imported package fails any digest/trust check:** do not create authoritative import evidence.
- **Database schema outside the compatibility window:** fail startup/upgrade and use the documented intermediate release path.
- **Support-mode mutation attempt:** reject; support elevation is not governance authority.
