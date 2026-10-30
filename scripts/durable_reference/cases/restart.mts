/** Bounded DE14a generic-task comparison; explicit stored-row controls, not SIGKILL.
 * MIT (c)2025 Mario Zechner; see tests/fixtures/durable/UPSTREAM-LICENSE.txt. */
import assert from 'node:assert/strict';
import {readFileSync,writeFileSync,realpathSync,mkdirSync} from 'node:fs';
import {registerHooks} from 'node:module';
import {fileURLToPath} from 'node:url';
import {sep} from 'node:path';
const root=realpathSync(process.cwd()),[inputPath,out]=process.argv.slice(2),resolutions:any[]=[];
registerHooks({resolve(specifier,context,next){const result=next(specifier,context);if(!result.url.startsWith('node:')){const path=realpathSync(fileURLToPath(result.url));assert.ok(path.startsWith(root+sep));resolutions.push({specifier,path:path.slice(root.length+1)});}return result;}});
const {Harness,createRegistry,defineExtension,defineTask,defineDoc}=await import('./packages/durable/src/index.ts');
const {openNodeSqliteStorage}=await import('./packages/durable/src/storage/sqlite/node.ts');
const {createModels}=await import('@earendil-works/pi-ai');
const {BACKGROUND_CONTEXT:ctx}=await import('@earendil-works/chord/context');
const input=JSON.parse(readFileSync(inputPath,'utf8'));assert.equal(input.phase,'restart-parking-v1');
const clone=(v:any)=>JSON.parse(JSON.stringify(v));
const count=(r:any)=>({present:Object.hasOwn(r,'restarts'),value:r.restarts??null});
const raw:any[]=[];
for(const recipe of input.cases){
 const owners=new Set<any>();let originalFailure:any=null;
 try{
 const folder=out+'/stores/pi/'+recipe.id;mkdirSync(folder,{recursive:true,mode:0o700});const path=folder+'/session.sqlite';
 let mode='complete',ready!:()=>void,finish!:()=>void;
 let reached=new Promise<void>(r=>ready=r),done=new Promise<void>(r=>finish=r);
 const calls:any[]=[],closed:string[]=[],observedPolicies:any[]=[];
 const Note=defineDoc({checkpointWhen:()=>false,kind:'fixture.restart.note',version:1,scope:'task',initial:()=>({writes:0})});
 function waitSignal(signal:AbortSignal){return new Promise<void>(resolve=>{if(signal.aborted)resolve();else signal.addEventListener('abort',()=>resolve(),{once:true});});}
 async function phase(task:any,runtime:any,context:any){
  calls.push({phase:task.state.checkpoint.phase,count:count(task)});
  observedPolicies.push({phase:task.state.checkpoint.phase,limit:Number.isFinite(runtime.settings.restartLimit)?runtime.settings.restartLimit:null});
  if(mode==='progress'&&task.state.checkpoint.phase==='run'){await runtime.commit(()=>({status:'running',checkpoint:{phase:'next',n:1}}),context);return;}
  if(mode==='chatter'){
   await runtime.memo('fixture.memo',{kept:true},context);
   await runtime.commit(async(tx:any)=>{(await tx.doc(Note,runtime.taskId)).writes++;},context);
   await runtime.commit(()=>({status:'running',checkpoint:Object.fromEntries(Object.entries(task.state.checkpoint).reverse())}),context);
  }
  if(['progress','chatter','hold'].includes(mode)){ready();await waitSignal(runtime.signal);return;}
  await runtime.commit(()=>({status:'terminal',outcome:{status:'completed',result:{ok:true}}}),context);finish();
 }
 const token=defineTask({name:'fixture.restart',version:1,initial:()=>({phase:'run',n:0}),phases:{run:phase,next:phase},abort:async()=>{throw Error('abort outside restart profile');}});
 const registry=createRegistry();registry.install(defineExtension({name:'fixture.restart.extension',tasks:[token]}));
 let taskId:any=null,last:any=null;
 async function owner(limit:any){
  const inner=await openNodeSqliteStorage(path);
  const storage=new Proxy(inner,{get(target,key){const value=Reflect.get(target,key,target);if(typeof value!=='function')return value;return async(...args:any[])=>{if(key==='close'&&taskId!==null)last=clone(await target.task(taskId,ctx));return value.apply(target,args);};}});
  const settings=limit==='default'?{}:{restartLimit:limit==='Infinity'?Infinity:limit};
  const opened=await Harness.open(storage as any,{models:createModels(),registry,settings,now:()=>1000},ctx);owners.add(opened);return opened;
 }
 async function end(h:any){await h.close(ctx);closed.push((await h.closed).reason);owners.delete(h);assert.ok(last);return clone(last);}
 let h=await owner(recipe.limit);const conversation=await h.createConversation({ownership:{kind:'ownerless'}},ctx);
 taskId=await h.commit((tx:any)=>tx.createTask(token,{fixture:recipe.id},{ownership:{kind:'conversation'},conversationId:conversation.id}),ctx);
 const created=clone(await h.getTask(taskId,ctx));
 await h.commit(async(tx:any)=>{const row=await tx.task(taskId);tx.setTask({...row,...(recipe.count===null?{}:{restarts:recipe.count}),state:{...row.state,...(recipe.running?{status:'running'}:{})}});},ctx);
 const seeded=clone(await h.getTask(taskId,ctx));
 // Closing a Harness that never dispatched this fixture has no invocation to rewrite.
 await end(h);closed.length=0;
 h=await owner(recipe.limit);const after_open=clone(await h.getTask(taskId,ctx));const calls_at_open=calls.length;
 const inspected=(await h.inspect(ctx)).tasks.find((t:any)=>t.record.id===taskId);const parked=inspected?.state.kind==='blocked'&&inspected.state.reason==='restart_limit';
 let first_policy_close:any=null,higher_policy_record:any=null,unlimited_control:any=null;
 if(recipe.action==='fresh'){
  first_policy_close=await end(h);h=await owner(6);assert.equal(calls.length,0);
  mode='hold';reached=new Promise<void>(r=>ready=r);h.resume();await reached;higher_policy_record=clone(await h.getTask(taskId,ctx));await end(h);
  // Deliberate typed control, not a crash/effect: distinguish Infinity from merely higher finite6.
  const control=await openNodeSqliteStorage(path);const original=await control.task(taskId,ctx);const changed={...original,restarts:1e100};await control.commit([{type:'task',value:changed}],ctx);await control.close(ctx);unlimited_control={before:original,after:changed};
  h=await owner('Infinity');mode='complete';done=new Promise<void>(r=>finish=r);h.resume();await done;
 }else if(['retry','noop'].includes(recipe.action)){
  mode='complete';done=new Promise<void>(r=>finish=r);await h.retryTask(taskId,ctx);await done;
 }else{mode=recipe.action;reached=new Promise<void>(r=>ready=r);h.resume();await reached;}
 const after_action=clone(await h.getTask(taskId,ctx));let chatter_evidence:any=null;if(recipe.action==='chatter'){const note=await h.commit(async(tx:any)=>clone(await tx.doc(Note,taskId)),ctx);chatter_evidence={document:note.writes,memo:after_action.memos?.['fixture.memo']??null};}const after_close=await end(h);
 h=await owner(recipe.action==='fresh'?'Infinity':recipe.limit);const after_reopen=clone(await h.getTask(taskId,ctx));const before_cold_close=calls.length;await end(h);assert.equal(calls.length,before_cold_close);
 raw.push({id:recipe.id,control:{kind:'explicit-temporary-task-row-seed',created,seeded,physical_SIGKILL:false},before_open:seeded,after_open,calls_at_open,parked,after_action,after_close,after_reopen,phases:calls,chatter_evidence,closed,private_retry_return:null,first_policy_close,higher_policy_record,unlimited_control,observed_runtime_policies:observedPolicies,mutations:'Task creation, explicit seed/control writes, actual Harness/runtime/progress/memo/document/reset/close commits in temporary SQLite; not zero-effect'});
 }catch(error){originalFailure=error;throw error;}finally{const errors:any[]=[];for(const owner of owners){try{await owner.close(ctx);}catch(error){errors.push(error);}}if(errors.length)throw new AggregateError(originalFailure===null?errors:[originalFailure,...errors],'Restart fixture Harness cleanup failed');}
}
writeFileSync(out+'/raw-stored.json',JSON.stringify(raw));writeFileSync(out+'/resolutions.json',JSON.stringify(resolutions));
