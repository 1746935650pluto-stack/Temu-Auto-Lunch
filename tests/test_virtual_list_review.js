const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {createDocument} = require('./dom_fixture');
const source = fs.readFileSync(path.join(__dirname, '..', 'shared-automation.js'), 'utf8');
const code = (start, end) => source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));

test('status replacement restores the same action, or the same row if that action vanished', () => {
  const document = createDocument();
  document.body = document.createElement('body');
  const prototype = Object.getPrototypeOf(document.body);
  const replace = prototype.replaceChildren, remove = prototype.remove;
  prototype.replaceChildren = function(...children) {
    if (this.children.some(node => node.contains(document.activeElement))) document.activeElement = document.body;
    return replace.apply(this, children);
  };
  prototype.remove = function() {
    if (this.parentNode && this.contains(document.activeElement)) document.activeElement = document.body;
    return remove.call(this);
  };
  prototype.getBoundingClientRect = function() {return {height:100,width:1000};};
  const node = document.createElement('div');
  node.clientHeight = 400; node.clientWidth = 1000; document.body.append(node);
  const window = {requestAnimationFrame:()=>1, cancelAnimationFrame(){}, innerHeight:1000};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '..', 'virtual-list.js'), 'utf8'), {window,document});
  const list = new window.SharedVirtualList(node, {estimateHeight:()=>100, onViews(){}, createView() {
    const line = document.createElement('article'); let status;
    return {line, update(row) {
      if (status === row.status) return;
      status = row.status;
      const details = document.createElement('button'); details.textContent = 'Details';
      line.replaceChildren(details);
      if (row.status !== 'running') {const retry=document.createElement('button');retry.textContent='Retry';line.append(retry);}
    }};
  }});
  const rows = Array.from({length:100}, (_,index)=>({key:String(index),status:'failed'}));
  list.setRows(rows);
  const view = list.views.get('0'), oldDetails = view.line.children[0]; oldDetails.focus();
  list.setRows(rows.map(row=>row.key==='0'?{...row,status:'needs_review'}:row));
  assert.notEqual(view.line.children[0], oldDetails);
  assert.equal(document.activeElement, view.line.children[0]);
  view.line.children[1].focus();
  list.setRows(rows.map(row=>row.key==='0'?{...row,status:'running'}:row));
  assert.equal(document.activeElement, view.line);
  assert.equal(view.line.tabIndex, -1);
});

test('pager keeps pending controls disabled and preserves final first/last page boundaries', async () => {
  const document=createDocument(); document.createNode('sharedJobsPager');
  const state={jobs:{page:1,pageSize:50,selected:new Map()}};
  let release;
  const context=vm.createContext({document,state,$:id=>document.getElementById(id),notice(){},updateTaskControls(){},
    refresh:async()=>{
      context.pager('jobs',{page:state.jobs.page,pages:2,total:100});
      await new Promise(resolve=>{release=resolve;});
    }});
  vm.runInContext(code('  function element(', '  async function api(')+code('  function pager(', '  function selection('),context);
  context.pager('jobs',{page:1,pages:2,total:100});
  const view=state.jobs.pagerView;
  const next=view.next.dispatch('click');
  assert.equal(view.next.disabled,true);
  assert.equal(state.jobs.page,2);
  release();await next;
  assert.equal(view.next.disabled,true,'last-page boundary must survive the click finally');
  const previous=view.prev.dispatch('click');
  assert.equal(view.prev.disabled,true);
  release();await previous;
  assert.equal(state.jobs.page,1);
  assert.equal(view.prev.disabled,true,'first-page boundary must survive the click finally');
});
