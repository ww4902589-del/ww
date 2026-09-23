/* ComfyBatch 前端脚本。

原先分散在两个内联脚本块里，两块共享同一个全局词法作用域，
合并为一个文件不改变语义，只是去掉了那个与职责无关的切分。
按模块分段：状态与工具 / 本地环境 / 导入与任务 / 工作流 /
风格与 LoRA / 参数工作台 / 检查 / 运行 / 审图与确认 / 抽屉 /
租约与事件流。 */

let inventory={
  styles:[],loras:[],models:[],workflows:[],style_lora_presets:[],image_presets:[]}
,
bundle=null,
importMapping=null,
selectedLoras=[],
selectedStyles=[],
comfyConnected=false,
loraSaveTimers={
}
,
selectedPromptIndexes=new Set();
let reviewPicks=new Set();
let dupGroups=new Set();
let activePresetId='';
// 后端预设指纹：变化说明别的页面改了预设库，需要静默重拉一次 inventory。
// '' 表示还没收到过任何快照，用于首次只记基准、不触发重拉。
let presetsRev='';
const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"']/g,
c=>({
  '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}
[c]));
// 模型引用在两种分隔符下都出现过，取文件名时统一处理，和 Python 侧的
// model_basename 保持一致。
const basename=s=>String(s??'').replace(/\\/g,'/').split('/').pop();

/* ---- 共享状态 ----------------------------------------------------------
所有渲染都从这里读。集中声明在文件前部，原因不是整洁：goStep 在启动时会
调用真相栏，而真相栏要读 lastInspect / reviewState。这些变量若声明在启动
调用之后，就处在暂时性死区，启动时会直接抛错并让整段脚本停止执行——
页面能看，但什么都不工作。
-------------------------------------------------------------------- */
let reviewState={
  run_id:'',results:[],review:{
  }
  ,statuses:[],redo_modes:[],aborted_reason:''}
;
let lastInspect={
}
;
let railStatus=null;
let activeIndex=-1;
let batchOverrides={
}
;
let currentStep=Number(localStorage.getItem('comfybatch-step')||1),
pollTimer=null,
lastRunStatus='';
// 四个阶段：1 文件与任务 / 2 生成配置（工作流、模型、风格、LoRA、图像规格、参数） /
// 3 检查与运行 / 4 成图与确认。进入第 2 阶段时惰性读取参数清单。
const LAST_STEP=4;
function goStep(step){
  currentStep=Math.max(1,Math.min(LAST_STEP,Number(step)||1));
  localStorage.setItem('comfybatch-step',String(currentStep));
  document.querySelectorAll('.step-panel').forEach(x=>x.classList.toggle('active',Number(x.dataset.step)===currentStep));
  document.querySelectorAll('.step-tab').forEach(x=>x.classList.toggle('active',Number(x.dataset.stepTarget)===currentStep));
  $('backStep').disabled=currentStep===1;
  $('nextStep').disabled=currentStep===LAST_STEP;
  window.scrollTo({
    top:0,behavior:'smooth'}
  );
  if(currentStep===2&&!workbench)loadWorkbench();
  renderRail()}
function changeStep(delta){
  goStep(currentStep+delta)}
function notify(message,type='success'){
  const host=$('toasts'),row=document.createElement('div');
  row.className='toast '+type;
  row.textContent=message;
  host.appendChild(row);
  setTimeout(()=>row.remove(),3200)}
function setBusy(button,busy,label='处理中'){
  if(!button)return;
  if(busy){
    button.dataset.oldText=button.textContent;
    button.textContent=label;
    button.classList.add('busy');
    button.disabled=true}
  else{
    button.textContent=button.dataset.oldText||button.textContent;
    button.classList.remove('busy');
    button.disabled=false}
}
async function api(path,options={
}
){
  // Every write carries this page's id so the server can enforce the editing lease.
  if(options.method==='POST'&&typeof withClient==='function'){
    let parsed=null;
    try{
      parsed=options.body?JSON.parse(options.body):{
      }
    }
    catch(e){
      parsed=null}
    if(parsed&&typeof parsed==='object')options=Object.assign({
    }
    ,options,{
      body:JSON.stringify(withClient(parsed))}
    );
  }
  const r=await fetch(path,{
    headers:{
      'Content-Type':'application/json'}
    ,...options}
  );
  const v=await r.json();
  if(!v.ok){
    const e=new Error(v.error||'请求失败');
    e.payload=v;
    throw e}
  return v}
function opts(el,rows,label,value){
  el.innerHTML=rows.map(x=>`<option value="${
  esc(value(x))}">${esc(label(x))}</option>`).join('')||'<option value="">未发现</option>'}
function configurePayload(){
  return {
    comfy_root:$('comfyRoot').value,comfy_url:$('comfyUrl').value,workflow_roots:[$('workflowRoot').value],output_root:$('outputRoot').value}
}
async function fetchInventory(quiet){
  // 静默重拉给 SSE 后台同步和按钮共用：同一段请求/替换逻辑，避免两处漂移。
  const v=await api('/api/configure',{method:'POST',body:JSON.stringify(configurePayload())});
  inventory=v.inventory;
  renderInventory();
  if(!quiet)notify('本地资源扫描完成')}
async function refreshInventory(button){
  setBusy(button,true,'正在扫描');
  try{
    await fetchInventory(false)}
  catch(e){
    notify(e.message,'error')}
  finally{
    setBusy(button,false)}
}
function triggerLabel(status){
  return status==='manual'?'已记忆触发词':status==='confirmed'?'已读取触发词':'触发词未知'}
function presets(){
  return inventory.style_lora_presets||[]}
function presetById(id){
  return presets().find(x=>x.id===id)}
function imagePresets(){
  return inventory.image_presets||[]}
function imagePresetById(id){
  return imagePresets().find(x=>x.id===id)}
function renderPresetOptions(){
  const a=activePresetId||$('presetPicker').value,b=$('assignPreset').value;
  $('presetPicker').innerHTML='<option value="">自定义全局配置（未载入设定）</option>'+presets().map(x=>`<option value="${
  esc(x.id)}">${esc(x.name)}</option>`).join('');
  opts($('assignPreset'),presets(),x=>x.name,x=>x.id);
  if(presets().some(x=>x.id===a))$('presetPicker').value=a;
  if(presets().some(x=>x.id===b))$('assignPreset').value=b;
  showPresetName()}
function renderImagePresetOptions(){
  const global=$('globalImagePreset').value,assigned=$('assignImagePreset').value;
  opts($('globalImagePreset'),imagePresets(),x=>x.name,x=>x.id);
  opts($('assignImagePreset'),imagePresets(),x=>x.name,x=>x.id);
  const defaultPreset=imagePresets().find(x=>x.id==='wide-m')||imagePresets()[0];
  $('globalImagePreset').value=imagePresetById(global)?global:(defaultPreset?.id||'');
  if(imagePresetById(assigned))$('assignImagePreset').value=assigned;
  applyGlobalImagePreset()}
function applyGlobalImagePreset(){
  const preset=imagePresetById($('globalImagePreset').value);
  if(preset)$('aspect').value=preset.aspect_ratio;
  renderImageSpecSummary();
  renderConfigFeedback()}
function currentImagePreset(){
  return imagePresetById($('globalImagePreset').value)}
function renderImageSpecSummary(){
  const summary=$('imageSpecSummary'),meta=$('imageSpecMeta');
  if(!summary||!meta)return;
  const preset=currentImagePreset(),aspect=$('aspect')?.value||'未选择';
  const assigned=bundle?(bundle.items||[]).filter(x=>x.metadata?.image_preset_id).length:0;
  summary.textContent=`${preset?.name||'自定义规格'} · ${aspect}`;
  meta.textContent=`像素预算 ${Number(preset?.megapixels||1.2).toFixed(1)} MP${assigned?` · ${assigned} 条任务使用独立规格`:''} · 最终尺寸以实际检查为准。`;
}
function showPresetName(){
  const x=presetById($('presetPicker').value);
  if(x)$('presetName').value=x.name}
async function savePreset(asNew){
  const id=asNew?'':$('presetPicker').value,name=$('presetName').value.trim();
  if(!name)return alert('请输入预设名称');
  if(!asNew&&!id)return alert('请先选择要更新的预设');
  try{
    const v=await api('/api/save-style-lora-preset',{method:'POST',body:JSON.stringify({id,name,styles:selectedStyles,loras:selectedLoras})});
    inventory.style_lora_presets=v.presets;
    activePresetId=v.preset.id;
    renderPresetOptions();
    $('presetPicker').value=v.preset.id;
    $('assignPreset').value=v.preset.id;
    $('presetName').value=v.preset.name;
    renderBundle();
    renderConfigFeedback()}
  catch(e){
    alert(e.message)}
}
function enrichPresetLora(x){
  const source=inventory.loras.find(row=>row.value===x.name)||{
  }
  ;
  return {
    ...x,display_name:source.display_name||source.name||x.name,trigger_words:[...(source.trigger_words||[])],trigger_status:source.trigger_status||'unknown',use_triggers:x.use_triggers!==false}
}
function enrichPresetStyle(x){
  const source=inventory.styles.find(row=>row.library===x.catalog&&row.name===x.name)||{
  }
  ;
  return {
    ...source,...x,display_name:source.display_name||source.name_cn||x.name,thumbnail:source.thumbnail||''}
}
function loadPreset(fromSelection=false){
  const x=presetById($('presetPicker').value);
  if(!x){
    activePresetId='';
    renderConfigFeedback();
    if(!fromSelection)alert('没有可载入的预设');
    return}
  activePresetId=x.id;
  $('presetName').value=x.name;
  selectedStyles=(x.styles||[]).map(enrichPresetStyle);
  selectedLoras=(x.loras||[]).map(enrichPresetLora);
  renderStyleBasket();
  renderLoras()}
async function deletePreset(){
  const id=$('presetPicker').value,x=presetById(id);
  if(!x)return alert('没有可删除的预设');
  if(!confirm(`删除预设“${x.name}”？已使用它的提示词将恢复全局配置。`))return;
  try{
    const v=await api('/api/delete-style-lora-preset',{method:'POST',body:JSON.stringify({id})});
    inventory.style_lora_presets=v.presets;
    bundle=v.bundle||bundle;
    if(activePresetId===id)activePresetId='';
    $('presetName').value='';
    renderPresetOptions();
    renderBundle();
    renderConfigFeedback()}
  catch(e){
    alert(e.message)}
}
async function restoreDefaultPresets(btn){
  if(!confirm('恢复全部默认风格／LoRA 预设？已删除的默认预设会重新出现。'))return;
  setBusy(btn,true);
  try{
    const v=await api('/api/restore-default-presets',{method:'POST',body:'{}'});
    inventory.style_lora_presets=v.presets;
    renderPresetOptions();
    notify(`已恢复 ${v.restored||0} 个默认预设`,'success');
  }
  catch(e){
    notify(e.message,'error')}
  finally{
    setBusy(btn,false)}
}
async function clearSavedParams(btn){
  if(!confirm('清除当前工作流已保存的参数默认值？页面输入不受影响，只删除「记住的默认值」。'))return;
  setBusy(btn,true);
  try{
    await api('/api/params/clear',{method:'POST',body:JSON.stringify({workflow_path:$('workflow').value})});
    notify('已清除当前工作流保存的参数默认值','success');
    if(typeof loadWorkbench==='function')loadWorkbench();
  }
  catch(e){
    notify(e.message,'error')}
  finally{
    setBusy(btn,false)}
}
function updateSelectionStatus(){
  $('selectionStatus').textContent=`已选择 ${selectedPromptIndexes.size} 条${bundle?' / 共 '+bundle.items.length+' 条':''}`}
function togglePrompt(index,checked){
  checked?selectedPromptIndexes.add(index):selectedPromptIndexes.delete(index);
  renderBundle()}
function parseRangeText(text,total){
  const result=new Set();
  for(const token of text.trim().split(/[,，;；\s]+/).filter(Boolean)){
    const m=token.match(/^(\d+)\s*[-—~至]\s*(\d+)$/);
    if(m){
      let a=Number(m[1]),b=Number(m[2]);
      if(a>b)[a,b]=[b,a];
      for(let i=a;i<=b;i++)result.add(i)}
    else if(/^\d+$/.test(token))result.add(Number(token));
    else throw new Error('无法识别的编号：'+token)}
  for(const i of result)if(i<1||i>total)throw new Error(`编号 ${i} 超出范围 1-${total}`);
  return result}
function selectRange(){
  if(!bundle)return alert('请先导入提示词合集');
  try{
    selectedPromptIndexes=parseRangeText($('promptRange').value,bundle.items.length);
    renderBundle()}
  catch(e){
    alert(e.message)}
}
function selectAllPrompts(){
  if(!bundle)return;
  selectedPromptIndexes=new Set(bundle.items.map((_,i)=>i+1));
  renderBundle()}
function invertPromptSelection(){
  if(!bundle)return;
  selectedPromptIndexes=new Set(bundle.items.map((_,i)=>i+1).filter(i=>!selectedPromptIndexes.has(i)));
  renderBundle()}
function clearPromptSelection(){
  selectedPromptIndexes.clear();
  renderBundle()}
