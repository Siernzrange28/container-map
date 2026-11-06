"""Spatial flight invariants, executed against the same simulation the page flies."""
import json
from pathlib import Path
import shutil
import re
import subprocess
import tempfile
import time
import unittest

import testenv  # noqa: F401
import test_game
from test_3d_core import CDP_RUNNER

ROOT = Path(__file__).resolve().parent


@unittest.skipUnless(shutil.which('node'), 'node required')
class SpatialFlight(unittest.TestCase):
    def sim(self, body):
        script = ('require(' + json.dumps(str(ROOT / 'world3d' / 'flight.js')) + ');'
                  'const F = globalThis.CM3D.flightPhysics;\n' + body)
        p = subprocess.run(['node', '-e', script], capture_output=True, text=True, timeout=20)
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout)

    def test_nose_up_changes_real_height_and_reduces_horizontal_travel(self):
        v = self.sim('const s=F.create({speed:200}); for(let i=0;i<120;i++) F.step(s,{speed:200,heading:0,pitch:1},1/120,[]); console.log(JSON.stringify(s));')
        self.assertGreater(v['y'], 25)
        self.assertLess(v['x'], 200)
        self.assertGreater(v['pitch'], 35)
        self.assertLessEqual(v['speed'], 200.001)

    def test_release_holds_the_climb_angle_and_level_recovers(self):
        v = self.sim('const s=F.create({speed:200}); for(let i=0;i<120;i++) F.step(s,{speed:200,pitch:1},1/120,[]);'
                     'for(let i=0;i<240;i++) F.step(s,{speed:200},1/120,[]); const held={pitch:s.pitch,y:s.y};'
                     'for(let i=0;i<240;i++) F.step(s,{speed:200,level:true},1/120,[]); console.log(JSON.stringify({held,s}));')
        self.assertGreater(v['held']['pitch'], 35)
        self.assertGreater(v['held']['y'], 200)
        self.assertLess(abs(v['s']['pitch']), .1)

    def test_can_dive_below_the_map_plane(self):
        v = self.sim('const s=F.create({speed:200}); for(let i=0;i<180;i++) F.step(s,{speed:200,pitch:-1},1/120,[]); console.log(JSON.stringify(s));')
        self.assertLess(v['y'], -70)
        self.assertLess(v['climb'], -100)

    def test_turns_have_momentum_and_braking_settles(self):
        v = self.sim('const s=F.create({speed:200}); F.step(s,{speed:200,heading:Math.PI/2},.05,[]); const turn={vx:s.vx,vz:s.vz};'
                     'for(let i=0;i<120;i++) F.step(s,{speed:0,brake:true},1/120,[]); console.log(JSON.stringify({turn,s}));')
        self.assertGreater(v['turn']['vx'], 100)
        self.assertGreater(v['turn']['vz'], 0)
        self.assertLess(v['s']['speed'], .1)

    def test_swept_contact_prevents_tunnelling_at_high_speed(self):
        v = self.sim('const s=F.create({x:-100,speed:20000}); F.step(s,{speed:20000},.05,[{uid:"rock",x:0,y:0,r:8}]); console.log(JSON.stringify(s));')
        self.assertLessEqual(v['x'], -22)
        self.assertGreater(v['contacts'], 0)
        self.assertLess(v['speed'], .01)

    def test_slides_along_a_sphere_and_escapes_when_turned_away(self):
        v = self.sim('const s=F.create({x:-23,z:0,speed:150}); const b=[{x:0,y:0,r:8}];'
                     'for(let i=0;i<60;i++) F.step(s,{speed:150,heading:.7},1/120,b); const slide={x:s.x,z:s.z};'
                     'for(let i=0;i<60;i++) F.step(s,{speed:150,heading:Math.PI},1/120,b); console.log(JSON.stringify({slide,s}));')
        self.assertGreater(v['slide']['z'], 20)
        self.assertGreaterEqual((v['slide']['x'] ** 2 + v['slide']['z'] ** 2) ** .5, 22)
        self.assertLess(v['s']['x'], v['slide']['x'] - 20)

    def test_a_body_against_the_wall_never_pins_the_ship(self):
        # the page's own flightStep3d and p3Glance (render-game.py) over this physics: a planet whose pad reaches the east
        # wall, the ship coming up between them (Codex review r2's case). The edge's turn and the glance must not cancel.
        src = (ROOT / 'render-game.py').read_text(encoding='utf-8')
        fns = [re.search(r'^function %s\(.*?^\}' % name, src, re.S | re.M).group(0) for name in ('flightStep3d', 'p3Glance')]
        v = self.sim('const k={cruise:200,brakeSpd:110,boostK:1.7,boostUp:.5,boostDown:1,burn:2,refill:4.5,lockout:false,turnDeg:55,turnHardDeg:110,pitch:30};'
                     'const clamp=(x,a,b)=>Math.max(a,Math.min(b,x)),headingNorm=h=>Math.atan2(Math.sin(h),Math.cos(h)),p3PitchDives=()=>true;'
                     'const TILE=1,minGX=-2400,maxGX=2400,minGY=-1500,maxGY=1500; let px,py;'
                     'const FL={hdg:294*Math.PI/180,spd:200,meter:1,locked:false,spent:false};\n' + '\n'.join(fns) + '\n'
                     'const bodies=[{uid:"outer",x:2208,y:0,r:300}],bounds={x0:minGX,x1:maxGX,z0:minGY,z1:maxGY,y0:-8000,y1:8000};'
                     'const f=F.create({x:2080,y:0,z:289,heading:FL.hdg,speed:200}); px=f.x; py=f.z; let last=0, at6=null;'
                     'for(let i=0;i<12*60;i++){ flightStep3d(1/60,{},k,0); F.shift(f,px-f.x,0,py-f.z,bodies,bounds);'
                     ' F.step(f,{heading:FL.hdg,speed:FL.spd,attitude:0},1/60,bodies,bounds); p3Glance(f,1/60,k); px=f.x; py=f.z;'
                     ' if(f.contact) last=(i+1)/60; if(i===359) at6={x:f.x,z:f.z}; }'
                     'console.log(JSON.stringify({last,moved:Math.hypot(f.x-at6.x,f.z-at6.z),contact:f.contact}));')
        self.assertLess(v['last'], 3, v)               # the contact ends within a few seconds
        self.assertIsNone(v['contact'], v)
        self.assertGreater(v['moved'], 200 * 4, v)     # and the ship flies on (pinned it would not move at all)

    def test_pause_freezes_position_pitch_and_velocity(self):
        v = self.sim('const s=F.create({speed:200}); const before=JSON.stringify(s); F.step(s,{speed:500,pitch:1,paused:true},.1,[]); console.log(JSON.stringify(before===JSON.stringify(s)));')
        self.assertTrue(v)

    def test_time_steps_are_consistent_at_30_60_and_144_hz(self):
        v = self.sim('function run(hz){const s=F.create({speed:200}); for(let i=0;i<hz*3;i++) F.step(s,{speed:200,pitch:i<hz?1:0,heading:0},1/hz,[]); return s;}'
                     'console.log(JSON.stringify([run(30),run(60),run(144)]));')
        for s in v[1:]:
            self.assertAlmostEqual(s['x'], v[0]['x'], delta=2)
            self.assertAlmostEqual(s['y'], v[0]['y'], delta=2)
            self.assertAlmostEqual(s['pitch'], v[0]['pitch'], delta=.3)

    def test_spawn_and_bounds_are_finite_and_clear(self):
        v = self.sim('const h=F.spawnHeight(0,0,[{x:0,y:0,r:100}]); const s=F.create({x:100,speed:200});'
                     'F.step(s,{speed:200},.1,[],{x0:-100,x1:100}); console.log(JSON.stringify({h,s}));')
        self.assertEqual(v['h'], 124)
        self.assertEqual(v['s']['x'], 100)
        self.assertEqual(v['s']['vx'], 0)
        self.assertEqual(v['s']['edge'], 'x')


