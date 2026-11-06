#!/usr/bin/env python3
"""test_3d_craft.py — the 3D craft: the hull kit, the neutral craft at their budgets, the generated rocks, the skin
overlay, formation and travel, liveries, beacons, engine glow, the flight table, and the 2D sprites baked from the hulls.

The browser checks (pixels, the live scene) are probe steps; these run the very same world3d/*.js under
node with a stub registry, and the Python tools beside them. The owner's private skin has its own in
test_skin_private.py. Run: python3 -m unittest test_3d_craft
"""
import colorsys
import json
import os
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

import testenv  # noqa: F401  (first: clears what steers the tool)
import themepacks as tp

ROOT = Path(__file__).resolve().parent
SKINS = ROOT / "skins"
NODE = shutil.which("node")
# the 2D sprite letters the page fills per craft and the ones lit from inside
DYNAMIC = set("ae")


def node(body, extra=()):
    """Run `body` under node with world3d/hullkit.js, rocks.js and craft.js on a stub registry; its stdout as JSON."""
    files = [ROOT / "world3d" / n for n in ("hullkit.js", "rocks.js", "craft.js")] + list(extra)
    neutral = (SKINS / "neutral-3d.json").read_text(encoding="utf-8")
    script = ("globalThis.CM3D={v:1,mods:{},module(n,d){this.mods[n]=d;return d;},get(n){return this.mods[n]||null;}};"
              + "".join(f"require({json.dumps(str(f))});" for f in files)
              + f"const HK=CM3D.hull,C=CM3D.craftKit,N={neutral};" + body)
    run = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=120)
    if run.returncode:
        raise AssertionError(run.stderr[-2000:])
    return json.loads(run.stdout)


