/** Actual pinned Pi Session checkpoints/unload, Memory/SQLite/reopen.
 * MIT (c) 2025 Mario Zechner. No live Draft traversal for instrumentation.
 * owner_replay labels a separate creation mirror or Chord replay, never a Session probe. */
import assert from "node:assert/strict";
import {readFileSync,writeFileSync,realpathSync,mkdirSync} from "node:fs";
import {registerHooks} from "node:module";
import {fileURLToPath} from "node:url";
import {sep} from "node:path";
const root=realpathSync(process.cwd()),[inputPath,out]=process.argv.slice(2),resolutions:any[]=[];
registerHooks({resolve(s,c,next){const result=next(s,c);if(!result.url.startsWith('node:')){const p=realpathSync(fileURLToPath(result.url));assert.ok(p.startsWith(root+sep));resolutions.push({specifier:s,path:p.slice(root.length+1)});}return result;}});
const {SessionImpl}=await import('./packages/durable/src/session/session.ts');
const {MemoryStorage}=await import('./packages/durable/src/storage/memory.ts');
const {openNodeSqliteStorage}=await import('./packages/durable/src/storage/sqlite/node.ts');
const {defineDoc,defineDocFamily}=await import('./packages/durable/src/documents.ts');
const {track,applyImmutable}=await import('./packages/chord/src/delta/index.ts');
const {BACKGROUND_CONTEXT:ctx}=await import('@earendil-works/chord/context');
const input=JSON.parse(readFileSync(inputPath,'utf8'));
assert.equal(input.schema_version,1);assert.equal(input.phase,'session-checkpoints-j-v1');
assert.equal(input.terminal_members,257);assert.equal(input.clock,1000);
function hex(s:string){const b=Buffer.alloc(s.length*2);for(let i=0;i<s.length;i++)b.writeUInt16BE(s.charCodeAt(i),i*2);return b.toString('hex');}
function encode(v:any):any{if(v===null||v===undefined)return ['null'];if(typeof v==='boolean')return ['bool',v];if(typeof v==='number'){assert.ok(Number.isFinite(v));const b=Buffer.alloc(8);b.writeDoubleBE(v);return ['number',b.toString('hex')];}if(typeof v==='string')return ['string',hex(v)];if(Array.isArray(v))return ['array',v.map(encode)];return ['object',Object.keys(v).map(k=>[hex(k),encode(v[k])])];}
function unhex(h:string){const b=Buffer.from(h,'hex');let s='';for(let i=0;i<b.length;i+=2)s+=String.fromCharCode(b.readUInt16BE(i));return s;}
function decode(v:any):any{const [k,p]=v;if(k==='null')return null;if(k==='bool')return p;if(k==='number')return Buffer.from(p,'hex').readDoubleBE();if(k==='string')return unhex(p);if(k==='array')return p.map(decode);assert.equal(k,'object');return Object.fromEntries(p.map(([k,v]:any)=>[unhex(k),decode(v)]));}
function at(v:any,path:any[]){for(const k of path)v=v[k];return v;}
function actions(v:any,steps:any[]){for(const [op,path,arg] of steps){if(op==='set')at(v,path.slice(0,-1))[path.at(-1)]=decode(arg);else if(op==='flip'){const p=at(v,path.slice(0,-1)),k=path.at(-1),z=p[k];p[k]=1;p[k]=-z;}else if(op==='pop-reinsert'){const a=at(v,path),x=a.splice(0,1)[0];a.splice(0,0,x);}else if(op==='bulk'){for(let n=0;n<arg;n++)v['field'+n]=1;}else throw Error('Unknown action');}}
function mirror(base:any,steps:any[],created=false){
 const working=structuredClone(base);actions(working,steps); // plain mirror, not a live Draft probe
 const tracker=track(base),change=tracker.beginChange();actions(change.state,steps);
 const prepared=change.prepare();
 return {working:encode(working),value:created?working:applyImmutable(base,prepared.ops),ops:prepared.ops};
}
async function bounded<T>(promise:Promise<T>){let timer:any;try{return await Promise.race([promise,new Promise<never>((_,r)=>timer=setTimeout(()=>r(Error('checkpoint deadline')),10000))]);}finally{clearTimeout(timer);}}
function task(terminal=false){return {id:20,conversationId:7,kind:'checkpoint.task',version:1,input:null,state:terminal?{status:'terminal',outcome:{status:'completed',result:null}}:{status:'pending',checkpoint:null},background:false,abortRequested:false};}
async function runtimeBoundaries(backend:string,folder:string){
 mkdirSync(folder,{mode:0o700});const db=folder+'/store.sqlite';let inner:any=backend==='memory'?new MemoryStorage():await openNodeSqliteStorage(db);
 const writes:any[]=[],events:string[]=[],children:any[]=[];
 const storage=new Proxy({}, {get(_target,key){return async(...args:any[])=>{const result=await inner[key](...args);if(key==='commit'){writes.push(args[0]);if(backend==='reopen'){await inner.close(ctx);inner=await openNodeSqliteStorage(db);}}return result;};}});
 const session=new SessionImpl(storage as any,()=>1000,()=>600000,()=>undefined);session.observeCommits(()=>events.push('publication'));
 const P=defineDoc({kind:'runtime.promise',version:1,scope:'session',initial:()=>({n:0}),checkpointWhen:((_v:any,_o:any,_i:any)=>Promise.resolve(false)) as any});
 const N=defineDoc({kind:'runtime.nested',version:1,scope:'session',initial:()=>({n:0}),checkpointWhen:()=>{children.push(session.commit(()=>{events.push('child');},ctx));return false;}});
 try{
  await bounded(session.commit(async(tx:any)=>{await tx.doc(P);await tx.doc(N);},ctx));
  const before=writes.length;let refused=false;
  try{await bounded(session.commit(async(tx:any)=>{(await tx.doc(P)).n=1;},ctx));}catch{refused=true;}
  const content=writes.length>before?writes.at(-1)[0].content:null;
  const promise={refused,admitted:writes.length-before,content_kind:content?.kind??null,value:encode(await session.snapshot(P,ctx))};
  events.length=0;await bounded(session.commit(async(tx:any)=>{(await tx.doc(N)).n=1;},ctx));await bounded(Promise.all(children));
  const nested={same_task_refused:false,child_after_publication:events.join(',')==='publication,child'};
  await bounded(session.close(ctx));let closedUnload=true;try{await bounded(session.unloadDocuments());}catch{closedUnload=false;}
  return {promise,nested,closed_unload_fulfilled:closedUnload};
 }finally{await bounded(session.close(ctx));}
}
const lanes:any[]=[];let serial=0;
for(const backend of ['memory','sqlite','reopen']){
 const cases:any[]=[];
 for(const recipe of input.cases){
  const folder=out+'/case-'+serial++;mkdirSync(folder,{mode:0o700});const db=folder+'/store.sqlite';
  let inner:any=backend==='memory'?new MemoryStorage():await openNodeSqliteStorage(db);
  const reopen=async()=>{await inner.close(ctx);inner=await openNodeSqliteStorage(db);};
  await inner.commit([{type:'conversation',value:{id:1}},{type:'conversation',value:{id:7}},{type:'task',value:task()}],ctx);
  if(recipe.special==='terminal')await inner.commit(Array.from({length:257},(_,i)=>({type:'document.create',record:{id:1000+i,kind:'unknown.'+i,scope:{kind:'task',taskId:20}},content:{kind:'base',version:1,value:{n:0}}})),ctx);
  const writes:any[]=[],publications:any[]=[],callbacks:any[]=[],effects:string[]=[],reports:any[]=[];
  const storage=new Proxy({}, {get(_target,key){return async(...args:any[])=>{if(key==='scanDocuments')effects.push('scan');if(key==='submission')effects.push('submission');const result=await inner[key](...args);if(key==='commit'){writes.push(args[0]);effects.push('commit');if(backend==='reopen')await reopen();}return result;};}});
  class Driver extends SessionImpl{report(e:any,info:any){reports.push({name:e.name,message:e.message,info});}}
  const makeSession=()=>{const s=new Driver(storage as any,()=>1000,()=>600000,()=>undefined);s.observeCommits((p:any)=>{publications.push(p);effects.push('publication');});return s;};let session=makeSession();
  const roots:any[]=[];let failSecond=false;const sentinel=Error('second policy');
  let base=decode(recipe.base);if(recipe.special==='root')base=Object.fromEntries(Array.from({length:4097},(_,i)=>['field'+i,0]));
  const make=(kind:string,initial:any,second=false)=>{const definition:any={kind,version:1,scope:recipe.scope,...(recipe.family?{family:true}:{}),initial:()=>structuredClone(initial),...(recipe.scope==='conversation'?{history:'latest',fork:'initial'}:{})};definition.checkpointWhen=function(value:any,ops:any,info:any){let revoked=true;for(const root of roots)try{Object.keys(root);revoked=false;}catch{}callbacks.push({value:encode(value),ops:encode(ops),count:info.deltasSinceBase,revoked,frozen:[Object.isFrozen(value),Object.isFrozen(ops),Object.isFrozen(info)]});effects.push('policy');if(second&&failSecond)throw sentinel;return recipe.policy==='true'||recipe.policy==='count>=2'&&info.deltasSinceBase>=2;};return recipe.family?defineDocFamily(definition):defineDoc(definition);};
  const D=make('checkpoint.'+recipe.id,base),args:any[]=[...(recipe.scope==='session'?[]:[recipe.scope==='conversation'?7:20]),...(recipe.family?['']:[])];
  const acquire=(tx:any,d:any=D)=>tx.doc(d,...args,...(recipe.family?[null]:[]));
  const snap=async(d:any=D)=>(await session.snapshot(d,...args,ctx))??null;
  const read=async(d:any=D)=>{const scope=recipe.scope==='session'?{kind:'session'}:recipe.scope==='conversation'?{kind:'conversation',conversationId:7}:{kind:'task',taskId:20};const record=await inner.findDocument({kind:d.definition.kind,scope,...(recipe.family?{key:''}:{})},'current',ctx);return record?await inner.document(record.id,'current',ctx):null;};
  const commit=(fn:any)=>bounded(session.commit(fn,ctx));
  const snapshotStep=async(before:any,start:any,derived:any)=>{
   const warm=await snap(),stored=await read(),pub=publications.length>start.pub?publications.at(-1):null;
   const doc=pub?.changes.find((c:any)=>c.type==='document'&&c.record.kind===D.definition.kind);
   const batch=writes.length>start.write?writes.at(-1):[],write=batch.find((w:any)=>(w.type==='document.change'||w.type==='document.create')&&(w.id===before?.id||w.record?.kind===D.definition.kind));
   const recent=callbacks.slice(start.callback),beforeUnload=[writes.length,publications.length];
   const actualOps=recent.length?decode(recent[0].ops):write?.content.kind==='delta'?write.content.ops:doc?.ops??[];
   const storedReplay=before?applyImmutable(before.stored.value,actualOps):write?.content.value;
   const replayBasis={value:encode(before?.stored.value),count:before?.stored.deltasSinceBase??null,ops_source:!before?'creation-base':recent.length?'checkpoint-argument':write?.content.kind==='delta'?'submitted-delta':doc?.ops.length?'publication':'empty-no-content'};
   await session.unloadDocuments();const cold=await snap();assert.deepEqual(beforeUnload,[writes.length,publications.length]);
   return {value:encode(warm),stored:encode(stored?.value),stored_replay:encode(storedReplay),replay_basis:replayBasis,cold:encode(cold),count:stored?.deltasSinceBase??null,version:stored?.version??null,same_snapshot:warm===before?.warm,admitted:writes.length-start.write,published:publications.length-start.pub,callbacks:recent,ops:encode(doc?.ops??[]),publication_value:encode(doc?.value),write_kind:write?.content.kind??null,write_value:encode(write?.content.value),write_ops:encode(write?.content.ops??[]),retired:batch.filter((w:any)=>w.type==='document.retire').length,effects:effects.slice(start.effect),working:{source:'derived-plain-mirror; not observed live Draft',value:derived.working},owner_replay:{source:before?'separate pinned Chord replay; not actual Session':'plain final creation mirror; not actual Session',value:encode(derived.value),ops:encode(derived.ops)}};
  };
  try{
   const creationMirror=mirror(base,recipe.creation,true);let ownerValue=creationMirror.value;
   const start={write:writes.length,pub:publications.length,callback:callbacks.length,effect:effects.length};
   await commit(async(tx:any)=>{const draft=await acquire(tx);roots.splice(0,roots.length,draft);actions(draft,recipe.creation);});
   const creation=await snapshotStep(null,start,{...creationMirror,ops:[]});
   const steps:any[]=[],facts:any={};
   if(recipe.special==='rollback'){
    const E=make('checkpoint.second',base,true);await commit((tx:any)=>acquire(tx,E));const old=await snap(),second=await snap(E),n=[writes.length,publications.length];let entry:any,submission:any,caught:any;failSecond=true;
    try{await commit(async(tx:any)=>{const a=await acquire(tx),b=await acquire(tx,E);roots.splice(0,roots.length,a,b);a.n=8;b.n=9;entry=await tx.appendEntry(7,{kind:'checkpoint.entry'});submission=await tx.createSubmission({conversationId:7,type:'input',status:'queued',requestId:'checkpoint.rollback'});tx.setTask(task(true));await tx.retireDoc(D,...args);});}catch(e){caught=e;}
    facts.rollback={same_error:caught===sentinel,same_first:await snap()===old,same_second:await snap(E)===second,no_admission:writes.length===n[0],no_publication:publications.length===n[1],entry_absent:await inner.entry(entry.id,ctx)===undefined,submission_absent:await inner.submission(submission.id,ctx)===undefined,task_pending:(await inner.task(20,ctx)).state.status==='pending'};failSecond=false;
   }
   for(const commands of recipe.steps){
    const stored=await read(),before={id:stored?.record.id,stored,warm:await snap()},start={write:writes.length,pub:publications.length,callback:callbacks.length,effect:effects.length};
    const derived=mirror(ownerValue,commands);ownerValue=derived.ops.length?derived.value:ownerValue;
    await commit(async(tx:any)=>{const draft=await acquire(tx);roots.splice(0,roots.length,draft);actions(draft,commands);if(recipe.special==='terminal')tx.setTask(task(true));if(recipe.special==='retire')await tx.retireDoc(D,...args);});
    steps.push(await snapshotStep(before,start,derived));
   }
   const final=await snap();let fresh:any=null;
   if(backend!=='memory'){await session.close(ctx);inner=await openNodeSqliteStorage(db);session=makeSession();fresh=await snap();}else{await session.unloadDocuments();fresh=await snap();}
   assert.deepEqual(reports,[]);
   cases.push({id:recipe.id,creation,steps,facts,final:encode(final),fresh:encode(fresh)});
  }finally{await bounded(session.close(ctx));}
 }
 lanes.push({backend,cases,runtime:await runtimeBoundaries(backend,out+'/runtime-'+backend)});
}
writeFileSync(out+'/observed.json',JSON.stringify({schema_version:1,phase:input.phase,producer:'pi',profile:input.profile,backends:lanes})+'\n');
writeFileSync(out+'/resolutions.json',JSON.stringify(resolutions,null,2)+'\n');
