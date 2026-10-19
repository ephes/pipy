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
    "tool-recovery-v1": ["clock_epoch_ms", "api", "variants"],
    "durable-abort-v1": ["clock_epoch_ms"],
    "env-conformance-v1": ["clock_epoch_ms", "scenarios"],
    "harness-round-v1": ["clock_epoch_ms", "api", "tool_name", "tool_description", "tool_arguments",
      "tool_call_id", "input", "progress_details", "tool_result", "answer"],
    "context-heads-v1": ["clock_epoch_ms", "old", "fresh", "after", "summary", "tail"],
  };
  assert.ok(Object.hasOwn(fields, caseId), "Unknown case");
  assert.deepEqual(Object.keys(input).sort(), fields[caseId].slice().sort(), "Input fields drift");
  assert.ok(Number.isSafeInteger(input.clock_epoch_ms) && (input.clock_epoch_ms as number) > 0);
}

/** Small source-derived task-support.ts barriers, Pi MIT (c) 2025 Mario Zechner. */
export function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>(done => { resolve = done; });
  return { promise, resolve };
}
export function aborted(signal: AbortSignal): Promise<never> {
  return new Promise((_, reject) => {
    if (signal.aborted) { reject(signal.reason); return; }
    signal.addEventListener("abort", () => reject(signal.reason), { once: true });
  });
}
/** Explicit semantic Error DTO, preserving own optional fields; never stringify Error as {}. */
export function errorValue(error: unknown): any {
  assert.ok(error instanceof Error);
  return { name: error.name, message: error.message,
    ...Object.fromEntries(Object.keys(error).filter(key => key !== "name").map(key =>
      [key, (error as any)[key] instanceof Error ? errorValue((error as any)[key]) : (error as any)[key]])),
    ...(Object.hasOwn(error, "cause") ? { cause: errorValue(error.cause) } : {}) };
}

/** harness-sleep.test.ts fakeTimers/controlled clock adapted to auto-fire queued
 * foreground callbacks only; no global clock or wall-time sleep. */
export function fixtureClock(epoch: number) {
  let current = epoch, nextId = 0;
  const facts: any[] = [];
  return { facts, now: () => current,
    setTimer: (callback: () => void, delayMs: number, options: { background: boolean }) => {
      assert.ok(Number.isFinite(delayMs) && delayMs >= 0);
      const id = ++nextId;
      if (options.background) { facts.push({ op: "decline-background", id, delayMs, at: current }); return undefined; }
      let active = true;
      const due = current + Math.ceil(delayMs);
      facts.push({ op: "arm", id, delayMs, at: current, due });
      queueMicrotask(() => {
        if (!active) return;
        active = false;
        const before = current; current = Math.max(current, due);
        facts.push({ op: "advance", id, before, at: current });
        facts.push({ op: "callback", id, at: current }); callback();
      });
      return () => { facts.push({ op: "cancel", id, active, at: current }); active = false; };
    },
  };
}
