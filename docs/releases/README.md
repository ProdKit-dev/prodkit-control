# Releases and versioning

This directory records the exact boundary of each ProdKit Control release. Release notes are historical evidence: once a release is published, its release note describes what that release actually contained and must not be silently expanded by later roadmap work.

## Versioning model

ProdKit Control uses semantic `vMAJOR.MINOR.PATCH` tags and intentionally reserves the initial zero version for the canonical foundation.

| Version shape | Meaning before 1.0 |
| --- | --- |
| `v0.0.0` | Canonical foundation snapshot: architecture, contracts, package boundaries, reference runtime, release machinery, and documentation baseline. |
| `v0.N.0`, `N >= 1` | A maturity/capability milestone defined by `ROADMAP.md`. |
| `v0.N.P`, `P >= 1` | A corrective release for an already published milestone. It is not a new roadmap stage. |
| `v1.0.0` | The production assurance profile after every documented 1.0 gate is evidenced. |

The intended roadmap sequence is therefore:

```text
v0.0.0 -> v0.1.0 -> v0.2.0 -> ... -> v0.9.0 -> v1.0.0
```

Patch releases may appear between milestones when a published milestone requires a compatible correction.

## Git tag and GitHub Release naming

The immutable Git tag is the semantic version only:

```text
vX.Y.Z
```

The human-readable GitHub Release title includes the repository display name:

```text
ProdKit Control vX.Y.Z
```

Examples:

```text
Tag:     v0.0.0
Release: ProdKit Control v0.0.0

Tag:     v0.1.0
Release: ProdKit Control v0.1.0
```

Release publishing must fail closed if the tag, release title, source version, package versions, release note, or release assets disagree.

## Release source contract

A release candidate is valid only when all first-party release-bearing surfaces agree on the exact version, including:

- root workspace metadata;
- every Python package;
- every TypeScript package;
- lock metadata required by the build;
- runtime/API version metadata where exposed;
- changelog entry;
- `docs/releases/vX.Y.Z.md`;
- release commit subject `release: vX.Y.Z`.

The permanent lifecycle first requires CI, Security, and CodeQL to succeed on the exact current `main` SHA. An explicitly dispatched **Trusted Release Proof** must then re-prove that same immutable source without mutating tracked files. Only after those gates succeed may the Release workflow consume the proof, build every distribution, inspect package contents, generate checksums/SBOM evidence and attestations, create or verify the immutable tag, verify published asset metadata, and publish the GitHub Release.

Release Metadata is a separate repair/reconciliation workflow. It may normalize release titles and repository metadata after publication, but it is not a substitute for exact-source proof or publication gates.

## Release completion versus product maturity

These are deliberately different concepts.

**Release complete** means that one version's source, CI/security proof, tag, release metadata, artifacts, checksums, and cleanup are closed.

**Milestone complete** means the corresponding roadmap capabilities and release gates are actually implemented and evidenced.

**Production assurance complete** is reserved for the `v1.0.0` supported profile and its documented guarantees.

A perfectly closed `v0.0.0` release is therefore still a foundation release; it does not imply that the later production and enterprise controls already exist.

## Corrections

Normal published tags are immutable. If a shipped milestone requires a compatible fix, publish a patch version rather than moving its tag.

A repository-bootstrap numbering correction is exceptional and must be completed as one controlled operation: establish and independently verify the canonical replacement release first, then retire the provisional release/tag only after the replacement is proven. The repository must not leave two releases claiming to be the canonical foundation.

## Roadmap ownership

`ROADMAP.md` defines future maturity milestones and their gates. Individual release notes define historical shipped scope. Architecture documents define the system's current and target invariants. When those disagree, the repository must be corrected before a release is declared complete.
