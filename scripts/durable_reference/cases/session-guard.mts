/** Internal DE02b1b guard oracle: actual pinned SessionImpl GuardedStorage through a bounded driver subclass.
 * Guard/lifecycle only (no Session line, Tx, Harness or close listeners). Close routes only through
 * SessionImpl.close: Pi's GuardedStorage.close is not close-once, so this driver never calls it.
 * Pi-derived MIT (c)2025 Mario Zechner; see tests/fixtures/durable/UPSTREAM-LICENSE.txt. */
import assert from "node:assert/strict";
import {readFileSync,writeFileSync,realpathSync} from "node:fs";
import {registerHooks} from "node:module";
import {fileURLToPath} from "node:url";
import {resolve,sep} from "node:path";
const root=realpathSync(process.cwd()),[inputPath,outputPath]=process.argv.slice(2);
assert.ok(inputPath&&outputPath);
const resolutions:any[]=[];
registerHooks({resolve(specifier,context,nextResolve){const result=nextResolve(specifier,context);if(result.url.startsWith("node:"))return result;assert.ok(result.url.startsWith("file:"));const path=realpathSync(fileURLToPath(result.url));assert.ok(path.startsWith(root+sep));resolutions.push({specifier,path:path.slice(root.length+1)});return result;}});
const {SessionImpl}=await import("./packages/durable/src/session/session.ts");
const {SessionFailed,StorageRequestError}=await import("./packages/durable/src/errors.ts");
const {MemoryStorage}=await import("./packages/durable/src/storage/memory.ts");
const {BACKGROUND_CONTEXT,withCancel}=await import("@earendil-works/chord/context");
function string(hex:string){let s="";const b=Buffer.from(hex,"hex");for(let i=0;i<b.length;i+=2)s+=String.fromCharCode(b.readUInt16BE(i));return s;}
function hex(s:string){const b=Buffer.alloc(s.length*2);for(let i=0;i<s.length;i++)b.writeUInt16BE(s.charCodeAt(i),i*2);return b.toString("hex");}
function decode(v:any):any{switch(v[0]){case "null":return null;case "bool":return v[1];case "string":return string(v[1]);case "number":return Buffer.from(v[1],"hex").readDoubleBE();case "array":return v[1].map(decode);case "object":return Object.fromEntries(v[1].map(([k,x]:any)=>[string(k),decode(x)]));default:throw Error("Invalid transport");}}
function encode(v:any):any{if(v===undefined)return ["undefined"];if(v===null)return ["null"];if(typeof v==="boolean")return ["bool",v];if(typeof v==="string")return ["string",hex(v)];if(typeof v==="number"){assert.ok(Number.isFinite(v));const b=Buffer.alloc(8);b.writeDoubleBE(v);return ["number",b.toString("hex")];}if(Array.isArray(v))return ["array",v.map(encode)];assert.ok(Object.getPrototypeOf(v)===Object.prototype||Object.getPrototypeOf(v)===null);return ["object",Object.keys(v).map(k=>[hex(k),encode(v[k])]).sort((a,b)=>a[0]<b[0]?-1:a[0]>b[0]?1:0)];}
const input=JSON.parse(readFileSync(inputPath,"utf8"));assert.equal(input.schema_version,1);assert.equal(input.phase,"session-guard-b1b-v1");
const WAIT_MS=10000;
function deferred(){let resolve:any;const promise=new Promise(r=>{resolve=r;});return {promise,resolve};}
function deadline<T>(p:Promise<T>,label:string):Promise<T>{let timer:any;return Promise.race([p,new Promise<never>((_,reject)=>{timer=setTimeout(()=>reject(new Error("Driver deadline: "+label)),WAIT_MS);})]).finally(()=>clearTimeout(timer));}
const drain=()=>new Promise(r=>setImmediate(r));
// Guarded calls start synchronously inside GuardedStorage.#call, so the backend reads the caller handle here.
let ambient:string|null=null;
let errors=new Map<string,unknown>();
const raw:any[]=[];const driverErrors:unknown[]=[];
function ref(e:any,signal?:AbortSignal,depth=0):any{
 for(const [id,obj] of errors)if(e===obj)return {kind:"scripted",id};
 if(depth<4&&e instanceof SessionFailed&&e.name==="SessionFailed")return {kind:"session-failed",message:e.message,cause:ref((e as any).cause,undefined,depth+1)};
 if(signal!==undefined&&signal.aborted&&e===signal.reason)return {kind:"wait-cancelled"};
 if(e instanceof Error&&Object.getPrototypeOf(e)===Error.prototype&&e.message==="Session is closed")return {kind:"session-closed",message:e.message};
 if(e instanceof StorageRequestError&&e.name==="StorageRequestError")return {kind:"request",message:e.message};
 return {kind:"unclassified",name:String(e?.name),message:String(e?.message)};
}
function rawError(scenario:string,where:string,e:any){raw.push({scenario,where,name:String(e?.name),message:String(e?.message),stack:typeof e?.stack==="string"?e.stack:null,constructor:String(e?.constructor?.name),...(e!==null&&typeof e==="object"&&Object.hasOwn(e,"cause")?{cause:{name:String(e.cause?.name),message:String(e.cause?.message),constructor:String(e.cause?.constructor?.name)}}:{})});}
/** Bounded subclass: only the protected guarded Storage is exposed and report is recorded. */
class GuardDriver extends SessionImpl {
 onReport:((error:unknown,info:any)=>void)|undefined;
 get guarded():any{return this.storage;}
 report(error:unknown,info?:any,_context?:unknown):void{try{this.onReport?.(error,info);}catch(e){driverErrors.push(e);}}
}
function scanArgs(query:any,limit:any,cursor:any){return {query,limit,...(cursor===undefined?{}:{cursor})};}
/** Storage over a fresh actual pinned MemoryStorage with scripted per-handle holds/outcomes and an ordered effect log. */
class ControlledBackend {
 memory=new MemoryStorage();effects:any[]=[];directives=new Map<string,any>();closeDirective:any;cancels:Map<string,()=>void>;
 constructor(cancels:Map<string,()=>void>){this.cancels=cancels;}
 call(op:string,args:any,fn:()=>Promise<any>):Promise<any>{
  const h=ambient,d=h===null?undefined:this.directives.get(h);
  this.effects.push({e:"call",h,op,args:encode(args)});
  const rejected=(error:unknown)=>this.effects.push({e:"return",h,status:"rejected",error:ref(error)});
  if(d?.kind==="sync_throw"){rejected(d.error);throw d.error;}
  return (async()=>{try{
   if(d?.hold){d.entered.resolve();await d.release.promise;}
   if(d?.kind==="throw")throw d.error;
   if(d?.kind==="abort_then_throw"){this.cancels.get(h!)!();throw d.error;}
   const value=await fn();this.effects.push({e:"return",h,status:"fulfilled"});return value;
  }catch(error){rejected(error);throw error;}})();
 }
 commit(writes:any,ctx:any){return this.call("commit",{writes},()=>this.memory.commit(writes,ctx));}
 mintId(){return this.call("mint",{},()=>this.memory.mintId());}
 conversation(id:any,ctx:any){return this.call("conversation",{id},()=>this.memory.conversation(id,ctx));}
 scanConversations(q:any,l:any,c:any,ctx:any){return this.call("scan_conversations",scanArgs(q,l,c),()=>this.memory.scanConversations(q,l,c,ctx));}
 entry(a:any,b:any,c?:any){return c===undefined?this.call("entry",{id:a},()=>this.memory.entry(a,b)):this.call("entry",{id:b,conversation_id:a},()=>this.memory.entry(a,b,c));}
 findLatestHeadMarker(conversation:any,bound:any,ctx:any){return this.call("head",{conversation_id:conversation,...(bound===undefined?{}:{bound})},()=>this.memory.findLatestHeadMarker(conversation,bound,ctx));}
 scanEntries(q:any,l:any,c:any,ctx:any){return this.call("scan_entries",scanArgs(q,l,c),()=>this.memory.scanEntries(q,l,c,ctx));}
 task(id:any,ctx:any){return this.call("task",{id},()=>this.memory.task(id,ctx));}
 scanTasks(q:any,l:any,c:any,ctx:any){return this.call("scan_tasks",scanArgs(q,l,c),()=>this.memory.scanTasks(q,l,c,ctx));}
 submission(id:any,ctx:any){return this.call("submission",{id},()=>this.memory.submission(id,ctx));}
 scanSubmissions(q:any,l:any,c:any,ctx:any){return this.call("scan_submissions",scanArgs(q,l,c),()=>this.memory.scanSubmissions(q,l,c,ctx));}
 submissionByRequest(conversation:any,request:any,ctx:any){return this.call("request",{conversation_id:conversation,request_id:request},()=>this.memory.submissionByRequest(conversation,request,ctx));}
 findDocument(address:any,at:any,ctx:any){return this.call("find_document",{address,at},()=>this.memory.findDocument(address,at,ctx));}
 document(id:any,at:any,ctx:any){return this.call("document",{id,at},()=>this.memory.document(id,at,ctx));}
 scanDocuments(q:any,l:any,c:any,ctx:any){return this.call("scan_documents",scanArgs(q,l,c),()=>this.memory.scanDocuments(q,l,c,ctx));}
 close(ctx:any):Promise<void>{const d=this.closeDirective;this.effects.push({e:"close-enter"});return (async()=>{try{
  if(d?.hold){d.entered.resolve();await d.release.promise;}
  await this.memory.close(ctx);if(d?.kind==="throw")throw d.error;this.effects.push({e:"close-exit",status:"fulfilled"});
 }catch(error){this.effects.push({e:"close-exit",status:"rejected",error:ref(error)});throw error;}})();}
}
function invoke(s:any,op:string,a:any,ctx:any):Promise<any>{switch(op){case "mint":return s.mintId();case "commit":return s.commit(a.writes,ctx);case "conversation":return s.conversation(a.id,ctx);case "task":return s.task(a.id,ctx);case "submission":return s.submission(a.id,ctx);case "request":return s.submissionByRequest(a.conversation_id,a.request_id,ctx);case "entry":return a.conversation_id===undefined?s.entry(a.id,ctx):s.entry(a.conversation_id,a.id,ctx);case "head":return s.findLatestHeadMarker(a.conversation_id,a.bound,ctx);case "scan_conversations":return s.scanConversations(a.query,a.limit,a.cursor,ctx);case "scan_entries":return s.scanEntries(a.query,a.limit,a.cursor,ctx);case "scan_tasks":return s.scanTasks(a.query,a.limit,a.cursor,ctx);case "scan_submissions":return s.scanSubmissions(a.query,a.limit,a.cursor,ctx);case "document":return s.document(a.id,a.at??"current",ctx);case "find_document":return s.findDocument(a.address,a.at??"current",ctx);case "scan_documents":return s.scanDocuments(a.query,a.limit,a.cursor,ctx);default:throw Error("Unknown guarded operation");}}
const facts:any[]=[];
const scenarios:any[]=[];
for(const scenario of input.scenarios){
 errors=new Map();for(const item of input.errors)errors.set(item.id,item.kind==="request"?new StorageRequestError(item.message):new Error(item.message));
 const cancels=new Map<string,()=>void>(),backend=new ControlledBackend(cancels),driver=new GuardDriver(backend as any);
 const handles=new Map<string,any>(),cache=new Map<string,any>(),steps:any[]=[];
 let closedValue:any,end:any,effects:any[]|undefined;
 void driver.closed.then(value=>{closedValue=value;});
 facts.push({guarded_is_backend:driver.guarded===backend,guarded_constructor:driver.guarded.constructor.name,failure_initially:driver.failure===undefined});
 const track=(h:string,p:Promise<any>,extra:any={})=>{const item:any={promise:p,settled:false,...extra};p.then(v=>{item.settled=true;item.ok=true;item.value=v;},e=>{item.settled=true;item.ok=false;item.error=e;});handles.set(h,item);return item;};
 const outcome=(h:string,item:any)=>{if(item.ok)return {status:"fulfilled",value:encode(item.value)};rawError(scenario.id,h,item.error);return {status:"rejected",error:ref(item.error,item.signal)};};
 const settle=async(h:string)=>{const item=handles.get(h);assert.ok(item);await deadline(item.promise.then(()=>{},()=>{}),scenario.id+":"+h);await drain();if(item.ok)cache.set(h,item.value);return outcome(h,item);};
 const rp=scenario.report_probe;
 driver.onReport=(error,info)=>{backend.effects.push({e:"report",error:ref(error),type:info?.type??null,failure_visible:driver.failure!==undefined});
  if(rp!==undefined&&!handles.has(rp)){const previous=ambient;ambient=rp;let p:Promise<any>;try{p=driver.guarded.task(1,BACKGROUND_CONTEXT);}catch(e){p=Promise.reject(e);}finally{ambient=previous;}track(rp,p);}};
 const start=(step:any)=>{const a=decode(step.call.args);if(a.cursor_ref!==undefined){assert.ok(cache.has(a.cursor_ref));a.cursor=cache.get(a.cursor_ref).next;delete a.cursor_ref;}
  let ctx=BACKGROUND_CONTEXT;if(step.facade!=="default"){const c=withCancel(BACKGROUND_CONTEXT);ctx=c.context;cancels.set(step.handle,()=>c.cancel());if(step.facade==="cancelled")c.cancel();}
  ambient=step.handle;let p:Promise<any>;try{p=invoke(driver.guarded,step.call.op,a,ctx);}catch(e){p=Promise.reject(e);}finally{ambient=null;}track(step.handle,p);};
 const directive=(step:any)=>{const o=step.outcome;const kind=o==="pass"?"pass":Object.keys(o)[0];return {hold:step.hold,kind,error:kind==="pass"?undefined:errors.get(o[kind]),entered:deferred(),release:deferred()};};
 try{
  for(const step of scenario.steps){const record:any={id:step.id,op:step.op};
   switch(step.op){
   case "arm":backend.directives.set(step.handle,directive(step));break;
   case "arm_close":backend.closeDirective=directive(step);break;
   case "start":start(step);break;
   case "call":case "probe":start(step);record.outcome=await settle(step.handle);break;
   case "await_entered":await deadline(backend.directives.get(step.handle).entered.promise,step.id);record.reached=true;break;
   case "release":backend.directives.get(step.handle).release.resolve();break;
   case "settle":record.outcome=await settle(step.handle);break;
   case "cancel":cancels.get(step.handle)!();break;
   case "fail":try{driver.fail(errors.get(step.error));record.outcome={status:"fulfilled",value:["undefined"]};}catch(e){rawError(scenario.id,step.id,e);record.outcome={status:"rejected",error:ref(e)};}break;
   case "close_start":{let ctx=BACKGROUND_CONTEXT;let signal:AbortSignal|undefined;if(step.cancellable){const c=withCancel(BACKGROUND_CONTEXT);ctx=c.context;signal=c.context.abortSignal;cancels.set(step.handle,()=>c.cancel());}track(step.handle,driver.close(ctx),{signal});break;}
   case "quiesce":await drain();break;
   case "await_close_entered":await deadline(backend.closeDirective.entered.promise,step.id);record.reached=true;break;
   case "release_close":backend.closeDirective.release.resolve();break;
   case "observe":{await drain();const count=(e:string)=>backend.effects.filter(x=>x.e===e).length;
    record.state={failure:driver.failure===undefined?null:ref(driver.failure.error),close_entered:count("close-enter"),close_exited:count("close-exit"),reports:count("report"),end:closedValue===undefined?null:closedValue.reason==="closed"?{reason:"closed"}:{reason:"failed",error:ref(closedValue.error)},pending:[...handles].filter(([h,x])=>h!==rp&&!x.settled).map(([h])=>h).sort()};break;}
   case "raw_read":{const a=decode(step.call.args);try{record.outcome={status:"fulfilled",value:encode(await invoke(backend.memory,step.call.op,a,BACKGROUND_CONTEXT))};}catch(e){rawError(scenario.id,step.id,e);record.outcome={status:"rejected",error:ref(e)};}break;}
   case "await_end":{const value:any=await deadline(driver.closed,scenario.id+":end");await drain();effects=backend.effects.map(x=>({...x}));end=value.reason==="closed"?{reason:"closed"}:{reason:"failed",error:ref(value.error)};break;}
   default:throw Error("Unknown recipe step");}
   steps.push(record);}
 }finally{
  for(const d of backend.directives.values())d.release.resolve();backend.closeDirective?.release.resolve();
  await deadline(driver.close(BACKGROUND_CONTEXT).catch(()=>{}),scenario.id+":cleanup");
  await deadline(Promise.allSettled([...handles.values()].map(x=>x.promise)),scenario.id+":handles");
 }
 assert.ok(effects!==undefined);assert.equal(driverErrors.length,0);
 scenarios.push({id:scenario.id,steps,effects,end});
}
const save=(name:string,value:any)=>writeFileSync(resolve(outputPath,name),JSON.stringify(value,null,2)+"\n");
assert.ok(facts.every(x=>x.guarded_is_backend===false&&x.guarded_constructor==="GuardedStorage"&&x.failure_initially===true));
save("observed.json",{schema_version:1,phase:input.phase,backend:"node",scenarios});
save("reference-context.json",{schema_version:1,phase:input.phase,scenarios:scenarios.length,driver:{base:Object.getPrototypeOf(GuardDriver).name,own_members:Object.getOwnPropertyNames(GuardDriver.prototype).sort(),guarded_is_backend:false,guarded_constructor:"GuardedStorage",close_route:"SessionImpl.close"}});
save("guard-raw.json",{schema_version:1,errors:raw});save("resolutions.json",resolutions);
