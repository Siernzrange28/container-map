// ui/labels.js — planet names: one fixed slot under the body, one displacement with a leader, else a pip; CM.labelText for 2D plates
// and 3D labels.
// Inlined into the page's main script right after the CM registry (render-game.py, the seams): declare functions and
// assign into CM only, nothing that runs page code at load (start-up goes in CM.onInit). Real backslashes here: this
// file is read as it is, not through a Python string. Neutral words only.
//
// The slots (after webglite localChart.ts:49-76: each label wants its own row; if that row is taken it tries
// one row lower, then one row higher, else it is dropped; ours tries the two sides before it folds to a pip):
//   own    centred under the body, its top r+6 px below the centre
//   +1     one label row lower (own + the label's height + 2 px)
//   -1     the mirror of own above the body, its bottom r+6 px above the centre
//   right  beside the body, r+6 px to the right, centred on it
//   left   the same on the left
//   pip    no room anywhere: the badge alone (or a dot), the name on hover, in focus and in the a11y list
// Names are stroked then filled: a 3 px #05070D stroke under #E8EEF6, so a name reads over
// any body, ring or nebula without a plate behind it. Everything here works in screen pixels.
const LABEL={
  STROKE:3, GAP:6, ROWGAP:2, BADGE_GAP:10, CHIP_GAP:5, EDGE:4, PAD:2,
  SLOTS:['own','+1','-1','right','left'],
  INK:'#E8EEF6', HALO:'#05070D', SUN:'#F5B342', RED:'#FF5A5F', AMBER:'#FFB547', CHIP_INK:'#BFDDFF', CYAN:'#6FB7FF',
  FACE:'"Barlow Condensed","Arial Narrow",sans-serif',
  mem:new Map(),        // uid -> the slot its label had last frame (a free remembered slot is kept: names do not hop)
  widths:new Map(),     // text|px|weight -> width (cleared when the web font arrives)
  ctx:null,
  last:{items:[],placed:[],frame:-1},
};
// the name size: 15 px on a desktop or a tablet, 13 px on a phone
LABEL.px=function(){ return (typeof MOB!=='undefined'&&MOB)?13:15; };
LABEL.font=function(px,w){ return (w||600)+' '+px+'px '+LABEL.FACE; };
// the width of a line of text in this face (a canvas of its own, so a measure never disturbs a drawing in progress)
LABEL.measure=function(text,px,w){
  text=String(text==null?'':text); px=px||LABEL.px(); w=w||600;
  const key=text+'|'+px+'|'+w; let v=LABEL.widths.get(key); if(v!=null) return v;
  try{ if(!LABEL.ctx){ const c=document.createElement('canvas'); LABEL.ctx=c&&c.getContext?c.getContext('2d'):null; }
    if(LABEL.ctx&&typeof LABEL.ctx.measureText==='function'){ LABEL.ctx.font=LABEL.font(px,w); v=Number(LABEL.ctx.measureText(text).width); } }catch(e){ v=null; }
  if(!Number.isFinite(v)||v<=0) v=text.length*px*.46;   // no canvas (a stub page): the face's average advance
  v=Math.ceil(v); if(LABEL.widths.size>600) LABEL.widths.clear(); LABEL.widths.set(key,v); return v;
};
// the name a planet, the sun, a moon or a craft carries, on the 2D map and on the 3D labels (one source): a folder's
// name as written with a leading "_" or "." dropped, the sun "This machine" on its own map
CM.labelText=function(uid){
  uid=uid==null?'':String(uid);
  if(typeof BOARD!=='undefined'&&uid===BOARD.uid) return String(CM.word('this machine')).replace(/^./,c=>c.toUpperCase());
  let m=null; try{ m=(typeof MOONS!=='undefined'&&MOONS.find(x=>x.uid===uid))||(typeof unitByUid==='function'?unitByUid(uid):null); }catch(e){ m=null; }
  if(!m) return uid;
  const raw=String(m.label||m.alias||m.name||uid), clean=raw.replace(/^[_.]+(?=.)/,'');
  return clean||raw;
};
// the badge after a name: the number of what waits there, red when anything there is broken, amber otherwise
LABEL.badgeOf=function(n,broken){ n=Number(n)||0; return n>0?{n:n,col:broken?LABEL.RED:LABEL.AMBER}:null; };
// one label's box: the name row (name, then the badge 10 px after it) and, under it, the chip ("Fix labels · 2 of 4")
LABEL.box=function(it,px){
  px=px||it.px||LABEL.px();
  const nw=LABEL.measure(it.text,px,600), bh=px+1, b=it.badge, bw=b?Math.max(bh,LABEL.measure(String(b.n),Math.max(12,px-3),600)+10):0;
  const rowW=nw+(bw?LABEL.BADGE_GAP+bw:0), rowH=Math.round(px*1.2);
  let w=rowW, h=rowH, cw=0, ch=0;
  if(it.chip){ const cs=Math.max(12,px-2); cw=LABEL.measure(it.chip,cs,600)+22; ch=cs+7; w=Math.max(w,cw); h+=LABEL.CHIP_GAP+ch; }
  return {w:Math.ceil(w),h:Math.ceil(h),nw:nw,bw:bw,bh:bh,rowW:rowW,rowH:rowH,cw:cw,ch:ch,px:px};
};
// where each slot puts a w x h label for a body at (x, y) of radius r (screen pixels)
LABEL.slotRect=function(slot,x,y,r,w,h){
  const g=LABEL.GAP;
  if(slot==='own') return {x:x-w/2,y:y+r+g,w:w,h:h};
  if(slot==='+1') return {x:x-w/2,y:y+r+g+h+LABEL.ROWGAP,w:w,h:h};
  if(slot==='-1') return {x:x-w/2,y:y-r-g-h,w:w,h:h};
  if(slot==='right') return {x:x+r+g,y:y-h/2,w:w,h:h};
  if(slot==='left') return {x:x-r-g-w,y:y-h/2,w:w,h:h};
  return null;
};
// px from the body's edge to the label's near side (own: 6)
LABEL.gapOf=function(slot,x,y,r,q){
  if(slot==='own'||slot==='+1') return q.y-(y+r);
  if(slot==='-1') return (y-r)-(q.y+q.h);
  if(slot==='right') return q.x-(x+r);
  if(slot==='left') return (x-r)-(q.x+q.w);
  return 0;
};
function lbHit(a,b,pad){ return a.x<b.x+b.w+pad&&a.x+a.w+pad>b.x&&a.y<b.y+b.h+pad&&a.y+a.h+pad>b.y; }
function lbDisc(a,c,r){ const dx=Math.max(a.x-c.x,0,c.x-(a.x+a.w)), dy=Math.max(a.y-c.y,0,c.y-(a.y+a.h)); return dx*dx+dy*dy<r*r; }
// The placer, pure: items [{uid, x, y, r, text, pri, badge?, chip?, w?, h?}] (screen pixels), placed in pri order, highest
// first (ties keep the list's order); view {w, h, x?, y?, discs?:[{x,y,r}], boxes?:[{x,y,w,h}], mem?:Map, px?}.
// Each label takes the first free slot of own, +1, -1, right, left (its last frame's slot first when view.mem holds one and
// it is still free), else it folds to a pip. Free: inside the view (4 px in), on no label already placed (2 px apart), on
// no body but its own, under no box. Returns [{uid, slot, x, y, w, h, gap, item}] in the order placed.
LABEL.place=function(items,view){
  view=view||{}; const vx=Number(view.x)||0, vy=Number(view.y)||0, vw=Number(view.w)||0, vh=Number(view.h)||0, e=LABEL.EDGE, pad=LABEL.PAD;
  const order=(Array.isArray(items)?items:[]).map((it,i)=>({it:it,i:i})).filter(o=>o.it&&Number.isFinite(o.it.x)&&Number.isFinite(o.it.y))
    .sort((a,b)=>(Number(b.it.pri)||0)-(Number(a.it.pri)||0)||a.i-b.i);
  const discs=(Array.isArray(view.discs)?view.discs:[]).concat(order.filter(o=>Number(o.it.r)>0).map(o=>({x:o.it.x,y:o.it.y,r:Number(o.it.r)})));
  const boxes=Array.isArray(view.boxes)?view.boxes:[], soft=Array.isArray(view.soft)?view.soft:[], placed=[], out=[], mem=view.mem instanceof Map?view.mem:null;
  order.forEach(o=>{
    const it=o.it, r=Math.max(0,Number(it.r)||0), sz=(it.w>0&&it.h>0)?{w:it.w,h:it.h}:LABEL.box(it,view.px);
    let hard=false;
    const free=q=>{
      if(vw>0&&vh>0&&(q.x<vx+e||q.y<vy+e||q.x+q.w>vx+vw-e||q.y+q.h>vy+vh-e)) return false;
      if(!hard) for(const d of soft){ if(Math.hypot(d.x-it.x,d.y-it.y)<Math.max(1,r)) continue; if(lbDisc(q,d,d.r)) return false; }
      for(const p of placed) if(lbHit(q,p,pad)) return false;
      for(const b of boxes) if(lbHit(q,b,0)) return false;
      for(const d of discs){ if(Math.hypot(d.x-it.x,d.y-it.y)<Math.max(1,r)) continue; if(lbDisc(q,d,d.r)) return false; }   // its own body, and what sits on it (a ship parked on the sun), never block its name
      return true; };
    const order=Array.isArray(it.slots)&&it.slots.length?it.slots:LABEL.SLOTS, rec0=mem&&it.uid!=null?mem.get(it.uid):null;
    const rec=rec0==null?null:(typeof rec0==='string'?{slot:rec0,since:null}:rec0), now=Number(view.now)||0;
    let first=null, kept=null;
    for(const s of order){ const q=LABEL.slotRect(s,it.x,it.y,r,sz.w,sz.h); if(q&&free(q)){ first={slot:s,q:q}; break; } }
    if(!first&&soft.length){ hard=true; for(const s of order){ const q=LABEL.slotRect(s,it.x,it.y,r,sz.w,sz.h); if(q&&free(q)){ first={slot:s,q:q}; break; } } }   // over a craft rather than folded
    // last frame's slot stays while it is free; when a better one has been free for 1.5 s the name settles there (it never
    // hops on a passing box, and it does not stay displaced for good)
    let since=null;
    if(rec&&order.includes(rec.slot)&&(!first||first.slot!==rec.slot)){ const q=LABEL.slotRect(rec.slot,it.x,it.y,r,sz.w,sz.h);
      if(q&&free(q)){ since=rec.since==null?now:rec.since; if(!first||now-since<1500) kept={slot:rec.slot,q:q}; else since=null; } }
    const got=kept||first;
    if(got){ placed.push(got.q); out.push({uid:it.uid,slot:got.slot,since:kept?since:null,x:got.q.x,y:got.q.y,w:got.q.w,h:got.q.h,gap:LABEL.gapOf(got.slot,it.x,it.y,r,got.q),item:it}); }
    else{ const b=it.badge, pw=b?Math.max(LABEL.px()+1,LABEL.measure(String(b.n),Math.max(12,LABEL.px()-3))+10):6, ph=b?LABEL.px()+1:6;
      out.push({uid:it.uid,slot:'pip',x:it.x-pw/2,y:it.y+r+LABEL.GAP/2,w:pw,h:ph,gap:LABEL.GAP/2,item:it}); }
  });
  return out;
};
// a 1 px leader from the body's edge to a displaced label's near side (+1, -1, right, left)
LABEL.leader=function(g,p){
  const pref=p&&p.item&&Array.isArray(p.item.slots)&&p.item.slots.length?p.item.slots[0]:'own';
  if(!p||p.slot===pref||p.slot==='pip') return;   // a name in its first slot needs no line to its body
  const it=p.item, r=Math.max(0,Number(it.r)||0), tx=Math.max(p.x,Math.min(it.x,p.x+p.w)), ty=Math.max(p.y,Math.min(it.y,p.y+p.h));
  const dx=tx-it.x, dy=ty-it.y, d=Math.hypot(dx,dy)||1; if(d<=r+1) return;
  g.save(); g.globalAlpha=(g.globalAlpha||1)*.7; g.strokeStyle=it.lead||'#9AA8BC'; g.lineWidth=1; g.beginPath();
  g.moveTo(Math.round(it.x+dx/d*(r+1))+.5,Math.round(it.y+dy/d*(r+1))+.5); g.lineTo(Math.round(tx)+.5,Math.round(ty)+.5); g.stroke(); g.restore();
};
// text stroked then filled, top-left at (x, y)
LABEL.text=function(g,s,x,y,o){
  o=o||{}; g.font=LABEL.font(o.px||LABEL.px(),o.w||600); g.textAlign=o.align||'left'; g.textBaseline='top';
  if(o.stroke!==false){ g.lineJoin='round'; g.miterLimit=2; g.lineWidth=o.sw||LABEL.STROKE; g.strokeStyle=o.halo||LABEL.HALO; g.strokeText(String(s),x,y); }
  g.fillStyle=o.col||LABEL.INK; g.fillText(String(s),x,y);
};
function lbPill(g,x,y,w,h,fill,stroke){
  g.beginPath(); if(typeof g.roundRect==='function') g.roundRect(x,y,w,h,h/2); else g.rect(x,y,w,h);
  if(fill){ g.fillStyle=fill; g.fill(); } if(stroke){ g.strokeStyle=stroke; g.lineWidth=1; g.stroke(); }
}
// paint one placed label (screen pixels; the caller set the transform): the name, its badge, its chip; a pip is the badge alone
LABEL.paint=function(g,p){
  const it=p.item, px=it.px||LABEL.px(), B=LABEL.box(it,px), a=it.alpha==null?1:it.alpha; if(a<=0) return;
  g.save(); g.globalAlpha=(g.globalAlpha==null?1:g.globalAlpha)*a; g.textBaseline='top';
  const badge=(bx,by)=>{ const b=it.badge; if(!b) return;
    g.save(); g.shadowColor='rgba(0,0,0,.6)'; g.shadowBlur=4; lbPill(g,bx,by,B.bw,B.bh-1,b.col); g.restore();
    LABEL.text(g,String(b.n),bx+B.bw/2,by+Math.max(.5,(B.bh-1-Math.max(12,px-3))/2),{px:Math.max(12,px-3),col:LABEL.HALO,stroke:false,align:'center'}); };
  if(p.slot==='pip'){
    if(it.badge) badge(p.x,p.y); else { g.fillStyle=it.col||LABEL.INK; g.strokeStyle=LABEL.HALO; g.lineWidth=2; g.beginPath(); g.arc(p.x+3,p.y+3,2.5,0,Math.PI*2); g.stroke(); g.fill(); }
    g.restore(); return; }
  LABEL.leader(g,p);
  const x0=Math.round(p.x+(p.w-B.rowW)/2), y0=Math.round(p.y);
  LABEL.text(g,it.text,x0,y0+Math.round((B.rowH-px)/2)-1,{px:px,col:it.col||LABEL.INK});
  if(it.badge) badge(x0+B.nw+LABEL.BADGE_GAP,y0+Math.round((B.rowH-B.bh)/2));
  if(it.chip){ const cs=Math.max(12,px-2), cx0=Math.round(p.x+(p.w-B.cw)/2), cy0=y0+B.rowH+LABEL.CHIP_GAP;
    lbPill(g,cx0+.5,cy0+.5,B.cw-1,B.ch-1,'rgba(11,26,46,.88)','rgba(111,183,255,.75)');
    g.fillStyle=LABEL.CYAN; g.beginPath(); g.arc(cx0+9,cy0+B.ch/2,2.6,0,Math.PI*2); g.fill();
    LABEL.text(g,it.chip,cx0+15,cy0+3,{px:cs,col:LABEL.CHIP_INK,stroke:false}); }
  g.restore();
};
CM.onInit.push(()=>{   // the web face arrives after the first frames: measure again once it is in
  try{ if(document.fonts&&document.fonts.load){ document.fonts.load(LABEL.font(15,600)).then(()=>{ LABEL.widths.clear(); }).catch(()=>{}); } }catch(e){}
});
// ---- the page's label pass (runPlates calls it before the craft plates): this frame's requests (FC.lb, world units) laid out
// on the screen against the bodies, the craft hulls and the page's boxes; the craft plates then keep off what it placed ----
const LABEL_DIR={own:[4,0],'+1':[4,1],'-1':[0,0],right:[2,0],left:[6,0],pip:[4,0]};
function labelsRun(){
  const aw=VW-PANEL_W, ah=VH-(typeof plateSheet==='function'?plateSheet():0), z=ZOOM, items=[], pips=[];
  (FC.lb||[]).forEach(o=>{
    const sx=(o.x-camx)*z, sy=(o.y-camy)*z, sr=o.rpx?o.r:o.r*z;
    if(!(Number.isFinite(sx)&&Number.isFinite(sy))) return;
    if(sx+sr<0||sy+sr<0||sx-sr>aw||sy-sr>ah) return;            // off the map: no label, a named edge arrow (fcEdge)
    const it=Object.assign({},o,{x:sx,y:sy,r:sr,wx:o.x,wy:o.y}); (o.pip?pips:items).push(it); });
  const discs=[];
  (PLATE_DISCS||[]).forEach(d=>discs.push({x:(d.x-camx)*z,y:(d.y-camy)*z,r:(d.r0||d.r)*z}));
  if(typeof LY!=='undefined'&&LY.star&&deck==='home') discs.push({x:(LY.star.x-camx)*z,y:(LY.star.y-camy)*z,r:fcSunR()*z});
  const soft=(PLATE_HULLS||[]).map(h=>({x:(h.x-camx)*z,y:(h.y-camy)*z,r:h.r*z}));   // craft: kept off when there is room (LABEL.place view.soft)
  const boxes=(typeof overlayBoxes==='function'?overlayBoxes():[]).map(b=>({x:b.x,y:b.y,w:b.w,h:b.h}));
  (FC.edgeBoxes||[]).forEach(b=>boxes.push(b));
  // a name keeps last frame's slot while it is free (no hopping as the camera moves), except while the focus camera eases in:
  // the open planet's stations start crowded and spread out, so each takes the best free slot as it comes
  const placed=LABEL.place(items,{x:0,y:0,w:aw,h:ah,discs:discs,soft:soft,boxes:boxes,mem:FOCUS_CAM.phase==='in'?null:LABEL.mem,now:FRAME_T});
  placed.forEach(p=>{ if(p.uid==null) return; if(p.slot==='pip') LABEL.mem.delete(p.uid); else LABEL.mem.set(p.uid,{slot:p.slot,since:p.since==null?null:p.since}); });
  pips.forEach(it=>{ const b=it.badge, pw=b?Math.max(LABEL.px()+1,LABEL.measure(String(b.n),Math.max(12,LABEL.px()-3))+10):6, ph=b?LABEL.px()+1:6;
    placed.push({uid:it.uid,slot:'pip',x:it.x-pw/2,y:it.y+it.r+LABEL.GAP/2,w:pw,h:ph,gap:LABEL.GAP/2,item:it}); });
  if(LABEL.mem.size>400) LABEL.mem.clear();
  const k=1/z, full=placed.filter(p=>p.slot!=='pip');
  const world=p=>({x:camx+p.x*k,y:camy+p.y*k,w:p.w*k,h:p.h*k});
  const out={all:placed,full:full,boxes:full.map(p=>Object.assign({t:'box',id:'label'},world(p))),asPlaced:full.map(p=>Object.assign(world(p),{q:{kind:'tag'}}))};
  FC.lbOut=out; return out;
}
function labelsPaint(out){
  if(!out||!out.all.length) return;
  cx.save(); cx.setTransform(DPR,0,0,DPR,0,0);
  out.all.forEach(p=>LABEL.paint(cx,p));
  cx.restore();
  const k=1/ZOOM; out.all.forEach(p=>{ if(p.uid&&p.item.kind!=='sun-off') CLICK_BOXES.push({x:camx+p.x*k,y:camy+p.y*k,w:p.w*k,h:p.h*k,uid:p.uid}); });
}
// the audit rows of the labels (screen and world units), as __plateAudit lists them
function labelsAudit(F){
  const L=(F&&F.labels)||[], z=F.zoom, k=1/z;
  return L.filter(p=>p.slot!=='pip').map(p=>{ const d=LABEL_DIR[p.slot]||[4,0], it=p.item;
    return {key:it.key||('tag:'+p.uid),kind:'tag',pri:0,uid:p.uid||null,dock:null,ring:d[1],dir:d[0],slot:p.slot,gap:Math.round(p.gap*100)/100,stroke:LABEL.STROKE,
      px:it.px||LABEL.px(),label:it.kind||'planet',text:[it.text,it.badge?String(it.badge.n):'',it.chip||''].filter(Boolean).join(' · '),
      x:p.x,y:p.y,w:p.w,h:p.h,wx:F.camx+p.x*k,wy:F.camy+p.y*k,ww:p.w*k,wh:p.h*k}; });
}
