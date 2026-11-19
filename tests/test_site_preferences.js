const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const {createDocument}=require('./dom_fixture');
const source=fs.readFileSync(require('node:path').join(__dirname,'..','shipping-bindings.js'),'utf8').split('/* Explicit local logistics')[0];
test('saved sites limit all selectors and can be updated without changing tasks',async()=>{
 const document=createDocument();document.documentElement=document.createElement('html');document.createTextNode=text=>{const n=document.createElement('span');n.textContent=text;return n;};
 for(const id of ['visibleSitesOptions','visibleSitesStatus','refreshVisibleSites','selectAllVisibleSites','saveVisibleSites'])document.createNode(id);
 const live=document.createNode('liveSite','select');for(const key of ['US','CO']){const n=document.createElement('option');n.value=key;live.append(n);}live.value='US';
 const calls=[],window={};let data=['US'];
 const fetch=async(url,init={})=>{calls.push({url,init});if(url==='/api/sites')return {ok:true,json:async()=>({data:{data:{publishSiteOptions:[{key:'US',value:'美国'},{key:'CO',value:'哥伦比亚'}]}}})};
  if(init.method==='POST')data=JSON.parse(init.body).sites;return {ok:true,json:async()=>({data:{sites:data}})};};
 class MutationObserver{observe(){}};class Event{constructor(type){this.type=type;}}
 live.dispatchEvent=()=>{};
 vm.runInNewContext(source,{window,document,fetch,MutationObserver,Event,Set});await window.SitePreferences.ready;
 assert.equal(window.SitePreferences.allowed('CO'),false);assert.equal(live.options[1].disabled,true);
 await window.SitePreferences.open();const checks=document.getElementById('visibleSitesOptions').querySelectorAll('input');
 assert.deepEqual(checks.map(x=>x.checked),[true,false]);checks[1].checked=true;
 await document.getElementById('saveVisibleSites').dispatch('click');
 assert.equal(window.SitePreferences.allowed('CO'),true);assert.equal(live.options[1].disabled,false);
 assert.deepEqual(data,['US','CO']);assert.equal(calls.filter(c=>c.init.method==='POST').length,1);
});
