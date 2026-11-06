// ui/focus.js — the overview and the focus view: CM.focusUid, CM.inFocus, CM.detailHidden, FOCUS_CAM, named edge arrows,
// window.__mapAudit.
// Inlined into the page's main script right after the CM registry (render-game.py, the seams): declare functions and
// assign into CM only, nothing that runs page code at load (start-up goes in CM.onInit). Real backslashes here: this
// file is read as it is, not through a Python string. Neutral words only.
//
//. The OVERVIEW draws only the planets (purpose bodies, a name, one badge), the sun ("This machine"), the agents at
// work at their planets and the idle ones stacked at the carrier. A planet's sub-folders, its problem craft and what waits
// there appear when the owner opens it: the FOCUS view. Focus follows the open card (click, tap, Enter or E, a For you row,
// a workflow step, the 3D lock all open one); Esc closes it (closeAll) and the camera eases back to the view it left.
// In focus the planet sits in the middle of the free map at ~45% of its height (FOCUS_CAM, 0.8 s); its sub-folders are
// stations on the side away from the sun (12 at most, "+N more"); its problems ride one outer orbit, the oldest nearest the
// sun (8 at most, "+N"; research/source-tcrf.md 1.8.7: deal with the threats closest to home first, so the most urgent faces home);
// what waits on the owner rides the same orbit after them; its agents sit on its working ring, named. Each craft carries a
// 2-4 word tag. Every other planet drops to 35%, name hidden, badge kept; an off-screen body gets a named edge arrow.
// Detail fades in and out over 0.3 s, and the switch uses hysteresis (cosmosjourneyer clusteredLightingSystem.ts:31: on at
// 0.5% of the screen, off at 0.4%), so a zoom that wobbles around the threshold never flickers.
const FOCUS_CAM={uid:null,phase:'off',t0:0,dur:800,za:1,sa:null,home:null,left:null,set:null,zt:0};
const FC={
  ON:.005, OFF:.004, FADE:300, MAXZ:8, FLOOR:{desk:12,phone:17}, SUNFLOOR:{desk:22,phone:20}, SCALE:1.8, scaleNow:1.8, floorNow:null,
  toggles:0, gate:new Map(), alpha:new Map(), alphaT:-1,
  pos:new Map(),          // uid -> {x, y, r}: where the focus view drew a station or a craft (world units), for pickAt
  mine:new Set(),         // ships and threads the focus view draws itself at its planet (the units loop skips them)
  stack:new Set(),        // idle agents stacked at the carrier on the overview (the units loop skips them)
  view:null,              // the last overview camera {z, cx, cy}: the view Esc comes back to
  drawn:null, audit:null, planets:[], foes:[], waiting:[], stations:[], agents:[], arrows:[], free:null, freeAt:-1e9, freeSig:'',
  lb:[],                  // this frame's label requests (LABEL.place runs in runPlates)
  lbOut:null,             // the last frame's placed labels
};
// ---- the predicate (understand/u1-map.md §8) ----
CM.focusUid=function(){
  let u=null;
  try{ u=(typeof composerOpen==='function'&&composerOpen()&&AIM)?unitByUid(AIM):openUnit; }catch(e){ u=null; }
  if(!u) return null;
  if(typeof BOARD!=='undefined'&&u===BOARD) return BOARD.uid;
  if(u.kind==='task'){ const v=unitByUid(u.uid); if(v) u=v; }                 // openTask's stub carries no docked_at
  if(u.kind==='area') return u.uid;
  return u.area||u.docked_at||null;
};
CM.inFocus=function(m){ if(!m) return false; const f=CM.focusUid(); return !!f&&(f===m.uid||(m.uid==='sky:star'&&f===BOARD.uid)); };
// a unit the units loop does not draw: moons (the focus view draws its own stations), jobs and processes (they live in the
// card), the craft the focus view draws itself, another planet's craft while one planet is open, the idle agents stacked at the
// carrier, and (the quiet map) the hangar's berths, the relay beacons, the gates and the runners
CM.detailHidden=function(u){
  if(!u) return false;
  const k=u.kind, home=typeof deck==='undefined'||deck==='home';
  if(k==='dir') return home;
  if(k==='cron'||k==='proc') return true;
  if(k==='seat'||k==='ship'||k==='task'){
    if(FC.mine.has(u.uid)) return true;
    const f=CM.focusUid(); if(f&&u.docked_at&&u.docked_at!==f) return true;
    return FC.stack.has(u.uid); }
  if(k==='channel'||k==='portal'||k==='runner'||(typeof isBerth==='function'&&isBerth(u))) return !!(typeof QUIET!=='undefined'&&QUIET);
  return false;
};
// the planet in focus on the home map, else null (the sun's focus moves no camera: its card is the decision board)
function fcArea(){ if(typeof deck!=='undefined'&&deck!=='home') return null; if(typeof VIEW!=='undefined'&&VIEW!=='map') return null;
  const f=CM.focusUid(); return f&&f!==BOARD.uid?(MOONS.find(m=>m.uid===f)||null):null; }
