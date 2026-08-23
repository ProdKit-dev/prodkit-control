# v0.3 interoperability profile

ProdKit Control integrates with external standards and policy/agent ecosystems through adapters. These adapters translate data or decisions at explicit boundaries; they do not transfer ownership of ProdKit's canonical authorization and evidence model.

| Integration | ProdKit boundary | Compatibility rule | Failure rule |
| --- | --- | --- | --- |
| in-toto | portable statement envelope | emit/read Statement v1 and preserve digest-bound subjects | malformed/unsupported statement fails verification |
| SLSA | provenance predicate | use `https://slsa.dev/provenance/v1`; buildDefinition/runDetails remain external-standard data | claimed SLSA provenance must parse and remain digest-bound |
| Sigstore/Cosign | optional external signer/verifier | use blob bundles; key or keyless identity+issuer; custom trust root/offline supported | non-zero/timeout/missing bundle fails closed |
| OpenTelemetry | non-authoritative projection | bounded ProdKit attributes plus standard error status/type behavior | telemetry loss never changes canonical evidence |
| MCP | agent proposal ingress | MCP tool calls map through administrator-owned bindings into `ActionSpec` proposals | unknown binding or invalid target fails before policy/execution |
| OPA | external policy decision point | adapter returns a digest-bound `PolicyDecision`; compose with ProdKit through `ConjunctivePolicyEngine` | transport/malformed decisions fail or deny; external ALLOW cannot override stricter policy |
| Other PDPs (Permit/AuthZen/custom) | `PolicyEngine` port | normalize to exact action/tenant-bound `PolicyDecision`, then compose conjunctively | mismatched action/tenant or conflicting constraints fail closed |

## MCP and agent frameworks

MCP describes tool invocation, not ProdKit authorization. The `prodkit-agentgateway` MCP adapter therefore has no execution method. A tool call is mapped through an administrator-owned `MCPToolBinding`; the binding supplies executor, operation, effect class, risk class, and target scope. Model/server-provided annotations do not determine those security properties.

The resulting `ActionSpec` enters the normal lifecycle:

```mermaid
flowchart LR
    Agent[Agent / MCP client] --> Call[MCP tool call]
    Call --> Binding[Admin-owned binding]
    Binding --> Proposal[ActionSpec proposal]
    Proposal --> Policy[Policy evaluation]
    Policy --> Approval[Exact approval when required]
    Approval --> Credential[Short-lived credential lease]
    Credential --> Executor[Controlled executor]
    Executor --> Observe[Observation + verification]
    Observe --> Evidence[Canonical evidence]
```

Reserved `interop.*` policy-context keys are owned by the adapter and cannot be overwritten by caller-supplied context. Tool-call identity is used to derive a deterministic action/idempotency identity so redelivery cannot silently become a second external effect.

Other agent frameworks should map their tool/function invocation into the same proposal shape rather than implementing an execution shortcut. A framework adapter is acceptable when it preserves the same administrator-owned binding and normal ProdKit lifecycle.

## Policy-engine composition

`ConjunctivePolicyEngine` defines portable semantics across policy systems:

- every component decision must match the exact `action_id`, action digest, and tenant;
- `DENY` dominates `REQUIRE_APPROVAL`, which dominates `ALLOW`;
- required approval roles are unioned;
- compatible constraints are combined;
- conflicting values for the same constraint force `DENY`;
- the earliest component expiry becomes the composed expiry;
- reasons preserve the originating engine name.

This prevents an integration adapter from becoming a policy bypass. An organization can combine the built-in policy with OPA or another PDP and know that an external `ALLOW` cannot relax a stronger local decision.

### OPA

`prodkit-opa` sends the canonical action and exact action digest to an OPA data API endpoint. Missing structured results and invalid outcome values become denial. HTTP/transport failure propagates rather than converting to allow.

### Permit, AuthZen, and custom PDPs

The compatibility contract is the `PolicyEngine` port, not a vendor-specific response object. Adapters should resolve any vendor principal/resource/action vocabulary outside the canonical broker and return `PolicyDecision`. They must fail closed when required principal or resource context is unavailable, and they should be composed with organization-owned ProdKit policy through `ConjunctivePolicyEngine`.

A package or module name alone does not constitute a production-ready integration. Each claimed adapter must have protocol fixtures and failure-path tests before release notes claim support.

## OpenTelemetry projection

The event projector intentionally exposes bounded metadata only: run/event/action identifiers, event sequence/hash/schema, tenant/correlation identifiers, and evidence/lineage reference counts. Arbitrary event payloads are not copied into span attributes because they can contain secrets, high-cardinality values, or large data.

Execution uncertainty, credential-revocation failures, unsuccessful execution, and failed verification project an `ERROR` span status and a predictable `error.type`. The canonical ledger remains authoritative even if traces are sampled, lost, redacted, or stored under a different retention policy.

## Sigstore operating profile

Pin a reviewed Cosign release in the deployment image. `CosignClient` never invokes a shell and never treats output text as proof; process exit status is authoritative. Keyless verification requires both certificate identity and OIDC issuer. Offline verification is the default for blob bundles when all required trust material is present.

For enterprise release workflows, organization policy should define whether both the ProdKit checkpoint and a Sigstore bundle are required. A required external signature that cannot be created or verified is a release-blocking assurance failure, not a warning.
