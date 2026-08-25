# Portable canonicalization profile: `prodkit-json-v1`

## Status

Normative for portable ProdKit Control content identity in v0.9 and later until superseded by a versioned profile.

## Domain

The portable input domain is JSON-compatible data composed of:

- `null`;
- booleans;
- UTF-8 strings;
- integers in the JavaScript safe-integer interval `[-9007199254740991, 9007199254740991]`;
- arrays of portable values, preserving array order;
- objects with string keys and portable values.

Cross-runtime decimals that require exact decimal semantics MUST be represented by a schema-defined string form unless a later profile defines a decimal encoding. NaN, infinities, binary floating-point values that cannot be represented portably, duplicate object keys, and host-language-only values are outside this portable profile and MUST be rejected or normalized by a versioned schema before canonicalization.

## Canonical representation

1. Encode the normalized value as JSON with no insignificant whitespace.
2. Object keys are ordered lexicographically by Unicode scalar value.
3. Array order is preserved.
4. Strings use ordinary JSON escaping for quotation mark, reverse solidus, and control characters. Other Unicode scalar values remain UTF-8 rather than being converted to ASCII `\uXXXX` escapes merely for transport convenience.
5. Booleans and `null` use the JSON literals `true`, `false`, and `null`.
6. Portable integers use their shortest base-10 representation with no leading plus sign or unnecessary leading zeroes.
7. The resulting character sequence is encoded as UTF-8.

The content digest for this profile is lowercase hexadecimal SHA-256 of those UTF-8 bytes.

## Typed normalization

Native runtimes may accept richer host-language values such as UUIDs, datetimes, enums, paths, decimal objects, or byte strings. Their conversion into portable JSON is normative only where a published schema/specification defines that conversion. A convenience conversion implemented only in Python or only in TypeScript does not silently become a portable contract.

## Conformance

`contracts/conformance/canonicalization-v1.json` contains shared golden vectors. Every native runtime claiming `prodkit-json-v1` MUST reproduce both the exact canonical JSON and SHA-256 digest for every vector.

A runtime may support a strict superset for local convenience, but that superset is not portable authority unless separately specified and versioned.