BROWSER_FLIGHT = r"""(async()=>{
  const sleep=ms=>new Promise(r=>setTimeout(r,ms));
  const until=async(f,ms)=>{const end=performance.now()+ms;while(performance.now()<end){if(f())return true;await sleep(40);}return false;};
  const key=(type,key)=>dispatchEvent(new KeyboardEvent(type,{key,bubbles:true,cancelable:true}));
  const tap=k=>{key('keydown',k);key('keyup',k);};
  const advance=seconds=>__3dStep(Math.round(seconds*60),1/60);
  setView('map'); CM.enter3d({});
  const out={ready:await until(()=>__3dAudit().mode==='fp'&&!!__3dAudit().input.flight,15000)};
  if(!out.ready)return out;
  // the flight (flight-rs's handling): the ship always flies forward. This short test map (436 u tall) has a band clear of
  // its edges only near its middle line; fly there, between the sun and the west planet, so the edge never steers
  const B=PAGE3D.bounds(), midY=(B.y0+B.y1)/2, K=flyK(), fa=()=>__fleetAudit(), sa=()=>__3dAudit().ship;
  const start=h=>{ PAGE3D.place(-600,midY,h); P3.hold=false; advance(.05); };
  const wrap=d=>((d+540)%360)-180;
  const reset=()=>{ ['w','s','q','e','c','Shift','x'].forEach(k=>key('keyup',k)); FL.tap={}; FL.roll=null; FL.meter=1; FL.locked=false; FL.spent=false; };
  const setting=v=>{ CM.openHelp('settings'); const control=document.querySelector('#set-pitch-keys');
    if(!control)throw new Error('Missing flight pitch setting');
    control.value=v; control.dispatchEvent(new Event('change',{bubbles:true}));
    const saved=p3Get('pitchKeys','')===v; CM.closeHelp(); return saved; };
  __3dHold(true);
  start(Math.PI); const s0=sa(); advance(1); const s1=sa();
  out.forward=Math.abs(fa().speed-K.cruise)<2&&s1.x<s0.x-K.cruise*.8&&Math.abs(s1.alt-s0.alt)<1;
  // R climbs (the nose's attitude, not a rate: at most the skin's pitch), the cockpit rides the ship, letting go levels it
  start(Math.PI); const before=sa();
  key('keydown','r'); advance(1); key('keyup','r');
  const climbed=sa();
  out.climb=climbed.alt>before.alt+10&&climbed.pitch>20&&climbed.pitch<=K.pitch+.5;
  out.cameraFollows=Math.abs(__3dAudit().camera.pos[1]-climbed.alt-1.4)<.2;
  advance(1.5); out.levels=Math.abs(sa().pitch)<1;
  // W dives by default (pilot style), S climbs; X holds the nose level whatever is held
  start(Math.PI); let a0=sa().alt; key('keydown','w'); advance(1); out.wDives=sa().pitch< -20&&sa().alt<a0-10; key('keyup','w'); advance(1.5);
  start(Math.PI); a0=sa().alt; key('keydown','s'); advance(1); out.sClimbs=sa().pitch>20&&sa().alt>a0+10;
  key('keydown','x'); advance(1.5); out.level=Math.abs(sa().pitch)<1; key('keyup','x'); key('keyup','s'); advance(1);
  // Change the real Settings control so a broken event route or stale pitch cache fails too.
  const pitchCase=(mode,k,sign)=>{ reset(); const saved=setting(mode); start(Math.PI); advance(1); const alt=sa().alt;
    key('keydown',k); advance(1); key('keyup',k); return saved&&sign*sa().pitch>20&&sign*(sa().alt-alt)>10; };
  out.pitchSettings=pitchCase('dive','w',-1)&&pitchCase('dive','s',1)&&pitchCase('climb','w',1)&&pitchCase('climb','s',-1);
  reset(); setting('dive');
  // A/D turn at the turn rate with the wings at bankSoft; Q/E alone turn at half of it with the wings at bankHard; with
  // A/D on the same side the tight turn; the wings level again when let go
  const turned=down=>{ reset(); start(0); advance(1); const h0=fa().heading; down.forEach(x=>key('keydown',x)); advance(.5);
    const r={d:wrap(fa().heading-h0),bank:fa().bank,edge:FL.homing}; down.forEach(x=>key('keyup',x)); return r; };
  const right=turned(['d']), left=turned(['a']), tilt=turned(['e']), tiltQ=turned(['q']), hard=turned(['d','e']);
  out.turn=Math.abs(right.d-K.turnDeg*.5)<2&&right.bank>K.bankSoft*.8&&Math.abs(left.d+K.turnDeg*.5)<2&&left.bank< -K.bankSoft*.8;
  out.tilt=Math.abs(tilt.d-K.turnDeg*.25)<2&&tilt.bank>K.bankHard*.8;
  out.tiltQ=Math.abs(tiltQ.d+K.turnDeg*.25)<2&&tiltQ.bank< -K.bankHard*.8&&!tiltQ.edge;
  out.hardTurn=Math.abs(hard.d-K.turnHardDeg*.5)<2&&![right,left,tilt,hard].some(r=>r.edge);
  // (a slow tilt turn that ends along the map, so the ship stays clear of this narrow map's edge band, whose turn banks)
  start(-K.turnDeg*.25*Math.PI/180); key('keydown','e'); advance(.5); key('keyup','e'); advance(1.2);
  out.wingsLevel=Math.abs(fa().bank)<1&&!FL.homing;
  // a double tap of E (or Q) rolls the ship where it is: the heading and the path stay
  start(0); advance(.2); const h1=fa().heading; tap('e'); tap('e'); advance(.1);
  out.roll=fa().rolling&&sa().rollDeg>0&&Math.abs(wrap(fa().heading-h1))<.5;
  advance(.6); out.rollEnds=!fa().rolling;
  reset(); start(0); advance(.2); const hq=fa().heading; tap('q'); tap('q'); advance(.1);
  out.rollQ=fa().rolling&&sa().rollDeg<0&&Math.abs(wrap(fa().heading-hq))<.5;
  advance(.6); out.rollQEnds=!fa().rolling;
  // Shift boosts (and the view widens), C brakes; both draw the one meter, an empty one stops the boost until let go
  reset(); start(Math.PI); advance(.5); const fov0=__3dAudit().camera.fov, streak0=__3dAudit().scene.dustStreak;
  key('keydown','Shift'); advance(1);
  out.boost=fa().speed>K.cruise*1.5&&fa().boosting&&__3dAudit().camera.fov>fov0+4;
  const streakBoost=__3dAudit().scene.dustStreak;
  key('keyup','Shift'); advance(1.5);
  out.speedLines=streak0>0&&streakBoost>streak0&&__3dAudit().scene.dustStreak===streak0;
  reset(); start(Math.PI); advance(.5); const speedBefore=fa().speed, meterBefore=fa().meter;
  key('keydown','c'); advance(.8); out.brake=fa().braking&&fa().speed<speedBefore-20&&fa().speed<=K.brakeSpd+5&&fa().meter<meterBefore-.1; key('keyup','c'); advance(1);
  start(Math.PI); FL.meter=1; key('keydown','Shift'); advance(K.burn+.3); out.meter=!fa().boosting&&fa().meter<.1; key('keyup','Shift'); advance(.1);
  // straight at the sun: the ship glances off it (the nose swings to the slide) and flies on, clear of it
  start(0); advance(4.5); const st=cmWorld().star, fl=P3.flight;
  out.glance=!fl.contact&&Math.abs(wrap(fa().heading))>20&&Math.hypot(fl.x-st.x,fl.y,fl.z-st.y)>st.r+14-.5;
  // nose straight out at the map's east edge: the ship turns back in and stays inside
  PAGE3D.place(B.x1-60,midY,0); P3.hold=false; advance(2.5);
  out.edge=Math.cos(fa().heading*Math.PI/180)<0&&fa().x<=B.x1+.01;
  // pause freezes it all
  start(Math.PI);
  key('keydown','Escape'); key('keyup','Escape');
  const paused=__3dAudit().ship;
  key('keydown','r'); advance(1); key('keyup','r');
  const frozen=__3dAudit().ship;
  out.pause=__3dAudit().mode==='paused'&&Math.hypot(frozen.x-paused.x,frozen.y-paused.y,frozen.alt-paused.alt)<.05&&Math.abs(frozen.pitch-paused.pitch)<.05;
  key('keydown','Escape'); key('keyup','Escape'); advance(.2);
  out.released=!__3dAudit().input.held.up;
  out.guide=!document.getElementById('flight3d-instruments').hidden&&/Speed: (cruising|boosting|slowing)/.test(document.getElementById('flight3d-instruments').textContent);
  // L locks the planet ahead; E no longer does (it tilts the wings)
  const p=cmWorld().planets[0]; PAGE3D.place(p.x-400,p.y,0); advance(.05);
  tap('e'); advance(.05); const eLock=__3dAudit().hud.lock.state; tap('l'); advance(.05); const lLock=__3dAudit().hud.lock;
  out.lockKey=eLock!=='locked'&&lLock.state==='locked'&&lLock.uid===p.uid;
  PAGE3D.place(p.x+p.r+20,p.y,0); advance(.05);
  key('keydown','v'); key('keyup','v'); advance(.05);
  const eye=__3dAudit().camera.pos;
  out.chase=__3dAudit().camera.rig==='chase'&&cmWorld().planets.concat([cmWorld().star]).every(b=>Math.hypot(eye[0]-b.x,eye[1],eye[2]-b.y)>=b.r);
  __3dHold(false); CM3D.leave('key'); out.left=await until(()=>!CM.mode3d(),5000);
  out.cleaned=!document.getElementById('flight3d-instruments')&&!P3.flight&&FL.spd===0;
  return out;
})()"""


