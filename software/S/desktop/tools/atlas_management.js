/* Main gallery and related browser have independent view state. */
const MG={folder:'',day:null,sort:'original',selected:new Set(),open:false,query:null,
  page:1,total:0,ids:[],images:[],picked:new Map(),preview:null,serial:0,busy:false,loading:false};
function mgPrimary(){
  const folders=D.management_folders||[];
  const opts=(values,chosen)=>values.map(([id,label])=>`<option value="${esc(id)}" ${id===chosen?'selected':''}>${esc(label)}</option>`).join('');
  const dates=[...new Set(D.images.filter(im=>!MG.folder||im.folder_id===MG.folder).map(im=>im.day||''))].sort().reverse();
  return `<div class="fg mgtools"><h3>文件夹与生产日期</h3>
    <label>当前文件夹<select id="mgFolder" onchange="mgFilter('folder',this.value)">${opts([['','全部文件夹'],...folders.map(f=>[f.id,f.name+'（'+f.count+'）'])],MG.folder)}</select></label>
    <button onclick="mgCreate()">新建文件夹</button>
    <label>生产日期<select id="mgDate" onchange="mgFilter('day',this.value)">${opts([['*','全部日期'],...dates.map(d=>[d,d||'日期未知'])],MG.day===null?'*':MG.day)}</select></label>
    <label>排序<select id="mgSort" onchange="MG.sort=this.value;apply()">${opts([['original','原顺序'],['newest','生产日期倒序'],['name','文件名']],MG.sort)}</select></label>
    <p>新文件夹用于组织图片；原文件位置保持不变。</p>
    <label>组织到文件夹<select id="mgTarget">${opts(folders.map(f=>[f.id,f.name]),MG.folder)}</select></label>
    <button onclick="mgAssign()">组织选定图片（${MG.selected.size}）</button>
    <button onclick="MG.selected.clear();renderGrid();renderPrimary()">清除组织选择</button>
  </div>`;
}
function mgFilter(key,value){MG[key]=key==='day'&&value==='*'?null:value;apply();}
function mgMatches(im){return (!MG.folder||im.folder_id===MG.folder)&&(MG.day===null||(im.day||'')===MG.day);}
function mgOrder(list){
  if(MG.sort==='newest')list.sort((a,b)=>(b.produced_at||'').localeCompare(a.produced_at||''));
  if(MG.sort==='name')list.sort((a,b)=>baseName(a.path).localeCompare(baseName(b.path)));
  return list;
}
function mgSelect(uid,checked){checked?MG.selected.add(uid):MG.selected.delete(uid);renderPrimary();}
function mgFieldHTML(im){
  return (im.style_fields||[]).map((f,i)=>`<button class="mgfield" data-uid="${esc(im.image_uid||im.uid)}" data-field="${i}" title="在当前文件夹查找同字段同值">${esc(f.label||f.key)}：${esc(f.value)}</button>`).join('')||'<span>无风格字段</span>';
}
document.addEventListener('click',event=>{
  const field=event.target.closest('.mgfield');if(!field)return;
  const im=D.images.find(im=>(im.image_uid||im.uid)===field.dataset.uid);
  if(im)mgOpen(im,im.style_fields[Number(field.dataset.field)]);
});
async function mgWrite(body){
  const r=await fetch('/api/organization',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const result=await r.json();if(!r.ok)throw Error(result.error||'保存失败');
  D=result.data;MG.folder=result.folder_id;MG.day=null;MG.selected.clear();apply();
  return result;
}
async function mgCreate(){
  const name=await mgDialog('新文件夹名称',true);if(!name||!name.trim())return;
  try{await mgWrite({action:'create',name});toast('文件夹已创建，可选定图片后组织到此文件夹');}catch(e){toast(e.message);}
}
async function mgAssign(){
  if(!MG.selected.size){toast('请用图片复选框选定要组织的图片');return;}
  const folder_id=$('#mgTarget').value;if(!folder_id){toast('请先创建文件夹');return;}
  if(!await mgDialog(`将选定的 ${MG.selected.size} 张图片组织到这个文件夹？`))return;
  try{await mgWrite({action:'assign',folder_id,image_uids:[...MG.selected]});toast('图片组织已保存');}catch(e){toast(e.message);}
}
function mgEnsure(){
  if($('#relatedPanel'))return;
  const el=document.createElement('aside');el.id='relatedPanel';el.setAttribute('aria-label','同类图片浏览区');
  el.innerHTML=`<header><b id="relatedTitle">同类图片</b><button onclick="mgClose()">关闭同类</button></header>
    <p id="relatedScope"></p><label><input id="relatedDay" type="checkbox" onchange="MG.page=1;mgQuery()">仅当前生产日期</label>
    <p id="relatedStatus" role="status"></p><div id="relatedPreview"></div><div id="relatedGrid"></div>
    <div id="relatedPager"></div><footer><p id="relatedCount"></p>
      <button onclick="mgPickAll()">全选当前结果</button><button onclick="MG.picked.clear();mgRender()">清除累计选择</button>
      <select id="relatedMark" aria-label="标记目标"></select>
      <button onclick="mgMark('keep')">保留</button><button onclick="mgMark('reject')">淘汰</button>
      <button onclick="mgMark('set')">标记到收藏夹</button>
    </footer>`;
  document.body.append(el);
}
function mgOpen(im,field){
  mgEnsure();
  MG.query={folder_id:MG.folder||im.folder_id,field_key:field.key,field_value:field.norm,
    label:field.label||field.key,day:MG.day};MG.page=1;MG.preview=null;MG.open=true;
  $('#relatedPanel').classList.add('on');$('#relatedDay').checked=false;
  $('#relatedPreview').innerHTML='';
  $('#relatedDay').disabled=MG.day===null;
  $('#relatedTitle').textContent=`${MG.query.label}：${field.value}`;
  const folder=(D.management_folders||[]).find(f=>f.id===MG.query.folder_id);
  $('#relatedScope').textContent='当前文件夹：'+(folder?.name||'未知');
  mgQuery();
}
function mgClose(){MG.open=false;MG.serial++;$('#relatedPanel')?.classList.remove('on');}
async function mgQuery(){
  if(!MG.open||!MG.query)return;
  const serial=++MG.serial;MG.loading=true;MG.ids=[];MG.images=[];MG.total=0;mgRender();
  $('#relatedStatus').textContent='正在查找…';
  const params=new URLSearchParams({folder_id:MG.query.folder_id,field_key:MG.query.field_key,
    field_value:MG.query.field_value,page:String(MG.page),page_size:'24'});
  if($('#relatedDay').checked&&MG.query.day!==null)params.set('date',MG.query.day);
  try{
    const r=await fetch('/api/related?'+params);const result=await r.json();
    if(serial!==MG.serial||!MG.open)return;
    if(!r.ok)throw Error(result.error||'查找失败');
    MG.loading=false;MG.ids=result.matched_ids;MG.images=result.images;MG.total=result.total;MG.page=result.page;
    $('#relatedStatus').textContent=`命中 ${MG.total} 张`;
    mgRender();
  }catch(e){if(serial===MG.serial){MG.loading=false;MG.ids=[];MG.images=[];MG.total=0;mgRender();$('#relatedStatus').textContent=e.message;}}
}
function mgPicked(uid,checked){
  const im=MG.images.find(i=>i.image_uid===uid);if(!im)return;
  checked?MG.picked.set(uid,im):MG.picked.delete(uid);mgCount();
}
function mgPickAll(){
  if(MG.loading){toast('请等待当前结果加载完成');return;}
  const map=new Map(D.images.map(im=>[im.image_uid,im]));
  MG.ids.forEach(uid=>{const im=map.get(uid);if(im)MG.picked.set(uid,im);});mgRender();
}
function mgCount(){
  $('#relatedCount').textContent=`当前结果选定 ${MG.ids.filter(uid=>MG.picked.has(uid)).length} / ${MG.total} · 累计选定 ${MG.picked.size}`;
}
function mgRender(){
  if(!MG.open)return;
  $('#relatedGrid').innerHTML=MG.images.map((im,i)=>`<article class="relatedCard">
    <label><input type="checkbox" aria-label="选定 ${esc(baseName(im.path))}" ${MG.picked.has(im.image_uid)?'checked':''}
      onchange="mgPicked(${JSON.stringify(im.image_uid).replace(/"/g,'&quot;')},this.checked)">选定</label>
    <button class="relatedImage" onclick="mgPreview(${i})"><img loading="lazy" src="${thumbSrc(im)}" data-p="${esc(im.path)}" onerror="imgErr(this)" alt="查看 ${esc(baseName(im.path))}"></button>
    <p>${esc(baseName(im.path))}</p><p>${esc(im.day||'日期未知')}</p>
    <p>${setsOf(im.uid,im.path).map(s=>esc(s.name)).join(' · ')}</p></article>`).join('');
  const pages=Math.max(1,Math.ceil(MG.total/24));
  $('#relatedPager').innerHTML=`<button ${MG.page<=1?'disabled':''} onclick="MG.page--;mgQuery()">上一页</button> 第 ${MG.page} / ${pages} 页 <button ${MG.page>=pages?'disabled':''} onclick="MG.page++;mgQuery()">下一页</button>`;
  const chooser=$('#relatedMark'), selected=chooser.value;
  chooser.innerHTML=SETS.map(s=>`<option value="${esc(s.id)}">${esc(s.name)}</option>`).join('');
  if(getSet(selected))chooser.value=selected;
  mgCount();
}
function mgPreview(index){
  MG.preview=MG.images[index];const im=MG.preview;if(!im)return;
  $('#relatedPreview').innerHTML=`<p>面板预览 · ${esc(baseName(im.path))}</p><img src="${imgSrc(im.path)}" data-p="${esc(im.path)}" onerror="imgErr(this)" alt="同类图片预览"><button onclick="MG.preview=null;$('#relatedPreview').innerHTML=''">关闭预览</button>${mgFieldHTML(im)}`;
}
// Compare editable mark fields; server revision/dead/dropped are transport metadata.
function mgMarksKey(doc){
  return JSON.stringify({sets:(doc?.sets||[]).map(s=>({id:s.id,name:s.name,paths:[...(s.paths||[])],note:s.note||{},color:s.color})),active:doc?.active||''});
}
async function mgMark(kind){
  if(MG.busy||!MG.picked.size)return;
  const n=MG.picked.size;
  const sid=kind==='set'?$('#relatedMark').value:kind==='keep'?'verdict-keep':'verdict-reject';
  if(!sid){toast('请选择标记目标');return;}
  const name=kind==='set'?getSet(sid)?.name:kind==='keep'?'保留':'淘汰';
  if(!await mgDialog(`将累计选定的 ${n} 张图片标记为「${name}」？`))return;
  MG.busy=true;
  const uids=[...MG.picked.values()].map(im=>im.uid);
  const ops=[{set:sid,name,uids}];
  if(kind!=='set')ops.unshift({set:kind==='keep'?'verdict-reject':'verdict-keep',uids,remove:true});
  // Share the gallery's write queue; acknowledged snapshots include these deltas.
  mkQueue=mkQueue.then(async()=>{
    if(mkBlocked||!mkBase)throw Error('收藏尚未同步完成，请重连后再标记');
    const response=await fetch('/api/marks/apply',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ops})});
    const result=await response.json();if(!response.ok)throw Error(result.error||'标记失败');
    // Rebase unsent gallery edits on the newly acknowledged marks.
    const serial=mkEdit,local=cloneDoc(marksDoc()),base=cloneDoc(mkBase);
    const r=await fetch('/api/marks');const m=await r.json();if(!r.ok)throw Error('标记已保存，读取失败，请刷新');
    if(serial===mkEdit&&mgMarksKey(local)===mgMarksKey(base)){
      mkBase=cloneDoc(m);mkAck=cloneDoc(m);acceptMarks(m);refreshMarks();
    }
    mgRender();toast(`已标记 ${n} 张`);
  }).catch(e=>toast(e.message)).finally(()=>{MG.busy=false;});
  await mkQueue;
}
let mgMarksPoll=false;
function mgDialog(message,input=false){
  return new Promise(resolve=>{
    const dialog=document.createElement('dialog');dialog.className='mgdialog';
    dialog.innerHTML=`<form method="dialog"><p>${esc(message)}</p>${input?'<label>文件夹名称<input name="folderName" aria-label="文件夹名称" required maxlength="100"></label>':''}<button value="cancel" formnovalidate>取消</button><button value="accept">确认</button></form>`;
    dialog.addEventListener('close',()=>{
      const value=dialog.returnValue==='accept'?(input?dialog.querySelector('input').value:true):false;
      dialog.remove();resolve(value);
    },{once:true});
    document.body.append(dialog);dialog.showModal();
  });
}
setInterval(async()=>{
  if(!useServer||mgMarksPoll||MG.busy||mkBlocked||!mkBase)return;
  if(mgMarksKey(marksDoc())!==mgMarksKey(mkBase))return;
  mgMarksPoll=true;const serial=mkEdit,baseline=mgMarksKey(mkBase);
  try{
    const r=await fetch('/api/marks');const m=await r.json();if(!r.ok)return;
    if(!MG.busy&&serial===mkEdit&&baseline===mgMarksKey(mkBase)&&baseline===mgMarksKey(marksDoc())){
      mkBase=cloneDoc(m);mkAck=cloneDoc(m);acceptMarks(m);refreshMarks();mgRender();
    }
  }catch(e){}finally{mgMarksPoll=false;}
},5000);
