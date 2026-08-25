# `prodkit-executor-database`

Fail-closed PostgreSQL executor for controlled production database effects.

The executor requires an explicit database allowlist, exact SHA-256 allowlisting of every SQL statement, parameterized arguments, and a short-lived credential lease. Credential material is resolved only inside the executor and is never copied into action or evidence payloads. Read operations are bounded by a configured row limit and all operations use a configured statement timeout.

Applications may inject a connection factory for managed PostgreSQL services or testing; the default implementation uses `asyncpg`.