BROWSER_POV = r"""(async()=>{
  const sleep=ms=>new Promise(r=>setTimeout(r,ms));
  const until=async f=>{const end=performance.now()+15000;while(performance.now()<end){if(f())return true;await sleep(40);}return false;};
  const root=document.getElementById('pov-switch'), button=view=>root.querySelector('[data-pov="'+view+'"]');
  const selected=view=>CM.pov()===view&&button(view).getAttribute('aria-pressed')==='true'&&root.querySelectorAll('[aria-pressed="true"]').length===1;
  const fits=()=>{const r=root.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth&&r.top>=0&&r.bottom<=innerHeight;};
  const out={};
  setView('today'); out.hiddenOnToday=root.hidden;
  setView('map'); out.map=!root.hidden&&selected('map')&&root.children.length===3&&fits();
  if(MOB) out.touchTargets=Array.from(root.children).every(b=>b.getBoundingClientRect().height>=44);
  else { const a=root.getBoundingClientRect(), b=document.getElementById('mate').getBoundingClientRect();
    out.assistantClear=a.right<=b.left||a.left>=b.right||a.bottom<=b.top||a.top>=b.bottom; }
  button('chase').click();
  out.thirdEntry=await until(()=>CM.mode3d()&&__3dAudit().mode==='fp'&&__3dAudit().camera.rig==='chase');
  if(!out.thirdEntry)return out;
  __3dHold(true); __3dStep(1,0);
  out.third=selected('chase')&&!__3dAudit().camera.cockpit.visible&&__3dAudit().craft.ownShown&&fits();
  if(MOB) out.counterClear=document.querySelector('.steps3d').getBoundingClientRect().bottom<=root.getBoundingClientRect().top;
  const flight=P3.flight, before=JSON.stringify(flight);
  button('cockpit').click();
  out.preserved=P3.flight===flight&&JSON.stringify(P3.flight)===before;
  __3dStep(1,0);
  out.first=selected('cockpit')&&__3dAudit().camera.cockpit.visible&&!__3dAudit().craft.ownShown&&fits();
  button('chase').click(); __3dStep(1,0);
  out.backToThird=selected('chase')&&__3dAudit().craft.ownShown;
  const alt=P3.flight.y, pitch=P3.flight.pitch;
  P3.flight.y=900; P3.flight.pitch=80; __3dStep(1,0);
  const projected=__3dProject('ship');
  out.thirdFramed=projected.x>innerWidth*.15&&projected.x<innerWidth*.85&&projected.y>innerHeight*.15&&projected.y<innerHeight*.85;
  out.fullWingspan=projected.r*__3dAudit().models.own.span/7<innerWidth*.9;
  P3.flight.y=alt; P3.flight.pitch=pitch; __3dStep(1,0);
  const key=key=>{dispatchEvent(new KeyboardEvent('keydown',{key,bubbles:true}));dispatchEvent(new KeyboardEvent('keyup',{key,bubbles:true}));};
  key('v'); __3dStep(1,0); out.vFirst=await until(()=>selected('cockpit')&&__3dAudit().camera.cockpit.visible);
  key('v'); __3dStep(1,0); out.vThird=await until(()=>selected('chase')&&__3dAudit().craft.ownShown);
  p3Set('view3d','cockpit'); __3dStep(1,0); out.setting=await until(()=>selected('cockpit')&&__3dAudit().camera.cockpit.visible);
  button('map').click(); __3dHold(false);
  out.returned=await until(()=>!CM.mode3d());
  out.returned=out.returned&&selected('map')&&!root.hidden&&!P3.flight&&fits();
  out.invalid=CM.setPov('invalid')===false&&selected('map');
  if(MOB){ mtab('threads'); out.hiddenOnThreads=root.hidden; mtab('map'); out.mapTab=!root.hidden&&selected('map'); }
  return out;
})()"""


