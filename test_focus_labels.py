#!/usr/bin/env python3
"""test_focus_labels.py — the overview and the focus view, and the names on the map (-2.3, 2.5, 2.8 and
): the focus predicate and the detail gates with hysteresis, the focus camera and its way back, the label slots
in the webglite order with the fold to a pip, names stroked then filled, the urgency orbit, the named edge arrows, the idle
agents stacked at the carrier, the phone's fit-all view and the machines kept off the 2D map.

Run: python3 -m unittest -q test_focus_labels   (node needed for the page half; else those tests skip)
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import testenv  # noqa: F401  (first: clears what steers the tool)
import test_game

ROOT = Path(__file__).resolve().parent


def write_tree(root, files):
    root.mkdir(parents=True, exist_ok=True)
    for name, size in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x" * size, encoding="utf-8")


class FocusLabelsSource(unittest.TestCase):
    """The template and the lane's files, read as text."""

    @classmethod
    def setUpClass(cls):
        cls.src = (ROOT / "render-game.py").read_text(encoding="utf-8")
        cls.labels = (ROOT / "ui" / "labels.js").read_text(encoding="utf-8")
        cls.focus = (ROOT / "ui" / "focus.js").read_text(encoding="utf-8")

    def test_the_lane_owns_its_names(self):
        for name in ("LABEL.place=function(items,view)", "CM.labelText=function(uid)", "LABEL.paint=function(g,p)",
                     "function labelsRun()"):
            self.assertIn(name, self.labels)
        for name in ("CM.focusUid=function()", "CM.inFocus=function(m)", "CM.detailHidden=function(u)",
                     "const FOCUS_CAM=", "window.__mapAudit=function()", "function fcEdgePlan()", "function fcGate(key,frac)"):
            self.assertIn(name, self.focus)
        self.assertIn("SLOTS:['own','+1','-1','right','left']", self.labels)
        # names are stroked, then filled
        text = self.labels[self.labels.index("LABEL.text=function"):]
        self.assertLess(text.index("strokeText"), text.index("fillText"))

    def test_the_overview_draws_no_wedges_rings_routes_or_machines(self):
        body = self.src[self.src.index("function drawSystem(t, moons, list, starName, quests, oldest){"):]
        body = body[:body.index("\n}\n")]
        for gone in ("LY.wedges", "drawFlows(", "drawPlanet(", "setLineDash", "LY.planets"):
            self.assertNotIn(gone, body)
        moon = self.src[self.src.index("function drawMoon(m,t){"):]
        moon = moon[:moon.index("\n}\n")]
        self.assertNotIn("pixelRing(", moon)            # the room rings went with the moons: stations draw in focus only

    def test_the_hysteresis_and_the_focus_numbers(self):
        self.assertIn("ON:.005, OFF:.004, FADE:300", self.focus)
        self.assertIn("C.dur=REDUCED?0:800", self.focus)
        self.assertIn("d=Math.min(.45*F.h,.6*F.w)", self.focus)

    def test_the_seams_stay_as_they_were(self):
        for line in ("MOONS.forEach(m=>{ if(CM.inFocus(m)) drawBattle(m,t); });", "function foePos(m,i,t){",
                     "function drawMoonBody(m,t){"):
            self.assertEqual(self.src.count(line), 1, line)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class FocusLabelsPage(unittest.TestCase):
    """The page half, in the stub DOM on a 1200x856 stage."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        work = Path(cls.tmp.name, "work")
        write_tree(work / "Alpha", {"a.py": 300, "src/b.py": 300, "src/c.py": 30, "docs/d.md": 30, "tests/t.py": 30,
                                    ".cache/x.bin": 30})
        for name in ("Beta", "Gamma", "Delta", "Epsilon", "Zeta", "_Eta"):
            write_tree(work / name, {"a.md": 200, "b.py": 100})
        run = test_game.render_fixture(cls.tmp.name)
        if run.returncode:
            raise AssertionError(run.stderr[-2000:])
        cls.js = Path(cls.tmp.name, "page.js")
        test_game.page_js(Path(cls.tmp.name, "page.html"), cls.js)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def drive(self, body, **env):
        extra = {"HARNESS_STAGE": "1200x856"}
        extra.update(env)
        test_game.run_page_driver(self, self.js, PRELUDE + body, env_extra=extra)

    def test_the_slots_fill_in_the_webglite_order_then_fold(self):
        self.drive(r"""
  // the spec's own check: six labels wanting one slot
  const b = { x: 800, y: 450, r: 20 }, r = LABEL.place(['a', 'b', 'c', 'd', 'e', 'f'].map((u, i) => Object.assign({ uid: u, text: 'Name ' + i, pri: 6 - i }, b)), { w: 1600, h: 900 });
  ok('slot-order', r.map((q) => q.slot).join() === 'own,+1,-1,right,left,pip' && r[0].y > b.y && r[1].y > r[0].y && r[2].y < b.y && r[3].x > b.x && r[4].x < b.x);
  ok('own sits r+6 under the body, -1 r+6 over it, the sides r+6 beside it', r[0].gap === 6 && r[2].gap === 6 && r[3].gap === 6 && r[4].gap === 6
    && Math.abs(r[0].y - (b.y + b.r + 6)) < 1e-9 && Math.abs(r[2].y + r[2].h - (b.y - b.r - 6)) < 1e-9);
  ok('+1 is one label row lower: own + its height + 2', Math.abs(r[1].y - (r[0].y + r[0].h + 2)) < 1e-9);
  ok('placed in pri order, highest first', LABEL.place([{ uid: 'lo', text: 'x', pri: 1, x: 100, y: 100, r: 10 }, { uid: 'hi', text: 'x', pri: 9, x: 100, y: 100, r: 10 }], { w: 400, h: 400 })
    .map((q) => q.uid + ':' + q.slot).join() === 'hi:own,lo:+1');
  // a name never covers another body, a box or the edge of the view
  const p = LABEL.place([{ uid: 'a', text: 'alpha', pri: 1, x: 200, y: 200, r: 10 }], { w: 400, h: 400, discs: [{ x: 200, y: 232, r: 12 }] });
  ok('a body under the own slot sends the name to the next free one', p[0].slot === '-1');
  const q = LABEL.place([{ uid: 'a', text: 'alpha', pri: 1, x: 200, y: 386, r: 10 }], { w: 400, h: 400 });
  ok('...and the edge of the view does too', q[0].slot === '-1');
  const k = LABEL.place([{ uid: 'a', text: 'alpha', pri: 1, x: 200, y: 200, r: 10 }], { w: 400, h: 400, boxes: [{ x: 150, y: 210, w: 100, h: 30 }] });
  ok('...and a page box', k[0].slot === '-1');
  // a ship parked on the body does not block its own name
  ok('what sits on the body never blocks its name', LABEL.place([{ uid: 's', text: 'This machine', pri: 1, x: 200, y: 200, r: 40 }], { w: 400, h: 400, discs: [{ x: 200, y: 230, r: 20 }] })[0].slot === 'own');
  // the memory: last frame's slot stays while it is free; a better free one takes over after 1.5 s
  const mem = new Map([['a', { slot: '-1', since: null }]]), it = [{ uid: 'a', text: 'alpha', pri: 1, x: 200, y: 200, r: 10 }];
  const m1 = LABEL.place(it, { w: 400, h: 400, mem: mem, now: 1000 });
  ok('a remembered free slot is kept', m1[0].slot === '-1' && m1[0].since === 1000);
  mem.set('a', { slot: '-1', since: 1000 });
  ok('...for 1.5 s while the better one stays free', LABEL.place(it, { w: 400, h: 400, mem: mem, now: 2400 })[0].slot === '-1'
    && LABEL.place(it, { w: 400, h: 400, mem: mem, now: 2600 })[0].slot === 'own');
  ok('a remembered slot that is taken is given up at once', LABEL.place(it, { w: 400, h: 400, mem: new Map([['a', { slot: '-1', since: null }]]), now: 1, boxes: [{ x: 150, y: 150, w: 100, h: 40 }] })[0].slot === 'own');
  ok('preferred slots: a station names itself on its right first', LABEL.place([{ uid: 'd', text: 'logs', pri: 1, x: 200, y: 200, r: 10, slots: ['right', 'left', 'own'] }], { w: 400, h: 400 })[0].slot === 'right');
