/** DE07: genuine pinned Harness generation and its atomic admission publication.
 * MIT (c)2025 Mario Zechner; see tests/fixtures/durable/UPSTREAM-LICENSE.txt.
 * The bounded common view stops at admission. Native process/transport recovery
 * and held-worker proofs are separate evidence, not fabricated Pi observations.
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
const {ProviderDoc}=await import('./packages/durable/src/harness/provider.ts');
const {UsageDoc}=await import('./packages/durable/src/harness/usage.ts');
const {createModels,fauxProvider,fauxAssistantMessage,fauxToolCall,calculateCost,Type}=await import('@earendil-works/pi-ai');
const {BACKGROUND_CONTEXT:ctx}=await import('@earendil-works/chord/context');
const {openNodeSqliteStorage}=await import('./packages/durable/src/storage/sqlite/node.ts');
const input=JSON.parse(readFileSync(inputPath,'utf8'));assert.equal(input.phase,'generation-b-v1');
function hex(s:string){const b=Buffer.alloc(s.length*2);for(let i=0;i<s.length;i++)b.writeUInt16BE(s.charCodeAt(i),i*2);return b.toString('hex');}
function encode(v:any):any{if(v===null)return ['null'];if(typeof v==='boolean')return ['bool',v];if(typeof v==='string')return ['string',hex(v)];if(typeof v==='number'){assert.ok(Number.isFinite(v));const b=Buffer.alloc(8);b.writeDoubleBE(v);return ['number',b.toString('hex')];}if(Array.isArray(v))return ['array',v.map(encode)];assert.ok(v!==undefined&&Object.getPrototypeOf(v)===Object.prototype);return ['object',Object.keys(v).map(k=>[hex(k),encode(v[k])]).sort((a,b)=>a[0]<b[0]?-1:a[0]>b[0]?1:0)];}
const cases:any[]=[],raw:any[]=[];
mkdirSync(out+'/stores/pi',{recursive:true,mode:0o700});
for(const recipe of input.cases){
 const path=out+'/stores/pi/'+recipe.id+'.sqlite';
 const faux=fauxProvider({api:'openai-completions',provider:'openai',models:[{id:'gpt-test',cost:{input:1,output:2,cacheRead:3,cacheWrite:4}}],tokenSize:{min:1000,max:1000}});
 const baseModels=createModels();baseModels.setProvider(faux.provider);
 const registry=createRegistry();
 registry.install(defineExtension({name:'fixture.generation',tools:[defineTool({name:'read',description:'Read',parameters:Type.Object({}),execute:async()=>{await new Promise(resolve=>setTimeout(resolve,100));return {output:[{type:'text',text:'later'}]};}})]}));
 let harness:any;const requests:any[]=[];
 let admittedResolve:any;const admitted=new Promise(resolve=>{admittedResolve=resolve;});let publication:any;
 const usage:any={input:16,output:10,cacheRead:4,cacheWrite:0,totalTokens:30,reasoning:3,cost:{input:0,output:0,cacheRead:0,cacheWrite:0,total:0}};
 calculateCost(faux.getModel(),usage);
 const content:any[]=recipe.tools ? [{type:'text',text:'done'}, ...['read','read','unavailable'].map((name,index)=>fauxToolCall(name,{x:1},{id:'call:'+index}))] : [{type:'text',text:'done'}];
 const response={...fauxAssistantMessage(content,{stopReason:recipe.tools?'toolUse':'stop',timestamp:1000}),api:'openai-completions',provider:'openai',model:'gpt-test',usage};
 // The faux transport estimates tokens and replaces supplied usage. Use the
 // pinned generation tests' fixed stream seam so token/cost observations come
 // from this declared response, with cost calculated by actual pi-ai above.
 const models=new Proxy(baseModels,{get(target,property){
   if(property==='streamSimple') return (_model:any,transcript:any,options:any)=>{
     const final=(async()=>{
       const tasks=await harness.commit((tx:any)=>tx.scanTasks({},100),ctx);
       const generation=tasks.items.find((task:any)=>task.kind==='pi.generation');
       const entries=await harness.commit((tx:any)=>tx.scanEntries({conversationId:generation.conversationId},1),ctx);
       requests.push({messages:structuredClone(transcript.messages),sessionId:options.sessionId,cutoff:generation.state.checkpoint.cutoff,latest:entries.items[0]?.id});
       return structuredClone(response);
     })();
     return {[Symbol.asyncIterator]:async function*(){},result:()=>final};
   };
   const value=Reflect.get(target,property,target);
   return typeof value==='function'?value.bind(target):value;
 }});
 harness=await Harness.open(await openNodeSqliteStorage(path),{models,registry,now:()=>1000,settings:{retry:{enabled:false},compaction:{enabled:false}}},ctx);
 const before_calls=requests.length;
 harness.subscribeCommits((value:any)=>{
   if(!publication && value.changes.some((change:any)=>change.type==='entry'&&change.value.kind==='pi.assistant')){
     publication=structuredClone(value);admittedResolve();
   }
 });
 const conversation=await harness.root(ctx,{agent:{model:{provider:'openai',modelId:'gpt-test'}}});
 harness.resume();await conversation.submit({type:'input',content:'input'},ctx);
 await admitted;
 const changes=publication.changes;
 const answer=changes.find((change:any)=>change.type==='entry'&&change.value.kind==='pi.assistant').value;
 const parent=changes.find((change:any)=>change.type==='task'&&change.value.kind==='pi.generation').value;
 const children=changes.filter((change:any)=>change.type==='task'&&change.value.kind==='pi.tool').map((change:any)=>change.value);
 const unavailable=changes.filter((change:any)=>change.type==='entry'&&change.value.kind==='pi.tool-result').map((change:any)=>change.value.model[0]);
 const assistant=answer.model[0];
 const provider=await harness.snapshot(ProviderDoc,conversation.id,ctx);
 const ledger=await harness.snapshot(UsageDoc,conversation.id,ctx);
 const observed:any={before_calls,request_calls:requests.length,
   provider:{provider:assistant.provider,model:assistant.model,api:assistant.api,session:typeof provider.sessionId==='string'&&provider.sessionId.length>0&&provider.sessionId===requests[0].sessionId,cutoff:requests[0].cutoff===requests[0].latest},
   assistant:{text:assistant.content.filter((block:any)=>block.type==='text').map((block:any)=>block.text).join(''),calls:assistant.content.filter((block:any)=>block.type==='toolCall').map((call:any)=>({id:call.id,name:call.name,arguments:call.arguments}))},
   usage:assistant.usage,ledger_usage:ledger.models['openai/gpt-test'],
   admission:{waiting:parent.state.status==='waiting',children:children.map((child:any)=>({kind:child.kind,version:child.version,owner:child.owner===parent.id,conversation:child.conversationId===conversation.id,assistant:child.input.assistant===answer.id,callId:child.input.callId})),unavailable:unavailable.map((result:any)=>({callId:result.toolCallId,isError:result.isError}))},
   publication:changes.some((change:any)=>change.type==='entry'&&change.value.id===answer.id)&&children.every((child:any)=>changes.some((change:any)=>change.type==='task'&&change.value.id===child.id)),
   usage_once:JSON.stringify(encode(ledger.models['openai/gpt-test']))===JSON.stringify(encode(assistant.usage))};
 await harness.close(ctx);observed.close_reason=(await harness.closed).reason;
 const reopened=await Harness.open(await openNodeSqliteStorage(path),{models,registry,now:()=>1000},ctx);
 observed.resumed_after_terminal=!recipe.tools;observed.retained_terminal=null;
 if(!recipe.tools){
   reopened.resume();await reopened.waitForIdle(ctx);
   const retained=await reopened.getTask(parent.id,ctx);
   observed.retained_terminal=JSON.stringify(encode(retained.state))===JSON.stringify(encode(parent.state));
 }
 await reopened.close(ctx);assert.equal((await reopened.closed).reason,'closed');
 observed.retained_calls=requests.length;
 const lifecycle={resumed_after_terminal:observed.resumed_after_terminal,retained_terminal:observed.retained_terminal,close_reason:observed.close_reason,retained_calls:observed.retained_calls};
 raw.push({id:recipe.id,publication,requests,provider,ledger,lifecycle});
 cases.push({id:recipe.id,observed:encode(observed)});
}
writeFileSync(out+'/observed.json',JSON.stringify({schema_version:1,phase:input.phase,cases}));
writeFileSync(out+'/resolutions.json',JSON.stringify(resolutions));
writeFileSync(out+'/raw-stored.json',JSON.stringify(raw));