# a phone: the longest flight readout leaves "Controls" on the screen, and an arrow to a planet behind never sits on the
# touch controls (an outside review found both at 390 x 844)
BROWSER_PHONE_ROOM = r"""(async()=>{
  const sleep=ms=>new Promise(r=>setTimeout(r,ms));
  const until=async(f,ms)=>{const end=performance.now()+ms;while(performance.now()<end){if(f())return true;await sleep(40);}return false;};
  const tap=k=>{dispatchEvent(new KeyboardEvent('keydown',{key:k,bubbles:true}));dispatchEvent(new KeyboardEvent('keyup',{key:k,bubbles:true}));};
  const advance=s=>__3dStep(Math.round(s*60),1/60);
  const box=e=>{const r=e.getBoundingClientRect();return {x:r.left,y:r.top,w:r.width,h:r.height};};
  const hit=(a,b)=>a.x<b.x+b.w&&a.x+a.w>b.x&&a.y<b.y+b.h&&a.y+a.h>b.y;
  setView('map'); CM.enter3d({});
  const out={ready:await until(()=>__3dAudit().mode==='fp'&&!!__3dAudit().input.flight,15000)};
  if(!out.ready)return out;
  __3dHold(true); advance(9);
  // the longest readout the flight writes: Controls stays on the screen and the words stay in the strip
  PAGE3D.flightState=()=>({speed:200,alt:4570,climb:0,edge:'y'}); advance(.3);
  const ins=document.getElementById('flight3d-instruments'), stats=ins.querySelector('.flight3d-stats'), ctl=ins.querySelector('details');
  const vw=document.documentElement.clientWidth, c=box(ctl), s=box(stats);
  out.readout=/height limit/.test(stats.textContent);
  out.controlsOnScreen=c.x>=0&&c.x+c.w<=vw&&c.y>=0&&c.w>=44;
  out.wordsInStrip=s.x>=0&&s.x+s.w<=c.x&&stats.scrollWidth<=stats.clientWidth+1;
  // a locked planet behind the ship at several bearings: its arrow never sits on the stick, LOCK ON, BRAKE, BOOST or the view switch
  const p=cmWorld().planets[0]; PAGE3D.place(p.x-400,p.y,0); advance(.05); tap('l'); advance(.05);
  out.locked=__3dAudit().hud.lock.state==='locked';
  const deck=document.getElementById('deck3d');
  out.deck=!!deck&&deck.style.display==='block';
  let seen=0, clash=[];
  for(const h of [Math.PI,Math.PI-.5,Math.PI+.5,Math.PI-1,Math.PI+1,Math.PI-1.4,Math.PI+1.4]){
    PAGE3D.place(p.x-400,p.y,h); advance(.1);
    const controls=Array.from(deck.children).filter(e=>!e.classList.contains('deck3d-stick-zone')).concat([document.getElementById('pov-switch')])
      .map(box).filter(r=>r.w>0&&r.h>0);
    (__3dAudit().hud.arrows||[]).filter(a=>a.rect).forEach(a=>{ seen++;
      const r={x:a.rect[0],y:a.rect[1],w:a.rect[2],h:a.rect[3]};
      if(controls.some(q=>hit(r,q))) clash.push([+(h.toFixed(2)),a.name,a.rect]); });
  }
  out.arrowsSeen=seen>=5; out.arrowsClear=clash.length===0; if(clash.length)out.clash=clash;
  __3dHold(false);
  return out;
})()"""


