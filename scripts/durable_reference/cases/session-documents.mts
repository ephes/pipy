/** DE04a typed current documents: actual pinned SessionImpl, MemoryStorage and definitions.
 * Loaded changes use checkpointWhen:()=>true solely as the existing full-base adaptation;
 * normalized operations/checkpoint policy remain DE04b. MIT (c)2025 Mario Zechner. */
import assert from "node:assert/strict";
import {readFileSync,writeFileSync,realpathSync} from "node:fs";
import {registerHooks} from "node:module";
import {fileURLToPath} from "node:url";
import {resolve,sep} from "node:path";
const root=realpathSync(process.cwd()),[inputPath,out]=process.argv.slice(2);
const resolutions:any[]=[];
registerHooks({resolve(specifier,context,nextResolve){const result=nextResolve(specifier,context);if(result.url.startsWith("node:"))return result;const path=realpathSync(fileURLToPath(result.url));assert.ok(path.startsWith(root+sep));resolutions.push({specifier,path:path.slice(root.length+1)});return result;}});
const {SessionImpl}=await import("./packages/durable/src/session/session.ts");
const {MemoryStorage}=await import("./packages/durable/src/storage/memory.ts");
const {defineDoc,defineDocFamily}=await import("./packages/durable/src/documents.ts");
const {BACKGROUND_CONTEXT:ctx}=await import("@earendil-works/chord/context");
const input=JSON.parse(readFileSync(inputPath,"utf8"));
const ids=[...Array.from({length:9},(_,i)=>"A"+(i+1)),...Array.from({length:10},(_,i)=>"T"+(i+1)),"P1","P2","P3","C1","C2"];
assert.deepEqual(input.cases,ids);assert.equal(input.clock,1000);assert.equal(input.retirement_count,257);
function hex(s:string){const b=Buffer.alloc(s.length*2);for(let i=0;i<s.length;i++)b.writeUInt16BE(s.charCodeAt(i),i*2);return b.toString("hex");}
function encode(v:any):any{if(v===undefined)return ["undefined"];if(v===null)return ["null"];if(typeof v==="boolean")return ["bool",v];if(typeof v==="string")return ["string",hex(v)];if(typeof v==="number"){assert.ok(Number.isFinite(v));const b=Buffer.alloc(8);b.writeDoubleBE(v);return ["number",b.toString("hex")];}if(Array.isArray(v))return ["array",v.map(encode)];assert.ok(Object.getPrototypeOf(v)===Object.prototype||Object.getPrototypeOf(v)===null);return ["object",Object.keys(v).map(k=>[hex(k),encode(v[k])]).sort((a,b)=>a[0]<b[0]?-1:a[0]>b[0]?1:0)];}
function deferred(){let resolve:any;const promise=new Promise<void>(r=>resolve=r);return {promise,resolve};}
async function bounded<T>(promise:Promise<T>):Promise<T>{let timer:any;try{return await Promise.race([promise,new Promise<never>((_,reject)=>timer=setTimeout(()=>reject(Error("driver deadline")),10000))]);}finally{clearTimeout(timer);}}
function task(id=20,terminal=false,conversation=7){return {id,conversationId:conversation,kind:"probe.task",version:1,input:null,state:terminal?{status:"terminal",outcome:{status:"completed",result:{ok:true}}}:{status:"pending",checkpoint:null},background:false,abortRequested:false};}
function base(id:number,kind="probe.single",owner=20,key?:string){return {type:"document.create",record:{id,kind,scope:{kind:"task",taskId:owner},...(key===undefined?{}:{key})},content:{kind:"base",version:1,value:{lines:["raw"]}}};}
const names:any={mintId:"mint_id",findDocument:"find_document",scanDocuments:"scan_documents",scanConversations:"scan_conversations",scanEntries:"scan_entries",scanTasks:"scan_tasks",scanSubmissions:"scan_submissions",submissionByRequest:"submission_by_request",findLatestHeadMarker:"find_latest_head_marker"};
const profiles:any[]=[],raw:any[]=[];
for(const id of input.cases){
 const memory=new MemoryStorage(),calls:any[]=[],writes:any[]=[],initial:any[]=[],errors:any[]=[],publications:any[]=[],facts:any={},pendingSnapshots:Promise<any>[]=[];let hold:any;
 await memory.commit([{type:"conversation",value:{id:1}},{type:"conversation",value:{id:7}},{type:"task",value:task()}],ctx);
 const storage=new Proxy(memory,{get(target,key){const fn=Reflect.get(target,key);if(typeof fn!=="function")return fn;return async(...args:any[])=>{const op=names[String(key)]??String(key);calls.push({op,args:encode(args.slice(0,key==="scanDocuments"?3:key==="findDocument"||key==="document"?2:key==="close"||key==="mintId"?0:1))});if(hold?.op===op){const h=hold;hold=undefined;h.entered.resolve();await h.release.promise;}if(key==="commit")writes.push(...structuredClone(args[0]));return fn.apply(target,args);};}});
 class Driver extends SessionImpl{report(e:any,info:any){errors.push({name:e.name,message:e.message,info});}}
 const session=new Driver(storage as any,()=>input.clock,()=>600000,()=>undefined);
 session.observeCommits((p:any)=>publications.push(p));
 const make=(scope="session",family=false,kind="probe.single",version=1)=>{const def:any={kind,version,scope,...(scope==="conversation"?{history:"latest",fork:"initial"}:{}),initial:(seed:any)=>{initial.push({scope,family,kind,...(family?{seed:structuredClone(seed)}:{})});return family?{seed:structuredClone(seed),hits:0}:{lines:[]};},checkpointWhen:()=>true};return family?defineDocFamily({...def,family:true}):defineDoc(def);};
 const S=make(),C=make("conversation"),T=make("task"),FS=make("session",true),FC=make("conversation",true),FT=make("task",true);
 const capture=async(label:string,fn:()=>any)=>{try{const value=await fn();facts[label]={status:"fulfilled",...(value===undefined?{}:{value})};}catch(e:any){facts[label]={status:"rejected",error:{name:e.name,message:e.message,code:e.code??null}};raw.push({id,label,name:e.name,message:e.message,code:e.code??null,stack:e.stack??null});}};
 const seed=async(...values:any[])=>memory.commit(values,ctx);
 const gate=()=>{const h={op:"find_document",entered:deferred(),release:deferred()};hold=h;return h;};
 const commit=(cb:any)=>session.commit(cb,ctx);
 const snap=async(token:any,...args:any[])=>(await session.snapshot(token,...args,ctx))??null;
 try{
 switch(id){
 case "A1":await commit(async(tx:any)=>{for(const [token,args] of [[S,[]],[C,[7]],[T,[20]],[FS,["k",{n:1}]],[FC,[7,"k",{n:2}]],[FT,[20,"k",{n:3}]]] as any){const d=await tx.doc(token,...args);if("hits"in d)d.hits++;else d.lines.push("value");}});facts.values=await Promise.all([snap(S),snap(C,7),snap(T,20),snap(FS,"k"),snap(FC,7,"k"),snap(FT,20,"k")]);break;
 case "A2":await commit(async(tx:any)=>{await tx.doc(S);for(const k of ["","k","\ud800","😀"])await tx.doc(FS,k,k);facts.same=await tx.doc(FS,"\ud83d\ude00",new Date())===await tx.doc(FS,"😀",null);await tx.doc(FC,7,"k",2);await tx.doc(FT,20,"k",3);});facts.values=await Promise.all([snap(S),snap(FS,""),snap(FS,"k"),snap(FS,"\ud800"),snap(FS,"😀"),snap(FC,7,"k"),snap(FT,20,"k")]);break;
 case "A3":case "A4":case "A5":{const h=gate(),caller:any={nested:{n:1}};try{await commit(async(tx:any)=>{const first=tx.doc(FS,"k",caller);await bounded(h.entered.promise);caller.nested.n=99;const second=tx.doc(FS,"k",id==="A5"?new Date():{tag:"second"});facts.same_pending=first===second;h.release.resolve();facts.same=await first===await second;});}finally{h.release.resolve();}facts.value=await snap(FS,"k");break;}
 case "A6":await commit(async(tx:any)=>{await tx.doc(FS,"k","stored");});await capture("invalid",()=>commit((tx:any)=>tx.doc(FS,"k",new Date())));await commit(async(tx:any)=>{(await tx.doc(FS,"k","unused")).hits++;});facts.value=await snap(FS,"k");break;
 case "A7":await commit(async(tx:any)=>{const first=await tx.doc(FS,"k","old");await tx.retireDoc(FS,"k");facts.same=first===await tx.doc(FS,"k","new");});await capture("invalid",()=>commit(async(tx:any)=>{await tx.retireDoc(FS,"k");await tx.doc(FS,"k",new Date());}));facts.value=await snap(FS,"k");break;
 case "A8":await commit(async(tx:any)=>{const a=await tx.doc(FS,"k","first");facts.same=await tx.doc(make("session",true,"probe.single",2),"k",new Date())===a;});await capture("version",()=>commit((tx:any)=>tx.doc(make("session",true,"probe.single",2),"k",null)));facts.value=await snap(FS,"k");break;
 case "A9":facts.values=await Promise.all([snap(S),snap(C,7),snap(T,20),snap(FS,"k"),snap(FC,7,"k"),snap(FT,20,"k")]);await commit(async(tx:any)=>{await tx.retireDoc(S);await tx.retireDoc(C,7);await tx.retireDoc(T,20);await tx.retireDoc(FS,"k");await tx.retireDoc(FC,7,"k");await tx.retireDoc(FT,20,"k");});break;
 case "T1":await capture("missing",()=>commit((tx:any)=>tx.doc(T,4242)));await seed({type:"task",value:task(20,true)});await capture("terminal",()=>commit((tx:any)=>tx.doc(T,20)));break;
 case "T2":case "T3":await seed(...(id==="T2"?[{type:"task",value:task(20,true)}]:[]),base(100,"probe.single",id==="T2"?20:4242));await commit(async(tx:any)=>{(await tx.doc(T,id==="T2"?20:4242)).lines.push("loaded");});facts.value=await snap(T,id==="T2"?20:4242);break;
 case "T4":case "T5":await seed(base(100));await commit(async(tx:any)=>{const d=id==="T5"?await tx.doc(T,20):undefined;tx.setTask(task(20,true));await capture("single",()=>tx.doc(T,20));await capture("family",()=>tx.doc(FT,20,"late",new Date()));if(d)d.lines.push("final");});facts.value=await snap(T,20);break;
 case "T6":case "T7":await commit(async(tx:any)=>{const n=await tx.createTask({definition:{name:"probe.task",version:1,initial:()=>null}},null,{ownership:{kind:"conversation"},conversationId:7});const d=await tx.doc(T,n);d.lines.push("new");await tx.doc(FT,n,"one",null);if(id==="T7")tx.setTask(task(n,true));facts.task=n;});facts.values=await Promise.all([snap(T,facts.task),snap(FT,facts.task,"one")]);break;
 case "T8":case "T9":{if(id==="T9")await seed(base(100));const h=gate();try{await commit(async(tx:any)=>{const pending=tx.doc(T,20);await bounded(h.entered.promise);tx.setTask(task(20,true));h.release.resolve();await capture("acquire",async()=>{const d=await pending;if(id==="T9")d.lines.push("last");});});}finally{h.release.resolve();}facts.value=await snap(T,20);break;}
 case "T10":await seed({type:"task",value:task(20,true)},base(100));await commit((tx:any)=>tx.retireDoc(make("task",false,"probe.single",2),20));facts.value=await snap(T,20);break;
 case "P1":case "P2":await seed(...Array.from({length:input.retirement_count},(_,i)=>base(1000+i,"unknown."+i,20,i%3===0?undefined:i%3===1?"":"k")));await commit(async(tx:any)=>{if(id==="P2"){await tx.retireDoc(make("task",false,"unknown.0"),20);(await tx.doc(make("task",true,"unknown.1"),20,"",null)).lines.push("final");}tx.setTask(task(20,true));});facts.remaining=(await memory.scanDocuments({scope:{kind:"task",taskId:20},at:"current"},300,undefined,ctx)).items.length;break;
 case "P3":await seed({type:"task",value:task(20,true)});await capture("replacement",()=>commit((tx:any)=>tx.setTask(task(20,true))));break;
 case "C1":await commit(async(tx:any)=>{await tx.createConversation({ownership:{kind:"ownerless"}});(await tx.doc(T,20)).lines.push("owner");});facts.value=await snap(T,20);break;
 case "C2":await commit(async(tx:any)=>{for(const k of ["","a","b"])await tx.doc(FT,20,k,k);});const old=await snap(FT,20,"");facts.identity=[];session.observeCommits((p:any)=>{for(const c of p.changes)if(c.type==="document"){pendingSnapshots.push(snap(FT,20,c.record.key).then(v=>facts.identity.push(v===c.value)));}});await commit((tx:any)=>tx.setTask(task(20,true)));facts.old=old;facts.values=await Promise.all([snap(FT,20,""),snap(FT,20,"a"),snap(FT,20,"b")]);break;
 default:throw Error("unknown recipe");
 }
 await bounded(Promise.all(pendingSnapshots));
 const counts:any={};for(const c of calls)counts[c.op]=(counts[c.op]??0)+1;
 const published=publications.flatMap((p:any)=>p.changes.filter((c:any)=>c.type==="document").map((c:any)=>{if(c.value===null)assert.deepEqual(c.ops,[]);return {record:c.record,conversationId:c.conversationId??null,version:c.version??null,value:c.value};}));
 profiles.push({id,profile:encode({facts,initial,writes,counts,scans:calls.filter(c=>c.op==="scan_documents").map(c=>{const args=structuredClone(c.args);if(args[1][2]?.[0]==="undefined")args[1][2]=["null"];return args;}),publications:published})});
 raw.push({id,calls,publications:encode(publications),reports:errors});
 }finally{if(hold)hold.release.resolve();await bounded(session.close(ctx));}
}
writeFileSync(resolve(out,"observed.json"),JSON.stringify({schema_version:1,phase:input.phase,cases:profiles},null,2)+"\n");
writeFileSync(resolve(out,"raw.json"),JSON.stringify(raw,null,2)+"\n");
writeFileSync(resolve(out,"resolutions.json"),JSON.stringify(resolutions,null,2)+"\n");
