'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const root = path.resolve(__dirname, '../../backend/web');
function setup(t) {
  const dom=new JSDOM(fs.readFileSync(path.join(root,'index.html'),'utf8'),{url:'http://127.0.0.1:18000/admin',runScripts:'outside-only'});
  t.after(async()=>{await new Promise(resolve=>setTimeout(resolve,0));dom.window.close();});const w=dom.window;
  w.tr=(s,...a)=>s.replace(/\{(\d+)\}/g,(_,i)=>String(a[i]));
  w.fetch=()=>new Promise(()=>{});
  require('node:vm').runInContext(fs.readFileSync(path.join(root,'assets/admin.js'),'utf8'),dom.getInternalVMContext());
  const messages=[];w.postMessage=message=>messages.push(message);require('node:vm').runInContext(fs.readFileSync(path.join(root,'assets/ollama-setup.js'),'utf8'),dom.getInternalVMContext());
  const state={phase:'checked',busy:false,installed:false,ready:false,selected:{model:'qwen3:4b',contextLength:8192},
    report:{eligible:true,blockers:[],warnings:[],system:{ram:16*1024**3,threads:8},modelBytes:2*1024**3}};
  const receive=value=>w.dispatchEvent(new w.MessageEvent('message',{source:w,origin:w.location.origin,data:{channel:'improver-local-ollama-v1',from:'desktop',state:{...state,...value}}}));
  return {w,receive,messages,el:id=>w.document.getElementById(id)};
}
test('ordinary browsers never offer local installation; incompatible hardware blocks desktop button',t=>{
  const {el,receive,w}=setup(t);
  assert.equal(el('localOllama').hidden,true);
  el('llmProvider').value='local';el('llmProvider').dispatchEvent(new w.Event('change'));
  receive({report:{eligible:false,blockers:[{code:'ram',args:[8]}],warnings:[],system:{ram:4*1024**3,threads:8}}});
  assert.equal(el('localOllama').hidden,false);assert.equal(el('localOllamaUse').disabled,true);
  assert.match(el('localOllamaChecks').textContent,/8/);
});
test('changing model invalidates inspection and repeated clicks cannot duplicate setup',t=>{
  const {el,receive,w}=setup(t);receive({});assert.equal(el('localOllamaUse').disabled,false);
  el('localOllamaModel').value='qwen3:14b';el('localOllamaModel').dispatchEvent(new w.Event('input'));
  assert.equal(el('localOllamaUse').disabled,true);
  receive({busy:true,phase:'downloading',progress:0.5});
  assert.equal(el('localOllamaUse').disabled,true);assert.equal(el('localOllamaProgressGroup').hidden,true);
  assert.equal(el('localOllamaCancel').hidden,false);
});
test('one click prepares the model, shows overall progress and connects only after verification',async t=>{
  const {el,receive,w,messages}=setup(t);const writes=[];
  w.request=async(url,options)=>{writes.push({url,body:JSON.parse(options.body)});return {settings:{llm:writes[0].body.settings.llm}};};
  w.setValue=(id,value)=>{el(id).value=value;};w.toast=()=>{};w.loadStatus=()=>{};
  el('llmUrl').value='https://remote.example/v1';el('identityNames').value='Unsaved owner name';
  receive({phase:'idle',report:null});
  el('llmProvider').value='local';el('llmProvider').dispatchEvent(new w.Event('change'));
  assert.equal(messages.at(-1).action,'inspect');
  receive({});
  assert.equal(el('localOllamaUse').disabled,false);
  assert.equal(writes.length,0);
  el('localOllamaUse').click();el('localOllamaUse').click();
  assert.equal(messages.filter(m=>m.action==='setup').length,1);
  assert.equal(el('localOllamaProgressGroup').hidden,false);
  receive({phase:'downloading',busy:true,progress:0.5});
  assert.equal(el('localOllamaProgress').value,17);
  assert.equal(el('localOllamaPercent').textContent,'17%');
  receive({phase:'pulling',busy:true,progress:0.5});
  assert.equal(el('localOllamaProgress').value,65);
  receive({phase:'verifying',busy:true});
  assert.equal(el('localOllamaProgress').value,90);
  assert.equal(writes.length,0);
  receive({phase:'model_ready',busy:true,ready:true,modelReady:true});
  assert.equal(writes.length,0);
  receive({phase:'model_ready',ready:true,modelReady:true});
  await new Promise(resolve=>setTimeout(resolve,0));
  assert.deepEqual(writes,[{url:'/settings',body:{settings:{llm:{provider:'ollama',base_url:'http://127.0.0.1:11434',model:'qwen3:4b',context_length:8192}}}}]);
  assert.equal(el('identityNames').value,'Unsaved owner name');
  assert.equal(el('llmProvider').value,'local');
  assert.equal(el('localOllamaPercent').textContent,'100%');
  assert.equal(el('localOllamaUse').disabled,true);
  el('localOllamaUse').click();
  assert.equal(messages.filter(m=>m.action==='setup').length,1);
});

