// ui/kinds.js — what each folder is: planetLook(a) -> {purpose, glyph, conditions, seed}, PURPOSE, the condition overlays, GLYPHS,
// the legend chip's content, surfaceAt (shared with the 3D view), window.__spriteAudit.
// Inlined into the page's main script right after the CM registry (render-game.py, the seams): declare functions and
// assign into CM only, nothing that runs page code at load (start-up goes in CM.onInit). Real backslashes here: this
// file is read as it is, not through a Python string. Neutral words only.
//
// Purpose -> body, condition -> overlay: a folder's body says what it holds, never how it is doing. How it is
// doing is drawn over the body: a soft red/amber aura when something waits on the owner, frost and faded colour after 30
// quiet days, a dashed haze shell when it is mostly rebuildable, a dashed outline when it is empty, a cyan ring segment
// filling with progress while work runs there, and lit windows on its night side while a step or an agent works there.
// Ports and ideas, each re-written here (NOTICE carries the licences):
/* port: cosmos (MIT) */ /* port: deep-fold (MIT) */
// eluvade/cosmos with Deep-Fold PixelPlanets: a body as an ordered list of layers (composer.ts:225-254), six stable seeds
// from one hash (seed.ts:188-194), light in three toon bands at steps 0.4/0.6 (base-planet.glsl.ts:35-36), one art pixel
// per screen block (composer.ts:237), log-spiral galaxies for other machines (galaxy.glsl.ts:202-232)
/* port: tsl-textures (MIT) */ // boytchev/tsl-textures: gas-giant latitude bands bent by a domain warp (gas-giant.js:31-61), for Data
/* port: procedural-planets (MIT) */ // dgreenheck/threejs-procedural-planets: five colours cut by four thresholds (index.html:312-331)
/* port: space-3d (Unlicense) */ // wwwtyro/space-3d: a direction-only domain-warped nebula with the displacement started at
// zero (nebula.glsl:33-58), baked once: one recipe and one seed for the 2D backdrop and the 3D sky
// Activity illuminates the night side.
// (earth.fragment.glsl:235-237).

const PURPOSE_KEYS=['code','notes','reference','data','media','config','shared','archive','unsorted'];
// body: the surface id the old code paths know (m.type), surface: the body's own name, profile: its layers in order (the
// composer below runs exactly these, KIND_LAYERS), core: the lit sphere's share of the radius when parts ring it
const PURPOSE={
  code:     {name:'Code',       glyph:'code',    body:'city',     surface:'city',          shape:'sphere', profile:['base','city','lights','atmosphere'],
             look:'City world: streets by day, windows lit at night', rule:'mostly code files, or a git repository that is not mostly notes'},
  notes:    {name:'Notes',      glyph:'notes',   body:'ocean',    surface:'ocean',         shape:'sphere', profile:['ocean','bands','clouds'],
             look:'Ocean world with page-white bands', rule:'mostly notes and documents'},
  reference:{name:'Reference',  glyph:'book',    body:'desert',   surface:'ringed-desert', shape:'sphere', profile:['desert','dunes','ring'],
             look:'Ringed desert (the ring is a shelf)', rule:'reference material: read, not written'},
  data:     {name:'Data',       glyph:'data',    body:'gas',      surface:'gas-bands',     shape:'sphere', profile:['base','bands-warp','storm'],
             look:'Banded gas giant with a storm', rule:'half or more of its files are tables, databases or arrays'},
  media:    {name:'Media',      glyph:'media',   body:'aurora',   surface:'aurora',        shape:'sphere', profile:['base','aurora'],
             look:'Aurora swirl', rule:'half or more of its files are images, audio or video'},
  config:   {name:'Settings',   glyph:'config',  body:'crystal',  surface:'crystal',       shape:'gem',    profile:['facets','edges'],
             look:'Faceted crystal', rule:'settings and configuration'},
  shared:   {name:'Synced',     glyph:'sync',    body:'station',  surface:'station',       shape:'sphere', profile:['ring','dock','core'], core:.48,
             look:'Ring station with a dock', rule:'a shared folder or a bus: things pass through it'},
  archive:  {name:'Archive',    glyph:'archive', body:'asteroid', surface:'crate-belt',    shape:'lumpy',  profile:['rubble','crates'], core:.6,
             look:'Crates riding a rubble belt', rule:'retired things, kept for later'},
  unsorted: {name:'Unknown',    glyph:'unknown', body:'rock',     surface:'rock',          shape:'lumpy',  profile:['rock','craters'],
             look:'Plain grey rock', rule:'nothing tells yet what it holds'},
};
const KIND_PURPOSE={code:'code',knowledge:'notes',reference:'reference',data:'data',media:'media',config:'config',shared:'shared',bus:'shared',archive:'archive',unknown:'unsorted'};
const PURPOSE_KIND={code:'code',notes:'knowledge',reference:'reference',data:'data',media:'media',config:'config',shared:'shared',archive:'archive',unsorted:'unknown'};
// the emblems an owner may give a planet (areas.json "glyph", config.AREA_GLYPHS); a purpose's own emblem when the user gave none
const GLYPHS=['code','notes','money','health','career','journal','learning','archive','sync','tools','home','star'];
const GLYPH_WORD={code:'Code',notes:'Notes',money:'Money',health:'Health',career:'Career',journal:'Journal',learning:'Learning',archive:'Archive',
  sync:'Sync',tools:'Tools',home:'Home',star:'Star',book:'Reference',data:'Data',media:'Media',config:'Settings',unknown:'Unknown'};
// the neutral planet colours (skins/neutral.json "planets" carries the same; a skin or a theme may bring its own)
const PLANETS_NEUTRAL={
  // a lit blue-grey world, not a grey rock: a navy shadow and street colour, blue-grey roofs, a warm cream for the brightest, amber
  // windows. The Not sorted rock (below) stays a plain grey, so at 14 px a code planet and a rock are never taken for each other
  code:     {colours:['#0b1530','#2b4170','#7189b4','#b3c6e0','#e3ecf8'],thresholds:[.36,.44,.55,.7],glow:'#ffb347'},
  notes:    {colours:['#08214a','#0f4a8a','#1f78c0','#5fb3e6','#eef3f8'],thresholds:[.36,.5,.62,.8],glow:'#8fd3ff'},
  reference:{colours:['#5a3418','#94562a','#c48544','#e0b26b','#f3dca6'],thresholds:[.22,.42,.62,.82],glow:'#ffb46b'},
  data:     {colours:['#2a1f5c','#5143a6','#8b6fd0','#d39ad6','#f4e3d4'],thresholds:[.3,.46,.6,.76],glow:'#b98cff'},
  media:    {colours:['#0b1030','#1b2458','#2c8f7a','#4fe0a8','#e070c8'],thresholds:[.4,.6,.74,.88],glow:'#4fe0a8'},
  config:   {colours:['#0f3f4a','#1f7f86','#47bfb6','#a2e9df','#f0fffc'],thresholds:[.3,.5,.66,.84],glow:'#7fffe8'},
  shared:   {colours:['#1d2733','#3a4a5c','#6b7f94','#b7c4d2','#5ff0ff'],thresholds:[.3,.5,.7,.9],glow:'#5ff0ff'},
  archive:  {colours:['#2e1f14','#5a3d25','#8a6239','#c2925a','#e8c48e'],thresholds:[.3,.48,.66,.84],glow:'#ffb066'},
  unsorted: {colours:['#2a2c33','#474b55','#6c707c','#9a9ea8','#c9ccd3'],thresholds:[.3,.48,.66,.84],glow:'#9a9ea8'},
};
const KIND_STATUS={broken:'#FF5A5F',waiting:'#FFB547',working:'#6FB7FF',done:'#4ADE80',idle:'#6B7684',frost:'#d8f4ff',sun:'#F5B342'};
// the sun's light: from the planet toward the sun, lifted 12 degrees toward the viewer. said 35 degrees; at 35 the
// light reaches two thirds of the disc and the far half is only 1.3x darker than the sun half (check0 measured it on the bench),
// so the crescent did not read. At 12 degrees half the disc is lit, a quarter is night, and the sun half is about 3x the far
// half. CM.planetLight() hands the same numbers to the 3D view (the 3D module's first frame uses the same per-planet light).
const KIND_LIGHT={el:12*Math.PI/180,steps:48};
const KIND_TOON=[.4,.6];                            // the toon steps (cosmos base-planet.glsl.ts:35-36)

// ---- seeds: one hash of the path, six stable seeds from it (cosmos seed.ts:188-194) ----
function kindsFnv(s){ let h=0x811c9dc5; s=String(s==null?'':s); for(let i=0;i<s.length;i++){ h^=s.charCodeAt(i); h=Math.imul(h,0x01000193); } return h>>>0; }
function hashSeed(base,layer){
  let h=(Math.imul(base|0,2654435761)+Math.imul(layer|0,2246822519))|0;
  h=Math.imul((h>>>16)^h,0x45d9f3b); h=Math.imul((h>>>16)^h,0x45d9f3b); h=(h>>>16)^h;
  return ((h>>>0)%10000)/100+0.1;
}
function kindsSeeds(path){ const b=kindsFnv(path); return [1,2,3,4,5,6].map(i=>hashSeed(b,i)); }
const kindsInt=v=>Math.floor(v*997)%99991;

// ---- palettes: the theme's "planets" block, else (classic) the skin's, else the neutral one; any other theme without a
// block takes its colours from its own surfaces, so a one-colour theme stays one colour ----
function kindsLum(h){ const c=[1,3,5].map(i=>parseInt(String(h).slice(i,i+2),16)/255).map(v=>v<=.03928?v/12.92:Math.pow((v+.055)/1.055,2.4)); return .2126*c[0]+.7152*c[1]+.0722*c[2]; }
// the theme surface a purpose's body takes its colours from: its own when the theme has it (themepacks: aurora, crystal and
// asteroid are optional surfaces), else the nearest surface every theme carries, so Media, Config, Archive and Not sorted never
// share one palette in a theme without a "planets" block (aurora -> toxic glow, crystal -> ice, asteroid -> the belt)
const KIND_SURF_FALLBACK={aurora:'toxic',crystal:'ice',asteroid:'belt'};
function kindsSurface(p){
  const b=PURPOSE[p]?PURPOSE[p].body:'rock';
  return STYLE[b]?b:(KIND_SURF_FALLBACK[b]&&STYLE[KIND_SURF_FALLBACK[b]])?KIND_SURF_FALLBACK[b]:'rock';
}
function planetPal(p){
  const own=PACK&&PACK.planets&&PACK.planets[p];
  if(own) return {name:THEME_NAME,colours:own.colours,thresholds:own.thresholds,glow:own.glow||own.colours[3]};
  if(!PACK||THEME_NAME==='classic'){
    const s=SKIN&&SKIN.planets&&SKIN.planets[p];
    if(s) return {name:String(SKIN.name||'skin'),colours:s.colours,thresholds:s.thresholds,glow:s.glow||s.colours[3]};
    const n=PLANETS_NEUTRAL[p]; return {name:'neutral',colours:n.colours,thresholds:n.thresholds,glow:n.glow};
  }
  const st=STYLE[kindsSurface(p)]||{a:'#6c707c',b:'#474b55',c:'#9a9ea8',glow:'#9a9ea8'};
  const cols=[st.a,st.b,st.c,st.d||mixPixel(st.a,st.c,.5),st.glow].sort((a,b)=>kindsLum(a)-kindsLum(b));
  return {name:THEME_NAME,colours:cols,thresholds:PLANETS_NEUTRAL[p].thresholds,glow:st.glow,surface:kindsSurface(p)};
}
function kindsIdx(P,h){ const t=P.thresholds; let k=0; while(k<4&&h>=t[k]) k++; return k; }

// ---- what a planet is: purpose (its body), glyph, conditions (overlays), seeds ----
const KINDS_FRAME={t:-1,per:null,work:null,seats:null};
// the agents working at each planet: a seat or ship whose status is WORKING, docked there (render-game.py place() docks a seat
// whose working folder is inside the planet's, and a dock override). CM.workingPlanets() (workflow steps, running threads) does
// not count them, so a working agent would leave its planet dark without this.
function kindsSeatsAt(){
  const list=(typeof deck!=='undefined'&&deck!=='home'&&typeof remoteUnits!=='undefined'&&Array.isArray(remoteUnits))?remoteUnits:(typeof units!=='undefined'&&Array.isArray(units)?units:[]), at={};
  list.forEach(u=>{ if(u&&u.docked_at&&isShip(u)&&!u.remote&&/^working$/i.test(String(u.status||''))) at[u.docked_at]=(at[u.docked_at]||0)+1; });
  return at;
}
function kindsPer(){
  if(KINDS_FRAME.t===FRAME_T&&KINDS_FRAME.per) return KINDS_FRAME;
  const DECIDE=['ledger','board','quest','fleet'], per={};
  (typeof skyContacts==='function'?skyContacts():[]).forEach(c=>{ const k=c.planet_uid; if(!k) return; const n=per[k]=per[k]||{need_you:0,problems:0,decisions:0};
    if(DECIDE.includes(c.source)) n.decisions++; else if(c.state==='failed'||c.state==='stale') n.problems++; else n.need_you++; });
  let work=new Set(); try{ work=CM.workingPlanets(); }catch(e){}
  KINDS_FRAME.t=FRAME_T; KINDS_FRAME.per=per; KINDS_FRAME.work=work; KINDS_FRAME.seats=kindsSeatsAt(); return KINDS_FRAME;
}
// the progress of the work at a planet, 0..1 (a workflow step's done/total), else null
function kindsProgress(uid){
  let best=null;
  try{ CM.wfList().forEach(w=>(w&&Array.isArray(w.steps)?w.steps:[]).forEach(st=>{
    if(st&&st.state==='working'&&st.planet_uid===uid&&st.progress&&st.progress.total>0){ const f=Math.max(0,Math.min(1,st.progress.done/st.progress.total)); best=best==null?f:Math.max(best,f); } })); }catch(e){}
  return best;
}
function planetLook(a){
  if(!a) return null;
  const uid=a.uid, u=(typeof unitByUid==='function'&&unitByUid(uid))||a, m=(MOONS||[]).find(x=>x.uid===uid)||a;
  const kind=String(u.area_kind||m.kind||'unknown'), purpose=u.alias_of?'unsorted':(KIND_PURPOSE[kind]||'unsorted');
  const glyphSet=GLYPHS.includes(u.glyph), glyph=glyphSet?u.glyph:PURPOSE[purpose].glyph;
  const F=kindsPer(), n=F.per[uid]||{need_you:0,problems:0,decisions:0}, life=String(u.life||m.life||'');
  const conditions=[];
  if(life==='EMPTY'||life==='ALIAS') conditions.push('empty');
  else if(life==='COLD') conditions.push('quiet');
  if(Number(u.regen_share||0)>=50) conditions.push('rebuildable');
  if(n.problems||n.need_you||n.decisions) conditions.push('waiting');
  // working: a workflow step or a running thread here (CM.workingPlanets, m.allies), or an agent docked here at work
  const agents=(F.seats&&F.seats[uid])||0, working=F.work.has(uid)||(m.allies||0)>0||agents>0;
  if(working) conditions.push('working');
  const seeds=kindsSeeds(u.path||m.label||uid), P=planetPal(purpose);
  return {uid:uid,kind:'area',label:String(m.label||u.name||uid),purpose:purpose,name:PURPOSE[purpose].name,glyph:glyph,glyphSet:glyphSet,
    conditions:conditions,seed:seeds[0],seeds:seeds,profile:PURPOSE[purpose].profile.slice(),surface:PURPOSE[purpose].surface,shape:PURPOSE[purpose].shape,
    toonBands:3,palette:P.name,pal:P,guessed:u.kind_guessed!==false,
    health:n.problems?'broken':(n.need_you||n.decisions)?'waiting':life==='COLD'?'quiet':'ok',counts:n,agents:agents,
    nightLights:working,progress:working?kindsProgress(uid):null,worst:!!m.worst};
}

