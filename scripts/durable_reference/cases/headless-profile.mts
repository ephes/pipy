/** DE12 actual pinned P16 COMMIT/SIGKILL/reopen request-boundary witness.
 * MIT (c)2025 Mario Zechner; see UPSTREAM-LICENSE.txt. Only Models' stream
 * transport is scripted. Storage, Harness, tasks, recovery and requests are real.
 */
import assert from 'node:assert/strict';
import {mkdirSync,readFileSync,writeFileSync,realpathSync} from 'node:fs';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {registerHooks} from 'node:module';
import {sep} from 'node:path';
import {createHash} from 'node:crypto';

const file=fileURLToPath(import.meta.url);
function stored(path:string,value:any){writeFileSync(path,JSON.stringify(value));}
function sha(path:string){return createHash('sha256').update(readFileSync(path)).digest('hex');}

export async function profiles(inputPath:string,out:string,python:string,reader:string){
 const input=JSON.parse(readFileSync(inputPath,'utf8')),records:any[]=[];
 mkdirSync(out+'/profile-records',{recursive:true,mode:0o700});
 mkdirSync(out+'/processes',{recursive:true,mode:0o700});
 for(const recipe of input.profiles){
  const processes:any[]=[];
  for(const mode of ['capture','resume']){
   const argv=[process.execPath,...process.execArgv,file,'--profile-worker',inputPath,out,recipe.id,mode,python,reader];
   const inputBefore=sha(inputPath),workerBefore=sha(file);
   const child=spawnSync(argv[0],argv.slice(1),{cwd:process.cwd(),encoding:'utf8',timeout:30000,maxBuffer:64*1024*1024});
   assert.equal(child.stdout,'');assert.equal(child.stderr,'');
   assert.equal(child.status,mode==='capture'?null:0);assert.equal(child.signal,mode==='capture'?'SIGKILL':null);
   const label='pi-profile-'+recipe.id+'-'+mode;
   const record={argv,cwd:process.cwd(),pid:child.pid,status:child.status,signal:child.signal,stdout:child.stdout,stderr:child.stderr,
    input_before:inputBefore,input_after:sha(inputPath),worker_before:workerBefore,worker_after:sha(file)};
   writeFileSync(out+'/processes/'+label+'.stdout',child.stdout);writeFileSync(out+'/processes/'+label+'.stderr',child.stderr);
   stored(out+'/processes/'+label+'.json',record);processes.push(record);
  }
  records.push({id:recipe.id,capture:JSON.parse(readFileSync(out+'/profile-records/'+recipe.id+'-capture.json','utf8')),
    resumed:JSON.parse(readFileSync(out+'/profile-records/'+recipe.id+'-resume.json','utf8')),processes});
 }
 return records;
}