BROWSER_LONG_WORDS = r"""(async()=>{
  const sleep=ms=>new Promise(r=>setTimeout(r,ms));
  const until=async(f,ms)=>{const end=performance.now()+ms;while(performance.now()<end){if(f())return true;await sleep(40);}return false;};
  const tap=k=>{dispatchEvent(new KeyboardEvent('keydown',{key:k,bubbles:true}));dispatchEvent(new KeyboardEvent('keyup',{key:k,bubbles:true}));};
  const advance=s=>__3dStep(Math.round(s*60),1/60);
  const box=e=>{const r=e.getBoundingClientRect();return {x:r.left,y:r.top,w:r.width,h:r.height};};
  const hit=(a,b)=>a.x<b.x+b.w&&a.x+a.w>b.x&&a.y<b.y+b.h&&a.y+a.h>b.y;
  setView('map'); CM.enter3d({});
  const out={ready:await until(()=>__3dAudit().mode==='fp'&&!!__3dAudit().input.flight,15000)};
  if(!out.ready)return out;
  __3dHold(true); advance(3);
  // one folder with a long name waits on twelve finished tasks: the gauge says it whole, on the screen, clear of the view switch
  const ps=cmWorld().planets, one=ps[0];
  ps.forEach(p=>{p.health='ok';p.counts=Object.assign({},p.counts,{need_you:0,problems:0,review:0,answer:0,decisions:0});});
  one.label='customer-documentation'; one.health='waiting'; one.counts=Object.assign({},one.counts,{need_you:12,review:12});
  advance(.3);
  const cap=document.querySelector('.gauge3d .gcap'), pov=document.getElementById('pov-switch'), vw=document.documentElement.clientWidth;
  const c=box(cap), v=box(pov);
  out.sentence=cap.textContent==='customer-documentation needs you: 12 finished tasks to review';
  out.capOnScreen=c.x>=0&&c.x+c.w<=vw;
  out.capClearOfSwitch=!(v.w>0&&hit(c,v));
  out.capWhole=cap.scrollWidth<=cap.clientWidth+1&&parseFloat(getComputedStyle(cap).fontSize)>=12;
  // the keys list open: a selected planet's label, wherever the planet sits, never hides under it
  const det=document.querySelector('#flight3d-instruments details'), list=document.querySelector('.flight3d-controls');
  det.open=true; advance(.1);
  out.keysOpen=det.open&&box(list).w>0;
  const p=ps[1]; let seen=0, left=0, clash=[];
  PAGE3D.place(p.x-400,p.y,0); advance(.05); tap('l'); det.open=true; advance(.1);
  for(const h of [0,-.1,.1,-.18,.18,-.26,.26]){
    PAGE3D.place(p.x-400,p.y,h); det.open=true; advance(.3);
    const t=document.querySelector('.target3d');
    if(t&&t.style.display!=='none'&&box(t).w>0){ seen++; if(t.classList.contains('left')) left++; if(hit(box(t),box(list))) clash.push(h); }
  }
  out.targetsSeen=seen>=3&&left>=1; out.targetClearOfKeys=clash.length===0; if(clash.length||!(seen>=3&&left>=1))out.seen=[seen,left,clash];
  __3dHold(false);
  return out;
})()"""


