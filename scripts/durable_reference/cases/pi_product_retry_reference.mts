/** Public FullHarness retry witness; real cut, no row seeds/private scheduling probes.
 * MIT(c)2025 Mario Zechner; fixture upstream license applies. */
import assert from 'node:assert/strict';
import {mkdirSync,readFileSync,writeFileSync,realpathSync} from 'node:fs';
import {registerHooks} from 'node:module';
import {fileURLToPath} from 'node:url';
import {sep} from 'node:path';
import {spawn} from 'node:child_process';
import {DatabaseSync} from 'node:sqlite';
import {isDeepStrictEqual} from 'node:util';
const root=realpathSync(process.cwd()),[inputPath,out,mode,childCase]=process.argv.slice(2);
const resolutions:any[]=[];
registerHooks({resolve(specifier,context,next){const result=next(specifier,context);if(!result.url.startsWith('node:')){const path=realpathSync(fileURLToPath(result.url));assert.ok(path.startsWith(root+sep));resolutions.push({specifier,path:path.slice(root.length+1)});}return result;}});
const {Harness,createRegistry,defineExtension,defineTask}=await import('./packages/durable/src/index.ts');
const {openNodeSqliteStorage}=await import('./packages/durable/src/storage/sqlite/node.ts');
const {createModels}=await import('@earendil-works/pi-ai');
const {BACKGROUND_CONTEXT:ctx}=await import('@earendil-works/chord/context');
const recipe=JSON.parse(readFileSync(inputPath,'utf8'));
assert.deepEqual(recipe,{schema_version:1,phase:'task-product-retry-enable-v1',cases:[{id:'genuine-parked-sqlite',parked:true,restart_limit:1},{id:'live-noop-sqlite',parked:false,restart_limit:1}]});
const clone=(x:any)=>structuredClone(x);
const deferred=()=>{let resolve:any,reject:any;const promise=new Promise<any>((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
function aborted(signal:AbortSignal){return new Promise<never>((_,reject)=>{if(signal.aborted)reject(signal.reason);else signal.addEventListener('abort',()=>reject(signal.reason),{once:true});});}
function sqlSnapshot(path:string){const db=new DatabaseSync(path,{readOnly:true});try{db.exec('PRAGMA query_only=ON; BEGIN');const value:any={};for(const {name}of db.prepare("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").all()as any[]){const quoted='"'+name.replaceAll('"','""')+'"';const columns=(db.prepare('PRAGMA table_info('+quoted+')').all()as any[]).map(x=>x.name);const rows=(db.prepare('SELECT * FROM '+quoted).all()as any[]).map(row=>columns.map(k=>row[k]instanceof Uint8Array?{sqlite_blob_hex:Buffer.from(row[k]).toString('hex')}:typeof row[k]==='bigint'?{sqlite_bigint:row[k].toString()}:row[k]));rows.sort((a,b)=>JSON.stringify(a).localeCompare(JSON.stringify(b)));value[name]={columns,rows};}return value;}finally{db.close();}}
function counter(path:string,kind?:string){const db=new DatabaseSync(path,{readOnly:kind===undefined});try{if(kind!==undefined){db.exec('CREATE TABLE IF NOT EXISTS calls(seq INTEGER PRIMARY KEY AUTOINCREMENT,kind TEXT NOT NULL)');db.prepare('INSERT INTO calls(kind) VALUES (?)').run(kind);return;}return db.prepare('SELECT seq,kind FROM calls ORDER BY seq').all();}finally{db.close();}}
async function cutActor(c:any,folder:string){
 const destination=out+'/processes';mkdirSync(destination,{recursive:true,mode:0o700});const stdout:string[]=[],stderr:string[]=[];
 const argv=[...process.execArgv,fileURLToPath(import.meta.url),inputPath,out,'--cut-child',c.id];
 const child=spawn(process.execPath,argv,{cwd:root,env:process.env,stdio:['pipe','pipe','pipe']});
 const ready=deferred(),closed=deferred();let buffer='',event:any;
 child.stdout.on('data',chunk=>{const text=chunk.toString();stdout.push(text);buffer+=text;while(buffer.includes('\n')){const at=buffer.indexOf('\n'),line=buffer.slice(0,at);buffer=buffer.slice(at+1);try{const actual=JSON.parse(line);if(actual.event==='cut-ready'){event=actual;ready.resolve(actual);}}catch(error){ready.reject(error);}}});
 child.stderr.on('data',chunk=>stderr.push(chunk.toString()));
 child.on('error',error=>ready.reject(error));
 child.on('close',(code,signal)=>{if(event===undefined)ready.reject(Error('Cut child ended before actual running callback'));closed.resolve({code,signal});});
 try{
  await ready.promise;assert.equal(event.record.state.status,'running');assert.equal(event.record.restarts,undefined);
  assert.ok(child.kill('SIGKILL'));const result=await closed.promise;assert.equal(result.signal,'SIGKILL');
  const receipt={argv:[process.execPath,...argv],cwd:root,pid:child.pid,pgid:null,pgid_qualification:'Not measured; child shares owned parent process domain, no detached child group inferred',exit_signal:result.signal,exit_code:result.code,reaped:true,ready:event,stdout:stdout.join(''),stderr:stderr.join('')};
  writeFileSync(destination+'/'+c.id+'-cut.json',JSON.stringify(receipt));return receipt;
 }finally{
  if(child.exitCode===null&&child.signalCode===null){child.kill('SIGKILL');await closed.promise;}
  child.stdin.destroy();writeFileSync(destination+'/'+c.id+'-cut.stdout',stdout.join(''));writeFileSync(destination+'/'+c.id+'-cut.stderr',stderr.join(''));
 }
}
async function owner(c:any,folder:string,cutChild:boolean){
 const path=folder+'/session.sqlite',counterPath=folder+'/callback-counter.sqlite';
 const gate=deferred(),entered=deferred(),reports:any[]=[],batches:any[]=[],events:any[]=[],resetRows:any[]=[];
 let taskId:any=null,retryStage=false,before:any;
 const called=(kind:string)=>counter(counterPath,kind);
 const task=defineTask({name:'fixture.product-retry.hold',version:1,initial:()=>({phase:'hold'}),phases:{hold:async(record:any,runtime:any,context:any)=>{
  called(cutChild?'cut-running':'retry-running');entered.resolve();
  if(cutChild){writeFileSync(folder+'/cut-created.json',JSON.stringify({task_id:record.id,record,modules:resolutions}));process.stdout.write(JSON.stringify({event:'cut-ready',record,modules:resolutions})+'\n');}
  await Promise.race([gate.promise,aborted(runtime.signal)]);
  await runtime.commit(()=>({status:'terminal',outcome:{status:'completed',result:null}}),context);called('commit-completed');
 }},abort:async()=>{throw Error('No explicit abort in public retry profile');}});
 const registry=createRegistry();registry.install(defineExtension({name:'fixture.product-retry.extension',tasks:[task]}));
 const inner=await openNodeSqliteStorage(path);
 const storage=new Proxy(inner,{get(target,key){const value=Reflect.get(target,key,target);if(typeof value!=='function')return value;return async(...args:any[])=>{const writes=key==='commit'?clone(args[0]):undefined;const result=await value.apply(target,args);if(key==='commit'){batches.push({writes,seq:result});if(retryStage&&taskId!==null){const actual=clone(await target.task(taskId,ctx));const expected=clone(before);delete expected.restarts;if(isDeepStrictEqual(actual,expected))resetRows.push(actual);}}return result;};}});
 const h=await Harness.open(storage,{models:createModels(),registry,settings:{restartLimit:c.restart_limit},onReport:(error:any,info:any)=>reports.push({error:{name:error.name,message:error.message,stack:error.stack},info:clone(info)})},ctx);
 const setTarget=(id:any)=>{taskId=id;};
 const create=async()=>{const conversation=await h.createConversation({ownership:{kind:'ownerless'}},ctx);return h.commit((tx:any)=>tx.createTask(task,{fixture:c.id},{ownership:{kind:'conversation'},conversationId:conversation.id}),ctx);};
 return {h,path,counterPath,gate,entered,reports,batches,events,resetRows,setTarget,create,arm(row:any){before=clone(row);retryStage=true;},disarm(){retryStage=false;}};
}
if(mode==='--cut-child'){
 const c=recipe.cases.find((x:any)=>x.id===childCase);assert.ok(c&&c.parked);const folder=out+'/stores/pi/'+c.id;mkdirSync(folder,{recursive:true,mode:0o700});
 // A live stdin pipe owns the cut actor's lifetime; no timer creates success.
 process.stdin.resume();const o=await owner(c,folder,true);
 const id=await o.create();o.setTarget(id);o.h.resume();await o.entered.promise;
 await new Promise(()=>{}); // Real running callback remains held until measured SIGKILL.

}else{
 const raw:any[]=[];
 for(const c of recipe.cases){
  const folder=out+'/stores/pi/'+c.id;mkdirSync(folder,{recursive:true,mode:0o700});let o:any,cut:any=null,failed:any;
  try{
   if(c.parked)cut=await cutActor(c,folder);
   o=await owner(c,folder,false);
   let taskId:any,initial:any;
   if(c.parked){const created=JSON.parse(readFileSync(folder+'/cut-created.json','utf8'));taskId=created.task_id;initial=created.record;}
   else{taskId=await o.create();initial=clone(await o.h.getTask(taskId,ctx));}
   o.setTarget(taskId);const before=clone(await o.h.getTask(taskId,ctx));
   const inspected=await o.h.inspect(ctx);assert.equal(inspected.scheduling,'paused');const inspection=clone(inspected.tasks.find((x:any)=>x.record.id===taskId));assert.ok(inspection);
   assert.equal(inspection.state.kind==='blocked'&&inspection.state.reason==='restart_limit',c.parked);
   if(c.parked){assert.equal(before.restarts,1);assert.ok(o.reports.some((x:any)=>x.info.type==='task_parked'));}
   const event=(name:string)=>o.events.push({seq:o.events.length+1,event:name});
   o.arm(before);const returned=await o.h.retryTask(taskId,ctx);o.disarm();assert.equal(returned,undefined);event('retry_return');
   await o.entered.promise;const atGate=clone(await o.h.getTask(taskId,ctx));assert.equal(atGate.state.status,'running');event('gate_read');
   const gate={retry_returned:true,callback_entered:true,released:false,result_settled:atGate.state.status==='terminal'};
   const resetAfter=c.parked?o.resetRows[0]:before;assert.ok(resetAfter,'Actual reset window not observed');
   // Only now may waitForTask auto-resume: retry already admitted held work.
   const completion=o.h.waitForTask(taskId,ctx);o.gate.resolve();event('release');const finished=await completion;event('complete');
   const after=clone(await o.h.getTask(taskId,ctx));await o.h.close(ctx);const closed=(await o.h.closed).reason;event('closed');
   raw.push({id:c.id,role:'pi',backend_kind:'sqlite',task_id:taskId,records:{initial,before_retry:before,at_gate:atGate,after_release:after},inspection_before:inspection,reset:{before,after:resetAfter,actual_commit_rows:o.resetRows},gate,events:o.events,cut,control:{typed_counter_seed:false,arbitrary_terminal_mutation:false},callback_rows:counter(o.counterPath),callback_sqlite:sqlSnapshot(o.counterPath),closed:[closed],sqlite_final:sqlSnapshot(o.path),reference_only:{retry_return:{kind:'void'},wait_started_before_gate:false,private_enabled_or_cache:'NOT_OBSERVED',actual_Harness_hook:'installed unchanged settleSchedulerOutcome',reports:o.reports,batches:o.batches,wait_result:clone(finished),modules:cut?.ready?.modules??[]}});
   o=null;
  }catch(error){failed=error;throw error;}finally{if(o){o.gate.resolve();try{await o.h.close(ctx);}catch(error){throw new AggregateError(failed===undefined?[error]:[failed,error],'Public Pi retry cleanup');}}}
 }
 writeFileSync(out+'/raw-stored.json',JSON.stringify(raw));writeFileSync(out+'/resolutions.json',JSON.stringify(resolutions));
}