@unittest.skipUnless(NODE, "node not installed")
class HullKit(unittest.TestCase):
    """The kit (loft, slab, fin, mirror, bevel strips, decal atlas; nova-lancer's method, MIT) and the neutral craft."""

    def test_the_neutral_craft_meet_their_budgets(self):
        got = node("const R=HK.resolve({},N),o={};for(const k of Object.keys(R.models3d)){const b=HK.build(R.models3d[k],{models:R.models3d});"
                   "o[k]={tris:b.stats.tris,bevels:b.stats.bevels,panels:b.stats.panels,span:b.stats.span,length:b.stats.length,parts:b.stats.parts};}"
                   "o.lay=HK.atlasLayout(R.atlas3d);console.log(JSON.stringify(o))")
        own = got["own"]
        self.assertTrue(450 <= own["tris"] <= 900, own["tris"])
        self.assertGreaterEqual(own["bevels"], 8)
        sizes = [got["lay"]["panels"][p][2] for p in own["panels"]]
        self.assertGreaterEqual(len(sizes), 4)
        self.assertTrue(all(64 <= s <= 128 for s in sizes), sizes)
        self.assertAlmostEqual(own["span"], 24, places=6, msg="the public skiff fills the chase frame")
        self.assertTrue(150 <= got["wingman"]["tris"] <= 300, got["wingman"]["tris"])
        for k in ("raider", "drone", "derelict"):
            self.assertTrue(60 <= got[k]["tris"] <= 150, (k, got[k]["tris"]))
        self.assertTrue(900 <= got["carrier"]["tris"] <= 2500, got["carrier"]["tris"])
        self.assertAlmostEqual(got["flagship"]["length"], 70, places=6, msg="the flagship is the carrier at half size")

    def test_the_kit_operations(self):
        got = node("""
          const slab=HK.build({span:10,mats:{a:{c:'#ffffff'}},parts:[{name:'w',op:'slab',mat:'a',thick:0.2,bevel:0.05,pts:[[0,1],[4,0],[0,-1]]}]});
          const flat=HK.build({span:10,mats:{a:{c:'#ffffff'}},parts:[{name:'w',op:'slab',mat:'a',thick:0.2,pts:[[0,1],[4,0],[0,-1]]}]});
          const mir=HK.build({span:10,mats:{a:{c:'#ffffff'}},parts:[{name:'w',op:'slab',mat:'a',thick:0.2,pts:[[1,1],[4,0],[1,-1]],mirror:true}]});
          // every face of a closed loft points away from its axis
          const loft=HK.build({length:10,mats:{a:{c:'#ffffff'}},parts:[{name:'b',op:'loft',mat:'a',sides:8,sec:[[-5,0.1,0.1,0],[0,1,0.8,0],[5,0.5,0.4,0]]}]});
          let outward=0; loft.tris.forEach(t=>{const n=HK.triNormal(t.p),cx=(t.p[0]+t.p[3]+t.p[6])/3,cy=(t.p[1]+t.p[4]+t.p[7])/3; if(n[0]*cx+n[1]*cy>=-1e-9||Math.abs(n[2])>0.5) outward++;});
          const sides=mir.tris.filter(t=>t.side===-1).map(t=>HK.triNormal(t.p)[0]), right=mir.tris.filter(t=>t.side!==-1).map(t=>HK.triNormal(t.p)[0]);
          console.log(JSON.stringify({slab:slab.stats.tris,bev:slab.stats.bevels,flat:flat.stats.tris,mir:mir.stats.tris,loft:loft.tris.length,outward,
            mirrored:sides.map(v=>Math.round(v*1e6)).sort().join()===right.map(v=>-Math.round(v*1e6)).sort().join()}))""")
        self.assertEqual(got["flat"], 1 + 1 + 6, "a triangle plate: top, bottom, three sides")
        self.assertEqual(got["slab"], got["flat"] + 6, "a hard bevel strip on each top edge")
        self.assertEqual(got["bev"], 3)
        self.assertEqual(got["mir"], 2 * got["flat"])
        self.assertTrue(got["mirrored"], "the mirrored copy is the same plate turned to face the other way")
        self.assertEqual(got["outward"], got["loft"], "every loft face points out")

    def test_positive_z_recipes_adapt_once_to_the_flight_frame(self):
        got = node("const b=HK.build({length:10,forward:'+z',mats:{a:{c:'#ffffff'}},parts:[{name:'dart',op:'loft',mat:'a',sides:4,sec:[[-1,1,1,0],[4,0,0,0]]}],engines:[[0,0,-1,0.2]]});const before=b.stats.bbox;HK.flightBuilt(b);const once=JSON.stringify(b.tris);HK.flightBuilt(b);console.log(JSON.stringify({before,after:b.stats.bbox,engine:b.anchors.engines[0][2],once:once===JSON.stringify(b.tris)}))")
        self.assertEqual(got["after"][0][2], -got["before"][1][2])
        self.assertGreater(got["engine"], 0)
        self.assertTrue(got["once"])

    def test_the_kit_is_a_port_with_its_marker(self):
        text = (ROOT / "world3d" / "hullkit.js").read_text(encoding="utf-8")
        self.assertIn("/* port: nova-lancer (MIT)", text)
        craft = (ROOT / "world3d" / "craft.js").read_text(encoding="utf-8")
        self.assertIn("/* port: yuka (MIT)", craft)
        self.assertIn("/* port: spacekit (MIT)", craft)

    def test_the_own_ship_reads_as_three_colour_masses_with_a_backlit_rim(self):
        # (video-o5 #1-#6): one big light mass, one saturated mass, one accent; a fresnel rim of power 3
        got = node("const R=HK.resolve({},N);const b=HK.build(R.models3d.own,{models:R.models3d});console.log(JSON.stringify(HK.colourMasses(b)))")
        self.assertGreaterEqual(got["count"], 3, got)
        fams = {f["key"]: f["share"] for f in got["families"]}
        self.assertGreater(fams["light"], 0.25, "the big light mass")
        self.assertEqual(json.loads((SKINS / "neutral-3d.json").read_text(encoding="utf-8"))["look3d"]["rimPow"], 3)
        hk = (ROOT / "world3d" / "hullkit.js").read_text(encoding="utf-8")
        # the rim is worked out on the creased normals (a band along the silhouette), the faces stay flat-lit
        self.assertIn("pow(1.0 - clamp(dot(Ns, V), 0.0, 1.0), uRimPow)", hk)
        self.assertIn("attribute float aEmit; attribute vec3 aNs;", hk)
        self.assertIn("geo.setAttribute('aNs', new THREE.BufferAttribute(HK.creaseNormals(pos, nrm), 3));", hk)
        self.assertIn("float w = clamp(dot(N, L) * 0.5 + 0.5, 0.0, 1.0);", hk, "the toon bands read the flat normal")

    def test_the_rim_normals_are_smooth_over_a_loft_and_sharp_at_a_wing_edge(self):
        # the 3D craft module check: the backlit rim did not read (a flat face is edge-on all over or not at all). HK.creaseNormals
        # averages the faces meeting at a corner that turn less than 66 degrees from it: an 8-sided loft's facets (45
        # degrees apart) share one normal per corner, a plate's top and its side (90 degrees) keep their own
        got = node("""
          const flat=b=>{const n=b.tris.length,pos=new Float32Array(n*9),nrm=new Float32Array(n*9);
            b.tris.forEach((t,k)=>{const nn=HK.triNormal(t.p);for(let v=0;v<3;v++){for(let a=0;a<3;a++){pos[k*9+v*3+a]=t.p[v*3+a];nrm[k*9+v*3+a]=nn[a];}}});
            return {pos,nrm,ns:HK.creaseNormals(pos,nrm)};};
          const loft=flat(HK.build({length:10,mats:{a:{c:'#ffffff'}},parts:[{name:'b',op:'loft',mat:'a',sides:8,sec:[[-5,1,1,0],[5,1,1,0]]}]}));
          const slab=flat(HK.build({span:10,mats:{a:{c:'#ffffff'}},parts:[{name:'w',op:'slab',mat:'a',thick:0.2,pts:[[0,1],[4,0],[0,-1]]}]}));
          const stat=f=>{let same=0,moved=0,unit=0,maxDev=0;const n=f.pos.length/3,by={};
            for(let i=0;i<n;i++){const k=[0,1,2].map(a=>Math.round(f.pos[i*3+a]*1e3)).join();(by[k]=by[k]||[]).push(i);
              const d=f.nrm[i*3]*f.ns[i*3]+f.nrm[i*3+1]*f.ns[i*3+1]+f.nrm[i*3+2]*f.ns[i*3+2];if(d<0.9999)moved++;maxDev=Math.max(maxDev,Math.acos(Math.min(1,d))*180/Math.PI);
              if(Math.abs(Math.hypot(f.ns[i*3],f.ns[i*3+1],f.ns[i*3+2])-1)<1e-5)unit++;}
            return {n,moved,unit,maxDev};};
          const top=[];slab.nrm.forEach((v,i)=>{if(i%3===1&&v>0.99)top.push(slab.ns[i]);});
          console.log(JSON.stringify({loft:stat(loft),slab:stat(slab),topY:Math.min(...top)}))""")
        self.assertEqual(got["loft"]["unit"], got["loft"]["n"], "unit normals")
        # 16 side faces (48 corners) take the shared normal, about half the 45 degrees between facets off their own; the two
        # 8-face caps (90 degrees from the sides) stay flat
        self.assertEqual(got["loft"]["moved"], got["loft"]["n"] / 2, "a loft's side corners take the shared normal, its caps do not")
        self.assertTrue(15 < got["loft"]["maxDev"] < 35, got["loft"]["maxDev"])
        self.assertAlmostEqual(got["topY"], 1, places=6, msg="a plate's top keeps its own normal at its edges")

    def test_emissive_strips_bloom_on_the_hull(self):
        # the 3D craft module check: the emissives lacked punch. hullkit.stripPoints puts glow points on the emissive faces of each
        # part (never on a lamp, a point of light already, or at a nozzle, the engine glow's place); the craft draw a soft
        # halo on each in the one glow buffer (shape 3), in flight and in the hero view
        got = node("""
          const R=HK.resolve({},N),b=HK.build(R.models3d.own,{models:R.models3d}),mats=R.models3d.own.mats;
          const emit=b.tris.filter(t=>{const m=mats[t.m]||{};return m.emit||m.glow>=0.5;});
          const near=p=>Math.min(...emit.map(t=>{const c=[0,1,2].map(a=>(t.p[a]+t.p[3+a]+t.p[6+a])/3);
            return Math.min(Math.hypot(p[0]-c[0],p[1]-c[1],p[2]-c[2]),...[0,1,2].map(v=>Math.hypot(p[0]-t.p[v*3],p[1]-t.p[v*3+1],p[2]-t.p[v*3+2])));}));
          const eng=b.anchors.engines,lamps=R.models3d.own.lights.map(l=>l.map(v=>v*b.stats.k));
          console.log(JSON.stringify({n:b.anchors.strips.length,far:Math.max(...b.anchors.strips.map(near)),
            atEngine:b.anchors.strips.filter(p=>eng.some(e=>Math.hypot(p[0]-e[0],p[1]-e[1],p[2]-e[2])<1)).length,
            atLamp:b.anchors.strips.filter(p=>lamps.some(e=>Math.hypot(p[0]-e[0],p[1]-e[1],p[2]-e[2])<0.6)).length,
            colours:[...new Set(b.anchors.strips.map(p=>p[7]))],none:HK.build({span:4,mats:{a:{c:'#888888'}},parts:[{name:'w',op:'slab',mat:'a',thick:0.2,pts:[[0,1],[4,0],[0,-1]]}]}).anchors.strips.length}))""")
        self.assertTrue(8 <= got["n"] <= 24, got["n"])
        self.assertLess(got["far"], 1.0, "every point sits on an emissive face (a strip, not the air beside it)")
        self.assertEqual(got["atEngine"], 0)
        self.assertEqual(got["atLamp"], 0)
        self.assertEqual(got["colours"], ["#FFC46B"])
        self.assertEqual(got["none"], 0, "a hull without emissive faces has none")
        craft = (ROOT / "world3d" / "craft.js").read_text(encoding="utf-8")
        self.assertIn("else if (S.own.mesh && S.own.mesh.visible) stripBudget -= stripGlows(S.glow, S.own.built, o.matrixWorld, 1, THREE, stripBudget);", craft)
        self.assertIn("stripGlows(gl, b, new THREE.Matrix4().makeTranslation(-ctr.x, -ctr.y, -ctr.z), 1, THREE, 32);", craft)

    def test_problems_carry_one_to_four_tells_coloured_by_kind(self):
        got = node("console.log(JSON.stringify({t:[0,0.3,0.7,1,5,40,'aging','raider',undefined].map(u=>C.tellCount({urgency:u})),"
                   "k:['cron','task','big file','wreck','t3'].map(k=>C.foeClass({kind:k}))}))")
        self.assertTrue(all(1 <= t <= 4 for t in got["t"]), got["t"])
        self.assertEqual(got["t"][0], 1)
        self.assertEqual(got["t"][3], 4, "the most urgent: four tells")
        self.assertEqual([k["model"] for k in got["k"]], ["drone", "raider", "rock", "derelict", "raider"])
        tells = json.loads((SKINS / "neutral-3d.json").read_text(encoding="utf-8"))["look3d"]["tells"]
        self.assertEqual(tells, {"job_late": "#FFB547", "task_failed": "#FF5A5F", "big_file": "#E8EEF6"})

    def test_a_problem_contact_is_classed_by_its_source(self):
        # cmWorld() gives every problem contact kind 'contact' and its producer in source (render-game.py cmWorldParts):
        # a late or failed cron is a drone with amber tells, a failed task, thread or night run a raider with red ones,
        # a lost agent (the map's own wreck) a derelict
        got = node("console.log(JSON.stringify([{kind:'contact',source:'cron'},{kind:'contact',source:'task'},{kind:'contact',source:'t3'},"
                   "{kind:'contact',source:'night'},{kind:'cron',source:'cron'},{kind:'wreck',source:'wreck'},{kind:'contact',source:'big file'},"
                   "{kind:'contact',source:''}].map(f=>{const c=C.foeClass(f);return c.model+'/'+c.tell;})))")
        self.assertEqual(got, ["drone/job_late", "raider/task_failed", "raider/task_failed", "raider/task_failed",
                               "drone/job_late", "derelict/task_failed", "rock/big_file", "raider/task_failed"])
        craft = (ROOT / "world3d" / "craft.js").read_text(encoding="utf-8")
        self.assertIn("eu.set(0, yawOf(hdg), -C.FOE_BANK, 'YXZ')", craft, "problem craft bank into their turn (never edge-on)")


