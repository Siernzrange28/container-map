"""test_tactical_map.py — the map in the tactical (vector) look.

The page script runs in test_game's stub DOM on a fixture world rendered with theme tactical. The drivers read
window.__vecAudit (what the vector look drew last frame) and the page's own state: the tilted plane is one
projection (every planet back on its orbit after unproject, pickAt hits it where it is drawn), no pixel-art
draw call runs while VEC is on, threats get a hatched zone and an edge chevron off screen, the boot plays once
per tab session and never under reduced motion, a merge raises the intertitle, and every other theme keeps
the pixel look with no tilt.
"""
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

import testenv  # noqa: F401  (first: clears what steers the tool)
from test_game import page_js, render_fixture, run_page_driver

ROOT = Path(__file__).resolve().parent
SRC = (ROOT / "render-game.py").read_text(encoding="utf-8")
STAGE = {"HARNESS_STAGE": "1600x900"}

# a fixture world, the map on screen, a few frames drawn; vtF(n) draws n more frames
HEAD = r"""
  VIEW = 'map';
  let vtT = 2000; const vtF = (n) => { for (let i = 0; i < (n || 1); i++) { vtT += 16; draw(vtT); } };
    vtF(3);
"""

PLANE_DRIVER = HEAD + r"""
  ok('tactical is the vector look', VEC === true && document.documentElement.getAttribute('data-render') === 'vector');
  const a = __vecAudit();
  ok('the plane is tilted by 0.62', a.tilt === 0.62 && LY.tilt === 0.62);
  ok('the quiet overview draws no orbit ring', a.orbits.length === 0);
  setQuiet(false); vtF(2); const ao = __vecAudit(); setQuiet(true); vtF(1);
  ok('orbits are ellipses (ry/rx = 0.62), in the full view', ao.orbits.length >= 3 && ao.orbits.every((o) => Math.abs(o.ry / o.rx - 0.62) < 1e-9));
  ok('no pixel planet cells or pixel sprites while VEC is on', a.pixel === 0 && a.reroute === 0);
  ok('every planet sits on its orbit after unproject (one projection)', a.planets.length >= 6
    && a.planets.every((p) => Math.abs(p.plane - p.ring) < 0.5));
  ok('positions are squashed, the planets are not', MOONS.every((m) => Math.abs(unproject(m.x, m.y).y - m.y / 0.62) < 1e-6));
  ok('project and unproject are inverses', [[300, -800], [-1200, 450], [0, 0]].every(([x, y]) => {
    const p = project(x, y), q = unproject(p.x, p.y); return Math.abs(q.x - x) < 1e-9 && Math.abs(q.y - y) < 1e-9; }));
  ok('every planet is drawn where it is stored', a.drawn.length === MOONS.length && a.drawn.every((d) => {
    const m = MOONS.find((x) => x.uid === d.uid); return Math.abs(d.x / ZOOM + camx - m.x) < 1e-6 && Math.abs(d.y / ZOOM + camy - m.y) < 1e-6; }));
  ok('pickAt hits each planet at its drawn centre', a.drawn.every((d) => { const h = pickAt(d.x / ZOOM + camx, d.y / ZOOM + camy); return !!h && h.uid === d.uid; }));
  ok('a finger tap picks through the same projection', a.drawn.every((d) => { const h = pickAt(d.x / ZOOM + camx + 3 / ZOOM, d.y / ZOOM + camy, TAP_PX / ZOOM); return !!h && h.uid === d.uid; }));
  const before = fitZoom(1600, 900); ok('the home fit uses the plane (wider than tall)', before > 0.2 && before <= 1);
"""

