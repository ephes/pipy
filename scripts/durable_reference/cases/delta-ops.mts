// Verification-only adaptation of Pi MIT (c) 2025 Mario Zechner.
// Full notice: tests/fixtures/durable/UPSTREAM-LICENSE.txt.
import { readFileSync } from "node:fs";
import { registerHooks } from "node:module";
const modules = ["json.ts", "delta/index.ts", "delta/diff.ts", "delta/tracker.ts", "delta/apply-immutable-trusted.ts", "delta/revision-validator.ts"];
const allowed = new Set(modules.map(p => new URL("./packages/chord/src/" + p, import.meta.url).href));
const loaded: string[] = [];
registerHooks({load(url, context, nextLoad) {
 if (!allowed.has(url)) throw new Error("Unexpected delta dependency: " + url);
 loaded.push(url); return nextLoad(url, context);
}});
const { applyImmutable } = await import("./packages/chord/src/delta/index.ts");
const { JsonRevisionValidator } = await import("./packages/chord/src/delta/revision-validator.ts");
function profile(value: any): any {
 if (value === null) return ["null"];
 if (typeof value === "boolean") return ["bool",value];
 if (typeof value === "number") { if (!Number.isFinite(value)) throw Error("nonfinite");const b=Buffer.alloc(8);b.writeDoubleBE(value);return ["number",b.toString("hex")]; }
 const units=(s: string) => { const b=Buffer.alloc(s.length*2); for(let i=0;i<s.length;i++) b.writeUInt16BE(s.charCodeAt(i),i*2);return b.toString("hex"); };
 if (typeof value === "string") return ["string",units(value)];
 if (Array.isArray(value)) return ["array",value.map(profile)];
 return ["object",Object.entries(value).map(([k,v]) => [units(k),profile(v)]).sort((a,b) => a[0]<b[0]?-1:a[0]>b[0]?1:0)];
}
const input=JSON.parse(readFileSync(process.argv[2]!,"utf8"));
const observed=input.map((recipe: any) => {
 const validator=new JsonRevisionValidator();validator.validate(recipe.value);validator.validate(recipe.ops);
 const ops=profile(recipe.ops);
 try { return {ops,result:profile(validator.validate(applyImmutable(recipe.value,recipe.ops)))}; }
 catch(error: any) { if (!["TypeError","PathError","UnsafePathError"].includes(error.name)) throw error;return {ops,error:error.name}; }
});
process.stdout.write(JSON.stringify({observed,loaded}));