// ---- one surface function for every body, old and new: the 2D art and the 3D bake both call it ----
// Old styles (city, ocean, desert, ice, toxic, blight, gas, star, rock, anything else) return exactly the cell the old
// bodyArt drew: {c, e, lvl}. A purpose ('code' ... 'unsorted') returns {h, k, c, e, en, nrm}: h the height field 0..1, k the
// palette index, e an emissive colour (en: only on the night side), nrm a facet normal for the crystal.
function surfaceAt(style,P,lon,lat,seed,o){
  o=o||{};
  if(PURPOSE[style]) return purposeSurface(style,P,lon,lat,seed,o);
  const s=seed, phase=o.phase||0, lon0=o.lon0==null?lon-phase:o.lon0, light=o.light==null?.5:o.light, lw=((lon%TAU)+TAU)%TAU;
  let lvl=o.lvl==null?Math.max(0,Math.min(4,Math.floor(light*4+.6))):o.lvl, base, emit=null;
  if(style==='city'){
    const n=sfbm(lon,lat,s,2.0);
    base=n>.53?(n>.70?'#b6f59a':P.b):(n<.36?mixPixel(P.a,'#062a8a',.45):P.a);
    if(Math.abs(lat)>1.18) base=P.c;
    const cl=sfbm(lon0+phase*1.45,lat*1.25,s+17,3.1);
    if(cl>.64){ base=P.c; lvl=Math.min(4,lvl+1); }
    else if(n>.53&&light<.08&&hash3(Math.floor(lw*10),Math.floor(lat*10),3,s)>.78) emit=P.d;
  }else if(style==='ocean'){
    const n=sfbm(lon+Math.sin(lat*3+s)*.5,lat,s,2.4);
    base=n>.56?P.b:(n<.38?mixPixel(P.a,'#003a8c',.45):P.a);
    if(sfbm(lon0+phase*1.6,lat*1.4,s+23,3.4)>.67){ base=P.c; lvl=Math.min(4,lvl+1); }
  }else if(style==='desert'){
    const v=Math.sin(lat*12+sfbm(lon,lat,s,1.6)*7);
    base=v>.2?P.a:(v>-.5?mixPixel(P.a,P.b,.5):P.b);
    if(sfbm(lon0+phase*1.3,lat,s+29,2.6)>.7) base=P.c;
  }else if(style==='ice'){
    const n=sfbm(lon,lat,s,2.8); base=P.a;
    if(Math.abs(n-.5)<.04) base=P.b;
    if(Math.abs(lat)>1.0) base=P.c;
  }else if(style==='toxic'){
    const n=sfbm(lon,lat,s,2.6); base=n>.58?P.b:(n<.36?P.c:P.a);
  }else if(style==='blight'){
    const n=sfbm(lon,lat,s,2.2); base=P.a;
    if(Math.abs(n-.5)<.06) base=P.b;
    if(Math.abs(n-.5)<.02) emit=P.c;
  }else if(style==='gas'){
    const lg=lon0+phase*(1+.35*Math.sin(lat*4));
    const v=lat*5.5+(sfbm(lg,lat,s,1.5)-.5)*2.4, bands=[P.a,P.b,P.d,P.c,P.a,P.d];
    base=bands[((Math.floor(v)%6)+6)%6];
    const dl=Math.hypot(((((lg-1.2)%TAU)+TAU)%TAU)-Math.PI,(lat-.35)*2.2);
    if(dl<.32) base='#ff3b8a';
  }else if(style==='star'){
    const n=sfbm(lon,lat,s,3.6); base=n>.6?P.c:(n<.4?P.b:P.a);
    const rr=o.rr||0; lvl=rr>.82?2:(rr>.5?3:4);
  }else{
    const n=sfbm(lon,lat,s,2.4); base=n>.52?P.b:P.a;
    if(n>.7) base=P.c;
    if(style==='rock'&&sfbm(lon,lat,s+5,5.5)<.3){ base=P.b; lvl=Math.max(0,lvl-1); }
  }
  return {c:base,e:emit,lvl:lvl};
}
// a crater field on lon/lat cells: -1 on a crater floor, +1 on its rim, 0 elsewhere
function kindsCrater(lw,lat,seed,cells){
  const u=lw/TAU*cells, v=(lat/Math.PI+.5)*cells*.5, i=Math.floor(u), j=Math.floor(v);
  if(hash3(i,j,11,seed)<.5) return 0;
  const cxp=i+.25+hash3(i,j,12,seed)*.5, cyp=j+.25+hash3(i,j,13,seed)*.5, r=.18+hash3(i,j,14,seed)*.2, d=Math.hypot((u-cxp)*Math.cos(lat),v-cyp);
  return d<r*.7?-1:d<r?1:0;
}
// the crystal's facets: fixed points on the sphere per seed (a jittered Fibonacci sphere)
const KIND_FACETS=new Map();
function kindsFacets(seed){
  let f=KIND_FACETS.get(seed); if(f) return f; f=[]; const N=22, ga=Math.PI*(3-Math.sqrt(5));
  for(let i=0;i<N;i++){ const y=1-(i+.5)/N*2+(hash3(i,1,2,seed)-.5)*.12, r=Math.sqrt(Math.max(0,1-y*y)), a=i*ga+(hash3(i,3,4,seed)-.5)*.5;
    const v=[Math.cos(a)*r,y,Math.sin(a)*r], l=Math.hypot(v[0],v[1],v[2]); f.push(v.map(x=>x/l)); }
  KIND_FACETS.set(seed,f); return f;
}
// ---- the profile composer (cosmos composer.ts:225-254): a body is its purpose's layers, run in order ----
// A surface layer (surf) reads and writes one cell {h, k, c, e, en, w, nrm}: h the height 0..1, k the palette index, e an
// emissive colour (en: it shows only where it is dark; w its weight 0..1, the share of windows that light), nrm a facet normal.
// X is the sample: lon, lat (and lw, cl, lon0, phase), the palette (P, C), the detail (art pixels across the radius, so a
// pattern stays readable at 14 px and fine at 400) and X.s, the layer's own seed: layer i of a profile takes seeds[i] of the
// six. A layer with `part` draws around the body (kindsBody: rings, the dock, the belt, the crates), one with `rim` at its
// limb (purposeArt). Editing a purpose's profile changes what is drawn; surfaceAt(..., {profile}) runs any list.
// c.fill marks a cell of a body's plain ground (no street, window, line, band, ribbon, facet or crater): a tiled theme lays its
// own texture there and nowhere else, so the purpose's structure (what makes a city a city) survives every theme
const KIND_LAYERS={
  // a height field cut by the palette's thresholds (a city world's districts, the dim ground under an aurora, a gas giant's fill)
  base:{surf(c,X){ const o={code:[1.6,4],data:[1.5,2],media:[1.8,1]}[X.p]||[2.0,4];
    c.h=sfbm(X.lon,X.lat,X.s,o[0]); c.k=Math.min(o[1],kindsIdx(X.P,c.h)); c.c=X.C[c.k]; c.fill=true;
    // under an aurora: a dusky blue-green ground, light enough for the three bands to show (the palette's two darkest alone
    // left the globe one flat dark disc)
    if(X.p==='media'){ c.k=c.h>.5?1:0; c.c=c.k?mixPixel(X.C[1],X.C[2],.8):mixPixel(X.C[1],X.C[2],.45); } }},
  // a city world ("city world, window lights on the night side"): built over from pole to pole, a few dark pools at
  // most (no seas: blue seas round grey land read as an Earth). The city is districts (cells of a coarse 3-D Worley field on
  // the sphere, so nothing lines up with a latitude: a latitude grid read as a disco ball), each with its own street grid
  // turned its own way, rectangular blocks about four art pixels a side and avenues along the district borders. A block's
  // shade comes from its district's density (suburb, town, downtown), not from its own dice (per-block shades read as gravel).
  // Under 12 art pixels a radius the blocks are smaller than the pixels: the ground is an even grain of roofs and streets, and
  // the night lights carry the read there.
  city:{surf(c,X){
    const det=X.detail||12, v=X.vec;
    // a few dark pools (the lowest of the base's heights) once the blocks show, never oceans
    if(c.h<.3&&det>=12){ c.sea=true; c.c=mixPixel(X.C[0],X.C[1],.35); c.fill=true; return; }
    const D=kindsWorley(v[0]*2.1,v[1]*2.1,v[2]*2.1,X.s), dens=sfbm(X.lon,X.lat,X.s+3,3.4)+(c.h-.5)*.8+(D.id-.5)*.12;
    c.fill=false; c.dense=dens; c.block=D.id;
    if(det>=12&&D.f2-D.f1<Math.min(.16,1.9*2.1/det)){ c.k=2; c.c=mixPixel(X.C[2],X.C[0],.6); c.street=true; c.avenue=true; return; }
    const lvl=dens>.55?4:dens>.38?3:2;
    if(det>=12){
      // the district's own grid: the point's offset from the district's centre on the tangent plane there, turned by the
      // district's angle, in lots of about 4.6 art pixels. Each lot holds one building, a roof of its own size set inside the lot,
      // so the gaps between roofs are the streets (one to two art pixels): a town seen from above, not a mesh of equal tiles
      const n=D.n, ux=Math.abs(n[1])>.9?[1,0,0]:[n[2],0,-n[0]], ul=Math.hypot(ux[0],ux[1],ux[2])||1, t1=ux.map(x=>x/ul);
      const t2=[n[1]*t1[2]-n[2]*t1[1],n[2]*t1[0]-n[0]*t1[2],n[0]*t1[1]-n[1]*t1[0]], d=[v[0]-n[0],v[1]-n[1],v[2]-n[2]];
      const a=D.id*Math.PI, ca=Math.cos(a), sa=Math.sin(a), u0=d[0]*t1[0]+d[1]*t1[1]+d[2]*t1[2], w0=d[0]*t2[0]+d[1]*t2[1]+d[2]*t2[2];
      const G=det/4.6, u=(u0*ca-w0*sa)*G, w=(u0*sa+w0*ca)*G, iu=Math.floor(u), iw=Math.floor(w), fu=u-iu, fw=w-iw, did=Math.floor(D.id*977);
      const hb=k=>hash3(iu,iw,did*7+k,X.s), x0=.06+hb(1)*.11, x1=.94-hb(2)*.13, y0=.06+hb(3)*.11, y1=.94-hb(4)*.13;
      c.bu=iu; c.bw=iw;
      if(fu<x0||fu>=x1||fw<y0||fw>=y1||(lvl===2&&hb(5)>.8)){ c.c=mixPixel(X.C[2],X.C[1],lvl===2?.1:lvl===3?.2:.3); c.k=2; c.street=true; return; }
      const r=hb(6), k=Math.max(2,Math.min(4,lvl+(r>.84?1:r<.2?-1:0)));
      c.k=k; c.c=X.C[k];
      // the roof's sun-away edge one step darker, so a block reads as a building with height
      if(fu>=x1-.22||fw>=y1-.22) c.c=mixPixel(X.C[k],X.C[0],.22);
    }else{
      // under 12 art pixels a radius a block is about one art pixel: each is a dark street or a roof, mid or bright (more bright
      // ones downtown), so the ground is an even grain of roofs, never a smooth plain, a sea, or broad light and dark patches
      // (those read as the maria of a moon)
      const g=det*1.15, hb=hash3(Math.floor(v[0]*g),Math.floor(v[1]*g),Math.floor(v[2]*g)+311,X.s);
      if(hb<.3){ c.k=1; c.c=X.C[1]; c.street=true; return; }
      // mostly mid roofs, a third light, a few pale ones downtown: a lit blue-grey world, about as bright as the same body drawn
      // large (a small one that came out twice as bright read as a snowball), never a plain of one colour
      const r=(hb-.3)/.7, k=r>.95-Math.max(0,Math.min(.1,(dens-.45)*.3))?4:r>.65?3:2; c.k=k; c.c=X.C[k];
    }
  }},
  // windows: one window cell about an art pixel across inside the blocks, more of them lit in the dense districts, so the lights
  // cluster the way a city's do at night, white-gold downtown and dim amber in the suburbs. purposeArt lights them by the toon
  // band (KIND_WIN), as points: an idle city's scattered over its night side and a few at dusk; packed, brighter and many on the
  // terminator while work runs there (X.night); never on the day side. The streets stay dark; the avenues carry a few lamps.
  lights:{surf(c,X){ if(c.sea) return;
    const det=X.detail||12, g=det*.95, v=X.vec, h=hash3(Math.floor(v[0]*g),Math.floor(v[1]*g),Math.floor(v[2]*g)+7919,X.s), d=Math.max(0,Math.min(1,(c.dense-.3)/.4));
    if(c.avenue){ if(h>.55){ c.e=mixPixel(X.P.glow,X.C[0],.35); c.en=true; c.w=.9; } return; }
    if(c.street) return;
    // warm: amber in the suburbs (the glow pushed toward orange), a warm white only downtown (never a blue-white point)
    const amber=mixPixel(X.P.glow,'#ff9a3c',.3), downtown='#fff0c8';
    c.e=X.night?mixPixel(amber,downtown,.2+d*.4):mixPixel(amber,downtown,d*.3); c.en=true; c.w=h*.55+d*.45; }},
  atmosphere:{rim:true},
  ocean:{surf(c,X){ c.h=sfbm(X.lon+Math.sin(X.lat*3+(X.s%7))*.4,X.lat,X.s,1.7); c.k=c.h>.6?3:c.h>.42?2:1; c.c=X.C[c.k]; c.fill=true; }},
  // ruled lines: thin, evenly spaced, page white, following the latitudes (the axial tilt bends them round the sphere; a gas
  // giant's bands are broad, warped and coloured)
  // On a small body the lines thin out to fit the pixels: at least three art pixels apart and about one wide, so they stay whole
  // lines round the globe (at 9 art pixels the full set came out as broken dashes)
  bands:{surf(c,X){ const det=X.detail||12, K=Math.min(4.6,Math.max(2.2,det/3.3)), th=Math.min(.36,Math.max(.17,1.0*K/det));
    const v=Math.sin(X.lat)*K+(X.s%7)/7, f=v-Math.floor(v); if(f<th){ c.k=4; c.c=X.C[4]; c.line=true; c.fill=false; } }},
  clouds:{surf(c,X){ if(!c.line&&sfbm(X.lon0+X.phase*1.6,X.lat*1.4,X.s,3.4)>.76){ c.c=mixPixel(c.c,X.C[3],.5); c.fill=false; } }},
  desert:{surf(c,X){ const v=Math.sin(X.lat*12+sfbm(X.lon,X.lat,X.s,1.6)*7); c.h=(v+1)/2; c.k=kindsIdx(X.P,c.h); c.c=X.C[c.k]; c.fill=true; }},
  dunes:{surf(c,X){ if(sfbm(X.lon0+X.phase*1.3,X.lat,X.s,2.6)>.72){ c.c=X.C[4]; c.k=4; c.fill=false; } }},
  ring:{part:true},
  // gas giant: latitude bands bent by a domain warp (tsl gas-giant.js:31-61), the height stretched so the bands run through
  // the whole palette (dark and light bands side by side read as a gas giant at 9 art pixels; mid lilac alone did not)
  'bands-warp':{surf(c,X){
    const N=(x,y,z)=>vnoise(x,y,z,X.s)*2-1, S=2.4, off=(X.s%89)*1.37;
    const px=X.cl*Math.sin(X.lon)*S+off, py=Math.sin(X.lat)*S*2.1, pz=X.cl*Math.cos(X.lon)*S+off*.7;
    const turb=Math.abs(5*.07*(N(1,py*.5+1,1)+.5*N(1,py+1,1)+.25*N(2,py*2+1,2)));
    const spot=Math.max(0,Math.min(1,10*Math.pow((N(px/4,py/4,pz/4)+1)/2,10)));
    const w=turb*Math.exp(2*spot)*.55, qx=px+N(px,py,pz)*w, qy=py+N(py,px,pz)*w, qz=pz+N(py,pz,px)*w;
    // alternating bands of uneven width (a sine of the warped latitude, its phase pushed by noise), each band's shade nudged
    const band=(N(0,qy*.8,0)+1)/2, fine=(N(0,qy*3.6,.5)+1)/2, streak=N(qx*.6,qy*6,qz*.6)*.015, wave=Math.sin(qy*2.3+(band-.5)*3);
    c.h=Math.max(0,Math.min(1,.5+wave*.42+(fine-.5)*.3+streak)); c.k=kindsIdx(X.P,c.h); c.c=X.C[c.k]; c.fill=false; }},
  // one oval storm riding the bands, placed by its own seed
  storm:{surf(c,X){ const lc=(X.s%628)/100, la=((Math.floor(X.s/7)%100)/100-.5)*.9, dl=(((X.lon-lc)%TAU)+TAU+Math.PI)%TAU-Math.PI;
    const d=Math.hypot(dl*X.cl*.62,(X.lat-la)*1.7), R=.2+.9/Math.max(6,X.detail||12); if(d<R){ c.c=mixPixel(X.C[4],X.P.glow,d<R/2?.55:.25); c.k=4; } }},
  // aurora: a glowing curtain round each pole (the tilt shows the north one as a crown), green at its core with a pink upper
  // fringe, a dim skirt below, and fainter swirls nearer the equator. It glows (c.aur): full on the night side, faint on the day
  // side, as an aurora does, so the bright crown never reads as the lit side
  aurora:{surf(c,X){
    const al=Math.abs(X.lat), wob=sfbm(X.lon,X.lat,X.s,2.2)-.5, oval=.98+.09*Math.sin(X.lon*3+(X.s%13))+.2*wob, d=(al-oval)/.12;
    const sw=(al-(.58+.1*Math.sin(X.lon*2+(X.s%17))+.25*wob))/.07;
    if(d>=-.9&&d<=.9){ c.k=3; c.c=X.C[3]; c.e=c.c; c.aur=1; c.h=.9; c.fill=false; }
    else if(d>.9&&d<=1.6){ c.k=4; c.c=X.C[4]; c.e=c.c; c.aur=1; c.h=1; c.fill=false; }
    else if(d<-.9&&d>=-1.9){ c.k=2; c.c=X.C[2]; c.h=.7; c.fill=false; }
    else if(Math.abs(sw)<.8){ c.e=mixPixel(c.c,X.C[3],.5); c.aur=.6; c.fill=false; } }},
  // faceted crystal: flat facets (a normal each, turned back into the view so the light falls on whole facets), bright edges
  // where two facets meet
  facets:{surf(c,X){ const F=kindsFacets(X.s), v=[X.cl*Math.sin(X.lon),Math.sin(X.lat),X.cl*Math.cos(X.lon)];
    let b1=-2,b2=-2,i1=0; F.forEach((f,i)=>{ const d=f[0]*v[0]+f[1]*v[1]+f[2]*v[2]; if(d>b1){ b2=b1; b1=d; i1=i; } else if(d>b2) b2=d; });
    c.k=1+Math.floor(hash3(i1,1,9,X.s)*3); c.c=X.C[c.k]; c.h=c.k/4; c.gap=b1-b2; c.facet=i1; c.fill=false;
    const f=F[i1], lf=Math.atan2(f[0],f[2])-X.phase, bf=Math.asin(Math.max(-1,Math.min(1,f[1])));
    c.nrm=X.view([Math.cos(bf)*Math.sin(lf),Math.sin(bf),Math.cos(bf)*Math.cos(lf)]); c.facetK=.62; }},
  edges:{surf(c,X){ if(c.gap==null) return; if(c.gap<.03){ c.k=4; c.c=X.C[4]; c.h=1; }
    else if(X.night&&hash3(c.facet,2,7,X.s)>.6&&c.gap<.09){ c.e=X.P.glow; c.en=true; c.w=1; } }},
  dock:{part:true},
  // the station core: panels, seams, lamps (lamps light on its night side, more of them while work runs there)
  core:{surf(c,X){ const gu=X.lw/TAU*16, gv=(X.lat/Math.PI+.5)*8, pane=hash3(Math.floor(gu),Math.floor(gv),2,X.s);
    c.k=pane>.66?3:pane>.33?2:1; c.c=X.C[c.k]; c.h=c.k/4; c.fill=false;
    if(gu-Math.floor(gu)<.14||gv-Math.floor(gv)<.14){ c.k=0; c.c=X.C[0]; c.h=0; }
    else if(hash3(Math.floor(gu*2),Math.floor(gv*2),3,X.s)>(X.night?.6:.88)){ c.e=X.C[4]; c.en=true; c.w=1; } }},
  // archive rubble: a cratered core (its part is the belt of grains and rocks around it)
  rubble:{part:true,surf(c,X){ c.h=sfbm(X.lon,X.lat,X.s,2.6); c.k=Math.max(1,Math.min(3,kindsIdx(X.P,c.h))); c.c=X.C[c.k]; c.fill=true;
    const cr=kindsCrater(X.lw,X.lat,X.s+101,10); if(cr){ c.fill=false; c.k=cr<0?Math.max(0,c.k-1):Math.min(4,c.k+1); c.c=X.C[c.k]; } }},
  crates:{part:true},
  rock:{surf(c,X){ c.h=sfbm(X.lon,X.lat,X.s,2.2); c.k=Math.max(1,Math.min(3,kindsIdx(X.P,c.h))); c.c=X.C[c.k]; c.fill=true; }},
  craters:{surf(c,X){ const cr=kindsCrater(X.lw,X.lat,X.s,7); if(cr){ c.fill=false; c.k=cr<0?Math.max(0,c.k-1):Math.min(4,c.k+1); c.c=X.C[c.k]; } }},
};
// a 3-D Worley field: the distances to the nearest and second nearest feature point (one jittered point per unit cell) and the
// nearest one's own hash; sampled on the sphere it has no seam and no pole
function kindsWorley(x,y,z,s){
  const xi=Math.floor(x), yi=Math.floor(y), zi=Math.floor(z); let f1=9, f2=9, bx=0, by=0, bz=0;
  for(let i=-1;i<=1;i++) for(let j=-1;j<=1;j++) for(let k=-1;k<=1;k++){
    const cx=xi+i, cy=yi+j, cz=zi+k, dx=cx+hash3(cx,cy,cz,s)-x, dy=cy+hash3(cx,cy,cz,s+1)-y, dz=cz+hash3(cx,cy,cz,s+2)-z, d=dx*dx+dy*dy+dz*dz;
    if(d<f1){ f2=f1; f1=d; bx=cx; by=cy; bz=cz; } else if(d<f2) f2=d; }
  const px=bx+hash3(bx,by,bz,s), py=by+hash3(bx,by,bz,s+1), pz=bz+hash3(bx,by,bz,s+2), pl=Math.hypot(px,py,pz)||1;
  return {f1:Math.sqrt(f1),f2:Math.sqrt(f2),id:hash3(bx,by,bz,s+3),n:[px/pl,py/pl,pz/pl]};
}
// six integer seeds for the layers: from the look's six (planetLook seeds) or, given one number, derived from it the same way
function kindsLayerSeeds(seed,seeds){
  if(Array.isArray(seeds)&&seeds.length) return seeds.map(v=>Number.isInteger(v)?v:kindsInt(v));
  const b=seed|0; return [b].concat([2,3,4,5,6].map(i=>kindsInt(hashSeed(b,i))));
}
// body space -> view space for a body whose north pole leans t radians toward the viewer (purposeArt's axial tilt)
function kindsToView(v,t){ if(!t) return v; const ct=Math.cos(t), st=Math.sin(t); return [v[0],v[1]*ct+v[2]*st,-v[1]*st+v[2]*ct]; }
function purposeSurface(p,P,lon,lat,seed,o){
  const prof=o.profile||PURPOSE[p].profile, seeds=kindsLayerSeeds(seed,o.seeds), phase=o.phase||0, tilt=o.tilt||0;
  const X={p:p,P:P,C:P.colours,lon:lon,lat:lat,lw:((lon%TAU)+TAU)%TAU,cl:Math.cos(lat),phase:phase,lon0:o.lon0==null?lon-phase:o.lon0,
    night:!!o.night,detail:o.detail||12,s:seeds[0],view:v=>kindsToView(v,tilt)};
  X.vec=[X.cl*Math.sin(lon),Math.sin(lat),X.cl*Math.cos(lon)];
  const c={h:.5,k:0,c:P.colours[0],e:null,en:false,w:1,nrm:null,fill:false}, ran=[], tally=o.tally;
  prof.forEach((name,i)=>{ const L=KIND_LAYERS[name]; if(!L||!L.surf) return; ran.push(name); X.s=seeds[i%seeds.length];
    if(!tally){ L.surf(c,X); return; }
    // a layer is counted where it really changed the cell (its colour, height, glow, structure), never because it ran: a
    // profile entry whose layer writes nothing on this body is not a layer of it (the audit reads these counts). Where
    // emission is not painted (o.noEmit: the mono palette skips it) a change to the glow alone is no mark either
    const a=c.c,b=c.e,d=c.h,k=c.k,f=c.fill,st=c.street,li=c.line,au=c.aur,se=c.sea,w=c.w,en=c.en,nr=c.nrm;
    L.surf(c,X);
    const emit=!o.noEmit&&(b!==c.e||au!==c.aur||w!==c.w||en!==c.en);
    if(a!==c.c||d!==c.h||k!==c.k||f!==c.fill||st!==c.street||li!==c.line||se!==c.sea||nr!==c.nrm||emit) tally[name]=(tally[name]||0)+1; });
  c.ran=ran;
  return c;
}