@unittest.skipUnless(NODE, "node not installed")
class Rocks(unittest.TestCase):
    """world3d/rocks.js: generated rocks (grey, orange, crater) with levels of 476/160/60 faces."""
    PATTERN = re.compile(r"face\.json|face[_-]?rock|[\"']face[\"'][ \t]*:|(^|[^A-Za-z0-9_])face[ \t]*:", re.I | re.M)

    def test_generated_rocks_no_face_rock(self):
        import vendor3d
        text = (ROOT / "world3d" / "rocks.js").read_text(encoding="utf-8")
        bundle = vendor3d.bundle(str(ROOT))
        self.assertIn("/* world3d/rocks.js */\n// world3d/rocks.js — three generated low-poly rocks", bundle, "the neutral bundle holds the rocks")
        self.assertEqual(vendor3d.ORDER[vendor3d.ORDER.index("hullkit") + 1], "rocks")
        self.assertEqual(self.PATTERN.findall(text), [])
        self.assertEqual([m.group(0) for m in self.PATTERN.finditer(bundle.split("/* the neutral skin's 3D data */")[0])], [])
        self.assertIn("Original container-map geometry", text)
        got = node("const R=CM3D.rocks,o={};for(const k of Object.keys(R.models)){const m=R.models[k];"
                   "o[k]={v:m.vertices.length,lods:m.lods.map(l=>l.faces.length),pal:m.palette_rgb.length,idx:m.lods.map(l=>l.face_palette_index.length),"
                   "maxi:Math.max(...m.lods.map(l=>Math.max(...l.faces.flat())))};}"
                   "o.keys=Object.keys(R.models).sort();o.same=JSON.stringify(R.models.grey.vertices)===JSON.stringify(R.models.orange.vertices);"
                   "o.craterDiffers=JSON.stringify(R.models.crater.vertices)!==JSON.stringify(R.models.orange.vertices);o.lod=[60,56,30,24,10].map(C.rockLod);"
                   "console.log(JSON.stringify(o))")
        self.assertEqual(got["keys"], ["crater", "grey", "orange"])
        for k in got["keys"]:
            self.assertEqual(got[k]["v"], 240, k)
            self.assertEqual(got[k]["lods"], [476, 160, 60], k)
            self.assertEqual(got[k]["idx"], [476, 160, 60], k)
            self.assertEqual(got[k]["pal"], 6, k)
            self.assertLess(got[k]["maxi"], 240, "every level indexes the one vertex list")
        self.assertTrue(got["same"], "grey shares orange's geometry")
        self.assertTrue(got["craterDiffers"])
        self.assertEqual(got["lod"], [0, 0, 1, 1, 2], "476 / 160 / 60 at >= 56 / >= 24 px / smaller")

    def test_the_levels_are_closed_surfaces(self):
        got = node("const o={};for(const k of ['orange','crater']){o[k]=CM3D.rocks.models[k].lods.map(l=>{const e={};"
                   "l.faces.forEach(f=>{for(let i=0;i<3;i++){const a=f[i],b=f[(i+1)%3],key=Math.min(a,b)+','+Math.max(a,b);e[key]=(e[key]||0)+1;}});"
                   "return Object.values(e).every(n=>n===2);});}console.log(JSON.stringify(o))")
        self.assertEqual(got, {"orange": [True, True, True], "crater": [True, True, True]})


