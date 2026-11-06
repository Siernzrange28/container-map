#!/usr/bin/env python3
"""test_seams.py — the seams the lanes build on (2026-10-12): the CM registry and its defaults, the hook lines, the
ui/*.js and ui/*.css inliners, WORDS, cmWorld(), the 3D module bundle, the seed stubs, and the promise that the
seed changed nothing a person can see: on the bench, the plates (key, text, x, y) equal those of the commit the
seams were cut from, at 1600x900 and on a 390x844 phone, and the world differs from that commit's only by the two
new keys (counts, workflows).

The comparison with the base commit needs git history, bench.py (not shipped), node and a Chrome; without them it
is skipped. A lane that changes the plates or the world on purpose moves these two checks in its own commit.

Run: python3 -m unittest -q test_seams
"""
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from pathlib import Path

import testenv  # noqa: F401  (first: clears what steers the tool)
import test_game

ROOT = Path(__file__).resolve().parent
BASE = "c89af9f9d24a3dcc47839aa0a5ab84af18641c2c"      # the commit the seams were cut at
UI_JS = ("words", "kinds", "labels", "focus", "chrome", "phone", "workflows2d", "constellations", "a11y", "term")
UI_CSS = ("chrome", "phone", "labels", "workflows2d", "hud3d", "a11y", "term")
CM_DEFAULTS = ("constellations", "counts", "word", "labelText", "inFocus", "detailHidden", "focusUid", "drawWorkflows2D", "onTasks",
               "dockOverride", "action", "stripSet", "railCard", "backLayer", "key3d", "mode3d", "frame3d", "onInit",
               # defaults that change nothing
               "settingsExtra", "drawBackdrop2D", "nebulaRecipe", "haptic", "sceneClock",
               "wfList", "showList", "selectedWorkflow", "selectWorkflow", "workingPlanets", "wfRows", "labelChip",
               "enter3d", "backLayers")
# the workflow states the probes and the lanes test with, beside FIX (workflow-demo.json)
VARIANTS = ("FIX_DONE", "FIX_FAILED", "FIX_PLANNED", "FIX_GATE", "FIX_MERGE", "FIX_ART", "FIX_CHECK", "FIX_SHOWS", "BIG")
FIXTURES = ROOT / "tests" / "fixtures"
# the hook lines: frozen, a lane implements the CM function behind one and never edits the line
HOOKS = (
    ("MOONS.forEach(m=>{ if(CM.inFocus(m)) drawBattle(m,t); });", 1),
    ("if(SKY_STAR.enemies&&skyOn()&&CM.inFocus(SKY_STAR)){", 1),
    ("if(CM.detailHidden(u)) return;", 2),
    ("CM.drawWorkflows2D(t);", 1),
    ("CM.drawBackdrop2D(cx);", 1),
    ("CM.onTasks(j);", 1),
    ("const o=CM.dockOverride(u), a=o?{uid:o}:areasByPath.find(", 1),
    ("if(!['visit','compose'].includes(a.action)&&CM.action(a)){activeAction=null;return;}", 1),
    ("if(e.key==='Escape'&&CM.backLayer('esc')) return;", 1),
    ("+(CM.backLayer('has')?1:0);", 1),
    ("if(CM.backLayer('back')) return;", 1),
    ("addEventListener('keydown',e=>{ if(!typing()&&!modKey(e)&&CM.key3d(e)){ e.preventDefault(); e.stopImmediatePropagation(); } },true);", 1),
    ("if(CM.mode3d()){ pumpActions(); CM.frame3d(t,dt); FRAME_T=t; wantFrame(); return; }", 1),
    ("if(CM.mode3d()){ lastPaint=t; step(t); return; }", 1),
    ("if(!typing()&&!CM.mode3d())location.reload();", 1),
    ("CM.onInit.forEach(f=>{ try{ f(); }catch(e){ console.error('CM.onInit', e); } });", 1),
    ("function drawMoonBody(m,t){", 1),
    ("function foePos(m,i,t){", 1),
    ("function cmWorld(){", 1),
)


def main_script(html):
    return re.search(r"<script>(.*)</script>", html, re.S).group(1)


def main_style(html):
    return re.search(r"<style>(.*?)</style>", html, re.S).group(1)


class SeamSource(unittest.TestCase):
    """The template and the lanes' files, read as text."""

    @classmethod
    def setUpClass(cls):
        cls.src = (ROOT / "render-game.py").read_text(encoding="utf-8")

    def test_every_hook_line_is_there_once_as_written(self):
        for line, count in HOOKS:
            with self.subTest(line=line[:60]):
                self.assertEqual(self.src.count(line), count)

    def test_the_registry_follows_the_world_line_with_every_default(self):
        at = self.src.index("const W = __WORLD__;\nconst WORDS = __WORDS__;\n")
        block = self.src[self.src.index("const CM={\n", at):]
        block = block[:block.index("\n};\n")]
        keys = re.findall(r"^  ([A-Za-z0-9]+)(?::|\()", block, re.M)
        self.assertEqual(keys, list(CM_DEFAULTS))
        self.assertLess(self.src.index("};\n/*__CM_UI__*/\n", at), self.src.index("function joinRooms(w){"))

    def test_the_backdrop_hook_is_the_first_thing_drawn_after_the_background(self):
        body = self.src[self.src.index("function draw(t){"):]
        body = body[:body.index("\n}\n")]
        at = body.index("  CM.drawBackdrop2D(cx);\n")
        self.assertEqual(body.index("cx.fillStyle=T.bg; cx.fillRect(0,0,VW,VH);\n") + len(
            "cx.fillStyle=T.bg; cx.fillRect(0,0,VW,VH);\n"), at)
        for later in ("if(VEC) vecSpace(t);", "drawNebula(t);", "drawStars(t);", "cx.save(); cx.scale(ZOOM,ZOOM);"):
            self.assertLess(at, body.index(later), later)

    def test_the_data_defaults_are_the_ones_the_lanes_were_promised(self):
        block = self.src[self.src.index("const CM={\n"):]
        block = block[:block.index("\n};\n")]
        for line in ("  onTasks(j){ if(j&&Array.isArray(j.workflows)) W.workflows=j.workflows; if(j&&Array.isArray(j.shows)) W.shows=j.shows; },",
                     "  backLayer:k=>CM.backLayers.slice().sort((a,b)=>b.pri-a.pri).some(l=>{ try{ return !!l.fn(k); }catch(e){ return false; } }),",
                     "  wfList:()=>W.workflows||[],", "  showList:()=>W.shows||[],",
                     "  selectedWorkflow:()=>CM._wfSel||null,", "  selectWorkflow(id){ CM._wfSel=id||null; cmSceneTouch(); },",
                     "  labelChip:uid=>'',", "  enter3d(opts){},", "  backLayers:[],",
                     "  settingsExtra:[],", "  drawBackdrop2D(){},", "  nebulaRecipe:()=>null,", "  haptic(){},",
                     "  sceneClock:{scale:1,t:0},"):
            with self.subTest(line=line[:50]):
                self.assertIn(line + "\n", block + "\n")

    def test_the_css_marker_closes_the_main_stylesheet(self):
        self.assertIn("/*__CM_CSS__*/\n</style><style id=\"cm-tac\">__TACCSS__", self.src)

    def test_the_lanes_files_exist_and_hold_no_placeholder(self):
        for name in UI_JS:
            self.assertTrue((ROOT / "ui" / (name + ".js")).is_file(), name)
        for name in UI_CSS:
            self.assertTrue((ROOT / "ui" / (name + ".css")).is_file(), name)
        for path in sorted((ROOT / "ui").iterdir()) + sorted((ROOT / "world3d").iterdir()):
            with self.subTest(path=path.name):
                self.assertIsNone(re.search(r"__[A-Z0-9_]+__", path.read_text(encoding="utf-8")))

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_every_ui_and_world3d_script_passes_node_check(self):
        import vendor3d
        files = sorted((ROOT / "ui").glob("*.js")) + sorted((ROOT / "world3d").glob("*.js"))
        self.assertEqual(len(files), len(UI_JS) + len(vendor3d.ORDER))
        for path in files:
            with self.subTest(path=f"{path.parent.name}/{path.name}"):
                run = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True, timeout=60)
                self.assertEqual(run.returncode, 0, run.stderr)