async function assignPresetToSelection(clear=false){
  if(!bundle)return alert('请先导入提示词合集');
  if(!selectedPromptIndexes.size)return alert('请先选择提示词');
  const presetId=clear?'':$('assignPreset').value;
  if(!clear&&!presetId)return alert('请先保存或选择预设');
  try{
    const v=await api('/api/assign-style-lora-preset',{method:'POST',body:JSON.stringify({preset_id:presetId,indexes:[...selectedPromptIndexes]})});
    bundle=v.bundle;
    $('assignmentFeedback').textContent=clear?`已让 ${selectedPromptIndexes.size} 条提示词恢复全局风格与 LoRA。`:`已为 ${selectedPromptIndexes.size} 条提示词分配：${presetById(presetId)?.name||'设定'}。`;
    renderBundle()}
  catch(e){
    alert(e.message)}
}
async function assignImagePresetToSelection(clear=false){
  if(!bundle)return alert('请先导入提示词合集');
  if(!selectedPromptIndexes.size)return alert('请先选择提示词');
  const presetId=clear?'':$('assignImagePreset').value;
  if(!clear&&!presetId)return alert('请先选择图像规格');
  try{
    const v=await api('/api/assign-image-preset',{method:'POST',body:JSON.stringify({preset_id:presetId,indexes:[...selectedPromptIndexes]})});
    bundle=v.bundle;
    $('assignmentFeedback').textContent=clear?`已让 ${selectedPromptIndexes.size} 条提示词恢复全局图像规格。`:`已为 ${selectedPromptIndexes.size} 条提示词分配：${imagePresetById(presetId)?.name||'图像规格'}。`;
    renderBundle()}
  catch(e){
    alert(e.message)}
}
function renderLoraOptions(){
  const model=inventory.models.find(x=>x.value===$('model').value)||{
  }
  ;
  const rows=inventory.loras.filter(x=>!model.family||model.family==='unknown'||x.family==='unknown'||x.family===model.family);
  opts($('loraPick'),rows,x=>`${x.display_name||x.value} · ${triggerLabel(x.trigger_status)} · ${x.family||'unknown'}`,x=>x.value)}
function styleSource(catalog=$('styleLibrary').value,name=$('styleName').value){
  return inventory.styles.find(x=>x.library===catalog&&x.name===name)}
function styleThumbnailUrl(x){
  return x?.thumbnail?`/api/style-thumbnail?library=${encodeURIComponent(x.library||x.catalog)}&name=${encodeURIComponent(x.name)}`:''}
function renderStyles(){
  const lib=$('styleLibrary').value;
  opts($('styleName'),inventory.styles.filter(x=>x.library===lib),x=>x.display_name||x.name_cn||x.name,x=>x.name);
  renderStylePreview()}
function renderStylePreview(){
  const x=styleSource(),url=styleThumbnailUrl(x);
  $('stylePreview').innerHTML=x?`${url?`<img src="${
  esc(url)}" alt="${
  esc(x.display_name||x.name)}" onerror="this.style.display='none'">`:''}<div>
  <b>${esc(x.display_name||x.name_cn||x.name)}</b>
  <small>原名：${esc(x.name)}</small>
  <small>${url?'预览图来自原风格节点':'暂无预览图'}</small>
  </div>`:'<div>尚未发现可用风格</div>'}
function addStyle(){
  const catalog=$('styleLibrary').value,name=$('styleName').value;
  if(!catalog||!name||selectedStyles.some(x=>x.catalog===catalog&&x.name===name))return;
  if(selectedStyles.length>=4)return alert('一次最多组合4个风格');
  const source=styleSource(catalog,name)||{
  }
  ;
  selectedStyles.push({...source,catalog,name});
  renderStyleBasket()}
function renderStyleBasket(){
  $('styles').innerHTML=selectedStyles.map((x,i)=>{const url=styleThumbnailUrl(x);return `<div class="chip chip-with-thumb">${url?`<img class="style-thumb" src="${
    esc(url)}" alt="${
    esc(x.display_name||x.name)}" onerror="this.style.visibility='hidden'">`:'<div class="style-thumb"></div>'}<div>
    <b>${i===0?'主风格':'辅助风格'}：${esc(x.display_name||x.name_cn||x.name)}</b>
    <small>原名：${esc(x.name)}</small>
    <small>${esc(x.prompt||'该风格没有可预览的模板文字')}</small>
    </div>
    <button class="danger" onclick="selectedStyles.splice(${
    i},1);renderStyleBasket()">移除</button>
    </div>`}).join('');
  renderConfigFeedback()}
function addLora(){
  const name=$('loraPick').value,source=inventory.loras.find(x=>x.value===name);
  if(source&&!selectedLoras.some(x=>x.name===name)){
    selectedLoras.push({name,display_name:source.display_name||source.name||name,strength:1,trigger_words:[...(source.trigger_words||[])],trigger_status:source.trigger_status,use_triggers:source.use_triggers!==false});
    renderLoras()}
}
async function rememberLora(index){
  const x=selectedLoras[index];
  if(!x)return;
  try{
    const v=await api('/api/save-lora-profile',{method:'POST',body:JSON.stringify({name:x.name,display_name:x.display_name,trigger_words:x.trigger_words,use_triggers:x.use_triggers})});
    Object.assign(x,v.profile);
    const source=inventory.loras.find(row=>row.value===x.name);
    if(source)Object.assign(source,v.profile);
    renderLoraOptions()}
  catch(e){
    alert('LoRA 资料保存失败：'+e.message)}
}
function updateLoraTriggers(index,value){
  selectedLoras[index].trigger_words=value.split(/[,，;；]/).map(x=>x.trim()).filter(Boolean)}
function renderLoras(){
  $('loras').innerHTML=selectedLoras.map((x,i)=>`<div class="lora-row">
  <label>中文名称（自动记忆）<input value="${
  esc(x.display_name||x.name)}" oninput="selectedLoras[${
  i}].display_name=this.value" onchange="rememberLora(${
  i})">
  <small title="${
  esc(x.name)}">文件：${esc(x.name)}</small>
  </label>
  <label>强度<input type="number" min="-2" max="2" step="0.05" value="${
  x.strength}" onchange="selectedLoras[${
  i}].strength=Number(this.value);renderConfigFeedback()">
  </label>
  <label>触发词（自动记忆）<input value="${
  esc((x.trigger_words||[]).join(', '))}" oninput="updateLoraTriggers(${
  i},this.value)" onchange="rememberLora(${
  i})">
  </label>
  <label class="switch">
  <input type="checkbox" ${x.use_triggers?'checked':''} onchange="selectedLoras[${
  i}].use_triggers=this.checked;rememberLora(${
  i})">使用触发词</label>
  <button class="secondary" onclick="deleteLoraProfile('${
  esc(x.name)}')">删除档案</button>
  <button class="danger" onclick="selectedLoras.splice(${
  i},1);renderLoras()">×</button>
  </div>`).join('');
  renderConfigFeedback()}
async function deleteLoraProfile(name){
  if(!confirm(`删除 LoRA 档案“${name}”？只是删除软件内保存的触发词档案，不动模型文件。`))return;
  try{
    await api('/api/delete-lora-profile',{method:'POST',body:JSON.stringify({name})});
    // 档案只影响触发词记忆，行内已填的值保留；下次资源扫描后 trigger_status 会同步为未记忆。
    notify(`已删除档案 ${name}`,'success');
  }
  catch(e){
    notify(e.message,'error')}
}
function toggleLLM(){
  $('llmFields').classList.toggle('hidden',!$('llmEnabled').checked)}
$('bundleFile').addEventListener('change',async e=>{const f=e.target.files[0];if(!f)return;const input=e.target;input.disabled=true;notify(`正在读取 ${f.name}`,'info');try{const bytes=new Uint8Array(await f.arrayBuffer());let binary='';for(let i=0;i<bytes.length;i+=8192)binary+=String.fromCharCode(...bytes.subarray(i,i+8192));const v=await api('/api/import',{method:'POST',body:JSON.stringify({filename:f.name,base64:btoa(binary)})});bundle=v.bundle;importMapping=v.mapping||null;$('mappingButton').disabled=!importMapping;bundle.items.forEach(x=>x.count=1);selectedPromptIndexes.clear();renderBundle();notify(`已提取 ${bundle.items.length} 条任务`);precheckAfterImport()}catch(err){notify(err.message,'error')}finally{input.disabled=false}});
async function fileBase64(file){
  const bytes=new Uint8Array(await file.arrayBuffer());
  let binary='';
  for(let i=0;i<bytes.length;i+=8192)binary+=String.fromCharCode(...bytes.subarray(i,i+8192));
  return btoa(binary)}
$('imageFiles').addEventListener('change',async e=>{const files=[...e.target.files];if(!files.length)return;const input=e.target;input.disabled=true;notify(`正在导入 ${files.length} 张图片`,'info');try{const rows=[];for(let i=0;i<files.length;i++){rows.push({filename:files[i].name,base64:await fileBase64(files[i])});$('bundleInfo').textContent=`正在读取图片 ${i+1}/${files.length}`};const v=await api('/api/import-images',{method:'POST',body:JSON.stringify({mode:$('imageMode').value,files:rows})});bundle=v.bundle;importMapping=null;$('mappingButton').disabled=true;bundle.items.forEach(x=>x.count=1);selectedPromptIndexes.clear();renderBundle();notify(`已建立 ${bundle.items.length} 个图片任务`);precheckAfterImport()}catch(err){notify(err.message,'error')}finally{input.disabled=false}});
$('extractImageUrl').addEventListener('click',async()=>{const url=$('imageUrl').value.trim();if(!url){notify('请先粘贴网页、视频或图片链接','error');return}const button=$('extractImageUrl');setBusy(button,true,'正在提取');$('bundleInfo').textContent='正在读取公开网页图片';try{const v=await api('/api/extract-images',{method:'POST',body:JSON.stringify({url,mode:$('imageMode').value})});bundle=v.bundle;importMapping=null;$('mappingButton').disabled=true;bundle.items.forEach(x=>x.count=1);selectedPromptIndexes.clear();renderBundle();const covers=(v.extracted||[]).filter(x=>x.kind==='bilibili-cover').length;notify(`已提取 ${bundle.items.length} 张图片${covers?'（含 B 站封面）':''}`);precheckAfterImport()}catch(err){notify(err.message,'error')}finally{setBusy(button,false)}});
function mappingOptions(el,allowBlank=false){
  const rows=importMapping?.headers||[];
  el.innerHTML=(allowBlank?'<option value="">不使用此列</option>':'')+rows.map(x=>`<option value="${
  esc(x)}">${esc(x)}</option>`).join('')}
function openMappingDialog(){
  if(!importMapping)return alert('请先导入 Excel 文件');
  mappingOptions($('mappingTitle'),true);
  mappingOptions($('mappingPositive'));
  mappingOptions($('mappingNegative'),true);
  const chosen=importMapping.selected||importMapping.detected||{
  }
  ;
  $('mappingTitle').value=chosen.title||'';
  $('mappingPositive').value=chosen.positive||'';
  $('mappingNegative').value=chosen.negative||'';
  $('mappingMetadata').value=Array.isArray(chosen.metadata)?chosen.metadata.join('，'):'';
  $('mappingDialog').showModal()}
async function applyMapping(){
  const mapping={
    title:$('mappingTitle').value,positive:$('mappingPositive').value,negative:$('mappingNegative').value,metadata:$('mappingMetadata').value}
  ;
  try{
    const v=await api('/api/remap-import',{method:'POST',body:JSON.stringify({mapping})});
    bundle=v.bundle;
    importMapping={
      ...(v.mapping||importMapping),selected:mapping}
    ;
    bundle.items.forEach(x=>x.count=1);
    selectedPromptIndexes.clear();
    renderBundle();
    $('mappingDialog').close()}
  catch(e){
    alert(e.message)}
}
async function control(action,button){
  if(action==='cancel'&&!confirm('确定取消剩余任务？已提交的任务会继续完成当前这张。'))return;
  const labels={
    pause:'正在暂停',resume:'正在继续',cancel:'正在取消'}
  ;
  setBusy(button,true,labels[action]||'处理中');
  try{
    await api('/api/'+action,{method:'POST',body:'{}'});
    notify({pause:'已暂停提交新任务',resume:'已继续运行',cancel:'已取消剩余任务'}[action]||'操作完成');
    poll(true)}
  catch(e){
    notify(e.message,'error')}
  finally{
    setBusy(button,false)}
}
function currentWorkflow(){
  return inventory.workflows.find(x=>x.value===$('workflow').value)||{
  }
}
function currentWorkflowVariant(){
  return (currentWorkflow().variants||[]).find(x=>x.id===$('workflowVariant').value)||null}
function renderWorkflowVariants(){
  const workflow=currentWorkflow(),old=$('workflowVariant').value,rows=workflow.variants||[];
  opts($('workflowVariant'),rows,x=>`${x.name} · ${x.sampler_count||0}次采样${x.upscale_count?` · ${x.upscale_count}个放大步骤`:''}`,x=>x.id);
  const preferred=rows.find(x=>x.id===old)||rows.find(x=>x.active)||rows[0];
  if(preferred)$('workflowVariant').value=preferred.id;
  renderModels()}
function renderModels(){
  const workflow=currentWorkflow();
  // The server already decided which models this workflow may load and ships
  // them with the workflow. Rebuilding the filter here is what let the page
  // offer a different set than /api/start accepts.
  const rows=workflow.compatible_models||[];
  const old=$('model').value;
  // Label with the bare file name: the folder a model sits in is the loader's
  // business, and a relative path is noise in a dropdown.
  opts($('model'),rows,x=>`${basename(x.name||x.value)} [${x.family||'未知'}]`,x=>x.value);
  if(rows.some(x=>x.value===old))$('model').value=old;
  else{
    const red=rows.find(x=>/redcraft|红潮|红red/i.test(x.name||x.value));
    if(red)$('model').value=red.value}
  renderLoraOptions();
  renderConfigFeedback()}
function presetSignature(styles,loras){
  return JSON.stringify({styles:(styles||[]).map(x=>[x.catalog,x.name]),loras:(loras||[]).map(x=>[x.name,Number(x.strength||1),x.use_triggers!==false])})}
function taskPresetSummaries(){
  if(!bundle)return[];
  const groups=new Map();
  for(const item of bundle.items||[]){
    const id=item.metadata?.style_lora_preset_id||'';
    if(!id)continue;
    groups.set(id,(groups.get(id)||0)+Number(item.count||1))}
  return [...groups].map(([id,count])=>({id,count,preset:presetById(id)}))}