THREAT_DRIVER = HEAD + r"""
  W.tasks = { items: [1, 2, 3].map((i) => ({ id: 'q' + i, area: 'Alpha', finding: 'stale receipt ' + i })) };
  place(); vtF(2);
  const m = MOONS.find((x) => x.label === 'Alpha');
  ok('the fixture planet has its problems', !!m && m.enemies === 3);
  ok('on the overview its zone is not drawn (detail of the open planet)', !__vecAudit().threats.some((x) => x.uid === m.uid));
  openUnit = unitByUid(m.uid); vtF(2);   // the planet opened: its focus view
  let a = __vecAudit(), th = a.threats.find((x) => x.uid === m.uid);
  ok('a planet with problems lies in a hatched zone with its triangles', !!th && th.targets === 3 && Math.abs(th.ry / th.rx - FOE_SLANT) < 1e-9);
  ok('no chevron while it is on screen', !a.chevrons.some((c) => c.uid === m.uid));
  camOff.x = (m.x - px) - 4000; camOff.y = (m.y - py) - 3000; vtF(2);
  a = __vecAudit(); const c = a.chevrons.find((x) => x.uid === m.uid), v = a.view, p = a.planets.find((x) => x.uid === m.uid);
  ok('off screen it gets an edge chevron inside the rails', !!c && c.x > v.x0 && c.x < v.x1 && c.y > v.y0 && c.y < v.y1 && v.y0 >= 44 && v.y1 <= VH - 48);
  ok('the chevron points at it', !!c && Math.abs(c.angle - Math.atan2(p.y - (v.y0 + v.y1) / 2, p.x - (v.x0 + v.x1) / 2)) < 1e-9);
  ok('the plates keep off the chevron', OVERLAY_CACHE && plateObstacles().some((o) => o.id === 'vec-chevron'));
  camOff.x = 0; camOff.y = 0; openUnit = null; vtF(2);
"""

BOOT_DRIVER = r"""
  VIEW = 'today'; draw(1000);
  ok('the boot waits while Today covers the map', BOOT.on === false && BOOT.done === false);
  VIEW = 'map'; draw(1100);
  if (EXPECT === 'play') {
    ok('the boot plays the first time the map is shown', BOOT.on === true && sessionStorage.getItem('vecboot') === '1');
    draw(1100 + 600); ok('it is still playing at 600 ms', BOOT.on === true);
    draw(1100 + BOOT_END + 5); ok('it is over before 2 s', BOOT.on === false && BOOT.reason === 'played' && BOOT_END < 2000);
  } else if (EXPECT === 'skip') {
    ok('the boot plays', BOOT.on === true);
    (__WL.keydown || []).forEach((f) => f({ key: 'x', target: null, preventDefault() {}, stopPropagation() {} }));
    ok('any key skips it', BOOT.on === false && BOOT.reason === 'skipped');
  } else if (EXPECT === 'session') {
    ok('a second load in the same tab session does not boot', BOOT.on === false && BOOT.done === true && BOOT.reason === 'once per session');
  } else {
    ok('no boot under prefers-reduced-motion', REDUCED === true && BOOT.on === false && BOOT.done === true && BOOT.reason === 'reduced motion');
  }
"""

MERGE_DRIVER = HEAD + r"""
  W.agent_tasks = [{ id: 't1', title: 'docs index rebuild', status: 'review', target: '/x', runner: 'codex', base_branch: 'main' }];
  fxMergeBanner('docs index rebuild'); vtF(1);
  ok('a merge raises the intertitle and names the branch it went into', !!FX.toast && FX.toast.into === 'MAIN');
  FX.toast = null; W.agent_tasks = [];
  fxMergeBanner('a thread with no branch'); vtF(1);
  ok('with no branch known it says your branch', !!FX.toast && FX.toast.into === 'YOUR BRANCH');
  draw(vtT + 2500); ok('it is gone after 2.4 s', FX.toast === null);
  ok('the stamp is the dossier stamp', /^(SUN|MON|TUE|WED|THU|FRI|SAT) [0-9]{2} [A-Z]{3} [0-9]{4} . [0-9]{4} HRS$/.test(vecStamp('·')));
"""