class SeamPage(unittest.TestCase):
    """A rendered page: the parts in order, one main script, WORDS, and the defaults at work in the stub DOM."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name, "work")
        for name in ("Alpha", "Beta"):
            (root / name).mkdir(parents=True)
            (root / name / "notes.md").write_text("fixture\n", encoding="utf-8")
        proc = test_game.render_fixture(cls.tmp.name)
        if proc.returncode:
            raise AssertionError(proc.stderr[-2000:])
        cls.page = Path(cls.tmp.name, "page.html")
        cls.html = cls.page.read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_the_ui_scripts_go_in_after_the_registry_in_their_order(self):
        js = main_script(self.html)
        at = [js.index(f"/* ui/{name}.js */\n") for name in UI_JS]
        self.assertEqual(at, sorted(at))
        self.assertLess(js.index("const CM={"), at[0])
        self.assertLess(at[-1], js.index("function cmWorld(){"))
        self.assertLess(js.index("function cmWorld(){"), js.index("function joinRooms(w){"))

    def test_the_ui_styles_close_the_main_sheet_in_their_order(self):
        css = main_style(self.html)
        at = [css.index(f"/* ui/{name}.css */\n") for name in UI_CSS]
        self.assertEqual(at, sorted(at))
        self.assertNotIn("/* ui/", self.html[self.html.index('<style id="cm-tac">'):self.html.index("<script")])

    def test_the_ui_styles_go_through_the_theme_rewriter_unchanged(self):
        sys.path.insert(0, str(ROOT))
        import themepacks as tp
        style = re.sub(r'html\[data-theme="[a-z0-9_-]+"\]\{[^}]*\}', "", main_style(self.html)).replace(tp.THEME_CSS, "")
        undone = test_game.undo_variables(style)
        expected = "".join(f"/* ui/{name}.css */\n" + test_game.normal_hex((ROOT / "ui" / (name + ".css")).read_text(
            encoding="utf-8")).rstrip("\n") + "\n" for name in UI_CSS)
        self.assertEqual(undone[undone.index("/* ui/chrome.css */\n"):], expected + "\n")   # the template's own newline

    def test_one_main_script_and_no_marker_left(self):
        self.assertEqual(self.html.count("<script>"), 1)
        self.assertEqual(re.findall(r"<script\b[^>]*>", self.html),
                         ['<script id="theme-early">', '<script id="mobile-early">', '<script id="cm-sfx">', "<script>"])
        for marker in ("/*__CM_UI__*/", "/*__CM_CSS__*/", "__WORDS__"):
            self.assertNotIn(marker, self.html)

    def test_words_is_the_table_of_words_py(self):
        sys.path.insert(0, str(ROOT))
        import words
        line = re.search(r"(?m)^const WORDS = (.*);$", main_script(self.html)).group(1)
        self.assertEqual(json.loads(line), words.WORDS)

    def test_the_world_carries_counts_and_workflows(self):
        world = json.loads(re.search(r"(?m)^const W = (.*);$", self.html).group(1))
        self.assertEqual(world["workflows"], [])
        self.assertEqual(sorted(world["counts"]), sorted(("need_you", "answer", "review", "decisions", "problems",
                                                          "working", "on_hold", "done_today", "oldest_decision",
                                                          "total")))

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_the_defaults_change_nothing_and_cmworld_reads_the_page(self):
        js = Path(self.tmp.name, "page.js")
        test_game.page_js(self.page, js)
        test_game.run_page_driver(self, js, r"""
  // the 3D module has its start-up in CM.onInit (its settings rows, its back layer at 30, the ',' key-up), and implements
  // key3d (I enters 3D), mode3d, frame3d and enter3d; (ui/kinds.js) registers its start-up (the backdrop bake, the
  // purpose sheet's Esc layer at 25). Other lanes push their own start-up, rows and layers into the same lists when they
  // merge, so these read the lanes' entries and never pin a list's length.
  ok('CM is the registry', typeof CM === 'object' && Array.isArray(CM.onInit) && CM.onInit.length >= 1
    && CM.onInit.every((f) => typeof f === 'function'));
  ok('counts are the world counts', CM.counts() === W.counts && W.counts.total === 0);
  ok('word: the table, else the key (never a prototype member)', CM.word('in warp') === 'working' && CM.word('zz') === 'zz'
    && CM.word('constructor') === 'constructor' && CM.word('toString') === 'toString');
  ok('labelText names a planet by its label', MOONS.length > 0 && CM.labelText(MOONS[0].uid) === MOONS[0].label);
  // implements the three (ui/focus.js): no card open, no focus; a planet's moons are the focus view's to draw
  ok('no focus, so no planet is in focus, and a moon is detail', CM.inFocus(MOONS[0]) === false && CM.detailHidden({ kind: 'dir' }) === true && CM.focusUid() === null);
  ok('no dock override, no action taken, no layer, no 2D key taken but I, no 3D', CM.dockOverride({}) === null && CM.action({ action: 'point' }) === false
    && CM.backLayer('has') === false && CM.backLayer('back') === false && CM.key3d({ key: 'q' }) === false && CM.key3d({ key: 'v' }) === false
    && CM.mode3d() === false);
  ok('the drawing hooks are no-ops', CM.drawWorkflows2D(1000) === undefined && CM.onTasks({ tasks: [] }) === undefined
    && CM.frame3d(1000, 0.016) === undefined && CM.stripSet('route', 'x') === undefined && CM.railCard('planet', {}) === undefined);
  // the seam defaults: unused, they change nothing
  // implements the sky recipe (this machine's seed, two layers at most) and the backdrop hook (no bake in a stub DOM)
  ok('seam defaults: the 3D view\'s three settings rows, the backdrop hook draws nothing in a stub DOM, the planet kinds module\'s sky recipe, no haptic, the world clock at 1',
    Array.isArray(CM.settingsExtra) && ['render-scale', 'fps-cap', 'antialias'].every((k) => CM.settingsExtra.filter((r) => r.key === k).length === 1)
    && CM.drawBackdrop2D(cx) === undefined
    && CM.nebulaRecipe() !== null && Number.isInteger(CM.nebulaRecipe().seed) && CM.nebulaRecipe().layers.length <= 2 && CM.haptic('done') === undefined
    && JSON.stringify(CM.sceneClock) === JSON.stringify({ scale: 1, t: 0 }));
  ok('seam data defaults: no workflows, no shows, nothing selected, no working planet, no rows, no chip, no layers',
    CM.wfList().length === 0 && CM.showList().length === 0 && CM.selectedWorkflow() === null
    && CM.workingPlanets() instanceof Set && CM.workingPlanets().size === 0 && CM.wfRows().length === 0
    && CM.labelChip(MOONS[0].uid) === '' && typeof CM.enter3d === 'function' && Array.isArray(CM.backLayers)
    && CM.backLayers.filter((l) => l.pri === 30).length === 1 && CM.backLayers.filter((l) => l.pri === 25).length === 1
    && CM.backLayers.filter((l) => l.pri === 40).length === 1 && CM.backLayers.filter((l) => l.pri === 20).length === 1
    && CM.backLayers.filter((l) => l.pri === 10).length === 1 && CM.backLayers.every((l) => [40, 30, 25, 20, 10].includes(l.pri))
    && CM.backLayer('has') === false && CM.backLayer('esc') === false);   // the planet kinds module's purpose sheet (25), the 3D view (30), the phone module's phone sheet (20), the 2D workflows module's workflow layer (10) and an agent's page (40), all closed
  const w = cmWorld();
  ok('cmWorld v1: the scene contract', w.v === 1 && w.rev >= 1 && Array.isArray(w.light2d) && w.light2d.length === 3
    && ['moons', 'foes', 'waiting', 'machines', 'shows', 'workflows'].every((k) => Array.isArray(w[k]) && w[k].length === 0));
  ok('cmWorld: the real star, planets and ship', w.star.r === LY.star.r && w.planets.length === MOONS.length
    && w.planets.every((p, i) => p.uid === MOONS[i].uid && p.r === MOONS[i].r && typeof p.purpose === 'string' && p.counts)
    && w.ship.x === px && w.ship.y === py && w.counts === W.counts);
  ok('cmWorld rev holds while nothing changed', cmWorld().rev === w.rev);
  ok('foePos: where the first raider is on its orbit', (() => { const m = { x: 10, y: 20, r: 40, enemies: 3, uid: 'area:x' };
    const p = foePos(m, 0, 0), a = seedOf('area:x') % 7;
    return Math.abs(p.a - a) < 1e-12 && Math.abs(p.x - (10 + Math.cos(a) * 66)) < 1e-9 && Math.abs(p.y - (20 + Math.sin(a) * 66 * .78)) < 1e-9; })());
  ok('a frame draws with the hooks in', (() => { try { draw(1200); return true; } catch (e) { return false; } })());
