// ui/workflows2d.js — workflows on the map: the WF store and its poll (CM.onTasks), label chips, ribbons and gates in focus or
// selection, the route strip, the card, radio, the finish band, replay, shows (CM.action, CM.dockOverride,
// CM.drawWorkflows2D), window.__wfAudit.
// Inlined into the page's main script right after the CM registry (render-game.py, the seams): declare functions and
// assign into CM only, nothing that runs page code at load (start-up goes in CM.onInit). Real backslashes here: this
// file is read as it is, not through a Python string. Neutral words only.
//
// What the owner sees:
// - overview: a running workflow is its agent docked on the working planet's ring and a chip after that planet's name
//   ("Fix the phone labels · 2 of 4", CM.labelChip, placed by the label module); a planet where one finished in the last
//   24 h wears a medal. No ribbon, no gate, no show (the overview stays literal).
// - focus (a planet clicked, CM.focusUid): the gates of the steps at that planet, labelled; the shows anchored there.
// - a selected workflow (CM.selectWorkflow: the rail, the strip, Follow, a Today row): the explicit presentation view.
//   Its route as curved ribbons (done solid, working dashes moving 40 px/s with chevrons, waiting 1 px grey dashes, a
//   planned one a 40% ghost), a 14 px hex gate per step on the planet's limb, numbered step tags, the planets off the
//   route dimmed, the route strip in the status bar (CM.stripSet('route')) and the workflow card in the rail
//   (CM.railCard; the phone sheet through it). Until the chrome module is in, a small fallback (#wfcard, #wfstrip)
//   stands in for both, used only while CM.chromeReady is not set.
// Data: CM.onTasks keeps the /tasks reply's workflows and shows; while one works and the page is visible, /workflows?since=
// adds the changes every second (two on a phone). Every change of a step calls in on the radio (at most one line every
// 3 s per workflow), moves the agent (2 s), and a finish raises the banner (missionComplete) with a receipt of facts.
// All agent text reaches the page through textContent or the canvas, never as markup.

// ---------- colours (status colours never change with the skin; a skin's "route2d" block recolours the route marks) ----------
const WF_COL={done:'#4ADE80',working:'#6FB7FF',needs_you:'#FFB547',failed:'#FF5A5F',blocked:'#FF5A5F',waiting:'#9AA8BC',ready:'#9AA8BC',
  skipped:'#6B7684',planned:'#9AA8BC',stale:'#FFB547',stopped:'#9AA8BC'};
const WF_GREY='#6B7684', WF_FONT='"Barlow Condensed","Arial Narrow",sans-serif';
const WF_RUNNING=['working','needs_you','stale'], WF_FINISHED=['done','stopped','failed'];
const WF_TEMPLATES=[      // the three built-ins of workflows.TEMPLATES (labels and words; test_workflows_map pins them to the engine's)
  {name:'fix-and-check',label:'Fix and check',desc:'an agent does it in its own safe copy, another model family checks it, then you look at the changes',steps:2},
  {name:'review-folder',label:'Review this folder',desc:'an agent reads the folder and writes REVIEW.md, changing nothing else',steps:2},
  {name:'two-opinions',label:'Two opinions',desc:'two agents of different model families do the same thing side by side; you pick one',steps:3}];
