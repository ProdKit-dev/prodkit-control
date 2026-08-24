from pathlib import Path

path = Path("scripts/ci_recovery_game_day.py")
text = path.read_text(encoding="utf-8")

replacements = (
    (
        "    RestoreStatus,\n    TenantAccessContext,",
        "    RestoreStatus,\n    RunRecord,\n    RunStatus,\n    TenantAccessContext,",
        "RunRecord imports",
    ),
    (
        "    PostgresExecutionAttemptStore,\n    PostgresRecoveryStore,",
        "    PostgresExecutionAttemptStore,\n    PostgresRecoveryStore,\n    PostgresRunStore,",
        "PostgresRunStore import",
    ),
    (
        '''            attempt_store = PostgresExecutionAttemptStore(sessions)\n            attempt_id, action_id, run_id = uuid4(), uuid4(), uuid4()\n            claimed_at = datetime.now(UTC)\n            await attempt_store.create(\n''',
        '''            attempt_store = PostgresExecutionAttemptStore(sessions)\n            run_store = PostgresRunStore(sessions)\n            attempt_id, action_id, run_id = uuid4(), uuid4(), uuid4()\n            claimed_at = datetime.now(UTC)\n            await run_store.create(\n                RunRecord(\n                    run_id=run_id,\n                    tenant_id=tenant_id,\n                    status=RunStatus.RUNNING,\n                    initiated_by=admin.actor,\n                    environment="dr-game-day",\n                    purpose="qualify uncertain execution recovery during site loss",\n                    trace_id=f"dr-game-day-{run_id.hex}",\n                    started_at=claimed_at,\n                )\n            )\n            await attempt_store.create(\n''',
        "game-day run setup",
    ),
)

for old, new, label in replacements:
    if old not in text:
        raise SystemExit(f"missing expected {label}")
    text = text.replace(old, new, 1)

path.write_text(text, encoding="utf-8")
