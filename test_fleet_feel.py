#!/usr/bin/env python3
"""test_fleet_feel.py — the owner's own craft and how the pilot flies.

The data half reads the files: the classic pack's space is near-black with faint washes, each skin carries a livery
the validator accepts. The page half runs the real page script in node against test_game's stub DOM, one driver per
behaviour: the keyboard flight model (momentum, bank, boost meter, brake turn, barrel roll, U-turn, parking), the touch
pad and the pilot keeping their direct movement, reduced motion, the seats' letter tags in the plate layout, and the
radio calls on a seat's change between two world loads.

Run: python3 -m unittest -q test_fleet_feel   (node needed for the page half; else it is skipped)
"""
import colorsys
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import testenv  # noqa: F401  (first: clears what steers the tool)
import themepacks as tp
from test_game import page_js, render_fixture, run_page_driver

ROOT = Path(__file__).resolve().parent
RENDER_SRC = (ROOT / "render-game.py").read_text(encoding="utf-8")


def _lum(hex_colour):
    c = [int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    c = [v / 12.92 if v <= .03928 else ((v + .055) / 1.055) ** 2.4 for v in c]
    return .2126 * c[0] + .7152 * c[1] + .0722 * c[2]


class SpaceAndLiveryData(unittest.TestCase):
    def test_classic_space_is_near_black_with_faint_washes_and_crisp_white_stars(self):
        space = json.loads((ROOT / "themes" / "classic.json").read_text(encoding="utf-8"))["space"]
        self.assertLess(_lum(space["base"]), .003, "the void is pitch black")
        for wash in space["nebula"]:
            self.assertLess(_lum(wash), .04, wash + " is a faint wash, not a cloud")
            r, g, b = (int(wash[i:i + 2], 16) for i in (1, 3, 5))
            self.assertGreater(max(r, g, b) - min(r, g, b), 24, wash + " keeps some colour")
        self.assertIn("#ffffff", space["stars"])
        self.assertEqual(space["glow"], 0.6, "the halo scale stays where the classic look sits")

    def test_the_other_packs_keep_their_space(self):
        for name in ("earthen", "blueprint", "synthwave", "phosphor", "tactical"):
            space = json.loads((ROOT / "themes" / (name + ".json")).read_text(encoding="utf-8"))["space"]
            self.assertNotEqual(space.get("base"), "#050508", name)

    def test_each_skin_carries_a_livery_the_validator_accepts(self):
        # every skin in the tree (the export ships the neutral one only); skins/<name>-3d.json are the 3D craft and
        # <name>-ship3d.json / <name>-planets3d.json its baked ship and planet maps, not 2D skins. The livery is baked
        # from the skin's 3D hulls (tools/bake_sprites.py): the letters are the 3D colours
        for path in sorted(p for p in (ROOT / "skins").glob("*.json") if not p.stem.endswith(("-3d", "-ship3d", "-planets3d"))):
            name = path.stem
            skin = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(tp.validate_skin(skin, name), [], name)
            liv = skin["livery"]
            self.assertGreater(_lum(liv["h"]), .45, name + ": a light hull")
            self.assertLess(_lum(liv["C"]), _lum(liv["c"]), name + ": the canopy's glass darker than its lit face")
            r, g, b = (int(liv["b"][i:i + 2], 16) / 255 for i in (1, 3, 5))
            hue, _l, sat = colorsys.rgb_to_hls(r, g, b)
            self.assertTrue(215 <= hue * 360 <= 250 and max(r, g, b) - min(r, g, b) >= .3 * max(r, g, b),
                            name + ": a saturated blue-violet accent")

    def test_the_validator_refuses_a_livery_that_takes_the_pages_letters_or_is_not_colours(self):
        skin = json.loads((ROOT / "skins" / "neutral.json").read_text(encoding="utf-8"))
        for bad in ({"a": "#ffffff"}, {"e": "#ff00ff"}, {"h": "white"}, {"hh": "#ffffff"}, {}, ["#ffffff"]):
            with self.subTest(livery=bad):
                self.assertTrue(tp.validate_skin(dict(skin, livery=bad), "neutral"))

    def test_the_page_exposes_the_audits_and_the_radio_box(self):
        self.assertIn("window.__fleetAudit=function(){", RENDER_SRC)
        self.assertIn('<div id="radio" role="button"', RENDER_SRC)
        self.assertIn("'quests','radio'];", RENDER_SRC, "plates keep off the radio box while it shows")
        self.assertIn("html.m #radio{display:none!important}", RENDER_SRC, "a phone says it in the toast")


DRIVER_FLY = r"""
  let NOW = 100000; performance.now = () => NOW;
  VIEW = 'map'; document.activeElement = null; pilot = null; FL.kb = false; px = 0; py = 0;
  const fire = (type, key, o) => { const e = Object.assign({ key: key, repeat: false, ctrlKey: false, metaKey: false, altKey: false,
    shiftKey: false, prevented: false, preventDefault() { this.prevented = true; }, stopPropagation() {}, getModifierState() { return false; },
    target: document.activeElement || { blur() {} } }, o || {}); (__WL[type] || []).forEach((f) => f(e)); return e; };
  const run = (ms) => { for (let s = 0; s < ms; s += 16) { NOW += 16; step(NOW); } };
  const A = () => __fleetAudit(), turned = (a, b) => Math.abs(((b - a + 540) % 360) - 180);
  const reset = () => { ['w','a','s','d','shift','v-up'].forEach((k) => fire('keyup', k)); FL.spd = 0; FL.bank = FL.bankV = 0; FL.roll = FL.uturn = null;
    FL.meter = 1; FL.spent = false; FL.tap = {}; FL.hdg = -Math.PI / 2; FL.kb = true; pilot = null; px = 0; py = 0; NOW += 1000; step(NOW); };
  step(NOW);
  ok('before any key the keyboard does not fly the ship', A().keyboard === false);
  // bank into a turn, spring back level
  fire('keydown', 'd'); ok('a flight key hands the ship to the keyboard', A().keyboard === true);
  let h0 = A().heading; run(500);
  let a = A(); ok('turning: the craft banks past 30 degrees and the heading turns', a.bank > 30 && a.bank < 40 && turned(h0, a.heading) > 50);
  fire('keyup', 'd'); run(400); ok('let go: back under 3 degrees within 0.4 s', Math.abs(A().bank) < 3);
  // thrust, coast, park
  reset(); fire('keydown', 'w'); run(1000); a = A();
  ok('thrust: cruise speed along the heading', Math.abs(a.speed - SPEED) < 1 && a.heading === 270);
  fire('keyup', 'w'); run(300); ok('no key: it coasts', A().speed > 50 && A().speed < SPEED);
  run(2000); ok('no key: it parks', A().speed === 0);
  // boost meter: about 2 s of burn, speed falls back, 4.5 s to refill
  reset(); fire('keydown', 'w'); run(600); fire('keydown', 'Shift', { shiftKey: true }); run(400);
  ok('boost: 1.7x the speed', Math.abs(A().speed - SPEED * 1.7) < 1 && A().boosting);
  let empty = 0; const t0 = NOW; for (let s = 0; s < 2500 && !empty; s += 16) { NOW += 16; step(NOW); if (A().meter === 0) empty = NOW; }
  ok('boost: the meter empties after about 2 s of burn (' + (empty - t0) + ' ms after the first 0.4 s)', empty > 0 && Math.abs(empty - t0 - 1600) < 120);
  run(16); ok('...and the boost stops with it', !A().boosting);
  run(700); ok('boost spent: the speed falls back to cruise', Math.abs(A().speed - SPEED) < 1);
  fire('keyup', 'Shift'); fire('keyup', 'w');
  let full = 0; for (let s = 0; s < 6000 && !full; s += 16) { NOW += 16; step(NOW); if (A().meter === 1) full = NOW; }
  ok('the meter is full again about 4.5 s after it emptied', full - empty > 4300 && full - empty < 4800);
  // a parked craft does not lurch on Shift (Shift+H, Shift+V are page keys)
  reset(); fire('keydown', 'Shift', { shiftKey: true }); run(300); ok('Shift while parked does nothing', A().speed === 0 && A().meter === 1);
  fire('keyup', 'Shift');
  // brake: hard stop, and the brake turn is twice the rate with a hard bank
  reset(); fire('keydown', 'w'); run(800); fire('keyup', 'w'); fire('keydown', 's'); run(200);
  ok('brake: hard deceleration from the meter', A().speed < 200 && A().meter < 1 && A().braking);
  fire('keyup', 's'); reset(); fire('keydown', 'd'); h0 = A().heading; run(300); const plain = turned(h0, A().heading); fire('keyup', 'd');
  reset(); fire('keydown', 'w'); run(800); fire('keyup', 'w'); FL.tap = {}; fire('keydown', 's'); fire('keydown', 'd'); h0 = A().heading; run(300);
  const tight = turned(h0, A().heading); ok('brake turn: about twice the turn rate', tight > 1.7 * plain && tight < 2.3 * plain);
  run(300); ok('brake turn: the hard bank, near 80 degrees', A().bank > 70);
  fire('keyup', 's'); fire('keyup', 'd');
  // boost turn: a wider circle
  reset(); fire('keydown', 'w'); fire('keydown', 'Shift', { shiftKey: true }); run(200); fire('keydown', 'd'); h0 = A().heading; run(300);
  const wide = turned(h0, A().heading); ok('boost turn: a slower turn at 1.7x speed (a circle about 1.8x wider)', wide < plain && wide > .85 * plain);
  fire('keyup', 'd'); fire('keyup', 'Shift'); fire('keyup', 'w');
  // barrel roll: double tap within 250 ms, one 0.4 s roll, a small slip to the side
  reset(); const x0 = px, y0 = py; fire('keydown', 'd'); NOW += 60; fire('keyup', 'd'); NOW += 120; fire('keydown', 'd'); fire('keyup', 'd');
  ok('double-tap right: the barrel roll starts', A().rolling === true);
  run(200); ok('rolling at 0.2 s', A().rolling === true);
  run(260); ok('done by about 0.45 s', A().rolling === false);
  ok('the roll slips the craft to the right of its heading (east when it flies north)', px - x0 > 25 && Math.abs(py - y0) < 6);
  reset(); fire('keydown', 'a'); NOW += 300; fire('keyup', 'a'); NOW += 100; fire('keydown', 'a'); fire('keyup', 'a');
  ok('two taps 400 ms apart are two turns, not a roll', A().rolling === false);
  // U-turn: double tap down, a half loop onto the reverse heading in about 1.2 s
  reset(); FL.kb = true; h0 = A().heading; fire('keydown', 's'); NOW += 60; fire('keyup', 's'); NOW += 100; fire('keydown', 's'); fire('keyup', 's');
  ok('double-tap down: the U-turn starts', A().uturning === true);
  run(600); ok('mid-loop the craft is nearer the camera', pilotPose(NOW).grow > 1.2);
  run(700); a = A(); ok('the U-turn ends on the reverse heading', !a.uturning && turned(h0, a.heading) > 178);
  // the touch pad and the pilot keep their direct movement
  reset(); FL.kb = true; keys['v-up'] = 1; run(300); delete keys['v-up'];
  ok('the touch pad moves the ship straight, at once', py < -100 && Math.abs(px) < 1 && A().keyboard === false && A().speed === 0);
  reset(); fire('keydown', 'w'); run(400); fire('keyup', 'w'); pilot = { source: 'human', x: 300, y: 0 }; run(16);
  ok('click-to-fly takes over and the flight speed is dropped', A().keyboard === false && A().speed === 0);
  pilot = null;
  // typing: no keys reach the flight, the craft coasts
  reset(); fire('keydown', 'w'); run(500); document.activeElement = { tagName: 'INPUT' }; run(2500);
  ok('typing: the ship coasts to a stop', A().speed === 0); document.activeElement = null; fire('keyup', 'w');
  // drawing every pose does not throw
  try { reset(); fire('keydown', 'd'); run(200); draw(NOW); fire('keydown', 'w'); fire('keydown', 'Shift', { shiftKey: true }); run(200); draw(NOW);
    FL.roll = { t0: NOW - 100, dir: 1, lx: 1, ly: 0 }; draw(NOW); FL.roll = null; FL.uturn = { t0: NOW - 500, h0: 0, x: 0, y: 0, v: 200, R: 80, side: 1 }; draw(NOW);
    GB = true; draw(NOW); GB = false; ok('every pose draws', true); } catch (e) { ok('every pose draws', false); out.push('  ' + e.stack); }
  FL.uturn = null; ['w','d','shift'].forEach((k) => fire('keyup', k));
"""

DRIVER_REDUCED = r"""
  let NOW = 100000; performance.now = () => NOW;
  VIEW = 'map'; document.activeElement = null; pilot = null; px = 0; py = 0;
  const fire = (type, key) => { const e = { key: key, repeat: false, ctrlKey: false, metaKey: false, altKey: false, shiftKey: false,
    preventDefault() {}, stopPropagation() {}, getModifierState() { return false; }, target: { blur() {} } }; (__WL[type] || []).forEach((f) => f(e)); };
  const run = (ms) => { for (let s = 0; s < ms; s += 16) { NOW += 16; step(NOW); } };
  step(NOW);
  ok('reduced motion is on', REDUCED === true);
  fire('keydown', 'd'); run(16); ok('reduced: the bank state applies at once', Math.abs(__fleetAudit().bank - 35) < .1);
  ok('reduced: the craft is drawn flat', pilotPose(NOW) === POSE_FLAT); fire('keyup', 'd'); run(16);
  NOW += 1000; const x0 = px; fire('keydown', 'd'); fire('keyup', 'd'); NOW += 100; fire('keydown', 'd'); fire('keyup', 'd');
  ok('reduced: no roll animation, the slip applies at once', __fleetAudit().rolling === false && Math.abs(px - x0 - FLY.rollShift) < 1);
  run(16); const h0 = __fleetAudit().heading; NOW += 400; fire('keydown', 's'); fire('keyup', 's'); NOW += 100; fire('keydown', 's'); fire('keyup', 's');
  ok('reduced: the U-turn flips the heading at once', __fleetAudit().uturning === false && Math.abs(((__fleetAudit().heading - h0 + 540) % 360) - 180) > 179);
"""

DRIVER_TAGS = r"""
  VIEW = 'map'; px = 0; py = 0; setZoom(.5); camOff.x = 0; camOff.y = 0;
  const areas = W.chars.filter((c) => c.kind === 'area' && c.path);
  const seat = (name, status, area) => ({ uid: 'seat:' + name, kind: 'seat', name: name, status: status, cwd: area ? area.path : '', line: name + ' says hi', seat: name });
  const seats = [seat('hub', 'WORKING'), seat('mail', 'WORKING', areas[0]), seat('money', 'IDLE', areas[0]), seat('orbit', 'WAITING', areas[1]),
    seat('oracle', 'DRAFT', areas[1]), seat('ops', 'DEAD', areas[2]), seat('astra', 'WORKING', areas[3])];
  W.chars = W.chars.filter((c) => c.kind !== 'seat').concat(seats); place();
  const L = wingLetters();
  ok('one letter a seat: its first', L['seat:hub'] === 'H' && L['seat:astra'] === 'A');
  ok('two letters when seats share the first', L['seat:mail'] === 'MA' && L['seat:money'] === 'MO');
  const os = ['seat:orbit', 'seat:oracle', 'seat:ops'].map((u) => L[u]);
  ok('...and never the same two', new Set(os).size === 3 && os.every((s) => s.length === 2 && s[0] === 'O'));
  draw(2000); let P = __plateAudit();
  const shown = P.wings.filter((w) => !w.hidden);
  const onMap = (u) => { const x = (u.gx * TILE - camx) * ZOOM, y = (u.gy * TILE - camy) * ZOOM; return x > 0 && y > 0 && x < VW && y < VH; };
  const visible = seats.filter((s) => onMap(units.find((u) => u.uid === s.uid)));
  ok('the fixture puts seats on screen', visible.length >= 4);
  // sky mode (on by default) puts a working seat in warp, the carrier too: not drawn, so no tag; the full view draws them all
  // an idle agent is stacked at the carrier on the overview: not drawn one by one, so no tag of its own
  const U = (s) => units.find((u) => u.uid === s.uid), drawnSeats = visible.filter((s) => !skyHidden(U(s)) && !CM.detailHidden(U(s)));
  ok('every seat drawn on the map has its tag (the player none), and a seat in warp none', drawnSeats.every((s) => P.wings.some((w) => w.uid === s.uid))
    && !P.wings.some((w) => w.uid === 'player') && !P.wings.some((w) => skyHidden(units.find((u) => u.uid === w.uid) || {})));
  setSky(false); draw(2050); const PF = __plateAudit(), visF = seats.filter((s) => onMap(U(s)) && !CM.detailHidden(U(s)));
  ok('...and in the full view every seat on the map has its tag, the carrier and the working seats too', visF.length >= 4
    && visF.every((s) => PF.wings.some((w) => w.uid === s.uid)) && visF.some((s) => s.status === 'WORKING'));
  setSky(true); draw(2000); P = __plateAudit();
  const hit = (a, b) => a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y;
  ok('no tag sits on a planet tag or on another tag', shown.every((w) => !P.plates.some((p) => p.kind === 'tag' && hit(w, p)))
    && shown.every((w, i) => shown.every((v, j) => j === i || !hit(w, v))));
  ok('a tag is screen-sized: the same at every zoom', shown.every((w) => w.h === WING.h && (w.w === WING.w || w.w === WING.w2)));
  setZoom(1.5); draw(2100); P = __plateAudit(); ok('...zoomed in too', P.wings.every((w) => w.h === WING.h));
  // a dock busy enough to fold takes its tags into the fold
  const dock = units.find((u) => u.uid === 'seat:mail').docked_at;
  PLATE_FOLDS = [{ dock: dock }]; WING_Q = [{ uid: 'seat:mail', dock: dock, x: 0, y: 0, r: 10, col: '#3dff9e' }, { uid: 'seat:hub', dock: null, x: 50, y: 0, r: 10, col: '#3dff9e' }];
  const sq = wingDiscs(); ok('a folded dock’s tags go into its fold', sq.length === 1 && sq[0].ms[0].uid === 'seat:hub');
  // the friendly palette: the livery in the classic look, the status colour on the accents, never in the vector look or mono
  setZoom(.5); applyTheme('classic', false);
  ok('the classic look wears the skin livery', liveryOn() && friendPal('', '').h === SKIN.livery.h && friendPal('#3dff9e', '').b === '#3dff9e');
  ok('a problem raider goes gunmetal beside them', foePal('#ff4d5e', '').h === FOE_HULL.h);
  const tiled = THEME_NAMES.find((n) => Object.keys(THEME_PACKS[n].tiles || {}).length > 0); applyTheme(tiled, false);
  ok('a tiled pack keeps its own craft palette', !liveryOn() && friendPal('', '').h !== SKIN.livery.h);
  applyTheme('tactical', false); draw(2200);
  ok('the vector look draws no pixel tags', VEC === true && !liveryOn() && __plateAudit().wings.length === 0);
  applyTheme('classic', false); GB = true; let threw = false; try { draw(2300); } catch (e) { threw = true; out.push('  ' + e.stack); }
  ok('the mono palette draws them flat, without the livery', !threw && !liveryOn()); GB = false; draw(2400);
"""

DRIVER_RADIO = r"""
  VIEW = 'map';
  const calls = []; const realToast = toast; toast = (t, ms) => calls.push(t);
  ok('nothing calls on the first load', RADIO.cur === null && RADIO.q.length === 0);
  const areas = W.chars.filter((c) => c.kind === 'area' && c.path);
  const seat = (name, status, line) => ({ uid: 'seat:' + name, kind: 'seat', name: name, status: status, cwd: areas[0].path, line: line || '' });
  const world = (list) => Object.assign({}, W, { chars: W.chars.filter((c) => c.kind !== 'seat').concat(list), quests: W.quests || [] });
  applyWorld(world([seat('astra', 'WORKING', 'running the suite'), seat('jobs', 'IDLE'), seat('money', 'WORKING')]));
  ok('seats that first appear do not call', RADIO.cur === null);
  applyWorld(world([seat('astra', 'WAITING', 'Allow the migration? (y/n)'), seat('jobs', 'WORKING'), seat('money', 'WORKING')]));
  ok('a seat that starts to need you calls in, with its callsign and its own line', !!RADIO.cur && RADIO.cur.name === 'astra'
    && RADIO.cur.word === 'needs your answer' && RADIO.cur.line === 'Allow the migration? (y/n)');
  ok('...and a seat that only went to work does not', RADIO.q.length === 0);
  applyWorld(world([seat('astra', 'WAITING', 'Allow the migration? (y/n)'), seat('jobs', 'IDLE'), seat('money', 'DEAD')]));
  ok('finished and down queue behind it, one call at a time', RADIO.cur.name === 'astra' && RADIO.q.map((c) => c.name + ':' + c.word).join() === 'jobs:finished,money:stopped');
  radioNext(); ok('the next call takes the box', RADIO.cur.name === 'jobs' && RADIO.cur.line === '' && RADIO.q.length === 1);
  radioNext(); radioNext(); ok('the queue empties and the box goes', RADIO.cur === null);
  MOB = true; applyWorld(world([seat('astra', 'WORKING'), seat('jobs', 'WAITING', 'proceed?'), seat('money', 'DEAD')]));
  ok('on a phone the toast says it', calls.some((t) => t.indexOf('JOBS') === 0 && t.indexOf('needs you') > 0 && t.indexOf('proceed?') > 0));
  MOB = false; toast = realToast; radioNext(); radioNext();
"""


class FleetPageTests(unittest.TestCase):
    """The page script in test_game's stub DOM."""

    @classmethod
    def setUpClass(cls):
        if not shutil.which("node"):
            raise unittest.SkipTest("node not installed")
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name, "work")
        for name in ("Alpha", "Beta", "Gamma", "Delta", "Epsilon"):
            (root / name).mkdir(parents=True)
            (root / name / "notes.md").write_text("fixture\n", encoding="utf-8")
        proc = render_fixture(cls.tmp.name)
        if proc.returncode:
            raise AssertionError(proc.stderr[-2000:])
        cls.js = Path(cls.tmp.name, "page.js")
        page_js(Path(cls.tmp.name, "page.html"), cls.js)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def drive(self, body, env_extra=None):
        run_page_driver(self, self.js, body, None, env_extra)

    def test_the_keyboard_flies_the_pilot_with_momentum_bank_boost_brake_roll_and_u_turn(self):
        self.drive(DRIVER_FLY)

    def test_reduced_motion_applies_the_moves_at_once(self):
        self.drive(DRIVER_REDUCED, {"HARNESS_REDUCED": "1"})

    def test_each_seat_carries_its_letter_tag_in_the_plate_layout(self):
        self.drive(DRIVER_TAGS, {"HARNESS_STAGE": "1600x900"})

    def test_the_radio_calls_on_real_changes_only_one_at_a_time(self):
        self.drive(DRIVER_RADIO)


if __name__ == "__main__":
    unittest.main()
