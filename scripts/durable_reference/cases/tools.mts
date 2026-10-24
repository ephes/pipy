/** DE08: actual pinned Harness tool tasks and recovery intersection.
 * MIT (c)2025 Mario Zechner; see tests/fixtures/durable/UPSTREAM-LICENSE.txt.
 * The common view covers the effect/replay prefix and completed logical rounds.
 * Native process/worker/lock proofs remain distinct from Pi observations.
 */
import assert from 'node:assert/strict';
import {readFileSync, writeFileSync, realpathSync, mkdirSync} from 'node:fs';
import {registerHooks} from 'node:module';
import {fileURLToPath} from 'node:url';
import {sep} from 'node:path';
const root=realpathSync(process.cwd()), [inputPath,out]=process.argv.slice(2), resolutions:any[]=[];
registerHooks({resolve(specifier,context,next){const result=next(specifier,context);if(!result.url.startsWith('node:')){const path=realpathSync(fileURLToPath(result.url));assert.ok(path.startsWith(root+sep));resolutions.push({specifier,path:path.slice(root.length+1)});}return result;}});
const {Harness}=await import('./packages/durable/src/harness/harness.ts');
const {createRegistry}=await import('./packages/durable/src/harness/registry.ts');
const {defineExtension,defineTool}=await import('./packages/durable/src/harness/define.ts');
const {randomUUID}=await import('node:crypto');
const {UsageDoc}=await import('./packages/durable/src/harness/usage.ts');
const {createModels,fauxProvider,fauxAssistantMessage,fauxToolCall,calculateCost,Type}=await import('@earendil-works/pi-ai');
const {BACKGROUND_CONTEXT:ctx}=await import('@earendil-works/chord/context');
const {openNodeSqliteStorage}=await import('./packages/durable/src/storage/sqlite/node.ts');
const input=JSON.parse(readFileSync(inputPath,'utf8'));assert.equal(input.phase,'tool-execution-b-v1');
function hex(s:string){const b=Buffer.alloc(s.length*2);for(let i=0;i<s.length;i++)b.writeUInt16BE(s.charCodeAt(i),i*2);return b.toString('hex');}
function encode(v:any):any{if(v===null)return ['null'];if(typeof v==='boolean')return ['bool',v];if(typeof v==='string')return ['string',hex(v)];if(typeof v==='number'){assert.ok(Number.isFinite(v));const b=Buffer.alloc(8);b.writeDoubleBE(v);return ['number',b.toString('hex')];}if(Array.isArray(v))return ['array',v.map(encode)];assert.ok(v!==undefined&&Object.getPrototypeOf(v)===Object.prototype);return ['object',Object.keys(v).map(k=>[hex(k),encode(v[k])]).sort((a,b)=>a[0]<b[0]?-1:a[0]>b[0]?1:0)];}
const cases:any[]=[],raw:any[]=[];
mkdirSync(out+'/stores/pi',{recursive:true,mode:0o700});
mkdirSync(out+'/pi-effects',{recursive:true,mode:0o700});
function total(values:any[]){const sum:any={};for(const value of values)for(const [key,item] of Object.entries(value)){if(key==='cost'){const target=sum.cost??={};for(const [name,amount]of Object.entries(item as any))target[name]=(target[name]??0)+(amount as number);}else sum[key]=(sum[key]??0)+(item as number);}return sum;}
function usageMatches(values:any[],aggregate:any){return JSON.stringify(encode(total(values)))===JSON.stringify(encode(aggregate));}
for(const recipe of input.cases){
 const path=out+'/stores/pi/'+recipe.id+'.sqlite';
 const effectPath=out+'/pi-effects/'+recipe.id+'.json';
 const effects:any={calls:[],applications:{},prepares:[]};
 const persist=()=>writeFileSync(effectPath,JSON.stringify(effects));persist();
 let held=recipe.id!=='loop',workerExited=false,startedResolve:any;
 const started=new Promise(resolve=>{startedResolve=resolve;});
 let harness:any,conversation:any;const requests:any[]=[],publications:any[]=[];
 const faux=fauxProvider({api:'openai-completions',provider:'openai',models:[{id:'gpt-test',cost:{input:1,output:2,cacheRead:3,cacheWrite:4}}]});
 const baseModels=createModels();baseModels.setProvider(faux.provider);
 const models=new Proxy(baseModels,{get(target,property){
  if(property==='streamSimple')return(_model:any,transcript:any,options:any)=>{
   const final=(async()=>{
    const tasks=await harness.commit((tx:any)=>tx.scanTasks({},100),ctx);
    const parent=tasks.items.find((task:any)=>task.kind==='pi.generation'&&task.state.status==='running'&&task.state.checkpoint.phase==='request');
    const entries=await harness.commit((tx:any)=>tx.scanEntries({conversationId:parent.conversationId},100),ctx);
    requests.push({messages:structuredClone(transcript.messages),task:parent.id,attempt:parent.state.checkpoint.attempt,sessionId:options.sessionId,cutoff:parent.state.checkpoint.cutoff,latest:Math.max(...entries.items.map((e:any)=>e.id))});
    const names=requests.length===1?recipe.names:[];
    const usage:any={input:16,output:10,cacheRead:4,cacheWrite:0,totalTokens:30,cost:{input:0,output:0,cacheRead:0,cacheWrite:0,total:0}};
    if(Object.hasOwn(recipe,'reasoning'))usage.reasoning=recipe.reasoning;
    calculateCost(faux.getModel(),usage);
    return {...fauxAssistantMessage([{type:'text',text:'done'},...names.map((name:string,index:number)=>fauxToolCall(name,{x:1},{id:'call:'+index}))],{stopReason:names.length?'toolUse':'stop',timestamp:1000}),api:'openai-completions',provider:'openai',model:'gpt-test',usage};
   })();return {[Symbol.asyncIterator]:async function*(){},result:()=>final};
  };
  const value=Reflect.get(target,property,target);return typeof value==='function'?value.bind(target):value;
 }});
 function registry(policy:string,missing=false){
  const registry=createRegistry();
  if(!missing)registry.install(defineExtension({name:'fixture.tools',tools:['keyed','later'].map(name=>defineTool({name,description:name,parameters:Type.Object({x:Type.Integer(),prepared:Type.Optional(Type.Boolean())}),replay:policy as any,
   prepareArguments(args:any){effects.prepares.push(name);persist();return {...args,prepared:true};},
   async execute(args:any,api:any,callContext:any){
    const key=await api.memo('fixture.key','pi-tool-'+randomUUID(),callContext);
    const record=await api.getTask(api.taskId,callContext);
    const intent=record.state.checkpoint.phase==='execute'&&JSON.stringify(encode(record.state.checkpoint.arguments))===JSON.stringify(encode(args));
    const observed={name,key,args:structuredClone(args),intent,task:api.taskId,ended:false};effects.calls.push(observed);
    try{
    const previous=effects.applications[key];assert.ok(previous===undefined||JSON.stringify(encode(previous))===JSON.stringify(encode(args)));
    effects.applications[key]=structuredClone(args);persist();
    if(held){held=false;startedResolve();try{await new Promise((_resolve,reject)=>{const signal=callContext.abortSignal;if(signal.aborted)reject(signal.reason);else signal.addEventListener('abort',()=>reject(signal.reason),{once:true});});}finally{workerExited=true;}}
    return {output:[{type:'text',text:'actual:'+name}],details:{key}};
    }finally{observed.ended=true;persist();}
   }
  }))}));return registry;
 }
 async function open(policy:string,missing=false){
  const current=await Harness.open(await openNodeSqliteStorage(path),{models,registry:registry(policy,missing),now:()=>1000,settings:{retry:{enabled:false},compaction:{enabled:false}}},ctx);
  current.subscribeCommits((value:any)=>publications.push(structuredClone(value)));
  harness=current;return current;
 }
 harness=await open(recipe.stored);conversation=await harness.root(ctx,{agent:{model:{provider:'openai',modelId:'gpt-test'}}});
 harness.resume();const submission=await conversation.submit({type:'input',content:'input'},ctx);
 if(recipe.id!=='loop'){
  await started;await harness.close(ctx);assert.ok(workerExited);
  const before=JSON.stringify(effects);
  harness=await open(recipe.current);assert.equal(JSON.stringify(effects),before);
  harness.resume();
 }
 await (await harness.submission(submission.id,ctx)).wait(ctx);await harness.waitForIdle(ctx);
 const tasks=(await harness.commit((tx:any)=>tx.scanTasks({},100),ctx)).items;
 const entries=(await harness.commit((tx:any)=>tx.scanEntries({conversationId:conversation.id},100),ctx)).items.sort((a:any,b:any)=>a.id-b.id);
 const ledger=await harness.snapshot(UsageDoc,conversation.id,ctx);
 const parent=tasks.find((task:any)=>task.kind==='pi.generation');
 const closed=recipe.id==='loop'||recipe.stored===recipe.current&&recipe.current==='safe';
 const beforeClose=effects.calls.length;
 await harness.close(ctx);const afterClose=effects.calls.length;
 harness=await open(recipe.current,true);harness.resume();await harness.waitForIdle(ctx);
 const retained=await harness.getTask(parent.id,ctx);
 const lifecycle={worker_joined:effects.calls.every((call:any)=>call.ended),post_close_calls:afterClose-beforeClose,reuse_calls:effects.calls.length-beforeClose,retained_terminal:closed?JSON.stringify(encode(retained.state))===JSON.stringify(encode(parent.state)):null};
 await harness.close(ctx);
 const calls=effects.calls;
 const observed:any={calls:calls.length,applications:Object.keys(effects.applications).length,prepares:effects.prepares.length,
  arguments:calls.map((call:any)=>call.args),intent_before_effect:calls.every((call:any)=>call.intent),
  same_key:calls.every((a:any)=>calls.every((b:any)=>a.task!==b.task||a.key===b.key&&JSON.stringify(encode(a.args))===JSON.stringify(encode(b.args)))),
  distinct_keys:new Set(calls.map((c:any)=>c.key)).size===new Set(calls.map((c:any)=>c.task)).size,
  dispatch:calls.map((call:any)=>call.name),worker_joined:lifecycle.worker_joined,post_close_calls:lifecycle.post_close_calls,reuse_calls:lifecycle.reuse_calls};
 if(closed){
  const messages=requests.at(-1).messages;
  const results=messages.filter((message:any)=>message.role==='toolResult');
  observed.closed={fresh_round_identity:new Set(requests.map((request:any)=>String(request.task)+":"+String(request.attempt))).size===requests.length&&requests.slice(1).every((request:any,index:number)=>request.cutoff>requests[index].cutoff),provider_calls:requests.length,result_names:results.map((message:any)=>message.toolName),result_text:results.filter((m:any)=>m.toolName!=='unavailable').map((m:any)=>m.content.filter((c:any)=>c.type==='text').map((c:any)=>c.text).join('')),
   receipts:entries.filter((entry:any)=>entry.kind==='pi.tool-result').length,terminal:tasks.filter((task:any)=>task.kind==='pi.generation').every((task:any)=>task.state.status==='terminal'&&task.state.outcome.status==='completed'),usage:ledger.models['openai/gpt-test'],
   usage_once:usageMatches(entries.filter((entry:any)=>entry.kind==='pi.assistant').map((entry:any)=>entry.model[0].usage),ledger.models['openai/gpt-test']),
   parent_after_results:results.length===recipe.names.length,cutoff:requests.every((request:any)=>request.cutoff===request.latest),retained_terminal:lifecycle.retained_terminal};
 }
 raw.push({id:recipe.id,tasks,entries,requests,effects,ledger,publications,lifecycle,profile:closed?'closed-round':'replay-prefix-only'});
 cases.push({id:recipe.id,observed:encode(observed)});
}
writeFileSync(out+'/observed.json',JSON.stringify({schema_version:1,phase:input.phase,cases}));
writeFileSync(out+'/resolutions.json',JSON.stringify(resolutions));
writeFileSync(out+'/raw-stored.json',JSON.stringify(raw));
