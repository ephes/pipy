/** H2: harness-tools-recovery.test.ts unsafe intent + stored/current safety matrix.
 * Adapted Pi MIT Copyright (c) 2025 Mario Zechner; see UPSTREAM-LICENSE.txt. */
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, readFile, stat, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { BACKGROUND_CONTEXT as context } from "@earendil-works/chord/context";
import { createModels, fauxProvider, fauxAssistantMessage, fauxToolCall, Type } from "@earendil-works/pi-ai";
import { Harness } from "../../packages/durable/src/harness/harness.ts";
import { createRegistry } from "../../packages/durable/src/harness/registry.ts";
import { defineExtension, defineTool } from "../../packages/durable/src/harness/define.ts";
import { LiveDoc } from "../../packages/durable/src/harness/live.ts";
import { ProviderDoc } from "../../packages/durable/src/harness/provider.ts";
import { UsageDoc } from "../../packages/durable/src/harness/usage.ts";
import { openNodeSqliteStorage } from "../../packages/durable/src/storage/sqlite/node.ts";
import { deferred, aborted, errorValue, fixtureClock } from "../contract.mts";
export async function run(input: any, output: { directory: string }) {
  const variants: any[] = [], files: any[] = [];
  for (const policies of input.variants) {
    const directory = join(output.directory, policies.id); await mkdir(directory, { mode: 0o700 });
    const path = join(directory, "session.sqlite");
    const faux = fauxProvider({ api: input.api, tokenSize: { min: 64, max: 64 } });
    const models = createModels(); models.setProvider(faux.provider);
    const registry = createRegistry(), started = deferred(), clock = fixtureClock(input.clock_epoch_ms);
    const invocations: any[] = [], requests: any[] = [], reports: any[] = [];
    let runs = 0, phase = "before-close", toolId: any;
    const execute = async (args: any, api: any, callContext: any) => {
      runs++; console.error("H2", policies.id, "execute", runs);
      toolId = api.taskId;
      invocations.push({ ordinal: runs, phase, taskId: api.taskId,
        conversationId: api.conversationId, callId: api.callId, args: structuredClone(args) });
      api.output(`run ${runs}\n`); await api.details({ run: runs }, callContext);
      if (runs === 1) { started.resolve(); await aborted(callContext.abortSignal); }
      return {};
    };
    const extension = (replay: "safe" | "unsafe") => defineExtension({ name: "fixture.work", tools: [defineTool({
      name: "work", description: "work", parameters: Type.Object({}), replay, execute,
    })] });
    const stored = extension(policies.stored); registry.install(stored);
    const responses = [fauxAssistantMessage([fauxToolCall("work", {}, { id: "c1" })],
      { stopReason: "toolUse", timestamp: input.clock_epoch_ms }),
      fauxAssistantMessage("done", { timestamp: input.clock_epoch_ms })];
    faux.setResponses(responses.map(response => (transcript, options) => {
      requests.push(structuredClone({ messages: transcript.messages, sessionId: options?.sessionId,
        cacheRetention: options?.cacheRetention })); return response;
    }));
    const open = async () => Harness.open(await openNodeSqliteStorage(path), { models, registry,
      now: clock.now, setTimer: clock.setTimer, onReport: error => reports.push(errorValue(error)) }, context);
    let harness: any = await open(), root: any, observed: any, closed: any;
    const snapshot = async () => {
      const entries = await root.entries({}, 1000, undefined, context); assert.equal(entries.next, undefined);
      const tasks = await harness.commit(tx => tx.scanTasks({ conversationId: root.id }, 1000), context);
      assert.equal(tasks.next, undefined);
      return { task: await harness.getTask(toolId, context), tasks: tasks.items, history: [...entries.items].reverse(),
        provider: await harness.snapshot(ProviderDoc, root.id, context),
        live: await harness.snapshot(LiveDoc, root.id, context), runs, callCount: faux.state.callCount };
    };
    try {
      root = await harness.root(context, { agent: { model: { provider: "faux", modelId: "faux-1" } } });
      const admission = await root.submit({ type: "input", content: "go" }, context);
      const admitted = await admission.status(context);
      console.error("H2", policies.id, "submitted"); await started.promise; console.error("H2", policies.id, "started");
      const beforeClose = await snapshot(); console.error("H2", policies.id, "snapshot");
      await harness.close(context); console.error("H2", policies.id, "closed"); const firstClose = await harness.closed;
      assert.equal((await stat(path)).isFile(), true);
      const header = (await readFile(path)).subarray(0, 16).toString("utf8");
      assert.equal(header, "SQLite format 3\0");
      registry.uninstall(stored); registry.install(extension(policies.current));
      harness = await open(); console.error("H2", policies.id, "reopened"); root = await harness.root(context);
      const reopened = await snapshot(); // no resume before this observation
      phase = "after-reopen"; harness.resume();
      const pending = await harness.submission(admitted.id, context); assert.ok(pending);
      console.error("H2", policies.id, "resumed"); const submission = await pending.wait(context); await root.waitForIdle(context);
      const entries = await root.entries({}, 1000, undefined, context); assert.equal(entries.next, undefined);
      const tasks = await harness.commit(tx => tx.scanTasks({ conversationId: root.id }, 1000), context);
      assert.equal(tasks.next, undefined);
      observed = { id: policies.id, stored: policies.stored, current: policies.current,
        database: { name: `${policies.id}/session.sqlite`, header }, admitted, beforeClose, reopened, firstClose,
        submission, history: [...entries.items].reverse(), tasks: tasks.items,
        clock: clock.facts, invocations, requests, provider: await harness.snapshot(ProviderDoc, root.id, context),
        usage: await harness.snapshot(UsageDoc, root.id, context), sessionUsage: await harness.usage(context),
        live: await harness.snapshot(LiveDoc, root.id, context), reports, faux: structuredClone(faux.state), runs };
      assert.equal(submission.status, "done"); assert.equal(runs, policies.stored === "safe" && policies.current === "safe" ? 2 : 1);
      assert.deepEqual(observed.live, {});
    } finally { await harness.close(context); closed = await harness.closed; }
    variants.push({ ...observed, close: closed });
    files.push({ id: policies.id, path, bytes: (await stat(path)).size,
      sha256: createHash("sha256").update(await readFile(path)).digest("hex"), closed: true });
  }
  await writeFile(join(output.directory, "database-evidence.json"), JSON.stringify(files, null, 2)+"\n");
  return { variants };
}