function wfSkinCol(k){ const r=(typeof SKIN!=='undefined'&&SKIN&&typeof SKIN.route2d==='object')?SKIN.route2d:null, v=r&&r[k];
  return typeof v==='string'&&/^#[0-9a-fA-F]{6}$/.test(v)?v:null; }
function wfCol(st){ return WF_COL[st]||WF_COL.waiting; }
// the route's own colours: a private skin keeps its route colours (done, working, failed); amber and grey stay the page's
function wfRouteCol(st){ const k=st==='blocked'?'failed':st; return wfSkinCol(k)||(st==='waiting'||st==='ready'||st==='skipped'||st==='planned'?WF_GREY:wfCol(st)); }
function wfRgb(h){ h=String(h||'#000000'); return [1,3,5].map(i=>parseInt(h.slice(i,i+2),16)||0); }
function wfRgba(h,a){ const c=wfRgb(h); return 'rgba('+c[0]+','+c[1]+','+c[2]+','+a+')'; }
function wfMix(h,k,t){ const a=wfRgb(h), b=wfRgb(k); return 'rgb('+a.map((v,i)=>Math.round(v+(b[i]-v)*t)).join(',')+')'; }

// ---------- the store ----------
const WF={live:null,liveAt:0,tasksAt:0,showsAt:Date.now(),seen:new Map(),log:new Map(),selAt:0,
  poll:{timer:0,since:null,n:0,busy:false,on:false,err:''},
  radio:{last:new Map(),pend:new Map(),timers:new Map(),lines:[]},band:null,replay:null,crawl:new Map(),points:[],
  frame:{mode:'overview',ribbons:[],gates:[],tags:[],chips:{},medals:{},shows:{drawn:0,cards:0,charts:0,items:[]},dimmed:[],agents:[]},
  overview:{ribbons:0,gates:0,shows:0},card:{el:null,host:null,id:null,sig:'',phone:false},strip:{el:null,sig:'',on:false},
  view:{last:'',toasts:0},detail:new Map(),thumbs:new Map(),ui:false,caption:null,errLogged:false,fxSeen:new Map(),radioShows:new Set()};
function wfSteps(w){ return (w&&Array.isArray(w.steps)?w.steps:[]).filter(s=>s&&typeof s==='object'); }
function wfById(id){ return id?CM.wfList().find(w=>w&&w.id===id)||null:null; }
function wfSunUid(){ return (typeof BOARD!=='undefined'&&BOARD&&BOARD.uid)||'world:quest-board'; }
// where a step works: its planet, a named place ("place:docs site"), or this machine (the sun)
function wfAnchor(st){
  if(st&&typeof st.planet_uid==='string'&&st.planet_uid) return st.planet_uid;
  if(st&&typeof st.place==='string'&&st.place) return 'place:'+st.place;
  return wfSunUid(); }
function wfRunning(w){ return !!w&&WF_RUNNING.includes(w.state); }
// the step at work now: the current one (working or waiting on the user), else the first working, needing, failed one
function wfCurrent(w){
  const steps=wfSteps(w), cur=Array.isArray(w&&w.current)?w.current:[];
  return steps.find(x=>cur.includes(x.key)&&(x.state==='working'||x.state==='needs_you'))||steps.find(x=>cur.includes(x.key))
    ||steps.find(x=>x.state==='working')||steps.find(x=>x.state==='needs_you')||steps.find(x=>x.state==='failed')||null; }
// consecutive steps at one place make one stop of the route (repeats merged)
function wfStops(w){
  const out=[]; wfSteps(w).forEach((s,i)=>{ const uid=wfAnchor(s), last=out[out.length-1];
    if(last&&last.uid===uid) last.steps.push({s:s,i:i}); else out.push({uid:uid,steps:[{s:s,i:i}]}); });
  return out; }
// the state a stop's leg is drawn in: the work there now, else done when its first step is
function wfStopState(stop){
  const S=stop.steps.map(x=>x.s.state);
  if(S.includes('working')) return 'working'; if(S.includes('needs_you')||S.includes('stale')) return 'needs_you';
  if(S.includes('failed')||S.includes('blocked')) return 'failed'; if(S[0]==='done') return 'done'; return 'waiting'; }
// a label of at most n characters for a slot (the full words on hover): articles go first, then whole words with "…"
function wfShort(s,n){
  n=n||14; s=String(s==null?'':s).replace(/\s+/g,' ').trim(); if(s.length<=n) return s;
  const t=s.replace(/\b(the|a|an)\s+/gi,'').trim(); if(t.length<=n) return t;
  const w=t.split(' '), prep=/^(at|in|on|to|for|of|up|by|with|into)$/i.test(w[1]||'')?' '+w[1]:'', vl=w.length>2?w[0]+prep+' '+w[w.length-1]:'';
  if(vl&&vl.length<=n) return vl;
  let out=''; for(const x of w){ const nx=out?out+' '+x:x; if(nx.length>n-1) break; out=nx; }
  return (out||t.slice(0,n-1))+'…'; }
function wfTitle(w,n){ const t=String((w&&w.title)||'a workflow'); return n&&t.length>n&&CM.shortTitle?CM.shortTitle(t,n):t; }
function wfBy(w){ const b=w&&w.by&&typeof w.by==='object'?w.by:{}; return String(b.name||'an agent'); }
function wfPlaceName(st){ if(!st) return ''; if(st.place) return String(st.place); const u=st.planet_uid; return u?String(CM.labelText(u)):'this machine'; }
// a step's state in a few words (tags, the strip, the card)
function wfStepWord(s,where){
  if(!s) return '';
  if(s.state==='working'&&s.progress&&s.progress.total>0) return s.progress.done+' of '+s.progress.total;
  if(s.state==='working'&&s.task_status==='checking') return 'second opinion';
  if(s.state==='needs_you') return s.kind==='gate'?'you approve':String(s.why||CM.stateWord('needs_you'));
  if(s.state==='waiting'&&s.kind==='gate') return where==='strip'?'you, later':where==='card'?CM.stateWord('waiting'):'you approve';
  if(s.state==='failed') return s.why?'failed: '+wfShort(s.why,where==='strip'?12:28):CM.stateWord('failed');
  return CM.stateWord(s.state)||String(s.state||'');
}
function wfGateKind(st){ return st==='done'?'done':st==='working'?'working':(st==='needs_you'||st==='stale')?'needs':(st==='failed'||st==='blocked')?'failed':'waiting'; }
function wfProg(s){ return s&&s.progress&&s.progress.total>0?Math.max(0,Math.min(1,s.progress.done/s.progress.total)):null; }

// ---------- the CM data functions (same contract as the seam defaults; this module's store behind them) ----------
// the /tasks reply's list is the live one; a /state reply (applyWorld) brings the 15 s older render's, which must not
// win over it, so the list the page last had from /tasks or /workflows stands once there is one
function wfRaw(){ const L=WF.live||W.workflows; return Array.isArray(L)?L:[]; }
// during a replay the replayed workflow is its folded state, so every reader (the 2D map, Today, the 3D view) plays it
CM.wfList=()=>{ const L=wfRaw(), R=WF.replay; return R&&R.view?L.map(w=>w&&w.id===R.id?R.view:w):L; };
CM.showList=()=>Array.isArray(W.shows)?W.shows:[];
CM.selectedWorkflow=()=>CM._wfSel||null;
CM.onTasks=function(j){
  if(!j||typeof j!=='object') return;
  // W.counts is not set here: CM.takeSnapshot (applyTasks, right after) moves the sky and its counts together, by revision
  if(Array.isArray(j.shows)){ W.shows=j.shows; WF.showsAt=Date.now(); }
  if(Array.isArray(j.workflows)){ W.workflows=j.workflows; WF.live=j.workflows; WF.liveAt=Date.now(); WF.tasksAt=Date.now(); wfIngest(); }
  wfPollArm();
};
CM.selectWorkflow=function(id){
  id=typeof id==='string'&&id?id:null;
  const was=CM._wfSel||null; CM._wfSel=id; WF.selAt=Date.now();
  if(id!==was){ if(WF.replay&&WF.replay.id!==id) wfReplayStop(); WF.selT0=performance.now(); }
  cmSceneTouch();
  try{ wfCardSync(true); wfStripSync(); wfCrumbSync(); }catch(e){ wfErr(e); }
  if(id&&id!==was) wfDetailFetch(id,true);
};
CM.workingPlanets=()=>{ const s=new Set();
  CM.wfList().forEach(w=>wfSteps(w).forEach(st=>{ if(st.state==='working'&&typeof st.planet_uid==='string'&&st.planet_uid) s.add(st.planet_uid); }));
  const areas=MOONS.map(m=>({uid:m.uid,path:String((unitByUid(m.uid)||{}).path||'')})).filter(a=>a.path).sort((a,b)=>b.path.length-a.path.length);
  agentTasks().forEach(t=>{ if(!t||!TASK_WORKING.includes(t.status)) return; const p=String(t.target||''), a=p&&areas.find(x=>p===x.path||p.startsWith(x.path+'/')); if(a) s.add(a.uid); });
  return s; };
// one plain row per workflow for Today and the Agents tab: the seed's eight keys first, then what a row draws beyond
// them: stops (at most 7 around the step at work: label, short label, state, word, place, planet), the step's progress,
// night (the night run, a derived workflow: its row offers Replay), by and updated
CM.wfRows=()=>CM.wfList().filter(w=>w&&typeof w==='object').map(w=>{ const steps=wfSteps(w), cur=Array.isArray(w.current)?w.current:[];
  const at=steps.find(x=>cur.includes(x.key))||steps.find(x=>x.state==='working'||x.state==='needs_you')||steps.slice().reverse().find(x=>x.state==='done')||steps[0]||null;
  return {id:String(w.id||''), title:String(w.title||''), done:Number.isInteger(w.done)?w.done:steps.filter(x=>x.state==='done').length,
    total:Number.isInteger(w.total)?w.total:steps.length, state:String(w.state||''),
    place:at?String(at.place||(at.planet_uid?CM.labelText(at.planet_uid):'')):'', sentence:String(w.sentence||''), step:at?steps.indexOf(at)+1:0,
    stops:wfWindow(w).map(x=>({key:String(x.s.key||''),label:String(x.s.label||''),short:wfShort(x.s.label),state:String(x.s.state||''),
      word:wfStepWord(x.s,'strip'),place:wfPlaceName(x.s),uid:x.s.planet_uid||null})),
    progress:at&&at.progress&&at.progress.total>0?{done:at.progress.done,total:at.progress.total}:null,
    night:w.derived==='night', by:wfBy(w), updated:String(w.updated||'')}; });
// the chip after a planet's name while a workflow works there: "Fix the phone labels · 2 of 4" (the label module places it)
CM.labelChip=uid=>{ const c=wfChipOf(uid); return c?c.text:''; };
function wfChipOf(uid){
  if(!uid) return null; let best=null;
  CM.wfList().forEach(w=>{ if(!wfRunning(w)) return; const st=wfCurrent(w); if(!st||wfAnchor(st)!==uid) return;
    const steps=wfSteps(w), n=steps.indexOf(st)+1, rank=w.state==='needs_you'?0:w.state==='stale'?1:2;
    if(!best||rank<best.rank) best={rank:rank,id:w.id,state:w.state,text:wfTitle(w,26)+' · '+n+' of '+(Number.isInteger(w.total)?w.total:steps.length)}; });
  return best; }
// at most 7 steps around the one at work (the route strip, the rows' stops)
function wfWindow(w){
  const steps=wfSteps(w).map((s,i)=>({s:s,i:i})); if(steps.length<=7) return steps;
  const cur=wfCurrent(w), k=cur?steps.findIndex(x=>x.s===cur):0, a=Math.max(0,Math.min(steps.length-7,k-3));
  return steps.slice(a,a+7); }
// a planet where a workflow finished in the last 24 h wears a medal (planet state, not a route: the overview stays literal)
function wfMedals(){
  const out={}, now=Date.now();
  CM.wfList().forEach(w=>{ if(!w||w.state!=='done'||w.derived) return; const t=Date.parse(String(w.updated||''));
    if(!Number.isFinite(t)||now-t>86400000) return; const steps=wfSteps(w), last=steps[steps.length-1]; if(!last) return;
    const uid=wfAnchor(last); if(String(uid).startsWith('area:')) out[uid]=true; });
  return out; }

// ---------- change: what moved since the last list (radio, haptic, the agent's crawl, the finish) ----------
function wfSnap(w){ const st=wfCurrent(w);
  return {sig:JSON.stringify([w.state,wfSteps(w).map(s=>[s.key,s.state,s.says||'',s.progress?[s.progress.done,s.progress.total]:null])]),
    state:String(w.state||''),steps:Object.fromEntries(wfSteps(w).map(s=>[s.key,String(s.state||'')])),at:wfRunning(w)&&st?wfAnchor(st):null}; }
function wfIngest(){
  const ids=new Set();
  wfRaw().forEach(w=>{ if(!w||typeof w.id!=='string') return; ids.add(w.id);
    const prev=WF.seen.get(w.id), cur=wfSnap(w); WF.seen.set(w.id,cur);
    if(prev&&prev.sig!==cur.sig) try{ wfChanged(w,prev,cur); }catch(e){ wfErr(e); } });
  Array.from(WF.seen.keys()).forEach(id=>{ if(!ids.has(id)) WF.seen.delete(id); });
  try{ wfCardSync(false); wfStripSync(); }catch(e){ wfErr(e); }
}
function wfLog(id,text,st){ const L=WF.log.get(id)||[]; L.unshift({t:Date.now(),text:String(text),state:st||''}); WF.log.set(id,L.slice(0,8)); }
function wfChanged(w,prev,cur){
  const steps=wfSteps(w), moved=steps.filter(s=>prev.steps[s.key]!==undefined&&prev.steps[s.key]!==s.state);
  moved.forEach(s=>wfLog(w.id,'Step '+(steps.indexOf(s)+1)+' '+String(s.label||s.key)+': '+wfStepWord(s),s.state));
  // the radio: the change that matters most, as one line
  const order=['failed','needs_you','done','working'], top=moved.slice().sort((a,b)=>(order.indexOf(a.state)-order.indexOf(b.state))||(steps.indexOf(b)-steps.indexOf(a)))[0];
  if(top&&order.includes(top.state)) wfRadio(w,'Step '+(steps.indexOf(top)+1)+' of '+steps.length+': '+String(top.label||top.key)+' · '+wfPlaceName(top),top.state);
  // a step that changed while its card or sheet is open: one vibration (the phone's CM.haptic: done 30 ms, failure longer)
  if(WF.card.id===w.id&&moved.length){ const k=moved.some(s=>s.state==='failed')?'fail':moved.some(s=>s.state==='done')?'done':null; if(k) try{ CM.haptic(k); }catch(e){} }
  // the working step went to another planet: its agent flies there in 2 s
  if(prev.at&&cur.at&&prev.at!==cur.at&&wfRunning(w)) wfCrawlStart(w.id,prev.at,cur.at);
  if(cur.state!==prev.state){
    if(cur.state==='done') wfFinish(w,'done');
    else if(cur.state==='failed') wfFinish(w,'failed');
  }
  if(WF.card.id===w.id) wfDetailFetch(w.id,false);
}
function wfRadio(w,line,st){ WF.radio.pend.set(w.id,{id:w.id,name:wfBy(w),line:line,state:st}); wfRadioFlush(w.id); }
// the newest held line goes out once 3 s have passed since this workflow's last one (checked again when the timer fires)
function wfRadioFlush(id){
  const c=WF.radio.pend.get(id); if(!c) return;
  const wait=3000-(Date.now()-(WF.radio.last.get(id)||0));
  if(wait>0){ if(!WF.radio.timers.has(id)) WF.radio.timers.set(id,setTimeout(()=>{ WF.radio.timers.delete(id); wfRadioFlush(id); },wait+5)); return; }
  WF.radio.pend.delete(id); const tm=WF.radio.timers.get(id); if(tm){ clearTimeout(tm); WF.radio.timers.delete(id); }
  wfRadioSay(c); }
function wfRadioSay(c){
  WF.radio.last.set(c.id,Date.now()); WF.radio.lines.unshift({id:c.id,text:c.line,state:c.state,at:Date.now()}); WF.radio.lines.length=Math.min(WF.radio.lines.length,12);
  if(wfIsPhone()&&WF.card.id===c.id) return;
  try{ if(typeof radioCall==='function') radioCall({uid:'wf:'+c.id,name:c.name,word:CM.stateWord(c.state)||c.state,line:c.line,col:wfCol(c.state),
    carrier:false,mate:false,tag:String(c.name||'?').charAt(0).toUpperCase(),cue:c.state==='failed'||c.state==='needs_you'?'alert':'warp-in'}); }catch(e){ wfErr(e); } }

// ---------- the finish: the banner (missionComplete) and a receipt of facts that counts up (never points) ----------
function wfTallyFacts(w){
  const steps=wfSteps(w);
  return {steps:steps.filter(s=>s.state==='done').length,
    retries:steps.reduce((n,s)=>n+(s.kind==='task'&&Number.isInteger(s.attempt)&&s.attempt>1?s.attempt-1:0),0),
    checks:steps.filter(s=>s.check&&s.check.verdict==='PASS').length,
    elapsed_s:Number.isFinite(Number(w.elapsed_s))?Math.max(0,Math.round(Number(w.elapsed_s))):0}; }
function wfDur(s){ s=Math.max(0,Math.round(Number(s)||0)); if(s<60) return s+' s'; if(s<3600) return Math.round(s/60)+' min'; const h=Math.floor(s/3600), m=Math.round((s%3600)/60); return h+' h'+(m?' '+m+' min':''); }
function wfPl(n,one,many){ return n+' '+(n===1?one:many); }
function wfTally(w,t0){
  const f=wfTallyFacts(w), steps=wfSteps(w), checkers=Array.from(new Set(steps.map(s=>s.check&&s.check.runner).filter(Boolean).map(String)));
  const T={id:w.id,state:String(w.state||''),final:f,t0:t0==null?performance.now()-5000:t0,lines:['What ran','Time','Who','Checks'],
    text:wfPl(f.steps,'step','steps')+' · '+wfPl(f.retries,'retry','retries')+' · '+wfPl(f.checks,'check passed','checks passed')+' · '+wfDur(f.elapsed_s),
    who:wfBy(w)+(checkers.length?' · second opinion by '+checkers.join(', '):''), of:steps.length};
  T.toString=function(){ return this.text; };
  return T; }
// the count-up: each fact climbs at 30 a second or faster and lands within 0.9 s
function wfTallyNow(T){ const age=Math.max(0,(performance.now()-T.t0)/1000), out={};
  Object.keys(T.final).forEach(k=>{ const v=T.final[k]; out[k]=REDUCED?v:Math.min(v,Math.floor(age*Math.max(30,v/0.9))); }); return out; }
function wfTallyRows(T){ const c=wfTallyNow(T);
  return [['What ran',c.steps+' of '+T.of+' steps done'+(T.final.retries?' · '+wfPl(c.retries,'retry','retries'):' · no retries')],
    ['Time',wfDur(c.elapsed_s)],['Who',T.who],['Checks',T.final.checks?wfPl(c.checks,'second opinion passed','second opinions passed'):'no second opinion asked']]; }
function wfDoneWord(){ const w=typeof SKY_WORDS!=='undefined'&&SKY_WORDS&&SKY_WORDS.cleared; return w&&w!=='CLEARED'?w:'WORKFLOW DONE'; }
function wfFinish(w,state){
  WF.band={id:w.id,state:state,t0:performance.now(),until:Date.now()+12000,tally:wfTally(w,performance.now()),title:wfTitle(w)};
  if(state==='done'){ try{ if(typeof missionComplete==='function'&&missionComplete(wfTitle(w),wfDoneWord())) sfxCue('settle'); }catch(e){ wfErr(e); } }
  wfBandShow();
}

// ---------- the poll: /workflows?since= every second (two from a phone) while one works and the page shows ----------
function wfPollMs(){ return (typeof REMOTE!=='undefined'&&REMOTE)||MOB?2000:1000; }
// WF.poll.hold: a probe that pastes fixtures over a bridge that has none holds this poll (as it holds /tasks, TASKS_APPLIED)
function wfPollArm(){
  WF.poll.on=!WF.poll.hold&&!!(typeof LIVE!=='undefined'&&LIVE)&&wfRaw().some(w=>w&&(w.state==='working'||w.state==='stale'));
  if(WF.poll.on&&!WF.poll.timer) WF.poll.timer=setTimeout(wfPollTick,wfPollMs()); }
async function wfPollTick(){
  WF.poll.timer=0; if(!WF.poll.on) return;
  // a /tasks reply that just landed carried the whole list: this tick is not needed
  if(document.hidden) return;
  if(WF.poll.busy||Date.now()-WF.tasksAt<wfPollMs()-100){ wfPollArm(); return; }
  WF.poll.busy=true;
  try{ wfMerge(await api('/workflows'+(WF.poll.since?'?since='+encodeURIComponent(WF.poll.since):''))); WF.poll.err=''; }
  catch(e){ WF.poll.err=String((e&&e.message)||e); }
  finally{ WF.poll.busy=false; WF.poll.n++; wfPollArm(); } }
function wfMerge(j){
  if(!j||typeof j!=='object') return;
  if(typeof j.now==='string') WF.poll.since=j.now;
  let moved=false;
  if(Array.isArray(j.workflows)&&j.workflows.length){ const L=wfRaw().slice();
    j.workflows.forEach(w=>{ if(!w||typeof w.id!=='string') return; const i=L.findIndex(x=>x&&x.id===w.id); if(i>=0) L[i]=w; else L.push(w); });
    W.workflows=L; WF.live=L; moved=true; }
  if(Array.isArray(j.workflows)) WF.liveAt=Date.now();
  if(Array.isArray(j.shows)&&JSON.stringify(j.shows)!==JSON.stringify(CM.showList())){ W.shows=j.shows; WF.showsAt=Date.now(); moved=true; }
  if(moved){ cmSceneTouch(); wfIngest(); }
}
function wfMergeOne(w){ if(w&&typeof w.id==='string') wfMerge({workflows:[w]}); }

// ---------- where things are (world units, then screen pixels of the frame being drawn) ----------
function wfBody(uid){
  if(!uid) return null; uid=String(uid);
  let m=MOONS.find(x=>x.uid===uid)||MOONS.find(x=>(x.rooms||[]).some(r=>r&&r.uid===uid));
  if(m){ const p=VEC?project(m.x,m.y):{x:m.x,y:m.y}; return {uid:m.uid,x:p.x,y:p.y,r:m.r||30,kind:'planet'}; }
  if(uid===wfSunUid()) return {uid:uid,x:LY.star.x,y:LY.star.y,r:LY.star.r||60,kind:'sun'};
  if(uid.startsWith('place:')){   // a named place: a small beacon outside the planet ring, at an angle from its name
    const a=(seedOf(uid)%3600)/3600*Math.PI*2, R=(LY.rootOrbit||760)+340, p=VEC?project(LY.star.x+Math.cos(a)*R,LY.star.y+Math.sin(a)*R):{x:LY.star.x+Math.cos(a)*R,y:LY.star.y+Math.sin(a)*R};
    return {uid:uid,x:p.x,y:p.y,r:22,kind:'place',label:uid.slice(6)}; }
  const u=unitByUid(uid); if(u&&u.gx!=null) return {uid:uid,x:u.gx*TILE,y:u.gy*TILE,r:18,kind:'unit'};
  return null; }
function wfScr(p){ return {x:(p.x-camx)*ZOOM,y:(p.y-camy)*ZOOM,r:Math.max(5,(p.r||0)*ZOOM),uid:p.uid,kind:p.kind,label:p.label}; }
function wfIsPhone(){ return !!(typeof MOB!=='undefined'&&MOB); }
// the map's free box on screen (the fallback card and strip are walls; the chrome module's rail is beside the stage)
function wfFree(){ const fb=!CM.chromeReady&&!wfIsPhone();
  return {x0:8,y0:8,x1:VW-(typeof PANEL_W==='number'?PANEL_W:0)-8,y1:VH-(fb&&WF.strip.on?52:8)}; }
// the dock spot on a planet's working ring: the end of the arc that fills with the step's progress (ui/kinds.js draws the arc)
function wfDockSpot(uid,f){ const b=wfBody(uid); if(!b) return null; const s=wfScr(b), a=-Math.PI/2+(f==null?.25:Math.max(.08,f))*Math.PI*2, R=s.r+7;
  return {x:s.x+Math.cos(a)*R,y:s.y+Math.sin(a)*R,ang:a+Math.PI/2,s:s}; }
function wfCurve(a,b,bend){ const mx=(a[0]+b[0])/2, my=(a[1]+b[1])/2, dx=b[0]-a[0], dy=b[1]-a[1], qx=mx-dy*bend, qy=my+dx*bend, out=[];
  for(let i=0;i<=32;i++){ const t=i/32; out.push([(1-t)*(1-t)*a[0]+2*(1-t)*t*qx+t*t*b[0],(1-t)*(1-t)*a[1]+2*(1-t)*t*qy+t*t*b[1]]); } return out; }
function wfTrim(pts,r0,r1){ const a=pts[0], b=pts[pts.length-1]; return pts.filter(p=>Math.hypot(p[0]-a[0],p[1]-a[1])>r0&&Math.hypot(p[0]-b[0],p[1]-b[1])>r1); }
// the route of a workflow on this frame: its stops (consecutive steps at one place merged) and the curved legs between them,
// each bowed away from the sun and trimmed off the bodies
function wfRoute(w){
  const stops=wfStops(w).map(st=>{ const b=wfBody(st.uid); return b?Object.assign(st,{b:b,s:wfScr(b)}):null; }).filter(Boolean);
  const sun=wfScr({x:LY.star.x,y:LY.star.y,r:0}), segs=[];
  for(let i=1;i<stops.length;i++){ const A=stops[i-1].s, B=stops[i].s, len=Math.hypot(B.x-A.x,B.y-A.y); if(len<2) continue;
    const mx=(A.x+B.x)/2, my=(A.y+B.y)/2, nx=-(B.y-A.y)/len, ny=(B.x-A.x)/len;
    const out=Math.hypot(mx+nx*20-sun.x,my+ny*20-sun.y)>=Math.hypot(mx-nx*20-sun.x,my-ny*20-sun.y)?1:-1, bend=out*(.12+.06*(i%2));
    const full=wfCurve([A.x,A.y],[B.x,B.y],bend), pts=wfTrim(full,A.r+15,B.r+21);
    segs.push({from:stops[i-1],to:stops[i],pts:pts.length>3?pts:full,full:full,bend:bend,state:wfStopState(stops[i])}); }
  return {stops:stops,segs:segs}; }
// one gate per step on its planet's limb at r + 11, where the route meets the planet; several steps at one planet side by side
function wfGates(w,R,only){
  const out=[], steps=wfSteps(w);
  R.stops.forEach(st=>{ if(only&&st.uid!==only) return;
    const inc=R.segs.find(s=>s.to===st), og=R.segs.find(s=>s.from===st), P=inc?inc.pts[inc.pts.length-1]:og?og.pts[0]:null;
    let base=P?Math.atan2(P[1]-st.s.y,P[0]-st.s.x):-Math.PI/4; const rr=st.s.r+11, d=Math.min(.7,19/rr), n=st.steps.length;
    const wk=st.steps.find(x=>x.s.state==='working'||x.s.state==='needs_you');
    if(wk){ const aA=-Math.PI/2+Math.max(.08,wfProg(wk.s)==null?.25:wfProg(wk.s))*Math.PI*2, df=Math.atan2(Math.sin(base-aA),Math.cos(base-aA)), span=(n-1)/2*d+.5;
      if(Math.abs(df)<span) base=aA+(df>=0?1:-1)*span; }
    st.steps.forEach((x,k)=>{ const a=base+(k-(n-1)/2)*d;
      out.push({key:String(x.s.key),n:x.i+1,uid:st.uid,state:String(x.s.state||''),kind:wfGateKind(x.s.state),x:st.s.x+Math.cos(a)*rr,y:st.s.y+Math.sin(a)*rr,
        a:a,prog:wfProg(x.s),label:String(x.s.label||x.s.key),word:wfStepWord(x.s),s:x.s,st:st}); }); });
  return out; }

// ---------- painters (screen pixels) ----------
function wfText(g,s,x,y,o){ o=o||{}; g.save(); g.font=(o.w||600)+' '+(o.size||14)+'px '+WF_FONT; g.textAlign=o.align||'left'; g.textBaseline=o.base||'top';
  if(o.stroke!==false){ g.lineJoin='round'; g.lineWidth=o.sw||3; g.strokeStyle='#05070D'; g.strokeText(s,x,y); }
  g.fillStyle=o.col||'#E8EEF6'; g.fillText(s,x,y); const m=g.measureText(s).width; g.restore(); return m; }
function wfMeasure(g,s,size,w){ g.save(); g.font=(w||600)+' '+(size||14)+'px '+WF_FONT; const m=g.measureText(String(s)).width; g.restore(); return m; }
function wfRibbon(g,pts,st,o){
  o=o||{}; if(!pts||pts.length<2) return;
  const ghost=!!o.ghost, c=ghost?'#9AA8BC':wfRouteCol(st), quiet=ghost||st==='waiting'||st==='ready'||st==='skipped', path=()=>{ g.beginPath(); pts.forEach((p,i)=>i?g.lineTo(p[0],p[1]):g.moveTo(p[0],p[1])); };
  g.save(); g.lineCap='round'; g.lineJoin='round'; if(o.alpha!=null) g.globalAlpha=o.alpha;
  if(!quiet){ g.save(); g.strokeStyle=c; g.globalAlpha*=.16; g.lineWidth=9; path(); g.stroke(); g.restore(); }
  if(ghost){ g.globalAlpha*=.4; g.setLineDash([4,5]); g.lineWidth=1; g.strokeStyle=c; }
  else if(quiet){ g.setLineDash([3,5]); g.lineWidth=1; g.strokeStyle=WF_GREY; }
  else if(st==='working'){ g.setLineDash([12,6]); g.lineDashOffset=REDUCED?0:-((o.t||0)/1000*40)%18; g.lineWidth=3; g.strokeStyle=c; g.shadowColor=c; g.shadowBlur=8; }
  else if(st==='failed'){ g.setLineDash([6,4]); g.lineWidth=2.5; g.strokeStyle=c; g.shadowColor=c; g.shadowBlur=6; }
  else { g.lineWidth=3; g.strokeStyle=c; g.shadowColor=c; g.shadowBlur=6; }
  path(); g.stroke();
  if(st==='working'&&!ghost){ g.setLineDash([]); g.shadowBlur=0; g.fillStyle='rgba(214,236,255,.95)';       // chevrons: the way the work travels
    const off=REDUCED?0:Math.floor(((o.t||0)/1000*40)/18)%7;
    for(let i=4+off%7;i<pts.length-3;i+=7){ const a=pts[i], b=pts[i+1], an=Math.atan2(b[1]-a[1],b[0]-a[0]);
      g.save(); g.translate(a[0],a[1]); g.rotate(an); g.beginPath(); g.moveTo(3.5,0); g.lineTo(-2.5,-3.5); g.lineTo(-1,0); g.lineTo(-2.5,3.5); g.closePath(); g.fill(); g.restore(); } }
  const n=pts.length, a=pts[Math.max(0,n-4)], b=pts[n-1], ang=Math.atan2(b[1]-a[1],b[0]-a[0]);      // an arrowhead where the leg arrives
  g.setLineDash([]); g.shadowBlur=0; g.fillStyle=quiet?(ghost?'#9AA8BC':WF_GREY):c;
  g.translate(b[0],b[1]); g.rotate(ang); g.beginPath(); g.moveTo(2,0); g.lineTo(-7,-4.5); g.lineTo(-5,0); g.lineTo(-7,4.5); g.closePath(); g.fill();
  g.restore(); }
function wfHexPath(g,x,y,R){ g.beginPath(); for(let i=0;i<6;i++){ const a=Math.PI/6+i*Math.PI/3; if(i) g.lineTo(x+Math.cos(a)*R,y+Math.sin(a)*R); else g.moveTo(x+Math.cos(a)*R,y+Math.sin(a)*R); } g.closePath(); }
// a step gate: a 14 px hex ring. hollow grey waiting, a cyan progress arc working, amber "!" needs you, dashed red failed, filled ✓ done
function wfGate(g,x,y,kind,prog,R,num){
  R=R||7; const C={waiting:'#9AA8BC',working:wfRouteCol('working'),needs:WF_COL.needs_you,failed:wfRouteCol('failed'),done:wfRouteCol('done')}[kind]||'#9AA8BC';
  g.save(); g.fillStyle='rgba(5,7,13,.86)'; wfHexPath(g,x,y,R+2.5); g.fill();
  if(kind==='done'){ g.fillStyle=C; wfHexPath(g,x,y,R); g.fill(); g.strokeStyle='#05070D'; g.lineWidth=Math.max(1.6,R*.26); g.lineCap='round'; g.lineJoin='round';
    g.beginPath(); g.moveTo(x-R*.42,y); g.lineTo(x-R*.1,y+R*.35); g.lineTo(x+R*.45,y-R*.32); g.stroke(); }
  else if(kind==='working'){ g.strokeStyle=wfRgba(C,.35); g.lineWidth=2; wfHexPath(g,x,y,R); g.stroke();
    g.shadowColor=C; g.shadowBlur=8; g.strokeStyle=C; g.lineWidth=2.6; g.lineCap='round'; g.beginPath(); g.arc(x,y,R-.5,-Math.PI/2,-Math.PI/2+Math.max(.08,prog==null?.3:prog)*Math.PI*2); g.stroke(); }
  else if(kind==='needs'){ g.strokeStyle=C; g.lineWidth=2; wfHexPath(g,x,y,R); g.stroke(); }
  else if(kind==='failed'){ g.strokeStyle=C; g.lineWidth=2; g.setLineDash([4,3]); wfHexPath(g,x,y,R); g.stroke(); }
  else { g.strokeStyle=C; g.lineWidth=1.6; wfHexPath(g,x,y,R); g.stroke(); }
  g.restore();
  if(kind==='needs') wfText(g,'!',x,y-R+1,{size:Math.round(R*1.7),col:C,align:'center',stroke:false,w:700});
  else if(num!=null&&kind!=='done'&&kind!=='working') wfText(g,String(num),x,y-R*.95,{size:Math.max(12,Math.round(R*1.25)),col:kind==='waiting'?'#C9D3E1':C,align:'center',stroke:false});
}
// a tag: a chamfered plate in the state's colour, 2.5 px rule, 14 px words, a 45° elbow leader ending in a dot on the thing
function wfTagSize(g,s,size){ return {w:wfMeasure(g,s,size||14)+20,h:(size||14)+9}; }
function wfTag(g,s,x,y,col,lead,size){
  size=size||14; const S=wfTagSize(g,s,size), w=S.w, h=S.h, ch=6, c=wfRgb(col);
  if(lead){ const sx=lead[0], sy=lead[1], ex=x+(sx>x+w/2?w:0), ey=y+h/2, dx=ex-sx, dy=ey-sy, diag=Math.min(Math.abs(dy),Math.abs(dx))*Math.sign(dx||1);
    g.save(); g.strokeStyle=wfRgba(col,.75); g.lineWidth=1.2; g.beginPath(); g.moveTo(sx,sy); g.lineTo(sx+diag,ey); g.lineTo(ex,ey); g.stroke();
    g.fillStyle=col; g.beginPath(); g.arc(sx,sy,2.2,0,Math.PI*2); g.fill(); g.restore(); }
  g.save(); g.beginPath(); g.moveTo(x+ch,y); g.lineTo(x+w,y); g.lineTo(x+w,y+h-ch); g.lineTo(x+w-ch,y+h); g.lineTo(x,y+h); g.lineTo(x,y+ch); g.closePath();
  g.fillStyle='rgba('+Math.round(c[0]*.14*.45+6*.55)+','+Math.round(c[1]*.14*.45+9*.55)+','+Math.round(c[2]*.14*.45+16*.55)+',.94)'; g.fill();
  g.strokeStyle=wfRgba(col,.9); g.lineWidth=1; g.stroke(); g.fillStyle=col; g.fillRect(x+1,y+ch,2.5,h-ch-3); g.restore();
  wfText(g,s,x+10,y+4.5,{size:size,col:wfMix(col,'#FFFFFF',.62),stroke:false});
  return {x:x,y:y,w:w,h:h}; }
// the medal of a planet where a workflow finished today: gold disc with an embossed check on a two-tone ribbon (12×16 at s=.75)
function wfMedal(g,x,y,s){
  const disc=wfSkinCol('medal')||'#F2C14E', ribA=wfSkinCol('ribbon')||'#3FBF72', ribB=wfSkinCol('ribbon_dark')||'#2F9F5C';
  g.save(); g.translate(x,y); g.scale(s,s);
  g.fillStyle='rgba(0,0,0,.4)'; g.beginPath(); g.arc(.8,9,8.2,0,Math.PI*2); g.fill();
  [[-1,ribA],[1,ribB]].forEach(r=>{ const d=r[0]; g.fillStyle=r[1]; g.beginPath(); g.moveTo(d,-9); g.lineTo(d*7,-9); g.lineTo(d*4.5,2.5); g.lineTo(d*.2,2.5); g.closePath(); g.fill(); });
  g.fillStyle='#E8EEF6'; g.fillRect(-1.6,-9,3.2,11);
  const gr=g.createRadialGradient(-2.6,5.4,.5,0,8,8.4); gr.addColorStop(0,'#FFF6CC'); gr.addColorStop(.38,disc); gr.addColorStop(.8,'#B07C18'); gr.addColorStop(1,'#6A4208');
  g.fillStyle=gr; g.beginPath(); g.arc(0,8,7.4,0,Math.PI*2); g.fill();
  g.strokeStyle='rgba(255,236,170,.75)'; g.lineWidth=.9; g.beginPath(); g.arc(0,8,5.6,0,Math.PI*2); g.stroke();
  g.strokeStyle='#5A3606'; g.lineWidth=.8; g.beginPath(); g.arc(0,8,7.4,0,Math.PI*2); g.stroke();
  g.lineCap='round'; g.lineJoin='round'; g.strokeStyle='rgba(90,54,6,.8)'; g.lineWidth=1.7; g.beginPath(); g.moveTo(-2.6,8.5); g.lineTo(-.6,10.6); g.lineTo(3.2,6.2); g.stroke();
  g.strokeStyle='rgba(255,248,214,.95)'; g.lineWidth=1.1; g.beginPath(); g.moveTo(-2.6,8); g.lineTo(-.6,10.1); g.lineTo(3.2,5.7); g.stroke();
  g.restore(); }
// the workflow's agent: a friendly dart, white hull, cyan canopy and engine (it rides the working ring's fill end)
function wfCraft(g,x,y,L,ang,eng){
  g.save(); g.translate(x,y); g.rotate(ang); const ex=-L*.5, e=eng==null?1:eng;
  if(e>0){ g.save(); g.globalCompositeOperation='lighter'; let gr=g.createRadialGradient(ex,0,0,ex,0,L*.75*e);
    gr.addColorStop(0,'rgba(210,235,255,.95)'); gr.addColorStop(.18,'rgba(111,183,255,.75)'); gr.addColorStop(1,'rgba(111,183,255,0)');
    g.fillStyle=gr; g.beginPath(); g.arc(ex,0,L*.75*e,0,Math.PI*2); g.fill();
    gr=g.createLinearGradient(ex,0,ex-L*1.3*e,0); gr.addColorStop(0,'rgba(111,183,255,.55)'); gr.addColorStop(1,'rgba(111,183,255,0)');
    g.fillStyle=gr; g.beginPath(); g.moveTo(ex,-L*.07); g.lineTo(ex-L*1.3*e,0); g.lineTo(ex,L*.07); g.closePath(); g.fill(); g.restore(); }
  [-1,1].forEach(sg=>{ g.beginPath(); g.moveTo(L*.12,sg*L*.07); g.lineTo(-L*.3,sg*L*.52); g.lineTo(-L*.44,sg*L*.5); g.lineTo(-L*.34,sg*L*.1); g.closePath();
    const wg=g.createLinearGradient(0,0,0,sg*L*.5); wg.addColorStop(0,'#C9D3E1'); wg.addColorStop(1,'#7F8DA3'); g.fillStyle=wg; g.fill();
    g.strokeStyle='rgba(111,183,255,.95)'; g.lineWidth=Math.max(.7,L*.045); g.beginPath(); g.moveTo(-L*.3,sg*L*.52); g.lineTo(-L*.44,sg*L*.5); g.stroke(); });
  g.beginPath(); g.moveTo(L*.58,0); g.lineTo(L*.16,-L*.11); g.lineTo(-L*.42,-L*.1); g.lineTo(-L*.5,0); g.lineTo(-L*.42,L*.1); g.lineTo(L*.16,L*.11); g.closePath();
  const hg=g.createLinearGradient(0,-L*.11,0,L*.11); hg.addColorStop(0,'#FFFFFF'); hg.addColorStop(.55,'#DFE6F0'); hg.addColorStop(1,'#8E9BB0'); g.fillStyle=hg; g.fill();
  g.strokeStyle='rgba(5,7,13,.55)'; g.lineWidth=Math.max(.5,L*.025); g.stroke();
  g.beginPath(); g.ellipse(L*.16,0,L*.15,L*.055,0,0,Math.PI*2); const cg=g.createLinearGradient(L*.05,-L*.05,L*.28,L*.05); cg.addColorStop(0,'#D8F4FF'); cg.addColorStop(1,'#2F7FBF'); g.fillStyle=cg; g.fill();
  g.restore(); }
// an agent's pointer: a 40 px outlined amber arrow at its target (point; the map briefing's arrows)
function wfArrow(g,x,y,ang,t){
  g.save(); g.translate(x,y); g.rotate(ang);
  g.beginPath(); g.moveTo(0,0); g.lineTo(-16,-13); g.lineTo(-16,-6); g.lineTo(-40,-6); g.lineTo(-40,6); g.lineTo(-16,6); g.lineTo(-16,13); g.closePath();
  g.fillStyle='rgba(255,181,71,.16)'; g.fill(); g.lineJoin='round'; g.lineWidth=4; g.strokeStyle='rgba(5,7,13,.9)'; g.stroke(); g.lineWidth=2; g.strokeStyle=WF_COL.needs_you; g.stroke();
  g.restore(); }
// a dim veil over a planet that is not on the selected route (the comp's non-step planets at 42%)
function wfVeil(g,s){ const gr=g.createRadialGradient(s.x,s.y,s.r*.95,s.x,s.y,s.r*1.45); gr.addColorStop(0,'rgba(5,7,13,.66)'); gr.addColorStop(1,'rgba(5,7,13,0)');
  g.fillStyle=gr; g.beginPath(); g.arc(s.x,s.y,s.r*1.45,0,Math.PI*2); g.fill(); }
// a named place: a small neutral beacon (diamond) with its name
function wfBeacon(g,s,t){ g.save(); const p=1;
  g.fillStyle='rgba(5,7,13,.85)'; g.beginPath(); g.moveTo(s.x,s.y-9); g.lineTo(s.x+9,s.y); g.lineTo(s.x,s.y+9); g.lineTo(s.x-9,s.y); g.closePath(); g.fill();
  g.strokeStyle=wfRgba('#9AA8BC',p); g.lineWidth=1.4; g.stroke(); g.fillStyle='#E8EEF6'; g.beginPath(); g.arc(s.x,s.y,2.2,0,Math.PI*2); g.fill(); g.restore();
  wfText(g,String(s.label||''),s.x,s.y+13,{size:13,col:'#C9D3E1',align:'center'}); }

// ---------- the agent's crawl: when the working step moves to another planet its agent flies there in 2 s (along the ribbon
// when the workflow is selected, straight on the overview), through CM.dockOverride for a seat that runs it ----------
function wfCrawlStart(id,from,to){ WF.crawl.set(id,{id:id,from:from,to:to,t0:performance.now(),ms:REDUCED?0:2000}); }
function wfCrawlLive(id){ const c=WF.crawl.get(id); if(!c) return null;
  if(performance.now()-c.t0>=c.ms){ WF.crawl.delete(id); if(wfSeatOf(wfById(id))) WF.needPlace=true; return null; } return c; }
function wfPlaceLater(){ if(!WF.needPlace) return; WF.needPlace=false; try{ place(); }catch(e){ wfErr(e); } }
// the seat a workflow names as its author (by.seat), when it is on the map: it docks where the workflow works
function wfSeatOf(w){ const s=w&&w.by&&typeof w.by==='object'?w.by.seat:null; return s?units.find(u=>u&&u.uid===s&&isShip(u))||null:null; }
function wfShown(w){ return w&&WF.replay&&WF.replay.id===w.id&&WF.replay.view?WF.replay.view:w; }
function wfAgentAt(w){ if(!w) return null; const c=wfCrawlLive(w.id); if(c) return c.from; const v=wfShown(w); if(!wfRunning(v)) return null; const st=wfCurrent(v); return st?wfAnchor(st):null; }
CM.dockOverride=u=>{ if(!u||!u.uid) return null;
  for(const w of CM.wfList()){ if(!wfRunning(w)||!w.by||w.by.seat!==u.uid) continue; const at=wfAgentAt(w); return at&&String(at).startsWith('area:')?at:null; }
  return null; };
// where the agent is drawn now: its dock spot, or on its way between two (eased)
function wfAgentPos(w,route){
  const v=wfShown(w), c=wfCrawlLive(w.id), cur=wfCurrent(v), f=wfProg(cur);
  if(!c){ const at=wfAgentAt(w); return at?wfDockSpot(at,f):null; }
  const A=wfDockSpot(c.from,null), B=wfDockSpot(c.to,f); if(!A||!B) return B||A;
  let k=Math.max(0,Math.min(1,(performance.now()-c.t0)/c.ms)); k=k*k*(3-2*k);
  const seg=route&&route.segs.find(s=>s.from.uid===c.from&&s.to.uid===c.to);
  if(seg){ const q=wfCurve([A.x,A.y],[B.x,B.y],seg.bend), i=Math.min(q.length-2,Math.floor(k*(q.length-1))), p=q[i], n=q[i+1];
    return {x:p[0]+(n[0]-p[0])*(k*(q.length-1)-i),y:p[1]+(n[1]-p[1])*(k*(q.length-1)-i),ang:Math.atan2(n[1]-p[1],n[0]-p[0]),moving:true}; }
  return {x:A.x+(B.x-A.x)*k,y:A.y+(B.y-A.y)*k,ang:Math.atan2(B.y-A.y,B.x-A.x),moving:true}; }

// ---------- the module's timer (one animation frame at a time while a replay or a receipt band is up) ----------
function wfTickArm(){ if(WF.tickOn||!(WF.replay||WF.band)) return; WF.tickOn=true; try{ requestAnimationFrame(wfTickFrame); }catch(e){ WF.tickOn=false; } }
function wfTickFrame(){
  WF.tickOn=false; WF.ticks=(WF.ticks||0)+1;
  try{ wfPlaceLater(); wfReplayAdvance(performance.now()); wfBandTick(); wfReplayBarTick(); wfCrumbSync(); }catch(e){ wfErr(e); }
  wfTickArm(); }
// the chrome module came in after this module's fallbacks were up: hand the card and the strip over, retire the fallbacks
function wfChromeCheck(){ const on=!!CM.chromeReady; if(WF.chromeSeen===on) return; const was=WF.chromeSeen; WF.chromeSeen=on;
  if(was===undefined) return; WF.strip.sig=''; if(WF.card.id){ WF.card.handoff=true; try{ wfCardSync(true); }catch(e){ wfErr(e); } } try{ wfStripSync(); wfStripBox(); }catch(e){ wfErr(e); } }

// ---------- the 2D hook (after drawSystem, before the craft and the plates): routes, gates and agents on the map canvas;
// tags, medals, shows and pointers on a glass canvas over it, so no plate or craft covers them ----------
CM.drawWorkflows2D=function(t){ try{ wfDraw(t||performance.now()); }catch(e){ wfErr(e); } };
function wfErr(e){ if(WF.errLogged) return; WF.errLogged=true; try{ console.error('workflows2d: '+String((e&&e.stack)||e)); }catch(x){} }
function wfOverlay(){
  let c=WF.ov; if(c&&c.isConnected) return c;
  const st=document.getElementById('stage'); if(!st||!st.appendChild) return null;
  WF.ovW=WF.ovH=0; c=document.createElement('canvas'); c.id='wfover'; c.className='wf-over'; c.setAttribute('aria-hidden','true'); st.appendChild(c); WF.ov=c; return c; }
function wfDraw(t){
  const g=cx, phone=wfIsPhone(), now=performance.now();
  wfPlaceLater(); wfChromeCheck(); wfIdle();
  const selLive=wfById(CM.selectedWorkflow()), sel=selLive?wfShown(selLive):null;
  const focus=(typeof CM.focusUid==='function'&&CM.focusUid())||null, home=typeof deck==='undefined'||deck==='home';
  const F={mode:sel?'selected':focus?'focus':'overview',ribbons:[],gates:[],tags:[],chips:{},medals:wfMedals(),
    shows:{drawn:0,cards:0,charts:0,items:[]},dimmed:[],agents:[],places:[]};
  const sized=Number.isFinite(VW*DPR)&&Number.isFinite(VH*DPR), ov=sized?wfOverlay():null, o=ov&&ov.getContext?ov.getContext('2d'):null;   // an unmeasured stage: no glass
  if(o){ const W2=Math.round(VW*DPR), H2=Math.round(VH*DPR);
    if(WF.ovW!==W2||WF.ovH!==H2){ ov.width=W2; ov.height=H2; ov.style.width=VW+'px'; ov.style.height=VH+'px'; WF.ovW=W2; WF.ovH=H2; WF.ovDirty=false; }
    if(WF.ovDirty){ o.setTransform(1,0,0,1,0,0); o.clearRect(0,0,ov.width,ov.height); WF.ovDirty=false; WF.ovClears=(WF.ovClears||0)+1; }
    o.setTransform(DPR,0,0,DPR,0,0); }
  if(!home){ WF.frame=F; return; }
  g.save(); g.setTransform(DPR,0,0,DPR,0,0);
  let route=null, gates=[];
  if(sel){
    route=wfRoute(sel);
    const on=new Set(route.stops.map(s=>s.b.uid));
    MOONS.forEach(m=>{ if(on.has(m.uid)) return; const s=wfScr(VEC?Object.assign({},m,project(m.x,m.y)):m); wfVeil(g,s); F.dimmed.push(m.uid); });
    const ghost=sel.state==='planned', age=(now-(WF.selT0||0))/1000, fade=REDUCED?1:Math.max(0,Math.min(1,age));
    route.segs.forEach(seg=>{ if(fade<1&&!ghost) wfRibbon(g,seg.pts,'waiting',{t:t});
      wfRibbon(g,seg.pts,seg.state,{t:t,ghost:ghost,alpha:ghost?1:fade}); });
    const steps=wfSteps(sel);
    F.ribbons.push({id:sel.id,segments:route.segs.length,filled:steps.filter(s=>s.state==='done').length,ghost:ghost,state:String(sel.state||''),
      legs:route.segs.map(s=>s.state),mids:route.segs.map(s=>{ const q=s.pts[Math.floor(s.pts.length/2)]; return {state:s.state,x:Math.round(q[0]),y:Math.round(q[1])}; })});
    gates=wfGates(sel,route);
    route.stops.forEach(st=>{ if(st.b.kind==='place') F.places.push(st.s); });
  } else if(focus){
    CM.wfList().forEach(w=>{ if(!w||WF_FINISHED.includes(w.state)&&w.state!=='failed') return; const v=wfShown(w);
      if(!wfSteps(v).some(s=>wfAnchor(s)===focus)) return; const R=wfRoute(v); gates=gates.concat(wfGates(v,R,focus).map(x=>Object.assign(x,{wf:w.id}))); });
  }
  gates.forEach(G=>{ wfGate(g,G.x,G.y,G.kind,G.prog,phone?6:7,G.n); F.gates.push({key:G.key,n:G.n,uid:G.uid,state:G.state,x:Math.round(G.x),y:Math.round(G.y)}); });
  // the agents: each running workflow's agent on its planet's working ring (a seat that runs it is moved instead)
  CM.wfList().forEach(w=>{ if(!w) return; const v=wfShown(w); if(!wfRunning(v)&&!WF.crawl.has(w.id)) return;
    const P=wfAgentPos(w,sel&&sel.id===w.id?route:null); if(!P) return; const seat=wfSeatOf(w);
    if(seat){ if(P.moving){ seat.gx=(P.x/ZOOM+camx)/TILE; seat.gy=(P.y/ZOOM+camy)/TILE; } }
    else wfCraft(g,P.x,P.y,phone?16:22,P.ang,v.state==='working'?1:.35);
    F.agents.push({id:w.id,at:wfAgentAt(w),x:Math.round(P.x),y:Math.round(P.y),seat:seat?seat.uid:null}); });
  g.restore();
  if(o){
    // medals (planet state: every view)
    Object.keys(F.medals).forEach(uid=>{ const b=wfBody(uid); if(!b) return; const s=wfScr(b); wfMedal(o,s.x-s.r*.34,s.y-s.r-12,.75); WF.ovDirty=true; });
    // the chip after a name, until the label module places it in the name's own slot (CM.labelChip)
    MOONS.forEach(m=>{ const c=wfChipOf(m.uid); if(!c) return; F.chips[m.uid]=c.text;
      if(!(typeof LABEL!=='undefined'&&LABEL&&typeof LABEL.place==='function')&&!sel) { wfChipDraw(o,wfScr(VEC?Object.assign({},m,project(m.x,m.y)):m),c,phone,wfPlateBox(m.uid)); WF.ovDirty=true; } });
    { const sun=wfChipOf(wfSunUid()); if(sun) F.chips[wfSunUid()]=sun.text; }
    F.places.forEach(s=>{ wfBeacon(o,s,t); WF.ovDirty=true; });
    if(gates.length){ wfTagsDraw(o,gates,F,phone,sel); WF.ovDirty=true; }
    wfShowsDraw(o,F,focus,sel,route,t);
    wfPointsDraw(o,F,t);
  }
  wfCrumbSync();
  WF.frame=F; if(F.mode==='overview') WF.overview={ribbons:F.ribbons.length,gates:F.gates.length,shows:F.shows.drawn};
}
function wfPlateBoxes(){
  try{ const F=typeof PLATE_FINAL!=='undefined'?PLATE_FINAL:null; if(!F||!Array.isArray(F.placed)) return [];
    return F.placed.filter(p=>p&&p.q).map(p=>({x:(p.x-F.camx)*F.zoom,y:(p.y-F.camy)*F.zoom,w:p.w*F.zoom,h:p.h*F.zoom,plate:true})); }catch(e){ return []; } }
// the boxes the page lays over the stage (its own cards; homeCovers lists them for the home view)
function wfCovers(){ const now=performance.now(); if(WF.covers&&now-WF.covers.at<400) return WF.covers.list;     // measured at most every 0.4 s
  let list=[]; try{ list=typeof homeCovers==='function'?homeCovers().map(b=>({x:b.x,y:b.y,w:b.w,h:b.h})):[]; }catch(e){}
  WF.covers={at:now,list:list}; return list; }
function wfPlateBox(uid){
  try{ const F=typeof PLATE_FINAL!=='undefined'?PLATE_FINAL:null; if(!F||!Array.isArray(F.placed)) return null;
    const p=F.placed.find(x=>x&&x.q&&x.q.uid===uid&&x.q.kind!=='tag'); if(!p) return null;
    return {x:(p.x-F.camx)*F.zoom,y:(p.y-F.camy)*F.zoom,w:p.w*F.zoom,h:p.h*F.zoom}; }catch(e){ return null; } }
function wfChipDraw(g,s,c,phone,plate){
  const size=phone?12:13, col=c.state==='needs_you'||c.state==='stale'?WF_COL.needs_you:WF_COL.working, w=wfMeasure(g,c.text,size)+22, h=size+7;
  const x=plate?plate.x+plate.w/2-w/2:s.x-w/2, y=plate&&plate.y>s.y?plate.y+plate.h+5:s.y+s.r+(phone?22:26);
  g.save(); g.beginPath(); if(g.roundRect) g.roundRect(x,y,w,h,h/2); else g.rect(x,y,w,h); g.fillStyle='rgba(11,26,46,.88)'; g.fill(); g.strokeStyle=wfRgba(col,.75); g.lineWidth=1; g.stroke();
  g.fillStyle=col; g.beginPath(); g.arc(x+9,y+h/2,2.6,0,Math.PI*2); g.fill(); g.restore();
  wfText(g,c.text,x+15,y+3,{size:size,col:c.state==='needs_you'?'#FFE2B0':'#BFDDFF',stroke:false}); }
// numbered step tags beside their gates, on free ground: away from the bodies, each other and the edges
function wfTagsDraw(g,gates,F,phone,sel){
  const size=phone?13:14, fr=wfFree(), placed=wfPlateBoxes().concat(wfCovers()), discs=MOONS.map(m=>wfScr(VEC?Object.assign({},m,project(m.x,m.y)):m));
  const hit=b=>placed.some(p=>b.x<p.x+p.w+6&&b.x+b.w+6>p.x&&b.y<p.y+p.h+4&&b.y+b.h+4>p.y)
    ||discs.some(d=>{ const dx=Math.max(b.x-d.x,0,d.x-b.x-b.w), dy=Math.max(b.y-d.y,0,d.y-b.y-b.h); return dx*dx+dy*dy<(d.r+8)*(d.r+8); });
  const fit=b=>({x:Math.max(fr.x0,Math.min(fr.x1-b.w,b.x)),y:Math.max(fr.y0,Math.min(fr.y1-b.h,b.y)),w:b.w,h:b.h});
  gates.forEach(G=>{
    const txt=G.n+'  '+G.label+' · '+G.word, S=wfTagSize(g,txt,size), dx=Math.cos(G.a), dy=Math.sin(G.a); let best=null, first=null;
    for(const dist of [30,50,74,100]){ for(const sh of [0,-28,28,-56,56]){
      const ax=G.x+dx*dist, ay=G.y+dy*dist+sh, b=fit({x:dx>=-.15?ax:ax-S.w,y:ay-S.h/2,w:S.w,h:S.h}); if(!first) first=b;
      if(!hit(b)){ best=b; break; } } if(best) break; }
    best=best||first; placed.push(Object.assign({},best));
    const col=G.kind==='done'?WF_COL.done:G.kind==='working'?WF_COL.working:G.kind==='needs'?WF_COL.needs_you:G.kind==='failed'?WF_COL.failed:'#9AA8BC';
    wfTag(g,txt,best.x,best.y,col,[G.x+dx*9.5,G.y+dy*9.5],size);
    F.tags.push({key:G.key,text:txt,x:Math.round(best.x),y:Math.round(best.y),w:Math.round(best.w)}); }); }

// ---------- shows: what an agent puts on the map, drawn in focus where they are anchored, or anywhere while their
// workflow is selected; every item names its author first ("Claude Code: label overlaps"); no links, no buttons ----------
function wfPlanetOf(uid){ uid=String(uid||''); if(!uid) return null; if(MOONS.some(m=>m.uid===uid)) return uid;
  const m=MOONS.find(x=>(x.rooms||[]).some(r=>r&&r.uid===uid)); if(m) return m.uid; if(uid.startsWith('place:')) return uid;
  const u=unitByUid(uid); return u&&u.docked_at?u.docked_at:uid; }
function wfShowLive(sh){ const left=Number(sh.expires_in!=null?sh.expires_in:sh.left_s); return !Number.isFinite(left)||Date.now()-WF.showsAt<=left*1000; }
function wfWrap(g,s,w,size,max){ const words=String(s||'').split(/\s+/), out=[]; let line='';
  words.forEach(x=>{ const nx=line?line+' '+x:x; if(wfMeasure(g,nx,size,500)<=w||!line) line=nx; else { out.push(line); line=x; } }); if(line) out.push(line);
  if(out.length>max){ const k=out.slice(0,max); k[max-1]=k[max-1].replace(/\s*\S*$/,'')+'…'; return k; } return out; }
function wfGlass(g,x,y,w,h){ g.save(); g.beginPath(); if(g.roundRect) g.roundRect(x,y,w,h,8); else g.rect(x,y,w,h);
  const gr=g.createLinearGradient(0,y,0,y+h); gr.addColorStop(0,'rgba(16,25,44,.97)'); gr.addColorStop(1,'rgba(9,14,26,.97)'); g.fillStyle=gr; g.fill();
  g.strokeStyle='rgba(154,168,188,.45)'; g.lineWidth=1; g.setLineDash([4,3]); g.stroke(); g.restore(); }
function wfShowCard(g,op,by,x,y,dry){
  const W2=240, pad=10, title=by+': '+String(op.title||''), tl=wfWrap(g,title,W2-pad*2,14,2), rows=(Array.isArray(op.rows)?op.rows:[]).slice(0,8),
    tx=op.text?wfWrap(g,op.text,W2-pad*2,13,3):[], bar=Array.isArray(op.bar)&&op.bar.length===2?op.bar:null;
  const h=pad+tl.length*17+(rows.length?6+rows.length*17:0)+(tx.length?6+tx.length*16:0)+(bar?22:0)+pad-2;
  if(dry) return {w:W2,h:h};
  wfGlass(g,x,y,W2,h); let cy=y+pad; const drawn=tl[0];
  tl.forEach(l=>{ wfText(g,l,x+pad,cy,{size:14,stroke:false}); cy+=17; });
  if(rows.length){ cy+=6; rows.forEach(r=>{ wfText(g,String(r[0]),x+pad,cy,{size:13,w:500,col:'#9AA8BC',stroke:false}); wfText(g,String(r[1]),x+W2-pad,cy,{size:13,col:'#E8EEF6',stroke:false,align:'right'}); cy+=17; }); }
  if(tx.length){ cy+=6; tx.forEach(l=>{ wfText(g,l,x+pad,cy,{size:13,w:500,col:'#C9D3E1',stroke:false}); cy+=16; }); }
  if(bar){ cy+=6; const a=Number(bar[0])||0, b=Math.max(1,Number(bar[1])||1), f=Math.max(0,Math.min(1,a/b)), bw=W2-pad*2-52;
    g.save(); g.fillStyle='rgba(111,183,255,.2)'; g.fillRect(x+pad,cy+5,bw,4); g.fillStyle=WF_COL.working; g.shadowColor=WF_COL.working; g.shadowBlur=6; g.fillRect(x+pad,cy+5,bw*f,4); g.restore();
    wfText(g,a+' of '+b,x+W2-pad,cy-1,{size:13,col:'#BFDDFF',stroke:false,align:'right'}); }
  return {w:W2,h:h,drawn:drawn}; }
function wfShowChart(g,op,by,x,y,dry){
  const W2=160, H2=60; if(dry) return {w:W2,h:H2};
  const vals=(Array.isArray(op.values)?op.values:[]).map(Number).filter(Number.isFinite), lab=by+': '+String(op.label||'');
  wfGlass(g,x,y,W2,H2);
  let s=lab; while(s.length>4&&wfMeasure(g,s,12,600)>W2-12) s=s.slice(0,-2)+'…';
  const drawn=s;
  wfText(g,s,x+6,y+4,{size:12,stroke:false});
  if(vals.length){ const lo=Math.min(0,...vals), hi=Math.max(...vals,lo+1e-9), px=x+8, py=y+22, pw=W2-16, ph=H2-28, Y=v=>py+ph-(v-lo)/(hi-lo)*ph;
    g.save();
    if(op.kind==='bars'){ const bw=pw/vals.length; vals.forEach((v,i)=>{ g.fillStyle=i===vals.length-1?WF_COL.working:'rgba(111,183,255,.5)'; g.fillRect(px+i*bw+1,Y(v),Math.max(1,bw-2),py+ph-Y(v)); }); }
    else { g.strokeStyle=WF_COL.working; g.lineWidth=1.6; g.beginPath(); vals.forEach((v,i)=>{ const xx=px+(vals.length>1?i/(vals.length-1)*pw:pw/2); if(i) g.lineTo(xx,Y(v)); else g.moveTo(xx,Y(v)); }); g.stroke();
      g.fillStyle='#E8EEF6'; g.beginPath(); g.arc(px+pw,Y(vals[vals.length-1]),2.2,0,Math.PI*2); g.fill(); }
    g.restore();
    wfText(g,String(vals[vals.length-1])+(op.unit?' '+op.unit:''),x+W2-6,y+4,{size:12,col:'#BFDDFF',stroke:false,align:'right'}); }
  return {w:W2,h:H2,drawn:drawn}; }
// where an op is anchored: its own at, else the show's (a beam's two ends, a route's stops)
function wfOpAnchors(op,base){
  if(op.op==='beam') return [op.from,op.to].filter(x=>typeof x==='string'&&x);
  if(op.op==='route') return (Array.isArray(op.stops)?op.stops:[]).filter(x=>typeof x==='string'&&x);
  const a=op.at||base; return typeof a==='string'&&a?[a]:[]; }
// each op on its own: drawn in focus when one of its anchors is on the planet in focus, in a selection when one is on the
// selected workflow's route (its planets and places); never on the overview. The author is painted with every op, and the
// audit's item text is the very line painted
function wfShowsDraw(g,F,focus,sel,route,t){
  if(!focus&&!sel) return;
  const onRoute=new Set(); if(sel&&route) route.stops.forEach(st=>{ onRoute.add(st.uid); if(st.b) onRoute.add(st.b.uid); });
  const want=uids=>uids.some(u=>{ const p=wfPlanetOf(u); return focus?p===focus:(onRoute.has(p)||onRoute.has(u)); });
  const fr=wfFree(), cols=new Map();
  const col=(anchor,w,h)=>{ let c=cols.get(anchor);
    if(!c){ const b=wfBody(anchor), s=b?wfScr(b):null; c={s:s,x:s?s.x+s.r+30:fr.x0+20,y:s?s.y-s.r:fr.y0+20,right:true,first:true};
      if(s&&c.x+240>fr.x1){ c.x=s.x-s.r-30-240; c.right=false; } cols.set(anchor,c); }
    const y=Math.max(fr.y0,Math.min(fr.y1-h,c.y)), x=c.right?c.x:c.x+240-w;
    if(c.s&&c.first){ g.save(); g.strokeStyle='rgba(154,168,188,.55)'; g.setLineDash([3,3]); g.beginPath(); g.moveTo(c.s.x+(c.right?c.s.r+4:-c.s.r-4),c.s.y); g.lineTo(c.right?x:x+w,y+12); g.stroke(); g.restore(); c.first=false; }
    c.y=y+h+10; return {x:x,y:y}; };
  CM.showList().forEach(sh=>{ if(!sh||typeof sh!=='object'||!Array.isArray(sh.ops)||!wfShowLive(sh)) return;
    const by=String(sh.by||'an agent').slice(0,24), base=sh.at||sh.planet_uid||(sh.place?'place:'+sh.place:'');
    const ops=sh.ops.map((op,i)=>({op:op,i:i})).filter(x=>x.op&&typeof x.op==='object').map(x=>Object.assign(x,{at:wfOpAnchors(x.op,base)})).filter(x=>x.at.length&&want(x.at));
    if(!ops.length) return;
    F.shows.drawn++; WF.ovDirty=true;
    const item=(op,drawn,extra)=>F.shows.items.push(Object.assign({op:op,text:drawn,drawn:drawn},extra||{}));
    ops.forEach(x=>{ const op=x.op, i=x.i, home=wfPlanetOf(x.at[0]), at=wfBody(home), as=at?wfScr(at):null;
      if(op.op==='card'||op.op==='chart'){ const size=(op.op==='card'?wfShowCard:wfShowChart)(g,op,by,0,0,true), pos=col(home,size.w,size.h);
        const r=(op.op==='card'?wfShowCard:wfShowChart)(g,op,by,pos.x,pos.y,false);
        F.shows[op.op==='card'?'cards':'charts']++; item(op.op,r.drawn,{w:r.w,h:r.h,x:Math.round(pos.x),y:Math.round(pos.y)}); return; }
      if(op.op==='radio'){ const key=String(sh.id||'')+':'+i; if(!WF.radioShows.has(key)){ WF.radioShows.add(key);
          try{ radioCall({uid:'show:'+key,name:by,word:'says',line:String(op.text||''),col:'#9AA8BC',carrier:false,mate:false,tag:by.charAt(0).toUpperCase(),cue:'warp-in'}); }catch(e){} }
        item('radio',by+': '+String(op.text||''),{where:'radio'}); return; }
      if(!as) return;
      if(op.op==='label'){ const tx=by+': '+String(op.text||''); wfTag(g,tx,as.x+as.r+12,as.y-as.r-24,'#9AA8BC',[as.x+as.r*.7,as.y-as.r*.7],13); item('label',tx); }
      else if(op.op==='marker'){ const tx=by+': '+String(op.text||op.icon); wfText(g,{flag:'⚑',pin:'⌖',star:'★',warn:'⚠'}[op.icon]||'•',as.x,as.y-as.r-26,{size:18,col:op.icon==='warn'?WF_COL.needs_you:'#E8EEF6',align:'center'});
        wfText(g,tx,as.x,as.y-as.r-8,{size:13,align:'center',col:'#C9D3E1'}); item('marker',tx); }
      else if(op.op==='ring'){ const c=wfCol(op.state); g.save(); g.strokeStyle=wfRgba(c,.35); g.lineWidth=2; g.beginPath(); g.arc(as.x,as.y,as.r+16,0,Math.PI*2); g.stroke();
        const f=op.progress&&op.progress.total>0?op.progress.done/op.progress.total:Number(op.progress)||0; g.strokeStyle=c; g.lineWidth=3; g.beginPath(); g.arc(as.x,as.y,as.r+16,-Math.PI/2,-Math.PI/2+Math.max(.02,Math.min(1,f))*Math.PI*2); g.stroke(); g.restore();
        const tx=by+': '+(CM.stateWord(op.state)||String(op.state||'')); wfText(g,tx,as.x,as.y+as.r+22,{size:12,align:'center',col:'#C9D3E1'}); item('ring',tx); }
      else if(op.op==='beam'||op.op==='route'){ const pts=x.at.map(u=>wfBody(wfPlanetOf(u))).filter(Boolean).map(wfScr);
        if(pts.length<2) return;
        g.save(); g.strokeStyle=wfRgba(WF_COL.working,.8); g.lineWidth=1.6; g.setLineDash([6,5]); g.lineDashOffset=REDUCED?0:-(t/1000*30); g.shadowColor=WF_COL.working; g.shadowBlur=6;
        g.beginPath(); pts.forEach((p,k)=>k?g.lineTo(p.x,p.y):g.moveTo(p.x,p.y)); g.stroke(); g.restore();
        const m=pts.length>2?pts[Math.floor(pts.length/2)]:{x:(pts[0].x+pts[1].x)/2,y:(pts[0].y+pts[1].y)/2}, tx=by+': '+(op.op==='beam'?'link':'route');
        wfText(g,tx,m.x,m.y-18,{size:12,align:'center',col:'#BFDDFF'}); item(op.op,tx); }
      // an agent's page: a tag at its place (the page itself opens from the page dock's card, never from the map)
      else if(op.op==='page'){ const tx=by+': page · '+String(op.title||''); wfTag(g,tx,as.x+as.r+12,as.y+as.r+10,'#7FD4FF',[as.x+as.r*.7,as.y+as.r*.7],13); item('page',tx); }
      else if(op.op==='fx'){ const key=String(sh.id||'')+':'+i; if(!WF.fxSeen.has(key)) WF.fxSeen.set(key,performance.now());
        const life=(Number(op.life)||1.5)*1000, age=performance.now()-WF.fxSeen.get(key); if(age>=life) return;
        const k=REDUCED?0:age/life, tx=by+': '+String(op.text||op.preset);
        g.save(); g.globalAlpha=1-k; if(!REDUCED){ g.strokeStyle=op.color||WF_COL.done; g.lineWidth=2; g.beginPath(); g.arc(as.x,as.y,as.r+6+k*40,0,Math.PI*2); g.stroke(); }
        wfText(g,tx,as.x,as.y-as.r-20-k*16,{size:13,align:'center',col:op.color||'#E8EEF6'}); g.restore(); item('fx',tx); } }); }); }

// ---------- pointers (control action "point"): an arrow at the target and the words in the status bar for its seconds ----------
function wfPoint(a){
  const sec=Math.max(5,Math.min(120,Number(a.seconds)||20)), target=String(a.target), text=String(a.text||'').slice(0,80);
  WF.points=WF.points.filter(p=>p.until>Date.now()).slice(-7);
  WF.points.push({target:target,text:text,until:Date.now()+sec*1000,at:Date.now()});
  const name=CM.labelText(wfPlanetOf(target)||target);
  wfCaption((text?text+' · ':'')+(a.by?String(a.by):'an agent')+' points at '+String(name).replace(/^place:/,''),sec); }
function wfPointsDraw(g,F,t){
  WF.points=WF.points.filter(p=>p.until>Date.now());
  WF.points.forEach(p=>{ const b=wfBody(wfPlanetOf(p.target)); if(!b) return; const s=wfScr(b), a=Math.PI/4, d=s.r+8;
    wfArrow(g,s.x-Math.cos(a)*d,s.y-Math.sin(a)*d,a,t); WF.ovDirty=true; }); }

// ---------- DOM helpers (agent text only through textContent) ----------
function wfEl(tag,cls,text,attrs){ const e=document.createElement(tag); if(cls) e.className=cls; if(text!=null) e.textContent=String(text);
  if(attrs) Object.keys(attrs).forEach(k=>{ if(attrs[k]!=null) e.setAttribute(k,String(attrs[k])); }); return e; }
function wfBtn(text,cls,attrs){ return wfEl('button','wf-btn'+(cls?' '+cls:''),text,Object.assign({type:'button'},attrs||{})); }
function wfHex(kind,prog,num,size){ const c=wfEl('canvas','wf-hex'); size=size||28; const d=Math.min(3,Math.max(1,Math.round(typeof DPR==='number'?DPR:1)));
  c.width=size*d; c.height=size*d; c.style.width=size+'px'; c.style.height=size+'px'; const g=c.getContext&&c.getContext('2d');
  if(g&&g.scale){ g.scale(d,d); wfGate(g,size/2,size/2,kind,prog,size*.36,num); } return c; }
function wfThumb(uid,size){
  const k=uid+'|'+size; if(WF.thumbs.has(k)) return WF.thumbs.get(k); let url='';
  try{ const u=unitByUid(uid), L=u&&typeof CM.planetLook==='function'?CM.planetLook(u):null;
    if(L&&typeof CM.purposeIcon==='function'){ const c=CM.purposeIcon(L.purpose,size*2,{seed:String(u.path||uid)}); url=c&&c.toDataURL?c.toDataURL():''; } }catch(e){}
  WF.thumbs.set(k,url); return url; }
function wfRawById(id){ return id?wfRaw().find(w=>w&&w.id===id)||null:null; }
function wfStarted(w){ const u=Date.parse(String(w.updated||'')); const e=Number(w.elapsed_s); return Number.isFinite(u)?u-(Number.isFinite(e)?e*1000:0):NaN; }
function wfOwnerPage(){ return !(wfIsPhone()||(typeof REMOTE!=='undefined'&&REMOTE)); }

// ---------- the workflow card (the rail on a desktop, the sheet on a phone; the fallback box until the chrome module is in) ----------
// where a desktop button would be on a phone: what it is, and "On your computer"
function wfAway(verb){ const e=wfEl('span','wf-away'); e.appendChild(wfEl('span','v',verb)); e.appendChild(wfEl('span','k','On your computer')); return e; }
function wfStateLine(v,steps,cur){
  const st=String(v.state||''), n=cur?steps.indexOf(cur)+1:0, T=steps.length, R=WF.replay&&WF.replay.id===v.id?'REPLAY · ':'';
  if(st==='planned') return R+'PLANNED · '+T+' STEPS';
  if(st==='done') return R+'DONE · '+T+' OF '+T+' STEPS';
  if(st==='stopped') return R+'STOPPED · '+(Number(v.done)||0)+' OF '+T+' DONE';
  if(st==='failed'){ const f=steps.find(s=>s.state==='failed'); return R+'FAILED AT STEP '+(f?steps.indexOf(f)+1:n)+' OF '+T; }
  return R+String(CM.stateWord(st)||st).toUpperCase()+(n?' · STEP '+n+' OF '+T:''); }
function wfStepMeta(v,s,i,steps){
  const word=wfStepWord(s,'card'), place=wfPlaceName(s); let extra='';
  if(s.state==='working') extra=s.kind==='task'?(s.task_status==='checking'?'a second opinion reads it':'an agent works on it'+(s.attempt>1?' · try '+s.attempt:'')):wfBy(v);
  else if((s.state==='waiting'||s.state==='ready')&&s.kind==='gate') extra='you approve here';
  else if(s.state==='waiting'||s.state==='ready'){ const a=(Array.isArray(s.after)?s.after:[]).map(k=>steps.findIndex(x=>x.key===k)+1).filter(x=>x>0); if(a.length) extra='starts after step '+a.join(' and '); }
  else if(s.state==='needs_you'&&s.kind!=='gate') extra='your turn';
  return {word:word,place:place,extra:extra}; }
function wfCardBuild(el,v,live,phone){
  el.textContent=''; el.setAttribute('data-wf-card',String(live.id)); el.classList.toggle('phone',!!phone);
  const steps=wfSteps(v), cur=wfCurrent(v), st=String(v.state||''), owner=wfOwnerPage()&&!live.derived, R=WF.replay&&WF.replay.id===live.id;
  if(!CM.chromeReady&&!(phone&&typeof CM.sheetCard==='function')){
    const top=wfEl('div','wf-top'); if(phone) top.appendChild(wfEl('i','wf-grab',null,{'aria-hidden':'true'}));
    top.appendChild(wfBtn(phone?'Close':'← Workflows','wf-back',{'data-wf-view':'close'})); if(!phone) top.appendChild(wfEl('span','wf-key','Esc')); el.appendChild(top); }
  const head=wfEl('div','wf-head st-'+st), sl=wfEl('div','wf-stateline'); sl.appendChild(wfEl('i','wf-dot')); sl.appendChild(wfEl('span',null,wfStateLine(v,steps,cur)));
  head.appendChild(sl); head.appendChild(wfEl('h2','wf-title',String(v.title||'a workflow')));
  const t0=wfStarted(v), by=wfBy(v)==='you'?'by you':'by '+wfBy(v);
  head.appendChild(wfEl('div','wf-byline',by+(Number.isFinite(t0)?' · '+(st==='planned'?'made ':'started ')+CM.agoOf(t0):'')+(v.derived==='night'?' · from the night report':'')));
  el.appendChild(head);
  if(v.goal){ const g=wfEl('div','wf-goal'); g.appendChild(wfEl('span','wf-gl','Goal')); g.appendChild(wfEl('span',null,String(v.goal))); el.appendChild(g); }
  // progress: one segment a step (a bar past 12 steps) and the count with what is left
  const done=steps.filter(s=>s.state==='done').length, pr=wfEl('div','wf-prog'+(steps.length>12?' bar':''));
  (steps.length>12?[{state:'done',f:done/Math.max(1,steps.length)}]:steps.map(s=>({state:s.state,f:wfProg(s)}))).forEach(x=>{ const i=wfEl('i','st-'+x.state); const f=x.state==='working'?(x.f==null?.35:x.f):steps.length>12?x.f:null;
    if(f!=null){ const b=wfEl('b'); b.style.width=Math.round(Math.max(.04,f)*100)+'%'; i.appendChild(b); } pr.appendChild(i); });
  el.appendChild(pr);
  const pl=wfEl('div','wf-progline'), left=wfEl('span'); left.appendChild(wfEl('b',null,done+' of '+steps.length)); left.appendChild(wfEl('span',null,' done')); pl.appendChild(left);
  const el_s=Number(v.elapsed_s), frac=cur?(wfProg(cur)||0):0;
  if(wfRunning(v)&&done>0&&Number.isFinite(el_s)&&el_s>0){ const per=el_s/(done+frac), rest=per*Math.max(0,steps.length-done-frac); pl.appendChild(wfEl('span',null,'about '+wfDur(Math.max(60,rest))+' left')); }
  else if(st==='done'&&Number.isFinite(el_s)) pl.appendChild(wfEl('span',null,'took '+wfDur(el_s)));
  el.appendChild(pl);
  // the stepper
  const list=wfEl('ol','wf-steps');
  steps.forEach((s,i)=>{ const row=wfEl('li','wf-step st-'+s.state+(s===cur?' cur':'')), M=wfStepMeta(v,s,i,steps);
    row.appendChild(wfHex(wfGateKind(s.state),wfProg(s),i+1,28));
    const body=wfEl('div','wf-sb'); body.appendChild(wfEl('div','wf-st',String(s.label||s.key)));
    const meta=wfEl('div','wf-sm'); meta.appendChild(wfEl('b','w st-'+s.state,M.word)); meta.appendChild(wfEl('span',null,' · '+M.place+(M.extra?' · '+M.extra:''))); body.appendChild(meta);
    if(s.says) body.appendChild(wfEl('div','wf-says',String(s.says)));
    if(s.output||s.artifact){ const o=wfEl('span','wf-out'); o.appendChild(wfEl('i','wf-doc')); o.appendChild(wfEl('span',null,String(s.artifact&&s.output&&String(s.output).indexOf(String(s.artifact))<0?s.artifact+': '+s.output:(s.output||s.artifact)))); body.appendChild(o); }
    if(s.state==='working'&&wfProg(s)!=null){ const b=wfEl('div','wf-sbar'), f=wfEl('i'); f.style.width=Math.round(wfProg(s)*100)+'%'; b.appendChild(f); body.appendChild(b); }
    if(s.state==='failed'&&s.why) body.appendChild(wfEl('div','wf-why',String(s.why)));
    // the owner's buttons where the user acts: a gate (the phone too), a failed task step, a step waiting on accepted changes
    const acts=wfEl('div','wf-sacts');
    if(!R&&!live.derived&&s.kind==='gate'&&s.state==='needs_you'){ acts.appendChild(wfBtn('Approve','prim',{'data-wf-action':'gate-pass','data-wf-key':s.key})); acts.appendChild(wfBtn('Reject','',{'data-wf-action':'gate-fail','data-wf-key':s.key})); }
    if(!R&&owner&&(s.state==='failed'||s.state==='blocked')&&s.kind!=='gate'){ if(s.kind==='task') acts.appendChild(wfBtn('Retry','',{'data-wf-action':'retry','data-wf-key':s.key})); acts.appendChild(wfBtn('Skip','ghost',{'data-wf-action':'skip','data-wf-key':s.key})); }
    if(!R&&s.kind==='task'&&s.tid&&s.state==='needs_you') acts.appendChild(wfBtn('Look at the changes','',{'data-wf-view':'task','data-tid':s.tid}));
    if(!R&&!owner&&!live.derived&&(s.state==='failed'||s.state==='blocked')&&s.kind!=='gate') acts.appendChild(wfAway(s.kind==='task'?'Retry or skip':'Skip'));
    if(acts.childNodes.length) body.appendChild(acts);
    row.appendChild(body); list.appendChild(row); });
  el.appendChild(list);
  if(WF.card.msg&&WF.card.msgId===live.id) el.appendChild(wfEl('div','wf-msg',WF.card.msg));
  // Start lists what runs as the user first
  const C=WF.card.confirm;
  if(C&&C.id===live.id&&owner){ const box=wfEl('div','wf-confirm'); box.appendChild(wfEl('div','wf-ch','These run as you when it starts:'));
    C.lines.forEach(l=>box.appendChild(wfEl('code',null,String(l)))); const a=wfEl('div','wf-acts');
    a.appendChild(wfBtn('Start them','prim big',{'data-wf-action':'start-confirm'})); a.appendChild(wfBtn('Cancel','ghost big',{'data-wf-view':'confirm-cancel'})); box.appendChild(a); el.appendChild(box); }
  const acts=wfEl('div','wf-acts');
  if(owner&&!R){
    if(st==='planned'&&!C) acts.appendChild(wfBtn('Start','prim big',{'data-wf-action':'start'}));
    if(wfRunning(v)&&typeof CM.enter3d==='function') acts.appendChild(wfBtn('Follow in 3D','big strong',{'data-wf-view':'follow3d'}));
    if(wfRunning(v)) acts.appendChild(wfBtn(WF.card.stopArm===live.id?'Press again to stop it':'Stop the workflow','ghost big'+(WF.card.stopArm===live.id?' warn':''),{'data-wf-action':'stop'}));
    if(WF_FINISHED.includes(st)) acts.appendChild(wfBtn('Run again','big strong',{'data-wf-action':'new'}));
  } else if(!R) {
    acts.appendChild(wfBtn('Show on map','big strong',{'data-wf-view':'show'}));
    if(!live.derived&&(st==='planned'||wfRunning(v)||WF_FINISHED.includes(st))) acts.appendChild(wfAway(st==='planned'?'Start':wfRunning(v)?'Stop the workflow':'Run again'));
  }
  if(R) acts.appendChild(wfBtn('Back to now','big strong',{'data-wf-view':'replay-close'}));
  if(acts.childNodes.length) el.appendChild(acts);
  // finished: the receipt (facts only)
  if(WF_FINISHED.includes(st)&&!R){ const T=wfTally(live), box=wfEl('div','wf-receipt'); box.appendChild(wfEl('h4',null,'Receipt'));
    wfTallyRows(Object.assign({},T,{t0:-1e9})).forEach(r=>{ const row=wfEl('div','wf-rr'); row.appendChild(wfEl('span',null,r[0])); row.appendChild(wfEl('b',null,r[1])); box.appendChild(row); }); el.appendChild(box); }
  // what happened: the newest three events in plain words
  const log=wfEl('div','wf-log'), lh=wfEl('div','wf-lh'); lh.appendChild(wfEl('h4',null,'What happened'));
  if(!R) lh.appendChild(wfBtn('Replay','link',{'data-wf-view':'replay'})); log.appendChild(lh);
  const lines=wfLogLines(live); if(!lines.length) log.appendChild(wfEl('div','wf-e none','Nothing yet'));
  lines.forEach(x=>{ const e=wfEl('div','wf-e'); e.appendChild(wfEl('i','st-'+(x.state||'waiting'))); e.appendChild(wfEl('span','tx',x.text)); e.appendChild(wfEl('span','ag',x.t?String(CM.relTime((Date.now()-x.t)/1000)).replace(/ ago$/,''):'')); log.appendChild(e); });
  el.appendChild(log); }
function wfLogLines(w){
  const D=WF.detail.get(w.id), steps=wfSteps(w), key=new Map(steps.map((s,i)=>[s.key,{s:s,i:i}])), out=[];
  if(D&&Array.isArray(D.events)) D.events.slice().reverse().forEach(e=>{ if(out.length>=3||!e) return; const k=key.get(e.key), n=k?k.i+1:'', lab=k?String(k.s.label||k.s.key):'';
    let tx=null, st='';
    if(e.ev==='derived'&&e.key){ tx='Step '+n+' '+lab+': '+(CM.stateWord(e.state)||e.state)+(e.says?' · '+e.says:''); st=e.state; }
    else if(e.ev==='gate'){ tx=(e.pass===false?'You rejected step ':'You approved step ')+n; st=e.pass===false?'failed':'done'; }
    else if(e.ev==='launch'){ tx='An agent took step '+n+' in '+wfPlaceName(k&&k.s)+(e.attempt>1?' (try '+e.attempt+')':''); st='working'; }
    else if(e.ev==='deferred'){ tx='Step '+n+' waits: '+String(e.why||''); st='waiting'; }
    else if(e.ev==='launch_failed'){ tx='Step '+n+' could not start: '+String(e.why||''); st='failed'; }
    else if(e.ev==='check'){ tx='A second opinion was asked for step '+n; st='working'; }
    else if(e.ev==='stop'){ tx='Stopped'+(e.why?': '+e.why:''); st='stopped'; }
    if(tx) out.push({text:CM.plainText?CM.plainText(tx,110):tx,state:st,t:Number(e.t)>0?Number(e.t)*1000:null}); });
  if(!out.length) (WF.log.get(w.id)||[]).slice(0,3).forEach(x=>out.push({text:x.text,state:x.state,t:x.t}));
  if(!out.length){ const upd=Date.parse(String(w.updated||''));
    steps.slice().reverse().forEach(s=>{ if(out.length>=3) return; const i=steps.indexOf(s)+1, at=wfPlaceName(s);
      if(s.state==='working'||s.state==='needs_you') out.push({text:wfBy(w)+(s.state==='working'?' works on step ':' waits for you at step ')+i+' in '+at,state:s.state,t:Number.isFinite(upd)?upd:null});
      else if(s.state==='done') out.push({text:'Step '+i+' done in '+at+(s.says?': '+s.says:''),state:'done',t:null});
      else if(s.state==='failed') out.push({text:'Step '+i+' failed in '+at+(s.why?': '+s.why:''),state:'failed',t:null}); }); }
  return out; }
function wfCardSync(force){
  const id=CM.selectedWorkflow(), live=wfRawById(id), phone=wfIsPhone();
  if(!live){ if(WF.card.id){ WF.card.id=null; WF.card.sig=''; wfCardClose(); } return; }
  const v=wfById(id)||live, D=WF.detail.get(id);
  const sig=JSON.stringify([phone,v,live.state,D?D.at:0,(WF.log.get(id)||[]).length,!!(WF.replay&&WF.replay.id===id),WF.card.confirm,WF.card.msg,WF.card.stopArm,!!CM.chromeReady]);
  const fresh=WF.card.id!==id||!WF.card.el||!!WF.card.handoff; WF.card.handoff=false; if(!force&&!fresh&&WF.card.sig===sig) return;
  WF.card.id=id; WF.card.sig=sig; WF.card.phone=phone; if(fresh){ WF.card.confirm=null; WF.card.msg=''; WF.card.stopArm=null; }
  if(!WF.card.el) WF.card.el=wfEl('div','wf-card');
  wfCardBuild(WF.card.el,v,live,phone);
  if(fresh||force) wfCardOpen(WF.card.el,live,phone); }
function wfCardOpen(el,w,phone){
  const opts={title:wfTitle(w),back:'Workflows',onBack:()=>CM.selectWorkflow(null)};
  const retire=()=>{ const h=document.getElementById('wfcard'); if(h){ h.hidden=true; h.textContent=''; } const st=document.getElementById('stage');
    if(WF.card.pushed&&st&&st.style){ st.style.right=''; WF.card.pushed=false; } };
  if(CM.chromeReady&&typeof CM.railCard==='function'){ retire(); CM.railCard(el,opts); WF.card.host='rail'; return; }
  if(phone&&typeof CM.sheetCard==='function'){ retire(); CM.sheetCard(el,opts); WF.card.host='sheet'; return; }
  let host=document.getElementById('wfcard');
  if(!host){ host=wfEl('aside','wf-host',null,{id:'wfcard','aria-label':'Workflow',role:'region','data-wall':''}); }
  if(document.body&&host.parentNode!==document.body) document.body.appendChild(host);
  host.classList.toggle('phone',!!phone); if(host.firstChild!==el){ host.textContent=''; host.appendChild(el); } host.hidden=false; WF.card.host='box';
  // the map keeps the room beside it: an old rail already open there (the agents rail) is covered, never left peeking out
  const st=document.getElementById('stage');
  if(st&&st.style&&!phone){ if(!WF.card.pushed){ let r=0; try{ r=parseFloat(getComputedStyle(st).right)||0; }catch(e){} WF.card.width=Math.max(400,Math.round(r)); }
    host.style.width=WF.card.width+'px'; st.style.right=WF.card.width+'px'; WF.card.pushed=true; } }
function wfCardClose(){
  try{ if(WF.card.host==='rail'&&typeof CM.railCard==='function') CM.railCard(null); else if(WF.card.host==='sheet'&&typeof CM.sheetCard==='function') CM.sheetCard(null); }catch(e){}
  const h=document.getElementById('wfcard'); if(h) h.hidden=true; WF.card.host=null;
  const st=document.getElementById('stage'); if(WF.card.pushed&&st&&st.style){ st.style.right=''; WF.card.pushed=false; } }
function wfDetailFetch(id,force){
  const w=wfRawById(id); if(!w||w.derived||!(typeof LIVE!=='undefined'&&LIVE)) return;
  const D=WF.detail.get(id); if(D&&(D.busy||(!force&&Date.now()-D.at<4000))) return;
  WF.detail.set(id,Object.assign({},D||{at:0,events:[]},{busy:true}));
  api('/workflow?id='+encodeURIComponent(id)).then(j=>{ WF.detail.set(id,{at:Date.now(),events:Array.isArray(j.events)?j.events:[],shell:Array.isArray(j.shell_lines)?j.shell_lines:[]}); })
    .catch(e=>{ WF.detail.set(id,{at:Date.now(),events:(D&&D.events)||[],err:String((e&&e.message)||e)}); })
    .finally(()=>{ try{ wfCardSync(false); }catch(e){} }); }

// ---------- the route strip: up to 7 glass slots (planet, label of 14 characters, state word) linked in the left slot's
// state colour, the current one lit; a click shows that planet ----------
function wfStripBuild(v,win){
  const el=wfEl('div','wf-strip',null,{role:'list','aria-label':'Steps of '+wfTitle(v)}), cur=wfCurrent(v);
  win.forEach((x,k)=>{ const s=x.s;
    if(k){ const p=win[k-1].s.state, L=wfEl('i','wf-link st-'+p,null,{'aria-hidden':'true','data-state':p}); el.appendChild(L); }
    const b=wfEl('button','wf-slot st-'+s.state+(s===cur?' cur':''),null,{type:'button',role:'listitem','data-wf-slot':String(s.key),'data-at':wfAnchor(s),
      title:(x.i+1)+'. '+String(s.label||s.key)+' · '+wfPlaceName(s)+' · '+wfStepWord(s)});
    const u=s.planet_uid?wfThumb(s.planet_uid,28):''; if(u){ const im=wfEl('img','wf-th',null,{alt:'',src:u}); b.appendChild(im); } else b.appendChild(wfEl('i','wf-th none'));
    const tx=wfEl('span','wf-sl'); tx.appendChild(wfEl('span','l',wfShort(s.label||s.key,14))); tx.appendChild(wfEl('span','s',wfStepWord(s,'strip'))); b.appendChild(tx);
    if(s.state==='done') b.appendChild(wfEl('i','wf-medal',null,{'aria-label':'done'}));
    el.appendChild(b); });
  return el; }
function wfStripSync(){
  const id=CM.selectedWorkflow(), live=wfRawById(id), phone=wfIsPhone();
  if(!live||phone){ if(WF.strip.on){ WF.strip.on=false; WF.strip.sig=''; WF.strip.el=null; try{ if(CM.chromeReady&&typeof CM.stripSet==='function') CM.stripSet('route',null); }catch(e){} wfStripBox(); } return; }
  const v=wfById(id)||live, win=wfWindow(v), sig=JSON.stringify([id,win.map(x=>[x.s.key,x.s.state,wfStepWord(x.s,'strip'),x.s.planet_uid,x.s.label]),wfCurrent(v)&&wfCurrent(v).key]);
  if(WF.strip.on&&WF.strip.sig===sig&&WF.strip.el) return;
  WF.strip.on=true; WF.strip.sig=sig; WF.strip.el=wfStripBuild(v,win);
  WF.strip.data={slots:win.length,links:win.slice(0,-1).map(x=>String(x.s.state||'')),medals:win.filter(x=>x.s.state==='done').length,
    labels:win.map(x=>wfShort(x.s.label||x.s.key,14)),current:(wfCurrent(v)||{}).key||null};
  if(CM.chromeReady&&typeof CM.stripSet==='function') CM.stripSet('route',WF.strip.el); else wfStripBox(); }
// the fallback status line under the map (until the chrome module's status bar): the route, then a pointer's words
function wfStripBox(){
  if(CM.chromeReady||wfIsPhone()){ const h=document.getElementById('wfstrip'); if(h){ h.hidden=true; h.textContent=''; } return; } const cap=WF.caption&&WF.caption.until>Date.now()?WF.caption.text:'';
  let h=document.getElementById('wfstrip'); if(!WF.strip.on&&!cap){ if(h) h.hidden=true; return; }
  if(!h){ h=wfEl('div','wf-host-strip',null,{id:'wfstrip',role:'status','data-wall':''}); const st=document.getElementById('stage'); if(st) st.appendChild(h); }
  h.textContent=''; if(WF.strip.on&&WF.strip.el) h.appendChild(WF.strip.el); if(cap) h.appendChild(wfEl('span','wf-cap',cap));
  h.hidden=false; }
function wfCaption(text,sec){
  WF.caption={text:String(text),until:Date.now()+sec*1000};
  try{ if(CM.chromeReady&&typeof CM.stripSet==='function') CM.stripSet('readout',WF.caption.text); else wfStripBox(); }catch(e){}
  clearTimeout(WF.capTimer); WF.capTimer=setTimeout(()=>{ WF.caption=null; try{ if(CM.chromeReady&&typeof CM.stripSet==='function') CM.stripSet('readout',null); else wfStripBox(); }catch(e){} },sec*1000); }

function wfCrumbSync(){
  const sel=wfById(CM.selectedWorkflow()), focus=(typeof CM.focusUid==='function'&&CM.focusUid())||null; let el=document.getElementById('wfcrumb'); const sig=sel&&!focus&&!wfIsPhone()?String(sel.id)+'|'+String(sel.title||''):'';
  if(sig===WF.crumbSig&&(!sig||(el&&!el.hidden))) return; WF.crumbSig=sig;
  if(!sig){ if(el) el.hidden=true; return; }
  if(!el){ el=wfEl('nav','wf-crumb',null,{id:'wfcrumb','aria-label':'Where you are','data-wall':''}); const st=document.getElementById('stage'); if(st) st.appendChild(el); }
  el.textContent=''; el.appendChild(wfEl('span','wf-key','Esc')); el.appendChild(wfBtn('All planets','wf-cl',{'data-wf-view':'close'}));
  el.appendChild(wfEl('span','wf-cs','›',{'aria-hidden':'true'})); el.appendChild(wfEl('b',null,'Workflow · '+wfTitle(sel,48))); el.hidden=false; }

// ---------- the finish band (the receipt under the banner) ----------
function wfBandShow(){
  const B=WF.band; if(!B) return; let el=document.getElementById('wfband');
  if(!el){ el=wfEl('div','wf-band',null,{id:'wfband',role:'status','aria-live':'polite'}); const st=document.getElementById('stage'); if(st) st.appendChild(el); }
  el.textContent=''; el.className='wf-band st-'+B.state+(wfIsPhone()?' phone':'');
  const top=wfEl('div','wf-bt'); top.appendChild(wfHex(B.state==='done'?'done':'failed',null,null,26));
  const hd=wfEl('div','wf-bh'); hd.appendChild(wfEl('div','wf-bw',B.state==='done'?'RECEIPT':'STOPPED: A STEP FAILED')); hd.appendChild(wfEl('div','wf-btl',B.title)); top.appendChild(hd);
  top.appendChild(wfBtn('×','x',{'data-wf-view':'band-close','aria-label':'Close'})); el.appendChild(top);
  const rows=wfEl('div','wf-brows'); wfTallyRows(B.tally).forEach((r,i)=>{ const row=wfEl('div','wf-rr'); row.appendChild(wfEl('span',null,r[0])); row.appendChild(wfEl('b',null,r[1],{'data-row':i})); rows.appendChild(row); });
  el.appendChild(rows); const a=wfEl('div','wf-acts'); a.appendChild(wfBtn('Replay','strong',{'data-wf-replay':B.id})); a.appendChild(wfBtn('Open','ghost',{'data-wf-view':'band-open'})); el.appendChild(a);
  el.hidden=false; wfTickArm(); }
function wfBandTick(){
  const B=WF.band; if(!B) return; const lw=wfRawById(B.id); if(Date.now()>B.until||!lw||lw.state!==B.state){ wfBandHide(); return; }
  if(B.settled) return; const el=document.getElementById('wfband'); if(!el) return;
  const last=performance.now()-B.t0>1000||REDUCED;          // the count-up lands within 0.9 s: this write is the final one
  (last?wfTallyRows(Object.assign({},B.tally,{t0:-1e9})):wfTallyRows(B.tally)).forEach((r,i)=>{ const b=el.querySelector('[data-row="'+i+'"]'); if(b&&b.textContent!==r[1]) b.textContent=r[1]; });
  if(last) B.settled=true; }
function wfBandHide(){ WF.band=null; const el=document.getElementById('wfband'); if(el) el.hidden=true; }

// ---------- replay: the card's Replay folds the frames through the live render path in 30 s (×1, ×10, pause, scrub), and
// drives CM.sceneClock so the same replay plays in 3D ----------
const WF_DETAIL=['says','why','progress','output','artifact'];     // workflows.FRAME_DETAIL
function wfDetailOf(s){ const o={}; WF_DETAIL.forEach(k=>{ o[k]=s&&s[k]!==undefined?s[k]:null; }); o.says=o.says||''; return o; }
function wfSummaryFrames(w){      // workflows.summary_frames: each step in order goes working, then to its state
  const t=(Date.parse(String(w.updated||''))||Date.now())/1000, steps=wfSteps(w), out=[]; let seq=0;
  steps.forEach((s,i)=>{ (s.state!=='waiting'?['working',s.state]:['waiting']).forEach(state=>{ seq++;
    out.push(Object.assign({seq:seq,t:t-(steps.length-i)*60,key:s.key,state:state},wfDetailOf(s),{planet_uid:s.planet_uid||null,place:s.place||null,by:wfBy(w)})); }); });
  out.push({seq:seq+1,t:t,key:null,state:String(w.state||''),says:'',planet_uid:null,place:null,by:'map'}); return out; }
// workflows.fold: every step waiting with empty details, then each frame sets its state and the details it carries; the
// workflow's state is its own frames' (until one comes, what its steps say, so a night run's replay reads as working)
function wfFold(w,frames,upto){
  const v=JSON.parse(JSON.stringify(w)), by=new Map(wfSteps(v).map(s=>[s.key,s])); let wst=null;
  wfSteps(v).forEach(s=>{ s.state='waiting'; Object.assign(s,wfDetailOf(null)); });
  for(let i=0;i<upto&&i<frames.length;i++){ const f=frames[i]; if(!f) continue;
    if(f.key==null){ wst=f.state; continue; } const s=by.get(f.key); if(!s) continue;
    s.state=f.state; WF_DETAIL.forEach(k=>{ if(Object.prototype.hasOwnProperty.call(f,k)) s[k]=k==='says'?(f[k]||''):f[k]; }); }
  const S=wfSteps(v).map(s=>s.state);
  v.state=wst||(S.includes('failed')&&!S.includes('working')?'failed':S.includes('needs_you')?'needs_you':S.includes('working')?'working':S.length&&S.every(x=>x==='done'||x==='skipped')?'done':upto?'working':'planned');
  v.current=wfSteps(v).filter(s=>s.state==='working'||s.state==='needs_you').map(s=>s.key); v.done=S.filter(x=>x==='done').length; return v; }
CM.wfReplay=function(id,o){
  o=o||{}; const w=wfRawById(id); if(!w) return false;
  WF.replay={id:id,frames:[],pos:[],src:'summary',i:-1,t:0,dur:30,speed:o.speed===10?10:1,playing:true,last:performance.now(),view:null,endState:null,at:null};
  wfReplayLoad(wfSummaryFrames(w),'summary');
  if(CM.selectedWorkflow()!==id) CM.selectWorkflow(id);
  CM.sceneClock.scale=WF.replay.speed; wfReplayBar();
  if(typeof LIVE!=='undefined'&&LIVE&&!w.derived) wfReplayPages(id).then(all=>{ const R=WF.replay;
    if(R&&R.id===id&&all.length) wfReplayLoad(all,'events'); }).catch(()=>{});
  wfTickArm(); return true; };
// every page of /workflow/replay (2,000 frames a page), in order; 50 pages at most
async function wfReplayPages(id){
  let all=[], page=0, pages=1;
  while(page<pages&&page<50){ const j=await api('/workflow/replay?id='+encodeURIComponent(id)+(page?'&page='+page:''));
    if(!j||!Array.isArray(j.frames)) break; all=all.concat(j.frames); pages=Math.max(1,Number(j.pages)||1); page++; }
  return all; }
function wfReplayLoad(frames,src){
  const R=WF.replay, w=wfRawById(R.id); if(!w) return; R.frames=frames.filter(f=>f&&typeof f==='object').sort((a,b)=>(Number(a.seq)||0)-(Number(b.seq)||0)); R.src=src;
  const n=R.frames.length, t0=Number(R.frames[0]&&R.frames[0].t)||0, span=(Number(R.frames[n-1]&&R.frames[n-1].t)||0)-t0;
  // half by order, half by time: a long wait between two steps reads as one, but bursts do not pile up
  // every frame lands after the start (at 0 nothing has happened yet) and the last at the end
  R.pos=R.frames.map((f,i)=>.04+.96*(.5*(n>1?i/(n-1):1)+.5*(span>0?((Number(f.t)||t0)-t0)/span:(n>1?i/(n-1):1))));
  R.endState=wfFold(w,R.frames,n).state; R.i=-1; wfReplaySeek(R.t); }
function wfReplaySeek(t){
  const R=WF.replay; if(!R) return; R.t=Math.max(0,Math.min(R.dur,t)); const w=wfRawById(R.id); if(!w) return;
  const k=R.pos.filter(p=>p*R.dur<=R.t+1e-9).length; if(k===R.i&&R.view) return;
  const before=R.view, v=wfFold(w,R.frames,k); R.i=k; R.view=v;
  const a=before&&wfRunning(before)&&wfCurrent(before)?wfAnchor(wfCurrent(before)):null, b=wfRunning(v)&&wfCurrent(v)?wfAnchor(wfCurrent(v)):null;
  if(a&&b&&a!==b) wfCrawlStart(R.id,a,b);
  cmSceneTouch(); try{ wfCardSync(false); wfStripSync(); }catch(e){ wfErr(e); } }
function wfReplayAdvance(now){
  const R=WF.replay; if(!R) return; const dt=Math.max(0,Math.min(.25,(now-R.last)/1000)); R.last=now;
  if(!R.playing) return; CM.sceneClock.t=(Number(CM.sceneClock.t)||0)+dt*R.speed;
  wfReplaySeek(R.t+dt*R.speed);
  if(R.t>=R.dur){ R.playing=false; CM.sceneClock.scale=1; wfReplayBar(); } }
function wfReplayPlay(on){ const R=WF.replay; if(!R) return; if(on&&R.t>=R.dur) wfReplaySeek(0); R.playing=!!on; R.last=performance.now(); CM.sceneClock.scale=on?R.speed:0; wfReplayBar(); wfTickArm(); }
function wfReplaySpeed(){ const R=WF.replay; if(!R) return; R.speed=R.speed===10?1:10; if(R.playing) CM.sceneClock.scale=R.speed; wfReplayBar(); }
function wfReplayStop(){ if(!WF.replay) return; WF.replay=null; CM.sceneClock.scale=1; const el=document.getElementById('wfreplay'); if(el) el.hidden=true;
  cmSceneTouch(); try{ wfCardSync(false); wfStripSync(); }catch(e){} }
function wfClock(s){ s=Math.max(0,Math.round(s)); return Math.floor(s/60)+':'+String(s%60).padStart(2,'0'); }
function wfReplayBar(){
  const R=WF.replay; let el=document.getElementById('wfreplay'); if(!R){ if(el) el.hidden=true; return; }
  if(!el){ el=wfEl('div','wf-replay',null,{id:'wfreplay',role:'group','aria-label':'Replay','data-wall':''});
    el.appendChild(wfBtn('','play',{'data-wf-view':'replay-toggle'})); el.appendChild(wfBtn('','speed',{'data-wf-view':'replay-speed'}));
    const r=wfEl('input','wf-scrub',null,{type:'range',min:'0',max:'1000',step:'1','aria-label':'Where in the replay','data-wf-scrub':''}); el.appendChild(r);
    el.appendChild(wfEl('span','wf-rt')); el.appendChild(wfBtn('Back to now','ghost',{'data-wf-view':'replay-close'}));
    const parent=wfIsPhone()?document.body:document.getElementById('stage'); if(parent) parent.appendChild(el); }
  el.classList.toggle('phone',wfIsPhone());
  const p=el.querySelector('[data-wf-view=replay-toggle]'), s=el.querySelector('[data-wf-view=replay-speed]');
  if(p){ p.textContent=R.playing?'Pause':(R.t>=R.dur?'Play again':'Play'); p.setAttribute('aria-pressed',R.playing?'true':'false'); }
  if(s) s.textContent='×'+R.speed; el.hidden=false; wfReplayBarTick(); }
function wfReplayBarTick(){
  const R=WF.replay; if(!R) return; const el=document.getElementById('wfreplay'); if(!el) return;
  const r=el.querySelector('[data-wf-scrub]'), tx=el.querySelector('.wf-rt'), w=wfRawById(R.id);
  if(r&&document.activeElement!==r) r.value=String(Math.round(R.t/R.dur*1000));
  const line='Replay · '+wfTitle(w||{},34)+' · '+wfClock(R.t)+' of '+wfClock(R.dur)+(R.speed===10?' at ×10':''); if(tx&&tx.textContent!==line) tx.textContent=line; }

// ---------- the agents' view requests (control focus / follow) and the owner's setting ----------
function wfViewSetting(){ try{ const v=localStorage.getItem('cm.agentsView'); if(v==='off'||v==='idle'||v==='always') return v; }catch(e){} return 'idle'; }
function wfIdleMs(){ return performance.now()-(typeof lastInput==='number'?lastInput:0); }
function wfConsent(req){
  const set=wfViewSetting(), ok=set==='always'||(set==='idle'&&wfIdleMs()>=60000);
  let who=req.by?String(req.by):'An agent', what='', go=null;   // the bridge's control author (one plain line, <= 24)
  if(req.kind==='focus'){ const uid=wfPlanetOf(req.target)||req.target; what=String(CM.labelText(uid)).replace(/^place:/,'');
    go=()=>{ WF.view.last='focus:'+uid; if(typeof VIEW!=='undefined'&&VIEW!=='map'&&typeof setView==='function') setView('map'); const u=unitByUid(uid); if(u&&typeof activate==='function') activate(u); else wfPoint({target:uid,text:'',seconds:12}); }; }
  else { const w=wfRawById(req.workflow); if(!w) return; who=wfBy(w); what=wfTitle(w,40);
    go=()=>{ WF.view.last='follow:'+w.id; if(typeof VIEW!=='undefined'&&VIEW!=='map'&&typeof setView==='function') setView('map'); CM.selectWorkflow(w.id); }; }
  if(ok){ go(); return; }
  WF.view.last='asked:'+req.kind+':'+(req.target||req.workflow); WF.view.toasts++; wfToast(who+' wants to show you '+what,go); }
function wfToast(text,go){
  let el=document.getElementById('wftoast');
  if(!el){ el=wfEl('div','wf-toast',null,{id:'wftoast',role:'status','aria-live':'polite'}); const st=wfIsPhone()?document.body:document.getElementById('stage'); if(st) st.appendChild(el); }
  el.textContent=''; el.appendChild(wfEl('span','tx',text)); el.appendChild(wfBtn('Show','strong',{'data-wf-view':'toast-show'})); el.appendChild(wfBtn('×','x',{'data-wf-view':'toast-close','aria-label':'Not now'}));
  WF.toastGo=go; el.hidden=false; clearTimeout(WF.toastTimer); WF.toastTimer=setTimeout(()=>{ el.hidden=true; WF.toastGo=null; },15000); }
function wfToastClose(){ const el=document.getElementById('wftoast'); if(el) el.hidden=true; WF.toastGo=null; }
CM.action=function(a){
  if(!a||typeof a!=='object') return false;
  if(a.action==='point'){ if(typeof a.target!=='string'||!a.target) return false; wfPoint(a); return true; }
  if(a.action==='focus'){ if(typeof a.target!=='string'||!a.target) return false; wfConsent({kind:'focus',target:a.target,by:a.by}); return true; }
  if(a.action==='follow'){ if(typeof a.workflow!=='string'||!a.workflow) return false; wfConsent({kind:'follow',workflow:a.workflow}); return true; }
  return false; };

// ---------- the owner's buttons (desktop: the loopback page; a phone answers a gate only) ----------
function wfPost(path,body,btn){
  if(btn) btn.disabled=true; const id=body.id||body.from||CM.selectedWorkflow();
  return api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
    .then(j=>{ WF.card.msg=''; if(j&&j.workflow&&j.workflow.id) wfMergeOne(j.workflow); if(typeof pollTasks==='function') pollTasks(); return j; })
    .catch(e=>{ WF.card.msg=String((e&&e.message)||e); WF.card.msgId=id; try{ wfCardSync(false); }catch(x){} throw e; })
    .finally(()=>{ if(btn) btn.disabled=false; }); }
function wfDoAction(btn){
  const card=btn.closest('[data-wf-card]'), id=(card&&card.getAttribute('data-wf-card'))||CM.selectedWorkflow(), key=btn.getAttribute('data-wf-key'), act=btn.getAttribute('data-wf-action');
  if(!id) return; const quiet=()=>{};
  if(act==='gate-pass'||act==='gate-fail'){ wfPost('/workflow/gate',{id:id,key:key,pass:act==='gate-pass'},btn).catch(quiet); return; }
  if(!wfOwnerPage()) return;           // start, retry, skip, stop and new stay on the computer
  if(act==='retry'||act==='skip'){ wfPost('/workflow/'+act,{id:id,key:key},btn).catch(quiet); return; }
  if(act==='stop'){ if(WF.card.stopArm!==id){ WF.card.stopArm=id; wfCardSync(false); clearTimeout(WF.stopTimer); WF.stopTimer=setTimeout(()=>{ WF.card.stopArm=null; try{ wfCardSync(false); }catch(e){} },4000); return; }
    WF.card.stopArm=null; wfPost('/workflow/stop',{id:id},btn).catch(quiet); return; }
  if(act==='new'){ wfPost('/workflow/new',{from:id},btn).then(j=>{ if(j&&j.id) CM.selectWorkflow(j.id); }).catch(quiet); return; }
  if(act==='start-confirm'){ WF.card.confirm=null; wfPost('/workflow/start',{id:id},btn).catch(quiet); return; }
  if(act==='start'){ btn.disabled=true;      // what Start runs as the user (verify, setup lines) is shown first; none: it starts
    api('/workflow?id='+encodeURIComponent(id)).then(j=>{ const L=Array.isArray(j.shell_lines)?j.shell_lines.filter(Boolean):[];
      if(L.length){ WF.card.confirm={id:id,lines:L.map(x=>typeof x==='string'?x:String(x.line||x.cmd||JSON.stringify(x)))}; wfCardSync(false); return; }
      return wfPost('/workflow/start',{id:id},btn); }).catch(e=>{ WF.card.msg=String((e&&e.message)||e); WF.card.msgId=id; wfCardSync(false); }).finally(()=>{ btn.disabled=false; }); } }
function wfDoView(btn){
  const v=btn.getAttribute('data-wf-view'), card=btn.closest('[data-wf-card]'), id=(card&&card.getAttribute('data-wf-card'))||CM.selectedWorkflow(), w=wfRawById(id);
  if(v==='close') return CM.selectWorkflow(null);
  if(v==='follow3d'&&w){ try{ CM.enter3d({follow:w.id,target:wfAgentAt(w)||undefined}); }catch(e){} return; }
  // "Show on map" (the phone sheet): the sheet steps aside, the planet where it works is shown with its chip
  if(v==='show'&&w){ const at=wfAgentAt(w)||(wfSteps(w)[0]&&wfAnchor(wfSteps(w)[0])); if(wfIsPhone()) CM.selectWorkflow(null);
    if(typeof setView==='function') setView('map'); const u=at&&unitByUid(at); if(u&&typeof activate==='function') activate(u); return; }
  if(v==='replay'&&w) return CM.wfReplay(w.id,{speed:1});
  if(v==='replay-toggle') return wfReplayPlay(!(WF.replay&&WF.replay.playing));
  if(v==='replay-speed') return wfReplaySpeed();
  if(v==='replay-close') return wfReplayStop();
  if(v==='task'){ const tid=btn.getAttribute('data-tid'); if(tid&&typeof openTask==='function'){ if(typeof setView==='function') setView('map'); openTask(tid); } return; }
  if(v==='confirm-cancel'){ WF.card.confirm=null; return wfCardSync(false); }
  if(v==='band-close') return wfBandHide();
  if(v==='band-open'){ const B=WF.band; if(B) CM.selectWorkflow(B.id); return wfBandHide(); }
  if(v==='toast-show'){ const go=WF.toastGo; wfToastClose(); if(go) go(); return; }
  if(v==='toast-close') return wfToastClose();
  if(v==='new-close') return wfNewClose(); }
function wfClick(e){
  const t=e.target; if(!t||!t.closest) return;
  const a=t.closest('[data-wf-action]'); if(a){ e.preventDefault(); return wfDoAction(a); }
  const v=t.closest('[data-wf-view]'); if(v){ e.preventDefault(); return wfDoView(v); }
  const r=t.closest('[data-wf-replay]'); if(r){ e.preventDefault(); return CM.wfReplay(r.getAttribute('data-wf-replay'),{speed:1}); }
  const s=t.closest('[data-wf-slot]'); if(s){ const u=unitByUid(wfPlanetOf(s.getAttribute('data-at'))); if(u&&typeof activate==='function'){ if(typeof humanMove==='function') humanMove(); activate(u); } return; }
  const n=t.closest('[data-wf-new]'); if(n){ e.preventDefault(); return CM.wfNewSheet(n.getAttribute('data-wf-new')); }
  const p=t.closest('[data-wf-tpl]'); if(p){ WF.newTpl=p.getAttribute('data-wf-tpl'); document.querySelectorAll('[data-wf-tpl]').forEach(x=>{ const on=x===p; x.classList.toggle('on',on); x.setAttribute('aria-checked',on?'true':'false'); }); return; }
  if(t.closest('[data-wf-create]')) return wfCreate(t.closest('[data-wf-create]')); }
function wfInput(e){ const r=e.target; if(r&&r.hasAttribute&&r.hasAttribute('data-wf-scrub')&&WF.replay){ WF.replay.playing=false; CM.sceneClock.scale=0; wfReplaySeek(Number(r.value)/1000*WF.replay.dur); wfReplayBar(); } }

// ---------- the owner starts a workflow: "Run a workflow here" (a planet card; desktop only) -> a template, one sentence,
// an agent -> POST /workflow/new -> the planned workflow is selected (ghost route) and its card shows Start ----------
function wfRunners(){ return (Array.isArray(W.task_runners)?W.task_runners:[]).filter(r=>r&&r.name&&r.role!=='check'); }
CM.wfNewSheet=function(uid){
  if(!wfOwnerPage()) return false; const u=unitByUid(uid); if(!u||!u.path) return false;
  let el=document.getElementById('wfnew'); if(!el){ el=wfEl('div','wf-new',null,{id:'wfnew',role:'dialog','aria-modal':'true','aria-label':'Run a workflow','data-wall':''}); const st=document.getElementById('stage'); if(st) st.appendChild(el); }
  el.textContent=''; WF.newAt=uid; const runners=wfRunners();
  const top=wfEl('div','wf-nt'); top.appendChild(wfEl('h3',null,'Run a workflow at '+String(CM.labelText(uid)))); top.appendChild(wfBtn('×','x',{'data-wf-view':'new-close','aria-label':'Close'})); el.appendChild(top);
  if(!runners.length){ el.appendChild(wfEl('div','wf-nconnect','Connect an agent'));
    el.appendChild(wfEl('p','wf-np','No agent is set up on this map yet. Set one up first (? › Set up shows how); a workflow here hands its steps to that agent.'));
    el.appendChild(wfBtn('Close','ghost big',{'data-wf-view':'new-close'})); el.hidden=false; return true; }
  WF.newTpl=WF.newTpl&&WF_TEMPLATES.some(x=>x.name===WF.newTpl)?WF.newTpl:WF_TEMPLATES[0].name;
  const list=wfEl('div','wf-tpls',null,{role:'radiogroup','aria-label':'What kind of workflow'});
  WF_TEMPLATES.forEach(x=>{ const b=wfEl('button','wf-tpl'+(x.name===WF.newTpl?' on':''),null,{type:'button',role:'radio','aria-checked':x.name===WF.newTpl?'true':'false','data-wf-tpl':x.name});
    b.appendChild(wfEl('b',null,x.label)); b.appendChild(wfEl('span',null,x.desc)); b.appendChild(wfEl('i',null,x.steps+' steps')); list.appendChild(b); });
  el.appendChild(list);
  const gl=wfEl('label','wf-fl',null,{for:'wfgoal'}); gl.textContent='What should be true when it is done?'; el.appendChild(gl);
  el.appendChild(wfEl('input','wf-in',null,{id:'wfgoal',type:'text',maxlength:'140',placeholder:'One sentence, e.g. the tests pass on the phone layout',autocomplete:'off',spellcheck:'false'}));
  const rl=wfEl('label','wf-fl',null,{for:'wfrunner'}); rl.textContent='Agent'; el.appendChild(rl);
  const sel=wfEl('select','wf-in',null,{id:'wfrunner'}); runners.forEach(r=>{ const o=wfEl('option',null,String(r.label||r.name),{value:r.name}); sel.appendChild(o); }); el.appendChild(sel);
  el.appendChild(wfEl('p','wf-np','It is planned, not started: its card shows Start, and nothing runs until you press it.'));
  const a=wfEl('div','wf-acts'); a.appendChild(wfBtn('Create','prim big',{'data-wf-create':''})); a.appendChild(wfBtn('Cancel','ghost big',{'data-wf-view':'new-close'})); el.appendChild(a);
  el.appendChild(wfEl('div','wf-msg',''));
  el.hidden=false; const gi=document.getElementById('wfgoal'); if(gi&&gi.focus) gi.focus(); return true; };
function wfNewOpen(){ const el=document.getElementById('wfnew'); return !!el&&!el.hidden; }
function wfNewClose(){ const el=document.getElementById('wfnew'); if(el) el.hidden=true; }
function wfCreate(btn){
  const u=unitByUid(WF.newAt), goal=String((document.getElementById('wfgoal')||{}).value||'').trim(), runner=String((document.getElementById('wfrunner')||{}).value||''), m=document.querySelector('#wfnew .wf-msg');
  if(!u||!u.path) return; if(!goal){ if(m) m.textContent='Say in one sentence what should be true when it is done.'; return; }
  btn.disabled=true;
  api('/workflow/new',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({template:WF.newTpl,at:u.path,goal:goal,runner:runner})})
    .then(j=>{ if(j&&j.workflow) wfMergeOne(j.workflow); wfNewClose(); if(typeof closeAll==='function'&&!CM.chromeReady) closeAll(); if(j&&j.id) CM.selectWorkflow(j.id); if(typeof pollTasks==='function') pollTasks(); })
    .catch(e=>{ if(m) m.textContent=String((e&&e.message)||e); }).finally(()=>{ btn.disabled=false; }); }
