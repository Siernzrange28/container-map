#!/usr/bin/env python3
"""test_3d_fx.py — the 3D effects lane (world3d/fx.js): an effect fires only when the data changed.

The pure half runs in node (no DOM, no three.js): the world diff (baseline silence, shock / cleared /
warp / shield classification, one mark per planet per rev, the priority cap), the life curves, and the
skin colour fall-through. The bundle half checks vendor3d still ships fx after space and that the
joined bundle loads. The drawn half is checked in a browser through ?demo3d=fx and __3dAudit().fx.

Run: python3 -m unittest -q test_3d_fx
"""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import testenv  # noqa: F401  (first: clears what steers the tool)
import vendor3d

ROOT = Path(__file__).resolve().parent
CORE = ROOT / "world3d" / "core.js"
FX = ROOT / "world3d" / "fx.js"

WORLD_JS = r"""
const P = (over) => Object.assign({ uid: 'area:A', x: 100, y: 200, r: 80, purpose: 'code', conditions: [],
  counts: { need_you: 0, problems: 0, decisions: 0 }, health: 'ok' }, over || {});
const W = (over) => Object.assign({ star: { x: 0, y: 0, r: 150, load: 0 }, planets: [P()], foes: [],
  waiting: [], craft: [], machines: [], palette: { code: [78, 121, 200] } }, over || {});
const K = (d) => d.events.map((e) => [e.kind, e.uid, e.tone, e.shrink ? 1 : 0]);
"""