@unittest.skipUnless(NODE, "node not installed")
class Overlay(unittest.TestCase):
    def test_a_skin_without_a_model_falls_through_to_the_neutral_one(self):
        # Missing skin fields fall through to neutral defaults.
        got = node("const sk={name:'x-3d',models3d:{own:N.models3d.raider},feel3d:{turn:40}};const R=HK.resolve(sk,N);"
                   "console.log(JSON.stringify({drone:JSON.stringify(R.models3d.drone)===JSON.stringify(N.models3d.drone),own:R.models3d.own===sk.models3d.own,"
                   "ov:[R.overlay['models3d.drone'],R.overlay['models3d.own']],meter:R.feel3d.meter,turn:R.feel3d.turn,atlas:R.atlas3d===N.atlas3d}))")
        self.assertTrue(got["drone"])
        self.assertTrue(got["own"])
        self.assertEqual(got["ov"], ["neutral", "skin"])
        self.assertEqual(got["meter"], {"burn": 2, "refill": 4.5, "lockout": False})
        self.assertEqual(got["turn"], 40)
        self.assertTrue(got["atlas"])

    def test_a_recipe_can_be_another_at_a_lighter_level(self):
        got = node("const m={a:N.models3d.own,b:{from:'a',lod:1}};console.log(JSON.stringify([HK.build(m.a,{models:m}).stats.tris,HK.build(m.b,{models:m}).stats.tris]))")
        self.assertLess(got[1], got[0])