// until the chrome module's planet card carries it, the old planet card gets the button (desktop, a planet of this machine)
function wfCardButtonWatch(){
  const host=document.getElementById('pbody'); if(!host||typeof MutationObserver==='undefined') return;
  const add=()=>{ if(CM.chromeReady||wfIsPhone()) return; const u=typeof openUnit!=='undefined'?openUnit:null;
    if(!u||u.kind!=='area'||u.remote||!u.path||host.querySelector('[data-wf-new]')) return;
    const row=wfEl('div','wf-cardrow'); row.appendChild(wfBtn('Run a workflow here','strong',{'data-wf-new':u.uid})); host.insertBefore(row,host.firstChild); };
  new MutationObserver(add).observe(host,{childList:true}); add(); }

// ---------- Esc / Back (pri 10: under the 3D view and the phone sheet), idle, start-up ----------
function wfBack(how){
  const has=wfNewOpen()||!!WF.replay||!!CM._wfSel;
  if(how==='has') return has;
  if(wfNewOpen()){ wfNewClose(); return true; }
  if(WF.replay){ wfReplayStop(); return true; }
  if(CM._wfSel){ CM.selectWorkflow(null); return true; }
  return false; }
// the presentation view ends by itself after 5 min with no input
function wfIdle(){ if(CM._wfSel&&!WF.replay&&wfIdleMs()>300000&&Date.now()-WF.selAt>300000) CM.selectWorkflow(null); }
CM.onInit.push(()=>{
  WF.ui=true;
  CM.settingsExtra.push({key:'agents-move-view',group:'Workflows',label:'Agents can move my view',
    options:[{value:'off',label:'Off'},{value:'idle',label:'When idle (no input for a minute)'},{value:'always',label:'Always'}],
    get:()=>wfViewSetting(),set:v=>{ try{ localStorage.setItem('cm.agentsView',['off','idle','always'].includes(v)?v:'idle'); }catch(e){} }});
  CM.backLayers.push({pri:10,fn:wfBack});
  document.addEventListener('click',wfClick); document.addEventListener('input',wfInput);
  addEventListener('visibilitychange',()=>{ if(!document.hidden) wfPollArm(); });
  try{ wfCardButtonWatch(); }catch(e){}
  wfIngest(); wfPollArm(); });

