"""The phone, the tablet and Today (phone, tablet and the Today tab).

The phone's chrome: one top line (the three counts, two lines at four-digit counts, never under More) and a labelled "3D"
chip, both 44 px to the finger; no zoom stack, no hub button, "+ Task" on the Map tab only; tabs Today · Map · Agents ·
Ask whose badges are W.counts' need_you and working; a tapped planet opens a 35% peek, an empty tap never flies the ship,
a long press offers Open · New task here · Fly here · Fly in 3D · Ask <assistant> and buzzes through CM.haptic, the one
function every buzz goes through (T1). The composer from the map is a sheet over the Map tab that fits above the keyboard.
Today is one function for the desktop and the phone: Need you (the oldest four, then "+N more"), Running now (CM.wfRows:
"Fix the phone labels · 2 of 4 · ops", no ids or paths, re-rendered on a workflow-only change), Do today, empty sections
folded to "Quiet". Three halves: the source and one render, the page script in node against test_game's stub DOM, and a
real headless Chrome where the boxes are real; the probe runs the same checks on the benches.
"""
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

import testenv  # noqa: F401  (first: clears what steers the tool)
import test_game

ROOT = Path(__file__).resolve().parent
SRC = (ROOT / "render-game.py").read_text(encoding="utf-8")
PHONE_JS = (ROOT / "ui" / "phone.js").read_text(encoding="utf-8")
PHONE_CSS = (ROOT / "ui" / "phone.css").read_text(encoding="utf-8")
FIX = json.loads((ROOT / "tests" / "fixtures" / "workflow-demo.json").read_text(encoding="utf-8"))
TODAY_BODY = SRC[SRC.index("function renderToday(){"):SRC.index("document.getElementById('today').addEventListener")]
# a workflow whose words are valid (workflows.py) but carry a path and an id: Today must not show them (S5)
DIRTY = dict(FIX, id="w-20261012-fix", title="Fix /srv/ops w-20261012-fix", sentence="Now: fix /srv/ops/x.py for w-20261012-fix")


