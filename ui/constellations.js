// Root conversations in the outer sky. Positions depend on metadata, never on the frame clock.
const CT={points:[],hover:null,selected:null,card:null};
CM.constellationColour=t=>t.state==='working'?'#48F0FF':t.state==='failed'?'#F04438':t.state==='waiting'||(t.unfinished||[]).some(p=>!p.changed_since_read&&/^(him|human|user|you)$/i.test(p.owes||''))?'#F5A623':'#64748B';
CM.constellationAge=t=>Math.max(0,(Date.now()-(Date.parse(t.last||'')||Date.now()))/1000);
CM.constellationVisible=t=>!(['settled','done'].includes(t.state)&&CM.constellationAge(t)>48*3600);
CM.constellationRadius=()=>Math.max(440,...MOONS.map(m=>Math.hypot(m.x-LY.star.x,m.y-LY.star.y)+(m.r||0)))+180;
CM.constellationLayout=()=>{
  const groups=CM.constellations(), base=CM.constellationRadius(), wedge=2*Math.PI/Math.max(1,groups.length);
  return groups.map((g,i)=>{ const centre=-Math.PI/2+i*wedge, list=(g.threads||[]).filter(CM.constellationVisible);
    const threads=list.map((t,j)=>{ const a=centre-wedge*.35+wedge*.7*(j+.5)/Math.max(1,list.length), r=base+Math.floor(j/12)*65+(j%2?34:0);
      return {...t,uid:'thread:'+t.id,lane:g.lane,x:LY.star.x+Math.cos(a)*r,y:LY.star.y+Math.sin(a)*r,angle:a,
        radius:Math.min(10,4+1.5*Math.log2((t.unfinished_n||0)+1)),brightness:1-.65*Math.min(1,Math.max(0,CM.constellationAge(t)-7200)/(7*86400-7200)),colour:CM.constellationColour(t)}; });
    return {...g,threads,halfWedge:wedge/2,angle:centre,rim:base+Math.ceil(list.length/12)*65+90,apex:base+Math.ceil(list.length/12)*65+50}; });   // the header just past the outermost stars: inside it crossed the planet ring
};
CM.drawConstellations2D=function(t){
  CT.points=[]; const groups=CM.constellationLayout();
  groups.forEach(g=>{ const r=g.rim, a=g.angle;
    cx.save(); cx.lineWidth=1/ZOOM; cx.strokeStyle='#38BDF8'; cx.globalAlpha=.12;
    cx.beginPath(); for(const d of [-g.halfWedge,g.halfWedge]){ cx.moveTo(LY.star.x+Math.cos(a+d)*(r-180),LY.star.y+Math.sin(a+d)*(r-180)); cx.lineTo(LY.star.x+Math.cos(a+d)*r,LY.star.y+Math.sin(a+d)*r); } cx.stroke();
    cx.globalAlpha=.45; cx.beginPath(); g.threads.forEach((p,j)=>{ if(j) cx.lineTo(p.x,p.y); else cx.moveTo(p.x,p.y); }); cx.stroke();
    cx.globalAlpha=1; cx.font=wfont(12); cx.textAlign='center'; cx.textBaseline='middle';
    const n=g.threads.filter(p=>!['settled','done'].includes(p.state)).length, k=zs();
    const head=g.label+' · '+n+' open'+((g.count||0)>g.threads.length?' · '+g.count+' total':''), hx=LY.star.x+Math.cos(a)*g.apex, hy=LY.star.y+Math.sin(a)*g.apex;
    const hw=cx.measureText(head).width/2+8*k, hh=11*k;
    cx.fillStyle='rgba(13,19,31,.85)'; cx.fillRect(hx-hw,hy-hh,hw*2,hh*2); cx.fillStyle='#94A3B8'; cx.fillText(head,hx,hy); cx.textBaseline='alphabetic';
    const said=new Set(g.threads.filter(p=>p.colour==='#F5A623'||p.colour==='#F04438').sort((x,y)=>(Date.parse(y.last||'')||0)-(Date.parse(x.last||'')||0)).slice(0,2).map(p=>p.id));
    g.threads.forEach((p,j)=>{ CT.points.push(p); const hot=p.colour==='#F5A623'||p.colour==='#F04438';
      if(hot){ cx.globalAlpha=.22*p.brightness; cx.fillStyle=p.colour; cx.beginPath(); cx.arc(p.x,p.y,p.radius*2.4/ZOOM,0,Math.PI*2); cx.fill(); }   // a corona: it waits on the user
      cx.globalAlpha=p.brightness; cx.fillStyle=p.colour; cx.beginPath(); cx.arc(p.x,p.y,(hot?p.radius:Math.max(2.5,p.radius*.7))/ZOOM,0,Math.PI*2); cx.fill();
      if(CT.hover===p.id||CT.selected===p.id||said.has(p.id)){ cx.font=wfont(11); cx.textAlign='left'; const lx=p.x+(p.radius+6)/ZOOM, ly=p.y+4/ZOOM, txt=CM.shortTitle(p.title,18);
        cx.lineWidth=3/ZOOM; cx.strokeStyle='#0A0E17'; cx.strokeText(txt,lx,ly); cx.fillText(txt,lx,ly); cx.textAlign='center'; }
      if(p.state==='working'&&p.running){ const next=g.threads[j+1]||g.threads[j-1], angle=next?Math.atan2(next.y-p.y,next.x-p.x):p.angle;
        if(VEC) vecCraft('pilot',p.x+18/ZOOM,p.y+12/ZOOM,angle+Math.PI/2,p.colour,p.colour);
        else drawSprite('pilot',p.x+18/ZOOM,p.y+12/ZOOM,angle+Math.PI/2,pilotPal('','#48F0FF')); }
    }); cx.restore();
  });
};
CM.constellationHit=(x,y,slop)=>{
  const p=CT.points.find(p=>Math.hypot(p.x-x,p.y-y)<Math.max(9,p.radius+(slop||0)*ZOOM)/ZOOM);
  return p?{uid:p.uid,kind:'thread-star',name:p.title,thread:p,gx:p.x/TILE,gy:p.y/TILE}:null;
};
CM.threadCardHtml=function(p){
  let h='<h2>'+esc(p.title)+'</h2><p>'+[p.state,p.lane,p.machine,CM.agoOf(p.last)].filter(Boolean).map(esc).join(' · ')+'</p>';
  h+='<p>'+[p.children?Number(p.children)+' child threads':'',Number(p.unfinished_n||0)+' unfinished'].filter(Boolean).join(' · ')+'</p>';
  (p.unfinished||[]).forEach(q=>{ h+='<section'+(q.changed_since_read?' class="finding-changed"':'')+'><h3>'+esc(q.what)+'</h3>'+(q.changed_since_read?'<p>Changed since read</p>':'')+'<blockquote>'+esc(q.evidence)+'</blockquote><p>'+esc(q.when)+' · '+esc(/^(him|human|user|you)$/i.test(q.owes||'')?'on you':'on the agent')+'</p></section>'; });
  if(p.next_step) h+='<h3>Next step</h3><p>'+esc(p.next_step)+'</p>';
  const r=p.resume||{}, url=r.kind==='t3'?t3Url({kind:'ship',t3:{open_url:r.url}}):null;
  if(url) h+='<a class="cmbtn big lead" href="'+attr(url)+'" target="_blank" rel="noopener noreferrer">Resume in T3</a>';
  else if(r.command) h+='<button type="button" class="cmbtn big lead" data-thread-copy>Copy resume command</button><pre>'+esc(r.command)+'</pre>';
  else if(r.kind==='t3') h+='<p>Resume link unavailable for this T3 server.</p>';
  return h;
};
CM.openThread=function(p){
  CT.selected=p.id; const el=document.createElement('article'); el.className='thread-card'; el.innerHTML=CM.threadCardHtml(p); CT.card=el;
  el.addEventListener('click',e=>{ if(e.target.closest('[data-thread-copy]')&&navigator.clipboard) navigator.clipboard.writeText(p.resume.command).then(()=>toast('Resume command copied')).catch(()=>toast('Select and copy the command')); });
  CM.railCard(el,{title:p.title,onClose:()=>{CT.selected=null;}});
};
CM.onInit.push(()=>{ const canvas=document.getElementById('c'); canvas.addEventListener('pointermove',e=>{ const r=canvas.getBoundingClientRect(), p=CM.constellationHit((e.clientX-r.left)/ZOOM+camx,(e.clientY-r.top)/ZOOM+camy,0); CT.hover=p?p.thread.id:null; }); });
