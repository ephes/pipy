/** Internal DE03d2 actual pinned Memory/SQLite/reopen current-base comparison.
 * SQLite-reopen is a driver-local reconstruction of pinned test ReopeningStorage:
 * call-time delegation; finally reopen after EVERY commit, including helpers/errors.
 * The pinned test helper itself is not imported or executed here.
 * Pi-derived MIT (c)2025 Mario Zechner; see tests/fixtures/durable/UPSTREAM-LICENSE.txt. */
import assert from "node:assert/strict";
import {readFileSync,writeFileSync,realpathSync,mkdirSync} from "node:fs";
import {registerHooks} from "node:module";
import {fileURLToPath} from "node:url";
import {resolve,sep,join} from "node:path";
const root=realpathSync(process.cwd()),[inputPath,outputPath]=process.argv.slice(2);
assert.ok(inputPath&&outputPath);
const resolutions:any[]=[];
registerHooks({resolve(specifier,context,nextResolve){const result=nextResolve(specifier,context);if(result.url.startsWith("node:"))return result;assert.ok(result.url.startsWith("file:"));const path=realpathSync(fileURLToPath(result.url));assert.ok(path.startsWith(root+sep));resolutions.push({specifier,path:path.slice(root.length+1)});return result;}});
const {MemoryStorage}=await import("./packages/durable/src/storage/memory.ts");
const {openNodeSqliteStorage}=await import("./packages/durable/src/storage/sqlite/node.ts");
const {createStorageConformance}=await import("./packages/durable/src/testing/storage-conformance.ts");
const {BACKGROUND_CONTEXT:context}=await import("@earendil-works/chord/context");
function string(hex:string){let s="";const b=Buffer.from(hex,"hex");for(let i=0;i<b.length;i+=2)s+=String.fromCharCode(b.readUInt16BE(i));return s;}
function hex(s:string){const b=Buffer.alloc(s.length*2);for(let i=0;i<s.length;i++)b.writeUInt16BE(s.charCodeAt(i),i*2);return b.toString("hex");}
function decode(v:any):any{switch(v[0]){case "null":return null;case "bool":return v[1];case "string":return string(v[1]);case "number":return Buffer.from(v[1],"hex").readDoubleBE();case "array":return v[1].map(decode);case "object":return Object.fromEntries(v[1].map(([k,x]:any)=>[string(k),decode(x)]));default:throw Error("Invalid transport");}}
function encode(v:any):any{if(v===undefined)return ["undefined"];if(v===null)return ["null"];if(typeof v==="boolean")return ["bool",v];if(typeof v==="string")return ["string",hex(v)];if(typeof v==="number"){assert.ok(Number.isFinite(v));const b=Buffer.alloc(8);b.writeDoubleBE(v);return ["number",b.toString("hex")];}if(Array.isArray(v))return ["array",v.map(encode)];assert.ok(Object.getPrototypeOf(v)===Object.prototype||Object.getPrototypeOf(v)===null);return ["object",Object.keys(v).map(k=>[hex(k),encode(v[k])]).sort((a,b)=>a[0]<b[0]?-1:a[0]>b[0]?1:0)];}
let active:any={}; const rawErrors:any[]=[];
function error(e:any){rawErrors.push({...active,name:e.name,message:e.message,code:e.code??null,stack:e.stack??null,...(Object.hasOwn(e,"cause")?{cause:encode(e.cause)}:{})});return {name:e.name,message:e.message,code:e.code??null,...(Object.hasOwn(e,"cause")?{cause:encode(e.cause)}:{})};}
const input=JSON.parse(readFileSync(inputPath,"utf8"));assert.equal(input.schema_version,1);assert.equal(input.phase,"storage-sqlite-d2-v1");

