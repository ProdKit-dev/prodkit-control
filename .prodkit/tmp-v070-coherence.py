from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"missing expected source fragment: {label}")
    return text.replace(old, new, 1)


# Keep TypeScript parity aligned with the hardened canonical recovery contracts.
path = Path("packages/typescript/control/src/index.ts")
text = path.read_text(encoding="utf-8")
text = replace_once(
    text,
    '  | "uncertain_attempt_reconciled"\n  | "restore_completed"',
    '  | "uncertain_attempt_reconciled"\n  | "recovery_gap_reconciled"\n  | "restore_completed"',
    "recovery audit gap event",
)
text = replace_once(
    text,
    '  readonly target_site: string;\n  readonly requested_at: string;',
    '  readonly target_site: string;\n  readonly failure_detected_at: string;\n  readonly requested_at: string;',
    "restore failure time",
)
text = replace_once(
    text,
    '  readonly reconcile_uncertain: true;\n  readonly prohibit_blind_replay: true;',
    '  readonly reconcile_uncertain: true;\n  readonly reconcile_recovery_gap: true;\n  readonly prohibit_blind_replay: true;',
    "restore recovery gap flag",
)
text = replace_once(
    text,
    '  readonly chain_verified: boolean;\n  readonly trust_anchor_verified: boolean;',
    '  readonly chain_verified: boolean;\n  readonly checkpoint_verified: boolean;\n  readonly trust_anchor_verified: boolean;',
    "integrity checkpoint proof",
)
marker = '''export interface RestoreResult {\n'''
gap = '''export interface RecoveryGapReconciliation {\n  readonly schema_name: "prodkit.recovery-gap-reconciliation";\n  readonly schema_version: "1.0.0";\n  readonly reconciliation_id: string;\n  readonly restore_id: string;\n  readonly tenant_id: string;\n  readonly recovery_point_at: string;\n  readonly failure_detected_at: string;\n  readonly completed_at: string;\n  readonly source_references: readonly string[];\n  readonly unexpected_effect_count: number;\n  readonly unresolved_effect_count: number;\n  readonly evidence_reference: string;\n  readonly blind_replay_permitted: false;\n}\n\nexport interface RestoreResult {\n'''
text = replace_once(text, marker, gap, "recovery gap contract")
text = replace_once(
    text,
    '  readonly integrity_scan_id: string;\n  readonly uncertain_recoveries:',
    '  readonly integrity_scan_id: string;\n  readonly recovery_gap_reconciliation_id: string;\n  readonly recovery_gap_reconciled: boolean;\n  readonly uncertain_recoveries:',
    "restore gap result",
)
text = replace_once(
    text,
    '  readonly chain_verified: boolean;\n  readonly trust_anchor_verified: boolean;\n  readonly uncertain_actions_reconciled: boolean;\n  readonly blind_replay_count:',
    '  readonly chain_verified: boolean;\n  readonly checkpoint_verified: boolean;\n  readonly trust_anchor_verified: boolean;\n  readonly object_store_verified: boolean;\n  readonly uncertain_actions_reconciled: boolean;\n  readonly recovery_gap_reconciled: boolean;\n  readonly durable_catalog_verified: boolean;\n  readonly blind_replay_count:',
    "game day assurance fields",
)
path.write_text(text, encoding="utf-8")


# Ruff closure for the durable recovery implementation.
path = Path(
    "packages/python/prodkit-control-postgres/src/prodkit_control_postgres/recovery.py"
)
text = path.read_text(encoding="utf-8")
text = replace_once(
    text,
    "    RecoveryAuditEventType,\n    RecoveryComponent,\n    RecoveryGapReconciliation,",
    "    RecoveryAuditEventType,\n    RecoveryGapReconciliation,",
    "unused RecoveryComponent import",
)
text = replace_once(
    text,
    '''        suffix = " FOR SHARE" if for_share else ""\n        document = await session.scalar(\n            text(\n                "SELECT document FROM recovery_break_glass_grants "\n                "WHERE tenant_id = :tenant_id AND grant_id = :grant_id" + suffix\n            ),\n            {"tenant_id": tenant_id, "grant_id": grant_id},\n        )\n''',
    '''        statement = (\n            text(\n                "SELECT document FROM recovery_break_glass_grants "\n                "WHERE tenant_id = :tenant_id AND grant_id = :grant_id FOR SHARE"\n            )\n            if for_share\n            else text(\n                "SELECT document FROM recovery_break_glass_grants "\n                "WHERE tenant_id = :tenant_id AND grant_id = :grant_id"\n            )\n        )\n        document = await session.scalar(\n            statement,\n            {"tenant_id": tenant_id, "grant_id": grant_id},\n        )\n''',
    "static break-glass select",
)
path.write_text(text, encoding="utf-8")

path = Path("scripts/ci_recovery_game_day.py")
text = path.read_text(encoding="utf-8")
text = replace_once(
    text,
    "from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine",
    "from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine",
    "unused AsyncSession import",
)
path.write_text(text, encoding="utf-8")
