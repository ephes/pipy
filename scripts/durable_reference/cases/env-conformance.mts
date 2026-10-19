/** E1: executes two exported env-conformance.ts cases against NodeExecutionEnv.
 * Adapted Pi MIT Copyright (c) 2025 Mario Zechner; see UPSTREAM-LICENSE.txt. */
import assert from "node:assert/strict";
import { mkdir, readdir, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { BACKGROUND_CONTEXT as context } from "@earendil-works/chord/context";
import { NodeExecutionEnv } from "../../packages/durable/src/env/node.ts";
import { createEnvConformance } from "../../packages/durable/src/testing/env-conformance.ts";
import { errorValue } from "../contract.mts";
export async function run(input: any, output: { directory: string }) {
  const scenarios: any[] = [], chunkScenarios: any[] = [];
  const assertions = { ok: assert.ok, strictEqual: assert.strictEqual, deepEqual: assert.deepEqual,
    partialDeepEqual: assert.partialDeepStrictEqual,
    greaterThan: (actual: number, expected: number) => assert.ok(actual > expected),
    rejects: async (promise: Promise<unknown>, message: string) => assert.rejects(promise,
      error => error instanceof Error && error.message.includes(message)) };
  for (const selection of input.scenarios) {
    const chunks: any[] = [];
    const cwd = join(output.directory, selection.id); await mkdir(cwd, { mode: 0o700 });
    assert.deepEqual(await readdir(cwd), []);
    const operations: any[] = [], readers: any[] = [];
    let calls = 0, cleanup: any;
    const dto = (value: any): any => {
      if (value instanceof Error) return errorValue(value);
      if (value instanceof Uint8Array) return [...value];
      if (Array.isArray(value)) return value.map(dto);
      if (value && typeof value === "object") {
        assert.equal(Object.getPrototypeOf(value), Object.prototype, "Unhandled environment DTO");
        return Object.fromEntries(Object.entries(value).map(([key, child]) => [key, dto(child)]));
      }
      return value;
    };
    const cases = createEnvConformance({ assertions, shell: ["sh", "-c"], withEnv: async use => {
      calls++; assert.equal(calls, 1);
      const env = new NodeExecutionEnv({ cwd });
      const wrapped = new Proxy(env, { get(target, key) {
        const method = Reflect.get(target, key, target);
        if (typeof method !== "function") return method;
        return async (...args: any[]) => {
          const name = String(key);
          if (name === "openBinaryReader") {
            const result = await method.apply(target, args);
            if (!result.ok) { operations.push({ op: name, args: dto(args.slice(0, -1)), result: dto(result) }); return result; }
            const reader = result.value, handle = `reader:${readers.length + 1}`; readers.push(reader);
            operations.push({ op: name, args: dto(args.slice(0, -1)), result: { ok: true, value: { handle } } });
            return { ...result, value: new Proxy(reader, { get(r, prop) {
              const fn = Reflect.get(r, prop, r); if (typeof fn !== "function") return fn;
              return async (...a: any[]) => {
                const returned = await fn.apply(r, a);
                operations.push({ op: `reader.${String(prop)}`, handle, args: dto(a.slice(0, -1)),
                  aborted: a.at(-1)?.abortSignal?.aborted ?? false, result: dto(returned) });
                return returned;
              };
            } }) };
          }
          if (name === "exec") {
            const streams = { stdout: "", stderr: "" };
            const options = { ...args[1], onOutput: (text: string, ctx: any, info: any) => {
              assert.ok(info.stream === "stdout" || info.stream === "stderr");
              streams[info.stream as "stdout" | "stderr"] += text;
              chunks.push({ text, info: dto(info) });
              args[1]?.onOutput?.(text, ctx, info);
            } };
            const result = await method.call(target, args[0], options, args[2]);
            operations.push({ op: name, argv: dto(args[0]), result: dto(result), streams }); return result;
          }
          assert.ok(["writeFile", "exists"].includes(name), `Unexpected selected environment operation ${name}`);
          const result = await method.apply(target, args);
          operations.push({ op: name, args: dto(args.slice(0, -1)), result: dto(result) }); return result;
        };
      } });
      try { await use(wrapped); }
      finally {
        try { for (const reader of readers) await reader.close(context); }
        finally { await env.cleanup(context); cleanup = { status: "fulfilled" }; }
      }
    } });
    const selected = cases.filter(item => item.name === selection.name); assert.equal(selected.length, 1);
    await selected[0].run(); assert.equal(calls, 1);
    scenarios.push({ id: selection.id, name: selected[0].name, cwd, calls, operations, cleanup });
    chunkScenarios.push({ id: selection.id, chunks });
  }
  await writeFile(join(output.directory, "raw-chunks.json"), JSON.stringify({ schema_version: 1,
    scenarios: chunkScenarios }, null, 2)+"\n");
  return { scenarios };
}
