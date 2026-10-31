const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const {createDocument}=require('./dom_fixture');
const source=fs.readFileSync(require('node:path').join(__dirname,'..','app.js'),'utf8');
function setup() {
  const document=createDocument(),requests=[],messages=[];
  for(const id of ['deepSeekProfileName','deepSeekProfileKey'])document.createNode(id,'input');
  for(const id of ['saveDeepSeekProfile','refreshDeepSeekProfiles'])document.createNode(id,'button');
  for(const id of ['deepSeekProfileForm','deepSeekProfileList','deepSeekAccountAssignments','deepSeekProfileStatus'])document.createNode(id);
  const context=vm.createContext({document,$:selector=>document.querySelector(selector),
    window:{confirm:()=>true},miaoshouCollectionAccounts:[{id:1,name:'NL',configured:true},{id:2,name:'LULU',configured:true}],
    clearDeepSeekInput(){document.getElementById('deepSeekProfileKey').value='';},showToast:text=>messages.push(text),
    apiGet:async()=>({revision:0,profiles:[],assignments:{},default:{configured:true}}),
    fetch:async(url,options)=>{requests.push({url,body:JSON.parse(options.body)});return {ok:true,json:async()=>({data:{revision:1,profiles:[],assignments:{}}})};}});
  vm.runInContext(source.slice(source.indexOf('function deepSeekProfileId()'),source.indexOf('function organizeLiveModules()')),context);
  return {context,document,requests,messages,$:id=>document.getElementById(id)};
}
const event={preventDefault(){}};
const configured={revision:2,profiles:[{id:'alpha',name:'Alpha'}],assignments:{1:'alpha'},default:{configured:true}};

test('profile creation is single-flight and stale GET cannot erase the committed API from assignment choices',async()=>{
  const {context,$,requests}=setup();let finishRead,finishSave;
  context.apiGet=()=>new Promise(resolve=>{finishRead=resolve;});const reading=context.loadDeepSeekProfiles();
  $('deepSeekProfileName').value='Alpha';$('deepSeekProfileKey').value='private-fixture-key';
  context.fetch=(url,options)=>{requests.push({url,body:JSON.parse(options.body)});return new Promise(resolve=>{finishSave=resolve;});};
  const saving=context.saveDeepSeekProfile(event);await context.saveDeepSeekProfile(event);
  assert.equal(requests.length,1);assert.equal(requests[0].body.action,'create');assert.equal(requests[0].body.confirmation,'CREATE_DEEPSEEK_PROFILE');
  assert.equal($('saveDeepSeekProfile').disabled,true);
  const result={...configured,profiles:[{id:requests[0].body.id,name:'Alpha'}]};
  finishSave({ok:true,json:async()=>({data:result})});await saving;
  finishRead({revision:0,profiles:[],assignments:{}});await reading;
  assert.match($('deepSeekProfileList').textContent,/Alpha/);
  const choices=$('deepSeekAccountAssignments').querySelectorAll('select');assert.equal(choices[0].options.length,2);
  assert.equal($('deepSeekProfileKey').value,'');assert.equal($('saveDeepSeekProfile').disabled,false);
  assert.ok(!$('deepSeekProfileList').textContent.includes('private-fixture-key'));
});

test('failed assignment restores the confirmed selection and keeps available profiles visible',async()=>{
  const {context,$}=setup();context.renderDeepSeekProfiles(configured);
  const choice=$('deepSeekAccountAssignments').querySelectorAll('select')[0];choice.value='';
  context.fetch=async()=>({ok:false,json:async()=>({error:'account busy'})});
  await choice.dispatch('change');
  const restored=$('deepSeekAccountAssignments').querySelectorAll('select')[0];
  assert.equal(restored.value,'alpha');assert.equal(restored.disabled,false);assert.match($('deepSeekProfileStatus').textContent,/account busy/);
  assert.match($('deepSeekProfileList').textContent,/Alpha/);
});

test('replace is an explicit edit of an existing ID, preserves assignments and can be canceled back to create',async()=>{
  const {context,$,requests}=setup();context.renderDeepSeekProfiles(configured);
  const replace=$('deepSeekProfileList').querySelectorAll('button').find(button=>button.textContent==='替换密钥');
  await replace.dispatch('click');assert.equal($('deepSeekProfileName').value,'Alpha');assert.equal($('saveDeepSeekProfile').textContent,'替换此 API 密钥');
  $('deepSeekProfileKey').value='replacement-fixture';
  context.fetch=async(url,options)=>{requests.push({url,body:JSON.parse(options.body)});return {ok:true,json:async()=>({data:{...configured,revision:3}})};};
  await context.saveDeepSeekProfile(event);
  assert.equal(requests[0].body.action,'replace');assert.equal(requests[0].body.id,'alpha');assert.equal(requests[0].body.confirmation,'REPLACE_DEEPSEEK_PROFILE');
  assert.equal($('deepSeekAccountAssignments').querySelectorAll('select')[0].value,'alpha');
  assert.equal($('saveDeepSeekProfile').textContent,'新增 API 配置');
  context.editDeepSeekProfile(configured.profiles[0]);await $('cancelDeepSeekProfileEdit').dispatch('click');
  assert.equal(context.saveDeepSeekProfile.editId,'');assert.equal($('deepSeekProfileKey').value,'');
});

test('failed creation preserves name and request ID for safe retry, clears the secret and rejects older revisions',async()=>{
  const {context,$,requests}=setup();context.renderDeepSeekProfiles(configured);
  $('deepSeekProfileName').value='New';$('deepSeekProfileKey').value='fixture-key';
  context.fetch=async(url,options)=>{requests.push({url,body:JSON.parse(options.body)});throw Error('connection lost');};
  await context.saveDeepSeekProfile(event);
  assert.equal($('deepSeekProfileName').value,'New');assert.equal($('deepSeekProfileKey').value,'');
  $('deepSeekProfileKey').value='fixture-key';await context.saveDeepSeekProfile(event);
  assert.equal(requests.length,2);assert.equal(requests[0].body.id,requests[1].body.id);
  assert.equal(context.renderDeepSeekProfiles({revision:1,profiles:[],assignments:{}}),false);
  assert.match($('deepSeekProfileList').textContent,/Alpha/);
});

test('version label distinguishes loaded interface resources from the running backend',()=>{
  const {context,document}=setup();const label=document.createNode('version');label.className='app-version';label.dataset.resourceVersion='1.2.149';
  vm.runInContext(source.slice(source.indexOf('function updateApplicationVersion('),source.indexOf('async function checkApiStatus()')),context);
  context.updateApplicationVersion({version:'1.2.142'});
  assert.equal(label.textContent,'界面 V1.2.149 · 后台 V1.2.142 · 待安全更新');
  context.updateApplicationVersion({runtimeVersion:'1.2.149',resourceVersion:'1.2.149',pendingUpdate:false});
  assert.equal(label.textContent,'界面 V1.2.149 · 后台 V1.2.149');
  label.dataset.resourceVersion='1.2.146';
  context.updateApplicationVersion({runtimeVersion:'1.2.149',resourceVersion:'1.2.149',pendingUpdate:false});
  assert.match(label.textContent,/请刷新网页/);assert.equal(label.dataset.pendingUpdate,'false');
});