""")

    def test_the_overview_is_quiet_and_every_name_sits_in_a_slot(self):
        self.drive(r"""
  home();
  const a = __mapAudit(), P = __plateAudit().plates;
  ok('overview-quiet', a.focus === null && a.drawn.moons === 0 && a.drawn.foes === 0 && a.drawn.wedges === 0 && a.drawn.rings === 0 && a.drawn.ribbons === 0);
  ok('no moon, job or process is drawn and none is a target', units.filter((u) => u.kind === 'dir').every((u) => CM.detailHidden(u) && pickAt(u.gx * TILE, u.gy * TILE) !== u));
  ok('all-in-free', a.planets.length === MOONS.length && a.planets.every((p) => p.inFree));
  ok('every planet is named in a slot', a.planets.every((p) => p.label === 'full'));
  ok('slots-drawn', P.length > 0 && P.every((p) => ['own', '+1', '-1', 'right', 'left'].includes(p.slot) && p.stroke >= 3));
  ok('an own slot is 6 px from its body', P.filter((p) => p.slot === 'own' && p.kind === 'tag').every((p) => Math.abs(p.gap - 6) <= 1));
  ok('no-overlap', P.every((A, i) => P.every((B, j) => j <= i || A.x + A.w <= B.x || B.x + B.w <= A.x || A.y + A.h <= B.y || B.y + B.h <= A.y)));
  ok('names are 15 px Barlow Condensed on a desktop', P.filter((p) => p.kind === 'tag').every((p) => p.px === 15));
  ok('a leading underscore is dropped from the name', CM.labelText(MOONS.find((m) => m.label === '_Eta').uid) === 'Eta');
  ok('the sun says This machine', CM.labelText(BOARD.uid) === 'This machine' && P.some((p) => p.uid === BOARD.uid && p.text.startsWith('This machine')));
  ok('a body is drawn at least 24 px across', a.planets.every((p) => p.r >= 12 - 1e-9));
  ok('the other machines are not drawn on the 2D map', a.drawn.machines === 0);