@unittest.skipUnless(NODE, "node not installed")
class Flight(unittest.TestCase):
    """Formation, travel, liveries, beacons, glow: the craft module's pure helpers (yuka's steering, MIT)."""

    def test_the_formation_is_a_v_behind_the_leader(self):
        got = node("console.log(JSON.stringify([0,1,2].map(k=>C.formationSlot(k,14))))")
        self.assertGreater(got[0][0], 0)
        self.assertLess(got[1][0], 0)
        self.assertTrue(all(s[2] > 0 for s in got), "behind (z back)")
        self.assertGreater(got[2][2], got[0][2], "the second row further back")

    def test_a_chase_camera_sees_the_wingman_no_nearer_than_the_leader_and_inside_the_frame(self):
        # the slot sits a little behind the leader: from a chase camera view.back u behind, the wingman is about as far
        # as the leader (never bigger on screen); every slot stays on the leader's plane (no drop-line of its own, |alt| <= 2); a narrow (phone, tablet) frame moves the slot ahead along the plane, beside the leader
        # and clear of its wings, instead of folding it up over the leader (the 3D craft module check: "a paper airplane on a line")
        got = node("const v=a=>({aspect:a,fov:55,back:50});console.log(JSON.stringify([C.formationSlot(0,14,v(16/9)),C.formationSlot(1,14,v(16/9)),"
                   "C.formationSlot(0,14,v(390/844)),C.formationSlot(2,14,v(16/9)),C.formationSlot(1,14,v(390/844)),C.formationSlot(2,14,v(390/844)),"
                   "C.formationSlot(0,14,v(768/1024)),C.formationSlot(0,14),C.formationSlot(2,14)]))")
        phone, tablet = 390 / 844, 768 / 1024
        for slot, aspect in ((got[0], 16 / 9), (got[2], phone), (got[3], 16 / 9), (got[4], phone), (got[5], phone), (got[6], tablet)):
            depth = 50 - slot[2]
            self.assertGreaterEqual((slot[0] ** 2 + (9 - slot[1]) ** 2 + depth ** 2) ** 0.5, 0.9 * (50 ** 2 + 9 ** 2) ** 0.5,
                                    f"{slot}: no nearer the camera than the leader")
            half = 0.5206 * aspect                                  # tan(27.5 deg) x aspect
            self.assertLessEqual((abs(slot[0]) + 7) / depth, 0.86 * half, f"{slot}: wingtip inside the frame at aspect {aspect:.2f}")
            self.assertGreaterEqual(abs(slot[0]), 0.8 * 14 - 1e-9, f"{slot}: clear of the leader's wings")
        for slot in got:
            self.assertLessEqual(abs(slot[1]), 1.5, f"{slot}: on the leader's plane (no drop-line)")
        self.assertLess(got[2][2], 0, "the phone's slot flies ahead of the leader, where the frame is wide enough")
        self.assertLess(got[2][2], got[6][2], "a tablet needs less of it than a phone")
        self.assertEqual(got[0], got[7], "a wide screen keeps the V behind the leader as written")
        self.assertEqual(got[3][2], got[8][2])
        self.assertEqual(got[1][0], -got[0][0])
        self.assertEqual(got[4][0], -got[2][0])

    def test_a_wingman_on_a_flat_view_tilts_its_top_toward_the_camera(self):
        # the 3D craft module check (phone): the wingman on the plane, seen from a chase camera 9 u up, read as a line (Gemini: "no
        # wingmen"). C.showTilt turns its top toward the camera until it is seen from 23 degrees above; a camera already
        # looking down that steeply leaves it level. Rotation = axis x angle in the hull's frame (x right, y up, nose -z)
        got = node("console.log(JSON.stringify([C.showTilt([-22,9,83]),C.showTilt([22,9,83]),C.showTilt([-22,60,50]),C.showTilt([0,0,80]),C.showTilt([0,0,0])]))")
        import math

        def rotate(v, aa):
            ang = math.sqrt(sum(x * x for x in aa))
            if ang < 1e-9:
                return v
            k = [x / ang for x in aa]
            c, s_ = math.cos(ang), math.sin(ang)
            kv = sum(a * b for a, b in zip(k, v))
            cr = [k[1] * v[2] - k[2] * v[1], k[2] * v[0] - k[0] * v[2], k[0] * v[1] - k[1] * v[0]]
            return [v[i] * c + cr[i] * s_ + k[i] * kv * (1 - c) for i in range(3)]
        for aa, cam in ((got[0], (-22, 9, 83)), (got[1], (22, 9, 83)), (got[3], (0, 0, 80))):
            up = rotate([0, 1, 0], aa)
            n = math.sqrt(sum(x * x for x in cam))
            seen = math.degrees(math.asin(sum(a * b / n for a, b in zip(up, cam))))
            self.assertGreaterEqual(seen, 22.9, f"seen from {seen:.1f} degrees above its wings ({cam})")
            self.assertLessEqual(math.sqrt(sum(x * x for x in aa)), 0.5 + 1e-9)
        self.assertEqual(got[2], [0, 0, 0], "a camera looking down steeply leaves it level")
        self.assertEqual(got[4], [0, 0, 0])
        craft = (ROOT / "world3d" / "craft.js").read_text(encoding="utf-8")
        self.assertIn("if (mode === 'form' && camPos) {", craft, "only a wingman holding its V slot tilts")

    def test_an_agent_hops_planet_to_planet_and_arrives_without_overshoot(self):
        # the working step moves planet -> the agent reaches it in <= 3.5 s, overshoot < 0.05 r; every pair of
        # the bench's planets (world3d-demo.json), stepped at 60 Hz
        world = (ROOT / "tests" / "fixtures" / "world3d-demo.json").read_text(encoding="utf-8")
        got = node("const W=" + world + ",out=[];const P=W.planets;for(let i=0;i<P.length;i++)for(let j=0;j<P.length;j++){if(i===j)continue;"
                   "const a=P[i],b=P[j],orA=a.r*1.35+12,orB=b.r*1.35+12;const from=[a.x+orA,0,a.y];"
                   "const ang=Math.atan2(from[2]-b.y,from[0]-b.x),to=[b.x+Math.cos(ang)*orB,0,b.y+Math.sin(ang)*orB];"
                   "const v={pos:from.slice(),vel:[0,0,0],maxSpeed:300,mass:0.08},plan=C.travelPlan(from,to);let t=0,done=null;"
                   "for(let k=0;k<480;k++){t+=1/60;const left=C.travelStep(v,plan,1/60);if(done===null&&left<Math.max(1.5,0.02*b.r))done=t;}"
                   "out.push([done,plan.overshoot||0,b.r,plan.D]);}console.log(JSON.stringify(out))")
        self.assertGreater(len(got), 100)
        for done, over, r, dist in got:
            self.assertIsNotNone(done, f"never arrived over {dist:.0f} u")
            self.assertLessEqual(done, 3.5, f"{dist:.0f} u took {done:.2f} s")
            self.assertLess(over, 0.05 * r, f"overshoot {over:.2f} at r {r}")

    def test_offset_pursuit_settles_on_its_slot(self):
        got = node("const v={pos:[0,0,0],vel:[0,0,0],maxSpeed:420,mass:0.08},L={pos:[100,0,0],vel:[0,0,0]};"
                   "for(let i=0;i<240;i++)C.stepVehicle(v,C.offsetPursuit(v,L,[100,0,20],0.25),1/60);console.log(JSON.stringify(v.pos))")
        self.assertLess(abs(got[0] - 100) + abs(got[2] - 20), 0.5)

    def test_one_livery_per_agent(self):
        # one hull, one livery per agent (colour band, initial decal; a pattern tells apart two of one colour)
        got = node("const a=[{uid:'a',colour:'#6fb7ff',label:'api'},{uid:'b',colour:'#6fb7ff',label:'apps'},{uid:'c',colour:'#4ade80',label:'ops'},"
                   "{uid:'d',colour:'bad',label:''}];const L=C.liveries(a);console.log(JSON.stringify(Object.values(L)))")
        self.assertEqual(len({x["livery"] for x in got}), 4)
        self.assertEqual(got[0]["letter"], "A")
        self.assertEqual(got[3]["colour"], "#6FB7FF", "a colour that is not one falls back")

    def test_a_far_craft_becomes_a_ring_of_at_least_12_px(self):
        # an agent 6,000 u away is a few px: a ring beacon replaces its mesh. The audit's beaconPx is the
        # ring's outer diameter as the glow shader draws it (radius 0.74 of the point plus its 0.13 soft edge) from the
        # one size the draw call uses, and a ring off the screen is not counted
        got = node("console.log(JSON.stringify([C.projPx(11,6000,55,900),C.projPx(11,200,55,900),C.BEACON_PX,C.ringPx(C.BEACON_PX)]))")
        self.assertLess(got[0], 12)
        self.assertGreater(got[1], 12)
        self.assertGreaterEqual(got[3], 12)
        self.assertAlmostEqual(got[3], got[2] * 0.87, places=1)
        craft = (ROOT / "world3d" / "craft.js").read_text(encoding="utf-8")
        self.assertIn("if (px < 12) { ob.visible = false; ring(v.pos, lcol); }", craft)
        self.assertIn("S.glow.add(pos[0], pos[1], pos[2], hex, C.BEACON_PX, 1, true);", craft)
        self.assertIn("if (!pr || pr.onScreen) { beacons++; beaconPx = Math.max(beaconPx, C.ringPx(C.BEACON_PX)); }", craft)
        self.assertIn("float ring = smoothstep(0.13, 0.0, abs(r - 0.74));", craft, "the ring the size is read from")

    def test_tells_never_draw_smaller_than_their_floor(self):
        # the 3D craft module check: problem craft at approach distance are 30-60 px and their tells barely read. Each tell is a solid
        # lamp disc in its colour, fixed in CSS px and never under C.TELL_PX, inside a world-sized glow
        got = node("console.log(JSON.stringify([C.TELL_PX,C.glowPx(3.4*C.FOE_SCALE,900,55,900),C.glowPx(3.4*C.FOE_SCALE,60,55,900),C.FOE_SCALE]))")
        self.assertGreaterEqual(got[0], 16)
        self.assertLess(got[1], got[0], "at 900 u the world size alone is under the floor")
        self.assertGreater(got[2], got[0])
        self.assertGreaterEqual(got[3], 1.6)
        craft = (ROOT / "world3d" / "craft.js").read_text(encoding="utf-8")
        self.assertIn("dpx = Math.max(C.TELL_PX, tpx * 0.8);", craft)
        # its own lamp shape (4): a disc in the colour, a white core and a glint, so a red tell reads over a red-tinted planet
        self.assertIn("S.glow.add(lp.x, lp.y, lp.z, thex, dpx, 4, true);", craft)
        self.assertIn("float disc = smoothstep(0.62, 0.48, r), hot = smoothstep(0.24, 0.0, r);", craft)

    def test_engine_glow_by_state_and_flame_by_work(self):
        # Engine glow: neutral boost 1.6 / brake 0.5 / cooldown 0.7; flame 1.5 working,
        # no idle flame under the viewer brief; a skin's start flash only where it has one
        got = node("const f=N.feel3d;console.log(JSON.stringify([C.glowScale({boost:true},f,0),C.glowScale({brake:true},f,0),"
                   "C.glowScale({meter:0.5},f,0),C.glowScale({meter:1},f,0),C.glowScale({boost:true},f,1),"
                   "C.glowScale({boost:true},{glow:{boost:2,brake:0.3,cooldown:0.5,flash:5}},1),C.flameK('working'),C.flameK('idle')]))")
        self.assertEqual(got[:4], [1.6, 0.5, 0.7, 1])
        self.assertEqual(got[4], 1.6, "neutral: no start flash")
        self.assertEqual(got[5], 5)
        self.assertEqual(got[6:], [1.5, 0], "working engines glow; idle craft have no flame")
        craft = (ROOT / "world3d" / "craft.js").read_text(encoding="utf-8")
        # the audit reads the flames as drawn (the 3D craft module check: idleFlameLen was a constant that passed whatever was drawn)
        self.assertIn("cr.idleFlameLen = +(flameIdle || idleRate).toFixed(3);", craft)
        self.assertIn("burn = Math.max(burn, (e[3] || 0.3) * 6 * fk * flk);", craft)
        self.assertNotIn("0.6 * 1.8", craft)

    def test_problem_positions_do_not_advance_on_the_clock(self):
        got = node("const p={x:12,y:34,r:80,uid:'fixture:problem'};"
                   "console.log(JSON.stringify([C.foePos(p,0,2,0,false),"
                   "C.foePos(p,0,2,120000,false)]))")
        self.assertEqual(got[0], got[1])

    def test_shadows_fade_with_altitude(self):
        # (sm64 blob shadow): alpha falls from solid to a floor as the craft climbs
        got = node("console.log(JSON.stringify([0,100,400,900].map(C.shadowAlpha)))")
        self.assertEqual(got, sorted(got, reverse=True))
        self.assertAlmostEqual(got[-1], 0.12)
        self.assertIn("if (Math.abs(pos[1]) <= 2) return 0;", (ROOT / "world3d" / "craft.js").read_text(encoding="utf-8"),
                      "only a craft off the plane gets its drop-line and shadow")

    def test_the_module_registers_with_its_demo(self):
        got = node("console.log(JSON.stringify({craft:!!CM3D.get('craft'),hull:!!CM3D.get('hullkit'),demo:typeof CM3D.demos.ships.build,"
                   "measure:typeof CM3D.demos.ships.measure,angles:CM3D.demos.ships.angles}))")
        self.assertEqual(got, {"craft": True, "hull": True, "demo": "function", "measure": "function",
                               "angles": ["front34", "side", "top", "rear", "rear34", "hero"]})