function renderConfigFeedback(){
  const el=$('configFeedback');
  if(!el)return;
  const chosen=selectedStyles.length?selectedStyles.map(x=>x.display_name||x.name_cn||x.name).join(' + '):(styleSource()?.display_name||styleSource()?.name_cn||styleSource()?.name||'未选择');
  const loras=selectedLoras.length?selectedLoras.map(x=>`${x.display_name||x.name} ×${Number(x.strength||1).toFixed(2)}`).join('，'):'未添加';
  const image=currentImagePreset(),active=presetById(activePresetId),exact=active&&presetSignature(active.styles,active.loras)===presetSignature(selectedStyles,selectedLoras);
  const globalLabel=active?`${active.name}${exact?'':'（已临时修改）'}`:'自定义全局配置';
  const taskRows=taskPresetSummaries(),taskText=taskRows.length?taskRows.map(x=>{if(!x.preset)return esc(`已失效设定 ${x.id}：${x.count} 个任务`);const styles=(x.preset.styles||[]).map(s=>enrichPresetStyle(s).display_name||s.name).join(' + ')||'无风格';const ls=(x.preset.loras||[]).map(l=>`${enrichPresetLora(l).display_name||l.name} ×${Number(l.strength||1).toFixed(2)}`).join('，')||'无 LoRA';return esc(`${x.preset.name}：${x.count} 个任务；风格 ${styles}；LoRA ${ls}`)}).join('<br>'):'未分配（全部使用全局配置）',variant=currentWorkflowVariant();
  el.innerHTML=`<b>当前配置反馈（这里显示的就是启动时实际写入工作流的配置）</b>
  <div>执行分支：${esc(variant?.name||'当前已启用分支')} · ${variant?.sampler_count||0}次采样${variant?.upscale_count?` · ${variant.upscale_count}个放大步骤`:''}</div>
  <div>模型：${esc($('model')?.value||'未选择')}</div>
  <div>全局设定：${esc(globalLabel)}</div>
  <div>全局风格：${esc(chosen)} · ${selectedStyles.length?`已加入 ${selectedStyles.length} 个组合风格`:'使用当前具体风格'} · ${$('styleApplication')?.value==='native'?'原生组合器':'提示词融合'}</div>
  <div>全局 LoRA：${esc(loras)}</div>
  <div>任务级设定（优先覆盖全局）：${taskText}</div>
  <div>全局图像规格：${esc(image?.name||'自定义')} · 实际比例 ${esc($('aspect')?.value||'未选择')} · ${Number(image?.megapixels||1.2).toFixed(1)} MP</div>`}
function renderInventory(){
  comfyConnected=!!inventory.comfy_connected;
  const c=$('connection');
  c.textContent=comfyConnected?'ComfyUI 已连接':'ComfyUI 未连接：请先启动 ComfyUI';
  c.className='status '+(comfyConnected?'ok':'bad');
  opts($('workflow'),inventory.workflows.filter(x=>x.compatible),x=>x.name,x=>x.value);
  const libraryMap=new Map();
  inventory.styles.forEach(x=>{if(!libraryMap.has(x.library))libraryMap.set(x.library,x.library_cn||x.library)});
  const libs=[...libraryMap].map(([value,label])=>({value,label}));
  opts($('styleLibrary'),libs,x=>x.label,x=>x.value);
  const twoD=[...$('styleLibrary').options].find(x=>/Anime-Cel|anime_/i.test(x.value));
  if(twoD)$('styleLibrary').value=twoD.value;
  renderWorkflowVariants();
  renderStyles();
  renderPresetOptions();
  renderImagePresetOptions();
  renderPurposeOptions();
  renderBundle();
  renderConfigFeedback();
  const output=$('batchOutputDir');
  if(output&&!output.value)output.value=$('outputRoot')?.value||'';
  $('counts').textContent=`发现 ${inventory.workflows.length} 个工作流、${inventory.models.length} 个模型、${inventory.loras.length} 个 LoRA、${inventory.styles.length} 个风格、${presets().length} 个持久预设、${imagePresets().length} 个图像规格。`;
  $('start').disabled=!comfyConnected}
function renderBundle(){
  if(!bundle){
    updateSelectionStatus();
    renderImageSpecSummary();
    renderConfigFeedback();
    return}
  selectedPromptIndexes=new Set([...selectedPromptIndexes].filter(i=>i>=1&&i<=bundle.items.length));
  const expanded=bundle.items.reduce((sum,x)=>sum+Number(x.count||1),0),kind=bundle.source_format==='images'?'图片':'提示词';
  $('bundleInfo').textContent=`${bundle.name} · 提取 ${bundle.items.length} 条${kind}任务 · 实际任务 ${expanded} 个 · ${bundle.source_format.toUpperCase()}`;
  $('preview').innerHTML=bundle.items.map((x,i)=>{const n=i+1,id=x.metadata?.style_lora_preset_id||'',p=presetById(id),label=p?.name||'';const imageId=x.metadata?.image_preset_id||'',image=imagePresetById(imageId),imageLabel=image?.name||x.metadata?.image_preset_name||'全局图像规格',source=x.metadata?.source_image||'',dims=x.metadata?.source_dimensions||[],sourceBlock=source?`<div class="source-preview">
    <img loading="lazy" src="/api/source-image?path=${
    encodeURIComponent(source)}" alt="${
    esc(x.title)}">
    <div class="source-meta">
    <b>${esc(x.metadata?.processing_mode_name||'图片任务')}</b>
    <div>原文件：${esc(x.metadata?.source_filename||'')}</div>
    <div>原尺寸：${esc(dims.length?dims.join('×'):'未知')}</div>
    <div>输入位置：${esc(source)}</div>
    </div>
    </div>`:'';return `<div class="item ${
    selectedPromptIndexes.has(n)?'selected':''}">
    <div class="task-head">
    <input class="pick" type="checkbox" ${selectedPromptIndexes.has(n)?'checked':''} onchange="togglePrompt(${
    n},this.checked)">
    <input value="${
    esc(x.title)}" oninput="bundle.items[${
    i}].title=this.value">
    <label>数量<input type="number" min="1" max="99" value="${
    x.count||1}" onchange="bundle.items[${
    i}].count=Math.max(1,Number(this.value));renderBundle()">
    </label>
    <button class="secondary" onclick="duplicateTask(${
    i})">复制</button>
    <button class="danger" onclick="deleteTask(${
    i})">×</button>
    </div>${sourceBlock}${label?`<span class="preset-badge">风格预设：${esc(label)}</span>`:'<span class="preset-badge">风格：全局配置</span>'}<span class="image-badge">图像：${esc(imageLabel)}</span>
    <label>正面提示词<textarea oninput="bundle.items[${
    i}].prompt=this.value">${esc(x.prompt)}</textarea>
    </label>
    <label>本任务负面提示词（可留空）<textarea oninput="bundle.items[${
    i}].negative_prompt=this.value" placeholder="只作用于这一条任务">${esc(x.negative_prompt||'')}</textarea>
    </label>
    </div>`}).join('');
  updateSelectionStatus();
  renderImageSpecSummary();
  renderConfigFeedback()}
function duplicateTask(index){
  bundle.items.splice(index+1,0,JSON.parse(JSON.stringify(bundle.items[index])));
  selectedPromptIndexes.clear();
  renderBundle()}
function deleteTask(index){
  bundle.items.splice(index,1);
  selectedPromptIndexes.clear();
  renderBundle()}
async function syncBundle(){
  const items=[];
  for(const item of bundle.items){
    for(let i=0;i<Number(item.count||1);i++)items.push({title:Number(item.count||1)>1?`${item.title}-${i+1}`:item.title,prompt:item.prompt,negative_prompt:item.negative_prompt||'',metadata:item.metadata||{}})}
  const v=await api('/api/update-bundle',{method:'POST',body:JSON.stringify({items})});
  bundle=v.bundle}
function batchConfig(){
  // 这个对象就是页面真正提交给后端的全部内容。它是"页面显示的值 == 提交给
  // ComfyUI 的值"这条验收标准的起点，所以逐项列开，不压缩成一行。
  const image=currentImagePreset();
  return {
    workflow_path:$('workflow').value,
    workflow_variant:$('workflowVariant').value,
    // 使用目的：预检据此校验工作流能否做这件事。空值＝不做断言。
    purpose:$('purposeSelect')?$('purposeSelect').value:'',
    style_application:$('styleApplication').value,
    model:$('model').value,
    styles:selectedStyles,
    style_library:$('styleLibrary').value,
    style_name:selectedStyles.length?'':$('styleName').value,
    loras:selectedLoras,
    aspect_ratio:$('aspect').value,
    megapixels:Number(image?.megapixels||1.2),
    output_dir:$('batchOutputDir')?.value.trim()||$('outputRoot').value,
    negative_prompt:$('globalNegative').value,
    seed:$('seedMode').value==='fixed'?($('seedValue').value?Number($('seedValue').value):null):null,
    single_subject_guard:$('singleSubject').checked,
    max_retries:Number($('maxRetries').value),
    resource_overrides:batchOverrides,
    llm:{
      enabled:$('llmEnabled').checked,
      base_url:$('llmUrl').value,
      model:$('llmModel').value,
      api_key:$('llmKey').value,
      temperature:Number($('llmTemp').value),
      instruction:$('llmInstruction').value,
    }
    ,
  }
}

function useDefaultOutput(){
  const target=$('batchOutputDir');
  if(target)target.value=$('outputRoot')?.value||'';
  notify('已恢复为默认输出目录','success');
}
function updateSeedHint(){
  const fixed=$('seedMode').value==='fixed';
  $('seedValueWrap').hidden=!fixed;
  $('seedHint').hidden=!fixed;
  try{
    localStorage.setItem('comfybatch-seed-mode',$('seedMode').value)}
  catch(e){
  }
}
// ---- 使用目的：六种管线用途 -------------------------------------------- //
// 选一个用途等于声明"这批要干什么"，预检据此校验工作流能不能做这件事；不选则
// 不做任何断言，行为与以前完全一致。用途按管线阶段划分，因为只有阶段能从提交图
// 里验证——"文生图/图生图/局部重绘/高清重绘/放大超分/批量变体"。
// 这份清单只是兜底：随 /api/configure 回来的 purposes 才是判定的那份规则，页面
// 照它渲染，服务端改了口径这里不会各说各话。
const purposeFallback=[
  {
    id:'txt2img',name:'文生图',summary:'从提示词直接生成图片'},
  {
    id:'img2img',name:'图生图／参考图',summary:'用一张输入图引导生成'},
  {
    id:'inpaint',name:'局部重绘',summary:'只在遮罩区域内改动'},
  {
    id:'refine',name:'高清重绘（二采）',summary:'在一采结果上再采一次'},
  {
    id:'upscale',name:'放大超分',summary:'把结果放大到更高分辨率再保存'},
  {
    id:'variants',name:'批量变体',summary:'同一提示词产出多张不同种子'},
];
let purposeOptions=purposeFallback.slice();
function purposeEntry(id){
  return purposeOptions.find(x=>x.id===id)||null}
function renderPurposeOptions(){
  const select=$('purposeSelect');
  if(!select)return;
  const served=(inventory&&Array.isArray(inventory.purposes)?inventory.purposes:[])
    .filter(x=>x&&x.id)
    .map(x=>({
      id:String(x.id),name:String(x.name||x.id),summary:String(x.summary||'')}));
  if(served.length)purposeOptions=served;
  const keep=select.value;
  opts(select,[{
    id:'',name:'未指定（不做用途校验）',summary:''},...purposeOptions],x=>x.name,x=>x.id);
  select.value=purposeOptions.some(x=>x.id===keep)?keep:'';
  renderPurposeHint()}
// 真相栏与提示条读同一份判定，两处不可能各说各话。
// 判定是对某一次编译做的，所以它只代表当时那个分支。切了分支但没重新检查时，
// 旧结论说的是别的分支——这时候显示"待重新检查"，不拿旧结论冒充现在的选择。
function purposeStaleFor(f){
  if(!f||!f.checked)return false;
  const now=($('workflowVariant')&&$('workflowVariant').value)||'';
  return String(f.branchId||'')!==now}
function purposeFactText(f){
  const purpose=(f&&f.purpose)||{
  }
  ,chosen=purpose.id||($('purposeSelect')?$('purposeSelect').value:'');
  if(!chosen)return '未指定';
  const entry=purposeEntry(chosen),label=entry?entry.name:chosen;
  if(purposeStaleFor(f))return `${label}（待重新检查）`;
  if(!f||!f.checked)return `${label}（未检查）`;
  if(purpose.unknown)return `${label}（未定义）`;
  if(purpose.ready)return `${label} · 工作流支持`;
  const unmet=(purpose.requirements||[]).filter(x=>!x.met).map(x=>x.label);
  return `${label} · 工作流不支持`+(unmet.length?`（缺：${unmet.join('；')}）`:'')}
function renderPurposeHint(){
  const box=$('purposeHint');
  if(!box)return;
  const select=$('purposeSelect'),chosen=select?select.value:'',entry=purposeEntry(chosen);
  if(!entry){
    box.hidden=true;
    box.textContent='';
    box.className='help';
    return}
  const f=effectiveFacts(),purpose=f.purpose||{
  }
  ;
  const unmet=(purpose.requirements||[]).filter(x=>!x.met).map(x=>x.label);
  const supported=(purpose.supported||[]).map(x=>{
    const e=purposeEntry(x);
    return e?e.name:x});
  box.hidden=false;
  if(purposeStaleFor(f)){
    box.className='help';
    box.textContent=`使用目的：${entry.name}——当前选的是另一个分支，上次的判定不作数。`
      +'点「实际检查」重新核对。';
    return}
  if(!f.checked||!purpose.id){
    box.className='help';
    box.textContent=`使用目的：${entry.name}——${entry.summary}。点「实际检查」核对这个工作流是否支持它。`;
    return}
  if(purpose.ready){
    box.className='help';
    box.textContent=`使用目的：${entry.name}——工作流支持，预检已按它校验通过。`;
    return}
  box.className='help purpose-unsupported';
  box.textContent=`使用目的：${entry.name}——工作流不支持`
    +(unmet.length?`：缺 ${unmet.join('；')}`:'')
    +(supported.length?`。这个工作流支持：${supported.join('、')}`:'')
    +'。可在 ComfyUI 里补齐缺失阶段后重新检查，或改选支持的用途，也可以改回「未指定」。'}