""")

    def test_the_label_chip_rides_in_the_planet_label(self):
        self.drive(r"""
  home();
  const uid = MOONS[0].uid; CM.labelChip = (u) => (u === uid ? 'Fix labels · 2 of 4' : ''); frames(3);
  const P = __plateAudit().plates, p = P.find((x) => x.uid === uid);
  ok('chip-in-label', !!p && p.text.includes('2 of 4') && p.h > 30);
  ok('...and no-overlap holds', P.every((A, i) => P.every((B, j) => j <= i || A.x + A.w <= B.x || B.x + B.w <= A.x || A.y + A.h <= B.y || B.y + B.h <= A.y)));
  CM.labelChip = () => '';
""")

    def test_focus_follows_the_card_and_its_camera_comes_back(self):
        self.drive(r"""
  home();
  const m = MOONS.find((x) => x.label === 'Alpha'), z0 = ZOOM, c0 = { x: px + camOff.x, y: py + camOff.y };
  ok('no focus on the overview', CM.focusUid() === null && !CM.inFocus(m));
  const ship = { x: px, y: py };
  activate(unitByUid(m.uid)); frames(1); ok('activate opens the planet: it is the focus', CM.focusUid() === m.uid && CM.inFocus(m));
  ok('a planet opened on the map moves the camera, not the ship', !pilot && px === ship.x && py === ship.y);
  frames(4, 100); const mid = ZOOM;
  frames(8, 100);
  const a = __mapAudit(), F = a.free, s = { x: (m.x - camx) * ZOOM, y: (m.y - camy) * ZOOM }, d = 2 * fcDispR(m) * ZOOM;
  ok('the camera eases in 0.8 s (it was half way at 0.4 s)', mid > z0 && mid < ZOOM && FOCUS_CAM.phase === 'hold');
  ok('the planet sits in the middle of the free map', Math.abs(s.x - (F.x + F.w / 2)) < 2 && Math.abs(s.y - (F.y + F.h / 2)) < 2);
  ok('...at about 45% of its height', Math.abs(d / F.h - .45) < .02 || Math.abs(d / F.w - .6) < .02);
  const rooms = m.rooms.filter((r) => !r.hidden && !/^[.]/.test(r.name)).length;
  ok('focus: its sub-folders are stations, a dot-folder never', a.drawn.moons === rooms && rooms >= 3 && !a.stations.some((x) => /cache/.test(x.uid)));
  ok('a station is a target where it is drawn', a.stations.every((x) => { const u = unitByUid(x.uid), w = { x: camx + x.x / ZOOM, y: camy + x.y / ZOOM }; return pickAt(w.x, w.y) === u; }));
  ok('every other planet is dimmed with its name hidden', a.planets.filter((p) => p.uid !== m.uid).every((p) => p.label === 'hidden' || p.label === 'off'));
  ok('the sun off the map is a named edge arrow', a.arrows.some((x) => x.uid === BOARD.uid && x.name === 'This machine') || a.planets.length === 0);
  // Esc (closeAll) leaves the focus and the camera eases back to the view it left
  closeAll(); frames(8, 100);
  ok('restore', CM.focusUid() === null && Math.abs(ZOOM - z0) < 1e-9 && Math.abs(px + camOff.x - c0.x) < 1e-6 && Math.abs(py + camOff.y - c0.y) < 1e-6);
  ok('...and the overview is quiet again', __mapAudit().drawn.moons === 0 && __mapAudit().drawn.foes === 0);
  // the owner takes the camera (the wheel): the focus stays, Esc still comes back
  activate(unitByUid(m.uid)); frames(10, 100); setZoom(ZOOM * 1.3); frames(2, 100);
  ok('a wheel turn in focus zooms past 2 and lets the focus camera go', FOCUS_CAM.phase === 'user' && CM.focusUid() === m.uid);
  closeAll(); frames(8, 100);
  ok('...Esc brings the overview back all the same', Math.abs(ZOOM - z0) < 1e-9);
