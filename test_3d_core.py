#!/usr/bin/env python3
"""test_3d_core.py — the 3D view's core: the render-scale governor's tested path, the audit namespaces, faults
and quarantine, the two scripts vendor3d.py names (digest names, the skin's data only in its own bundle, the copies
beside a page on disk, the one-file holders), the bridge's /vendor/ address (immutable, JavaScript, its own gzip, on
the phone listener for a paired device only), the merged three.js, and the page side: cmWorld()'s moons, foes, palette
and sky, and the 3D hooks in the stub DOM.

The browser half (the view entering, pausing, leaving, the fallback sentence, context loss, the probe hooks) is the
probe set: steps/*.json run with the probe. WarmUpAndApproach runs the parts a checker
found broken in headless Chrome on a page on disk: __3dApproach from the held match frame, a leave under the shader
warm-up, the warm-up's never-ready and failed ends, and the 3D canvas stretched smoothly (no pixel blocks on a phone).

Run: python3 -m unittest -q test_3d_core
"""
import gzip
import http.client
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

import testenv  # noqa: F401  (first: clears what steers the tool)
import bridge
import test_bridge
import test_game
import vendor3d

ROOT = Path(__file__).resolve().parent
CORE = ROOT / "world3d" / "core.js"
# the three@0.180.0 npm package the merge is checked against: wherever this machine keeps one (CM3D_THREE_PKG), else skipped
THREE_PKG = Path(os.environ["CM3D_THREE_PKG"]) if os.environ.get("CM3D_THREE_PKG") else None


def node(script, *args, timeout=120):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp, "run.js")
        path.write_text(script, encoding="utf-8")
        run = subprocess.run(["node", str(path), *args], capture_output=True, text=True, timeout=timeout)
    return run


@unittest.skipUnless(shutil.which("node"), "node not installed")
class GovernorAndAudit(unittest.TestCase):
    """world3d/core.js in node with no DOM and no three.js: the parts that are pure."""

    def run_core(self, body):
        run = node(f"require({json.dumps(str(CORE))}); const C = globalThis.CM3D;\n{body}")
        self.assertEqual(run.returncode, 0, run.stderr[-3000:])
        return json.loads(run.stdout.strip().splitlines()[-1])

    # 200 frames of 0.025 s, one of 0.150 s, then 1,600 of 0.010 s; the clock is the sum of every dt
    PATH_SCRIPT = r"""
function path(floor, slow) {
  const g = C.govNew({ floor: floor }); let t = 0, last = g.ratio; const out = [];
  const feed = (dt) => { t += dt; const r = C.govStep(g, dt); if (r !== last) { out.push([t, r]); last = r; } };
  for (let i = 0; i < 200; i++) feed(0.025);
  if (slow) feed(0.150);
  for (let i = 0; i < 1600; i++) feed(0.010);
  return out;
}
console.log(JSON.stringify({ desk: path(0.5, true), phone: path(0.6, true), deskNoSlow: path(0.5, false), phoneNoSlow: path(0.6, false) }));
"""

    # the entry's launch out of the sun's near field (core.js launchPick): the bearings whose point lies outside the world's
    # bounds (the flight clamps the ship to them) are skipped on a sparse map; none inside and the ship stays
    LAUNCH_SCRIPT = r"""
const L = C._core.launchPick, w = { star: { x: 0, y: 0, r: 100 }, planets: [{ uid: 'a', x: 900, y: 0, r: 60 }], craft: [{ role: 'carrier', x: 150, y: 0 }] };
const sp = { x: 120, y: 0 }, out = 100 * 4 * 1.05 + 60;
const free = L(w, sp, null), wide = L(w, sp, { x0: -2000, y0: -2000, x1: 2000, y1: 2000 });
const flat = L(w, sp, { x0: -2000, y0: -100, x1: 2000, y1: 100 }), none = L(w, sp, { x0: -100, y0: -100, x1: 100, y1: 100 });
const far = L(w, { x: 2000, y: 0 }, null);
console.log(JSON.stringify({ free, wide, flat, none, far, out }));
"""

    def test_launch_skips_bearings_outside_the_world_bounds(self):
        got = self.run_core(self.LAUNCH_SCRIPT)
        out = got["out"]
        # no bounds: the carrier's bearing (0 degrees) has the planet in its run, so the next 15 degree step is taken
        self.assertEqual(got["free"]["skipped"], 0, got["free"])
        self.assertAlmostEqual(math.hypot(got["free"]["x"], got["free"]["y"]), out, delta=0.01)
        self.assertAlmostEqual(got["free"]["a"], math.pi / 12, delta=1e-9)
        self.assertEqual((got["wide"]["x"], got["wide"]["y"], got["wide"]["skipped"]), (got["free"]["x"], got["free"]["y"], 0), got["wide"])
        # a flat map (|y| <= 100): every bearing but 0 and 180 degrees lies outside; 0 has the planet ahead, so 180 is taken
        self.assertEqual(got["flat"]["skipped"], 22, got["flat"])
        self.assertAlmostEqual(abs(got["flat"]["a"]), math.pi, delta=1e-9)
        self.assertLessEqual(abs(got["flat"]["y"]), 100, got["flat"])
        # a box too small for any launch point: no move
        self.assertEqual((got["none"]["none"], got["none"]["skipped"]), (True, 24), got["none"])
        # a ship already outside the near field: no launch
        self.assertIsNone(got["far"])

    def test_governor_path(self):
        got = self.run_core(self.PATH_SCRIPT)
        desk = [(0.80, 0.85), (1.60, 0.7225), (2.40, 0.6141), (3.20, 0.5220), (4.00, 0.50), (9.22, 0.54), (13.23, 0.5832),
                (17.24, 0.6299)]
        phone = [(0.80, 0.85), (1.60, 0.7225), (2.40, 0.6141), (3.20, 0.60), (9.22, 0.648), (13.23, 0.6998), (17.24, 0.7558)]
        for name, want in (("desk", desk), ("phone", phone)):
            with self.subTest(series=name):
                self.assertEqual(len(got[name]), len(want), got[name])
                for (t, r), (wt, wr) in zip(got[name], want):
                    self.assertAlmostEqual(t, wt, delta=0.04)
                    self.assertAlmostEqual(r, wr, delta=0.0005)
        # without the slow frame: the same ratios, every later time 0.15 s earlier (the 0.150 s frame was ignored), on the
        # desktop floor and on the phone's
        for name in ("desk", "phone"):
            with self.subTest(series=name + "NoSlow"):
                self.assertEqual(len(got[name + "NoSlow"]), len(got[name]))
                self.assertEqual([r for _, r in got[name + "NoSlow"]], [r for _, r in got[name]])
                for (t0, _), (t1, _) in zip(got[name + "NoSlow"], got[name]):
                    self.assertAlmostEqual(t1 - t0, 0.15 if t1 > 5.0 else 0.0, delta=0.011)

    def test_a_frame_over_a_tenth_of_a_second_and_no_time_are_ignored(self):
        got = self.run_core("const g=C.govNew({floor:0.5}); C.govStep(g,0.5); C.govStep(g,0); C.govStep(g,-1);"
                            "console.log(JSON.stringify(g));")
        self.assertEqual((got["ratio"], got["ema"], got["slowFor"], got["fastFor"]), (1, None, 0, 0))

    def test_audit_namespaces(self):
        got = self.run_core(r"""
const K = C._core, out = {};
const as = (name, fn) => K.guard(name, 'build', () => { const prev = K.CURRENT; fn(K.auditFor(name)); });
out.spaceOwn = as('space', (a) => { a.scene.planets = 12; a.sky.region = 'code'; a.fx.pool = 3; a.ext.space = 1; });
out.spaceCamera = as('space', (a) => { a.camera.rig = 'stolen'; });
out.spaceTop = as('space', (a) => { a.calls = 1; });
out.cameraOwn = as('camera', (a) => { a.camera.rig = 'cockpit'; a.lock = 'area:ops'; a.hud.gauge = '9 of 13'; });
out.craftScene = as('craft', (a) => { a.scene.foes = 99; });
out.craftOwn = as('craft', (a) => { a.models.pilot = { tris: 600 }; a.craft.own = 1; });
out.unknownExt = as('boomtest', (a) => { a.ext.boom = true; });
out.unknownScene = as('boomtest', (a) => { a.scene.x = 1; });
out.deep = as('hud', (a) => { a.scene.belt = 1; });
out.read = as('hud', (a) => { if (a.scene.planets !== 12) throw new Error('cannot read'); });
out.A = { scene: K.A.scene, sky: K.A.sky, camera: K.A.camera, lock: K.A.lock, calls: K.A.calls, models: K.A.models, ext: K.A.ext };
out.errors = C.audit().errors;
console.log(JSON.stringify(out));
""")
        self.assertTrue(got["spaceOwn"] and got["cameraOwn"] and got["craftOwn"] and got["unknownExt"] and got["read"])
        self.assertFalse(got["spaceCamera"] or got["spaceTop"] or got["craftScene"] or got["unknownScene"] or got["deep"])
        self.assertEqual(got["A"]["scene"], {"planets": 12})
        self.assertEqual(got["A"]["camera"], {"rig": "cockpit"})
        self.assertEqual((got["A"]["lock"], got["A"]["calls"]), ("area:ops", 0))
        by = {e["module"]: e for e in got["errors"]}
        self.assertEqual(sorted(by), ["boomtest", "craft", "hud", "space"])
        self.assertEqual(by["space"]["count"], 2)
        self.assertIn('3D module "space" may not write audit.calls', by["space"]["message"])
        self.assertIn('may not write audit.scene', by["craft"]["message"])

    def test_a_module_that_keeps_throwing_is_counted_once_and_switched_off_after_three(self):
        got = self.run_core(r"""
const K = C._core; let calls = 0, other = 0;
for (let i = 0; i < 6; i++) { K.guard('boom', 'update', () => { calls++; throw new Error('boom test'); }); K.guard('fine', 'update', () => { other++; }); }
console.log(JSON.stringify({ calls: calls, other: other, errors: C.audit().errors }));
""")
        self.assertEqual((got["calls"], got["other"]), (3, 6))
        self.assertEqual(got["errors"], [{"module": "boom", "phase": "update", "message": "boom test", "count": 3, "quarantined": True}])

    def test_the_registry_and_its_public_face(self):
        got = self.run_core(r"""
C.module('aa', { build() {} }); C.provides('planets', 'sun');
let bad = false; try { C.module('Bad Name', {}); } catch (e) { bad = true; }
let badFn = false; try { C.module('bb', { update: 3 }); } catch (e) { badFn = true; }
console.log(JSON.stringify({ mods: C.modules(), p: [C.provided('planets'), C.provided('sun'), C.provided('sky')], bad: bad, badFn: badFn,
  owns: C.owns(), started: C.started(), hooks: ['__3dStep', '__3dPixel', '__3dProject', '__3dSunSide', '__3dPick', '__3dApproach', '__3dHold'].map((k) => typeof globalThis[k]),
  debug: Object.keys(globalThis.__3dDebug).sort(), audit: C.audit().mode, step: globalThis.__3dStep(1, 0.016),
  seam: Object.keys(C.transition).sort(), seamOff: [C.transition.phase(), C.transition.step('dive'), C.transition.done('chase'), C.transition.match()],
  auditKeys: ['ship', 'focus', 'skin', 'look', 'handoff'].filter((k) => k in C.audit()), ship: Object.keys(C.audit().ship).sort(),
  sunSide: globalThis.__3dSunSide('area:x') }));
""")
        self.assertEqual(got["mods"], ["aa"])
        self.assertEqual(got["p"], [True, True, False])
        self.assertTrue(got["bad"] and got["badFn"])
        self.assertEqual((got["owns"], got["started"], got["audit"], got["step"]), (False, False, "off", None))
        self.assertEqual(got["hooks"], ["function"] * 7)
        self.assertEqual(got["debug"], ["clearContacts", "clouds", "cycleComms", "fps", "hideHud", "planets", "reset", "spawnContact"])
        # the transition seam a camera module's timeline plugs into (the browser half: the lane's probe set, trans*.json)
        self.assertEqual(got["seam"], ["done", "match", "phase", "step"])
        self.assertEqual(got["seamOff"], [None, False, False, None])
        # the audit's ship (the 2D truth plus the cosmetic attitude), focus, skin and look are always there
        self.assertEqual(got["auditKeys"], ["ship", "focus", "skin", "look", "handoff"])
        self.assertEqual(got["ship"], sorted(["x", "y", "alt", "hdg", "spd", "bank", "pitch", "rollDeg", "uturn", "boost", "brake"]))
        self.assertIsNone(got["sunSide"])

    def test_a_capped_frame_is_judged_on_its_own_budget_and_a_stall_never_counts(self):
        got = self.run_core(r"""
const a = C.govNew({ floor: 0.5 }); C.govFeed(a, 0.150, 30); C.govFeed(a, 0.150, 'display');
const b = C.govNew({ floor: 0.5 }); C.govFeed(b, 1 / 30, 30);
const c = C.govNew({ floor: 0.5 }); C.govFeed(c, 0.06, 60);
console.log(JSON.stringify({ a: a, b: b.ema, c: c.ema }));
""")
        self.assertEqual((got["a"]["ema"], got["a"]["slowFor"], got["a"]["ratio"]), (None, 0, 1))   # 150 ms at a cap of 30: a stall
        self.assertAlmostEqual(got["b"], 1 / 60, places=6)        # on time at 30 a second
        self.assertAlmostEqual(got["c"], 0.06, places=6)          # 60 ms at a cap of 60: slow, as it came


