/** Verification-only synthetic fixtures adapted from Pi MIT (c) 2025 Mario Zechner.
 * Full notice: tests/fixtures/durable/UPSTREAM-LICENSE.txt. */
import assert from "node:assert/strict";
export function encode(value: unknown): unknown {
  if (value === undefined) return { type: "undefined" };
  if (value === null || typeof value === "string" || typeof value === "boolean") return value;
  if (typeof value === "number") {
    assert.ok(Number.isFinite(value) && (!Number.isInteger(value) || Number.isSafeInteger(value)));
    return value;
  }
  if (Array.isArray(value)) {
    for (let index = 0; index < value.length; index++) {
      assert.ok(Object.hasOwn(value, index), "Sparse arrays are unsupported trace values");
    }
    return { type: "array", items: value.map(encode) };
  }
  assert.ok(typeof value === "object" && Object.getPrototypeOf(value) === Object.prototype,
    "Trace observations must be detached plain values");
  return { type: "object", entries: Object.entries(value).map(([key, child]) => [key, encode(child)]) };
}
export function validateInput(caseId: string, input: Record<string, unknown>): void {
  const fields: Record<string, string[]> = {
    "kernel-memory-v1": ["clock_epoch_ms"],
    "harness-round-v1": ["clock_epoch_ms", "api", "tool_name", "tool_description", "tool_arguments",
      "tool_call_id", "input", "progress_details", "tool_result", "answer"],
    "context-heads-v1": ["clock_epoch_ms", "old", "fresh", "after", "summary", "tail"],
  };
  assert.ok(Object.hasOwn(fields, caseId), "Unknown case");
  assert.deepEqual(Object.keys(input).sort(), fields[caseId].slice().sort(), "Input fields drift");
  assert.ok(Number.isSafeInteger(input.clock_epoch_ms) && (input.clock_epoch_ms as number) > 0);
}
