const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'live-editor.js'), 'utf8');

test('a task error exposes its named missing attributes through the shared default service', async () => {
  const context = vm.createContext({
    editorState: {
      item: { attributes: [] },
      requestedAttributeNames: ['面料纹理', '平方克重'],
      attributeRules: [
        { name: '面料纹理', templatePid: 11, pid: 12, valueType: 'SELECT', values: [{ vid: 1, name: '条纹' }] },
        { name: '平方克重', templatePid: 21, pid: 22, valueType: 'NUMBER', minValue: 80, maxValue: 300, valueUnit: ['g/㎡'] },
      ],
    },
    syncAttributeJson() {},
    editEl:()=>({value:'[]'}),AbortController,window:{setTimeout,clearTimeout},
    fetch:async(url,init)=>{
      assert.equal(url,'/api/item/defaults');
      const body=JSON.parse(init.body);assert.deepEqual(body.targets,['面料纹理','平方克重']);
      return {ok:true,json:async()=>({data:{attributes:body.item.attributes}})};
    },
  });
  const start = source.indexOf('function ruleForAttribute');
  const end = source.indexOf('function valueFromRuleChoice', start);
  vm.runInContext(source.slice(start, end), context);
  const names = vm.runInContext("automationTaskAttributeNames({message: '产品属性自动识别未完成：【面料纹理】资料不足', events: [{message: '【平方克重】资料不足'}]})", context);
  assert.deepEqual(Array.from(names), ['面料纹理', '平方克重']);
  await vm.runInContext('ensureRequiredAttributes()', context);
  const attributes = vm.runInContext('editorState.item.attributes', context);
  assert.equal(attributes.length, 2);
  assert.deepEqual(JSON.parse(JSON.stringify(attributes[0].values)), []);
  assert.deepEqual(JSON.parse(JSON.stringify(attributes[1].values)), [{
    vid: 0, name: '', numberValue: '', valueUnit: 'g/㎡',
  }]);
});


test('opening a LULU task activates the workspace without clicking another account',async()=>{
  const nodes=new Map(),pages=[],reads=[];
  const editEl=id=>{if(!nodes.has(id))nodes.set(id,{textContent:'',classList:{remove(){}}});return nodes.get(id);};
  const context=vm.createContext({Number,String,Object,URLSearchParams,
    editorState:{},editEl,editorError(){},claimStatus(){},showToast:message=>{throw Error(message);},
    activatePage:page=>pages.push(page),
    document:{querySelector(){throw Error('must not click first account navigation');}},
    window:{collectionAccountContext:{id:4},dispatchEvent(){}},CustomEvent:class{},
    apiGet:async url=>{reads.push(url);return {version:'v',item:{title:'LULU product'}};},
    cloneItem:value=>structuredClone(value),automationTaskAttributeNames:()=>[],fillEditor(){},
  });
  const start=source.indexOf('async function openEditorForAutomationTask(');
  vm.runInContext(source.slice(start,source.indexOf('function scrollEditorStage(',start)),context);
  await context.openEditorForAutomationTask({detailId:3491193278,shopId:19180606,site:'US',status:'needs_input'});
  assert.deepEqual(pages,['workbench']);assert.equal(reads.length,1);
  assert.match(reads[0],/detailId=3491193278/);assert.match(reads[0],/shopId=19180606/);
  assert.equal(context.window.collectionAccountContext.id,4);
  assert.equal(context.editorState.item.title,'LULU product');
});