// ---- the 2D art of a purpose body: one canvas pixel per art pixel, lit from the sun in three toon bands ----
function kindsLightBucket(x,y){ const a=Math.atan2(LY.star.y-y,LY.star.x-x); return ((Math.round(a/TAU*KIND_LIGHT.steps)%KIND_LIGHT.steps)+KIND_LIGHT.steps)%KIND_LIGHT.steps; }
function kindsGrey(c,k){ const v=Math.round(Math.pow(kindsLum(c),1/2.2)*255), g='#'+[v,v,v].map(x=>x.toString(16).padStart(2,'0')).join(''); return mixPixel(c,g,k); }
// n: radius in art pixels; L: planetLook (or {purpose, seeds, pal, conditions, nightLights, profile?}); ph: spin phase; la: light bucket
// Windows (emissive cells with en) light by toon band, never on the day side: an idle city's scattered over the night band (a
// city has lights at night) and a few at dusk on the terminator band; while work runs there (nightLights) about twice as many,
// brighter, packed closer and many on the terminator, so a working city world reads from across the room.
// Index = band (0 night, 1 terminator, 2 day); a window lights when its weight is over the number and no lit window is within
// gap art pixels (gap[0] idle, gap[1] at work).
const KIND_WIN={idle:[.5,.8,2],busy:[.2,.55,2],gap:[2,1]};
// the axial tilt: every body's north pole leans 23 degrees toward the viewer, so latitudes (page lines, gas bands, the aurora
// crown) curve round the sphere instead of crossing it as flat scanlines. The light stays in view space (it comes from the sun).
const KIND_TILT=23*Math.PI/180;
// the three toon bands' colours from one surface colour: the day band is the colour, the terminator band a clear step darker,
// the night band near the theme's shadow ink (the mono palette keeps its four greens)
const KIND_BANDS=new Map();
function kindsBand(c,band){
  if(GB) return shades(c)[[0,2,3][band]];
  if(band===2) return c;
  const k=c+'|'+band+'|'+SHADE.dark[0]; let v=KIND_BANDS.get(k);
  // the terminator darkens a bright colour more (a cream gas band or page line in the terminator band otherwise outshone a dark
  // band in full sun, and the body read as lit from the wrong side)
  if(!v){ v=band===1?mixPixel(c,SHADE.dark[1],.48+.25*kindsLum(c)):mixPixel(c,SHADE.dark[0],.84); if(KIND_BANDS.size>4000) KIND_BANDS.clear(); KIND_BANDS.set(k,v); }
  return v;
}
// a tiled theme's texture for a purpose's body: the tile of the body's own surface (city, ocean, desert, gas, rock, station, or an
// aurora, crystal or asteroid tile a theme brings), laid only on the body's plain ground (c.fill). A body coloured from a stand-in
// surface (kindsSurface: toxic, ice, belt) takes no tile: a toxic texture under an aurora read as noise
function kindsTile(p){ if(GB||!PURPOSE[p]) return null; const k=kindsSurface(p); return k===PURPOSE[p].body?(TILES[k]||null):null; }
function purposeArt(n,L,ph,la,opt){
  opt=opt||{}; n=Math.max(3,n|0);
  const P=L.pal||planetPal(L.purpose), quiet=!opt.noCond&&(L.conditions||[]).includes('quiet'), night=!opt.noCond&&!!L.nightLights;
  const seeds=kindsLayerSeeds(0,opt.seeds||L.seeds), prof=opt.profile||L.profile||PURPOSE[L.purpose].profile;
  // the activity glow (T4 nightLights) of a body without windows of its own: its night side glows at the limb and faintly
  // across, in the purpose's glow, or amber / red when something there waits or is broken (the halo follows health). The
  // colour is part of the cache key: a health change while work goes on re-bakes the art in the new colour
  const halo=night&&!prof.includes('lights')?(L.health==='broken'?KIND_STATUS.broken:L.health==='waiting'?KIND_STATUS.waiting:mixPixel(P.glow,'#ffffff',.3)):null;
  const key=['ka',n,L.purpose,prof.join('+'),P.name,P.colours.join(''),seeds.join(','),ph,la,quiet?1:0,night?1:0,halo||'',GB?1:0,THEME_NAME,opt.core||''].join('|');
  return artCache(key,()=>{
    const S=2*n+4, art=document.createElement('canvas'); art.width=art.height=S;
    const g=art.getContext('2d'), mid=n+2, phase=ph*TAU/PHASES, a=la/KIND_LIGHT.steps*TAU, ce=Math.cos(KIND_LIGHT.el);
    const Lx=ce*Math.cos(a), Ly=ce*Math.sin(a), Lz=Math.sin(KIND_LIGHT.el), shape=PURPOSE[L.purpose].shape, cT=Math.cos(KIND_TILT), sT=Math.sin(KIND_TILT);
    const TL=kindsTile(L.purpose), atm=prof.some(k=>KIND_LAYERS[k]&&KIND_LAYERS[k].rim), atmCol=mixPixel(P.colours[4],'#8fd3ff',.55);
    const tally={}, thr=night?KIND_WIN.busy:KIND_WIN.idle, seen=[0,0,0], o={phase:phase,night:night,detail:n,seeds:seeds,profile:prof,tilt:KIND_TILT,tally:tally,noEmit:!!GB};
    const cityGlow=night&&prof.includes('lights');
    let lx=0,ly=0,lw=0,lum=0,cnt=0,lit=0,glow=0,rimPx=0,llx=0,lly=0; const winAt=[];
    // every painted cell, for the read-back below: where it is, its direction from the centre, the surface colour's own
    // luminance (its albedo) and whether it is a light (a window) or on the rim (where a halo glows)
    const cellX=[], cellY=[], cellNx=[], cellNy=[], cellAlb=[], cellSkip=[];
    // the windows lit so far (one byte per art pixel): a window lights only when no lit window already drawn is within R art
    // pixels (the rows above and the pixels to its left), so the lights are points (a city from orbit), never runs or blobs of
    // gold. An idle city keeps them two pixels apart (a scatter), a city at work one (about twice as many, packed)
    const litMap=new Uint8Array(S*S), R=night?KIND_WIN.gap[1]:KIND_WIN.gap[0], litNear=(x,y)=>{
      for(let dy=-R;dy<=0;dy++) for(let dx=-R;dx<=R;dx++){ if(dy===0&&dx>=0) break; const X=x+dx, Y=y+dy; if(X>=0&&X<S&&Y>=0&&litMap[Y*S+X]) return true; }
      return false; };
    for(let yy=-n-2;yy<n+2;yy++) for(let xx=-n-2;xx<n+2;xx++){
      const nx=(xx+.5)/n, ny=(yy+.5)/n, d=Math.hypot(nx,ny); let lim=1;
      if(shape==='gem'){ const th=Math.atan2(ny,nx), sec=Math.PI/4, q=((th%sec)+sec)%sec-sec/2; lim=Math.cos(sec/2)/Math.cos(q); }
      else if(shape==='lumpy'){ const th=Math.atan2(ny,nx); lim=.8+.2*vnoise(Math.cos(th)*1.7+3,Math.sin(th)*1.7+3,seeds[0]*.01,seeds[0]); }
      if(d>lim) continue;
      const q=d/lim, nz=Math.sqrt(Math.max(0,1-q*q)), sx=nx/lim, sy=ny/lim;
      // view -> body: the pole leans toward the viewer (KIND_TILT), then the body turns (phase)
      const by=sy*cT-nz*sT, bz=sy*sT+nz*cT, lon=Math.atan2(sx,bz)+phase, lat=Math.asin(Math.max(-1,Math.min(1,by)));
      o.lon0=lon-phase; const s=surfaceAt(L.purpose,P,lon,lat,seeds[0],o);
      // a facet's normal leans 62% its own way and 38% the sphere's: whole facets step in colour (a cut gem) while the three bands
      // still follow the sun round the body (flat normals alone left a band out on some gems and tilted the crescent)
      const fk=s.nrm?(s.facetK==null?1:s.facetK):0, N=fk?[s.nrm[0]*fk+sx*(1-fk),s.nrm[1]*fk+sy*(1-fk),s.nrm[2]*fk+nz*(1-fk)]:[sx,sy,nz];
      const dot=(N[0]*Lx+N[1]*Ly+N[2]*Lz)/(fk?Math.hypot(N[0],N[1],N[2])||1:1), t=(dot+1)/2+(s.h-.5)*.05;
      const band=t<KIND_TOON[0]?0:t<KIND_TOON[1]?1:2;
      let c=s.c;
      if(TL&&s.fill){ const tv=Math.max(8,Math.round(Math.PI*n*2/TILE_TEXEL)), tu=Math.max(8,Math.round(TAU*n*2/TILE_TEXEL));
        c=TL[(Math.min(tv-1,Math.floor((lat/Math.PI+.5)*tv))+(seeds[0]>>3))&7][(Math.floor(((((lon/TAU)%1)+1)%1)*tu)+seeds[0])&7]; }
      if(quiet) c=kindsGrey(c,.5);
      let col=kindsBand(c,band);
      const rim=q>(n-1.2)/n;
      if(atm&&rim&&!GB&&band>0){ col=mixPixel(col,atmCol,band===2?.45:.22); rimPx++; }
      if(quiet&&rim&&!GB) col=mixPixel(col,KIND_STATUS.frost,.7);
      const shade=col;                         // the body's own light: what the lit side measures (windows and glows are lights, not lighting)
      // emissive layers: an aurora glows about as bright on both sides (glowing full at night and faint by day, its bright crown
      // read as the lit side, the body as lit away from the sun); windows and lamps only where it is dark enough for their weight
      // and no lit window is near (KIND_WIN); other emitters everywhere
      if(s.e&&!GB){ const e=quiet?kindsGrey(s.e,.35):s.e;
        if(s.aur) col=mixPixel(col,e,(band===2?.78:.8)*s.aur);
        else if(!s.en) col=e;
        else if(s.w>thr[band]&&!litNear(mid+xx,mid+yy)){ col=band===0?e:mixPixel(e,col,.35); lit++; litMap[(mid+yy)*S+mid+xx]=1; winAt.push([mid+xx,mid+yy,band]); } }
      if(halo&&band<2&&!GB){ col=mixPixel(col,halo,band===0?(q>.8?.55:.14):(q>.8?.25:.05)); glow++; }
      // a city at work glows: the night ground between its windows takes a warm cast (the light of the streets on the haze)
      else if(cityGlow&&band<2&&!GB&&col!==s.e) col=mixPixel(col,P.glow,band===0?.13:.06);
      const isWin=winAt.length&&winAt[winAt.length-1][0]===mid+xx&&winAt[winAt.length-1][1]===mid+yy;
      g.fillStyle=col; g.fillRect(mid+xx,mid+yy,1,1);
      // the light that fell there: the painted brightness over the colour it was painted from (1 on the day band, a step less
      // in the terminator, little at night, whatever the surface's own albedo), so a pale line or a dark roof never moves the
      // lit side; the clamp keeps a near-black ground from dividing by nothing. The painted colour is read back from the
      // canvas after the loop (kindsReadLit); this running sum from the colour handed to the canvas is the fallback where
      // the canvas cannot be read. Windows are lights, not lighting, and with a halo on the rim glows: both stay out of the measure
      const alb=Math.max(.012,kindsLum(c)), wl=Math.min(1,kindsLum(col)/alb);
      const skip=isWin||(halo&&q>.8);           // a window is a light; with a halo on, the rim glows on the night side
      cellX.push(mid+xx); cellY.push(mid+yy); cellNx.push(nx); cellNy.push(ny); cellAlb.push(alb); cellSkip.push(skip?1:0);
      seen[band]++; lx+=band*nx; ly+=band*ny; lw+=band; if(!skip){ llx+=wl*nx; lly+=wl*ny; } lum+=kindsLum(col); cnt++;
    }
    // the lit side from the pixels really on the canvas (getImageData): the brightness painted at each cell over its albedo,
    // windows and the rim left out; where the canvas gives no pixels (a context that cannot be read) the colours handed to it
    const read=kindsReadLit(g,S,cellX,cellY,cellNx,cellNy,cellAlb,cellSkip);
    if(read){ llx=read[0]; lly=read[1]; }
    // the windows painted: the lights layer's cells are the windows on the canvas, never the cells it offered one
    if(prof.includes('lights')) tally.lights=lit;
    // what was drawn, counted from the pixels: the lit side (lit = by toon band, litLuma = by the brightness of the colour
    // painted, windows and glows left out), the mean brightness, the toon bands that really appear, and each layer's own cells (the
    // pixels it changed; the atmosphere's rim pixels): `layers` are the profile's entries that left a mark, in profile order
    const cells=Object.assign({},tally); prof.forEach(k=>{ if(KIND_LAYERS[k]&&KIND_LAYERS[k].rim&&!GB&&rimPx) cells[k]=rimPx; });
    KIND_META.set(art,{lit:Math.atan2(ly,lx)*180/Math.PI,litLuma:Math.atan2(lly,llx)*180/Math.PI,litFrom:read?'pixels':'paint',luma:cnt?lum/cnt:0,n:n,bands:seen.filter(v=>v>0).length,
      layers:prof.filter(k=>cells[k]>0),cells:cells,windows:lit,
      winBands:[0,1,2].map(b=>winAt.filter(w=>w[2]===b).length),winAt:winAt,glow:glow});
    return art; });
}
const KIND_META=new WeakMap();
const kindsMeta=art=>(art&&KIND_META.get(art))||{lit:0,luma:0,n:0,bands:0,layers:[]};
// the luminance of a painted pixel (linear, the same weights as kindsLum)
function kindsLumPx(r,g,b){ const f=v=>{ v/=255; return v<=.03928?v/12.92:Math.pow((v+.055)/1.055,2.4); }; return .2126*f(r)+.7152*f(g)+.0722*f(b); }
// [sum of wl*nx, sum of wl*ny] over the cells, wl = the painted pixel's luminance over the cell's albedo, read from the canvas;
// null when the canvas gives no pixels (a stub context, a context that throws)
function kindsReadLit(g,S,X,Y,NX,NY,ALB,SKIP){
  let px=null; try{ const img=g.getImageData&&g.getImageData(0,0,S,S); if(img&&img.data&&typeof img.data.length==='number'&&img.data.length>=S*S*4) px=img.data; }catch(e){ px=null; }
  if(!px) return null;
  let sx=0, sy=0;
  for(let i=0;i<X.length;i++){ if(SKIP[i]) continue; const k=(Y[i]*S+X[i])*4; const wl=Math.min(1,kindsLumPx(px[k],px[k+1],px[k+2])/ALB[i]); sx+=wl*NX[i]; sy+=wl*NY[i]; }
  return [sx,sy];
}
// one art pixel = 2 screen pixels: the body's radius in art pixels at this zoom (the vector look draws one per device pixel), on
// an 8% ladder so a zoom that eases (focus, home) reuses a few arts instead of baking new ones every frame. Never under
// KIND_MIN_N art pixels: at 4 a crescent cannot be told from its grain (the lit side read 24 degrees off the sun on some
// seeds); at 5 every seed, phase and light bucket holds within 20
const KIND_MIN_N=5;
function kindsArtN(r){ const raw=Math.max(KIND_MIN_N,r*ZOOM*(VEC?(DPR||1):.5)), k=Math.round(Math.log(raw)/Math.log(1.08)); return Math.max(KIND_MIN_N,Math.round(Math.pow(1.08,k))); }