// ---------- what a test, or a curious console, reads back ----------
function wfTallyNowOf(){
  if(WF.band) return WF.band.tally; const sel=wfRawById(CM.selectedWorkflow()); if(sel&&WF_FINISHED.includes(sel.state)) return wfTally(sel);
  const fin=wfRaw().filter(w=>w&&WF_FINISHED.includes(w.state)).sort((a,b)=>String(b.updated||'').localeCompare(String(a.updated||'')))[0];
  return fin?wfTally(fin):null; }
function wfStripAudit(){
  const el=WF.strip.on?WF.strip.el:null; if(!el) return {slots:0,links:[],medals:0,labels:[],current:null};
  // read back from the slots on the page; a page without a real DOM (the stub) says what was built
  if(typeof NodeList==='undefined'||!(el.querySelectorAll('.wf-slot') instanceof NodeList)) return Object.assign({host:'data'},WF.strip.data);
  const slots=Array.from(el.querySelectorAll('.wf-slot'));
  return {slots:slots.length,links:Array.from(el.querySelectorAll('.wf-link')).map(x=>x.getAttribute('data-state')),medals:el.querySelectorAll('.wf-slot .wf-medal').length,
    labels:slots.map(s=>{ const l=s.querySelector('.l'); return l?l.textContent:''; }),current:(slots.find(s=>s.classList.contains('cur'))||{getAttribute:()=>null}).getAttribute('data-wf-slot'),
    host:CM.chromeReady?'status bar':'fallback'}; }