@unittest.skipUnless(NODE, "node not installed")
class Looks(unittest.TestCase):
    """The look the owner picks and what reaches the browser."""

    def test_the_look_comes_from_the_page_setting(self):
        got = node("const sk={look3d:{default:'kit'}},st={get:(k,d)=>k==='look3d'?'whole':d};console.log(JSON.stringify(["
                   "C.lookOf({page:{settings:st}},sk),C.lookOf({look3d:'kit',page:{settings:st}},sk),C.lookOf({page:{settings:{get:()=>''}}},sk),"
                   "C.lookOf({page:{settings:{get:()=>{throw new Error('x');}}}},sk),C.lookOf({},{})]))")
        self.assertEqual(got, ["whole", "kit", "kit", "kit", "kit"])
        page = (ROOT / "render-game.py").read_text(encoding="utf-8")
        self.assertIn("get:()=>p3Get('look3d',String(L.default||opts[0][0])), set:v=>p3Set('look3d',v)});", page,
                      "the Settings row stores the pick where the craft read it (cm3d.look3d)")

    def test_the_roles_keep_the_spec_order(self):
        got = node("console.log(JSON.stringify(C.rolesInOrder({city:'c',buoy:'b',drone:'d',flagship:'f',own_near:'o',extra:'x'})))")
        self.assertEqual(list(json.loads(json.dumps(got))), ["own_near", "drone", "buoy", "flagship", "city", "extra"])

    def test_another_skins_bundle_carries_the_neutral_file_under_it(self):
        # the 3D craft module at run time: a key the skin's 3D file lacks resolves from the neutral file in the browser too
        import tempfile
        import vendor3d
        with tempfile.TemporaryDirectory() as tmp:
            skins = Path(tmp)
            (skins / "other-3d.json").write_text(json.dumps({"name": "other-3d", "models3d": {"own": {"span": 14, "parts": []}}}),
                                                 encoding="utf-8")
            text = vendor3d.bundle(str(ROOT), "other", str(skins))
            path = Path(tmp, "b.js")
            path.write_text(text, encoding="utf-8")
            run = subprocess.run(["node", "-e", f"require({json.dumps(str(path))});const R=CM3D.hull.resolve(CM3D.skin3d,CM3D.neutral3d);"
                                  "console.log(JSON.stringify([R.overlay['models3d.flagship'],R.overlay['models3d.own'],!!R.models3d.derelict,CM3D.neutral3d.name]))"],
                                 capture_output=True, text=True, timeout=120)
        self.assertEqual(run.returncode, 0, run.stderr[-1500:])
        self.assertEqual(json.loads(run.stdout), ["neutral", "skin", True, "neutral-3d"])
        self.assertIn("CM3D.neutral3d=CM3D.skin3d;", vendor3d.bundle(str(ROOT), "neutral"))

    def test_lamps_sit_on_the_hull(self):
        # every light anchor (nav lamps, tip lights) within 0.6 u of a hull vertex that is not a lamp itself: the wingman's
        # lamps once floated 5 u ahead of its swept tips (a slab's forward v is -z)
        got = node("const R=HK.resolve({},N),out={};Object.keys(R.models3d).forEach(n=>{const b=HK.build(R.models3d[n],{models:R.models3d}),"
                   "mats=b.recipe.mats||{};const V=[];b.tris.forEach(t=>{const m=mats[t.m]||{};if(m.emit)return;for(let i=0;i<9;i+=3)V.push(t.p.slice(i,i+3));});"
                   "out[n]=(b.anchors.lights||[]).map(a=>Math.min(...V.map(v=>Math.hypot(v[0]-a[0],v[1]-a[1],v[2]-a[2]))));});console.log(JSON.stringify(out))")
        for model, dists in got.items():
            for d in dists:
                with self.subTest(model=model):
                    self.assertLess(d, 0.9, f"{model}: a lamp {d:.2f} u off the hull")

    def test_light_strips_stay_out_of_the_2d_bake(self):
        # a 3D-only part ("bake": false, inside the hull's extent) lights the hull in 3D and leaves the 2D sprite alone
        got = node("const R=HK.resolve({},N),b=HK.build(R.models3d.own,{models:R.models3d});const nb=b.tris.filter(t=>t.nobake);"
                   "const lo=[1e9,1e9,1e9],hi=[-1e9,-1e9,-1e9];b.tris.filter(t=>!t.nobake).forEach(t=>{for(let i=0;i<9;i++){const a=i%3;lo[a]=Math.min(lo[a],t.p[i]);hi[a]=Math.max(hi[a],t.p[i]);}});"
                   "console.log(JSON.stringify({n:nb.length,emit:nb.every(t=>(b.recipe.mats[t.m]||{}).emit),box:b.stats.bbox,lo:lo,hi:hi,masses:HK.colourMasses(b)}))")
        self.assertGreater(got["n"], 0)
        self.assertTrue(got["emit"])
        for a in (0, 2):
            self.assertAlmostEqual(got["box"][0][a], got["lo"][a], 4, "a light strip never widens the hull the bake frames")
            self.assertAlmostEqual(got["box"][1][a], got["hi"][a], 4)
        emit = {f["key"]: f["share"] for f in got["masses"]["families"]}.get("emit", 0)
        self.assertGreaterEqual(emit, 0.02, "the amber light strips and drive are an emissive mass")


