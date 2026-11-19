const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {createDocument}=require('./dom_fixture');
const source=fs.readFileSync(require('node:path').join(__dirname,'..','shared-automation.js'),'utf8');
test('shared dialog uses a rounded clipping shell and remains dismissible',()=>{
 const document=createDocument();document.body=document.createElement('body');const state={modal:null};
 const ctx=vm.createContext({document,state,AbortController,element:(tag,text='',cls='')=>{const n=document.createElement(tag);n.textContent=text;n.className=cls;return n;},
  button:(text,action)=>{const n=document.createElement('button');n.textContent=text;n.action=action;return n;}});
 vm.runInContext(source.slice(source.indexOf('  function modal('),source.indexOf('  function pager(')),ctx);
 const dialog=ctx.modal('测试标题');assert.equal(dialog.overlay.children[0].className,'shared-modal-shell');
 assert.equal(dialog.overlay.children[0].children[0],dialog.card);assert.equal(dialog.heading.textContent,'测试标题');
 assert.equal(dialog.overlay.attrs.role,'dialog');assert.equal(state.modal,dialog);
 dialog.card.children[0].action();assert.equal(dialog.closed,true);assert.equal(dialog.abortController.signal.aborted,true);assert.equal(state.modal,null);
});