function fcPhone(){ return typeof MOB!=='undefined'&&!!MOB; }
// the size a planet is drawn at: its size on disk (layoutSystem's r, cube root of the bytes) half again, so a body reads at
// the overview's zoom as the comps draw it (the ring keeps its room: layoutSystem spaces the planets by their room rings),
// and never under 24 px across (34 on a phone;.1 asks 18 and 28 at least); r and the result in world units
// (the home view draws them at their plain size when the map is too crowded for that: FC.scaleNow)
function fcDispR(m){ const fl=FC.floorNow||(fcPhone()?FC.FLOOR.phone:FC.FLOOR.desk); return Math.max((Number(m&&m.r)||0)*FC.scaleNow,fl/Math.max(ZOOM,1e-6)); }
function fcSunR(){ const fl=fcPhone()?FC.SUNFLOOR.phone:FC.SUNFLOOR.desk; return Math.max(LY.star.r,fl/Math.max(ZOOM,1e-6)); }
function fcScr(x,y){ return {x:(x-camx)*ZOOM,y:(y-camy)*ZOOM}; }
function fcMapW(){ return VW-PANEL_W; }
function fcMapH(){ return VH-((typeof plateSheet==='function')?plateSheet():0); }
// ---- the free map: the chrome module's measure when its chrome is in, else the stage minus the walls ----
// the walls: what homeCovers() lists (the cards over the stage and every [data-wall] box); the free rect is the largest
// rectangle of the map that touches none of them (cached, a quarter second)
function fcFree(){
  const now=(typeof performance!=='undefined'&&performance.now)?performance.now():Date.now();
  if(FC.free&&Math.abs(now-FC.freeAt)<250&&FC.freeSig===VW+'x'+VH+'x'+PANEL_W+'x'+fcMapH()) return FC.free;
  let F=null;
  // CM.mapFree, not __chromeAudit(): the audit also reads every element's contrast, seconds a call under a long thread log
  try{ if(CM.chromeReady&&typeof CM.mapFree==='function'){ const f=CM.mapFree();
      if(f&&f.w>0&&f.h>0){ let ox=0, oy=0; try{ const sr=STAGE&&STAGE.getBoundingClientRect(); if(sr&&Number.isFinite(sr.left)){ ox=sr.left; oy=sr.top; } }catch(e){}
        F={x:Math.max(0,f.x-ox),y:Math.max(0,f.y-oy),w:Math.min(fcMapW(),f.w),h:Math.min(fcMapH(),f.h),from:'chrome'}; } } }catch(e){ F=null; }
  if(!F) F=fcLargest(fcCovers(),fcMapW(),fcMapH());
  FC.free=F; FC.freeAt=now; FC.freeSig=VW+'x'+VH+'x'+PANEL_W+'x'+fcMapH(); return F;
}
function fcMapArea(){
  try{ if(CM.chromeReady&&typeof CM.mapFree==='function'){ const F=fcFree(); if(F.from==='chrome') return F; } }catch(e){}
  return {x:0,y:0,w:fcMapW(),h:fcMapH(),from:'stage'};
}
function fcCovers(){ let c=[]; try{ c=(typeof homeCovers==='function'?homeCovers():[]).filter(b=>b&&b.w>0&&b.h>0&&b.id!=='fcrumb'); }catch(e){ c=[]; } return c; }   // the crumb chip sits in the free map's corner: never a wall
function fcLargest(boxes,W0,H0){
  const bs=boxes.map(b=>({x:Math.max(0,b.x),y:Math.max(0,b.y),x1:Math.min(W0,b.x+b.w),y1:Math.min(H0,b.y+b.h)})).filter(b=>b.x1>b.x&&b.y1>b.y);
  if(!bs.length||!(W0>0&&H0>0)) return {x:0,y:0,w:Math.max(0,W0),h:Math.max(0,H0),from:'stage'};
  const xs=[0,W0], ys=[0,H0]; bs.forEach(b=>{ xs.push(b.x,b.x1); ys.push(b.y,b.y1); });
  const X=[...new Set(xs)].sort((a,b)=>a-b), Y=[...new Set(ys)].sort((a,b)=>a-b);
  let best=null;
  for(let i=0;i<X.length;i++) for(let j=i+1;j<X.length;j++){ const x0=X[i], x1=X[j];
    const cut=bs.filter(b=>b.x<x1&&b.x1>x0);
    for(let k=0;k<Y.length;k++) for(let l=k+1;l<Y.length;l++){ const y0=Y[k], y1=Y[l], a=(x1-x0)*(y1-y0); if(best&&a<=best.a) continue;
      if(cut.some(b=>b.y<y1&&b.y1>y0)) continue; best={a:a,x:x0,y:y0,w:x1-x0,h:y1-y0}; } }
  return best?{x:best.x,y:best.y,w:best.w,h:best.h,from:'stage'}:{x:0,y:0,w:W0,h:H0,from:'stage'};
}
// ---- the camera ----
function fcViewNow(){ return {z:ZOOM,cx:px+camOff.x,cy:py+camOff.y}; }
// set ZOOM and camOff so the world point (wx, wy) lands on the screen point (sx, sy) of the map (draw()'s camx/camy, unrounded)
function fcPut(wx,wy,sx,sy,z){
  ZOOM=z; camOff.x=wx-sx/z-px+fcMapW()/2/z; camOff.y=wy-sy/z-py+(VH-(typeof SHEET_CUR!=='undefined'?SHEET_CUR:0))/2/z;
  FOCUS_CAM.set={z:ZOOM,ox:camOff.x,oy:camOff.y};
}
// where a planet sits on the screen for a camera {z, cx, cy} (cx, cy: the world point at the map's centre)
function fcAt(m,v){ return {x:fcMapW()/2+(m.x-v.cx)*v.z,y:(VH-(typeof SHEET_CUR!=='undefined'?SHEET_CUR:0))/2+(m.y-v.cy)*v.z}; }
// the focus target: the zoom that makes the planet ~45% of the free map's height (60% of its width at most) and its centre
function fcTarget(m){
  const F=fcFree(), d=Math.min(.45*F.h,.6*F.w), z=Math.max(.2,Math.min(FC.MAXZ,d/(2*Math.max(1,(Number(m.r)||1)*FC.scaleNow))));
  return {z:z,sx:F.x+F.w/2,sy:F.y+F.h/2};
}
function fcEase(k){ return 1-Math.pow(1-Math.max(0,Math.min(1,k)),3); }
// each frame, before draw() reads the camera
function fcCamera(t){
  const C=FOCUS_CAM, m=fcArea();
  if(!(fcMapW()>0&&fcMapH()>0)){ C.uid=m?m.uid:null; C.phase='off'; C.set=null; return; }   // no stage measured yet: nothing to aim at
  // someone else moved the camera since this wrote it (the wheel, a drag, C, a key that flies): the focus camera lets go,
  // the card and the focus stay; Esc still comes back to the overview
  if(C.set&&(ZOOM!==C.set.z||camOff.x!==C.set.ox||camOff.y!==C.set.oy)){
    if(C.phase==='in'||C.phase==='hold') C.phase='user'; else if(C.phase==='out'){ C.phase='off'; C.home=null; } }
  C.set=null;
  if(m&&m.uid!==C.uid){
    if(C.phase==='off'||!C.home) C.home=FC.view?Object.assign({},FC.view):fcViewNow();
    C.uid=m.uid; C.phase='in'; C.t0=t; C.dur=REDUCED?0:800; C.za=ZOOM; C.sa=fcScr(m.x,m.y);
    LABEL.mem.forEach((v,key)=>{ if(/^dir:/.test(String(key))) LABEL.mem.delete(key); });
  } else if(!m&&C.uid){
    const was=MOONS.find(x=>x.uid===C.uid); C.uid=null;
    if(C.home&&was){ C.phase='out'; C.t0=t; C.dur=REDUCED?0:400; C.za=ZOOM; C.sa=fcScr(was.x,was.y); C.left=was; }
    else { C.phase='off'; C.home=null; }
  }
  if(m&&(C.phase==='in'||C.phase==='hold')){
    const g=fcTarget(m); C.zt=g.z;
    let busy=C.phase!=='hold';
    if(C.phase==='hold'){ const k=REDUCED?1:.25;   // the free map changed (a card opened): follow it softly
      const s=fcScr(m.x,m.y), z=ZOOM*Math.pow(g.z/ZOOM,k); busy=Math.abs(g.sx-s.x)+Math.abs(g.sy-s.y)>1||Math.abs(g.z/ZOOM-1)>.002; fcPut(m.x,m.y,s.x+(g.sx-s.x)*k,s.y+(g.sy-s.y)*k,z); }
    else { const k=C.dur>0?(t-C.t0)/C.dur:1, e=fcEase(k);
      fcPut(m.x,m.y,C.sa.x+(g.sx-C.sa.x)*e,C.sa.y+(g.sy-C.sa.y)*e,C.za*Math.pow(g.z/C.za,e)); if(k>=1) C.phase='hold'; }
    if(busy&&typeof wantFrame==='function') lastInput=Math.max(lastInput||0,performance.now());   // the ease runs at the full frame rate; a settled hold is idle
  } else if(C.phase==='out'&&C.left){
    const H=C.home, s1=fcAt(C.left,H), k=C.dur>0?(t-C.t0)/C.dur:1, e=fcEase(k);
    if(k>=1){ ZOOM=H.z; camOff.x=H.cx-px; camOff.y=H.cy-py; C.set={z:ZOOM,ox:camOff.x,oy:camOff.y}; C.phase='off'; C.home=null; C.left=null; }
    else { fcPut(C.left.x,C.left.y,C.sa.x+(s1.x-C.sa.x)*e,C.sa.y+(s1.y-C.sa.y)*e,C.za*Math.pow(H.z/C.za,e)); lastInput=Math.max(lastInput||0,performance.now()); }
  }
  if(!m&&C.phase==='off') FC.view=fcViewNow();
}
// a wheel turn while a planet is open zooms past the overview's 2 (the focus zoom of a small planet is higher)
function fcZoomMax(){ return (FOCUS_CAM.uid||FOCUS_CAM.phase==='user')?Math.max(2,FC.MAXZ):2; }
// ---- detail gates: hysteresis on the share of the map a body covers ----
function fcGate(key,frac){
  const was=FC.gate.get(key)===true, on=was?frac>=FC.OFF:frac>=FC.ON;
  if(on!==was){ FC.toggles++; FC.gate.set(key,on); }
  return on;
}
// the detail of planet m: open (focus) and big enough on the screen; its alpha fades over 0.3 s
function fcDetail(m,t){
  const R=fcDispR(m)*ZOOM, A=fcMapW()*fcMapH(), frac=A>0?Math.PI*R*R/A:1, on=fcGate('detail:'+m.uid,frac)&&CM.inFocus(m);   // no stage measured: no size to judge by
  let a=FC.alpha.get(m.uid); if(a==null) a=0;
  const dt=FC.alphaT<0?16:Math.max(0,Math.min(100,t-FC.alphaT));
  a=REDUCED?(on?1:0):Math.max(0,Math.min(1,a+(on?1:-1)*dt/FC.FADE));
  FC.alpha.set(m.uid,a); return a;
}
// ---- one frame's books ----
function fcFrameStart(t){
  FC.drawn={moons:0,foes:0,wedges:0,rings:0,ribbons:0,machines:0,waiting:0,agents:0,stations:0,working:{}};
  FC.lb=[]; FC.planets=[]; FC.foes=[]; FC.waiting=[]; FC.stations=[]; FC.agents=[]; FC.arrows=[]; FC.pos.clear(); FC.mine.clear();
  FC.route=null;   // a selected workflow: the planets its steps are at; every other name drops to the dimmed style
  try{ const id=CM.selectedWorkflow(); if(id){ const w=CM.wfList().find(x=>x&&x.id===id); if(w) FC.route=new Set((Array.isArray(w.steps)?w.steps:[]).map(st=>st&&st.planet_uid).filter(Boolean)); } }catch(e){ FC.route=null; }
  fcCamera(t);
  fcStack();
}
function fcFrameEnd(t){
  try{ const W0=window.__wfAudit&&window.__wfAudit(); const o=W0&&W0.overview; if(o&&Number.isFinite(o.ribbons)) FC.drawn.ribbons=o.ribbons; }catch(e){}
  FC.alphaT=t;
  FC.audit={drawn:FC.drawn,planets:FC.planets.slice(),foes:FC.foes.slice(),waiting:FC.waiting.slice(),stations:FC.stations.slice(),agents:FC.agents.slice(),arrows:FC.arrows.slice()};
}
function fcIdle(u){ const s=String(u.status||''); return isShip(u)&&!u.remote&&u!==mothership()&&(s==='IDLE'||s==='idle'||s==='BLANK'); }
// the idle agents the overview stacks at the carrier (never those of the open planet: the focus view names them there)
function fcStack(){
  FC.stack.clear(); if(typeof deck!=='undefined'&&deck!=='home') return;
  const f=CM.focusUid(); units.forEach(u=>{ if(fcIdle(u)&&!(f&&u.docked_at===f)) FC.stack.add(u.uid); });
}
// ---- the overview's planet: its label request and its books (drawMoon calls this) ----
function fcPlanetCounts(m){
  let n=0, broken=false;
  try{ const F=kindsPer(), c=F.per[m.uid]; if(c){ n=(c.need_you||0)+(c.problems||0)+(c.decisions||0); broken=(c.problems||0)>0; } }catch(e){}
  if(!n&&m.enemies){ n=m.enemies; broken=true; }
  return {n:n,broken:broken};
}
function fcPlanet(m,R,t){
  const f=CM.focusUid(), mine=f===m.uid, other=!!f&&!mine, s=fcScr(m.x,m.y), r=R*ZOOM, c=fcPlanetCounts(m);
  const chip=(()=>{ try{ return String(CM.labelChip(m.uid)||''); }catch(e){ return ''; } })();
  const L={key:'tag:'+m.uid,uid:m.uid,x:m.x,y:m.y,r:R,text:CM.labelText(m.uid),badge:LABEL.badgeOf(c.n,c.broken),chip:chip||'',
    pri:(typeof plateHot==='function'&&plateHot(m.uid)?1e9:0)+(c.n?1e6:0)+m.r,kind:'planet',px:mine?22:LABEL.px()};   // pointed at: first pick (a folded name comes back)
  if(other){ L.pip=true; L.alpha=1; L.chip=''; }
  else if(FC.route&&!FC.route.has(m.uid)){ L.alpha=.4; L.chip=''; L.pri-=1e6; L.dim=true; }   // off the selected workflow's route
  FC.lb.push(L);
  let work=0;
  try{ const W1=CM.workingPlanets(); if(W1&&W1.has(m.uid)) work++; }catch(e){}
  units.forEach(u=>{ if(isShip(u)&&!u.remote&&u.docked_at===m.uid&&/^working$/i.test(String(u.status||''))&&!FC.stack.has(u.uid)&&!skyHidden(u)) work++; });
  if(!f&&work) FC.drawn.working[m.uid]=work;
  FC.planets.push({uid:m.uid,x:s.x,y:s.y,r:r,other:other,mine:mine});
}
// the open planet's rim glow (the comps' focus: a soft halo to 1.35r in red when something there is broken, amber when it waits)
function fcRim(m,R,t){
  const c=fcPlanetCounts(m); if(!c.n||GB) return;
  const col=c.broken?'255,90,95':'255,181,71', g=cx.createRadialGradient(m.x,m.y,R*.92,m.x,m.y,R*1.38);
  g.addColorStop(0,'rgba('+col+',.30)'); g.addColorStop(.45,'rgba('+col+',.10)'); g.addColorStop(1,'rgba('+col+',0)');
  cx.save(); cx.fillStyle=g; cx.beginPath(); cx.arc(m.x,m.y,R*1.38,0,Math.PI*2); cx.fill(); cx.restore();
}
// ---- the overview's idle agents: a V of parked darts off the carrier's bow and "N idle" under it ----
function fcIdleStack(t){
  const n=FC.stack.size; if(!n||CM.focusUid()) return;
  const M=mothership(), k=1/ZOOM;
  let x, y, L;
  if(M&&!M.remote){ x=M.gx*TILE; y=M.gy*TILE-6; L=Math.max(72,2*craftHull('carrier',SPRITE_CELL,craftScale(CARRIER_BASE))*ZOOM); }
  else { x=LY.star.x; y=LY.star.y-LY.dock; L=72; }
  // a V off the carrier's bow (it flies nose right), clear of its letter tag; with no carrier, the V itself marks the dock
  const s=fcScr(x,y), V=M&&!M.remote?[[.34,-.5],[.56,-.66],[.56,-.34]]:[[.12,0],[-.12,-.17],[-.12,.17]];
  cx.save(); cx.setTransform(DPR,0,0,DPR,0,0);
  for(let i=0;i<Math.min(n,3);i++) fcDart(cx,s.x+V[i][0]*L,s.y+V[i][1]*L,L*.26,0,{engine:.12});
  cx.restore();
  if(M&&!M.remote) plateHull(x+.48*L*k,y-.5*L*k,.25*L*k);
  else { plateHull(x,y,.3*L*k); queuePlate({key:'idle',kind:'carrier',pri:PLATE_PRI.carrier,x:x,y:y,r:.3*L*k,face:'s',segs:[{t:n+' idle',col:'#BFD0E6'}]}); }
}
// ---- the focus view of one planet (drawBattle calls this for the open planet, the sun's planetless problems too) ----
const FC_TAGW={red:'#FF5A5F',amber:'#FFB547',cyan:'#6FB7FF',grey:'#9AA8BC'};
function fcAge(f){
  const c=f&&f.contact; if(c&&c.age_days!=null&&Number.isFinite(Number(c.age_days))) return Number(c.age_days);
  const u=f&&f.uid?unitByUid(f.uid):null;
  if(u&&typeof u.receipt_age==='number') return u.receipt_age/86400;
  if(u&&u.kind==='task'){ const t=(agentTasks()||[]).find(x=>'task:'+x.id===u.uid); const e=t&&Date.parse(t.ended||t.started||''); if(Number.isFinite(e)) return Math.max(0,(Date.now()-e)/864e5); }
  return 0;
}
// the first few words of a title, never ending on a little word ("split the importer", not "split the")
const FC_SMALL=new Set(['the','a','an','to','of','and','in','for','on','with','at','by','from','into','is','it','its','so','that','this']);
function fcWords(s,n){ const w=String(s||'').replace(/^(goal|task|job)\s*:\s*/i,'').replace(/[.,;:!?]+$/,'').split(/\s+/).filter(Boolean).slice(0,n);
  while(w.length>1&&FC_SMALL.has(w[w.length-1].toLowerCase())) w.pop(); return w.join(' '); }
