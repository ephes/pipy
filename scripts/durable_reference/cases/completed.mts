/** DE09 completed facts observed through actual pinned public Harness watches.
 * MIT (c)2025 Mario Zechner; see tests/fixtures/durable/UPSTREAM-LICENSE.txt.
 * Only the provider stream and fixture tools are scripted. Native lifecycle,
 * task-graph shape and full experimental event/partial parity are not paired.
 */
import assert from 'node:assert/strict';
import {readFileSync, writeFileSync, realpathSync, mkdirSync} from 'node:fs';
import {registerHooks} from 'node:module';
import {fileURLToPath} from 'node:url';
import {sep} from 'node:path';

const root=realpathSync(process.cwd()), [inputPath,out]=process.argv.slice(2), resolutions:any[]=[];
registerHooks({resolve(specifier,context,next){
 const result=next(specifier,context);
 if(!result.url.startsWith('node:')){
  const path=realpathSync(fileURLToPath(result.url));assert.ok(path.startsWith(root+sep));
  resolutions.push({specifier,path:path.slice(root.length+1)});
 }
 return result;
}});
const {Harness}=await import('./packages/durable/src/harness/harness.ts');
const {watchEvents}=await import('./packages/durable/src/harness/events.ts');
const {createRegistry}=await import('./packages/durable/src/harness/registry.ts');
const {defineExtension,defineTool}=await import('./packages/durable/src/harness/define.ts');
const {UsageDoc}=await import('./packages/durable/src/harness/usage.ts');
const {createModels,fauxProvider,fauxAssistantMessage,fauxToolCall,calculateCost,Type}=await import('@earendil-works/pi-ai');
const {BACKGROUND_CONTEXT:ctx}=await import('@earendil-works/chord/context');
const {openNodeSqliteStorage}=await import('./packages/durable/src/storage/sqlite/node.ts');
const input=JSON.parse(readFileSync(inputPath,'utf8'));assert.equal(input.phase,'completed-projection-b-v1');
function hex(s:string){const b=Buffer.alloc(s.length*2);for(let i=0;i<s.length;i++)b.writeUInt16BE(s.charCodeAt(i),i*2);return b.toString('hex');}
function encode(v:any):any{
 if(v===null)return ['null'];if(typeof v==='boolean')return ['bool',v];
 if(typeof v==='string')return ['string',hex(v)];
 if(typeof v==='number'){assert.ok(Number.isFinite(v));const b=Buffer.alloc(8);b.writeDoubleBE(v);return ['number',b.toString('hex')];}
 if(Array.isArray(v))return ['array',v.map(encode)];
 assert.ok(v!==undefined&&Object.getPrototypeOf(v)===Object.prototype);
 return ['object',Object.keys(v).map(k=>[hex(k),encode(v[k])]).sort((a,b)=>a[0]<b[0]?-1:a[0]>b[0]?1:0)];
}
function same(a:any,b:any){return JSON.stringify(encode(a))===JSON.stringify(encode(b));}
function total(values:any[]){const result:any={};for(const value of values)for(const [key,item]of Object.entries(value)){
 if(key==='cost'){const cost=result.cost??={};for(const [name,n]of Object.entries(item as any))cost[name]=(cost[name]??0)+(n as number);}
 else result[key]=(result[key]??0)+(item as number);
}return result;}
const cases:any[]=[],raw:any[]=[];
mkdirSync(out+'/stores/pi',{recursive:true,mode:0o700});
for(const recipe of input.cases){
 const path=out+'/stores/pi/'+recipe.id+'.sqlite';
 const effects:any={calls:[],applications:{},prepares:[]},requests:any[]=[],publications:any[]=[],reports:any[]=[];
 let harness:any, latest:any, automaticResolve:any, eventsResolve:any;
 const automatic=new Promise(resolve=>automaticResolve=resolve),eventCompleted=new Promise(resolve=>eventsResolve=resolve);
 const faux=fauxProvider({api:'openai-completions',provider:'openai',models:[{id:'gpt-test',cost:{input:1,output:2,cacheRead:3,cacheWrite:4}}]});
 const base=createModels();base.setProvider(faux.provider);
 const models=new Proxy(base,{get(target,property){
  if(property==='streamSimple')return(_model:any,transcript:any)=>{
   requests.push(structuredClone(transcript.messages));const names=requests.length===1?recipe.names:[];
   const usage={input:16,output:10,cacheRead:4,cacheWrite:0,totalTokens:30,reasoning:3,cost:{input:0,output:0,cacheRead:0,cacheWrite:0,total:0}};
   calculateCost(faux.getModel(),usage);
   const message={...fauxAssistantMessage([{type:'text',text:'done'},...names.map((name:string,index:number)=>fauxToolCall(name,{x:1},{id:'call:'+index}))],{stopReason:names.length?'toolUse':'stop',timestamp:1000}),api:'openai-completions',provider:'openai',model:'gpt-test',usage};
   return {[Symbol.asyncIterator]:async function*(){},result:async()=>message};
  };
  const value=Reflect.get(target,property,target);return typeof value==='function'?value.bind(target):value;
 }});
 function registry(missing=false){
  const result=createRegistry();
  if(!missing)result.install(defineExtension({name:'fixture.completed',tools:['keyed','later'].map(name=>defineTool({
   name,description:name,parameters:Type.Object({x:Type.Integer(),prepared:Type.Optional(Type.Boolean())}),replay:'safe',
   prepareArguments(args:any){effects.prepares.push(name);return {...args,prepared:true};},
   async execute(args:any,api:any,context:any){
    const key=await api.memo('fixture.key','completed-'+api.taskId,context);
    effects.calls.push({name,key,args:structuredClone(args)});effects.applications[key]=structuredClone(args);
    return {output:[{type:'text',text:'actual:'+name}],details:{key}};
   }
  }))}));return result;
 }
 async function open(missing=false){return Harness.open(await openNodeSqliteStorage(path),{
  models,registry:registry(missing),now:()=>1000,onReport:(_error:any,info:any)=>reports.push({type:info.type}),
  settings:{retry:{enabled:false},compaction:{enabled:false}}
 },ctx);}
 harness=await open();harness.subscribeCommits((value:any)=>publications.push(structuredClone(value)));
 const conversation=await harness.root(ctx,{agent:{model:{provider:'openai',modelId:'gpt-test'}}});
 const watch=await conversation.watch(ctx),bad=await conversation.watch(ctx),stream=await watchEvents(harness,conversation.id,ctx);
 const snapshots:any[]=[],events:any[]=[],independent:any[]=[];
 bad.start(async()=>{throw new Error('synthetic observer failure');});
 stream.start(async(batch:any)=>{
  events.push(structuredClone(batch));
  if(batch.some((event:any)=>event.type==='message_end'&&event.entry.model?.[0]?.role==='assistant'&&event.entry.model[0].stopReason==='stop'))eventsResolve();
 });
 watch.start(async(value:any)=>{
  const stored=(await harness.commit((tx:any)=>tx.scanEntries({conversationId:conversation.id},100),ctx)).items;
  let ok=value.entries.every((entry:any)=>stored.some((found:any)=>found.id===entry.id&&same(found,entry)));
  if(value.entries.some((entry:any)=>entry.model?.[0]?.role==='assistant'&&entry.model[0].stopReason==='stop')){
   const committedUsage=await harness.snapshot(UsageDoc,conversation.id,ctx);
   ok=ok&&same(committedUsage,value.docs['pi.usage']);
  }
  independent.push(ok);assert.ok(ok);snapshots.push(structuredClone(value));latest=value;
  if(value.entries.some((entry:any)=>entry.model?.[0]?.role==='assistant'&&entry.model[0].stopReason==='stop'))automaticResolve();
 });
 const submission=await conversation.submit({type:'input',content:'input'},ctx);
 // Await actual automatic public notifications before any final refresh/read.
 await automatic;await eventCompleted;
 await (await harness.submission(submission.id,ctx)).wait(ctx);await harness.waitForIdle(ctx);
 const tasks=(await harness.commit((tx:any)=>tx.scanTasks({},100),ctx)).items;
 const ledger=await harness.snapshot(UsageDoc,conversation.id,ctx);
 const badEnd=await bad.closed,stopEnd=await watch.stop();await stream.stop();
 const before={providers:requests.length,calls:effects.calls.length,applications:Object.keys(effects.applications).length,prepares:effects.prepares.length};
 await harness.close(ctx);harness=await open(true);
 const reopened=await harness.conversation(conversation.id,ctx),fresh=await reopened.watch(ctx),freshEvents=await watchEvents(harness,conversation.id,ctx);
 const retained=fresh.value,retainedEvents=freshEvents.snapshot;
 const reconnect=same(latest.entries,retained.entries);
 await fresh.stop();await freshEvents.stop();await harness.close(ctx);
 const after={providers:requests.length,calls:effects.calls.length,applications:Object.keys(effects.applications).length,prepares:effects.prepares.length};
 const entries=retained.entries.filter((entry:any)=>entry.model?.length===1),messages=entries.map((entry:any)=>entry.model[0]);
 const assistant=messages.find((m:any)=>m.role==='assistant'&&m.content.some((c:any)=>c.type==='toolCall'));
 const calls=assistant.content.filter((c:any)=>c.type==='toolCall');
 const results=calls.map((call:any)=>messages.find((m:any)=>m.role==='toolResult'&&m.toolCallId===call.id));assert.ok(results.every(Boolean));
 const logical=[...messages.filter((m:any)=>m.role==='user'),assistant,...results,...messages.filter((m:any)=>m.role==='assistant'&&m!==assistant)];
 const observed={
  completed:tasks.filter((t:any)=>t.kind==='pi.generation'||t.kind==='pi.tool').every((t:any)=>t.state.status==='terminal'&&t.state.outcome.status==='completed'),
  roles:logical.map((m:any)=>m.role),
  result_names:results.map((m:any)=>m.toolName),result_text:results.filter((m:any)=>m.toolName!=='unavailable').map((m:any)=>m.content.filter((b:any)=>b.type==='text').map((b:any)=>b.text).join('')),
  automatic:snapshots.length>0&&events.some((batch:any)=>batch.some((e:any)=>e.type==='message_end'&&e.entry.model?.[0]?.stopReason==='stop')),
  independent:independent.length>0&&independent.every(Boolean),isolation:badEnd.reason==='listener_error',
  reconnect:reconnect&&same(retainedEvents.entries,retained.entries),stop:stopEnd.reason==='stopped',
  effects_unchanged:same(before,after),provider_calls:after.providers,tool_calls:after.calls,
  usage:ledger.models['openai/gpt-test'],usage_once:same(total(messages.filter((m:any)=>m.role==='assistant').map((m:any)=>m.usage)),ledger.models['openai/gpt-test'])
 };
 cases.push({id:recipe.id,observed:encode(observed)});
 raw.push({id:recipe.id,tasks,ledger,effects,requests,publications,snapshots,events,independent,badEnd:{reason:badEnd.reason},stopEnd,retained,retainedEvents,before,after,reports});
}
writeFileSync(out+'/observed.json',JSON.stringify({schema_version:1,phase:input.phase,cases}));
writeFileSync(out+'/resolutions.json',JSON.stringify(resolutions));
writeFileSync(out+'/raw-stored.json',JSON.stringify(raw));
