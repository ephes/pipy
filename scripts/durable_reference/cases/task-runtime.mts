/** DE05b: actual pinned Harness/TaskScheduler execution.
 * MIT (c)2025 Mario Zechner; see tests/fixtures/durable/UPSTREAM-LICENSE.txt. */
import assert from 'node:assert/strict';
import {readFileSync,writeFileSync,realpathSync} from 'node:fs';
import {registerHooks} from 'node:module';
import {fileURLToPath} from 'node:url';
import {sep} from 'node:path';
const root=realpathSync(process.cwd()), [inputPath,out]=process.argv.slice(2), resolutions:any[]=[];
registerHooks({resolve(specifier,context,next){const result=next(specifier,context);if(!result.url.startsWith('node:')){const path=realpathSync(fileURLToPath(result.url));assert.ok(path.startsWith(root+sep));resolutions.push({specifier,path:path.slice(root.length+1)});}return result;}});
const {Harness}=await import('./packages/durable/src/harness/harness.ts');
const {createRegistry}=await import('./packages/durable/src/harness/registry.ts');
const {defineExtension}=await import('./packages/durable/src/harness/define.ts');
const {MemoryStorage}=await import('./packages/durable/src/storage/memory.ts');
const {defineTask}=await import('./packages/durable/src/tasks.ts');
const {createModels}=await import('@earendil-works/pi-ai');
const {BACKGROUND_CONTEXT:ctx}=await import('@earendil-works/chord/context');
const input=JSON.parse(readFileSync(inputPath,'utf8'));assert.equal(input.phase,'task-runtime-b-v1');
function hex(s:string){const b=Buffer.alloc(s.length*2);for(let i=0;i<s.length;i++)b.writeUInt16BE(s.charCodeAt(i),i*2);return b.toString('hex');}
function encode(v:any):any{if(v===null)return ['null'];if(typeof v==='boolean')return ['bool',v];if(typeof v==='string')return ['string',hex(v)];if(typeof v==='number'){assert.ok(Number.isFinite(v));const b=Buffer.alloc(8);b.writeDoubleBE(v);return ['number',b.toString('hex')];}if(Array.isArray(v))return ['array',v.map(encode)];assert.ok(v!==undefined&&Object.getPrototypeOf(v)===Object.prototype);return ['object',Object.keys(v).map(k=>[hex(k),encode(v[k])]).sort((a,b)=>a[0]<b[0]?-1:a[0]>b[0]?1:0)];}
const cases:any[]=[], raw:any[]=[];
for(const recipe of input.cases){
 const name=recipe.id, memory=new MemoryStorage();
 const row:any={id:20,conversationId:1,kind:'typed.work',version:1,input:{to:2},background:false,abortRequested:false,state:{status:name==='recovery'?'running':'pending',checkpoint:{phase:'start',n:name==='recovery'?1:0}}};
 if(name==='recovery')Object.assign(row,{restarts:2,memos:{stable:null},startedAt:1000});
 await memory.commit([{type:'conversation',value:{id:1}},{type:'task',value:row}],ctx);
 let stored:any=null,late=false;const phases:number[]=[],memos:any[]=[],runtimes=new Set();
 let ready!:()=>void;const reached=new Promise<void>(r=>ready=r);
 const storage=new Proxy(memory,{get(target,key){const fn=Reflect.get(target,key);if(typeof fn!=='function')return fn;return async(...args:any[])=>{if(key==='close')stored=structuredClone(await target.task(20,ctx));return fn.apply(target,args);};}});
 const task=defineTask({name:'typed.work',version:1,initial(){throw Error('initializer reran');},phases:{start:async(task:any,runtime:any,context:any)=>{
  phases.push(task.state.checkpoint.n);runtimes.add(runtime);
  memos.push(await runtime.memo('stable',null,context));memos.push(await runtime.memo('stable',999,context));
  if(name==='throws')throw Error('boom');
  if(name==='no-progress'){await runtime.commit(()=>({status:'running',checkpoint:{n:0,phase:'start'}}),context);return;}
  if(name==='close'){await runtime.commit(()=>({status:'running',checkpoint:{phase:'start',n:1}}),context);ready();await new Promise<void>(resolve=>{if(runtime.signal.aborted)resolve();else runtime.signal.addEventListener('abort',()=>resolve(),{once:true});});return;}
  const n=task.state.checkpoint.n+1;
  await runtime.commit(()=>n===2||name==='terminal-late'?{status:'terminal',outcome:{status:'completed',result:n}}:{status:'running',checkpoint:{phase:'start',n}},context);
  if(name==='terminal-late'){try{await runtime.memo('late',1,context);}catch{late=true;}throw Error('after terminal');}
 }},abort:async()=>{throw Error('abort not selected');}});
 const registry=createRegistry();registry.install(defineExtension({name:'fixture.tasks',tasks:[task]}));
 const harness=await Harness.open(storage as any,{models:createModels(),registry,now:()=>1000,onReport:(error:any)=>raw.push({id:name,name:error.name,message:error.message,stack:error.stack})},ctx);
 const before=await harness.getTask(20,ctx),before_calls=phases.length;
 harness.resume();harness.resume();
 if(name==='close')await reached;else await harness.waitForTask(20,ctx);
 await harness.close(ctx);
 cases.push({id:name,observed:encode({before,before_calls,phases,memos,runtimes:runtimes.size,late_refused:late,stored,closed:(await harness.closed).reason})});
}
writeFileSync(out+'/observed.json',JSON.stringify({schema_version:1,phase:input.phase,cases}));
writeFileSync(out+'/resolutions.json',JSON.stringify(resolutions));
writeFileSync(out+'/raw-errors.json',JSON.stringify(raw));