async function worker(){
 const [inputPath,out,id,mode,python,reader]=process.argv.slice(3),root=realpathSync(process.cwd());
 const resolutions:any[]=[];
 registerHooks({resolve(specifier,context,next){const result=next(specifier,context);if(!result.url.startsWith('node:')){
  const path=realpathSync(fileURLToPath(result.url));assert.ok(path.startsWith(root+sep));resolutions.push({specifier,path:path.slice(root.length+1)});
 }return result;}});
 const {Harness,createRegistry}=await import('./packages/durable/src/index.ts');
 const {ProviderDoc}=await import('./packages/durable/src/harness/provider.ts');
 const {UsageDoc}=await import('./packages/durable/src/harness/usage.ts');
 const {createModels,fauxProvider,fauxAssistantMessage,calculateCost}=await import('@earendil-works/pi-ai');
 const {BACKGROUND_CONTEXT:ctx}=await import('@earendil-works/chord/context');
 const {openNodeSqliteStorage}=await import('./packages/durable/src/storage/sqlite/node.ts');
 const input=JSON.parse(readFileSync(inputPath,'utf8')),recipe=input.profiles.find((x:any)=>x.id===id);
 assert.ok(recipe);assert.ok(['capture','resume'].includes(mode));
 const target=mode==='capture'?'https://fixture.invalid/original':'https://fixture.invalid/current';
 const path=out+'/stores/pi-profile/'+id+'.sqlite';mkdirSync(out+'/stores/pi-profile',{recursive:true,mode:0o700});
 const faux=fauxProvider({api:'openai-completions',provider:'openai',models:[{id:'gpt-test',reasoning:true,cost:{input:1,output:2,cacheRead:3,cacheWrite:4}}]});
 const base=createModels();base.setProvider(faux.provider);const lookups:any[]=[],requests:any[]=[];
 const models=new Proxy(base,{get(object,property){
  if(property==='getModel')return(provider:string,modelId:string)=>{const model=object.getModel(provider,modelId);if(model===undefined)return undefined;
   const result={...model,baseUrl:target};lookups.push({provider,modelId,api:result.api,target:result.baseUrl});return result;};
  if(property==='streamSimple')return(model:any,transcript:any,options:any)=>{
   requests.push({provider:model.provider,model:model.id,api:model.api,target:model.baseUrl,messages:structuredClone(transcript.messages),
    options:{sessionId:options.sessionId,reasoning:options.reasoning,...Object.fromEntries(Object.keys(recipe.stream).map(k=>[k,options[k]]))}});
   const usage:any={input:16,output:10,cacheRead:4,cacheWrite:0,totalTokens:30,reasoning:0,cost:{input:0,output:0,cacheRead:0,cacheWrite:0,total:0}};
   calculateCost(model,usage);const message={...fauxAssistantMessage([{type:'text',text:'done'}],{stopReason:'stop',timestamp:1000}),provider:'openai',model:'gpt-test',api:'openai-completions',usage};
   return {[Symbol.asyncIterator]:async function*(){},result:async()=>message};
  };const value=Reflect.get(object,property,object);return typeof value==='function'?value.bind(object):value;
 }});
 const raw=await openNodeSqliteStorage(path);let captured=false;
 const storage=new Proxy(raw,{get(object,property){
  if(property==='commit')return async(writes:any[],context:any)=>{
   const seq=await object.commit(writes,context);
   const task=writes.find(w=>w.type==='task'&&w.value.kind==='pi.generation'&&
    (recipe.cut==='prepare'?w.value.state.status==='pending'&&w.value.state.checkpoint.phase==='prepare':w.value.state.status==='running'&&w.value.state.checkpoint.phase==='request'))?.value;
   if(mode==='capture'&&task!==undefined&&!captured){
    captured=true;const entry=recipe.cut==='prepare'?writes.filter(w=>w.type==='entry').at(-1)?.value.id:task.state.checkpoint.cutoff;
    assert.ok(Number.isSafeInteger(entry));
    const argv=[python,'-I',reader,path,String(entry),String(task.id)];
    const observed=spawnSync(argv[0],argv.slice(1),{cwd:root,encoding:'utf8',timeout:10000,maxBuffer:64*1024*1024});
    assert.equal(observed.status,0,observed.stderr);assert.equal(observed.stderr,'');assert.equal(observed.signal,null);
    stored(out+'/profile-records/'+id+'-capture.json',{seq,writes:structuredClone(writes),task,entry,lookups,requests,
     independent:{argv,cwd:root,pid:observed.pid,status:observed.status,stdout:observed.stdout,stderr:observed.stderr,observed:JSON.parse(observed.stdout)},
     runtime:{pid:process.pid,argv:process.argv,execArgv:process.execArgv,executable:process.execPath,version:process.version,sqlite:process.versions.sqlite,cwd:root},resolutions});
    process.kill(process.pid,'SIGKILL');await new Promise(()=>{});
   }return seq;
  };const value=Reflect.get(object,property,object);return typeof value==='function'?value.bind(object):value;
 }});
 const harness=await Harness.open(storage,{models,registry:createRegistry(),now:()=>1000,
  settings:{retry:{enabled:false},compaction:{enabled:false},stream:recipe.stream}},ctx);
 if(mode==='capture'){
  const conversation=await harness.root(ctx,{agent:{model:{provider:'openai',modelId:'gpt-test'},thinkingLevel:'low'}});
  await conversation.submit({type:'input',content:'input',whenBusy:'reject'},ctx);
  await harness.waitForIdle(ctx);throw new Error('P16 cut absent');
 }
 const before=(await harness.commit((tx:any)=>tx.scanTasks({},100),ctx)).items;
 const selected=before.find((t:any)=>t.kind==='pi.generation');assert.ok(selected);
 harness.resume();await harness.waitForIdle(ctx);
 const after=(await harness.commit((tx:any)=>tx.scanTasks({},100),ctx)).items;
 const conversation=await harness.conversation(selected.conversationId,ctx);const watch=await conversation.watch(ctx);
 const final=structuredClone(watch.value);await watch.stop();
 const provider=await harness.snapshot(ProviderDoc,selected.conversationId,ctx),usage=await harness.snapshot(UsageDoc,selected.conversationId,ctx);
 await harness.close(ctx);assert.equal((await harness.closed).reason,'closed');
 stored(out+'/profile-records/'+id+'-resume.json',{before,after,lookups,requests,provider,usage,final,
  runtime:{pid:process.pid,argv:process.argv,execArgv:process.execArgv,executable:process.execPath,version:process.version,sqlite:process.versions.sqlite,cwd:root},resolutions});
}

if(process.argv[2]==='--profile-worker')await worker();
