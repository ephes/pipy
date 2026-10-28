/** Bounded DE14b2 generic owner settings publication; actual host object, no Pi setter.
 * MIT (c)2025 Mario Zechner; see tests/fixtures/durable/UPSTREAM-LICENSE.txt. */
import assert from 'node:assert/strict';
import {readFileSync,writeFileSync,realpathSync,mkdirSync} from 'node:fs';
import {registerHooks} from 'node:module';
import {fileURLToPath} from 'node:url';
import {sep} from 'node:path';
const root=realpathSync(process.cwd()),[inputPath,out]=process.argv.slice(2),resolutions:any[]=[];
registerHooks({resolve(specifier,context,next){const result=next(specifier,context);if(!result.url.startsWith('node:')){const path=realpathSync(fileURLToPath(result.url));assert.ok(path.startsWith(root+sep));resolutions.push({specifier,path:path.slice(root.length+1)});}return result;}});
const {Harness,createRegistry,defineExtension,defineTask}=await import('./packages/durable/src/index.ts');
const {openNodeSqliteStorage}=await import('./packages/durable/src/storage/sqlite/node.ts');
const {createModels}=await import('@earendil-works/pi-ai');
const {BACKGROUND_CONTEXT:ctx}=await import('@earendil-works/chord/context');
const input=JSON.parse(readFileSync(inputPath,'utf8'));assert.equal(input.phase,'restart-policy-live-v1');
const clone=(v:any)=>JSON.parse(JSON.stringify(v));
const number=(v:any)=>v==='Infinity'?Infinity:v;
const count=(r:any)=>({present:Object.hasOwn(r,'restarts'),value:r.restarts??null});
const raw:any[]=[];
for(const recipe of input.cases){
 const owners=new Set<any>();let originalFailure:any=null;
 try{
  const folder=out+'/stores/pi/'+recipe.id;mkdirSync(folder,{recursive:true,mode:0o700});const path=folder+'/session.sqlite';
  const calls:any[]=[],closed:string[]=[];let taskId:any=null,last:any=null,settings:any=null;
  const token=defineTask({name:'fixture.restart.live',version:1,initial:()=>({phase:'run'}),phases:{run:async(task:any,runtime:any,context:any)=>{calls.push(count(task));await runtime.commit(()=>({status:'terminal',outcome:{status:'completed',result:{ok:true}}}),context);}},abort:async()=>{throw Error('Abort outside live-policy common profile');}});
  const registry=createRegistry();registry.install(defineExtension({name:'fixture.restart.live.extension',tasks:[token]}));
  async function owner(limit:any){
   const inner=await openNodeSqliteStorage(path);
   const storage=new Proxy(inner,{get(target,key){const value=Reflect.get(target,key,target);if(typeof value!=='function')return value;return async(...args:any[])=>{if(key==='close'&&taskId!==null)last=clone(await target.task(taskId,ctx));return value.apply(target,args);};}});
   settings={restartLimit:number(limit)};
   const opened=await Harness.open(storage as any,{models:createModels(),registry,settings,now:()=>1000},ctx);owners.add(opened);return opened;
  }
  async function end(h:any){await h.close(ctx);closed.push((await h.closed).reason);owners.delete(h);assert.ok(last);return clone(last);}
  async function parked(h:any){const inspection=await h.inspect(ctx);const info=inspection.tasks.find((t:any)=>t.record.id===taskId);return info?.state.kind==='blocked'&&info.state.reason==='restart_limit';}
  let h=await owner(recipe.initial);const conversation=await h.createConversation({ownership:{kind:'ownerless'}},ctx);
  taskId=await h.commit((tx:any)=>tx.createTask(token,{fixture:recipe.id},{ownership:{kind:'conversation'},conversationId:conversation.id}),ctx);
  const created=clone(await h.getTask(taskId,ctx));
  await h.commit(async(tx:any)=>{const row=await tx.task(taskId);tx.setTask({...row,restarts:recipe.count});},ctx);
  const seeded=clone(await h.getTask(taskId,ctx));await end(h);closed.length=0;
  h=await owner(recipe.initial);const after_open=clone(await h.getTask(taskId,ctx));const calls_at_open=calls.length;
  const parked_before=await parked(h),before_update=clone(await h.getTask(taskId,ctx));
  // Actual documented host settings object mutation, not a fictional Harness setter.
  settings.restartLimit=number(recipe.update);
  const after_update=clone(await h.getTask(taskId,ctx)),calls_at_update=calls.length,parked_after=await parked(h);
  // CONTROL: this is the first resume of this freshly opened owner.
  // Pi idle means no live ordinary task, not completion of a parked reservation pass.
  const before_resume_inspection=await h.inspect(ctx);assert.equal(before_resume_inspection.scheduling,'paused');
  h.resume();
  if(parked_after){
   // The drain microtask queued by resume enqueues its reservation commit synchronously.
   // Our later microtask enqueues a real FIFO Session-line read after that callback.
   // This observes that reservation boundary, NOT global/host quiescence.
   await new Promise<void>((resolve,reject)=>{queueMicrotask(()=>{h.readOnLine(async()=>undefined).then(resolve,reject);});});
  }else{await h.waitForIdle(ctx);}
  const first_resume_observer={before_scheduling:before_resume_inspection.scheduling,kind:parked_after?'control-read-on-Session-line-after-first-resume-reservation':'actual-live-graph-idle'};
  const after_resume=clone(await h.getTask(taskId,ctx)),calls_after_resume=calls.length;
  const first_close=await end(h);h=await owner(recipe.fresh);
  const before_final_resume=clone(await h.getTask(taskId,ctx)),calls_before_final_resume=calls.length;
  h.resume();await h.waitForIdle(ctx);
  const after_final_resume=clone(await h.getTask(taskId,ctx)),after_close=await end(h);
  raw.push({id:recipe.id,control:{kind:'explicit-temporary-task-row-seed',created,seeded,physical_SIGKILL:false},after_open,before_update,after_update,calls_at_open,calls_at_update,parked_before,parked_after,after_resume,calls_after_resume,first_close,before_final_resume,calls_before_final_resume,after_final_resume,after_close,phase_counts:calls,closed,reference_only:{update:'HarnessOptions.settings host object assignment; explicit resume; eligible branch waitForIdle also enables scheduling',first_resume_observer,setter_ACK:null},mutations:'Actual creation/seed and real Harness/runtime/resume/terminal/close commits in temporary SQLite. Host settings mutation is separated from later writes; not a blanket zero-effect claim.'});
 }catch(error){originalFailure=error;throw error;}finally{const errors:any[]=[];for(const owner of owners){try{await owner.close(ctx);}catch(error){errors.push(error);}}if(errors.length)throw new AggregateError(originalFailure===null?errors:[originalFailure,...errors],'Live-policy Harness cleanup failed');}
}
writeFileSync(out+'/raw-stored.json',JSON.stringify(raw));writeFileSync(out+'/resolutions.json',JSON.stringify(resolutions));