BROWSER_LABEL_ROOM = r"""(async()=>{
  const sleep=ms=>new Promise(r=>setTimeout(r,ms));
  const until=async(f,ms)=>{const end=performance.now()+ms;while(performance.now()<end){if(f())return true;await sleep(40);}return false;};
  const tap=k=>{dispatchEvent(new KeyboardEvent('keydown',{key:k,bubbles:true}));dispatchEvent(new KeyboardEvent('keyup',{key:k,bubbles:true}));};
  const advance=s=>__3dStep(Math.round(s*60),1/60);
  const hit=(a,b)=>a.x<b.x+b.w&&a.x+a.w>b.x&&a.y<b.y+b.h&&a.y+a.h>b.y;
  const union=e=>{let x0=1e9,y0=1e9,x1=-1e9,y1=-1e9;[e].concat(Array.from(e.children)).forEach(q=>{if(q.style.display==='none')return;const r=q.getBoundingClientRect();if(r.width<=0||r.height<=0)return;x0=Math.min(x0,r.left);y0=Math.min(y0,r.top);x1=Math.max(x1,r.right);y1=Math.max(y1,r.bottom);});return x1>x0?{x:x0,y:y0,w:x1-x0,h:y1-y0}:null;};
  setView('map'); CM.enter3d({});
  const out={ready:await until(()=>__3dAudit().mode==='fp'&&!!__3dAudit().input.flight,15000)};
  if(!out.ready)return out;
  __3dHold(true); advance(3);
  // a planet that fills the view (flying close to a big one, or inside it): its label is clamped to a corner and must still
  // keep off every HUD slot that shows (gauge with its caption, agents, boost, need, counter, radar, comms)
  const ps=cmWorld().planets, p=ps[0], r0=p.r, clash=[];
  let seen=0, wide=0;
  for(const [grow,k,dh] of [[1,1.3,0],[4,1.0,0],[4,1.2,0],[4,1.2,-.25],[4,.9,.25],[8,1.2,0],[8,1.2,-.25],[8,1.0,.25],[4,2,0],[2,2,0],[4,1.5,.1]]){
    p.r=r0*grow; PAGE3D.place(p.x-p.r*k,p.y+Math.sin(dh)*p.r*k*.5,dh); advance(.3); tap('l'); advance(.4);
    const t=document.querySelector('.target3d'), tr=t&&t.style.display!=='none'?t.getBoundingClientRect():null;
    if(tr&&tr.width>0){
      seen++; const b={x:tr.left,y:tr.top,w:tr.width,h:tr.height};
      if(b.x>=0&&b.y>=0&&b.x+b.w<=innerWidth&&b.y+b.h<=innerHeight)wide++;
      ['gauge3d','agents3d','boost3d','need3d','steps3d','radar3d','comms3d'].forEach(c=>{
        const e=document.querySelector('.'+c); if(!e||e.style.display==='none')return;
        const u=union(e); if(u&&hit(b,u))clash.push([grow,k,dh,c,Math.round(b.x),Math.round(b.y)]);
      });
    }
    tap('l'); advance(.2);
  }
  p.r=r0; __3dHold(false);
  out.targetsSeen=seen>=3; out.onScreen=wide===seen; out.clearOfSlots=clash.length===0; if(clash.length)out.clash=clash.slice(0,6);
  return out;
})()"""


# a sideways phone, a long folder name waiting on twelve finished tasks and the keys list open (sol-loop r1, R7-01):
# the agents line's words never shrink under 12 px, so its reserved box must be its real height
BROWSER_LABEL_ROOM_LONG = BROWSER_LABEL_ROOM.replace(
    "const ps=cmWorld().planets, p=ps[0], r0=p.r, clash=[];",
    "const ps=cmWorld().planets, p=ps[0], r0=p.r, clash=[];\n"
    "  p.label='customer-documentation'; p.health='waiting'; p.counts=Object.assign({},p.counts,{need_you:12,review:12});\n"
    "  const det=document.querySelector('#flight3d-instruments details'); det.open=true; advance(.1);").replace(
    "tap('l'); advance(.4);", "tap('l'); det.open=true; advance(.4);")
assert BROWSER_LABEL_ROOM_LONG.count('customer-documentation') == 1 and BROWSER_LABEL_ROOM_LONG.count('det.open=true') == 2