class Bundles(unittest.TestCase):
    """vendor3d.py: the two scripts and their names."""

    def tree(self, tmp):
        """A copy of what a bundle is built from: world3d/, skins/, vendor/three-r180.js."""
        root = Path(tmp, "tool")
        shutil.copytree(ROOT / "world3d", root / "world3d")
        shutil.copytree(ROOT / "skins", root / "skins")
        (root / "vendor").mkdir()
        shutil.copy(ROOT / "vendor" / "three-r180.js", root / "vendor" / "three-r180.js")
        return root

    def test_names_are_digests_and_move_with_world3d(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.tree(tmp)
            (three, three_text), (name, text) = vendor3d.assets(str(root), "neutral")
            self.assertRegex(three, r"^three-r180-[0-9a-f]{8}\.js$")
            self.assertRegex(name, r"^cm3d-neutral-[0-9a-f]{8}\.js$")
            self.assertEqual(three[len("three-r180-"):-3], vendor3d.digest(three_text))
            core = root / "world3d" / "core.js"
            core.write_text(core.read_text(encoding="utf-8") + "\n// one more line\n", encoding="utf-8")
            (three2, _), (name2, _) = vendor3d.assets(str(root), "neutral")
            self.assertEqual(three2, three)
            self.assertNotEqual(name2, name)
            with self.assertRaises(ValueError):
                vendor3d.bundle(str(root), "../ownerskin")

    def test_a_skins_3d_data_rides_only_in_its_own_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.tree(tmp)
            # the private marker key is written in two pieces: leak-markers.txt refuses the key itself in a shipped file
            #, and this test ships with the export
            key = "PRIV" + "ATE"
            (root / "skins" / "ownerskin-3d.json").write_text(json.dumps({key: "only-in-the-private-bundle",
                                                                       "note": "</script> stays inert"}), encoding="utf-8")
            neutral = vendor3d.bundle(str(root), "neutral")
            private = vendor3d.bundle(str(root), "ownerskin")
            self.assertNotIn("only-in-the-private-bundle", neutral)
            self.assertNotIn("PRIVATE", neutral)
            self.assertIn('"' + key + '":"only-in-the-private-bundle"', private)
            self.assertNotIn("</script", private.lower())
            # the neutral file rides in every bundle: its own as skin3d, under another skin as neutral3d
            mine_neutral = vendor3d.script_json(vendor3d.skin3d("neutral", str(root)))
            self.assertIn(f"CM3D.skin3d=Object.freeze({mine_neutral});", neutral)
            self.assertIn("CM3D.neutral3d=CM3D.skin3d;", neutral)
            self.assertIn(f"CM3D.neutral3d=Object.freeze({mine_neutral});", private)
            (root / "skins" / "neutral-3d.json").unlink()                   # a skin with no 3D file: {}
            self.assertIn("CM3D.skin3d=Object.freeze({});", vendor3d.bundle(str(root), "neutral"))
            # the config's skins_dir is looked in first
            mine = Path(tmp, "mine")
            mine.mkdir()
            (mine / "neutral-3d.json").write_text('{"look3d": {"x": 1}}', encoding="utf-8")
            self.assertIn('"look3d":{"x":1}', vendor3d.bundle(str(root), "neutral", str(mine)))
            (mine / "neutral-3d.json").write_text("[1]", encoding="utf-8")
            with self.assertRaises(ValueError):
                vendor3d.bundle(str(root), "neutral", str(mine))

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_the_bundle_runs_and_carries_the_skin_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.tree(tmp)
            (root / "skins" / "neutral-3d.json").write_text('{"look3d": {"tone": "nightglass"}}', encoding="utf-8")
            path = Path(tmp, "bundle.js")
            path.write_text(vendor3d.bundle(str(root), "neutral"), encoding="utf-8")
            run = node(f"require({json.dumps(str(path))}); console.log(JSON.stringify([CM3D.modules(), CM3D.skin3d, CM3D.core, typeof CM3D.flightPhysics.step]))")
        self.assertEqual(run.returncode, 0, run.stderr)
        mods, skin, core, flight = json.loads(run.stdout)
        # The rock data and pure flight integrator register no scene module.
        self.assertEqual(mods, [n for n in vendor3d.ORDER[1:] if n not in ("rocks", "flight")])
        self.assertEqual(flight, "function")
        self.assertEqual((skin, core), ({"look3d": {"tone": "nightglass"}}, 1))

    def test_write_beside_and_serve(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.tree(tmp)
            out = Path(tmp, "site", "game.html")
            out.parent.mkdir()
            names = vendor3d.write_beside(str(out), "neutral", str(root))
            folder = out.parent / vendor3d.ASSET_DIR
            self.assertEqual(sorted(p.name for p in folder.iterdir() if not p.name.startswith("cm3dp-")), sorted(names))
            stamp = {p.name: p.stat().st_mtime_ns for p in folder.iterdir()}
            vendor3d.write_beside(str(out), "neutral", str(root))          # only the missing ones are written
            self.assertEqual({p.name: p.stat().st_mtime_ns for p in folder.iterdir()}, stamp)
            three, code = names
            self.assertEqual(vendor3d.serve(three, str(out.parent), str(root)), (folder / three).read_text(encoding="utf-8"))
            self.assertEqual(vendor3d.serve(code, str(out.parent), str(root)), (folder / code).read_text(encoding="utf-8"))
            # a file beside the page whose content no longer has its name's digest is never served: the tool's own is
            (folder / code).write_text("alert(1)", encoding="utf-8")
            self.assertEqual(vendor3d.serve(code, str(out.parent), str(root)), vendor3d.bundle(str(root), "neutral"))
            for bad in ("../three-r180-00000000.js", "three-r180-0000000.js", "three-r180-zzzzzzzz.js", "cm3d-Neutral-00000000.js",
                        "cm3d-neutral-00000000.js", "index.html", "", "three-r180-00000000.js?x"):
                with self.subTest(name=bad):
                    self.assertIsNone(vendor3d.serve(bad, str(out.parent), str(root)))

    def test_inline_holders(self):
        text = vendor3d.inline_holders(str(ROOT), "neutral")
        self.assertEqual(re.findall(r"<script\b[^>]*>", text), ['<script type="text/plain" id="cm3d-three">',
                                                                 '<script type="text/plain" id="cm3d-bundle">'])
        self.assertEqual(text.count("</script>"), 2)
        self.assertIn("globalThis.THREE=(function(){", text)


class ThreeMerge(unittest.TestCase):
    """vendor/three-r180.js: one classic script, three's licence kept, nothing that can close a script."""

    def test_the_vendored_file(self):
        text = (ROOT / "vendor" / "three-r180.js").read_text(encoding="ascii")
        self.assertTrue(text.startswith("/**\n * @license\n * Copyright 2010-2025 Three.js Authors\n * SPDX-License-Identifier: MIT\n */"))
        self.assertNotIn("</script", text.lower())
        self.assertNotIn("<!--", text)
        self.assertNotRegex(text, r"(?m)^\s*(import|export)\b")
        self.assertIn("MIT", (ROOT / "vendor" / "LICENSE-three.txt").read_text(encoding="utf-8"))
        notice = (ROOT / "NOTICE").read_text(encoding="utf-8")
        self.assertIn("vendor/three-r180.js", notice)
        self.assertIn("vendor/LICENSE-three.txt", notice)
        self.assertNotIn("No other library, script, stylesheet or font is bundled", notice)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_it_runs_as_one_classic_script(self):
        run = node(f"require({json.dumps(str(ROOT / 'vendor' / 'three-r180.js'))}); const T = globalThis.THREE;"
                   "const g = new T.LineGeometry(); g.setPositions([0,0,0, 1,1,1, 2,0,0]);"
                   "const l = new T.Line2(g, new T.LineMaterial({ color: 0xff0000, linewidth: 2, dashed: true }));"
                   "console.log(JSON.stringify([T.REVISION, Object.isFrozen(T), l.isLine2 === true,"
                   "['Line2','LineSegments2','LineGeometry','LineSegmentsGeometry','LineMaterial','Lensflare','LensflareElement',"
                   "'WebGLRenderer','SRGBColorSpace','InstancedMesh'].every((k) => k in T), Object.keys(T).length]))")
        self.assertEqual(run.returncode, 0, run.stderr[-2000:])
        rev, frozen, line2, every, keys = json.loads(run.stdout)
        self.assertEqual((rev, frozen, line2, every), ("180", True, True, True))
        self.assertGreaterEqual(keys, 438)

    @unittest.skipUnless(THREE_PKG and (THREE_PKG / "package.json").is_file(),
                         "set CM3D_THREE_PKG to a three@0.180.0 package folder to check the merge")
    def test_the_merge_is_reproducible(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp, "three.js")
            run = subprocess.run([sys.executable, str(ROOT / "bin" / "merge_three.py"), str(THREE_PKG), "--out", str(out)],
                                 capture_output=True, text=True, timeout=120)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(out.read_bytes(), (ROOT / "vendor" / "three-r180.js").read_bytes())


class VendorRoute(unittest.TestCase):
    """GET /vendor/<name> on the loopback listener and, for a paired device, on the phone listener."""
    HOST, ORIGIN = "phone.example.com", "https://phone.example.com"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.game = os.path.join(root, "game.html")
        with open(self.game, "w", encoding="utf-8") as handle:
            handle.write("<!doctype html><style>body{color:white}</style><script>\nconst W = {};\nfunction closeAll(){}\n</script>")
        self.names = vendor3d.write_beside(self.game, "neutral")
        self.remote_port = test_bridge.free_port()
        self.app = bridge.BridgeApp(
            seats_path=os.path.join(root, "no-seats.json"), game_path=self.game, renderer_path="unused",
            live_path=os.path.join(root, ".bridge-live"), log_path=os.path.join(root, "bridge.log"),
            tmux_runner=test_bridge.FakeTmux(), world_loader=lambda: {"chars": [], "quests": [], "tasks": {"items": []},
                                                                       "activity": [], "inflight": [], "generated": "now", "oldest": 0},
            hop_runner=test_bridge.FakeHop(), synced_dir=os.path.join(root, "synced"), file_roots=(root,),
            areas_path=os.path.join(root, "areas.json"), verbs_dir=os.path.join(root, "verbs"),
            runs_path=os.path.join(root, "state", "runs.jsonl"), rulings_path=os.path.join(root, "state", "rulings.json"),
            tasks_dir=os.path.join(root, "tasks"), agent_verify=False, tick_seconds=0,
            remote={"listen": "127.0.0.1", "port": self.remote_port, "public_origin": self.ORIGIN},
            remote_clock=test_bridge.FakeClock())
        self.server = bridge.BridgeHTTPServer((bridge.LOOPBACK, 0), bridge.Handler, self.app)
        self.app.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.assertTrue(self.app.start_remote(), self.app.remote.refused or self.app.remote.error)

    def tearDown(self):
        self.app.stop_remote()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tmp.cleanup()

    def get(self, port, path, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("GET", path, headers=headers or {})
        r = conn.getresponse()
        data = r.read()
        conn.close()
        return r.status, {k.lower(): v for k, v in r.getheaders()}, data

    def test_loopback_serves_both_scripts_cached_for_good(self):
        host = {"Host": f"127.0.0.1:{self.server.server_port}"}
        for name in self.names:
            with self.subTest(name=name):
                status, h, data = self.get(self.server.server_port, "/vendor/" + name, host)
                self.assertEqual(status, 200)
                self.assertEqual(h["cache-control"], "public, max-age=31536000, immutable")
                self.assertEqual(h["content-type"], "text/javascript; charset=utf-8")
                self.assertEqual(h["x-content-type-options"], "nosniff")
                self.assertEqual(h["cross-origin-resource-policy"], "same-origin")
                self.assertNotIn("content-security-policy", h)
                self.assertEqual(vendor3d.digest(data.decode("utf-8")), name[-11:-3])
                status, h, packed = self.get(self.server.server_port, "/vendor/" + name, dict(host, **{"Accept-Encoding": "gzip"}))
                self.assertEqual((status, h.get("content-encoding")), (200, "gzip"))
                self.assertEqual(gzip.decompress(packed), data)
                self.assertEqual(int(h["content-length"]), len(packed))
        self.assertIs(self.app.vendor_asset(self.names[0]), self.app.vendor_asset(self.names[0]))     # packed once, kept
        for path in ("/vendor/three-r180-00000000.js", "/vendor/../game.html", "/vendor/", "/vendor/" + self.names[0] + "?x=1"):
            with self.subTest(path=path):
                status, h, _ = self.get(self.server.server_port, path, host)
                self.assertIn(status, (400, 404))
                self.assertEqual(h["cache-control"], "no-store")
        status, _, _ = self.get(self.server.server_port, "/vendor/" + self.names[0], dict(host, Origin="http://evil.example"))
        self.assertEqual(status, 403)
        # another site's <script src> sends no Origin, only Fetch Metadata: refused; the page's own load is fine
        for site, want in (("cross-site", 403), ("same-site", 403), ("same-origin", 200), ("none", 200)):
            with self.subTest(site=site):
                status, _, _ = self.get(self.server.server_port, "/vendor/" + self.names[1],
                                        dict(host, **{"Sec-Fetch-Site": site, "Sec-Fetch-Mode": "no-cors", "Sec-Fetch-Dest": "script"}))
                self.assertEqual(status, want)
        status, _, _ = self.get(self.server.server_port, "/vendor/" + self.names[0], {"Host": "evil.example"})
        self.assertEqual(status, 421)

    def test_the_3d_view_addresses_on_the_page(self):
        """/?scene3d=1&seed=7, ?hold=match, ?demo3d=..., ?perf3d=1, ?probe=1 reach the page; nothing else does."""
        host = {"Host": f"127.0.0.1:{self.server.server_port}"}
        for query in ("scene3d=1&seed=7", "scene3d=1&hold=match", "demo3d=sky&angle=front34&px=96&region=code", "perf3d=1",
                      "probe=1", "scene3d=1&seed=7&perf3d=1&probe=1"):
            with self.subTest(query=query):
                status, h, data = self.get(self.server.server_port, "/?" + query, host)
                self.assertEqual(status, 200)
                self.assertIn(b"const W = ", data)
                self.assertIn("text/html", h["content-type"])
        for query in ("scene3d=1&x=1", "scene3d=1&scene3d=1", "scene3d=%3Cb%3E", "scene3d", "seed=" + "7" * 41, "fresh=1&scene3d=1", "x=1"):
            with self.subTest(query=query):
                status, _, _ = self.get(self.server.server_port, "/?" + query, host)
                self.assertEqual(status, 403)
        self.assertTrue(bridge.view_query("hold=match") and not bridge.view_query("") and not bridge.view_query("hold=a b"))

    def test_the_phone_listener_serves_them_to_a_paired_device_only(self):
        path = "/vendor/" + self.names[0]
        status, _, _ = self.get(self.remote_port, path, {"Host": self.HOST, "Sec-Fetch-Site": "same-origin"})
        self.assertEqual(status, 401)
        # pair a device (the desktop page asks for a code, the phone posts it)
        _, h, _ = self.get(self.server.server_port, "/", {"Host": f"127.0.0.1:{self.server.server_port}"})
        ui = {"Cookie": h["set-cookie"].split(";", 1)[0], "Origin": f"http://127.0.0.1:{self.server.server_port}",
              "Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "cors", "Content-Type": "application/json",
              "Host": f"127.0.0.1:{self.server.server_port}"}
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        conn.request("POST", "/phone/pair", body="{}", headers=ui)
        code = json.loads(conn.getresponse().read())["code"]
        conn.close()
        conn = http.client.HTTPConnection("127.0.0.1", self.remote_port, timeout=10)
        conn.request("POST", "/pair", body=json.dumps({"code": code, "name": "Test phone"}),
                     headers={"Host": self.HOST, "Origin": self.ORIGIN, "Sec-Fetch-Site": "same-origin", "Content-Type": "application/json"})
        r = conn.getresponse()
        r.read()
        cookie = r.getheader("Set-Cookie").split(";", 1)[0]
        conn.close()
        status, h, data = self.get(self.remote_port, path, {"Host": self.HOST, "Cookie": cookie, "Sec-Fetch-Site": "same-origin"})
        self.assertEqual(status, 200)
        self.assertEqual(h["cache-control"], "public, max-age=31536000, immutable")
        self.assertEqual(h["content-type"], "text/javascript; charset=utf-8")
        self.assertEqual(h["cross-origin-resource-policy"], "same-origin")
        self.assertTrue(data.startswith(b"/**\n * @license"))
        status, _, _ = self.get(self.remote_port, path, {"Host": self.HOST, "Cookie": cookie, "Sec-Fetch-Site": "cross-site"})
        self.assertEqual(status, 403)
        status, _, _ = self.get(self.remote_port, "/vendor/three-r180-00000000.js", {"Host": self.HOST, "Cookie": cookie, "Sec-Fetch-Site": "same-origin"})
        self.assertEqual(status, 404)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class PageSide(unittest.TestCase):
    """The page the renderer writes: its 3D names, the files beside it, the one-file form, and the page side in the stub."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name, "work")
        for name in ("Alpha", "Beta"):
            (root / name).mkdir(parents=True)
            (root / name / "notes.md").write_text("fixture\n", encoding="utf-8")
        # a planet with sub-folders of every kind, for the moons' purpose guess
        for sub, files in (("clips", ("a.png", "b.jpg", "c.mp4")), ("stats", ("a.csv", "b.csv", "c.json")),
                           ("captions", ("a.md", "b.md", "c.txt")), ("lib", ("a.py", "b.py", "c.md")), (".cache", ("x.bin",))):
            (root / "Media" / sub).mkdir(parents=True)
            for f in files:
                (root / "Media" / sub / f).write_text("x\n", encoding="utf-8")
        proc = test_game.render_fixture(cls.tmp.name)
        if proc.returncode:
            raise AssertionError(proc.stderr[-2000:])
        cls.page = Path(cls.tmp.name, "page.html")
        cls.html = cls.page.read_text(encoding="utf-8")
        cls.js = Path(cls.tmp.name, "page.js")
        test_game.page_js(cls.page, cls.js)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_the_page_names_the_two_files_written_beside_it(self):
        refs = json.loads(re.search(r"(?m)^const V3D=(.*);$", self.html).group(1))
        self.assertEqual(refs, vendor3d.page_refs(str(ROOT), "neutral"))
        folder = self.page.parent / vendor3d.ASSET_DIR
        self.assertEqual(sorted(p.name for p in folder.iterdir() if not p.name.startswith("cm3dp-")), sorted([refs["three"], refs["bundle"]]))
        self.assertNotIn("__CM3D_INLINE__", self.html)
        self.assertNotIn("__V3D__", self.html)
        self.assertNotIn('id="cm3d-three"', self.html)

    def test_three_inline_makes_one_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp, "work", "Alpha")).mkdir(parents=True)
            proc = test_game.render_fixture(tmp, three_inline=True)
            self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
            html = Path(tmp, "page.html").read_text(encoding="utf-8")
            self.assertFalse(Path(tmp, vendor3d.ASSET_DIR).exists())
            self.assertEqual(re.findall(r"<script\b[^>]*>", html), ['<script id="theme-early">', '<script id="mobile-early">',
                                                                     '<script id="cm-sfx">', '<script type="text/plain" id="cm3d-three">',
                                                                     '<script type="text/plain" id="cm3d-bundle">', "<script>"])
            self.assertIn("__THREE__", html.split('<script type="text/plain" id="cm3d-bundle">')[0])   # three's own text, untouched
            self.assertTrue(json.loads(re.search(r"(?m)^const V3D=(.*);$", html).group(1))["inline"])

    def test_the_page_side_in_the_stub(self):
        test_game.run_page_driver(self, self.js, r"""
  const w = cmWorld(), by = {}; w.moons.forEach((m) => { by[m.label] = m; });
  ok('moons: the sub-folders, dot-folders never', Object.keys(by).sort().join() === 'captions,clips,lib,stats');
  ok('moons: each with its purpose guessed from its files', by.clips.purpose === 'media' && by.stats.purpose === 'data'
    && by.captions.purpose === 'notes' && by.lib.purpose === 'code');
  ok('moons: the planet, bytes and days', w.moons.every((m) => m.parent === 'area:Media' && m.bytes > 0 && m.days !== null && typeof m.uid === 'string'));
  ok('palette: every purpose an [r, g, b]', ['code','notes','reference','data','media','config','shared','archive','unsorted'].every((k) =>
    Array.isArray(w.palette[k]) && w.palette[k].length === 3 && w.palette[k].every((v) => v >= 0 && v <= 255)));
  ok('sky, selected workflow and shows come through CM', JSON.stringify(w.sky) === JSON.stringify(CM.nebulaRecipe()) && w.selectedWorkflow === null && Array.isArray(w.shows)
    && (CM.selectWorkflow('wf-x'), cmWorld().selectedWorkflow === 'wf-x') && (CM.selectWorkflow(null), true));
  ok('the parts are built once per rev', cmWorld().moons === cmWorld().moons);
  ok('purpose guess: majorities, links and folders skipped', cmGuessPurpose([['a.csv',null,'1K',1,0],['b.csv',null,'1K',1,0],['c.md',null,'1K',1,0]]) === 'data'
    && cmGuessPurpose([['d/',3,'1K',1,1],['l → x',null,'',1,0]]) === 'unsorted' && cmGuessPurpose([]) === 'unsorted');
  ok('bytes from the size words', cmBytes('1.5K') === 1536 && cmBytes('2M') === 2097152 && cmBytes('') === 0);
  // the 3D hooks with no 3D files loaded
  ok('3D: off, nothing owns the screen, the audit says so', CM.mode3d() === false && __3dAudit().mode === 'off' && __3dAudit().errors.length === 0);
  ok('3D: the back layer at 30 answers only while 3D is up', CM.backLayers.some((l) => l.pri === 30) && CM.backLayer('esc') === false);
  ok('3D: a forced fallback says the sentence and stays 2D', (() => { CM.forceNoGL = true; CM.enter3d({}); CM.forceNoGL = false;
    return __3dAudit().reason === 'no-webgl2' && P3.sentence.indexOf('WebGL 2') > 0 && CM.mode3d() === false && P3.mode === 'off'; })());
  // the 3D module's three rows by key: the craft and the flight and HUD lanes push rows of their own after them
  const rows9 = () => ['render-scale', 'fps-cap', 'antialias'].map((k) => CM.settingsExtra.find((r) => r.key === k));
  ok('3D: the three settings rows read their defaults', rows9().map((r) => r.get()).join() === 'auto,display,on');
  ok('3D: Retro 3D is a settings row, off by default (the painted look)', (CM.settingsExtra.find((r) => r.key === 'retro-3d') || {}).get() === 'off');
  // a phone draws at DPR 1: without multisampling a 3x screen shows every edge in 3-pixel stairs
  ok('3D: on a phone smooth edges stay on by default, the frame rate is 30', (() => { const was = MOB; MOB = true;
    const v = rows9().map((r) => r.get()).join(); MOB = was; return v === 'auto,30,on'; })());
  ok('3D: no parameters, no 3D at load', Object.keys(P3_PARAMS).length === 0);
  ok('3D: the audit before any entry has the ship, focus, skin and look', ['ship', 'focus', 'skin', 'look'].every((k) => k in __3dAudit())
    && __3dAudit().ship.x === px && __3dAudit().skin === 'neutral');
  // the parts follow every fact they read: a room's age and size, its files, a contact's words (checker repro: stale.json)
  const media = MOONS.find((m) => m.uid === 'area:Media'), room = media.rooms.find((r) => r.name === 'clips');
  const rev0 = cmWorld().rev, days0 = cmWorld().moons.find((m) => m.uid === room.uid).days;
  room.age = 45; room.size = '9M'; cmSceneTouch();
  const moved = cmWorld().moons.find((m) => m.uid === room.uid);
  ok('parts: a room older and bigger moves rev and reaches the moon', cmWorld().rev === rev0 + 1 && moved.days === 45 && moved.bytes === 9437184 && days0 !== 45);
  room.children = (room.children || []).concat([['new.csv', null, '1K', 1, 0], ['more.csv', null, '1K', 1, 0], ['last.csv', null, '1K', 1, 0],
    ['x.csv', null, '1K', 1, 0], ['y.csv', null, '1K', 1, 0], ['z.csv', null, '1K', 1, 0], ['w.csv', null, '1K', 1, 0]]);
  CM_WORLD.at = -1e6;                                             // a second later: cmWorld() signs again by itself
  ok('parts: new files change the purpose without a reply touching', cmWorld().rev === rev0 + 2 && cmWorld().moons.find((m) => m.uid === room.uid).purpose === 'data');
  ok('parts: no change, no new rev', (CM_WORLD.at = -1e6, cmWorld().rev) === rev0 + 2);
  // a moon circling its planet is not data: its room's place moves every frame, rev stays, the moon's x/y follow it
  const lib = media.rooms.find((r) => r.name === 'lib'), revA = cmWorld().rev;
  lib.gx = 3.25; lib.gy = -1.5; CM_WORLD.at = -1e6; const m1 = cmWorld().moons.find((m) => m.uid === lib.uid), at1 = [m1.x, m1.y];
  lib.gx = 4.75; CM_WORLD.at = -1e6; const w2 = cmWorld(), m2 = w2.moons.find((m) => m.uid === lib.uid);
  ok('orbit: a moving moon moves no rev', w2.rev === revA);
  ok('orbit: the moon is where its room is now', at1[0] === 3.25 * TILE && at1[1] === -1.5 * TILE && m2.x === 4.75 * TILE && m2.y === -1.5 * TILE);
  // the machines: at their layout place; a layout that moves one (the roots ring keeping its size) moves rev and the machine
  units.push({ uid: 'machine:farbox', kind: 'machine', name: 'farbox', status: 'DOWN' }); LY.planets.farbox = { x: 2000, y: -900 };
  CM_WORLD.at = -1e6; const revM = cmWorld().rev, mA = cmWorld().machines.find((m) => m.uid === 'machine:farbox');
  LY.planets.farbox = { x: 2500, y: -900 }; CM_WORLD.at = -1e6; const wM = cmWorld(), mB = wM.machines.find((m) => m.uid === 'machine:farbox');
  ok('machines: in cmWorld at their layout place', !!mA && mA.x === 2000 && mA.y === -900 && mA.state === 'down' && mA.label === 'farbox');
  ok('machines: a machine the layout moves moves rev and its x', wM.rev === revM + 1 && !!mB && mB.x === 2500);
  units.pop(); delete LY.planets.farbox; CM_WORLD.at = -1e6; cmWorld();
  // __3dStep's flight (each stepped frame runs PAGE3D.fly(dt, true, false)): the held keys fly, and a barrel roll and a
  // U-turn run on the stepped clock, so after n frames of dt they have run exactly n*dt
  P3.stepBase = null; P3.stepAt = performance.now(); P3.hold = false; P3.paused = false;
  FL.kb = true; FL.hdg = 0; FL.spd = 0; FL.roll = null; FL.uturn = null; FL.tap = {};
  keys.d = true; for (let i = 0; i < 15; i++) PAGE3D.fly(1 / 30, true, false); keys.d = false;
  ok('stepped: a held key turns the ship (2.4 rad/s for 0.5 s)', Math.abs(FL.hdg - 1.2) < 0.05 && FL.spd >= 200);
  if (!REDUCED) {
    flightTap('d'); flightTap('d');
    const rolled = !!FL.roll; for (let i = 0; i < 6; i++) PAGE3D.fly(1 / 30, true, false);
    const half = PAGE3D.ship().rollDeg; for (let i = 0; i < 7; i++) PAGE3D.fly(1 / 30, true, false);
    ok('stepped: a barrel roll is half way after 0.2 s of stepped time and over after 0.4 s', rolled && Math.abs(half - 180) < 1e-6 && FL.roll === null);
    const h0 = FL.hdg; flightTap('s'); flightTap('s');
    const turning = !!FL.uturn; for (let i = 0; i < 18; i++) PAGE3D.fly(1 / 30, true, false);
    const mid = PAGE3D.ship().uturnU; for (let i = 0; i < 19; i++) PAGE3D.fly(1 / 30, true, false);
    ok('stepped: a U-turn is half way after 0.6 s and done after 1.2 s, on the reverse heading', turning && Math.abs(mid - 0.5) < 1e-6
      && FL.uturn === null && Math.abs(Math.abs(headingNorm(FL.hdg - h0)) - Math.PI) < 1e-6);
  }
  PAGE3D.fly(0.016, false, false);
  ok('a live frame hands the clock back to the wall', P3.stepBase === null);
""")


# headless Chrome on a page on disk: evaluate one async expression, report its value and every uncaught exception
CDP_RUNNER = r"""
import { readFileSync } from 'node:fs';
const [port, url, exprFile] = process.argv.slice(2);
const expr = readFileSync(exprFile, 'utf8');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let list;
for (let i = 0; i < 100 && !list; i++) { try { list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json(); } catch (e) { await sleep(100); } }
const target = list.find((t) => t.type === 'page');
const ws = new WebSocket(target.webSocketDebuggerUrl);
const pending = new Map(); let id = 0, blocked = 0; const errors = [];
const send = (method, params = {}) => new Promise((res, rej) => { const i = ++id; pending.set(i, { res, rej }); ws.send(JSON.stringify({ id: i, method, params })); });
ws.onmessage = (m) => {
  const j = JSON.parse(m.data);
  if (j.id && pending.has(j.id)) { const p = pending.get(j.id); pending.delete(j.id); if (j.error) p.rej(new Error(JSON.stringify(j.error))); else p.res(j.result); }
  else if (j.method === 'Fetch.requestPaused') {
    if (/^(file|data|about):/.test(j.params.request.url)) send('Fetch.continueRequest', { requestId: j.params.requestId });
    else { blocked++; send('Fetch.failRequest', { requestId: j.params.requestId, errorReason: 'BlockedByClient' }); }
  } else if (j.method === 'Runtime.exceptionThrown') errors.push(j.params.exceptionDetails.exception?.description || j.params.exceptionDetails.text);
};
await new Promise((r) => { ws.onopen = r; });
await send('Fetch.enable', { patterns: [{ urlPattern: '*' }] });
await send('Runtime.enable'); await send('Page.enable');
await send('Emulation.setDeviceMetricsOverride', { width: 1280, height: 720, deviceScaleFactor: 1, mobile: false });
const loaded = new Promise((r) => ws.addEventListener('message', (m) => { if (JSON.parse(m.data).method === 'Page.loadEventFired') r(); }));
await send('Page.navigate', { url });
await loaded; await sleep(1500);
const r = await send('Runtime.evaluate', { returnByValue: true, awaitPromise: true, expression: expr });
console.log('RESULT ' + JSON.stringify({ value: r.exceptionDetails ? null : r.result.value,
  exception: r.exceptionDetails ? (r.exceptionDetails.exception?.description || r.exceptionDetails.text) : null, blocked, errors: errors.slice(0, 5) }));
ws.close();
process.exit(0);
"""

WARMUP_EXPR = r"""(async () => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const until = async (f, ms) => { const t0 = performance.now(); while (performance.now() - t0 < ms) { try { if (f()) return true; } catch (e) {} await sleep(40); } return false; };
  const leave = async () => { CM3D.leave('key'); return until(() => __3dAudit().mode === 'off', 12000); };
  const out = {};
  // the waits are wall-clock budgets the predicates end early: generous, since a loaded machine (other suites, SwiftShader)
  // can draw a frame or two a second while the view clock moves at most 0.1 s a frame
  // a page on disk reloads itself every 15 s outside 3D (render-game.py, no bridge); this run leaves 3D six times and the
  // flight module's exit timeline makes each leave take a while, so the page is told a box has focus for the run
  typing = () => true;
  // 1. the approach from the held match frame: the distance is for the lens the cut opens, not the 4 degree match lens
  P3_PARAMS.hold = 'match'; CM.enter3d({});
  out.held = await until(() => __3dAudit().mode === 'entering' && __3dAudit().transition.phase === 'match', 15000);
  out.fovHeld = __3dAudit().camera.fov;
  const r1 = __3dApproach('area:Alpha'); __3dStep(90, 1 / 30); out.share = __3dAudit().widthShare['area:Alpha']; out.d = r1 && r1.d;
  const r2 = __3dApproach('area:Alpha'); __3dStep(90, 1 / 30); out.share2 = __3dAudit().widthShare['area:Alpha']; out.d2 = r2 && r2.d;
  P3_PARAMS.hold = ''; __3dHold(false);
  out.left1 = await leave();
  // 2. a leave under the warm-up disposes the real materials as a normal leave does, and each stand-in once
  // counted per material, not per call: the craft share one hull material over many meshes and dispose it with each
  // mesh, so the calls follow how many craft the view had built; Bcalls says each stand-in went exactly once
  const seen = new Map(); const od = THREE.Material.prototype.dispose;
  THREE.Material.prototype.dispose = function () { seen.set(this, (seen.get(this) || 0) + 1); return od.apply(this, arguments); };
  const count = () => { const c = { S: 0, B: 0, o: 0, Bcalls: 0 }; seen.forEach((n, m) => { const k = m.userData && m.userData.cmStandIn ? 'B' : m.isShaderMaterial ? 'S' : 'o'; c[k]++; if (k === 'B') c.Bcalls += n; }); seen.clear(); return c; };
  CM.enter3d({}); out.on2 = await until(() => __3dAudit().mode === 'fp' && __3dAudit().compile.pending === 0, 20000);
  out.emptyStepsHidden = getComputedStyle(document.querySelector('.steps3d')).display === 'none';
  out.left2 = await leave(); out.normal = count();
  CM.slowCompile = true; CM.enter3d({});
  out.slowPending = await until(() => __3dAudit().compile.placeholders > 0, 20000); out.placeholders = __3dAudit().compile.placeholders;   // a loaded machine's entry can take over 4 s
  out.left3 = await leave(); await sleep(3000); out.early = count(); CM.slowCompile = false;
  // 2b. an exit 0.3 s after the entry, the warm-up still running (CM.slowCompile holds its end 2 s): every material is
  // disposed exactly once, whoever made it; the modules mark their own (userData.cmOwner) and the core skips those
  const calls = new Map(); THREE.Material.prototype.dispose = function () { calls.set(this, (calls.get(this) || 0) + 1); return od.apply(this, arguments); };
  CM.slowCompile = true; CM.enter3d({});
  out.quickOn = await until(() => __3dAudit().mode !== 'off', 10000); await sleep(300);
  out.quickPending = __3dAudit().compile.pending;
  CM3D.leave('key'); CM3D.leave('key');                 // the second leave skips the exit timeline: gone at 0.3 s
  out.quickOff = await until(() => __3dAudit().mode === 'off', 2000); await sleep(3000); CM.slowCompile = false;
  const twice = []; let owned = 0;
  calls.forEach((n, m) => { if (m.userData && m.userData.cmOwner) owned++; if (n > 1) twice.push((m.userData && (m.userData.cmOwner || (m.userData.cmStandIn ? 'stand-in' : ''))) + ' ' + m.type + ' x' + n); });
  out.quick = { materials: calls.size, owned, twice: twice.slice(0, 12), nTwice: twice.length };
  THREE.Material.prototype.dispose = od;
  // 3. never ready: the stand-ins draw until the 2.5 s race ends, then the real materials go back
  // read while it waits, 0.4 s in: the flight module's entry timeline may outlast the 2.5 s race before the view flies
  CM.compileStall = true; CM.enter3d({});
  await until(() => __3dAudit().compile.placeholders > 0, 10000); await sleep(400);
  out.stallPending = __3dAudit().compile.pending; out.stallPlaceholders = __3dAudit().compile.placeholders;
  out.stallFp = await until(() => __3dAudit().mode === 'fp', 20000);
  out.stallEnd = await until(() => __3dAudit().compile.last === 'timeout', 5000);
  out.stallAfter = JSON.parse(JSON.stringify(__3dAudit().compile));
  out.left4 = await leave(); CM.compileStall = false;
  // 3b. the race honours the entry's timeline: an entry held in its timeline (no view time passes) keeps its stand-ins past
  // 2.5 s; once the timeline is over the race ends at once and says so
  CM.compileStall = true; CM.enter3d({});
  await until(() => __3dAudit().mode !== 'off' && __3dAudit().compile.placeholders > 0, 4000); __3dHold(true);
  await sleep(3200);
  out.heldRace = { phase: __3dAudit().transition.phase, last: __3dAudit().compile.last || null, placeholders: __3dAudit().compile.placeholders, mode: __3dAudit().mode };
  __3dHold(false);
  out.heldFp = await until(() => __3dAudit().mode === 'fp', 20000);
  out.heldEnd = await until(() => __3dAudit().compile.last === 'timeout', 5000);
  out.heldWindow = __3dAudit().compile.window || null;
  out.left4b = await leave(); CM.compileStall = false;
  // 4. a compile that throws: every stand-in stays and the error box says so
  CM.compileFail = true; CM.enter3d({});
  out.failEnd = await until(() => __3dAudit().mode === 'fp' && __3dAudit().compile.last === 'failed', 20000);
  out.failAfter = JSON.parse(JSON.stringify(__3dAudit().compile)); out.failErrors = __3dAudit().errors.map((e) => e.module);
  out.left5 = await leave(); CM.compileFail = false;
  // 5b. "Retro 3D" (Settings, off by default; 's look moved from the classic theme to this row): 240 lines,
  // a 4x4 dither, 32 levels; off again, the clean look
  const retroRow = CM.settingsExtra.find((r) => r.key === 'retro-3d'); out.retroDefault = retroRow ? retroRow.get() : null;
  if (retroRow) {
    retroRow.set('on'); CM.enter3d({});
    out.retroFp = await until(() => __3dAudit().mode === 'fp', 20000); __3dStep(1, 0);
    out.retroPost = __3dAudit().post; out.retroErrors = __3dAudit().errors.filter((e) => e.module !== 'compile').map((e) => e.module + ': ' + e.message);   // the error box keeps 4.'s compile fault for the page's life
    out.retroCalls = __3dAudit().calls;
    // the drawn frame tells retro from clean: the dither quantises each channel to 32 levels, so a grid of samples over the
    // frame holds at most 32 distinct values a channel; the clean frame's gradients (sky, sun, planets) hold more
    const levels = () => { const b = __3dAudit().buffer, pr = __3dAudit().dpr || 1, R = new Set(), G = new Set(), B = new Set(); let n = 0;
      for (let i = 1; i < 24; i++) for (let j = 1; j < 24; j++) { const p = __3dPixel(Math.floor(b.w / pr * i / 24), Math.floor(b.h / pr * j / 24)); if (!p) continue; n++; R.add(p[0]); G.add(p[1]); B.add(p[2]); }
      return { n, r: R.size, g: G.size, b: B.size, max: Math.max(R.size, G.size, B.size) }; };
    out.retroLevels = levels();
    retroRow.set('off'); __3dStep(1, 0); out.cleanPost = __3dAudit().post; out.cleanCalls = __3dAudit().calls; out.cleanLevels = levels();
    out.left7 = await leave();
  }
  // 6. a targeted entry across the sun: the target is locked, the turn toward it is not made (the ship is placed beyond the
  // launch field with the sun between it and the first planet)
  {
    const w0 = cmWorld(), st = w0.star, p = w0.planets[0], ux = st.x - p.x, uy = st.y - p.y, n = Math.hypot(ux, uy) || 1, far = st.r * 4 * 1.05 + 60 + 200;
    px = st.x + ux / n * far; py = st.y + uy / n * far; FL.hdg = Math.atan2(uy, ux); heading = FL.hdg;
    CM.enter3d({ target: p.uid });
    out.acrossOn = await until(() => __3dAudit().mode !== 'off' && __3dAudit().transition && __3dAudit().transition.t != null, 15000);
    const want = Math.atan2(p.y - py, p.x - px), off = Math.abs(Math.atan2(Math.sin(FL.hdg - want), Math.cos(FL.hdg - want))) * 180 / Math.PI;
    out.across = { retarget: __3dAudit().retarget, launch: __3dAudit().launch, lock: __3dAudit().lock, offDeg: Math.round(off), uid: p.uid };
    out.left8 = await leave();
  }
  // 7. a retired planet's textures go with it, a focus change disposes the moons' materials once: the materials and textures
  // disposed over an entry that approaches Alpha (its moons built), then Beta (Alpha's moons gone), loses Gamma from the
  // world, and leaves
  {
    const mcalls = new Map(), tcalls = new Map(), ot = THREE.Texture.prototype.dispose;
    THREE.Material.prototype.dispose = function () { mcalls.set(this, (mcalls.get(this) || 0) + 1); return od.apply(this, arguments); };
    THREE.Texture.prototype.dispose = function () { tcalls.set(this, (tcalls.get(this) || 0) + 1); return ot.apply(this, arguments); };
    CM.enter3d({}); out.retireOn = await until(() => __3dAudit().mode === 'fp' && __3dAudit().compile.pending === 0, 20000);
    __3dApproach('area:Alpha'); __3dStep(30, 1 / 30); await sleep(1200); __3dStep(30, 1 / 30);
    out.moonsAlpha = (__3dAudit().scene && __3dAudit().scene.moons) || null;
    __3dApproach('area:Beta'); __3dStep(30, 1 / 30); await sleep(1200); __3dStep(30, 1 / 30);
    const texBefore = __3dAudit().textures, gone = MOONS.find((m) => m.uid === 'area:Gamma'), gi = MOONS.indexOf(gone);
    MOONS.splice(gi, 1); await sleep(1300); __3dStep(5, 1 / 30); await sleep(1300); __3dStep(5, 1 / 30);
    out.retire = { texBefore, texAfter: __3dAudit().textures, sharedMaps: (__3dAudit().scene.realPlanets || {}).maps || 0, planets: cmWorld().planets.map((q) => q.uid), canvasGone: 0 };
    tcalls.forEach((k, t) => { if (t.isCanvasTexture) out.retire.canvasGone += 1; });
    MOONS.splice(gi, 0, gone);
    out.left9 = await leave(); await sleep(1500);
    const twice2 = []; mcalls.forEach((k, m) => { if (k > 1) twice2.push((m.userData && m.userData.cmOwner) + ' ' + m.type + ' x' + k); });
    const ttwice = []; tcalls.forEach((k, t) => { if (k > 1) ttwice.push(t.type + ' x' + k); });
    out.retire.cubesDisposed = 0; tcalls.forEach((k, t) => { if (t.isCubeTexture) out.retire.cubesDisposed++; });
    out.retire.materialsTwice = twice2.slice(0, 12); out.retire.texturesTwice = ttwice.slice(0, 12); out.retire.materials = mcalls.size;
    THREE.Material.prototype.dispose = od; THREE.Texture.prototype.dispose = ot;
  }
  // 5. and the next entry is clean
  CM.enter3d({}); out.clean = await until(() => __3dAudit().mode === 'fp' && __3dAudit().compile.pending === 0 && __3dAudit().compile.placeholders === 0, 20000);
  // the drawing buffer (DPR 1 on a phone, the governor's scale) is stretched smoothly, never in the 2D map's pixel blocks
  out.canvasRendering = getComputedStyle(document.getElementById('c3d')).imageRendering;
  out.left6 = await leave();
  return out;
})()"""


class WarmUpAndApproach(unittest.TestCase):
    """The repros (approach.json, dispose.json) and the warm-up's two other ends, in headless Chrome."""

    def test_approach_from_the_match_frame_and_the_warm_up_ends(self):
        chrome = test_game.find_chrome()
        if not (chrome and shutil.which("node")):
            self.skipTest("needs node and a Chrome or Chromium binary (set CHROME_BIN)")
        with tempfile.TemporaryDirectory(prefix="cm3d-warm-") as tmp:
            for name in ("Alpha", "Beta", "Gamma"):
                Path(tmp, "work", name).mkdir(parents=True)
                Path(tmp, "work", name, "notes.md").write_text("fixture\n", encoding="utf-8")
            for sub in ("one", "two", "three"):                       # Alpha's sub-folders: its moons in 3D
                Path(tmp, "work", "Alpha", sub).mkdir()
                Path(tmp, "work", "Alpha", sub, "a.md").write_text("fixture\n", encoding="utf-8")
            proc = test_game.render_fixture(tmp)
            self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
            page = Path(tmp, "page.html")
            runner, expr = Path(tmp, "warm.mjs"), Path(tmp, "warm.js")
            runner.write_text(CDP_RUNNER, encoding="utf-8")
            expr.write_text(WARMUP_EXPR, encoding="utf-8")
            profile = Path(tmp, "profile")
            browser = subprocess.Popen(
                [chrome, "--headless=new", "--no-sandbox", "--remote-debugging-port=0", f"--user-data-dir={profile}",
                 "--no-first-run", "--no-default-browser-check", "--remote-allow-origins=*", "--allow-file-access-from-files",
                 "--enable-unsafe-swiftshader", "--use-angle=swiftshader", "--disable-background-networking",
                 "--disable-component-update", "--no-pings", "--hide-scrollbars", "--host-resolver-rules=MAP * ~NOTFOUND",
                 "--proxy-server=http://127.0.0.1:9", "--proxy-bypass-list=<-loopback>", "about:blank"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                port_file = profile / "DevToolsActivePort"
                deadline = time.time() + 30
                while time.time() < deadline and not (port_file.exists() and port_file.read_text().strip()):
                    if browser.poll() is not None:
                        self.fail(f"{chrome} exited with {browser.returncode}")
                    time.sleep(0.1)
                port = port_file.read_text().splitlines()[0]
                run = subprocess.run(["node", str(runner), port, page.as_uri(), str(expr)], capture_output=True, text=True,
                                     timeout=420)
            finally:
                browser.terminate()
                try:
                    browser.wait(10)
                except subprocess.TimeoutExpired:
                    browser.kill()
                    browser.wait()
        line = next((ln for ln in run.stdout.splitlines() if ln.startswith("RESULT ")), None)
        self.assertIsNotNone(line, (run.stdout + run.stderr)[-3000:])
        res = json.loads(line[len("RESULT "):])
        self.assertIsNone(res["exception"], res)
        self.assertEqual((res["errors"], res["blocked"]), ([], 0), res)
        got = res["value"]
        self.assertTrue(all(got[k] for k in ("left1", "left2", "left3", "left4", "left5", "left6")), got)
        # 1. the planet at a third of the width from the match frame, the same distance as a call in flight
        self.assertTrue(got["held"], got)
        self.assertEqual(got["fovHeld"], 4, got)
        self.assertTrue(0.23 <= got["share"] <= 0.45, got)
        self.assertEqual((got["share"], got["d"]), (got["share2"], got["d2"]), got)
        # 2. the leave under the warm-up: the real materials disposed as on a normal leave, each stand-in once
        self.assertTrue(got["on2"] and got["slowPending"], got)
        self.assertTrue(got["emptyStepsHidden"], "no workflow means no placeholder steps HUD")
        self.assertGreater(got["normal"]["S"], 0, got)
        self.assertEqual(got["early"]["S"], got["normal"]["S"], got)
        self.assertEqual(got["early"]["B"], got["placeholders"], got)
        self.assertEqual(got["early"]["Bcalls"], got["placeholders"], got)
        # 2b. an exit 0.3 s after the entry, under the warm-up: each material disposed once (space's and craft's are
        # their modules' to dispose; the warm-up's end no longer disposes them a second time)
        self.assertTrue(got["quickOn"] and got["quickOff"], got)
        self.assertGreater(got["quickPending"], 0, got["quick"])
        self.assertGreater(got["quick"]["materials"], 0, got["quick"])
        self.assertGreater(got["quick"]["owned"], 0, got["quick"])
        self.assertEqual(got["quick"]["nTwice"], 0, got["quick"])
        # 3. never ready: stand-ins while it waits, the real materials back when the 2.5 s race ends
        self.assertTrue(got["stallFp"] and got["stallEnd"], got)
        self.assertGreater(got["stallPending"], 0, got)
        self.assertGreater(got["stallPlaceholders"], 0, got)
        self.assertEqual((got["stallAfter"]["pending"], got["stallAfter"]["placeholders"]), (0, 0), got)
        # 3b. held in its timeline past 2.5 s the entry keeps its stand-ins; the race ends once the timeline does
        self.assertEqual(got["heldRace"]["mode"], "entering", got["heldRace"])
        self.assertIsNone(got["heldRace"]["last"], got["heldRace"])
        self.assertGreater(got["heldRace"]["placeholders"], 0, got["heldRace"])
        self.assertTrue(got["heldFp"] and got["heldEnd"] and got["left4b"], got)
        self.assertEqual(got["heldWindow"]["until"], "timeline", got["heldWindow"])
        self.assertIs(got["heldWindow"]["held"], True, got["heldWindow"])        # recorded as it waited, not read off the clock
        self.assertGreaterEqual(got["heldWindow"]["ms"], 3200, got["heldWindow"])
        # 4. a compile that throws keeps every stand-in and says so in the error box
        self.assertTrue(got["failEnd"], got)
        self.assertGreater(got["failAfter"]["placeholders"], 0, got)
        self.assertIn("compile", got["failErrors"], got)
        self.assertTrue(got["clean"], got)
        # 5b. Retro 3D: off by default; on, the scene renders at 240 lines with the dither; off, the clean look again
        self.assertEqual(got["retroDefault"], "off", got)
        self.assertTrue(got["retroFp"] and got["left7"], got)
        self.assertEqual((got["retroPost"]["retro"], got["retroPost"]["look"], got["retroPost"]["rtHeight"]), (True, "classic", 240), got["retroPost"])
        self.assertEqual(got["retroErrors"], [], got)
        self.assertEqual((got["cleanPost"]["retro"], got["cleanPost"]["look"], got["cleanPost"]["rtHeight"]), (False, "clean", None), got["cleanPost"])
        # the frame itself: retro's 32 levels a channel over a 23x23 grid, the clean frame's gradients more; and one more draw
        # call (the full-screen triangle that brings the 240-line target up)
        self.assertGreater(got["retroLevels"]["n"], 400, got["retroLevels"])
        self.assertLessEqual(got["retroLevels"]["max"], 32, got["retroLevels"])
        self.assertGreater(got["cleanLevels"]["max"], got["retroLevels"]["max"], (got["cleanLevels"], got["retroLevels"]))
        self.assertEqual(got["retroCalls"], got["cleanCalls"] + 1, (got["retroCalls"], got["cleanCalls"]))
        self.assertEqual(got["canvasRendering"], "auto", got)
        # 6. a targeted entry across the sun locks the target and keeps the heading (never a first look across the glare)
        self.assertTrue(got["acrossOn"] and got["left8"], got)
        across = got["across"]
        self.assertEqual(across["retarget"], {"uid": across["uid"], "turned": False, "acrossSun": True}, across)   # (audit.lock is the HUD's dwell lock)
        self.assertIsNone(across["launch"], across)                       # placed beyond the near field: no launch
        self.assertGreater(across["offDeg"], 150, across)                 # the heading still points away from the target
        # 7. a retired planet takes its textures with it; a focus change and the leave dispose each material and texture once
        self.assertTrue(got["retireOn"] and got["left9"], got)
        self.assertNotIn("area:Gamma", got["retire"]["planets"], got["retire"])
        self.assertGreaterEqual(got["retire"]["canvasGone"], 1, got["retire"])
        if got["retire"]["sharedMaps"]:
            # Baked maps are cached for the session: another folder may share one.
            # Retirement must not grow the cache; leaving releases every cube once.
            self.assertLessEqual(got["retire"]["texAfter"], got["retire"]["texBefore"], got["retire"])
            self.assertGreaterEqual(got["retire"]["cubesDisposed"], got["retire"]["sharedMaps"], got["retire"])
        else:
            self.assertLess(got["retire"]["texAfter"], got["retire"]["texBefore"], got["retire"])
        self.assertEqual((got["retire"]["materialsTwice"], got["retire"]["texturesTwice"]), ([], []), got["retire"])


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(shutil.which("node"), "node not installed")
class ConstellationShellTests(unittest.TestCase):
    def test_shell_direction_and_budget(self):
        script = "globalThis.CM3D={module(){}};require(" + json.dumps(str(CORE.parent / "routes.js")) + ");"
        script += "const w={star:{x:0,y:0},constellations:Array.from({length:6},(_,i)=>({angle:i*Math.PI/3,threads:Array.from({length:500},()=>({angle:i*Math.PI/3}))}))};const g=CM3D.constellationKit.layout(w,768);console.log(JSON.stringify({n:g.reduce((s,l)=>s+l.points.length,0),p:g[0].points[0].pos}));"
        proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
        got = json.loads(proc.stdout)
        self.assertEqual(got["n"], 768)
        self.assertGreaterEqual(got["p"][0], 3000)