function lastSeedValue(){
  for(const row of (reviewState.results||[])){
    const values=Object.values((row.generation||{}).seeds||{});
    if(values.length)return values[0];
  }
  return null;
}
function useLastSeed(){
  for(const row of (reviewState.results||[])){
    const seeds=(row.generation||{}).seeds||{
    }
    ;
    const values=Object.values(seeds);
    if(values.length){
      $('seedValue').value=String(values[0]);
      notify(`已填入第 ${row.index} 张的种子 ${values[0]}`,'success');
      return}
  }
  notify('还没有可用的历史种子，先跑一次批次或重做','error');
}

async function openBatchOutput(){
  const path=$('batchOutputDir')?.value.trim()||$('outputRoot')?.value.trim();
  if(!path){
    notify('请先填写输出目录','error');
    return}
  try{
    await api('/api/open-folder',{method:'POST',body:JSON.stringify({kind:'output',path})});
  }
  catch(e){
    notify(e.message,'error')}
}
function renderEffective(r){
  const facts=(({base,predicted})=>({base,predicted}))(effectiveFacts());
  const el=$('effective'),errors=r.errors||[],warnings=r.warnings||[],size=r.dimensions?`${r.dimensions.width}×${r.dimensions.height}`:'未确定',wf=r.workflow||{
  }
  ,nodes=r.nodes||{
  }
  ,variant=r.variant||{
  }
  ;
  const nodeText=kind=>(nodes[kind]||[]).length?(nodes[kind]||[]).join('、'):'未发现';
  const overrideCount=bundle?(bundle.items||[]).filter(x=>x.metadata?.image_preset_id||x.metadata?.style_lora_preset_id).length:0;
  el.className='effective '+(r.ready?'':'bad');
  el.innerHTML=`<b>实际生效检查：${r.ready?'可以生成':'不能生成'}</b>
  <div class="truth-grid">
  <div class="truth-row full">
  <small>实际工作流</small>
  <strong>${esc(wf.name||'未确定')}</strong>
  <small>${esc(wf.path||'')}</small>
  </div>
  <div class="truth-row">
  <small>执行分支</small>
  <strong>${esc(variant.name||'当前已启用分支')} · ${variant.sampler_count||0}次采样 · ${variant.upscale_count||0}个放大步骤</strong>
  </div>
  <div class="truth-row">
  <small>工作流指纹</small>
  <strong>${esc(wf.fingerprint||'未生成')}</strong>
  </div>
  <div class="truth-row">
  <small>工作流格式</small>
  <strong>${esc(r.format||'未知')}</strong>
  </div>
  <div class="truth-row">
  <small>模型</small>
  <strong>${esc(r.model?.name||'未选择')} · ${esc(r.model?.loader||'不可用')}</strong>
  </div>
  <div class="truth-row">
  <small>初始尺寸 → 预计输出</small>
  <strong>${esc(size)}${facts.predicted?` → ${facts.predicted.width}×${facts.predicted.height}（×${facts.predicted.factor}）`:""}</strong>
  </div>
  <div class="truth-row">
  <small>采样器节点</small>
  <strong>${esc(nodeText('samplers'))}</strong>
  </div>
  <div class="truth-row">
  <small>放大节点</small>
  <strong>${esc(nodeText('upscale'))}</strong>
  </div>
  <div class="truth-row">
  <small>提示词节点</small>
  <strong>${esc(nodeText('prompt'))}</strong>
  </div>
  <div class="truth-row">
  <small>图片输入节点</small>
  <strong>${esc(nodeText('image_input'))}${r.input_image?.requested?` · ${r.input_image.supported?'已接入':'不支持'}`:' · 当前无图片任务'}</strong>
  </div>
  <div class="truth-row">
  <small>尺寸节点</small>
  <strong>${esc(nodeText('dimensions'))}</strong>
  </div>
  <div class="truth-row">
  <small>保存节点</small>
  <strong>${esc(nodeText('save'))}</strong>
  </div>
  <div class="truth-row">
  <small>风格</small>
  <strong>${esc(r.style?.mode||'未选择')} · ${r.style?.count||0} 个${r.style?.native_fallback?' · 原生锚点不可用，已改编译':''}</strong>
  </div>
  <div class="truth-row">
  <small>LoRA</small>
  <strong>${esc(r.lora?.mode||'未选择')} · ${r.lora?.count||0} 个</strong>
  </div>
  <div class="truth-row">
  <small>负面提示词</small>
  <strong>${esc(r.negative_prompt?.mode||'未填写')}${(r.negative_prompt?.replaces||[]).length?` · 将替换 ${esc((r.negative_prompt.replaces||[]).join('、'))}`:''}${r.negative_prompt?.fallback_expected?' · CLIP 来源为回退所得':''}${r.negative_prompt?.empty_negative?' · 负面词不生效':''}</strong>
  </div>
  <div class="truth-row">
  <small>任务级覆盖</small>
  <strong>${overrideCount?`${overrideCount} 条任务将覆盖部分全局设置`:'无'}</strong>
  </div>
  </div>
  ${warnings.map(x=>`<div>提醒：${esc(x)}</div>`).join('')}${errors.map(x=>`<div>错误：${esc(x)}</div>`).join('')}`
  if(typeof renderWorkflowFacts==='function')renderWorkflowFacts();
  if(typeof renderRail==='function')renderRail()}
async function preflight(body=batchConfig()){
  const v=await api('/api/preflight',{method:'POST',body:JSON.stringify(body)});
  // 预检响应同时带回检查载荷（成功与阻断两条路径形状一致）；存下来，真相栏与
  // 阶段 2 才能在预检后立刻更新，而不是非要先打开抽屉。
  lastInspect=v;
  renderEffective(v.report||v.preflight);
  // 真相栏必须在这里重画。少了这一步，预检明明给出了「不能生成」的结论，右侧
  // 仍停在「尚未检查 / 没有阻断问题」上，两块面板互相矛盾。
  renderRail();
  renderWorkflowFacts();
  // 抽屉若开着，内容也要跟着刷新：阻断时留下的旧内容会一直挂着「预检 未通过」，
  // 与刚通过的结论冲突。关着的抽屉不动，免得每次预检都多一次请求。
  if(!$('drawer').hidden)openDrawer('inspect').catch(()=>{
  }
  );
  return v.report||v.preflight}
/* ---- 导入即预检 --------------------------------------------------------
SwarmUI 的 Simple Tab 在选中工作流的当下就显示其参数与能力；借这个思路，
导入完成后立刻跑一次真实预检（POST 当前 batchConfig，与「实际检查」同一
后端，没有任何新口径），把"能不能跑"从阶段 4 提前到导入后几秒内。完整
结果仍在「检查与运行」与真相栏，这里只是一个提前的快速结论。 */
async function precheckAfterImport(){
  const banner=$('precheck');
  if(!banner)return;
  banner.hidden=false;
  banner.className='precheck run';
  banner.innerHTML='<b>导入即预检</b><div>正在检查当前所选工作流能否生成…</div>';
  try{
    const report=await preflight();
    const warn=(report.warnings||[]).length;
    banner.className='precheck ok';
    banner.innerHTML=`<b>导入即预检：可以生成</b>
    <div>结构与资源检查通过${warn?`，另有 ${warn} 条提醒（详见「检查与运行」）`:''}。结论以导入时的所选配置为准；之后换了工作流或模型，可点「重新预检」或「实际检查」刷新。</div>`;
  }
  catch(e){
    const payload=e.payload||{
    }
    ;
    // 与 preflightAction 同一条取数顺序：先看预检层错误，再看编译层阻断。
    const problems=(payload.preflight_errors||[]).length?payload.preflight_errors:((payload.blocking||[]).length?payload.blocking:[]);
    banner.className='precheck bad';
    banner.innerHTML=`<b>导入即预检：当前配置不能生成</b>
    <div>${
    esc(e.message)}</div>`
    +problems.slice(0,4).map(p=>`<div>· ${
    esc(p.title||'')}${
    p.node_id?`（节点 ${
    esc(String(p.node_id))}）`:''}</div>`).join('')
    +'<div class="review-actions" style="margin-top:8px"><button class="secondary" onclick="precheckAfterImport()">重新预检</button></div>';
  }
}
/* ---- 参数工作台 ---------------------------------------------------------
The controls are generated from the server's parameter registry, so the page
cannot offer a parameter the compiler does not understand, and every field
shows both the value you would set and the value currently in effect. */
let workbench=null, batchParams={
}
;
let activeParamSection='basic';
const PARAM_SECTIONS=[
{
  id:'basic',label:'基础成图',keys:['base_width','base_height','batch_size','steps','cfg','denoise']}
,
{
  id:'sampling',label:'采样控制',keys:['steps_stage1','steps_stage2','sampler_name','scheduler','start_at_step','end_at_step','add_noise','return_with_leftover_noise']}
,
{
  id:'upscale',label:'高清放大',keys:['upscale_by','upscale_model','tile_size','latent_upscale_by']}
,
{
  id:'advanced',label:'高级与接缝',keys:[]}
,
];

async function loadWorkbench(btn){
  if(btn)setBusy(btn,true);
  try{
    const v=await api('/api/params');
    workbench=v.params||{
    }
    ;
    batchParams=Object.assign({},workbench.batch_values||{});
    renderWorkbench();
    if(btn)notify('已读取参数清单','success');
  }
  catch(e){
    notify(e.message,'error')}
  finally{
    if(btn)setBusy(btn,false)}
}

function allParamSpecs(){
  return workbench?((workbench.groups||[]).flatMap(g=>g.params)):[]}

function paramsBySection(){
  const claimed=new Set(PARAM_SECTIONS.flatMap(x=>x.keys));
  const all=allParamSpecs();
  return PARAM_SECTIONS.map(section=>({
    ...section,
    params:section.id==='advanced'?all.filter(p=>!claimed.has(p.key)):all.filter(p=>section.keys.includes(p.key)),
  })).filter(section=>section.params.length);
}

function renderParamCompactSummary(){
  const target=$('paramCompactSummary');
  if(!target)return;
  if(!workbench){
    target.textContent='沿用工作流默认参数';
    return;
  }
  const values=Object.assign({},workbench.applied||{},workbench.workflow_defaults||{},batchParams||{});
  const parts=[];
  if(values.base_width&&values.base_height)parts.push(`${values.base_width}×${values.base_height}`);
  if(values.steps!==undefined&&values.steps!=='')parts.push(`${values.steps} 步`);
  if(values.cfg!==undefined&&values.cfg!=='')parts.push(`CFG ${values.cfg}`);
  if(values.denoise!==undefined&&values.denoise!=='')parts.push(`降噪 ${values.denoise}`);
  if(values.steps_stage2!==undefined&&values.steps_stage2!=='')parts.push(`二采 ${values.steps_stage2} 步`);
  target.textContent=parts.length?parts.slice(0,4).join(' · '):'沿用工作流默认参数';
}

function setParamSection(sectionId){
  activeParamSection=sectionId;
  document.querySelectorAll('.wb-tab').forEach(button=>{
    const active=button.dataset.section===sectionId;
    button.classList.toggle('active',active);
    button.setAttribute('aria-selected',active?'true':'false');
  });
  document.querySelectorAll('.wb-section').forEach(section=>{
    section.hidden=section.dataset.section!==sectionId;
  });
}

function renderWorkbench(){
  const host=$('paramGroups');
  if(!host||!workbench)return;
  const scope=$('paramScope').value;
  const applied=workbench.applied||{
  }
  , defaults=workbench.workflow_defaults||{
  }
  ;
  $('paramScopeHint').textContent = scope==='task'
  ? `任务级参数只写入选中的任务（当前已分配 ${workbench.task_assigned_count||0} 条）。先在「导入与任务」里勾选任务范围。`
  : scope==='workflow'
  ? '保存为该工作流的默认值，以后每次运行都会带上。工作流结构改变后需要重新确认。'
  : '只对本次批次生效，关闭页面即失效。';
  const rememberWrap=$('rememberWrap');
  if(rememberWrap)rememberWrap.hidden=scope!=='batch';
  const sections=paramsBySection();
  if(!sections.some(section=>section.id===activeParamSection))activeParamSection=sections[0]?.id||'basic';
  host.innerHTML=`<div class="wb-tabs" role="tablist" aria-label="参数类别">
  ${sections.map(section=>`<button type="button" class="wb-tab${section.id===activeParamSection?' active':''}" data-section="${section.id}" aria-selected="${section.id===activeParamSection?'true':'false'}" onclick="setParamSection('${section.id}')">
  <span>${esc(section.label)}</span>
  <small>${section.params.length}</small>
  </button>`).join('')}
  </div>
  <div class="wb-section-stack">
  ${sections.map(section=>`<section class="wb-group wb-section" data-section="${section.id}"${section.id===activeParamSection?'':' hidden'}>
  <div class="wb-section-title">
  <h3>${esc(section.label)}</h3>
  <span>留空即沿用工作流</span>
  </div>
  <div class="wb-grid">${section.params.map(p=>fieldHtml(p,applied,defaults)).join('')}</div>
  </section>`).join('')}
  </div>`;
}