// ---- pixel helpers in the drawing context's own units: q = the size of one art pixel there ----
const KIND_INK={n:0};                        // every dot a part draws counts here: kindsBody reads what each part really painted
function kindsDot(g,x,y,q,col){ KIND_INK.n++; g.fillStyle=col; g.fillRect(Math.round(x/q)*q,Math.round(y/q)*q,q,q); }
// one paint call on a context, counted (the sun's layers are the paints that really happened: drawStar, vecStar)
function kindsPaint(g,op,...a){ KIND_INK.n++; return g[op](...a); }
// run one layer's painting; its name goes on the list only when it painted something (KIND_INK moved)
function kindsLayerPaint(list,name,fn){ const n0=KIND_INK.n; fn(); if(KIND_INK.n>n0) list.push(name); }
function kindsEllipse(g,x,y,rx,ry,tilt,a0,a1,q,colAt,dash){
  const ct=Math.cos(tilt), st=Math.sin(tilt), steps=Math.max(8,Math.ceil((a1-a0)*Math.max(rx,ry)/q));
  for(let i=0;i<=steps;i++){ if(dash&&i%6>2) continue; const a=a0+(a1-a0)*i/steps, ex=Math.cos(a)*rx, ey=Math.sin(a)*ry;
    const px=x+ex*ct-ey*st, py=y+ex*st+ey*ct, col=colAt(a,px,py); if(col) kindsDot(g,px,py,q,col); }
}
function kindsBlit(g,art,x,y,q,alpha){
  KIND_INK.n++; const S=art.width*q, x0=Math.round((x-S/2)/q)*q, y0=Math.round((y-S/2)/q)*q;
  if(alpha!=null&&alpha<1){ g.save(); g.globalAlpha*=alpha; g.drawImage(art,x0,y0,S,S); g.restore(); } else g.drawImage(art,x0,y0,S,S);
}
// the sun side of a ring point: brighter there (the light comes from the sun)
function kindsSunSide(px,py,x,y,la){ const a=la/KIND_LIGHT.steps*TAU; return (px-x)*Math.cos(a)+(py-y)*Math.sin(a)>0; }

