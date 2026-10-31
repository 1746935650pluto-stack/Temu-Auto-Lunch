/* Bounded display-only snapshots. Task identities and actions always come from the backend. */
(() => {
  'use strict';
  const DB='temu-display-cache', STORE='views', MAX_VIEWS=20, MAX_BYTES=8*1024*1024, TTL=24*60*60*1000;
  let opening=null;
  const text=(value,max)=>String(value ?? '').slice(0,max);
  function cleanRow(row) {
    let thumbnail='';
    try { const url=new URL(row.thumbnail);if(['http:','https:'].includes(url.protocol))thumbnail=text(url.href,2048); } catch {}
    return {title:text(row.title || '未命名商品',500),thumbnail,detailId:text(row.detailId,30),accountName:text(row.accountName,100)};
  }
  function open() {
    if(!window.indexedDB)return Promise.resolve(null);
    if(opening)return opening;
    opening=new Promise(resolve=>{
      let settled=false;
      const finish=value=>{if(!settled){settled=true;resolve(value);}};
      const timer=setTimeout(()=>finish(null),1500);
      const request=indexedDB.open(DB,1);
      request.onupgradeneeded=()=>{if(!request.result.objectStoreNames.contains(STORE))request.result.createObjectStore(STORE,{keyPath:'key'});};
      request.onsuccess=()=>{clearTimeout(timer);const db=request.result;db.onversionchange=()=>{db.close();opening=null;};if(settled)db.close();else finish(db);};
      request.onerror=request.onblocked=()=>{clearTimeout(timer);finish(null);};
    });
    return opening;
  }
  function key(namespace,query){return /^[a-f0-9]{24}$/.test(namespace || '') && typeof query==='string' && query.length<=2000 ? namespace+'|'+query : null;}
  async function get(namespace,query) {
    try {
      const id=key(namespace,query),db=id && await open();if(!db)return null;
      const value=await new Promise(resolve=>{const request=db.transaction(STORE).objectStore(STORE).get(id);request.onsuccess=()=>resolve(request.result);request.onerror=()=>resolve(null);});
      if(!value || value.format!==1 || !Array.isArray(value.rows) || value.rows.length>500 ||
          !Number.isFinite(value.savedAt) || Date.now()-value.savedAt>TTL || value.savedAt>Date.now()+60000)return null;
      return {...value,rows:value.rows.map(cleanRow)};
    }catch{return null;}
  }
  async function put(namespace,query,rows) {
    try {
      const id=key(namespace,query),db=id && await open();if(!db || !Array.isArray(rows))return;
      const value={key:id,format:1,savedAt:Date.now(),rows:rows.slice(0,500).map(cleanRow)};
      value.bytes=JSON.stringify(value).length*2;
      await new Promise(resolve=>{
        const transaction=db.transaction(STORE,'readwrite'),store=transaction.objectStore(STORE);
        store.put(value);
        const all=store.getAll();
        all.onsuccess=()=>{
          const records=all.result.sort((a,b)=>b.savedAt-a.savedAt);let bytes=0,count=0;
          for(const record of records){const size=Number(record.bytes)||MAX_BYTES;
            if(count>=MAX_VIEWS || bytes+size>MAX_BYTES || Date.now()-record.savedAt>TTL)store.delete(record.key);
            else{bytes+=size;count++;}}
        };
        transaction.oncomplete=transaction.onerror=transaction.onabort=()=>resolve();
      });
    }catch{/* Cache failure never blocks a live list or a user action. */}
  }
  function clearPreview(holder){holder?.cachePreviewNode?.remove();if(holder)holder.cachePreviewNode=null;}
  async function preview(namespace,query,holder,stillCurrent) {
    if(!holder)return false;
    const saved=await get(namespace,query);
    if(!saved || !saved.rows.length || !stillCurrent())return false;
    clearPreview(holder);
    const node=document.createElement('div');node.className='local-cache-preview';
    const note=document.createElement('p');note.className='local-cache-note';
    note.textContent=`本机缓存 · 北京时间 ${new Date(saved.savedAt).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})} · 仅供预览，核对最新数据后才能操作`;
    node.append(note);
    for(const row of saved.rows.slice(0,25)){
      const line=document.createElement('article');line.className='local-cache-row';
      if(row.thumbnail){const image=document.createElement('img');image.src=row.thumbnail;image.alt='';image.loading='lazy';image.decoding='async';line.append(image);}
      const body=document.createElement('div'),title=document.createElement('strong'),caption=document.createElement('p');
      title.textContent=row.title;caption.textContent=`${row.accountName} · 商品 ID ${row.detailId}`;
      body.append(title,caption);line.append(body);node.append(line);
    }
    holder.cachePreviewNode=node;holder.prepend(node);return true;
  }
  async function clear(){try{const db=await open();if(db)await new Promise(resolve=>{const tx=db.transaction(STORE,'readwrite');tx.objectStore(STORE).clear();tx.oncomplete=tx.onerror=tx.onabort=resolve;});}catch{}}
  async function stats(){try{const db=await open();if(!db)return null;return await new Promise(resolve=>{const request=db.transaction(STORE).objectStore(STORE).getAll();request.onsuccess=()=>resolve({views:request.result.length,bytes:request.result.reduce((n,r)=>n+(Number(r.bytes)||0),0)});request.onerror=()=>resolve(null);});}catch{return null;}}
  window.LocalViewCache={get,put,preview,clearPreview,clear,stats,cleanRow,limits:{views:MAX_VIEWS,bytes:MAX_BYTES,ttl:TTL}};
})();
