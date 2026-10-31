const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'..','live-editor.js'),'utf8');
function setup() {
  const rule={name:'面料克重1（g/m²）',templatePid:1,pid:2,valueType:'NUMBER',valueUnit:['g/m²']};
  const state={item:{cid:'100',attributes:[]},attributeRules:[rule],attributeRulesCid:'100',attributeRequestVersion:0,requestedAttributeNames:[]};
  const fields={editCid:{value:'100'},editAttributes:{value:'[]'},liveAttributesHint:{textContent:''}};
  let renders=0,reads=0;
  const calls=[];
  const context=vm.createContext({editorState:state,editEl:id=>fields[id],AbortController,
    window:{setTimeout,clearTimeout},syncAttributeJson(){fields.editAttributes.value=JSON.stringify(state.item.attributes);},
    renderProductAttributes(){renders++;fields.liveAttributesHint.textContent='rendered';},
    apiGet:async()=>{reads++;return {productAttributeRules:[rule]};},
    fetch:async(url,init)=>{calls.push({url,body:JSON.parse(init.body)});return {ok:true,json:async()=>({data:{attributes:[{templatePid:1,pid:2,name:rule.name,values:[{vid:0,name:'5',numberValue:5,valueUnit:'g/m²'}]}]}})};}});
  vm.runInContext(source.slice(source.indexOf('function attributeForRule('),source.indexOf('function renderAttributeSelect(')),context);
  return {context,state,fields,calls,rule,get renders(){return renders;},get reads(){return reads;}};
}

test('manual cached-category entry awaits backend defaults and preserves their validated values',async()=>{
  const f=setup();await f.context.loadAttributeRules('100');
  assert.equal(f.reads,0);assert.equal(f.calls.length,1);assert.equal(f.calls[0].url,'/api/item/defaults');
  assert.deepEqual(f.calls[0].body.rules,[f.rule]);assert.equal(f.state.item.attributes[0].values[0].numberValue,5);
  assert.equal(JSON.parse(f.fields.editAttributes.value)[0].values[0].numberValue,5);assert.equal(f.renders,1);
});

test('cached-category default failure is handled and retains options, attributes and a visible error',async()=>{
  const f=setup();const attribute={templatePid:1,pid:2,values:[{numberValue:72}]};f.state.item.attributes=[attribute];f.fields.editAttributes.value=JSON.stringify([attribute]);
  f.context.fetch=async()=>{throw Error('offline');};
  await assert.doesNotReject(f.context.loadAttributeRules('100'));
  assert.equal(f.state.attributeRules[0],f.rule);assert.equal(f.state.attributeRulesCid,'100');assert.equal(f.state.item.attributes[0],attribute);
  assert.match(f.fields.liveAttributesHint.textContent,/默认值读取失败.*offline/);assert.equal(f.renders,1);
});

test('default preview cannot overwrite direct JSON edits or visible attribute edits made during loading',async()=>{
  for(const advanced of [true,false]){
    const f=setup();let finish;
    f.context.fetch=()=>new Promise(resolve=>{finish=resolve;});const pending=f.context.loadAttributeRules('100');
    if(advanced)f.fields.editAttributes.value='[{"name":"manual"}]';else f.state.item.attributes.push({name:'manual',values:[{numberValue:99}]});
    finish({ok:true,json:async()=>({data:{attributes:[{name:'service'}]}})});await pending;
    if(advanced)assert.equal(f.fields.editAttributes.value,'[{"name":"manual"}]');else assert.equal(f.state.item.attributes[0].name,'manual');
    assert.equal(f.renders,0);assert.match(f.fields.liveAttributesHint.textContent,/保留/);
  }
});

test('switching items or clearing category invalidates late default results and errors',async()=>{
  for(const clear of [true,false]){
    const f=setup();let reject;
    f.context.fetch=()=>new Promise((_,failed)=>{reject=failed;});const pending=f.context.loadAttributeRules('100');
    if(clear){f.fields.editCid.value='';await f.context.loadAttributeRules('');}
    else {f.state.item={cid:'100',attributes:[{name:'new item'}]};f.fields.liveAttributesHint.textContent='new item';}
    const hint=f.fields.liveAttributesHint.textContent, renders=f.renders;
    reject(Error('old request'));await pending;
    assert.equal(f.fields.liveAttributesHint.textContent,hint);assert.equal(f.renders,renders);
    if(clear)assert.equal(f.state.attributeRules.length,0);else assert.equal(f.state.item.attributes[0].name,'new item');
  }
});

test('newer same-category preview wins and malformed service data never replaces current attributes',async()=>{
  const f=setup(),pending=[];
  f.context.fetch=()=>new Promise(resolve=>pending.push(resolve));
  const first=f.context.loadAttributeRules('100'),second=f.context.loadAttributeRules('100');
  pending[1]({ok:true,json:async()=>({data:{attributes:[{name:'newer'}]}})});await second;
  pending[0]({ok:true,json:async()=>({data:{attributes:[{name:'older'}]}})});await first;
  assert.equal(f.state.item.attributes[0].name,'newer');
  f.context.fetch=async()=>({ok:true,json:async()=>({data:{}})});await f.context.loadAttributeRules('100');
  assert.equal(f.state.item.attributes[0].name,'newer');assert.match(f.fields.liveAttributesHint.textContent,/未返回有效/);
});
