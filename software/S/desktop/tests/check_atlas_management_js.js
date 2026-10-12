const fs=require('fs'),vm=require('vm'),assert=require('assert');
const elements=new Map();
const element=id=>{if(!elements.has(id))elements.set(id,{innerHTML:'',textContent:'',value:'',checked:false,classList:{add(){},remove(){}}});return elements.get(id);};
const images=[{image_uid:'first',uid:'first#run#1',path:'a.png',folder_id:'folder',style_fields:[{key:'painting',value:'水彩',norm:'水彩'}]},
 {image_uid:'second',uid:'second#run#1',path:'b.png',folder_id:'folder',style_fields:[{key:'brush',value:'水彩',norm:'水彩'}]}];
let pending=[];
const context={D:{images,management_folders:[{id:'folder',name:'作品'}]},document:{addEventListener(){},querySelector(){}},
 $:selector=>element(selector),esc:x=>String(x),baseName:p=>p,thumbSrc:im=>im.path,imgSrc:p=>p,
 SETS:[{id:'default',name:'默认'}],setsOf:()=>[],getSet:()=>null,
 setInterval(){},URLSearchParams,fetch:()=>new Promise(resolve=>pending.push(resolve)),toast(){}};
vm.createContext(context);vm.runInContext(fs.readFileSync('tools/atlas_management.js','utf8'),context);
const run=code=>vm.runInContext(code,context);
(async()=>{
 run("MG.open=true;MG.query={folder_id:'folder',field_key:'painting',field_value:'水彩',day:null};MG.ids=['old'];MG.images=[D.images[0]];");
 const query=run('mgQuery()');
 run('mgPickAll()');assert.equal(run('MG.picked.size'),0,'query in flight must not select old results');
 pending.shift()({ok:true,json:async()=>({matched_ids:['first','second'],images:[images[0]],total:2,page:1})});await query;
 run('mgPickAll()');assert.equal(run('MG.picked.size'),2,'select all includes results beyond page');
 run("MG.query.field_key='brush';MG.ids=['second'];MG.images=[D.images[1]];mgRender()");
 assert.equal(run('MG.picked.size'),2,'selection remains across queries');
 assert(element('#relatedCount').textContent.includes('当前结果选定 1 / 2 · 累计选定 2'));
 run("mgPreview(0)");assert(element('#relatedPreview').innerHTML.includes('b.png'));
 const older=run('mgQuery()'), newer=run('mgQuery()');
 pending[1]({ok:true,json:async()=>({matched_ids:['second'],images:[images[1]],total:1,page:1})});await newer;
 pending[0]({ok:true,json:async()=>({matched_ids:['first'],images:[images[0]],total:1,page:1})});await older;
 assert.equal(run('MG.ids[0]'),'second','late response cannot replace current query');
 run('mgClose()');assert.equal(run('MG.open'),false);
 const editable={sets:[{id:'default',name:'默认',paths:[],note:{},color:0}],active:'default'};
 context.markEditable=editable;context.markRemote={...editable,revision:7,dead:[],dropped:0};
 assert.equal(run('mgMarksKey(markEditable)'),run('mgMarksKey(markRemote)'),'server metadata must not block shared mark refresh');
 context.markRemote.sets=[{...editable.sets[0],paths:['changed']}];
 assert.notEqual(run('mgMarksKey(markEditable)'),run('mgMarksKey(markRemote)'),'unsent edits must still block replacement');
 console.log('PASS: request races, off-page select all, cumulative picks, independent preview and close');
})().catch(e=>{console.error(e);process.exitCode=1;});
