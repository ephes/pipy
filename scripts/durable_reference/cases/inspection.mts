/** DE10 genuine public Harness.inspect/getTask; native operator policy is not Pi API.
 * MIT (c)2025 Mario Zechner; see tests/fixtures/durable/UPSTREAM-LICENSE.txt.
 */
import assert from 'node:assert/strict';
import {readFileSync,writeFileSync,realpathSync,mkdirSync} from 'node:fs';
import {registerHooks} from 'node:module';
import {fileURLToPath} from 'node:url';
import {sep} from 'node:path';
const root=realpathSync(process.cwd()),[inputPath,out]=process.argv.slice(2),resolutions:any[]=[];
registerHooks({resolve(specifier,context,next){const result=next(specifier,context);if(!result.url.startsWith('node:')){const path=realpathSync(fileURLToPath(result.url));assert.ok(path.startsWith(root+sep));resolutions.push({specifier,path:path.slice(root.length+1)});}return result;}});
const {Harness,createRegistry,defineExtension,defineTask}=await import('./packages/durable/src/index.ts');
const {createModels}=await import('@earendil-works/pi-ai');
const {BACKGROUND_CONTEXT:ctx}=await import('@earendil-works/chord/context');
const {openNodeSqliteStorage}=await import('./packages/durable/src/storage/sqlite/node.ts');
const input=JSON.parse(readFileSync(inputPath,'utf8'));assert.equal(input.phase,'inspection-v1');
function hex(s:string){const b=Buffer.alloc(s.length*2);for(let i=0;i<s.length;i++)b.writeUInt16BE(s.charCodeAt(i),i*2);return b.toString('hex');}
function encode(v:any):any{if(v===null)return ['null'];if(typeof v==='boolean')return ['bool',v];if(typeof v==='string')return ['string',hex(v)];if(typeof v==='number'){assert.ok(Number.isFinite(v));const b=Buffer.alloc(8);b.writeDoubleBE(v);return ['number',b.toString('hex')];}if(Array.isArray(v))return ['array',v.map(encode)];assert.ok(v!==undefined&&Object.getPrototypeOf(v)===Object.prototype);return ['object',Object.keys(v).map(k=>[hex(k),encode(v[k])]).sort((a,b)=>a[0]<b[0]?-1:a[0]>b[0]?1:0)];}
function same(a:any,b:any){return JSON.stringify(encode(a))===JSON.stringify(encode(b));}
mkdirSync(out+'/stores/pi',{recursive:true,mode:0o700});
const cases:any[]=[],raw:any[]=[];
for(const recipe of input.cases){
 let poisoned=false,code=0;
 function task(name:string,version:number){return defineTask({name,version,initial:()=>{code++;assert.equal(poisoned,false);return {phase:'execute',private:'synthetic-frame-sentinel'};},phases:{execute:async()=>{code++;throw new Error('inspection executed phase');}},abort:async()=>{code++;throw new Error('inspection executed abort');}});}
 const registry=createRegistry();const registered=recipe.tasks.filter((r:any)=>r.current!==null).map((r:any)=>task('fixture.'+r.label,r.current));registry.install(defineExtension({name:'fixture.inspection',tasks:registered}));
 const path=out+'/stores/pi/'+recipe.id+'.sqlite';
 async function open(){return Harness.open(await openNodeSqliteStorage(path),{models:createModels(),registry,settings:{retry:{enabled:false},compaction:{enabled:false}}},ctx);}
 let harness=await open();const conversation=await harness.createConversation({ownership:{kind:'ownerless'}},ctx);
 const ids:any={};
 for(const item of recipe.tasks){const token=task('fixture.'+item.label,item.stored);ids[item.label]=await harness.commit((tx:any)=>tx.createTask(token,{private:'synthetic-input-sentinel'},{ownership:{kind:'conversation'},conversationId:conversation.id}),ctx);if(item.terminal)await harness.commit(async(tx:any)=>{const record=await tx.task(ids[item.label]);tx.setTask({...record,state:{status:'terminal',outcome:{status:'aborted',reason:'synthetic-private-reason'}}});},ctx);}
 const before:any={};for(const [label,id]of Object.entries(ids))before[label]=await harness.getTask(id as any,ctx);
 const codeBefore=code;poisoned=true;
 const reports=[await harness.inspect(ctx),await harness.inspect(ctx)];
 const after:any={};for(const [label,id]of Object.entries(ids))after[label]=await harness.getTask(id as any,ctx);
 const current:any={};for(const [label,record]of Object.entries(after) as any){const found=registry.snapshot().task(record.kind);current[label]=found===undefined?null:found.definition.version;}
 const labels=new Map(Object.entries(ids).map(([label,id])=>[id,label]));
 const reasons:any={missing_task:'definition_missing',task_too_old:'stored_version_newer',migration_failed:'definition_version_mismatch'};
 const observed={live:reports[0].tasks.map((t:any)=>labels.get(t.record.id)),tasks:Object.entries(after).map(([label,r]:any)=>{const state=reports[0].tasks.find((t:any)=>t.record.id===r.id)?.state;return {label,version:r.version,status:r.state.status,phase:r.state.status==='terminal'?null:r.state.checkpoint.phase,outcome:r.state.status==='terminal'?r.state.outcome.status:null,fit:r.state.status==='terminal'?null:state.kind==='ready'&&!state.migrates,reason:r.state.status==='terminal'?null:state.kind==='blocked'?reasons[state.reason]:null,same_conversation:r.conversationId===conversation.id,record_unchanged:same(before[label],r)};}),code_runs_delta:code-codeBefore,read_count:Object.values(after).filter(Boolean).length,repeat_equal:same(reports[0].tasks.map((t:any)=>({record:t.record,state:{...t.state,error:t.state.error===undefined?null:{message:t.state.error.message}}})),reports[1].tasks.map((t:any)=>({record:t.record,state:{...t.state,error:t.state.error===undefined?null:{message:t.state.error.message}}}))),extra_live:reports[0].tasks.filter((t:any)=>!labels.has(t.record.id)).length};
 assert.ok(reports.every((r:any)=>r.scheduling==='paused'));assert.equal(code-codeBefore,0);
 raw.push({id:recipe.id,ids,conversation:conversation.id,current,before,after,reports:reports.map((r:any)=>({...r,tasks:r.tasks.map((t:any)=>({...t,state:{...t.state,...(t.state.error===undefined?{}:{error:{message:t.state.error.message}})}}))})),codeBefore,codeAfter:code});
 cases.push({id:recipe.id,observed:encode(observed)});await harness.close(ctx);
}
writeFileSync(out+'/observed.json',JSON.stringify({schema_version:1,phase:input.phase,cases}));writeFileSync(out+'/raw-stored.json',JSON.stringify(raw));writeFileSync(out+'/resolutions.json',JSON.stringify(resolutions));
