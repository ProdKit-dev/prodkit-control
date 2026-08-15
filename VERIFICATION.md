# Verification Report

Generated on 2026-08-15 for the `prodkit-control` rename and typed-lineage foundation.

## Passed in the creation environment

- Ruff formatting and lint checks across the repository
- Strict Mypy checking across 41 core, runtime, API, CLI, and PostgreSQL source files
- 22 Python tests with 89.42% branch-aware coverage across core and runtime
- Fail-closed tests for missing or unsuccessful production-lineage stages
- 13 generated JSON Schemas with drift checking
- Strict TypeScript checking and builds for all four renamed `@prodkit/control*` packages
- Source and wheel builds for all 32 Python workspace packages
- End-to-end `prodkit-control` CLI demo producing and verifying an eight-event evidence bundle
  containing 11 typed lineage nodes and 10 relations
- Docker Compose configuration validation using `.env.example`
- Locked Python and pnpm dependency resolution

## Not executed in this environment

- A Docker image build was not run because the local Docker daemon was unavailable.
- The PostgreSQL adapter was built and strictly type-checked, but live database migration and
  integration tests require a running PostgreSQL service.

## Important scope statement

This verifies the repository foundation and deterministic reference implementation. It does not
constitute a security certification or prove completeness for changes that bypass ProdKit Control.
Production guarantees require hardened identity, durable stores, isolated executors,
organization-owned policies, signing keys, independent external reconciliation, bypass prevention,
operational controls, and security review.
