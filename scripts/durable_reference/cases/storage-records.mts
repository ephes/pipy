/** DE02a1 value/consumed-record probe only; no Python Storage parity.
 * Source-derived Pi durable/Chord MIT (c)2025 Mario Zechner.
 * Full notice: tests/fixtures/durable/UPSTREAM-LICENSE.txt. */
import assert from "node:assert/strict";
import {readFileSync,writeFileSync,realpathSync} from "node:fs";
import {registerHooks} from "node:module";
import {fileURLToPath} from "node:url";
import {resolve,sep} from "node:path";
const root=realpathSync(process.cwd());
const [inputPath,outputPath]=process.argv.slice(2);
assert.ok(inputPath&&outputPath);
const resolutions:{specifier:string,path:string}[]=[];
registerHooks({resolve(specifier,context,nextResolve){
 const result=nextResolve(specifier,context);
 if(result.url.startsWith("node:"))return result;
 assert.ok(result.url.startsWith("file:"));
 const path=realpathSync(fileURLToPath(result.url));
 assert.ok(path.startsWith(root+sep),"Module escaped pinned snapshot");
 resolutions.push({specifier,path:path.slice(root.length+1)});return result;
}});
const {copyJson,isJsonValue}=await import("./packages/chord/src/json.ts");
const {MemoryStorage}=await import("./packages/durable/src/storage/memory.ts");
const {BACKGROUND_CONTEXT:context}=await import("@earendil-works/chord/context");
function string(hex:string):string{
 const bytes=Buffer.from(hex,"hex");let result="";
 for(let i=0;i<bytes.length;i+=2)result+=String.fromCharCode(bytes.readUInt16BE(i));
 return result;
}
function hex(value:string):string{
 const bytes=Buffer.alloc(value.length*2);
 for(let i=0;i<value.length;i++)bytes.writeUInt16BE(value.charCodeAt(i),i*2);
 return bytes.toString("hex");
}
function decode(item:any):any{
 switch(item[0]){
 case "null":return null;case "bool":return item[1];case "string":return string(item[1]);
 case "number":return Buffer.from(item[1],"hex").readDoubleBE();
 case "array":return item[1].map(decode);
 case "object":return Object.fromEntries(item[1].map(([key,value]:any)=>[string(key),decode(value)]));
 default:throw Error("Invalid scalar transport");
 }
}
function encode(value:any):any{
 if(value===null)return ["null"];
 if(typeof value==="boolean")return ["bool",value];
 if(typeof value==="string")return ["string",hex(value)];
 if(typeof value==="number"){
  assert.ok(Number.isFinite(value));const bytes=Buffer.alloc(8);bytes.writeDoubleBE(value);
  return ["number",bytes.toString("hex")];
 }
 if(Array.isArray(value))return ["array",value.map(encode)];
 return ["object",Object.keys(value).map(key=>[hex(key),encode(value[key])]).sort((a,b)=>a[0]<b[0]?-1:a[0]>b[0]?1:0)];
}
const input=JSON.parse(readFileSync(inputPath,"utf8"));
assert.equal(input.schema_version,1);assert.equal(input.phase,"values-records-a1");
const values=input.values.map((value:any)=>{const raw=decode(value);assert.ok(isJsonValue(raw));return encode(copyJson(raw));});
const shared={items:[1]};const source={left:shared,right:shared};const copied:any=copyJson(source);
shared.items.push(2);const sourceDetached=copied.left.items.length===1;
copied.left.items.push(3);const independent=copied.right.items.length===1;
const omitted:any=copyJson({missing:undefined,present:null},{omitUndefinedProperties:true});
let rejectsUndefinedArray=false;try{copyJson([undefined],{omitUndefinedProperties:true});}catch{rejectsUndefinedArray=true;}
const astral="\u{1f600}",pair="\ud83d\ude00";
const keyRecipe=Object.fromEntries([[astral,1],[pair,2]]);
const keyObservation={same_units:astral===pair,key_count:Object.keys(copyJson(keyRecipe)).length,last_value:keyRecipe[astral]};
const rejected=[NaN,Infinity,-Infinity].map(value=>{try{copyJson(value);return false;}catch{return !isJsonValue(value);}});
const storage=new MemoryStorage();let seq:any;const records:any[]=[];
try{
 const writes=input.records.map((record:any)=>{
  const value=decode(record.value);
  if(record.kind==="document"||record.kind==="document.create"){
   const {createdAt,retiredAt,...create}=value;
   return {type:"document.create",record:create,content:{kind:"base",version:1,value:{sample:[null]}}};
  }
  return {type:record.kind,value};
 });
 seq=await storage.commit(writes,context);
 for(const record of input.records){
  const expected=decode(record.value);let observed:any;
  switch(record.kind){
   case "conversation":observed=await storage.conversation(expected.id,context);break;
   case "entry":observed=(await storage.entry(expected.id,context))?.entry;break;
   case "task":observed=await storage.task(expected.id,context);break;
   case "submission":observed=await storage.submission(expected.id,context);break;
   case "document":observed=(await storage.document(expected.id,"current",context))?.record;break;
   case "document.create":{
    const stored=await storage.findDocument({kind:expected.kind,scope:expected.scope,...("key" in expected?{key:expected.key}:{})},"current",context);
    assert.ok(stored);const {createdAt,retiredAt,...create}=stored;observed=create;break;
   }
   default:throw Error("Unknown record family");
  }
  assert.ok(observed);records.push({kind:record.kind,value:encode(observed)});
 }
 const first:any=input.records.find((record:any)=>record.kind==="entry");const id=decode(first.value).id;
 const retained=(await storage.entry(id,context))!.entry as any;
 retained.data.nested.push("caller mutation");
 const again=(await storage.entry(id,context))!.entry as any;
 const readDetached=again.data.nested.length===1;
 writeFileSync(resolve(outputPath,"observed.json"),JSON.stringify({schema_version:1,phase:"values-records-a1",values,records,facts:{source_detached:sourceDetached,placements_independent:independent,read_detached:readDetached,omission_distinct:!("missing" in omitted)&&omitted.present===null&&rejectsUndefinedArray,nonfinite_rejected:rejected,utf16_key_identity:keyObservation.same_units}},null,2)+"\n");
 writeFileSync(resolve(outputPath,"reference-context.json"),JSON.stringify({schema_version:1,backend:"Node MemoryStorage only",commit_seq:seq,record_count:records.length,utf16_key_recipe:keyObservation,python_storage_implemented:false},null,2)+"\n");
}finally{await storage.close(context);}
writeFileSync(resolve(outputPath,"resolutions.json"),JSON.stringify(resolutions,null,2)+"\n");