function fieldHtml(p,applied,defaults){
  const id='param-'+p.key;
  const inBatch=batchParams[p.key], inDefault=defaults[p.key], inApplied=applied[p.key];
  const value = inBatch!==undefined ? inBatch : (inDefault!==undefined ? inDefault : '');
  const shown = inBatch!==undefined ? 'batch' : (inDefault!==undefined ? 'def' : '');
  const hint=[];
  if(inDefault!==undefined)hint.push('工作流默认值 '+esc(String(inDefault)));
  if(inApplied!==undefined)hint.push('实际生效 '+esc(String(inApplied)));
  if(p.help)hint.push(esc(p.help));
  let control='';
  if(p.kind==='bool'){
    control='<select id="'+id+'">'+['','true','false'].map(o=>{
      const sel=(value===''&&o==='')||(value===true&&o==='true')||(value===false&&o==='false')?' selected':'';
      return `<option value="${
      o}"${sel}>${o===''?'沿用工作流':o==='true'?'是':'否'}</option>`;
    }).join('')+'</select>';
  }
  else if(p.kind==='enum'){
    const opts=p.options||[];
    control='<select id="'+id+'"><option value="">沿用工作流'+(value!==''?'（当前 '+esc(String(value))+'）':'')+'</option>'
    +opts.map(o=>`<option value="${
    esc(o)}"${o===value?' selected':''}>${esc(o)}</option>`).join('')+'</select>';
  }
  else{
    const attrs=(p.step!==undefined?' step="'+p.step+'"':'')+(p.min!==undefined?' min="'+p.min+'"':'')+(p.max!==undefined?' max="'+p.max+'"':'');
    control='<input id="'+id+'" type="number"'+attrs+' value="'+(value===''?'':esc(String(value)))+'" placeholder="沿用工作流">';
  }
  const badge = shown==='batch'?'<span class="wb-badge batch">批次值</span>':shown==='def'?'<span class="wb-badge def">默认值</span>':'';
  return '<div class="'+(shown?'wb-field set':'wb-field')+'" id="field-'+p.key+'"><label>'+esc(p.label)+badge+control+'</label>'
  +'<div class="hint">'+(p.stage?('第 '+p.stage+' 个采样器 · '):'')+hint.join(' · ')+'</div></div>';
}

function collectWorkbench(){
  const values={
  }
  , cleared=[];
  for(const p of allParamSpecs()){
    const el=$('param-'+p.key);
    if(!el)continue;
    const raw=el.value;
    if(raw===''||raw===null){
      if(batchParams[p.key]!==undefined)cleared.push(p.key);
      continue }
    values[p.key] = p.kind==='bool' ? (raw==='true') : raw;
  }
  return {
    values,cleared}
  ;
}

async function applyWorkbench(btn){
  if(!workbench){
    await loadWorkbench();
    if(!workbench)return}
  const scope=$('paramScope').value;
  const collected=collectWorkbench();
  const values=collected.values;
  if(!Object.keys(values).length&&!collected.cleared.length){
    notify('没有要应用的参数','error');
    return}
  const body={
    workflow_path:workbench.workflow_path||$('workflow').value,scope,params:values}
  ;
  for(const k of collected.cleared)body.params[k]='';
  const rememberNow=scope==='batch'&&$('rememberParams')&&$('rememberParams').checked;
  if(rememberNow)body.remember=true;
  if(scope==='task'){
    const indexes=[...selectedPromptIndexes];
    if(!indexes.length){
      notify('请先在「导入与任务」里勾选任务','error');
      return}
    body.indexes=indexes;
  }
  setBusy(btn,true);
  try{
    const v=await api('/api/params/apply',{method:'POST',body:JSON.stringify(body)});
    const problems=v.problems||[];
    const blocking=problems.filter(p=>p.workflow_level);
    workbench=v.params||workbench;
    if(scope==='batch')batchParams=Object.assign({},values);
    else batchParams={
    }
    ;
    renderWorkbench();
    $('paramFeedback').innerHTML = blocking.length
    ? '<b style="color:#8d3022">参数未通过校验，已全部忽略：</b>'+blocking.map(p=>'<div>· '+esc(p.detail||p.title)+'</div>').join('')
    : (problems.length?'部分参数已忽略：'+problems.map(p=>esc(p.title)).join('、')+'。':'参数已应用')
    +(scope==='workflow'?' 已保存为该工作流默认值。':scope==='task'?(' 已写入 '+(body.indexes||[]).length+' 条任务。'):(rememberNow?' 本次批次生效，并已记住为该工作流默认值。':' 本次批次生效。'))
    +'<div class="review-actions" style="margin-top:8px"><button class="secondary" onclick="preflightAction($(\'preflightButton\'))">立即检查实际生效</button></div>';
    notify(blocking.length?'参数被拒绝':'参数已应用',blocking.length?'error':'success');
  }
  catch(e){
    notify(e.message,'error')}
  finally{
    setBusy(btn,false)}
}

async function clearWorkbench(btn){
  batchParams={
  }
  ;
  if(btn)setBusy(btn,true);
  try{
    await loadWorkbench();
    notify('已清空未应用的输入','success')}
  catch(e){
    notify(e.message,'error')}
  finally{
    if(btn)setBusy(btn,false)}
}

/* ---- 事件流、编辑租约、单实例激活 --------------------------------------
Three things that keep several open pages from corrupting each other:
* SSE replaces polling, so every page sees the same server state;
* one page holds the editing lease, the rest are read-only;
* a second launch activates the running instance instead of starting a new
backend. */
const CLIENT_ID_KEY='comfybatch-client-id';
const clientId=(()=>{
  let id=sessionStorage.getItem(CLIENT_ID_KEY);
  if(!id){
    id='p-'+Math.random().toString(36).slice(2,10)+Date.now().toString(36).slice(-4);
    sessionStorage.setItem(CLIENT_ID_KEY,id)}
  return id;
}
)();
let eventSource=null,
lastActivatedAt=0,
leaseState={
  held:false,mine:false,holder:null}
;

function connectEvents(){
  if(typeof EventSource==='undefined'){
    return}
  // polling stays as the fallback
  if(eventSource){
    eventSource.close()}
  eventSource=new EventSource(`/api/events?client_id=${
  encodeURIComponent(clientId)}
  `);
  eventSource.addEventListener('snapshot',(event)=>{
    let payload=null;
    try{
      payload=JSON.parse(event.data)}
    catch(e){
      return}
    if(payload.status){
      applyStatus(payload.status);lastRunStatus=payload.status.status}
    if(payload.bundle&&(!bundle||(typeof isReadOnly==='function'&&isReadOnly()))){
      bundle=payload.bundle;bundle.items.forEach(x=>x.count=x.count||1);renderBundle()
    }
    if(payload.instance_id){
      document.body.dataset.instance=payload.instance_id}
    applyLease(payload.lease||{
    }
    );
    renderReview(payload.review);
    // 预设库可能在别的页面被改过。后端每帧只发一个轻量指纹；指纹变了就静默重拉
    // 一次 inventory（不弹提示、不动按钮 busy 态），让下拉跟着更新。首次只记基准，
    // 避免刚打开就多发一次请求。旧服务端没有 presets_rev 时行为保持不变。
    if(typeof payload.presets_rev!=='undefined'&&payload.presets_rev!==null){
      if(presetsRev&&payload.presets_rev!==presetsRev){
        presetsRev=payload.presets_rev;
        fetchInventory(true).catch(()=>{
        }
        )}
      else{
        presetsRev=payload.presets_rev}}
    if(payload.activated_at&&payload.activated_at>lastActivatedAt){
      lastActivatedAt=payload.activated_at;
      onActivated();
    }
  }
  );
  eventSource.onerror=()=>{
    // The stream drops when the server exits; retry rather than dying silently.
    eventSource.close();
    eventSource=null;
    setTimeout(connectEvents,1500);
  }
  ;
}

/* Shared by the SSE handler and the polling fallback, so both paths render the
same way and one cannot drift from the other. */
function applyStatus(s){
  if(!s)return;
  ['total','submitted','completed','errors'].forEach(k=>{
    const el=$(k);if(el)el.textContent=s[k]||0}
  );
  $('progress').style.width=(s.total?Math.round(100*(s.completed+s.errors)/s.total):0)+'%';
  $('runText').textContent=`状态：${
  s.status}
  ${
  s.current?' · 当前：'+s.current:''}
  ${
  s.report?' · 报告：'+s.report:''}
  `;
  $('start').disabled=!comfyConnected||isReadOnly()||['running','paused','starting'].includes(s.status);
  railStatus=s;
  // 每条状态路径都更新真相栏，所以推送与轮询不会显示不同的值。
  if(typeof renderRail==='function')renderRail();
  $('gallery').innerHTML=(s.results||[]).filter(x=>x.copied_to).map(x=>`<figure>
  <img loading="lazy" src="/api/preview?path=${encodeURIComponent(x.copied_to)}" onclick="openViewer(${x.index})">
  <figcaption>${
  esc(x.index+'. '+x.title)}
  ${
  x.quality?.repeated_panels?' · 疑似多栏':''}
  </figcaption>
  </figure>`).join('');
}

function applyLease(lease){
  leaseState=lease||{
    held:false,mine:false,holder:null}
  ;
  document.body.classList.toggle('readonly',isReadOnly());
  renderLeaseBanner();
}

function isReadOnly(){
  return leaseState.held&&!leaseState.mine}

function renderLeaseBanner(){
  let banner=$('leaseBanner');
  if(!banner){
    banner=document.createElement('div');
    banner.id='leaseBanner';
    const host=document.querySelector('.workspace')||document.body;
    host.insertBefore(banner,host.firstChild);
  }
  if(!isReadOnly()){
    banner.className='';
    const pill=leaseState.mine?'<span class="lease-pill mine">本页可编辑</span>':'';
    banner.innerHTML=pill;
    return;
  }
  const holder=(leaseState.holder||{
  }
  ).client_id||'另一个页面';
  banner.className='readonly-banner';
  banner.innerHTML=`另一个页面正在编辑（${
  esc(holder)}
  ），本页为只读，避免两边保存不同配置互相覆盖。`
  +`<button class="secondary lease-ok" onclick="takeOverLease(this)">接管编辑</button>`;
}

async function takeOverLease(btn){
  setBusy(btn,true,'接管中');
  try{
    const v=await api('/api/lease/claim',{
      method:'POST',body:JSON.stringify({
        client_id:clientId,force:true,label:'接管'}
      )}
    );
    applyLease(v.lease||{
    }
    );
    notify('已接管编辑','success');
  }
  catch(e){
    notify(e.message,'error')}
  finally{
    setBusy(btn,false)}
}

async function releaseLease(){
  try{
    await api('/api/lease/release',{
      method:'POST',body:JSON.stringify({
        client_id:clientId}
      )}
    )}
  catch(e){
  }
}

function onActivated(){
  // A second launch asked this instance to come forward: show the running task.
  goStep(3);
  notify('已切换到正在运行的窗口','success');
  poll(true);
}

/* Every write carries the client id, which is what lets the server tell two
pages apart. */
function withClient(body){
  if(body&&typeof body==='object')return Object.assign({
  }
  ,body,{
    client_id:clientId}
  );
  return {
    client_id:clientId}
  ;
}

function staleNotice(v){
  const stale=(v&&v.stale_rules)||[];
  if(!stale.length)return '';
  return ` 有 ${
  stale.length}
  条替换规则因为工作流改动而暂停生效，可在「生效检查与依赖」里沿用。`;
}

async function preflightAction(button){
  if(typeof isReadOnly==='function'&&isReadOnly()){
    notify('本页为只读（另一个页面正在编辑）。要在这里操作请点顶部的「接管编辑」。','error');
    renderLeaseBanner();
    return}
  setBusy(button,true,'正在检查');
  try{
    const result=await preflight();
    notify(result.ready?'实际配置检查通过':'实际配置不能生成',result.ready?'success':'error');
    return result}
  catch(e){
    // The server returns the blocking problems, warnings and stale rules with the
    // failure; render them so the user sees the cause, not just a message.
    const payload=e.payload||{
    }
    ;
    const report=payload.preflight||{
    }
    ;
    // A blocked check answers with HTTP 400, so it lands here rather than in the
    // success path. Store the payload and repaint the rail anyway: leaving this
    // out is what let the rail sit on "尚未检查" beside a "不能生成" verdict.
    if(Object.keys(payload).length){
      lastInspect=payload;
      renderRail();
      renderWorkflowFacts()}
    $('effective').className='effective bad';
    // The reason for a blocked check lives in preflight_errors (structure,
    // resources, parameters) -- the blocking list is about the compiled graph,
    // which does not exist when a check is blocked. Show whichever is present.
    const problems=(payload.preflight_errors||[]).length?payload.preflight_errors:((payload.blocking||[]).length?payload.blocking:(report.blocking||[]));
    const warnings=report.warnings||[];
    $('effective').innerHTML=`<b>实际生效检查：不能生成</b>
    <div>${esc(e.message)}</div>`
    +problems.map(p=>`<div>· ${
    esc(p.title||'')}
    ${
    p.node_id?` 节点 ${
    esc(p.node_id)}
    （${
    esc(p.class_type||'')}
    ）`:''}
    ${
    p.input_name?` 参数 ${
    esc(p.input_name)}
    `:''}
    ${
    p.received_value!=null?` 当前值 ${
    esc(String(p.received_value))}
    `:''}
    ${
    (p.candidates||[]).length?` · 可用候选 ${
    esc(p.candidates.join('、'))}
    `:''}
    </div>`).join('')
    +warnings.map(w=>`<div>提示：${
    esc(w)}
    </div>`).join('')
    +staleNotice(payload);
    notify(e.message,'error');
    openDrawer('inspect').catch(()=>{
    }
    );
    throw e}
  finally{
    setBusy(button,false)}
}
async function startBatch(button){
  if(typeof isReadOnly==='function'&&isReadOnly()){
    notify('本页为只读（另一个页面正在编辑）。要在这里操作请点顶部的「接管编辑」。','error');
    renderLeaseBanner();
    return}
  if(!comfyConnected)return notify('ComfyUI 未启动，无法提交任务','error');
  if(!bundle||!bundle.items.length)return notify('请先导入提示词合集','error');
  if($('seedMode').value==='fixed'&&!Number($('seedValue').value)&&!lastSeedValue())return notify('固定种子需要填写种子值，或先点一次「用最近一次」','error');
  setBusy(button,true,'正在提交');
  try{
    await syncBundle();
    const body=batchConfig();
    await preflight(body);
    await api('/api/start',{
      method:'POST',body:JSON.stringify(body)}
    );
    goStep(3);
    notify('批次已提交到 ComfyUI');
    poll(true)}
  catch(e){
    $('effective').className='effective bad';
    $('effective').innerHTML=`<b>实际生效检查：不能生成</b>
    <div>${
    esc(e.message)}
    </div>`;
    notify(e.message,'error')}
  finally{
    setBusy(button,false)}
}
async function poll(immediate=false){
  if(pollTimer){
    clearTimeout(pollTimer);
    pollTimer=null}
  try{
    const v=await api('/api/status?client_id='+encodeURIComponent(typeof clientId==='string'?clientId:'')),s=v.status;
    if(v.bundle&&(!bundle||(typeof isReadOnly==='function'&&isReadOnly()))){
      bundle=v.bundle;
      bundle.items.forEach(x=>x.count=x.count||1);
      if(!selectedPromptIndexes)selectedPromptIndexes=new Set();
      renderBundle()
    }
    applyStatus(s);
    if(typeof applyLease==='function')applyLease(v.lease||{
    }
    );
    renderReview(v.review);
    if(lastRunStatus&&lastRunStatus!==s.status&&['completed','completed_with_errors','cancelled','aborted'].includes(s.status))notify(`运行状态：${
    s.status}
    `,s.errors?'error':'success');
    lastRunStatus=s.status;
    pollTimer=setTimeout(()=>poll(),['running','paused','starting'].includes(s.status)?650:2200)}
  catch(e){
    pollTimer=setTimeout(()=>poll(),2500)}
}
try{
  const savedSeedMode=localStorage.getItem('comfybatch-seed-mode');
  if(savedSeedMode==='fixed'||savedSeedMode==='random')$('seedMode').value=savedSeedMode;
  const savedDensity=localStorage.getItem('comfybatch-review-density');
  if(savedDensity)$('reviewDensity').value=savedDensity;
}
catch(e){
}
updateSeedHint();
setReviewDensity($('reviewDensity').value||'compact');
goStep(currentStep);
refreshInventory();
poll(true);
initReviewDialogs();

