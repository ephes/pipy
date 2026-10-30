/** Bounded real Harness task-too-old root orphan. MIT(c)2025 Mario Zechner; fixture license applies. */
import assert from 'node:assert/strict';
import {readFileSync,writeFileSync,realpathSync,mkdirSync} from 'node:fs';
import {registerHooks} from 'node:module';
import {fileURLToPath} from 'node:url';
import {sep} from 'node:path';
import {DatabaseSync} from 'node:sqlite';
const root=realpathSync(process.cwd()),[inputPath,out]=process.argv.slice(2),resolutions:any[]=[];
registerHooks({resolve(specifier,context,next){const result=next(specifier,context);if(!result.url.startsWith('node:')){const path=realpathSync(fileURLToPath(result.url));assert.ok(path.startsWith(root+sep));resolutions.push({specifier,path:path.slice(root.length+1)});}return result;}});
const {Harness,createRegistry,defineExtension,defineTask,defineDoc}=await import('./packages/durable/src/index.ts');
const {MemoryStorage}=await import('./packages/durable/src/storage/memory.ts');
const {openNodeSqliteStorage}=await import('./packages/durable/src/storage/sqlite/node.ts');
const {createModels}=await import('@earendil-works/pi-ai');
const {BACKGROUND_CONTEXT:ctx}=await import('@earendil-works/chord/context');
const recipe=JSON.parse(readFileSync(inputPath,'utf8'));assert.deepEqual(recipe,{"schema_version":1,"phase":"task-orphan-task-too-old-v1","cases":[{"id":"memory-warm","backend":"memory","cold":false,"parked":false,"install":false},{"id":"sqlite-warm","backend":"sqlite","cold":false,"parked":false,"install":false},{"id":"sqlite-cold-unmarked","backend":"sqlite","cold":true,"parked":false,"install":false},{"id":"sqlite-parked-control","backend":"sqlite","cold":false,"parked":true,"install":false},{"id":"sqlite-terminal-new-definition","backend":"sqlite","cold":false,"parked":false,"install":true}],"definition_versions":{"stored":2,"available":1}});
const clone=(x:any)=>structuredClone(x);
const NOTE=defineDoc({checkpointWhen:()=>false,kind:'fixture.orphan.too-old.note',version:1,scope:'task',initial:()=>({n:0})});
const CACHED=defineDoc({checkpointWhen:()=>false,kind:'fixture.orphan.too-old.cached',version:1,scope:'task',initial:()=>({n:0})});
const OTHER=defineDoc({checkpointWhen:()=>false,kind:'fixture.orphan.too-old.unrelated',version:1,scope:'conversation',history:'rewindable',fork:'current',initial:()=>({n:0})});
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
async function documents(storage:any,scope:any){
 let cursor:any=undefined;const result:any[]=[];
 do{const page=await storage.scanDocuments({scope,at:'current'},128,cursor,ctx);for(const record of page.items){result.push({record:clone(record),materialized:clone(await storage.document(record.id,'current',ctx))});}cursor=page.next;}while(cursor!==undefined);
 return result;
}
const raw:any[]=[];
for(const c of recipe.cases){
 const owners=new Set<any>();let failure:any=null;
 try{
  const folder=out+'/stores/pi/'+c.id;mkdirSync(folder,{recursive:true,mode:0o700});const path=folder+'/session.sqlite',counterPath=folder+'/callback-counter.sqlite';
  {const db=new DatabaseSync(counterPath);try{db.exec('CREATE TABLE calls(seq INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL)');}finally{db.close();}}
  const counts:any={initial:0,migrate:0,handler:0,abort:0},callback_log:string[]=[],batches:any[]=[],minted:any[]=[],closed:string[]=[],steps:any[]=[];
  const ledger=()=>{const db=new DatabaseSync(counterPath,{readOnly:true});try{return db.prepare('SELECT seq,kind FROM calls ORDER BY seq').all();}finally{db.close();}};
  const called=(name:string)=>{const db=new DatabaseSync(counterPath);try{db.prepare('INSERT INTO calls(kind) VALUES (?)').run(name);}finally{db.close();}counts[name]++;callback_log.push(name);};
  const definition=(version:number)=>defineTask({name:'fixture.orphan.too-old.generic',version,initial(){called('initial');return {phase:'run'};},phases:{run:async()=>{called('handler');throw Error('Target phase outside task-too-old profile');}},abort:async()=>{called('abort');throw Error('Target abort outside task-too-old profile');},migrate:()=>{called('migrate');throw Error('Target migration outside task-too-old profile');}});
  const task=definition(recipe.definition_versions.stored),availableTask=definition(recipe.definition_versions.available);
  const registry=createRegistry();registry.install(defineExtension({name:'fixture.orphan.too-old.extension',tasks:[availableTask]}));let h:any,storage:any,taskId:any,cid:any;
  async function owner(){
   const inner=c.backend==='memory'?new MemoryStorage():await openNodeSqliteStorage(path);
   storage=new Proxy(inner,{get(target,key){const value=Reflect.get(target,key);if(typeof value!=='function')return value;return async(...args:any[])=>{const before=key==='commit'?clone(args[0]):undefined;const result=await value.apply(target,args);if(key==='commit')batches.push({seq:result,writes:before});if(key==='mintId')minted.push(result);return result;};}});
   h=await Harness.open(storage,{models:createModels(),registry,settings:{restartLimit:5},now:()=>1000},ctx);owners.add(h);
  }
  async function end(){await h.close(ctx);closed.push((await h.closed).reason);owners.delete(h);}
  async function snapshot(label:string,ACK:any=null){
   const row=await h.readOnLine(async()=>({label,record:clone(await storage.task(taskId,ctx)),documents:{target:await documents(storage,{kind:'task',taskId}),conversation:await documents(storage,{kind:'conversation',conversationId:cid})},counts:clone(counts),callback_rows:ledger(),backend:c.backend==='sqlite'?sqlSnapshot(path):{kind:'memory-current-only',commit_batches:clone(batches),minted:clone(minted)},batch_count:batches.length,mint_count:minted.length,ACK}));
   assert.ok(row.record);steps.push(row);return row;
  }
  await owner();const conversation=await h.createConversation({ownership:{kind:'ownerless'}},ctx);cid=conversation.id;
  taskId=await h.commit(async(tx:any)=>{const id=await tx.createTask(task,{fixture:c.id},{ownership:{kind:'conversation'},conversationId:cid});(await tx.doc(NOTE,id)).n=1;(await tx.doc(CACHED,id)).n=2;(await tx.doc(OTHER,cid)).n=1;return id;},ctx);
  await h.commit(async(tx:any)=>{(await tx.doc(OTHER,cid)).n=2;},ctx);
  await h.commit(async(tx:any)=>{(await tx.doc(OTHER,cid)).n=1;},ctx);
  const created=(await snapshot('created')).record;
  if(c.parked){await h.commit(async(tx:any)=>{const record=await tx.task(taskId);assert.ok(record);tx.setTask({...record,restarts:5,memos:{'fixture.memo':{kept:true}}});},ctx);await snapshot('seeded-restarts');}
  if(c.cold){await end();await owner();await snapshot('unmarked-reopen');}
  const before=await snapshot('before-action'),inspection=await h.inspect(ctx);assert.equal(inspection.scheduling,'paused');
  const policy_observation={configured_limit:5,derived_parked:(before.record.restarts??0)>=5,inspection};
  if(c.parked){assert.equal(before.record.restarts,5);assert.equal(policy_observation.derived_parked,true);}
  const definition_observation=await h.readOnLine(async()=>{const record=await storage.task(taskId,ctx);assert.ok(record);const captured:any=registry.snapshot().task(record.kind);assert.ok(captured);return {available_version:captured.definition.version,stored_version:record.version,strictly_older:captured.definition.version<record.version};});
  assert.deepEqual(definition_observation,{available_version:recipe.definition_versions.available,stored_version:recipe.definition_versions.stored,strictly_older:true});
  const ACK=await h.abortTask(taskId,ctx);await snapshot('orphan',ACK);
  if(c.install){registry.install(defineExtension({name:'fixture.orphan.too-old.extension',tasks:[task]}));await snapshot('install-definition');}
  await snapshot('repeat',await h.abortTask(taskId,ctx));
  if(c.backend==='sqlite'){await end();await owner();await snapshot('terminal-reopen');await snapshot('repeat-after-reopen',await h.abortTask(taskId,ctx));}
  const final_backend=steps.at(-1).backend;await end();
  raw.push({id:c.id,role:'pi',backend_kind:c.backend,control:{actual_Task_Tx_creation:true,typed_restart_memo_seed:c.parked,physical_SIGKILL:false,fabricated_task_authority:false},created,definition_observation,steps,commit_batches:batches,minted,callback_log,callback_rows:ledger(),callback_sqlite:sqlSnapshot(counterPath),closed,sqlite_final:c.backend==='sqlite'?sqlSnapshot(path):null,final_backend,reference_only:{before_action_inspection:inspection,policy_observation,actual_Harness_hook:'installed settleSchedulerOutcome with source-pinned non-pi generic guard; not replaced or separately instrumented',initial_conversation_documents:'full actual baseline retained before action',Memory_history:'raw backend-specific current materialization/commit tape; no equality to native physical revisions claimed'},mutations:'Actual Task/doc creation and reversible conversation edit; optional typed counter/memo control; real Harness abortTask and stable repetition. No task/provider/tool callback or fabricated carrier; all physical backend differences retained.'});
 }catch(error){failure=error;throw error;}finally{const errors:any[]=[];for(const owner of owners){try{await owner.close(ctx);}catch(error){errors.push(error);}}if(errors.length)throw new AggregateError(failure===null?errors:[failure,...errors],'Orphan Harness cleanup failed');}
}
writeFileSync(out+'/raw-stored.json',JSON.stringify(raw));writeFileSync(out+'/resolutions.json',JSON.stringify(resolutions));
