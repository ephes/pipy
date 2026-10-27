/** Finite interrupted recovery through actual Harness/SQLite/next model history.
 * MIT (c)2025 Mario Zechner; see tests/fixtures/durable/UPSTREAM-LICENSE.txt.
 * First streamSimple and fixture tool prepare/execute are scripted; next request
 * uses the exported OpenAI adapter/client/body/SSE parser with synthetic fetch.
 */
import assert from 'node:assert/strict';
import {readFileSync,writeFileSync,realpathSync,mkdirSync} from 'node:fs';
import {registerHooks} from 'node:module';
import {fileURLToPath} from 'node:url';
import {sep} from 'node:path';
const root=realpathSync(process.cwd()),[inputPath,out]=process.argv.slice(2),resolutions:any[]=[];
registerHooks({resolve(specifier,context,next){const result=next(specifier,context);if(!result.url.startsWith('node:')){const path=realpathSync(fileURLToPath(result.url));assert.ok(path.startsWith(root+sep));resolutions.push({specifier,path:path.slice(root.length+1)});}return result;}});
const {Harness,createRegistry,defineExtension,defineTool,LiveDoc}=await import('./packages/durable/src/index.ts');
const {createModels,fauxProvider,fauxAssistantMessage,fauxToolCall,calculateCost,Type}=await import('@earendil-works/pi-ai');
const {BACKGROUND_CONTEXT:ctx}=await import('@earendil-works/chord/context');
const {streamSimple:streamSimpleOpenAI}=await import('@earendil-works/pi-ai/api/openai-completions');
const {openNodeSqliteStorage}=await import('./packages/durable/src/storage/sqlite/node.ts');
const input=JSON.parse(readFileSync(inputPath,'utf8'));assert.equal(input.phase,'interrupted-tool-continuation-v1');
const raw:any[]=[];mkdirSync(out+'/stores/pi',{recursive:true,mode:0o700});
for(const recipe of input.cases){
 const path=out+'/stores/pi/'+recipe.id+'.sqlite',effects:any={calls:[],prepares:[]},requests:any[]=[],wire:any[]=[],wireText:string[]=[],reports:any[]=[];
 const faux=fauxProvider({api:'openai-completions',provider:'openai',models:[{id:'gpt-test'}]});const base=createModels();base.setProvider(faux.provider);
 const models=new Proxy(base,{get(target,property){
  if(property==='streamSimple')return(model:any,transcript:any,options:any)=>{
   requests.push(structuredClone(transcript.messages));
   if(requests.length>1){
    const fetch=async(url:any,init:any)=>{
     const request=url instanceof Request?url:undefined;
     const text=typeof init?.body==='string'?init.body:request?await request.clone().text():await new Response(init?.body).text();
     wireText.push(text);wire.push(structuredClone(JSON.parse(text)));
     const first={id:'fixture',object:'chat.completion.chunk',created:0,model:'gpt-test',choices:[{index:0,delta:{role:'assistant',content:'terminal answer'},finish_reason:null}]};
     const last={id:'fixture',object:'chat.completion.chunk',created:0,model:'gpt-test',choices:[{index:0,delta:{},finish_reason:'stop'}],usage:{prompt_tokens:20,completion_tokens:10,total_tokens:30,prompt_tokens_details:{cached_tokens:4}}};
     return new Response('data: '+JSON.stringify(first)+'\n\ndata: '+JSON.stringify(last)+'\n\ndata: [DONE]\n\n',{status:200,headers:{'content-type':'text/event-stream'}});
    };
    // This is the real exported adapter and its real SDK body/parser. Only fetch
    // is synthetic; headers/URL/auth material are deliberately not retained.
    return streamSimpleOpenAI({...model,baseUrl:'https://fixture.invalid/v1'},transcript,{...options,apiKey:'fixture-key',fetch,maxRetries:0});
   }
   const usage={input:16,output:10,cacheRead:4,cacheWrite:0,totalTokens:30,cost:{input:0,output:0,cacheRead:0,cacheWrite:0,total:0}};
   calculateCost(faux.getModel(),usage);
   const message={...fauxAssistantMessage([{type:'text',text:'done'},fauxToolCall('keyed',{x:1},{id:'call:0'})],{stopReason:'toolUse',timestamp:1000}),api:'openai-completions',provider:'openai',model:'gpt-test',usage};
   return {[Symbol.asyncIterator]:async function*(){},result:async()=>message};
  };
  const value=Reflect.get(target,property,target);return typeof value==='function'?value.bind(target):value;
 }});
 let held=true,startedResolve:any;const started=new Promise(resolve=>{startedResolve=resolve;});
 function registry(mode='saved'){
  const registry=createRegistry();
  const names=mode==='missing'?['later','unused']:['later','keyed','unused'];
  if(mode!=='empty')registry.install(defineExtension({name:'fixture.interrupted',tools:names.map(name=>defineTool({
   name,description:'fixture '+name,parameters:Type.Object({x:Type.Integer(),prepared:Type.Optional(Type.Boolean())}),replay:name==='keyed'?recipe.stored:'unsafe',
   prepareArguments(args:any){effects.prepares.push(structuredClone(args));return {...args,prepared:true};},
   async execute(args:any,api:any,callContext:any){
    effects.calls.push(structuredClone(args));api.output('retained output');api.diagnostic({severity:'warn',message:'committed diagnostic',code:'fixture'});
    await api.details({retained:[null,false,0]},callContext);
    reports.push({operation:'details-ack',committed:structuredClone(await api.snapshot(LiveDoc,api.conversationId,callContext))});
    if(held){held=false;startedResolve();await new Promise((_resolve,reject)=>{const signal=callContext.abortSignal;if(signal.aborted)reject(signal.reason);else signal.addEventListener('abort',()=>reject(signal.reason),{once:true});});}
    return {output:[{type:'text',text:'tool completed normally'}],details:{normal:true}};
   }
  }))}));return registry;
 }
 async function open(mode='saved'){return Harness.open(await openNodeSqliteStorage(path),{models,registry:registry(mode),settings:{retry:{enabled:false},compaction:{enabled:false}}},ctx);}
 let harness=await open(),conversation=await harness.root(ctx,{agent:{model:{provider:'openai',modelId:'gpt-test'}}});const cid=conversation.id;
 const submission=await conversation.submit({type:'input',content:'input',whenBusy:'reject'},ctx);
 await started;await harness.close(ctx);
 const cut={calls:effects.calls.length,prepares:effects.prepares.length,providers:requests.length,details_acknowledged:true};
 harness=await open(recipe.current==='missing'?'missing':'saved');conversation=await harness.conversation(cid,ctx);harness.resume();
 const resumed=await harness.submission(submission.id,ctx);await resumed.wait(ctx);await harness.waitForIdle(ctx);
 let watch=await conversation.watch(ctx);const final=structuredClone(watch.value);await watch.stop();const tasks=(await harness.commit((tx:any)=>tx.scanTasks({},100),ctx)).items;
 const before={providers:requests.length,calls:effects.calls.length,prepares:effects.prepares.length};await harness.close(ctx);
 harness=await open('empty');conversation=await harness.conversation(cid,ctx);watch=await conversation.watch(ctx);const retained=structuredClone(watch.value);await watch.stop();const repeated=await (await harness.submission(submission.id,ctx)).wait(ctx);await harness.waitForIdle(ctx);await harness.close(ctx);
 const after={providers:requests.length,calls:effects.calls.length,prepares:effects.prepares.length};assert.equal(wire.length,1);
 raw.push({id:recipe.id,cid,submission:submission.id,requests,wire,wireText,effects,reports,tasks,final,retained,before,after,repeated,cut});
}
writeFileSync(out+'/raw-stored.json',JSON.stringify(raw));writeFileSync(out+'/resolutions.json',JSON.stringify(resolutions));
writeFileSync(out+'/runtime.json',JSON.stringify({pid:process.pid,executable:process.execPath,version:process.version,sqlite:process.versions.sqlite,argv:process.argv,cwd:process.cwd()}));