/* 审图相关的一次性绑定集中在这里，避免散落的 onchange 属性。
两个对话框都支持 Esc 关闭（dialog 的默认行为）。 */
function initReviewDialogs(){
  const mode=$('rejectMode');
  if(mode)mode.addEventListener('change',updateRejectHint);
  const image=$('viewerImage');
  if(image)image.addEventListener('click',toggleViewerFull);
}

/* ---- 审图看板、键盘审图、原图查看器 ---- */
/* Per-image review, error drawer and keyboard triage.
Rendered from server state only: the page never builds paths or reads report
files itself. */
// Replacements the user chose for this batch only. Permanent ones live on the
// server and are merged there, so this never needs to know about them.
const BADGE_CLASS={
  '待确认':'pending','已通过':'approved','需重做':'rejected','已替换':'replaced'}
;

function renderReview(v){
  if(!v)return;
  reviewState=Object.assign(reviewState,v||{
  }
  );
  if(typeof renderRail==='function')renderRail();
  const s=reviewState.results||[];
  const banner=$('abortBanner');
  if(reviewState.aborted_reason){
    banner.hidden=false;
    banner.textContent='批次已提前停止：'+reviewState.aborted_reason}
  else{
    banner.hidden=true;
    banner.textContent=''}
  const by=reviewState.review?.by_status||{
  }
  ;
  $('reviewSummary').textContent=s.length?`共 ${
  s.length}
  张 · 待确认 ${
  by['待确认']||0}
  · 已通过 ${
  by['已通过']||0}
  · 需重做 ${
  by['需重做']||0}
  · 已替换 ${
  by['已替换']||0}
  `:'';
  dupGroups=new Set();
  const seen=new Map();
  // 疑似重复判定：seeds 与提示词完全一致的两张必然同图，只在这种强条件下标记。
  for(const row of s){
    const key=JSON.stringify([(row.generation||{}).seeds||{},(row.compiled_prompt||row.prompt||'')]);
    const first=seen.get(key);
    if(first!==undefined){
      dupGroups.add(first);
      dupGroups.add(row.index)}
    else seen.set(key,row.index);
  }
  $('reviewBoard').innerHTML=s.map(cardHtml).join('');
  syncReviewPicks();
  const pendingCount=s.filter(x=>x.copied_to&&(x.review_status||'待确认')==='待确认').length;
  const confirmBtn=$('confirmAllButton');
  if(confirmBtn)confirmBtn.textContent=pendingCount?`全部通过（${pendingCount}）`:'全部通过';
}

function setReviewDensity(value){
  const board=$('reviewBoard');
  if(board){
    board.classList.remove('density-compact','density-standard','density-loose');
    board.classList.add('density-'+(value||'compact'))}
  try{
    localStorage.setItem('comfybatch-review-density',value||'compact')}
  catch(e){
  }
}

