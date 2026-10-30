/** Internal DE02b3 Session oracle: actual pinned SessionImpl commit/snapshot/close paths through a bounded driver subclass
 * (protected guarded Storage getter and a recording report; nothing else is overridden). Oracle adaptations, recorded in the
 * manifest scope: document definitions omit checkpointWhen and loaded nonempty changes persist actual deltas;
 * createTask receives a structural task-definition stub. Close routes only through SessionImpl.close.
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
const {SessionFailed,StorageRequestError,ReadAfterWrite}=await import("./packages/durable/src/errors.ts");
const {MemoryStorage}=await import("./packages/durable/src/storage/memory.ts");
const {defineDoc}=await import("./packages/durable/src/documents.ts");
const {BACKGROUND_CONTEXT,withCancel}=await import("@earendil-works/chord/context");
function hex(s:string){const b=Buffer.alloc(s.length*2);for(let i=0;i<s.length;i++)b.writeUInt16BE(s.charCodeAt(i),i*2);return b.toString("hex");}
const drafts=new WeakSet<object>();
function encode(v:any):any{if(v!==null&&typeof v==="object"&&drafts.has(v))return ["draft"];if(v===undefined)return ["undefined"];if(v===null)return ["null"];if(typeof v==="boolean")return ["bool",v];if(typeof v==="string")return ["string",hex(v)];if(typeof v==="number"){assert.ok(Number.isFinite(v));const b=Buffer.alloc(8);b.writeDoubleBE(v);return ["number",b.toString("hex")];}if(Array.isArray(v))return ["array",v.map(encode)];assert.ok(Object.getPrototypeOf(v)===Object.prototype||Object.getPrototypeOf(v)===null);return ["object",Object.keys(v).map(k=>[hex(k),encode(v[k])]).sort((a,b)=>a[0]<b[0]?-1:a[0]>b[0]?1:0)];}
const input=JSON.parse(readFileSync(inputPath,"utf8"));assert.equal(input.schema_version,1);assert.equal(input.phase,"session-commit-b3-v1");
const WAIT_MS=10000;
const PENDING="Session commit callback settled before its pending Tx operations";
const METHOD:Record<string,string>={conversation:"conversation",entry:"entry",task:"task",submission:"submission",submissionByRequest:"submission_by_request",latestHeadMarker:"latest_head_marker",scanConversations:"scan_conversations",scanEntries:"scan_entries",scanTasks:"scan_tasks"};
function deferred(){let resolve:any;const promise=new Promise<void>(r=>{resolve=r;});return {promise,resolve};}
function deadline<T>(p:Promise<T>,label:string):Promise<T>{let timer:any;return Promise.race([p,new Promise<never>((_,reject)=>{timer=setTimeout(()=>reject(new Error("Driver deadline: "+label)),WAIT_MS);})]).finally(()=>clearTimeout(timer));}
const drain=()=>new Promise(r=>setImmediate(r));
let errors=new Map<string,unknown>();
const reasons=new Set<unknown>(),raw:any[]=[],driverErrors:unknown[]=[];
const plain=(e:any,type:any)=>e instanceof type&&Object.getPrototypeOf(e)===type.prototype;
/** Closed error projection; anything else is unclassified and never equals a native ref. */
function ref(e:any,depth=0):any{
 for(const [id,obj] of errors)if(e===obj)return {kind:"scripted",id};
 if(depth<4&&e instanceof SessionFailed&&e.name==="SessionFailed")return {kind:"session-failed",message:e.message,cause:ref((e as any).cause,depth+1)};
 if(reasons.has(e))return {kind:"commit-cancelled"};
 if(e instanceof ReadAfterWrite&&e.name==="ReadAfterWrite"){const m=/^Tx\.(\w+)\(\) cannot read tables after the first table write$/.exec(e.message);if(m!==null&&Object.hasOwn(METHOD,m[1]))return {kind:"read-after-write",method:METHOD[m[1]]};}
 if(e instanceof StorageRequestError&&e.name==="StorageRequestError")return {kind:"request",message:e.message};
 if(plain(e,TypeError)&&e.message==="Cannot use a settled overlay")return {kind:"draft-revoked"};
 if(plain(e,Error)){if(e.message==="Session is closed")return {kind:"session-closed",message:e.message};if(e.message==="Transaction has settled")return {kind:"tx-settled"};if(e.message===PENDING)return {kind:"pending-ops",message:e.message};return {kind:"error",message:e.message};}
 return {kind:"unclassified",side:"node",name:String(e?.name),message:String(e?.message)};
}
function rawError(scenario:string,where:string,e:any){raw.push({scenario,where,name:String(e?.name),message:String(e?.message),stack:typeof e?.stack==="string"?e.stack:null,constructor:String(e?.constructor?.name)});}
/** Bounded subclass: the protected guarded Storage is exposed for off-line reads, and report is recorded. */
class SessionDriver extends SessionImpl {
 onReport:((error:unknown,info:any)=>void)|undefined;
 get guarded():any{return this.storage;}
 report(error:unknown,info?:any,_context?:unknown):void{try{this.onReport?.(error,info);}catch(e){driverErrors.push(e);}}
}
function scanArgs(query:any,limit:any,cursor:any){return {query,limit,...(cursor===undefined?{}:{cursor})};}
/** Storage over a fresh actual pinned MemoryStorage: per-operation FIFO directives (hold/outcome) and an ordered effect log. */
class ControlledBackend {
 memory=new MemoryStorage();effects:any[]=[];admitted:any[]=[];queues=new Map<string,any[]>();closeDirective:any;
 abort:(job:string)=>void;
 constructor(abort:(job:string)=>void){this.abort=abort;}
 call(op:string,args:any,fn:()=>Promise<any>):Promise<any>{
  const d=this.queues.get(op)?.shift(),gate=d?.gate??null;
  this.effects.push({e:"call",op,gate,args:encode(args)});
  return (async()=>{try{
   if(d?.hold){d.entered.resolve();await d.release.promise;}
   if(d?.kind==="abort_then_throw")this.abort(d.job);
   if(d?.kind==="throw_before"||d?.kind==="abort_then_throw")throw d.error;
   const value=await fn();
   if(d?.kind==="throw_after")throw d.error;
   this.effects.push({e:"return",op,gate,status:"fulfilled",value:encode(value)});return value;
  }catch(error){this.effects.push({e:"return",op,gate,status:"rejected",error:ref(error)});throw error;}})();
 }
 commit(writes:any,ctx:any){this.admitted.push(...writes);return this.call("commit",{writes},()=>this.memory.commit(writes,ctx));}
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
  await this.memory.close(ctx);this.effects.push({e:"close-exit",status:"fulfilled"});
 }catch(error){this.effects.push({e:"close-exit",status:"rejected",error:ref(error)});throw error;}})();}
}
function invoke(s:any,op:string,a:any,ctx:any):Promise<any>{switch(op){case "mint":return s.mintId();case "conversation":return s.conversation(a.id,ctx);case "task":return s.task(a.id,ctx);case "submission":return s.submission(a.id,ctx);case "entry":return s.entry(a.id,ctx);case "scan_entries":return s.scanEntries(a.query,a.limit,a.cursor,ctx);case "find_document":return s.findDocument(a.address,a.at??"current",ctx);case "document":return s.document(a.id,a.at??"current",ctx);default:throw Error("Unknown storage operation");}}
function change(c:any):any{
 if(c.type==="document"){const base={type:"document",record:encode(c.record),conversation:encode(c.conversationId),ops:encode(c.ops)};if(c.value===null){assert.equal(c.version,undefined);return {...base,retired:true};}return {...base,version:encode(c.version),value:encode(c.value)};}
 assert.ok(["conversation","entry","task","submission"].includes(c.type));return {type:c.type,value:encode(c.value)};
}
function walk(value:any,path:any[],draft=true):any{let node=value;for(const key of path)node=node[key];if(draft&&node!==null&&typeof node==="object")drafts.add(node);return node;}
const facts:any[]=[],scenarios:any[]=[];
for(const scenario of input.scenarios){
 errors=new Map();for(const item of input.errors)errors.set(item.id,item.kind==="request"?new StorageRequestError(item.message):new Error(item.message));
 const cancels=new Map<string,()=>void>(),handles=new Map<string,any>(),binds=new Map<string,any>(),marks=new Map<string,any>(),holds=new Map<string,any>(),gates=new Map<string,any>(),listeners=new Map<string,()=>void>();
 const backend=new ControlledBackend(job=>cancels.get(job)!());
 const driver=new SessionDriver(backend as any,()=>input.clock,()=>600_000,()=>undefined);
 const effects=backend.effects,steps:any[]=[];let current:string|undefined,end:any,observed:any[]|undefined;
 const once=(table:Map<string,any>,name:string)=>{let d=table.get(name);if(d===undefined){d=deferred();table.set(name,d);}return d;};
 const tokens=new Map<string,any>();
 for(const [name,spec] of Object.entries<any>(input.docs))tokens.set(name,defineDoc({checkpointWhen:()=>false,kind:spec.kind,version:spec.version,scope:spec.scope,...(spec.scope==="conversation"?{history:spec.history,fork:spec.fork}:{}),initial:()=>{effects.push({e:"init",doc:name});return structuredClone(spec.initial);}} as any));
 const owner=(doc:string,conversation:any)=>[tokens.get(doc),...(conversation===undefined?[]:[conversation])];
 const value=(v:any):any=>{if(Array.isArray(v))return v.map(value);if(v!==null&&typeof v==="object"){if(Object.hasOwn(v,"$local")||Object.hasOwn(v,"$bind"))return binds.get(v.$local??v.$bind);return Object.fromEntries(Object.entries(v).map(([k,x])=>[k,value(x)]));}return v;};
 const track=(h:string,p:Promise<any>,extra:any={})=>{const item:any={promise:p,settled:false,...extra};p.then(v=>{item.settled=true;item.ok=true;item.value=v;},e=>{item.settled=true;item.ok=false;item.error=e;});handles.set(h,item);return item;};
 const outcome=(where:string,item:any)=>{if(item.ok)return {status:"fulfilled",value:encode(item.value)};rawError(scenario.id,where,item.error);return {status:"rejected",error:ref(item.error)};};
 const settle=async(h:string)=>{const item=handles.get(h);assert.ok(item);await deadline(item.promise.then(()=>{},()=>{}),scenario.id+":"+h);await drain();if(item.ok&&item.bind!==undefined)binds.set(item.bind,item.value);return outcome(h,item);};
 const snapshot=(doc:string,conversation:any)=>{try{return driver.snapshot(...owner(doc,conversation),BACKGROUND_CONTEXT);}catch(e){return Promise.reject(e);}};
 const txCall=(tx:any,op:any):any=>{switch(op.tx){
  case "conversation":return tx.conversation(op.id);case "entry":return tx.entry(op.id);case "task":return tx.task(op.id);case "submission":return tx.submission(op.id);
  case "submission_by_request":return tx.submissionByRequest(op.conversation,op.request);case "latest_head_marker":return tx.latestHeadMarker(op.conversation);
  case "scan_conversations":return tx.scanConversations(value(op.query),op.limit);case "scan_entries":return tx.scanEntries(value(op.query),op.limit);case "scan_tasks":return tx.scanTasks(value(op.query),op.limit);
  case "create_conversation":return tx.createConversation({ownership:value(op.ownership)});case "append_entry":return tx.appendEntry(op.conversation,value(op.draft));
  case "create_task":{const checkpoint=op.checkpoint;return tx.createTask({definition:{name:op.kind,version:op.version,initial:()=>structuredClone(checkpoint)}},value(op.input),{ownership:value(op.ownership),...(op.conversation===undefined?{}:{conversationId:op.conversation}),...(op.background===undefined?{}:{background:op.background})});}
  case "create_submission":return tx.createSubmission(value(op.create));case "settle_submission":return tx.settleSubmission(op.id,value(op.settlement));case "place_submission":return tx.placeSubmission(op.id,op.entry);case "set_task":return tx.setTask(value(op.record));
  case "doc":{const p=tx.doc(...owner(op.doc,op.conversation));p.then((d:any)=>{if(d!==null&&typeof d==="object")drafts.add(d);},()=>{});return p;}
  case "retire_doc":return tx.retireDoc(...owner(op.doc,op.conversation));
  default:throw Error("Unknown Tx operation");}};
 /** The closed callback-program interpreter; each op is awaited in order, as an async Pi callback does. */
 const exec=async(op:any,tx:any):Promise<void>=>{
  if(Object.hasOwn(op,"tx")){const v=await txCall(tx,op);if(op.bind!==undefined)binds.set(op.bind,v);}
  else if(Object.hasOwn(op,"start_tx"))track(op.handle,txCall(tx,{...op,tx:op.start_tx}));
  else if(Object.hasOwn(op,"await")){const v=await handles.get(op.await).promise;if(op.bind!==undefined)binds.set(op.bind,v);}
  else if(Object.hasOwn(op,"draft_set")){const node=walk(binds.get(op.draft_set),op.path.slice(0,-1));node[op.path.at(-1)]=value(op.value);}
  else if(Object.hasOwn(op,"draft_push"))walk(binds.get(op.draft_push),op.path).push(...op.values.map(value));
  else if(Object.hasOwn(op,"draft_pop"))walk(binds.get(op.draft_pop),op.path).pop();
  else if(Object.hasOwn(op,"draft_get")){const v=walk(binds.get(op.draft_get),op.path);if(op.bind!==undefined)binds.set(op.bind,v);}
  else if(Object.hasOwn(op,"bind_tx"))binds.set(op.bind_tx,tx);
  else if(Object.hasOwn(op,"local"))binds.set(op.local,structuredClone(op.value));
  else if(Object.hasOwn(op,"mutate_local")){const node=walk(binds.get(op.mutate_local),op.path.slice(0,-1),false);node[op.path.at(-1)]=structuredClone(op.value);}
  else if(Object.hasOwn(op,"catch")){try{for(const inner of op.catch)await exec(inner,tx);}catch(e){rawError(scenario.id,"catch:"+op.mark,e);effects.push({e:"caught",mark:op.mark,error:ref(e)});}}
  else if(Object.hasOwn(op,"mark")){effects.push({e:"mark",mark:op.mark});once(marks,op.mark).resolve();}
  else if(Object.hasOwn(op,"hold"))await deadline(once(holds,op.hold).promise,scenario.id+":hold:"+op.hold);
  else if(Object.hasOwn(op,"throw"))throw errors.get(op.throw);
  else throw Error("Unknown program op");};
 const result=(spec:any):any=>Object.hasOwn(spec,"value")?structuredClone(spec.value):Object.hasOwn(spec,"binds")?spec.binds.map((b:string)=>binds.get(b)):spec.key!==undefined?binds.get(spec.bind)[spec.key]:binds.get(spec.bind);
 const program=async(name:string,job:string,tx:any)=>{current=job;for(const op of input.programs[name]){if(Object.hasOwn(op,"return"))return result(op.return);await exec(op,tx);}return undefined;};
 const start=(step:any)=>{let ctx=BACKGROUND_CONTEXT;if(step.context==="cancellable"){const c=withCancel(BACKGROUND_CONTEXT);ctx=c.context;cancels.set(step.job,()=>{c.cancel();reasons.add(c.context.abortSignal!.reason);});}
  let p:Promise<any>;try{p=driver.commit((tx:any)=>program(step.program,step.job,tx),ctx);}catch(e){p=Promise.reject(e);}track(step.job,p);};
 driver.onReport=(error,info)=>{effects.push({e:"report",error:ref(error),type:info?.type??"listener_error",failure_visible:driver.failure!==undefined});};
 const subscribe=(l:any)=>{
  const act=()=>{if(l.action==="throw")throw errors.get(l.error);if(l.action==="reject")return Promise.reject(errors.get(l.error));return undefined;};
  if(l.kind==="close"){listeners.set(l.id,driver.subscribeClose(()=>{effects.push({e:"close-listener",listener:l.id});return act();}));return;}
  const fn=(publication:any)=>{effects.push({e:"publish",listener:l.id,seq:encode(publication.seq),changes:publication.changes.map(change),handle_settled:current!==undefined&&handles.get(current)?.settled===true});
   if(l.bind!==undefined)binds.set(l.bind,publication.changes.find((c:any)=>c.type==="document"&&c.value!==null)?.value);
   if(l.hit!==undefined)snapshot(l.hit.doc,l.hit.conversation).then(v=>binds.set(l.hit.bind,v),e=>{driverErrors.push(e);});
   return act();};
  listeners.set(l.id,l.kind==="internal"?driver.observeCommits(fn):driver.subscribeCommits(fn));
 };
 for(const l of scenario.listeners??[])if(l.deferred!==true)subscribe(l);
 try{
  for(const step of scenario.steps){const record:any={id:step.id,op:step.op};
   switch(step.op){
   case "start":start(step);break;
   case "call":start(step);record.outcome=await settle(step.job);break;
   case "settle":record.outcome=await settle(step.job??step.handle);break;
   case "await_mark":await deadline(once(marks,step.mark).promise,scenario.id+":"+step.id);record.reached=true;break;
   // Pi admits at the commit()/snapshot() call: a started job is already on the line.
   case "await_queued":assert.ok(handles.has(step.job??step.handle));record.reached=true;break;
   case "release":if(step.hold!==undefined)once(holds,step.hold).resolve();else gates.get(step.gate).release.resolve();break;
   case "cancel":cancels.get(step.job)!();break;
   case "arm":{const o=step.outcome,kind=o==="pass"?"pass":Object.keys(o)[0];const d={gate:step.gate,hold:step.hold,kind,error:kind==="pass"?undefined:errors.get(o[kind]),job:step.job,entered:deferred(),release:deferred()};
    gates.set(step.gate,d);const queue=backend.queues.get(step.target)??[];queue.push(d);backend.queues.set(step.target,queue);break;}
   case "await_entered":await deadline(gates.get(step.gate).entered.promise,scenario.id+":"+step.id);record.reached=true;break;
   case "arm_close":backend.closeDirective={hold:step.hold,entered:deferred(),release:deferred()};break;
   case "await_close_entered":await deadline(backend.closeDirective.entered.promise,scenario.id+":"+step.id);record.reached=true;break;
   case "release_close":backend.closeDirective.release.resolve();break;
   case "fail":try{driver.fail(errors.get(step.error));record.outcome={status:"fulfilled",value:["undefined"]};}catch(e){rawError(scenario.id,step.id,e);record.outcome={status:"rejected",error:ref(e)};}break;
   case "close_start":{let p:Promise<any>;try{p=driver.close(BACKGROUND_CONTEXT);}catch(e){p=Promise.reject(e);}track(step.handle,p);break;}
   case "quiesce":await drain();await drain();break;
   case "observe":{await drain();record.state={pending:[...handles].filter(([h,x])=>!x.settled&&!h.startsWith("#")).map(([h])=>h).sort(),failure:driver.failure===undefined?null:ref(driver.failure.error),reports:effects.filter(x=>x.e==="report").length};break;}
   case "snapshot":{const h=step.handle??"#"+step.id;track(h,snapshot(step.doc,step.conversation),{bind:step.bind});if(step.handle===undefined)record.outcome=await settle(h);break;}
   case "guard_read":{const h="#"+step.id;let p:Promise<any>;try{p=invoke(driver.guarded,step.call.op,step.call.args,BACKGROUND_CONTEXT);}catch(e){p=Promise.reject(e);}track(h,p);record.outcome=await settle(h);break;}
   case "raw_read":try{record.outcome={status:"fulfilled",value:encode(await invoke(backend.memory,step.call.op,step.call.args,BACKGROUND_CONTEXT))};}catch(e){rawError(scenario.id,step.id,e);record.outcome={status:"rejected",error:ref(e)};}break;
   case "raw_commit":record.outcome={status:"fulfilled",value:encode(await backend.memory.commit(step.writes,BACKGROUND_CONTEXT))};break;
   case "identity":record.outcome={status:"fulfilled",value:["bool",binds.get(step.a)===binds.get(step.b)]};break;
   case "inspect":record.outcome={status:"fulfilled",value:encode(binds.get(step.bind))};break;
   case "use":try{await exec(step.action,step.tx===undefined?undefined:binds.get(step.tx));record.outcome={status:"fulfilled",value:["undefined"]};}catch(e){rawError(scenario.id,step.id,e);record.outcome={status:"rejected",error:ref(e)};}break;
   case "subscribe":subscribe(scenario.listeners.find((l:any)=>l.id===step.listener));break;
   case "unsubscribe":listeners.get(step.listener)!();break;
   case "await_end":{const value:any=await deadline(driver.closed,scenario.id+":end");await drain();observed=effects.map(x=>({...x}));end=value.reason==="closed"?{reason:"closed"}:{reason:"failed",error:ref(value.error)};break;}
   default:throw Error("Unknown recipe step");}
   steps.push(record);}
 }finally{
  for(const d of gates.values())d.release.resolve();backend.closeDirective?.release.resolve();
  for(const d of [...holds.values(),...marks.values()])d.resolve();
  await deadline(driver.close(BACKGROUND_CONTEXT).catch(()=>{}),scenario.id+":cleanup");
  await deadline(Promise.allSettled([...handles.values()].map(x=>x.promise)),scenario.id+":handles");
 }
 assert.ok(observed!==undefined);assert.equal(driverErrors.length,0);
 const changes=backend.admitted.filter(w=>w.type==="document.change");
 facts.push({sweeps:driver.documentCacheSizes.sweeps,changes:changes.length,delta:changes.every(w=>w.content.kind==="delta")});
 scenarios.push({id:scenario.id,steps,effects:observed,end});
}
const save=(name:string,value:any)=>writeFileSync(resolve(outputPath,name),JSON.stringify(value,null,2)+"\n");
assert.ok(facts.every(x=>x.sweeps===0&&x.delta));
save("observed.json",{schema_version:1,phase:input.phase,backend:"node",scenarios});
save("reference-context.json",{schema_version:1,phase:input.phase,scenarios:scenarios.length,driver:{base:Object.getPrototypeOf(SessionDriver).name,own_members:Object.getOwnPropertyNames(SessionDriver.prototype).sort(),close_route:"SessionImpl.close",checkpoint_when:"explicit-false",task_definition:"structural-stub",sweeps:facts.reduce((n,x)=>n+x.sweeps,0),delta_document_changes:facts.reduce((n,x)=>n+x.changes,0)}});
save("session-raw.json",{schema_version:1,errors:raw});save("resolutions.json",resolutions);
