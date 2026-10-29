/** Actual public held-completion ordinary ownership witness. MIT(c)2025 Mario Zechner; fixture license applies. */
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

const clone=(x:any)=>structuredClone(x);
const recipe=JSON.parse(readFileSync(inputPath,'utf8'));
assert.deepEqual(recipe,{
  "schema_version": 1,
  "phase": "task-ordinary-owned-live-v1",
  "cases": [
    {
      "id": "memory-background-first",
      "backend": "memory",
      "cold": false,
      "first": "Z"
    },
    {
      "id": "sqlite-foreground-first",
      "backend": "sqlite",
      "cold": false,
      "first": "D"
    },
    {
      "id": "sqlite-cold-ended-bridge",
      "backend": "sqlite",
      "cold": true,
      "first": "Z"
    }
  ]
}
);
const deferred=()=>{let resolve:any,reject:any;const promise=new Promise<any>((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
function aborted(signal:AbortSignal){return new Promise<never>((_,reject)=>{if(signal.aborted)reject(signal.reason);else signal.addEventListener('abort',()=>reject(signal.reason),{once:true});});}
async function scan(method:any,query:any){const rows:any[]=[];let cursor:any;do{const page=await method(query,128,cursor,ctx);rows.push(...page.items.map(clone));cursor=page.next;}while(cursor!==undefined);return rows;}
const raw:any[]=[];
for(const c of recipe.cases){
 const folder=out+'/stores/pi/'+c.id;mkdirSync(folder,{recursive:true,mode:0o700});
 const path=folder+'/session.sqlite',counterPath=folder+'/callback-counter.sqlite';
 {const db=new DatabaseSync(counterPath);try{db.exec('CREATE TABLE calls(seq INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL)');}finally{db.close();}}
 const owners=new Set<any>(),watches=new Set<any>();let failure:any;
 const gates=new Map<string,any>(),acks=new Map<string,any>(),starts=new Map<string,any>();
 const need=(map:Map<string,any>,name:string)=>{if(!map.has(name))map.set(name,deferred());return map.get(name);};
 const callback_log:string[]=[],batches:any[]=[],minted:any[]=[],closed:string[]=[],lifecycle:any[]=[],frames:any[]=[],commit_acks:string[]=[],reports:any[]=[];
 const ledger=()=>{const db=new DatabaseSync(counterPath,{readOnly:true});try{return db.prepare('SELECT seq,kind FROM calls ORDER BY seq').all();}finally{db.close();}};
 const called=(kind:string)=>{const db=new DatabaseSync(counterPath);try{db.prepare('INSERT INTO calls(kind) VALUES (?)').run(kind);}finally{db.close();}callback_log.push(kind);};
 const task=defineTask({name:'fixture.ordinary-owned-live.hold',version:1,
  initial(value:any){called('initial:'+value.name);return {phase:'hold'};},
  phases:{hold:async(record:any,runtime:any,context:any)=>{const name=record.input.name;called('run:'+name);need(starts,name).resolve();await Promise.race([need(gates,name).promise,aborted(runtime.signal)]);await runtime.commit(()=>({status:'terminal',outcome:{status:'completed',result:null}}),context);called('ack:'+name);commit_acks.push(name);need(acks,name).resolve();}},
  abort:async()=>{called('abort:unexpected');throw Error('No abort in bounded ownership completion witness');}});
 const registry=createRegistry();registry.install(defineExtension({name:'fixture.ordinary-owned-live.extension',tasks:[task]}));
 let h:any,storage:any,ids:any;
 async function open(){const inner=c.backend==='memory'?new MemoryStorage():await openNodeSqliteStorage(path);
  storage=new Proxy(inner,{get(target,key){const value=Reflect.get(target,key);if(typeof value!=='function')return value;return async(...args:any[])=>{const before=key==='commit'?clone(args[0]):undefined;const result=await value.apply(target,args);if(key==='commit')batches.push({seq:result,writes:before});if(key==='mintId')minted.push(result);return result;};}});
  h=await Harness.open(storage,{models:createModels(),registry,onReport:(error:any,info:any)=>reports.push({error:{name:error.name,message:error.message,stack:error.stack},info:clone(info)})},ctx);owners.add(h);
 }
 async function end(){await h.close(ctx);const reason=(await h.closed).reason;closed.push(reason);owners.delete(h);return reason;}
 async function snapshot(){return h.commit(async()=>{const tasks=await scan(storage.scanTasks.bind(storage),{}),conversations=await scan(storage.scanConversations.bind(storage),{}),documents:any[]=[];
  for(const conversation of conversations){for(const record of await scan(storage.scanDocuments.bind(storage),{scope:{kind:'conversation',conversationId:conversation.id},at:'current'}))documents.push({record,materialized:clone(await storage.document(record.id,'current',ctx))});}
  return {tasks,conversations,documents};},ctx);}
 async function step(label:string){return {label,snapshot:await snapshot(),callbacks:ledger(),batch_count:batches.length,mint_count:minted.length,backend:c.backend==='sqlite'?sqlSnapshot(path):null,query:null};}
 async function release(name:string){need(gates,name).resolve();const row=await h.waitForTask(ids[name],ctx);assert.deepEqual(row.state.outcome,{status:'completed',result:null});return row;}
 try{
  await open();const root=await h.root(ctx);
  ids=await root.commit(async(tx:any)=>{const p=await tx.createTask(task,{name:'P'},{ownership:{kind:'conversation'}}),b=await tx.createTask(task,{name:'B'},{ownership:{kind:'task',taskId:p}}),a=await tx.createTask(task,{name:'A'},{ownership:{kind:'task',taskId:b}}),owned=await tx.createConversation({ownership:{kind:'task',taskId:a}}),z=await tx.createTask(task,{name:'Z'},{ownership:{kind:'conversation'},conversationId:owned.id,background:true});return {root:root.id,P:p,B:b,A:a,C:owned.id,Z:z};},ctx);
  h.resume();await need(starts,'Z').promise;await release('A');await release('B');
  if(c.cold){const before=await snapshot();lifecycle.push({action:'close',reason:await end(),before});await open();lifecycle.push({action:'reopen',after:await snapshot()});}
  const owned=await h.conversation(ids.C,ctx);assert.ok(owned);
  ids.D=await owned.commit((tx:any)=>tx.createTask(task,{name:'D'},{ownership:{kind:'conversation'}}),ctx);
  h.resume();await need(starts,'D').promise;
  const held=deferred(),watch=await h.watchTaskGraph(ctx);watches.add(watch);const watch_initial=clone(watch.value);
  watch.start(async(value:any)=>{const actual=clone(value);frames.push(actual);const state=actual.tasks[String(ids.P)]?.state;if(state?.status==='completing'&&state.outcome==='completed')held.resolve(actual);});
  need(gates,'P').resolve();await need(acks,'P').promise;const held_frame=await held.promise;
  const steps=[await step('held')];assert.equal((await h.getTask(ids.P,ctx)).state.status,'completing');
  await release(c.first);
  if(c.first==='D')await h.waitForTask(ids.P,ctx);
  steps.push(await step('after-first'));
  await release(c.first==='Z'?'D':'Z');const released=await h.waitForTask(ids.P,ctx);
  steps.push(await step('released'));await watch.stop();await watch.closed;watches.delete(watch);
  const final_backend={snapshot:await snapshot(),batches:clone(batches),minted:clone(minted),callbacks:ledger(),backend:c.backend==='sqlite'?sqlSnapshot(path):null};
  await end();
  raw.push({id:c.id,role:'pi',backend_kind:c.backend,control:{actual_Task_Tx_creation:true,native_direct_Tx_terminalization:false,physical_SIGKILL:false},topology:ids,steps,commit_batches:batches,minted,callback_log,callback_rows:ledger(),callback_sqlite:sqlSnapshot(counterPath),closed,sqlite_final:c.backend==='sqlite'?sqlSnapshot(path):null,final_backend,
   reference_only:{held_frame,watch_initial,frames,commit_acks,release_outcome:clone(released.state.outcome),lifecycle,reports,private_owned_live:'NOT_OBSERVED',actual_Harness_hook:'installed unchanged settleSchedulerOutcome; no replacement/instrumentation',observation:'Public task graph completing frame + actual runtime.commit ACK + coherent read-only public commit/getTask; actual waitForTask terminal release, no global idle'},
   mutations:'Actual public creation/execution/normal outcomes; coldclose/reopen where declared, no direct Pi terminalization or private cache observation'});
 }catch(error){failure=error;throw error;}finally{
  for(const gate of gates.values())gate.resolve();
  const errors:any[]=[];for(const watch of watches){try{await watch.stop();await watch.closed;}catch(error){errors.push(error);}}
  for(const owner of owners){try{await owner.close(ctx);}catch(error){errors.push(error);}}
  if(errors.length)throw new AggregateError(failure===undefined?errors:[failure,...errors],'Ownership fixture cleanup failed');
 }
}
writeFileSync(out+'/raw-stored.json',JSON.stringify(raw));writeFileSync(out+'/resolutions.json',JSON.stringify(resolutions));
