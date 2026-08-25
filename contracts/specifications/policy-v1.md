# Portable policy semantics v1

## Status

Normative for the built-in reference policy and fail-closed policy composition used by ProdKit Control v0.9 and later until superseded by a versioned profile.

This specification does **not** make ProdKit Control a universal policy language. Organization policy content may remain in OPA/Rego, Permit, cloud IAM, or another provider. Those systems are adapters. The portable authority here defines the canonical outcomes and how multiple decisions are composed before an action receives authority.

## Canonical outcomes

The portable policy outcome vocabulary is:

- `allow`;
- `require_approval`;
- `deny`.

The security precedence is:

`deny > require_approval > allow`.

A provider-specific outcome that cannot be mapped unambiguously into this vocabulary MUST fail closed rather than be guessed.

## `prodkit-default-policy-v1`

Given an action with `effect_class` and `risk_class`:

1. If `effect_class == privileged` AND `risk_class == critical`, return:
   - outcome: `deny`;
   - reason: `critical_privileged_action_denied_by_default`;
   - no required approval roles.
2. Otherwise, if `effect_class` is one of `write`, `destructive`, or `privileged`, OR `risk_class` is one of `high` or `critical`, return:
   - outcome: `require_approval`;
   - reason: `side_effect_requires_exact_approval`;
   - required role: `production_approver`.
3. Otherwise return:
   - outcome: `allow`;
   - reason: `low_risk_action_allowed`;
   - no required approval roles.

This profile is a safe reference policy, not an organization-specific authorization policy.

## `prodkit-conjunctive-policy-v1`

Given one or more normalized policy decisions:

1. The resulting outcome is the strictest input outcome according to the precedence above.
2. Reason codes are retained in input order and namespaced as `<policy_engine>:<reason_code>`.
3. Required approval roles are the sorted unique union of all input roles.
4. Constraints are combined by key. If two decisions provide different values for the same key, the resulting outcome becomes `deny` and a reason `constraint_conflict:<key>` is appended for each conflicting key in sorted key order.
5. If no input decision supplies a reason code, use `all_policy_engines_returned_no_reason`.
6. Decision identity, tenant, action digest, expiry, and freshness validation are part of the surrounding Control decision contract. A decision bound to another action or tenant is an integrity violation and MUST NOT participate in composition.
7. The composed expiry is the earliest non-null input expiry.

No adapter may downgrade a `deny` to `require_approval` or `allow`, drop a conflicting constraint, or synthesize approval authority that was not present in the normalized inputs.

## Cross-runtime conformance

`contracts/conformance/policy-v1.json` is shared by every native implementation. The Python and TypeScript runtimes MUST independently reproduce the expected outcome, reason codes, roles, and constraints.

Runtime-specific implementation details—async APIs, classes, framework bindings, clocks, UUID generation, or provider clients—are not normative. Only the specified portable inputs and outputs are authoritative.
