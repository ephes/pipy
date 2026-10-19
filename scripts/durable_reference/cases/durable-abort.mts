/** H3: harness-tasks.test.ts late writes after durable abort mark.
 * Adapted Pi MIT Copyright (c) 2025 Mario Zechner; see UPSTREAM-LICENSE.txt. */
import assert from "node:assert/strict";
import { BACKGROUND_CONTEXT as context } from "@earendil-works/chord/context";
import { createModels } from "@earendil-works/pi-ai";
import { Harness } from "../../packages/durable/src/harness/harness.ts";
import { createRegistry } from "../../packages/durable/src/harness/registry.ts";
import { defineExtension } from "../../packages/durable/src/harness/define.ts";
import { defineTask } from "../../packages/durable/src/tasks.ts";
import { MemoryStorage } from "../../packages/durable/src/storage/memory.ts";
import { deferred, aborted, errorValue } from "../contract.mts";
export async function run(input: any) {
  const reached = deferred(), events: any[] = [], publications: any[] = [], late: any[] = [], reports: any[] = [];
  let runRuntime: any, callbackEntered = false, memoRead: any, abortInvocations = 0, freshRuntime: any, abortCurrent: any;
  const task = defineTask<null, { phase: "run" }, null>({ name: "fixture.marked", version: 1,
    initial: () => ({ phase: "run" }), phases: { run: async (_task, runtime, ctx) => {
      runRuntime = runtime; await runtime.memo("kept", 1, ctx); events.push("run.kept"); reached.resolve();
      await aborted(runtime.signal).catch(() => {}); events.push("run.cancellation-continuation");
      memoRead = await runtime.memo("kept", context);
      try { await runtime.memo("late", 2, context); late.push({ op: "memo", status: "fulfilled" }); }
      catch (error) { late.push({ op: "memo", status: "rejected", error: errorValue(error) }); }
      try { await runtime.commit(async tx => {
        callbackEntered = true; await tx.appendEntry(_task.conversationId, { kind: "fixture.forbidden", data: { late: true } });
        return { status: "terminal", outcome: { status: "completed", result: null } };
      }, context); late.push({ op: "commit", status: "fulfilled" }); }
      catch (error) { late.push({ op: "commit", status: "rejected", error: errorValue(error) }); }
      events.push("run.late-settled");
    } }, abort: async (_task, runtime, ctx) => {
      abortInvocations++; freshRuntime = runtime !== runRuntime; events.push("abort.invocation");
      await runtime.commit((_tx, current) => {
        abortCurrent = structuredClone(current);
        return { status: "terminal", outcome: { status: "aborted", reason: "mark" } };
      }, ctx);
    },
  });
  const registry = createRegistry(); registry.install(defineExtension({ name: "fixture.tasks", tasks: [task] }));
  const harness = await Harness.open(new MemoryStorage(), { models: createModels(), registry,
    now: () => input.clock_epoch_ms, onReport: error => reports.push(errorValue(error)) }, context);
  let observed: any;
  try {
    const root = await harness.root(context);
    const id = await root.commit(tx => tx.createTask(task, null, { ownership: { kind: "conversation" } }), context);
    const admitted = await harness.getTask(id, context);
    const unsubscribe = harness.subscribeCommits(publication => {
      for (const change of publication.changes) if (change.type === "task" && change.value.id === id && change.value.abortRequested) {
        publications.push(structuredClone(publication));
        if (publications.length === 1) events.push("host.durable-mark-publication");
      }
    });
    try {
      harness.resume(); await reached.promise;
      const running = await harness.getTask(id, context);
      const abortResult = await harness.abortTask(id, context); events.push("abortTask.joined");
      const final = await harness.waitForTask(id, context);
      const page = await root.entries({}, 1000, undefined, context); assert.equal(page.next, undefined);
      observed = { id, admitted, running, publications, events, memoRead, late, callbackEntered,
        abortInvocations, freshRuntime, abortCurrent, abortResult, final, history: [...page.items].reverse(), reports };
      assert.equal(callbackEntered, false); assert.equal(abortInvocations, 1); assert.equal(freshRuntime, true);
      assert.equal(memoRead, 1); assert.deepEqual(final.state.outcome, { status: "aborted", reason: "mark" });
      assert.equal(publications.length > 0, true); assert.equal(late.length, 2);
      assert.ok(late.every(item => item.status === "rejected"));
    } finally { unsubscribe(); }
  } finally { await harness.close(context); }
  return { ...observed, close: await harness.closed };
}
