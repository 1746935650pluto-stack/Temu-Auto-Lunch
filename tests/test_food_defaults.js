const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '..', 'live-editor.js'), 'utf8');

test('editor applies the shared service response without adding frontend food-contact defaults', async () => {
  for (const vid of [67314, 67313]) {
    const state = {item:{attributes:[{name:'是否可用于食品接触',templatePid:10,pid:1,values:[{vid,name:vid===67314?'否':'是'}]}]},
      attributeRules:[{name:'是否可用于食品接触',templatePid:10,pid:1,values:[{vid:67313,name:'是'},{vid:67314,name:'否'}]},
        {name:'与食品接触部位材质',templatePid:20,pid:2,parentTemplatePid:10,values:[{vid:30,name:'不锈钢',parentVidList:[67313]}]}]};
    const expected=JSON.parse(JSON.stringify(state.item.attributes));
    if(vid===67313)expected.push({name:'与食品接触部位材质',templatePid:20,pid:2,values:[{vid:30,name:'不锈钢'}]});
    const ctx = vm.createContext({editorState:state, syncAttributeJson:()=>{},editEl:()=>({value:'[]'}),AbortController,
      window:{setTimeout,clearTimeout},fetch:async(url,init)=>{
        assert.equal(url,'/api/item/defaults');assert.equal(JSON.parse(init.body).item.attributes[0].values[0].vid,vid);
        return {ok:true,json:async()=>({data:{attributes:expected}})};
      }});
    vm.runInContext(source.slice(source.indexOf('function attributeForRule('), source.indexOf('async function loadAttributeRules(')), ctx);
    await ctx.ensureRequiredAttributes();
    assert.equal(state.item.attributes.length,vid===67314?1:2);
    assert.equal(state.item.attributes[0].values[0].vid,vid);
  }
});
