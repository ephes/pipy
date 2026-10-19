/** Verification-only bounded workload adapter, Pi MIT (c) 2025 Mario Zechner.
 * Adapts packages/durable/{src/testing/storage-benchmark.ts,test/storage.bench.ts,
 * test/storage-memory.ts} at 2ec55d808d455b1a64a2bf7888198207a096120a.
 * Full notice: tests/fixtures/durable/UPSTREAM-LICENSE.txt. */
import assert from "node:assert/strict";
import { cpus, totalmem, platform, release, arch } from "node:os";
import { performance } from "node:perf_hooks";
import { BACKGROUND_CONTEXT as context } from "@earendil-works/chord/context";
import { MemoryStorage } from "../../packages/durable/src/storage/memory.ts";
import { ROOT_CONVERSATION_ID as root } from "../../packages/durable/src/types.ts";
import { TIMING_SCALE, seedStorageBenchmark, storageBenchmarkPrimaryRecordCount,
  STORAGE_READ_BENCHMARKS as reads, seedStorageWriteBenchmark,
  STORAGE_WRITE_BENCHMARKS as writes } from "../../packages/durable/src/testing/storage-benchmark.ts";

const readNames = ["exact entry lookup", "entry page scan (100)", "filtered task scan (50)",
  "exact document address among many", ...[0,16,128,1024].map(n => `document replay tail (${n})`),
  "ancient historical read before newer base", "recent historical read after newer base",
  "fork-depth history scan (100)", "fork-depth head lookup"];
const writeNames = ["commit one entry", "commit 100 entries", "commit mixed entry/task/submission/document"];

async function sample(scenario: typeof writes[number], id: number) {
  const storage = new MemoryStorage();
  const seedStart = performance.now();
  try {
    await seedStorageWriteBenchmark(storage);
    const seedEnd = performance.now();
    const before = await storage.scanEntries({conversationId: root}, 1000, undefined, context);
    assert.equal(before.items.length, 100); assert.equal(before.next, undefined);
    const commits: any[] = [];
    const commit = storage.commit.bind(storage);
    storage.commit = async (batch, ctx) => {
      const seq = await commit(batch, ctx);
      // Transparent delegate: no cloning/readback in the measured span. Its
      // one completed-commit append is explicitly part of this adapter's cost.
      commits.push({seq, writes: batch});
      return seq;
    };
    const start = performance.now();
    const actual = await scenario.run(storage);
    const end = performance.now();
    assert.equal(actual, scenario.expected);
    assert.equal(commits.length, 1);
    const [{seq, writes: committed}] = commits;
    const observed: any[] = [];
    for (const write of committed) {
      if (write.type === "entry") {
        const record = await storage.entry(write.value.id, context);
        assert.deepEqual(record?.entry, write.value); assert.equal(record?.commitSeq, seq);
        observed.push({type: write.type, value: record});
      } else if (write.type === "task") {
        const record = await storage.task(write.value.id, context);
        assert.deepEqual(record, write.value); observed.push({type: write.type, value: record});
      } else if (write.type === "submission") {
        const record = await storage.submission(write.value.id, context);
        assert.deepEqual(record, write.value);
        const byRequest = await storage.submissionByRequest(root, write.value.requestId, context);
        assert.deepEqual(byRequest, record); observed.push({type: write.type, value: record, byRequest});
      } else {
        assert.equal(write.type, "document.create");
        const record = await storage.findDocument(write.record, "current", context);
        const materialized = await storage.document(write.record.id, "current", context);
        assert.deepEqual(record, {...write.record, createdAt: seq});
        assert.deepEqual(materialized, {record, version: 1, value: write.content.value, deltasSinceBase: 0});
        observed.push({type: write.type, value: materialized, byAddress: record});
      }
    }
    const after = await storage.scanEntries({conversationId: root}, 1000, undefined, context);
    const count = committed.filter((w: any) => w.type === "entry").length;
    assert.equal(after.items.length, 100 + count); assert.equal(after.next, undefined);
    for (const original of before.items) assert.deepEqual(after.items.find(entry => entry.id === original.id), original);
    assert.equal(new Set(after.items.map(entry => entry.id)).size, after.items.length);
    assert.equal(committed.length, scenario.expected);
    if (scenario.expected === 4) {
      const [entry, task, submission, doc] = committed;
      assert.deepEqual(committed.map((w: any) => w.type), ["entry","task","submission","document.create"]);
      assert.equal(submission.value.entry, entry.value.id);
      assert.equal(submission.value.requestId, `benchmark-${submission.value.id}`);
      assert.equal(doc.record.key, String(doc.record.id));
      assert.deepEqual(doc.content.value, {entryId: entry.value.id, taskId: task.value.id});
    }
    const checked = performance.now();
    return {id, name: scenario.name, expected: scenario.expected, actual, seed: {start: seedStart, end: seedEnd},
      run: {start,end}, checked, before: before.items.length, after: after.items.length,
      commit: {seq, writes: committed}, readbacks: observed, closed: true};
  } finally { await storage.close(context); }
}

export async function run(input: any) {
  assert.deepEqual(input, {schema_version:1, backend:"memory", scale:"timing", read_rounds:10, write_samples:3});
  assert.deepEqual(reads.map(x => x.name), readNames); assert.deepEqual(writes.map(x => x.name), writeNames);
  const started = performance.now();
  const storage = new MemoryStorage();
  const seedStart = performance.now();
  let dataset: any, correctness: any[], measured: any[];
  const writeChecks: any[] = [], writeSamples: any[] = [];
  try {
    dataset = await seedStorageBenchmark(storage, TIMING_SCALE);
    const seedEnd = performance.now();
    const primary = storageBenchmarkPrimaryRecordCount(TIMING_SCALE);
    assert.equal(primary,1870);
    correctness = [];
    for (const scenario of reads) {
      const actual = await scenario.run(storage,dataset), expected = scenario.expected(dataset);
      assert.equal(actual,expected); correctness.push({name:scenario.name,actual,expected});
    }
    // Every untimed correctness/readback case completes before ANY measurement.
    let nextId = 0;
    for (const scenario of writes) writeChecks.push(await sample(scenario,++nextId));
    const correctnessEnd = performance.now();
    measured = [];
    for (let round=0; round<input.read_rounds; round++) {
      for (const scenario of reads) {
        const start = performance.now(), actual = await scenario.run(storage,dataset), end = performance.now();
        assert.equal(actual,scenario.expected(dataset)); measured.push({name:scenario.name,round,start,end,actual});
      }
    }
    for (const scenario of writes) {
      for (let index=0; index<input.write_samples; index++) writeSamples.push(await sample(scenario,++nextId));
    }
    return {schema_version:1, backend:"memory", scale:TIMING_SCALE, primary_records:primary, dataset,
      hardware:{platform:platform(),release:release(),arch:arch(),cpu_model:cpus()[0].model,
        cpu_count:cpus().length,total_ram_bytes:totalmem(),node:process.version},
      limits:{memory_retained:"uncollected",protocol:"bounded-local-v1",warmups:0,
        write_span:"exported run including mint/batch and one transparent commit-observer append; excludes seed/readback",
        performance_parity:false},
      spans:{started,seed:{start:seedStart,end:seedEnd},correctness_end:correctnessEnd,finished:performance.now()},
      reads:{correctness,samples:measured},writes:{correctness:writeChecks,samples:writeSamples},closed:true};
  } finally { await storage.close(context); }
}