class Skin3D(unittest.TestCase):
    """skins/neutral-3d.json and the validator."""

    @classmethod
    def setUpClass(cls):
        cls.neutral = json.loads((SKINS / "neutral-3d.json").read_text(encoding="utf-8"))

    def test_the_neutral_file_validates_and_is_neutral(self):
        self.assertEqual(tp.validate_skin3d(self.neutral, "neutral-3d"), [])
        self.assertNotIn("PRIVATE", self.neutral)
        self.assertNotIn("meshes3d", self.neutral)
        self.assertEqual(self.neutral["feel3d"]["meter"], {"burn": 2, "refill": 4.5, "lockout": False}, "the 2D meter's numbers")
        self.assertIn("neutral-3d.json", (ROOT / "public-export.sh").read_text(encoding="utf-8").split("SKINS_SHIP=(")[1].split(")")[0])

    def test_the_validator_refuses_a_broken_meter_and_private_content(self):
        import copy

        def errs(change):
            sk = copy.deepcopy(self.neutral)
            change(sk)
            return tp.validate_skin3d(sk, "neutral-3d")
        self.assertTrue(errs(lambda s: s["feel3d"]["meter"].pop("refill")), "a meter without refill")
        self.assertTrue(errs(lambda s: s["feel3d"]["meter"].__setitem__("burn", 0)), "burn 0")
        self.assertTrue(errs(lambda s: s["feel3d"]["meter"].__setitem__("burn", -1)), "burn below 0")
        self.assertTrue(errs(lambda s: s.__setitem__("PRIVATE", "x")))
        self.assertTrue(errs(lambda s: s["models3d"]["own"]["parts"][0].__setitem__("op", "sphere")), "an op the kit lacks")
        self.assertTrue(errs(lambda s: s["models3d"].pop("drone")), "the neutral file is complete")