// ---- a purpose body, whole: its back parts, the lit core art, its front parts. g/q: context and art pixel size ----
// The parts are the profile's `part` layers (KIND_PARTS), each with a back half (before the core) and a front half (after it),
// each with the seed of its place in the profile; a profile without parts is just the lit core.
const KIND_PARTS={
  // a ring: the shelf of a Reference world, or the turning station ring with its lamps and spokes for Shared
  ring:{back(D){ D.ring(Math.PI,TAU); }, front(D){ D.ring(0,Math.PI); }, init(D){
    const {g,x,y,r,q,C,lit,t}=D;
    if(D.L.purpose==='shared'){
      const rx=Math.max(r*1.4,q*8), ry=rx*.42, tilt=-.25, rot=(REDUCED?0:t/40000)%1, ct=Math.cos(tilt), st=Math.sin(tilt);
      D.pt=(a,k)=>[x+Math.cos(a)*rx*k*ct-Math.sin(a)*ry*k*st, y+Math.cos(a)*rx*k*st+Math.sin(a)*ry*k*ct];
      D.ring=(a0,a1)=>{ kindsEllipse(g,x,y,rx,ry,tilt,a0,a1,q,(a,px,py)=>lit(a,px,py,C[3],C[2]));
        kindsEllipse(g,x,y,rx-q*1.5,ry-q*1.2,tilt,a0,a1,q,(a,px,py)=>lit(a,px,py,C[2],C[1]));
        if(!GB) for(let i=0;i<12;i++){ const a=((rot+i/12)%1)*TAU; if(a<a0||a>a1) continue; const p=D.pt(a,1); kindsDot(g,p[0],p[1],q,i%4===0?C[4]:mixPixel(C[3],'#ffffff',.4)); }
        if(a0>=Math.PI) for(let i=0;i<3;i++){ const a=rot*TAU+i*TAU/3+.5; for(let k=.32;k<.95;k+=.1){ const p=D.pt(a,k); kindsDot(g,p[0],p[1],q,C[1]); } } };
    }else{
      const rx=r*1.75, ry=r*.42, tilt=-.32;
      D.ring=(a0,a1)=>{ const back=a0>=Math.PI;
        kindsEllipse(g,x,y,rx,ry,tilt,a0,a1,q,(a,px,py)=>lit(a,px,py,back?C[3]:C[4],back?C[1]:C[2]));
        kindsEllipse(g,x,y,rx*1.12,ry*1.12,tilt,a0,a1,q,(a,px,py)=>lit(a,px,py,back?C[2]:C[3],back?C[0]:C[1])); };
    } }},
  // the dock: an arm off the sun-away side with a lit port at its end
  dock:{front(D){ const {g,x,y,r,q,C}=D, da=D.la/KIND_LIGHT.steps*TAU+Math.PI*.8;
    for(let k=r*.4;k<r*1.05;k+=q){ kindsDot(g,x+Math.cos(da)*k,y+Math.sin(da)*k,q,C[2]); kindsDot(g,x+Math.cos(da)*k+Math.cos(da+Math.PI/2)*q,y+Math.sin(da)*k+Math.sin(da+Math.PI/2)*q,q,C[1]); }
    kindsDot(g,x+Math.cos(da)*r*1.1,y+Math.sin(da)*r*1.1,q,C[4]); }},
  // the archive's belt: grains behind, a few tumbling rocks (two of the six seeds each) in front
  rubble:{back(D){ const {g,x,y,r,q,C,s}=D, spin=0;
    for(let i=0;i<120;i++){ const a=hash3(i,1,2,s)*TAU+spin, rr=r*(.75+hash3(i,3,4,s)*.7); kindsDot(g,x+Math.cos(a)*rr,y+Math.sin(a)*rr*.62,q,i%5===0?C[3]:i%2?C[1]:C[2]); } },
    front(D){ const {g,x,y,r,q,s,L,P}=D;
      for(let i=0;i<5;i++){ const a=i*2.39996+(s%11), d=(.55+hash3(i,5,6,s)*.4)*r;
        const rock=purposeArt(Math.max(2,Math.round(D.n*(.12+hash3(i,8,9,s)*.1))),{purpose:'archive',seeds:[D.seeds[4+(i%2)],D.seeds[(i+2)%6]],profile:['rubble'],pal:P,conditions:L.conditions},0,D.la,{noCond:true});
        kindsBlit(g,rock,x+Math.cos(a)*d,y+Math.sin(a)*d*.62,q); } }},
  // the crates: shipping boxes riding the belt, outlined, a lit lid and a dark side, so they read as made, not rock
  crates:{back(D){ const {g,x,y,r,q,C,P,s}=D, spin=0, cb=P.glow, lid=mixPixel(cb,'#ffffff',.45), side=mixPixel(cb,C[0],.5);
    for(let i=0;i<8;i++){ const a=hash3(i,7,2,s)*TAU+spin, rr=r*(.85+hash3(i,8,4,s)*.55), px=x+Math.cos(a)*rr, py=y+Math.sin(a)*rr*.62;
      const w=q*(hash3(i,9,1,s)>.5?6:5), h=q*4, bx=Math.round(px/q)*q, by=Math.round(py/q)*q;
      g.fillStyle=C[0]; g.fillRect(bx-q,by-q,w+2*q,h+2*q); g.fillStyle=cb; g.fillRect(bx,by,w,h); g.fillStyle=lid; g.fillRect(bx,by,w,q);
      g.fillStyle=side; g.fillRect(bx+w-q,by+q,q,h-q); g.fillRect(bx+Math.floor(w/q/2)*q,by+q,q,h-q); KIND_INK.n+=5; } }},
};
let KIND_PARTS_DRAWN=[];
function kindsBody(g,x,y,r,L,o){
  const q=o.q, ph=o.ph||0, la=o.la||0, t=o.t||0, n=o.n, P=L.pal||planetPal(L.purpose), C=P.colours, al=o.alpha==null?1:o.alpha;
  const seeds=kindsLayerSeeds(0,L.seeds), prof=L.profile||PURPOSE[L.purpose].profile, empty=(L.conditions||[]).includes('empty');
  const D={g:g,x:x,y:y,r:r,q:q,n:n,la:la,t:t,L:L,P:P,C:C,seeds:seeds,lit:(a,px,py,hi,lo)=>kindsSunSide(px,py,x,y,la)?hi:lo};
  const parts=[], ink={}; prof.forEach((k,i)=>{ const pt=KIND_PARTS[k]; if(pt) parts.push([pt,seeds[i%6],k]); });
  g.save(); if(al<1) g.globalAlpha*=al;
  parts.forEach(([pt,s,k])=>{ D.s=s; const n0=KIND_INK.n; if(pt.init) pt.init(D); if(pt.back) pt.back(D); ink[k]=(ink[k]||0)+KIND_INK.n-n0; });
  const core=purposeArt(parts.length&&PURPOSE[L.purpose].core?Math.max(KIND_MIN_N,Math.round(n*PURPOSE[L.purpose].core)):n,L,ph,la,PURPOSE[L.purpose].core?{core:L.purpose[0]}:undefined);
  kindsBlit(g,core,x,y,q);
  parts.forEach(([pt,s,k])=>{ D.s=s; const n0=KIND_INK.n; if(pt.front) pt.front(D); ink[k]=(ink[k]||0)+KIND_INK.n-n0; });
  g.restore(); KIND_PARTS_DRAWN=parts.filter(p=>ink[p[2]]>0).map(p=>p[2]);   // a part counts where it painted something
  if(empty){ g.save(); g.globalAlpha*=.85; kindsEllipse(g,x,y,r+q,r+q,0,0,TAU,q,()=>KIND_STATUS.idle,true); g.restore(); }
  return core;
}
// the conditions over and under a body; behind = the parts drawn before the body
function kindsOverlays(g,x,y,r,L,o,behind){
  const q=o.q, t=o.t||0, cond=L.conditions||[];
  if(behind){
    if(cond.includes('waiting')&&!GB&&!o.noAura){ const col=L.health==='broken'?KIND_STATUS.broken:KIND_STATUS.waiting;   // noAura: the open planet
      const gr=g.createRadialGradient(x,y,r*.85,x,y,r*2.1); gr.addColorStop(0,col+'99'); gr.addColorStop(.35,col+'4d'); gr.addColorStop(1,col+'00');
      g.fillStyle=gr; g.beginPath(); g.arc(x,y,r*2.1,0,TAU); g.fill(); }
    if(cond.includes('rebuildable')&&!GB){ const gr=g.createRadialGradient(x,y,r,x,y,r*1.5); gr.addColorStop(0,'#9AA8BC00'); gr.addColorStop(.6,'#9AA8BC26'); gr.addColorStop(1,'#9AA8BC00');
      g.fillStyle=gr; g.beginPath(); g.arc(x,y,r*1.5,0,TAU); g.fill(); }
    return;
  }
  if(cond.includes('rebuildable')) kindsEllipse(g,x,y,r*1.28,r*1.28,0,0,TAU,q,()=>GB?'#8bac0f':'#9AA8BC',true);
  if(cond.includes('waiting')){ const col=L.health==='broken'?KIND_STATUS.broken:KIND_STATUS.waiting, on=REDUCED||Math.floor(t/500)%2===0;
    if(on){ const a=-Math.PI/4, px=x+Math.cos(a)*(r+q*2), py=y+Math.sin(a)*(r+q*2); g.fillStyle='#05070D'; g.fillRect(Math.round(px/q)*q-q,Math.round(py/q)*q-q,q*4,q*4);
      g.fillStyle=GB?'#0f380f':col; g.fillRect(Math.round(px/q)*q,Math.round(py/q)*q,q*2,q*2); } }
  if(cond.includes('working')){
    const col=GB?'#8bac0f':((SKIN&&/^#[0-9a-fA-F]{6}$/.test(SKIN.working_ring||''))?SKIN.working_ring:KIND_STATUS.working), R=r+q*3, f=L.progress==null?.25:Math.max(.08,L.progress);
    kindsEllipse(g,x,y,R,R,0,-Math.PI/2,Math.PI*1.5,q,()=>mixPixel(col,'#05070D',.7));
    kindsEllipse(g,x,y,R,R,0,-Math.PI/2,-Math.PI/2+f*TAU,q,()=>col);
    kindsEllipse(g,x,y,R+q,R+q,0,-Math.PI/2,-Math.PI/2+f*TAU,q,()=>col);
  }
  if(L.glyphSet||o.hot){ const s=Math.max(q*7,Math.min(r*.7,q*10)), gx=x-r*.72, gy=y-r*.72;
    g.save(); g.fillStyle='#05070Dd8'; g.beginPath(); g.arc(gx,gy,s*.68,0,TAU); g.fill(); g.restore();
    kindsGlyph(g,L.glyph,gx-s/2,gy-s/2,s,GB?'#9bbc0f':'#E8EEF6'); }
}

// ---- glyphs: small line drawings on a 16-unit grid ----
function kindsGlyph(g,name,x,y,s,col){
  const k=s/16; g.save(); g.translate(x,y); g.scale(k,k); g.strokeStyle=col; g.fillStyle=col; g.lineWidth=1.8; g.lineCap='round'; g.lineJoin='round';
  const P=(pts,close)=>{ g.beginPath(); pts.forEach((p,i)=>i?g.lineTo(p[0],p[1]):g.moveTo(p[0],p[1])); if(close) g.closePath(); g.stroke(); };
  const R=(a,b,w,h)=>g.strokeRect(a,b,w,h), O=(a,b,r,s0,s1)=>{ g.beginPath(); g.arc(a,b,r,s0==null?0:s0,s1==null?TAU:s1); g.stroke(); };
  switch(name){
    case 'code': P([[5,4],[1,8],[5,12]]); P([[11,4],[15,8],[11,12]]); P([[9.5,3],[6.5,13]]); break;
    case 'notes': R(3.5,1.5,9,13); P([[6,5],[10,5]]); P([[6,8],[10,8]]); P([[6,11],[9,11]]); break;
    case 'book': P([[8,4],[8,14]]); P([[8,4],[2,2.5],[2,12.5],[8,14],[14,12.5],[14,2.5],[8,4]]); break;
    case 'data': g.beginPath(); g.ellipse(8,4,5.5,2,0,0,TAU); g.stroke(); P([[2.5,4],[2.5,12]]); P([[13.5,4],[13.5,12]]);
      g.beginPath(); g.ellipse(8,12,5.5,2,0,0,Math.PI); g.stroke(); g.beginPath(); g.ellipse(8,8,5.5,2,0,0,Math.PI); g.stroke(); break;
    case 'media': R(1.5,3,13,10); P([[3,12],[7,7],[10,10],[11.5,8.5],[14,11.5]]); g.beginPath(); g.arc(11,6,1.3,0,TAU); g.fill(); break;
    case 'config': O(8,8,3.2); for(let i=0;i<8;i++){ const a=i*Math.PI/4; P([[8+Math.cos(a)*4.6,8+Math.sin(a)*4.6],[8+Math.cos(a)*6.8,8+Math.sin(a)*6.8]]); } break;
    case 'sync': P([[2,6],[13,6]]); P([[10,3],[13,6],[10,9]]); P([[14,11],[3,11]]); P([[6,8],[3,11],[6,14]]); break;
    case 'archive': R(2,5,12,9); P([[1.5,5],[14.5,5],[14.5,2.5],[1.5,2.5],[1.5,5]]); P([[6.5,8],[9.5,8]]); break;
    case 'unknown': O(8,5.5,3.2,Math.PI*1.05,Math.PI*2.35); P([[9.6,8.2],[8,9.8],[8,11]]); g.beginPath(); g.arc(8,14,1,0,TAU); g.fill(); break;
    case 'money': R(2,2,12,12); O(8,8,3.4); P([[8,4.6],[8,11.4]]); P([[4.6,8],[11.4,8]]); break;
    case 'health': g.beginPath(); g.moveTo(8,14); g.bezierCurveTo(1,9,1,3,5,3); g.bezierCurveTo(6.8,3,8,4.5,8,5.5); g.bezierCurveTo(8,4.5,9.2,3,11,3);
      g.bezierCurveTo(15,3,15,9,8,14); g.stroke(); break;
    case 'career': R(1.5,5,13,9); P([[5.5,5],[5.5,2.5],[10.5,2.5],[10.5,5]]); P([[1.5,9],[14.5,9]]); break;
    case 'journal': R(3.5,1.5,10,13); for(let i=0;i<4;i++) P([[2,4+i*3],[5,4+i*3]]); P([[7,6],[11,6]]); break;
    case 'learning': P([[8,3],[15,6.5],[8,10],[1,6.5],[8,3]]); P([[4,8],[4,12],[8,14],[12,12],[12,8]]); P([[15,6.5],[15,11]]); break;
    case 'tools': P([[3,13],[10,6]]); P([[9,2.5],[13.5,7],[11.5,9],[7,4.5],[9,2.5]]); break;
    case 'home': P([[1.5,8],[8,2],[14.5,8]]); P([[3.5,6.5],[3.5,14],[12.5,14],[12.5,6.5]]); R(6.5,9.5,3,4.5); break;
    case 'star': { const pts=[]; for(let i=0;i<10;i++){ const a=-Math.PI/2+i*Math.PI/5, rr=i%2?3:7; pts.push([8+Math.cos(a)*rr,8.5+Math.sin(a)*rr]); } P(pts,true); break; }
    default: O(8,8,5);
  }
  g.restore();
}
function kindsCanvas(w,h){ const c=document.createElement('canvas'); c.width=w; c.height=h; return c; }
// an icon of a purpose body (legend, sheet, card): the whole body, lit from the upper left, transparent around it
function purposeIcon(p,size,o){
  o=o||{}; const pr=o.progress==null?'':Math.round(o.progress*20);
  const key=['ki',p,size,o.seed||0,GB?1:0,THEME_NAME,(planetPal(p)||{}).name,(o.conditions||[]).join('+'),o.health||'',o.night?1:0,pr].join('|');
  return artCache(key,()=>{
    const c=kindsCanvas(size,size), g=c.getContext('2d'), q=Math.max(1,Math.round(size/40)), r=size*(p==='reference'||p==='shared'?.26:p==='archive'?.3:.38);
    const L={purpose:p,seeds:kindsSeeds(o.seed||('legend:'+p)),pal:planetPal(p),conditions:o.conditions||[],nightLights:!!o.night,progress:o.progress==null?null:o.progress,health:o.health||'ok',glyph:PURPOSE[p].glyph};
    g.imageSmoothingEnabled=false; const la=Math.round(KIND_LIGHT.steps*5/8)%KIND_LIGHT.steps;
    kindsOverlays(g,size/2,size/2,r,L,{q:q,t:0},true);
    kindsBody(g,size/2,size/2,r,L,{q:q,n:Math.max(KIND_MIN_N,Math.round(r/q)),ph:7,la:la,t:0});
    kindsOverlays(g,size/2,size/2,r,L,{q:q,t:0},false);
    return c; });
}
function purposeIconImg(p,size,o){ return '<img class="file-planet" src="'+purposeIcon(p,size||28,o).toDataURL()+'" alt="">'; }

// ---- the map's planet drawing (render-game.py drawMoonBody / vecMoonBody call these) ----
function kindsPeriod(seed){ return 70+(seed%40); }
function kindsDrawMoon(m,t){
  const L=planetLook(m); if(!L) return null;
  const n=kindsArtN(m.r), q=m.r/n, seed=kindsInt(L.seeds[0]);
  const ph=bodyPhase(seed,kindsPeriod(seed)*(1+3*(m.decay||0))), la=kindsLightBucket(m.x,m.y), hot=typeof plateHot==='function'&&plateHot(m.uid);
  const o={q:q,n:n,ph:ph,la:la,t:t,hot:hot,alpha:(L.conditions.includes('empty')?.35:1),noAura:!!m.noAura};
  if(VEC) cx.imageSmoothingEnabled=true;
  kindsOverlays(cx,m.x,m.y,m.r,L,o,true);
  const core=kindsBody(cx,m.x,m.y,m.r,L,o);
  kindsOverlays(cx,m.x,m.y,m.r,L,o,false);
  if(VEC) cx.imageSmoothingEnabled=false;
  KIND_DRAWN.set(m.uid,{core:core,look:L,la:la,at:FRAME_T,parts:KIND_PARTS_DRAWN.slice()});
  return L;
}
const KIND_DRAWN=new Map();

// another machine: a small log-spiral galaxy (cosmos galaxy.glsl.ts:202-232: arms, tilt, a bulge), red when it is down; its
// brightness in three toon steps (core, arm, haze) like the planets' light
function machineArt(n,seed,col){
  return artCache(['km',n,seed%997,col,GB?1:0].join('|'),()=>{
    const S=2*n+4, c=kindsCanvas(S,S), g=c.getContext('2d'), mid=n+2, ARMS=2, PITCH=2.4, tilt=.55+hash3(1,2,3,seed)*.3, spin=hash3(4,5,6,seed)*TAU, seen=[0,0,0];
    let armPx=0, bulgePx=0;                    // the pixels the arms and the bulge each carried (the audit's layers)
    for(let yy=-n-2;yy<n+2;yy++) for(let xx=-n-2;xx<n+2;xx++){
      const ux=(xx+.5)/n, uy=(yy+.5)/n/tilt, r=Math.hypot(ux,uy); if(r>1.05) continue;
      const a=Math.atan2(uy,ux), ph=ARMS*(a+spin)+Math.log(Math.max(.05,r))*PITCH*ARMS, arm=Math.pow(.5+.5*Math.cos(ph),3)*(1-r);
      const bulge=Math.exp(-r*r*14), v=Math.min(1,arm*1.6+bulge+(vnoise(ux*6,uy*6,1,seed)-.5)*.15*(1-r));
      if(v<.14) continue;
      if(bulge>=arm*1.6) bulgePx++; else armPx++;
      const band=v>.62?2:v>.3?1:0; seen[band]++;
      g.fillStyle=GB?(band?'#8bac0f':'#306230'):[mixPixel(col,'#05070D',.5),mixPixel(col,'#ffffff',.2),'#FFE8B0'][band];
      g.fillRect(mid+xx,mid+yy,1,1); }
    KIND_META.set(c,{lit:0,litLuma:0,luma:0,n:n,bands:seen.filter(v=>v>0).length,layers:[armPx?'arms':'',bulgePx?'bulge':''].filter(Boolean),cells:{arms:armPx,bulge:bulgePx}});
    return c; });
}
// the sun's art: the draw function that paints it says what it painted (kindsSunDrew, called by drawStar and vecStar) and the
// audit reads that, never a fixed list: each layer's name is on the list only when its paint calls happened (kindsLayerPaint
// counts them through kindsPaint: a corona glow that drew nothing, rays the mono palette skips, are not layers). The pixel
// look paints a corona glow, rays and then the 'star' surface, whose limb darkens in the steps surfaceAt really returns
// across the disc (counted here: 'limb' is a layer when the surface was painted and steps at least twice); the vector look
// paints a glow, a streak, a white disc and two instrument rings, with no stepped surface (no toon bands: the tactical sun
// is exempt from the three-band rule; the probes run in classic)
const KIND_SUN={at:-1,layers:[],surface:false};
function kindsSunDrew(layers,surface){ KIND_SUN.at=FRAME_T; KIND_SUN.layers=layers.slice(); KIND_SUN.surface=!!surface; }
function kindsSunMeta(){
  const P=STYLE.star||{a:'#ffc53a',b:'#ff6f17',c:'#fff5be'}, lv=new Set();
  if(KIND_SUN.surface) for(let i=0;i<=20;i++){ const rr=i/20; lv.add(surfaceAt('star',P,i*.3,0,1,{rr:rr,light:.5}).lvl); }
  const layers=KIND_SUN.layers.slice(); if(KIND_SUN.surface&&lv.size>=2&&layers.includes('photosphere')) layers.push('limb');
  return {layers:layers,bands:KIND_SUN.surface?lv.size:0,drawn:KIND_SUN.at>=0};
}

// ---- __spriteAudit: what every body on the map is, read back from what was drawn ----
function kindsWrap(a){ return ((a+540)%360)-180; }
window.__spriteAudit=function(){
  const out=(MOONS||[]).map(m=>{
    const d=KIND_DRAWN.get(m.uid), L=(d&&d.look)||planetLook(m), la=d?d.la:kindsLightBucket(m.x,m.y);
    const core=d&&d.core?d.core:purposeArt(12,L,0,la);
    // profile and toon bands are read back from the art: the layers that left a mark (the surface layers' own cells, the
    // atmosphere's rim pixels, the parts that painted dots: never a layer that only ran) and the bands that really appear; a core
    // drawn smaller than 8 art pixels is measured on the same body at 12 (too few pixels to hold every band): layers, bands,
    // cells and windows all read that one art (drawnWindows is the drawn core's own). The lit side is the direction of the
    // brightness of the pixels painted on the drawn core, read back from its canvas, windows and a halo's rim left out
    const meta=kindsMeta(core), ref=meta.n>=8?meta:kindsMeta(purposeArt(12,L,0,la)), prof=L.profile||PURPOSE[L.purpose].profile;
    const parts=d&&d.parts?d.parts:[], did=new Set(ref.layers.concat(parts));
    const sun=Math.atan2(LY.star.y-m.y,LY.star.x-m.x)*180/Math.PI, lit=sun+kindsWrap(meta.litLuma-sun);
    const layers=prof.filter(k=>did.has(k));
    return {uid:m.uid,kind:'area',label:L.label,purpose:L.purpose,name:L.name,surface:L.surface,profile:layers,layers:layers,toonBands:ref.bands,windows:ref.windows||0,drawnWindows:meta.windows||0,agents:L.agents||0,
      conditions:L.conditions.slice(),glyph:L.glyph,glyphSet:L.glyphSet,palette:L.palette,seeds:L.seeds,guessed:L.guessed,health:L.health,
      nightLights:!!L.nightLights,progress:L.progress,litAngle:Math.round(lit*10)/10,litBandAngle:Math.round((sun+kindsWrap(meta.lit-sun))*10)/10,
      sunAngle:Math.round(sun*10)/10,declared:prof.slice(),cells:Object.assign({},ref.cells),
      artPx:meta.n,drawn:!!d&&d.at===FRAME_T,x:(m.x-camx)*ZOOM,y:(m.y-camy)*ZOOM,r:m.r*ZOOM};
  });
  const sm=kindsSunMeta();
  out.push({uid:'star',kind:'star',label:String(W.name||''),purpose:'sun',surface:'sun',profile:sm.layers,layers:sm.layers,toonBands:sm.bands,selfLit:true,conditions:[],
    palette:(planetPal('code')||{}).name,litAngle:0,sunAngle:0});
  (units||[]).filter(u=>u.kind==='machine'&&LY.planets&&LY.planets[u.name]).forEach(u=>{ const r=62, mm=kindsMeta(machineArt(kindsArtN(r),seedOf(u.name),KIND_STATUS.sun));
    out.push({uid:u.uid,kind:'machine',label:String(u.name),purpose:'machine',surface:'galaxy',profile:mm.layers,layers:mm.layers,toonBands:mm.bands,selfLit:true,conditions:[],
      palette:(planetPal('code')||{}).name,litAngle:0,sunAngle:0}); });
  out.backdrop=kindsBackdropAudit();
  return out;
};

// ---- the backdrop: one baked nebula for this machine, the same recipe the 3D sky bakes ----
function kindsMulberry(a){ return ()=>{ a|=0; a=a+0x6D2B79F5|0; let t=Math.imul(a^a>>>15,1|a); t=t+Math.imul(t^t>>>7,61|t)^t; return ((t^t>>>14)>>>0)/4294967296; }; }
// faint ("a faint nebula"): each layer's colour is the theme's, a quarter of the way to the sky's base and 45% of the
// way to its own grey, and a cloud shows only where the noise passes `cut`, at most `maxAlpha` opaque, so the sky stays a thin
// veil behind the bodies (the bench reviews read full-strength clouds as purple and teal masses that a dark hemisphere or a
// purple Data body sank into). The 3D sky bakes the same numbers.
const KIND_NEB_FAINT={toBase:.25,desaturate:.45,cut:.46,gain:2.6,maxAlpha:.55};
CM.nebulaRecipe=function(){
  const seed=kindsFnv(String(W.name||'')+String(W.root_path||'')), rnd=kindsMulberry(seed), nb=(SPACE&&SPACE.nebula)||['#2a1460','#0a3a5c','#5c2a0e'];
  const base=(SPACE&&SPACE.base)||'#050508', F=KIND_NEB_FAINT, mute=c=>/^#[0-9a-fA-F]{6}$/.test(c)?kindsGrey(mixPixel(c,base,F.toBase),F.desaturate):c;
  const layer=c=>({colour:mute(c),theme:c,offset:[rnd()*2000-1000,rnd()*2000-1000,rnd()*2000-1000],scale:1.2+rnd()*.8,falloff:3+rnd()*2,intensity:1.0+rnd()*.3});
  return {seed:seed,layers:[layer(nb[0]),layer(nb[1])],colours:nb.slice(0,3),base:base,size:[1024,512],
    view:{lon:[-90,90],lat:[-45,45]},warpSteps:4,cut:F.cut,gain:F.gain,maxAlpha:F.maxAlpha};
};
// gen: one number per bake started; a running slice carries its own and stops the moment it is not the latest (a theme changed
// A -> B -> A while A's first bake was still running: that bake must not come back to life). baked: the last three finished
// bakes by recipe key, so a theme the owner already saw comes back at once instead of baking again.
const KIND_NEB={state:'idle',key:'',canvas:null,alpha:1,meanLuma:0,bodyLuma:0,nebLuma:0,ms:0,paintedAt:-1,layers:0,seed:0,colours:null,gen:0,bakes:0,capKey:'',baked:new Map()};
// a backdrop only where the sky is dark: none in the vector look (tactical), the mono palette or a light sky (blueprint's paper),
// where the theme's own sky stays; the reason, or '' when it is wanted
function kindsBackdropOff(){
  if(VEC) return 'vector look'; if(GB) return 'mono palette';
  return kindsLum((SPACE&&SPACE.base)||'#050508')>.12?'light sky':'';
}
// the recipe's identity: the theme's name, the seed, the colours and the base, every layer's own numbers and the cut, gain and
// ceiling; recomputed only when the theme, its space or the machine changes. A backdrop baked under another key is stale and is
// never painted (V switches the theme: the new colours bake, the theme's sky shows meanwhile). applyTheme calls
// CM.invalidateBackdrop() so a switch is seen at once, not on the next frame that happens to look
const KIND_NKEY={space:null,theme:null,name:null,root:null,key:''};
function kindsNebKey(){
  if(KIND_NKEY.space!==SPACE||KIND_NKEY.theme!==THEME_NAME||KIND_NKEY.name!==W.name||KIND_NKEY.root!==W.root_path){ const R=CM.nebulaRecipe();
    KIND_NKEY.space=SPACE; KIND_NKEY.theme=THEME_NAME; KIND_NKEY.name=W.name; KIND_NKEY.root=W.root_path;
    KIND_NKEY.key=JSON.stringify([THEME_NAME,R.seed,R.colours,R.base,R.layers.map(l=>[l.colour,l.offset,l.scale,l.falloff,l.intensity]),R.cut,R.gain,R.maxAlpha]); }
  return KIND_NKEY.key;
}
// a theme (or the machine's name) changed: drop what was baked for another recipe now, and any bake still running for it
CM.invalidateBackdrop=function(){
  KIND_NKEY.space=null; const key=kindsNebKey();
  if(KIND_NEB.key!==key){ KIND_NEB.gen++; KIND_NEB.key=''; KIND_NEB.state='idle'; KIND_NEB.canvas=null; KIND_NEB.paintedAt=-1; }
};
function kindsNebNoise(x,y,z,s){ return vnoise(x,y,z,s); }
// the direction-only nebula of space-3d (nebula.glsl:33-58), the displacement started at zero (its own fix), on value noise
function kindsNebula(px,py,pz,s,steps){
  let scale=Math.pow(2,steps), dx=0, dy=0, dz=0;
  for(let i=0;i<steps;i++){ const ax=kindsNebNoise(px*scale+dx,py*scale+dy,pz*scale+dz,s), ay=kindsNebNoise(py*scale+dx,pz*scale+dy,px*scale+dz,s+1),
    az=kindsNebNoise(pz*scale+dx,px*scale+dy,py*scale+dz,s+2); dx=ax; dy=ay; dz=az; scale*=.5; }
  return kindsNebNoise(px*scale+dx,py*scale+dy,pz*scale+dz,s+3);
}
function kindsHexRgb(h){ return [1,3,5].map(i=>parseInt(String(h).slice(i,i+2),16)); }
function kindsLinLum(r,g,b){ const f=v=>{ v/=255; return v<=.03928?v/12.92:Math.pow((v+.055)/1.055,2.4); }; return .2126*f(r)+.7152*f(g)+.0722*f(b); }
// start (or restart, when the theme changed the colours) the bake in idle time: a 1024x512 canvas, the noise sampled on a
// 512x256 grid and scaled up smoothly (a soft cloud loses nothing; the audit reports both, size and grid), a few rows per idle
// slice. Without requestIdleCallback (Safari) the slices run on short timers with an 8 ms budget each.
const KIND_NEB_GRID=[512,256];
function kindsIdle(fn){
  if(typeof requestIdleCallback==='function') return requestIdleCallback(fn,{timeout:120});
  return setTimeout(()=>{ const t0=performance.now(); fn({didTimeout:false,timeRemaining:()=>Math.max(0,8-(performance.now()-t0))}); },16);
}
function kindsBakeSoon(){
  if(typeof ImageData==='undefined'||typeof document==='undefined'||kindsBackdropOff()) return;
  const R=CM.nebulaRecipe(), key=kindsNebKey();
  if(KIND_NEB.key===key&&KIND_NEB.state!=='idle') return;
  const gen=++KIND_NEB.gen;                       // this bake is the latest; every older one stops at its next slice
  KIND_NEB.key=key; KIND_NEB.seed=R.seed; KIND_NEB.layers=R.layers.length; KIND_NEB.colours=R.colours.slice();
  const hit=KIND_NEB.baked.get(key);
  if(hit){ KIND_NEB.canvas=hit.canvas; KIND_NEB.ms=hit.ms; kindsBackdropCap(); KIND_NEB.state='ready'; return; }   // seen before: back at once
  KIND_NEB.state='baking'; KIND_NEB.canvas=null; KIND_NEB.bakes++;
  const W2=KIND_NEB_GRID[0], H2=KIND_NEB_GRID[1], data=new Float32Array(W2*H2*4), t0=performance.now(), lay=R.layers.map(l=>({c:kindsHexRgb(l.colour),o:l.offset,s:l.scale,f:l.falloff,i:l.intensity}));
  let row=0;
  const work=dl=>{
    if(KIND_NEB.gen!==gen) return;
    while(row<H2&&(dl.timeRemaining()>1.5||dl.didTimeout)){
      const lat=(.5-(row+.5)/H2)*Math.PI/2, cl=Math.cos(lat), sl=Math.sin(lat);
      for(let x=0;x<W2;x++){ const lon=((x+.5)/W2-.5)*Math.PI, dx=cl*Math.sin(lon), dy=sl, dz=cl*Math.cos(lon);
        let r=0,g=0,b=0,a=0;
        lay.forEach((l,li)=>{ const v=kindsNebula(dx*l.s+l.o[0]*.001,dy*l.s+l.o[1]*.001,dz*l.s+l.o[2]*.001,(R.seed+li*977)%99991,R.warpSteps);
          const al=Math.pow(Math.min(1,Math.max(0,(v-R.cut)*R.gain*l.i)),l.f*.5)*R.maxAlpha; r=r*(1-al)+l.c[0]*al; g=g*(1-al)+l.c[1]*al; b=b*(1-al)+l.c[2]*al; a=a+(1-a)*al; });
        const k=(row*W2+x)*4; data[k]=r; data[k+1]=g; data[k+2]=b; data[k+3]=a; }   // premultiplied: r g b already carry a
      row++;
      if(dl.didTimeout&&row%8===0) break;
    }
    if(row<H2){ kindsIdle(work); return; }
    // the small grid into an image, scaled up to 1024x512
    const small=kindsCanvas(W2,H2), sg=small.getContext('2d'), img=sg.createImageData(W2,H2);
    for(let i=0;i<W2*H2;i++){ const a=data[i*4+3]; img.data[i*4]=a?Math.round(data[i*4]/a):0; img.data[i*4+1]=a?Math.round(data[i*4+1]/a):0; img.data[i*4+2]=a?Math.round(data[i*4+2]/a):0; img.data[i*4+3]=Math.round(a*255); }
    sg.putImageData(img,0,0);
    const big=kindsCanvas(1024,512), bg=big.getContext('2d'); bg.imageSmoothingEnabled=true; bg.imageSmoothingQuality='high'; bg.drawImage(small,0,0,1024,512);
    KIND_NEB.canvas=big; KIND_NEB.ms=Math.round(performance.now()-t0); kindsBackdropCap(); KIND_NEB.state='ready';
    KIND_NEB.baked.set(key,{canvas:big,ms:KIND_NEB.ms}); while(KIND_NEB.baked.size>3) KIND_NEB.baked.delete(KIND_NEB.baked.keys().next().value);
  };
  kindsIdle(work);
}
// the cap: the backdrop's mean luminance stays at or under 30% of the bodies' (both linear, WCAG), read from the pixels
function kindsBodyLuma(){
  const arts=(MOONS||[]).map(m=>{ const d=KIND_DRAWN.get(m.uid); if(d&&d.core&&kindsMeta(d.core).luma) return kindsMeta(d.core).luma; const L=planetLook(m); return L?kindsMeta(purposeArt(12,L,0,kindsLightBucket(m.x,m.y))).luma:0; }).filter(v=>v>0);
  return arts.length?arts.reduce((a,b)=>a+b,0)/arts.length:0.05;
}
// what the cap was measured against: the theme, the palette names and each body's purpose; the cap is taken again when it moves
function kindsCapKey(){ return THEME_NAME+'|'+(typeof SKIN!=='undefined'&&SKIN?SKIN.name:'')+'|'+(MOONS||[]).map(m=>(m.type||'')+(m.kind||'')).join(''); }
function kindsBackdropCap(){
  const c=KIND_NEB.canvas; if(!c) return;
  KIND_NEB.capKey=kindsCapKey();
  const base=kindsHexRgb(((CM.nebulaRecipe()||{}).base)||'#050508'), px=c.getContext('2d').getImageData(0,0,c.width,c.height).data, body=kindsBodyLuma();
  const mean=al=>{ let s=0,n=0; for(let i=0;i<px.length;i+=4*7){ const a=px[i+3]/255*al; s+=kindsLinLum(base[0]*(1-a)+px[i]*a,base[1]*(1-a)+px[i+1]*a,base[2]*(1-a)+px[i+2]*a); n++; } return n?s/n:0; };
  let al=1; const cap=.3*body;
  if(mean(1)>cap){ let lo=0,hi=1; for(let i=0;i<14;i++){ const m=(lo+hi)/2; if(mean(m)>cap) hi=m; else lo=m; } al=lo; }
  KIND_NEB.alpha=al; KIND_NEB.bodyLuma=body; KIND_NEB.meanLuma=mean(al); KIND_NEB.nebLuma=mean(1);
}
CM.drawBackdrop2D=function(g){
  if(kindsBackdropOff()) return;                                    // the theme's own sky (drawNebula) paints instead
  const key=kindsNebKey();
  if(KIND_NEB.key!==key||KIND_NEB.state==='idle'){ kindsBakeSoon(); return; }
  if(KIND_NEB.state!=='ready'||!KIND_NEB.canvas) return;
  if(KIND_NEB.capKey!==kindsCapKey()) kindsBackdropCap();          // the bodies changed under it: the 30% ceiling is read again
  const c=KIND_NEB.canvas, h=VH, w=h*2, slack=Math.max(0,w-VW), cxw=((typeof camx==='number'?camx:0)+VW/2/ZOOM)*ZOOM*.02;
  const x0=-Math.max(0,Math.min(slack,slack/2+cxw)), y0=0;
  g.save(); g.fillStyle=(CM.nebulaRecipe()||{}).base||'#050508'; g.fillRect(0,0,VW,VH);
  g.globalAlpha=KIND_NEB.alpha; g.imageSmoothingEnabled=true; g.drawImage(c,x0,y0,w<VW?VW:w,h); g.restore();
  KIND_NEB.paintedAt=FRAME_T;
};
CM.backdropOn=()=>KIND_NEB.paintedAt===FRAME_T&&!kindsBackdropOff();
// seed and the lumas only for a backdrop baked from the current recipe; before the bake, while a theme change re-bakes, or where
// none is drawn, seed is null (so the [backdrop] check cannot pass on nothing) and `off` says why
function kindsBackdropAudit(){
  const R=CM.nebulaRecipe(), off=kindsBackdropOff(), fresh=!off&&KIND_NEB.state==='ready'&&!!KIND_NEB.canvas&&KIND_NEB.key===kindsNebKey();
  return {seed:fresh?KIND_NEB.seed:null,recipeSeed:R.seed,drawn:fresh&&KIND_NEB.paintedAt>=0,painted:CM.backdropOn(),state:off?'off':(KIND_NEB.state==='ready'&&!fresh?'stale':KIND_NEB.state),
    off:off||null,colours:fresh?KIND_NEB.colours:null,recipeColours:R.colours,meanLuma:fresh?KIND_NEB.meanLuma:null,bodyLuma:fresh?KIND_NEB.bodyLuma:null,
    nebulaLuma:fresh?KIND_NEB.nebLuma:null,alpha:fresh?KIND_NEB.alpha:null,layers:KIND_NEB.layers||R.layers.length,bakes:KIND_NEB.bakes,cached:KIND_NEB.baked.size,
    size:fresh?[KIND_NEB.canvas.width,KIND_NEB.canvas.height]:R.size.slice(),grid:KIND_NEB_GRID.slice(),bakeMs:KIND_NEB.ms};
}

// ---- the legend: two lines for the chip, and the full sheet's rows ----
// What the chip says; where it sits is the chrome's. Exactly two lines of plain text, each at most 100 characters and made of
// letters, digits, spaces and the middle dot only (the page's fonts have no arc, snowflake or dashed-line symbols: they printed as
// boxes), so the chrome can put them in one box of two rows without wrapping or measuring. text is the two joined by a newline;
// items are the nine purposes with their icons, marks the five conditions with their colour (the chrome may draw them as it likes).
CM.LEGEND_MARKS=[['broken','broken','red'],['waiting','needs you','amber'],['working','working','blue arc'],['quiet','quiet 30+ days','frost'],['rebuildable','rebuildable','dashed ring']];
CM.legendChip=function(){
  const l1='A planet holds: '+PURPOSE_KEYS.map(k=>PURPOSE[k].name).join(' · '),
    l2=CM.LEGEND_MARKS.map(m=>m[2]+' '+m[1]).join(' · ');
  return {lines:[l1,l2],text:l1+'\n'+l2,
    items:PURPOSE_KEYS.map(k=>({purpose:k,name:PURPOSE[k].name,icon:()=>purposeIcon(k,28)})),
    marks:CM.LEGEND_MARKS.map(m=>({key:m[0],word:m[1],shows:m[2],colour:m[0]==='broken'?KIND_STATUS.broken:m[0]==='waiting'?KIND_STATUS.waiting:m[0]==='working'?KIND_STATUS.working:m[0]==='quiet'?KIND_STATUS.frost:KIND_STATUS.idle}))};
};
function kindsLegendHtml(){
  const row=(icon,name,text)=>'<div class="planet-key-row">'+icon+'<b>'+esc(name)+'</b><small>'+esc(text)+'</small></div>';
  const marks=[['working','An arc fills while an agent or a workflow step works there; its night side lights up',{conditions:['working'],night:true,progress:.4}],
    ['waiting','Amber glow and dot: it waits for your answer or a decision; red: something is broken',{conditions:['waiting'],health:'broken'}],
    ['quiet','Faded, frost rim: nothing written there for 30 days or more',{conditions:['quiet']}],
    ['rebuildable','Dashed shell: most of its files can be rebuilt (caches, dependencies)',{conditions:['rebuildable']}],
    ['empty','Dashed outline: an empty folder',{conditions:['empty']}]];
  return '<div class="planet-key" data-kinds-key="1">'+PURPOSE_KEYS.map(k=>row(purposeIconImg(k,40),PURPOSE[k].name,PURPOSE[k].look+'. '+PURPOSE[k].rule[0].toUpperCase()+PURPOSE[k].rule.slice(1)+'.')).join('')+'</div>'+
    '<h2>MARKS ON A PLANET</h2><div class="planet-key" data-kinds-key="1">'+marks.map(([k,txt,o])=>row(purposeIconImg('code',40,Object.assign({seed:'mark:'+k},o)),k,txt)).join('')+'</div>';
}
// the legend's purpose and mark grids, three across with small icons, so all nine purposes sit in the top half of the sheet
// (styles set from JS: the served page's CSP refuses style attributes in markup)
function kindsLegendFit(root){
  if(!root||!root.querySelectorAll||MOB) return;
  // the owner opened it: it sits over the wingmate panel (z 31), whose box covered its lower half at 1600x900
  root.style.zIndex='32';
  root.querySelectorAll('[data-kinds-key]').forEach(k=>{ Object.assign(k.style,{gridTemplateColumns:'repeat(3,minmax(0,1fr))',gap:'6px',marginBottom:'8px'});
    k.querySelectorAll('.planet-key-row').forEach(r=>{ Object.assign(r.style,{gridTemplateColumns:'44px 1fr',padding:'6px',columnGap:'6px'});
      const i=r.querySelector('.file-planet'); if(i) Object.assign(i.style,{width:'40px',height:'40px'});
      const t=r.querySelector('small'); if(t) Object.assign(t.style,{lineHeight:'1.3'}); }); });
}

// ---- the purpose row on a planet card, and the sheet that changes it (Change; the bridge serves POST /area/set) ----
CM.purposeRow=function(uid){
  const u=typeof unitByUid==='function'?unitByUid(uid):null; if(!u||u.kind!=='area') return {text:'',canChange:false};
  const p=u.alias_of?'unsorted':(KIND_PURPOSE[String(u.area_kind||'unknown')]||'unsorted');
  return {text:PURPOSE[p].name+' · '+(u.kind_guessed===false?'set by you':'guessed from the files'),canChange:!MOB&&!REMOTE,purpose:p,glyph:GLYPHS.includes(u.glyph)?u.glyph:null};
};
const KIND_SHEET={uid:null,open:false};
function kindsSheetEl(){
  let el=document.getElementById('purposesheet'); if(el) return el;
  el=document.createElement('div'); el.id='purposesheet'; el.setAttribute('role','dialog'); el.setAttribute('aria-modal','true');
  // opaque and above the map's plates and panels: a decision sheet must not show the map's words through it
  Object.assign(el.style,{position:'fixed',left:'50%',top:'50%',transform:'translate(-50%,-50%)',zIndex:'400',width:'min(560px,94vw)',maxHeight:'92vh',overflow:'auto',
    background:'#0B1220',border:'1px solid #22304A',boxShadow:'0 0 0 100vmax rgba(5,7,13,.55), 0 12px 40px rgba(0,0,0,.6)',padding:'18px 20px',color:'#E8EEF6',
    font:'15px/1.4 Barlow, "Barlow Condensed", system-ui, sans-serif',display:'none'});
  el.addEventListener('click',kindsSheetClick); (document.body||document.documentElement).appendChild(el); return el;
}
// a choice button; the chosen one has a thin light frame and a tinted fill. The keyboard focus ring is the text colour, never
// the page's amber focus outline (amber means review or decision on this map, a colour that only says status)
function kindsBtn(el,on){ Object.assign(el.style,{display:'flex',flexDirection:'column',alignItems:'center',gap:'4px',padding:'8px 6px',minHeight:'44px',cursor:'pointer',
  background:on?'rgba(232,238,246,.10)':'transparent',color:'#E8EEF6',border:on?'2px solid #E8EEF6':'1px solid #22304A',font:'600 14px/1.2 "Barlow Condensed", Barlow, sans-serif',
  outline:'none',boxShadow:'none'});
  el.addEventListener('focus',()=>{ el.style.boxShadow='0 0 0 2px #05070D, 0 0 0 4px #E8EEF6'; });
  el.addEventListener('blur',()=>{ el.style.boxShadow='none'; }); }
CM.openPurposeSheet=function(uid){
  const u=typeof unitByUid==='function'?unitByUid(uid):null; if(!u||u.kind!=='area') return null;
  const row=CM.purposeRow(uid), el=kindsSheetEl(); KIND_SHEET.uid=uid; el.innerHTML=''; el.setAttribute('aria-label','What '+String(u.name)+' holds');
  const h=document.createElement('h2'); h.textContent='What does '+String(u.name)+' hold?';
  Object.assign(h.style,{margin:'0 0 4px',font:'600 22px/1.2 "Barlow Condensed", Barlow, sans-serif',color:'#E8EEF6'});
  const sub=document.createElement('div'); sub.textContent='Now: '+row.text+'. Pick what it holds; its planet changes to match.'; Object.assign(sub.style,{color:'#9AA8BC',marginBottom:'12px'});
  // purposes three across (a 3 x 3 square), emblems five across; items may carry their own attribute name
  const grid=(items,attrName,cols)=>{ const d=document.createElement('div'); Object.assign(d.style,{display:'grid',gridTemplateColumns:'repeat('+(cols||3)+',minmax(0,1fr))',gap:'8px',marginBottom:'14px'});
    items.forEach(it=>{ const b=document.createElement('button'); b.type='button'; b.setAttribute(it.attr||attrName,it.key); b.setAttribute('aria-pressed',it.on?'true':'false'); kindsBtn(b,it.on);
      b.appendChild(it.icon); const s=document.createElement('span'); s.textContent=it.name; b.appendChild(s); d.appendChild(b); }); return d; };
  // icons keep their own pixel size (the page's canvas rules would stretch them across the button)
  const fix=(c,px)=>{ Object.assign(c.style,{width:px+'px',height:px+'px',flex:'none',imageRendering:'pixelated'}); return c; };
  const pIcon=k=>{ const c=kindsCanvas(72,72), g=c.getContext('2d'); g.imageSmoothingEnabled=false; g.drawImage(purposeIcon(k,72),0,0); return fix(c,72); };
  const gIcon=k=>{ const c=kindsCanvas(28,28); kindsGlyph(c.getContext('2d'),k,4,4,20,'#E8EEF6'); return fix(c,28); };
  const h3=t=>{ const e=document.createElement('h3'); e.textContent=t; Object.assign(e.style,{margin:'4px 0 8px',font:'600 18px/1.2 "Barlow Condensed", Barlow, sans-serif',color:'#9AA8BC'}); return e; };
  const msg=document.createElement('div'); msg.id='purposemsg'; msg.setAttribute('aria-live','polite'); Object.assign(msg.style,{minHeight:'20px',color:'#FFB547'});
  const close=document.createElement('button'); close.type='button'; close.textContent='Close'; close.setAttribute('data-sheet-close','1'); kindsBtn(close,false); close.style.flexDirection='row'; close.style.padding='8px 18px';
  el.appendChild(h); el.appendChild(sub); el.appendChild(h3('Purpose'));
  el.appendChild(grid(PURPOSE_KEYS.map(k=>({key:k,name:PURPOSE[k].name,icon:pIcon(k),on:k===row.purpose})),'data-purpose',3));
  // the emblems: the purpose's own one first (marked while the owner has chosen none, so something is always marked), then the
  // twelve the user may choose ([data-glyph]); choosing the first clears the user's choice
  el.appendChild(h3('Emblem'));
  const own=PURPOSE[row.purpose||'unsorted'].glyph;
  el.appendChild(grid([{key:'',attr:'data-glyph-own',name:'Its own: '+(GLYPH_WORD[own]||own),icon:gIcon(own),on:!row.glyph}]
    .concat(GLYPHS.map(k=>({key:k,name:GLYPH_WORD[k],icon:gIcon(k),on:k===row.glyph}))),'data-glyph',5));
  // the footer: the save message on the left, Close on the right
  const foot=document.createElement('div'); Object.assign(foot.style,{display:'flex',alignItems:'center',justifyContent:'space-between',gap:'12px'});
  foot.appendChild(msg); foot.appendChild(close); el.appendChild(foot);
  el.style.display='block'; KIND_SHEET.open=true; try{ el.querySelector('[aria-pressed="true"]')&&el.querySelector('[aria-pressed="true"]').focus(); }catch(e){}
  return el;
};
CM.closePurposeSheet=function(){ const el=document.getElementById('purposesheet'); if(el){ el.style.display='none'; } KIND_SHEET.uid=null; KIND_SHEET.open=false; };
function kindsSheetOpen(){ return KIND_SHEET.open; }
async function kindsSheetClick(e){
  const t=e.target&&e.target.closest?e.target:null; if(!t) return;
  if(t.closest('[data-sheet-close]')){ CM.closePurposeSheet(); return; }
  const pb=t.closest('[data-purpose]'), gb=t.closest('[data-glyph]'), ob=t.closest('[data-glyph-own]'); if(!pb&&!gb&&!ob) return;
  const uid=KIND_SHEET.uid, row=CM.purposeRow(uid), msg=document.getElementById('purposemsg');
  if(!row.canChange){ if(msg) msg.textContent='Change this on your computer.'; return; }
  const kind=PURPOSE_KIND[pb?pb.getAttribute('data-purpose'):row.purpose], glyph=ob?null:gb?gb.getAttribute('data-glyph'):row.glyph;
  if(msg) msg.textContent='Saving…';
  try{
    await api('/area/set',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({uid:uid,kind:kind,glyph:glyph||null})});
    try{ applyWorld(await api('/state?fresh=1')); }catch(err){}
    CM.closePurposeSheet(); toast(String((unitByUid(uid)||{}).name||'')+' is now '+PURPOSE[KIND_PURPOSE[kind]||'unsorted'].name,2400);
  }catch(err){ if(msg) msg.textContent='Could not save: '+String(err&&err.message||err); }
}
// ---- the one palette: the old tables' folder keys read from PURPOSE (render-game.py SECTORS, THEMES, TYPE_THEME, KIND_THEME) ----
// A folder's place (the sector record: floor, grid, accent, glow, ink) and its sub-folders' fallback colours (the THEMES record:
// base, land, hi, glow) are made from its purpose's five colours, so the body, its plate and its rooms agree in every theme and
// skin. The accent leans toward the secondary text grey: colour means status, a purpose only tints.
const KIND_SECT=new Map();
function kindsSector(p){
  const P=planetPal(PURPOSE[p]?p:'unsorted'), key=[p,THEME_NAME,P.name,P.colours.join(''),GB?1:0].join('|');
  let r=KIND_SECT.get(key); if(r) return r;
  const C=P.colours, base=(SPACE&&SPACE.base)||'#050508';
  r={floor:mixPixel(C[0],base,.6),floor2:mixPixel(C[0],base,.45),grid:mixPixel(C[1],base,.35),accent:mixPixel(C[3],'#9AA8BC',.35),
    glow:mixPixel(C[2],'#9AA8BC',.3),ink:mixPixel(C[4],'#E8EEF6',.5),purpose:p};
  KIND_SECT.set(key,r); return r;
}
// the STYLE record of a purpose (render-game.py STYLE['p:code'] ...): a, b, c, d and glow from its five colours
function kindsStyle(p){
  const P=planetPal(PURPOSE[p]?p:'unsorted'), C=P.colours;
  return {a:C[2],b:C[1],c:C[4],d:C[3],glow:P.glow,purpose:PURPOSE[p]?p:'unsorted'};
}
function kindsTheme(p){
  const q=PURPOSE[p]?p:'unsorted', P=planetPal(q), C=P.colours;
  return {name:PURPOSE[q].name.toLowerCase(),group:PURPOSE[q].name+' folder',base:C[2],land:C[1],hi:C[4],glow:mixPixel(C[3],P.glow,.3),purpose:q};
}
// the 3D view's first frame lights each planet the same way: the elevation in radians and the toon steps
CM.planetLight=()=>({elevation:KIND_LIGHT.el,toon:KIND_TOON.slice(),bands:3,tilt:KIND_TILT});
CM.planetLook=planetLook; CM.surfaceAt=surfaceAt; CM.purposes=PURPOSE; CM.glyphs=GLYPHS; CM.purposeIcon=purposeIcon; CM.glyphDraw=kindsGlyph;
CM.onInit.push(()=>{
  CM.backLayers.push({pri:25,fn:k=>{ if(!kindsSheetOpen()) return false; if(k!=='has') CM.closePurposeSheet(); return true; }});
  kindsBakeSoon();
  const L=document.getElementById('legend');
  if(L&&typeof MutationObserver==='function') new MutationObserver(()=>kindsLegendFit(L)).observe(L,{childList:true});
  // '?' opens the legend (what the bodies and marks mean) until the '?' sheet lands (the chrome module, CM.openHelp), which carries it
  addEventListener('keydown',e=>{
    if(e.key!=='?'||e.repeat||typing()||modKey(e)||typeof CM.openHelp==='function') return;
    const el=document.getElementById('legend'); if(!el) return;
    el.classList.toggle('on'); if(el.classList.contains('on')){ el.innerHTML=systemLegend(); kindsLegendFit(el); }
  });
});
