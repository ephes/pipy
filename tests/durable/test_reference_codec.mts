import assert from "node:assert/strict";
import { encode } from "../../scripts/durable_reference/contract.mts";

const wire = (value: unknown): string => JSON.stringify(encode(value));
const dense = [[undefined], [null], [{ type: "undefined" }], []];
assert.equal(new Set(dense.map(wire)).size, dense.length);
assert.equal(wire([undefined]), '{"type":"array","items":[{"type":"undefined"}]}');
assert.equal(wire([null]), '{"type":"array","items":[null]}');

const sparseIndex: unknown[] = [];
sparseIndex[2] = null;
const inherited = new Array(1);
Object.setPrototypeOf(inherited, Object.assign(Object.create(Array.prototype), { 0: null }));
for (const value of [new Array(1), [null, ,], [[,]], sparseIndex, inherited]) {
  assert.throws(() => encode(value), /Sparse arrays/);
}
console.log("Reference codec: dense values remain distinct; sparse arrays rejected");
