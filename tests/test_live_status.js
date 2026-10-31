const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
function setup(){
  const listeners={},streams=[],timers=new Map(),received=[],tasks={classList:{contains:()=>active}};
  let active=true,next=0;
  const document={hidden:false,getElementById:()=>tasks,addEventListener:(name,fn)=>listeners[name]=fn};
  const window={sharedAutomation:{consumeActivity:payload=>{if(typeof payload.paused!=='boolean')throw Error('invalid');received.push(payload);}},addEventListener:(name,fn)=>listeners[name]=fn};
  class EventSource{constructor(url){this.url=url;this.events={};streams.push(this);}addEventListener(name,fn){this.events[name]=fn;}close(){this.closed=true;}}
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'..','live-status.js'),'utf8'),{window,document,EventSource,Date,JSON,Error,
    setTimeout:fn=>{timers.set(++next,fn);return next;},clearTimeout:id=>timers.delete(id)});
  return {status:window.WorkbenchLiveStatus,document,listeners,streams,timers,received,active:value=>{active=value;}};
}
test('a visible task centre has one live stream and can fall back when disconnected',()=>{
  const {status,streams,received,timers}=setup();status.configure({activityEvents:true});status.reconcile();assert.equal(streams.length,1);assert.equal(status.isLive(),false);
  streams[0].events.activity({data:JSON.stringify({data:{paused:false}})});assert.equal(status.isLive(),true);assert.equal(received.length,1);
  streams[0].onerror();assert.equal(status.isLive(),false);assert.equal(streams[0].closed,true);assert.equal(timers.size,1);
});
test('hidden or departed pages close streams; old messages cannot overwrite the active view',()=>{
  const {status,streams,document,listeners,received}=setup();status.configure({activityEvents:true});const old=streams[0];
  document.hidden=true;listeners.visibilitychange();assert.equal(old.closed,true);assert.equal(status.isLive(),false);
  old.events.activity({data:JSON.stringify({data:{paused:true}})});assert.equal(received.length,0);
  document.hidden=false;listeners.visibilitychange();assert.equal(streams.length,2);
});
test('missing capability and malformed events keep polling available',()=>{
  const {status,streams,received}=setup();status.configure({});assert.equal(streams.length,0);
  status.configure({activityEvents:true});streams[0].events.activity({data:'{}'});assert.equal(status.isLive(),false);assert.equal(received.length,0);
});
