# Action and approval model

## Action digest

`ActionSpec.digest` binds identity, tenant, executor, operation, effect and risk classes, target,
arguments, expected effect, idempotency key, expiry, work pack, repository operation, and policy
context.

## Approval binding

An approval is valid only when all of the following still match:

- action digest;
- target digest and environment;
- policy decision ID and revision;
- tenant;
- approval role;
- approval outcome;
- expiration.

Changing a command argument, deployment artifact, base state, environment, or policy revision
requires a new approval.

## Human authority

The model may request approval but cannot issue an approval for itself. Applications must resolve
human identities from an authenticated identity provider and must not accept arbitrary actor IDs
from an untrusted client in production.
