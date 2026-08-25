import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";

import {
  canonicalPortableJson,
  combinePolicySemantics,
  evaluateDefaultPolicySemantics,
} from "../packages/typescript/control/dist/portable.js";

const root = new URL("../", import.meta.url);

async function load(name) {
  const url = new URL(`contracts/conformance/${name}`, root);
  return JSON.parse(await readFile(url, "utf8"));
}

async function checkCanonicalization() {
  const document = await load("canonicalization-v1.json");
  assert.equal(document.profile, "prodkit-json-v1");
  for (const vector of document.vectors) {
    const encoded = canonicalPortableJson(vector.input);
    assert.equal(encoded, vector.canonical_json, `canonical JSON mismatch: ${vector.id}`);
    const digest = createHash("sha256").update(encoded, "utf8").digest("hex");
    assert.equal(digest, vector.sha256, `canonical digest mismatch: ${vector.id}`);
  }
}

async function checkPolicy() {
  const document = await load("policy-v1.json");
  for (const vector of document.default_policy) {
    const result = evaluateDefaultPolicySemantics(
      vector.input.effect_class,
      vector.input.risk_class,
    );
    assert.deepEqual(result, vector.expected, `default policy mismatch: ${vector.id}`);
  }
  for (const vector of document.conjunctive_policy) {
    const result = combinePolicySemantics(vector.decisions);
    assert.deepEqual(result, vector.expected, `conjunctive policy mismatch: ${vector.id}`);
  }
}

await checkCanonicalization();
await checkPolicy();
console.log("portable contract conformance: TypeScript runtime passed shared vectors");
