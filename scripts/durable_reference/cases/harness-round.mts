/** H1: adapted from harness-tools.test.ts tool round + explicit-null-details.
 * Pi MIT Copyright (c) 2025 Mario Zechner; see UPSTREAM-LICENSE.txt. */
import assert from "node:assert/strict";
import { BACKGROUND_CONTEXT as context } from "@earendil-works/chord/context";
import { createModels, fauxProvider, fauxAssistantMessage, fauxToolCall, Type } from "@earendil-works/pi-ai";
import { Harness } from "../../packages/durable/src/harness/harness.ts";
import { createRegistry } from "../../packages/durable/src/harness/registry.ts";
import { defineExtension, defineTool } from "../../packages/durable/src/harness/define.ts";
import { LiveDoc } from "../../packages/durable/src/harness/live.ts";
import { ProviderDoc } from "../../packages/durable/src/harness/provider.ts";
import { UsageDoc } from "../../packages/durable/src/harness/usage.ts";
import { MemoryStorage } from "../../packages/durable/src/storage/memory.ts";
export async function run(input: any) {
  const faux = fauxProvider({ api: input.api, tokenSize: { min: 64, max: 64 } });
  const models = createModels(); models.setProvider(faux.provider);
  const registry = createRegistry();
  const invocations: unknown[] = [], requests: unknown[] = [], reports: unknown[] = [];
  registry.install(defineExtension({ name: "fixture.echo", tools: [defineTool({
    name: input.tool_name, description: input.tool_description,
    parameters: Type.Object({ text: Type.Optional(Type.String()) }),
    execute: async (args, api, callContext) => {
      invocations.push(structuredClone(args));
      await api.details(input.progress_details, callContext);
      return structuredClone(input.tool_result);
    },
  })] }));
  const responses = [fauxAssistantMessage([
    fauxToolCall(input.tool_name, input.tool_arguments, { id: input.tool_call_id })
  ], { stopReason: "toolUse", timestamp: input.clock_epoch_ms }),
    fauxAssistantMessage(input.answer, { timestamp: input.clock_epoch_ms })];
  faux.setResponses(responses.map(response => (transcript, options) => {
    requests.push(structuredClone({ messages: transcript.messages,
      sessionId: options?.sessionId, cacheRetention: options?.cacheRetention }));
    return response;
  }));
  const harness = await Harness.open(new MemoryStorage(), { models, registry,
    now: () => input.clock_epoch_ms, onReport: error => {
      reports.push(error instanceof Error ? { name: error.name, message: error.message,
        ...(Object.hasOwn(error, "code") ? { code: (error as any).code } : {}) } : error);
    } }, context);
  let observed: any;
  try {
    const root = await harness.root(context, { agent: { model: { provider: "faux", modelId: "faux-1" } } });
    const submission = await (await root.submit({ type: "input", content: input.input }, context)).wait(context);
    await root.waitForIdle(context);
    const page = await root.entries({}, 1000, undefined, context);
    assert.equal(page.next, undefined, "History page incomplete");
    const history = [...page.items].reverse();
    const tasks = await harness.commit(tx => tx.scanTasks({ conversationId: root.id }, 1000), context);
    assert.equal(tasks.next, undefined, "Task page incomplete");
    const provider = await harness.snapshot(ProviderDoc, root.id, context);
    const usage = await harness.snapshot(UsageDoc, root.id, context);
    const sessionUsage = await harness.usage(context);
    const live = await harness.snapshot(LiveDoc, root.id, context);
    assert.equal(submission.status, "done");
    assert.deepEqual(history.map(entry => entry.kind), ["pi.user", "pi.system", "pi.assistant", "pi.tool-result", "pi.assistant"]);
    assert.equal(faux.state.callCount, 2); assert.deepEqual(live, {});
    const result = history[3].model![0] as any;
    assert.equal(result.toolCallId, input.tool_call_id); assert.equal(result.toolName, input.tool_name);
    assert.equal(result.isError, false); assert.ok(Object.hasOwn(result, "details"));
    assert.equal(result.details, null); assert.deepEqual(result.content, input.tool_result.output);
    assert.deepEqual(history[3].data, { diagnostics: [] }); assert.ok(history[3].byTaskId);
    const system = history[1].model![0] as any;
    assert.deepEqual(system.toolsAdded, [{ name: input.tool_name, description: input.tool_description,
      parameters: Type.Object({ text: Type.Optional(Type.String()) }) }]);
    assert.deepEqual(invocations, [input.tool_arguments]);
    observed = { submission, history, tasks: tasks.items, invocations, requests, provider,
      usage, sessionUsage, live, reports, faux: structuredClone(faux.state) };
  } finally { await harness.close(context); }
  return { ...observed, close: await harness.closed };
}