test('failed or cancelled setup never switches the current model', t=>{
  const {w,el,receive}=setup(t);
  let writes=0;w.request=async()=>{writes++;};
  el('llmProvider').value='local';receive({});
  for(const phase of ['error','cancelled','blocked']) {
    receive({});el('localOllamaUse').click();
    receive({phase,error:phase==='error'?'model_test':null});
    assert.equal(writes,0);
    assert.equal(el('llmProvider').disabled,false);
    assert.equal(el('localOllamaProgressGroup').hidden,true);
  }
});

test('model edits trigger a new automatic resource check', async t=>{
  const {w,el,receive,messages}=setup(t);
  el('llmProvider').value='local';receive({});
  el('localOllamaModel').value='qwen3:8b';
  el('localOllamaModel').dispatchEvent(new w.Event('input'));
  assert.equal(el('localOllamaUse').disabled,true);
  await new Promise(resolve=>setTimeout(resolve,400));
  assert.equal(messages.at(-1).action,'inspect');
  assert.equal(messages.at(-1).model,'qwen3:8b');
});

test('local installation instructions and blockers are localized',t=>{
  const {w,receive,el}=setup(t);
  w.eval(fs.readFileSync(path.join(root,'assets/translations.js'),'utf8'));
  w.localStorage.setItem('secretary.language','en');
  w.eval(fs.readFileSync(path.join(root,'assets/i18n.js'),'utf8'));
  receive({phase:'blocked',report:{eligible:false,blockers:[{code:'ram',args:[8]}],warnings:[],system:{ram:4*1024**3,threads:8}}});
  assert.equal(el('localOllamaTitle').textContent,'Local Ollama');
  assert.match(el('localOllamaChecks').textContent,/8 GiB of RAM/);
});

test('provider choice controls fields and desktop progress does not reopen local setup', t => {
  const {w,el,receive}=setup(t);
  const select=value=>{el('llmProvider').value=value;el('llmProvider').dispatchEvent(new w.Event('change'));};
  assert.equal(el('remoteModelSettings').hidden,false);
  assert.equal(el('openaiKeySettings').hidden,true);
  select('openai');
  assert.equal(el('openaiKeySettings').hidden,false);
  assert.equal(el('openaiUrlHint').hidden,false);
  select('local');
  assert.equal(el('remoteModelSettings').hidden,true);
  assert.equal(el('localOllamaUnavailable').hidden,false);
  receive({});
  assert.equal(el('localOllamaUnavailable').hidden,true);
  assert.equal(el('localOllama').hidden,false);
  select('ollama');
  receive({phase:'pulling',busy:true});
  assert.equal(el('localOllama').hidden,true);
  assert.equal(el('remoteModelSettings').hidden,false);
  assert.equal(el('openaiKeySettings').hidden,true);
  assert.equal(w.savedModelProvider({provider:'ollama',base_url:'http://127.0.0.1:11434'}),'local');
  assert.equal(w.savedModelProvider({provider:'openai',base_url:'http://127.0.0.1:11434/v1'}),'openai');
});