""")

    def test_problems_ride_one_orbit_oldest_nearest_the_sun(self):
        self.drive(r"""
  const m = MOONS.find((x) => x.label === 'Alpha');
  const cs = []; for (let i = 0; i < 11; i++) cs.push({ id: 'cron:job' + i, source: 'cron', state: 'stale', title: 'job backup' + i + ': late', age_days: (i * 7) % 11,
    tier: 'aging', planet_uid: m.uid, since: null, open: null, voice: '' });
  cs.push({ id: 'task:r1', source: 'task', state: 'review', title: 'Rotate the deploy key', age_days: 2, planet_uid: m.uid, since: null, open: null, voice: '' });
  W.sky = Object.assign({}, W.sky || {}, { contacts: cs, error: undefined }); place();
  home(); activate(unitByUid(m.uid)); frames(12, 100);
  const a = __mapAudit(), f = a.foes.slice().sort((x, y) => y.age - x.age);
  ok('eight problems at most, the rest a count', a.drawn.foes === 8 && a.drawn.more >= 3);
  ok('urgency-orbit', f.every((x, i) => i === 0 || f[i - 1].sunDist <= x.sunDist));
  ok('the oldest of all is the one nearest the sun', a.foes[0].age === Math.max(...cs.filter((c) => c.state === 'stale').map((c) => c.age_days)));
  ok('one orbit: every craft at the same distance from the planet', (() => { const c = { x: (m.x - camx) * ZOOM, y: (m.y - camy) * ZOOM }, d = a.foes.map((x) => Math.hypot(x.x - c.x, x.y - c.y));
    return Math.max(...d) - Math.min(...d) < 1; })());
  ok('a review waits as parked cargo on the same orbit, after the problems', a.waiting.length === 1 && a.waiting[0].kind === 'cargo'
    && a.waiting[0].sunDist >= Math.max(...a.foes.map((x) => x.sunDist)));
  const tags = __plateAudit().plates.filter((p) => /^f[tw]:/.test(p.key));
  ok('each craft carries a tag of two to four words', tags.length === 9 && tags.every((p) => { const n = p.text.split('·')[0].trim().split(/\s+/).length; return n >= 1 && n <= 4; }));
  ok('a job that is late says so by name', tags.some((p) => /^backup\d+ late/.test(p.text)));
