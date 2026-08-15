# Contributing

Thank you for contributing to ProdKit Control.

## Development process

1. Open an issue for significant behavior or contract changes.
2. Add or update an architecture decision record when changing a trust boundary, canonical schema, or guarantee.
3. Keep provider- and vendor-specific behavior outside the core package.
4. Add deterministic tests for every lifecycle transition and failure mode.
5. Run `make check` before submitting a pull request.

## Compatibility rules

- Canonical event and lineage schemas use semantic versions.
- Existing fields are not repurposed.
- Breaking contract changes require a major schema version.
- Generated JSON Schemas must be committed and drift-checked in CI.
- Events are immutable after append; corrections are represented by new events.
- Lineage identities and relations are immutable; corrections add superseding assertions.

## Security-sensitive changes

Changes to action authorization, approval binding, event hashing, credential handling,
executor isolation, signatures, or reconciliation require two maintainer approvals once the
project has more than one active maintainer.

## Developer certificate of origin

By contributing, you certify that you have the right to submit the contribution under the
Apache License, Version 2.0. Sign commits with `git commit -s` when possible.
