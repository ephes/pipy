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
const { track, applyImmutable } = await import("./packages/chord/src/delta/index.ts");
const { JsonRevisionValidator } = await import("./packages/chord/src/delta/revision-validator.ts");
function profile(value: any): any {
 if (value === null) return ["null"];
 if (typeof value === "boolean") return ["bool",value];
 if (typeof value === "number") { if (!Number.isFinite(value)) throw Error("nonfinite");const b=Buffer.alloc(8);b.writeDoubleBE(value);return ["number",b.toString("hex")]; }
 const units=(s: string) => { const b=Buffer.alloc(s.length*2); for(let i=0;i<s.length;i++) b.writeUInt16BE(s.charCodeAt(i),i*2);return b.toString("hex"); };
 if (typeof value === "string") return ["string",units(value)];
 if (Array.isArray(value)) return ["array",value.map(profile)];
 return ["object",Object.entries(value).map(([k,v]) => [units(k),profile(v)])];
}
const input=JSON.parse(readFileSync(process.argv[2]!,"utf8"));
const raw: any[] = [];
const observed=input.map((recipe: any) => {
 const validator=new JsonRevisionValidator();validator.validate(recipe.base);
 const tracker=track(recipe.base), change=tracker.beginChange(), root=change.state;
 const held = new Map();
 const get=(path: any[],start: any=root)=>path.reduce((v,k)=>v[k],start);
 const set=(path: any[],value: any,start: any=root)=>{get(path.slice(0,-1),start)[path.at(-1)]=value;};
 for(const [op,path,...args] of recipe.actions){
  if(op==='set')set(path,args[0]);
  else if(op==='del')delete get(path.slice(0,-1))[path.at(-1)];
  else if(op==='splice')get(path).splice(...args.slice(0,2),...args[2]);
  else if(op==='reverse')get(path).reverse();
  else if(op==='hold')held.set(path,get(args[0]));
  else if(op==='hset')set(args[0],args[1],held.get(path));
  else if(op==='hsplice')get(args[0],held.get(path)).splice(...args.slice(1,3),...args[3]);
  else if(op==='place')set(path,get(args[0]));
  else if(op==='pop_reinsert'){const a=get(path),v=a.splice(args[0],1)[0];a.splice(args[0],0,v);}
  else if(op==='clear'){const a=get(path);if(Array.isArray(a))a.length=0;else for(const k of Object.keys(a))delete a[k];}
  else throw Error(op);
 }
 const prepared=change.prepare();
 const replay=applyImmutable(recipe.base,prepared.ops);
 let revoked=false;try{Object.keys(root)}catch{revoked=true;}
 raw.push({name:recipe.name,ops:prepared.ops,prepared:prepared.value,replay,revoked});
 // Profiles are captured before JSON.stringify erases negative zero.
 return {name:recipe.name,ops:profile(prepared.ops),prepared:profile(prepared.value),replay:profile(replay),revoked};
});
process.stdout.write(JSON.stringify({observed,raw,loaded}));