function fcShort(s,max){ s=String(s||''); return s.length>max?s.slice(0,max-1).trimEnd()+'…':s; }
// a problem: its craft (drone: a job; raider: a task, an agent or a step that failed; rock: a big file) and its 2-4 word tag
function fcFoeLook(f){
  const c=f.contact, src=String((c&&c.source)||f.kind||''), text=String((c&&(c.title||c.voice))||f.full||f.name||'');
  const age=fcAge(f), aw=age>=1?' · '+CM.ageWords(age):'';
  if(src==='cron'||f.kind==='cron'){ const u=unitByUid(f.uid), nm=fcWords(String((u&&u.name)||(c&&c.title)||f.name||'job').replace(/^job\s+/i,'').split(':')[0].split('/').pop(),1)||'job';
    const v=u&&u.verdict?CM.verdictWord(u.verdict):(c?CM.word('contact:'+c.state):'late');
    return {kind:'drone',tag:fcShort(nm,14)+' '+(v==='contact:'+(c&&c.state)?'late':v)+aw,age:age}; }
  if(f.kind==='wreck'||src==='task'||src==='workflow'||src==='night') return {kind:'raider',tag:(src==='workflow'?'step failed':'task failed')+aw,age:age};
  if(src==='t3'||src==='seat') return {kind:'raider',tag:'agent stopped'+aw,age:age};
  if(/\b\d+(\.\d+)?\s*[kmgt]i?b?\b|big file|large file/i.test(text)) return {kind:'rock',tag:'big file'+aw,age:age};
  return {kind:'drone',tag:fcShort(fcWords(CM.shortTitle?CM.shortTitle(text,40,false):text,3),22)+aw,age:age};
}
function fcWaitLook(f){
  const c=f.contact||{}, b=CM.bucket(c), age=fcAge(f), title=fcShort(fcWords(CM.shortTitle?CM.shortTitle(String(c.title||c.voice||''),40,false):c.title,3),20);
  if(b==='decision') return {kind:'buoy',tag:(title?title+' · ':'')+'decision',age:age,col:FC_TAGW.amber};
  if(b==='answer') return {kind:'buoy',tag:(title?title+' · ':'')+'asks you',age:age,col:FC_TAGW.amber};
  return {kind:'cargo',tag:(title?title+' · ':'')+'to review',age:age,col:FC_TAGW.amber};
}
// sub-folders shown as stations: dot-folders never; the ones that hold a problem first, then the biggest
function fcRooms(m){
  const bad=new Set(units.filter(c=>c.kind==='cron'&&c.area===m.uid&&BAD_VERDICTS.includes(c.verdict)).map(c=>c.room));
  return (m.rooms||[]).filter(r=>r&&!r.hidden&&!/^\./.test(String(r.name||''))).map(r=>({r:r,bad:bad.has(r.name)}))
    .sort((a,b)=>(b.bad?1:0)-(a.bad?1:0)||(Number(b.r.files)||0)-(Number(a.r.files)||0));
}
function drawFocusView(m,t){
  // everything here is laid out in screen pixels around the planet and drawn in world units at that size (the world
  // transform scaled by 1/ZOOM about each thing), so nothing needs the camera's corner
  const sun=m.uid==='sky:star', R=(sun?fcSunR():fcDispR(m))*ZOOM, a=sun?(CM.inFocus(m)?1:0):fcDetail(m,t);
  const toSun=sun?-Math.PI/2:Math.atan2(LY.star.y-m.y,LY.star.x-m.x), open=toSun+Math.PI, k=1/ZOOM;
  const W0=(dx,dy)=>({x:m.x+dx*k,y:m.y+dy*k}), scr=w=>({x:(w.x-camx)*ZOOM,y:(w.y-camy)*ZOOM});
  const at=(w,fn)=>{ cx.save(); cx.translate(w.x,w.y); cx.scale(k,k); fn(); cx.restore(); };
  const problems=[], waiting=[];
  (m.foes||[]).forEach(f=>{ if(f.kind==='contact'&&f.contact&&CM.bucket(f.contact)!=='problem') waiting.push(f); else problems.push(f); });
  // the ships and the threads at this planet: agents on the working ring, a thread to review is parked cargo
  const here=units.filter(u=>(isShip(u)||u.kind==='task')&&!u.remote&&u.docked_at===m.uid);
  const reviewIds=new Set(waiting.map(f=>String(f.uid)));
  here.forEach(u=>{ FC.mine.add(u.uid);
    if(u.kind==='task'&&u.status==='review'&&!reviewIds.has(u.uid)&&!reviewIds.has('task:'+u.task_id)) waiting.push({kind:'contact',uid:u.uid,contact:{source:'task',state:'review',title:u.name,age_days:0},unit:u}); });
  const agents=here.filter(u=>isShip(u)||(u.kind==='task'&&TASK_WORKING.includes(u.status)));
  if(a<=0) return;
  const P=problems.map(f=>Object.assign({f:f},fcFoeLook(f))).sort((x,y)=>y.age-x.age);
  const Wt=waiting.map(f=>Object.assign({f:f},fcWaitLook(f))).sort((x,y)=>y.age-x.age);
  const shownP=P.slice(0,8), shownW=Wt.slice(0,4), slots=shownP.length+shownW.length;
  // one outer orbit: slot 0 faces the sun, then alternately either side, each a little further round (strictly), so the
  // order of the slots is the order of their distance to the sun
  const Ro=R*1.34+14, step=slots>1?Math.min(.36,3.3/(slots-1)):.36;
  const slotAt=i=>{ const side=i%2?1:-1, n=Math.ceil(i/2), ang=toSun+(i?side*(n*step+i*.004):0); return Object.assign({a:ang},W0(Math.cos(ang)*Ro,Math.sin(ang)*Ro)); };
  const sunDist=w=>Math.hypot(w.x-LY.star.x,w.y-LY.star.y)*ZOOM;
  cx.save(); cx.globalAlpha*=a;
  if(slots){ cx.save(); cx.strokeStyle='rgba(232,238,246,.10)'; cx.lineWidth=k; cx.beginPath(); cx.arc(m.x,m.y,Ro*k,0,Math.PI*2); cx.stroke(); cx.restore(); FC.drawn.rings++; }
  shownP.forEach((p,i)=>{ const q=slotAt(i), L=(p.kind==='rock'?26:32)*(fcPhone()?.7:1);
    at(q,()=>fcFoe(cx,p.kind,0,0,L,q.a+Math.PI/2));
    plateHull(q.x,q.y,L*.55*k);
    if(R>=120||i<4) queuePlate({key:'ft:'+p.f.uid,kind:'seat',pri:PLATE_PRI.seat,style:'chamfer',x:q.x,y:q.y,r:L*.55*k,face:fcFace(q.a),col:FC_TAGW.red,lead:FC_TAGW.red,segs:[{t:p.tag,col:FC_TAGW.red}]});
    if(mouseW&&!dragging&&Math.hypot(mouseW.x-q.x,mouseW.y-q.y)<20*k) fxLockFoe(p.f,q.x,q.y,t);
    FC.drawn.foes++; const sc=scr(q);
    FC.foes.push({uid:String(p.f.uid),kind:p.kind,tag:p.tag,age:p.age,x:sc.x,y:sc.y,wx:q.x,wy:q.y,sunDist:sunDist(q)}); });
  shownW.forEach((w,j)=>{ const q=slotAt(shownP.length+j), L=24;
    at(q,()=>{ if(w.kind==='buoy') fcBuoy(cx,0,0,L); else fcCargo(cx,0,0,L,q.a+Math.PI/2); });
    plateHull(q.x,q.y,L*.55*k);
    if(R>=120||j<2) queuePlate({key:'fw:'+w.f.uid,kind:'seat',pri:PLATE_PRI.seat,style:'chamfer',x:q.x,y:q.y,r:L*.55*k,face:fcFace(q.a),col:w.col,lead:w.col,segs:[{t:w.tag,col:w.col}],uid:w.f.unit?w.f.unit.uid:null});
    if(w.f.unit) FC.pos.set(w.f.unit.uid,{x:q.x,y:q.y,r:16*k});
    if(mouseW&&!dragging&&Math.hypot(mouseW.x-q.x,mouseW.y-q.y)<20*k) fxLockFoe(w.f,q.x,q.y,t);   // pointed at: the same lock as a problem
    FC.drawn.waiting++; const sc=scr(q); FC.waiting.push({uid:String(w.f.uid),kind:w.kind,tag:w.tag,age:w.age,x:sc.x,y:sc.y,wx:q.x,wy:q.y,sunDist:sunDist(q)}); });
  { const more=(P.length-shownP.length)+(Wt.length-shownW.length);
    if(more>0){ const q=slotAt(slots), e=W0(Math.cos(q.a)*(Ro+4),Math.sin(q.a)*(Ro+4));
      at(e,()=>LABEL.text(cx,'+'+more,0,-9,{px:15,col:'#E8EEF6',align:'center'})); FC.drawn.more=more; } }
  // stations: the sub-folders on the side away from the sun, on two arcs, 12 at most
  if(!sun){ const rooms=fcRooms(m), show=rooms.slice(0,12), n=show.length, span=n>1?Math.min(2.6,.4*(n-1)):0;
    show.forEach((o,i)=>{ const ang=open-span/2+(n>1?span*i/(n-1):0), rr=R*(i%2?1.76:1.52)+(i%2?22:12), w=W0(Math.cos(ang)*rr,Math.sin(ang)*rr);
      const sz=Math.max(12,Math.min(17,10+2.4*Math.log10(1+(Number(o.r.files)||0))));
      at(w,()=>fcStation(cx,0,0,sz,{tint:o.bad?FC_TAGW.red:null}));
      plateHull(w.x,w.y,sz*1.05*k); FC.pos.set(o.r.uid,{x:w.x,y:w.y,r:Math.max(14,sz+6)*k});
      const right=Math.cos(ang)>=-.2;
      FC.lb.push({key:'st:'+o.r.uid,uid:o.r.uid,x:w.x,y:w.y,r:sz+2,rpx:true,text:String(o.r.name||'').replace(/^[_.]+(?=.)/,''),pri:500-i,kind:'station',
        slots:right?['right','left','own','-1','+1']:['left','right','own','-1','+1'],alpha:a});
      FC.drawn.moons++; FC.drawn.stations++; const sc=scr(w); FC.stations.push({uid:o.r.uid,x:sc.x,y:sc.y,wx:w.x,wy:w.y,bad:o.bad}); });
    if(rooms.length>n){ const ang=open+span/2+.32; at(W0(Math.cos(ang)*R*1.5,Math.sin(ang)*R*1.5),()=>LABEL.text(cx,'+'+(rooms.length-n)+' more',0,-7,{px:13,col:'#9AA8BC',align:'center'})); } }
  // agents on the working ring, working ones first, each named with what it does
  agents.sort((x,y)=>(String(y.status).toLowerCase()==='working'?1:0)-(String(x.status).toLowerCase()==='working'?1:0));
  let prog=null; try{ prog=kindsProgress(m.uid); }catch(e){}
  agents.slice(0,6).forEach((u,i)=>{ const f=prog==null?.25:Math.max(.08,prog), ang=-Math.PI/2+f*Math.PI*2-i*.42, rr=R+10, w=W0(Math.cos(ang)*rr,Math.sin(ang)*rr);
    const busy=u.kind==='task'||/^working$/i.test(String(u.status||'')), L=fcPhone()?16:28;
    at(w,()=>fcDart(cx,0,0,L,ang+Math.PI/2,{engine:busy?1:.12}));
    plateHull(w.x,w.y,L*.5*k); FC.pos.set(u.uid,{x:w.x,y:w.y,r:Math.max(16,L*.6)*k});
    const what=u.kind==='task'?CM.shortTitle(String(u.name||''),34,false):(busy?String(u.line||u.voice||'working'):String(CM.stateWord?CM.stateWord(String(u.status||'idle').toLowerCase()):'idle'));
    const nm=String(u.alias||u.name||'agent'), txt=u.kind==='task'?fcShort(what,40):fcShort(nm,18)+' · '+fcShort(what,30);
    queuePlate({key:'fa:'+u.uid,kind:'seat',pri:PLATE_PRI.seat,style:'chamfer',x:w.x,y:w.y,r:L*.5*k,face:fcFace(ang),col:FC_TAGW.cyan,lead:FC_TAGW.cyan,segs:[{t:txt,col:FC_TAGW.cyan}],uid:u.uid,unit:u.uid,dock:m.uid,cls:'ship'});
    FC.drawn.agents++; const sc=scr(w); FC.agents.push({uid:u.uid,x:sc.x,y:sc.y,wx:w.x,wy:w.y,tag:txt}); });
  cx.restore();
}
// the plate's first direction: away from the planet along the craft's own angle
function fcFace(a){ const c=Math.cos(a), s=Math.sin(a); if(Math.abs(c)>=Math.abs(s)*.7) return c>0?'e':'w'; return s>0?'s':'n'; }
// ---- painters (screen pixels; neutral shapes from the comps' art.js: agentCraft, foe, cargo, buoy, station) ----
function fcDart(g,x,y,L,ang,o){
  o=o||{}; const eg=o.engine==null?1:o.engine; g.save(); g.translate(x,y); g.rotate(ang);
  if(eg>0){ g.save(); g.globalCompositeOperation='lighter'; const ex=-L*.5; let gr=g.createRadialGradient(ex,0,0,ex,0,L*.75*eg);
    gr.addColorStop(0,'rgba(210,235,255,.95)'); gr.addColorStop(.18,'rgba(111,183,255,.75)'); gr.addColorStop(1,'rgba(111,183,255,0)');
    g.fillStyle=gr; g.beginPath(); g.arc(ex,0,L*.75*eg,0,Math.PI*2); g.fill(); g.restore(); }
  const wing=sg=>{ g.beginPath(); g.moveTo(L*.12,sg*L*.07); g.lineTo(-L*.3,sg*L*.52); g.lineTo(-L*.44,sg*L*.5); g.lineTo(-L*.34,sg*L*.1); g.closePath();
    const wg=g.createLinearGradient(0,0,0,sg*L*.5); wg.addColorStop(0,'#c9d3e1'); wg.addColorStop(1,'#7f8da3'); g.fillStyle=wg; g.fill();
    g.strokeStyle='rgba(111,183,255,.95)'; g.lineWidth=Math.max(.7,L*.045); g.beginPath(); g.moveTo(-L*.3,sg*L*.52); g.lineTo(-L*.44,sg*L*.5); g.stroke(); };
  wing(-1); wing(1);
  g.beginPath(); g.moveTo(L*.58,0); g.lineTo(L*.16,-L*.11); g.lineTo(-L*.42,-L*.1); g.lineTo(-L*.5,0); g.lineTo(-L*.42,L*.1); g.lineTo(L*.16,L*.11); g.closePath();
  const hg=g.createLinearGradient(0,-L*.11,0,L*.11); hg.addColorStop(0,'#ffffff'); hg.addColorStop(.55,'#dfe6f0'); hg.addColorStop(1,'#8e9bb0');
  g.fillStyle=hg; g.fill(); g.strokeStyle='rgba(5,7,13,.55)'; g.lineWidth=Math.max(.5,L*.025); g.stroke();
  g.beginPath(); g.ellipse(L*.16,0,L*.15,L*.055,0,0,Math.PI*2); const cg=g.createLinearGradient(L*.05,-L*.05,L*.28,L*.05); cg.addColorStop(0,'#d8f4ff'); cg.addColorStop(1,'#2f7fbf'); g.fillStyle=cg; g.fill();
  g.restore();
}
function fcFoe(g,kind,x,y,L,ang){
  g.save(); g.translate(x,y); g.rotate(ang);
  g.save(); g.globalCompositeOperation='lighter'; const gr=g.createRadialGradient(-L*.45,0,0,-L*.45,0,L*.6);
  gr.addColorStop(0,'rgba(255,200,190,.9)'); gr.addColorStop(.2,'rgba(255,90,95,.7)'); gr.addColorStop(1,'rgba(255,90,95,0)'); g.fillStyle=gr; g.beginPath(); g.arc(-L*.45,0,L*.6,0,Math.PI*2); g.fill(); g.restore();
  if(kind==='drone'){ g.strokeStyle='#6e1424'; g.lineWidth=Math.max(1,L*.08);
    [[.7,-.5],[.7,.5],[-.5,-.55],[-.5,.55]].forEach(([p,q])=>{ g.beginPath(); g.moveTo(0,0); g.lineTo(p*L*.55,q*L*.9); g.stroke(); });
    const bg=g.createRadialGradient(-L*.1,-L*.1,0,0,0,L*.4); bg.addColorStop(0,'#e06a78'); bg.addColorStop(.6,'#c54555'); bg.addColorStop(1,'#5a1020');
    g.fillStyle=bg; g.beginPath(); g.arc(0,0,L*.38,0,Math.PI*2); g.fill();
    g.fillStyle='#2a0810'; g.beginPath(); g.arc(L*.12,0,L*.17,0,Math.PI*2); g.fill(); g.fillStyle='#ffd0d0'; g.beginPath(); g.arc(L*.14,0,L*.07,0,Math.PI*2); g.fill(); }
  else if(kind==='rock'){ g.beginPath(); for(let i=0;i<9;i++){ const a=i/9*Math.PI*2, rr=L*(.36+((i*37)%17)/17*.16); if(i) g.lineTo(Math.cos(a)*rr,Math.sin(a)*rr); else g.moveTo(Math.cos(a)*rr,Math.sin(a)*rr); }
    g.closePath(); const rg=g.createLinearGradient(-L*.4,-L*.4,L*.4,L*.4); rg.addColorStop(0,'#8a7a72'); rg.addColorStop(1,'#3a2c2a'); g.fillStyle=rg; g.fill();
    g.strokeStyle='#ff5a5f'; g.lineWidth=Math.max(.8,L*.05); g.beginPath(); g.moveTo(-L*.2,-L*.1); g.lineTo(0,L*.05); g.lineTo(L*.12,-L*.05); g.lineTo(L*.22,L*.12); g.stroke(); }
  else { g.beginPath(); g.moveTo(L*.6,0); g.lineTo(-L*.2,-L*.5); g.lineTo(-L*.05,-L*.12); g.lineTo(-L*.45,-L*.14); g.lineTo(-L*.45,L*.14); g.lineTo(-L*.05,L*.12); g.lineTo(-L*.2,L*.5); g.closePath();
    const hg=g.createLinearGradient(0,-L*.5,0,L*.5); hg.addColorStop(0,'#5a2a34'); hg.addColorStop(.5,'#3a1822'); hg.addColorStop(1,'#22090f');
    g.fillStyle=hg; g.fill(); g.strokeStyle='#e0484f'; g.lineWidth=Math.max(.8,L*.05); g.stroke();
    g.fillStyle='#ff8a8e'; g.beginPath(); g.ellipse(L*.22,0,L*.1,L*.04,0,0,Math.PI*2); g.fill(); }
  g.restore();
}
function fcCargo(g,x,y,L,ang){
  g.save(); g.translate(x,y); g.rotate(ang);
  g.fillStyle='#9aa8bc'; g.fillRect(-L*.45,-L*.25,L*.9,L*.5); g.fillStyle='#c9d3e1'; g.fillRect(-L*.45,-L*.25,L*.9,L*.12);
  g.fillStyle='#5d6874'; for(let i=0;i<3;i++) g.fillRect(-L*.3+i*L*.25,-L*.1,L*.04,L*.32);
  g.save(); g.globalCompositeOperation='lighter'; const gr=g.createRadialGradient(L*.5,0,0,L*.5,0,L*.5); gr.addColorStop(0,'rgba(200,230,255,.95)'); gr.addColorStop(.3,'rgba(90,150,255,.6)'); gr.addColorStop(1,'rgba(90,150,255,0)');
  g.fillStyle=gr; g.beginPath(); g.arc(L*.5,0,L*.5,0,Math.PI*2); g.fill(); g.restore(); g.restore();
}
function fcBuoy(g,x,y,L){
  g.save(); g.translate(x,y); g.fillStyle='#7f8da3'; g.fillRect(-L*.14,-L*.1,L*.28,L*.5); g.fillStyle='#c9d3e1'; g.fillRect(-L*.14,-L*.1,L*.08,L*.5);
  g.save(); g.globalCompositeOperation='lighter'; const gr=g.createRadialGradient(0,-L*.2,0,0,-L*.2,L*.6); gr.addColorStop(0,'rgba(255,230,180,.95)'); gr.addColorStop(.25,'rgba(255,181,71,.6)'); gr.addColorStop(1,'rgba(255,181,71,0)');
  g.fillStyle=gr; g.beginPath(); g.arc(0,-L*.2,L*.6,0,Math.PI*2); g.fill(); g.restore(); g.restore();
}
function fcStation(g,x,y,s,o){
  o=o||{}; const tint=o.tint||'#6FB7FF'; g.save(); g.translate(x,y);
  g.fillStyle='rgba(0,0,0,.35)'; g.beginPath(); g.ellipse(0,s*.35,s*1.05,s*.32,0,0,Math.PI*2); g.fill();
  let gr=g.createLinearGradient(0,-s*.3,0,s*.3); gr.addColorStop(0,'#9aa6b6'); gr.addColorStop(1,'#3a4452'); g.fillStyle=gr; g.beginPath(); g.ellipse(0,s*.12,s,s*.3,0,0,Math.PI*2); g.fill();
  g.fillStyle='#c4ccd8'; g.beginPath(); g.ellipse(0,0,s,s*.3,0,0,Math.PI*2); g.fill(); g.fillStyle='#6a7686'; g.beginPath(); g.ellipse(0,0,s*.62,s*.18,0,0,Math.PI*2); g.fill();
  g.fillStyle=tint; for(let i=0;i<10;i++){ const a=i/10*Math.PI*2; if(Math.sin(a)<-.2) continue; g.fillRect(Math.cos(a)*s*.9-.7,Math.sin(a)*s*.27+s*.06-.7,1.4,1.4); }
  gr=g.createLinearGradient(-s*.12,0,s*.12,0); gr.addColorStop(0,'#e8eef6'); gr.addColorStop(1,'#6a7686'); g.fillStyle=gr; g.beginPath(); g.moveTo(-s*.16,0); g.lineTo(0,-s*.95); g.lineTo(s*.16,0); g.closePath(); g.fill();
  g.save(); g.globalCompositeOperation='lighter'; const lg=g.createRadialGradient(0,-s*.95,0,0,-s*.95,s*.4); lg.addColorStop(0,tint); lg.addColorStop(1,'rgba(0,0,0,0)'); g.fillStyle=lg; g.beginPath(); g.arc(0,-s*.95,s*.4,0,Math.PI*2); g.fill(); g.restore();
  g.restore();
}
// a chamfered tag with its 45-degree elbow leader to the thing it names (q: the plate; at: the craft on the screen)
function fcTagPaint(g,q,x,y,w,h,at){
  const col=q.col||FC_TAGW.red, ch=6, rgb=[1,3,5].map(i=>parseInt(col.slice(i,i+2),16)), rgba=(al)=>'rgba('+rgb.join(',')+','+al+')';
  if(at){ const ex=at.x>x+w/2?x+w:x, ey=y+h/2, dx=ex-at.x, dy=ey-at.y, diag=Math.min(Math.abs(dy),Math.abs(dx))*Math.sign(dx||1);
    g.save(); g.strokeStyle=rgba(.75); g.lineWidth=1.2; g.beginPath(); g.moveTo(at.x,at.y); g.lineTo(at.x+diag,ey); g.lineTo(ex,ey); g.stroke();
    g.fillStyle=rgba(1); g.beginPath(); g.arc(at.x,at.y,2.2,0,Math.PI*2); g.fill(); g.restore(); }
  g.save(); g.beginPath(); g.moveTo(x+ch,y); g.lineTo(x+w,y); g.lineTo(x+w,y+h-ch); g.lineTo(x+w-ch,y+h); g.lineTo(x,y+h); g.lineTo(x,y+ch); g.closePath();
  g.fillStyle='rgba('+rgb.map(v=>Math.round(v*.14*.45+[6,9,16][0]*.55)).join(',')+',.94)'; g.fill(); g.strokeStyle=rgba(.9); g.lineWidth=1; g.stroke();
  g.fillStyle=rgba(1); g.fillRect(x+1,y+ch,2.5,h-ch-3); g.restore();
  const ink='rgb('+rgb.map(v=>Math.round(v+(255-v)*.62)).join(',')+')';
  let tx=x+10; q.rows.forEach((row,ri)=>{ const t=row.map(s=>String(s.t)).join(' · '); LABEL.text(g,t,tx,y+4+ri*20,{px:14,col:ink,stroke:false}); });
}
// ---- the named edge arrows: an off-map body gets an arrow at the free map's edge and its name (no plate for it).
// Planned before the plates (their boxes are obstacles for the labels and the plates), painted after them ----
function fcEdgePlan(){
  FC.edge=[]; FC.edgeBoxes=[];
  if(typeof deck!=='undefined'&&deck!=='home') return;
  const F=fcFree(), A=fcMapArea(), f=CM.focusUid(), cxm=F.x+F.w/2, cym=F.y+F.h/2, list=[];
  const add=(uid,name,x,y,r,col)=>{ const s=fcScr(x,y), rr=r*ZOOM; if(s.x+rr>A.x&&s.x-rr<A.x+A.w&&s.y+rr>A.y&&s.y-rr<A.y+A.h) return; list.push({uid:uid,name:name,s:s,col:col}); };
  add(BOARD.uid,CM.labelText(BOARD.uid),LY.star.x,LY.star.y,fcSunR(),LABEL.SUN);
  if(!f) MOONS.forEach(m=>add(m.uid,CM.labelText(m.uid),m.x,m.y,fcDispR(m),LABEL.INK));
  list.slice(0,8).forEach(a=>{ const dx=a.s.x-cxm, dy=a.s.y-cym, ang=Math.atan2(dy,dx), mg=22;
    const kx=dx?((dx>0?F.x+F.w-mg:F.x+mg)-cxm)/dx:Infinity, ky=dy?((dy>0?F.y+F.h-mg:F.y+mg)-cym)/dy:Infinity, kk=Math.min(Math.abs(kx),Math.abs(ky));
    let x=cxm+dx*kk, y=cym+dy*kk; const tw=LABEL.measure(a.name,13,600);
    if(f&&x<F.x+16+190&&y<F.y+16+34+12){ x=F.x+mg; y=Math.max(y,F.y+16+34+26); }   // under the crumb chip, on the left edge
    const lx=Math.cos(ang)>.3?x-14-tw:x+14, ly=y-8;
    if(FC.edge.some(p=>Math.abs(p.ly-ly)<18&&Math.abs(p.lx-lx)<Math.max(p.tw,tw))) return;
    FC.edge.push({uid:a.uid,name:a.name,x:x,y:y,angle:ang,lx:lx,ly:ly,tw:tw,col:a.col});
    FC.edgeBoxes.push({x:Math.min(x-12,lx)-2,y:Math.min(y-10,ly)-2,w:Math.max(x+12,lx+tw)-Math.min(x-12,lx)+4,h:24}); });
}
function fcEdge(t){
  if(!FC.edge||!FC.edge.length) return;
  cx.save(); cx.setTransform(DPR,0,0,DPR,0,0);
  FC.edge.forEach(a=>{ cx.save(); cx.translate(a.x,a.y); cx.rotate(a.angle); cx.fillStyle=a.col; cx.strokeStyle=LABEL.HALO; cx.lineWidth=2;
    cx.beginPath(); cx.moveTo(10,0); cx.lineTo(-4,-7); cx.lineTo(-1,0); cx.lineTo(-4,7); cx.closePath(); cx.stroke(); cx.fill(); cx.restore();
    LABEL.text(cx,a.name,a.lx,a.ly,{px:13,col:a.col});
    FC.arrows.push({uid:a.uid,name:a.name,x:a.x,y:a.y,angle:a.angle}); });
  cx.restore();
}
// ---- the crumb chip at the top left of the free map: "Esc · All planets › ops" while a planet is open, the selected workflow's
// ("Esc · All planets › Workflow · Ship the login page", calls CM.crumb) otherwise. One chip, two writers: the planet
// in focus wins. CM.crumb(segments, from): segments an array of words (null hides that writer's crumb) ----
FC.crumbs={focus:null,other:null,sig:''};
CM.crumb=function(segments,from){
  const s=Array.isArray(segments)?segments.map(x=>String(x==null?'':x).slice(0,80)).filter(Boolean).slice(0,4):null;
  FC.crumbs[from==='focus'?'focus':'other']=s&&s.length?s:null; fcCrumbPaint(); return true;
};
function fcCrumbPaint(){
  const el=document.getElementById('fcrumb'); if(!el||!el.classList||typeof el.appendChild!=='function') return;
  const segs=(typeof CM.mode3d==='function'&&CM.mode3d())?null:(FC.crumbs.focus||FC.crumbs.other), sig=segs?segs.join('\u0001'):'';
  if(el.hidden!==!segs) el.hidden=!segs;
  if(sig!==FC.crumbs.sig){ FC.crumbs.sig=sig; el.textContent='';   // the old words go (never a loop over children: a stub element has endless ones)
    if(segs){ const add=(tag,cls,text)=>{ const e=document.createElement(tag); if(cls) e.className=cls; if(text!=null) e.textContent=text; el.appendChild(e); return e; };
      add('kbd','','Esc');
      const b=add('button','fc-all',segs[0]); b.type='button';
      b.addEventListener('click',()=>{ if(!FC.crumbs.focus&&CM.backLayer('esc')) return; closeAll(); });   // the workflow's own layer lets go first
      segs.slice(1).forEach((t,i)=>{ const sep=add('span','fc-sep','›'); sep.setAttribute('aria-hidden','true'); add(i===segs.length-2?'b':'span',i===segs.length-2?'fc-here':'fc-mid',t); }); } }
  if(!segs) return;
  const F=fcFree(), l=Math.round(F.x+16)+'px', tp=Math.round(F.y+16)+'px'; if(el.style&&el.style.left!==l) el.style.left=l; if(el.style&&el.style.top!==tp) el.style.top=tp;
}
function fcCrumb(){ const m=fcArea(); CM.crumb(m?['All planets',CM.labelText(m.uid)]:null,'focus'); }
// ---- the test hook ----
window.__mapAudit=function(){
  const A=FC.audit||{drawn:{moons:0,foes:0,wedges:0,rings:0,ribbons:0,machines:0,working:{}},planets:[],foes:[],waiting:[],stations:[],agents:[],arrows:[]};
  const F=fcFree(), M=fcMapArea(), covers=fcCovers(), lab=new Map(((FC.lbOut&&FC.lbOut.all)||[]).map(p=>[p.uid,p]));
  const inside=(p)=>p.x-p.r>=M.x-1&&p.y-p.r>=M.y-1&&p.x+p.r<=M.x+M.w+1&&p.y+p.r<=M.y+M.h+1&&!covers.some(b=>{ const dx=Math.max(b.x-p.x,0,p.x-b.x-b.w), dy=Math.max(b.y-p.y,0,p.y-b.y-b.h); return dx*dx+dy*dy<p.r*p.r; });
  const onMap=(p)=>p.x+p.r>0&&p.y+p.r>0&&p.x-p.r<fcMapW()&&p.y-p.r<fcMapH();
  const planets=A.planets.map(p=>{ const l=lab.get(p.uid); return {uid:p.uid,x:p.x,y:p.y,r:p.r,inFree:inside(p),
    label:!onMap(p)?'off':p.other?'hidden':l?(l.slot==='pip'?'pip':'full'):'none',slot:l?l.slot:null}; });
  const body=uid=>{ const p=A.planets.find(x=>x.uid===uid); if(p) return {x:p.x,y:p.y,r:p.r};
    if(uid===BOARD.uid){ const s=fcScr(LY.star.x,LY.star.y); return {x:s.x,y:s.y,r:fcSunR()*ZOOM}; } return null; };
  const empty=(()=>{ const occupied=(x,y)=>A.planets.some(p=>Math.hypot(p.x-x,p.y-y)<p.r+40)||Math.hypot(fcScr(LY.star.x,LY.star.y).x-x,fcScr(LY.star.x,LY.star.y).y-y)<fcSunR()*ZOOM+40
      ||((FC.lbOut&&FC.lbOut.all)||[]).some(l=>x>l.x-20&&x<l.x+l.w+20&&y>l.y-20&&y<l.y+l.h+20)||covers.some(b=>x>b.x&&x<b.x+b.w&&y>b.y&&y<b.y+b.h);
    for(let gy=.2;gy<.9;gy+=.1) for(let gx=.1;gx<.95;gx+=.05){ const x=F.x+F.w*gx, y=F.y+F.h*gy; if(!occupied(x,y)) return {x:Math.round(x),y:Math.round(y)}; } return null; })();
  return {focus:CM.focusUid(),drawn:Object.assign({},A.drawn,{working:Object.assign({},A.drawn.working)}),free:{x:F.x,y:F.y,w:F.w,h:F.h,from:F.from},
    planets:planets,foes:A.foes,waiting:A.waiting,stations:A.stations,agents:A.agents,arrows:A.arrows,detailToggles:FC.toggles,
    camera:{phase:FOCUS_CAM.phase,zoom:ZOOM,target:FOCUS_CAM.zt},bodyOf:body,emptyPoint:empty};
};
window.__mapAudit.reset=function(){ FC.toggles=0; return true; };
CM.onInit.push(()=>{
  const st=document.getElementById('stage'); if(!st||!document.createElement) return;
  const el=document.createElement('nav'); if(!el||typeof el.setAttribute!=='function') return;
  el.id='fcrumb'; el.className='fcrumb'; el.setAttribute('aria-label','Where you are'); el.hidden=true;
  try{ st.appendChild(el); FC.crumbs.sig='\u0002'; fcCrumbPaint(); }catch(e){}
});
