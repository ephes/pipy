// Verification-only adaptation of Pi MIT (c) 2025 Mario Zechner.
// Full notice: tests/fixtures/durable/UPSTREAM-LICENSE.txt.
import assert from "node:assert/strict";
import { readFileSync, mkdirSync } from "node:fs";
import { registerHooks } from "node:module";
const input = JSON.parse(readFileSync(process.argv[2]!, "utf8"));
const allowed = new Set(input.modules.map((p: string) => new URL(p, import.meta.url).href));
const aliases: Record<string, string> = {
 "@earendil-works/chord/json": "packages/chord/src/json.ts",
 "@earendil-works/chord/delta": "packages/chord/src/delta/index.ts",
 "@earendil-works/chord/context": "packages/chord/src/context/index.ts",
 "@earendil-works/chord/sqlite": "packages/chord/src/sqlite/index.ts",
 "@earendil-works/chord/sqlite/node": "packages/chord/src/sqlite/node.ts",
 "@earendil-works/pi-telemetry": "packages/telemetry/src/index.ts",
};
const loaded: string[] = [];
registerHooks({
 resolve(specifier, context, next) {
  if (Object.hasOwn(aliases, specifier)) return {url: new URL(aliases[specifier], import.meta.url).href, shortCircuit: true};
  return next(specifier, context);
 },
 load(url, context, next) {
  if (!url.startsWith("node:")) {
   assert.ok(allowed.has(url), "Unexpected current-delta dependency");
   loaded.push(url);
  }
  return next(url, context);
 }
});
const {MemoryStorage} = await import("./packages/durable/src/storage/memory.ts");
const {openNodeSqliteStorage} = await import("./packages/durable/src/storage/sqlite/node.ts");
const {BACKGROUND_CONTEXT: context} = await import("./packages/chord/src/context/index.ts");
function profile(value: any): any {
 if (value === null) return ["null"];
 if (typeof value === "boolean") return ["bool",value];
 if (typeof value === "number") {assert.ok(Number.isFinite(value));const b=Buffer.alloc(8);b.writeDoubleBE(value);return ["number",b.toString("hex")];}
 const units=(s: string) => {const b=Buffer.alloc(s.length*2);for(let i=0;i<s.length;i++)b.writeUInt16BE(s.charCodeAt(i),i*2);return b.toString("hex");};
 if (typeof value === "string") return ["string",units(value)];
 if (Array.isArray(value)) return ["array",value.map(profile)];
 return ["object",Object.entries(value).map(([k,v]) => [units(k),profile(v)]).sort((a,b)=>a[0]<b[0]?-1:a[0]>b[0]?1:0)];
}
const observed: any[] = [];
let serial = 0;
for (const backend of ["memory", "sqlite", "reopen"]) {
 for (const recipe of input.cases) {
  const folder = process.argv[3]! + "/node-" + serial++;
  mkdirSync(folder, {mode:0o700});
  const path=folder+"/store.sqlite";
  let store:any=backend==="memory"?new MemoryStorage():await openNodeSqliteStorage(path);
  const steps:any[]=[];
  try {
   for (const writes of recipe.writes) {
    try {
     let seq;
     try {seq=await store.commit(writes,context);}
     finally {if(backend==="reopen"){await store.close(context);store=await openNodeSqliteStorage(path);}}
     const document=await store.document(2,"current",context);
     steps.push({seq,document:profile(document)});
    } catch(error:any) {steps.push({error:{name:error.name,code:error.code??null}});break;}
   }
  } finally {await store.close(context);}
  observed.push({backend,id:recipe.id,steps});
 }
}
process.stdout.write(JSON.stringify({observed,loaded}));