""")

    def test_detail_switches_with_hysteresis_and_fades(self):
        self.drive(r"""
  home(); const m = MOONS.find((x) => x.label === 'Alpha'); activate(unitByUid(m.uid)); frames(12, 100);
  ok('the open planet shows its detail', FC.alpha.get(m.uid) === 1);
  const A = (VW - PANEL_W) * (VH - plateSheet()), zt = Math.sqrt(FC.ON * A / Math.PI) / (m.r * FC.SCALE);
  ZOOM = zt * 1.01; frames(1, 16); __mapAudit.reset();
  for (let i = 0; i < 10; i++) { ZOOM = zt * .97; frames(1, 16); ZOOM = zt * 1.03; frames(1, 16); }
  ok('no-flicker', __mapAudit().detailToggles <= 1);
  ZOOM = zt * .85; frames(1, 16);
  ok('well under the off threshold it switches off once', __mapAudit().detailToggles === 1);
  const a0 = FC.alpha.get(m.uid); frames(1, 100);
  ok('...and fades over 0.3 s instead of popping', a0 < 1 && a0 > 0 && FC.alpha.get(m.uid) < a0);
  frames(4, 100); ok('...to nothing', FC.alpha.get(m.uid) === 0 && __mapAudit().drawn.moons === 0);
  closeAll(); frames(8, 100);
""")

    def test_idle_agents_stack_at_the_carrier(self):
        self.drive(r"""
  const area = W.chars.find((c) => c.kind === 'area' && c.label !== 'x' && c.path);
  const seat = (name, status, cwd) => ({ uid: 'seat:' + name, kind: 'seat', name: name, status: status, cwd: cwd || '', line: name + ' at work', seat: name });
  W.chars = W.chars.filter((c) => c.kind !== 'seat').concat([seat('hub', 'IDLE'), seat('mail', 'IDLE', area.path), seat('astra', 'IDLE'), seat('kite', 'WORKING', area.path + '/src')]);
  place(); home();
  const M = mothership(), P = __plateAudit().plates, idle = units.filter((u) => u.kind === 'seat' && u.status === 'IDLE' && u !== M);
  ok('the idle agents are stacked at the carrier, not drawn one by one', idle.length === 2 && idle.every((u) => CM.detailHidden(u)) && !CM.detailHidden(M));
  const c = P.find((p) => p.key === 'c:' + M.uid);
  ok('the carrier says "N idle" in its own slot under it', !!c && c.text === '2 idle' && c.slot === 'own');
  ok('...and no planet name lies on it', P.filter((p) => p.kind === 'tag').every((p) => p.x + p.w <= c.x || c.x + c.w <= p.x || p.y + p.h <= c.y || c.y + c.h <= p.y));
  ok('a working agent stays at its planet', !CM.detailHidden(units.find((u) => u.uid === 'seat:kite')));
  activate(unitByUid(area.uid)); frames(12, 100);
  const a = __mapAudit();
  ok('in focus every agent of the planet is named on its ring', a.agents.length === 1 && /kite/.test(a.agents[0].tag) && FC.mine.has('seat:kite'));
  closeAll(); frames(8, 100);
