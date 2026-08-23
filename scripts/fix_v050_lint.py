from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if old in text:
        target.write_text(text.replace(old, new, 1), encoding="utf-8")
        return
    if new not in text:
        raise RuntimeError(f"expected normalization target not found in {path}")


replace_once(
    "packages/python/prodkit-control-postgres/src/prodkit_control_postgres/lineage.py",
    "            validated = LineageGraph(\n",
    "            LineageGraph(\n",
)

replace_once(
    "packages/python/prodkit-control-postgres/src/prodkit_control_postgres/tenancy.py",
    '''        suffix = " FOR UPDATE" if for_update else ""
        row = (
            (
                await session.execute(
                    text(
                        "SELECT document FROM tenant_isolation_profiles "
                        "WHERE tenant_id = :tenant_id" + suffix
                    ),
                    {"tenant_id": tenant_id},
                )
            )
            .mappings()
            .first()
        )
''',
    '''        statement = (
            text(
                "SELECT document FROM tenant_isolation_profiles "
                "WHERE tenant_id = :tenant_id FOR UPDATE"
            )
            if for_update
            else text(
                "SELECT document FROM tenant_isolation_profiles "
                "WHERE tenant_id = :tenant_id"
            )
        )
        row = (
            (await session.execute(statement, {"tenant_id": tenant_id}))
            .mappings()
            .first()
        )
''',
)

replace_once(
    "packages/python/prodkit-control-postgres/src/prodkit_control_postgres/tenancy.py",
    '''        suffix = " FOR UPDATE" if for_update else ""
        row = (
            (
                await session.execute(
                    text(
                        "SELECT document FROM support_elevation_grants "
                        "WHERE tenant_id = :tenant_id AND grant_id = :grant_id" + suffix
                    ),
                    {"tenant_id": tenant_id, "grant_id": grant_id},
                )
            )
            .mappings()
            .first()
        )
''',
    '''        statement = (
            text(
                "SELECT document FROM support_elevation_grants "
                "WHERE tenant_id = :tenant_id AND grant_id = :grant_id FOR UPDATE"
            )
            if for_update
            else text(
                "SELECT document FROM support_elevation_grants "
                "WHERE tenant_id = :tenant_id AND grant_id = :grant_id"
            )
        )
        row = (
            (
                await session.execute(
                    statement,
                    {"tenant_id": tenant_id, "grant_id": grant_id},
                )
            )
            .mappings()
            .first()
        )
''',
)

replace_once(
    "packages/python/prodkit-control-postgres/src/prodkit_control_postgres/tenancy.py",
    '''        suffix = " FOR UPDATE" if for_update else ""
        row = (
            (
                await session.execute(
                    text(
                        "SELECT document FROM tenant_lifecycle "
                        "WHERE tenant_id = :tenant_id" + suffix
                    ),
                    {"tenant_id": tenant_id},
                )
            )
            .mappings()
            .first()
        )
''',
    '''        statement = (
            text(
                "SELECT document FROM tenant_lifecycle "
                "WHERE tenant_id = :tenant_id FOR UPDATE"
            )
            if for_update
            else text(
                "SELECT document FROM tenant_lifecycle "
                "WHERE tenant_id = :tenant_id"
            )
        )
        row = (
            (await session.execute(statement, {"tenant_id": tenant_id}))
            .mappings()
            .first()
        )
''',
)