(typeof window!=='undefined'?window:globalThis).__wfAudit=function(){
  const F=WF.frame, selId=CM.selectedWorkflow(), sel=wfRawById(selId), R=WF.replay;
  const run=sel&&(wfRunning(sel)||WF.crawl.has(sel.id))?sel:wfRaw().find(w=>w&&(wfRunning(w)||WF.crawl.has(w.id)))||null;
  const c=run?wfCrawlLive(run.id):null, chips={}; MOONS.forEach(m=>{ const x=wfChipOf(m.uid); if(x) chips[m.uid]=x.text; }); { const x=wfChipOf(wfSunUid()); if(x) chips[wfSunUid()]=x.text; }
  const T=wfTallyNowOf();
  return {mode:F.mode,overview:Object.assign({},WF.overview),chips:chips,ribbons:F.ribbons,gates:F.gates,tags:F.tags,dimmed:F.dimmed,agents:F.agents,
    strip:wfStripAudit(),radio:{lines:WF.radio.lines.slice(0,6),last:WF.radio.lines[0]||null},band:WF.band?WF.band.state:null,tally:T,
    replay:R?{active:true,id:R.id,frames:R.frames.length,i:R.i,t:Math.round(R.t*100)/100,dur:R.dur,playing:R.playing,speed:R.speed,scale:CM.sceneClock.scale,endState:R.endState,src:R.src,state:R.view?R.view.state:null}:null,
    state:sel?String(sel.state||''):null,selected:sel?{id:sel.id,title:String(sel.title||''),state:String(sel.state||''),done:sel.done,total:sel.total}:null,
    shows:{drawn:F.shows.drawn,cards:F.shows.cards,charts:F.shows.charts,items:F.shows.items.slice()},medals:wfMedals(),
    crawl:{active:!!c,id:c?c.id:null,from:c?c.from:null,to:c?c.to:null},agentAt:run?wfAgentAt(wfById(run.id)||run):null,
    points:WF.points.filter(p=>p.until>Date.now()).map(p=>({target:p.target,text:p.text,left_s:Math.round((p.until-Date.now())/1000)})),caption:WF.caption&&WF.caption.until>Date.now()?WF.caption.text:'',
    view:{setting:wfViewSetting(),idle_s:Math.round(wfIdleMs()/1000),last:WF.view.last,toasts:WF.view.toasts,toast:!!document.getElementById('wftoast')&&!document.getElementById('wftoast').hidden},
    poll:{on:WF.poll.on,n:WF.poll.n,ms:wfPollMs(),since:WF.poll.since,err:WF.poll.err},
    card:{open:!!WF.card.id,id:WF.card.id,host:WF.card.host,phone:WF.card.phone},err:WF.errLogged}; };
