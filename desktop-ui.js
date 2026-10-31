(() => {
  'use strict';
  const pages=new Set(['workbench','automation','tasks','settings','pricing','zhihou','orders']);
  document.addEventListener('DOMContentLoaded',()=>{
    async function cacheStatus(){const status=document.getElementById('localDisplayCacheStatus'),stats=await window.LocalViewCache?.stats();if(status)status.textContent=stats?`当前显示缓存：${stats.views} 页 · ${(stats.bytes/1024/1024).toFixed(2)} MB。清理仅影响显示缓存，商品、任务和 API 配置保留。`:'当前环境未启用显示缓存，实时列表可正常使用。';}
    document.getElementById('clearLocalDisplayCache')?.addEventListener('click',async()=>{await window.LocalViewCache?.clear();await cacheStatus();});
    void cacheStatus();
    const page=new URLSearchParams(location.search).get('page');
    if(pages.has(page))document.querySelector(`.nav-item[data-page="${page}"]`)?.click();
    if(!window.temuDesktop)return;
    document.body.classList.add('desktop-client');
    const fallback=document.getElementById('pricingWorkbenchFallback');
    if(fallback){
      fallback.replaceChildren();
      const title=document.createElement('strong'),note=document.createElement('p'),button=document.createElement('button');
      title.textContent='核价工作台与 Chrome 扩展联动';
      note.textContent='点击后在已安装扩展的 Chrome 中打开，继续使用原有核价配置与 1688 图搜。';
      button.type='button';button.className='button primary';button.textContent='在 Chrome 中打开核价工作台';
      button.addEventListener('click',()=>window.temuDesktop.openBrowser('pricing'));
      fallback.append(title,note,button);
    }
    const form=document.getElementById('pricingConfigForm');
    if(form){
      form.querySelectorAll('input,textarea,select,button').forEach(node=>node.disabled=true);
      const button=document.createElement('button');button.type='button';button.className='button primary';button.textContent='在 Chrome 中配置核价工作台';
      button.addEventListener('click',()=>window.temuDesktop.openBrowser('settings'));form.after(button);
    }
  });
})();