""")

    def test_one_crumb_chip_two_writers_and_the_route_dims_other_names(self):
        self.drive(r"""
  home(); const m = MOONS.find((x) => x.label === 'Alpha');
  ok('CM.crumb is the one crumb (the 2D workflows module writes the workflow one, the focus view the planet one)', typeof CM.crumb === 'function');
  CM.crumb(['All planets', 'Workflow · Ship the login page']);
  ok('a workflow crumb shows while no planet is open', FC.crumbs.other.join('|') === 'All planets|Workflow · Ship the login page' && FC.crumbs.focus === null);
  activate(unitByUid(m.uid)); frames(2, 100);
  ok('a planet in focus takes the chip', FC.crumbs.focus.join('|') === 'All planets|Alpha' && FC.crumbs.other !== null);
  closeAll(); frames(8, 100);
  ok('...and gives it back', FC.crumbs.focus === null && FC.crumbs.other.length === 2);
  CM.crumb(null); ok('null hides it', FC.crumbs.other === null);
  // a selected workflow: the names off its route drop to the dimmed style
  const wf = { id: 'w-1', title: 'Fix labels', state: 'working', steps: [{ key: 's1', state: 'working', planet_uid: m.uid }] };
  const list = CM.wfList, sel = CM.selectedWorkflow; CM.wfList = () => [wf]; CM.selectedWorkflow = () => 'w-1'; frames(2);
  const L = FC.lbOut.all, on = L.find((p) => p.uid === m.uid), off = L.filter((p) => p.item.kind === 'planet' && p.uid !== m.uid);
  ok('the route planet keeps its full name', !!on && on.item.alpha !== .4 && on.slot !== 'pip');
  ok('every other name is dimmed', off.length > 0 && off.every((p) => p.item.alpha === .4 && p.item.dim === true));
  CM.wfList = list; CM.selectedWorkflow = sel; frames(2);
  ok('...and back when nothing is selected', FC.lbOut.all.filter((p) => p.item.kind === 'planet').every((p) => !p.item.dim));
""")

    def test_off_map_bodies_get_a_named_edge_arrow(self):
        self.drive(r"""
  home(); const m = MOONS[0];
  camOff.x += 9000; frames(2, 16);
  const a = __mapAudit();
  ok('a planet off the map gets an arrow with its name and no label', a.arrows.some((x) => x.uid === m.uid && x.name === CM.labelText(m.uid))
    && !__plateAudit().plates.some((p) => p.uid === m.uid));
  ok('the arrows sit inside the free map', a.arrows.every((x) => x.x >= a.free.x && x.x <= a.free.x + a.free.w && x.y >= a.free.y && x.y <= a.free.y + a.free.h));
""")


@unittest.skipUnless(shutil.which("node"), "node not installed")
class FocusLabelsPhone(unittest.TestCase):
    """A 390x844 phone: the fit-all home view (it replaces the fixed 0.22), every body 34 px, every name 13 px."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        work = Path(cls.tmp.name, "work")
        for i, name in enumerate(("Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta", "Eta", "Theta", "Iota", "Kappa",
                                  "Lambda", "Mu", "Nu")):
            write_tree(work / name, {"a.md": 100 * (i + 1), "b.py": 50})
        run = test_game.render_fixture(cls.tmp.name)
        if run.returncode:
            raise AssertionError(run.stderr[-2000:])
        cls.js = Path(cls.tmp.name, "page.js")
        test_game.page_js(Path(cls.tmp.name, "page.html"), cls.js)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_the_phone_fits_the_whole_system(self):
        test_game.run_page_driver(self, self.js, PRELUDE + r"""
  ok('a phone', MOB === true);
  home();
  const a = __mapAudit(), P = __plateAudit().plates;
  ok('all thirteen planets on the screen, none under a box', a.planets.length === 13 && a.planets.every((p) => p.inFree));
  ok('all-in-free, the sun too', (() => { const s = a.bodyOf(BOARD.uid); return s.x - s.r >= 0 && s.y - s.r >= 0 && s.x + s.r <= VW && s.y + s.r <= VH; })());
  ok('every body at least 34 px across', a.planets.every((p) => p.r >= 17 - 1e-9));
  ok('every name 13 px', P.filter((p) => p.kind === 'tag').length >= 13 && P.filter((p) => p.kind === 'tag').every((p) => p.px === 13));
  ok('zoomed out past the old fixed 0.22', ZOOM < 0.22 && homeZoom() === ZOOM);
""", env_extra={"HARNESS_MOBILE": "1", "HARNESS_STAGE": "390x788"})


# the driver's own helpers: draw frames on a clock of its own, open the map at the home view
PRELUDE = r"""
  let NOW = 1000; performance.now = () => NOW;
  const frames = (n, dt) => { for (let i = 0; i < n; i++) { NOW += dt || 16; draw(NOW); } };
  const home = () => { VIEW = 'map'; deck = 'home'; fit(); HOME.on = true; HOME.laid = false; homeNote(); homeSettle(); frames(3); };
"""


if __name__ == "__main__":
    unittest.main()
