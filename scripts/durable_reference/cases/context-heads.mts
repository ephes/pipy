/** C1: harness-context.test.ts newest/self head scenario; adapted helpers.
 * Pi MIT Copyright (c) 2025 Mario Zechner; see UPSTREAM-LICENSE.txt. */
import assert from "node:assert/strict";
import { BACKGROUND_CONTEXT as context } from "@earendil-works/chord/context";
import { createModels, fauxAssistantMessage } from "@earendil-works/pi-ai";
import { Harness } from "../../packages/durable/src/harness/harness.ts";
import { createRegistry } from "../../packages/durable/src/harness/registry.ts";
import { MemoryStorage } from "../../packages/durable/src/storage/memory.ts";
export async function run(input: any) {
  const harness = await Harness.open(new MemoryStorage(), { models: createModels(),
    registry: createRegistry(), now: () => input.clock_epoch_ms }, context);
  let observed: any;
  try {
    const root = await harness.root(context);
    const user = (text: string) => ({ role: "user" as const, content: text, timestamp: 1 });
    const append = (draft: any) => root.commit(tx => tx.appendEntry(root.id, draft), context);
    await append({ kind: "message", model: [user(input.old)] });
    const reset = await append({ kind: "reset", head: "self", model: [user(input.fresh)] });
    const after = await append({ kind: "message", model: [fauxAssistantMessage(input.after, { timestamp: 2 })] });
    const first = await root.context(context);
    assert.equal(reset.head, reset.id); assert.equal(first.head?.id, reset.id);
    assert.deepEqual(first.entries.map(e => e.id), [reset.id, after.id]);
    const summary = await append({ kind: "summary", head: after.id, model: [user(input.summary)] });
    const tail = await append({ kind: "message", model: [user(input.tail)] });
    const final = await root.context(context);
    assert.equal(final.head?.id, summary.id); assert.equal(final.head?.head, after.id);
    assert.deepEqual(final.entries.map(e => e.id), [summary.id, after.id, tail.id]);
    assert.deepEqual(first.messages.map(m => m.role === "assistant" ? (m.content[0] as any).text : m.content), [input.fresh, input.after]);
    assert.deepEqual(final.messages.map(m => m.role === "assistant" ? (m.content[0] as any).text : m.content), [input.summary, input.after, input.tail]);
    const page = await root.entries({}, 1000, undefined, context);
    assert.equal(page.next, undefined, "History page incomplete");
    observed = { history: [...page.items].reverse(), first, final, requested_cutoff: tail.id };
  } finally { await harness.close(context); }
  return { ...observed, close: await harness.closed };
}