class PhoneSource(unittest.TestCase):
    """What the page carries, read from the source and one render."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        (Path(cls.tmp.name, "work", "Alpha")).mkdir(parents=True)
        proc = test_game.render_fixture(cls.tmp.name)
        if proc.returncode:
            raise AssertionError(proc.stderr[-2000:])
        cls.html = Path(cls.tmp.name, "page.html").read_text(encoding="utf-8")
        src = test_game.source_css()
        cls.rules = test_game.css_rules(src[src.index(test_game.MOBILE_MARK):])

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_the_top_line_and_the_3d_chip_sit_in_the_hud_before_more(self):
        hud = re.search(r'<div id="hud">(.*?)</div>\n', self.html, re.S).group(1)
        self.assertLess(hud.index('id="mline"'), hud.index('data-chip="3d"'))
        self.assertLess(hud.index('data-chip="3d"'), hud.index('id="mmore"'))
        self.assertRegex(hud, r'<button type="button" id="m3d" data-chip="3d" aria-label="Fly in 3D"><svg[^>]*>.*?</svg>3D</button>')
        # on a desktop both wait hidden, like every phone piece
        self.assertIn("#hud #mmore,#hud #mline,#hud #m3d{display:none}", self.html)

    def test_the_tabs_carry_an_icon_and_a_word_and_the_task_button_says_task(self):
        tabs = re.search(r'<nav id="tabs"[^>]*>(.*?)</nav>', self.html, re.S).group(1)
        self.assertEqual(tabs.count('<svg class="ti"'), 4)
        self.assertIn('<nav id="tabs" role="tablist" aria-label="Views" data-wall>', self.html)
        self.assertIn('<button type="button" id="mfab" aria-label="New task" data-wall><b aria-hidden="true">+</b>Task</button>', self.html)
        self.assertIn("if(sl&&sl.textContent!=='Ask') sl.textContent='Ask';", self.html)

    def test_the_seams_other_lanes_read(self):
        # the peek's height is one line other tests read; the zoom stack is hidden where the phone starts (the browser
        # class below measures both); the map's + Task is one named function (mobNewTask) the node driver calls
        self.assertIn("SHEET_H=pOn&&!MOB_LAND?Math.round(innerHeight*.35)+MOB_P:0;", self.html)
        self.assertIn("mq$('hud').setAttribute('data-wall',''); mq$('mzoom').hidden=true;", self.html)
        self.assertIn("mq$('mfab').addEventListener('click',mobNewTask);", self.html)

    def test_every_buzz_goes_through_the_one_function(self):
        # C3: the Vibrate setting, the 3 s gap, the visible page and the phone-only rule hold for every buzz
        self.assertNotIn("navigator.vibrate", SRC, "render-game.py buzzes only through CM.haptic")
        self.assertEqual(PHONE_JS.count("navigator.vibrate"), 2)   # the guard and the one call, both inside CM.haptic
        fn = PHONE_JS[PHONE_JS.index("CM.haptic = function"):PHONE_JS.index("// ---- the phone sheet")]
        self.assertEqual(fn.count("navigator.vibrate"), 2)
        self.assertIn("CM.haptic('tap');\n  BURSTS.push", SRC)                       # the long press
        self.assertIn("held=true; CM.haptic('tap'); zoomHome();", SRC)              # the zoom ring's long press

    def test_todays_actions_are_buttons_and_the_page_has_one_keyboard_handler_for_rows(self):
        # S4: Enter and Space on a role=button row go through the page's one document handler; the actions inside
        # a row are real buttons (focusable, one click per key press)
        self.assertIn('<button type="button" class="tbtn" data-show="\'+escA(String(cur.planet_uid))+\'">Show on map</button>', TODAY_BODY)
        self.assertIn('<button type="button" class="tbtn" data-follow="\'+escA(r.id)+\'">Follow in 3D</button>', TODAY_BODY)
        self.assertIn('<button type="button" class="tbtn tic" data-show="\'+escA(uid)+\'" aria-label="Show on map"', TODAY_BODY)
        self.assertNotIn('<span class="tbtn" data-', TODAY_BODY)
        self.assertNotIn('<span class="tbtn tic"', TODAY_BODY)
        self.assertNotIn("addEventListener('keydown'", PHONE_JS)
        self.assertIn("if(e.key==='Enter'||e.key===' '){ e.preventDefault(); e.stopPropagation(); t.click(); } });", SRC)

    def test_no_daily_sources_still_show_the_task_loop_in_plain_words(self):
        # S5: the line the user reads names no config key; the key sits under a "Set up" fold like the night run's
        self.assertIn("A map with no personal daily sources still has reviews and tasks", TODAY_BODY)
        self.assertNotIn("Today is off:", TODAY_BODY)

    def test_today_is_one_function_for_both_screens_and_writes_no_style(self):
        self.assertEqual(SRC.count("function renderToday(){"), 1)
        self.assertNotIn("MOB", TODAY_BODY, "the desktop and the phone read the same markup; the stylesheet lays it out")
        self.assertNotIn('style="', TODAY_BODY.replace("\\\\", "\\"), "no inline style from markup (the page's policy)")
        for sec in ("needs", "decisions", "tasks", "agents", "night", "word", "done", "plan", "daily", "folders", "missing"):
            self.assertIn(f'data-sec="{sec}"', TODAY_BODY)

    def test_the_phone_reads_today_in_the_comps_order(self):
        # C1: Need you, (also waiting), Running now with its agents and the night run, then Do today
        order = {s: b for s, b in self.rules if s.startswith("html.m #today section[data-sec=")}
        self.assertEqual([order[f"html.m #today section[data-sec={k}]"] for k in ("needs", "decisions", "tasks", "agents", "night", "word", "done")],
                         ["order:1", "order:2", "order:3", "order:4", "order:5", "order:6", "order:7"])
        self.assertIn("rows.slice(0,4).join('')+(rows.length>4?det('+'+(rows.length-4)+' more',rows.slice(4).join(''),'tmore'):'')", TODAY_BODY)

    def test_the_composer_sheet_has_a_keyboard_rule_and_the_targets_are_44px(self):
        # S1: a rule that outranks the sheet's 28dvh top once the keyboard is up (the seed's kb rule gives top/height)
        self.assertIn("html.m.kb body.m-comp #side{top:var(--vvt,0px);bottom:auto;height:calc(var(--vvh,100dvh) - var(--P));border-radius:0}", PHONE_CSS)
        # C4: More and the 3D chip are 44 px tall buttons whose drawn box is a ::before (the browser class measures them)
        self.assertRegex(PHONE_CSS, r"html\.m #hud #mmore\{[^}]*width:44px;height:44px")
        self.assertRegex(PHONE_CSS, r"html\.m #hud #m3d\{[^}]*height:44px")
        self.assertIn("html.m #hud #mline.t3{flex-wrap:wrap;white-space:normal", PHONE_CSS)

    def test_the_ui_files_hold_neutral_words_and_the_lanes_own_functions(self):
        # leak-markers.txt stays home (the export does not ship it): an exported tree checks the lane's functions only
        lm = ROOT / "leak-markers.txt"
        markers = [m.strip() for m in lm.read_text(encoding="utf-8").splitlines()
                   if m.strip() and not m.lstrip().startswith("#")] if lm.exists() else []
        for text, name in ((PHONE_JS, "ui/phone.js"), (PHONE_CSS, "ui/phone.css"), (TODAY_BODY, "renderToday")):
            for m in markers:
                self.assertNotIn(m.lower(), text.lower(), f"{name} holds {m!r}")
        for fn in ("CM.sheetCard = function", "CM.haptic = function", "window.__phoneAudit = function", "CM.planetThumb = function",
                   "function zoomAvoid(", "function phoneTick(", "function sheetBack("):
            self.assertIn(fn, PHONE_JS)
        self.assertIn("CM.backLayers.push({ pri: 20, fn: sheetBack });", PHONE_JS)
        self.assertIn("if (typeof window.__chromeAudit !== 'function') window.__chromeAudit = window.__phoneAudit;", PHONE_JS)

    def test_the_hex_colours_are_lower_case_for_the_theme_rewriter(self):
        self.assertEqual(re.findall(r"#[0-9a-f]*[A-F][0-9A-Fa-f]*\b", PHONE_CSS), [])


TODAY_DRIVER = r"""
  const FIX = __FIX__, DIRTY = __DIRTY__;
  // Running now reads CM.wfRows(): one card per working workflow, its row in one line, no id or path in its words
  elCache.today = cap();
  W.today = { configured: true, date: 'Mon Oct 12', agents: { working: [], waiting: [], idle: [], problems: [] }, needs: [], daily: [], done: [],
    folders: [], unmapped: [], word: null, night: null };
  applyTasks({ tasks: [], workflows: [FIX] });
  renderToday(); let h = String(elCache.today.innerHTML);
  const card = (html) => { const at = html.indexOf('class="tcard trun'); return at < 0 ? '' : html.slice(at, html.indexOf('</section>', at)); };
  const words = (html) => html.replace(/^[^>]*>/, '').replace(/<[^>]*>/g, ' ');
  let cardH = card(h);
  const aria = (cardH.match(/aria-label="([^"]*)"/) || [])[1];
  ok('today-running: the workflow row reads "Fix the phone labels · 2 of 4 · ops"', aria === 'Fix the phone labels · 2 of 4 · ops');
  ok('...and its words hold no id and no path', !!cardH && !/w-2026|\//.test(words(cardH)) && /Fix the phone labels/.test(words(cardH)) && /2 of 4/.test(words(cardH)));
  ok('...the sentence says the step at work and where', /Now: <b>Fix the label slots<\/b> in ops · 120 of 800/.test(h));
  ok('...its route has a stop per step, the done one green, the one at work its progress', (h.match(/class="tst"/g) || []).length === 4
    && /<span class="g">done<\/span>/.test(h) && /<span class="c">120 of 800<\/span>/.test(h));
  ok('...the progress bar is a class, never a style', /<div class="tdr"><i class="l\d+"><\/i><\/div>/.test(h) && !/style="/.test(h));
  ok('...Show on map and Follow in 3D are real buttons', /<button type="button" class="tbtn" data-show="area:ops">Show on map<\/button><button type="button" class="tbtn" data-follow="w-[^"]+">Follow in 3D<\/button>/.test(cardH));
  ok('Running now counts its rows', h.includes('Running now <span class="n">1</span>'));
  // S5: a valid title and sentence that carry a path and an id: neither reaches the words, the title nor the aria line
  applyTasks({ tasks: [], workflows: [DIRTY] }); renderToday(); h = String(elCache.today.innerHTML); cardH = card(h);
  { const a2 = (cardH.match(/aria-label="([^"]*)"/) || [])[1], w2 = words(cardH);
    ok('a workflow title with a path and an id reads clean ("Fix ops · 2 of 4 · ops")', a2 === 'Fix ops · 2 of 4 · ops');
    ok('...its words hold no path and no id (a file keeps its name)', !!cardH && !/w-2026|\/srv|\//.test(w2) && /Fix ops/.test(w2));
    ok('...the sentence too', /<div class="tnow">Now: fix x\.py for<\/div>/.test(cardH) && !/\/srv|w-2026/.test((cardH.match(/<div class="tnow">([^<]*)<\/div>/) || [])[1] || ''));
    ok('...the id rides data-wf only', cardH.includes('data-wf="w-20261012-fix"')); }
  // the workflow module's rows (the 2D workflows module) carry the route and the bar: row.stops and row.progress win
  { const realRows = CM.wfRows;
    CM.wfRows = () => [{ id: 'w-x', title: 'Ship the login page', done: 1, total: 4, state: 'working', place: 'site', sentence: '', step: 2, progress: 0.4,
      stops: [{ uid: 'area:x', name: 'Add route', state: 'done' }, { uid: '', name: 'Build page', state: 'working', progress: { done: 3, total: 5 } },
        { uid: '', name: 'Guide', state: 'waiting' }, { uid: '', name: 'Approve', state: 'waiting', kind: 'gate' }] },
      { id: 'w-n', title: 'Night run', done: 3, total: 3, state: 'done', night: true }];
    W.today = Object.assign({}, W.today, { night: { date: '2026-10-12', ran: 3, ready: 2, failed: 1, source: 'json', items: [] } });
    renderToday(); const hb = String(elCache.today.innerHTML);
    ok('the 2D workflows module rows: the route from row.stops, the bar from row.progress', (hb.match(/class="tst"/g) || []).length === 4 && /<span class="c">3 of 5<\/span>/.test(hb)
      && /<i class="l8"><\/i>/.test(hb) && /<span class="">you<\/span>/.test(hb) && (hb.match(/class="tcard trun/g) || []).length === 1);
    ok('...the night run\'s row is its card\'s Replay (data-wf-replay), never a Running now row', hb.includes('data-wf-replay="w-n"') && !hb.includes('data-wf="w-n"'));
    CM.wfRows = realRows; W.today = Object.assign({}, W.today, { night: null }); }
  // a derived workflow (the night run) is the Night run card, never a Running now row
  applyTasks({ tasks: [], workflows: [FIX, Object.assign({}, FIX, { id: 'w-night', derived: 'night', title: 'Night run' })] });
  renderToday(); h = String(elCache.today.innerHTML);
  ok('the night run\'s own workflow is no Running now row', (h.match(/class="tcard trun/g) || []).length === 1);
  // C2: with Today on, a workflow-only change (no task moved) re-renders it within the poll, no renderToday call needed
  applyTasks({ tasks: [], workflows: [] }); renderToday();
  setView('today'); elCache.today.innerHTML = '';
  applyTasks({ tasks: [], workflows: [FIX] });
  ok('C2: a workflow arriving while Today is on shows in Running now at once', String(elCache.today.innerHTML).includes('class="tcard trun') && VIEW === 'today');
  { const moved = JSON.parse(JSON.stringify(FIX)); moved.steps[1].progress = { done: 400, total: 800 };
    applyTasks({ tasks: [], workflows: [moved] });
    ok('...a step\'s progress moving re-renders too', /120 of 800/.test(String(elCache.today.innerHTML)) === false && /400 of 800/.test(String(elCache.today.innerHTML)));
    applyTasks({ tasks: [], workflows: [] });
    ok('...and a workflow ending takes its card away', !String(elCache.today.innerHTML).includes('class="tcard trun')); }
  setView('map');
  // C1: Need you shows the oldest four rows, then one "+N more" row that opens the rest in place
  { const rows = []; for (let i = 0; i < 13; i++) rows.push({ id: 't3:t' + i, source: 't3', state: 'review', title: 'review item ' + i, planet_uid: null, age_days: 20 - i, voice: '' });
    W.sky = { contacts: rows, error: null };
    renderToday(); const hn = String(elCache.today.innerHTML), sec = hn.slice(hn.indexOf('data-sec="needs"'), hn.indexOf('data-sec="decisions"'));
    const before = sec.slice(0, sec.indexOf('<details')), inside = sec.slice(sec.indexOf('<details'));
    ok('C1: four rows, then "+9 more"', (before.match(/class="tn need/g) || []).length === 4 && /<details class="tdet tmore"><summary>\+9 more<\/summary>/.test(inside)
      && (inside.match(/class="tn need/g) || []).length === 9 && hn.includes('Need you <span class="n"><b data-count="need_you">13</b></span>'));
    ok('...oldest first, the oldest marked', /class="tn need first" data-cid="t3:t0"/.test(before));
    delete W.sky; }
  // an empty section folds to its heading and one Quiet word; Need you, Running now and Do today are always there
  applyTasks({ tasks: [], workflows: [] }); W.today = Object.assign({}, W.today || {}, { word: null, night: null });
  renderToday(); h = String(elCache.today.innerHTML);
  ok('empty sections fold to "Quiet"', /<h2 class="tq">Need you <span class="q"[^>]*>Quiet<\/span><\/h2>/.test(h));
  ok('...Running now and Do today too', /<h2 class="tq">Running now <span class="q"[^>]*>Quiet<\/span>/.test(h) && /<h2 class="tq">Do today <span class="q"[^>]*>Quiet<\/span>/.test(h));
  ok('the header says the day and when the page was made', /<h1>Today <span>· /.test(h) && /<span class="gen">updated /.test(h));
  ok('the way back says T, the key that toggles Today', /Back to the map <span class="key">T<\/span>/.test(h));
  // S9: T toggles Today (M still returns); S5: Today off is plain words
  { const press = (key) => { const e = { key: key, repeat: false, preventDefault() {}, stopPropagation() {}, stopImmediatePropagation() {}, target: null }; (__WL.keydown || []).forEach((f) => f(e)); };
    setView('map'); press('t'); const a = VIEW; press('t'); const b = VIEW; press('t'); press('m'); const c = VIEW;
    ok('S9: T opens Today, T leaves it, M leaves it too', a === 'today' && b === 'map' && c === 'map'); }
  { W.today = Object.assign({}, W.today, { configured: false }); renderToday(); const ho = String(elCache.today.innerHTML);
    ok('S5: without daily sources, For you still shows the task loop', ho.includes('<h1>For you ') && ho.includes('data-sec="needs"') && !ho.includes('Today is off'));
    W.today = Object.assign({}, W.today, { configured: true }); }
  // S8: the once-a-second upkeep paints nothing while the page is hidden
  { let calls = 0; const real = todayMini; todayMini = () => { calls++; return true; };
    setView('today'); document.hidden = true; phoneTick(); const hid = calls; document.hidden = false; phoneTick(); phoneTick();
    todayMini = real; setView('map'); delete document.hidden;
    ok('S8: hidden, the miniature is not painted; shown, each tick asks it (it paints only on change)', hid === 0 && calls === 2); }
  // S10: the tablet's zoom column keeps clear of any wall on any side (pure geometry)
  { const vw = 1024, vh = 768, zw = 44, zh = 140;
    const box = (at) => ({ x: vw - at.right - zw, y: vh - at.bottom - zh, w: zw, h: zh });
    const cross = (r, z) => r.x < z.x + z.w && r.x + r.w > z.x && r.y < z.y + z.h && r.y + r.h > z.y;
    const clear = (walls) => { const at = zoomAvoid(walls, vw, vh, zw, zh), z = box(at); return walls.every((r) => !cross(r, z)) && z.x >= 0 && z.y >= 0 ? at : null; };
    const rail = { x: 704, y: 0, w: 320, h: 768 }, bar = { x: 0, y: 700, w: 1024, h: 68 }, hud = { x: 0, y: 0, w: 1024, h: 48 };
    const small = { x: 824, y: 560, w: 200, h: 140 }, chip = { x: 900, y: 720, w: 100, h: 48 }, mid = { x: 900, y: 600, w: 80, h: 60 }, wide = { x: 100, y: 650, w: 900, h: 60 };
    const a = clear([]), b = clear([rail, hud]), c = clear([bar]), d = clear([small]), e = clear([chip]), f = clear([mid]), g = clear([wide]), i = clear([rail, bar, small, chip]);
    ok('S10: no wall, the corner', a && a.right === 16 && a.bottom === 56);
    ok('...left of a right rail, above a bottom bar', b && b.right === 336 && b.bottom === 56 && c && c.right === 16 && c.bottom === 80);
    ok('...clear of a small box at the right edge, a chip at the bottom, a box in the middle, a wide floating bar', !!d && !!e && !!f && !!g);
    ok('...and of all of them at once', !!i); }
"""

PHONE_DRIVER = r"""
  const cl = (set) => ({ contains: (c) => set.has(c), add: (c) => set.add(c), remove: (c) => set.delete(c),
    toggle: (c, f) => { if (f === undefined) f = !set.has(c); if (f) set.add(c); else set.delete(c); return f; } });
  // T1 haptics: tap 10 ms, a step done 30, a failure [60, 40, 60], needs you [20, 60, 20]; one every 3 s at most
  const vib = []; global.navigator.vibrate = (p) => { vib.push(p); return true; };
  global.localStorage = (() => { const st = {}; return { getItem: (k) => (k in st ? st[k] : null), setItem: (k, v) => { st[k] = String(v); }, removeItem: (k) => { delete st[k]; } }; })();
  HAPTIC.last = -1e9; CM.haptic('done'); CM.haptic('done');
  ok('haptic-fn: a step done is one 30 ms buzz, a second inside 3 s is dropped', JSON.stringify(vib) === '[30]');
  HAPTIC.last = -1e9; CM.haptic('fail'); HAPTIC.last = -1e9; CM.haptic('needs'); HAPTIC.last = -1e9; CM.haptic('tap'); HAPTIC.last = -1e9; CM.haptic('nonsense');
  ok('...the vocabulary', JSON.stringify(vib) === '[30,[60,40,60],[20,60,20],10]');
  hapticSet(false); HAPTIC.last = -1e9; CM.haptic('done'); hapticSet(true);
  ok('...the Vibrate setting off keeps it quiet', vib.length === 4 && CM.settingsExtra.some((r) => r.key === 'vibrate' && r.get() === 'on'));
  MOB = false; HAPTIC.last = -1e9; CM.haptic('done'); MOB = true;
  ok('...and a desktop never buzzes', vib.length === 4);
  document.visibilityState = 'hidden'; HAPTIC.last = -1e9; CM.haptic('done'); delete document.visibilityState;
  ok('...nor a page in the background', vib.length === 4);
  // the long press: Open · New task here · Fly here · Fly in 3D · Ask <assistant>, in that order
  const area = units.find((u) => u.kind === 'area');
  const rows = pressRows(area, { x: 0, y: 0 }).map((r) => r.label);
  ok('long-press: the spec\'s five in its order', rows.slice(0, 5).join('|') === ['Open', 'New task here', 'Fly here', 'Fly in 3D', 'Ask ' + mateName()].join('|'));
  { const e3 = [], real = CM.enter3d; CM.enter3d = (o) => e3.push(o || {}); pressRows(area, { x: 0, y: 0 })[3].run(); CM.enter3d = real;
    ok('...Fly in 3D flies to the planet held', e3.length === 1 && e3[0].target === area.uid); }
  // C3: the long press itself buzzes through CM.haptic: the Vibrate setting off keeps it quiet, on it is the 10 ms tap
  VW = 1280; VH = 800; PANEL_W = 0; SHEET_CUR = 0; camOff.x = 200000; camOff.y = 0; draw(1100); CLICK_BOXES.length = 0; pilot = null;
  const P = new Set(); elCache.panel = cap(); elCache.panel.classList = cl(P); elCache.composer = cap(); elCache.composer.classList = cl(new Set());
  const MS = new Set(); elCache.msheet = cap(); elCache.msheet.classList = cl(MS);
  const pressAt = () => { GEST = { pan: false, eat: false, tap: true }; PINCH = null; FINGERS.clear(); FINGERS.set(1, { x: 640, y: 400 }); fingerPress(); };
  hapticSet(false); HAPTIC.last = -1e9; pressAt();
  ok('C3: a long press with Vibrate off never buzzes, and the menu opens', vib.length === 4 && MS.has('on'));
  MS.delete('on'); hapticSet(true); HAPTIC.last = -1e9; pressAt();
  ok('...with Vibrate on it is the 10 ms tap, once', vib.length === 5 && vib[4] === 10 && MS.has('on'));
  MS.delete('on');
  // an empty tap never flies the ship; with a sheet up it only puts the sheet away
  fingerTap({ clientX: 640, clientY: 400 });
  ok('tap-no-fly: an empty tap sets no pilot', pilot === null);
  P.add('on'); fingerTap({ clientX: 640, clientY: 400 });
  ok('...and with the card up it puts the card away, still no pilot', !P.has('on') && pilot === null);
  camOff.x = 0;
  // the badges: Today = W.counts.need_you, Agents = W.counts.working
  const bd = (id) => { const a = { hidden: true, attrs: {}, getAttribute(k) { return this.attrs[k]; }, setAttribute(k, v) { this.attrs[k] = v; } }; elCache[id] = a; return a; };
  const bt = bd('tabn-today'), ba = bd('tabn-threads');
  W.counts = Object.assign({}, W.counts, { need_you: 4, working: 3, review: 9 }); mobSync();
  ok('badge: Today says need you (4), Agents who works (3), not the reviews', bt.attrs['data-n'] === '4' && !bt.hidden && ba.attrs['data-n'] === '3' && !ba.hidden);
  // S7 / K1: the map's + Task (mobNewTask) opens the composer as a sheet over the Map tab, aimed at the planet in view;
  // closed, the tab is the map again; from the Agents list the composer is the list's
  { const wrapSet = new Set(), compSet = new Set(), sideSet = new Set(), bodySet = new Set();
    elCache.wrap = cap(); elCache.wrap.classList = cl(wrapSet); elCache.side = cap(); elCache.side.classList = cl(sideSet);
    elCache.composer = cap(); elCache.composer.classList = cl(compSet); elCache.cfolder = cap(); elCache.cfolder.value = '';
    const body = cap(); body.dataset = { tab: 'map' }; body.classList = cl(bodySet); document.body = body;
    setView('map'); near = area; mobNewTask(); mobSync();   // the page's class observer calls mobSync; the stub DOM has none
    ok('composer-sheet-phone: + Task opens the composer over the Map tab, aimed at the planet in view',
      composerOpen() && MOB_CMAP === true && mobTab() === 'map' && String(elCache.cfolder.value) === area.path && body.dataset.tab === 'map' && bodySet.has('m-comp'));
    composerClose();
    ok('...closed, the tab is the map and the flag is down', !composerOpen() && MOB_CMAP === false && mobTab() === 'map' && !bodySet.has('m-comp'));
    body.dataset.tab = 'threads'; sideShow(true); openComposer(null);
    ok('...opened in the Agents list, the tab is Agents', composerOpen() && MOB_CMAP === false && mobTab() === 'threads');
    composerClose(); near = null; }
  // the sheet: an element in the card sheet, Back (priority 20) puts it away; S2: not while the long-press menu is over it
  const pb = cap(), kids = []; pb.textContent = ''; pb.appendChild = (e) => kids.push(e); pb.contains = (e) => kids.includes(e);
  elCache.pbody = pb; const PS = new Set(); elCache.panel = cap(); elCache.panel.classList = cl(PS);
  const el = { tag: 'card' }; CM.sheetCard(el, {});
  ok('sheetCard shows the element in the card sheet', PS.has('on') && kids.includes(el) && CM.backLayer('has') === true);
  MS.add('on');
  ok('S2: with the long-press menu open, Back leaves the sheet alone (the menu closes first)', CM.backLayer('back') === false && PS.has('on') && CM.backLayer('has') === true);
  MS.delete('on');
  ok('...Back puts it away', CM.backLayer('back') === true && !PS.has('on') && CM.backLayer('has') === false);
"""


@unittest.skipUnless(shutil.which("node"), "node not installed")
class PhonePage(unittest.TestCase):
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
        cls.js = Path(cls.tmp.name, "page.js")
        test_game.page_js(Path(cls.tmp.name, "page.html"), cls.js)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_today_reads_the_workflow_rows_and_folds_what_is_empty(self):
        test_game.run_page_driver(self, self.js, TODAY_DRIVER.replace("__FIX__", json.dumps(FIX)).replace("__DIRTY__", json.dumps(DIRTY)))

    def test_the_phone_buzzes_presses_taps_badges_and_sheets(self):
        test_game.run_page_driver(self, self.js, PHONE_DRIVER, env_extra={"HARNESS_MOBILE": "1"})


# ---- a real headless Chrome at the phone's and the desktop's size: where the boxes are real ----
PHONE_CHROME = r"""
const area = unitByUid(W.chars.find((c) => c.kind === 'area' && c.path).uid);
const rect = (el) => { const r = el.getBoundingClientRect(); return { x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height), b: Math.round(r.bottom), r: Math.round(r.right) }; };
const shown = (id) => getComputedStyle(document.getElementById(id)).display;
const out = { mob: MOB, vw: innerWidth, vh: innerHeight };
mtab('map'); mobSync();
out.zoomOnMap = shown('mzoom'); out.fabOnMap = shown('mfab');
mtab('today'); mobSync(); out.fabOnToday = shown('mfab');
mtab('map'); mobSync();
// C4: the headline at four-digit counts fits beside More, which is 44 px to the finger, like the 3D chip
const line = document.getElementById('mline'), more = document.getElementById('mmore'), chip = document.getElementById('m3d');
out.counts = [[1533, 153, 15], [153, 15, 1], [15, 1, 1], [1, 1, 1]].map((c) => {
  W.counts = Object.assign({}, W.counts, { need_you: c[0], decisions: c[1], problems: c[2] }); mobSync();
  const L = rect(line), M = rect(more), C = rect(chip), hud = rect(document.getElementById('hud'));
  const mcs = [...line.querySelectorAll('.mc')].map(rect);
  return { c: c, text: line.textContent.replace(/\s+/g, ' ').trim(), cls: line.className, fits: line.scrollWidth <= line.clientWidth + 1,
    lineRight: L.r, moreLeft: M.x, more: [M.w, M.h], chip: [C.w, C.h], hudH: hud.h,
    inside: mcs.every((m) => m.x >= L.x - 1 && m.r <= L.r + 1 && m.y >= hud.y - 1 && m.b <= hud.b + 1), lineBottom: L.b, hudBottom: hud.b }; });
W.counts = Object.assign({}, W.counts, { need_you: 0, decisions: 0, problems: 0 }); mobSync();
// the peek: a tapped planet's card is 35% of the screen
activate(area); mobSync(); await new Promise((r) => setTimeout(r, 250));
const panel = rect(document.getElementById('panel')), A = __phoneAudit();
out.peek = { h: panel.h, share: Math.round(panel.h / innerHeight * 100) / 100, audit: A.sheet, fab: shown('mfab'), fabBottom: rect(document.getElementById('mfab')).b, panelTop: panel.y };
closeAll(); mobSync();
// S1 / K1: + Task from the map is a sheet over the Map tab aimed at the planet; with the keyboard up it fits above it
near = area; mobNewTask(); await new Promise((r) => setTimeout(r, 120));
const side = document.getElementById('side'), btns = document.querySelector('#composer .cbtns');
const B1 = __phoneAudit();
out.comp = { sheet: B1.composer.sheet, target: B1.composer.target, path: area.path, tab: document.getElementById('tab-map').getAttribute('aria-selected'),
  side: rect(side), btns: rect(btns), go: rect(document.getElementById('cgo')), cancel: rect(document.getElementById('ccancel')) };
Object.defineProperty(window, 'visualViewport', { configurable: true, value: { width: innerWidth, height: 480, offsetTop: 0, offsetLeft: 0, pageTop: 0, pageLeft: 0, scale: 1, addEventListener() {}, removeEventListener() {} } });
mobVV(); await new Promise((r) => setTimeout(r, 120));
const B2 = __phoneAudit();
out.kb = { cls: document.documentElement.classList.contains('kb'), vvh: getComputedStyle(document.documentElement).getPropertyValue('--vvh').trim(),
  sheet: B2.composer.sheet, bottom: B2.composer.bottom, vvBottom: B2.composer.vvBottom, side: rect(side), btns: rect(btns), go: rect(document.getElementById('cgo')), cancel: rect(document.getElementById('ccancel')),
  tabs: shown('tabs') };
out.chromeAudit = typeof window.__chromeAudit === 'function' && window.__chromeAudit().composer.sheet === B2.composer.sheet;
return out;
"""

TODAY_CHROME = r"""
const FIX = __FIX__;
const out = { mob: MOB };
// the fixture config has no today block: Today itself is given the day, as the node driver does
W.today = { configured: true, date: 'Mon Oct 12', agents: { working: [], waiting: [], idle: [], problems: [] }, needs: [], daily: [], done: [], folders: [], unmapped: [], word: null, night: null };
applyTasks({ tasks: [], workflows: [FIX] });
if (MOB) mtab('today'); else setView('today');
await new Promise((r) => setTimeout(r, 120));
// S4: Enter on a row activates it once (the page's one document handler); the actions are real buttons
let n = 0; CM.selectWorkflow = () => { n++; }; CM.sheetCard = () => {}; CM.railCard = () => {};
const card = document.querySelector('#today [data-wf]'); card.focus();
card.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
out.enter = n; n = 0;
card.dispatchEvent(new KeyboardEvent('keydown', { key: ' ', bubbles: true, cancelable: true }));
out.space = n;
out.follow = (document.querySelector('#today [data-follow]') || {}).tagName; out.show = (document.querySelector('#today .tbtns [data-show]') || {}).tagName;
let e3 = 0; CM.enter3d = () => { e3++; }; document.querySelector('#today [data-follow]').click(); out.followClicks = e3;
// S8 (desktop): the miniature paints once, then only when something it shows has changed
if (!MOB) { if (VIEW !== 'today') setView('today'); const a = todayMini(), b = todayMini(); MOONS[0].x += 1; const c = todayMini(), d = todayMini(); out.mini = [a, b, c, d]; }
return out;
"""


class PhoneBrowser(test_game.ChromeCase):
    """The phone's chrome measured in a real browser at 390x844 (C4, S1, S7) and Today's keys and miniature (S4, S8)."""

    def test_the_chrome_at_phone_size(self):
        v = self.run_body(390, 844, True, PHONE_CHROME, "phone-chrome")
        self.assertTrue(v["mob"], v)
        self.assertEqual((v["zoomOnMap"], v["fabOnToday"]), ("none", "none"), "no zoom stack on a phone; no + Task on Today")
        self.assertEqual(v["fabOnMap"], "flex", "+ Task on the Map tab")
        for c in v["counts"]:
            with self.subTest(counts=c["c"]):
                self.assertTrue(c["fits"], c)                                   # never clipped
                self.assertLessEqual(c["lineRight"], c["moreLeft"], c)           # never under More
                self.assertTrue(c["inside"], c)                                  # every count inside the line and the bar
                self.assertGreaterEqual(c["more"][0], 44, c); self.assertGreaterEqual(c["more"][1], 44, c)
                self.assertGreaterEqual(c["chip"][0], 44, c); self.assertGreaterEqual(c["chip"][1], 44, c)
                self.assertRegex(c["text"].replace(" ", ""), r"^%dwaitingforyou·%ddecisions?·%dproblems?$" % tuple(c["c"]))
        self.assertEqual(v["counts"][0]["cls"], "t3", "four-digit counts wrap to two lines")
        p = v["peek"]
        self.assertAlmostEqual(p["share"], 0.35, delta=0.02, msg=p)
        self.assertEqual((p["audit"]["on"], p["audit"]["full"]), (True, False), p)
        self.assertEqual(p["fab"], "flex"); self.assertLessEqual(p["fabBottom"], p["panelTop"] + 1, "+ Task rides above the peek")
        c = v["comp"]
        self.assertTrue(c["sheet"], c); self.assertEqual(c["target"], c["path"]); self.assertEqual(c["tab"], "true", "the composer stays on the Map tab")
        self.assertGreater(c["side"]["y"], 844 * 0.15, "the planet stays in view above the sheet")
        k = v["kb"]
        self.assertTrue(k["cls"], k); self.assertEqual(k["vvh"], "480px")
        self.assertTrue(k["sheet"], k)
        for box in ("side", "btns", "go", "cancel"):
            self.assertLessEqual(k[box]["b"], 481, f"{box} sits under the keyboard: {k}")
            self.assertGreaterEqual(k[box]["y"], -1, k)
        self.assertEqual(k["tabs"], "none", "the tab bar goes with the keyboard up")
        self.assertTrue(v["chromeAudit"], "__chromeAudit answers until the chrome module's is in")

    def test_todays_rows_answer_one_key_press_once_and_the_miniature_paints_on_change(self):
        for width, height, mobile in ((390, 844, True), (1600, 900, False)):
            with self.subTest(size=f"{width}x{height}"):
                v = self.run_body(width, height, mobile, TODAY_CHROME.replace("__FIX__", json.dumps(FIX)), f"today-{width}")
                self.assertEqual(v["mob"], mobile)
                self.assertEqual((v["enter"], v["space"]), (1, 1), v)
                self.assertEqual((v["follow"], v["show"]), ("BUTTON", "BUTTON"))
                self.assertEqual(v["followClicks"], 1)
                if not mobile:
                    self.assertEqual(v["mini"], [True, False, True, False], "painted once, then only on change")


if __name__ == "__main__":
    unittest.main()
