# Releases and versioning

This directory records the exact boundary of each ProdKit Control release. Release notes are historical evidence: once a release is published, its release note describes what that release actually contained and must not be silently expanded by later roadmap work.

## Versioning model

ProdKit Control uses semantic `vMAJOR.MINOR.PATCH` tags and intentionally reserves the initial zero major version for maturity-gated evolution.

| Version shape | Meaning before 1.0 |
| --- | --- |
| `v0.0.0` | Canonical foundation snapshot: architecture, contracts, package boundaries, reference runtime, release machinery, and documentation baseline. |
| `v0.N.0`, `N >= 1` | A maturity/capability milestone defined by `ROADMAP.md`. |
| `v0.N.P`, `P >= 1` | A compatible correction/readiness release for an already published milestone. It is not a new roadmap stage. |
| `v1.0.0` | The production assurance profile after every documented 1.0 gate is evidenced. |

The current maturity sequence is:

```text
v0.0.0 -> v0.1.0 -> ... -> v0.8.0 -> v0.9.0 -> v0.10.0 -> v1.0.0
                                         |          |
                                         |          +-- Production Candidate
                                         +-- cumulative completeness + language-neutral authority
```

`v0.9.1` is the public-readiness patch on the v0.9 milestone. It does not replace v0.10.0 as the next maturity milestone.

## Current public release boundary

The v0.9.1 public release is expected to provide a coherent end-user surface in addition to the inherited v0.9 implementation guarantees:

- current-facing README/security/support/getting-started docs agree on the exact release and next milestone;
- source checkout / exact source archive is reproducible from committed locks;
- public examples and the real CLI demo execute successfully in CI/release proof;
- first-party package metadata carries public README/license/repository identity;
- every GitHub Release artifact remains bound to the exact source and independently verified.

Registry channels are separate claims. v0.9.1 does not imply that PyPI, npm, or a container registry is an official publication channel unless a future release explicitly qualifies that channel.

## Git tag and GitHub Release naming

The immutable Git tag is the semantic version only:

```text
vX.Y.Z
```

The human-readable GitHub Release title includes the repository display name:

```text
ProdKit Control vX.Y.Z
```

Release publishing fails closed if the tag, release title, source version, package versions, release note, or release assets disagree.

## Release source contract

A release candidate is valid only when all first-party release-bearing surfaces agree on the exact version, including:

- root workspace metadata;
- every Python package;
- every TypeScript package;
- frozen dependency metadata required by the build;
- runtime/API version metadata where exposed;
- package-completeness and public-readiness contracts;
- changelog entry;
- `docs/releases/vX.Y.Z.md`;
- release commit subject `release: vX.Y.Z`.

For v0.9.1 and later, current-facing documentation and public package metadata are explicit release gates rather than informal cleanup.

## Permanent release lifecycle

The permanent lifecycle first requires CI, Security, and CodeQL to succeed on the exact current `main` SHA. An explicitly dispatched **Trusted Release Proof** then re-proves that same immutable source without mutating tracked files. Only after those gates succeed may the Release workflow consume the proof, build every distribution, inspect package contents, generate checksums/SBOM evidence, create/verify the immutable tag, verify published asset metadata, and publish the GitHub Release.

A separately dispatched **Release Verification** workflow verifies the immutable publication from the tag and published assets. Only successful independent verification may dispatch the guarded release-branch cleanup.

Release preparation is ordinary committed source. CI/release workflows verify source; they do not rewrite release versions, regenerate changelog text, or mutate the release candidate into a different source tree during qualification.

## Release completion versus product maturity

These are deliberately different concepts.

**Release complete** means one version's source, CI/security proof, tag, release metadata, artifacts, checksums/SBOM, independent verification, and cleanup are closed.

**Milestone complete** means the corresponding roadmap capabilities and release gates are actually implemented and evidenced.

**Public-ready** means the supported distribution/documentation/examples/support/security surface is usable and release-gated. It does not automatically mean Production Candidate or enterprise assurance.

**Production assurance complete** is reserved for the `v1.0.0` supported profile and its documented guarantees.

## Corrections

Published tags are immutable. If a shipped milestone requires a compatible fix, publish a patch version rather than moving its tag. Documentation corrections that need to be part of an immutable source snapshot likewise use a patch release.

Historical release notes, tags, release artifacts, checksums, and accepted ADRs are not rewritten to make a later release look cleaner. Current-facing docs may be corrected by later commits/releases while preserving the original evidence.

## Roadmap ownership

`ROADMAP.md` defines future maturity milestones and their gates. Individual release notes define historical shipped scope. Architecture documents define the system's current and target invariants. The machine-readable public-readiness/package-completeness contracts bind current release-facing surfaces. When these disagree, the repository must be corrected before a release is declared complete.
