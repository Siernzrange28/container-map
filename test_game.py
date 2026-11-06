#!/usr/bin/env python3
"""test_game.py — the dead-page guard. render-game.py must exit 0, emit a
shaped world, ship syntactically valid JS, and draw one canvas frame (home,
remote, mono) without throwing, using a dependency-free stub harness.

Run: python3 test_game.py   (node needed only for the JS half; else skipped)
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

import testenv  # noqa: F401  (first: clears what steers the tool)

ROOT = Path(__file__).resolve().parent

# Dependency-free DOM/canvas stub: every element absorbs anything, the 2d
# context records fillRect calls so the test can prove a frame was drawn.
HARNESS = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');
let fills = 0;
const store = process.env.HARNESS_SESSION ? JSON.parse(process.env.HARNESS_SESSION) : {};   // what the tab already kept
global.sessionStorage = { getItem: (k) => (k in store ? store[k] : null),
  setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } };
const H_LOC = process.env.HARNESS_LOCATION
  ? JSON.parse(process.env.HARNESS_LOCATION) : { protocol: 'file:', hostname: '' };
global.location = Object.assign({ reload: () => {} }, H_LOC);
global.devicePixelRatio = 1; global.innerWidth = 1280; global.innerHeight = 800;
// HARNESS_MOBILE=1: a 390x844 phone (the page's phone query matches; nothing else does)
if (process.env.HARNESS_MOBILE === '1') {
  global.innerWidth = 390; global.innerHeight = 844;
  global.matchMedia = (q) => ({ matches: /max-width:700px/.test(q), addEventListener: () => {} });
}
// window listeners are kept by event name, so a driver can press a key on the page: __WL.keydown.forEach((f) => f(event))
global.__WL = {};
global.addEventListener = (t, f) => { (global.__WL[t] = global.__WL[t] || []).push(f); };
const rafQ = [];
global.requestAnimationFrame = (cb) => { rafQ.push(cb); return 0; };
global.setInterval = () => 0; global.clearInterval = () => {};
global.navigator = {};
// HARNESS_REDUCED=1: the page reads prefers-reduced-motion as set (REDUCED is read once, at load)
if (process.env.HARNESS_REDUCED === '1') global.matchMedia = (q) => ({ matches: /prefers-reduced-motion/.test(q), addEventListener: () => {} });
function anyFn() { return (...a) => universal(); }
function universal() {
  return new Proxy(function () {}, {
    get: (o, k) => {
      if (k === 'measureText') return (s) => ({ width: String(s).length * 5.5 });
      if (k === 'getBoundingClientRect') return () => ({ left: 0, top: 0, width: 1280, height: 800 });
      if (k === 'getContext') return () => ctx;
      if (k === 'canvas') return {};
      if (k === 'style') return { setProperty: () => {} };
      if (k === 'classList') return { add: () => {}, remove: () => {}, toggle: () => {}, contains: () => false };
      if (k === 'dataset') return {};
      if (k === 'width' || k === 'height' || k === 'scrollHeight' || k === 'scrollTop' || k === 'clientHeight') return 100;
      if (k === 'innerHTML' || k === 'textContent' || k === 'value') return '';
      if (k === Symbol.toPrimitive) return () => 0;
      return anyFn();
    },
    set: () => true,
    apply: () => universal(),
  });
}
const ctx = new Proxy({}, {
  get: (o, k) => {
    if (k in o) return o[k];
    if (k === 'measureText') return (s) => ({ width: String(s).length * 5.5 });
    if (k === 'fillRect') return (...a) => { fills += 1; };
    if (k === 'createLinearGradient' || k === 'createRadialGradient' || k === 'createPattern')
      return (...a) => ({ addColorStop: () => {} });
    if (k === 'drawImage') return (...a) => {};
    if (typeof k === 'string') return (...a) => {};
    return undefined;
  },
  set: (o, k, v) => { o[k] = v; return true; },
});
const elCache = { c: Object.assign(universal(), { width: 0, height: 0, getContext: () => ctx }) };
// HARNESS_STAGE=WxH: the stage the page measures (fit() reads its client size); otherwise the stage is unmeasured
if (process.env.HARNESS_STAGE) { const [sw, sh] = process.env.HARNESS_STAGE.split('x').map(Number);
  elCache.stage = new Proxy(universal(), { get: (t, k) => (k === 'clientWidth' ? sw : k === 'clientHeight' ? sh : t[k]), set: () => true }); }
global.document = {
  getElementById: (id) => (elCache[id] || (elCache[id] = universal())),
  addEventListener: () => {},
  querySelector: () => universal(), querySelectorAll: () => [],
  createElement: () => universal(), activeElement: null,
  body: universal(),
  // records data-theme the way the page sets it on <html>, and the classes (m, land, t, kb) the page puts there
  documentElement: { attrs: {}, setAttribute(k, v) { this.attrs[k] = String(v); }, getAttribute(k) { return this.attrs[k]; },
    style: { setProperty: () => {} }, cls: new Set(),
    classList: { contains(c) { return this.o.cls.has(c); }, add(c) { this.o.cls.add(c); }, remove(c) { this.o.cls.delete(c); },
      toggle(c, f) { const on = f === undefined ? !this.o.cls.has(c) : !!f; if (on) this.o.cls.add(c); else this.o.cls.delete(c); return on; } } },
};
document.documentElement.classList.o = document.documentElement;
global.window = global;
// a page the bridge served carries its own flag: HARNESS_BRIDGE is the JSON the bridge would write
if (process.env.HARNESS_BRIDGE) global.CM_BRIDGE = JSON.parse(process.env.HARNESS_BRIDGE);
// a static page has no bridge; a live one gets an empty answer for anything a test does not script
global.fetch = (H_LOC.protocol === 'http:' || H_LOC.protocol === 'https:')
  ? async () => ({ ok: true, status: 200, json: async () => ({}) })
  : async () => { throw new Error('static file - view only'); };
const driver = `
;(function(){
  const out = [];
  const ok = (n, c) => out.push((c ? 'PASS ' : 'FAIL ') + n);
  const savedConstellations=W.constellations;
  W.constellations=[{lane:'Money',label:'Money',count:1,threads:[{id:'invented:one',title:'Invented thread',state:'waiting',last:new Date().toISOString(),unfinished_n:1,children:2,running:0,unfinished:[{what:'Review fixture',evidence:'Invented quote',when:'2026-10-06',owes:'him'}],next_step:'Read fixture',resume:{kind:'claude',command:'claude --resume invented'}}]}];
  CM.drawConstellations2D(999);
  ok('constellation star is drawn and picked',CT.points.length===1&&CM.constellationHit(CT.points[0].x,CT.points[0].y,0).kind==='thread-star');
  const star=CT.points[0], html=CM.threadCardHtml(star); show(CM.constellationHit(star.x,star.y,0));
  ok('constellation star opens its proof and resume card',CT.selected===star.id&&html.includes('Invented quote')&&html.includes('claude --resume invented')&&html.includes('Read fixture'));
  ok('settled stars age out and working hue is cyan',!CM.constellationVisible({state:'settled',last:'2020-01-01'})&&CM.constellationColour({state:'working'})==='#48F0FF');
  const stale={...star,state:'ready',unfinished_n:0,unfinished:star.unfinished.map(q=>({...q,changed_since_read:true}))};
  const changedHtml=CM.threadCardHtml(stale);
  ok('changed findings keep a grey quote without amber',CM.constellationColour(stale)==='#64748B'&&changedHtml.includes('finding-changed')&&changedHtml.includes('Changed since read')&&changedHtml.includes('Invented quote'));
  W.constellations=savedConstellations;
  draw(1000);
  ok('home frame draws', fills > 500);
  const before = fills;
  try { GB = true; draw(1100); ok('mono frame draws', fills > before); }
  catch (e) { ok('mono frame draws', false); }
  GB = false;
  // every theme pack draws a frame, tiles and sprites included, and switching back restores the classic look
  const classic = JSON.stringify([STYLE, THEMES, SPRITES]);
  ok('theme packs are inlined', THEME_NAMES.length >= 5 && THEME_NAMES[0] === 'classic');
  ok('default theme is the config one', THEME_NAME === THEME_CFG && document.documentElement.getAttribute('data-theme') === THEME_CFG);
  THEME_NAMES.forEach((n) => {
    const b = fills;
    try {
      applyTheme(n, false);
      draw(1150);
      const P = THEME_PACKS[n], tiled = Object.keys(P.tiles || {}).length > 0;
      bodyArt(40, 'city', null, 7, 0, 1, 0); rockArt(14, 'rock', 3, 2);
      spriteArt('fighter', hullPal('#3dff9e', '#ff6a2e'), 3, 16, 3); spriteArt('cargo', cargoPal('t', ''), 5, 16, 3);
      spriteArt('pilot', pilotPal('#3dff9e', ''), 3, 16, 4); spriteArt('pilot_boost', pilotPal('', ''), 5, 16, 4);
      spriteArt('carrier', hullPal('#5ff2ff', '#ff6a2e'), 4, 16, 3); spriteArt('raider', hullPal('#ff4d5e', '#ff5a2e'), 6, 16, 3);
      spriteArt('gate', hullPal('#3dff9e', ''), 0, 16, 3);
      ok('theme ' + n + ' draws', fills > b && THEME_NAME === n && document.documentElement.getAttribute('data-theme') === n);
      ok('theme ' + n + ' tiles follow the pack', (tileOf('city', null) !== null) === tiled && (n !== 'classic' || !tiled));
      ok('theme ' + n + ' sprites and space follow the pack', JSON.stringify(SPRITES.fighter) === JSON.stringify((P.sprites || BASE_SPRITES).fighter) && SPACE.nebula === P.space.nebula);
    } catch (e) { ok('theme ' + n + ' draws', false); out.push('  ' + (e && e.stack)); }
  });
  applyTheme('classic', false);
  ok('switching back to classic restores the classic constants', JSON.stringify([STYLE, THEMES, SPRITES]) === classic);
  ok('theme cycle moves on', (() => { const was = THEME_NAME; cycleTheme(1); const moved = THEME_NAME !== was; applyTheme('classic', false); return moved; })());
  try {
    const tiledName = THEME_NAMES.find((n) => Object.keys(THEME_PACKS[n].tiles || {}).length > 0);
    applyTheme(tiledName, false); ok('a tiled pack tiles', tileOf('city', null) !== null);
    GB = true; draw(1160); ok('theme packs stay off under the mono palette', tileOf('city', null) === null);
  } catch (e) { ok('theme packs stay off under the mono palette', false); }
  GB = false; applyTheme(THEME_CFG, false);
  try { buildRemoteDeck('lab-b', { tabs: [], note: 'smoke' }); deck = 'lab-b'; draw(1200);
    ok('remote frame draws', fills > before); }
  catch (e) { ok('remote frame draws', false); }
  deck = 'home';
  // universal() drops writes, so give the asserted elements a recorder that keeps innerHTML.
  const recorder = () => { let h = ''; return new Proxy(universal(), {
    get: (t, k) => (k === 'innerHTML' ? h : t[k]),
    set: (t, k, v) => { if (k === 'innerHTML') h = String(v); return true; } }); };
  elCache.topbar = recorder(); elCache.pbody = recorder();
  const savedUnits=units.slice(); units.length=0;
  units.push({uid:'ship:t3:fixture',kind:'ship',source:'t3',status:'WORKING',name:'thread',pane:'activity'},
    {uid:'ship:deck:fixture',kind:'ship',source:'deck',status:'STALE',name:'old deck'},
    {uid:'seat:fixture',kind:'seat',status:'IDLE',name:'idle tmux'});
  topbarTick();
  ok('top bar counts working ships', elCache.topbar.innerHTML.includes('1 running') && elCache.topbar.innerHTML.includes('agents working'));
  const savedSources=W.ship_sources; W.ship_sources=[{source:'t3',status:'ABSENT',message:'T3 database missing'}];
  topbarTick(); ok('missing source appears in quiet top bar', elCache.topbar.innerHTML.includes('T3 database missing'));
  W.ship_sources=savedSources;
  units.length=0; savedUnits.forEach(u=>units.push(u));
  const area=W.chars.find(c=>c.kind==='area'&&c.path);
  if(area){
    W.chars.push({uid:'ship:fixture',kind:'ship',source:'t3',status:'WORKING',name:'fixture thread',cwd:area.path+'/child'});
    place();
    const s=units.find(u=>u.uid==='ship:fixture');
    ok('metadata ship appears and docks at its planet', !!s&&s.docked_at===area.uid&&Number.isFinite(s.gx)&&Number.isFinite(s.gy));
    show(s); ok('metadata ship panel has no terminal send',
      elCache.pbody.innerHTML.length > 0 && !elCache.pbody.innerHTML.includes('id="sayf"'));
    W.chars.pop(); place();
  }
  const savedVerbs = W.verbs;
  W.verbs = {items:[{name:'improve-loop',label:'improve loop',scope:'tool',applies:['dir'],kind:'runner'}],
    errors:[],dir:'/fixture/verbs',loop:'refused <script>not markup</script>'};
  const receiptPanel = verbsHtml({kind:'dir',path:W.tool});
  ok('loop receipt escaped in verbs panel', receiptPanel.includes('LAST IMPROVEMENT PASS') && receiptPanel.includes('&lt;script&gt;'));
  ok('loop offered on tool', receiptPanel.includes('data-verb="improve-loop"'));
  ok('loop hidden on other dirs', !verbsHtml({kind:'dir',path:'/not-the-tool'}).includes('data-verb="improve-loop"'));
  W.verbs = savedVerbs;
  // rooms sent by uid come back as the characters; an id with no character is dropped
  const jw = joinRooms({chars:[{kind:'area',uid:'area:x',rooms:['dir:x/a','dir:x/gone',{uid:'dir:x/b',name:'b'}]},
    {kind:'dir',uid:'dir:x/a',name:'a'}]});
  ok('joinRooms keeps known rooms, drops ids with no character', jw.chars[0].rooms.length === 2
    && jw.chars[0].rooms[0] === jw.chars[1] && jw.chars[0].rooms.every(r => r && typeof r === 'object'));
  try { if (rafQ.length) { rafQ[0](1300); out.push('PASS raf frame'); } else out.push('FAIL raf frame'); }
  catch (e) { out.push('FAIL raf frame ' + (e && e.message)); }
  return out;
})()`;
// argv[3]: a driver file to run instead of the built-in smoke driver; it may be async
const drv = process.argv[3] ? fs.readFileSync(process.argv[3], 'utf8') : driver;
Promise.resolve(eval(src + drv)).then((res) => {
  console.log(res.join('\n'));
  if (res.some((l) => l.indexOf('FAIL') === 0)) process.exit(1);
  console.log('SMOKE_OK');
  process.exit(0);
}, (e) => { console.log('FAIL driver threw ' + ((e && e.stack) || e)); process.exit(1); });
"""


# In the owner's tree container.json is present and the page is rendered by the world seat. In
# an exported tree there is no config: render the tool directory itself (zero-config) so the
# guard runs anywhere.
ROOT_ARGS = [] if (ROOT / "container.json").exists() else ["--root", str(ROOT)]
# a CONTAINER_OUT from the environment wins, so a test run can leave the tracked game.html alone
PAGE = Path(os.environ.get("CONTAINER_OUT") or ROOT / "game.html")
RENDER_ENV = dict(os.environ, CONTAINER_OUT=str(PAGE))


def config_args(tmp):
    """Arguments for rendering with the owner's container.json but with the two files a render
    writes under work_root/.container-map kept off it: the hourly history row goes to `tmp`,
    and the cron syslog offset cache is off (an empty cron_syslog_file means log mtimes only).
    In that config both are live files shared with the running world seat."""
    own = ROOT / "container.json"
    if not own.exists():
        return ROOT_ARGS
    cfg = json.loads(own.read_text(encoding="utf-8"))
    cfg["history_jsonl"] = str(Path(tmp, "history.jsonl"))
    cfg["cron_syslog_file"] = ""
    cfg["skin"] = "neutral"                    # these tests are about the shipped look, whatever skin the owner wears
    path = Path(tmp, "container.json")
    path.write_text(json.dumps(cfg), encoding="utf-8")
    return ["--config", str(path)]


class GameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.cfg_args = config_args(cls.tmp.name)
        # A tracked page may belong to an older renderer; exercise the current source.
        subprocess.run([sys.executable, "render-game.py", *cls.cfg_args], cwd=ROOT, env=RENDER_ENV,
                       capture_output=True, text=True, timeout=300, check=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_render_exits_zero_with_shaped_world(self):
        proc = subprocess.run(
            [sys.executable, "render-game.py", "--json", *self.cfg_args],
            cwd=ROOT, capture_output=True, text=True, timeout=300)
        self.assertEqual(proc.returncode, 0, msg=proc.stderr[-2000:])
        world = json.loads(proc.stdout)
        self.assertIsInstance(world.get("chars"), list)
        self.assertTrue(world["chars"], msg="world has no characters")
        self.assertIsInstance(world.get("quests"), list)
        self.assertIsInstance(world.get("generated"), str)
        kinds = {c.get("kind") for c in world["chars"]}
        self.assertIn("area", kinds, msg=f"missing core kinds: {sorted(kinds)}")   # seats need a tmux session
        for char in world["chars"]:
            self.assertIsInstance(char.get("kind"), str)
            self.assertIsInstance(char.get("name"), str)

    def test_game_html_is_fresh_and_complete(self):
        page = PAGE
        self.assertTrue(page.exists(), msg="render game.html first")
        html = page.read_text(encoding="utf-8")
        self.assertIn('<canvas id="c">', html)
        self.assertNotIn("__WORLD__", html)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_embedded_js_draws_stubbed_frames(self):
        html = PAGE.read_text(encoding="utf-8")
        match = re.search(r"<script>(.*)</script>", html, re.S)
        self.assertIsNotNone(match, msg="no embedded script found")
        with tempfile.TemporaryDirectory() as tmp:
            js = Path(tmp, "game.js")
            js.write_text(match.group(1), encoding="utf-8")
            harness = Path(tmp, "harness.js")
            harness.write_text(HARNESS, encoding="utf-8")
            check = subprocess.run(
                ["node", "--check", str(js)],
                capture_output=True, text=True, timeout=120)
            self.assertEqual(check.returncode, 0, msg=check.stderr[-2000:])
            run = subprocess.run(
                ["node", str(harness), str(js)],
                capture_output=True, text=True, timeout=120)
            self.assertEqual(run.returncode, 0,
                             msg=(run.stdout + run.stderr)[-3000:])
            self.assertIn("SMOKE_OK", run.stdout)


# ---------------------------------------------------------------- theme packs and texture packs

import themepacks as tp                                  # noqa: E402  (next to the tests that use it)

SHIPPED_PACKS = ["classic", "blueprint", "earthen", "phosphor", "synthwave", "tactical"]     # classic first, then by name
RENDER_SRC = (ROOT / "render-game.py").read_text(encoding="utf-8")
HEX_COLOR = re.compile(r"#[0-9a-fA-F]{6}\Z")


def js_block(name):
    """The body of `const NAME={ ... };` in render-game.py's page script."""
    return re.search(r"const " + name + r"=\{\n(.*?)\n\};", RENDER_SRC, re.S).group(1)


def code_style():
    out = {}
    for line in js_block("STYLE").split("\n"):
        m = re.match(r"\s*(\w+):\s*\{(.*)\},?\s*$", line)
        out[m.group(1)] = dict(re.findall(r"(\w+):'(#[0-9a-fA-F]{6})'", m.group(2)))
    return out


def code_kinds():
    out = {}
    for line in js_block("THEMES").split("\n"):
        m = re.match(r"\s*(\w+):\s*\{(.*)\},?\s*$", line)
        found = dict(re.findall(r"(\w+):'([^']*)'", m.group(2)))
        out[m.group(1)] = {k: found[k] for k in ("base", "land", "hi", "glow")}
    return out


def code_sprites():
    out = {}
    for line in js_block("SPRITES").split("\n"):
        m = re.match(r"\s*(\w+):\[(.*)\],?\s*$", line)
        out[m.group(1)] = re.findall(r"'([^']*)'", m.group(2))
    return out


def source_css():
    """The stylesheet exactly as render-game.py carries it (the classic colours, as literals)."""
    return re.search(r"<style>(.*?)</style>", RENDER_SRC, re.S).group(1)


def undo_variables(css):
    """Put each variable's default back: the :root block names every one of them."""
    root = re.match(r":root\{([^}]*)\}", css)
    assert root, "the stylesheet starts with its :root defaults"
    defaults = dict(d.split(":", 1) for d in root.group(1).split(";"))
    names = "|".join(re.escape(k[2:]) for k in defaults)
    return re.sub(r"var\(--(" + names + r")\)", lambda m: defaults["--" + m.group(1)], css[root.end():])


def normal_hex(css):
    return tp._HEX_TOKEN.sub(lambda m: "#" + tp._expand(m.group(0)), css)


def render_with(cfg, env=None):
    """Render the page with a throwaway config (an empty work root): (returncode, html, stderr).
    `env` adds environment variables to the render."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp, "root")
        root.mkdir()
        conf = Path(tmp, "container.json")
        conf.write_text(json.dumps(dict({"version": 1, "work_root": str(root), "tmux_session": "none",
                                         "processes": False, "decision_scan": False}, **cfg)), encoding="utf-8")
        out = Path(tmp, "page.html")
        proc = subprocess.run([sys.executable, "render-game.py", "--config", str(conf)], cwd=ROOT,
                              env=dict(os.environ, CONTAINER_OUT=str(out), **(env or {})), capture_output=True,
                              text=True, timeout=300)
        return proc.returncode, (out.read_text(encoding="utf-8") if out.exists() else ""), proc.stderr


class ThemePackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.warnings = []
        cls.packs = tp.load_dir(str(ROOT / "themes"), cls.warnings.append)
        cls.html = PAGE.read_text(encoding="utf-8")

    def test_five_packs_load_and_validate(self):
        self.assertEqual(self.warnings, [], "every file in themes/ is a valid pack")
        self.assertEqual(list(self.packs), SHIPPED_PACKS)
        for name, pack in self.packs.items():
            with self.subTest(pack=name):
                self.assertEqual(tp.validate(pack, name), [])
                self.assertEqual(tp.quality(pack), [], "text contrast, accents against the panel, a/b/c apart")
                self.assertEqual(json.loads((ROOT / "themes" / f"{name}.json").read_text(encoding="utf-8")), pack)
                self.assertEqual(set(pack["ui"]), set(tp.UI_KEYS))
                self.assertEqual(set(pack["style"]), set(tp.SURFACES))
                self.assertEqual(len(pack["space"]["nebula"]), 3)
                self.assertIn(len(pack["space"]["stars"]), (2, 3, 4))
                self.assertIsInstance(pack["space"]["scanlines"], bool)
                self.assertTrue(0 <= pack["space"]["glow"] <= 1)
                every = list(pack["ui"].values()) + pack["space"]["nebula"] + pack["space"]["stars"]
                every += [c for e in pack["style"].values() for c in e.values()]
                if pack.get("render") != "vector":                    # a vector pack draws no tiles
                    self.assertTrue("tiles" in pack and "tile_palette" in pack, "a pixel pack names its tiles")
                every += list(pack.get("tile_palette", {}).values()) + list(pack["sprite_palette"].values())
                for color in every:
                    self.assertRegex(color, HEX_COLOR)
                for surface, rows in pack.get("tiles", {}).items():   # 8 rows of 8 letters, all in the palette
                    self.assertIn(surface, tp.SURFACES)
                    self.assertEqual((len(rows), {len(r) for r in rows}), (8, {8}), surface)
                    self.assertLessEqual(set("".join(rows)), set(pack["tile_palette"]), surface)
                    self.assertGreaterEqual(len(set("".join(rows))), 2, f"{surface}: not a flat tile")
                for craft in tp.SPRITES:
                    rows = pack["sprites"][craft]
                    width = len(rows[0])
                    self.assertTrue(all(len(r) == width for r in rows) and width % 2 == 1 and 7 <= width <= 13, craft)
                    letters = set("".join(rows)) - {"."}
                    self.assertLessEqual(letters, set(pack["sprite_palette"]) | tp.DYNAMIC_LETTERS, craft)

    def test_each_pack_is_what_it_claims(self):
        packs = self.packs
        self.assertEqual(packs["classic"]["tiles"], {})                       # procedural: the look that has always shipped
        for name in ("earthen", "blueprint", "synthwave", "phosphor"):
            self.assertEqual(set(packs[name]["tiles"]), {"city", "ocean", "desert", "ice", "toxic", "blight", "gas", "rock"})
        self.assertEqual(packs["blueprint"]["space"]["glow"], 0)           # paper and ink: no glow
        self.assertGreaterEqual(packs["synthwave"]["space"]["glow"], 0.9)  # strong glow
        phosphor = packs["phosphor"]
        self.assertTrue(phosphor["space"]["scanlines"])
        shades = set(phosphor["tile_palette"].values())
        self.assertEqual(len(shades), 4)                                   # four shades of one green
        for entry in phosphor["style"].values():
            self.assertLessEqual({entry["a"], entry["b"], entry["c"]}, shades)
        for rgb in map(tp.rgb, shades):
            self.assertGreater(rgb[1], rgb[0])                             # green leads in every shade
            self.assertGreater(rgb[1], rgb[2])
        self.assertFalse(any(packs[n]["space"]["scanlines"] for n in ("classic", "earthen", "blueprint", "synthwave")))

    def test_classic_pack_equals_the_codes_current_look(self):
        classic = self.packs["classic"]
        self.assertEqual(classic["ui"], tp.UI_BASE)
        self.assertEqual(classic["style"], code_style())
        self.assertEqual(classic["kinds"], code_kinds())
        self.assertEqual(classic["sprites"], code_sprites())                  # the page's own small craft
        hull = re.search(r"const pal=\{h:'(#\w+)',m:'(#\w+)',d:'(#\w+)',a:accent,c:'(#\w+)',e:engine\|\|'',"
                         r"r:engine\|\|'(#\w+)',p:'(#\w+)',P:'(#\w+)'\};", RENDER_SRC)
        ink = re.search(r"const CRAFT_INK=\{([^}]*)\};", RENDER_SRC).group(1)       # o b B R g k K: the big craft letters
        self.assertEqual(classic["sprite_palette"], dict(zip("hmdcrpP", hull.groups())) | dict(re.findall(r"(\w):'(#\w+)'", ink)))
        base = re.search(r"const SPACE_BASE=\{base:'(#\w+)',nebula:\[([^\]]*)\],stars:\[([^\]]*)\],scanlines:false\};", RENDER_SRC)
        self.assertEqual(classic["space"], {"base": base.group(1), "nebula": re.findall(r"'(#\w+)'", base.group(2)),
                                         "stars": re.findall(r"'(#\w+)'", base.group(3)), "scanlines": False,
                                         "glow": float(re.search(r"const GLOW_REF=([.\d]+);", RENDER_SRC).group(1))})
        self.assertEqual(classic["tile_palette"], {})

    def test_default_stylesheet_is_the_current_one(self):
        original = normal_hex(source_css())
        # the module's transform, on the source
        css, defaults, keys = tp.themed_css(source_css())
        self.assertIn("--bg:#080d20", defaults)
        self.assertTrue(len(keys) > 100)
        self.assertEqual(undo_variables(":root{" + ";".join(defaults) + "}" + css), original)
        # and in the page that shipped: its :root defaults put back give the same stylesheet
        style = re.search(r"<style>(.*?)</style>", self.html, re.S).group(1)
        style = re.sub(r'html\[data-theme="[a-z0-9_-]+"\]\{[^}]*\}', "", style)
        style = style.replace(tp.THEME_CSS, "")
        # the template's own sheet, then the lanes' ui/*.css where the seams marker was (test_seams checks that part)
        head, marker, _ = original.partition("/*__CM_CSS__*/\n")
        page = undo_variables(style)
        self.assertEqual(marker, "/*__CM_CSS__*/\n")
        self.assertEqual(page[:len(head)], head)
        self.assertTrue(page[len(head):].startswith("/* ui/chrome.css */\n"), page[len(head):len(head) + 80])
        for role in ("bg", "panel", "border", "text", "muted", "danger", "ok"):
            self.assertIn(f"var(--{role})", style)
        self.assertNotIn("var(--accent)", style)                           # --accent is the panel's own sector colour
        self.assertIn(f"--accent:{tp.UI_BASE['accent']}", style)

    def test_role_accent_does_not_leak_into_the_panel_fallbacks(self):
        # the page already uses --accent as a per-panel colour (set inline, read with a fallback)
        users = re.findall(r"([^{}]*)\{[^{}]*var\(--accent", source_css())
        self.assertTrue(users)
        for selector in users:
            self.assertRegex(selector.strip(), r"^#panel", "a new reader of var(--accent,…) needs the same reset")
        self.assertIn("#panel,#side{--accent:initial}", tp.THEME_CSS)

    def test_mono_rules_keep_their_literals(self):
        css, defaults, keys = tp.themed_css(source_css())
        for gb in ("0f380f", "306230", "8bac0f", "9bbc0f"):
            self.assertNotIn(gb, keys)
        self.assertIn("body.gb #panel{background:#9bbc0ff2", css)

    def test_page_inlines_the_packs_and_the_theme_rules(self):
        start = self.html.index("const THEME_PACKS = ") + len("const THEME_PACKS = ")
        inlined, _ = json.JSONDecoder().raw_decode(self.html, start)
        self.assertEqual(inlined, self.packs)
        self.assertEqual(list(inlined), SHIPPED_PACKS)
        for placeholder in ("__THEME__", "__THEMEPACKS__", "__THEMENAMES__"):
            self.assertNotIn(placeholder, self.html)
        cfg = re.search(r"const THEME_CFG='([a-z0-9_-]+)';", self.html).group(1)
        self.assertIn(cfg, inlined)
        self.assertIn(f'<html lang="en" data-theme="{cfg}">', self.html)
        for name in SHIPPED_PACKS[1:]:
            self.assertIn(f'html[data-theme="{name}"]{{--bg:', self.html)
        self.assertNotIn('html[data-theme="classic"]', self.html)             # the defaults are the classic look

    def test_theme_key_chooses_the_page_theme(self):
        rc, html, err = render_with({"theme": "phosphor"})
        self.assertEqual(rc, 0, err[-1500:])
        self.assertIn('<html lang="en" data-theme="phosphor">', html)
        self.assertIn("const THEME_CFG='phosphor';", html)
        rc, html, err = render_with({"theme": "no-such-pack"})
        self.assertEqual(rc, 0, err[-1500:])
        self.assertIn('<html lang="en" data-theme="classic">', html)          # an unknown name falls back, loudly
        self.assertIn("theme 'no-such-pack'", err)
        rc, html, err = render_with({})
        self.assertEqual(rc, 0, err[-1500:])
        self.assertIn('<html lang="en" data-theme="classic">', html)          # the default

    def test_validator_rejects_broken_packs(self):
        import copy
        good = self.packs["earthen"]

        def broken(edit):
            pack = copy.deepcopy(good)
            edit(pack)
            return tp.validate(pack, "earthen")

        self.assertTrue(broken(lambda p: p["tiles"]["city"].append("bbbbbbbb")), "nine rows")
        self.assertTrue(broken(lambda p: p["tiles"].__setitem__("city", [r + "b" for r in p["tiles"]["city"]])), "nine wide")
        self.assertTrue(broken(lambda p: p["tiles"].__setitem__("ocean", ["z" * 8] * 8)), "a letter outside the palette")
        self.assertTrue(broken(lambda p: p["tiles"].__setitem__("rock", ["bbbbbbbb"] * 8)), "a flat tile")
        self.assertTrue(broken(lambda p: p["ui"].__setitem__("bg", "1b130b")), "a colour without #")
        self.assertTrue(broken(lambda p: p["ui"].pop("ok")), "a missing ui role")
        self.assertTrue(broken(lambda p: p["style"].pop("blight")), "a missing surface")
        self.assertTrue(broken(lambda p: p["sprites"].__setitem__("fighter", ["..hh..", "..hh.."])), "an even-width craft")
        self.assertTrue(broken(lambda p: p["space"].__setitem__("glow", 2)), "glow above 1")
        self.assertTrue(broken(lambda p: p.__setitem__("name", "other")), "name differs from the file")
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "earthen.json").write_text(json.dumps(good), encoding="utf-8")
            Path(tmp, "bad.json").write_text(json.dumps({"name": "bad"}), encoding="utf-8")
            Path(tmp, "worse.json").write_text("{not json", encoding="utf-8")
            seen = []
            self.assertEqual(list(tp.load_dir(tmp, seen.append)), ["earthen"])
            self.assertEqual(len(seen), 2)                                 # each broken file is named, never fatal

    def test_recolouring_keeps_roles_and_readability(self):
        pairs = [("#e6edf3", "#141922", 4.5), ("#aeb9c6", "#0d1119", 4.5), ("#d7e0ea", "#0b1428", 4.5),
                 ("#cfe0f5", "#141f33", 4.5), ("#6b7684", "#0b1428", 3), ("#8ea2b8", "#101c31", 3),
                 ("#7ee787", "#101c31", 3), ("#ff7b72", "#101c31", 3), ("#5fb4ff", "#0b1428", 3),
                 ("#a8caff", "#101e34", 3), ("#04210f", "#3dff9e", 3), ("#090e21", "#ffd166", 3)]
        for name, pack in self.packs.items():
            ui = pack["ui"]
            for role in ("bg", "panel", "border", "text", "muted", "accent", "danger", "ok"):
                self.assertEqual(tp.recolor(tp.UI_BASE[role], ui), ui[role], f"{name} {role}")
            if name == "classic":
                continue
            for fg, bg, floor in pairs:
                self.assertGreaterEqual(tp.contrast(tp.recolor(fg, ui), tp.recolor(bg, ui)), floor, f"{name} {fg} on {bg}")
            self.assertEqual(tp.pack_css(pack, ["141922"]).count("--c-141922:"), 1)
        # a light pack flips the dark UI to paper; a dark pack keeps it dark
        self.assertGreater(tp.luminance(tp.recolor("#141922", self.packs["blueprint"]["ui"])), 0.5)
        self.assertLess(tp.luminance(tp.recolor("#141922", self.packs["earthen"]["ui"])), 0.05)
        # red stays danger, green stays ok
        for name in ("earthen", "synthwave"):
            ui = self.packs[name]["ui"]
            self.assertEqual(tp._family("#ff4d5e"), "danger")
            self.assertEqual(tp._family("#3fb950"), "ok")
            gap = abs(tp._hls(tp.recolor("#ff4d5e", ui))[0] - tp._hls(ui["danger"])[0])
            self.assertLessEqual(min(gap, 360 - gap), 4)                   # the recoloured red keeps the pack's red

    def test_config_has_the_theme_key(self):
        import config
        self.assertEqual(config.DEFAULTS["theme"], "classic")
        with tempfile.TemporaryDirectory() as tmp:
            for raw, want in (({"theme": "phosphor"}, "phosphor"), ({"theme": 3}, "classic"), ({}, "classic")):
                conf = Path(tmp, "c.json")
                conf.write_text(json.dumps(raw), encoding="utf-8")
                self.assertEqual(config.load(str(conf))["theme"], want)
class SpriteCraftTests(unittest.TestCase):
    """The craft the page draws: the page's own small grids plus the neutral skin's (baked from its 3D hulls by
    tools/bake_sprites.py; the bake's own checks are in test_3d_craft.py), the effects section, and the pack fields.
    (The owner's private skin has its own shape tests in test_skin_private.py.)"""

    # width range, height range of each grid (the boost variant may grow a longer flare). The skin's craft are baked at
    # the o5 bar: pilot 48 wide, carrier 40x64, raider 32x28, its problem drone 24x24 (drawn as foe_drone)
    SIZES = {"pilot": ((48, 48), (32, 48)), "pilot_boost": ((48, 48), (42, 58)), "carrier": ((40, 40), (64, 64)),
             "raider": ((32, 32), (28, 28)), "foe_drone": ((24, 24), (24, 24)), "cargo": ((9, 11), (15, 19)),
             "drone": ((7, 7), (7, 7)), "probe": ((9, 9), (5, 5)), "gate": ((13, 13), (13, 13))}
    LETTERS = set("ohmdbBRcaegkKxXyYrpPCTwvil")    # every letter the page can colour (hullPal + CRAFT_INK)
    PAGE_OWN = ("cargo", "drone", "probe", "gate")  # the page's own hand-drawn grids: odd widths
    EFFECTS = ("fxTrail", "fxBoostRings", "fxBolts", "fxSpark", "fxVolley", "fxShield", "fxLockOn", "fxLockFoe",
               "fxGateShimmer", "fxMergeBanner", "drawMergeBanner")

    @classmethod
    def setUpClass(cls):
        cls.page_sprites = code_sprites()
        cls.skin = tp.load_skin("neutral", [str(ROOT / "skins")])
        skin = dict(cls.skin["sprites"])
        skin["foe_drone"] = skin.pop("drone")            # as the page merges it (skinCraft): its own drone stays
        cls.sprites = dict(cls.page_sprites, **skin)
        cls.html = PAGE.read_text(encoding="utf-8")
        cls.packs = tp.load_dir(str(ROOT / "themes"))

    def test_the_page_carries_its_own_small_craft_and_the_skin_carries_the_characters(self):
        block = re.search(r"const SPRITES=\{.*?\n\};", self.html, re.S).group(0)
        for name in ("fighter", "cargo", "drone", "probe", "beacon", "gate"):
            self.assertIn(f"\n  {name}:[", block, f"the rendered page carries {name}")
        for name in tp.SPRITES_SKIN:
            self.assertNotIn(f"\n  {name}:[", block, f"{name} comes from the skin")
        self.assertEqual(set(self.skin["sprites"]), (set(self.SIZES) & set(tp.SPRITES_SKIN)) | set(tp.SKIN_FOES))
        self.assertIn("if(out.drone){ out.foe_drone=out.drone; delete out.drone; }", RENDER_SRC, "the skin's drone never replaces the page's")

    def test_each_grid_is_rectangular_odd_and_the_size_the_brief_gave(self):
        for name, ((w0, w1), (h0, h1)) in self.SIZES.items():
            rows = self.sprites[name]
            with self.subTest(sprite=name):
                self.assertEqual(len({len(r) for r in rows}), 1, "every row the same width")
                if name in self.PAGE_OWN:
                    self.assertEqual(len(rows[0]) % 2, 1, "odd width: a centre column to turn about")
                self.assertTrue(w0 <= len(rows[0]) <= w1, f"width {len(rows[0])}")
                self.assertTrue(h0 <= len(rows) <= h1, f"height {len(rows)}")

    def test_every_letter_is_one_the_page_can_colour(self):
        for name in self.SIZES:
            self.assertLessEqual(set("".join(self.sprites[name])) - {"."}, self.LETTERS, name)

    def test_every_craft_is_outlined_by_the_renderer_not_by_letters(self):
        # spriteArt paints a one-pixel dark halo under every cell, so a grid needs no ring of 'o' cells (a thick
        # letter outline is what made the big craft read as blobs).
        halo = re.search(r"g\.fillStyle=GB\?'#0f380f':SHADE\.out; cells\.forEach\(c=>g\.fillRect\(c\[0\]-1,c\[1\]-1,cell\+2,cell\+2\)\);", RENDER_SRC)
        self.assertIsNotNone(halo, "the halo pass before the cell fills")
        for name in tp.SPRITES_SKIN:
            rows = self.sprites[name]
            self.assertLess(sum(r.count("o") for r in rows), 0.12 * sum(len(r) - r.count(".") for r in rows), name + " is not ringed in outline cells")

    def test_the_gate_is_a_ring_and_the_boost_is_the_same_craft_with_a_longer_flare(self):
        gate = self.sprites["gate"]
        self.assertEqual(gate[6][6], ".", "hollow centre")
        pilot, boost = self.sprites["pilot"], self.sprites["pilot_boost"]
        self.assertEqual(boost[:len(pilot)], pilot, "the same hull, wings and nose")
        self.assertGreater(len(boost), len(pilot) + 2, "a longer tail")
        self.assertTrue(set("".join(boost[len(pilot):])) <= set(".oBbg"))

    def test_the_neutral_shapes(self):
        def mask(rows):
            return ["".join("." if ch == "." else "#" for ch in r) for r in rows]

        def widths(rows):
            return [len(r.strip(".")) for r in rows if r.strip(".")]

        def nozzles(rows):
            row = next(r for r in reversed(rows) if "e" in r)
            return len(re.findall(r"e+", row))
        # the pilot (the Skiff): a pointed bow, the bridge glass in the front half, slate deck in three tones with ochre
        # crates, one fan nozzle at the stern; its outline symmetric (the light falls from the nose-left side)
        pilot = self.sprites["pilot"]
        self.assertLessEqual(widths(pilot)[0], 2, "a pointed bow")
        self.assertTrue(any("C" in r for r in pilot[:len(pilot) // 2]), "the canopy glass sits in the front half")
        self.assertTrue(all(r == r[::-1] for r in mask(pilot)), "an outline symmetric about the centre line")
        self.assertGreaterEqual(sum(r.count("b") + r.count("B") + r.count("v") for r in pilot), 120, "the accent wings")
        self.assertTrue({"b", "B", "v"} <= set("".join(pilot)), "the accent in three tones")
        self.assertGreaterEqual(len(pilot[0]), len(pilot), "a squat craft: at least as wide as long")
        # the carrier (the Hauler): a long spine, bridge glass, a ring, cargo pods, its exhausts drawn as engine cells
        # (the page puts the engine glows on them, craftNozzles)
        carrier = self.sprites["carrier"]
        self.assertGreater(len(carrier), 1.3 * len(carrier[0]), "a long carrier")
        self.assertTrue(any("C" in r for r in carrier), "bridge glass")
        self.assertTrue(all(r == r[::-1] for r in mask(carrier)))
        self.assertGreaterEqual(nozzles(carrier), 1, "the exhausts")
        self.assertIn("craftNozzles('carrier')", RENDER_SRC, "the glows sit on the exhausts the grid draws")
        # the raider, one per problem: a crimson delta (no legs, no feelers: a craft at any size), the status-coloured
        # cockpit window flickering, gunmetal spine, twin engine pods at the tail
        raider = self.sprites["raider"]
        self.assertTrue(all(r == r[::-1] for r in mask(raider)))
        self.assertLessEqual(widths(raider)[0], 2, "a narrow nose")
        self.assertGreaterEqual("".join(raider).count("a"), 2, "the cockpit window")
        self.assertTrue({"k", "K", "R"} <= set("".join(raider)))
        runs = [len(re.findall(r"[^.]+", r)) for r in raider if r.strip(".")]
        widest = widths(raider).index(max(widths(raider)))
        self.assertEqual(runs[:widest], [1] * widest, "one solid hull up to the wing tips")
        self.assertGreaterEqual(max(widths(raider)), len(raider[0]) - 2, "the wing tips reach the edges")
        w = widths(raider)[:widest + 1]
        self.assertEqual(w, sorted(w), "a delta: it only widens to the tips")
        self.assertEqual(nozzles(raider), 2, "two engine nozzles at the tail")

    def test_effects_section_exists(self):
        self.assertIn("// ---- EFFECTS:", RENDER_SRC)
        for name in self.EFFECTS:
            self.assertIn(f"function {name}(", RENDER_SRC)
        self.assertIn("SKIN_NAMES.merged", RENDER_SRC)               # the banner's words are the skin's
        self.assertIn("SKIN_NAMES.carrier+' · '+CM.word('home')", RENDER_SRC)    # the label plates' carrier word is the skin's too
        self.assertIn("REDUCED", RENDER_SRC.split("// ---- EFFECTS:")[1].split("const FILE_ICONS")[0], "the effects read the motion preference")
        self.assertIn("drawMergeBanner(t);", RENDER_SRC)               # the banner is drawn by the frame
        self.assertIn("fxMergeBanner(taskTitle(t))", RENDER_SRC)      # and raised by a thread turning merged, by its short title

    def test_themes_carry_the_small_craft_and_never_the_characters(self):
        for name, pack in self.packs.items():
            self.assertEqual(set(pack["sprites"]) & set(tp.SPRITES_SKIN), set(), name)
            self.assertLessEqual(set(tp.SPRITES), set(pack["sprites"]), name + " keeps its three own craft")
            self.assertTrue(set("obBRgkK") <= set(pack["sprite_palette"]), name + " colours the big craft's letters")
        self.assertEqual(self.packs["classic"]["sprites"], self.page_sprites)

    def test_validator_takes_the_big_craft_limits_in_a_skin(self):
        import copy
        good = self.skin

        def verdict(name, rows, **palette):
            skin = copy.deepcopy(good)
            skin["sprites"][name] = rows
            if palette:
                skin["sprite_palette"] = palette
            return tp.validate_skin(skin, "neutral")

        for name in good["sprites"]:                                 # the shipped grids pass
            self.assertEqual(verdict(name, good["sprites"][name]), [], name)
        self.assertTrue(verdict("pilot", ["." * 15] * 14 + ["." * 14]), "ragged rows")
        self.assertEqual(len(good["sprites"]["pilot"][0]) % 2, 0, "either parity: the baked pilot is 48 wide")
        self.assertTrue(verdict("carrier", good["sprites"]["carrier"] + [good["sprites"]["carrier"][0]] * 9), "73 rows, past the limit")
        self.assertTrue(verdict("raider", ["." * 50] * 12), "50 wide")
        self.assertTrue(verdict("pilot", [r.replace("h", "q") for r in good["sprites"]["pilot"]]), "a letter nobody colours")
        self.assertEqual(verdict("pilot", [r.replace("h", "q") for r in good["sprites"]["pilot"]], q="#112233"), [],
                         "...unless the skin does")
        self.assertTrue(verdict("flagship", good["sprites"]["pilot"]), "an unknown craft name")
        self.assertTrue(verdict("gate", self.page_sprites["gate"]), "the warp gate is a theme's, not a skin's")
        skin = copy.deepcopy(good)
        del skin["sprites"]["raider"]
        self.assertTrue(tp.validate_skin(skin, "neutral"), "pilot, carrier and raider stay required")
        del skin["sprites"]["pilot_boost"]
        skin["sprites"]["raider"] = good["sprites"]["raider"]
        self.assertEqual(tp.validate_skin(skin, "neutral"), [], "the boost variant is optional")


class SkinTests(unittest.TestCase):
    """skins/*.json: loading, validation, the config keys, and the page that carries the skin."""

    @classmethod
    def setUpClass(cls):
        cls.skin = tp.load_skin("neutral", [str(ROOT / "skins")])

    def custom_dir(self, tmp, name="mine", words=None, **extra):
        import copy
        skin = copy.deepcopy(self.skin)
        skin["name"] = name
        skin["names"] = dict(skin["names"], **(words or {}))
        skin.update(extra)
        Path(tmp, name + ".json").write_text(json.dumps(skin), encoding="utf-8")
        return tmp

    def test_the_neutral_skin_ships_and_validates(self):
        warnings = []
        self.assertIsNotNone(tp.load_skin("neutral", [str(ROOT / "skins")], warnings.append))
        self.assertEqual(warnings, [])
        self.assertEqual(tp.validate_skin(self.skin, "neutral"), [])
        self.assertEqual(self.skin["names"], {"carrier": "CARRIER", "merged": "THREAD MERGED", "worst": "BLIGHT",
                                              "wingmate": "Scout"})
        frames = self.skin["portrait"]["frames"]
        self.assertEqual(set(frames), {"IDLE", "BLINK", "TALK"})
        self.assertEqual({(len(r[0]), len(r)) for r in frames.values()}, {(32, 32)})
        self.assertEqual(self.skin["portrait"]["palette"].keys() & {"."}, set())

    def test_config_has_the_skin_keys(self):
        import config
        self.assertEqual((config.DEFAULTS["skin"], config.DEFAULTS["skins_dir"]), ("neutral", ""))
        with tempfile.TemporaryDirectory() as tmp:
            for raw, want in (({"skin": "mine"}, "mine"), ({"skin": 3}, "neutral"), ({}, "neutral")):
                conf = Path(tmp, "c.json")
                conf.write_text(json.dumps(raw), encoding="utf-8")
                self.assertEqual(config.load(str(conf))["skin"], want)

    def test_skin_loading_prefers_skins_dir_and_falls_back_loudly(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.custom_dir(tmp, "mine", words={"wingmate": "Pip"})
            self.custom_dir(tmp, "neutral", words={"wingmate": "Mine"})        # shadows the shipped one
            Path(tmp, "broken.json").write_text("{not json", encoding="utf-8")
            Path(tmp, "bad.json").write_text(json.dumps({"name": "bad"}), encoding="utf-8")
            seen = []
            cfg = {"skin": "mine", "skins_dir": tmp}
            self.assertEqual(tp.active_skin(cfg, seen.append)["names"]["wingmate"], "Pip")
            self.assertEqual(tp.wingmate_name(cfg), "Pip")
            self.assertEqual(tp.active_skin({"skins_dir": tmp}, seen.append)["names"]["wingmate"], "Mine")
            self.assertEqual(seen, [])
            for broken in ("broken", "bad", "missing"):
                seen.clear()
                got = tp.active_skin({"skin": broken, "skins_dir": tmp}, seen.append)
                self.assertEqual(got["names"]["wingmate"], "Mine", broken + " falls back to neutral")
                self.assertTrue(any(broken in m for m in seen), seen)
            self.assertEqual(tp.wingmate_name({"skin": "missing"}), "Scout")
            self.assertIsNone(tp.load_skin("../etc/passwd", [tmp]), "a skin name is a plain file name")

    def test_no_skin_at_all_falls_back_to_the_default_words(self):
        # (the page itself with no skins/ folder: NoSkinPageTests)
        with tempfile.TemporaryDirectory() as tmp:
            here = tp.HERE
            tp.HERE = tmp
            try:
                self.assertIsNone(tp.active_skin({}))
                self.assertEqual(tp.wingmate_name({}), "Scout")
                self.assertEqual(tp.skin_names({}), tp.DEFAULT_NAMES)
            finally:
                tp.HERE = here

    def test_validator_rejects_broken_skins(self):
        import copy

        def broken(edit):
            skin = copy.deepcopy(self.skin)
            edit(skin)
            return tp.validate_skin(skin, "neutral")

        self.assertTrue(broken(lambda s: s.__setitem__("name", "other")), "name differs from the file")
        self.assertTrue(broken(lambda s: s["names"].pop("merged")), "a missing word")
        self.assertTrue(broken(lambda s: s["names"].__setitem__("carrier", "x" * 25)), "a word over 24 characters")
        self.assertTrue(broken(lambda s: s["names"].__setitem__("worst", "<b>")), "markup in a word")
        self.assertTrue(broken(lambda s: s["names"].__setitem__("extra", "x")), "an unknown word")
        self.assertTrue(broken(lambda s: s["portrait"]["frames"].pop("IDLE")), "a portrait needs IDLE")
        self.assertTrue(broken(lambda s: s["portrait"]["frames"]["TALK"].pop()), "frames differ in size")
        self.assertTrue(broken(lambda s: s["portrait"]["frames"].__setitem__("IDLE", ["Z" * 32] * 32)), "a letter outside the palette")
        self.assertTrue(broken(lambda s: s["portrait"]["frames"].__setitem__("IDLE", ["." * 49] * 49)), "past 48x48")
        self.assertTrue(broken(lambda s: s["portrait"].__setitem__("palette", {"A": "07101e"})), "a colour without #")
        self.assertTrue(broken(lambda s: s.__setitem__("sprite_palette", {"qq": "#112233"})), "a two-letter palette key")
        for letter in "ae":
            errs = broken(lambda s: s.__setitem__("sprite_palette", {letter: "#112233"}))
            self.assertTrue(errs and "status colour" in errs[0], f"{letter} belongs to the page: {errs}")
        self.assertEqual(broken(lambda s: s.__setitem__("sprite_palette", {"q": "#112233"})), [], "letters of its own")
        self.assertEqual(broken(lambda s: s["names"].__setitem__("merged", "x" * 16)), [], "16 characters fit the banner")
        self.assertTrue(broken(lambda s: s["names"].__setitem__("merged", "x" * 17)), "17 do not: the banner is 420 px")
        self.assertEqual(broken(lambda s: s["names"].__setitem__("carrier", "x" * 24)), [], "other words go to 24")
        self.assertEqual(broken(lambda s: s.pop("portrait")), [], "the portrait is optional")
        self.assertEqual(tp.validate_skin([], None), ["a skin is a JSON object"])

    def test_a_theme_refuses_the_characters_craft(self):
        import copy
        pack = copy.deepcopy(tp.load_dir(str(ROOT / "themes"))["classic"])
        pack["sprites"]["pilot"] = self.skin["sprites"]["pilot"]
        errs = tp.validate(pack, "classic")
        self.assertTrue(errs and "skin" in errs[0], errs)

    def test_the_page_inlines_the_skin_and_names_the_wingmate_after_it(self):
        rc, html, err = render_with({})
        self.assertEqual(rc, 0, err[-1500:])
        start = html.index("const SKIN = ") + len("const SKIN = ")
        inlined, _ = json.JSONDecoder().raw_decode(html, start)
        self.assertEqual(inlined, self.skin)
        self.assertNotIn("__SKIN__", html)
        self.assertIn('<b id="matename">Scout</b>', html)
        world = json.loads(re.search(r"(?m)^const W = (.*);$", html).group(1))
        self.assertEqual(world["wingmate"]["name"], "Scout")
        with tempfile.TemporaryDirectory() as tmp:
            self.custom_dir(tmp, "mine", words={"wingmate": "Pip", "merged": "ALL MERGED"})
            rc, html, err = render_with({"skin": "mine", "skins_dir": tmp})
            self.assertEqual(rc, 0, err[-1500:])
            self.assertIn('<b id="matename">Pip</b>', html)
            self.assertIn('"merged":"ALL MERGED"', html)
            world = json.loads(re.search(r"(?m)^const W = (.*);$", html).group(1))
            self.assertEqual(world["wingmate"]["name"], "Pip")
            rc, html, err = render_with({"skin": "mine", "skins_dir": tmp, "wingmate": {"name": "Own"}})
            self.assertIn('<b id="matename">Own</b>', html, "the config's own name beats the skin's")
            rc, html, err = render_with({"skin": "no-such-skin"})
            self.assertEqual(rc, 0, err[-1500:])
            self.assertIn("skin 'no-such-skin'", err)
            self.assertIn('"carrier":"CARRIER"', html)                       # the neutral skin stands in

    def legacy_file(self, tmp, raw):
        path = Path(tmp, "legacy-ids.json")
        path.write_text(json.dumps(raw), encoding="utf-8")
        return str(path)

    def test_a_build_with_no_legacy_file_has_no_old_ids_and_renders_none(self):
        # the shipped tool carries no old name: no table in the code (the export scan reads it), an empty one in every page
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(tp.load_legacy(str(Path(tmp, "missing.json"))), ({}, {}))
            Path(tmp, "bad.json").write_text("[1, 2]", encoding="utf-8")
            self.assertEqual(tp.load_legacy(str(Path(tmp, "bad.json"))), ({}, {}))
            Path(tmp, "worse.json").write_text("{", encoding="utf-8")
            self.assertEqual(tp.load_legacy(str(Path(tmp, "worse.json"))), ({}, {}))
        self.assertEqual((tp.THEME_ALIASES, tp.SURFACE_ALIASES), ({}, {}), "testenv points the file at /dev/null")
        rc, html, err = render_with({"theme": "old-look"})
        self.assertEqual(rc, 0, err[-1500:])
        self.assertIn("is not a pack", err)
        self.assertIn("al={},", html)
        self.assertIn("const THEME_ALIAS = {};", html)

    def test_old_theme_ids_still_choose_the_renamed_themes(self):
        packs = tp.load_dir(str(ROOT / "themes"))
        with tempfile.TemporaryDirectory() as tmp:
            path = self.legacy_file(tmp, {"themes": {"old-look": "classic", "older": "earthen", "Bad Id": "classic",
                                                     "x": "Not A Name", "y": 3}, "surfaces": {"old-rock": "blight", "z": "nope"}})
            themes, surfaces = tp.load_legacy(path)
            self.assertEqual(themes, {"old-look": "classic", "older": "earthen"}, "plain names only")
            self.assertEqual(surfaces, {"old-rock": "blight"}, "and a surface alias must name a surface")
            with mock.patch.dict(tp.THEME_ALIASES, themes, clear=True):
                for old, new in themes.items():
                    self.assertIn(new, packs)
                    self.assertEqual(tp.resolve_theme(old, packs), new)
                self.assertEqual(tp.resolve_theme("no-such", packs), "")
                self.assertEqual(tp.resolve_theme(3, packs), "")
            for old, new in themes.items():
                rc, html, err = render_with({"theme": old}, env={"CONTAINER_LEGACY_IDS": path})
                self.assertEqual(rc, 0, err[-1500:])
                self.assertIn(f'<html lang="en" data-theme="{new}">', html)
                self.assertNotIn("is not a pack", err)
                self.assertIn(f'"{old}":"{new}"', html)                      # the page maps a saved choice the same way

    def test_the_wingmate_name_is_text_whatever_the_config_says(self):
        # a number, a list or a bare string where the wingmate object belongs must not be a dead page
        for raw, want in (({"off": True, "name": 5}, "5"), ({"name": 5}, "5"), ({"name": "x" * 40}, "x" * 24),
                          ({"name": ["a"]}, "[&#x27;a&#x27;]"), ("Pip", "Scout"), (5, "Scout")):
            with self.subTest(wingmate=raw):
                rc, html, err = render_with({"wingmate": raw})
                self.assertEqual(rc, 0, err[-1500:])
                self.assertIn(f'<b id="matename">{want}</b>', html)
                world = json.loads(re.search(r"(?m)^const W = (.*);$", html).group(1))
                self.assertIsInstance(world["wingmate"]["name"], str)

    def test_a_theme_file_with_the_old_surface_name_still_loads_with_a_warning(self):
        old, new = "old-rock", "blight"
        pack = json.loads((ROOT / "themes" / "earthen.json").read_text(encoding="utf-8"))
        pack["name"] = "mine"
        pack["style"][old] = pack["style"].pop(new)
        pack["tiles"][old] = pack["tiles"].pop(new)
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(tp.SURFACE_ALIASES, {old: new}, clear=True):
            Path(tmp, "mine.json").write_text(json.dumps(pack), encoding="utf-8")
            seen = []
            packs = tp.load_dir(tmp, seen.append)
            self.assertIn("mine", packs)
            self.assertEqual(tp.validate(packs["mine"], "mine"), [])
            self.assertIn(new, packs["mine"]["style"])
            self.assertIn(new, packs["mine"]["tiles"])
            self.assertNotIn(old, packs["mine"]["style"])
            self.assertEqual(len(seen), 1)
            self.assertIn(f"{old!r} is now {new!r}", seen[0])
            pack["style"][new] = dict(pack["style"][old])                      # both names: the file is ambiguous
            Path(tmp, "mine.json").write_text(json.dumps(pack), encoding="utf-8")
            self.assertEqual(tp.load_dir(tmp, lambda msg: None), {})
        with tempfile.TemporaryDirectory() as tmp:                             # no table: the old name is simply not a surface
            pack["style"].pop(new)
            Path(tmp, "mine.json").write_text(json.dumps(pack), encoding="utf-8")
            self.assertEqual(tp.load_dir(tmp, lambda msg: None), {})


class SkinPageTests(unittest.TestCase):
    """The page with a minimal skin (no boost variant, no portrait) draws every craft and every theme."""

    @classmethod
    def setUpClass(cls):
        if not shutil.which("node"):
            raise unittest.SkipTest("node not installed")
        cls.tmp = tempfile.TemporaryDirectory()
        skin = tp.load_skin("neutral", [str(ROOT / "skins")])
        bare = {"name": "bare", "names": skin["names"],
                "sprites": {k: skin["sprites"][k] for k in ("pilot", "carrier", "raider")}}
        for name in ("Alpha", "Beta"):
            (Path(cls.tmp.name, "work") / name).mkdir(parents=True)
            (Path(cls.tmp.name, "work") / name / "notes.md").write_text("fixture\n", encoding="utf-8")
        skins = Path(cls.tmp.name, "skins")
        skins.mkdir()
        (skins / "bare.json").write_text(json.dumps(bare), encoding="utf-8")
        proc = render_fixture(cls.tmp.name, skin="bare", skins_dir=str(skins))
        if proc.returncode:
            raise AssertionError(proc.stderr[-2000:])
        cls.js = Path(cls.tmp.name, "page.js")
        page_js(Path(cls.tmp.name, "page.html"), cls.js)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_a_skin_without_boost_or_portrait_draws(self):
        run_page_driver(self, self.js, SKINLESS_DRIVER)

    def test_every_pack_and_the_mono_palette_draw_the_craft_set(self):
        run_page_driver(self, self.js, CRAFT_DRIVER)


class NoSkinPageTests(unittest.TestCase):
    """A tool folder without skins/ at all (a stripped copy, a bad install) still renders a page that draws
    every craft: the skin's craft fall back to the page's small fighter and the words to the default ones."""

    @classmethod
    def setUpClass(cls):
        if not shutil.which("node"):
            raise unittest.SkipTest("node not installed")
        cls.tmp = tempfile.TemporaryDirectory()
        tool = Path(cls.tmp.name, "tool")
        shutil.copytree(ROOT, tool, ignore=shutil.ignore_patterns(".*", "__pycache__", "dist", "skins", "*.png"))
        for name in ("Alpha", "Beta"):
            Path(cls.tmp.name, "work", name).mkdir(parents=True)
            Path(cls.tmp.name, "work", name, "notes.md").write_text("fixture\n", encoding="utf-8")
        cfg = fixture_config(cls.tmp.name, skin="mine")      # a name nothing answers to: no skins/ here at all
        env = dict(os.environ, CONTAINER_CONFIG=str(cfg), CONTAINER_OUT=str(Path(cls.tmp.name, "page.html")))
        cls.proc = subprocess.run([sys.executable, str(tool / "render-game.py")], cwd=tool, env=env,
                                  capture_output=True, text=True, timeout=120)
        if cls.proc.returncode:
            raise AssertionError(cls.proc.stderr[-2000:])
        cls.js = Path(cls.tmp.name, "page.js")
        page_js(Path(cls.tmp.name, "page.html"), cls.js)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_the_render_says_so_and_does_not_fail(self):
        self.assertFalse(Path(self.tmp.name, "tool", "skins").exists())
        self.assertIn("no skin could be loaded", self.proc.stderr)
        html = Path(self.tmp.name, "page.html").read_text(encoding="utf-8")
        self.assertIn("const SKIN = {};", html)
        self.assertIn('<b id="matename">Scout</b>', html)

    def test_the_page_draws_with_no_skin(self):
        run_page_driver(self, self.js, SKINLESS_DRIVER)

    def test_every_pack_and_the_mono_palette_draw_the_craft_set_with_no_skin(self):
        run_page_driver(self, self.js, CRAFT_DRIVER)


# ---------------------------------------------------------------------------------------------
# Bug-hunt regressions (page-world module). The page half runs the real page script in node against
# the stub DOM above with one driver per scenario; the world half renders a fixture workspace.
# ---------------------------------------------------------------------------------------------
DRIVER_HEAD = r"""
;(async function(){
  const out = [];
  const ok = (n, c) => out.push((c ? 'PASS ' : 'FAIL ') + n);
  const later = () => new Promise((r) => setImmediate(r));
  const settle = async () => { for (let i = 0; i < 10; i++) await later(); };
  // universal() drops every write: cap() keeps what the page writes to an element
  const cap = () => { const s = {}; return new Proxy(universal(), {
    get: (t, k) => (k in s ? s[k] : t[k]), set: (t, k, v) => { s[k] = v; return true; } }); };
  const T = (id, status, o) => Object.assign({ id: id, title: 'thread ' + id, status: status,
    runner: 'claude', model: '', target: W.root_path, runs: 1, retry_due: false,
    started: new Date().toISOString() }, o || {});
"""
DRIVER_TAIL = r"""
  return out;
})()
"""

# a live page (served by the bridge): the thread panel, the thread list, the diff, the composer
LIVE_DRIVER = r"""
  ok('page is live on 127.0.0.1', LIVE === true);
  const timers = [];
  global.setInterval = (fn, ms) => {
    timers.push({ fn: fn, ms: ms, live: true }); return timers.length; };
  global.clearInterval = (id) => { if (timers[id - 1]) timers[id - 1].live = false; };
  const panelPolls = () => timers.filter((t) => t.live && t.ms === 1500).length;
  let detail = null;
  const setDetail = (o) => {
    detail = Object.assign(T('t1', 'failed', { retry_due: true, mode: 'inplace', repo: false }),
      { run: 1, lines: [], more: false, offset: 0, prompts: [{ n: 1, text: 'do it' }] },
      o || {}); };
  global.fetch = async (url) => {
    const body = String(url).startsWith('/task?')
      ? () => JSON.parse(JSON.stringify(detail)) : () => ({});
    return { ok: true, status: 200, json: async () => body() }; };
  ok('a failed run with a retry due is called retrying, not failed',
    taskWord({ status: 'failed', retry_due: true }) === 'retrying'
    && taskWord({ status: 'failed' }) === 'failed');

  // F1: the retry starts behind the open panel's back
  elCache.tvstatus = cap(); elCache.tvnote = cap();
  setDetail({ note: "setup runs only in a thread's own worktree; this one works in place, "
    + 'so it was skipped' });
  W.agent_tasks = [T('t1', 'failed', { retry_due: true })];
  openTask('t1'); await settle();
  ok('F1 the panel loaded run 1', !!TV && TV.shown === 1 && TV.status === 'failed');
  ok('F1 failed with a retry due reads retrying', elCache.tvstatus.textContent === 'retrying');
  ok('F1 the panel keeps polling while a retry is due', panelPolls() === 1);
  ok('F10 the skipped-setup note shows under the thread header',
    String(elCache.tvnote.textContent).includes('was skipped') && elCache.tvnote.hidden === false);
  setDetail({ status: 'running', retry_due: false, runs: 2, run: 2,
    prompts: [{ n: 1, text: 'do it' }, { n: 2, text: 'fix it', auto: true }] });
  applyTasks({ tasks: [T('t1', 'running', { runs: 2 })] }); await settle();
  ok('F1 the list poll moves the panel to the retry run',
    TV.shown === 2 && TV.status === 'running' && TV.runs === 2);
  ok('F1 and the panel keeps following it', panelPolls() === 1);

  // a settled panel has no poll; a new run found by the list poll wakes it
  setDetail({ status: 'failed', retry_due: false });
  W.agent_tasks = [T('t1', 'failed')];
  openTask('t1'); await settle();
  ok('F8 a settled thread stops its poll', panelPolls() === 0);
  setDetail({ status: 'checking', retry_due: false, runs: 2, run: 2,
    prompts: [{ n: 1, text: 'a' }, { n: 2, text: 'check it', kind: 'check', runner: 'claude' }] });
  applyTasks({ tasks: [T('t1', 'checking', { runs: 2 })] }); await settle();
  ok('F8 the list poll wakes the panel for a check',
    panelPolls() === 1 && TV.shown === 2 && TV.status === 'checking');

  // a world refresh must redraw the thread list when it brings a new thread
  elCache.wrap = cap();
  elCache.wrap.classList = { contains: (c) => c === 'side', toggle: () => {}, add: () => {},
    remove: () => {} };
  elCache.tasks = cap(); elCache.tasks.innerHTML = 'stale list';
  stopTaskPoll();
  W.agent_tasks = [T('a1', 'review', { title: 'first thread' })];
  TASKS_AT = 0;
  applyWorld({ chars: W.chars, quests: W.quests, agent_tasks: [
    T('a1', 'review', { title: 'first thread' }),
    T('b2', 'running', { title: 'second thread' })] });
  ok('F9 a world refresh redraws the thread list with the new thread',
    String(elCache.tasks.innerHTML).includes('second thread'));

  // two diff requests answered out of order: the newest wins
  setDetail({ status: 'review', retry_due: false });
  W.agent_tasks = [T('t1', 'review')];
  openTask('t1'); await settle();
  elCache.tdiff = cap();
  const pending = [], prevFetch = global.fetch;
  global.fetch = async (url) => {
    if (!String(url).startsWith('/task/diff')) return prevFetch(url);
    const k = pending.length + 1;
    return new Promise((res) => pending.push(() => res({ ok: true, status: 200,
      json: async () => ({ files: [{ path: 'diff-' + k + '.txt', add: 1, del: 0 }],
        patch: '' }) })));
  };
  loadDiff(); loadDiff();
  ok('F10 two diff requests are in flight', pending.length === 2);
  pending[1](); await settle(); pending[0](); await settle();
  const drawn = String(elCache.tdiff.innerHTML);
  ok('F10 the older diff answer does not overwrite the newer one',
    drawn.includes('diff-2.txt') && !drawn.includes('diff-1.txt'));
  global.fetch = prevFetch;

  // F10: setup runs only in a thread's own worktree
  elCache.ciso = cap(); elCache.csetup = cap();
  elCache.ciso.checked = false; syncSetup();
  ok('F10 the setup field is off outside its own worktree', elCache.csetup.disabled === true);
  elCache.ciso.checked = true; syncSetup();
  ok('F10 and on again inside one', elCache.csetup.disabled === false);
"""

LOCALHOST_DRIVER = r"""
  ok('F7 the page is live on localhost', LIVE === true);
  ok('F7 the way back to the bridge is this origin, not a fixed port',
    LIVE_URL === 'http://localhost:7431/');
"""

FILE_DRIVER = r"""
  ok('a file page is not live', LIVE === false);
  ok('a file page points at the default bridge', LIVE_URL === 'http://127.0.0.1:7428/');
"""

# a page the bridge served to a paired phone: live because the bridge says so, desktop-only
# controls off
PHONE_DRIVER = r"""
  ok('a phone page is live and remote, not a desktop',
    LIVE === true && REMOTE === true && DESK === false);
  ok('its way back to the bridge is its own origin', LIVE_URL === 'https://phone.example.com/');
  const sent = [];
  let detail = T('t1', 'review', { mode: 'worktree', branch: 'cm/t1', base_branch: 'main',
    run: 1, lines: [], more: false, offset: 0, prompts: [{ n: 1, text: 'do it' }],
    merge_blockers: ['verify failed (exit 1)'] });
  global.fetch = async (url, opt) => {
    sent.push({ url: String(url), body: opt && opt.body });
    const body = String(url).startsWith('/task?') ? JSON.parse(JSON.stringify(detail)) : {};
    return { ok: true, status: 200, json: async () => body }; };
  // what the bridge answers 404 to never leaves the page
  for (const p of ['/pane?seat=hub&lines=5', '/control?after=0', '/agent/unit?uid=x',
    '/file?path=/x', '/open', '/door', '/say', '/permit', '/dispatch', '/verb', '/resolve',
    '/checkin', '/task/check', '/remote/world?machine=x', '/remote/pane?machine=x&target=y',
    '/remote/say', '/remote/render', '/phone']) {
    let refused = false;
    try { await api(p, { method: 'POST' }); }
    catch (e) { refused = /desktop only/.test(e.message); }
    ok('a phone page never asks for ' + p, refused);
  }
  const desktopRoute = new RegExp('^/(' + ['pane', 'control', 'agent', 'file', 'open', 'door',
    'say', 'permit', 'dispatch', 'verb', 'resolve', 'checkin', 'remote', 'phone'].join('|') + ')');
  ok('no desktop route went out',
    sent.every((s) => !desktopRoute.test(s.url) && s.url !== '/task/check'));
  const before = sent.length;
  for (const p of ['/state', '/tasks', '/task?id=a&since=0', '/task/diff?id=a', '/task',
    '/task/followup', '/task/stop', '/task/close', '/wingmate', '/wingmate/ask',
    '/wingmate/answer?id=1']) await api(p);
  ok('the thread and wingmate routes go out', sent.length - before === 11);
  ok('the poll loop for the desktop control queue never starts',
    await pollControl() === undefined && sent.every((s) => !s.url.startsWith('/control')));
  // the thread panel: with the evidence against it a phone is not offered the landing, or a
  // second opinion
  elCache.tact = cap(); elCache.tvmeta = cap(); elCache.truns = cap();
  elCache.tvstatus = cap(); elCache.tvnote = cap();
  W.agent_tasks = [T('t1', 'review', { mode: 'worktree',
    merge_blockers: ['verify failed (exit 1)'] })];
  openTask('t1'); await settle();
  let acts = String(elCache.tact.innerHTML);
  ok('with blockers a phone is offered no merge, set aside or done',
    !/data-tact="(merge|discard|done)"/.test(acts));
  ok('nor a check by another agent',
    !acts.includes('tcheckr') && !acts.includes('data-tact="check"'));
  ok('it is told the computer accepts or rejects it', acts.includes('accept or reject it on your computer'));
  ok('it can still reply', acts.includes('id="tfollowf"'));
  detail = Object.assign({}, detail, { merge_blockers: [] });
  await loadTask(TV.epoch);
  acts = String(elCache.tact.innerHTML);
  ok('with nothing against it the phone is offered the merge',
    acts.includes('data-tact="merge"') && acts.includes('data-tact="discard"'));
  ok('the merge is never offered as a force', !acts.includes('click twice'));
  // the composer sends the words, the folder, the agent: no verify, setup, retries, and always
  // its own worktree
  elCache.ctext = cap(); elCache.ctext.value = 'do it';
  elCache.cfolder = cap(); elCache.cfolder.value = '/srv/x';
  elCache.crunner = cap(); elCache.crunner.value = 'claude';
  elCache.ciso = cap(); elCache.ciso.checked = false;
  elCache.cmodel = cap(); elCache.cmodel.value = '';
  elCache.cverify = cap(); elCache.cverify.value = 'make test';
  elCache.cretries = cap(); elCache.cretries.value = '2';
  elCache.csetup = cap(); elCache.csetup.value = 'npm ci';
  elCache.cmsg = cap(); elCache.cgo = cap();
  await submitComposer(); await settle();
  const made = sent.find((s) => s.url === '/task' && s.body && s.body.includes('"text"'));
  ok('a thread from a phone is posted', !!made);
  const asked = made ? JSON.parse(made.body) : {};
  ok('with no verify, retries or setup',
    !('verify' in asked) && !('retries' in asked) && !('setup' in asked));
  ok('and in its own worktree whatever the box said',
    asked.isolate === true && asked.text === 'do it' && asked.runner === 'claude');
  // the Advanced toggle of the phone layout opens a row of setup, verify and retries: a remote phone has none of them, so no toggle
  elCache.cadv = cap(); elCache.cadv.style = { display: '' }; openComposer(null);
  ok('a remote phone has no Advanced toggle', elCache.cadv.style.display === 'none');
  // doors are a desktop thing
  ok('a phone cannot open a door', (await openDoor('/srv/x', cap())) === null);
  // the star panel has no pairing section on a phone
  elCache.pbody = cap(); showStar();
  ok('the phone section of the star panel is for the desktop only',
    !String(elCache.pbody.innerHTML).includes('phonebox'));
"""

# the desktop page (a flagged loopback page): the star panel carries the phone section
PHONE_PANEL_DRIVER = r"""
  ok('the desktop page is not remote', DESK === true && REMOTE === false);
  const calls = [];
  let answer = { enabled: true, listening: true, public_origin: 'https://phone.example.com',
    error: '', pending: 0,
    devices: [{ id: 'a1b2c3d4', name: 'My <b>phone</b>', created: '2026-10-10T10:00:00Z',
      last_seen: '2026-10-10T11:02:00Z' }] };
  global.fetch = async (url, opt) => {
    calls.push([String(url), opt && opt.method, opt && opt.body]);
    const made = { code: 'ABCDEFGH', expires_in: 600, url: 'https://phone.example.com/pair' };
    const body = String(url) === '/phone/pair' ? made : String(url) === '/phone' ? answer : {};
    return { ok: true, status: 200, json: async () => body }; };
  elCache.pbody = cap(); showStar();
  ok('the star panel carries the phone section',
    String(elCache.pbody.innerHTML).includes('id="phonebox"'));
  elCache.phonebox = cap();
  await phoneBox();
  let html = String(elCache.phonebox.innerHTML);
  ok('the origin, a pair button and the device are listed',
    html.includes('https://phone.example.com') && html.includes('id="phonepair"')
    && html.includes('data-phone-revoke="a1b2c3d4"') && html.includes('2026-10-10'));
  ok('a device name is escaped, never markup',
    html.includes('My &lt;b&gt;phone&lt;/b&gt;') && !html.includes('My <b>'));
  answer = Object.assign({}, answer, { devices: [] });
  await phoneBox(); html = String(elCache.phonebox.innerHTML);
  ok('with no device it says so', html.includes('no phone is paired'));
  answer = { enabled: false, listening: false, public_origin: '', error: '', devices: [] };
  await phoneBox(); html = String(elCache.phonebox.innerHTML);
  ok('with phone access off it points at the config key',
    html.includes('Phone access is off') && html.includes('remote')
    && !html.includes('id="phonepair"'));
  answer = { enabled: false, listening: false, public_origin: '',
    error: 'remote.listen must name one interface', devices: [] };
  await phoneBox(); html = String(elCache.phonebox.innerHTML);
  ok('a refused block shows why', html.includes('remote.listen must name one interface'));
  answer = { enabled: true, listening: false, public_origin: 'https://phone.example.com',
    error: 'cannot listen on 127.0.0.1:7429: busy', devices: [] };
  await phoneBox(); html = String(elCache.phonebox.innerHTML);
  ok('a listener that did not open is shown, with no pair button',
    html.includes('cannot listen') && !html.includes('id="phonepair"'));
"""

# the same page served to the desktop by the bridge: the flag says live and not remote,
# whatever the host name
BRIDGE_DESK_DRIVER = r"""
  ok('a bridge-served page is live because the bridge says so',
    LIVE === true && REMOTE === false && DESK === true);
  ok('and its way back is its own origin', LIVE_URL === 'https://box.lan/');
"""

# the page without the flag, on a name that is not loopback: not live (a copy someone else hosts)
UNFLAGGED_DRIVER = r"""
  ok('no flag, not loopback: static', LIVE === false && REMOTE === false && DESK === false);
"""

PHONE_LOCATION = {"protocol": "https:", "hostname": "phone.example.com",
                  "origin": "https://phone.example.com"}
BOX_LOCATION = {"protocol": "https:", "hostname": "box.lan", "origin": "https://box.lan"}
PHONE_FLAG = '{"live":true,"remote":true}'
DESK_FLAG = '{"live":true,"remote":false}'

# the static page's way back to the map: a visible link at the port the page was rendered for. It
# never probes that address and never navigates there by itself (another app may answer on it).
FILE_BANNER_DRIVER = r"""
  ok('a file page is not live', LIVE === false);
  ok('the fallback is the port this page was rendered for',
    LIVE_URL === 'http://127.0.0.1:__PORT__/');
  const mk = (tag) => ({ tag: tag, kids: [], text: '',
    appendChild(c) { this.kids.push(c); return c; } });
  const made = [];
  document.createElement = (tag) => { const n = mk(tag); made.push(n); return n; };
  document.createTextNode = (t) => ({ tag: '#text', text: String(t) });
  const body = mk('body'); document.body = body;
  const calls = [];
  global.fetch = async (...a) => { calls.push(a); return { ok: true }; };
  ok('the banner builder exists', typeof staticBanner === 'function');
  staticBanner();
  await settle();
  const banner = made.find((n) => n.id === 'staticbanner');
  const link = made.find((n) => n.tag === 'a');
  ok('the banner is on the page', !!banner && body.kids.includes(banner));
  ok('it links to the live map and shows the address',
    !!link && link.href === LIVE_URL && link.textContent === LIVE_URL);
  const said = banner.kids.map((k) => k.text || '').join('');
  ok('the text says what the link is', said.includes('live map at'));
  ok('the hint starts the map on the port the link names, by either install',
    said.includes('container-map serve ROOT__FLAG__ (or python3 cli.py serve ROOT__FLAG__ from'));
  ok('the page never probes it', calls.length === 0);
  ok('and never navigates there', location.href === undefined);
"""

LIVE_LOCATION = {"protocol": "http:", "hostname": "127.0.0.1", "origin": "http://127.0.0.1:7428"}
LOCALHOST_LOCATION = {"protocol": "http:", "hostname": "localhost",
                      "origin": "http://localhost:7431"}


# a page that cannot reach a bridge: what is drawn from W alone
COUNTERS_DRIVER = r"""
  // F6: checking and stopping are work, in Today and in the top bar
  elCache.today = cap();
  W.task_runners = [{ name: 'claude', label: 'Claude' }];
  W.today = { configured: true, date: 'Fri', agents: { working: [], waiting: [], idle: [],
    problems: [] }, needs: [], daily: [], done: [], folders: [{ name: 'Old', tag: 'LIVE',
    desc: 'in use' }], unmapped: ['Live'], unmapped_off_map: true };
  for (const st of ['running', 'checking', 'stopping']) {
    W.agent_tasks = [T('a', st)]; renderToday();
    const h = String(elCache.today.innerHTML);
    ok('F6 ' + st + ' is a row in Today under Running now',
      h.indexOf('data-sec="tasks"') >= 0 && h.indexOf('data-sec="tasks"') < h.indexOf('thread a') && h.includes('Running now <span class="n">1</span>') && !h.includes('to review'));
    ok('F6 ' + st + ' counts as running', taskCounts().running === 1 && taskCounts().review === 0);
  }
  W.agent_tasks = [T('a', 'review')]; renderToday();
  { const h = String(elCache.today.innerHTML), at = (x) => h.indexOf(x);
    ok('F6 review still counts as to review: a row of Need you (with a sky), else the Running now header',
      taskCounts().review === 1 && (todayNeed() ? at('data-sec="needs"') < at('thread a') && at('thread a') < at('data-sec="decisions"') && h.includes('to review')
        : h.includes('1 to review'))); }
  elCache.topbar = cap();
  const savedUnits = units.slice(); units.length = 0;
  W.agent_tasks = [T('a', 'running'), T('b', 'checking'), T('c', 'stopping')];
  W.agent_tasks.forEach((t) => units.push({ uid: 'task:' + t.id, kind: 'task', task_id: t.id,
    name: t.title, status: t.status, target: t.target }));
  topbarTick();
  const bar = String(elCache.topbar.innerHTML);
  ok('F6 the top bar counts all three as running',
    bar.includes('3 running') && bar.includes('3 working'));
  units.length = 0; savedUnits.forEach((u) => units.push(u));

  // F11: with planets, the folders on no planet are "not on the map" and link nowhere
  W.agent_tasks = []; renderToday();
  let h = String(elCache.today.innerHTML);
  ok('F11 folders on no planet read "not on the map"',
    h.includes('not on the map: Live/') && !h.includes('no description yet'));
  ok('F11 and carry no link that flies nowhere', !h.includes('data-area="Live"'));
  W.today.unmapped_off_map = false; renderToday(); h = String(elCache.today.innerHTML);
  ok('F11 without planets they stay "no description yet" and linked',
    h.includes('no description yet') && h.includes('data-area="Live"'));

  // F9: only a thread that is working defends its planet
  const area = W.chars.find((c) => c.kind === 'area' && c.path);
  const fight = (status) => { W.agent_tasks = [T('a', status, { target: area.path })]; place();
    const m = MOONS.find((x) => x.uid === area.uid); return [m.allies, m.wrecks]; };
  for (const st of ['running', 'checking', 'stopping']) {
    ok('F9 ' + st + ' is an ally', fight(st)[0] === 1); }
  for (const st of ['review', 'stopped']) ok('F9 ' + st + ' is not an ally', fight(st)[0] === 0);
  ok('F9 failed is a wreck, not an ally', JSON.stringify(fight('failed')) === '[0,1]');
  W.agent_tasks = []; place();

  // F14: the HUD text follows the world it is given
  elCache.hudgen = cap();
  applyWorld({ chars: W.chars, quests: W.quests, generated: 'Sat Oct 10, 2:00 AM', oldest: 7 });
  ok('F14 the HUD rebuilt time follows the live world',
    String(elCache.hudgen.textContent) === 'updated Sat Oct 10, 2:00 AM');

  // F13: the wingmate wait covers the command's own limit
  W.wingmate = Object.assign({}, W.wingmate, { max_seconds: 300 });
  ok('F13 a 300 s wingmate is waited for 305 s', mateWaitMs() === 305000);
  W.wingmate.max_seconds = 60;
  ok('F13 a 60 s wingmate keeps the 95 s floor', mateWaitMs() === 95000);
  delete W.wingmate.max_seconds;
  ok('F13 no limit given keeps the 95 s floor', mateWaitMs() === 95000);
"""


# the characters' craft: every pack and the mono palette draw every craft and effect; a battlefield fires,
# the hovered problem takes the lock-on, a merged thread raises the banner
SKINLESS_DRIVER = r"""
  ok('the skin has no boost variant and no portrait', !SPRITES.pilot_boost && !SKIN.portrait && MATE_FRAMES.IDLE === undefined);
  boostOn = true; drawPilot(100, 100, .4, 1, 2000); boostOn = false; drawPilot(100, 100, .4, 1, 2000);
  ok('the boost falls back to the pilot grid', true);
  mateDraw(1000);
  ok('a wingmate without a portrait draws nothing and does not throw', true);
  ok('the wingmate takes the skin name', MATE.name === 'Scout' && mateName() === 'Scout');
  drawCarrier(100, 100, 2000);
  spriteArt('nothing-of-the-kind', hullPal('#3dff9e', ''), 0, 16, 3);
  ok('an unknown craft falls back to the small fighter', true);
"""
CRAFT_DRIVER = r"""
  const area = W.chars.find((c) => c.kind === 'area' && c.path);
  W.tasks = { items: [{ id: 'q1', area: area.name, finding: 'a stale decision', who: 'owner' }] };
  W.agent_tasks = ['running', 'running', 'checking', 'failed', 'review'].map((st, i) =>
    T('s' + i, st, { target: area.path }));
  place();
  const moon = MOONS.find((m) => m.uid === area.uid);
  ok('the fixture planet is a battlefield with two defenders', moon.enemies >= 2 && moon.allies === 3);
  for (const name of THEME_NAMES.concat(['gb'])) {
    try {
      if (name === 'gb') GB = true; else { GB = false; applyTheme(name, false); }
      const before = fills;
      draw(2000);
      // the problems ride the open planet's one orbit (ui/focus.js): open it, take a drawn problem's place
      openUnit = unitByUid(moon.uid); VIEW = 'map'; draw(2050); draw(2100);
      // The viewer brief fixes problem slots until their data changes.
      const f0 = VEC ? null : __mapAudit().foes[0], er = moon.r + 26, a0 = (seedOf(moon.uid) % 7);
      mouseW = f0 ? { x: f0.wx, y: f0.wy, sx: 0, sy: 0 } : { x: moon.x + Math.cos(a0) * er, y: moon.y + Math.sin(a0) * er * .78, sx: 0, sy: 0 };
      FX.lock = null; drawBattle(f0 ? MOONS.find((x) => x.uid === moon.uid) : moon, 2100); mouseW = null; openUnit = null;   // the planet as this look laid it out
      ok(name + ': the hovered problem takes the lock-on', !!FX.lock && FX.lock.uid === (f0 ? f0.uid : moon.foes[0].uid));
      drawCarrier(100, 100, 2000); drawCrew(100, 100, 1, '#3dff9e', true, 2000); drawGate(100, 100, '#3dff9e', 2000);
      boostOn = true; drawPilot(100, 100, .4, 1, 2000); boostOn = false; drawPilot(100, 100, .4, 1, 2000);
      for (const st of ['running', 'checking', 'review', 'failed'])
        drawTaskShip({ uid: 'task:' + st, kind: 'task', task_id: 'x' + st, name: 'x', status: st,
          docked_at: moon.uid, gx: 0, gy: 0 }, 100, 100, 2000);
      for (let f = 0; f < 6; f++) fxSpark(50, 50, f);
      fxMergeBanner('a merged thread'); drawMergeBanner(FRAME_T + 600);
      mateDraw(1000);
      ok(name + ': the craft set draws', fills > before);
    } catch (e) { ok(name + ': the craft set draws', false); out.push('  ' + (e && e.stack)); }
  }
  GB = false; applyTheme(THEME_CFG, false); FX.toast = null;

  // the banner and the carrier's plate use the skin's words
  const texts = []; ctx.fillText = (s) => { texts.push(String(s)); };
  FRAME_T = 1000; fxMergeBanner('merged'); drawMergeBanner(1600);
  ok('the merge banner says the skin word', texts.includes(SKIN_NAMES.merged));
  ok('the most troubled planet is said in plain words, never as a skin class', TYPE_NAME.blight === 'most troubled' && TYPE_NAME.blight.indexOf(String(SKIN_NAMES.worst)) < 0);
  FX.toast = null;

  // a thread that turns merged raises the banner; one first seen already merged does not
  FX.toast = null; W.agent_tasks = [T('m1', 'review')];
  applyTasks({ tasks: [T('m1', 'merged', { title: 'tidy the notes' })] });
  ok('a merged thread raises the banner with its title', !!FX.toast && FX.toast.title === 'tidy the notes');
  FX.toast = null;
  applyTasks({ tasks: [T('m1', 'merged'), T('m2', 'merged')] });
  ok('a thread first seen merged raises nothing', FX.toast === null);
"""


# every effect, run with and without prefers-reduced-motion (HARNESS_REDUCED): animated, or one still frame
EFFECTS_DRIVER = r"""
  ok('the page read the motion preference', REDUCED === (process.env.HARNESS_REDUCED === '1'));
  const rec = [];
  ['fillRect', 'strokeRect', 'stroke', 'fill', 'arc', 'ellipse', 'moveTo', 'lineTo', 'drawImage', 'fillText'].forEach((k) => {
    ctx[k] = (...a) => { rec.push(k + ':' + a.map((v) => (typeof v === 'number' ? Math.round(v * 100) / 100 : typeof v)).join(',')
      + '|' + ctx.fillStyle + '|' + ctx.strokeStyle + '|' + ctx.globalAlpha); }; });
  const frame = (fn) => { rec.length = 0; fn(); return rec.join(';'); };
  const check = (name, fn, t1, t2, drawsWhenStill) => {
    frame(() => fn(t1));   // the stub context keeps the last style (save/restore do nothing): settle it first
    const a = frame(() => fn(t1)), b = frame(() => fn(t2));
    ok(name + (REDUCED ? ' is one still frame under reduced motion' : ' animates'), REDUCED ? a === b : (a !== b && a.length > 0 && b.length > 0));
    if (REDUCED && drawsWhenStill) ok(name + ' still shows its frame', a.length > 0);
    if (REDUCED && !drawsWhenStill) ok(name + ' draws nothing under reduced motion', a.length === 0);
  };
  const foes = [[120, 0], [0, 120]];
  check('twin bolts', (t) => fxVolley(0, 0, foes, [0, 1], 0, t), 100, 300, true);
  check('hit spark', (t) => fxVolley(0, 0, foes, [0, 1], 0, t), 550, 620, true);
  check('shield ring', (t) => fxShield(0, 0, 30, t), 200, 400, true);
  // The viewer brief removes decorative shimmer: a gate has no live transfer event.
  frame(() => fxGateShimmer(0, 0, '#3dff9e', 1000));
  const gate = frame(() => fxGateShimmer(0, 0, '#3dff9e', 1000));
  ok('gate holds a visible stable frame', gate.length > 0 && gate === frame(() => fxGateShimmer(0, 0, '#3dff9e', 1300)));
  check('engine trail', (t) => fxTrail(0, 0, .5, t, 30, 2, [-6, 6]), 1000, 1130, false);
  check('lock-on reticle', (t) => fxLockOn(0, 0, 16, t, 1000), 1050, 1400, true);
  FX.rings.length = 0; FX.ringAt = 0; boostOn = true;
  check('boost rings', (t) => fxBoostRings(0, 0, 0, 1, t), 1000, 1100, true);
  boostOn = false; FX.rings.length = 0;
  FRAME_T = 1000; fxMergeBanner('merged');
  check('merge banner', (t) => drawMergeBanner(t), 1100, 1500, true);
  drawMergeBanner(1000 + 5000);
  ok('the banner ends', FX.toast === null);
  // boost: the stars stretch into streaks along the heading; reduced motion keeps them dots
  boostOn = true; px = 500; lastPx = 480; py = 0; lastPy = 0; FX.boostK = 0;
  for (let i = 0; i < 8; i++) drawStars(1000);
  const streaks = frame(() => drawStars(1000)).split(';').filter((r) => r.startsWith('lineTo')).length;
  ok(REDUCED ? 'no boost streaks under reduced motion' : 'boost stretches the stars into streaks',
    REDUCED ? streaks === 0 : streaks > 100);
  boostOn = false; lastPx = px;
"""


# a remote deck is laid out from its own synced world; the home world has the same planet name
REMOTE_DRIVER = r"""
  const alpha = JSON.parse(JSON.stringify(W.chars.find((c) => c.kind === 'area')));
  W.trend = {}; W.trend[alpha.name] = { worse: true, why: 'home storm' };
  W.tasks = { items: [{ id: 'h1', area: alpha.name, finding: 'a home problem', who: 'owner' }] };
  W.wingmate = Object.assign({}, W.wingmate, { home_planet: alpha.name });
  const synced = { chars: [alpha], trend: {}, tasks: { items: [] },
    wingmate: { home_planet: 'Elsewhere' }, quests: [], oldest: 0 };
  synced.trend[alpha.name] = { better: true, why: 'recovering' };
  buildRemoteSystem('lab-b', synced, null, {});
  const rm = remoteMoons.find((m) => m.label === alpha.name);
  ok('F2 the remote planet has its own trend',
    !!rm && !!rm.trend && rm.trend.better === true && !rm.trend.worse);
  ok('F2 the home board items are not its problems', rm.enemies === 0 && rm.trouble === 0);
  ok('F2 the home planet marker is not carried over', rm.home === false);
  place();
  const hm = MOONS.find((m) => m.label === alpha.name);
  ok('F2 the home deck still reads the home world',
    hm.trend.worse === true && hm.enemies === 1 && hm.trouble === 1 && hm.home === true);
"""


# design round one: the comms rows, the rail's five-row sections, the bottom strip's four numbers
CONSOLE_DRIVER = r"""
  ok('a permission banner is not a comms line', cleanLine('⏵⏵ bypass permissions on (shift+tab to cycle)') === '');
  ok('a key hint is not a comms line', cleanLine('? for shortcuts            5 warnings f2') === '');
  ok('shell prompts are not comms lines', cleanLine('/srv/notes/Areas/Career$') === ''
    && cleanLine('dev@box:/srv/x$ ') === '' && cleanLine('❯ ') === '' && cleanLine('$') === '');
  ok('escape codes and leading glyphs go, the words stay',
    cleanLine('\x1b[32m● Wrote 3 files\x1b[0m') === 'Wrote 3 files');
  const long = cleanLine('word '.repeat(30));
  ok('a comms line is cut to 60 with an ellipsis', long.length === 60 && long.endsWith('…'));
  ok('the render\'s cleaned line wins over the raw pane', commsLine({ line: 'clean words', pane: '❯ draft' }) === 'clean words'
    && commsLine({ pane: '~/src$ ' }) === '');
  elCache.cbody = cap(); elCache.csum = cap(); elCache.dash = cap(); elCache.nums = cap();
  const saved = units.slice(); units.length = 0;
  const seat = (n, st, o) => Object.assign({ uid: 'seat:' + n, kind: 'seat', name: n, status: st, window_index: n.length,
    pane: '? for shortcuts', line: 'ran the tests' }, o || {});
  units.push(seat('hub', 'IDLE'), seat('jobs', 'IDLE'));
  COMMS_OPEN = false; commsTick();
  ok('every seat idle: comms fold to the one strip', elCache.cbody.innerHTML === ''
    && String(elCache.csum.textContent).includes('all 2 agents idle'));
  units.push(seat('astra', 'WORKING', { line: 'Re-pointing the cron lines' })); commsTick();
  const rows = String(elCache.cbody.innerHTML);
  ok('a working seat opens comms: name, state word, the clean line, never the raw pane',
    rows.includes('>astra<') && rows.includes('>working<') && rows.includes('Re-pointing the cron lines')
    && !rows.includes('for shortcuts'));
  units.length = 0; saved.forEach((u) => units.push(u));
  elCache.wrap = cap();
  elCache.wrap.classList = { contains: (c) => c === 'dash' || c === 'side', toggle: () => {}, add: () => {}, remove: () => {} };
  W.agent_tasks = [];
  // the Agents section is every agent at work, the rows W.counts.working counts (workingRows): seven working seats
  const saved2 = units.slice(); [1, 2, 3, 4, 5, 6, 7].forEach((i) => units.push(seat('crew ' + i, 'WORKING', { uid: 'seat:w' + i, cwd: W.root_path })));
  W.today = { configured: true, agents: { working: [], waiting: [], idle: [] }, needs: [], word: { items: [{ lane: 'health', what: 'book it', state: 'need' }] } };
  DASH_TAB = 'console'; DASH_HTML = ''; renderDash();
  let d = String(elCache.dash.innerHTML);
  units.length = 0; saved2.forEach((u) => units.push(u));
  ok('the rail leads with NEEDS YOU, then AGENTS, then SYSTEM',
    d.indexOf('Needs you') >= 0 && d.indexOf('Needs you') < d.indexOf('>Agents') && d.indexOf('>Agents') < d.indexOf('System'));
  ok('a section shows five rows and one +N more row, its number the count\'s', (d.match(/class="row work"/g) || []).length === 5 && d.includes('+2 more')
    && /data-count="working">7</.test(d));
  ok('Today waits under its tab', !d.includes('book it') && d.includes('data-dtab="today"'));
  DASH_TAB = 'today'; renderDash(); d = String(elCache.dash.innerHTML);
  ok('the Today tab shows the word', d.includes('book it') && !d.includes('Needs you'));
  DASH_TAB = 'console';
  W.agent_tasks = [T('r1', 'review')]; SKY = false; W.counts = { need_you: 2, decisions: 5, problems: 1, working: 0 }; stripTick();
  const n = String(elCache.nums.innerHTML);
  ok('the strip carries the one count (W.counts) in plain words: need you, decisions, problem, working',
    (n.match(/data-num=/g) || []).length === 4 && ['>waiting for you<', '>decisions<', '>problem<', '>working<'].every((w) => n.includes(w))
    && /data-count="need_you"[^>]*><b>2</.test(n) && /data-count="problems"[^>]*><b>1</.test(n));
  SKY = true; stripTick(); const n2 = String(elCache.nums.innerHTML);
  ok('...the same in sky mode, never the old game words', n2 === n && !/bogeys|in warp|parked|settled|EMPTY SKY|crons/.test(n2));
  W.agent_tasks = [];
"""


# the plate layout: every label on the map goes through queuePlate/layoutPlates. The synthetic
# half drives the layout with made-up plates and obstacles; the dock half draws real frames of a
# busy dock at desktop and phone sizes
PLATES_DRIVER = r"""
  SKY = false;   // every label of every unit: the full view (sky mode takes working threads out of sight)
  const area = W.chars.find((c) => c.kind === 'area' && c.path);
  const moon = MOONS.find((m) => m.uid === area.uid);
  const eps = 0.01;
  const hit = (a, b) => a.x < b.x + b.w - eps && a.x + a.w > b.x + eps && a.y < b.y + b.h - eps && a.y + a.h > b.y + eps;
  const circ = (r, h) => { const dx = Math.max(r.x - h.x, 0, h.x - (r.x + r.w)), dy = Math.max(r.y - h.y, 0, h.y - (r.y + r.h));
    return dx * dx + dy * dy < (h.r - 1) * (h.r - 1); };
  const overlapping = (A) => { const out = [];
    for (let i = 0; i < A.plates.length; i++) for (let j = i + 1; j < A.plates.length; j++)
      if (hit(A.plates[i], A.plates[j])) out.push(A.plates[i].key + ' / ' + A.plates[j].key);
    return out; };
  const outside = (A) => A.plates.filter((p) => p.x < -eps || p.y < -eps || p.x + p.w > A.view.w + eps || p.y + p.h > A.view.h + eps).map((p) => p.key);
  const onBoxes = (A) => { const out = []; A.plates.forEach((p) => A.overlays.forEach((o) => { if (hit(p, o)) out.push(p.key + ' on ' + o.id); })); return out; };
  const onHulls = (A) => A.plates.filter((p) => p.kind !== 'tag')
    .filter((p) => PLATE_HULLS.some((h) => circ({ x: p.wx, y: p.wy, w: p.ww, h: p.wh }, h))).map((p) => p.key);
  const view = (w, h) => { VW = w; VH = h; PANEL_W = 0; ZOOM = 1; camOff.x = 0; camOff.y = 0; OVERLAY_CACHE.at = -1e9; };
  const dock = (n, tasks, atPlanet, status) => {
    W.chars = W.chars.filter((c) => !(c.kind === 'seat' || c.kind === 'ship'));
    const sts = ['WAITING', 'WORKING', 'IDLE', 'DRAFT', 'LIMITED', 'WORKING', 'WAITING'];
    ['hub', 'astra', 'jobs', 'money', 'health', 'projects', 'learning'].slice(0, n).forEach((name, i) => W.chars.push(
      { uid: 'seat:' + name, kind: 'seat', name: name, status: status || sts[i], cwd: atPlanet ? area.path + '/x' : '',
        window_index: i + 1, pane: 'x', line: 'ran the tests' }));
    W.agent_tasks = tasks; place();
  };
  const long = 'refactor the ledger importer so that the whole thing runs offline and then keep going';
  const at = { target: area.path };   // a thread works on a folder: that planet is where it docks
  const busy = [T('a1', 'running', Object.assign({ title: long }, at)), T('a2', 'review', at), T('a3', 'checking', at), T('a4', 'failed', at),
    T('a5', 'review', at), T('a6', 'running', at)];
  const aim = () => { px = moon.x; py = moon.y + 190; };

  // ---- fitLabel: the ellipsis
  const cut = fitLabel(long, 120);
  ok('fitLabel cuts a long line to the width with an ellipsis', cut.endsWith('…') && cut.length * 5.5 <= 120 && long.startsWith(cut.slice(0, -1)));
  ok('fitLabel leaves a line that fits alone, and an empty one empty', fitLabel('short', 120) === 'short' && fitLabel('', 50) === ''
    && fitLabel(null, 50) === '');

  // ---- the layout on made-up plates (world units, zoom 1)
  ZOOM = 1; FRAME_T = 1000;
  const mk = (key, kind, pri, x, y, face, txt, extra) => Object.assign({ key: key, kind: kind, pri: pri, x: x, y: y, r: 20, gap: 4, face: face,
    style: 'plate', hot: false, w: 0, h: 0, rows: [[{ t: txt, col: '#ffffff', ord: 0 }]] }, extra || {});
  const own = (q) => ({ t: 'hull', x: q.x, y: q.y, r: q.r });
  const cr = { x: 0, y: 0, w: 600, h: 400 };
  PLATE_MEMORY.clear();
  let qa = mk('a', 'seat', 3, 300, 200, 's', 'alpha'), qb = mk('b', 'seat', 3, 300, 200, 's', 'bravo'), qc = mk('c', 'thread', 4, 300, 200, 's', 'charlie');
  let res = layoutPlates([qc, qb, qa], [own(qa)], cr);
  ok('plates of one craft stack: all placed, none overlapping, none on the hull', res.placed.length === 3 && res.dropped.length === 0
    && !res.placed.some((p, i) => res.placed.some((q, j) => j > i && hit(p, q))) && !res.placed.some((p) => circ(p, own(qa))));
  ok('the better priority sits closest, in the direction asked', res.placed.map((p) => p.q.key).join() === 'a,b,c'
    && res.placed[0].ring === 0 && res.placed[0].dir === PLATE_FACE.s);
  PLATE_MEMORY.clear();
  const hull2 = { t: 'hull', x: 300, y: 262, r: 20 };
  res = layoutPlates([qa], [own(qa), hull2], cr);
  ok('a plate keeps off another craft hull', res.placed.length === 1 && !circ(res.placed[0], hull2));
  PLATE_MEMORY.clear();
  const box = { t: 'box', id: 'comms', x: 150, y: 215, w: 300, h: 80 };
  res = layoutPlates([qa], [own(qa), box], cr);
  ok('a plate keeps out from under a page box (comms, the wingmate, the HUD)', res.placed.length === 1 && !hit(res.placed[0], box));
  PLATE_MEMORY.clear();
  const corner = mk('k', 'seat', 3, 30, 12, 'n', 'corner');
  res = layoutPlates([corner], [own(corner)], cr);
  ok('a plate near the edge slides back inside the view', res.placed.length === 1 && res.placed[0].x >= cr.x && res.placed[0].y >= cr.y
    && res.placed[0].x + res.placed[0].w <= cr.x + cr.w && res.placed[0].y + res.placed[0].h <= cr.y + cr.h);
  PLATE_MEMORY.clear();
  res = layoutPlates([qa], [own(qa), { t: 'box', x: -2000, y: -2000, w: 5000, h: 5000 }], cr);
  ok('a plate with no free place is left out, never printed on something', res.placed.length === 0 && res.dropped.length === 1
    && res.dropped[0].key === 'a');
  PLATE_MEMORY.clear();
  res = layoutPlates([mk('badge', 'badge', 1, 300, 200, 's', 'NEEDS YOU'), mk('tag', 'tag', 0, 300, 200, 's', 'ALPHA'),
    mk('you', 'player', 5, 300, 200, 's', 'hub · you'), mk('seat', 'seat', 3, 300, 200, 's', 'alpha', { hot: true })], [own(qa)], cr);
  ok('plates are placed in priority order, a pointed-at plate right after the tags',
    res.placed.map((p) => p.q.key).join() === 'tag,seat,badge,you');
  // stickiness: a slot freed after a block is not taken back, so a plate does not jump about
  PLATE_MEMORY.clear();
  const first = layoutPlates([mk('s1', 'seat', 3, 300, 200, 's', 'alpha')], [own(qa), hull2], cr).placed[0];
  const again = layoutPlates([mk('s1', 'seat', 3, 300, 200, 's', 'alpha')], [own(qa)], cr).placed[0];
  ok('a plate stays in last frame\'s slot when a nearer one frees up', first.ring === 0 && first.dir !== PLATE_FACE.s
    && again.dir === first.dir && again.ring === first.ring && Math.abs(again.x - first.x) < eps && Math.abs(again.y - first.y) < eps);
  PLATE_MEMORY.clear();
  const fresh = layoutPlates([mk('s1', 'seat', 3, 300, 200, 's', 'alpha')], [own(qa)], cr).placed[0];
  ok('a plate with no memory takes the nearest slot in its direction', fresh.ring === 0 && fresh.dir === PLATE_FACE.s);
  const blocked = layoutPlates([mk('s1', 'seat', 3, 300, 200, 's', 'alpha')],
    [own(qa), { t: 'hull', x: fresh.x + fresh.w / 2, y: fresh.y + fresh.h / 2, r: 12 }], cr).placed[0];
  ok('a remembered slot that gets blocked is given up', blocked.dir !== fresh.dir || blocked.ring !== fresh.ring);

  // ---- one busy dock: seats and threads at one planet, desktop then phone
  dock(7, busy, true);
  view(1280, 800); aim(); draw(1000);
  let A = __plateAudit();
  const foldText = (a) => (a.plates.find((p) => p.kind === 'fold') || {}).text;
  ok('the audit hands back the final rects', !!A && A.plates.length > 0 && A.plates.every((p) => p.w > 0 && p.h > 0 && p.key) && A.view.w === 1280 && A.view.h === 800);
  ok('more than four plates at one planet fold into one count', A.folds.length === 1 && A.plates.filter((p) => p.kind === 'fold').length === 1
    && A.plates.filter((p) => p.dock === area.uid).length === 0);
  ok('the fold counts ships, tasks and who needs you', foldText(A) === '7 ships · 6 tasks · 5 need you');
  ok('the planet keeps its tag', A.plates.some((p) => p.kind === 'tag' && p.uid === area.uid));
  ok('desktop: no two plates overlap and none leaves the view', overlapping(A).length === 0 && outside(A).length === 0);
  ok('the click boxes are the final rects', A.plates.filter((p) => p.uid).every((p) => CLICK_BOXES.some((b) => b.uid === p.uid
    && Math.abs(b.x - p.wx) < eps && Math.abs(b.y - p.wy) < eps && Math.abs(b.w - p.ww) < eps && Math.abs(b.h - p.wh) < eps)));
  hover = unitByUid(area.uid); draw(1100); A = __plateAudit();
  ok('pointing at the planet unfolds its dock', A.folds.length === 0 && A.plates.filter((p) => p.dock === area.uid).length >= 5);
  ok('the unfolded plates do not overlap, leave the view or lie on a craft', overlapping(A).length === 0 && outside(A).length === 0 && onHulls(A).length === 0);
  hover = null; mouseW = { x: moon.x, y: moon.y + 60, sx: 0, sy: 0 }; draw(1200); A = __plateAudit();
  ok('a dock stays unfolded while the pointer is over it', DOCK_ZONE.get(area.uid).open && A.folds.length === 0);
  mouseW = { x: moon.x + 5000, y: moon.y, sx: 0, sy: 0 }; draw(1300); A = __plateAudit();
  ok('and folds again once the pointer leaves', A.folds.length === 1);
  mouseW = null; hover = null;
  openUnit = unitByUid(area.uid); draw(1310); A = __plateAudit();
  ok('opening the planet\'s card (a tap) unfolds its dock', A.folds.length === 0 && A.plates.filter((p) => p.dock === area.uid).length >= 5);
  openUnit = null; near = units.find((u) => u.docked_at === area.uid); draw(1320); A = __plateAudit();
  ok('flying up to a craft of the dock unfolds it', A.folds.length === 0);
  near = null; draw(1330); A = __plateAudit();
  ok('and it folds once nothing points at it', A.folds.length === 1);
  // the page's own boxes are obstacles: the comms box is laid over the planet's lower half
  const savedComms = elCache.comms;
  elCache.comms = { getBoundingClientRect: () => ({ left: 500, top: 215, width: 300, height: 200 }) };
  OVERLAY_CACHE.at = -1e9; draw(1400); A = __plateAudit();
  ok('the audit lists the page boxes it avoided', A.overlays.some((o) => o.id === 'comms' && o.x === 500 && o.w === 300));
  ok('no plate lies under a page box', onBoxes(A).length === 0 && overlapping(A).length === 0 && outside(A).length === 0);
  hover = unitByUid(area.uid); draw(1500); A = __plateAudit();
  ok('unfolded plates keep out from under a page box too', onBoxes(A).length === 0 && overlapping(A).length === 0 && outside(A).length === 0);
  hover = null; elCache.comms = savedComms; OVERLAY_CACHE.at = -1e9;
  view(390, 844); aim(); draw(1600); A = __plateAudit();
  ok('phone: the dock folds, the planet keeps its tag', A.folds.length === 1 && A.plates.some((p) => p.kind === 'tag') && A.view.w === 390);
  ok('phone: no two plates overlap and none leaves the view', overlapping(A).length === 0 && outside(A).length === 0);
  hover = unitByUid(area.uid); draw(1700); A = __plateAudit();
  ok('phone: the unfolded dock fits too', overlapping(A).length === 0 && outside(A).length === 0 && onHulls(A).length === 0);
  hover = null;

  // ---- no jumping: a page box sweeps across the unfolded dock; a plate only changes slot when its old one is taken
  dock(7, busy, true); view(1280, 800); aim(); hover = unitByUid(area.uid); PLATE_MEMORY.clear(); draw(3000);
  let prev = __plateAudit(), moved = 0; const jumps = [];
  for (let i = 0; i < 70; i++) {
    const bx = 250 + i * 9;
    elCache.comms = { getBoundingClientRect: () => ({ left: bx, top: 150, width: 150, height: 90 }) };
    OVERLAY_CACHE.at = -1e9; draw(3100 + i * 16);
    const B = __plateAudit();
    B.plates.forEach((p) => {
      const was = prev.plates.find((q) => q.key === p.key);
      if (!was || (was.dir === p.dir && was.ring === p.ring)) return;
      moved++;
      const old = { x: was.x, y: was.y, w: was.w, h: was.h }, oldWorld = { x: was.wx, y: was.wy, w: was.ww, h: was.wh };
      const taken = B.overlays.some((o) => hit(old, o)) || B.plates.some((q) => q.key !== p.key && hit(old, q))
        || PLATE_HULLS.some((h) => circ(oldWorld, h)) || old.x < 0 || old.y < 0 || old.x + old.w > B.view.w || old.y + old.h > B.view.h;
      if (!taken) jumps.push(i + ':' + p.key);
    });
    prev = B;
  }
  ok('the sweep moved some plates (the test is not empty)', moved > 0);
  ok('no plate leaves a slot that is still free', jumps.length === 0);
  elCache.comms = savedComms; OVERLAY_CACHE.at = -1e9; hover = null;

  // ---- badges: five seats waiting on you at the star's dock each get a badge, none on another
  dock(5, [], false, 'WAITING');
  view(1280, 800); px = LY.star.x; py = LY.star.y; draw(1800); A = __plateAudit();
  ok('five NEEDS YOU badges, all placed', A.plates.filter((p) => p.kind === 'badge' && p.text === 'NEEDS YOU').length === 5);
  ok('badges do not pile up: no overlaps, none on a craft, none outside', overlapping(A).length === 0 && onHulls(A).length === 0 && outside(A).length === 0);

  // ---- one plate for the hub: carrier, main session and "you" are the same line ("hub · you" -> you,
  // "<carrier> · home" -> "<carrier> · main session"; the seat named hub is not named again)
  dock(2, [], false);
  const hubU = units.find((u) => u.name === 'hub');
  view(1280, 800); px = hubU.gx * TILE + 20; py = hubU.gy * TILE + 30; draw(1900); A = __plateAudit();
  const hubPlates = A.plates.filter((p) => p.key === 'c:' + hubU.uid);
  ok('the hub carries one plate: carrier, main session and "you"', hubPlates.length === 1 && hubPlates[0].text.includes(SKIN_NAMES.carrier + ' · main session')
    && /\byou\b/.test(hubPlates[0].text) && !/\bhub\b/.test(hubPlates[0].text) && !A.plates.some((p) => p.key === 'player'));
  px += 320; draw(2000); A = __plateAudit();
  ok('with the player gone the hub plate and the player plate part', A.plates.some((p) => p.key === 'player' && p.text === 'you')
    && A.plates.some((p) => p.key === 'c:' + hubU.uid && !/\byou\b/.test(p.text)));

  // ---- thread titles: cut with an ellipsis, only when close, hovered or open
  dock(0, [T('t1', 'running', { title: long })], false);
  const th = units.find((u) => u.kind === 'task');
  view(1280, 800); px = th.gx * TILE; py = th.gy * TILE + 60; draw(2100); A = __plateAudit();
  const title = A.plates.find((p) => p.kind === 'thread');
  ok('a close thread shows its title, cut with an ellipsis', !!title && title.text.endsWith('…') && title.w <= 260 && title.text.startsWith('▶ refactor the'));
  px = th.gx * TILE + 220; py = th.gy * TILE; draw(2200); A = __plateAudit();
  ok('a thread further than 140 away shows no title', !A.plates.some((p) => p.kind === 'thread'));
  hover = th; draw(2300); A = __plateAudit();
  ok('pointing at it brings the title back', A.plates.some((p) => p.kind === 'thread'));
  hover = null;
"""


# the same busy dock in a real browser (headless Chrome over the DevTools protocol), where the
# page's own boxes are real: comms, the wingmate, the HUD, the ticker. Every request that is not a file
# is failed, so the static page's probe of the live bridge never leaves the machine.
BROWSER_RUNNER = r"""
import fs from 'node:fs';
const [port, url, width, height, mobile, scenario] = process.argv.slice(2);
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
await send('Emulation.setDeviceMetricsOverride', { width: +width, height: +height, deviceScaleFactor: 1, mobile: mobile === '1' });
if (mobile === '1') await send('Emulation.setTouchEmulationEnabled', { enabled: true });
const loaded = new Promise((r) => ws.addEventListener('message', (m) => { if (JSON.parse(m.data).method === 'Page.loadEventFired') r(); }));
await send('Page.addScriptToEvaluateOnNewDocument', { source: 'try{ sessionStorage.clear(); }catch(e){}' });   // a first visit: the tab keeps nothing of the last scenario
await send('Page.navigate', { url });
await loaded; await sleep(1200);
const r = await send('Runtime.evaluate', { returnByValue: true, awaitPromise: true,
  expression: `(async()=>{ if(location.protocol!=='file:') throw new Error('left the file: '+location.href); ${fs.readFileSync(scenario, 'utf8')} })()` });
console.log('RESULT ' + JSON.stringify({ value: r.exceptionDetails ? null : r.result.value,
  exception: r.exceptionDetails ? (r.exceptionDetails.exception?.description || r.exceptionDetails.text) : null, blocked, errors: errors.slice(0, 3) }));
ws.close();
process.exit(0);
"""

BROWSER_SCENARIO = r"""
const CFG = __CFG__;
const area = W.chars.find((c) => c.kind === 'area' && c.path);
W.chars = W.chars.filter((c) => !(c.kind === 'seat' || c.kind === 'ship'));
const sts = ['WAITING', 'WORKING', 'IDLE', 'DRAFT', 'LIMITED', 'WORKING', 'WAITING'];
['hub', 'astra', 'jobs', 'money', 'health', 'projects', 'learning'].forEach((name, i) => W.chars.push(
  { uid: 'seat:' + name, kind: 'seat', name: name, status: sts[i], cwd: area.path + '/x', window_index: i + 1, pane: 'x', line: 'ran the tests' }));
const task = (id, st) => ({ id: id, title: 'refactor the ledger importer so that the whole thing runs offline ' + id, status: st, runner: 'claude',
  model: '', target: area.path, runs: 1, retry_due: false, started: new Date().toISOString() });
W.agent_tasks = [task('a1', 'running'), task('a2', 'review'), task('a3', 'checking'), task('a4', 'failed'), task('a5', 'review'), task('a6', 'running')];
setView('map'); setQuiet(CFG.quiet); SKY = false;   // the full view: every craft at its planet (sky mode docks one at rest in the hangar)
if (CFG.hideMate) mateShow(false);
if (!CFG.rails) { document.getElementById('wrap').classList.remove('dash'); document.getElementById('wrap').classList.remove('side'); }
fit(); place();
const moon = MOONS.find((m) => m.uid === area.uid);
px = moon.x; py = moon.y + 190; humanMove(); setZoom(1);
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const frames = async (n) => {   // let the page's own loop draw n more frames (a loaded machine may be slow)
  let last = FRAME_T, seen = 0; const end = Date.now() + 10000;
  while (seen < n && Date.now() < end) { await wait(25); if (FRAME_T !== last) { last = FRAME_T; seen++; } }
};
const eps = 0.01;
const hit = (a, b) => a.x < b.x + b.w - eps && a.x + a.w > b.x + eps && a.y < b.y + b.h - eps && a.y + a.h > b.y + eps;
const audit = (name) => {
  const A = __plateAudit(), P = A.plates;
  const overlap = [], onbox = [];
  for (let i = 0; i < P.length; i++) for (let j = i + 1; j < P.length; j++) if (hit(P[i], P[j])) overlap.push(P[i].key + ' / ' + P[j].key);
  P.forEach((p) => A.overlays.forEach((o) => { if (hit(p, o)) onbox.push(p.key + ' on ' + o.id); }));
  const out = P.filter((p) => p.x < -eps || p.y < -eps || p.x + p.w > A.view.w + eps || p.y + p.h > A.view.h + eps).map((p) => p.key);
  const onhull = P.filter((p) => p.kind !== 'tag').filter((p) => PLATE_HULLS.some((h) => {
    const dx = Math.max(p.wx - h.x, 0, h.x - (p.wx + p.ww)), dy = Math.max(p.wy - h.y, 0, h.y - (p.wy + p.wh)); return dx * dx + dy * dy < (h.r - 1) * (h.r - 1); })).map((p) => p.key);
  return { name: name, vw: A.vw, vh: A.vh, plates: P.length, kinds: P.map((p) => p.kind), docked: P.filter((p) => p.dock === area.uid).length,
    fold: (P.find((p) => p.kind === 'fold') || {}).text || null, folds: A.folds.length, overlap: overlap, onbox: onbox, out: out, onhull: onhull,
    overlays: A.overlays.map((o) => o.id), dropped: A.dropped.map((d) => d.key) };
};
const steps = [];
await frames(12); steps.push(audit('folded'));
mouseW = { x: moon.x, y: moon.y, sx: 0, sy: 0 }; await frames(12); steps.push(audit('pointing at the planet'));
mouseW = null; await frames(12); steps.push(audit('pointer gone'));
return { live: LIVE, href: location.href, steps: steps };
"""


def find_chrome():
    """A Chrome or Chromium binary: $CHROME_BIN, else the first of the usual names on PATH."""
    named = os.environ.get("CHROME_BIN", "")
    if named and os.access(named, os.X_OK):
        return named
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


# the phone: the tab rule, hints, toasts, the More sheet, the camera over the card sheet (390x844, html.m)
MOBILE_DRIVER = r"""
  const html0 = document.documentElement;
  ok('a phone: MOB mirrors html.m, portrait, no html.t',
    MOB === true && html0.classList.contains('m') && !html0.classList.contains('land') && !html0.classList.contains('t') && MOB_LAND === false);
  ok('the first load on a phone fits the whole world when nothing is stored (fit-all; it was a fixed 0.22)', ZOOM === fitZoom(VW, VH) && HOME.on === true);
  ok('the quiet layout is forced and the wingmate is not hidden', QUIET === true && MATE.hidden === false);
  const cl = (set) => ({ contains: (c) => set.has(c), add: (c) => set.add(c), remove: (c) => set.delete(c),
    toggle: (c, f) => { if (f === undefined) f = !set.has(c); if (f) set.add(c); else set.delete(c); return f; } });
  const wrapSet = new Set(), sideSet = new Set();
  elCache.wrap = cap(); elCache.wrap.classList = cl(wrapSet);
  elCache.side = cap(); elCache.side.classList = cl(sideSet);
  VIEW = 'today'; MOB_WINGMATE = false;
  ok('Today is the tab while Today is the view', mobTab() === 'today');
  VIEW = 'map'; ok('the map is the tab otherwise', mobTab() === 'map');
  MOB_WINGMATE = true; ok('the wingmate tab beats the map', mobTab() === 'wingmate');
  wrapSet.add('side'); ok('an open rail is the Threads tab, over the rest', mobTab() === 'threads');
  sideSet.add('pick'); ok('picking a planet hides the rail, so the tab falls back', mobTab() === 'wingmate');
  MOB_WINGMATE = false; ok('...to the map', mobTab() === 'map');
  wrapSet.delete('side'); sideSet.delete('pick');

  // hints: key legends leave the page's own words (buttons, labels, dashboard links, the legend), never what a person or an agent wrote
  const scrub = (s, within) => { const has = (sel) => (within || []).some((a) => sel.split(',').includes(a));
    const n = { nodeType: 3, nodeValue: s, parentNode: { closest: (sel) => (has(sel) ? {} : null) } }; hintScrub(n); return n.nodeValue; };
  ok('(N) goes', scrub('+ new (N)', ['button']) === '+ new');
  ok('Ctrl+Enter goes', scrub('Start thread · Ctrl+Enter', ['button']) === 'Start thread');
  ok('Ctrl/Cmd+Enter sends goes', scrub('Draft for hub; Ctrl/Cmd+Enter sends', ['label']) === 'Draft for hub');
  ok('an emptied bracket goes with it', scrub('what to do next (Ctrl+Enter)', ['button']) === 'what to do next');
  ok('a dashboard link loses its key', scrub('board (Q)', ['a[data-dash]']) === 'board');
  ok('the legend paragraph loses its key', scrub(' = your tasks (N) and traffic', ['#legend']) === ' = your tasks and traffic');
  ok('text inside a terminal or transcript is left alone', scrub('press (N) or Ctrl+Enter', ['button', '#tlog']) === 'press (N) or Ctrl+Enter'
    && scrub('press (N)', ['.live']) === 'press (N)' && scrub('press (N)', ['pre']) === 'press (N)');
  ok('a thread title is left alone', scrub('Add dark mode (A)', ['.trow']) === 'Add dark mode (A)' && scrub('Add dark mode (A)', []) === 'Add dark mode (A)');
  ok('a wingmate reply, card text and the diff are left alone', scrub('take option (B) or Ctrl+Enter', ['#matetxt']) === 'take option (B) or Ctrl+Enter'
    && scrub('notes (B).txt', ['dd']) === 'notes (B).txt' && scrub('notes (B).txt', ['#tdiff']) === 'notes (B).txt');
  ok('...also when a button holds a row of them', scrub('Add dark mode (A)', ['button', '.trow']) === 'Add dark mode (A)');
  ok('text with no hint is not rewritten', scrub('plain words', ['button']) === 'plain words');
  const ta = { nodeType: 1, tagName: 'TEXTAREA', id: '', firstChild: null, classList: { contains: () => false }, ph: 'Reply (Ctrl+Enter)',
    hasAttribute: () => true, getAttribute() { return this.ph; }, setAttribute(k, v) { this.ph = v; } };
  hintScrub(ta); ok('a placeholder loses its hint', ta.ph === 'Reply');

  // the four numbers: the full view (Y) keeps its words, sky mode (the default) says contacts, in warp, parked, settled
  elCache.nums = cap(); W.agent_tasks = [T('r1', 'review')]; SKY = false; W.counts = { need_you: 2, decisions: 0, problems: 3, working: 1 }; stripTick();
  const nn = String(elCache.nums.innerHTML);
  ok('the phone strip says need you, decisions, problems, working (the one count)',
    ['>waiting for you<', '>decisions<', '>problems<', '>working<'].every((w) => nn.includes(w)) && !nn.includes('crons'));
  SKY = true; stripTick(); const ns = String(elCache.nums.innerHTML);
  ok('the phone strip in sky mode says the same, no game words', ns === nn && !/EMPTY SKY|in warp|parked|settled/.test(ns));
  W.agent_tasks = [];

  // badges
  const bd = { attrs: {}, hidden: true, getAttribute(k) { return this.attrs[k]; }, setAttribute(k, v) { this.attrs[k] = v; } };
  elCache['tabn-today'] = bd;
  mobBadge('today', 3); ok('a count shows on the tab', bd.attrs['data-n'] === '3' && bd.hidden === false);
  mobBadge('today', 120); ok('a big count reads 99+', bd.attrs['data-n'] === '99+');
  mobBadge('today', 0); ok('zero hides the badge', bd.hidden === true && bd.attrs['data-n'] === '');
  const dot = { attrs: {}, hidden: true, getAttribute(k) { return this.attrs[k]; }, setAttribute(k, v) { this.attrs[k] = v; } };
  elCache['tabn-wingmate'] = dot; mobBadge('wingmate', 1, true); ok('the wingmate gets a dot, no number', dot.hidden === false && dot.attrs['data-n'] === '');

  // the More sheet and a row's action
  const made = [], realCreate = document.createElement;
  document.createElement = () => { const o = { listeners: {}, className: '', textContent: '', addEventListener(k, f) { this.listeners[k] = f; } }; made.push(o); return o; };
  elCache['msheet-body'] = cap(); elCache['msheet-body'].appendChild = () => {};
  const shSet = new Set(); elCache.msheet = cap(); elCache.msheet.classList = cl(shSet);
  elCache.scrim = cap(); elCache.scrim.classList = cl(new Set());
  let ran = 0;
  msheetOpen('Rows', [{ label: 'one', run() { ran++; } }, { label: 'two', run() { throw new Error('boom'); } }]);
  ok('msheetOpen opens the sheet with one button per row', shSet.has('on') && made.length === 2 && made[0].textContent === 'one' && made[0].className === 'mrow');
  made[0].listeners.click(); ok('a row closes the sheet, then runs its action', !shSet.has('on') && ran === 1);
  msheetOpen('Rows', [{ label: 'boom', run() { throw new Error('boom'); } }]); made[2].listeners.click();
  ok('a row whose action throws leaves the sheet closed and the page alive', !shSet.has('on'));
  suppressClickUntil = Date.now() + 400; msheetOpen('Rows', [{ label: 'one', run() { ran++; } }]); const ranBefore = ran; made[3].listeners.click();
  ok('a row ignores the click that ends the long press which opened the sheet', shSet.has('on') && ran === ranBefore);
  suppressClickUntil = 0; made[3].listeners.click(); ok('and answers the next tap', !shSet.has('on') && ran === ranBefore + 1);
  made.length = 0; const fired = [];
  global.KeyboardEvent = function (t, o) { this.type = t; Object.assign(this, o); }; global.dispatchEvent = (e) => fired.push(e.key);
  mobMore(); const labs = made.map((o) => o.textContent);
  ok('More carries the actions that had only keys, and the Vibrate switch', labs.length === 8 && labs.some((l) => /quick tour/i.test(l)) && labs.some((l) => /board/i.test(l)) && labs.some((l) => /looking at/.test(l))
    && labs.some((l) => /^Flows/.test(l)) && labs.some((l) => l.includes('Theme: ' + THEME_NAME)) && labs.some((l) => /Mono palette/.test(l)) && labs.some((l) => /^Fly pad/.test(l))
    && labs[7] === 'Vibrate on, tap to flip');
  made[2].listeners.click(); made[3].listeners.click(); made[4].listeners.click();
  ok('the key rows send the key the page already answers (L, F, V)', fired.join('') === 'lfv');
  ok('no row label carries a key hint', !labs.some((l) => /\([A-Z]\)|Ctrl/.test(l)));
  document.createElement = realCreate;

  // tabs: the wingmate flag, the view, the way back from a thread
  mtab('wingmate'); ok('the wingmate tab sets its flag over the map view', MOB_WINGMATE === true && VIEW === 'map');
  mtab('today'); ok('Today clears it and shows Today', MOB_WINGMATE === false && VIEW === 'today');
  mtab('threads'); ok('Threads opens the rail over the map view', sideOpen() === true && VIEW === 'map');
  mtab('map'); ok('Map closes the rail', sideOpen() === false && VIEW === 'map');
  mtab('nowhere'); ok('an unknown tab does nothing', VIEW === 'map');
  openTaskId = 't1'; MOB_RET = 'today'; threadBack();
  ok('back out of a thread opened from Today returns to Today', openTaskId === null && VIEW === 'today' && MOB_RET === '');
  mtab('threads'); openTaskId = 't1'; MOB_RET = ''; threadBack();
  ok('back out of any other thread stays on the list', openTaskId === null && sideOpen() === true && VIEW === 'map');
  mtab('map');

  // the toast lands in #mtoast off the map
  elCache.mtoast = cap(); const mt = new Set(); elCache.mtoast.classList = cl(mt);
  toast('saved'); ok('toast() also shows #mtoast on a phone', mt.has('on') && elCache.mtoast.textContent === 'saved');

  // the pick state ends when aimAt runs
  elCache.side = cap(); const ss = new Set(['pick']); elCache.side.classList = cl(ss);
  aimAt(null); ok('aimAt() takes the rail out of pick mode', !ss.has('pick'));

  // the camera aims above the card sheet, one eased step a frame, and gives itself back
  elCache.panel = cap(); const ps = new Set(['on']); elCache.panel.classList = cl(ps); elCache.panel.offsetWidth = 420;
  elCache.side = cap(); elCache.side.classList = cl(new Set());
  mobSync(); ok('an open card is a peek: the sheet height is 35 percent of the screen', SHEET_H === Math.round(844 * 0.35));
  px = 0; py = 0; camOff.x = 0; camOff.y = 0; SHEET_H = 0; SHEET_CUR = 0; draw(2000); const c0 = camy;
  SHEET_H = 300; draw(2016);
  ok('a sheet moves the camera one eased step toward what is above it', camy > c0 && SHEET_CUR > 0 && SHEET_CUR < 300);
  for (let i = 0; i < 60; i++) draw(2032 + i * 16);
  ok('...and settles on it', SHEET_CUR === 300 && camy === Math.round((py + camOff.y - (VH - 300) / 2 / ZOOM) / 2) * 2);
  SHEET_H = 0; for (let i = 0; i < 80; i++) draw(4000 + i * 16);
  ok('closing the sheet gives the camera back', SHEET_CUR === 0 && camy === c0);
  VW = 780; MOB_LAND = false; syncPanelW(); ok('portrait phone: the card is a sheet, the camera keeps no side width', PANEL_W === 0);
  MOB_LAND = true; syncPanelW(); ok('landscape phone: the drawer width moves the camera', PANEL_W === 420);
  MOB_LAND = false;

  // the keyboard is what is left under the visual viewport, not a pinch zoom
  { const root = document.documentElement; root.style = { props: {}, setProperty(k, v) { this.props[k] = v; }, removeProperty(k) { delete this.props[k]; } };
    global.visualViewport = { height: 544, offsetTop: 0, scale: 1 };
    mobVV(); ok('a keyboard shrinks the visual viewport: --kb and html.kb', root.style.props['--kb'] === '300px' && root.classList.contains('kb'));
    visualViewport.height = 422; visualViewport.scale = 2;
    mobVV(); ok('a pinch-zoomed page is not a keyboard: no --kb, no html.kb, the layout keeps its height',
      root.style.props['--kb'] === '0px' && !root.classList.contains('kb') && root.style.props['--vvh'] === '844px' && root.style.props['--vvt'] === '0px');
    visualViewport.scale = 1; visualViewport.height = 844; mobVV(); ok('...and a keyboard that goes away takes html.kb with it', !root.classList.contains('kb'));

    // a waiting seat docks over the tab bar: the screens are told how much room it takes (--P), and the card camera aims above both
    elCache.permits = cap(); const pset = new Set(['on']); elCache.permits.classList = cl(pset); elCache.permits.getBoundingClientRect = () => ({ height: 168.4 });
    mobPerm(); ok('an open permits box reserves its height, rounded up', MOB_P === 169 && root.style.props['--P'] === '169px');
    elCache.panel = cap(); elCache.panel.classList = cl(new Set(['on'])); elCache.side = cap(); elCache.side.classList = cl(new Set());
    mobSync(); ok('the card sheet camera aims above the card and the permits', SHEET_H === Math.round(844 * 0.35) + 169);
    pset.delete('on'); mobPerm(); ok('an answered seat gives the room back', MOB_P === 0 && root.style.props['--P'] === '0px');
    elCache.panel.classList = cl(new Set()); mobSync(); ok('...and the sheet camera with it', SHEET_H === 0);

    // narrowing a desktop window to a phone and widening it again: the stored switches come back, none was written on the way
    const ls = { quiet: '0', mate: 'off', dash: 'on' }, wrote = [];
    global.localStorage = { getItem: (k) => (k in ls ? ls[k] : null), setItem: (k, v) => { wrote.push(k); ls[k] = String(v); } };
    const wset = new Set(['dash']); elCache.wrap = cap(); elCache.wrap.classList = cl(wset);
    QUIET = false; MATE.hidden = true;
    mobStart(); ok('a phone forces the quiet layout, the wingmate and no rail, in memory', QUIET === true && MATE.hidden === false && !wset.has('dash'));
    ok('...and writes no preference', wrote.length === 0 && ls.quiet === '0' && ls.mate === 'off' && ls.dash === 'on');
    MOB = false; mobStop();
    ok('back on a desktop the rail, the full HUD and the hidden wingmate return', wset.has('dash') && QUIET === false && MATE.hidden === true);
    ok('...from the stored switches, which still read what they did', ls.quiet === '0' && ls.mate === 'off' && ls.dash === 'on');
    MOB = true;
  }
"""

# the finger on the canvas: pan, tap, long press, pinch, the pixel ratio and the draw skip (390x844 phone, stub canvas 1280x800)
GESTURE_DRIVER = r"""
  VW = 1280; VH = 800; PANEL_W = 0; SHEET_CUR = 0; SHEET_H = 0; px = 0; py = 0; pilot = null;
  const cl = (set) => ({ contains: (c) => set.has(c), add: (c) => set.add(c), remove: (c) => set.delete(c),
    toggle: (c, f) => { if (f === undefined) f = !set.has(c); if (f) set.add(c); else set.delete(c); return f; } });
  const down = (id, x, y, primary) => fingerDown({ pointerId: id, isPrimary: primary !== false, clientX: x, clientY: y });
  const move = (id, x, y) => fingerMove({ pointerId: id, clientX: x, clientY: y });
  const up = (id, x, y, cancel) => fingerUp({ pointerId: id, clientX: x, clientY: y }, !!cancel);
  const under = (sx, sy) => ({ x: px + camOff.x + (sx - (VW - PANEL_W) / 2) / ZOOM, y: py + camOff.y + (sy - (VH - SHEET_CUR) / 2) / ZOOM });
  const fresh = (z) => { setZoom(z); camOff.x = 0; camOff.y = 0; pilot = null; CLICK_BOXES.length = 0; draw(1000); CLICK_BOXES.length = 0; };
  let acted = null; const realActivate = activate; activate = (u) => { acted = u; };
  let shown = null; const realSheet = msheetOpen; msheetOpen = (t, rows) => { shown = { t: t, rows: rows }; };
  elCache.panel = cap(); const panelSet = new Set(); elCache.panel.classList = cl(panelSet);
  elCache.composer = cap(); const compSet = new Set(); elCache.composer.classList = cl(compSet);
  const m0 = MOONS[0], u0 = unitByUid(m0.uid);

  // one finger: a pan after the slop, by the whole distance from where it landed
  fresh(0.5);
  down(1, 100, 100); move(1, 105, 100);
  ok('inside the slop a finger pans nothing', camOff.x === 0 && dragging === false);
  move(1, 120, 100);
  ok('past the slop it pans by the whole distance from where it landed', Math.abs(camOff.x + 20 / ZOOM) < 1e-9 && dragging === true);
  move(1, 150, 130);
  ok('and then follows the finger one to one', Math.abs(camOff.x + 50 / ZOOM) < 1e-9 && Math.abs(camOff.y + 30 / ZOOM) < 1e-9);
  up(1, 150, 130);
  ok('a lift after a pan is not a tap, and the click after it is ignored', acted === null && dragging === false && suppressClickUntil > Date.now());
  ok('a finger leaves the pointer state clean', FINGERS.size === 0 && GEST === null && PINCH === null && mouseW === null && hover === null);

  // a tap picks with a finger-sized radius: 22 px of screen, in world units
  fresh(0.22);
  const n = Math.hypot(m0.x, m0.y) || 1, off = hitRadius(u0) + 10, wx = m0.x + m0.x / n * off, wy = m0.y + m0.y / n * off;
  const sx = (wx - camx) * ZOOM, sy = (wy - camy) * ZOOM;
  ok('a point just off a planet is a miss for a pointer', pickAt(wx, wy) === null);
  ok('...and a hit with the finger radius', pickAt(wx, wy, 22 / ZOOM) === u0);
  ok('...but a radius too small to reach it still misses', pickAt(wx, wy, 1) === null);
  down(1, sx, sy); up(1, sx, sy);
  ok('a tap there opens the planet', acted === u0);
  acted = null;

  // a tap on empty ground
  camOff.x = 200000; camOff.y = 0; draw(1100); CLICK_BOXES.length = 0; pilot = null;
  down(1, 640, 400); up(1, 640, 400);
  ok('a tap on empty ground flies nowhere (a long press flies)', pilot === null && acted === null);
  panelSet.add('on'); camOff.x = 200000; draw(1200); CLICK_BOXES.length = 0; pilot = null;
  down(1, 640, 400); up(1, 640, 400);
  ok('a tap on empty ground with a sheet open closes the sheet and flies nowhere', !panelSet.has('on') && pilot === null && acted === null);
  panelSet.add('on'); compSet.add('on'); camOff.x = 200000; draw(1300); CLICK_BOXES.length = 0; pilot = null;
  down(1, 640, 400); up(1, 640, 400);
  ok('with the composer open the same tap leaves everything as it was (the sheet is not the composer)', panelSet.has('on') && pilot === null);
  panelSet.clear(); compSet.clear(); pilot = null;

  // a lift cancelled by the system is no tap; a slow touch is no tap
  fresh(0.22); down(1, sx, sy); up(1, sx, sy, true);
  ok('pointercancel never taps', acted === null && FINGERS.size === 0);
  down(1, sx, sy); GEST.t0 -= 600; up(1, sx, sy);
  ok('a touch held past 450 ms is not a tap', acted === null);

  // a held finger: a menu for what is under it, its lift eaten
  fresh(0.22);
  down(1, sx, sy); fingerPress();
  const labels = shown ? shown.rows.map((r) => r.label) : [];
  ok('a held finger opens the menu with the planet\'s rows', !!shown && shown.t === u0.name
    && labels.join('|') === ['Open', 'New task here', 'Fly here', 'Fly in 3D', 'Ask ' + mateName(), 'Prompt an agent'].join('|'));
  { const e3 = [], real3 = CM.enter3d; CM.enter3d = (o) => e3.push(o || {}); pressRows(u0, { x: 1, y: 2 })[3].run(); CM.enter3d = real3;
    ok('Fly in 3D asks the 3D view to fly to the planet held', e3.length === 1 && e3[0].target === u0.uid); }
  up(1, sx, sy);
  ok('the lift of a long press is not a tap and keeps its click off the sheet', acted === null && suppressClickUntil > Date.now() + 300);
  const rowsFor = (hit) => pressRows(hit, { x: 5, y: 6 }).map((r) => r.label).join('|');
  ok('empty ground: fly here, recenter', rowsFor(null) === 'Fly here|Recenter');
  ok('the star has no prompt, a task has none either', !rowsFor(BOARD).includes('Prompt') && rowsFor(BOARD).includes('New task here')
    && !rowsFor({ kind: 'task', uid: 'task:x', name: 'x', path: '/x' }).includes('Prompt'));
  ok('a gate has no new-task row', !rowsFor({ kind: 'portal', uid: 'portal:x', name: 'x' }).includes('New task'));
  camOff.x = 7; pressRows(null, { x: 5, y: 6 })[1].run();
  ok('the recenter row recenters', camOff.x === 0);
  pressRows(null, { x: 9, y: 9 })[0].run();
  ok('the fly row sets a human pilot on that ground', !!pilot && pilot.source === 'human'); pilot = null;
  shown = null; MOB = false; down(1, sx, sy); fingerPress(); up(1, sx, sy); MOB = true;
  ok('on a desktop with a touch screen there is no menu, but the lift is still eaten', shown === null && acted === null);
  compSet.add('on'); shown = null; down(1, sx, sy); fingerPress(); up(1, sx, sy); compSet.clear();
  ok('no menu over the composer', shown === null);

  // a second finger ends the press and starts a pinch
  shown = null; down(1, 300, 300); down(2, 500, 300, false);
  ok('a second finger clears the long press and the tap', PRESS_T === 0 && PINCH !== null && GEST.tap === false && dragging === true);
  fingerPress();
  ok('...so a timer that had already fired opens nothing', shown === null);
  up(2, 500, 300); up(1, 300, 300);

  // the pinch: the world under the fingers' midpoint stays under it
  fresh(0.3);
  const w0 = under(400, 300);
  down(1, 300, 300); down(2, 500, 300, false);
  move(1, 200, 300); move(2, 600, 300);
  ok('twice the finger distance is twice the zoom', Math.abs(ZOOM / 0.3 - 2) < 1e-9);
  const w1 = under(400, 300);
  ok('the point under the midpoint does not move', Math.hypot(w1.x - w0.x, w1.y - w0.y) * ZOOM < 0.01);
  move(1, 250, 300); move(2, 650, 300);
  const w2 = under(450, 300);
  ok('and follows the midpoint when both fingers slide', Math.hypot(w2.x - w0.x, w2.y - w0.y) * ZOOM < 0.01);
  move(1, -200, 300); move(2, 1480, 300);
  ok('the zoom stays inside 0.2 to 2', ZOOM === 2 && Number.isFinite(camOff.x) && Number.isFinite(camOff.y));
  up(2, 1480, 300);
  ok('one finger left: it pans from where it is, no jump', PINCH === null && GEST.pan === true && dragging === true);
  const ox = camOff.x; move(1, -180, 300);
  ok('...and follows', Math.abs(camOff.x - (ox - 20 / ZOOM)) < 1e-9);
  up(1, -180, 300);
  ok('the last finger up ends it, nothing opens', dragging === false && PINCH === null && acted === null && suppressClickUntil > Date.now());

  // a card over the stage moves the pinch centre with the sheet and the drawer
  fresh(0.3); PANEL_W = 300; SHEET_CUR = 200;
  const w3 = under(400, 300);
  down(1, 300, 300); down(2, 500, 300, false); move(1, 200, 300); move(2, 600, 300);
  const w4 = under(400, 300);
  ok('the pinch centre accounts for the drawer and the sheet', Math.hypot(w4.x - w3.x, w4.y - w3.y) * ZOOM < 0.01);
  up(2, 600, 300); up(1, 200, 300); PANEL_W = 0; SHEET_CUR = 0;

  // a pointerup that never came is forgotten when the next gesture starts
  down(7, 10, 10); down(1, 100, 100);
  ok('the first finger of a gesture drops what a lost pointerup left', FINGERS.size === 1 && !FINGERS.has(7));
  up(1, 100, 100);

  // the zoom buttons
  setZoom(0.22); camOff.x = 9; zoomHome();
  { const h = homeView(); ok('long press on the ring: the phone\'s home view, the whole world (fit-all)', ZOOM === h.zoom && camOff.x === h.ox && camOff.y === h.oy && homeZoom() === h.zoom); }
  setZoom(0.22); zoomBy(ZOOM_STEP); ok('+ zooms in a quarter', Math.abs(ZOOM - 0.275) < 1e-9);
  zoomBy(1 / ZOOM_STEP); ok('- zooms out by the same', Math.abs(ZOOM - 0.22) < 1e-9);
  zoomBy(100); ok('+ stops at 2', ZOOM === 2); zoomBy(1e-6); ok('- stops at the phone\'s floor (its fit-all view goes past 0.2)', ZOOM === zoomFloor() && zoomFloor() < 0.2);
  camOff.y = 3; recenter(); ok('the ring recenters', camOff.x === 0 && camOff.y === 0);

  // the pixel ratio
  ok('a phone draws at its ratio up to 3', canvasDpr(3, 390, 788) === 3 && canvasDpr(4, 390, 788) === 3 && canvasDpr(2, 390, 788) === 2);
  ok('...two when the canvas passes 3.8 million pixels', canvasDpr(3, 430, 1000) === 2 && canvasDpr(3, 430, 900) === 3);
  ok('...and 1 for a missing ratio', canvasDpr(undefined, 390, 788) === 1);
  DPR_DROPPED = true; ok('one step lower once the frames said so (never under 1)', canvasDpr(3, 390, 788) === 2 && canvasDpr(1, 390, 788) === 1); DPR_DROPPED = false;

  // covered canvas: the world steps, nothing is drawn; frame time drops the ratio once
  const realBody = document.body, bodyCls = new Set();
  document.body = { dataset: { tab: 'map' }, classList: cl(bodyCls) };
  ok('the map tab is not covered', mapCovered() === false);
  bodyCls.add('m-over'); ok('...but the board over it covers it', mapCovered() === true); bodyCls.clear();
  let drawn = 0; const realDraw = draw; draw = (t) => { drawn++; return realDraw(t); };
  for (const tab of ['today', 'threads', 'wingmate']) { document.body.dataset.tab = tab; step(5000); }
  ok('Today, Threads and the wingmate draw no frames', drawn === 0);
  ok('...but the clock the canvas banners run on keeps moving', FRAME_T === 5000);
  elCache.mtoast = cap(); const mts = new Set(); elCache.mtoast.classList = cl(mts); document.body.dataset.tab = 'threads';
  fxMergeBanner('a merged thread');
  ok('a banner raised under a covered canvas is said in a toast', mts.has('on') && String(elCache.mtoast.textContent).includes('a merged thread'));
  mts.clear(); document.body.dataset.tab = 'map'; fxMergeBanner('b');
  ok('...and not when the map shows it', !mts.has('on')); FX.toast = null;
  document.body.dataset.tab = 'map'; step(5016);
  ok('the map draws', drawn === 1);
  draw = realDraw;
  const realFit = fit; let fits = 0; fit = () => { fits++; };
  FT_PREV = 0; FT_EMA = 0; FT_SLOW = 0; DPR_DROPPED = false;
  let t = 10000, frames = 0;
  while (!DPR_DROPPED && frames < 300) { mobFrameTime(t, true); t += 40; frames++; }
  ok('90 frames over 28 ms drop the ratio a step', DPR_DROPPED === true && frames >= 85 && frames <= 95 && fits === 1);
  for (let i = 0; i < 200; i++) { mobFrameTime(t, true); t += 40; }
  ok('...once', fits === 1);
  DPR_DROPPED = false; fits = 0; FT_PREV = 0; FT_EMA = 0; FT_SLOW = 0;
  for (let i = 0; i < 300; i++) { mobFrameTime(t, true); t += 16; }
  ok('60 frames a second never drops it', DPR_DROPPED === false);
  for (let i = 0; i < 300; i++) { mobFrameTime(t, false); t += 40; }
  ok('throttled idle frames say nothing', DPR_DROPPED === false);
  document.body.dataset.tab = 'today'; for (let i = 0; i < 300; i++) { mobFrameTime(t, true); t += 40; }
  ok('a covered map says nothing either', DPR_DROPPED === false);
  document.body.dataset.tab = 'map'; FT_PREV = 0; mobFrameTime(t, true); t += 40; mobFrameTime(t, true); t += 900; mobFrameTime(t, true);
  ok('a stall is not a measure', FT_EMA < 100);
  fit = realFit; document.body = realBody; DPR_DROPPED = false; FT_PREV = 0; FT_EMA = 0; FT_SLOW = 0;
  activate = realActivate; msheetOpen = realSheet;

  // the label layout on a phone: the card sheet takes the bottom of the map, the phone's own boxes are obstacles,
  // and the wingmate tab is called what the skin calls the wingmate
  SHEET_CUR = 300; const pclamp = plateClamp();
  ok('plates stay above the card sheet', Math.abs(pclamp.h * ZOOM - (VH - 300 - 2 * PLATE.edge)) < 1e-6 && Math.abs(plateSheet() - 300) < 1e-9);
  SHEET_CUR = 0; ok('...and use the whole map without one', Math.abs(plateClamp().h * ZOOM - (VH - 2 * PLATE.edge)) < 1e-6);
  OVERLAY_CACHE.at = -1e9; overlayBoxes();
  ok('the layout reads the tab bar, the buttons over the map, the toast, the pick bar and the More sheet',
    PLATE_OVERLAYS_TOUCH.join() === 'tabs,mfab,mzoom,mclose,pickbar,mtoast,msheet' && PLATE_OVERLAYS_TOUCH.every((id) => id in elCache));
  elCache['tabl-wingmate'] = cap(); { const a = {}; elCache['tab-wingmate'] = cap(); elCache['tab-wingmate'].getAttribute = (k) => a[k]; elCache['tab-wingmate'].setAttribute = (k, v) => { a[k] = v; }; mobSync();
  ok('the assistant\'s tab says Ask and carries the skin\'s name in its label, not a hard-coded one', mateName() === 'Scout' && elCache['tabl-wingmate'].textContent === 'Ask' && /^Ask Scout( · 1 new message)?$/.test(a['aria-label'])); }
"""

# a desktop must not know the phone exists
DESKTOP_DRIVER = r"""
  ok('desktop: MOB is false and html carries no phone class',
    MOB === false && !document.documentElement.classList.contains('m') && !document.documentElement.classList.contains('t'));
  ok('desktop: an unmeasured stage keeps the zoom at 1', ZOOM === 1);
  // a first visit keeps the zoom of 1 (only a phone starts zoomed out), and the reset goes to the same place
  // the first load with no stored zoom fits the world: the far edge of the farthest planet, from the ship, in the stage minus a margin
  ok('desktop: an unmeasured stage has nothing to fit, the reset is 1', homeZoom() === 1);
  { const moons0 = MOONS.splice(0), sx = px, sy = py; px = 0; py = 0; MOONS.push({ x: 1000, y: 0, r: 70 });
    // each axis on its own (the classic system is laid wider than tall, so a circle would waste the width)
    ok('desktop: the fit zoom holds the farthest planet inside the stage', fitZoom(1600, 900) === 0.69 && fitZoom(1600, 2000) === 0.69);
    MOONS[0] = { x: 0, y: 1000, r: 70 }; ok('...a planet straight below fits the height', fitZoom(1600, 900) === 0.38 && fitZoom(1600, 2000) === 0.87);
    MOONS[0] = { x: 1000, y: 0, r: 70 };
    ok('desktop: a small world is never zoomed past 1, a tiny stage is not measured', fitZoom(9000, 9000) === 1 && fitZoom(100, 100) === 1 && fitZoom('x', 3) === 1);
    MOONS.length = 0; ok('desktop: no planets, no fit', fitZoom(1600, 900) === 1);
    MOONS.push({ x: 9000, y: 0, r: 70 }); ok('desktop: a huge world stops at 0.2', fitZoom(1600, 900) === 0.2);
    MOONS.length = 0; moons0.forEach((m) => MOONS.push(m)); px = sx; py = sy; }
  ok('desktop: the pixel ratio keeps its cap of 2', canvasDpr(3, 1280, 800) === 2 && canvasDpr(1, 1280, 800) === 1);
  ok('desktop: the canvas is never covered', mapCovered() === false);
  for (let i = 0; i < 5; i++) draw(1000 + i * 16);
  ok('desktop: the camera never reads a sheet height', SHEET_H === 0 && SHEET_CUR === 0);
  ok('desktop: the strip says the one count in plain words', (() => { elCache.nums = cap(); W.agent_tasks = [T('r1', 'review')]; SKY = false; stripTick();
    const n = String(elCache.nums.innerHTML); SKY = true; W.agent_tasks = []; return n.includes('>waiting for you<') && n.includes('>decisions<') && !n.includes('crons failing'); })());
  ok('desktop: sky mode says the same words', (() => { elCache.nums = cap(); stripTick(); const n = String(elCache.nums.innerHTML); return n.includes('>working<') && !n.includes('settled'); })());
  toast('x'); ok('desktop: toast() has no #mtoast', !('mtoast' in elCache));
  PLATE_OVERLAYS_TOUCH.forEach((id) => { delete elCache[id]; }); OVERLAY_CACHE.at = -1e9; overlayBoxes();
  const vw1 = VW; VW = 1000;
  ok('desktop: the plate layout never reads the phone boxes and the map is whole', PLATE_OVERLAYS_TOUCH.every((id) => !(id in elCache))
    && plateSheet() === 0 && Math.abs(plateClamp().h * ZOOM - (VH - 2 * PLATE.edge)) < 1e-6);
  VW = vw1;
  ok('desktop: no history, observers or timers were started for the phone', MOB_BOOTED === true && MOB_MO === null);
"""


def page_js(html_path, out_path):
    html = Path(html_path).read_text(encoding="utf-8")
    match = re.search(r"<script>(.*)</script>", html, re.S)
    Path(out_path).write_text(match.group(1), encoding="utf-8")


def run_page_driver(case, js, body, location=None, env_extra=None):
    """Run one driver body against the page script `js` in the stub DOM; the test case asserts the outcome."""
    with tempfile.TemporaryDirectory() as tmp:
        harness, driver = Path(tmp, "harness.js"), Path(tmp, "driver.js")
        harness.write_text(HARNESS, encoding="utf-8")
        driver.write_text(DRIVER_HEAD + body + DRIVER_TAIL, encoding="utf-8")
        env = dict(os.environ, **(env_extra or {}))
        if location:
            env["HARNESS_LOCATION"] = json.dumps(location)
        run = subprocess.run(["node", str(harness), str(js), str(driver)], env=env,
                             capture_output=True, text=True, timeout=120)
    case.assertEqual(run.returncode, 0, msg=(run.stdout + run.stderr)[-4000:])
    case.assertIn("SMOKE_OK", run.stdout)
    case.assertGreater(run.stdout.count("PASS "), 0, msg=run.stdout)


def fixture_config(tmp, **extra):
    """A throwaway workspace config: nothing of the owner's, every file under `tmp`."""
    root = Path(tmp, "work")
    root.mkdir(exist_ok=True)
    cfg = {"work_root": str(root), "tmux_session": "none", "processes": False,
           "decision_scan": False, "crontab_file": str(Path(tmp, "no-crontab")),
           "cron_syslog_file": "", "name": "FIXTURE",
           "history_jsonl": str(Path(tmp, "history.jsonl")), "tasks_dir": str(Path(tmp, "tasks")),
           "rulings_json": str(Path(tmp, "no-rulings.json"))}
    cfg.update(extra)
    path = Path(tmp, "fixture.json")
    path.write_text(json.dumps(cfg), encoding="utf-8")
    return path


def render_fixture(tmp, *args, env_extra=None, **extra):
    """Run render-game.py on the fixture config; returns the finished process. BRIDGE_PORT from
    the caller's environment never reaches it (the page bakes that port in); env_extra sets more."""
    cfg = fixture_config(tmp, **extra)
    env = dict(os.environ, CONTAINER_CONFIG=str(cfg), CONTAINER_OUT=str(Path(tmp, "page.html")))
    env.pop("BRIDGE_PORT", None)
    env.update(env_extra or {})
    return subprocess.run([sys.executable, str(ROOT / "render-game.py"), *args], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=120)


# the meta board (U): the key, the panel, what it draws from a hostile answer, and the routes its buttons call
META_DRIVER = r"""
  ok('the page is the live desktop page', LIVE === true && DESK === true && REMOTE === false);
  const sets = {};
  const cl = (id) => { const st = sets[id] = sets[id] || new Set(); return { contains: (c) => st.has(c),
    add: (...c) => c.forEach((x) => st.add(x)), remove: (...c) => c.forEach((x) => st.delete(x)),
    toggle: (c, f) => { if (f === undefined) f = !st.has(c); if (f) st.add(c); else st.delete(c); return f; } }; };
  ['meta', 'wrap', 'side', 'composer', 'legend', 'panel', 'quests', 'prompter'].forEach((id) => {
    elCache[id] = cap(); elCache[id].classList = cl(id); });
  ['metaboard', 'metachat', 'metastat', 'metago', 'metaq', 'metamodel', 'metamiss'].forEach((id) => { elCache[id] = cap(); });
  const isOpen = () => sets.meta.has('on');
  const fire = (key, o) => {
    const e = Object.assign({ key: key, repeat: false, ctrlKey: false, metaKey: false, altKey: false, shiftKey: false,
      prevented: false, preventDefault() { this.prevented = true; }, stopPropagation() {},
      getModifierState() { return false; }, target: document.activeElement || { blur() {} } }, o || {});
    (__WL.keydown || []).forEach((f) => f(e)); return e; };
  global.setTimeout = (fn) => { setImmediate(fn); return 0; };
  VIEW = 'map'; document.activeElement = null;

  const A = '20261018-101500-add-export-ab12cd', B = '20261018-093000-fix-labels-ef34ab';
  const C = '20261018-083000-rename-me-cd56ef', D = '20261018-073000-check-me-ab99cd';
  const E = '20261018-063000-runaway-aabbcc', F = '20261018-053000-dupe-ddeeff', G = '20261018-043000-fine-112233';
  const row = (ref, verdict, reason) => { const k = ref.split(':')[0];
    return { ref: ref, kind: k, id: ref.slice(k.length + 1), verdict: verdict, reason: reason }; };
  const INFO = { available: true, models: [{ name: 'haiku', label: 'Claude Haiku 5.5', family: 'claude' },
      { name: 'sonnet', label: 'Claude Sonnet 5.5', family: 'claude' }], default: 'sonnet', model: 'Claude Sonnet 5.5',
    max_seconds: 300, pending: 0, pending_id: null, elapsed: 0, chat: [{ q: 'Triage everything.', summary: 'Earlier: <b>two</b> were ready.' }], last: null };
  const RES = { id: 'aaaaaaaaaaaa', status: 'done', finished: 200, q: 'Triage everything.', model: 'Claude Sonnet 5.5', seconds: 41.2, error: null,
    triage: true, summary: 'One ready.', run: '20261019-120000',
    rows: [row('task:' + A, 'merge', 'review, no blockers <script>x()</script>'), row('task:' + B, 'follow-up', 'failed: fixture missing'),
      row('task:' + C, 'rename', 'the title says nothing'), row('task:' + D, 'check', 'unchecked'), row('task:' + E, 'stop', 'running 9h'),
      row('task:' + F, 'set-aside', 'duplicate of another'), row('task:' + G, 'keep', 'fine'),
      row('t3:th1', 'close', 'settled 5d ago'), row('t3:th2', 'keep', 'x'), row('seat:grok', 'go', 'waiting on a permission'),
      row('wt:/w/spike', 'retire', 'no owner, clean, ahead 0'), row('wt:/w/q"><b>x', 'land', 'carries 2 commits')],
    sends: [{ ref: 'task:' + B, kind: 'task', id: B, text: 'Create "the" fixture </textarea><script>boom()</script>' }],
    renames: [{ ref: 'task:' + C, id: C, title: 'Fix "labels" & overlap' }], checks: [{ id: D, runner: 'codex' }],
    missing: ['what each T3 thread was last asked <i>'], notes: ['worktree facts stopped at the 20 s budget'], unjudged: ['seat:idle'],
    rejected: [{ line: 'x', why: 'y' }],
    items: { ['task:' + A]: { kind: 'task', id: A, title: 'Add export <img src=x onerror=alert(1)>', status: 'review', runner: 'claude', model: '' },
      ['task:' + B]: { kind: 'task', id: B, title: 'x', status: 'failed' }, ['task:' + C]: { kind: 'task', id: C, title: 'Rename me', status: 'review' },
      ['task:' + D]: { kind: 'task', id: D, title: 'Check me', status: 'review' }, ['task:' + E]: { kind: 'task', id: E, title: 'Runaway', status: 'running' },
      ['task:' + F]: { kind: 'task', id: F, title: 'Dupe', status: 'review' }, ['task:' + G]: { kind: 'task', id: G, title: 'Fine', status: 'running' },
      'seat:idle': { kind: 'seat', id: 'idle', title: 'idle seat' },
      'seat:grok': { kind: 'seat', id: 'grok', uid: 'seat:grok', title: 'grok', status: 'WAITING' },
      'wt:/w/spike': { kind: 'wt', id: '/w/spike', title: 'spike', repo: '/w', branch: 'spike', ahead: 0, behind: 3, dirty: 0, subject: 'try <it>',
        owner: null, command: 'git -C /w worktree remove /w/spike' },
      'wt:/w/q"><b>x': { kind: 'wt', id: '/w/q"><b>x', title: 'q', repo: '/w', branch: 'q', ahead: 2, behind: 0, dirty: 2, subject: 's', owner: A },
      't3:th1': { kind: 't3', id: 'th1', title: 'Port <the> page', status: 'settled', open_url: 'http://t3.example/bridge?focus=e:th1', uid: 'ship:t3:th1' },
      't3:th2': { kind: 't3', id: 'th2', title: 'Bad link', status: 'settled', open_url: 'javascript:alert(1)', uid: 'ship:t3:th2' } } };
  const calls = []; let answers = 0;
  const reply = (body) => ({ ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(body)) });
  global.fetch = async (url, opt) => { calls.push([String(url), opt && opt.method, opt && opt.body]);
    if (url === '/meta') return reply(INFO);
    if (url === '/meta/ask') return reply({ id: 'aaaaaaaaaaaa', status: 'thinking', model: 'Claude Sonnet 5.5' });
    if (String(url).startsWith('/meta/answer')) return reply(++answers < 2 ? { id: 'aaaaaaaaaaaa', status: 'thinking' } : RES);
    return reply({}); };
  const polled = [], opened = [], shown = [];
  pollTasks = async () => { polled.push(1); };
  openTask = (id) => { opened.push(id); };
  show = (u) => { shown.push(u.uid); };
  units.push({ uid: 'seat:grok', kind: 'seat', name: 'grok', status: 'WAITING', gx: 1, gy: 1 });
  W.agent_tasks = [T(B, 'failed', { title: 'Fix the labels' })];

  // the key: U opens and closes it, Esc closes it, a chord or a typed U does not
  fire('u'); await settle();
  ok('U opens the meta board', isOpen());
  ok('...and asks the bridge who answers', calls.some((c) => c[0] === '/meta'));
  ok('the earlier chat is shown, escaped', String(elCache.metachat.innerHTML).includes('Earlier: &lt;b&gt;two&lt;/b&gt; were ready.')
    && !String(elCache.metachat.innerHTML).includes('<b>two'));
  ok('the model pill names the pick when there is a choice', elCache.metamodel.hidden === false && elCache.metamodel.textContent === 'Claude Sonnet 5.5');
  fire('Escape'); ok('Esc closes it', !isOpen());
  fire('u'); closeAll(); ok('closeAll closes it', !isOpen());
  fire('u'); fire('u'); ok('U toggles it off', !isOpen());
  ['ctrlKey', 'metaKey', 'altKey'].forEach((m) => { const e = fire('u', { [m]: true });
    ok('no chord with U opens it (' + m + ')', !isOpen() && !e.prevented); });
  document.activeElement = { tagName: 'INPUT', blur() {} }; fire('u'); ok('a U typed into a box does not open it', !isOpen());
  document.activeElement = null;
  fire('u', { repeat: true }); ok('a held U does not open it', !isOpen());

  VIEW = 'today'; fire('u'); await settle();
  ok('from the Today page U brings the map up under the board', isOpen() && VIEW === 'map'); fire('u'); VIEW = 'map';

  // Triage all: one ask, then the answer is polled until it is no longer thinking
  fire('u'); await settle();
  calls.length = 0;
  await metaAsk(''); await settle(); await settle();
  const ask = calls.find((c) => c[0] === '/meta/ask');
  ok('Triage all posts an empty question with the picked model', !!ask && ask[1] === 'POST' && ask[2] === JSON.stringify({ model: 'sonnet' }));
  ok('it polls /meta/answer until done', calls.filter((c) => c[0].startsWith('/meta/answer?id=aaaaaaaaaaaa')).length >= 2);
  ok('it is no longer busy', META.busy === false);
  const html = String(elCache.metaboard.innerHTML);
  ['Merge · <b>1</b>', 'Follow up · <b>1</b>', 'Check · <b>1</b>', 'Rename · <b>1</b>', 'Set aside · <b>1</b>', 'Stop · <b>1</b>',
    'Close in T3 · <b>1</b>', 'Needs you · <b>1</b>', 'Land · <b>1</b>', 'Retire the worktree · <b>1</b>', 'Keep · <b>2</b>'].forEach((h) =>
    ok('group ' + h, html.includes(h)));
  ok('the keep group is folded away', html.includes('<details class="mg"><summary>Keep'));
  ok('every title, reason and path is escaped', !html.includes('<img') && html.includes('Add export &lt;img src=x onerror=alert(1)&gt;')
    && !html.includes('<script') && html.includes('review, no blockers &lt;script&gt;x()&lt;/script&gt;')
    && html.includes('Port &lt;the&gt; page') && html.includes('try &lt;it&gt;') && !html.includes('<b>x'));
  ok('an attribute cannot be broken out of', html.includes('data-ref="wt:/w/q&quot;&gt;&lt;b&gt;x"'));
  ok('a task shows its current title when the page knows a newer one', html.includes('Fix the labels') && !html.includes('>x<small>'));
  ok('merge, set aside and stop only open the guarded thread panel', html.includes('open to merge') && html.includes('open to set aside')
    && html.includes('open to stop') && !html.includes('data-ma="merge"') && !html.includes('data-ma="close"') && !html.includes('data-ma="stop"'));
  ok('a follow-up is an editable box prefilled with the prompt and a send button', html.includes('<textarea maxlength="8000" aria-label="follow-up">Create "the" fixture &lt;/textarea&gt;&lt;script&gt;boom()&lt;/script&gt;</textarea>')
    && html.includes('data-ma="send"'));
  ok('a rename shows the proposed title in a box with a rename button', html.includes('value="Fix &quot;labels&quot; &amp; overlap"') && html.includes('data-ma="rename"'));
  ok('a check names its checker', html.includes('open · check with codex') && html.includes('data-runner="codex"'));
  ok('a T3 row links out only to an http(s) address', html.includes('href="http://t3.example/bridge?focus=e:th1"') && html.includes('rel="noopener noreferrer"')
    && !html.includes('javascript:'));
  ok('a seat row goes to the seat on the map', html.includes('data-ma="seat" data-id="grok" data-uid="seat:grok"'));
  ok('a worktree row has its path, what it carries and no button to remove it', html.includes('<div class="ms">/w/spike</div>') && html.includes('worktree on spike')
    && html.includes('ahead 0 behind 3 · clean · no task · last: try &lt;it&gt;') && html.includes('ahead 2 behind 0 · 2 changed files · task ' + A));
  ok('retire shows the exact command as selectable text', html.includes('<pre class="mcmd"') && html.includes('>git -C /w worktree remove /w/spike</pre>')
    && !html.includes('data-ma="retire"') && !html.includes('data-ma="remove"'));
  ok('what it could not see: its own lines, then the facts the bridge could not gather',
    html.includes('What I couldn’t see · <b>1</b>') && html.includes('what each T3 thread was last asked &lt;i&gt;')
    && html.includes('worktree facts stopped at the 20 s budget') && html.includes('1 item got no verdict') && html.includes('1 line of its answer did not fit')
    && html.includes('meta/20261019-120000'));
  ok('the status line says who answered', String(elCache.metastat.textContent).includes('answered by Claude Sonnet 5.5 in 41 s · 12 verdicts'));

  // the buttons: each one is a call the page already makes, or opens the thread panel
  const click = async (ref, what, extra, data) => {
    const input = { value: extra };
    const rowEl = { dataset: { ref: ref }, querySelector: () => input };
    const b = { dataset: Object.assign({ ma: what, id: ref.slice(ref.indexOf(':') + 1) }, data || {}), disabled: false, closest: () => rowEl };
    await metaClick({ target: { closest: () => b } }); await settle(); return b; };
  calls.length = 0;
  await click('task:' + A, 'open'); ok('open opens the thread panel and closes the board (kept for U)', opened.join() === A && !isOpen());
  fire('u'); await settle();
  ok('reopened, the same answer is still there', String(elCache.metaboard.innerHTML).includes('Merge · <b>1</b>'));
  calls.length = 0;
  await click('task:' + B, 'send', '  Create the fixture, rerun.  ');
  const sent = calls.find((c) => c[0] === '/task/followup');
  ok('send posts the edited text to /task/followup', !!sent && sent[1] === 'POST' && sent[2] === JSON.stringify({ id: B, text: 'Create the fixture, rerun.' }));
  ok('...refreshes the threads and says so on the row', polled.length >= 1 && String(elCache.metaboard.innerHTML).includes('sent: the agent is on it'));
  calls.length = 0;
  await click('task:' + B, 'send', '   ');
  ok('an empty prompt is not sent', !calls.some((c) => c[0] === '/task/followup') && String(elCache.metaboard.innerHTML).includes('say what to do next'));
  await click('task:' + C, 'rename', ' Better title ');
  const ren = calls.find((c) => c[0] === '/task/rename');
  ok('rename posts the title to /task/rename', !!ren && ren[1] === 'POST' && ren[2] === JSON.stringify({ id: C, title: 'Better title' }));
  ok('...and says renamed', String(elCache.metaboard.innerHTML).includes('renamed'));
  await click('task:' + D, 'check', '', { runner: 'codex' });
  ok('check opens the thread and offers the checker without running it', opened[opened.length - 1] === D && !calls.some((c) => c[0] === '/task/check')
    && String(flightFlash.text).includes('check it'));
  fire('u'); await settle();
  await click('seat:grok', 'seat', '', { uid: 'seat:grok' });
  ok('a seat button shows the seat on the map', shown.join() === 'seat:grok' && !isOpen());
  ok('nothing the board did merged, closed, stopped or removed',
    !calls.some((c) => /^\/task\/(close|stop|check)/.test(c[0]) || c[0].startsWith('/agent/') || c[0].startsWith('/open')));

  // a failing button keeps its row and says why
  fire('u'); await settle();
  const scripted = global.fetch;
  global.fetch = async (url, opt) => { if (url === '/task/rename') return { ok: false, status: 409, json: async () => ({ error: 'this thread is busy' }) };
    return scripted(url, opt); };
  const failed = await click('task:' + C, 'rename', 'Another');
  ok('a refused rename says why and can be tried again', failed.disabled === false && String(elCache.metaboard.innerHTML).includes('this thread is busy'));
  // a model that failed
  META.res = { id: 'bbbbbbbbbbbb', status: 'error', error: 'the model failed: login expired', notes: ['the thread list could not be read: x'] }; metaDraw();
  ok('an error is shown as it came, with what could not be read', String(elCache.metaboard.innerHTML).includes('the model failed: login expired')
    && String(elCache.metaboard.innerHTML).includes('the thread list could not be read: x'));
  // a run that is still going when the board is reopened is picked up, with the elapsed time
  fire('Escape'); META.res = null; META.busy = false; answers = 0;
  global.fetch = scripted; calls.length = 0;
  INFO.pending = 1; INFO.pending_id = 'aaaaaaaaaaaa'; INFO.elapsed = 12;
  fire('u'); await settle(); await settle(); await settle();
  ok('a run that outlived the board is found again and awaited', calls.some((c) => c[0].startsWith('/meta/answer?id=aaaaaaaaaaaa')) && META.busy === false
    && String(elCache.metaboard.innerHTML).includes('Merge · <b>1</b>'));
  // reopening finds a newer finished run (another tab, or a poll that failed): it replaces the stale board
  const OLD = Object.assign({}, RES, { id: 'cccccccccccc', finished: 100, summary: 'old', rows: [row('task:' + G, 'keep', 'the old board')] });
  META.res = OLD; META.busy = false; META.done = { ['task:' + G]: { text: 'sent: the agent is on it', err: false } };
  META.edits = { ['task:' + G]: 'half typed' };
  INFO.available = true; INFO.pending = 0; INFO.pending_id = null; INFO.last = RES; META.info = INFO;
  metaDraw(true);
  ok('the stale board shows the old rows', String(elCache.metaboard.innerHTML).includes('the old board'));
  await metaLoad();
  ok('a newer finished run from the bridge replaces the board', META.res.id === 'aaaaaaaaaaaa'
    && String(elCache.metaboard.innerHTML).includes('Merge · <b>1</b>') && !String(elCache.metaboard.innerHTML).includes('the old board'));
  ok('...and the old receipts and typed edits go with it', Object.keys(META.done).length === 0 && Object.keys(META.edits).length === 0);
  META.done = { ['task:' + B]: { text: 'sent: the agent is on it', err: false } }; META.edits = { ['task:' + B]: 'my edit' };
  await metaLoad();
  ok('the same run again keeps what was typed and sent', META.res.id === 'aaaaaaaaaaaa' && META.edits['task:' + B] === 'my edit'
    && META.done['task:' + B].text.startsWith('sent'));
  INFO.last = null; await metaLoad();
  ok('a bridge with no finished run (just restarted) leaves the board as it is', META.res.id === 'aaaaaaaaaaaa'
    && String(elCache.metaboard.innerHTML).includes('Merge · <b>1</b>'));
  INFO.last = Object.assign({}, RES, { id: 'dddddddddddd', finished: 300, status: 'error', error: 'the model failed: login expired', rows: [] }); await metaLoad();
  ok('a newer failed run is shown as failed, not hidden behind the older good board', META.res.id === 'dddddddddddd'
    && String(elCache.metaboard.innerHTML).includes('the model failed: login expired'));
  INFO.last = RES; META.res = RES; META.done = {}; META.edits = {}; metaDraw(true);
  // overlapping requests: the answer to the older one arrives last and must not roll the board back (B then A)
  const waiting = []; let hold = false; const direct = global.fetch;
  global.fetch = async (url, opt) => (hold && url === '/meta') ? new Promise((resolve) => waiting.push((body) => resolve(reply(body)))) : direct(url, opt);
  const newer = Object.assign({}, INFO, { last: RES }), older = Object.assign({}, INFO, { last: OLD });
  META.res = null; META.info = null; hold = true;
  const first = metaLoad(), second = metaLoad();
  ok('two overlapping reopen requests are in flight', waiting.length === 2);
  waiting[1](newer); await second; await settle();
  ok('the newer request installs the newer board', META.res && META.res.id === 'aaaaaaaaaaaa');
  META.edits = { ['task:' + B]: 'typed after it appeared' }; META.done = { ['task:' + B]: { text: 'sent: the agent is on it', err: false } };
  waiting[0](older); await first; await settle();
  ok('the delayed older answer is dropped: B then A stays B', META.res.id === 'aaaaaaaaaaaa' && META.edits['task:' + B] === 'typed after it appeared'
    && META.done['task:' + B].text.startsWith('sent') && String(elCache.metaboard.innerHTML).includes('Merge · <b>1</b>'));
  hold = false;
  META.res = RES; META.done = { ['task:' + B]: { text: 'sent', err: false } }; META.edits = { ['task:' + B]: 'kept' };
  INFO.last = OLD; await metaLoad();
  ok('a result that finished before the one held never replaces it', META.res.id === 'aaaaaaaaaaaa' && META.edits['task:' + B] === 'kept'
    && META.done['task:' + B].text === 'sent');
  INFO.last = Object.assign({}, OLD, { finished: 250 }); await metaLoad();
  ok('...but one that finished after it does', META.res.id === 'cccccccccccc' && Object.keys(META.edits).length === 0);
  global.fetch = direct; INFO.last = RES; META.res = RES; META.done = {}; META.edits = {};
  // no model set up: the box says so and the button waits
  INFO.available = false; INFO.pending = 0; INFO.pending_id = null; META.info = INFO; metaDraw();
  ok('with no model the board says what to set', elCache.metago.disabled === true && String(elCache.metaq.placeholder).includes('container.json'));
"""

# pages agents made (the show op "page"): the cards, the toast, the panel and the sandboxed frame
PAGES_DRIVER = r"""
  const sets = {};
  const cl = (id) => { const st = sets[id] = sets[id] || new Set(); return { contains: (c) => st.has(c),
    add: (...c) => c.forEach((x) => st.add(x)), remove: (...c) => c.forEach((x) => st.delete(x)),
    toggle: (c, f) => { if (f === undefined) f = !st.has(c); if (f) st.add(c); else st.delete(c); return f; } }; };
  let frameWrites = 0;
  ['pgpanel', 'pgdock', 'pgtoast'].forEach((id) => { elCache[id] = cap(); elCache[id].classList = cl(id); });
  ['pghead', 'pgtabs', 'pgclose'].forEach((id) => { elCache[id] = cap(); });
  const frameEl = cap();                                   // the frame's holder, counting how often the page rewrites it
  elCache.pgframe = new Proxy(frameEl, { set: (t, k, v) => { if (k === 'innerHTML') frameWrites++; t[k] = v; return true; }, get: (t, k) => t[k] });
  const isOpen = () => sets.pgpanel.has('on'), toastUp = () => sets.pgtoast.has('on');
  const fire = (key) => { const e = { key: key, repeat: false, ctrlKey: false, metaKey: false, altKey: false, shiftKey: false,
    preventDefault() {}, stopPropagation() {}, getModifierState() { return false; }, target: document.activeElement || { blur() {} } };
    (__WL.keydown || []).forEach((f) => f(e)); };
  global.setTimeout = (fn) => { setImmediate(fn); return 0; };
  VIEW = 'map'; document.activeElement = null;

  const calls = [];
  global.fetch = async (url) => { calls.push(String(url)); return { ok: true, status: 200, json: async () => ({}) }; };
  const mk = (id, by, title, extra) => Object.assign({ id: id, by: by, at: 'area:x', ttl: 1800, t: '2026-10-20T10:00:00Z', rev: 1,
    expires: new Date(Date.now() + 25 * 60000).toISOString(), expires_in: 1500,
    ops: [{ op: 'page', at: 'area:x', title: title, bytes: 120 }] }, extra || {});
  const click = (attr, id) => pgClick({ target: { closest: (sel) => (sel === '[' + attr + ']' ? { dataset: { [attr.slice(5)]: id } } : null) } });
  const html = () => String(elCache.pgframe.innerHTML);
  const src = () => { const m = /src="([^"]*)"/.exec(html()); return m ? m[1].replace(/&amp;/g, '&') : null; };
  const framed = (id, rev) => src() === '/show/page/frame?id=' + encodeURIComponent(id) + '&rev=' + rev;

  // a reply with no shows in it (the threads of a /state reply) says nothing about pages
  pgSync({ tasks: [] });
  ok('a reply that carries no shows neither primes nor announces', PG.primed === false && !toastUp());
  // the cards: a marker, the title, the author and the place; nothing yet to toast about at the first read
  W.shows = [mk('p1', 'Claude Code', 'Latency board', { rev: 11 })]; pgSync({ shows: W.shows });
  let dock = String(elCache.pgdock.innerHTML);
  ok('a live page draws a card in the dock', sets.pgdock.has('on') && dock.includes('class="pgm">page<') && dock.includes('Latency board')
    && dock.includes('by Claude Code') && dock.includes('data-pg="p1"') && dock.includes('role="button"'));
  ok('...that names its place and how long it has', dock.includes(' · at ') && /2[45] min left/.test(dock));
  ok('a page that was there when the map loaded is not announced', !toastUp() && String(elCache.pgtoast.innerHTML) === '');
  W.shows.push({ id: 'x', by: 'a', ops: [{ op: 'label', text: 'hi' }] });
  ok('a show with no page op draws no card', pgList().length === 1);

  // a new page: a toast with an Open button, never an open panel
  W.shows.push(mk('p2', 'Codex', 'Bug <b>map</b>', { rev: 12 })); pgSync({ shows: W.shows });
  let toast = String(elCache.pgtoast.innerHTML);
  ok('a new page toasts "<by> made a page: <title>" with Open', toastUp() && toast.includes('<b>Codex</b> made a page: Bug &lt;b&gt;map&lt;/b&gt;')
    && toast.includes('<button type="button" data-pg="p2">Open</button>'));
  ok('...and the panel stays shut, with no frame', !isOpen() && frameWrites === 0 && html() === '');
  elCache.pgtoast.innerHTML = ''; pgSync({ shows: W.shows });
  ok('the same pages again are not announced twice', String(elCache.pgtoast.innerHTML) === '');
  click('data-pgx', '1');
  ok('the toast has a dismiss', !toastUp());

  // opened from the card: the frame is the page's own document, named by id and revision, with exactly allow-scripts
  click('data-pg', 'p1');
  let head = String(elCache.pghead.innerHTML), frame = html();
  ok('opening opens the panel', isOpen() && PG.open === 'p1');
  ok('the header reads "<title> · by <by> · expires in …"', head.includes('<b>Latency board</b> · by Claude Code · expires in '));
  ok('the frame is one iframe with exactly sandbox="allow-scripts" and no referrer', /^<iframe sandbox="allow-scripts" referrerpolicy="no-referrer"/.test(frame)
    && frame.match(/sandbox="([^"]*)"/)[1] === 'allow-scripts' && frame.match(/<iframe/g).length === 1);
  ok('...and no other sandbox right', !/allow-(same-origin|forms|popups|top-navigation|modals|downloads|popups-to-escape)/.test(frame));
  ok('...whose address is the frame route with the page\'s id and revision, and no srcdoc', framed('p1', 11) && !/srcdoc/.test(frame));
  ok('no source passes through this page: nothing was fetched', !calls.some((c) => c.indexOf('/show/page') >= 0));
  ok('the live pages are tabs to switch between, the open one marked', String(elCache.pgtabs.innerHTML).includes('data-pgtab="p2" aria-current="false">Bug')
    && String(elCache.pgtabs.innerHTML).includes('data-pgtab="p1" aria-current="true">Latency board'));

  // Esc closes it, and the frame goes with it (its scripts stop)
  ok('the back layer says a page is up', CM.backLayer('has') === true);
  fire('Escape'); ok('Esc closes the panel', !isOpen() && html() === '' && CM.backLayer('has') === false);
  // opened from the toast, and switched between from the panel
  W.shows.push(mk('p3', 'Claude Code', 'Third', { rev: 13 })); pgSync({ shows: W.shows });
  ok('a third page toasts too, naming the newest', String(elCache.pgtoast.innerHTML).includes('made a page: Third'));
  click('data-pg', 'p2');
  ok('the toast\'s Open opens that page', isOpen() && framed('p2', 12) && String(elCache.pghead.innerHTML).includes('Bug &lt;b&gt;map&lt;/b&gt;'));
  click('data-pgtab', 'p1');
  ok('a tab switches to another page', framed('p1', 11) && String(elCache.pghead.innerHTML).includes('Latency board'));
  closeAll(); ok('closeAll closes it', !isOpen() && html() === '');

  // an id with quotes and angle brackets cannot leave the address or the attribute
  W.shows = [mk('a"b<c', 'x', 't', { rev: 14 })]; pgSync({ shows: W.shows });
  ok('a hostile id cannot leave its card\'s attribute', !String(elCache.pgdock.innerHTML).includes('"b<c') && String(elCache.pgdock.innerHTML).includes('data-pg="a&quot;b&lt;c"'));
  click('data-pg', 'a"b<c');
  ok('...nor the frame\'s address', !html().includes('"b<c') && html().includes('id=a%22b%3Cc&amp;rev=14') && framed('a"b<c', 14));
  closeAll();
  W.shows = [mk('p1', 'Claude Code', 'Latency board', { rev: 11 }), mk('p2', 'Codex', 'Bug <b>map</b>', { rev: 12 }), mk('p3', 'Claude Code', 'Third', { rev: 13 })];
  pgSync({ shows: W.shows });

  // a card that is stale (its page is gone since the list was read) opens to the expiry notice, not to a frame
  click('data-pg', 'p9');
  ok('a card for a page that is gone says so and loads no frame', isOpen() && !html().includes('<iframe') && html().includes('This page expired.') && PG.gone === true);
  closeAll();

  // replaced under the same id while it is open: the panel loads the new revision, and a same revision is left alone
  click('data-pg', 'p1'); const writes = frameWrites;
  elCache.pgtoast.innerHTML = ''; sets.pgtoast.delete('on');
  W.shows = W.shows.map((x) => (x.id === 'p1' ? mk('p1', 'Claude Code', 'Latency board', { rev: 11, expires: new Date(Date.now() + 20 * 60000).toISOString() }) : x)); pgSync({ shows: W.shows });
  ok('the same revision is the same page: the frame is left alone, whatever the clock says', frameWrites === writes && framed('p1', 11));
  W.shows = W.shows.map((x) => (x.id === 'p1' ? mk('p1', 'Claude Code', 'Latency board', { rev: 15, t: '2026-10-20T10:00:00Z', expires: x.expires }) : x)); pgSync({ shows: W.shows });
  ok('a new revision, in the same second with the same size and ttl, is loaded', frameWrites === writes + 1 && framed('p1', 15));
  ok('...without a toast (it is not new)', !toastUp() && String(elCache.pgtoast.innerHTML) === '');
  closeAll();

  // an open page that is taken down or runs out loses its frame (its scripts stop with it) and the panel says so
  click('data-pg', 'p1');
  W.shows = W.shows.filter((x) => x.id !== 'p1'); pgSync({ shows: W.shows });
  ok('the frame is gone, the panel says the page expired, the others are still tabs', !html().includes('<iframe') && html().includes('This page expired.')
    && String(elCache.pghead.innerHTML).includes('This page is gone') && String(elCache.pgtabs.innerHTML).includes('data-pgtab="p2"') && isOpen());
  const after = frameWrites; pgSync({ shows: W.shows });
  ok('and another sync leaves it so', frameWrites === after && html().includes('This page expired.'));
  click('data-pgtab', 'p2');
  ok('another page can still be picked from the tabs', framed('p2', 12) && PG.open === 'p2' && PG.gone === false);
  W.shows = W.shows.filter((x) => x.id !== 'p2'); pgSync({ shows: W.shows });
  ok('a page that was loaded loses its frame when it is taken down', !html().includes('<iframe') && html().includes('This page expired.'));
  W.shows = W.shows.concat([mk('p2', 'Codex', 'Two again', { rev: 30 })]); pgSync({ shows: W.shows });
  ok('a page of that id put anew is loaded into the open panel', framed('p2', 30));
  closeAll();
  // expired: the card says so
  W.shows = [mk('p1', 'Claude Code', 'Latency board', { expires: new Date(Date.now() - 5000).toISOString(), expires_in: 0 })]; pgSync({ shows: W.shows });
  ok('a card past its time reads expired', String(elCache.pgdock.innerHTML).includes('expired'));

  // hostile words are text everywhere
  const evil = '"><img src=x onerror=alert(1)><script>boom()</script>';
  W.shows = [mk('p9', 'Evil ' + evil, evil, { id: 'p9', at: 'place:' + evil })]; PG.seen.clear(); pgSync({ shows: W.shows }); click('data-pg', 'p9');
  const all = String(elCache.pgdock.innerHTML) + String(elCache.pgtoast.innerHTML) + String(elCache.pghead.innerHTML) + String(elCache.pgtabs.innerHTML);
  ok('titles, authors and places are escaped in the card, the toast and the header', !/<img|<script|onerror=alert\(1\)><|"><img/.test(all) && all.includes('&lt;img src=x'));
  closeAll();

  // what is not here any more: the page's source never passes through this page, so there is nothing to stamp, and the tokenizer is gone
  ok('no stamping code, no nonce, no policy of the page\'s own in this page', typeof pgStamp === 'undefined' && typeof pgTag === 'undefined' && typeof pgDoc === 'undefined'
    && typeof PG_NONCE === 'undefined' && typeof PG_CSP === 'undefined');
"""

PAGES_PHONE_DRIVER = r"""
  ok('the page is a phone', REMOTE === true && MOB === true && DESK === false);
  const sets = {};
  const cl = (id) => { const st = sets[id] = sets[id] || new Set(); return { contains: (c) => st.has(c), add: (...c) => c.forEach((x) => st.add(x)),
    remove: (...c) => c.forEach((x) => st.delete(x)), toggle: (c, f) => { if (f === undefined) f = !st.has(c); if (f) st.add(c); else st.delete(c); return f; } }; };
  ['pgpanel', 'pgdock', 'pgtoast'].forEach((id) => { elCache[id] = cap(); elCache[id].classList = cl(id); });
  ['pghead', 'pgtabs', 'pgframe', 'pgclose'].forEach((id) => { elCache[id] = cap(); });
  const calls = []; global.fetch = async (url) => { calls.push(String(url)); return { ok: true, status: 200, json: async () => ({}) }; };
  global.setTimeout = (fn) => { setImmediate(fn); return 0; };
  const mk = (id, title) => ({ id: id, by: 'Claude Code', at: 'place:pages', ttl: 1800, t: 'a', expires: new Date(Date.now() + 600000).toISOString(),
    ops: [{ op: 'page', at: 'place:pages', title: title, bytes: 10 }] });
  W.shows = []; pgSync({ shows: W.shows }); W.shows = [mk('p1', 'Latency board')]; pgSync({ shows: W.shows });
  const dock = String(elCache.pgdock.innerHTML);
  ok('a phone shows the page as a card with its marker, title, author and place', sets.pgdock.has('on') && dock.includes('class="pgm">page<') && dock.includes('Latency board')
    && dock.includes('by Claude Code') && dock.includes(' · at pages · '));
  ok('...that says to open it on the desktop and has nothing to press', dock.includes('open it on the desktop') && !dock.includes('data-pg=') && !dock.includes('role="button"'));
  ok('a toast says so too, with no Open button', String(elCache.pgtoast.innerHTML).includes('made a page: Latency board') && String(elCache.pgtoast.innerHTML).includes('open it on the desktop')
    && !String(elCache.pgtoast.innerHTML).includes('data-pg='));
  await pgShow('p1'); await settle();
  ok('the panel never opens and the source is never asked for', !sets.pgpanel.has('on') && !calls.some((c) => c.indexOf('/show/page') >= 0) && PG.open === null);
  let refused = false; try { await api('/show/page?id=p1'); } catch (e) { refused = /desktop only/.test(e.message); }
  ok('the phone page refuses the route itself', refused);
"""

PAGES_STATIC_DRIVER = r"""
  ok('the page is a static copy', LIVE === false);
  const sets = {};
  ['pgpanel', 'pgdock', 'pgtoast'].forEach((id) => { elCache[id] = cap(); elCache[id].classList = { toggle: (c, f) => { sets[id + c] = f; }, add: () => {}, remove: () => {}, contains: () => false }; });
  ['pghead', 'pgtabs', 'pgframe', 'pgclose'].forEach((id) => { elCache[id] = cap(); });
  pgSync({ shows: [] });
  ok('a static page has no pages and no cards', pgList().length === 0 && sets.pgdockon === false);
  await pgShow('p1'); ok('...and opening one only says why', PG.open === null && String(flightFlash && flightFlash.text).length > 0);
"""


class PageBehaviourTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which("node"):
            raise unittest.SkipTest("node not installed")
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name, "work")
        for name in ("Alpha", "Beta"):
            (root / name).mkdir(parents=True)
            (root / name / "notes.md").write_text("fixture\n", encoding="utf-8")
        proc = render_fixture(cls.tmp.name)
        if proc.returncode:
            raise AssertionError(proc.stderr[-2000:])
        cls.js = Path(cls.tmp.name, "page.js")
        cls.src = Path(cls.tmp.name, "page.html").read_text(encoding="utf-8")
        page_js(Path(cls.tmp.name, "page.html"), cls.js)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def drive(self, body, location=None, env_extra=None):
        run_page_driver(self, self.js, body, location, env_extra)

    def test_idle_craft_and_layout_hold_position_without_new_data(self):
        self.drive(r"""
  GB=false; VIEW='map';
  const u={uid:'fixture:idle',kind:'seat',name:'quiet seat',status:'IDLE',gx:0,gy:0};
  const calls=[];
  drawSprite=(...args)=>calls.push(['sprite',...args.slice(0,4)]);
  vecCraft=(...args)=>calls.push(['vector',...args.slice(0,4)]);
  cx.translate=(...args)=>calls.push(['translate',...args]);
  const capture=(fn,t)=>{ calls.length=0; fn(t); return JSON.stringify(calls); };
  for(const vector of [false,true]){
    VEC=vector;
    ok('idle hull holds position in '+(vector?'vector':'pixel'),
      capture(t=>sprite(u,0,0,t),0)===capture(t=>sprite(u,0,0,t),1200));
  }
  VEC=false;
  ok('carrier has no separate idle bob',
    capture(t=>drawCarrier(0,0,t,false),0)===capture(t=>drawCarrier(0,0,t,false),1200));
  const layouts=()=>JSON.stringify(units.map(x=>[x.uid,x.gx,x.gy]));
  orbitTick(0); const before=layouts(); orbitTick(120000);
  ok('time alone cannot move folders or agent slots',layouts()===before);
  const p={x:12,y:34,r:80,uid:'fixture:problem',enemies:2};
  ok('problem markers stay in their slots',JSON.stringify(foePos(p,0,0))===JSON.stringify(foePos(p,0,120000)));
""")

    def test_live_page_panel_follows_retries_list_redraws_diff_orders_setup_greys(self):
        self.drive(LIVE_DRIVER, LIVE_LOCATION)

    def test_the_meta_board_opens_on_u_draws_an_answer_escaped_and_acts_only_through_existing_routes(self):
        self.drive(META_DRIVER, LIVE_LOCATION, {"HARNESS_BRIDGE": json.dumps({"live": True, "remote": False})})

    def test_the_meta_board_is_not_on_a_phone_or_a_static_copy(self):
        none = r"""
          const sets = {}; const calls = [];
          elCache.meta = cap(); elCache.meta.classList = { contains: (c) => !!sets.on && c === 'on', add: () => { sets.on = true; },
            remove: () => { sets.on = false; }, toggle: () => {} };
          global.fetch = async (url) => { calls.push(String(url)); return { ok: true, status: 200, json: async () => ({}) }; };
          const fire = (key) => (__WL.keydown || []).forEach((f) => f({ key: key, repeat: false, ctrlKey: false, metaKey: false,
            altKey: false, shiftKey: false, preventDefault() {}, stopPropagation() {}, getModifierState() { return false; },
            target: document.activeElement || { blur() {} } }));
          VIEW = 'map'; document.activeElement = null;
          fire('u'); await settle();
          ok('U does not open the meta board here', !sets.on);
          ok('...and the bridge is not asked for it', !calls.some((c) => c.startsWith('/meta')));
          ok('...it says why', String(flightFlash && flightFlash.text).length > 0);
          if (REMOTE) { let refused = false;
            try { await api('/meta'); } catch (e) { refused = /desktop only/.test(e.message); }
            ok('a phone page refuses to call the meta routes', refused && !calls.length); }
        """
        self.drive(none, LIVE_LOCATION, {"HARNESS_BRIDGE": json.dumps({"live": True, "remote": True})})
        self.drive(none)                                            # a file:// copy: static, no bridge

    def test_agent_pages_are_cards_and_a_toast_and_open_in_a_sandboxed_frame_only_on_the_desktop(self):
        self.drive(PAGES_DRIVER, LIVE_LOCATION, {"HARNESS_BRIDGE": DESK_FLAG})

    def test_a_phone_shows_a_page_as_a_card_and_never_opens_it(self):
        self.drive(PAGES_PHONE_DRIVER, PHONE_LOCATION, {"HARNESS_BRIDGE": PHONE_FLAG, "HARNESS_MOBILE": "1"})

    def test_a_static_copy_of_the_page_has_no_pages(self):
        self.drive(PAGES_STATIC_DRIVER)

    def test_page_source_gives_a_page_frame_one_sandbox_right_and_listens_to_nothing_it_says(self):
        src = self.src
        code = "\n".join(line for line in src.splitlines() if not line.lstrip().startswith("//"))
        self.assertEqual(re.findall(r'sandbox="([^"]*)"', code), ["allow-scripts"], "the only sandbox the page sets")
        self.assertEqual(re.findall(r"<iframe\b", code), ["<iframe"], "one iframe in the whole page: the agent page's")
        self.assertNotRegex(code, r"allow-(same-origin|forms|popups|top-navigation|modals|downloads)")
        for listening in ("addEventListener('message'", 'addEventListener("message"', "onmessage", "postMessage", "BroadcastChannel"):
            self.assertNotIn(listening, src, listening)
        self.assertIn('referrerpolicy="no-referrer"', src)
        self.assertEqual(code.count("/show/page/frame?id="), 1, "the one place the page names a source")
        self.assertNotRegex(code, r"/show/page\?", "the JSON route that gave the source to this page is gone")
        for gone in ("srcdoc", "pgStamp", "PG_NONCE", "PG_CSP", "pgTag", "pgRawEnd", "pgCommentEnd"):
            self.assertNotIn(gone, code, f"{gone}: no source passes through this page now, so nothing is stamped")
        self.assertNotIn("html:", re.search(r"function pgList\(\)\{.*?\n\}", src, re.S).group(0), "a card is built from the title and size, never a source")

    def test_localhost_page_is_live_and_links_back_to_its_own_origin(self):
        self.drive(LOCALHOST_DRIVER, LOCALHOST_LOCATION)

    def test_file_page_is_static(self):
        self.drive(FILE_DRIVER)
        self.drive(FILE_BANNER_DRIVER.replace("__PORT__", "7428").replace("__FLAG__", ""))

    def test_file_page_links_to_the_port_it_was_rendered_for_and_never_jumps(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "work", "Alpha").mkdir(parents=True)
            proc = render_fixture(tmp, env_extra={"BRIDGE_PORT": "7461"})
            self.assertEqual(proc.returncode, 0, msg=proc.stderr[-2000:])
            page = Path(tmp, "page.html")
            js = Path(tmp, "page.js")
            page_js(page, js)
            self.assertIn('"http://127.0.0.1:7461/"', page.read_text(encoding="utf-8"))
            old_js, self.js = self.js, js
            try:
                self.drive(FILE_BANNER_DRIVER.replace("__PORT__", "7461")
                           .replace("__FLAG__", " --port 7461"))
            finally:
                self.js = old_js
            src = js.read_text(encoding="utf-8")
        self.assertNotIn("location.href=LIVE_URL", src)
        self.assertNotIn("fetch(LIVE_URL", src)
        self.assertNotIn("7428", src, "the port in the page is the one it was rendered for")

    def test_a_bad_port_in_the_environment_falls_back_to_the_default(self):
        for bad in ("", "0", "70000", "seven", "7428; alert(1)", "-5"):
            with tempfile.TemporaryDirectory() as tmp:
                Path(tmp, "work", "Alpha").mkdir(parents=True)
                proc = render_fixture(tmp, env_extra={"BRIDGE_PORT": bad})
                self.assertEqual(proc.returncode, 0, msg=(bad, proc.stderr[-2000:]))
                html = Path(tmp, "page.html").read_text(encoding="utf-8")
                self.assertIn("const LIVE_URL = LIVE ? location.origin + '/' : "
                              '"http://127.0.0.1:7428/";', html, bad)

    def test_a_phone_page_is_live_and_remote_and_hides_the_desktop_controls(self):
        self.drive(PHONE_DRIVER, PHONE_LOCATION, env_extra={"HARNESS_BRIDGE": PHONE_FLAG})

    def test_the_desktop_star_panel_pairs_and_lists_phones(self):
        self.drive(PHONE_PANEL_DRIVER, LIVE_LOCATION, env_extra={"HARNESS_BRIDGE": DESK_FLAG})

    def test_the_bridges_flag_decides_live_not_the_host_name(self):
        self.drive(BRIDGE_DESK_DRIVER, BOX_LOCATION, env_extra={"HARNESS_BRIDGE": DESK_FLAG})
        self.drive(UNFLAGGED_DRIVER, BOX_LOCATION)

    def test_page_source_gates_the_desktop_controls_on_desk(self):
        src = self.src
        self.assertIn("window.CM_BRIDGE", src)
        for gated in ("function pollControl", "async function crossTo", "function pollSynced",
                      "async function runVerb", "async function doPermit",
                      "async function openDoor", "function openPrompter"):
            body = src.split(gated, 1)[1][:400]
            self.assertIn("DESK", body, gated)

    def test_page_source_carries_the_thread_panel_fixes(self):
        src = self.src
        self.assertIn("tvFollow(j.tasks);", src)                  # F1: list poll wakes the panel
        self.assertIn("if(setup&&isolate) body.setup=setup;", src)   # F10: setup needs a worktree
        self.assertIn("W.agent_tasks=prevTasks;", src)            # F9: compare with the old list

    def test_counters_today_battlefield_hud_and_wingmate_wait(self):
        self.drive(COUNTERS_DRIVER)

    def test_every_pack_and_the_mono_palette_draw_the_craft_set(self):
        self.drive(CRAFT_DRIVER)

    def test_effects_animate_or_hold_one_still_frame_under_reduced_motion(self):
        self.drive(EFFECTS_DRIVER, env_extra={"HARNESS_REDUCED": "0"})
        self.drive(EFFECTS_DRIVER, env_extra={"HARNESS_REDUCED": "1"})

    def test_page_source_carries_the_counter_fixes(self):
        src = self.src
        self.assertRegex(src, r"(?m)^hudWorld\(\);$")               # F14: the HUD is filled at load
        self.assertIn('id="hudgen"', src)
        self.assertIn("'No answer after '+Math.round(waitMs/1000)+' seconds", src)     # F13
        self.assertNotIn("No answer after 90 seconds", src)

    def test_remote_deck_is_measured_against_its_own_world(self):
        self.drive(REMOTE_DRIVER)

    def test_console_comms_rail_and_strip(self):
        self.drive(CONSOLE_DRIVER)

    def test_plates_one_layout_for_every_label(self):
        self.drive(PLATES_DRIVER)

    def test_page_source_has_one_plate_path(self):
        src = self.src
        for gone in ("function unitPlate", "function bodyPlate", "LABEL_BOXES", "EXT_BOXES"):
            self.assertNotIn(gone, src, gone)
        for kept in ("function queuePlate(", "function layoutPlates(", "function foldDock(", "function fitLabel(",
                     "window.__plateAudit=", "const PLATE_MEMORY=new Map()"):
            self.assertIn(kept, src, kept)

    def test_page_source_lays_remote_decks_out_from_their_synced_world(self):
        self.assertIn("syncedScope(synced)", self.src)
        self.assertNotIn("(W.trend&&W.trend[b.a.name])", self.src)

    def test_a_phone_routes_tabs_strips_hints_toasts_and_aims_the_camera_over_the_sheet(self):
        self.drive(MOBILE_DRIVER, env_extra={"HARNESS_MOBILE": "1"})

    def test_a_desktop_does_not_know_the_phone_exists(self):
        self.drive(DESKTOP_DRIVER)

    def test_fingers_pan_tap_hold_and_pinch_and_the_canvas_costs_less_on_a_phone(self):
        self.drive(GESTURE_DRIVER, env_extra={"HARNESS_MOBILE": "1"})


class ChromeCase(unittest.TestCase):
    """A real headless Chrome with one rendered fixture page (the folders of FOLDERS under its workspace root) and a
    runner that loads it at a size, runs a script body in it and prints the result: where the page's boxes are real."""
    FOLDERS = ("Alpha", "Beta")

    @classmethod
    def setUpClass(cls):
        cls.chrome = find_chrome()
        if not (cls.chrome and shutil.which("node")):
            raise unittest.SkipTest("needs node and a Chrome or Chromium binary (set CHROME_BIN)")
        cls.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        for name in cls.FOLDERS:
            (Path(cls.tmp.name, "work", name)).mkdir(parents=True)
            Path(cls.tmp.name, "work", name, "notes.md").write_text("fixture\n", encoding="utf-8")
        proc = render_fixture(cls.tmp.name)
        if proc.returncode:
            raise AssertionError(proc.stderr[-2000:])
        cls.url = Path(cls.tmp.name, "page.html").as_uri()
        cls.runner = Path(cls.tmp.name, "runner.mjs")
        cls.runner.write_text(BROWSER_RUNNER, encoding="utf-8")
        profile = Path(cls.tmp.name, "profile")
        cls.browser = subprocess.Popen(
            [cls.chrome, "--headless=new", "--no-sandbox", "--disable-gpu", "--remote-debugging-port=0",
             f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check", "--remote-allow-origins=*",
             "--allow-file-access-from-files", "--disable-background-networking", "--disable-component-update", "--no-pings",
             "--host-resolver-rules=MAP * ~NOTFOUND", "--proxy-server=http://127.0.0.1:9", "--proxy-bypass-list=<-loopback>",
             "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        port_file = profile / "DevToolsActivePort"
        deadline = time.time() + 30
        while time.time() < deadline:
            if cls.browser.poll() is not None:
                raise AssertionError(f"{cls.chrome} exited with {cls.browser.returncode} before it opened a debugging port")
            if port_file.exists() and port_file.read_text(encoding="utf-8").strip():
                break
            time.sleep(0.1)
        else:
            raise AssertionError("Chrome opened no debugging port in 30 s")
        cls.port = port_file.read_text(encoding="utf-8").splitlines()[0]

    @classmethod
    def tearDownClass(cls):
        browser = getattr(cls, "browser", None)
        if browser is not None:
            browser.terminate()
            try:
                browser.wait(10)
            except subprocess.TimeoutExpired:
                browser.kill()
                browser.wait()
        tmp = getattr(cls, "tmp", None)
        if tmp is not None:
            tmp.cleanup()

    def run_body(self, width, height, mobile, body, name):
        scenario = Path(self.tmp.name, name + ".js")
        scenario.write_text(body, encoding="utf-8")
        run = subprocess.run(["node", str(self.runner), self.port, self.url, str(width), str(height),
                              "1" if mobile else "0", str(scenario)], capture_output=True, text=True, timeout=120)
        line = next((ln for ln in run.stdout.splitlines() if ln.startswith("RESULT ")), None)
        self.assertIsNotNone(line, (run.stdout + run.stderr)[-3000:])
        result = json.loads(line[len("RESULT "):])
        self.assertIsNone(result["exception"], result)
        self.assertEqual(result["errors"], [], "the page threw")
        self.assertGreaterEqual(result["blocked"], 0)
        return result["value"]


class PlateBrowserTests(ChromeCase):
    """A busy dock (seven seats and six threads at one planet) in a real headless Chrome, at desktop and phone
    sizes, where the page's own boxes are real: no two plates overlap, none leaves the view, none lies under a box
    or on a craft; the dock folds into one count, unfolds under the pointer and folds again."""

    def run_scenario(self, width, height, mobile, **cfg):
        return self.run_body(width, height, mobile, BROWSER_SCENARIO.replace("__CFG__", json.dumps(cfg)), f"scenario-{width}x{height}")

    def check(self, value, width, height):
        self.assertFalse(value["live"], "the page stayed a static file: the live bridge is never called")
        self.assertTrue(value["href"].startswith("file:"), value["href"])
        folded, pointed, gone = value["steps"]
        for step in value["steps"]:
            with self.subTest(size=f"{width}x{height}", step=step["name"]):
                self.assertEqual(step["overlap"], [], "plates overlap")
                self.assertEqual(step["out"], [], "plates leave the view")
                self.assertEqual(step["onbox"], [], "plates lie under the page's own boxes")
                self.assertEqual(step["onhull"], [], "plates lie on a craft")
        self.assertEqual(folded["folds"], 1, folded)
        self.assertEqual(folded["fold"], "7 ships · 6 tasks · 5 need you")
        self.assertEqual(folded["docked"], 0, "a folded dock shows no plate of its own")
        self.assertIn("tag", folded["kinds"], "the planet keeps its tag")
        self.assertEqual(pointed["folds"], 0, "pointing at the planet unfolds its dock")
        self.assertGreaterEqual(pointed["docked"], 1)
        self.assertEqual(gone["folds"], 1, "and it folds again once the pointer is gone")

    def test_desktop_busy_dock_with_the_page_boxes_over_the_map(self):
        value = self.run_scenario(1280, 800, False, quiet=False, hideMate=False, rails=False)
        self.check(value, 1280, 800)
        boxes = value["steps"][0]["overlays"]
        # what sits over a desktop's map is its legend chip; the HUD, comms, ticker and the wingmate card are retired
        # (the assistant is the status bar's line, outside the stage, so no box)
        self.assertIn("legendchip", boxes, "the layout read the page's legend chip")
        for box in ("hud", "comms", "ticker", "mate"):
            self.assertNotIn(box, boxes)
        self.assertGreaterEqual(value["steps"][1]["docked"], 4, "the unfolded dock shows its plates")

    def test_desktop_with_the_dashboard_rail_open(self):
        value = self.run_scenario(1280, 800, False, quiet=False, hideMate=False, rails=True)
        self.assertLess(value["steps"][0]["vw"], 1280, "the rail takes its share of the width")
        self.check(value, value["steps"][0]["vw"], 800)

    def test_phone_busy_dock(self):
        value = self.run_scenario(390, 844, True, quiet=True, hideMate=True, rails=False)
        self.check(value, 390, 844)

    def test_phone_with_every_box_showing_still_never_overprints(self):
        value = self.run_scenario(390, 844, True, quiet=False, hideMate=False, rails=False)
        self.assertFalse(value["live"])
        for step in value["steps"]:
            with self.subTest(step=step["name"]):
                self.assertEqual((step["overlap"], step["out"], step["onbox"], step["onhull"]), ([], [], [], []))


class TodayWingmateSourceTests(unittest.TestCase):
    """The wingmate card is a map card: Today covers the whole stage and the card sat over its folder list."""

    def test_one_rule_for_every_size_takes_the_card_and_its_nav_button_off_today(self):
        rules = css_rules(source_css())
        found = [(sel, body) for sel, body in rules if "#today.on~#mate" in sel]
        self.assertEqual(found, [("#today.on~#mate,#today.on~#mateshow", "display:none")])
        self.assertNotRegex(RENDER_SRC, r"html\.t #today\.on~#mate", "no longer a tablet rule")
        # and it comes after the card's own rule
        base = [i for i, (sel, _) in enumerate(rules) if sel == "#mate"]
        self.assertTrue(base and rules.index(found[0]) > base[0])


HOME_SCENARIO = r"""
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const first = { view: VIEW, zoom: ZOOM, stored: ZOOM_STORED, home: Object.assign({}, HOME), touch: touchUI(), mob: MOB,
  mate: getComputedStyle(document.getElementById('mate')).display };
const out = { first: first };
// Today first, as a configured page lands: the wingmate card is not over its folder list
setView('today'); await wait(150);
out.today = { mate: getComputedStyle(document.getElementById('mate')).display, home: document.getElementById('mate').parentNode.id,
  mateshow: getComputedStyle(document.getElementById('mateshow')).display, on: document.getElementById('today').classList.contains('on') };
// the map: the wingmate is back and the home view is made with the cards in place
setView('map'); await wait(400);
out.map = { mate: getComputedStyle(document.getElementById('mate')).display, audit: __fitAudit(), cls: document.documentElement.className, home: document.getElementById('mate').parentNode.id };
// the card hidden: its NAV button is off Today too, and back on the map
mateShow(false); setView('today'); await wait(100);
out.hidden = { mate: getComputedStyle(document.getElementById('mate')).display, show: getComputedStyle(document.getElementById('mateshow')).display };
setView('map'); await wait(100);
out.hiddenBack = { mate: getComputedStyle(document.getElementById('mate')).display, show: getComputedStyle(document.getElementById('mateshow')).display };
mateShow(true);
return out;
"""


class HomeViewBrowserTests(ChromeCase):
    """The first load of a desktop or a tablet in a real headless Chrome (a twelve-planet ring, a fresh tab): no planet
    disc lies under the HUD, the comms box, the wingmate card or the zoom column once the map is up, and Today is
    never covered by the wingmate card, at any size, which is back when Today closes."""
    FOLDERS = ("Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta", "Eta", "Theta", "Iota", "Kappa", "Lambda", "Mu")

    def run_home(self, width, height, mobile):
        return self.run_body(width, height, mobile, HOME_SCENARIO, f"home-{width}x{height}")

    def sizes(self):
        # 1600x900 and 1366x768 desktops (console rail open), a 1000 px window (no rail), a 768x1024 touch tablet
        return ((1600, 900, False), (1366, 768, False), (1000, 700, False), (768, 1024, True))

    def test_no_planet_disc_lies_under_a_card_at_the_home_view(self):
        for width, height, mobile in self.sizes():
            with self.subTest(size=f"{width}x{height}"):
                v = self.run_home(width, height, mobile)
                audit = v["map"]["audit"]
                self.assertTrue(v["first"]["home"]["on"], "a fresh tab starts on the home view")
                self.assertFalse(v["first"]["stored"])
                self.assertEqual(audit["home"]["laid"], True)
                self.assertEqual(len(audit["planets"]), 12)
                ids = {c["id"] for c in audit["covers"]}
                # a tablet keeps the old cards over its map; a desktop has only its legend chip there (:
                # the HUD and comms are retired, the assistant is a line in the status bar under the map)
                # a touch tablet takes the desktop frame too: its fly pad and zoom column are what sits over its map
                for card in (("touchpad",) if mobile else ("legendchip",)):
                    self.assertIn(card, ids, "the view read the page's " + card)
                self.assertFalse(ids & {"hud", "comms", "ticker", "mate"}, ids)
                self.assertEqual(audit["under"], [], f"planets under a card at {width}x{height}: {audit['covers']}")
                self.assertEqual(audit["outside"], [], "planets off the stage")
                self.assertGreaterEqual(audit["zoom"], 0.2)
                self.assertLessEqual(audit["zoom"], 1)
                if not mobile and width >= 1280:
                    self.assertEqual(audit["vw"], width - 400, "the rail takes its 400 px beside the map")

    def test_the_wingmate_card_never_covers_today_and_comes_back_with_the_map(self):
        for width, height, mobile in self.sizes():
            with self.subTest(size=f"{width}x{height}"):
                v = self.run_home(width, height, mobile)
                self.assertTrue(v["today"]["on"])
                # the assistant is the status bar's one line (a desktop, a tablet), under Today as under the map, never over
                # either; a line does not hide, so there is no NAV button to bring it back
                self.assertEqual((v["today"]["home"], v["map"]["home"]), ("sb-ask", "sb-ask"))
                self.assertNotEqual(v["today"]["mate"], "none")
                self.assertNotEqual(v["map"]["mate"], "none")
                self.assertEqual(v["hidden"]["show"], "none", "no NAV button for a line that does not hide")
                self.assertEqual(v["hiddenBack"]["show"], "none")


class WorldBuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        Path(self.tmp.name, "work", "Alpha").mkdir(parents=True)

    def world(self, **extra):
        proc = render_fixture(self.tmp.name, "--json", **extra)
        self.assertEqual(proc.returncode, 0, msg=proc.stderr[-2000:])
        return json.loads(proc.stdout)

    def write(self, name, text):
        path = Path(self.tmp.name, name)
        path.write_text(text, encoding="utf-8")
        return str(path)

    def test_oldest_is_the_oldest_open_line_not_the_first_of_the_top_group(self):
        old = (date.today() - timedelta(days=120)).isoformat()
        new = (date.today() - timedelta(days=3)).isoformat()
        digest = self.write("DIGEST.md", f"- {new} job offer reply awaiting decision\n"
                                         f"- {old} plain thing awaiting decision\n")
        world = self.world(digest=digest, quest_priority=[{"label": "JOB", "match": "job|offer"}])
        self.assertEqual([(q["days"], q["focus"]) for q in world["quests"]],
                         [(3, "JOB"), (120, "")])
        self.assertEqual(world["oldest"], 120)
        # no quest_priority (the default): the board runs by age alone and carries no label
        world = self.world(digest=digest)
        self.assertEqual([(q["days"], q["focus"]) for q in world["quests"]], [(120, ""), (3, "")])

    def test_a_runner_receipt_stamped_the_way_agents_write_it_still_gets_its_duration(self):
        # datetime.fromisoformat on Python 3.10 refuses +0000 and a 5-digit fraction (and Z), so
        # the duration of a finished run went blank there; the portable parser reads all of them
        rows = [{"rid": "r1", "item": "tidy", "runner": "shell",
                 "at": "2026-10-09T10:00:00.12345+0000"},
                {"rid": "r1", "done": True, "rc": 0, "at": "2026-10-09T10:05:30Z"},
                {"rid": "r2", "item": "tidy", "runner": "shell", "at": "2026-10-09T11:00:00+00:00"},
                {"rid": "r2", "done": True, "rc": 1, "at": "2026-10-09T11:02:00.5Z"}]
        runs = self.write("runs.jsonl", "".join(json.dumps(r) + "\n" for r in rows))
        world = self.world(task_runs_json=runs)
        got = {r["rid"]: (r["status"], r["min"]) for r in world["runs"]["tidy"]}
        self.assertEqual(got, {"r1": ("DONE", "5.5"), "r2": ("FAILED rc=1", "2.0")})

    def test_undated_lines_are_never_the_oldest(self):
        digest = self.write("DIGEST.md", "- job thing one awaiting decision\n"
                                         "- job thing two awaiting decision\n")
        world = self.world(digest=digest)
        self.assertEqual([q["days"] for q in world["quests"]], [-1, -1])
        self.assertEqual(world["oldest"], 0)

    def test_one_impossible_date_does_not_kill_the_render(self):
        digest = self.write("DIGEST.md", "- blocked on his go 2026-13-45 awaiting decision\n")
        fleet = self.write("FLEET.md", "- another call 2026-02-31 awaiting decision\n")
        world = self.world(digest=digest, fleet=fleet)
        self.assertEqual(sorted(q["days"] for q in world["quests"]), [-1, -1])
        self.assertEqual(world["oldest"], 0)

    def test_a_failed_thread_counts_for_the_deepest_planet_on_a_path_boundary(self):
        work = Path(self.tmp.name, "work")
        planets = []
        for name, rel in (("Docs", "docs"), ("Areas", "docs/areas"),
                          ("api", "api"), ("api-old", "api-old")):
            (work / rel / "sub").mkdir(parents=True)
            (work / rel / "notes.md").write_text("x\n", encoding="utf-8")
            planets.append({"name": name, "path": str(work / rel), "kind": "code"})
        tasks = Path(self.tmp.name, "tasks")
        for n, rel in enumerate(("docs/areas", "docs/areas/sub", "api-old"), 1):
            tid = f"20261009-01000{n}-t-00000{n}"
            (tasks / tid).mkdir(parents=True)
            target = str(work / rel)
            (tasks / tid / "task.json").write_text(json.dumps({
                "id": tid, "title": "t" + str(n), "text": "x", "runner": "claude",
                "target": target, "cwd": target, "mode": "inplace"}), encoding="utf-8")
            (tasks / tid / "events.jsonl").write_text(
                json.dumps({"ev": "run", "n": 1, "at": "2026-10-09T01:00:00"}) + "\n"
                + json.dumps({"ev": "exit", "n": 1, "rc": 1, "at": "2026-10-09T01:01:00"}) + "\n",
                encoding="utf-8")
        self.world(planets=planets)
        lines = Path(self.tmp.name, "history.jsonl").read_text(encoding="utf-8").splitlines()
        failed = {name: vals[4] for name, vals in json.loads(lines[-1])["a"].items()}
        # Areas owns both of its threads; its parent Docs none; api is no prefix of api-old
        self.assertEqual(failed, {"Docs": 0, "Areas": 2, "api": 0, "api-old": 1})

    def test_render_tells_the_trend_each_planets_kind(self):
        src = (ROOT / "render-game.py").read_text(encoding="utf-8")
        self.assertIn("_history.trend(CFG[\"history_jsonl\"], NOW, kinds=_KINDS)", src)
        self.assertIn("_KINDS = {a[\"name\"]: a.get(\"area_kind\")", src)

    def test_render_keeps_thread_pids_out_of_the_process_rows(self):
        src = (ROOT / "render-game.py").read_text(encoding="utf-8")
        self.assertIn("_ships.build(CFG, NOW, [], dropped=_THREAD_PIDS, threads=THREAD_SHIPS)", src)
        self.assertIn("| _THREAD_PIDS", src)

class ConfigSurface(unittest.TestCase):
    """container.json as documented: the defaults, the two example files and docs/CONFIG.md agree."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ROOT))
        import config
        cls.config = config
        cls.defaults = {k: v for k, v in config.DEFAULTS.items() if not k.startswith("_")}
        cls.docs = (ROOT / "docs" / "CONFIG.md").read_text(encoding="utf-8")
        cls.examples = {name: json.loads((ROOT / name).read_text(encoding="utf-8"))
                        for name in ("container.example.json", "container.sources-example.json")}

    def bullets(self):
        """{key: the bullet's line} for the single-key entries of docs/CONFIG.md, and the set of every key named."""
        single, named = {}, set()
        for line in self.docs.splitlines():
            if line.startswith("- `"):
                keys = re.findall(r"`([a-z_0-9]+)`", line.split(" — ", 1)[0])
                named.update(keys)
                if len(keys) == 1:
                    single[keys[0]] = line
        return single, named

    def test_every_key_the_code_reads_is_documented_and_nothing_else(self):
        _, named = self.bullets()
        self.assertEqual(sorted(set(self.defaults) - named), [], "keys with no entry in docs/CONFIG.md")
        self.assertEqual(sorted(named - set(self.defaults)), [], "entries for keys the loader does not read")

    def test_a_documented_default_is_the_real_default(self):
        single, _ = self.bullets()
        for key, line in single.items():
            found = re.search(r"default `([^`]*)`", line)
            if found:
                self.assertEqual(json.loads(found.group(1)), self.defaults[key], key)

    def test_the_examples_carry_only_real_keys_in_the_type_the_loader_keeps(self):
        _, named = self.bullets()
        for name, example in self.examples.items():
            for key, value in example.items():
                if key.startswith("_"):
                    continue
                self.assertIn(key, self.defaults, f"{name}: {key} is not a key")
                self.assertIn(key, named, f"{name}: {key} is not in docs/CONFIG.md")
                self.assertEqual(type(value), type(self.defaults[key]),
                                 f"{name}: {key} would be ignored by the loader (wrong JSON type)")
        self.assertNotIn("Not in the example file", self.docs)

    def test_the_sources_example_does_not_narrow_what_it_replaces(self):
        sources = self.examples["container.sources-example.json"]
        self.assertEqual(sources["decision_markers"], self.defaults["decision_markers"])   # a list replaces the default
        self.assertNotIn("hub_channel", sources)          # only rounds.py reads it, and that script is not shipped
        self.assertEqual(self.config.load(str(ROOT / "container.sources-example.json"))["decision_markers"],
                         self.defaults["decision_markers"])

    def test_rounds_json_defaults_to_the_file_next_to_the_tool_except_for_another_root_or_the_demo(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            def load(raw, root=""):
                path = os.path.join(tmp, "c.json")
                Path(path).write_text(json.dumps(raw), encoding="utf-8")
                with mock.patch.object(self.config, "root_arg", return_value=root):
                    return self.config.load(path)["rounds_json"]

            beside = os.path.join(self.config.HERE, "rounds.json")
            self.assertEqual(load({}), beside)                          # what rounds.py writes, what the page reads
            self.assertEqual(load({"rounds_json": "~/r.json"}), os.path.expanduser("~/r.json"))
            self.assertEqual(load({"demo": True}), "")
            self.assertEqual(load({}, root=tmp), "")                    # a map of another root never inherits it

    def test_the_page_shows_the_rounds_file_next_to_the_tool_when_none_is_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            tool = Path(tmp, "tool")
            tool.mkdir()
            for source in ROOT.glob("*.py"):
                if not source.name.startswith("test_"):
                    shutil.copy(source, tool / source.name)
            work = Path(tmp, "work")
            (work / "alpha").mkdir(parents=True)
            Path(tmp, "crontab.txt").write_text("", encoding="utf-8")
            cfg = Path(tmp, "container.json")
            cfg.write_text(json.dumps({"work_root": str(work), "tmux_session": "none", "processes": False,
                                       "decision_scan": False, "cron_syslog_file": "", "detect_runners": False,
                                       "crontab_file": str(Path(tmp, "crontab.txt")), "tick_seconds": 0}),
                           encoding="utf-8")
            (tool / "rounds.json").write_text(json.dumps({"round": 7, "step": "checking", "checks": 3}), encoding="utf-8")
            env = dict(os.environ, CONTAINER_CONFIG=str(cfg), CONTAINER_OUT=str(Path(tmp, "out.html")))
            env.pop("CONTAINER_ROOT", None)
            done = subprocess.run([sys.executable, str(tool / "render-game.py"), "--json"], cwd=tmp, env=env,
                                  capture_output=True, text=True, timeout=300)
            self.assertEqual(done.returncode, 0, done.stderr[-2000:])
            rounds = json.loads(done.stdout)["rounds"]
            self.assertIsNotNone(rounds, "rounds.json beside the tool was not read")
            self.assertEqual((rounds["round"], rounds["step"], rounds["checks"]), (7, "checking", 3))


# ---------------------------------------------------------------- design round one: type, tokens, motion

SIZE_DECL = re.compile(r"(?<![\w-])font(?:-size)?\s*:\s*([^;}{\"'\n]*)")
CANVAS_FONT = re.compile(r"\.font\s*=\s*['\"]([^'\"]*)['\"]")
PX = re.compile(r"(\d+(?:\.\d+)?)px")


class DesignTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        (Path(cls.tmp.name, "work", "Alpha")).mkdir(parents=True)
        proc = render_fixture(cls.tmp.name)
        if proc.returncode:
            raise AssertionError(proc.stderr[-2000:])
        cls.html = Path(cls.tmp.name, "page.html").read_text(encoding="utf-8")
        cls.css = "".join(re.findall(r"<style[^>]*>(.*?)</style>", cls.html, re.S))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_page_carries_the_display_face_inline_with_its_licence(self):
        font = ROOT / "fonts" / "Silkscreen-Regular.ttf"
        self.assertTrue(font.is_file() and (ROOT / "fonts" / "OFL.txt").is_file())
        self.assertIn("SIL Open Font License", (ROOT / "fonts" / "OFL.txt").read_text(encoding="utf-8"))
        m = re.search(r'@font-face\{font-family:"Silkscreen";[^}]*src:url\(data:font/ttf;base64,([A-Za-z0-9+/=]+)\)', self.html)
        self.assertIsNotNone(m, "the display face is inlined as a data URI")
        import base64
        self.assertEqual(base64.b64decode(m.group(1)), font.read_bytes())
        self.assertNotRegex(self.html, r"@font-face[^}]*url\((?!data:)", "no font is fetched at runtime")

    def test_page_carries_the_type_scale_variables(self):
        for decl in ("--font-display:", "--font-mono:", "--fs-eyebrow:11px", "--fs-body:13px", "--fs-list:14px",
                     "--fs-title:16px", "--fs-hud:20px", "--fs-big:28px"):
            self.assertIn(decl, self.css)
        self.assertIn('"Silkscreen"', re.search(r"--font-display:([^;]*);", self.css).group(1))
        self.assertIn("var(--font-display)", self.css)

    def test_no_text_under_12px_anywhere_in_the_page(self):
        small = [(v.strip()[:60]) for v in SIZE_DECL.findall(self.html)
                 for px in PX.findall(v) if float(px) < 12]
        self.assertEqual(small, [], "font sizes under 12 px (CSS, inline styles, script-built styles)")
        literal = re.findall(r"(?<![\w-])font:\s*(?:(?:bold|normal|italic|\d00)\s+)*\d+(?:\.\d+)?px", self.css)
        self.assertEqual(literal, [], "font: shorthands take their size from the scale variables")
        canvas = [f for f in CANVAS_FONT.findall(self.html) if any(float(px) < 12 for px in PX.findall(f))]
        self.assertEqual(canvas, [], "canvas fonts under 12 px")
        self.assertIn("const FONT_MIN=12;", self.html)          # cfont()/wfont() never go below it

    def test_every_pack_has_a_plate_and_a_focus_ring(self):
        packs = tp.load_dir(str(ROOT / "themes"))
        self.assertEqual(list(packs), SHIPPED_PACKS)
        for name, pack in packs.items():
            with self.subTest(pack=name):
                self.assertRegex(pack["ui"]["plate"], HEX_COLOR)
                self.assertRegex(pack["ui"]["focus"], HEX_COLOR)
                self.assertGreaterEqual(tp.contrast(pack["ui"]["muted"], pack["ui"]["panel"]), 4.5)
                self.assertEqual(tp.quality(pack), [])
                if name != "classic":
                    rule = re.search(r'html\[data-theme="' + name + r'"\]\{([^}]*)\}', self.css).group(1)
                    self.assertIn("--plate:" + pack["ui"]["plate"], rule)
                    self.assertIn("--focus:" + pack["ui"]["focus"], rule)
        broken = json.loads((ROOT / "themes" / "classic.json").read_text(encoding="utf-8"))
        del broken["ui"]["focus"]
        self.assertTrue(tp.validate(broken, "classic"), "a pack without a focus colour is refused")
        self.assertIn("outline:2px solid var(--focus)", self.css)

    def test_reduced_motion_stops_bob_blink_and_the_ticker(self):
        self.assertRegex(self.css, r"@media \(prefers-reduced-motion:\s*reduce\)\{\*,\*::before,\*::after\{animation:none!important")
        self.assertIn("if(REDUCED&&tickI>=0) return;", self.html)                      # the receipts line stays put
        # the wingmate blinks only while it talks or thinks (no idle motion), never under reduced motion
        self.assertIn("const blink=!!(MATE.talking||MATE.thinking)&&!REDUCED&&", self.html)
        self.assertIn("(talk||think)&&!GB&&!REDUCED", self.html)

    def test_the_card_shows_problems_in_full_with_two_actions(self):
        self.assertIn("full:String(it.finding||it.id||'')", self.html)
        self.assertIn("CM.plainText(f.full||f.name,120)", self.html)        # in full, in plain words
        self.assertIn('data-cact="thread">Send an agent</button>', self.html)
        self.assertNotIn('data-cact="open">Open folder</button>', self.html)


def css_rules(css):
    """(selector, body) pairs of flat rules, comments gone."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return [(m.group(1).strip(), m.group(2)) for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css)]


def split_selector(sel):
    """A selector list split at its top-level commas (a comma inside :is( ) stays)."""
    out, depth, cur = [], 0, ""
    for ch in sel:
        depth += (ch == "(") - (ch == ")")
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    return out + [cur.strip()]


MOBILE_MARK = "/* ==== MOBILE."
NEW_IDS = ["tabs", "tab-today", "tab-map", "tab-threads", "tab-wingmate", "tabn-today", "tabn-threads", "tabn-wingmate",
           "mfab", "mzoom", "mz-in", "mz-out", "mz-c", "mmore", "msheet", "msheet-title", "msheet-body", "msheet-x",
           "scrim", "mclose", "mtoast", "pickbar", "pgrip", "sback", "cadv", "cpick"]


class MobileTests(unittest.TestCase):
    """The phone and tablet layer: markup, the stylesheet block, the early script. The behaviour half
    (tab rule, hints, toasts, the sheet camera) runs in PageBehaviourTests against the stub DOM."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        (Path(cls.tmp.name, "work", "Alpha")).mkdir(parents=True)
        proc = render_fixture(cls.tmp.name)
        if proc.returncode:
            raise AssertionError(proc.stderr[-2000:])
        cls.html = Path(cls.tmp.name, "page.html").read_text(encoding="utf-8")
        cls.css = "".join(re.findall(r"<style[^>]*>(.*?)</style>", cls.html, re.S))
        src = source_css()
        cls.block = src[src.index(MOBILE_MARK):]            # literals as written, before the pack variables
        cls.rules = css_rules(cls.block)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_every_new_id_is_in_the_page_once(self):
        ids = re.findall(r'\bid="([^"]+)"', self.html)
        for name in NEW_IDS:
            self.assertEqual(ids.count(name), 1, name)

    def test_the_new_pieces_sit_where_the_spec_puts_them(self):
        html = self.html
        self.assertRegex(html, r'<aside id="panel"[^>]*><button[^>]*id="pgrip"')             # the grip is the panel's first child
        self.assertRegex(html, r'<div class="shead"><button[^>]*id="sback"')                  # the way back is in the rail's head
        self.assertRegex(html, r'id="cfolder"[^>]*><button[^>]*id="cpick"')                   # the map picker sits beside the folder
        self.assertRegex(html, r'<button[^>]*id="cadv"[^>]*>[^<]*</button><div class="crow r2">')        # the toggle comes before the row, which stays where a desktop has it
        self.assertRegex(html, r'<div id="hud">.*?<button[^>]*id="mmore"[^>]*>[^<]*</button></div>')
        self.assertRegex(html, r'<div id="tabs"|<nav id="tabs"')
        for tab in ("today", "map", "threads", "wingmate"):
            self.assertRegex(html, r'<button[^>]*id="tab-' + tab + r'"[^>]*aria-selected="false"')
        self.assertEqual(re.search(r'<span class="tl" id="tabl-wingmate">([^<]*)</span>', html).group(1), "",
                         "the wingmate's tab takes its label from the configured name at load, no name is written here")

    def test_inputs_carry_the_phone_keyboard_hints(self):
        for field in ("cfolder", "csetup", "cverify"):
            self.assertRegex(self.html, r'<input[^>]*id="' + field + r'"[^>]*autocapitalize="off"')
        self.assertRegex(self.html, r'<input[^>]*id="mateq"[^>]*enterkeyhint="send"')

    def test_the_viewport_covers_the_notch_and_the_early_script_comes_second(self):
        self.assertIn('<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">', self.html)
        theme, early, style = (self.html.index(x) for x in ('<script id="theme-early">', '<script id="mobile-early">', "<style>"))
        self.assertLess(theme, early)
        self.assertLess(early, style)
        # the bridge puts its nonce on the first bare <script>: a new head script must carry an id, or the page script loses it
        self.assertGreater(self.html.index("<script>"), early)
        self.assertEqual(self.html.count("<script>"), 1)
        self.assertIn("const MOB_Q='(max-width:700px), (pointer:coarse) and (max-height:500px)';", self.html)
        self.assertIn('matchMedia(s).matches', self.html)
        self.assertIn('(max-width:700px), (pointer:coarse) and (max-height:500px)', re.search(r'<script id="mobile-early">.*?</script>', self.html).group(0))

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_the_early_script_sets_m_land_and_t(self):
        script = re.search(r'<script id="mobile-early">(.*?)</script>', self.html).group(1)
        js = ("const SCRIPT=" + json.dumps(script) + ";"
              "const run=(w,h,full,coarse)=>{ const cls=new Set(); global.innerWidth=w; global.innerHeight=h;"
              " global.matchMedia=(s)=>({matches: s.includes('max-width') ? full : coarse});"
              " global.document={documentElement:{classList:{toggle:(c,f)=>{ if(f) cls.add(c); else cls.delete(c); }}}};"
              " global.window=global; eval(SCRIPT); return [...cls].sort().join(' '); };"
              "console.log(JSON.stringify([run(390,844,true,true), run(844,390,true,true), run(768,1024,false,true), run(1600,900,false,false)]));")
        res = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30)
        self.assertEqual(res.returncode, 0, res.stderr[-1500:])
        self.assertEqual(json.loads(res.stdout), ["m", "land m", "t", ""])

    def test_every_phone_rule_is_prefixed_and_the_new_ids_wait_hidden(self):
        plain = []
        for sel, body in self.rules:
            for part in split_selector(sel):
                if not (part.startswith("html.m") or part.startswith("html.t")):
                    plain.append((part, body))
        self.assertTrue(plain)
        for part, body in plain:
            self.assertIn(body, ("display:none", "display:contents"), "an unprefixed rule in the block only hides a new id: " + part)
        hidden = {p for p, b in plain if b == "display:none"}
        for name in ("#tabs", "#mfab", "#mzoom", "#msheet", "#scrim", "#mclose", "#mtoast", "#pickbar", "#pgrip", "#sback", "#cpick", "#cadv", "#hud #mmore"):
            self.assertIn(name, hidden)
        self.assertEqual([b for p, b in plain if b not in ("display:none",)], [], "nothing unprefixed but hiding")
        # every new id waits hidden under its own rule on a desktop (not only through its parent), except the badges, which carry the hidden attribute
        for name in NEW_IDS:
            if name.startswith("tabn-"):
                self.assertRegex(self.html, r'<i id="' + name + r'"[^>]*\bhidden\b')
            else:
                self.assertTrue(any(p == "#" + name or p.endswith(" #" + name) for p in hidden), name + " has no display:none of its own")

    def test_important_marks_only_what_must_hide_or_win(self):
        for sel, body in self.rules:
            for decl in body.split(";"):
                if "!important" in decl:
                    ok = decl.strip() == "display:none!important" or (decl.strip() == "display:flex!important" and "wingmate" in sel)
                    self.assertTrue(ok, sel + " { " + decl)

    def test_the_block_uses_the_palette_and_no_stray_colour_forms(self):
        block = self.block
        for hexcol in ("#0b1428", "#344c6b", "#c8dcf5", "#9bacc0", "#ffd166"):
            self.assertIn(hexcol, block)
        self.assertNotIn("rgba(", block)
        self.assertNotRegex(block, r"(?<![\w-])white(?![\w-])")
        self.assertNotRegex(block, r"#(?:fff|ffffff)\b")
        for comment in re.findall(r"/\*(.*?)\*/", block, re.S):
            self.assertNotRegex(comment, r"[{}]", "a brace in a comment would end a rule for the pack rewriter")
            self.assertNotIn("rgba", comment)
            self.assertNotIn("white", comment)
        self.assertNotIn("body.gb", re.search(r"/\*.*?\*/", block, re.S).group(0))
        for sel, body in self.rules:
            for lit in re.findall(r"(?<![\w-])font:\s*(?:[a-z0-9]+\s+)*(\d+(?:\.\d+)?)px", body):
                self.fail("font shorthand with a literal size in " + sel)

    def test_the_phone_scale_and_tokens(self):
        rule = next(b for s, b in self.rules if s == "html.m")
        for decl in ("--T:env(safe-area-inset-top)", "--B:calc(60px + max(18px,env(safe-area-inset-bottom)))", "--row:44px",
                     "--fs-eyebrow:12px", "--fs-small:13px", "--fs-body:15px", "--fs-list:15px", "overscroll-behavior:none", "touch-action:manipulation"):
            self.assertIn(decl, rule)
        self.assertIn("--fs-eyebrow:11px", self.css)                     # the desktop :root keeps its 11 px eyebrow
        fields = next(b for s, b in self.rules if s.startswith("html.m :is(input,textarea,select)"))
        self.assertIn("font-size:16px", fields)
        self.assertRegex(self.block, r"html\.m input,html\.m textarea,html\.m pre\.live\{[^}]*user-select:text")

    def test_the_block_is_recoloured_by_every_pack_and_the_mono_palette_keeps_its_greens(self):
        tabs = next(b for s, b in css_rules(self.css) if s == "html.m #tabs")
        self.assertIn("background:var(--panel)", tabs)
        self.assertIn("border-top:1px solid var(--border)", tabs)
        active = next(b for s, b in css_rules(self.css) if s == "html.m #tabs button[aria-selected=true]")
        self.assertIn("color:var(--c-ffd166)", active)
        for ident in ("#tabs", "#mfab", "#mzoom button", "#msheet"):
            rule = next((b for s, b in css_rules(self.css) if s == "html.m body.gb " + ident), None)
            self.assertIsNotNone(rule, "a mono-palette rule for " + ident)
            self.assertRegex(rule, r"#(?:0f380f|9bbc0f|8bac0f|306230)")
            self.assertNotIn("var(--", rule)

    def test_today_reads_in_the_phone_order(self):
        for sec in ("needs", "decisions", "tasks", "word", "plan", "agents", "done", "daily", "folders", "missing"):
            self.assertIn(f'data-sec="{sec}"', self.html)
        orders = {m.group(1): int(m.group(2)) for m in re.finditer(r"html\.m #today (?:section)?\[data-sec=(\w+)\]\{order:(\d+)\}", self.block)}
        by_order = [k for k, _ in sorted(orders.items(), key=lambda kv: kv[1])]
        # the desktop's two columns read as one in the comp's order: Need you (four rows, then
        # "+N more"), the decisions' one line, Running now (the workflows, the tasks, the agents at work, the night run),
        # then Do today; the rest folded below. The four-row fold keeps Do today on the first screen ([today-fold])
        self.assertEqual(by_order, ["needs", "decisions", "tasks", "agents", "night", "word", "done", "plan", "daily", "folders", "missing"])
        self.assertIn("html.m #today .th{order:-1;", self.block)
        self.assertIn("html.m #today .tcols,html.m #today .tcol{display:contents}", self.block)

    def test_the_card_is_a_sheet_and_the_other_screens_close_their_own_way(self):
        panel = next(b for s, b in self.rules if s == "html.m #panel")
        for decl in ("left:0", "right:0", "bottom:calc(var(--B) + var(--P))", "height:35dvh", "z-index:32", "visibility:hidden"):
            self.assertIn(decl, panel)
        self.assertNotIn("box-shadow:0", panel)                              # closed: no shadow to bleed
        self.assertIn("height:calc(100dvh - var(--T) - var(--B) - var(--P))", next(b for s, b in self.rules if s == "html.m #panel.full"))
        self.assertEqual(next(b for s, b in self.rules if s == "html.m #panel.on").count("visibility:visible"), 1)
        close = next(b for s, b in self.rules if s == "html.m #panel .close")
        self.assertIn("font-size:0", close)
        self.assertIn("content:'×'", self.block)                        # the cross is drawn by ::after
        three = next(b for s, b in self.rules if s.startswith("html.m #quests,html.m #prompter,html.m #legend"))
        self.assertIn("z-index:34", three)
        self.assertIn("visibility:hidden", next(b for s, b in self.rules if s == "html.m #quests"))
        self.assertIn("z-index:41", next(b for s, b in self.rules if s == "html.m #permits"))
        self.assertIn("bottom:var(--B)", next(b for s, b in self.rules if s == "html.m #permits"))
        self.assertIn("min-height:48px", next(b for s, b in self.rules if s == "html.m #permits button"))

    def test_the_tab_bar_goes_with_the_keyboard_and_turns_into_a_rail_sideways(self):
        self.assertIn("display:none", next(b for s, b in self.rules if s == "html.m.kb #tabs"))
        rail = next(b for s, b in self.rules if s == "html.m.land #tabs")
        for decl in ("right:0", "top:0", "bottom:0", "width:var(--railw)", "flex-direction:column"):
            self.assertIn(decl, rail)
        self.assertIn("--railw:calc(64px + var(--sar))", next(b for s, b in self.rules if s == "html.m.land"))
        drawer = next(b for s, b in self.rules if s == "html.m.land #panel")
        for decl in ("right:var(--railw)", "bottom:var(--P)", "width:min(420px,52vw)"):
            self.assertIn(decl, drawer)
        self.assertIn("display:none", next(b for s, b in self.rules if s == "html.m.land #pgrip"))
        kb = next(b for s, b in self.rules if s.startswith("html.m.kb #today"))
        self.assertIn("top:var(--vvt,0px)", kb)
        self.assertIn("height:calc(var(--vvh,100dvh) - var(--P))", kb)

    def test_the_tab_bar_stays_reachable_over_the_more_sheet(self):
        z = lambda sel: int(re.search(r"z-index:(\d+)", next(b for s, b in self.rules if s == sel)).group(1))
        self.assertGreater(z("html.m #tabs"), z("html.m #msheet"))               # a tap on a tab lands on the tab, not on a sheet row
        self.assertGreater(z("html.m #tabs"), max(z(sel) for sel in ("html.m #permits", "html.m #panel", "html.m #mclose")))
        scrim = next(b for s, b in self.rules if s.startswith("html.m #scrim"))
        self.assertGreater(z("html.m #tabs"), int(re.search(r"z-index:(\d+)", scrim).group(1)))
        sheet = next(b for s, b in self.rules if s == "html.m #msheet")
        self.assertIn("bottom:var(--B)", sheet)                                   # the sheet rises from the bar, never over it
        self.assertIn("max-height:calc(100dvh - var(--T) - var(--B) - 8px)", sheet)
        self.assertIn("bottom:0", next(b for s, b in self.rules if s == "html.m.land #msheet,html.m.kb #msheet"))

    def test_a_waiting_seat_is_reserved_room_never_laid_over_a_docked_bar(self):
        self.assertIn("--P:0px", next(b for s, b in self.rules if s == "html.m"))
        for sel in ("html.m #today", "html.m #side", "html.m #panel", "html.m body[data-tab=wingmate] #mate"):
            body = next(b for s, b in self.rules if s == sel)
            self.assertIn("calc(var(--B) + var(--P))", body, sel)
        three = next(b for s, b in self.rules if s.startswith("html.m #quests,html.m #prompter,html.m #legend"))
        self.assertIn("bottom:calc(var(--B) + var(--P))", three)
        for sel in ("html.m #quick", "html.m #mfab", "html.m #mzoom", "html.m #mtoast.on"):
            self.assertIn("var(--P)", next(b for s, b in self.rules if s == sel), sel)
        self.assertIn("max-height:calc(var(--vvh,100dvh) * .3)", next(b for s, b in self.rules if s == "html.m.kb #permits"))

    def test_the_phone_bar_eats_the_taps_that_land_on_it(self):
        self.assertIn("pointer-events:auto", next(b for s, b in self.rules if s == "html.m #hud"))

    def test_file_rows_reach_44_px_through_their_cells(self):
        # min-height does nothing on a table row: the cell carries the floor
        for sel in ("html.m #panel tr.frow td", "html.t #panel tr.frow td"):
            body = next(b for s, b in self.rules if s == sel)
            self.assertIn("height:44px", body)
            self.assertIn("box-sizing:border-box", body)

    def test_the_advanced_toggle_is_a_button_and_the_row_follows_it(self):
        self.assertIn("html.m #cadv.open + .crow.r2", self.block)
        self.assertIn("display:none", next(b for s, b in self.rules if s == "html.m #composer .crow.r2"))
        self.assertIn("display:grid", next(b for s, b in self.rules if s == "html.m #cadv.open + .crow.r2"))

    def test_the_task_button_is_the_maps_and_the_thread_list_keeps_its_own(self):
        #.8 (P20): "+ Task" on the Map tab only, never over Today; the Agents list's own "+ New task" serves there
        shown = [s for s, b in self.rules if "#mfab" in s and "display:flex" in b]
        self.assertTrue(shown)
        for sel in shown:
            self.assertIn("body[data-tab=map]", sel, "the button shows on the map only: " + sel)
        self.assertFalse(any("data-tab=today" in s and "#mfab" in s for s, b in self.rules))
        self.assertEqual(next(b for s, b in self.rules if s == "html.m #today button.tnew"), "display:none")
        self.assertFalse(any("#snew" in s and b == "display:none" and "tview" not in s and "compose" not in s for s, b in self.rules))
        fab = next(b for s, b in self.rules if s == "html.m #mfab")
        for decl in ("right:calc(var(--railw) + 14px)", "bottom:calc(var(--B) + var(--P) + 14px)", "height:42px"):
            self.assertIn(decl, fab)
        today = next(b for s, b in self.rules if s == "html.m #today")
        self.assertGreater(int(re.search(r"z-index:(\d+)", fab).group(1)), int(re.search(r"z-index:(\d+)", today).group(1)),
                           "the button is above the screens")
        # it aims at the planet in view and opens the composer as a sheet over the map
        self.assertIn("const t=mobAim(); MOB_CMAP=document.body.dataset.tab==='map'; openComposer(t);", self.html)
        self.assertIn("return composerOpen()&&MOB_CMAP?'map':'threads';", self.html)

    def test_the_composer_gives_the_folder_its_whole_row_and_the_pick_button_sits_by_the_label(self):
        folder = next(b for s, b in self.rules if s == "html.m #composer .cwide #cfolder")
        self.assertIn("grid-column:1 / -1", folder)
        self.assertIn("grid-row:2", folder)
        for sel in ("html.m #composer .cwide label", "html.m #cpick"):
            self.assertIn("grid-row:1", next(b for s, b in self.rules if s == sel), sel)
        self.assertIn("function folderTail()", self.html)
        self.assertIn("document.getElementById('cfolder').addEventListener('blur',folderTail)", self.html)

    def test_sideways_the_map_runs_to_the_tab_rail_and_the_button_floats_over_it(self):
        # no zoom column on a phone: the stage runs to the tab rail, + Task sits in the map's corner
        self.assertEqual(next(b for s, b in self.rules if s == "html.m.land #stage"), "right:var(--railw)")
        fab = next(b for s, b in self.rules if s == "html.m.land #mfab")
        self.assertIn("right:calc(var(--railw) + 14px)", fab)
        self.assertIn("bottom:calc(var(--P) + 14px)", fab)
        self.assertFalse(any(s.startswith("html.m") and "#mzoom" in s and "display:flex" in b and "html.t" not in s for s, b in self.rules),
                         "a phone shows no zoom column")
        self.assertIn("mq$('mzoom').hidden=true;", self.html)

    def test_a_tablet_gets_the_touch_controls_and_nothing_else(self):
        tablet = [(s, b) for s, b in self.rules if s.startswith("html.t") or ",html.t" in s]
        self.assertTrue(tablet)
        joined = " ".join(s for s, _ in tablet)
        for needed in ("#mzoom", "#panel:not(.on)", "input", "button"):
            self.assertIn(needed, joined)
        # the one desktop-layout exceptions: the card on the right (the touch rule pinned it to the left edge), and the wingmate
        # bubble off the fly pad and out of a thread's footer (it sat on Mark done, the reply box and the arrows)
        mate_rules = {s: b for s, b in tablet if re.search(r"#mate\b", s)}
        # ...with fingers' room on its buttons (off Today's folder list it is on every size, not a tablet rule: see
        # TodayWingmateTests)
        self.assertEqual(sorted(mate_rules), ["html.t #mate", "html.t #mate #mateq", "html.t #mate :is(button,[role=button])",
                                              "html.t #wrap.side #mate"])
        self.assertEqual(mate_rules["html.t #mate"], "bottom:200px")
        self.assertEqual(mate_rules["html.t #wrap.side #mate"], "display:none")
        self.assertEqual(mate_rules["html.t #mate :is(button,[role=button])"], "min-height:44px;min-width:44px")
        self.assertEqual(mate_rules["html.t #mate #mateq"], "min-height:44px")
        self.assertEqual(next(b for s, b in tablet if s == "html.t #panel"), "left:auto;right:var(--sidew)")
        for s, _ in tablet:
            self.assertNotRegex(s, r"#(?:hud|ticker|quick|comms|dash|tabs|mfab)\b", "html.t leaves the desktop layout alone: " + s)

    def test_the_page_script_names_the_phone_pieces_and_wraps_nothing(self):
        script = re.search(r"<script>(.*)</script>", self.html, re.S).group(1)
        for name in ("function mobSync()", "function mtab(", "function msheetOpen(", "function msheetClose()", "function mobBack()",
                     "function threadBack()", "function mToast(", "function hintScrub(", "function mobVV()", "function mobDetect()"):
            self.assertIn(name, script)
        self.assertNotRegex(script, r"\b(?:openTask|show|openComposer|toggleQuests|renderToday|closeAll|setView)\s*=\s*(?:function|\()")
        self.assertNotRegex(script, r"\b_?orig\w*\s*=\s*(?:openTask|show|openComposer|toggleQuests)\b")
        self.assertIn("history.pushState(", script)
        self.assertIn("addEventListener('popstate'", script)
        self.assertIn("visualViewport", script)
        self.assertIn("scrollIntoView({block:'center'", script)
        self.assertNotIn("interactive-widget", self.html)                  # the default keyboard behaviour stays; visualViewport does the work
        self.assertIn("if(!MOB){ try{ localStorage.setItem('mate'", script)   # the phone never writes the wingmate pref
        self.assertIn("MATE.hidden=false", script)


class GestureSourceTests(unittest.TestCase):
    """What the gesture layer must leave alone, read from the page source: the mouse path, the desktop zoom rule."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        (Path(cls.tmp.name, "work", "Alpha")).mkdir(parents=True)
        proc = render_fixture(cls.tmp.name)
        if proc.returncode:
            raise AssertionError(proc.stderr[-2000:])
        cls.html = Path(cls.tmp.name, "page.html").read_text(encoding="utf-8")
        cls.script = re.search(r"<script>(.*)</script>", cls.html, re.S).group(1)
        src = source_css()
        cls.rules = css_rules(src[src.index(MOBILE_MARK):])

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_the_mouse_path_is_the_one_master_had(self):
        script = self.script
        for line in (
            "pointerStart=[e.clientX,e.clientY]; lastMouse=[e.clientX,e.clientY]; dragging=false;",
            "if(!dragging && Math.hypot(e.clientX-pointerStart[0],e.clientY-pointerStart[1])>12) dragging=true;",
            "if(dragging){ dragging=false; pointerStart=null; suppressClickUntil=Date.now()+200; return; }",
            "const hit=pickAt(wx,wy);",
            "if(hit)activate(hit);else pilot={source:'human',x:clamp(wx,minGX*TILE,maxGX*TILE),y:clamp(wy,minGY*TILE,maxGY*TILE)};",
        ):
            self.assertIn(line, script)
        # every touch branch keys on the pointer type; the handlers hand a finger over before the mouse code runs
        self.assertEqual(len(re.findall(r"if\(e\.pointerType==='touch'\)\{ finger(?:Down|Move|Up)\(e(?:,false)?\); return; \}", script)), 3)
        self.assertIn("if(e.pointerType==='touch'){ fingerUp(e,true); return; }", script)

    def test_the_canvas_takes_touches_and_gives_no_menu(self):
        script = self.script
        self.assertIn("cv.addEventListener('click',()=>{});", script)
        self.assertIn("cv.addEventListener('contextmenu',e=>{ if(touchUI()||Date.now()-TOUCH_AT<1500) e.preventDefault(); });", script)
        self.assertIn("cv.setPointerCapture(e.pointerId)", script)
        self.assertIn("const FINGERS=new Map()", script)
        rule = next(b for s, b in self.rules if s == "html.m canvas#c,html.t canvas#c")
        for decl in ("-webkit-touch-callout:none", "user-select:none"):
            self.assertIn(decl, rule)
        self.assertIn("canvas{display:block;image-rendering:pixelated;width:100%;height:100%;touch-action:none}", self.html)

    def test_the_numbers_the_spec_gives(self):
        script = self.script
        self.assertIn("TAP_SLOP=10, PRESS_SLOP=8, PRESS_MS=450, TAP_PX=22", script)
        self.assertIn("pickAt(w.x,w.y,TAP_PX/ZOOM)", script)
        self.assertIn("CM.haptic('tap');\n  BURSTS.push", script)   # the long press buzzes through the one haptic function, 10 ms
        self.assertIn("suppressClickUntil=Date.now()+200", script)
        self.assertIn("let d=Math.min(3,dpr); if(vw*vh*d*d>3.8e6) d=Math.min(d,2);", script)
        self.assertIn("FT_EMA>28", script)
        self.assertIn("FT_SLOW>=90", script)

    def test_a_phone_a_desktop_and_a_tablet_fit_the_world_unless_a_zoom_is_stored(self):
        # the phone's fixed 0.22 became the same fit-all home view as a desktop's
        script = self.script
        self.assertIn("let ZOOM=MOB?0.22:1, ZOOM_STORED=false;", script)
        self.assertIn("ZOOM_STORED=true", script)
        self.assertIn("if(!ZOOM_STORED){ ZOOM=fitZoom(VW,VH); HOME.on=true; homeNote(); }", script)
        self.assertIn("function homeZoom(){ return homeView().zoom; }", script)
        self.assertNotIn("if(MOB) return {zoom:0.22,ox:0,oy:0};", script)
        self.assertIn("function zoomFloor(){ return MOB?0.06:0.2; }", script)
        # the fit waits for the rails: the stage it measures is the one it keeps
        self.assertLess(script.index("sideShow(sd==null?"), script.index("if(!ZOOM_STORED){ ZOOM=fitZoom(VW,VH);"))
        # the cards over the stage are read when the map is up, and the 0 key and the ring's long press make the same view
        self.assertIn("if(HOME_LIVE) homeSettle();", script)
        self.assertIn("else if(e.key==='0') zoomHome();", script)
        self.assertEqual(script.count("function zoomHome()"), 1)

    def test_the_phone_borrows_the_desktop_switches_without_writing_them(self):
        script = self.script
        start = script[script.index("function mobStart(){"):script.index("function mobStop(){")]
        self.assertNotIn("setQuiet(", start)                                  # setQuiet writes the pref
        self.assertNotIn("localStorage", start)
        stop = script[script.index("function mobStop(){"):script.index("function mobFlip(){")]
        for key in ("'dash'", "'quiet'", "'mate'"):
            self.assertIn("localStorage.getItem(" + key + ")", stop)

    def test_the_phone_keeps_what_it_has_and_skips_what_nobody_sees(self):
        script = self.script
        self.assertIn("if(MOB&&cv.width===Math.floor(VW*DPR)&&cv.height===Math.floor(VH*DPR))", script)
        self.assertIn("pumpActions();if(!mapCovered())draw(t);else FRAME_T=t;wantFrame();", script)
        self.assertIn("if(near&&!MOB){", script)                      # no Enter / E hint on a phone

    def test_the_zoom_buttons_and_the_scrim_are_wired(self):
        script = self.script
        for line in ("mq$('mz-in').addEventListener('click',()=>zoomBy(ZOOM_STEP));",
                     "mq$('mz-out').addEventListener('click',()=>zoomBy(1/ZOOM_STEP));",
                     "c.addEventListener('click',()=>{ if(held){ held=false; return; } recenter(); });"):
            self.assertIn(line, script)
        self.assertRegex(script, r"mq\$\('scrim'\)\.addEventListener\('click',\(\)=>\{ if\(Date\.now\(\)<suppressClickUntil\) return; msheetClose\(\); \}\);")
        self.assertIn("mq$('msheet-x').addEventListener('click',()=>{ if(Date.now()<suppressClickUntil) return; msheetClose(); });", script)
        self.assertRegex(script, r"b\.addEventListener\('click',\(\)=>\{ if\(Date\.now\(\)<suppressClickUntil\) return; msheetClose\(\);")


class CommsLineTests(unittest.TestCase):
    """ships.comms_line: what a seat's pane says, without the furniture around it."""

    def test_a_key_or_token_in_a_pane_is_never_echoed(self):
        for pane, kept in (("export OPENAI_API_KEY=" + "sk-" + "abcdefghijklmnop1234", "export OPENAI_API_KEY=[redacted]"),
                           ("Authorization: Bearer abcdefghijklmnopqrstuvwxyz", "Authorization: [redacted]"),
                           ("token " + "ghp_" + "abcdefghijklmnopqrstuvwxyz123456 pushed", "token [redacted] pushed"),
                           ("hash 0123456789abcdef0123456789abcdef01234567 done", "hash [redacted] done"),
                           ("ran 34 tests in 7.9s OK", "ran 34 tests in 7.9s OK")):
            self.assertEqual(ships.comms_line(pane), kept)
            self.assertNotIn("abcdefghijklmnop", ships.comms_line(pane))

    def test_claude_code_tail_gives_its_last_words(self):
        pane = ("● Reading the cron map\n  ⎿  Read Areas/cron-map.md (212 lines)\n\n"
                "╭──────────╮\n│ >        │\n╰──────────╯\n  ⏵⏵ bypass permissions on (shift+tab to cycle)")
        self.assertEqual(ships.comms_line(pane), "Read Areas/cron-map.md (212 lines)")

    def test_working_spinner_keeps_its_words_without_the_key_hint(self):
        self.assertEqual(ships.comms_line("✻ Re-pointing the cron lines… (48s · esc to interrupt)\n> "),
                         "Re-pointing the cron lines…")

    def test_codex_footer_and_shell_prompts_are_dropped(self):
        self.assertEqual(ships.comms_line("• Ran tests\n  └ 34 passed\n› \n  ? for shortcuts    5 warnings f2"),
                         "34 passed")
        self.assertEqual(ships.comms_line("12 new listings\ndev@box:/srv/notes/Areas/Career$ "), "12 new listings")
        self.assertEqual(ships.comms_line("done\n/srv/notes/Areas/Finance$"), "done")
        self.assertEqual(ships.comms_line("(venv) ~/src$ "), "")

    def test_escape_codes_and_control_bytes_go(self):
        self.assertEqual(ships.comms_line("\x1b[32mregime: neutral\x1b[0m\x07 · BTC flat\r"), "regime: neutral · BTC flat")
        self.assertEqual(ships.comms_line("\x1b]0;title\x07hello"), "hello")

    def test_cut_to_width_with_an_ellipsis(self):
        line = ships.comms_line("word " * 40)
        self.assertEqual(len(line), 60)
        self.assertTrue(line.endswith("…"))
        self.assertEqual(len(ships.comms_line("x" * 30, width=20)), 20)

    def test_nothing_but_furniture_is_empty(self):
        for pane in ("", None, "❯ ", "╭────╮\n│ >  │\n╰────╯", "  ? for shortcuts", "$", "accept edits on (shift+tab to cycle)"):
            with self.subTest(pane=pane):
                self.assertEqual(ships.comms_line(pane), "")


# H2 page hardening: keys. Window listeners are captured by the harness (__WL); a stand-in element tree
# keeps what the page does to the classes of the rails, the composer and the sheets.
KEYS_DRIVER = r"""
  const sets = {};
  const cl = (id) => { const st = sets[id] = sets[id] || new Set(); return { contains: (c) => st.has(c),
    add: (...c) => c.forEach((x) => st.add(x)), remove: (...c) => c.forEach((x) => st.delete(x)),
    toggle: (c, f) => { if (f === undefined) f = !st.has(c); if (f) st.add(c); else st.delete(c); return f; } }; };
  ['wrap', 'side', 'composer', 'legend', 'panel', 'quests', 'prompter'].forEach((id) => { elCache[id] = cap(); elCache[id].classList = cl(id); });
  const has = (id, c) => sets[id].has(c);
  const fire = (key, o) => {
    const e = Object.assign({ key: key, repeat: false, ctrlKey: false, metaKey: false, altKey: false, shiftKey: false,
      prevented: false, preventDefault() { this.prevented = true; }, stopPropagation() {},
      getModifierState() { return false; }, target: document.activeElement || { blur() {} } }, o || {});
    (__WL.keydown || []).forEach((f) => f(e)); return e; };
  VIEW = 'map'; document.activeElement = null; hover = null; near = null;
  ok('the harness kept the page keydown listeners', (__WL.keydown || []).length >= 4);

  fire('n'); ok('N opens the composer', composerOpen() === true && sideOpen() === true);
  closeAll(); ok('closeAll closes the composer', composerOpen() === false);

  // a browser or system shortcut is not a page action
  const zoom0 = ZOOM, flows0 = FLOWS, quiet0 = QUIET;
  const combos = [['n', 'ctrlKey'], ['n', 'metaKey'], ['n', 'altKey'], ['k', 'metaKey'], ['k', 'ctrlKey'], ['l', 'ctrlKey'],
    ['f', 'ctrlKey'], ['f', 'metaKey'], ['-', 'ctrlKey'], ['=', 'ctrlKey'], ['+', 'metaKey'], ['0', 'ctrlKey'], ['0', 'metaKey'],
    ['[', 'metaKey'], [']', 'ctrlKey'], ['t', 'ctrlKey'], ['q', 'metaKey'], ['h', 'ctrlKey'], ['1', 'ctrlKey'], ['3', 'metaKey'],
    ['c', 'ctrlKey'], ['p', 'ctrlKey'], ['/', 'ctrlKey'], ['b', 'metaKey'], ['v', 'ctrlKey'], ['Home', 'altKey'], ['Backspace', 'ctrlKey'],
    ['Enter', 'metaKey'], ['e', 'ctrlKey'], ['ArrowLeft', 'altKey'], ['Escape', 'ctrlKey']];
  const dash0 = has('wrap', 'dash'), side0 = has('wrap', 'side'), th0 = THEME_NAME;
  const stray = [];
  combos.forEach(([k, m]) => { const e = fire(k, { [m]: true });
    if (e.prevented) stray.push(k + '+' + m + ' prevented'); });
  if (stray.length) out.push('  ' + stray.join(', '));
  ok('no Ctrl/Cmd/Alt chord is swallowed (preventDefault)', stray.length === 0);
  ok('no chord opens the composer, the rails, the legend or the board',
    composerOpen() === false && has('wrap', 'side') === side0 && has('wrap', 'dash') === dash0 && !has('legend', 'on') && !has('quests', 'on'));
  ok('no chord zooms, flows, quiets, changes the view or the theme',
    ZOOM === zoom0 && FLOWS === flows0 && QUIET === quiet0 && VIEW === 'map' && THEME_NAME === th0);
  ok('...and no chord starts the pilot', Object.keys(keys).length === 0);
  // ] is the side panel's on the desktop (one path; the old dashboard listener steps aside, [ is retired with it)
  const rc0 = has('wrap', 'rail-closed');
  fire(']', { ctrlKey: true, altKey: true, getModifierState: (m) => m === 'AltGraph' });
  ok('AltGr (Ctrl+Alt on Windows) still types the key', has('wrap', 'rail-closed') !== rc0 && has('wrap', 'dash') === dash0);
  fire(']');
  ok('a plain ] still toggles the side panel back', has('wrap', 'rail-closed') === rc0);
  fire('=', { shiftKey: true }); ok('a plain = still zooms in', ZOOM > zoom0);
  setZoom(zoom0);

  // a focused select, checkbox, radio or text field owns the arrows, Space and the letters
  const controls = { select: { tagName: 'SELECT' }, checkbox: { tagName: 'INPUT', type: 'checkbox' }, radio: { tagName: 'INPUT', type: 'radio' },
    input: { tagName: 'INPUT', type: 'text' }, textarea: { tagName: 'TEXTAREA' }, editable: { tagName: 'DIV', isContentEditable: true } };
  Object.keys(controls).forEach((name) => {
    document.activeElement = controls[name];
    ok(name + ': typing() is true', typing() === true);
    const arrow = fire('ArrowDown'), space = fire(' '), letter = fire('n'), wasd = fire('w'), k = fire('k');
    ok(name + ': arrows and Space reach the control', !arrow.prevented && !space.prevented);
    ok(name + ': letters fire no page action', composerOpen() === false && has('wrap', 'side') === side0 && !keys.w && !wasd.prevented && !k.prevented);
  });
  // a focused button keeps the letters for the page but its Space and Enter belong to it
  document.activeElement = { tagName: 'BUTTON', getAttribute() { return null; } };
  ok('a button is not typing', typing() === false);
  ok('a focused button gets its Space', fire(' ').prevented === false);
  ok('a focused link and a role=button element get their Space too',
    (document.activeElement = { tagName: 'A', getAttribute() { return null; } }, fire(' ').prevented === false)
    && (document.activeElement = { tagName: 'DIV', getAttribute: (a) => (a === 'role' ? 'button' : null) }, fire(' ').prevented === false));
  document.activeElement = { tagName: 'BUTTON', getAttribute() { return null; } };
  let activated = 0; near = { uid: 'x', kind: 'dir' };
  const realActivate = activate; activate = () => { activated++; };
  fire('Enter'); ok('Enter on a focused button does not also activate the planet beside the ship', activated === 0);
  document.activeElement = null; fire('Enter'); ok('Enter on the page still activates it', activated === 1);
  activate = realActivate; near = null;
  document.activeElement = null;
  ok('with nothing focused, Space and the arrows are held back from the page scroll', fire(' ').prevented === true && fire('ArrowDown').prevented === true);
  delete keys.arrowdown;

  // K, [ and ] with the composer open: the rail goes, so the composer goes with it and N opens it again
  [['k', 'K'], [']', ']']].forEach(([key, label]) => {
    fire('n'); ok(label + ': composer open first', composerOpen() === true && sideOpen() === true);
    fire(key);
    ok(label + ': the rail is hidden and the composer is not left open behind it', sideOpen() === false && composerOpen() === false && !has('side', 'compose'));
    fire('n'); ok(label + ': N opens it again at once', composerOpen() === true && sideOpen() === true);
    closeAll();
  });
  fire('n'); fire('[');
  ok('[ leaves the composer and its rail alone', composerOpen() === true && sideOpen() === true);
  fire('['); closeAll();
  fire('n'); document.getElementById('sclose'); sideShow(false);
  ok('the rail close button closes the composer too', composerOpen() === false);

  // the legend closes by Esc and by closeAll
  fire('l'); ok('L opens the legend', has('legend', 'on'));
  fire('Escape'); ok('Esc closes the legend', !has('legend', 'on'));
  fire('l'); closeAll(); ok('closeAll closes the legend', !has('legend', 'on'));
  fire('l'); fire('l'); ok('L still toggles it off', !has('legend', 'on'));
"""

# H2 page hardening in a real headless Chrome: the page is the rendered file, a script runs before it (init:
# a bridge flag and a fake fetch), then steps run in it (eval) or resize the window.
HARDEN_RUNNER = r"""
import fs from 'node:fs';
const [port, url, specFile] = process.argv.slice(2);
const S = JSON.parse(fs.readFileSync(specFile, 'utf8'));
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
const metrics = (w, h) => send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: S.dpr || 1, mobile: !!S.mobile });
await metrics(S.width, S.height);
if (S.mobile) await send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 });
await send('Page.addScriptToEvaluateOnNewDocument', { source: 'try{localStorage.clear();sessionStorage.clear();}catch(e){}\n' + (S.init || '') });
errors.length = 0;   // Runtime.enable replays what an earlier page threw: only this page counts
const loaded = new Promise((r) => ws.addEventListener('message', (m) => { if (JSON.parse(m.data).method === 'Page.loadEventFired') r(); }));
await send('Page.navigate', { url });
await loaded; await sleep(S.settle || 1200);
const values = {}; let exception = null;
for (const st of S.steps) {
  if (st.resize) { await metrics(st.resize[0], st.resize[1]); await sleep(st.wait || 600); continue; }
  const r = await send('Runtime.evaluate', { returnByValue: true, awaitPromise: true,
    expression: `(async()=>{ const sleep=(ms)=>new Promise(r=>setTimeout(r,ms)); ${st.eval} })()` });
  if (r.exceptionDetails) { exception = r.exceptionDetails.exception?.description || r.exceptionDetails.text; break; }
  if (st.name) values[st.name] = r.result.value;
}
console.log('RESULT ' + JSON.stringify({ values, exception, blocked, errors: errors.slice(0, 5) }));
ws.close();
process.exit(0);
"""

# a live page whose one thread has a 60000-entry log, served by entry (100 bytes each, 1000 per read)
BIG_LOG_INIT = r"""
window.CM_BRIDGE = { live: true, remote: false };
window.__reqs = []; window.__total = 60000;
const KINDS = ['text', 'tool', 'cmd', 'file', 'raw', 'think', 'err', 'result', 'meta'], BYTES = 100, CHUNK = 1000;
window.fetch = async (url) => {
  url = String(url);
  const ok = (o) => ({ ok: true, status: 200, json: async () => o });
  if (!url.startsWith('/task?')) return ok({});
  const since = +new URLSearchParams(url.split('?')[1]).get('since');
  window.__reqs.push(since);
  const from = Math.floor(since / BYTES), to = Math.min(window.__total, from + CHUNK), lines = [];
  for (let i = from; i < to; i++) lines.push({ k: KINDS[i % 3 === 0 ? 0 : i % KINDS.length], s: 'L' + i });
  return ok({ id: 't1', title: 'big', status: 'running', runner: 'claude', model: '', target: '/', runs: 1, run: 1,
    mode: 'inplace', repo: false, offset: to * BYTES, size: window.__total * BYTES, more: to < window.__total,
    lines, prompts: [{ n: 1, text: 'do it' }] });
};
"""
BIG_LOG_HELPERS = r"""
const drawn = () => Array.from(document.querySelectorAll('#tlog p, #tlog .tv-al>div, #tlog>.tv-msg.err, #tlog>.tv-div:not(.tl-more):not(.tl-ph)'))
  .map((n) => +n.textContent.match(/L(\d+)/)[1]);
const state = () => { const ids = drawn(); return { total: TV.total, lo: TV.lo, n: ids.length, first: ids[0], last: ids[ids.length - 1],
  contiguous: ids.every((v, i) => i === 0 || v === ids[i - 1] + 1), more: (document.querySelector('.tl-more') || {}).textContent || '',
  elements: document.getElementById('tlog').querySelectorAll('*').length, reqs: __reqs.slice() }; };
const until = async (f, n) => { for (let i = 0; i < (n || 80) && !f(); i++) await sleep(250); };
"""


class HardeningBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chrome = find_chrome()
        if not (cls.chrome and shutil.which("node")):
            raise unittest.SkipTest("needs node and a Chrome or Chromium binary (set CHROME_BIN)")
        cls.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        for name in ("Alpha", "Beta"):
            Path(cls.tmp.name, "work", name).mkdir(parents=True)
            Path(cls.tmp.name, "work", name, "notes.md").write_text("fixture\n", encoding="utf-8")
        proc = render_fixture(cls.tmp.name)
        if proc.returncode:
            raise AssertionError(proc.stderr[-2000:])
        cls.url = Path(cls.tmp.name, "page.html").as_uri()
        cls.runner = Path(cls.tmp.name, "runner.mjs")
        cls.runner.write_text(HARDEN_RUNNER, encoding="utf-8")
        profile = Path(cls.tmp.name, "profile")
        cls.browser = subprocess.Popen(
            [cls.chrome, "--headless=new", "--no-sandbox", "--disable-gpu", "--remote-debugging-port=0",
             f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check", "--remote-allow-origins=*",
             "--allow-file-access-from-files", "--disable-background-networking", "--disable-component-update", "--no-pings",
             "--host-resolver-rules=MAP * ~NOTFOUND", "--proxy-server=http://127.0.0.1:9", "--proxy-bypass-list=<-loopback>",
             "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        port_file = profile / "DevToolsActivePort"
        deadline = time.time() + 30
        while time.time() < deadline:
            if cls.browser.poll() is not None:
                raise AssertionError(f"{cls.chrome} exited with {cls.browser.returncode} before it opened a debugging port")
            if port_file.exists() and port_file.read_text(encoding="utf-8").strip():
                break
            time.sleep(0.1)
        else:
            raise AssertionError("Chrome opened no debugging port in 30 s")
        cls.port = port_file.read_text(encoding="utf-8").splitlines()[0]
        cls.runs = 0

    @classmethod
    def tearDownClass(cls):
        browser = getattr(cls, "browser", None)
        if browser is not None:
            browser.terminate()
            try:
                browser.wait(10)
            except subprocess.TimeoutExpired:
                browser.kill()
                browser.wait()
        tmp = getattr(cls, "tmp", None)
        if tmp is not None:
            tmp.cleanup()

    def run_steps(self, width, height, steps, mobile=False, init="", dpr=1, settle=None):
        type(self).runs += 1
        spec = Path(self.tmp.name, f"spec-{self.runs}.json")
        spec.write_text(json.dumps({"width": width, "height": height, "mobile": mobile, "init": init, "dpr": dpr,
                                    "settle": settle, "steps": steps}), encoding="utf-8")
        run = subprocess.run(["node", str(self.runner), self.port, self.url, str(spec)],
                             capture_output=True, text=True, timeout=180)
        line = next((ln for ln in run.stdout.splitlines() if ln.startswith("RESULT ")), None)
        self.assertIsNotNone(line, (run.stdout + run.stderr)[-3000:])
        result = json.loads(line[len("RESULT "):])
        self.assertIsNone(result["exception"], result)
        self.assertEqual(result["errors"], [], "the page threw")
        return result["values"]

    def test_a_tablet_shows_the_zoom_buttons_over_the_map_only(self):
        probe = ("const z = document.getElementById('mzoom'), r = z.getBoundingClientRect();"
                 " return { cls: document.documentElement.className, shown: getComputedStyle(z).display !== 'none' && r.width > 0,"
                 " view: VIEW, today: document.getElementById('today').classList.contains('on') };")
        steps = [{"name": "map", "eval": "setView('map'); await sleep(200);" + probe},
                 {"name": "today", "eval": "setView('today'); await sleep(200);" + probe},
                 {"name": "back", "eval": "setView('map'); await sleep(200);" + probe}]
        got = self.run_steps(768, 1024, steps, mobile=True)
        self.assertEqual(got["map"]["cls"].split(), ["t"])
        self.assertTrue(got["map"]["shown"], "the buttons sit over the map")
        self.assertTrue(got["today"]["today"])
        self.assertFalse(got["today"]["shown"], "Today covers the map and its buttons: they are hidden")
        self.assertTrue(got["back"]["shown"])

    def test_a_tablets_card_opens_on_the_right_and_the_planet_stays_in_view(self):
        steps = [{"name": "card", "eval":
                  "setView('map'); await sleep(300); const m = MOONS[0]; activate(unitByUid(m.uid)); await sleep(3000);"
                  " const P = document.getElementById('panel'), p = P.getBoundingClientRect(), st = document.getElementById('stage').getBoundingClientRect();"
                  " return { cls: document.documentElement.className, on: P.classList.contains('on'), left: p.left, right: p.right, vw: innerWidth,"
                  " px: (m.x - camx) * ZOOM + st.left, panelW: PANEL_W };"}]
        for width, height in ((768, 1024), (744, 1000), (1024, 768)):
            got = self.run_steps(width, height, steps, mobile=True)["card"]
            self.assertEqual(got["cls"].split(), ["t"], width)
            self.assertTrue(got["on"], width)
            self.assertEqual(got["right"], width, "the card sits against the right edge (%d)" % width)
            self.assertGreater(got["left"], 0, "and leaves the left part of the map free (%d)" % width)
            # the card is the rail's: the stage ends where the rail begins, so the camera centres in the stage
            self.assertEqual(got["panelW"], 0, "a card in the rail covers none of the map (%d)" % width)
            self.assertLess(got["px"], got["left"] - 20, "the planet it opened for stays in the free strip (%d)" % width)

    def test_a_tablets_wingmate_leaves_the_fly_pad_and_steps_aside_for_a_thread(self):
        steps = [{"name": "pad", "eval":
                  "setView('map'); await sleep(300); const pad = document.getElementById('touchpad'), up = pad.querySelector('[data-dir=up]'),"
                  " r = up.getBoundingClientRect(), mate = document.getElementById('mate'), mr = mate.getBoundingClientRect(),"
                  " hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2), w = document.getElementById('wrap');"
                  " w.classList.add('side'); await sleep(100); const during = getComputedStyle(mate).display; w.classList.remove('side');"
                  " return { cls: document.documentElement.className, hit: hit === up, mateH: mr.height, mateBottom: mr.bottom, mateTop: mr.top,"
                  " padTop: pad.getBoundingClientRect().top, padBottom: pad.getBoundingClientRect().bottom, during };"}]
        for width, height in ((768, 1024), (1024, 768)):
            got = self.run_steps(width, height, steps, mobile=True)["pad"]
            self.assertEqual(got["cls"].split(), ["t"], width)
            self.assertGreater(got["mateH"], 0, width)
            self.assertTrue(got["hit"], "the arrow is not under the assistant (%d)" % width)
            # a tablet's assistant is the status bar's line, under the fly pad, never over it; a thread in the rail
            # leaves it where it is
            self.assertGreaterEqual(got["mateTop"], got["padBottom"], width)
            self.assertNotEqual(got["during"], "none", width)

    def test_a_desktop_page_has_no_phone_classes_and_its_fixed_geometry_is_the_chromes(self):
        # what a desktop page keeps whatever the wingmate says or the theme is called: no m or t class, every new piece
        # hidden, and the places the rails, the HUD, the comms box, the ticker and the closed card have always had
        ids = ["tabs", "tab-today", "tab-map", "tab-threads", "tab-wingmate", "mfab", "mzoom", "mz-in", "mz-out", "mz-c", "mmore",
               "msheet", "msheet-title", "msheet-body", "msheet-x", "scrim", "mclose", "mtoast", "pickbar", "pgrip", "sback", "cadv", "cpick"]
        probe = ("const q = (s) => { const e = document.querySelector(s); const r = e.getBoundingClientRect();"
                 " return [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)]; };"
                 " return { cls: document.documentElement.className, shown: " + json.dumps(ids) + ".filter((i) => getComputedStyle(document.getElementById(i)).display !== 'none'),"
                 " dash: q('#dash'), hud: q('#hud'), comms: q('#comms'), ticker: q('#ticker'), mate: q('#mate'), panel: q('#panel'), vw: innerWidth,"
                 " stage: q('#stage'), rail: q('#rail'), bar: q('#statusbar') };")
        steps = [{"name": "d", "eval": "setView('map'); await sleep(400);" + probe}]
        for width, height in ((1600, 900), (1280, 800)):
            got = self.run_steps(width, height, steps)["d"]
            self.assertEqual(got["cls"], "", width)
            self.assertEqual(got["shown"], [], "the new pieces stay hidden on a desktop (%d)" % width)
            # the map at (0,0), the 400 px rail on the right, the 44 px status bar under the map with the
            # assistant's line in it; the retired boxes keep their ids and take no room; the closed card is nowhere
            self.assertEqual(got["stage"], [0, 0, width - 400, height - 44], width)
            self.assertEqual(got["rail"], [width - 400, 0, 400, height], width)
            self.assertEqual(got["bar"], [0, height - 44, width - 400, 44], width)
            for box in ("dash", "hud", "comms", "ticker", "panel"):
                self.assertEqual(got[box][2:], [0, 0], box)
            self.assertGreaterEqual(got["mate"][1], height - 44, "the assistant's line sits in the status bar (%d)" % width)
            self.assertLessEqual(got["mate"][1] + got["mate"][3], height, width)

    # ---- the phone and the tablet (H2 item 9) ----
    def test_a_tablets_worktree_box_has_a_44_px_hit_box_and_its_words_toggle_it(self):
        steps = [{"name": "box", "eval": "openComposer(null); await sleep(200);"
                  " const r = (e) => { const b = e.getBoundingClientRect(); return [Math.round(b.width), Math.round(b.height)]; };"
                  " const iso = document.getElementById('ciso'), before = iso.checked;"
                  " const sizes = { label: r(document.querySelector('label[for=ciso]')), chk: r(document.querySelector('#composer .chk')) };"
                  " document.querySelector('#composer .chk > span').click(); const afterWords = iso.checked;"
                  " iso.click(); const afterBox = iso.checked;"
                  " return { cls: document.documentElement.className, sizes, before, afterWords, afterBox };"}]
        got = self.run_steps(768, 1024, steps, mobile=True)["box"]
        self.assertEqual(got["cls"].split(), ["t"])
        for name, (w, h) in got["sizes"].items():
            self.assertGreaterEqual((w, h), (44, 44), name)
            self.assertGreaterEqual(h, 44, name)
        self.assertTrue(got["before"])
        self.assertFalse(got["afterWords"], "tapping its words toggles it")
        self.assertTrue(got["afterBox"], "and the box itself still does")

    def test_a_phone_keeps_what_people_and_agents_wrote_and_scrubs_only_the_pages_own_hints(self):
        steps = [{"name": "t", "eval":
                  "W.agent_tasks = [{ id: 't1', title: 'Add dark mode (A)', status: 'review', runner: 'claude', model: '', target: W.root_path, runs: 1, started: new Date().toISOString() }];"
                  " mtab('threads'); renderTaskList(); await sleep(400);"
                  " const row = document.querySelector('#tasks .trow b'), snew = document.getElementById('snew');"
                  " mateSay('Take option (B) or press Ctrl+Enter', { instant: true }); await sleep(300);"
                  " const L = document.getElementById('legend'); L.classList.add('on'); L.innerHTML = systemLegend(); await sleep(300);"
                  " const out = { cls: document.documentElement.className, title: row && row.textContent, snew: snew.textContent,"
                  " mate: document.getElementById('matetxt').textContent, legend: L.textContent,"
                  " keyed: Array.from(document.querySelectorAll('button, summary, label')).filter((e) => e.getClientRects().length && /[(][A-Z][)]|Ctrl.?Enter/.test(e.textContent)).map((e) => e.textContent) };"
                  " mtab('map'); return out;"}]
        got = self.run_steps(390, 844, steps, mobile=True)["t"]
        self.assertEqual(got["cls"].split(), ["m"])
        self.assertEqual(got["title"], "Add dark mode (A)", "a thread title is what the person wrote")
        self.assertIn("(B)", got["mate"], "so is a wingmate reply")
        self.assertIn("Ctrl+Enter", got["mate"])
        self.assertNotIn("(N)", got["snew"])
        self.assertIn("your tasks", got["legend"])
        self.assertNotRegex(got["legend"], r"your tasks \(N\)", "the legend paragraph loses its key")
        self.assertEqual(got["keyed"], [], "no button, summary or label teaches a key")

    def test_text_in_the_transcript_the_diff_and_the_card_can_be_selected_on_a_phone(self):
        steps = [{"name": "sel", "eval":
                  "const us = (e) => getComputedStyle(e).userSelect;"
                  " mtab('threads'); openTask('t1'); await sleep(300);"
                  " const log = document.getElementById('tlog'), msg = document.createElement('div'); msg.className = 'tv-msg agent'; msg.textContent = 'hello'; log.appendChild(msg);"
                  " const out = { tlog: us(log), msg: us(msg), tdiff: us(document.getElementById('tdiff')), tabs: us(document.getElementById('tabs')) };"
                  " const m = MOONS.find((x) => x.uid.startsWith('area:')); m.foes = [{ kind: 'cron', name: 'nightly', uid: 'cron:nightly' }, { kind: 'wreck', name: 'it broke', uid: 'wreck:x' }];"
                  " mtab('map'); show(units.find((u) => u.uid === m.uid)); await sleep(700);"
                  # every element of the open card that holds text of its own, by what it is
                  " const pb = document.getElementById('pbody'), seen = {}, wrong = [];"
                  " for (const e of pb.querySelectorAll('*')) { if (!Array.from(e.childNodes).some((n) => n.nodeType === 3 && n.textContent.trim())) continue;"
                  "   const inBtn = !!e.closest('button, .ctabs'), key = e.tagName.toLowerCase() + (e.className && typeof e.className === 'string' ? '.' + e.className.split(' ')[0] : '');"
                  "   (seen[key] = seen[key] || new Set()).add(us(e)); if (!inBtn && us(e) !== 'text') wrong.push(key); }"
                  " out.wrong = Array.from(new Set(wrong)); out.keys = Object.keys(seen);"
                  " out.rows = pb.querySelectorAll('.cprob span').length; out.state = !!pb.querySelector('.cstate'); out.csec = !!pb.querySelector('.csec');"
                  " out.btn = Array.from(pb.querySelectorAll('button')).map((b) => us(b)); out.grip = us(document.getElementById('pgrip')); out.close = us(document.getElementById('pclose'));"
                  " out.canvas = us(document.getElementById('c')); return out;"}]
        got = self.run_steps(390, 844, steps, mobile=True, init=BIG_LOG_INIT)["sel"]
        for key in ("tlog", "msg", "tdiff"):
            self.assertEqual(got[key], "text", key)
        self.assertEqual(got["rows"], 2, "the card has its problem rows")
        self.assertTrue(got["state"] and got["csec"], "...and its state line and section titles")
        self.assertEqual(got["wrong"], [], "no text of the open card is locked")
        self.assertEqual(set(got["btn"]), {"none"}, "buttons stay taps")
        self.assertEqual((got["tabs"], got["canvas"], got["grip"], got["close"]), ("none",) * 4,
                         "everything else on a phone is still not selectable")

    def test_the_landscape_card_leaves_room_for_its_close_button(self):
        steps = [{"name": "card", "eval":
                  "mtab('map'); show(units.find((u) => u.kind === 'area')); await sleep(900);"
                  " const c = document.getElementById('pclose').getBoundingClientRect();"
                  " const under = Array.from(document.querySelectorAll('#pbody button, #pbody h2, #pbody .ctabs *')).filter((e) => { const r = e.getBoundingClientRect();"
                  " return r.width > 0 && r.height > 0 && !(r.right <= c.left || r.left >= c.right || r.bottom <= c.top || r.top >= c.bottom); }).map((e) => e.textContent.slice(0, 20));"
                  " return { cls: document.documentElement.className, under, close: [c.left, c.right] };"}]
        got = self.run_steps(844, 390, steps, mobile=True)["card"]
        self.assertEqual(got["cls"].split(), ["m", "land"])
        self.assertEqual(got["under"], [], "nothing of the card's header lies under the cross")

    def test_the_wingmate_tab_dot_counts_from_when_you_left_it(self):
        steps = [{"name": "dot", "eval":
                  "const dot = () => document.getElementById('tabn-wingmate').hidden === false;"
                  " mtab('wingmate'); await sleep(300); MATE.lastSaid = Date.now(); await sleep(60); mtab('map'); await sleep(1300);"
                  " const seenWhileThere = dot(); await sleep(60); MATE.lastSaid = Date.now(); mobSync();"
                  " return { seenWhileThere, newWhileAway: dot() };"}]
        got = self.run_steps(390, 844, steps, mobile=True)["dot"]
        self.assertFalse(got["seenWhileThere"], "what it said while you were on the tab is not news")
        self.assertTrue(got["newWhileAway"], "what it says after you left is")

    def test_the_grip_answers_the_keyboard_and_the_more_sheet_takes_the_focus_in_and_gives_it_back(self):
        steps = [{"name": "grip", "eval":
                  "mtab('map'); show(units.find((u) => u.kind === 'area')); await sleep(900);"
                  " const g = document.getElementById('pgrip'), full = () => document.getElementById('panel').classList.contains('full'), ex = () => g.getAttribute('aria-expanded');"
                  " const start = [full(), ex()]; g.focus(); g.click(); await sleep(150); const up = [full(), ex()];"
                  " g.dispatchEvent(new MouseEvent('click', { detail: 1, bubbles: true })); await sleep(100); const tapAlone = [full(), ex()];"
                  " g.click(); await sleep(150); const down = [full(), ex()];"
                  " closeAll(); await sleep(300);"
                  " const more = document.getElementById('mmore'); more.focus(); mobMore(); await sleep(150);"
                  " const inside = document.activeElement && document.activeElement.closest('#msheet') ? document.activeElement.textContent : null;"
                  " const first = document.querySelector('#msheet-body button').textContent;"
                  " msheetClose(); await sleep(100);"
                  " return { start, up, tapAlone, down, inside, first, back: document.activeElement === more };"}]
        got = self.run_steps(390, 844, steps, mobile=True)["grip"]
        self.assertEqual(got["start"], [False, "false"])
        self.assertEqual(got["up"], [True, "true"], "Enter or Space on the grip pulls the card to full")
        self.assertEqual(got["tapAlone"], [True, "true"], "a pointer click is the tap handler's, not the keyboard's")
        self.assertEqual(got["down"], [False, "false"], "...and again goes back to half")
        self.assertEqual(got["inside"], got["first"], "the More sheet puts the focus on its first row")
        self.assertTrue(got["back"], "and gives it back when it closes")

    def test_a_phones_planet_label_carries_no_key_and_stays_on_the_screen(self):
        steps = [{"name": "tag", "eval":
                  "const m = MOONS.find((x) => x.uid.startsWith('area:')); m.enemies = 2; m.allies = 0; m.foes = [{ kind: 'cron', name: 'a', uid: 'cron:a' }, { kind: 'cron', name: 'b', uid: 'cron:b' }];"
                  " px = m.x; py = m.y + m.r + 40; mtab('map'); await sleep(1300);"
                  " const a = window.__plateAudit(), t = a.plates.find((p) => p.uid === m.uid && p.kind === 'tag');"
                  " return { text: t.text, label: CM.labelText(m.uid), slot: t.slot, x: t.x, w: t.w, view: a.view.w };"}]
        got = self.run_steps(390, 844, steps, mobile=True)["tag"]
        # a planet's label is its name in a fixed slot and one badge (the number of what is wrong
        # there); the words (no agent on it, long-press to send one) live in the card and the phone's long-press menu
        self.assertTrue(got["text"].startswith(got["label"] + " · 2"), got)
        self.assertNotRegex(got["text"], r"\bN to send|long-press|BATTLEFIELD|UNDEFENDED")
        self.assertIn(got["slot"], ("own", "+1", "-1", "right", "left"))
        self.assertLessEqual(got["x"] + got["w"], got["view"], "the label stays inside the screen")
        desk = self.run_steps(1280, 800, [{"name": "tag", "eval":
                  "const m = MOONS.find((x) => x.uid.startsWith('area:')); m.enemies = 2; m.allies = 0; m.foes = [{ kind: 'cron', name: 'a', uid: 'cron:a' }, { kind: 'cron', name: 'b', uid: 'cron:b' }];"
                  " px = m.x; py = m.y + m.r + 40; await sleep(1300);"
                  " return window.__plateAudit().plates.find((p) => p.uid === m.uid && p.kind === 'tag').text;"}])["tag"]
        # P13: no key hint inside a label; the key that sends an agent is in the "?" sheet
        self.assertRegex(desk, r" · 2$", "a desktop label says how much is wrong, no key")
        self.assertNotRegex(desk, r"\bN to send")

    def test_the_mono_palette_skins_the_whole_phone(self):
        greens = ["rgb(15, 56, 15)", "rgb(48, 98, 48)", "rgb(139, 172, 15)", "rgb(155, 188, 15)"]
        steps = [{"name": "gb", "eval":
                  "mtab('threads'); toast('x'); document.getElementById('mtoast').classList.add('on'); await sleep(300);"
                  " const out = { gb: document.body.classList.contains('gb') };"
                  " ['#hud', '#nums button', '#mmore', '#pickbar', '#mtoast', '#mclose', '#today', '#side', '#tabs'].forEach((sel) => { const c = getComputedStyle(document.querySelector(sel));"
                  " out[sel] = [c.backgroundColor, c.color, parseFloat(c.borderTopWidth) > 0 ? c.borderTopColor : null]; });"
                  " return out;"}]
        got = self.run_steps(390, 844, steps, mobile=True, init="sessionStorage.setItem('theme', 'gb');")["gb"]
        self.assertTrue(got["gb"])
        for sel, (bg, ink, edge) in ((k, v) for k, v in got.items() if k != "gb"):
            with self.subTest(sel):
                self.assertIn(bg, greens + ["rgba(0, 0, 0, 0)"], "background of " + sel)
                self.assertIn(ink, greens, "text of " + sel)
                if edge:
                    self.assertIn(edge, greens, "border of " + sel)

    def test_a_thread_opens_on_a_static_copy_of_the_page_without_an_error(self):
        steps = [{"name": "t", "eval": "W.agent_tasks = [{ id: 't1', title: 'one', status: 'review', runner: 'claude', model: '', target: W.root_path, runs: 1 }];"
                  " openTask('t1'); await sleep(300); return { live: LIVE, log: document.getElementById('tlog').textContent, poll: TV };"}]
        got = self.run_steps(1280, 800, steps)["t"]
        self.assertFalse(got["live"])
        self.assertIn("static copy of the page", got["log"])
        self.assertIsNone(got["poll"], "nothing is polled on a static copy")

    def test_a_huge_thread_log_draws_its_newest_lines_and_reads_on_incrementally(self):
        steps = [
            {"name": "opened", "eval": BIG_LOG_HELPERS + "openTask('t1'); await until(() => TV && TV.total >= __total); return state();"},
            {"name": "grown", "eval": BIG_LOG_HELPERS + "const before = __reqs.length; __total += 50;"
                " await until(() => TV.total >= __total, 40); const s = state(); s.new = s.reqs.slice(before); return s;"},
            {"name": "earlier", "eval": BIG_LOG_HELPERS + "const before = __reqs.length; document.querySelector('[data-tmore]').click();"
                " await until(() => TV.lo < 56000, 40); await sleep(200); const s = state(); s.new = s.reqs.slice(before); return s;"},
            {"name": "after", "eval": BIG_LOG_HELPERS + "const before = __reqs.length; __total += 10;"
                " await until(() => TV.total >= __total, 40); const s = state(); s.new = s.reqs.slice(before); return s;"},
        ]
        got = self.run_steps(1280, 800, steps, init=BIG_LOG_INIT)
        opened, grown, earlier, after = got["opened"], got["grown"], got["earlier"], got["after"]
        self.assertEqual(opened["total"], 60000)
        self.assertLessEqual(opened["n"], 3000, "the page draws only the newest lines")
        self.assertEqual((opened["last"], opened["lo"], opened["first"]), (59999, 57000, 57000))
        self.assertTrue(opened["contiguous"])
        self.assertIn("57000 earlier lines not shown", opened["more"])
        self.assertIn("show earlier", opened["more"])
        self.assertLess(opened["elements"], 12000, "the transcript is a few thousand elements, not 150000")
        self.assertEqual(opened["reqs"], sorted(opened["reqs"]), "the first read goes forward once")
        self.assertEqual(opened["reqs"][0], 0)
        self.assertEqual(grown["new"], [6000000], "polling asks only for what is new, from where it stopped")
        self.assertEqual((grown["total"], grown["last"]), (60050, 60049))
        self.assertLessEqual(grown["n"], 3000)
        self.assertTrue(grown["contiguous"])
        self.assertEqual((earlier["lo"], earlier["first"], earlier["last"]), (54050, 54050, 60049))
        self.assertTrue(earlier["contiguous"], "the earlier page joins the rest with no gap and no repeat")
        self.assertGreater(earlier["n"], 5000)
        self.assertGreater(earlier["new"][0], 0, "earlier lines are read from the nearest mark, not from the start")
        self.assertLessEqual(earlier["new"][-1], 57000 * 100)
        self.assertIn("54050 earlier lines not shown", earlier["more"])
        self.assertEqual(after["new"], [6005000], "...and polling is still incremental afterwards")
        self.assertEqual(after["last"], 60059)
        self.assertTrue(after["contiguous"])
        self.assertEqual(after["lo"], 54050, "lines the reader asked for stay while they scroll")


# the wingmate's tips: keys on a desktop, touch on a phone
# a touch tablet in the stub: the class mobDetect puts on <html> for a coarse pointer that is not a phone
TABLET_PREFIX = "document.documentElement.classList.add('t'); if (MOB || !touchUI()) throw new Error('not a tablet');"

TIPS_DRIVER = r"""
  W.agent_tasks = [T('r1', 'review'), T('f1', 'failed')]; W.quests = [{ title: 'q' }];
  // the decisions tip says the one count (W.counts.decisions, the words module) when the sky is read, the parked lines without one
  W.counts = Object.assign({}, W.counts || {}, { decisions: 1 });
  MOONS.push({ uid: 'moon:x', label: 'ALPHA', enemies: 2, allies: 0, foes: [{ name: 'a' }, { name: 'b' }], decay: 0.9, life: 'LIVE', age: 40, trend: { worse: true, why: 'w' } });
  units.push({ kind: 'seat', status: 'WAITING', name: 'seat1' });
  const tips = mateTips().map((t) => t.s), all = tips.join('\n');
  ok('a tip exists for each case', tips.length >= 12);
  if (MOB) {
    const keyed = tips.filter((t) => /\([A-Z]\)|[Pp]ress [A-Z]\b|[Ww]heel|\b0 resets|\bE opens|\b[NKTLQC] (starts|lists|is|recenters)|[Cc]lick|[Hh]over/.test(t));
    if (keyed.length) out.push('  ' + keyed.join(' | '));
    ok('no tip on a phone teaches a key, the wheel or a click', keyed.length === 0);
    ok('...they say what to touch', /Long-press it to send an agent/.test(all) && /Pinch zooms/.test(all) && /Agents tab/.test(all) && /decisions number/.test(all) && /Tap its ship/.test(all));
  } else if (touchUI()) {
    // a touch tablet (html.t, not a phone): no mouse and no keys either, and none of the phone's own controls
    const keyed = tips.filter((t) => /\([A-Z]\)|[Pp]ress [A-Z]\b|[Ww]heel|\b0 resets|\bE opens|\b[NKTLQC] (starts|lists|is|recenters)|[Cc]lick|[Hh]over/.test(t));
    if (keyed.length) out.push('  ' + keyed.join(' | '));
    ok('no tip on a touch tablet teaches a key, the wheel or a click', keyed.length === 0);
    ok('...they say what to touch', /Long-press it to send an agent/.test(all) && /Pinch zooms/.test(all) && /Agents rail/.test(all) && /decisions number/.test(all) && /Tap its ship/.test(all));
    ok('...and name nothing only a phone carries: no tab bar, no + button, no More', !/Agents tab|\+ button|The tabs below|More, at the top/.test(all));
  } else {
    ok('a desktop tip still teaches the keys', /\(K\)/.test(all) && /Press N on it to send an agent/.test(all) && /Wheel zooms, drag pans, 0 resets the zoom/.test(all) && /press Q\./.test(all));
  }
"""

# what the wingmate says about a thing you hover or tap: the keys and the mouse on a desktop, touch on a phone
EXPLAIN_DRIVER = r"""
  MATE.hidden = false; MATE.thinking = false;
  MOONS.push({ uid: 'moon:gamma', label: 'GAMMA', enemies: 2, allies: 0, foes: [{ name: 'a' }, { name: 'b' }], type: 'city', typeWhy: 'code' });
  const say = (u) => { MATE.mute = 0; mateExplain(u, true); return String(MATE.text); };
  const things = {
    board: BOARD,
    area: { kind: 'area', uid: 'moon:gamma', name: 'gamma', files: 3, age: 5 },
    dir: { kind: 'dir', uid: 'dir:d', name: 'd', area: 'area:gamma', files: 2, age: 1 },
    running: { kind: 'task', uid: 'task:1', name: 'a', status: 'running', runner: 'claude', model: '', target: W.root_path },
    review: { kind: 'task', uid: 'task:2', name: 'b', status: 'review', runner: 'claude', model: '', target: W.root_path },
    failed: { kind: 'task', uid: 'task:3', name: 'c', status: 'failed', runner: 'claude', model: '', target: W.root_path },
    lost: { kind: 'task', uid: 'task:4', name: 'd', status: 'lost', runner: 'claude', model: '', target: W.root_path },
    waiting: { kind: 'seat', uid: 'seat:1', name: 'seat1', status: 'WAITING' },
    seat: { kind: 'seat', uid: 'seat:2', name: 'seat2', status: 'WORKING' },
    proc: { kind: 'proc', uid: 'proc:1', name: 'p', elapsed: '1h', count: 2 },
    portal: { kind: 'portal', uid: 'portal:1', name: 'x', machine: 'other', status: 'up' },
  };
  const texts = Object.keys(things).map((k) => k + ': ' + say(things[k]));
  const keyed = texts.filter((t) => /[Cc]lick|(^|[^-\w])[Pp]ress |\bN\b|[Hh]over|\bwheel\b/.test(t));
  mateGreet();
  const greet = String(MATE.text);
  if (MOB) {
    if (keyed.length) out.push('  ' + keyed.join(' | '));
    ok('nothing the wingmate says about a thing on a phone teaches a click, a key or the hover', keyed.length === 0);
    ok('...the greeting asks for a tap', /Tap anything and I explain it/.test(greet) && !/[Hh]over/.test(greet));
    ok('...an undefended planet says to long-press it', /no agent on it: long-press it to send one/.test(things.area && texts[1]));
    ok('...and a tap opens it, the + button starts a task', /Tap to open it; the \+ button starts a task here/.test(texts[1]));
    ok('...the others say what to tap', /Tap it for the overview/.test(texts[0]) && /tap to watch/.test(texts[3]) && /tap to answer it/.test(texts[7]));
  } else if (touchUI()) {
    if (keyed.length) out.push('  ' + keyed.join(' | '));
    ok('nothing the wingmate says about a thing on a touch tablet teaches a click, a key or the hover', keyed.length === 0);
    ok('...the greeting asks for a tap', /Tap anything and I explain it/.test(greet) && !/[Hh]over/.test(greet));
    ok('...an undefended planet says to long-press it', /no agent on it: long-press it to send one/.test(texts[1]));
    ok('...a tap opens it and holding it starts a task: a tablet has no + button', /Tap to open it; hold it to start a task here/.test(texts[1]) && !/\+ button/.test(texts[1]));
    ok('...the others say what to tap', /Tap it for the overview/.test(texts[0]) && /tap to watch/.test(texts[3]) && /tap to answer it/.test(texts[7]));
  } else {
    ok('a desktop still says click and N', /press N here to send one/.test(texts[1]) && /Click to open it; N starts a task here/.test(texts[1])
      && /Click it for the overview/.test(texts[0]) && /click to watch/.test(texts[3]) && /Hover anything/.test(greet));
  }
"""

# the hull the plate layout keeps off must hold every painted pixel of its craft, at every heading and every scale
HULL_DRIVER = r"""
  const made = [];
  const real = document.createElement;
  document.createElement = () => { const art = { width: 0, height: 0, rects: [], getContext() { return { set fillStyle(v) {}, fillRect: (x, y, w, h) => art.rects.push([x, y, w, h]) }; } }; made.push(art); return art; };
  let n = 0;
  const reach = (name, cell, dir) => {
    n += 1; const pal = hullPal('#' + (100000 + n), '#' + (200000 + n));           // a palette no cached craft has
    made.length = 0; spriteArt(name, pal, dir, 16, cell);
    const art = made[made.length - 1], c = art.width / 2;
    return Math.max(...art.rects.map(([x, y, w, h]) => Math.hypot(Math.max(Math.abs(x - c), Math.abs(x + w - c)), Math.max(Math.abs(y - c), Math.abs(y + h - c)))));
  };
  const crafts = [['carrier', SPRITE_CELL], ['pilot', PILOT_CELL], ['pilot', CREW_CELL], ['fighter', SPRITE_CELL], ['raider', 2], ['cargo', SPRITE_CELL], ['gate', SPRITE_CELL]];
  for (const [name, cell] of crafts) {
    let worst = 0;
    for (let dir = 0; dir < 16; dir++) worst = Math.max(worst, reach(name, cell, dir));
    const hull = craftHull(name, cell, 1);
    if (hull < worst) out.push('  ' + name + '/' + cell + ': hull ' + hull.toFixed(1) + ' < painted reach ' + worst.toFixed(1));
    ok(name + ' (cell ' + cell + '): the hull holds every painted pixel at all 16 headings', hull >= worst);
    ok('...and is not a bigger circle than the grid needs (within 2 cells)', hull <= worst + 2 * cell + 2);
    ok('...and scales with the craft', Math.abs(craftHull(name, cell, 2) - 2 * hull) < 1e-9);
  }
  document.createElement = real;
  ok('the carrier is no longer the 46 px circle its nose reached out of', craftHull('carrier', SPRITE_CELL, 1) > 55);
  // the carrier on a map of big folders (the owner's: planets to r 272): about half the biggest planet across, so the size
  // order star > big planets > carrier > small planets > crew holds; on a map of small planets the craft scale alone
  const radii = MOONS.map((m) => m.r);
  MOONS.forEach((m, i) => { m.r = i ? 45 : 272; });
  const kBig = carrierScale(), share = craftHull('carrier', SPRITE_CELL, kBig) / 272;
  MOONS.forEach((m) => { m.r = 30; });
  const kSmall = carrierScale();
  MOONS.forEach((m, i) => { m.r = radii[i]; });
  ok('the carrier: half the biggest planet across on a big map, bigger than a small planet', share >= 0.4 && share <= 0.5 && craftHull('carrier', SPRITE_CELL, kBig) > 45 * 1.5);
  ok('the carrier: the craft scale alone on a map of small planets', kSmall === craftScale(CARRIER_BASE));
  // picking covers the hull as drawn: on a map of big folders (the carrier scaled up) a point on its wing is the carrier,
  // not the space behind it (the pick radius was the craft scale's 52 while the hull drew ~255 u across)
  // (the fixture has no hub seat: one is docked for the test, far from every other unit, and taken away after)
  const fake = { uid: 'seat:hub-test', kind: 'seat', name: 'hub', remote: false, status: 'LIVE', gx: 1000, gy: 1000, hp: 100 };
  units.push(fake);
  const mom = mothership();
  ok('the carrier to pick is the docked hub seat', mom === fake);
  if (mom) {
    MOONS.forEach((m, i) => { m.r = i ? 45 : 272; });
    const k = carrierScale(), hull = craftHull('carrier', SPRITE_CELL, k), cx = mom.gx * TILE, cy = mom.gy * TILE;
    ok('the carrier scaled up for the test', k >= 1.5 && hull > 100);
    ok('the pick radius holds the drawn hull', hitRadius(mom) >= hull);
    const wing = [[cx + hull * 0.9, cy], [cx - hull * 0.9, cy], [cx, cy + hull * 0.9], [cx, cy - hull * 0.9]].map(([x, y]) => pickAt(x, y, 0));
    ok('a wing point picks the carrier (or a craft docked on it), never nothing', wing.every((u) => u && (u === mom || isShip(u))) && wing.includes(mom));
    ok('past the hull: not the carrier', pickAt(cx + hull * 1.5 + 60, cy + hull * 1.5 + 60, 0) !== mom);
    MOONS.forEach((m, i) => { m.r = radii[i]; });
  }
  units.splice(units.indexOf(fake), 1);
  // the entry's launch (PAGE3D.move) ends a U-turn or a barrel roll anchored to the old place: fleetTick rewrote px, py from
  // the U-turn's anchor every frame, undoing the launch and moving the 2D camera's centre
  const now = performance.now(), p0 = { x: px, y: py }, c0 = { x: camOff.x, y: camOff.y };
  FL.uturn = { t0: now, h0: 0, x: px, y: py, v: 100, R: 50, side: 1 }; FL.roll = { t0: now, dir: 1, lx: 0, ly: 1 };
  PAGE3D.move(p0.x + 300, p0.y - 200, 1.2);
  ok('a launch ends the U-turn and the barrel roll', FL.uturn === null && FL.roll === null && px === p0.x + 300 && py === p0.y - 200 && FL.hdg === 1.2);
  ok('...and keeps the 2D camera centre', Math.abs(camOff.x - (c0.x - 300)) < 1e-9 && Math.abs(camOff.y - (c0.y + 200)) < 1e-9);
  px = p0.x; py = p0.y; camOff.x = c0.x; camOff.y = c0.y;
"""

# a path as the composer's chip and the cards name it
PATH_DRIVER = r"""
  W.root_path = '/srv/team/code';
  ok('below the root: the part after it', shortPath('/srv/team/code/api/v2') === 'api/v2');
  ok('the root itself: its folder, never the absolute path', shortPath('/srv/team/code') === 'code');
  W.root_path = '/srv/team/code/';
  ok('...with a trailing slash too', shortPath('/srv/team/code/') === 'code');
  W.root_path = '/srv/team/code';
  ok('outside the root: as it is', shortPath('/srv/other') === '/srv/other' && shortPath('/srv/team/codes') === '/srv/team/codes');
  ok('nothing: nothing', shortPath('') === '' && shortPath(null) === '');
  W.root_path = '';
  ok('no root: as it is', shortPath('/a/b') === '/a/b');
"""

# the first load: a desktop or a tablet with no stored zoom fits the world, a stored zoom is kept exactly
FIT_DRIVER = r"""
  const inside = (z) => MOONS.every((m) => Math.abs(m.x - px) * z + (m.r || 0) * z <= VW / 2 && Math.abs(m.y - py) * z + (m.r || 0) * z <= VH / 2);
  if (EXPECT === 'fit') {
    ok('the page measured the stage', VW === 1600 && VH === 900);
    ok('no zoom stored: the world is fitted, under 1, over the floor', ZOOM === fitZoom(VW, VH) && ZOOM < 1 && ZOOM >= 0.2);
    ok('...and every planet is inside the stage at that zoom', inside(ZOOM));
    ok('...while 1 would leave planets outside', !inside(1));
    ok('the fit is not written as a choice', [null, '0.72'].includes(sessionStorage.getItem('zoom')));
    ok('the reset of the ring goes to the same fit', homeZoom() === ZOOM);
  } else if (EXPECT === 'stored') {
    ok('a stored zoom is kept exactly', ZOOM === 0.7 && sessionStorage.getItem('zoom') === '0.7');
    ok('the ring still resets to the fit', homeZoom() === fitZoom(VW, VH) && homeZoom() !== 0.7);
  } else if (EXPECT === 'unmeasured') {
    ok('a stage that is not measured keeps 1', ZOOM === 1);
  } else if (EXPECT === 'phone') {
    ok('a phone fits the whole world too (fit-all; it kept a fixed 0.22)', homeZoom() <= fitZoom(VW, VH) && (ZOOM === fitZoom(VW, VH) || ZOOM === homeZoom()));
  }
  // key 0 resets to the same home as the ring's long press, not to 1
  const key0 = () => { const e = { key: '0', repeat: false, ctrlKey: false, metaKey: false, altKey: false, shiftKey: false,
    preventDefault() {}, stopPropagation() {}, getModifierState() { return false; }, target: document.activeElement || { blur() {} } };
    (__WL.keydown || []).forEach((f) => f(e)); };
  setZoom(0.5); key0();
  ok('key 0 resets the zoom to the fit (home), whatever it was', ZOOM === homeZoom());
  setZoom(2); key0();
  ok('...from a zoom past the fit too', ZOOM === homeZoom());
  if (EXPECT === 'fit') ok('...and the whole world is on the stage again', inside(ZOOM) && ZOOM < 1);
"""

# the home view of a desktop or a tablet: the cards the page lays over the stage are read when the map is up, and no planet
# disc lies under one (CARDS: id -> [x, y, w, h] in stage pixels, as a browser lays them over a stage of this size)
HOME_DRIVER = r"""
  QUIET = true;                                         // no minimap: the cards below are all there is
  const rectOf = (x, y, w, h) => ({ left: x, top: y, right: x + w, bottom: y + h, width: w, height: h });
  const card = (x, y, w, h) => { const r = rectOf(x, y, w, h);
    return new Proxy(universal(), { get: (t, k) => (k === 'getBoundingClientRect' ? () => r : t[k]), set: () => true }); };
  PLATE_OVERLAYS.concat(PLATE_OVERLAYS_TOUCH, ['dash', 'side']).forEach((id) => { elCache[id] = card(0, 0, 0, 0); });
  Object.keys(CARDS).forEach((id) => { elCache[id] = card(...CARDS[id]); });
  const rects = Object.keys(CARDS).map((id) => ({ id: id, x: CARDS[id][0], y: CARDS[id][1], w: CARDS[id][2], h: CARDS[id][3] }));
  // a disc under a card: the nearest point of the card is closer than the radius
  const discs = () => MOONS.map((m) => ({ label: m.label, x: VW / 2 + (m.x - px - camOff.x) * ZOOM, y: VH / 2 + (m.y - py - camOff.y) * ZOOM, r: (m.r || 0) * ZOOM }));
  const under = () => discs().filter((d) => rects.some((b) => { const dx = Math.max(b.x - d.x, 0, d.x - b.x - b.w), dy = Math.max(b.y - d.y, 0, d.y - b.y - b.h);
    return dx * dx + dy * dy < d.r * d.r; })).map((d) => d.label);
  const outside = () => discs().filter((d) => d.x - d.r < 0 || d.y - d.r < 0 || d.x + d.r > VW || d.y + d.r > VH).map((d) => d.label);
  const key0 = () => { const e = { key: '0', repeat: false, ctrlKey: false, metaKey: false, altKey: false, shiftKey: false,
    preventDefault() {}, stopPropagation() {}, getModifierState() { return false; }, target: document.activeElement || { blur() {} } };
    (__WL.keydown || []).forEach((f) => f(e)); };
  ok('the page measured the stage the cards sit on', VW === STAGE_W && VH === STAGE_H);
  ok('the world is a ring of planets', MOONS.length >= 12);
  const plain = fitZoom(VW, VH);
  if (EXPECT === 'fresh') {
    // the placeholder of a first load, on Today: the map is not up, so nothing is measured yet
    setView('today'); ZOOM = plain; camOff.x = 0; camOff.y = 0; HOME.on = true; HOME.laid = false; homeNote();
    ok('with Today up the first-load view is the plain fit, centred', ZOOM === plain && camOff.x === 0 && camOff.y === 0 && HOME.laid === false);
    ok('...and the planets are under the cards there (this is what the home view is for)', under().length > 0);
    setView('map');
    ok('the map coming up makes the home view', HOME.laid === true && HOME.on === true);
    ok('the zoom stays inside 0.2 to 1 and never passes the plain fit', ZOOM >= 0.2 && ZOOM <= plain && Number.isFinite(camOff.x) && Number.isFinite(camOff.y));
    ok('no planet disc lies under a card: ' + under().join(','), under().length === 0);
    ok('every planet disc is on the stage: ' + outside().join(','), outside().length === 0);
    const A = __fitAudit();
    ok('the audit reads the same cards and finds the same: nothing under, nothing outside',
      A.under.length === 0 && A.outside.length === 0 && rects.every((b) => A.covers.some((c) => c.id === b.id && c.x === b.x && c.y === b.y && c.w === b.w && c.h === b.h)));
    ok('the audit names every planet', A.planets.length === MOONS.length && A.zoom === ZOOM);
    ok('the first load is no choice made: nothing stored', [null, '0.72'].includes(sessionStorage.getItem('zoom')));
    draw(1000);
    ok('the frame draws the planets where the audit says (camera offset in the frame)',
      MOONS.every((m, i) => Math.abs((m.x - camx) * ZOOM - discs()[i].x) <= 2 * ZOOM + 1e-6 && Math.abs((m.y - camy) * ZOOM - discs()[i].y) <= 2 * ZOOM + 1e-6));
    // the same view again while nothing was touched (the rails open, a bigger stage): it is made again
    HOME.laid = false; homeSettle();
    ok('while nothing was touched the view can be made again', HOME.laid === true && under().length === 0);
    // key 0 and the ring's long press make this view, from any zoom and any pan
    setZoom(2); camOff.x = 300; camOff.y = -200; key0();
    const h = homeView();
    ok('key 0 goes to the home view: its zoom and its offset', ZOOM === h.zoom && camOff.x === h.ox && camOff.y === h.oy);
    ok('...and no planet is under a card again: ' + under().join(','), under().length === 0 && outside().length === 0);
    setZoom(2); camOff.x = 300; camOff.y = -200; zoomHome();
    ok('the zoom column\'s long press makes the same view', ZOOM === h.zoom && camOff.x === h.ox && camOff.y === h.oy && homeZoom() === h.zoom);
    // a zoom or a pan of the visitor's is theirs: the map coming up again leaves it alone
    setZoom(0.5); camOff.x = 40; homeSettle(); setView('today'); setView('map');
    ok('a zoom the visitor chose is kept, and so is the pan', ZOOM === 0.5 && camOff.x === 40 && HOME.on === false);
  } else if (EXPECT === 'stored') {
    setView('today'); setView('map'); HOME.laid = false; homeSettle();
    ok('a stored zoom is kept exactly, with the ship in the middle', ZOOM === 0.7 && camOff.x === 0 && camOff.y === 0 && HOME.on === false);
    key0();
    const h = homeView();
    ok('...and key 0 still goes to the home view', ZOOM === h.zoom && camOff.x === h.ox && camOff.y === h.oy && ZOOM !== 0.7 && under().length === 0);
  } else if (EXPECT === 'hopeless') {
    // two cards that between them cover the whole stage: nothing clears; the fewest planets under a card, never a broken view
    const h = homeView();
    ok('nothing clears: still a view inside 0.2 to 1 with finite offsets', h.zoom >= 0.2 && h.zoom <= plain && Number.isFinite(h.ox) && Number.isFinite(h.oy));
    ok('...and the cards are still named', homeCovers().length === Object.keys(CARDS).length);
  }
"""

class HardeningWorldTests(unittest.TestCase):
    """H2: what the renderer embeds. A name that is not UTF-8 never stops the page; the process list
    of other agents is read only when the owner turned agent_processes on."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def bad_names_root(self):
        root = Path(self.tmp.name, "work")
        (root / "Alpha").mkdir(parents=True)
        (root / "Beta").mkdir()
        (root / "Beta" / "notes.md").write_text("fixture\n", encoding="utf-8")
        base = os.fsencode(root / "Alpha")
        bad_dir = base + b"/dir\xff\xfeX"
        os.mkdir(bad_dir)
        for name in (bad_dir + b"/in\xc3.md", base + b"/bad\xe9name.md", base + b"/ok.md"):
            with open(name, "wb") as handle:
                handle.write(b"hello\n")
        return root

    def test_a_name_that_is_not_utf8_never_stops_the_page(self):
        self.bad_names_root()
        proc = render_fixture(self.tmp.name)
        self.assertEqual(proc.returncode, 0, msg=proc.stderr[-2000:])
        page = Path(self.tmp.name, "page.html")
        html = page.read_bytes().decode("utf-8")        # the page is valid UTF-8 whole
        self.assertIn("ok.md", html)
        self.assertNotRegex(html, "[\ud800-\udfff]")
        self.assertIn("�", html, "the unreadable bytes read as the replacement character")
        self.assertIn("bad�name.md", html)
        proc = render_fixture(self.tmp.name, "--json")
        self.assertEqual(proc.returncode, 0, msg=proc.stderr[-2000:])
        self.assertNotRegex(proc.stdout, r"\\ud[89a-f][0-9a-f]{2}")
        self.assertIn("bad\ufffdname.md", json.dumps(json.loads(proc.stdout), ensure_ascii=False))

    def test_the_loop_render_survives_such_a_name_too(self):
        self.bad_names_root()
        for _ in range(2):
            proc = render_fixture(self.tmp.name, "--loop-render")
            self.assertEqual(proc.returncode, 0, msg=proc.stderr[-2000:])
        Path(self.tmp.name, "page.html").read_bytes().decode("utf-8")

    def test_the_page_is_seeded_with_every_open_thread_and_the_newest_closed_ones(self):
        work = Path(self.tmp.name, "work")
        (work / "Alpha").mkdir(parents=True)
        tasks = Path(self.tmp.name, "tasks")

        def thread(tid, closed):
            (tasks / tid).mkdir(parents=True)
            (tasks / tid / "task.json").write_text(json.dumps({
                "id": tid, "title": "t " + tid, "text": "x", "runner": "claude",
                "target": str(work / "Alpha"), "cwd": str(work / "Alpha"), "mode": "inplace"}), encoding="utf-8")
            events = [{"ev": "run", "n": 1, "at": "2026-10-09T01:00:00"}, {"ev": "exit", "n": 1, "rc": 1, "at": "2026-10-09T01:01:00"}]
            if closed:
                events.append({"ev": "done", "at": "2026-10-09T01:02:00"})
            (tasks / tid / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")

        old_open = ["20260101-010000-t-00000%d" % n for n in (1, 2)]
        for tid in old_open:
            thread(tid, closed=False)
        for n in range(120):
            thread("20261009-02%04d-t-%06x" % (n, n), closed=True)
        proc = render_fixture(self.tmp.name, "--json")
        self.assertEqual(proc.returncode, 0, msg=proc.stderr[-2000:])
        seeded = [t["id"] for t in json.loads(proc.stdout)["agent_tasks"]]
        for tid in old_open:
            self.assertIn(tid, seeded, "an old thread that is still open stays on the page")
        closed = [i for i in seeded if i not in old_open]
        self.assertEqual(len(closed), 80, "...next to the newest closed ones")
        self.assertEqual(closed, sorted(closed, reverse=True)[:80])
        self.assertEqual(seeded, sorted(seeded, reverse=True), "newest first, as the store lists them")

    def fake_pgrep(self):
        """A pgrep that reports one agent process and leaves a mark when it is asked at all."""
        bindir = Path(self.tmp.name, "bin")
        bindir.mkdir()
        mark = Path(self.tmp.name, "pgrep.asked")
        script = bindir / "pgrep"
        script.write_text(f"#!/bin/sh\necho asked >> {mark}\necho '424242 codex exec do the secret thing'\n",
                          encoding="utf-8")
        script.chmod(0o755)
        Path(self.tmp.name, "work", "Alpha").mkdir(parents=True)
        return dict(PATH=f"{bindir}:{os.environ['PATH']}"), mark

    def test_other_agent_processes_are_not_collected_unless_the_owner_asked(self):
        env, mark = self.fake_pgrep()
        proc = render_fixture(self.tmp.name, "--json", env_extra=env)
        self.assertEqual(proc.returncode, 0, msg=proc.stderr[-2000:])
        self.assertEqual(json.loads(proc.stdout)["inflight"], [])
        self.assertFalse(mark.exists(), "the process table is not even asked")
        proc = render_fixture(self.tmp.name, "--json", env_extra=env, agent_processes=False)
        self.assertEqual(json.loads(proc.stdout)["inflight"], [])
        page = render_fixture(self.tmp.name, env_extra=env)
        self.assertEqual(page.returncode, 0, msg=page.stderr[-2000:])
        self.assertNotIn("424242", Path(self.tmp.name, "page.html").read_text(encoding="utf-8"))

    def test_with_agent_processes_on_the_in_flight_list_is_filled(self):
        env, mark = self.fake_pgrep()
        proc = render_fixture(self.tmp.name, "--json", env_extra=env, agent_processes=True)
        self.assertEqual(proc.returncode, 0, msg=proc.stderr[-2000:])
        flight = json.loads(proc.stdout)["inflight"]
        self.assertEqual([(j["pid"], j["who"]) for j in flight], [("424242", "codex")])
        self.assertTrue(mark.exists())


    def fake_ps(self):
        """A ps whose table holds an agent started with a private prompt next to a planet's own file,
        a claude and a threads process, and one plain job of the planet."""
        root = Path(self.tmp.name, "work")
        (root / "Alpha").mkdir(parents=True, exist_ok=True)
        planet = root / "Alpha"
        rows = [
            f"128470 1 900 python3 -c import time;time.sleep(60) {planet}/x.py codex exec secret prompt one",
            f"128471 1 800 {planet}/bin/claude -p secret prompt two {planet}/notes.md",
            f"128472 1 700 python3 {planet}/threads.py run secret prompt three",
            f"128473 1 600 python3 {planet}/job.py --nightly",
        ]
        bindir = Path(self.tmp.name, "psbin")
        bindir.mkdir(exist_ok=True)
        script = bindir / "ps"
        script.write_text("#!/bin/sh\ncase \"$1\" in -eo) cat <<'EOF'\n" + "\n".join(rows)
                          + "\nEOF\n;; esac\n", encoding="utf-8")
        script.chmod(0o755)
        return dict(PATH=f"{bindir}:{os.environ['PATH']}")

    def test_a_process_row_that_is_an_agent_carries_no_pid_or_argv_unless_the_owner_asked(self):
        env = self.fake_ps()
        proc = render_fixture(self.tmp.name, "--json", env_extra=env, processes=True, agent_processes=False)
        self.assertEqual(proc.returncode, 0, msg=proc.stderr[-2000:])
        procs = [c for c in json.loads(proc.stdout)["chars"] if c.get("kind") == "proc"]
        self.assertEqual([c["pids"] for c in procs], [[128473]], "the planet's own job is still drawn")
        for leak in ("secret prompt", "128470", "128471", "128472", "codex exec"):
            self.assertNotIn(leak, proc.stdout)
        render_fixture(self.tmp.name, env_extra=env, processes=True)       # agent_processes defaults off
        page = Path(self.tmp.name, "page.html").read_text(encoding="utf-8")
        for leak in ("secret prompt", "128470", "128471", "128472"):
            self.assertNotIn(leak, page)
        self.assertIn("job.py --nightly", page)

    def test_a_process_row_is_still_drawn_for_an_agent_when_the_owner_turned_agent_processes_on(self):
        env = self.fake_ps()
        proc = render_fixture(self.tmp.name, "--json", env_extra=env, processes=True, agent_processes=True)
        self.assertEqual(proc.returncode, 0, msg=proc.stderr[-2000:])
        pids = {p for c in json.loads(proc.stdout)["chars"] if c.get("kind") == "proc" for p in c["pids"]}
        self.assertEqual(pids, {128470, 128471, 128472, 128473})


class HardeningPageTests(unittest.TestCase):
    """H2: the page script in the stub DOM, one driver per fix."""

    @classmethod
    def setUpClass(cls):
        if not shutil.which("node"):
            raise unittest.SkipTest("node not installed")
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name, "work")
        for name in ("Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta", "Eta", "Theta", "Iota", "Kappa", "Lambda", "Mu"):
            (root / name).mkdir(parents=True)
            (root / name / "notes.md").write_text("fixture\n", encoding="utf-8")
        proc = render_fixture(cls.tmp.name)
        if proc.returncode:
            raise AssertionError(proc.stderr[-2000:])
        cls.js = Path(cls.tmp.name, "page.js")
        cls.src = Path(cls.tmp.name, "page.html").read_text(encoding="utf-8")
        page_js(Path(cls.tmp.name, "page.html"), cls.js)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def drive(self, body, location=None, env_extra=None):
        run_page_driver(self, self.js, body, location, env_extra)

    def test_keys_leave_browser_shortcuts_to_the_browser_and_focused_controls_to_themselves(self):
        self.drive(KEYS_DRIVER)

    def test_the_first_load_fits_the_world_unless_a_zoom_is_stored(self):
        def fit(expect, **env):
            self.drive("const EXPECT = '" + expect + "';" + FIT_DRIVER, env_extra=env)
        fit("fit", HARNESS_STAGE="1600x900")
        fit("fit", HARNESS_STAGE="1600x900", HARNESS_SESSION=json.dumps({"zoom": "0.72"}))   # the old default is no choice
        fit("stored", HARNESS_STAGE="1600x900", HARNESS_SESSION=json.dumps({"zoom": "0.7"}))
        fit("unmeasured")
        fit("phone", HARNESS_STAGE="390x800", HARNESS_MOBILE="1")

    def test_the_home_view_keeps_every_planet_out_from_under_the_cards_over_the_stage(self):
        # a 1600x900 desktop with the console rail (a 1290x900 stage) and a 1366x768 one (1056x768); the cards as a browser lays them
        sizes = {
            "1290x900": {"hud": [12, 12, 282, 145], "comms": [12, 172, 400, 32], "mate": [12, 622, 480, 234], "ticker": [0, 868, 1290, 32]},
            "1056x768": {"hud": [12, 12, 282, 145], "comms": [12, 172, 400, 32], "mate": [12, 490, 480, 234], "ticker": [0, 736, 1056, 32]},
        }
        for stage, cards in sizes.items():
            w, h = stage.split("x")
            head = f"const EXPECT = '%s', STAGE_W = {w}, STAGE_H = {h}, CARDS = {json.dumps(cards)};"
            with self.subTest(stage=stage):
                self.drive(head % "fresh" + HOME_DRIVER, env_extra={"HARNESS_STAGE": stage})
                self.drive(head % "stored" + HOME_DRIVER, env_extra={"HARNESS_STAGE": stage, "HARNESS_SESSION": json.dumps({"zoom": "0.7"})})
        # no way to clear them all: the fewest under a card, and the view is still sound
        w, h = 1290, 900
        halves = {"hud": [0, 0, 645, 900], "comms": [645, 0, 645, 900]}
        self.drive(f"const EXPECT = 'hopeless', STAGE_W = {w}, STAGE_H = {h}, CARDS = {json.dumps(halves)};" + HOME_DRIVER,
                   env_extra={"HARNESS_STAGE": f"{w}x{h}"})

    def test_the_wingmates_tips_teach_keys_on_a_desktop_and_touch_on_a_phone(self):
        self.drive(TIPS_DRIVER)
        self.drive(TIPS_DRIVER, env_extra={"HARNESS_MOBILE": "1"})
        # a touch tablet is html.t (a coarse pointer, wider than a phone): touch words, but not the phone's tab bar and + button
        self.drive(TABLET_PREFIX + TIPS_DRIVER)

    def test_the_workspace_root_is_named_by_its_folder_in_the_composer_chip(self):
        self.drive(PATH_DRIVER)

    def test_a_crafts_hull_in_the_plate_layout_holds_all_of_its_painted_pixels(self):
        self.drive(HULL_DRIVER)
        for hard_coded in ("hr=46*craftScale", "pr=34*craftScale", "hr=26*craftScale", "hr=16;", "hr=18;"):
            self.assertNotIn(hard_coded, RENDER_SRC, "a hull radius is read from the craft's grid, not typed beside the drawing")

    def test_what_the_wingmate_says_about_a_thing_names_the_input_the_device_has(self):
        # a phone has no mouse and no N: the words under the Scout tab said "press N here" and "Click to open it"
        self.drive(EXPLAIN_DRIVER)
        self.drive(EXPLAIN_DRIVER, env_extra={"HARNESS_MOBILE": "1"})
        self.drive(TABLET_PREFIX + EXPLAIN_DRIVER)       # ...and a tablet's touch screen has no mouse and no N either


class NoSqliteTests(unittest.TestCase):
    """A Python built without _sqlite3 (some minimal builds) still draws the map: the renderer
    imports ships.py, which reads the T3 database and nothing else with sqlite3."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.blocked = Path(self.tmp.name, "no-sqlite")
        (self.blocked / "sqlite3").mkdir(parents=True)
        (self.blocked / "sqlite3" / "__init__.py").write_text(
            "raise ImportError(\"No module named '_sqlite3'\")\n", encoding="utf-8")
        self.extra = {"PYTHONPATH": str(self.blocked)}

    def test_the_blocker_blocks(self):
        proc = subprocess.run([sys.executable, "-c", "import sqlite3"], cwd=self.tmp.name,
                              env=dict(os.environ, **self.extra), capture_output=True, text=True)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("_sqlite3", proc.stderr)

    def test_the_renderer_draws_the_map_without_sqlite3(self):
        t3 = Path(self.tmp.name, "state.sqlite")
        t3.write_bytes(b"")
        proc = render_fixture(self.tmp.name, "--json", env_extra=self.extra, t3_state=str(t3))
        self.assertEqual(proc.returncode, 0, msg=proc.stderr[-2000:])
        self.assertEqual(json.loads(proc.stdout)["name"], "FIXTURE")
        proc = render_fixture(self.tmp.name, env_extra=self.extra)
        self.assertEqual(proc.returncode, 0, msg=proc.stderr[-2000:])
        self.assertTrue(Path(self.tmp.name, "page.html").is_file())

    def test_a_configured_t3_database_is_reported_unavailable_not_fatal(self):
        t3 = Path(self.tmp.name, "state.sqlite")
        t3.write_bytes(b"")
        code = ("import json, sys, ships\n"
                "print(json.dumps(ships.t3_ships(sys.argv[1], 0)))\n")
        proc = subprocess.run([sys.executable, "-c", code, str(t3)], cwd=ROOT,
                              env=dict(os.environ, **self.extra), capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, msg=proc.stderr[-2000:])
        chars, state = json.loads(proc.stdout)
        self.assertEqual((chars, state["status"], state["source"]), ([], "ERROR", "t3"))
        self.assertIn("sqlite3", state["message"])


# ---------------------------------------------------------------------------------------------
# The sky round (L10): sky mode, the widened radio, the ledger and the night run on Today, the mission-complete
# banner. The page half runs in the stub DOM against a fixture world whose W.sky each driver writes in the shape
# sky.py promises (sky.py's own tests cover how the rollup is built).
SKY_HEAD = r"""
  VIEW = 'map';
  const areas = W.chars.filter((c) => c.kind === 'area' && c.path), area = areas[0], other = areas[1];
  const C = (id, source, state, o) => Object.assign({ id: id, source: source, state: state, title: 'contact ' + id, since: null,
    age_days: 0, tier: 'fresh', planet_uid: null, open: null, voice: 'voice ' + id }, o || {});
  const SKY0 = (contacts, o) => Object.assign({ contacts: contacts, hyper: { n: 0, by_source: { t3: 0, task: 0, seat: 0 } },
    parked: 0, settled_today: 0, derelicts: 0, by_source: {}, oldest_days: null }, o || {});
  const world = (o) => Object.assign({}, W, { chars: W.chars.filter((c) => c.kind !== 'ship' && c.kind !== 'seat').concat(o.chars || []),
    quests: W.quests || [], agent_tasks: o.tasks || [], sky: o.sky, today: o.today || W.today });
"""

SKY_MODE_DRIVER = SKY_HEAD + r"""
  ok('sky mode is on by default and the fixture world has a sky', SKY === true && skyOk() && skyOn());
  W.agent_tasks = [T('w1', 'running', { target: area.path }), T('r1', 'review', { target: area.path })];
  W.chars = W.chars.filter((c) => c.kind !== 'ship' && c.kind !== 'seat').concat([
    { uid: 'ship:t3:th1', kind: 'ship', source: 't3', status: 'WORKING', name: 'proj · thread one', cwd: area.path, t3: { state: 'working' } },
    { uid: 'seat:hub', kind: 'seat', name: 'hub', status: 'WORKING', cwd: '' }]);
  W.today = Object.assign({}, W.today, { ledger: [{ id: 'L-1', lane: 'zz-' + other.name, title: 'a parked call', opened: '2026-08-22', age_days: 50 }] });
  W.sky = SKY0([C('task:r1', 'task', 'review', { planet_uid: area.uid, tier: 'aging', age_days: 3 }),
    C('ledger:L-1', 'ledger', 'blocked', { tier: 'raider', age_days: 50 }), C('cron:cron:x', 'cron', 'stale', { planet_uid: area.uid }),
    C('fleet:T-9', 'fleet', 'blocked', { tier: 'raider', age_days: 9 })],
    { parked: 2, settled_today: 1, oldest_days: 50, hyper: { n: 3, by_source: { t3: 1, task: 1, seat: 1 } } });
  place();
  let m = MOONS.find((x) => x.uid === area.uid);
  ok('a contact with a planet is a foe there, with its tier', m.foes.filter((f) => f.kind === 'contact').map((f) => f.uid + ':' + f.tier).join() === 'task:r1:aging,cron:cron:x:fresh');
  ok('a ledger row with no planet named finds one by its lane word', MOONS.find((x) => x.uid === other.uid).foes.some((f) => f.uid === 'ledger:L-1' && f.tier === 'raider'));
  ok('a contact with no planet circles the star', SKY_STAR.enemies === 1 && SKY_STAR.foes[0].uid === 'fleet:T-9');
  ok('the working thread still counts as the planet’s defender', m.allies === 1);
  const U = (uid) => units.find((u) => u.uid === uid);
  ok('sky mode hides the working thread and the working T3 ship', skyHidden(U('task:w1')) && skyHidden(U('ship:t3:th1')));
  ok('...and the working tmux seat, the carrier too: running work is in warp, as sky.py counts it', skyHidden(U('seat:hub')));
  ok('...not a thread in review', !skyHidden(U('task:r1')));
  const t3s = (st) => ({ uid: 'ship:t3:' + st, kind: 'ship', source: 't3', status: 'IDLE', cwd: area.path, t3: { state: st } });
  ok('a T3 thread settled or snoozed leaves the sky (done or parked), and is not in warp', skyHidden(t3s('settled')) && skyHidden(t3s('snoozed'))
    && !warpHidden(t3s('settled')) && !warpHidden(t3s('snoozed')));
  ok('...one that finished unsettled or waits stays drawn', !skyHidden(t3s('ready')) && !skyHidden(t3s('waiting')) && !skyHidden(t3s('failed')));
  const drawn = []; const realSprite = sprite; sprite = (u, ...a) => { drawn.push(u.uid); return realSprite(u, ...a); };
  const lockedBy = []; const realLock = fxLockFoe; fxLockFoe = (f, ...a) => { lockedBy.push(f.uid); return realLock(f, ...a); };
  draw(3000);
  ok('a drawn frame leaves the working thread, the T3 ship and the working seat out', !drawn.includes('task:w1') && !drawn.includes('ship:t3:th1')
    && !drawn.includes('seat:hub') && drawn.includes('task:r1'));
  const wt = U('task:w1'), hit = pickAt(wt.gx * TILE, wt.gy * TILE);
  ok('...and a click where one sits does not pick it', !hit || hit.uid !== 'task:w1');
  // every look and the mono palette draw contacts of each tier, the star's ring included, and the hovered one takes the lock
  for (const name of THEME_NAMES.concat(['gb'])) {
    try { if (name === 'gb') GB = true; else { GB = false; applyTheme(name, false); }
      const b = fills; draw(3100); drawBattle(m, 3150); SKY_STAR.x = LY.star.x; SKY_STAR.y = LY.star.y; SKY_STAR.r = LY.star.r; drawBattle(SKY_STAR, 3150);
      ok(name + ': the contacts draw', fills > b || VEC);
    } catch (e) { ok(name + ': the contacts draw', false); out.push('  ' + (e && e.stack)); }
  }
  GB = false; applyTheme(THEME_CFG, false);
  { openUnit = unitByUid(m.uid); VIEW = 'map'; draw(2050); draw(2100);   // the open planet's orbit: hover a drawn contact
    const f0 = __mapAudit().foes.concat(__mapAudit().waiting).find((f) => f.uid === m.foes[0].uid);   // a review waits on the same orbit
    mouseW = f0 ? { x: f0.wx, y: f0.wy, sx: 0, sy: 0 } : null;
    FX.lock = null; drawBattle(MOONS.find((x) => x.uid === m.uid), 2100); mouseW = null; openUnit = null;   // the planet as this look laid it out
    ok('the hovered contact takes the lock-on and its plate carries the age and the title', !!f0 && !!FX.lock && FX.lock.uid === m.foes[0].uid && m.foes[0].name === '3 days · contact task:r1'); }   // the age in words (CM.ageWords), never '3d'
  sprite = realSprite; fxLockFoe = realLock;
  setSky(false); drawn.length = 0; sprite = (u, ...a) => { drawn.push(u.uid); return realSprite(u, ...a); }; draw(3200); sprite = realSprite;
  m = MOONS.find((x) => x.uid === area.uid);
  ok('Y (every unit) draws them again, and the planet keeps only its old problems', drawn.includes('task:w1') && drawn.includes('ship:t3:th1') && drawn.includes('seat:hub')
    && !m.foes.some((f) => f.kind === 'contact') && SKY_STAR.enemies === 0);
  setSky(true);
  // the strip and the boot line say the one count (sky.counts, W.counts; CM.countsOf is its twin in the page)
  W.counts = CM.countsOf(W.sky, W.agent_tasks, []);
  elCache.nums = cap(); stripTick(); const st = String(elCache.nums.innerHTML);
  const num = (k) => { const r = new RegExp('data-num="' + k + '"[^>]*><b>([0-9]+)</b>').exec(st); return r ? Number(r[1]) : -1; };
  ok('the strip: 1 needs you, 2 decisions, 1 problem, 3 working', num('need_you') === 1 && num('decisions') === 2 && num('problems') === 1 && num('working') === 3);
  ok('the boot line says the same count', bootLine() === '1 FINISHED TASK TO REVIEW · 2 DECISIONS · 1 PROBLEM · 3 WORKING');
  // working is the sky's own count of sessions plus the threads at work
  W.counts = CM.countsOf(Object.assign({}, W.sky, { hyper: { n: 4, by_source: { t3: 2, task: 1, seat: 1 } } }), W.agent_tasks, []);
  ok('working says sky.py\'s sessions and the working threads', bootLine().endsWith('4 WORKING'));
  W.counts = CM.countsOf(W.sky, W.agent_tasks, []);
  { // the review's case: one WORKING seat and nothing else at work: hyper 1, the seat out of sight and IN WARP 1
    const keep = { chars: W.chars, tasks: W.agent_tasks, sky: W.sky };
    W.chars = keep.chars.filter((c) => c.kind !== 'ship' && c.kind !== 'seat').concat([{ uid: 'seat:astra', kind: 'seat', name: 'astra', status: 'WORKING', cwd: area.path, command: 'claude' },
      { uid: 'seat:sh', kind: 'seat', name: 'sh', status: 'WORKING', cwd: area.path, command: 'bash', shell: true }]);
    W.agent_tasks = []; W.sky = SKY0([], { hyper: { n: 1, by_source: { t3: 0, task: 0, seat: 1 } } }); W.counts = CM.countsOf(W.sky, [], []); place();
    const s1 = U('seat:astra'); drawn.length = 0; sprite = (u, ...a) => { drawn.push(u.uid); return realSprite(u, ...a); }; draw(3300); sprite = realSprite;
    const h1 = pickAt(s1.gx * TILE, s1.gy * TILE);
    elCache.nums = cap(); stripTick(); const st1 = String(elCache.nums.innerHTML);
    ok('one working seat: not drawn, not picked, IN WARP 1 on the strip and the boot line', skyHidden(s1) && !drawn.includes('seat:astra') && (!h1 || h1.uid !== 'seat:astra')
      && /data-num="working"[^>]*><b>1<\/b>/.test(st1) && bootLine() === 'NOTHING WAITING ON YOU · 1 WORKING');
    ok('...a bare shell that reads as working is an empty berth, never in warp', !warpHidden(U('seat:sh')) && isBerth(U('seat:sh')));
    // a failed thread whose retry is due restarts within seconds: in warp (sky.py counts it in hyper), not a wreck on its planet
    W.agent_tasks = [T('rt', 'failed', { target: area.path, retry_due: true }), T('ft', 'failed', { target: area.path, retry_due: false })]; place();
    const mm = MOONS.find((x) => x.uid === area.uid);
    ok('a due retry is in warp and leaves no wreck; a plain failure stays a wreck', skyHidden(U('task:rt')) && !skyHidden(U('task:ft'))
      && !mm.foes.some((f) => f.uid === 'task:rt') && mm.foes.some((f) => f.uid === 'task:ft' && f.kind === 'wreck'));
    setSky(false); ok('...and the full view keeps the old wreck for it', MOONS.find((x) => x.uid === area.uid).foes.some((f) => f.uid === 'task:rt')); setSky(true);
    W.chars = keep.chars; W.agent_tasks = keep.tasks; W.sky = keep.sky; place();
  }
  // a problem the sky already carries is drawn once, as the contact; one it does not carry keeps its old craft
  W.tasks = { items: [{ id: 'b7', area: area.name, finding: 'a board item', who: 'owner' }] };
  W.sky.contacts.push(C('board:b7', 'board', 'blocked'));
  place(); m = MOONS.find((x) => x.uid === area.uid);
  ok('a board item the sky carries is one foe (the contact, on the item’s planet)', m.foes.filter((f) => f.uid === 'board:b7' || f.uid === 'quest:b7').length === 1 && m.foes.some((f) => f.uid === 'board:b7'));
  W.sky.contacts.pop(); place();
  ok('one the sky does not carry keeps its old craft', MOONS.find((x) => x.uid === area.uid).foes.some((f) => f.uid === 'quest:b7' && f.kind === 'item'));
  W.tasks = { items: [] };
  // a stale cron line: its uid already reads "cron:<n>", so sky.py's contact id is that uid (or the uid behind "cron:" again);
  // either way the line is one foe, the contact
  W.chars = W.chars.concat([{ uid: 'cron:7', kind: 'cron', name: 'nightly', tool: 'nightly', area: area.uid, verdict: 'STALE', gx: 1, gy: 1 }]);
  place(); ok('a stale cron line the sky does not carry keeps its old craft', MOONS.find((x) => x.uid === area.uid).foes.some((f) => f.kind === 'cron' && f.uid === 'cron:7'));
  for (const cid of ['cron:7', 'cron:cron:7']) {
    W.sky.contacts.push(C(cid, 'cron', 'stale', { planet_uid: area.uid })); place(); m = MOONS.find((x) => x.uid === area.uid);
    ok('a stale cron line the sky carries as ' + cid + ' is one foe, the contact', m.foes.filter((f) => f.uid === 'cron:7' || f.uid === cid).length === 1
      && m.foes.some((f) => f.kind === 'contact' && f.uid === cid));
    W.sky.contacts.pop(); }
  W.chars = W.chars.filter((c) => c.uid !== 'cron:7'); place();
  // the card lists a contact with an open action, not a send
  elCache.pbody = cap(); renderCard(units.find((u) => u.uid === area.uid)); const card = String(elCache.pbody.innerHTML);
  { const row = card.split('<div class="row ').find((r) => /contact task:r1/i.test(r)) || '';   // the comps' card: its row has verbs
    ok('the planet card offers to open a contact, never to send an agent on it', (row.includes('data-copen=') || row.includes('data-act="task"')) && !row.includes('data-cfix=') && row.includes('to review')); }
  // snap: J walks the contacts in the sky's order, the star's included
  const was = toast; const said = []; toast = (t) => said.push(t);
  const c1 = snapContact(1), c2 = snapContact(1), cl = snapContact(-1);
  ok('J snaps to the first contact, then the next; Shift+J goes back', c1.id === 'task:r1' && c2.id === 'ledger:L-1' && cl.id === 'task:r1' && said[0].indexOf('1 of 4 · contact task:r1 · to review') === 0);
  pageVoice('next'); ok('the voice command next does the same', SKY_FOCUS_ID === 'ledger:L-1');
  pageVoice({ kind: 'briefing' }); ok('briefing opens Today', VIEW === 'today'); VIEW = 'map';
  toast = was;
  // no contact: EMPTY SKY; a sky that could not be built: the map as it was, and the error said
  W.sky = SKY0([]); W.counts = CM.countsOf(W.sky, W.agent_tasks, []); place(); stripTick();
  ok('no contact: zero need you on the strip, the boot line says nothing waits', /data-num="need_you"[^>]*><b>0</.test(String(elCache.nums.innerHTML)) && bootLine().indexOf('NOTHING WAITING ON YOU · ') === 0);
  W.sky = { contacts: [], error: 'sky could not be built: ValueError: x' }; place(); stripTick();
  ok('a broken sky turns sky mode off, so every unit and problem shows', !skyOn() && SKY_STAR.enemies === 0 && !skyHidden(U('task:w1'))
    && String(elCache.nums.innerHTML).includes('data-num="working"'));
  elCache.topbar = cap(); topbarTick(); ok('...and the top bar says so', String(elCache.topbar.innerHTML).includes('sky could not be built'));
  delete W.sky; place(); ok('a world with no sky at all keeps the old map', !skyOn() && bootLine() === '');
"""

SKY_RADIO_DRIVER = SKY_HEAD + r"""
  const cues = [], spoken = [];
  window.cmSfx = { cue: (n) => cues.push(n), enabled: () => true, setEnabled: () => {}, speak: (t) => spoken.push(t),
    voiceOut: () => true, setVoiceOut: () => {}, listen: { supported: false, reason: 'not secure', start() {}, stop() {}, onCommand: null } };
  const ship = (st, t3) => ({ uid: 'ship:t3:th1', kind: 'ship', source: 't3', status: st, name: 'proj · thread one', cwd: area.path, pane: 'Edited 3 files', line: 'Edited 3 files', t3: { state: t3 } });
  const L1 = C('ledger:L-1', 'ledger', 'blocked', { tier: 'raider', age_days: 50, voice: 'pick the job offer' });
  applyWorld(world({ chars: [ship('WORKING', 'working')], tasks: [T('t1', 'running', { title: 'fix the index' })], sky: SKY0([L1]) }));
  ok('a ship and a thread that first appear do not call; a contact that drops into the sky does', !!RADIO.cur && RADIO.cur.uid === 'sky:ledger:L-1'
    && RADIO.cur.word === 'decision' && RADIO.cur.name === 'ledger' && RADIO.cur.line === 'pick the job offer · 7 weeks' && RADIO.q.length === 0 && cues.join() === 'alert');
  while (RADIO.cur) radioNext(); cues.length = 0; spoken.length = 0;
  applyWorld(world({ chars: [ship('IDLE', 'ready')], tasks: [T('t1', 'review', { title: 'fix the index' })],
    sky: SKY0([L1, C('night:n1', 'night', 'ready', { voice: 'tidy notes is ready to keep', age_days: 0 }),
      C('t3:th1', 't3', 'ready', { open: { url: 'https://box.example:10001/bridge?focus=e:th1' } }), C('task:t1', 'task', 'review')]) }));
  const q = [RADIO.cur].concat(RADIO.q);
  ok('a thread ready for review, a T3 ship that finished and a night contact call in, once each (the thread from the task list the load takes)',
    q.map((c) => c.uid + ':' + c.word).join() === 'task:t1:to review,ship:t3:th1:finished,sky:night:n1:to review');
  ok('...each in its source’s own words', q[0].line === 'fix the index' && q[1].line === 'Edited 3 files' && q[2].line.indexOf('tidy notes is ready to keep') === 0);
  ok('the calls go through window.cmSfx: a cue each, the call on screen spoken', cues.join() === 'warp-in,warp-in,warp-in'
    && spoken.length === 1 && spoken[0].indexOf(', to review. fix the index') > 0);
  radioNext(); radioNext(); radioNext(); cues.length = 0;
  ok('the queue empties', RADIO.cur === null);
  // a blocked contact cues the alert; six new contacts at once: one on screen, four queued
  const six = [1, 2, 3, 4, 5, 6].map((i) => C('fleet:T-' + i, 'fleet', i === 1 ? 'blocked' : 'stale'));
  applyWorld(world({ chars: [ship('IDLE', 'ready')], tasks: [T('t1', 'review', { title: 'fix the index' })], sky: SKY0([L1].concat(six)) }));
  ok('the queue holds four behind the call on screen', !!RADIO.cur && RADIO.cur.uid === 'sky:fleet:T-1' && RADIO.q.length === 4 && RADIO.q[3].uid === 'sky:fleet:T-6');
  ok('a blocked contact cues the alert, the rest warp in; the three that left were not resolved, so nothing settles', cues[0] === 'alert'
    && cues.slice(1, 6).every((c) => c === 'warp-in') && cues.length === 6);
  ok('a contact call opens what it opens: the ledger row on Today', (() => { const was = toast; let said = ''; toast = (t) => { said = t; };
    radioOpen({ contact: L1 }); toast = was; const v = VIEW; VIEW = 'map'; return v === 'today' && said.indexOf('This decision closes when you rule on it') === 0; })());
  ok('...a T3 contact its Bridge link in a new tab', (() => { const opened = []; window.open = (u, t, f) => { opened.push([u, t, f]); };
    openContact(C('t3:th1', 't3', 'ready', { open: { url: 'https://box.example:10001/bridge?focus=e:th1' } }));
    openContact(C('t3:th2', 't3', 'ready', { open: { url: 'javascript:alert(1)' } }));
    return opened.length === 1 && opened[0][0] === 'https://box.example:10001/bridge?focus=e:th1' && opened[0][1] === '_blank' && /noopener/.test(opened[0][2]); })());
  while (RADIO.cur) radioNext();
  // the 4 s task poll sees a change before the next world load: the radio calls it then, and the load after it neither
  // loses it nor calls it twice (the load's contact for the same thread in the same state stays quiet)
  applyWorld(world({ chars: [ship('IDLE', 'ready')], tasks: [T('p1', 'review', { title: 'poll me' })], sky: SKY0([C('task:p1', 'task', 'review')]) }));
  while (RADIO.cur) radioNext(); cues.length = 0;
  applyTasks({ tasks: [T('p1', 'failed', { title: 'poll me' })] });
  ok('a thread in review that the poll sees fail calls in at once', !!RADIO.cur && RADIO.cur.uid === 'task:p1' && RADIO.cur.word === RADIO_WORDS.failed && RADIO.q.length === 0);
  while (RADIO.cur) radioNext();
  applyWorld(world({ chars: [ship('IDLE', 'ready')], tasks: [T('p1', 'failed', { title: 'poll me' })], sky: SKY0([C('task:p1', 'task', 'failed')]) }));
  ok('...and the world load after it does not call again', RADIO.cur === null && RADIO.q.length === 0);
  TASKS_AT = 0;    // the same through a world load whose task list is the fresher one: one call, from that list
  applyWorld(world({ chars: [ship('IDLE', 'ready')], tasks: [T('p1', 'review', { title: 'poll me' })], sky: SKY0([C('task:p1', 'task', 'review')]) }));
  ok('a change carried by the world load calls once', !!RADIO.cur && RADIO.cur.uid === 'task:p1' && RADIO.cur.word === RADIO_WORDS.review && RADIO.q.length === 0);
  while (RADIO.cur) radioNext();
  // a contact whose state changes calls in, once: a ledger row that now blocks something, and a T3 thread (its ship calls)
  applyWorld(world({ chars: [ship('IDLE', 'ready')], tasks: [T('p1', 'review', { title: 'poll me' })],
    sky: SKY0([C('task:p1', 'task', 'review'), C('ledger:L-2', 'ledger', 'stale', { voice: 'pick a date' }), C('t3:th1', 't3', 'ready')]) }));
  while (RADIO.cur) radioNext();
  applyWorld(world({ chars: [ship('IDLE', 'waiting')], tasks: [T('p1', 'review', { title: 'poll me' })],
    sky: SKY0([C('task:p1', 'task', 'review'), C('ledger:L-2', 'ledger', 'blocked', { voice: 'pick a date' }), C('t3:th1', 't3', 'blocked')]) }));
  const q2 = [RADIO.cur].concat(RADIO.q).filter(Boolean);
  ok('a contact that changes state calls in, and a T3 thread whose ship called stays at one call', q2.map((c) => c.uid + ':' + c.word).join() === 'ship:t3:th1:needs your answer,sky:ledger:L-2:decision');
  while (RADIO.cur) radioNext();
  // X mutes the cues through the sound script
  let on = true; window.cmSfx.enabled = () => on; window.cmSfx.setEnabled = (b) => { on = !!b; };
  toggleSound(); ok('X turns the sound off and on', on === false); toggleSound(); ok('...and on', on === true);
  // Shift+X: the radio's voice-out, through the sound script, saying when the device has no voice for it
  let vo = false; window.cmSfx.voiceOut = () => vo; window.cmSfx.setVoiceOut = (b) => { vo = !!b; }; window.cmSfx.voiceReady = () => false;
  { const was = toast; const said = []; toast = (t) => said.push(t);
    toggleVoiceOut(); ok('Shift+X turns the radio voice on and says the device has no voice for it yet', vo === true && said[0].indexOf('radio voice on · no voice on this device yet') === 0);
    toggleVoiceOut(); ok('...and off', vo === false && said[1].indexOf('radio voice off') === 0);
    toast = was; }
  // the arrival chime keeps quiet while the sound is muted
  { let tones = 0; const fake = { state: 'running', currentTime: 0, destination: {},
      createOscillator() { tones++; return { type: '', frequency: {}, connect() {}, start() {}, stop() {} }; },
      createGain() { return { gain: { value: 0, setValueAtTime() {}, exponentialRampToValueAtTime() {} }, connect() {} }; } };
    const was = audioCtx; audioCtx = fake;
    on = false; chime(); ok('X mutes the arrival chime too', tones === 0);
    on = true; chime(); ok('...which sounds again with the sound on', tones === 2);
    audioCtx = was; }
"""

SKY_BANNER_DRIVER = SKY_HEAD + r"""
  const cues = []; window.cmSfx = { cue: (n) => cues.push(n), enabled: () => true, setEnabled() {}, speak() {}, voiceOut: () => false, setVoiceOut() {}, listen: null };
  const ship = (tid, st, t3) => ({ uid: 'ship:t3:' + tid, kind: 'ship', source: 't3', status: st, name: 'proj · ' + tid, cwd: area.path, pane: '', t3: { state: t3 } });
  const ledgerOk = (rows) => Object.assign({}, W.today, { ledger: rows, parked: rows.length });
  const L1 = C('ledger:L-1', 'ledger', 'blocked'), R1 = C('t3:th2', 't3', 'ready', { title: 'proj · review the diff' });
  const L1row = { id: 'L-1', lane: 'x', title: 'contact ledger:L-1', opened: '2026-09-01', age_days: 40 };
  applyWorld(world({ chars: [ship('th2', 'IDLE', 'ready')], today: ledgerOk([L1row]), sky: SKY0([L1, R1]) })); FX.toast = null;
  // leaving is not resolving: the T3 database could not be read this time, so the thread's ship is gone with its contact
  applyWorld(world({ chars: [], today: ledgerOk([L1row]), sky: SKY0([L1]) }));
  ok('a T3 contact that leaves with its ship (an unread database) raises nothing', FX.toast === null && !cues.includes('settle'));
  applyWorld(world({ chars: [ship('th2', 'IDLE', 'ready')], today: ledgerOk([L1row]), sky: SKY0([L1, R1]) })); FX.toast = null;
  applyWorld(world({ chars: [ship('th2', 'IDLE', 'snoozed')], today: ledgerOk([L1row]), sky: SKY0([L1]) }));
  ok('...nor one snoozed (parked, not done)', FX.toast === null);
  applyWorld(world({ chars: [ship('th2', 'IDLE', 'ready')], today: ledgerOk([L1row]), sky: SKY0([L1, R1]) })); FX.toast = null;
  applyWorld(world({ chars: [ship('th2', 'IDLE', 'settled')], today: ledgerOk([L1row]), sky: SKY0([L1]) }));
  ok('a T3 thread settled: the banner with its title and the sky word', !!FX.toast && FX.toast.title === 'proj · review the diff' && FX.toast.word === 'CLEARED' && cues.includes('settle'));
  FX.toast = null; applyWorld(world({ chars: [ship('th2', 'IDLE', 'settled')], today: ledgerOk([L1row]), sky: SKY0([L1]) }));
  ok('the same world again raises nothing: once per settle', FX.toast === null);
  // a ledger whose read failed has no open rows to show, which proves no ruling
  cues.length = 0; applyWorld(world({ today: Object.assign({}, W.today, { ledger: [], parked: null }), sky: SKY0([]) }));
  ok('a ledger row gone with an unread ledger raises nothing, not even EMPTY SKY', FX.toast === null && !cues.includes('empty-sky'));
  applyWorld(world({ today: ledgerOk([L1row]), sky: SKY0([L1]) })); FX.toast = null;
  applyWorld(world({ today: ledgerOk([]), sky: SKY0([]) }));
  ok('the last one leaving, ruled (read, no longer open), says ALL CLEAR', !!FX.toast && FX.toast.word === 'ALL CLEAR' && FX.toast.title === 'contact ledger:L-1' && cues.includes('empty-sky'));
  // restarting the last failed thread empties the sky while it runs: no completion
  FX.toast = null; applyWorld(world({ tasks: [T('f1', 'failed', { title: 'flaky build' })], today: ledgerOk([]), sky: SKY0([C('task:f1', 'task', 'failed', { title: 'flaky build' })]) }));
  FX.toast = null; cues.length = 0; applyWorld(world({ tasks: [T('f1', 'running', { title: 'flaky build' })], today: ledgerOk([]), sky: SKY0([]) }));
  ok('a failed thread restarted leaves the sky without a banner or the EMPTY SKY cue', FX.toast === null && cues.length === 0);
  // a thread set aside, and a claim marked done today, resolve their contacts; one resolved beside one that only left: CLEARED
  applyWorld(world({ tasks: [T('d1', 'failed', { title: 'drop me' })], today: ledgerOk([]),
    sky: SKY0([C('task:d1', 'task', 'failed', { title: 'drop me' }), C('fleet:T-7', 'fleet', 'blocked', { title: 'a claim' }), C('seat:s1', 'seat', 'blocked', { title: 'a seat' })]) }));
  FX.toast = null; applyWorld(world({ tasks: [T('d1', 'discarded', { title: 'drop me' })], today: ledgerOk([]),
    sky: SKY0([C('fleet:T-7', 'fleet', 'blocked', { title: 'a claim' }), C('seat:s1', 'seat', 'blocked', { title: 'a seat' })]) }));
  ok('a thread set aside raises the banner', !!FX.toast && FX.toast.title === 'drop me' && FX.toast.word === 'CLEARED');
  FX.toast = null; applyWorld(world({ tasks: [T('d1', 'discarded', { title: 'drop me' })], today: Object.assign(ledgerOk([]), { done: [{ id: 'T-7', title: 'a claim', at: '10:00', checked: false }] }),
    sky: SKY0([]) }));
  ok('a claim done today beside a seat that was only answered: CLEARED with the claim, not EMPTY SKY', !!FX.toast && FX.toast.title === 'a claim' && FX.toast.word === 'CLEARED');
  // a merged thread whose merge raised the banner does not raise a second one when its contact leaves
  FX.toast = null; applyWorld(world({ tasks: [T('m1', 'review', { title: 'tidy the notes' })], sky: SKY0([L1, C('task:m1', 'task', 'review', { title: 'tidy the notes' })]) }));
  FX.toast = null; applyWorld(world({ tasks: [T('m1', 'merged', { title: 'tidy the notes' })], sky: SKY0([L1]) }));
  ok('a merge and its contact leaving make one banner, the merge’s', !!FX.toast && FX.toast.title === 'tidy the notes' && !FX.toast.word);
  // the banner draws its word in the pixel looks and the vector intertitle says it under the title
  const texts = []; const realFill = ctx.fillText; ctx.fillText = (s) => { texts.push(String(s)); };
  FX.toast = null; FRAME_T = 1000; missionComplete('one thing done', SKY_WORDS.cleared); drawMergeBanner(1600);
  ok('the pixel banner says CLEARED and the title', texts.includes('CLEARED') && texts.includes('one thing done'));
  applyTheme('tactical', false); texts.length = 0; FX.toast = null; FRAME_T = 1000; missionComplete('another', SKY_WORDS.cleared); drawMergeBanner(1600);
  ok('the vector intertitle says it after the stamp, with no branch', texts.some((t) => /· CLEARED$/.test(t)) && FX.toast.into == null);
  applyTheme(THEME_CFG, false); ctx.fillText = realFill; FX.toast = null;
"""

TODAY_SKY_DRIVER = r"""
  elCache.today = cap();
  const base = { configured: true, date: 'Sun', agents: { working: [], waiting: [], idle: [], problems: [] }, needs: [], daily: [], done: [],
    folders: [], unmapped: [], parked: 13 };
  W.agent_tasks = [];
  W.today = Object.assign({}, base, { night: null, ledger: [
    { id: 'D-2', lane: 'money', title: 'second <b>call</b>', opened: '2026-09-04', age_days: 37, blocks: null, estimate: false },
    { id: 'D-1', lane: 'jobs-career', title: 'first call', opened: '2026-08-22', age_days: 50, blocks: 'the apply', estimate: true },
    { id: 'D-3', lane: 'doctrine', title: 'third call', opened: '2026-10-04', age_days: 7 }] });
  renderToday(); let h = String(elCache.today.innerHTML);
  const at = (s) => h.indexOf(s);
  ok('Decisions lists the ledger rows oldest first, after Needs you', at('data-sec="needs"') < at('data-sec="decisions"') && at('data-sec="decisions"') < at('data-ledger="D-1"') && at('data-ledger="D-1"') > 0 && at('data-ledger="D-1"') < at('data-ledger="D-2"') && at('data-ledger="D-2"') < at('data-ledger="D-3"'));
  ok('each row: its age, lane, what it holds up, an estimated date flagged; its id in the row\'s data, never a tooltip (C4)', h.includes('waiting 7 weeks · jobs-career · holds up the apply · start date guessed</small>')
    && h.includes('waiting 7 days · doctrine</small>') && h.includes('data-ledger="D-1">') && !h.includes('title="D-1"') && !h.includes('· D-1'));
  ok('the count line is replaced by the rows, in plain words', !h.includes('older decisions wait') && h.includes('3 in the ledger, oldest first · the oldest waits 50 days') && !h.includes('read only'));
  ok('a row title is text, never markup', h.includes('second &lt;b&gt;call&lt;/b&gt;'));
  ok('no night result: said in place, in its own section after Need you and Running now', h.includes('data-night="none"') && at('data-sec="needs"') < at('data-sec="tasks"') && at('data-sec="tasks"') < at('data-sec="night"'));
  W.today.night_note = 'no night-run report for last night: the newest in the digest is for <2026-10-08>';
  renderToday(); h = String(elCache.today.innerHTML);
  ok('today.py\'s own reason replaces the generic line, as text', h.includes('data-night="none">no night-run report for last night: the newest in the digest is for &lt;2026-10-08&gt;<')
    && !h.includes('No night run on record yet'));
  W.today.night_note = null;
  W.today.night = { date: '2026-10-10', ran: 2, ready: 2, failed: 0, source: 'digest', line: 'night queue 10-10: 2 ran, 2 ready for your keep/drop, 0 FAILED',
    items: [{ id: 'n1', title: 'tidy the notes', repo: 'notes', verdict: 'ready', minutes: 12 }] };
  renderToday(); h = String(elCache.today.innerHTML);
  ok('the night run: one card (what is left for you in a few words), the counts, the digest line it came from, each item', h.includes('Night run · 2 tasks') && h.includes('<div class="tnow">2 ready, 0 failed</div>')
    && h.includes('the night of 2026-10-10') && h.includes('2 ran · 2 ready for your keep or drop · 0 failed')
    && h.includes('from the digest: night queue 10-10: 2 ran, 2 ready for your keep/drop, 0 FAILED') && h.includes('tidy the notes<small>notes · ready · 12 min</small>')
    && h.includes('data-night="item" data-item="n1"') && !h.includes('title="n1"'));
  ok('keep and drop stay with the night run', h.includes('You keep or drop each one in the night run.') && !h.includes('data-night="none"'));
  ok('no refused count, no refused word', !h.includes('refused<small>'));
  W.today.night.refused = 1; renderToday(); h = String(elCache.today.innerHTML);
  ok('a refused count in the night record is said after the failed one', h.includes('0 failed · 1 refused<small>'));
  // a count the record does not state (today.py keeps it null) is left unsaid, never shown as 0
  W.today.night = { date: '2026-10-10', ran: null, ready: null, failed: null, refused: null, source: 'json', line: null,
    items: [{ id: 'n1', title: 'tidy the notes', repo: 'notes', verdict: 'ready', minutes: 12 }] };
  renderToday(); h = String(elCache.today.innerHTML);
  ok('an item-only record states no counts: none is made up, and its ready item still asks for you', !/\b0 (ran|ready|failed)/.test(h)
    && h.includes('data-night="sum">the record states no counts, only the 1 item below<small>') && h.includes('class="tdl need" data-night="sum"'));
  W.today.night = { date: '2026-10-10', ran: 2, ready: null, failed: 0, source: 'digest', line: 'night queue 10-10: 2 ran, 0 FAILED', items: [] };
  renderToday(); h = String(elCache.today.innerHTML);
  ok('only the stated counts are said', h.includes('data-night="sum">2 ran · 0 failed<small>') && !h.includes('ready for your keep or drop'));
  W.today = Object.assign({}, base, { ledger: [], night: null });
  renderToday(); h = String(elCache.today.innerHTML);
  ok('with no ledger rows the old count line stays', h.includes('13 older decisions wait in the ledger.'));
"""


HANGAR_DRIVER = SKY_HEAD + r"""
  const seat = (name, st, o) => Object.assign({ uid: 'seat:' + name, kind: 'seat', name: name, status: st, cwd: area.path, line: '', command: 'claude' }, o || {});
  W.sky = SKY0([]);
  W.chars = W.chars.filter((c) => c.kind !== 'seat' && c.kind !== 'ship').concat([seat('hub', 'IDLE', { cwd: '' }), seat('astra', 'WORKING'), seat('jobs', 'IDLE'),
    seat('money', 'IDLE', { command: 'bash', shell: true }), seat('notes', 'BLANK', { command: 'zsh', shell: true }),
    { uid: 'runner:old', kind: 'runner', name: 'old-seat', status: 'DEAD', role: 'a runner' }, { uid: 'runner:ok', kind: 'runner', name: 'live-one', status: 'LIVE', role: 'a runner' },
    // BROKEN is not one of sky.py's derelict states (DEAD, DOWN, FROZEN): the belt and the berth line count what the sky counts
    { uid: 'runner:br', kind: 'runner', name: 'broken-one', status: 'BROKEN', role: 'a runner' }]);
  W.hangar_slots = 2; place();
  const U = (uid) => units.find((u) => u.uid === uid);
  ok('a bare shell is not a ship but an empty berth', !isShip(U('seat:money')) && isBerth(U('seat:money')) && isShip(U('seat:jobs')) && !isBerth(U('seat:jobs')));
  ok('sky mode docks a seat at rest in the hangar, a working one stays where it works', !U('seat:jobs').docked_at && U('seat:astra').docked_at === area.uid);
  ok('...out of sight there (in warp), while the seats at rest and the berths show', skyHidden(U('seat:astra')) && !skyHidden(U('seat:jobs')) && !skyHidden(U('seat:money')));
  const r = (u) => Math.hypot(u.gx * TILE - LY.star.x, u.gy * TILE - LY.star.y);
  ok('the berths ride the dock ring with the ships at rest', Math.abs(r(U('seat:money')) - r(U('seat:jobs'))) < 1 && r(U('seat:jobs')) >= LY.dock - 1);
  const berths = []; const realBerth = drawBerth; drawBerth = (u, ...a) => { berths.push(u.uid); return realBerth(u, ...a); };
  setZoom(.3); px = LY.star.x; py = LY.star.y; draw(4000);
  ok('a berth is drawn as one, never as a craft', berths.includes('seat:money') && berths.includes('seat:notes') && !berths.includes('seat:jobs'));
  drawBerth = realBerth;
  ok('the agents list counts the terminal slots against the limit, in words', berthLine() === '3 of 2 terminal slots used (over the limit) · 2 idle · 2 empty · 1 stopped agent');
  elCache.cbody = cap(); elCache.csum = cap(); commsTick();
  ok('...the line says who works and who idles (the shells left out), the slots sit in the opened list', String(elCache.csum.textContent).indexOf('1 working · 2 idle') === 0
    && !String(elCache.csum.textContent).includes('terminal slots') && String(elCache.cbody.innerHTML).includes('3 of 2 terminal slots used (over the limit)'));
  W.hangar_slots = 6; ok('within the limit it says so plainly', berthLine().indexOf('3 of 6 terminal slots used · 2 idle · 2 empty') === 0);
  const der = drawDerelicts(); ok('a dead runner drifts on the belt beyond the planets, a live one does not', der.length === 1 && der[0][0] === 'old-seat'
    && Math.hypot(der[0][1] - LY.star.x, unproject(der[0][1], der[0][2]).y - LY.star.y) > LY.rootOrbit);
  const all = units.filter((u) => u.kind === 'seat').map((u) => u.uid).sort().join();
  ok('nothing is removed: every window is still a unit', all === 'seat:astra,seat:hub,seat:jobs,seat:money,seat:notes');
  setSky(false); ok('the full view keeps a seat at rest at its planet', U('seat:jobs').docked_at === area.uid); setSky(true);
"""


class HangarWorldTests(unittest.TestCase):
    """render-game.py reads each window's last activity and whether a bare shell is all it runs (fake tmux on PATH)."""

    FAKE_TMUX = r'''#!__PYTHON__
import os, sys, time
a = sys.argv[1:]
old = os.environ.get("FAKE_TMUX_OLD") == "1"
if a[:1] == ["list-windows"]:
    fmt = a[a.index("-F") + 1]
    if "window_activity" not in fmt:
        print("@1\tagent\n@2\tshell")
    else:
        idle = str(int(time.time()) - 3 * 86400 - 60)
        rows = [("0", "@1", "agent", "%1", "claude", "101", idle, "/tmp"), ("1", "@2", "shell", "%2", "bash", "102", idle, "/tmp")]
        for r in rows:
            r = list(r)
            if old:
                del r[6]
            print("\x1f".join(r))
elif a[:1] == ["capture-pane"]:
    print("$ " if a[-1] == "%2" else "> done")
'''

    def render(self, old=False):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "work", "Alpha").mkdir(parents=True)
            bin_dir = Path(tmp, "bin"); bin_dir.mkdir()
            fake = bin_dir / "tmux"; fake.write_text(self.FAKE_TMUX.replace("__PYTHON__", sys.executable), encoding="utf-8"); fake.chmod(0o755)
            env = {"PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", "")}
            if old:
                env["FAKE_TMUX_OLD"] = "1"
            proc = render_fixture(tmp, "--json", env_extra=env, tmux_session="fake", seats_json=str(Path(tmp, "no-seats.json")))
            self.assertEqual(proc.returncode, 0, msg=proc.stderr[-2000:])
            return {c["name"]: c for c in json.loads(proc.stdout)["chars"] if c.get("kind") == "seat"}

    def test_a_bare_shell_is_flagged_and_a_window_at_rest_has_its_idle_days(self):
        seats = self.render()
        self.assertEqual(set(seats), {"agent", "shell"})
        self.assertIs(seats["shell"]["shell"], True)
        self.assertIs(seats["agent"]["shell"], False)
        self.assertEqual(seats["agent"]["status"], "IDLE")
        self.assertEqual(seats["agent"]["idle"], 3)
        self.assertIsInstance(seats["agent"]["activity"], int)

    def test_a_tmux_that_answers_without_the_activity_field_still_gives_the_seats(self):
        seats = self.render(old=True)
        self.assertEqual(set(seats), {"agent", "shell"})
        self.assertEqual((seats["agent"]["idle"], seats["agent"]["activity"]), (0, None))
        self.assertEqual(seats["shell"]["cwd"], "/tmp")


class SkyPageTests(unittest.TestCase):
    """Sky mode, the radio's new callers, Today's ledger rows and night block, the mission-complete banner."""

    @classmethod
    def setUpClass(cls):
        if not shutil.which("node"):
            raise unittest.SkipTest("node not installed")
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name, "work")
        for name in ("Alpha", "Beta", "Gamma"):
            (root / name).mkdir(parents=True)
            (root / name / "notes.md").write_text("fixture\n", encoding="utf-8")
        proc = render_fixture(cls.tmp.name)
        if proc.returncode:
            raise AssertionError(proc.stderr[-2000:])
        cls.js = Path(cls.tmp.name, "page.js")
        cls.html = Path(cls.tmp.name, "page.html").read_text(encoding="utf-8")
        page_js(Path(cls.tmp.name, "page.html"), cls.js)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def drive(self, body, env_extra=None):
        run_page_driver(self, self.js, body, None, env_extra)

    def test_the_rendered_world_carries_the_sky(self):
        world = json.loads(re.search(r"(?m)^const W = (.*);$", self.html).group(1))
        self.assertIsInstance(world.get("sky"), dict)
        self.assertIsInstance(world["sky"].get("contacts"), list)
        self.assertIsInstance(world.get("hangar_slots"), int)

    def test_sky_mode_hides_work_in_warp_and_draws_the_contacts(self):
        self.drive(SKY_MODE_DRIVER, {"HARNESS_STAGE": "1600x900"})

    def test_the_radio_calls_for_ships_threads_and_contacts_through_the_sound_script(self):
        self.drive(SKY_RADIO_DRIVER)

    def test_a_contact_leaving_raises_the_banner_once(self):
        self.drive(SKY_BANNER_DRIVER)

    def test_today_lists_the_ledger_oldest_first_and_what_ran_overnight(self):
        self.drive(TODAY_SKY_DRIVER)

    def test_a_page_with_the_sound_script_hands_it_the_voice_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            js = Path(tmp, "page.js")
            js.write_text("window.cmSfx = { cue() {}, enabled: () => false, setEnabled() {}, speak() {}, voiceOut: () => !!window.__vo,"
                          " setVoiceOut(b) { window.__vo = !!b; },"
                          " listen: { supported: true, reason: null, start() { window.__heard = (window.__heard || 0) + 1; }, stop() {}, onCommand: null } };\n"
                          + self.js.read_text(encoding="utf-8"), encoding="utf-8")
            run_page_driver(self, js, r"""
  ok('the page takes the voice commands', window.cmSfx.listen.onCommand === pageVoice);
  window.cmSfx.listen.onCommand('sky'); ok('a spoken "sky" switches sky mode', SKY === false); window.cmSfx.listen.onCommand('sky');
  const e = { key: 'g', repeat: false, ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, preventDefault() {}, stopPropagation() {},
    getModifierState() { return false; }, target: { blur() {} } };
  document.activeElement = null; (__WL.keydown || []).forEach((f) => f(e));
  ok('G held starts listening', window.__heard === 1);
  ok('a phrase that is not a command is said, not dropped', typeof window.cmSfx.listen.onMiss === 'function');
  const sx = Object.assign({}, e, { key: 'X', shiftKey: true }); (__WL.keydown || []).forEach((f) => f(sx));
  ok('Shift+X turns the radio voice on', window.__vo === true);
""")

    def test_a_skin_names_the_banners_never_the_states(self):
        # skins rename characters, never states; a skin's "sky" may name the two banners only
        skin = json.loads((ROOT / "skins" / "neutral.json").read_text(encoding="utf-8"))
        skin["name"] = "mine"
        skin["radio"] = {"needs": "wants orders", "down": "x" * 25}        # one word over 24: ignored
        skin["sky"] = {"empty": "ALL CLEAR", "cleared": "<b>DONE</b>"}      # markup: ignored
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "work", "Alpha").mkdir(parents=True)
            Path(tmp, "skins").mkdir()
            Path(tmp, "skins", "mine.json").write_text(json.dumps(skin), encoding="utf-8")
            proc = render_fixture(tmp, skin="mine", skins_dir=str(Path(tmp, "skins")))
            self.assertEqual(proc.returncode, 0, msg=proc.stderr[-2000:])
            js = Path(tmp, "page.js")
            page_js(Path(tmp, "page.html"), js)
            run_page_driver(self, js, r"""
  ok('a state keeps its plain word whatever the skin says', RADIO_WORDS.needs === 'needs your answer' && RADIO_WORDS.down === 'stopped');
  ok('the skin names the banner; markup keeps the neutral one', SKY_WORDS.empty === 'ALL CLEAR' && SKY_WORDS.cleared === 'CLEARED');
  ok('a seat that needs you calls in with the plain word', radioWord('WORKING', 'WAITING') === 'needs your answer');
  elCache.nums = cap(); stripTick(); ok('the strip says plain words', String(elCache.nums.innerHTML).includes('>waiting for you<') && !String(elCache.nums.innerHTML).includes('wants orders'));
""")

    def test_bare_shells_are_berths_and_the_hangar_counts_its_slots(self):
        self.drive(HANGAR_DRIVER, {"HARNESS_STAGE": "1600x900"})

    def test_a_skins_own_words_fit_where_the_page_writes_them(self):
        # the neutral words are the page's own defaults; a skin may name only the two banners in its "sky" object (the words module:
        # states are said in plain words in every skin), so no shipped skin carries "radio" or a state word
        radio = set()
        sky = {"cleared", "empty"}
        for path in sorted((ROOT / "skins").glob("*.json")):
            if path.name.endswith(("-ship3d.json", "-planets3d.json")):
                continue                    # a 3D skin's baked data beside it (its "sky" is a map's name), no words
            skin = json.loads(path.read_text(encoding="utf-8"))
            for key, words, most in (("radio", radio, 24), ("sky", sky, 16)):
                got = skin.get(key)
                if got is None:
                    continue
                self.assertLessEqual(set(got), words, f"{path.name}: {key} words the page does not read")
                for word, text in got.items():
                    self.assertTrue(isinstance(text, str) and 0 < len(text) <= most and not re.search(r'[\x00-\x1f<>&"\\]', text),
                                    f"{path.name}: {key}.{word} would be ignored: {text!r}")

    def test_the_page_adds_no_animation_loop(self):
        src = (ROOT / "render-game.py").read_text(encoding="utf-8")
        self.assertEqual(src.count("requestAnimationFrame"), 2, "draws ride wantFrame (its two calls): no new loop")


import ships  # noqa: E402  (next to the tests that use it)


if __name__ == "__main__":
    unittest.main(verbosity=2)