function cardHtml(x){
  const status=x.review_status||'待确认';
  const isDup=dupGroups.has(x.index);
  const gen=x.generation||{
  }
  ;
  const base=x.base_dimensions||{
  }
  , final=x.final_dimensions||{
  }
  ;
  const size=(base.width&&final.width)
  ? `原始 ${
  base.width}
  ×${
  base.height}
  → 最终 ${
  final.width}
  ×${
  final.height}
  ${
  x.upscale_factor?`（×${
  x.upscale_factor}
  ）`:''}
  `
  : (final.width?`${
  final.width}
  ×${
  final.height}
  `:'');
  const loras=(gen.loras||[]).map(l=>l.name).filter(Boolean);
  const styles=(gen.styles||[]).map(st=>st.name||st.catalog).filter(Boolean);
  const errs=x.errors||[];
  const issue=errs.length?issuesHtml(errs):'';
  return `<article class="review-card ${x.index===activeIndex?'is-active':''}" id="card-${x.index}" onclick="setActive(${x.index})">
  <div class="thumb" onclick="event.stopPropagation();openViewer(${x.index})">
  ${
  x.copied_to?`<img loading="lazy" src="/api/preview?path=${
  encodeURIComponent(x.copied_to)}" alt="结果 ${
  x.index}">`:`<div class="help">${x.status==='completed'?'无成图':'尚未生成'}</div>`}
  </div>
  <div class="review-card-head">
  <h4>${esc(x.index+'. '+(x.title||''))}</h4>${isDup?'<span class="badge dup">疑似重复产出</span>':''}<span class="badge ${BADGE_CLASS[status]||'pending'}">${esc(status)}</span>
  <input type="checkbox" class="review-pick" data-index="${x.index}" ${reviewPicks.has(x.index)?'checked':''} onclick="event.stopPropagation()" onchange="toggleReviewPick(${x.index},this.checked)">
  </div>
  <div class="review-actions">
  <button class="secondary" onclick="event.stopPropagation();reviewConfirm(${
  x.index},'已通过')">通过</button>
  <button class="secondary" onclick="event.stopPropagation();openRejectDialog(${
  x.index})">打回重做</button>
  <button class="secondary" onclick="event.stopPropagation();openNoteDialog(${
  x.index})">记录问题</button>
  </div>
  <details class="review-details" onclick="event.stopPropagation()">
  <summary>查看生成详情</summary>
  <dl class="kv">
  <dt>状态</dt>
  <dd>${esc(x.status||'')}${x.elapsed!=null?` · ${x.elapsed}s`:''}${gen.workflow_variant?` · 分支 ${esc(gen.workflow_variant)}`:''}</dd>
  <dt>工作流</dt>
  <dd>${esc((gen.workflow_path||'').split(/[\\\\/]/).pop()||'')}</dd>
  <dt>模型</dt>
  <dd>${esc(gen.model||'')}</dd>
  <dt>风格</dt>
  <dd>${esc(styles.join('、')||'（无）')}</dd>
  <dt>LoRA</dt>
  <dd>${esc(loras.join('、')||'（无）')}</dd>
  ${size?`<dt>尺寸</dt>
  <dd>${esc(size)}${gen.megapixels?` · ${gen.megapixels}MP`:''}</dd>`:''}
  ${seedHtml(x)}
  <dt>检查</dt>
  <dd>${x.quality?.repeated_panels?'疑似多栏/三视图':'通过'}</dd>
  <dt>输出</dt>
  <dd>${esc(x.copied_to||'')}</dd>
  </dl>
  ${x.note?`<div class="prompt">问题记录：${esc(x.note)}</div>`:''}
  <div class="prompt">正面：${esc((x.compiled_prompt||x.prompt||'').slice(0,400))}</div>
  <div class="prompt">负面：${esc((x.negative_prompt||'').slice(0,200)||'（未填写）')}</div>
  ${(x.attempts||[]).length>1?`<div class="help">已生成 ${x.attempts.length} 次，当前显示最后一次</div>`:''}
  ${(x.history||[]).length?`<div class="help">历史版本 ${x.history.length} 个（已替换，记录保留）</div>`:''}
  ${issue}
  </details>
  </article>`;
}

function issuesHtml(errors){
  return errors.map(p=>`<div class="issue ${
  p.severity==='warning'?'warn':''}">
  <h5>${esc(p.title||'问题')}${p.workflow_level?' · 工作流级':''}</h5>
  ${p.node_id?`<p>节点 ${esc(p.node_id)}${p.node_type?`（${esc(p.node_type)}）`:''}${p.input_name?` · 参数 ${esc(p.input_name)}`:''}</p>`:''}
  ${p.received_value!=null?`<p>当前值：<code>${esc(String(p.received_value))}</code>
  </p>`:''}
  ${(p.candidates||[]).length?`<p>合法候选：${esc(p.candidates.slice(0,8).join('、'))}</p>`:''}
  ${p.detail?`<p>${esc(p.detail)}</p>`:''}
  ${(p.fixes||[]).length?`<ul>${p.fixes.map(f=>`<li>${esc(f)}</li>`).join('')}</ul>`:''}
  ${p.raw?`<details>
  <summary>原始 ComfyUI 信息</summary>
  <pre>${esc(JSON.stringify(p.raw,null,1))}</pre>
  </details>`:''}
  </div>`).join('');
}

function setActive(index){
  activeIndex=index;
  document.querySelectorAll('.review-card').forEach(el=>el.classList.toggle('is-active',el.id==='card-'+index))}

async function reviewConfirm(index,status,note){
  try{
    const v=await api('/api/review/confirm',{method:'POST',body:JSON.stringify({index,status,note:note||''})});
    renderReview(v.review);
    notify(`第 ${index} 张已标记为 ${status}`,'success');
  }
  catch(e){
    notify(e.message,'error')}
}

/* 重做方式的人话说明。以前这里让用户手打 "same_seed" 这类英文枚举值，
打错就只回一句"不支持的重做方式"，所以改成下拉选择 + 说明文字。 */
const REDO_MODE_LABELS={
  same_seed:'同种子重做（参数不变，检查是不是偶然）',
  new_seed:'换种子重做（只换随机种子，重新抽一张）',
  edited_prompt:'改提示词后重做（针对这一条单独改词）',
  reupscale_only:'仅重做放大（复用上一张成图作为输入）',
}
;

function redoModeLabel(mode){
  return REDO_MODE_LABELS[mode]||mode;
}

function subjectLine(index){
  const row=(reviewState.results||[]).find(x=>x.index===index);
  return row?`第 ${index} 张 · ${row.title||''}`:`第 ${index} 张`;
}

function openRejectDialog(index){
  const row=(reviewState.results||[]).find(x=>x.index===index);
  if(!row){
    notify('找不到这一张，请先刷新','error');
    return;
  }
  if(row.restored){
    notify('这是上一次运行的成图，无法直接重做；请重新运行批次','error');
    return;
  }
  const modes=reviewState.redo_modes&&reviewState.redo_modes.length
  ? reviewState.redo_modes
  : Object.keys(REDO_MODE_LABELS);
  $('rejectSubject').textContent=subjectLine(index);
  const select=$('rejectMode');
  select.innerHTML=modes.map(m=>`<option value="${esc(m)}">${esc(redoModeLabel(m))}</option>`).join('');
  select.value=modes.includes('new_seed')?'new_seed':modes[0];
  updateRejectHint();
  $('rejectPrompt').value=row.prompt||'';
  updateRejectPromptVisibility();
  $('rejectNote').value=row.note||'';
  $('rejectSeed').value='';
  $('rejectSeed').disabled=false;
  $('rejectDialog').dataset.index=String(index);
  $('rejectDialog').dataset.indexes='';
  $('rejectDialog').showModal();
  $('rejectNote').focus();
}

function updateRejectHint(){
  const hint=$('rejectModeHint');
  if(hint)hint.textContent=REDO_MODE_LABELS[$('rejectMode').value]||'';
  updateRejectSeedVisibility();
}

function updateRejectPromptVisibility(){
  const wrap=$('rejectPromptWrap');
  if(wrap)wrap.hidden=$('rejectMode')?.value!=='edited_prompt';
}

function updateRejectSeedVisibility(){
  const wrap=$('rejectSeedWrap');
  if(wrap)wrap.hidden=$('rejectMode')?.value==='same_seed';
}

function resetRejectPrompt(){
  const index=Number($('rejectDialog').dataset.index||0);
  const row=(reviewState.results||[]).find(x=>x.index===index);
  if($('rejectPrompt'))$('rejectPrompt').value=row?.prompt||'';
}

async function submitReject(button){
  // 批量打回复用同一个对话框：dataset.indexes 非空即批量提交。
  const batchIndexes=$('rejectDialog').dataset.indexes;
  if(batchIndexes)return submitRejectBatch(button);
  const index=Number($('rejectDialog').dataset.index||0);
  const mode=$('rejectMode').value;
  const prompt=$('rejectPrompt')?.value.trim()||'';
  const note=$('rejectNote').value.trim();
  const seedRaw=$('rejectSeed')?.value.trim()||'';
  const seed=seedRaw!==''?Number(seedRaw):null;
  if(!index){
    notify('没有选中要重做的成图','error');
    return;
  }
  if(mode==='edited_prompt'&&!prompt){
    notify('修改提示词模式需要填写提示词','error');
    return;
  }
  setBusy(button,true,'已提交');
  try{
    const payload={
      index,mode,prompt:mode==='edited_prompt'?prompt:'',note}
    ;
    if(seed!==null&&!Number.isNaN(seed))payload.seed=seed;
    await api('/api/review/redo',{method:'POST',body:JSON.stringify(payload)});
    $('rejectDialog').close();
    notify(`第 ${index} 张已按「${redoModeLabel(mode)}」重新生成`,'success');
    poll(true);
  }
  catch(e){
    notify(e.message,'error')}
  finally{
    setBusy(button,false)}
}
function selectAllPending(){
  const pending=(reviewState.results||[]).filter(x=>x.copied_to&&(x.review_status||'待确认')==='待确认'&&!x.restored).map(x=>x.index);
  if(!pending.length)return notify('没有待确认的成图','error');
  reviewPicks=new Set(pending);
  syncReviewPicks();
}
function syncReviewPicks(){
  document.querySelectorAll('#reviewBoard .review-pick').forEach(box=>{
    box.checked=reviewPicks.has(Number(box.dataset.index));
  });
  const btn=$('redoSelectedButton');
  if(btn)btn.textContent=reviewPicks.size?`打回选中（${reviewPicks.size}）`:'打回选中';
}
function toggleReviewPick(index,checked){
  checked?reviewPicks.add(index):reviewPicks.delete(index);
  syncReviewPicks();
}
function openRejectBatchDialog(){
  const indexes=[...reviewPicks];
  if(!indexes.length)return notify('请先用卡片头部的勾选框选择要打回的成图','error');
  const modes=reviewState.redo_modes&&reviewState.redo_modes.length?reviewState.redo_modes:Object.keys(REDO_MODE_LABELS).filter(m=>m!=='edited_prompt');
  $('rejectSubject').textContent=`批量重做 ${indexes.length} 张：${indexes.join('、')}`;
  const select=$('rejectMode');
  select.innerHTML=modes.map(m=>`<option value="${esc(m)}">${esc(redoModeLabel(m))}</option>`).join('');
  select.value=modes.includes('new_seed')?'new_seed':modes[0];
  updateRejectHint();
  $('rejectPromptWrap').hidden=true;
  $('rejectPrompt').value='';
  $('rejectSeed').value='';
  $('rejectSeed').disabled=false;
  $('rejectNote').value='';
  $('rejectDialog').dataset.index='0';
  $('rejectDialog').dataset.indexes=JSON.stringify(indexes);
  $('rejectDialog').showModal();
}
async function submitRejectBatch(button){
  const indexes=JSON.parse($('rejectDialog').dataset.indexes||'[]');
  const mode=$('rejectMode').value;
  const note=$('rejectNote').value.trim();
  const seedRaw=$('rejectSeed')?.value.trim()||'';
  if(!indexes.length){
    notify('没有选中要重做的成图','error');
    return}
  setBusy(button,true,'已提交');
  try{
    const payload={
      indexes,mode,note}
    ;
    if(seedRaw!=='')payload.seed=Number(seedRaw);
    const v=await api('/api/review/redo-batch',{method:'POST',body:JSON.stringify(payload)});
    reviewPicks.clear();
    syncReviewPicks();
    $('rejectDialog').close();
    notify(`已提交 ${v.count||indexes.length} 张批量重做（${redoModeLabel(mode)}）`,'success');
    poll(true);
  }
  catch(e){
    notify(e.message,'error')}
  finally{
    setBusy(button,false)}
}

function openNoteDialog(index){
  const row=(reviewState.results||[]).find(x=>x.index===index);
  if(!row){
    notify('找不到这一张，请先刷新','error');
    return;
  }
  $('noteSubject').textContent=subjectLine(index);
  $('noteText').value=row.note||'';
  $('noteDialog').dataset.index=String(index);
  $('noteDialog').showModal();
  $('noteText').focus();
}

async function submitNote(button){
  const index=Number($('noteDialog').dataset.index||0);
  if(!index){
    notify('没有选中要记录的成图','error');
    return;
  }
  setBusy(button,true,'保存中');
  try{
    await reviewConfirm(index,'需重做',$('noteText').value.trim());
    $('noteDialog').close();
  }
  catch(e){
    notify(e.message,'error')}
  finally{
    setBusy(button,false)}
}

// 键盘审图（N）走同一个对话框，保持两种入口行为一致。
function reviewReject(index){
  openRejectDialog(index);
}

function reviewNote(index){
  openNoteDialog(index);
}

async function confirmAllPending(btn){
  const pending=(reviewState.results||[]).filter(x=>x.copied_to&&(x.review_status||'待确认')==='待确认').map(x=>x.index);
  if(!pending.length){
    notify('没有待确认的成图','error');
    return}
  if(pending.length>5&&!confirm(`确定一次性把 ${pending.length} 张全部标记为已通过？`))return;
  setBusy(btn,true);
  try{
    const v=await api('/api/review/confirm',{method:'POST',body:JSON.stringify({indexes:pending,status:'已通过'})});
    renderReview(v.review);
    notify(`已通过 ${pending.length} 张`,'success');
  }
  catch(e){
    notify(e.message,'error')}
  finally{
    setBusy(btn,false)}
}

async function reloadSchema(btn){
  setBusy(btn,true);
  try{
    const v=await api('/api/reload-schema',{method:'POST',body:'{}'});
    notify(`已读取 ${v.nodes} 个节点定义`,'success');
  }
  catch(e){
    notify(e.message,'error')}
  finally{
    setBusy(btn,false)}
}

// 记住查看器当前展示的是哪一张：从网格或卡片进来都行，切换原图不必依赖
// setActive 是否先跑过。
let viewerIndex=-1;
let viewerRows=[];
let viewerPosition=-1;

function refreshViewerRows(){
  viewerRows=(reviewState.results||[]).filter(x=>x.copied_to);
}

function preloadViewer(position){
  const row=viewerRows[(position+viewerRows.length)%viewerRows.length];
  if(!row?.copied_to)return;
  const image=new Image();
  image.src='/api/preview?path='+encodeURIComponent(row.copied_to)+'&size=1600';
}

function showViewerAt(position){
  if(!viewerRows.length)return;
  viewerPosition=(position+viewerRows.length)%viewerRows.length;
  const row=viewerRows[viewerPosition];
  viewerIndex=row.index;
  const image=$('viewerImage');
  image.dataset.full='/api/image?path='+encodeURIComponent(row.copied_to);
  image.src=row.preview?'/api/preview?path='+encodeURIComponent(row.copied_to)+'&size=1600':image.dataset.full;
  image.dataset.fullShown='0';
  image.style.transform='scale(1)';
  const final=row.final_dimensions||{
  }
  ;
  const size=final.width?` · ${final.width}×${final.height}`:'';
  $('viewerCaption').textContent=`${row.index}. ${row.title||''} · ${row.generation?.model||''}${size}`;
  $('viewerCounter').textContent=`${viewerPosition+1} / ${viewerRows.length}`;
  $('viewerPrev').disabled=viewerRows.length<2;
  $('viewerNext').disabled=viewerRows.length<2;
  preloadViewer(viewerPosition-1);
  preloadViewer(viewerPosition+1);
}

function openViewer(index){
  refreshViewerRows();
  const position=viewerRows.findIndex(x=>x.index===index);
  if(position<0){
    notify('这一张还没有成图','error');
    return}
  $('viewer').showModal();
  showViewerAt(position);
}

function stepViewer(delta){
  if(!$('viewer').open)return;
  showViewerAt(viewerPosition+delta);
}

function setViewerZoom(scale){
  $('viewerImage').style.transform=scale?`scale(${scale})`:'scale(1)';
}

// 查看器里点一下切换原图，避免为了一张图再开一次大图请求。
function toggleViewerFull(){
  const image=$('viewerImage');
  if(!image.dataset.full)return;
  const showingFull=image.dataset.fullShown==='1';
  const row=(reviewState.results||[]).find(x=>x.index===viewerIndex);
  image.src=showingFull
  ?'/api/preview?path='+encodeURIComponent(row?.copied_to||'')+'&size=1600'
  :image.dataset.full;
  image.dataset.fullShown=showingFull?'0':'1';
}

document.addEventListener('keydown',event=>{
  if(!$('viewer')?.open)return;
  if(event.key==='ArrowLeft'||event.key.toLowerCase()==='k'){event.preventDefault();stepViewer(-1)}
  else if(event.key==='ArrowRight'||event.key.toLowerCase()==='j'){event.preventDefault();stepViewer(1)}
  else if(event.key==='Enter'){event.preventDefault();toggleViewerFull()}
});

/* ---- 软件内替换缺失资源 -------------------------------------------------
A missing model is resolved here, in the software, instead of the user having
to find the folder and rename files. Nothing is substituted silently: the user
picks a real candidate, and the choice is visible in the audit afterwards. */
function replaceHtml(p){
  if(!p.node_id||!p.input_name)return '';
  if(p.category!=='missing_resource')return '';
  const candidates=(p.candidates||[]).filter(Boolean);
  if(!candidates.length)return '';
  const id=`rep-${p.node_id}-${p.input_name}`, sel=`sel-${id}`;
  return `<div class="replace">
  <label>替换为<select id="${
  sel}">${candidates.map(c=>`<option value="${
  esc(c)}">${esc(c)}</option>`).join('')}</select>
  </label>
  <div class="review-actions">
  <button class="secondary" onclick="applyReplacement('${esc(p.node_id)}','${esc(p.input_name)}','batch','${esc(p.node_type||'')}')">仅本次批次生效</button>
  <button onclick="applyReplacement('${esc(p.node_id)}','${esc(p.input_name)}','permanent','${esc(p.node_type||'')}')">保存为永久规则</button>
  </div>
  </div>`;
  renderParamCompactSummary();
}

function staleRulesHtml(rules){
  if(!rules.length)return '';
  return `<div class="issue warn">
  <h5>有 ${rules.length} 条替换规则因为工作流文件改动而暂停生效</h5>
  <table class="provenance">
  <tr>
  <th>节点</th>
  <th>参数</th>
  <th>替换为</th>
  </tr>
  ${rules.map(r=>`<tr>
  <td>${esc(r.node_id)} ${esc(r.node_type||'')}</td>
  <td>${esc(r.input_name)}</td>
  <td>${esc(r.value)}</td>
  </tr>`).join('')}
  </table>
  <p>工作流改动后节点编号的含义可能已经变了，所以这些规则不会自动生效。
  确认节点编号仍然对应后，可以点下面的按钮重新启用；也可以忽略，重新选一次。</p>
  <div class="review-actions">
  <button class="secondary" onclick="reapplyRules()">沿用旧替换规则</button>
  </div>
  </div>`;
}

async function reapplyRules(){
  const workflowPath=lastInspect.workflow_path||$('workflow').value;
  try{
    await api('/api/resource/reapply',{method:'POST',body:JSON.stringify({workflow_path:workflowPath})});
    notify('已重新启用旧替换规则','success');
    await openDrawer('inspect');
    preflightAction($('preflightButton'));
  }
  catch(e){
    notify(e.message,'error')}
}

function rulesHtml(rules){
  if(!rules.length)return '';
  return `<details open>
  <summary>已保存的替换规则（${rules.length}）</summary>
  <table class="provenance">
  <tr>
  <th>节点</th>
  <th>参数</th>
  <th>替换为</th>
  <th>
  </th>
  </tr>
  ${rules.map(r=>`<tr>
  <td>${esc(r.node_id)} ${esc(r.node_type||'')}</td>
  <td>${esc(r.input_name)}</td>
  <td>${esc(r.value)}</td>
  <td>
  <button class="secondary" onclick="forgetReplacement('${esc(r.node_id)}','${esc(r.input_name)}')">删除</button>
  </td>
  </tr>`).join('')}
  </table>
  </details>`;
}

function foldersHtml(dirs){
  const names=Object.keys(dirs||{});
  if(!names.length)return '';
  return `<details>
  <summary>模型目录（把缺失文件放进对应目录后点「重新检查」）</summary>
  <div class="review-actions">${names.map(n=>`<button class="secondary" onclick="openFolder('${esc(n)}')">打开 ${esc(n)}</button>`).join('')}</div>
  ${names.map(n=>`<div class="help">${esc(n)}：${esc(dirs[n])}</div>`).join('')}
  </details>`;
}

async function applyReplacement(nodeId,inputName,scope,nodeType){
  const pick=$(`sel-rep-${nodeId}-${inputName}`);
  const value=pick?pick.value:'';
  if(!value){
    notify('请先选择替换目标','error');
    return}
  const workflowPath=lastInspect.workflow_path||$('workflow').value;
  try{
    await api('/api/resource/apply',{method:'POST',body:JSON.stringify({
        workflow_path:workflowPath,node_id:nodeId,input_name:inputName,value,scope,node_type:nodeType})});
    if(scope==='batch'){
      batchOverrides=Object.assign({},batchOverrides);
      batchOverrides[nodeId]=Object.assign({},batchOverrides[nodeId]||{},{[inputName]:value});
      notify(`已应用：节点 ${nodeId} 的 ${inputName} → ${value}（仅本次批次）`,'success');
    }
    else{
      notify(`已保存永久规则：节点 ${nodeId} 的 ${inputName} → ${value}`,'success');
    }
    await openDrawer('inspect');
    preflightAction($('preflightButton'));
  }
  catch(e){
    notify(e.message,'error')}
}

async function forgetReplacement(nodeId,inputName){
  const workflowPath=lastInspect.workflow_path||$('workflow').value;
  try{
    await api('/api/resource/forget',{method:'POST',body:JSON.stringify({workflow_path:workflowPath,node_id:nodeId,input_name:inputName})});
    notify('已删除该替换规则','success');
    await openDrawer('inspect');
  }
  catch(e){
    notify(e.message,'error')}
}

async function openFolder(name){
  try{
    await api('/api/open-folder',{method:'POST',body:JSON.stringify({folder:name})})}
  catch(e){
    notify(e.message,'error')}
}

/* Seed display: the seed is what decides whether a redo can reproduce an image,
so it is shown on every card and carried in the review record. */
function seedHtml(x){
  const seeds=(x.generation||{}).seeds||{
  }
  ;
  const keys=Object.keys(seeds);
  if(!keys.length)return '';
  return keys.map(k=>`<dt>种子</dt>
  <dd>${esc(k)} = <code>${esc(String(seeds[k]))}</code>
  <button class="secondary seed-reuse" type="button" onclick="event.stopPropagation();reuseSeed('${esc(String(seeds[k]))}')">复用</button>
  </dd>`).join('');
}

function reuseSeed(value){
  if($('seedMode')&&$('seedMode').value!=='fixed'){
    $('seedMode').value='fixed';
    updateSeedHint()}
  if($('seedValue'))$('seedValue').value=value;
  notify(`已把种子 ${value} 填入批次种子框`,'success');
}

async function openDrawer(which,btn){
  const drawer=$('drawer');
  drawer.hidden=false;
  $('drawerTitle').textContent='生效检查与依赖';
  $('drawerBody').innerHTML='<div class="help">正在读取…</div>';
  try{
    const v=(await api('/api/inspect?v=1')).inspect||{
    }
    ;
    lastInspect=v;
    if(typeof renderRail==='function')renderRail();
    const blocking=v.blocking||[];
    // Problems found before a graph exists (structure, resources, parameters).
    // They are the reason a check can say 不能生成 while there is no audit to
    // inspect, so they belong at the top of this drawer, with their reason text.
    const early=v.preflight_errors||[];
    const audit=v.audit||{
    }
    ;
    const counts=audit.source_counts||{
    }
    ;
    const overrides=(audit.overrides||[]).slice(0,200);
    $('drawerBody').innerHTML=`
    <div class="help">节点定义 ${v.schema_nodes||0} 个 · 预检 ${v.ready?'通过':'未通过'} · 提交图输入 ${audit.input_count||0} 个</div>
    ${early.length?`<h4>预检未通过（${early.length}）</h4>${issuesHtml(early)}`:''}
    <div class="help">来源统计：连线 ${counts.linked||0} · 控件 ${counts.widget||0} · 本次覆盖 ${counts.override||0} · 注入 ${counts.injected||0}</div>
    ${blocking.length?blocking.map(p=>issuesHtml([p])+replaceHtml(p)).join('')
    :(audit.input_count?`<div class="help">没有阻断问题。</div>`
    :(v.inspected?'<div class="help">预检未产生提交图，原因见上方。</div>':'<div class="help">还没有可检查的提交图。先点「实际检查」或启动一次批次，这里会显示本次生效的实际参数。</div>'))}
    ${staleRulesHtml(v.stale_rules||[])}
    ${rulesHtml(v.rules||[])}
    ${foldersHtml(v.model_directories||{})}
    ${overrides.length?`<details open>
    <summary>本次运行覆盖的参数（${overrides.length}）</summary>
    <table class="provenance">
    <tr>
    <th>节点</th>
    <th>参数</th>
    <th>值</th>
    <th>来源</th>
    </tr>
    ${overrides.map(o=>`<tr>
    <td>${esc(o.node_id)} ${esc(o.class_type||'')}</td>
    <td>${esc(o.input)}</td>
    <td>${esc(String(o.value).slice(0,60))}</td>
    <td class="src">${esc(o.source_cn||o.source)}</td>
    </tr>`).join('')}
    </table>
    </details>`:''}
    `;
  }
  catch(e){
    $('drawerBody').innerHTML=`<div class="issue">
    <h5>无法读取检查结果</h5>
    <p>${esc(e.message)}</p>
    <p>请先执行一次「实际检查」或启动批次。</p>
    </div>`;
  }
}

function closeDrawer(){
  $('drawer').hidden=true}

document.addEventListener('keydown',e=>{
  if(e.target.matches('input,textarea,select'))return;
  if(document.getElementById('viewer')?.open)return;
  if(!document.getElementById('viewer').open&&$('drawer')&&!$('drawer').hidden&&e.key==='Escape'){closeDrawer();return}
  const rows=(reviewState.results||[]).filter(x=>x.copied_to);
  if(!rows.length)return;
  const at=rows.findIndex(x=>x.index===activeIndex);
  if(e.key==='j'||e.key==='J'){const next=rows[Math.min(rows.length-1,at+1)];setActive(next.index);document.getElementById('card-'+next.index)?.scrollIntoView({block:'nearest'})}
  else if(e.key==='k'||e.key==='K'){const prev=rows[Math.max(0,at-1)];setActive(prev.index);document.getElementById('card-'+prev.index)?.scrollIntoView({block:'nearest'})}
  else if((e.key==='y'||e.key==='Y')&&at>=0){reviewConfirm(rows[at].index,'已通过')}
  else if((e.key==='n'||e.key==='N')&&at>=0){reviewReject(rows[at].index)}
  else if(e.key==='Enter'&&at>=0){openViewer(rows[at].index)}
});

/* ---- 阶段 2 的工作流事实 + 常驻真相栏 -----------------------------------
两处显示同一批派生值，所以派生逻辑只写一次：effectiveFacts() 是唯一来源，
renderWorkflowFacts() 与 renderRail() 只负责摆放。否则迟早出现
"阶段 2 说 A、真相栏说 B" 这种自相矛盾。 */

function effectiveFacts() {
  const ins = lastInspect || {
  }
  , audit = ins.audit || {
  }
  , eff = ins.effective || {
  }
  ;
  const wf = eff.workflow || {
  }
  , variant = eff.variant || {
  }
  ;
  const base = eff.dimensions || {
  }
  ;
  const nodes = audit.nodes || {
  }
  ;

  // Flatten every traced input once; the rail and the facts panel both read it.
  const traces = [];
  for (const entry of Object.values(nodes)) {
    for (const [name, trace] of Object.entries(entry.inputs || {})) traces.push({ input: name, ...trace });
  }
  const overrides = (audit.overrides || []).length ? audit.overrides : traces;
  const paramCount = overrides.filter((o) => o.source === 'param').length;
  const replaced = overrides.filter((o) => o.source === 'user_replaced').length;

  // 预计输出尺寸：只用真正写进提交图的放大倍数来算，不猜。
  let factor = 1;
  const scaleNotes = [];
  for (const trace of traces) {
    if (trace.value_kind !== 'int' && trace.value_kind !== 'float') continue;
    const value = Number(trace.value);
    if (!isFinite(value)) continue;
    if (trace.input === 'upscale_by') {
      factor *= value;
      scaleNotes.push(`放大 ×${value}`);
    }
    if (trace.input === 'scale_by') {
      factor *= value;
      scaleNotes.push(`潜空间 ×${value}`);
    }
  }
  const predicted = (base.width && factor > 1.01)
  ? {
    width: Math.round(base.width * factor), height: Math.round(base.height * factor), factor: Number(factor.toFixed(2)) }
  : null;

  const samplers = (eff.nodes && eff.nodes.samplers) || [];
  const upscales = (eff.nodes && eff.nodes.upscale) || [];
  return {
    name: wf.name || '',
    path: wf.path || '',
    file: (wf.path || '').split(/[\\/]/).pop() || '',
    fingerprint: ins.fingerprint || wf.fingerprint || '',
    // 分支：优先用用户认得的中文名，同时保留配置真正发送的分支 id。
    branch: variant.name || audit.branch || '',
    branchId: variant.id || audit.branch || '',
    // 使用目的的判定来自审计（只有它看过编译后的提交图）。尚未审计时为空对象，
    // rail 与提示条据此显示"（未检查）"，不会把未核对说成通过。
    purpose: audit.purpose || {
    }
    ,
    branchDetail: variant.id ? `${variant.sampler_count || 0} 次采样 · ${variant.upscale_count || 0} 个放大` : '',
    model: (eff.model && eff.model.name) || '',
    base, predicted, scaleNotes,
    paramCount, replaced,
    samplers, upscales,
    blocking: (ins.blocking || []).length,
    io: {
      prompt: ((eff.nodes && eff.nodes.prompt) || []).length,
      image: ((eff.nodes && eff.nodes.image_input) || []).length,
      save: ((eff.nodes && eff.nodes.save) || []).length,
    }
    ,
    ready: !!ins.ready,
    // ``checked`` says whether an audit has actually run. Until it has, the rail
    // shows what the user selected and marks it 未检查, never as effective. A
    // workflow rejected before compiling has no audit, so its values stay marked
    // unverified -- but the blocking count below still reports the rejection.
    checked: !!(audit.node_count),
    // Two buckets, because both stop a run: the audit's findings on the compiled
    // graph, and the problems the first three layers find before there is a graph
    // at all. Counting only the first made the rail claim "没有阻断问题" for a
    // configuration the check had just declared unbuildable. The server adds the
    // two up so the page and the drawer cannot disagree.
    blocking: typeof ins.blocking_count==='number'?ins.blocking_count:((ins.blocking||[]).length+(ins.preflight_errors||[]).length),
    blockingClear: !!ins.ready,
    selected: {
      file: (($('workflow') && $('workflow').value) || '').split(/[\/]/).pop() || '',
      branch: ($('workflowVariant') && $('workflowVariant').selectedOptions && $('workflowVariant').selectedOptions[0] ? $('workflowVariant').selectedOptions[0].textContent : '') || '',
      model: ($('model') && $('model').value) || '',
    }
    ,
  }
  ;
}

function setText(id, text) {
  const el = $(id);
  if (el && el.textContent !== text) el.textContent = text;
}

function renderWorkflowFacts() {
  const f = effectiveFacts();
  setText('workflowFingerprint', f.fingerprint || '尚未读取');
  setText('actualBranch', f.branch
  ? `${f.branch}${f.branchId && f.branchId !== f.branch ? ` · ${f.branchId}` : ''}${f.branchDetail ? `（${f.branchDetail}）` : ''}`
  : '尚未选择');
  setText('chainSummary', (f.samplers.length || f.upscales.length)
  ? `${f.samplers.length} 个采样器 · ${f.upscales.length} 个放大节点`
  : '尚未读取');
  setText('ioSummary', `提示词 ${f.io.prompt} · 图片输入 ${f.io.image} · 输出 ${f.io.save}`);
}

function renderRail() {
  const f = effectiveFacts();
  // 已检查：显示真正生效的值。未检查：显示所选值并标注，避免把"选了什么"
  // 当成"生效了什么"。
  const mark = (v) => v ? (f.checked ? v : v + '（未检查）') : (f.checked ? '未发现' : '未选择');
  setText('railWorkflow', mark(f.file || f.selected.file));
  setText('railFingerprint', f.fingerprint || '尚未检查');
  setText('railBranch', f.branch
  ? (f.branchId && f.branchId !== f.branch ? `${f.branch}（${f.branchId}）` : f.branch)
  + (f.checked ? '' : '（未检查）')
  : mark(f.selected.branch));
  setText('railPurpose', purposeFactText(f));
  // B1：采样链路与输入输出这两格能力事实常驻真相栏，任何阶段可见。与合并
  // 面板里的 facts 共用同一个 effectiveFacts()，两边不可能各说各话。
  setText('railSamplers', (f.samplers.length || f.upscales.length)
  ? `${f.samplers.length} 个采样器 · ${f.upscales.length} 个放大节点`
  : (f.checked ? '未发现' : '尚未检查'));
  setText('railIO', f.checked
  ? `提示词 ${f.io.prompt} · 图片输入 ${f.io.image} · 输出 ${f.io.save}`
  : '尚未检查');
  setText('railModel', mark(f.model || f.selected.model));
  setText('railSize', f.base.width
  ? (f.predicted
  ? `${f.base.width}×${f.base.height} → ${f.predicted.width}×${f.predicted.height}（×${f.predicted.factor}）`
  : `${f.base.width}×${f.base.height}`)
  : '尚未检查');

  const parts = [];
  if (f.paramCount) parts.push(`${f.paramCount} 处来自参数工作台`);
  if (f.replaced) parts.push(`${f.replaced} 处用户替换`);
  setText('railParams', parts.length ? parts.join(' · ') : (f.checked ? '无覆盖' : '尚未检查'));

  const blocking = $('railBlocking');
  if (blocking) {
    blocking.textContent = f.blocking ? `阻断问题 ${f.blocking} 个（点击查看）` : (f.checked ? '没有阻断问题' : '尚未检查');
    blocking.classList.toggle('has-blocking', f.blocking > 0);
  }

  const review = (reviewState.review && reviewState.review.by_status) || {
  }
  ;
  const total = Object.values(review).reduce((a, b) => a + b, 0);
  setText('railReview', total
  ? `待确认 ${review['待确认'] || 0} · 已通过 ${review['已通过'] || 0} · 需重做 ${review['需重做'] || 0} · 已替换 ${review['已替换'] || 0}`
  : '尚无结果');

  const st = railStatus || {
  }
  ;
  const done = (st.completed || 0) + (st.errors || 0);
  const bar = $('railProgress');
  if (bar) bar.style.width = (st.total ? Math.round(100 * done / st.total) : 0) + '%';
  setText('railRun', st.status
  ? `${st.status}${st.current ? ' · ' + st.current : ''}${st.total ? ` · ${done}/${st.total}` : ''}`
  : '空闲');
}

connectEvents();
