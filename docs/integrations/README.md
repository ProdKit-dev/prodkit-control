# Integrations

Integrations are replaceable adapters. Each adapter must document:

- trust boundary and credentials;
- canonical inputs and outputs;
- timeout, retry, and idempotency behavior;
- content retention and redaction;
- external IDs available for reconciliation;
- failure and partial-success semantics;
- minimum permissions;
- version compatibility.

No commercial integration may become mandatory for the core execution and evidence contracts.