# a locked folder with a long name behind the ship (sol-loop r2, R7-01): its arrow card says which way to turn, and the
# card stays on the screen, its words whole
BROWSER_BEHIND_CARD = r"""(async()=>{
  const sleep=ms=>new Promise(r=>setTimeout(r,ms));
  const until=async(f,ms)=>{const end=performance.now()+ms;while(performance.now()<end){if(f())return true;await sleep(40);}return false;};
  const tap=k=>{dispatchEvent(new KeyboardEvent('keydown',{key:k,bubbles:true}));dispatchEvent(new KeyboardEvent('keyup',{key:k,bubbles:true}));};
  const advance=s=>__3dStep(Math.round(s*60),1/60);
  setView('map'); CM.enter3d({});
  const out={ready:await until(()=>__3dAudit().mode==='fp'&&!!__3dAudit().input.flight,15000)};
  if(!out.ready)return out;
  __3dHold(true); advance(3);
  const p=cmWorld().planets[0]; p.label='customer-documentation-service';
  PAGE3D.place(p.x-400,p.y,0); advance(.3); tap('l'); advance(.4);
  let seen=0, inside=0, whole=0, turn=0, bad=[];
  for(const dh of [Math.PI+.5,Math.PI-.5,Math.PI+.9,Math.PI-.9]){
    PAGE3D.place(p.x-400,p.y,dh); advance(.4);
    const card=Array.from(document.querySelectorAll('.arrow3d')).filter(a=>a.style.display!=='none').map(a=>a.querySelector('.abox')).find(b=>/CUSTOMER-DOCUMENTATION-SERVICE/.test(b.textContent));
    if(!card)continue;
    seen++; const r=card.getBoundingClientRect();
    if(r.left>=0&&r.right<=innerWidth&&r.top>=0&&r.bottom<=innerHeight)inside++; else bad.push([dh.toFixed(2),Math.round(r.left),Math.round(r.right)]);
    if(Array.from(card.children).every(c=>c.scrollWidth<=c.clientWidth+1))whole++;
    if(/turn (left|right)/i.test(card.textContent))turn++;
  }
  out.cardsSeen=seen>=3; out.onScreen=inside===seen; out.wordsWhole=whole===seen; out.saysTurn=turn===seen; if(bad.length)out.bad=bad;
  return out;
})()"""


BROWSER_NAME_ROOM = r"""(async()=>{
  const sleep=ms=>new Promise(r=>setTimeout(r,ms));
  const until=async(f,ms)=>{const end=performance.now()+ms;while(performance.now()<end){if(f())return true;await sleep(40);}return false;};
  const advance=s=>__3dStep(Math.round(s*60),1/60);
  const hit=(a,b)=>a.x<b.x+b.w&&a.x+a.w>b.x&&a.y<b.y+b.h&&a.y+a.h>b.y;
  setView('map'); CM.enter3d({});
  const out={ready:await until(()=>__3dAudit().mode==='fp'&&!!__3dAudit().input.flight,15000)};
  if(!out.ready)return out;
  __3dHold(true); advance(3);
  // ten agents on one planet with long task names: the names never run together; the one that does not fit keeps its shape
  const p=cmWorld().planets[0];
  const names=['write the readme missing section','fix the phone labels','add complete items to list','rename the config keys','port the build script','update the changelog entry','review the open pull request','clean old temp files','tighten the lint rules','speed up the test run'];
  names.forEach((n,i)=>units.push({uid:'crowd-'+i,kind:'ship',status:'working',alias:n,name:n,docked_at:p.uid,remote:false,gx:p.x/TILE+(i%3-1)*.2,gy:p.y/TILE+Math.floor(i/3)*.1}));
  await sleep(1300); advance(1);
  out.crafted=cmWorld().craft.filter(c=>/^crowd-/.test(c.uid)).length===names.length;
  let shapes=0, named=0, bare=0, clash=[];
  const mobile=innerWidth<600;
  for(const [dx,dz,dh] of mobile?[[5,4,.2],[5,4,-.15],[7,1,-.2]]:[[5,4,.3],[6,1,-.35],[4,-3,.4],[7,5,-.4]]){
    const sx=p.x-p.r*dx, sy=p.y+p.r*dz;
    PAGE3D.place(sx,sy,Math.atan2(p.y-sy,p.x-sx)+dh); advance(1.2);
    const marks=Array.from(document.querySelectorAll('.mark3d')).filter(e=>e.style.display!=='none');
    shapes=Math.max(shapes,marks.length);
    const shown=marks.map(e=>e.querySelector('.name')).filter(n=>n.style.display!=='none'&&n.textContent);
    named+=shown.length; bare+=marks.filter(e=>e.querySelector('.name').style.display==='none'&&e.classList.contains('k-agent')).length;
    const rs=shown.map(n=>{const r=n.getBoundingClientRect();return {x:r.left,y:r.top,w:r.width,h:r.height,t:n.textContent};});
    for(let i=0;i<rs.length;i++)for(let j=i+1;j<rs.length;j++)if(hit(rs[i],rs[j]))clash.push([dx,dz,rs[i].t,rs[j].t]);
  }
  __3dHold(false);
  out.shapesStay=shapes>=(mobile?4:7); out.someNamed=named>=3; out.someBare=bare>=1; out.noNameOverlap=clash.length===0; if(clash.length)out.clash=clash.slice(0,4);
  return out;
})()"""