def node(script, timeout=120):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp, "run.js")
        path.write_text(script, encoding="utf-8")
        return subprocess.run(["node", str(path)], capture_output=True, text=True, timeout=timeout)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class FxDiff(unittest.TestCase):
    """world3d/fx.js in node: the classification of what changed — the honesty rule of the lane."""

    def run_fx(self, body):
        run = node(f"require({json.dumps(str(CORE))}); require({json.dumps(str(FX))});"
                   f" const F = globalThis.CM3D.fx;\n{WORLD_JS}\n{body}")
        self.assertEqual(run.returncode, 0, run.stderr[-3000:])
        return json.loads(run.stdout.strip().splitlines()[-1])

    def test_entering_and_no_change_are_quiet(self):
        got = self.run_fx("""
          console.log(JSON.stringify({
            baseline: K(F.diffWorlds(null, W())),
            same: K(F.diffWorlds(W(), W())),
            sameRung: K(F.diffWorlds(W({ planets: [P({ health: 'quiet' })] }), W())),
            junk: K(F.diffWorlds(W({ planets: [null, {}, P()] }), W())),
            empty: F.snapshot(null) }));
        """)
        self.assertEqual(got["baseline"], [])
        self.assertEqual(got["same"], [])
        self.assertEqual(got["sameRung"], [])      # quiet -> ok shares a rung: not news
        self.assertEqual(got["junk"], [])
        self.assertEqual(got["empty"]["planets"], {})

    def test_worsened_is_a_shock_improved_is_cleared(self):
        got = self.run_fx("""
          const broken = W({ planets: [P({ health: 'broken', counts: { need_you: 0, problems: 1, decisions: 0 } })] });
          const waits = W({ planets: [P({ health: 'waiting', counts: { need_you: 1, problems: 0, decisions: 0 } })] });
          console.log(JSON.stringify({
            bad: K(F.diffWorlds(W(), broken)),
            warn: K(F.diffWorlds(W(), waits)),
            fixed: K(F.diffWorlds(broken, W())),
            worseWins: K(F.diffWorlds(broken, W({ planets: [P({ health: 'waiting',
              counts: { need_you: 3, problems: 0, decisions: 0 } })] }))) }));
        """)
        self.assertEqual(got["bad"], [["shock", "area:A", "bad", 0]])
        self.assertEqual(got["warn"], [["shock", "area:A", "warn", 0]])
        self.assertEqual(got["fixed"], [["cleared", "area:A", "good", 0]])
        self.assertEqual(got["worseWins"], [["shock", "area:A", "warn", 0]])   # rank down but waiting up: shock

    def test_arrivals_warp_in_departures_warp_out(self):
        got = self.run_fx("""
          const two = W({ planets: [P(), P({ uid: 'area:B', x: 9, y: 9, r: 40 })],
            craft: [{ uid: 'ship:1', state: 'idle', planet: null, x: 5, y: 6 }],
            machines: [{ uid: 'machine:other', x: 700, y: 800 }] });
          const inD = F.diffWorlds(W(), two), outD = F.diffWorlds(two, W());
          console.log(JSON.stringify({ arrive: K(inD), rgb: inD.events[0].rgb, leave: K(outD) }));
        """)
        self.assertEqual(got["rgb"], [78, 121, 200])          # the new planet warps in its purpose colour
        self.assertEqual(sorted(got["arrive"]), sorted([
            ["warp", "area:B", "new", 0], ["warp", "machine:other", "new", 0], ["warp", "ship:1", "new", 0]]))
        self.assertTrue(all(row[3] == 1 for row in got["leave"]))   # every departure contracts

    def test_problems_and_waiting_mark_their_planet_once(self):
        got = self.run_fx("""
          const foe = { uid: 'debug:1', parent: 'area:A' }, foe2 = { uid: 'debug:2', parent: 'area:A' };
          const broken = W({ planets: [P({ health: 'broken', counts: { need_you: 0, problems: 1, decisions: 0 } })] });
          console.log(JSON.stringify({
            oneFoe: K(F.diffWorlds(W(), W({ foes: [foe] }))),
            twoFoes: K(F.diffWorlds(W(), W({ foes: [foe, foe2] }))),
            foeOnBroken: K(F.diffWorlds(W(), Object.assign(broken, { foes: [foe] }))),
            foeGone: K(F.diffWorlds(W({ foes: [foe] }), W())),
            waits: K(F.diffWorlds(W(), W({ waiting: [{ uid: 'q:1', parent: 'area:A' }] }))),
            noPlace: K(F.diffWorlds(W(), W({ foes: [{ uid: 'x:1', parent: 'area:gone' }] }))) }));
        """)
        self.assertEqual(got["oneFoe"], [["shock", "debug:1", "bad", 0]])
        self.assertEqual(len(got["twoFoes"]), 1)             # one mark per planet per rev
        self.assertEqual(got["foeOnBroken"], [["shock", "area:A", "bad", 0]])
        self.assertEqual(got["foeGone"], [["cleared", "debug:1", "good", 0]])
        self.assertEqual(got["waits"], [["shock", "q:1", "warn", 0]])
        self.assertEqual(got["noPlace"], [])                  # no known planet, no honest place to draw

    def test_work_starting_raises_a_shield(self):
        got = self.run_fx("""
          console.log(JSON.stringify({
            planet: K(F.diffWorlds(W(), W({ planets: [P({ conditions: ['working'] })] }))),
            craft: K(F.diffWorlds(
              W({ craft: [{ uid: 'ship:1', state: 'idle', planet: null, x: 5, y: 6 }] }),
              W({ craft: [{ uid: 'ship:1', state: 'working', planet: null, x: 5, y: 6 }] }))),
            craftUnderPlanet: K(F.diffWorlds(
              W({ craft: [{ uid: 'ship:1', state: 'idle', planet: 'area:A', x: 5, y: 6 }] }),
              W({ planets: [P({ conditions: ['working'] })],
                  craft: [{ uid: 'ship:1', state: 'working', planet: 'area:A', x: 5, y: 6 }] }))),
            star: K(F.diffWorlds(W(), W({ star: { x: 0, y: 0, r: 150, load: 2 } }))),
            starDown: K(F.diffWorlds(W({ star: { x: 0, y: 0, r: 150, load: 2 } }), W())) }));
        """)
        self.assertEqual(got["planet"], [["shield", "area:A", "work", 0]])
        self.assertEqual(got["craft"], [["shield", "ship:1", "work", 0]])
        self.assertEqual(got["craftUnderPlanet"], [["shield", "area:A", "work", 0]])   # not both at one place
        self.assertEqual(got["star"], [["shield", "star", "work", 0]])
        self.assertEqual(got["starDown"], [])

    def test_a_busy_rev_is_capped_shocks_first(self):
        got = self.run_fx("""
          const many = (health, problems) => W({ planets: Array.from({ length: 15 }, (_, i) =>
            P({ uid: 'area:P' + i, health: health, counts: { need_you: 0, problems: problems, decisions: 0 } })) });
          const d = F.diffWorlds(many('ok', 0), many('broken', 1));
          console.log(JSON.stringify({ n: d.events.length, cut: d.cut,
            kinds: d.events.map((e) => e.kind) }));
        """)
        self.assertEqual(got["n"], 12)
        self.assertEqual(got["cut"], 3)
        self.assertTrue(all(k == "shock" for k in got["kinds"]))

    def test_curves_and_colours(self):
        got = self.run_fx("""
          console.log(JSON.stringify({
            a0: F.alphaAt('shock', 0), a1: F.alphaAt('shock', 1), amid: F.alphaAt('shock', 0.3),
            grow: [F.ringScale('shock', 0, false, false), F.ringScale('shock', 1, false, false)],
            shrink: [F.ringScale('warp', 0, true, false), F.ringScale('warp', 1, true, false)],
            still: [F.ringScale('shock', 0.1, false, true), F.ringScale('shock', 0.9, false, true)],
            shell: [F.shieldScale(0, false), F.shieldScale(1, false)],
            colors: F.colors({ fx3d: { bad: '#123456', warp: 'red', good: '#GGGGGG' } }),
            plain: F.colors({}) }));
        """)
        self.assertEqual((got["a0"], got["a1"]), (0, 0))
        self.assertGreater(got["amid"], 0.3)
        self.assertLess(got["grow"][0], got["grow"][1])
        self.assertGreater(got["shrink"][0], got["shrink"][1])
        self.assertEqual(got["still"][0], got["still"][1])    # reduced motion: the ring does not travel
        self.assertLess(got["shell"][0], got["shell"][1])
        self.assertEqual(got["colors"]["bad"], "#123456")     # a skin's own colour wins
        self.assertEqual(got["colors"]["warp"], got["plain"]["warp"])   # junk falls through to neutral
        self.assertEqual(got["colors"]["good"], got["plain"]["good"])


class FxBundle(unittest.TestCase):
    def test_fx_ships_after_space_and_registers_both_faces(self):
        self.assertLess(vendor3d.ORDER.index("space"), vendor3d.ORDER.index("fx"))
        text = vendor3d.bundle(str(ROOT), "neutral")
        self.assertIn("CM3D.module('fx'", text)
        self.assertIn("CM3D.demo('fx'", text)
        self.assertIn("audit.fx", text)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_the_joined_bundle_loads_with_fx_live(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp, "bundle.js")
            bundle.write_text(vendor3d.bundle(str(ROOT), "neutral"), encoding="utf-8")
            run = node(f"require({json.dumps(str(bundle))}); const F = globalThis.CM3D.fx;"
                       " console.log(JSON.stringify({ diff: typeof F.diffWorlds, live: F.live() }));")
        self.assertEqual(run.returncode, 0, run.stderr[-3000:])
        self.assertEqual(json.loads(run.stdout.strip().splitlines()[-1]), {"diff": "function", "live": 0})


if __name__ == "__main__":
    unittest.main()