""")

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_rev_moves_with_the_data_and_the_hooks_see_only_their_part(self):
        js = Path(self.tmp.name, "page.js")
        test_game.page_js(self.page, js)
        test_game.run_page_driver(self, js, r"""
  // rev: the data of a /state or /tasks reply, never the minute it was built in
  const base = () => Object.assign({}, W, { chars: W.chars, quests: W.quests || [] });
  const r0 = cmWorld().rev, gen = String(W.generated || '');
  applyWorld(base()); ok('the same /state again holds rev', cmWorld().rev === r0);
  applyWorld(Object.assign(base(), { generated: 'Sat Oct 10, 2:00 AM' })); ok('a new build time alone holds rev', cmWorld().rev === r0);
  const one = { id: 'ledger:L-1', source: 'ledger', state: 'blocked', title: 'a parked call', since: null, age_days: 0, tier: 'fresh',
    planet_uid: MOONS[0].uid, open: null, voice: 'a parked call' };
  const sky = (contacts) => Object.assign({}, W.sky, { contacts: contacts, error: undefined });
  applyWorld(Object.assign(base(), { generated: gen, sky: sky([one]) }));
  ok('a contact that arrives in the same minute moves rev once', cmWorld().rev === r0 + 1 && skyContacts().length === 1);
  applyWorld(Object.assign(base(), { generated: gen, sky: sky([one]) })); ok('...and the same contacts hold it', cmWorld().rev === r0 + 1);
  applyWorld(Object.assign(base(), { generated: gen, sky: sky([Object.assign({}, one, { state: 'failed' })]) }));
  ok('a contact whose state changed moves rev', cmWorld().rev === r0 + 2);
  const wf = [{ id: 'w-20261012-0700-fix-a1b2c3', title: 'Fix the phone labels', state: 'working', steps: [{ key: 's1', state: 'working' }] }];
  const onTasks = CM.onTasks; CM.onTasks = (j) => { if (Array.isArray(j.workflows)) W.workflows = j.workflows; };
  const tasks = (W.agent_tasks || []).slice();
  applyTasks({ tasks: tasks, workflows: wf }); ok('workflows that ride /tasks move rev', cmWorld().rev === r0 + 3 && cmWorld().workflows === wf);
  applyTasks({ tasks: tasks, workflows: wf }); ok('...and the same workflows hold it', cmWorld().rev === r0 + 3);
  applyTasks({ tasks: tasks, workflows: [Object.assign({}, wf[0], { steps: [{ key: 's1', state: 'done' }] })] });
  ok('a step that moved on moves rev', cmWorld().rev === r0 + 4);
  CM.onTasks = onTasks;
  applyTasks({ tasks: tasks.concat([T('t9', 'running')]) }); ok('a new thread moves rev', cmWorld().rev === r0 + 5);
  TASKS_AT = 0; applyWorld(Object.assign(base(), { generated: gen, agent_tasks: tasks.concat([T('t9', 'review')]) }));
  ok('a /state reply that brings threads moves rev once', cmWorld().rev === r0 + 6 && agentTasks().some((t) => t.id === 't9' && t.status === 'review'));
  ok('cmWorld alone never moves rev', cmWorld().rev === r0 + 6 && cmWorld().rev === r0 + 6);
  // pumpActions: the lanes' action hook is asked only for the types the page does not know
  const asked = []; const action = CM.action; CM.action = (a) => { asked.push(a.action); return true; };
  const pump = (a) => { pilot = null; activeAction = null; visitHoldUntil = 0; actionQueue.length = 0; actionQueue.push(a); pumpActions(); };
  pump({ action: 'visit', target: MOONS[0].uid }); const flew = !!pilot;
  pump({ action: 'compose', target: MOONS[0].uid, text: 'hello' });
  pump({ action: 'point', target: MOONS[0].uid, text: 'here', seconds: 20 });
  ok('visit and compose stay the page\'s; an unknown type goes to CM.action', flew && asked.join() === 'point' && activeAction === null);
  CM.action = action; pilot = null; activeAction = null;
  // Escape: the lanes' layers close first, on Today as on the map, and are asked once
  const fire = (key) => { const e = { key: key, repeat: false, ctrlKey: false, metaKey: false, altKey: false, shiftKey: false,
    preventDefault() {}, stopPropagation() {}, stopImmediatePropagation() {}, getModifierState() { return false; }, target: { blur() {} } };
    (__WL.keydown || []).forEach((f) => f(e)); };
  document.activeElement = null;
  const layers = [], layer = { pri: 10, fn: (how) => { layers.push(how); return how === 'esc'; } };
  CM.backLayers.push(layer);
  VIEW = 'today'; fire('Escape'); const today = layers.join();
  layers.length = 0; VIEW = 'map'; fire('Escape');
  ok('Esc reaches CM.backLayer on Today and on the map, once each', today === 'esc' && layers.join() === 'esc');
  CM.backLayers.splice(CM.backLayers.indexOf(layer), 1); VIEW = 'today';
  ok('with the default, Esc on Today changes nothing', (() => { try { fire('Escape'); return VIEW === 'today'; } catch (e) { return false; } })());