test('saved local connection stays locked until another LLM is saved, including after reopening settings', async t => {
  const {w,el,receive,messages}=setup(t);
  const local={provider:'ollama',base_url:'http://127.0.0.1:11434',model:'qwen3:4b',context_length:8192};
  w.eval('settingsState = '+JSON.stringify({llm:local}));
  receive({});
  receive({runtimeOnly:true,runtime:{available:true,installedModels:['qwen3:4b'],models:[]}});
  assert.equal(el('localOllamaUse').disabled,true);
  assert.match(el('localOllamaStatus').textContent,/подключена/);
  el('localOllamaModel').value='qwen3:8b';
  el('localOllamaModel').dispatchEvent(new w.Event('input'));
  el('llmProvider').value='openai';el('llmProvider').dispatchEvent(new w.Event('change'));
  el('llmProvider').value='local';el('llmProvider').dispatchEvent(new w.Event('change'));
  await new Promise(resolve=>setTimeout(resolve,400));
  assert.equal(el('localOllamaUse').disabled,true);
  assert.equal(messages.some(m=>m.action==='inspect'||m.action==='setup'),false);
  w.eval('settingsState = '+JSON.stringify({llm:{provider:'openai',base_url:'https://api.openai.com/v1'}}));
  w.renderModelSettings();
  receive({selected:{model:'qwen3:8b',contextLength:8192}});
  assert.equal(el('localOllamaUse').disabled,false);
});


test('runtime panel renders live CPU/GPU allocation safely without changing setup state', t => {
  const {w,el,receive,messages}=setup(t);
  receive({});
  const initialDisabled=el('localOllamaUse').disabled;
  receive({runtimeOnly:true,runtime:{available:true,version:'0.34.2',endpoint:'http://127.0.0.1:11434',models:[
    {name:'<img src=x onerror=alert(1)>',size:4*1024**3,sizeVRAM:3*1024**3,contextLength:8192,quantization:'Q4_K_M'}]}});
  assert.match(el('localOllamaRuntimeModels').textContent,/25% CPU \/ 75% GPU/);
  assert.match(el('localOllamaRuntimeModels').textContent,/8192/);
  assert.match(el('localOllamaRuntimeVersion').textContent,/0.34.2/);
  assert.equal(el('localOllamaRuntimeModels').querySelector('img'),null);
  assert.equal(el('localOllamaUse').disabled,initialDisabled);
  assert.equal(messages.some(m=>m.action==='setup'),false);
  receive({runtimeOnly:true,runtime:{available:true,version:'0.34.2',endpoint:'http://127.0.0.1:11434',models:[]}});
  assert.match(el('localOllamaRuntimeStatus').textContent,/не загружены/);
  receive({runtimeOnly:true,runtime:{available:false,models:null}});
  assert.equal(el('localOllamaRuntimeVersion').textContent,'');
  assert.equal(el('localOllamaRuntimeModels').textContent,'');
  assert.match(el('localOllamaRuntimeStatus').textContent,/не отвечает/);
});


test('fresh desktop defaults do not claim a connection or lock installation without the configured model', t => {
  const {w,el,receive}=setup(t);
  w.eval('settingsState = {llm:{provider:"ollama",base_url:"http://127.0.0.1:11434",model:"qwen3:4b"}}');
  receive({});
  for (const runtime of [
    {available:false,models:null,installedModels:null},
    {available:true,models:[],installedModels:[]},
    {available:true,models:[],installedModels:['other:latest']},
    {available:true,models:[],installedModels:null},
  ]) {
    receive({runtimeOnly:true,runtime});
    assert.doesNotMatch(el('localOllamaStatus').textContent,/подключена/);
    assert.equal(el('localOllamaUse').disabled,false);
    assert.equal(el('localOllamaProgressGroup').hidden,true);
  }
  receive({runtimeOnly:true,runtime:{available:true,models:[],installedModels:['qwen3:4b']}});
  assert.match(el('localOllamaStatus').textContent,/подключена/);
  assert.equal(el('localOllamaUse').disabled,true);
  receive({runtimeOnly:true,runtime:{available:false,models:null,installedModels:null}});
  assert.doesNotMatch(el('localOllamaStatus').textContent,/подключена/);
  assert.equal(el('localOllamaUse').disabled,false);
});
