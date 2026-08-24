from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import asyncpg

MIGRATIONS = Path(
    "packages/python/prodkit-control-postgres/src/prodkit_control_postgres/migrations"
)


def _connection_values() -> tuple[str, int, str, str, str]:
    return (
        os.environ["PRODKIT_POSTGRES_HOST"],
        int(os.environ["PRODKIT_POSTGRES_PORT"]),
        os.environ["PRODKIT_POSTGRES_DATABASE"],
        os.environ["PRODKIT_POSTGRES_USER"],
        os.environ["PRODKIT_POSTGRES_PASSWORD"],
    )


async def _apply(connection: asyncpg.Connection, first: int, last: int) -> None:
    for version in range(first, last + 1):
        path = next(MIGRATIONS.glob(f"{version:04d}_*.sql"), None)
        if path is None:
            raise AssertionError(f"migration {version} is missing")
        await connection.execute(path.read_text(encoding="utf-8"))


async def main() -> None:
    host, port, database, user, password = _connection_values()
    connection = await asyncpg.connect(
        host=host, port=port, database=database, user=user, password=password
    )
    schema = f"prodkit_recovery_upgrade_{uuid4().hex[:10]}"
    tenant_id = "recovery-upgrade-tenant"
    run_id = uuid4()
    request_id = uuid4()
    try:
        await connection.execute(f'CREATE SCHEMA "{schema}"')
        await connection.execute(f'SET search_path TO "{schema}", public')
        await _apply(connection, 1, 7)
        version = await connection.fetchval(
            "SELECT version FROM prodkit_schema_metadata WHERE singleton = TRUE"
        )
        if version != 7:
            raise AssertionError(f"expected starting schema 7, got {version!r}")

        now = datetime.now(UTC)
        run_document = {
            "schema_name": "prodkit.run",
            "schema_version": "1.0.0",
            "run_id": str(run_id),
            "tenant_id": tenant_id,
            "environment": "migration-ci",
            "purpose": "prove schema-7 assurance state survives recovery migration",
            "status": "running",
            "started_at": now.isoformat(),
            "initiated_by": {
                "kind": "service",
                "id": "migration-ci",
                "tenant_id": tenant_id,
                "attributes": {},
            },
            "attributes": {},
        }
        await connection.execute(
            """
            INSERT INTO control_runs (run_id, tenant_id, status, started_at, document)
            VALUES ($1, $2, 'running', $3, $4::jsonb)
            """,
            run_id,
            tenant_id,
            now,
            json.dumps(run_document),
        )
        governance_document = {
            "schema_name": "prodkit.governance-change-request",
            "schema_version": "1.0.0",
            "request_id": str(request_id),
            "tenant_id": tenant_id,
            "target_type": "tenant_configuration",
            "target_id": "recovery-profile",
            "proposed_digest": "a" * 64,
            "expected_current_digest": None,
            "risk": "high",
            "reason": "preserve governance evidence across schema-8 upgrade",
            "ticket_reference": "DR-MIGRATION-1",
            "proposed_at": now.isoformat(),
            "proposed_by": {
                "kind": "service",
                "id": "migration-ci",
                "tenant_id": tenant_id,
                "attributes": {},
            },
            "status": "proposed",
            "approved_at": None,
            "approved_by": None,
            "applied_at": None,
        }
        await connection.execute(
            """
            INSERT INTO governance_change_requests (
              tenant_id, request_id, target_type, target_id, proposed_digest,
              expected_current_digest, risk, status, proposed_at, document
            ) VALUES ($1, $2, 'tenant_configuration', 'recovery-profile', $3,
                      NULL, 'high', 'proposed', $4, $5::jsonb)
            """,
            tenant_id,
            request_id,
            "a" * 64,
            now,
            json.dumps(governance_document),
        )

        await _apply(connection, 8, 8)
        version = await connection.fetchval(
            "SELECT version FROM prodkit_schema_metadata WHERE singleton = TRUE"
        )
        if version != 8:
            raise AssertionError("schema 7 -> 8 upgrade did not reach version 8")

        preserved_run = await connection.fetchrow(
            "SELECT tenant_id, status, document FROM control_runs WHERE run_id = $1",
            run_id,
        )
        if preserved_run is None or preserved_run["tenant_id"] != tenant_id:
            raise AssertionError("schema-8 migration did not preserve canonical run ownership")
        preserved_request = await connection.fetchrow(
            """
            SELECT tenant_id, proposed_digest, document
            FROM governance_change_requests
            WHERE tenant_id = $1 AND request_id = $2
            """,
            tenant_id,
            request_id,
        )
        if preserved_request is None or preserved_request["proposed_digest"] != "a" * 64:
            raise AssertionError("schema-8 migration did not preserve governance evidence")

        required_tables = {
            "recovery_profiles",
            "recovery_backup_manifests",
            "recovery_break_glass_grants",
            "recovery_break_glass_uses",
            "recovery_break_glass_revocations",
            "recovery_restore_plans",
            "recovery_integrity_scans",
            "recovery_uncertain_executions",
            "recovery_gap_reconciliations",
            "recovery_restore_results",
            "recovery_game_day_exercises",
            "recovery_audit_events",
        }
        rows = await connection.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname = $1",
            schema,
        )
        tables = {row["tablename"] for row in rows}
        if not required_tables.issubset(tables):
            raise AssertionError(
                "schema-8 recovery migration missing tables: "
                + ", ".join(sorted(required_tables - tables))
            )

        await connection.execute(
            """
            INSERT INTO governance_migration_evidence (
              migration_id, from_schema_version, to_schema_version, applied_at,
              control_version, backup_reference, document
            ) VALUES ($1, 7, 8, $2, '0.7.0', $3, $4::jsonb)
            """,
            uuid4(),
            datetime.now(UTC),
            "ci://backup/schema-7",
            json.dumps(
                {
                    "from_schema_version": 7,
                    "to_schema_version": 8,
                    "qualified": True,
                    "assurance_state_preserved": True,
                    "recovery_gap_barrier_available": True,
                }
            ),
        )
        try:
            await connection.execute(
                "UPDATE governance_migration_evidence SET control_version = 'tampered'"
            )
        except asyncpg.RaiseError:
            pass
        else:
            raise AssertionError("migration evidence must remain append-only after schema 8")
    finally:
        await connection.execute("SET search_path TO public")
        await connection.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await connection.close()


if __name__ == "__main__":
    asyncio.run(main())
