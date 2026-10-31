const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const {createDocument}=require('./dom_fixture');
function setup(){
  const records=new Map(),document=createDocument();
  let created=false;
  const db={objectStoreNames:{contains:()=>created},createObjectStore(){created=true;},close(){},transaction(){
    const tx={pending:0};
    function request(action){const result={};tx.pending++;queueMicrotask(()=>{result.result=action();result.onsuccess?.();if(--tx.pending===0)queueMicrotask(()=>{if(!tx.pending)tx.oncomplete?.();});});return result;}
    tx.objectStore=()=>({get:key=>request(()=>records.get(key)),getAll:()=>request(()=>[...records.values()]),
      put:value=>request(()=>{records.set(value.key,structuredClone(value));}),delete:key=>request(()=>records.delete(key)),clear:()=>request(()=>records.clear())});return tx;
  }};
  const indexedDB={open(){const request={};queueMicrotask(()=>{request.result=db;if(!created)request.onupgradeneeded?.();request.onsuccess?.();});return request;}};
  const window={indexedDB};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'..','local-view-cache.js'),'utf8'),{window,indexedDB,document,URL,Date,setTimeout,clearTimeout});
  return {cache:window.LocalViewCache,records,document};
}
const namespace='a'.repeat(24);
test('only display fields persist; task state, credentials and actions never enter the cache',async()=>{
  const {cache,records}=setup();
  await cache.put(namespace,'jobs?page=1',[{title:'item',thumbnail:'https://example.com/a.jpg',detailId:123,accountName:'NL',apiKey:'SYNTHETIC',item:{secret:'SYNTHETIC'},status:'ready_publish',available:true}]);
  const saved=[...records.values()][0];assert.deepEqual(Object.keys(saved.rows[0]).sort(),['accountName','detailId','thumbnail','title']);assert.equal(JSON.stringify(saved).includes('SYNTHETIC'),false);
  const loaded=await cache.get(namespace,'jobs?page=1');assert.equal(loaded.rows[0].title,'item');assert.equal(await cache.get('b'.repeat(24),'jobs?page=1'),null);
});
test('cached cards are plain readonly previews and late cache reads cannot replace live results',async()=>{
  const {cache,document}=setup(),holder=document.createElement('div');
  await cache.put(namespace,'pool?page=1',[{title:'<script>unsafe</script>',detailId:123,thumbnail:'javascript:alert(1)',accountName:'NL'}]);
  assert.equal(await cache.preview(namespace,'pool?page=1',holder,()=>false),false);assert.equal(holder.children.length,0);
  assert.equal(await cache.preview(namespace,'pool?page=1',holder,()=>true),true);
  assert.equal(holder.querySelectorAll('input,button').length,0);assert.equal(holder.querySelectorAll('img').length,0);
  assert.match(holder.textContent,/仅供预览/);assert.match(holder.textContent,/<script>unsafe/);
  cache.clearPreview(holder);assert.equal(holder.children.length,0);
});
test('cache evicts old views, enforces its byte budget and expires old data',async()=>{
  const {cache,records}=setup();
  for(let n=0;n<23;n++)await cache.put(namespace,'pool?page='+n,[{title:'view'+n}]);
  assert.equal(records.size,20);
  const rows=Array.from({length:510},()=>({title:'x'.repeat(500),thumbnail:'https://example.com/'+ 'x'.repeat(1950)}));
  for(let n=0;n<8;n++)await cache.put(namespace,'jobs?large='+n,rows);
  assert.ok([...records.values()].reduce((sum,r)=>sum+r.bytes,0)<=cache.limits.bytes);
  assert.ok([...records.values()].every(r=>r.rows.length<=500));
  const entry=[...records.values()][0];entry.savedAt=Date.now()-cache.limits.ttl-100;
  assert.equal(await cache.get(namespace,entry.key.split('|')[1]),null);
  await cache.clear();assert.equal(records.size,0);
});
