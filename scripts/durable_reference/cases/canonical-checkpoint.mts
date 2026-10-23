/** DE06b: actual pinned Harness/TaskScheduler execution.
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
const {openNodeSqliteStorage}=await import('./packages/durable/src/storage/sqlite/node.ts');
const {mkdirSync}=await import('node:fs');
const input=JSON.parse(readFileSync(inputPath,'utf8'));assert.equal(input.phase,'canonical-checkpoint-b-v1');
function hex(s:string){const b=Buffer.alloc(s.length*2);for(let i=0;i<s.length;i++)b.writeUInt16BE(s.charCodeAt(i),i*2);return b.toString('hex');}
function encode(v:any):any{if(v===null)return ['null'];if(typeof v==='boolean')return ['bool',v];if(typeof v==='string')return ['string',hex(v)];if(typeof v==='number'){assert.ok(Number.isFinite(v));const b=Buffer.alloc(8);b.writeDoubleBE(v);return ['number',b.toString('hex')];}if(Array.isArray(v))return ['array',v.map(encode)];assert.ok(v!==undefined&&Object.getPrototypeOf(v)===Object.prototype);return ['object',Object.keys(v).map(k=>[hex(k),encode(v[k])]).sort((a,b)=>a[0]<b[0]?-1:a[0]>b[0]?1:0)];}
const cases:any[]=[],raw:any[]=[];
mkdirSync(out+'/stores/pi',{recursive:true,mode:0o700});
for(const recipe of input.cases){
 const phases:any[]=[]; const path=out+'/stores/pi/'+recipe.id+'.sqlite';
 const task=defineTask({name:'canonical.pair',version:-1.5,initial:(value:any)=>({phase:'start',value}),phases:{
  start:async(task:any,runtime:any,context:any)=>{phases.push(structuredClone(task.state.checkpoint.value));await runtime.commit(()=>({status:'running',checkpoint:{phase:'settle',value:task.state.checkpoint.value}}),context);},
  settle:async(task:any,runtime:any,context:any)=>{phases.push(structuredClone(task.state.checkpoint.value));await runtime.commit(()=>({status:'terminal',outcome:{status:'completed',result:task.state.checkpoint.value}}),context);}
 },abort:async()=>{throw Error('abort not selected');}});
 const registry=createRegistry();registry.install(defineExtension({name:'fixture.canonical',tasks:[task]}));
 let storage=await openNodeSqliteStorage(path);
 const harness=await Harness.open(storage as any,{models:createModels(),registry,now:()=>1000},ctx);
 const value={payload:recipe.payload,identity:recipe.identity,settings:recipe.settings};
 const conversation=await harness.commit((tx:any)=>tx.createConversation({ownership:{kind:'ownerless'}}),ctx);
 const id=await harness.commit((tx:any)=>tx.createTask(task,value,{ownership:{kind:'conversation'},conversationId:conversation.id}),ctx);
 const before_calls=phases.length;
 harness.resume();await harness.waitForTask(id,ctx);
 const stored=await harness.getTask(id,ctx);const result=stored.state.outcome.result;
 raw.push({id:recipe.id,stored});
 await harness.close(ctx);
 storage=await openNodeSqliteStorage(path);
 const again=await Harness.open(storage as any,{models:createModels(),registry,now:()=>1000},ctx);
 const saved=await again.getTask(id,ctx);const reopened=saved.state.outcome.result;
 assert.equal(phases.length,2);await again.close(ctx);
 cases.push({id:recipe.id,observed:encode({before_calls,phases,result,reopened})});
}
writeFileSync(out+'/observed.json',JSON.stringify({schema_version:1,phase:input.phase,cases}));
writeFileSync(out+'/resolutions.json',JSON.stringify(resolutions));
writeFileSync(out+'/raw-stored.json',JSON.stringify(raw));
