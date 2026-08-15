# Event model

Each run owns a contiguous sequence beginning at one. An event contains:

- stable schema name and version;
- event, run, tenant, action, trace, span, causation, and correlation identities;
- event type and aware timestamps;
- requesting or recording actor;
- typed or versioned payload data;
- typed lineage-node references for assertions affected by the event;
- evidence references;
- previous-event hash and current-event hash;
- optional signature and signing-key identity.

## Integrity

The reference implementation computes:

```text
event_hash = SHA256(canonical_json({
  "event": event_without_integrity,
  "previous_event_hash": previous_event_hash
}))
```

Hash chaining detects insertion, deletion, modification, and reordering when a trusted final hash
or signed checkpoint is available. Hashes do not by themselves prevent an administrator from
replacing an entire history and its anchor; production profiles require signed checkpoints and
retention-locked exports.

## Corrections

Events are never edited. A correction references the incorrect event and records a new assertion.
Projectors choose the effective view while preserving the historical record.

Events explain when and by whom a lineage assertion entered ProdKit. The lineage graph expresses
the durable semantic relationship between product identities. Neither is a substitute for the
other, and evidence bundles carry both when lineage is available.