class BrowserFlight(unittest.TestCase):
    def run_browser(self, expression, mobile=False, size=None):
        chrome = test_game.find_chrome()
        if not (chrome and shutil.which('node')):
            self.skipTest('needs node and Chrome')
        with tempfile.TemporaryDirectory(prefix='cm-flight-check-') as tmp:
            for name in ('Alpha', 'Beta'):
                Path(tmp, 'work', name).mkdir(parents=True)
            render = test_game.render_fixture(tmp)
            self.assertEqual(render.returncode, 0, render.stderr[-2000:])
            runner, expr, profile = Path(tmp, 'run.mjs'), Path(tmp, 'run.js'), Path(tmp, 'profile')
            if size:
                mobile = True
            w, h = size or (390, 844)
            runner.write_text(CDP_RUNNER.replace('width: 1280, height: 720, deviceScaleFactor: 1, mobile: false',
                f'width: {w}, height: {h}, deviceScaleFactor: 1, mobile: true') if mobile else CDP_RUNNER, encoding='utf-8')
            expr.write_text(expression, encoding='utf-8')
            browser = subprocess.Popen([chrome, '--headless=new', '--no-sandbox', '--remote-debugging-port=0',
                f'--user-data-dir={profile}', '--no-first-run', '--no-default-browser-check', '--remote-allow-origins=*',
                '--allow-file-access-from-files', '--enable-unsafe-swiftshader', '--use-angle=swiftshader',
                '--disable-background-networking', '--disable-component-update', '--no-pings', '--hide-scrollbars',
                '--host-resolver-rules=MAP * ~NOTFOUND', '--proxy-server=http://127.0.0.1:9',
                '--proxy-bypass-list=<-loopback>', 'about:blank'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                port_file, deadline = profile / 'DevToolsActivePort', time.time() + 30
                while not port_file.exists() and time.time() < deadline:
                    self.assertIsNone(browser.poll(), 'browser exited before debug port opened')
                    time.sleep(.1)
                port = port_file.read_text().splitlines()[0]
                result = subprocess.run(['node', str(runner), port, Path(tmp, 'page.html').as_uri(), str(expr)],
                                        capture_output=True, text=True, timeout=150)
            finally:
                browser.terminate()
                try:
                    browser.wait(10)
                except subprocess.TimeoutExpired:
                    browser.kill()
                    browser.wait()
        line = next((s for s in result.stdout.splitlines() if s.startswith('RESULT ')), None)
        self.assertIsNotNone(line, (result.stdout + result.stderr)[-3000:])
        actual = json.loads(line[7:])
        self.assertIsNone(actual['exception'], actual)
        self.assertEqual((actual['errors'], actual['blocked']), ([], 0), actual)
        return actual['value']

    def test_real_keys_drive_spatial_flight_and_pause(self):
        actual = self.run_browser(BROWSER_FLIGHT)
        expected = ('ready', 'forward', 'climb', 'cameraFollows', 'levels', 'wDives', 'sClimbs', 'level', 'pitchSettings', 'turn', 'tilt', 'tiltQ', 'hardTurn',
                    'wingsLevel', 'roll', 'rollEnds', 'rollQ', 'rollQEnds', 'boost', 'speedLines', 'brake', 'meter', 'glance', 'edge', 'pause', 'released', 'guide', 'lockKey', 'chase',
                    'left', 'cleaned')
        self.assertEqual(actual, {name: True for name in expected})

    def test_three_views_and_flight_state_on_desktop(self):
        expected = ('hiddenOnToday', 'map', 'assistantClear', 'thirdEntry', 'third', 'preserved', 'first', 'backToThird', 'thirdFramed', 'fullWingspan', 'vFirst', 'vThird', 'setting', 'returned', 'invalid')
        self.assertEqual(self.run_browser(BROWSER_POV), {name: True for name in expected})

    def test_three_views_and_touch_targets_on_phone(self):
        expected = ('hiddenOnToday', 'map', 'touchTargets', 'thirdEntry', 'third', 'counterClear', 'preserved', 'first', 'backToThird', 'thirdFramed', 'fullWingspan', 'vFirst', 'vThird', 'setting', 'returned', 'invalid', 'hiddenOnThreads', 'mapTab')
        self.assertEqual(self.run_browser(BROWSER_POV, mobile=True), {name: True for name in expected})

    def test_a_phone_keeps_controls_and_arrows_clear_of_each_other(self):
        expected = ('ready', 'readout', 'controlsOnScreen', 'wordsInStrip', 'locked', 'deck', 'arrowsSeen', 'arrowsClear')
        self.assertEqual(self.run_browser(BROWSER_PHONE_ROOM, mobile=True), {name: True for name in expected})

    def test_long_words_stay_whole_and_clear_on_desk_and_phone(self):
        expected = ('ready', 'sentence', 'capOnScreen', 'capClearOfSwitch', 'capWhole', 'keysOpen', 'targetsSeen', 'targetClearOfKeys')
        self.assertEqual(self.run_browser(BROWSER_LONG_WORDS), {name: True for name in expected})
        self.assertEqual(self.run_browser(BROWSER_LONG_WORDS, mobile=True), {name: True for name in expected})


    def test_the_target_label_keeps_off_every_hud_slot_on_desk_and_phone(self):
        expected = ('ready', 'targetsSeen', 'onScreen', 'clearOfSlots')
        self.assertEqual(self.run_browser(BROWSER_LABEL_ROOM), {name: True for name in expected})
        self.assertEqual(self.run_browser(BROWSER_LABEL_ROOM, mobile=True), {name: True for name in expected})

    def test_the_target_label_keeps_off_the_agents_line_on_a_sideways_phone(self):
        expected = ('ready', 'targetsSeen', 'onScreen', 'clearOfSlots')
        self.assertEqual(self.run_browser(BROWSER_LABEL_ROOM_LONG, size=(844, 390)), {name: True for name in expected})

    def test_a_long_named_card_behind_the_ship_says_the_turn_and_stays_on_screen(self):
        expected = ('ready', 'cardsSeen', 'onScreen', 'wordsWhole', 'saysTurn')
        for kw in ({}, {'mobile': True}, {'size': (844, 390)}):
            with self.subTest(**kw):
                self.assertEqual(self.run_browser(BROWSER_BEHIND_CARD, **kw), {name: True for name in expected})

    def test_crowded_marker_names_never_overlap_and_keep_their_shapes(self):
        expected = ('ready', 'crafted', 'shapesStay', 'someNamed', 'someBare', 'noNameOverlap')
        self.assertEqual(self.run_browser(BROWSER_NAME_ROOM), {name: True for name in expected})
        phone = self.run_browser(BROWSER_NAME_ROOM, mobile=True)
        self.assertTrue(all(phone.get(name) for name in ('ready', 'crafted', 'shapesStay', 'noNameOverlap')), phone)


if __name__ == '__main__':
    unittest.main()