let serial=0;
async function create(backend:string,label:string){
 const directory=join(outputPath,`store-${serial++}`);mkdirSync(directory,{mode:0o700});
 const path=join(directory,'store.sqlite');let current:any=backend==='memory'?new MemoryStorage():await openNodeSqliteStorage(path);let closed=false;
 return new Proxy({}, {get(_t,key){
  if(key==='close')return async()=>{closed=true;return current.close(context);};
  if(typeof current[key]!=='function')return current[key];
  return async(...args:any[])=>{if(key!=='commit'||backend!=='sqlite-reopen'||closed)return current[key](...args);
   try{return await current.commit(...args);}finally{await current.close(context);current=await openNodeSqliteStorage(path);}
  };
 }});
}
const rawCalls:any[]=[],backends:any[]=[],contexts:any[]=[];
async function ownership(storage:any,args:any){const shared={items:[null,-0.0,2**60,"\ud800"]};const value:any={id:args.id,conversationId:1,kind:"",data:{left:shared,right:shared}};const writes=[{type:"entry",value}];const before=encode(writes);const seq=await storage.commit(writes,context);shared.items.push("caller" as never);const first=(await storage.entry(args.id,context))!.entry as any;const source_detached=first.data.left.items.length===4;first.data.left.items.push("read");const placements_independent=first.data.right.items.length===4;const again=(await storage.entry(args.id,context))!.entry;return {calls:[{op:"commit",args:before,result:encode(seq)},{op:"entry",id:args.id,mutated:encode(first)},{op:"entry",id:args.id,result:encode(again)}],source_detached,placements_independent,read_detached:(again as any).data.left.items.length===4};}
async function documentOwnership(storage:any,args:any){const shared={items:[null,-0.0,2**60,"\ud800"]};const writes=[{type:"document.create",record:{id:args.id,kind:"owned",scope:{kind:"session"}},content:{kind:"base",version:1,value:{left:shared,right:shared}}}];const before=encode(writes);const seq=await storage.commit(writes,context);shared.items.push("caller" as never);const first=await storage.document(args.id,"current",context);const source_detached=first.value.left.items.length===4;first.value.left.items.push("read");const placements_independent=first.value.right.items.length===4;const again=await storage.document(args.id,"current",context);return {calls:[{op:"commit",args:before,result:encode(seq)},{op:"document",id:args.id,mutated:encode(first)},{op:"document",id:args.id,result:encode(again)}],source_detached,placements_independent,read_detached:again.value.left.items.length===4};}
async function invoke(storage:any,op:string,a:any):Promise<any>{switch(op){case "mint":return storage.mintId();case "commit":return storage.commit(a.writes,context);case "conversation":return storage.conversation(a.id,context);case "task":return storage.task(a.id,context);case "submission":return storage.submission(a.id,context);case "request":return storage.submissionByRequest(a.conversation_id,a.request_id,context);case "entry":return a.conversation_id===undefined?storage.entry(a.id,context):storage.entry(a.conversation_id,a.id,context);case "head":return storage.findLatestHeadMarker(a.conversation_id,a.bound,context);case "scan_conversations":return storage.scanConversations(a.query,a.limit,a.cursor,context);case "scan_entries":return storage.scanEntries(a.query,a.limit,a.cursor,context);case "scan_tasks":return storage.scanTasks(a.query,a.limit,a.cursor,context);case "scan_submissions":return storage.scanSubmissions(a.query,a.limit,a.cursor,context);case "ownership":return ownership(storage,a);case "document_ownership":return documentOwnership(storage,a);case "document":return storage.document(a.id,a.at??"current",context);case "find_document":return storage.findDocument(a.address,a.at??"current",context);case "scan_documents":return storage.scanDocuments(a.query,a.limit,a.cursor,context);case "close":return storage.close(context);default:throw Error("Unknown recipe operation");}}

for(const backend of ['memory','sqlite','sqlite-reopen']){
 const conformance:any[]=[];

const assertions={ok:assert.ok,strictEqual:assert.strictEqual,deepEqual:assert.deepEqual,partialDeepEqual:assert.partialDeepStrictEqual,greaterThan:(a:number,b:number)=>assert.ok(a>b),rejects:async(p:Promise<unknown>,message:string)=>assert.rejects(p,e=>e instanceof Error&&e.message.includes(message))};
for(const name of input.exported_cases){active={backend,scenario:"conformance",operation:name};let calls=0,closed=false;
 const cases=createStorageConformance({assertions,withStorage:async use=>{assert.equal(++calls,1);const storage=await create(backend,"conformance-"+name);const wrapped=new Proxy(storage,{get(target,key){const fn=Reflect.get(target,key,target);if(typeof fn!=="function")return fn;return async(...args:any[])=>{const record:any={backend,case:name,op:String(key),args:encode(key==="mintId"?args:args.slice(0,-1))};try{const value=await fn.apply(target,args);record.outcome={status:"fulfilled",value:encode(value)};return value;}catch(e){record.outcome={status:"rejected",error:error(e)};throw e;}finally{rawCalls.push(record);}};}});try{await use(wrapped);}finally{await wrapped.close(context);closed=true;}}});
 const selected=cases.filter(x=>x.name===name);assert.equal(selected.length,1);await selected[0].run();assert.equal(calls,1);assert.equal(closed,true);conformance.push({name,callback_calls:calls,closed,operations:rawCalls.filter(x=>x.backend===backend&&x.case===name).length});
}

const scenarios:any[]=[];
for(const scenario of input.scenarios){const storage=await create(backend,scenario.id),cache=new Map<string,any>(),operations:any[]=[];try{for(const step of scenario.operations){active={backend,scenario:scenario.id,operation:step.id};const a=decode(step.args);delete a.python_pair_fields;if(a.cursor_ref!==undefined){assert.ok(cache.has(a.cursor_ref));a.cursor=cache.get(a.cursor_ref).next;delete a.cursor_ref;}const record:any={id:step.id,op:step.op,args:encode(a)};try{const result=await invoke(storage,step.op,a);cache.set(step.id,result);record.outcome={status:"fulfilled",value:encode(result)};}catch(e){record.outcome={status:"rejected",error:error(e)};}operations.push(record);}}finally{await storage.close(context);}scenarios.push({id:scenario.id,operations});}

 backends.push({backend,scenarios});contexts.push({backend,exported_cases:conformance});
}
const save=(name:string,value:any)=>writeFileSync(resolve(outputPath,name),JSON.stringify(value,null,2)+"\n");
save('observed.json',{schema_version:1,phase:input.phase,backends});
save('reference-context.json',{schema_version:1,phase:input.phase,backends:contexts});
save('conformance-raw.json',{schema_version:1,calls:rawCalls,errors:rawErrors});save('resolutions.json',resolutions);