CLASSIC_DRIVER = HEAD + r"""
  applyTheme('classic', false); vtF(3);
  ok('classic is the pixel look', VEC === false && document.documentElement.getAttribute('data-render') === 'pixel');
  ok('classic has no tilt: every planet on a flat ring', LY.tilt === 1 && MOONS.every((m) => { const A = layoutAspect(); return Math.abs(Math.hypot(m.x / A[0], m.y / A[1]) - (m.orbit + (m.decay || 0) * 160)) < 0.5; })
    && MOONS.every((m) => Math.abs(m.orbit / LY.rootOrbit - 1) <= LAYOUT_SPREAD + 1e-3));
  ok('project is the identity without the tilt', [[300, -800], [-1200, 450]].every(([x, y]) => { const p = project(x, y); return p.x === x && p.y === y; }));
  ok('the vector audit stays quiet in classic', VEC_PIX === 0);
  applyTheme('tactical', false); vtF(2);
  ok('back to tactical lays the plane out again', LY.tilt === 0.62 && __vecAudit().planets.every((p) => Math.abs(p.plane - p.ring) < 0.5));
"""


class TacticalMapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which("node"):
            raise unittest.SkipTest("node not installed")
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name, "work")
        for name in ("Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta", "Eta", "Theta"):
            (root / name).mkdir(parents=True)
            (root / name / "notes.md").write_text("fixture\n", encoding="utf-8")
        proc = render_fixture(cls.tmp.name, theme="tactical")
        if proc.returncode:
            raise AssertionError(proc.stderr[-2000:])
        cls.js = Path(cls.tmp.name, "page.js")
        page_js(Path(cls.tmp.name, "page.html"), cls.js)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def drive(self, body, env_extra=None):
        run_page_driver(self, self.js, body, env_extra=dict(STAGE, **(env_extra or {})))

    def test_the_plane_is_one_projection_and_hits_land_where_drawn(self):
        self.drive(PLANE_DRIVER)

    def test_threats_get_a_zone_and_an_edge_chevron(self):
        self.drive(THREAT_DRIVER)

    def test_the_boot_plays_once_skips_on_a_key_and_never_under_reduced_motion(self):
        self.drive("const EXPECT = 'play';" + BOOT_DRIVER)
        self.drive("const EXPECT = 'skip';" + BOOT_DRIVER)
        self.drive("const EXPECT = 'session';" + BOOT_DRIVER, {"HARNESS_SESSION": json.dumps({"vecboot": "1"})})
        self.drive("const EXPECT = 'reduced';" + BOOT_DRIVER, {"HARNESS_REDUCED": "1"})

    def test_a_merge_raises_the_intertitle(self):
        self.drive(MERGE_DRIVER)

    def test_classic_keeps_the_flat_pixel_map(self):
        self.drive(CLASSIC_DRIVER)


class TacticalMapSourceTests(unittest.TestCase):
    """Everything the vector look adds sits behind VEC; the projection is one pair, applied in the layout."""

    def test_the_vector_look_is_gated(self):
        for gate in ("if(VEC){ vecSystem(t,moons,list,starName,quests,oldest); return; }",
                     "if(VEC){ vecIntertitle(t); return; }", "if(VEC){ vecTagPlate(o); return; }",
                     "if(VEC){ vecFocus(t); return; }", "    vecTargets(m,t); return; }",
                     "if(VEC){ vecMoonBody(m,t,seed,cold,empty,isHome); vecPlanetTag(m,t,cold); return; }", "if(VEC) vecSpace(t);",
                     "if(VEC) vecBootDraw(t);", "if(VEC) vecScreen(t);"):
            self.assertIn(gate, SRC)
        # the classic paths are still there, untouched
        for kept in ("drawNebula(t);", "drawStars(t); }", "drawStar(t, starName, quests, oldest);", "drawMergeBanner(t);"):
            self.assertIn(kept, SRC)

    def test_one_projection_pair_used_by_the_layout(self):
        self.assertEqual(len(re.findall(r"function project\(", SRC)), 1)
        self.assertEqual(len(re.findall(r"function unproject\(", SRC)), 1)
        self.assertIn("LY.tilt=VEC?TILT_K:1;", SRC)
        body = SRC.split("function layoutSystem(")[1].split("\nfunction ")[0]
        self.assertGreaterEqual(body.count("project("), 6, "the layout projects every system-level position")


if __name__ == "__main__":
    unittest.main()