@unittest.skipUnless(NODE, "node not installed")
class SpriteBake(unittest.TestCase):
    """tools/bake_sprites.py: the 2D craft baked from the hulls at the o5 bar."""
    GRIDS = {"pilot": (48, None), "carrier": (40, 64), "raider": (32, 28), "drone": (24, 24)}

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ROOT / "tools"))
        sys.dont_write_bytecode = True                   # no tools/__pycache__ beside a shipped tool
        import bake_sprites
        cls.bake = bake_sprites
        src = (ROOT / "render-game.py").read_text(encoding="utf-8")
        ink = dict(re.findall(r"(\w):'(#[0-9a-fA-F]{6})'", re.search(r"const CRAFT_INK=\{(.*?)\};", src).group(1)))
        # the bake's shade letters (w v i l): the page fills them from SHADE_INK in every theme
        ink |= dict(re.findall(r"(\w):'(#[0-9a-fA-F]{6})'", re.search(r"const SHADE_INK=\{(.*?)\};", src).group(1)))
        hull = re.search(r"const pal=\{h:'(#\w+)',m:'(#\w+)',d:'(#\w+)',a:accent,c:'(#\w+)',e:engine\|\|'',r:engine\|\|'(#\w+)',p:'(#\w+)',P:'(#\w+)'\}", src)
        cls.base = dict(zip("hmdcrpP", hull.groups())) | ink
        cls.foe = dict(re.findall(r"(\w):'(#[0-9a-fA-F]{6})'", re.search(r"const FOE_HULL=\{(.*?)\};", src).group(1)))

    def palette(self, skin, name):
        pal = dict(self.base)
        pal.update(self.foe if name in ("raider", "drone") else skin.get("livery", {}))
        return pal

    def groups(self, rows, pal):
        """Letters grouped by hue: within 15 degrees of another member (chained), colours under 0.25 saturation one
        family; each family's cell count and its distinct luma levels."""
        cnt = {}
        for r in rows:
            for ch in r:
                if ch != "." and ch not in DYNAMIC and ch not in tp.GLOW_LETTERS:
                    cnt[ch] = cnt.get(ch, 0) + 1
        info = {}
        for ch in cnt:
            r, g, b = (int(pal[ch][i:i + 2], 16) / 255 for i in (1, 3, 5))
            mx, mn = max(r, g, b), min(r, g, b)
            info[ch] = (colorsys.rgb_to_hsv(r, g, b)[0] * 360, (mx - mn) / mx if mx else 0, round(0.299 * r + 0.587 * g + 0.114 * b, 2))
        parent = {ch: ch for ch in cnt}

        def find(x):
            while parent[x] != x:
                x = parent[x]
            return x
        for a in cnt:
            for b in cnt:
                ha, sa, _ = info[a]
                hb, sb, _ = info[b]
                close = (sa < 0.25 and sb < 0.25) or (sa >= 0.25 and sb >= 0.25 and min(abs(ha - hb), 360 - abs(ha - hb)) <= 15)
                if close:
                    parent[find(a)] = find(b)
        fam = {}
        for ch in cnt:
            f = fam.setdefault(find(ch), {"cells": 0, "lumas": set(), "letters": ""})
            f["cells"] += cnt[ch]
            f["lumas"].add(info[ch][2])
            f["letters"] += ch
        return list(fam.values())

    def check_skin(self, name):
        skin = json.loads((SKINS / f"{name}.json").read_text(encoding="utf-8"))
        for craft, (w, h) in self.GRIDS.items():
            rows = skin["sprites"][craft]
            with self.subTest(skin=name, craft=craft):
                self.assertEqual(len(rows[0]), w)
                if h:
                    self.assertEqual(len(rows), h)
                letters = set("".join(rows)) - {"."}
                self.assertTrue(6 <= len(letters) <= 16, sorted(letters))
                self.assertTrue(letters & tp.GLOW_LETTERS or "a" in letters, "a letter lit from inside (engine, lamp or tell)")
                self.assertFalse(letters & {"o"}, "no outline letter: the page draws the halo")
                for fam in self.groups(rows, self.palette(skin, craft)):
                    if fam["cells"] >= 20:
                        self.assertGreaterEqual(len(fam["lumas"]), 3, f"{fam['letters']}: {fam['cells']} cells in {len(fam['lumas'])} shades")
        self.assertTrue(set("".join(skin["sprites"]["pilot"])) & tp.GLOW_LETTERS)

    @staticmethod
    def baked_skins():
        """Every 2D skin in skins/ whose craft are baked from a 3D file beside it (<name>-3d.json): the shipped neutral one
        always, and any other skin the tree carries."""
        return sorted(f.stem for f in SKINS.glob("*.json")
                      if not f.stem.endswith("-3d") and (SKINS / f"{f.stem}-3d.json").exists())

    def test_baked_sprite_quality(self):
        skins = self.baked_skins()
        self.assertIn("neutral", skins)
        for name in skins:
            self.check_skin(name)

    def test_the_bake_is_current(self):
        run = subprocess.run([sys.executable, "-I", str(ROOT / "tools" / "bake_sprites.py"), "--skin", "neutral", "--check"],
                             capture_output=True, text=True, timeout=120)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_the_sprite_is_the_hull_seen_from_above(self):
        # an independent top-down mask of the 3D hull (5 x 5 samples a cell, half covered) against the baked grid: IoU >= 0.85,
        # per baked skin
        for name in self.baked_skins():
            self.check_iou(name)

    def check_iou(self, name):
        hulls = self.bake.hulls(name)
        skin = json.loads((SKINS / f"{name}.json").read_text(encoding="utf-8"))
        for model, craft in (("own", "pilot"), ("carrier", "carrier"), ("raider", "raider"), ("drone", "drone")):
            m = hulls[model]
            rows = skin["sprites"][craft]
            w, h = len(rows[0]), len(rows)
            (lo, hi) = m["bbox"]
            span, length = hi[0] - lo[0], hi[2] - lo[2]
            cell = max(span / w, length / h) if m["sprite"].get("h") else span / w
            x0 = (lo[0] + hi[0]) / 2 - w * cell / 2
            z0 = (lo[2] + hi[2]) / 2 - h * cell / 2
            fine = self.bake.raster(m["tris"], w, h, cell, x0, z0, sub=5)
            a = {(r, c) for r in range(h) for c in range(w) if fine[r][c] is not None}
            b = {(r, c) for r in range(h) for c in range(w) if rows[r][c] != "."}
            with self.subTest(skin=name, craft=craft):
                self.assertGreaterEqual(len(a & b) / len(a | b), 0.85)

    def test_the_rocks_file_is_reproducible_without_external_assets(self):
        run = subprocess.run([sys.executable, "-I", str(ROOT / "tools" / "bake_sprites.py"), "--rocks", "--check"],
                             capture_output=True, text=True, timeout=120)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
