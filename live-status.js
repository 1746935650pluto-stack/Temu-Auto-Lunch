/* One live local status stream per visible task centre; polling is the fallback. */
(() => {
  'use strict';
  let enabled=false, source=null, connected=false, lastMessage=0, retry=null, failures=0;
  const eligible=()=>enabled && !document.hidden && document.getElementById('tasks')?.classList.contains('active');
  const isLive=()=>connected && Date.now()-lastMessage<20000;
  function disconnect() {
    const old=source;source=null;connected=false;
    if(old){old.onerror=null;old.close();}
  }
  function reconcile() {
    if(!eligible()){if(retry!==null){clearTimeout(retry);retry=null;}disconnect();return;}
    if(source || retry!==null || typeof EventSource!=='function')return;
    const current=source=new EventSource('/api/automation/shared/events');
    current.addEventListener('activity',event=>{
      if(source!==current)return;
      try{
        const payload=JSON.parse(event.data);
        if(payload.error || !payload.data)throw Error('状态暂未就绪');
        if(window.sharedAutomation?.consumeActivity(payload.data)===false)return;
        lastMessage=Date.now();connected=payload.data._snapshot?.status!=='stale';failures=0;
      }catch{current.onerror();}
    });
    current.onerror=()=>{
      if(source!==current)return;
      disconnect();
      if(eligible())retry=setTimeout(()=>{retry=null;reconcile();},Math.min(30000,5000*++failures));
    };
  }
  window.WorkbenchLiveStatus={isLive,reconcile,configure:capabilities=>{enabled=capabilities?.activityEvents===true;reconcile();}};
  document.addEventListener('visibilitychange',reconcile);
  document.addEventListener('DOMContentLoaded',()=>{
    const tasks=document.getElementById('tasks');
    if(tasks && typeof MutationObserver==='function')new MutationObserver(reconcile).observe(tasks,{attributes:true,attributeFilter:['class']});
  });
  window.addEventListener('pagehide',()=>{enabled=false;disconnect();if(retry!==null)clearTimeout(retry);});
})();
