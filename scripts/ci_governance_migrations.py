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
SUPPORTED_STARTS = (5, 6)


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


async def _qualify_upgrade(start_version: int) -> None:
    host, port, database, user, password = _connection_values()
    connection = await asyncpg.connect(host=host, port=port, database=database, user=user, password=password)
    schema = f"prodkit_upgrade_{start_version}_{uuid4().hex[:10]}"
    run_id = uuid4()
    tenant_id = f"upgrade-v{start_version}"
    try:
        await connection.execute(f'CREATE SCHEMA "{schema}"')
        await connection.execute(f'SET search_path TO "{schema}", public')
        await _apply(connection, 1, start_version)
        version = await connection.fetchval(
            "SELECT version FROM prodkit_schema_metadata WHERE singleton = TRUE"
        )
        if version != start_version:
            raise AssertionError(f"expected starting schema {start_version}, got {version!r}")

        started_at = datetime.now(UTC)
        document = {
            "schema_name": "prodkit.run",
            "schema_version": "1.0.0",
            "run_id": str(run_id),
            "tenant_id": tenant_id,
            "environment": "migration-ci",
            "purpose": "preserve durable row across governance upgrade",
            "status": "running",
            "started_at": started_at.isoformat(),
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
            started_at,
            json.dumps(document),
        )

        await _apply(connection, start_version + 1, 7)
        version = await connection.fetchval(
            "SELECT version FROM prodkit_schema_metadata WHERE singleton = TRUE"
        )
        if version != 7:
            raise AssertionError(f"upgrade from {start_version} did not reach schema 7")
        preserved = await connection.fetchrow(
            "SELECT tenant_id, status, document FROM control_runs WHERE run_id = $1",
            run_id,
        )
        if preserved is None or preserved["tenant_id"] != tenant_id or preserved["status"] != "running":
            raise AssertionError(f"upgrade from {start_version} did not preserve run ownership/state")
        if preserved["document"]["run_id"] != str(run_id):
            raise AssertionError(f"upgrade from {start_version} modified canonical run document")

        required_tables = {
            "governance_change_requests",
            "governance_retention_policies",
            "governance_legal_holds",
            "governance_trust_roots",
            "governance_audit_events",
            "governance_migration_evidence",
        }
        rows = await connection.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname = $1",
            schema,
        )
        tables = {row["tablename"] for row in rows}
        if not required_tables.issubset(tables):
            raise AssertionError(
                f"upgrade from {start_version} is missing governance tables: "
                f"{sorted(required_tables - tables)}"
            )

        await connection.execute(
            """
            INSERT INTO governance_migration_evidence (
              migration_id, from_schema_version, to_schema_version, applied_at,
              control_version, backup_reference, document
            ) VALUES ($1, 6, 7, $2, '0.6.0', $3, $4::jsonb)
            """,
            uuid4(),
            datetime.now(UTC),
            f"ci://backup/schema-{start_version}",
            json.dumps(
                {
                    "from_schema_version": 6,
                    "to_schema_version": 7,
                    "source_start_version": start_version,
                    "qualified": True,
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
            raise AssertionError("migration evidence must be append-only")
    finally:
        await connection.execute("SET search_path TO public")
        await connection.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await connection.close()


async def main() -> None:
    for start in SUPPORTED_STARTS:
        await _qualify_upgrade(start)


if __name__ == "__main__":
    asyncio.run(main())