""")


    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_the_workflow_defaults_carry_fix(self):
        """The /tasks reply's workflows and shows land in W through CM.onTasks, and every lane reads them
        through CM: wfList, workingPlanets, wfRows, the selection; backLayer asks the pushed layers, highest pri first."""
        if not (FIXTURES / "workflow-demo.json").is_file():
            self.skipTest("no tests/fixtures in this tree")
        fix = (FIXTURES / "workflow-demo.json").read_text(encoding="utf-8")
        js = Path(self.tmp.name, "page.js")
        test_game.page_js(self.page, js)
        test_game.run_page_driver(self, js, "  const FIX = " + fix.strip() + ";\n" + r"""
  const r0 = cmWorld().rev;
  ok('FIX through applyTasks', (() => { applyTasks({tasks:[],workflows:[FIX]});return CM.wfList()[0].id===FIX.id&&CM.workingPlanets().has('area:ops'); })());
  ok('...it is W.workflows and it moved rev once', W.workflows[0] === FIX && cmWorld().rev === r0 + 1);
  ok('working planets: the working steps only', [...CM.workingPlanets()].join() === 'area:ops');
  const row = CM.wfRows()[0];
  // the seed's eight keys first, as promised; adds what a Today row draws beyond them (stops, progress, night, by, updated)
  const base8 = ['id', 'title', 'done', 'total', 'state', 'place', 'sentence', 'step'];
  ok('wfRows: one plain row, no id or path in its words', JSON.stringify(Object.fromEntries(base8.map((k) => [k, row[k]]))) === JSON.stringify({ id: FIX.id,
    title: 'Fix the phone labels', done: 1, total: 4, state: 'working', place: 'ops', sentence: '', step: 2 })
    && Object.keys(row).slice(0, 8).join() === base8.join() && row.stops.length === 4 && row.stops.map((x) => x.place).join() === 'api,ops,ops,docs'
    && !/w-2026|\//.test(JSON.stringify(row.stops)));
  applyTasks({ tasks: [], shows: [{ id: 's-1', by: 'Claude Code', planet_uid: 'area:ops', ops: [] }] });
  ok('shows land in W.shows, the workflows stay', CM.showList().length === 1 && CM.wfList()[0] === FIX && cmWorld().rev === r0 + 2);
  applyTasks({ tasks: [], workflows: 'nonsense', shows: null });
  ok('a reply without arrays changes nothing', CM.wfList()[0] === FIX && CM.showList().length === 1 && cmWorld().rev === r0 + 2);
  CM.selectWorkflow(FIX.id); const r1 = cmWorld().rev;
  CM.selectWorkflow(FIX.id);
  ok('selectWorkflow selects and moves rev once', CM.selectedWorkflow() === FIX.id && r1 === r0 + 3 && cmWorld().rev === r1);
  CM.selectWorkflow(null); ok('null clears it', CM.selectedWorkflow() === null && cmWorld().rev === r0 + 4);
  // a thread at work in a planet's folder makes that planet working too
  const at = unitByUid(MOONS[0].uid).path;
  applyTasks({ tasks: [T('t1', 'running', { target: at + '/sub' }), T('t2', 'review', { target: unitByUid(MOONS[1].uid).path })] });
  ok('a running thread\'s planet is working, a thread to review is not', CM.workingPlanets().has(MOONS[0].uid)
    && !CM.workingPlanets().has(MOONS[1].uid) && CM.workingPlanets().has('area:ops'));
  // backLayer: the highest pri whose fn says true answers; a fn that throws is false
  const asked = [];
  CM.backLayers.push({ pri: 10, fn: (k) => { asked.push('wf:' + k); return true; } },
                     { pri: 30, fn: (k) => { asked.push('3d:' + k); return k === 'esc'; } },
                     { pri: 20, fn: () => { asked.push('sheet'); throw new Error('x'); } });
  ok('esc: the 3D layer answers first', CM.backLayer('esc') === true && asked.join() === '3d:esc');
  asked.length = 0;
  ok('back: 3D says no, the throwing sheet counts as no, the workflow layer answers', CM.backLayer('back') === true
    && asked.join() === '3d:back,sheet,wf:back');
  CM.backLayers.length = 0;
  ok('no layer: false again', CM.backLayer('has') === false);
""")


class SeamInliner(unittest.TestCase):
    """The inliners on a copy of the tool: a placeholder in a part stops the render, "</script" never closes the
    script, a missing part is skipped with a warning."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.tool = Path(cls.tmp.name, "tool")
        shutil.copytree(ROOT, cls.tool, ignore=shutil.ignore_patterns(".*", "__pycache__", "dist", "build", "*.png",
                                                                       "*.egg-info", "tests"))
        cls.work = Path(cls.tmp.name, "work")
        (cls.work / "Alpha").mkdir(parents=True)
        (cls.work / "Alpha" / "notes.md").write_text("fixture\n", encoding="utf-8")
        cfg = test_game.fixture_config(cls.tmp.name)
        # no .pyc: a swapped module put back within the same second at the same size would load the swap's cache
        cls.env = dict(os.environ, CONTAINER_CONFIG=str(cfg), CONTAINER_OUT=str(Path(cls.tmp.name, "page.html")),
                       PYTHONDONTWRITEBYTECODE="1")
        cls.env.pop("BRIDGE_PORT", None)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def render(self):
        return subprocess.run([sys.executable, str(self.tool / "render-game.py")], cwd=self.tool, env=self.env,
                              capture_output=True, text=True, timeout=120)

    def swap(self, rel, text):
        path = self.tool / rel
        old = path.read_text(encoding="utf-8")
        path.write_text(text, encoding="utf-8")
        self.addCleanup(path.write_text, old, encoding="utf-8")
        return old

    def test_a_placeholder_in_a_ui_script_is_refused(self):
        old = (self.tool / "ui" / "a11y.js").read_text(encoding="utf-8")
        self.swap("ui/a11y.js", old + "CM.x = '__WORLD__';\n")
        run = self.render()
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("ui/a11y.js holds the placeholder __WORLD__", run.stderr)

    def test_a_placeholder_in_a_ui_stylesheet_is_refused(self):
        old = (self.tool / "ui" / "hud3d.css").read_text(encoding="utf-8")
        self.swap("ui/hud3d.css", old + "/* __THEME__ */\n")
        run = self.render()
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("ui/hud3d.css holds the placeholder __THEME__", run.stderr)

    def test_a_placeholder_in_the_words_is_refused(self):
        old = (self.tool / "words.py").read_text(encoding="utf-8")
        self.swap("words.py", old.replace('"in warp": "working",', '"in warp": "__SFX__",', 1))
        run = self.render()
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("words.py WORDS holds the placeholder __SFX__", run.stderr)

    def test_a_closing_tag_in_a_part_never_closes_its_element(self):
        js = (self.tool / "ui" / "words.js").read_text(encoding="utf-8")
        css = (self.tool / "ui" / "chrome.css").read_text(encoding="utf-8")
        self.swap("ui/words.js", js + "CM.closing = '</script><b>';\n")
        self.swap("ui/chrome.css", css + "/* </style> */\n")
        run = self.render()
        self.assertEqual(run.returncode, 0, run.stderr[-2000:])
        html = Path(self.tmp.name, "page.html").read_text(encoding="utf-8")
        self.assertIn("CM.closing = '<\\/script><b>';", html)
        self.assertIn("/* <\\/style> */", html)
        self.assertEqual(html.count("</script>"), 4)

    def test_counts_that_fail_leave_zero_counts_and_say_why(self):
        sys.path.insert(0, str(ROOT))
        import sky
        old = (self.tool / "sky.py").read_text(encoding="utf-8")
        self.swap("sky.py", old + "\n\ndef counts(sky=None, tasks=None, workflows=None):\n    raise KeyError('bucket')\n")
        run = self.render()
        self.assertEqual(run.returncode, 0, run.stderr[-2000:])
        self.assertIn("warn: counts not built: 'bucket'", run.stderr)
        html = Path(self.tmp.name, "page.html").read_text(encoding="utf-8")
        counts = json.loads(re.search(r"(?m)^const W = (.*);$", html).group(1))["counts"]
        self.assertEqual(counts.pop("error"), "counts could not be built: KeyError: 'bucket'")
        self.assertEqual(counts, sky.counts(sky.empty(), [], []))

    def test_a_missing_part_is_skipped_with_a_warning(self):
        path = self.tool / "ui" / "focus.js"
        old = path.read_text(encoding="utf-8")
        path.unlink()
        self.addCleanup(path.write_text, old, encoding="utf-8")
        run = self.render()
        self.assertEqual(run.returncode, 0, run.stderr[-2000:])
        self.assertIn("warn: ui/focus.js not read", run.stderr)
        html = Path(self.tmp.name, "page.html").read_text(encoding="utf-8")
        self.assertNotIn("/* ui/focus.js */", html)
        self.assertIn("/* ui/labels.js */", html)


class SeamStubs(unittest.TestCase):
    """What the seed promises the lanes before they land: names, shapes and refusals."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ROOT))

    def test_words(self):
        import words
        # the words module filled the stubs: a short title drops "Goal:" and stays within 48 characters (test_plain_words.py)
        self.assertEqual(words.short_title("Goal: tidy the imports\nmore"), "Tidy the imports")
        self.assertEqual(len(words.short_title("x" * 200)), 48)
        self.assertEqual(words.short_title(""), "")
        self.assertEqual(words.state_word("needs_you"), "needs you")
        self.assertEqual(words.state_word("nonsense"), "nonsense")
        self.assertEqual([words.rel_time(s) for s in (5, 300, 7200, 86400 * 3, 86400 * 35, None)],
                         ["just now", "5 min ago", "2 h ago", "3 days ago", "5 weeks ago", ""])
        self.assertEqual(words.wf_sentence({"title": "Fix the phone labels", "steps": []}), "")   # no steps, no sentence

    def test_workflows_contract(self):
        import workflows
        self.assertEqual(workflows.KINDS, ("note", "task", "gate"))
        self.assertEqual(workflows.STEP_STATES, ("waiting", "ready", "working", "needs_you", "done", "failed",
                                                 "skipped", "blocked"))
        self.assertTrue(workflows.ID_RE.match("w-20261012-0700-fix-the-phone-labels-a1b2c3"))
        self.assertFalse(workflows.ID_RE.match("w-20261012-0700-x-a1b2c3/../"))
        self.assertEqual(workflows.BOUNDS["steps"], 24)
        store = workflows.store_for({"work_root": "/srv/root", "workflows_dir": "", "workflow_artifact_mb": 32})
        self.assertEqual(store.workflows_dir, os.path.join("/srv/root", ".container-map", "workflows"))
        # the engine has landed (workflows module, test_workflows.py): an empty store lists and ticks nothing,
        # a bad spec is a plain 400, and the schema an agent reads names the bounds
        self.assertEqual((store.list(), store.tick()), ([], []))
        with self.assertRaises(workflows.WorkflowError) as ctx:
            store.create({})
        self.assertEqual(ctx.exception.status, 400)
        self.assertEqual(workflows.schema()["bounds"]["steps"], 24)
        err = workflows.WorkflowError(403, "wrong token")
        self.assertEqual((err.status, err.message), (403, "wrong token"))
        self.assertIn("sentence", workflows.SUMMARY_KEYS)                 # the workflow engine stores words.wf_sentence there
        self.assertEqual(workflows.SUMMARY_KEYS[-1], "steps")

    def test_sky_counts_and_config(self):
        import config
        import sky
        self.assertEqual(sky.COUNT_BUCKETS, ("decision", "answer", "problem", "review"))
        self.assertEqual(sky.counts(sky.empty(), [], [])["total"], 0)
        self.assertEqual((config.DEFAULTS["workflows_dir"], config.DEFAULTS["workflow_artifact_mb"],
                          config.DEFAULTS["three_inline"]), ("", 32, False))
        self.assertEqual(config.DEFAULTS["workflow_launches_per_day"], 40)   # the default
        keys = list(config.DEFAULTS)
        self.assertEqual(keys.index("workflow_launches_per_day"), keys.index("workflows_dir") + 1)

    def test_the_fixtures_read(self):
        folder = ROOT / "tests" / "fixtures"
        if not folder.is_dir():
            self.skipTest("no tests/fixtures in this tree")
        import workflows
        demo = json.loads((folder / "workflow-demo.json").read_text(encoding="utf-8"))
        self.assertEqual((demo["title"], demo["by"]["name"], demo["state"]), ("Fix the phone labels", "Claude Code", "working"))
        self.assertEqual(list(demo), list(workflows.SUMMARY_KEYS))
        self.assertEqual(demo["sentence"], "")
        self.assertEqual([s["planet_uid"] for s in demo["steps"]], ["area:api", "area:ops", "area:ops", "area:docs"])
        self.assertEqual((demo["steps"][1]["state"], demo["steps"][1]["progress"]), ("working", {"done": 120, "total": 800}))
        self.assertTrue(workflows.ID_RE.match(demo["id"]))
        spec = json.loads((folder / "workflow-spec.json").read_text(encoding="utf-8"))
        self.assertTrue(all(workflows.KEY_RE.match(s["key"]) and s["kind"] in workflows.KINDS for s in spec["steps"]))
        world = json.loads((folder / "world3d-demo.json").read_text(encoding="utf-8"))
        self.assertEqual((world["v"], len(world["planets"])), (1, 13))
        self.assertGreater(len(world["craft"]), 0)

    def test_the_workflow_variants(self):
        """tests/fixtures/workflow-variants.json, the states of FIX the lanes test with."""
        folder = ROOT / "tests" / "fixtures"
        if not folder.is_dir():
            self.skipTest("no tests/fixtures in this tree")
        import workflows
        fix = json.loads((folder / "workflow-demo.json").read_text(encoding="utf-8"))
        var = json.loads((folder / "workflow-variants.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(k for k in var if not k.startswith("_")), sorted(VARIANTS))
        states = lambda w: [s["state"] for s in w["steps"]]
        for name in VARIANTS:
            w = var[name]
            with self.subTest(variant=name):
                self.assertEqual(list(w)[:len(workflows.SUMMARY_KEYS)], list(workflows.SUMMARY_KEYS))
                self.assertIn(w["state"], workflows.WORKFLOW_STATES)
                self.assertEqual(w["total"], len(w["steps"]))
                self.assertEqual(w["done"], states(w).count("done"))
                for st in w["steps"]:
                    self.assertEqual(list(st)[:len(workflows.STEP_KEYS)], list(workflows.STEP_KEYS))
                    self.assertIn(st["state"], workflows.STEP_STATES)
                    self.assertIn(st["kind"], workflows.KINDS)
                    self.assertTrue(workflows.KEY_RE.match(st["key"]))
                self.assertEqual(w["current"], [s["key"] for s in w["steps"] if s["state"] in ("working", "needs_you")])
                if name != "BIG":                       # the FIX_* variants are FIX at another moment: same workflow
                    self.assertEqual((w["id"], w["title"], w["by"], [s["key"] for s in w["steps"]],
                                      [s["planet_uid"] for s in w["steps"]]),
                                     (fix["id"], fix["title"], fix["by"], [s["key"] for s in fix["steps"]],
                                      [s["planet_uid"] for s in fix["steps"]]))
        done = var["FIX_DONE"]
        self.assertEqual((done["state"], states(done), done["elapsed_s"], done["steps"][-1]["planet_uid"]),
                         ("done", ["done"] * 4, 840, "area:docs"))
        self.assertEqual([s.get("check", {}).get("verdict") for s in done["steps"]].count("PASS"), 1)
        self.assertEqual(done["updated"][:10], fix["updated"][:10])          # the same day as FIX (probe macros: today)
        failed = var["FIX_FAILED"]
        self.assertEqual((failed["state"], failed["steps"][2]["state"], failed["steps"][2]["why"]), ("failed", "failed", "tests failed"))
        self.assertEqual(var["FIX_PLANNED"]["state"], "planned")
        self.assertEqual(set(states(var["FIX_PLANNED"])), {"waiting"})
        gate = var["FIX_GATE"]["steps"][1]
        self.assertEqual((gate["kind"], gate["state"]), ("gate", "needs_you"))
        merge = var["FIX_MERGE"]["steps"][1]
        self.assertEqual((merge["kind"], merge["wait"], merge["state"], merge["why"]),
                         ("task", "merged", "needs_you", "accept changes to continue"))
        art = var["FIX_ART"]["steps"][0]
        self.assertEqual((art["state"], art["artifact"]), ("done", "report.md"))
        check = var["FIX_CHECK"]["steps"][1]
        self.assertEqual((check["kind"], check["state"], check["task_status"], check["check"]["state"]),
                         ("task", "working", "checking", "running"))
        big = var["BIG"]
        self.assertNotEqual(big["id"], fix["id"])
        self.assertTrue(workflows.ID_RE.match(big["id"]))
        self.assertEqual([s["planet_uid"] for s in big["steps"]],
                         ["area:api", "area:ops", "area:docs", "area:data", "area:site", "area:infra"])
        self.assertEqual(states(big)[2], "working")
        shows = var["FIX_SHOWS"]
        self.assertEqual({k: v for k, v in shows.items() if k != "shows"}, fix)
        self.assertEqual(len(shows["shows"]), 1)
        show = shows["shows"][0]
        self.assertEqual((show["by"], show["planet_uid"], sorted(op["op"] for op in show["ops"])),
                         ("Claude Code", "area:ops", ["card", "chart"]))
        self.assertTrue(all(0 <= a <= b for a, b in [op["bar"] for op in show["ops"] if op["op"] == "card"]))
        self.assertTrue(all(isinstance(v, (int, float)) for op in show["ops"] if op["op"] == "chart" for v in op["values"]))


class SeamBench(unittest.TestCase):
    """bench.py --owner-scale's media planet, exactly 30 direct sub-folders in a fixed order (not shipped)."""

    def test_the_media_planet(self):
        if not (ROOT / "bench.py").is_file():
            self.skipTest("bench.py is not shipped")
        sys.path.insert(0, str(ROOT))
        import bench
        rows = bench.media_layout()
        self.assertEqual(len(rows), 30)
        self.assertEqual((rows[0][0], rows[2][0], rows[5][0], rows[-1][0]), ("clips-01", "captions-03", "stats-06", "stats-30"))
        self.assertEqual(sum(r[1] for r in rows), 735)
        self.assertEqual(sum(r[1] for r in rows if r[0].startswith("clips-")), 500)
        self.assertEqual([r[1] for r in rows], sorted((r[1] for r in rows), reverse=True))   # most files first = k order
        belt = rows[12:]
        self.assertEqual([r[4] for r in belt].count(2.5), 6)
        self.assertEqual([r[4] for r in belt].count(18.5), 6)
        self.assertEqual([r[4] for r in belt].count(45.5), 6)
        with tempfile.TemporaryDirectory() as tmp:
            t0 = time.time()
            media = bench._media_planet(t0, tmp)
            dirs = sorted(os.listdir(media))
            self.assertEqual(sorted(r[0] for r in rows), dirs)
            deeper = [d for d in dirs for x in os.scandir(os.path.join(media, d)) if not x.is_file(follow_symlinks=False)]
            self.assertEqual(deeper, [])
            k13 = os.path.join(media, "clips-13")
            files = sorted(os.listdir(k13))
            self.assertEqual(len(files), 27)
            self.assertEqual({os.path.splitext(f)[1] for f in files}, {".png", ".jpg", ".mp4"})
            st = os.stat(os.path.join(k13, files[0]))
            self.assertEqual(st.st_size, 13000)
            self.assertAlmostEqual((t0 - st.st_mtime) / 86400, 18.5, places=3)


class SeamBundle(unittest.TestCase):
    """vendor3d.py joins world3d/*.js, core first, into one classic script every module registers on."""

    def test_the_bundle_order_and_name(self):
        sys.path.insert(0, str(ROOT))
        import vendor3d
        text = vendor3d.bundle(str(ROOT))
        at = [text.index(f"/* world3d/{name}.js */\n") for name in vendor3d.ORDER]
        self.assertEqual(at, sorted(at))
        self.assertEqual(vendor3d.ORDER[0], "core")
        self.assertEqual(sorted(vendor3d.ORDER), sorted(p.stem for p in (ROOT / "world3d").glob("*.js")))
        self.assertRegex(vendor3d.bundle_name(text, "neutral"), r"^cm3d-neutral-[0-9a-f]{8}\.js$")
        with self.assertRaises(ValueError):
            vendor3d.bundle_name(text, "../x")

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_every_module_registers(self):
        sys.path.insert(0, str(ROOT))
        import vendor3d
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "bundle.js")
            path.write_text(vendor3d.bundle(str(ROOT)), encoding="utf-8")
            run = subprocess.run(["node", "-e", f"require({json.dumps(str(path))}); "
                                  "console.log(JSON.stringify([CM3D.modules(),typeof CM3D.flightPhysics.step]))"],
                                 capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        # The rock data and pure flight integrator register no scene module.
        modules, flight = json.loads(run.stdout)
        self.assertEqual(modules, [n for n in vendor3d.ORDER[1:] if n not in ("rocks", "flight")])
        self.assertEqual(flight, "function")


# ---- the bench against the base commit ----
PLATES_RUNNER = r"""
const [port, url, width, height, mobile, now, init] = process.argv.slice(2);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let list;
for (let i = 0; i < 100 && !list; i++) {
  try { list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json(); } catch (e) { await sleep(100); }
}
const target = list.find((t) => t.type === 'page');
const ws = new WebSocket(target.webSocketDebuggerUrl);
const pending = new Map(); let id = 0, blocked = 0; const errors = [];
const send = (method, params = {}) => new Promise((res, rej) => { const i = ++id; pending.set(i, { res, rej }); ws.send(JSON.stringify({ id: i, method, params })); });
ws.onmessage = (m) => {
  const j = JSON.parse(m.data);
  if (j.id && pending.has(j.id)) { const p = pending.get(j.id); pending.delete(j.id); if (j.error) p.rej(new Error(JSON.stringify(j.error))); else p.res(j.result); }
  else if (j.method === 'Fetch.requestPaused') {
    if (/^(file|data|blob|about):/.test(j.params.request.url)) send('Fetch.continueRequest', { requestId: j.params.requestId });
    else { blocked++; send('Fetch.failRequest', { requestId: j.params.requestId, errorReason: 'BlockedByClient' }); }
  } else if (j.method === 'Runtime.exceptionThrown') errors.push(j.params.exceptionDetails.exception?.description || j.params.exceptionDetails.text);
};
await new Promise((r) => { ws.onopen = r; });
await send('Fetch.enable', { patterns: [{ urlPattern: '*' }] });
await send('Runtime.enable'); await send('Page.enable');
const mob = mobile === '1';
await send('Emulation.setDeviceMetricsOverride', { width: +width, height: +height, deviceScaleFactor: 1, mobile: mob });
if (mob) await send('Emulation.setTouchEmulationEnabled', { enabled: true });
const loaded = new Promise((r) => ws.addEventListener('message', (m) => { if (JSON.parse(m.data).method === 'Page.loadEventFired') r(); }));
// one clock for both pages: Date.now and performance.now are held, and the page's own frame loop never runs
await send('Page.addScriptToEvaluateOnNewDocument', { source: `try{ sessionStorage.clear(); localStorage.clear(); ${init || ''} }catch(e){}
  (function(){ var N=${+now}; Date.now=function(){ return N; }; performance.now=function(){ return 50000; };
    window.requestAnimationFrame=function(){ return 1; }; })();` });
await send('Page.navigate', { url });
await loaded; await sleep(800);
const r = await send('Runtime.evaluate', { returnByValue: true, awaitPromise: true, expression: `(async()=>{
  setView('map'); if(MOB) mobSync(); fit(); place();
  for(let i=0;i<4;i++) step(50000);
  const A=__plateAudit(); if(!A) return null;
  return {vw:A.vw, vh:A.vh, zoom:A.zoom, plates:A.plates.map(p=>({key:p.key,text:p.text,x:Math.round(p.x*100)/100,y:Math.round(p.y*100)/100,w:Math.round(p.w*100)/100,h:Math.round(p.h*100)/100}))};
})()` });
console.log('RESULT ' + JSON.stringify({ value: r.exceptionDetails ? null : r.result.value,
  exception: r.exceptionDetails ? (r.exceptionDetails.exception?.description || r.exceptionDetails.text) : null, blocked, errors: errors.slice(0, 3) }));
ws.close();
process.exit(0);
"""


def base_available():
    if not ((ROOT / "bench.py").is_file() and shutil.which("git")):
        return False
    run = subprocess.run(["git", "-C", str(ROOT), "cat-file", "-e", BASE + "^{commit}"], capture_output=True, timeout=30)
    return run.returncode == 0


class SeamsChangeNothing(unittest.TestCase):
    """The bench rendered by the base commit and by this tree: the same plates, the same world but for the new keys."""
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        if not base_available():
            raise unittest.SkipTest("needs bench.py and the base commit in git history (not in an exported tree)")
        cls.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.base = Path(cls.tmp.name, "base")
        cls.base.mkdir()
        tar = subprocess.run(["git", "-C", str(ROOT), "archive", "--format=tar", BASE], capture_output=True, timeout=120)
        if tar.returncode:
            raise AssertionError(tar.stderr.decode(errors="replace")[-2000:])
        with tarfile.open(fileobj=io.BytesIO(tar.stdout)) as archive:
            try:
                archive.extractall(cls.base, filter="data")
            except TypeError:                        # python without extraction filters
                archive.extractall(cls.base)
        sys.path.insert(0, str(ROOT))
        import bench
        cls.bench = bench
        cls.ws = Path(cls.tmp.name, "ws")
        cls.ws.mkdir()
        cls.cfg = bench.build_workspace(str(cls.ws), 1, [])
        # guesses Data and Media from the files (the base had no such kinds), which moves the bench's data and
        # design planets into sectors of their own: both trees are told those two are unsorted, so the plates and the
        # world still compare everything else (test_folder_kinds.py and the planet kinds module probe check the guesses themselves)
        areas_path = cls.ws / "areas.json"
        areas = json.loads(areas_path.read_text(encoding="utf-8"))
        areas["areas"].update({"data": {"kind": "unknown"}, "design": {"kind": "unknown"}})
        areas_path.write_text(json.dumps(areas), encoding="utf-8")
        cls.pages = {}
        for name, tool in (("base", cls.base), ("seed", ROOT)):
            env = bench.env_for(cls.cfg, str(cls.ws))
            env["CONTAINER_OUT"] = str(Path(cls.tmp.name, name + ".html"))
            run = subprocess.run([sys.executable, str(tool / "render-game.py")], cwd=tool, env=env,
                                 capture_output=True, text=True, timeout=300)
            if run.returncode:
                raise AssertionError(f"{name}: {run.stderr[-2000:]}")
            cls.pages[name] = env["CONTAINER_OUT"]

    @classmethod
    def tearDownClass(cls):
        hist = Path(cls.tmp.name, "ws", "hist")
        if hist.is_dir():
            os.chmod(hist, 0o755)                     # the bench makes it read-only
        cls.tmp.cleanup()

    def test_the_world_differs_only_by_the_new_keys(self):
        saved, result = Path(self.tmp.name, "base-world.json"), Path(self.tmp.name, "result.json")
        common = ["--workspace", str(self.ws), "--skip", "loop,bridge,frames", "--runs", "1"]
        run = subprocess.run([sys.executable, str(ROOT / "bench.py"), "--tool", str(self.base), "--save-world",
                              str(saved), *common], capture_output=True, text=True, timeout=600)
        self.assertEqual(run.returncode, 0, run.stdout[-1000:] + run.stderr[-2000:])
        run = subprocess.run([sys.executable, str(ROOT / "bench.py"), "--tool", str(ROOT), "--diff-world", str(saved),
                              "--json", str(result), *common], capture_output=True, text=True, timeout=600)
        res = json.loads(result.read_text(encoding="utf-8"))
        self.assertTrue(res["world_vs_saved"] and res["world_vs_saved"][0] != "same", run.stdout[-2000:])
        # every difference, not the bench's first 50: the new keys, and the words module's plain words (the words module: a job named
        # by its script with its schedule in words, a job's and a task's contact in words); nothing else moves
        mine = Path(self.tmp.name, "this-world.json")
        run = subprocess.run([sys.executable, str(ROOT / "bench.py"), "--tool", str(ROOT), "--save-world", str(mine),
                              *common], capture_output=True, text=True, timeout=600)
        self.assertEqual(run.returncode, 0, run.stdout[-1000:] + run.stderr[-2000:])
        saved_world = json.loads(saved.read_text(encoding="utf-8"))
        now_world = json.loads(mine.read_text(encoding="utf-8"))
        allowed = re.compile(r"world\.(?:counts|workflows|constellations|constellation_skipped|lane_planets): present on one side only"
                             r"|world\.chars\[\d+\]\.when: present on one side only"
                             r"|world\.chars\[\d+\]\.name: '[^']*\.sh' != '[^']*'"
                             r"|world\.chars\[\d+\]\.hall: '[^']*' != ''"       # an unhomed job has no layout slot now
                             r"|world\.quests\[\d+\]\.focus: '[^']*' != ''"      # the board's labels come from quest_priority now
                             r"|world\.sky\.contacts\[\d+\]\.(?:title|voice): .*"
                             r"|world\.today\.daily\[\d+\]\.lines.*")        # BREAKS counts the jobs that never ran
        diffs = self.bench.differences(saved_world, now_world, limit=100000)
        self.assertEqual([d for d in diffs if not allowed.fullmatch(d)], [])
        self.assertIn("world.counts: present on one side only", diffs)
        self.assertIn("world.constellations: present on one side only", diffs)
        self.assertIn("world.constellation_skipped: present on one side only", diffs)
        for d in diffs:
            m = re.fullmatch(r"world\.sky\.contacts\[(\d+)\]\.(title|voice): .*", d)
            if m:
                self.assertIn(now_world["sky"]["contacts"][int(m.group(1))]["source"], ("cron", "task"), d)

    def test_the_plates_name_what_the_base_commit_named_on_the_desktop_and_the_phone(self):
        chrome = test_game.find_chrome()
        if not (chrome and shutil.which("node")):
            self.skipTest("needs node and a Chrome or Chromium binary (set CHROME_BIN)")
        runner = Path(self.tmp.name, "plates.mjs")
        runner.write_text(PLATES_RUNNER, encoding="utf-8")
        profile = Path(self.tmp.name, "profile")
        browser = subprocess.Popen(
            [chrome, "--headless=new", "--no-sandbox", "--disable-gpu", "--remote-debugging-port=0",
             f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check", "--remote-allow-origins=*",
             "--allow-file-access-from-files", "--disable-background-networking", "--disable-component-update",
             "--no-pings", "--hide-scrollbars", "--font-render-hinting=none", "--host-resolver-rules=MAP * ~NOTFOUND",
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
            now = str(int(time.time() * 1000))
            for width, height, mobile in ((1600, 900, False), (390, 844, True)):
                got = {}
                for name, page in self.pages.items():
                    run = subprocess.run(["node", str(runner), port, Path(page).as_uri(), str(width), str(height),
                                          "1" if mobile else "0", now], capture_output=True, text=True, timeout=120)
                    line = next((ln for ln in run.stdout.splitlines() if ln.startswith("RESULT ")), None)
                    self.assertIsNotNone(line, (run.stdout + run.stderr)[-3000:])
                    res = json.loads(line[len("RESULT "):])
                    self.assertIsNone(res["exception"], res)
                    self.assertEqual((res["errors"], res["blocked"]), ([], 0), f"{name} at {width}x{height}")
                    got[name] = res["value"]
                with self.subTest(size=f"{width}x{height}"):
                    self.assertGreater(len(got["base"]["plates"]), 5)
                    # moved this check on purpose: the planets' and the sun's Silkscreen tags became
                    # names in fixed slots (case as written, the badge and the chip after the name) and the overview lost its
                    # other plates; what holds is that every body the base commit named is still named, by the same key
                    tags = lambda v: {p["key"]: p["text"] for p in v["plates"] if p["key"].startswith("tag:")}
                    base, seed = tags(got["base"]), tags(got["seed"])
                    self.assertLessEqual(set(base), set(seed))   # the phone's fit-all view names more than its old 0.22
                    for key, text in base.items():
                        name = "THIS MACHINE" if key == "tag:world:quest-board" else text
                        self.assertEqual(seed[key].split(" · ")[0].upper(), name.lstrip("_."), key)
                    if not mobile:
                        # gives the desktop map its own frame: 1200x856 beside the 400 px rail and over the
                        # 44 px status bar, so every plate is laid out on that smaller stage (which plates, and their words: the focus and labels module's, above)
                        self.assertEqual((got["seed"]["vw"], got["seed"]["vh"]), (1200, 856))
                        off = [p for p in got["seed"]["plates"] if p["x"] < -1 or p["y"] < -1 or p["x"] + p["w"] > 1201 or p["y"] + p["h"] > 857]
                        self.assertEqual(off, [], "plates outside the 1200x856 stage")
                    else:
                        # gives the phone's tab bar its icons (78 px where it was 56) and
                        # trades the zoom stack and the hub button for "+ Task": the stage the plates lay out in is 22 px
                        # shorter, and every plate stays inside it (where each sits is the focus and labels module's, above)
                        self.assertEqual(got["seed"]["vh"], got["base"]["vh"] - 22)
                        vw, vh = got["seed"]["vw"], got["seed"]["vh"]
                        for p in got["seed"]["plates"]:
                            with self.subTest(size=f"{width}x{height}", plate=p["key"]):
                                self.assertTrue(p["x"] >= -0.5 and p["y"] >= -0.5 and p["x"] + p["w"] <= vw + 0.5 and p["y"] + p["h"] <= vh + 0.5,
                                                f"{p['key']} at {p['x']},{p['y']} {p['w']}x{p['h']} leaves the {vw}x{vh} stage")
        finally:
            browser.terminate()
            try:
                browser.wait(10)
            except subprocess.TimeoutExpired:
                browser.kill()
                browser.wait()


if __name__ == "__main__":
    unittest.main()
