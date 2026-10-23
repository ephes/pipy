/** DE05a: actual pinned defineTask -> Transaction.createTask -> SessionImpl.
 * MIT (c)2025 Mario Zechner; see tests/fixtures/durable/UPSTREAM-LICENSE.txt. */
import assert from 'node:assert/strict';
import {readFileSync,writeFileSync,realpathSync} from 'node:fs';
import {registerHooks} from 'node:module';
import {fileURLToPath} from 'node:url';
import {sep} from 'node:path';
const root=realpathSync(process.cwd()), [inputPath,out]=process.argv.slice(2), resolutions:any[]=[];
registerHooks({resolve(specifier,context,next){const result=next(specifier,context);if(!result.url.startsWith('node:')){const path=realpathSync(fileURLToPath(result.url));assert.ok(path.startsWith(root+sep));resolutions.push({specifier,path:path.slice(root.length+1)});}return result;}});
const {SessionImpl}=await import('./packages/durable/src/session/session.ts');
const {MemoryStorage}=await import('./packages/durable/src/storage/memory.ts');
const {defineTask}=await import('./packages/durable/src/tasks.ts');
const {BACKGROUND_CONTEXT:ctx}=await import('@earendil-works/chord/context');
const input=JSON.parse(readFileSync(inputPath,'utf8'));assert.equal(input.phase,'task-creation-a-v1');
function hex(s:string){const b=Buffer.alloc(s.length*2);for(let i=0;i<s.length;i++)b.writeUInt16BE(s.charCodeAt(i),i*2);return b.toString('hex');}
function encode(v:any):any{if(v===null)return ['null'];if(typeof v==='boolean')return ['bool',v];if(typeof v==='string')return ['string',hex(v)];if(typeof v==='number'){assert.ok(Number.isFinite(v));const b=Buffer.alloc(8);b.writeDoubleBE(v);return ['number',b.toString('hex')];}if(Array.isArray(v))return ['array',v.map(encode)];assert.ok(v!==undefined&&Object.getPrototypeOf(v)===Object.prototype);return ['object',Object.keys(v).map(k=>[hex(k),encode(v[k])]).sort((a,b)=>a[0]<b[0]?-1:a[0]>b[0]?1:0)];}
const cases:any[]=[], raw:any[]=[];
for(const recipe of input.cases){
 const memory=new MemoryStorage(),calls:string[]=[],writes:any[]=[],publications:any[]=[];
 const owner={id:20,conversationId:7,kind:'owner',version:1,input:null,background:false,abortRequested:false,state:{status:'pending',checkpoint:null}};
 await memory.commit([{type:'conversation',value:{id:1}},{type:'conversation',value:{id:7}},{type:'task',value:owner}],ctx);
 const names:any={mintId:'mint',conversation:'conversation',task:'task',commit:'commit'};
 const storage=new Proxy(memory,{get(target,key){const fn=Reflect.get(target,key);if(typeof fn!=='function')return fn;return async(...args:any[])=>{if(names[String(key)])calls.push(names[String(key)]);if(key==='commit')writes.push(...structuredClone(args[0]));return fn.apply(target,args);};}});
 class Driver extends SessionImpl{report(error:any){throw error;}}
 const session=new Driver(storage as any,()=>1000,()=>600000,()=>undefined);
 session.observeCommits((p:any)=>publications.push(p.changes));
 const failure=new Error('initial failed');let status='fulfilled',error:any=null,id:any=null;
 const caller:any={items:[1],zero:-0};
 const definition=defineTask({name:'typed.work',version:recipe.version,initial(value:any){calls.push('initial');if(recipe.id==='initializer-throw')throw failure;if(recipe.id==='initial-mutation')value.items.push(2);return recipe.id==='unregistered-phase'?null:{phase:'start',value};},phases:{start:async()=>{throw Error('must not dispatch');}},abort:async()=>{throw Error('must not dispatch');}});
 try{
  id=await session.commit((tx:any)=>tx.createTask(definition,caller,{ownership:recipe.id==='child'||recipe.id==='background-child'?{kind:'task',taskId:20}:recipe.id==='missing-owner'?{kind:'task',taskId:999}:{kind:'conversation'},...(['child','background-child','missing-owner'].includes(recipe.id)?{}:{conversationId:recipe.id==='missing-conversation'?999:7}),...(recipe.id==='background-child'?{background:true}:{})}),ctx);
 }catch(e:any){status='rejected';error=e===failure?'initializer':e.message;raw.push({id:recipe.id,name:e.name,message:e.message,stack:e.stack});}
 const stored=id===null?null:await memory.task(id,ctx);
 cases.push({id:recipe.id,status,error,result:encode(id),calls,stored:encode(stored),writes:encode(writes),publications:encode(publications)});
 await session.close(ctx);
}
writeFileSync(out+'/observed.json',JSON.stringify({schema_version:1,phase:input.phase,cases}));
writeFileSync(out+'/resolutions.json',JSON.stringify(resolutions));
writeFileSync(out+'/raw-errors.json',JSON.stringify(raw));
