# Operations runbook

## Integrity failure

1. Stop new production actions for the affected tenant or run.
2. Preserve database, object-store, signing, and external audit evidence.
3. Verify the latest signed checkpoint and locate the first mismatching event.
4. Determine whether the cause is corruption, application defect, or unauthorized mutation.
5. Record incident and correction events; never rewrite the original sequence.

## Ambiguous executor outcome

1. Do not automatically retry a non-idempotent action.
2. Query the target system using the execution attempt and provider operation IDs.
3. Reconcile expected and observed state.
4. Mark the action verified, mismatched, or unverifiable.
5. Require human decision before retry or rollback.

## Unexpected external action

1. Treat external activity without an authorized ProdKit event as a high-severity finding.
2. Identify the human or workload credential used.
3. Revoke bypass credentials and preserve external audit logs.
4. Determine whether production state must be rolled back.
5. Add controls preventing recurrence.
