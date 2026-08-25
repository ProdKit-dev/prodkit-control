# Portable protocol families

This directory is reserved for language-neutral protocol-family definitions that cannot be expressed completely by JSON Schema alone.

Protocol authority belongs here (together with published schemas and conformance vectors), not in a Python class or TypeScript interface. A native runtime may provide generated or idiomatic representations of a protocol, but those representations are implementations.

A protocol definition must identify:

- protocol family and version;
- request/response or state-transition semantics;
- required validation and fail-closed behavior;
- compatibility/unknown-field rules;
- canonical identity/fingerprint behavior where relevant;
- conformance vectors or fixtures.

The v0.9 contract-authority gate requires this root to exist even when a portable family is currently fully defined by schemas plus semantic specifications. New security-critical protocol semantics must not be introduced solely inside runtime code.
