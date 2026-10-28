/** DE14b1 actual cold generic migration; typed seeds are controls, not SIGKILL.
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
const input=JSON.parse(readFileSync(inputPath,'utf8'));assert.equal(input.phase,'task-migration-cold-v1');
const clone=(v:any)=>JSON.parse(JSON.stringify(v,(_k,x)=>x instanceof Error?{name:x.name,message:x.message}:x));
const same=(a:any,b:any):boolean=>{if(a===b)return true;if(a===null||b===null||typeof a!=='object'||typeof b!=='object')return false;if(Array.isArray(a)!==Array.isArray(b))return false;const keys=Object.keys(a);return keys.length===Object.keys(b).length&&keys.every(k=>Object.hasOwn(b,k)&&same(a[k],b[k]));};
const count=(r:any)=>({present:Object.hasOwn(r,'restarts'),value:r.restarts??null});
const handler=(r:any)=>({version:r.version,input:r.input,checkpoint:r.state.checkpoint,memos:r.memos??null,restarts:count(r)});
const reasons:any={missing_task:'definition_missing',task_too_old:'stored_version_newer',migration_failed:'migration_failed'};
async function until(predicate:()=>Promise<boolean>){const deadline=Date.now()+10000;while(!await predicate()){assert.ok(Date.now()<deadline,'Migration fixture discriminator timed out');await new Promise(r=>setTimeout(r,5));}}
const raw:any[]=[];
for(const recipe of input.cases){
 const owners=new Set<any>();let originalFailure:any=null;
 try{
  const folder=out+'/stores/pi/'+recipe.id;mkdirSync(folder,{recursive:true,mode:0o700});const path=folder+'/session.sqlite';
  const calls:any[]=[],handlers:any[]=[],second:any[]=[],closed:string[]=[],reports:any[]=[];let initializers=0,taskId:any=null,last:any=null;
  async function abort(){throw Error('abort outside cold migration profile');}
  async function phase(task:any,runtime:any,context:any){handlers.push(clone(task));await runtime.commit(()=>({status:'terminal',outcome:{status:'completed',result:{ok:true}}}),context);}
  async function secondPhase(task:any,runtime:any,context:any){second.push(clone(task));await runtime.commit(()=>({status:'terminal',outcome:{status:'completed',result:{second:true}}}),context);}
  function definition(version:number,mode:string,owner:string){
   function migrate(value:any,checkpoint:any,from_version:number){calls.push(clone({input:value,checkpoint,from_version,owner}));if(mode==='throw')throw Error('synthetic-migration-private-marker');if(mode==='invalid')return null;return {input:mode==='change'?{...value,migrated:true}:value,checkpoint:mode==='change'?{phase:'run',n:3}:checkpoint};}
   return defineTask({name:'fixture.migration',version,initial:()=>{initializers++;return {phase:'run',n:0};},phases:{run:phase},abort,...(mode==='none'?{}:{migrate})} as any);
  }
  const following=defineTask({name:'fixture.migration.following',version:1,initial:()=>({phase:'run'}),phases:{run:secondPhase},abort});
  async function owner(version:any,mode:string,tag:string){
   const registry=createRegistry();registry.install(defineExtension({name:'fixture.migration.extension',tasks:[following,...(version===null?[]:[definition(version,mode,tag)])]}));
   const inner=await openNodeSqliteStorage(path);
   const storage=new Proxy(inner,{get(target,key){const value=Reflect.get(target,key,target);if(typeof value!=='function')return value;return async(...args:any[])=>{if(key==='close'&&taskId!==null)last=clone(await target.task(taskId,ctx));return value.apply(target,args);};}});
   const opened=await Harness.open(storage as any,{models:createModels(),registry,now:()=>1000,onReport:(error:any,info:any)=>reports.push(clone({error,info}))},ctx);owners.add(opened);return opened;
  }
  async function end(h:any){await h.close(ctx);closed.push((await h.closed).reason);owners.delete(h);assert.ok(last);return clone(last);}
  async function record(h:any){return clone(await h.getTask(taskId,ctx));}
  async function inspection(h:any){const report=await h.inspect(ctx);return clone({report,selected:report.tasks.find((t:any)=>t.record.id===taskId)??null});}
  function reason(info:any){return info.selected?.state.kind==='blocked'?reasons[info.selected.state.reason]??info.selected.state.reason:null;}
  let h=await owner(recipe.stored,'none','create');const old=definition(recipe.stored,'none','create');
  const conversation=await h.createConversation({ownership:{kind:'ownerless'}},ctx);
  taskId=await h.commit((tx:any)=>tx.createTask(old,{fixture:recipe.id,n:1},{ownership:{kind:'conversation'},conversationId:conversation.id}),ctx);
  const created=await record(h);
  await h.commit(async(tx:any)=>{const row=await tx.task(taskId);tx.setTask({...row,state:{status:'pending',checkpoint:{phase:'run',n:2}},memos:{'fixture.memo':{kept:true}},restarts:recipe.parked?5:2});},ctx);
  let secondId:any=null;if(['throw','invalid'].includes(recipe.mode))secondId=await h.commit((tx:any)=>tx.createTask(following,null,{ownership:{kind:'conversation'},conversationId:conversation.id}),ctx);
  const seeded=await record(h);await end(h);closed.length=0;
  h=await owner(recipe.current,recipe.mode,'first');const after_open=await record(h),inspection_open=await inspection(h),calls_at_open=calls.length+handlers.length+second.length;
  const parked_at_open=inspection_open.selected?.state.kind==='blocked'&&inspection_open.selected.state.reason==='restart_limit';
  if(recipe.parked){assert.ok(parked_at_open);await h.retryTask(taskId,ctx);}else h.resume();
  const blocked=recipe.blocked!==null;let after_attempt:any,inspection_attempt:any,before_restore_calls:number,repeated_calls:number,blocked_unchanged:boolean,version:any;
  if(blocked){
   await until(async()=>reason(await inspection(h))===recipe.blocked);
   if(secondId!==null)await until(async()=>(await h.getTask(secondId,ctx))?.state.status==='terminal');
   after_attempt=await record(h);inspection_attempt=await inspection(h);before_restore_calls=calls.length;
   for(let i=0;i<3;i++){h.resume();assert.equal(reason(await inspection(h)),recipe.blocked);}
   repeated_calls=calls.length;blocked_unchanged=same(after_attempt,after_open);await end(h);
   version=Math.max(recipe.stored,recipe.current??recipe.stored)+1;h=await owner(version,'change','restore');h.resume();
  }else{
   await until(async()=>(await record(h)).state.status==='terminal');after_attempt=await record(h);inspection_attempt=await inspection(h);before_restore_calls=repeated_calls=calls.length;blocked_unchanged=true;version=recipe.current;
  }
  await until(async()=>(await record(h)).state.status==='terminal');const final=await record(h),after_close=await end(h);
  h=await owner(version,'change','restore');const after_cold_open=await record(h);h.resume();const inspection_cold=await inspection(h);await end(h);assert.equal(handlers.length,1);
  const identityKeys=['id','kind','conversationId','background','owner','links','trace','abortRequested','abortReason','abandonOnRestart'];
  const identity_preserved=identityKeys.every(k=>Object.hasOwn(final,k)===Object.hasOwn(seeded,k)&&same(final[k],seeded[k]));
  raw.push({id:recipe.id,control:{kind:'explicit-temporary-task-row-seed',created,seeded,physical_SIGKILL:false},before_open:seeded,after_open,after_attempt,after_close,after_cold_open,final,inspection_open,inspection_attempt,inspection_cold,calls_at_open,initializers,parked_at_open,migration_calls:calls,before_restore_calls,repeated_calls,first_handler:handler(handlers[0]),handler_records:handlers,second_handlers:second.length,second_handler_records:second,blocked_reason:blocked?reason(inspection_attempt):null,blocked_unchanged,identity_preserved,closed,cold_equal_calls:calls.length,private_retry_return:null,reports,mutations:'Actual task creation, explicit typed seeds, Harness migration/reservation/terminal/reset/close commits in private SQLite; not zero-effect'});
 }catch(error){originalFailure=error;throw error;}finally{const errors:any[]=[];for(const owner of owners){try{await owner.close(ctx);}catch(error){errors.push(error);}}if(errors.length)throw new AggregateError(originalFailure===null?errors:[originalFailure,...errors],'Migration fixture Harness cleanup failed');}
}
writeFileSync(out+'/raw-stored.json',JSON.stringify(raw));writeFileSync(out+'/resolutions.json',JSON.stringify(resolutions));
