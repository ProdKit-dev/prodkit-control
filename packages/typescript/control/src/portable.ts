export type PortableJsonValue =
  | null
  | boolean
  | string
  | number
  | readonly PortableJsonValue[]
  | { readonly [key: string]: PortableJsonValue };

function compareUnicodeScalarValues(left: string, right: string): number {
  const a = Array.from(left, (value) => value.codePointAt(0) ?? 0);
  const b = Array.from(right, (value) => value.codePointAt(0) ?? 0);
  const length = Math.min(a.length, b.length);
  for (let index = 0; index < length; index += 1) {
    const leftPoint = a[index];
    const rightPoint = b[index];
    if (leftPoint === undefined || rightPoint === undefined) break;
    if (leftPoint !== rightPoint) return leftPoint - rightPoint;
  }
  return a.length - b.length;
}

function encodeString(value: string): string {
  const encoded = JSON.stringify(value);
  if (encoded === undefined) throw new TypeError("portable JSON string could not be encoded");
  return encoded;
}

/** Implement the language-neutral `prodkit-json-v1` portable profile. */
export function canonicalPortableJson(value: PortableJsonValue): string {
  if (value === null) return "null";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "string") return encodeString(value);
  if (typeof value === "number") {
    if (!Number.isSafeInteger(value)) {
      throw new RangeError("prodkit-json-v1 permits only safe integers as JSON numbers");
    }
    return String(value);
  }
  if (Array.isArray(value)) {
    return `[${value.map((item) => canonicalPortableJson(item)).join(",")}]`;
  }

  const record = value as Readonly<Record<string, PortableJsonValue>>;
  const keys = Object.keys(record).sort(compareUnicodeScalarValues);
  return `{${keys
    .map((key) => `${encodeString(key)}:${canonicalPortableJson(record[key] as PortableJsonValue)}`)
    .join(",")}}`;
}

export type PortablePolicyOutcome = "allow" | "require_approval" | "deny";
export type PortableEffectClass = "read" | "write" | "destructive" | "privileged";
export type PortableRiskClass = "low" | "medium" | "high" | "critical";
export type PortableConstraintValue = string | number | boolean | null;

export interface PortablePolicySemanticDecision {
  readonly engine: string;
  readonly outcome: PortablePolicyOutcome;
  readonly reason_codes: readonly string[];
  readonly required_approval_roles: readonly string[];
  readonly constraints: Readonly<Record<string, PortableConstraintValue>>;
}

export interface PortablePolicySemanticResult {
  readonly outcome: PortablePolicyOutcome;
  readonly reason_codes: readonly string[];
  readonly required_approval_roles: readonly string[];
  readonly constraints: Readonly<Record<string, PortableConstraintValue>>;
}

/** Implement the language-neutral `prodkit-default-policy-v1` profile. */
export function evaluateDefaultPolicySemantics(
  effectClass: PortableEffectClass,
  riskClass: PortableRiskClass,
): PortablePolicySemanticResult {
  if (effectClass === "privileged" && riskClass === "critical") {
    return {
      outcome: "deny",
      reason_codes: ["critical_privileged_action_denied_by_default"],
      required_approval_roles: [],
      constraints: {},
    };
  }
  if (
    effectClass === "write" ||
    effectClass === "destructive" ||
    effectClass === "privileged" ||
    riskClass === "high" ||
    riskClass === "critical"
  ) {
    return {
      outcome: "require_approval",
      reason_codes: ["side_effect_requires_exact_approval"],
      required_approval_roles: ["production_approver"],
      constraints: {},
    };
  }
  return {
    outcome: "allow",
    reason_codes: ["low_risk_action_allowed"],
    required_approval_roles: [],
    constraints: {},
  };
}

/** Implement the language-neutral `prodkit-conjunctive-policy-v1` profile. */
export function combinePolicySemantics(
  decisions: readonly PortablePolicySemanticDecision[],
): PortablePolicySemanticResult {
  if (decisions.length === 0) throw new Error("conjunctive policy requires at least one decision");

  const rank: Record<PortablePolicyOutcome, number> = {
    allow: 0,
    require_approval: 1,
    deny: 2,
  };
  let outcome: PortablePolicyOutcome = "allow";
  const reasons: string[] = [];
  const roles = new Set<string>();
  const constraints: Record<string, PortableConstraintValue> = {};
  const conflicts = new Set<string>();

  for (const decision of decisions) {
    if (rank[decision.outcome] > rank[outcome]) outcome = decision.outcome;
    for (const reason of decision.reason_codes) reasons.push(`${decision.engine}:${reason}`);
    for (const role of decision.required_approval_roles) roles.add(role);
    for (const [key, value] of Object.entries(decision.constraints)) {
      if (Object.prototype.hasOwnProperty.call(constraints, key) && constraints[key] !== value) {
        conflicts.add(key);
      } else {
        constraints[key] = value;
      }
    }
  }

  if (conflicts.size > 0) {
    outcome = "deny";
    for (const key of [...conflicts].sort(compareUnicodeScalarValues)) {
      reasons.push(`constraint_conflict:${key}`);
    }
  }

  return {
    outcome,
    reason_codes: reasons.length > 0 ? reasons : ["all_policy_engines_returned_no_reason"],
    required_approval_roles: [...roles].sort(compareUnicodeScalarValues),
    constraints,
  };
}
