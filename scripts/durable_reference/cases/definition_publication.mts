/** Bounded registry Task-identity observations. MIT (c)2025 Mario Zechner; upstream license in fixtures. */
import assert from 'node:assert/strict';
import {readFileSync,writeFileSync,realpathSync,mkdirSync} from 'node:fs';
import {registerHooks} from 'node:module';
import {fileURLToPath} from 'node:url';
import {sep} from 'node:path';
import {DatabaseSync} from 'node:sqlite';
const root=realpathSync(process.cwd()),[inputPath,out]=process.argv.slice(2),resolutions:any[]=[];
registerHooks({resolve(specifier,context,next){const result=next(specifier,context);if(!result.url.startsWith('node:')){const path=realpathSync(fileURLToPath(result.url));assert.ok(path.startsWith(root+sep));resolutions.push({specifier,path:path.slice(root.length+1)});}return result;}});
const {Harness,createRegistry,defineExtension,defineTask}=await import('./packages/durable/src/index.ts');
const {openNodeSqliteStorage}=await import('./packages/durable/src/storage/sqlite/node.ts');
const {createModels}=await import('@earendil-works/pi-ai');
const {BACKGROUND_CONTEXT:ctx}=await import('@earendil-works/chord/context');
const recipe=JSON.parse(readFileSync(inputPath,'utf8'));assert.equal(recipe.phase,'task-definition-publication-idle-v1');
const names=['fixture.definition.main','fixture.definition.left','fixture.definition.right','fixture.definition.unrelated','fixture.definition.absent'];
const clone=(x:any)=>JSON.parse(JSON.stringify(x));
function sqlSnapshot(path:string){
 const db=new DatabaseSync(path,{readOnly:true});
 try{db.exec('PRAGMA query_only=ON; BEGIN');const result:any={};
  for(const {name}of db.prepare("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").all() as any[]){
   const q='"'+name.replaceAll('"','""')+'"';const columns=(db.prepare('PRAGMA table_info('+q+')').all() as any[]).map(x=>x.name);
   const rows=(db.prepare('SELECT * FROM '+q).all() as any[]).map(row=>columns.map(k=>row[k] instanceof Uint8Array?{sqlite_blob_hex:Buffer.from(row[k]).toString('hex')}:typeof row[k]==='bigint'?{sqlite_bigint:row[k].toString()}:row[k]));
   rows.sort((a,b)=>JSON.stringify(a).localeCompare(JSON.stringify(b)));result[name]={columns,rows};
  }return result;
 }finally{db.close();}
}
function snapshotData(snapshot:any){
 const all=snapshot.tasks();return {order:all.filter((t:any)=>t.definition.name.startsWith('fixture.definition.')).map((t:any)=>t.definition.name),lookup:Object.fromEntries(names.map(name=>{const task=snapshot.task(name);return [name,task===undefined?null:{version:task.definition.version,migrate:task.definition.migrate!==undefined}];})),declarations:all.map((t:any)=>({name:t.definition.name,version:t.definition.version,phases:Object.keys(t.definition.phases),migrate:t.definition.migrate!==undefined}))};
}
// A real FIFO Session-line read after the drain microtask's reservation enqueue.
// This proves reservation-job observation, NEVER #drain retirement or graph idle.
async function reservationLine(h:any){await new Promise<void>((resolve,reject)=>{queueMicrotask(()=>{h.readOnLine(async()=>undefined).then(resolve,reject);});});}
const raw:any[]=[];
for(const c of recipe.cases){
 const owners=new Set<any>();let failure:any=null;
 try{
  const folder=out+'/stores/pi/'+c.id;mkdirSync(folder,{recursive:true,mode:0o700});const path=folder+'/session.sqlite';
  const counts:any={initial:0,migrate:0,handler:0,abort:0},callback_log:string[]=[],closed:string[]=[],notifications:any[]=[];let h:any,taskId:any;
  const counterPath=folder+'/callback-counter.sqlite';{const db=new DatabaseSync(counterPath);try{db.exec('CREATE TABLE calls(seq INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL)');}finally{db.close();}}
  const ledger=()=>{const db=new DatabaseSync(counterPath,{readOnly:true});try{return db.prepare('SELECT seq,kind FROM calls ORDER BY seq').all();}finally{db.close();}};
  const called=(name:string)=>{const db=new DatabaseSync(counterPath);try{db.prepare('INSERT INTO calls(kind) VALUES (?)').run(name);}finally{db.close();}counts[name]++;callback_log.push(name);};
  const initial=()=>{called('initial');return {phase:'run'};};
  const phase=async(_task:any,runtime:any,context:any)=>{called('handler');await runtime.commit(()=>({status:'terminal',outcome:{status:'completed',result:{case:c.id}}}),context);};
  const abort=async()=>{called('abort');throw Error('Abort outside publication profile');};
  const failed=()=>{called('migrate');throw Error('fixture migration failure');};
  const success=(input:any,checkpoint:any)=>{called('migrate');return {input,checkpoint};};
  const task=(name=names[0],version=1,migrate:any=undefined)=>defineTask({name,version,initial,phases:{run:phase},abort,...(migrate===undefined?{}:{migrate})});
  const main=task(),a=task(names[0],2,failed),b=task(names[0],2,failed),good=task(names[0],2,success),left=task(names[1]),right=task(names[2]),left2=task(names[1]),unrelated=task(names[3]);
  const registry=createRegistry();registry.subscribe(()=>notifications.push('actual-registry-publication'));
  const extension=(token:any)=>defineExtension({name:'fixture.definition.extension.'+token.definition.name,tasks:[token]});
  const install=(token:any)=>registry.install(extension(token));
  const remove=(name:string)=>registry.uninstall(defineExtension({name:'fixture.definition.extension.'+name}));
  const failing=['enabled-failure-cache','failed-remove-readd-and-overwrite'].includes(c.id);
  if(c.id!=='missing-install'){install(main);if(c.id==='order-and-snapshot'){install(left);install(right);}}
  async function owner(){const opened=await Harness.open(await openNodeSqliteStorage(path),{models:createModels(),registry,now:()=>1000},ctx);owners.add(opened);return opened;}
  async function end(){await h.close(ctx);closed.push((await h.closed).reason);owners.delete(h);}
  h=await owner();const conversation=await h.createConversation({ownership:{kind:'ownerless'}},ctx);taskId=await h.commit((tx:any)=>tx.createTask(main,{fixture:c.id},{ownership:{kind:'conversation'},conversationId:conversation.id}),ctx);const created=clone(await h.getTask(taskId,ctx));
  if(failing){await end();install(a);h=await owner();}
  const old=registry.snapshot(),old_snapshot=snapshotData(old),steps:any[]=[];
  async function step(label:string,before:any=null,sql:any=null,side:any=null){const data=snapshotData(registry.snapshot());steps.push({label,record:clone(await h.getTask(taskId,ctx)),before_record:before,counts:clone(counts),callback_rows:ledger(),order:data.order,lookup:data.lookup,generation:data,old_snapshot:snapshotData(old),sql,reference_only:side});}
  async function resume(label:string){const inspect=await h.inspect(ctx);assert.equal(inspect.scheduling,'paused');h.resume();if(label==='first-resume')await reservationLine(h);else await h.waitForTask(taskId,ctx);await step(label,null,null,{before_scheduling:inspect.scheduling,observer:label==='first-resume'?'control-FIFO-reservation-line-not-idle':'actual-terminal-task-wait'});}
  async function publish(label:string,kind:string,incoming:any,enabled=false,final=false){
   const before=final?null:clone(await h.getTask(taskId,ctx)),sqlBefore=enabled?null:sqlSnapshot(path),notices=notifications.length;
   if(kind==='install')install(incoming);else remove(incoming);
   const delta=notifications.length-notices;assert.equal(delta,kind==='install'||label!=='absent-remove'?1:0);
   if(enabled){await reservationLine(h);if(final)await h.waitForTask(taskId,ctx);}
   await step(label,before,enabled?null:{before:sqlBefore,after:sqlSnapshot(path)},{registry_notification_delta:delta,observer:enabled?'control-FIFO-reservation-line-not-idle':'paused-publication-no-resume',ACK:null});
  }
  await step('open');
  if(c.id==='missing-install'){await publish('install','install',main);await resume('resume');}
  else if(c.id==='enabled-failure-cache'){await resume('first-resume');await publish('identical','install',a,true);await publish('unrelated','install',unrelated,true);await publish('replacement-success','install',good,true,true);}
  else if(c.id==='failed-remove-readd-and-overwrite'){await resume('first-resume');await publish('remove','remove',names[0],true);await publish('readd-same','install',a,true);await publish('replacement-failed','install',b,true);await publish('restore-A','install',a,true);await publish('replacement-success','install',good,true,true);}
  else if(c.id==='order-and-snapshot'){await publish('replace-middle','install',left2);await publish('remove-middle','remove',names[1]);await publish('reappend-middle','install',left);await resume('resume');}
  else{await publish('absent-remove','remove',names[4]);await publish('identical','install',main);await resume('resume');}
  const final_record=clone(await h.getTask(taskId,ctx));await end();
  raw.push({id:c.id,control:{kind:'actual-created-generic-task-and-real-registry-publication',physical_SIGKILL:false,fabricated_task_or_failure_memo:false},created,steps,old_snapshot,final_record,callback_log,callback_rows:ledger(),callback_sqlite:sqlSnapshot(counterPath),closed,sqlite_final:sqlSnapshot(path),reference_only:{notifications,registry_source_Task_identity:true,observer_scope:'FIFO reservation-line observation, not native idle gate or graph quiescence'},mutations:'Actual creation/migration/terminal commits in temporary SQLite. Paused publication boundary separated from successor work; no blanket zero-effect claim.'});
 }catch(error){failure=error;throw error;}finally{const errors:any[]=[];for(const owner of owners){try{await owner.close(ctx);}catch(error){errors.push(error);}}if(errors.length)throw new AggregateError(failure===null?errors:[failure,...errors],'Definition-publication Harness cleanup failed');}
}
writeFileSync(out+'/raw-stored.json',JSON.stringify(raw));writeFileSync(out+'/resolutions.json',JSON.stringify(resolutions));
