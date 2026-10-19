/** Verification only: source-derived probes, Pi MIT (c) 2025 Mario Zechner.
 * Provenance and full notice: tests/fixtures/durable/UPSTREAM-LICENSE.txt.
 * No product runtime imports this driver. */
import assert from "node:assert/strict";
import { realpathSync, writeFileSync } from "node:fs";
import { registerHooks } from "node:module";
import { fileURLToPath } from "node:url";
import { resolve, sep } from "node:path";

const root = realpathSync(process.cwd());
const resolutions: { specifier: string; path: string }[] = [];
registerHooks({
  resolve(specifier, context, nextResolve) {
    const result = nextResolve(specifier, context);
    if (result.url.startsWith("node:")) return result;
    assert.ok(result.url.startsWith("file:"), "Only local modules are allowed");
    const path = realpathSync(fileURLToPath(result.url));
    assert.ok(path.startsWith(root + sep), "Module escaped pinned snapshot");
    resolutions.push({ specifier, path: path.slice(root.length + 1) });
    return result;
  },
});
// Dynamic imports ensure every transitive source resolution goes through the guard.
const { createSession } = await import("./packages/durable/src/session/session.ts");
const { MemoryStorage } = await import("./packages/durable/src/storage/memory.ts");
const { defineDoc } = await import("./packages/durable/src/documents.ts");
const { BACKGROUND_CONTEXT: context } = await import("@earendil-works/chord/context");
const steps: object[] = [];
const storage = new MemoryStorage();
const session = createSession(storage, { now: () => 1700000000000 });
session.subscribeCommits(publication => {
  steps.push({ op: "commit.publication", value: publication });
});
const Counter = defineDoc<{ count: number; items: string[] }>({
  kind: "fixture.counter", version: 1, scope: "session", history: "rewindable",
  fork: "asOf", initial: () => ({ count: 0, items: [] }),
});
try {
  const before = await session.snapshot(Counter, context);
  assert.equal(before, undefined);
  steps.push({ op: "snapshot.absent", value: { present: false } });
  const conversation = await session.commit(async tx =>
    tx.createConversation({ ownership: { kind: "ownerless" } }), context);
  steps.push({ op: "conversation.create", value: conversation });
  await session.commit(async tx => {
    const doc = await tx.doc(Counter);
    doc.count = 1;
    doc.items.push("committed");
  }, context);
  const first = await session.snapshot(Counter, context);
  assert.deepEqual(first, { count: 1, items: ["committed"] });
  steps.push({ op: "document.commit", value: first });
  let failure: { name: string; message: string } | undefined;
  try {
    await session.commit(async tx => {
      (await tx.doc(Counter)).count = 999;
      throw new Error("fixture rollback");
    }, context);
  } catch (error) {
    assert.ok(error instanceof Error);
    failure = { name: error.name, message: error.message };
  }
  assert.deepEqual(failure, { name: "Error", message: "fixture rollback" });
  steps.push({ op: "document.rollback.error", value: failure });
  assert.deepEqual(await session.snapshot(Counter, context), first);
  await session.commit(async tx => { (await tx.doc(Counter)).count = 2; }, context);
  const second = await session.snapshot(Counter, context);
  assert.deepEqual(first, { count: 1, items: ["committed"] });
  assert.deepEqual(second, { count: 2, items: ["committed"] });
  steps.push({ op: "document.history.retained", value: { first, second } });
  const stored = await storage.conversation(conversation.id, context);
  assert.deepEqual(stored, conversation);
  steps.push({ op: "storage.conversation", value: stored });
} finally {
  await session.close(context);
}
steps.push({ op: "session.close", value: await session.closed });
writeFileSync(resolve("trace.json"), JSON.stringify({ schema_version: 1,
  case_id: "kernel-memory-v1", normalization: "finite-json-v1", steps }, (_key, value) => {
    if (value === undefined) return { $undefined: true };
    if (typeof value === "number") {
      assert.ok(Number.isFinite(value), "Trace has a non-finite number");
      assert.ok(!Number.isInteger(value) || Number.isSafeInteger(value), "Trace integer is unsafe");
    }
    return value;
  }, 2) + "\n");
writeFileSync(resolve("resolutions.json"), JSON.stringify(resolutions, null, 2) + "\n");
