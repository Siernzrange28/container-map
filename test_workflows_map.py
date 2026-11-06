#!/usr/bin/env python3
"""test_workflows_map.py — workflows on the 2D map (ui/workflows2d.js, ui/workflows2d.css).

Three levels, each with the fixtures the probes use (tests/fixtures/workflow-demo.json = FIX, workflow-variants.json):
- the source: the file parses, its template list is the engine's, agent text never becomes markup, the stylesheet's
  status colours are rgb() (the theme rewriter leaves them), neutral words only, the private route colours only in the
  private skin;
- the page in the stub DOM (test_game's driver): the WF store behind the CM data functions (rows, chips, the /tasks list
  over a render's), change (radio at most one a 3 s per workflow, the phone vibration, the agent's crawl, the finish
  receipt from facts), replay on the scene clock (its frames are the engine's summary_frames), shows only in focus,
  pointers, "Agents can move my view", Esc;
- the page in headless Chrome (a page on disk, and a test bridge's real /control reply): the route drawn in pixels, the
  gates, the strip, the card's buttons, the phone sheet's two gate buttons, "Connect an agent" with no runner, a point
  from the bridge's control queue at its planet with its words in the status line."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import testenv  # noqa: F401  (first: clears what steers the tool)
import test_game
import workflows
from test_workflows_api import Rig

ROOT = Path(__file__).resolve().parent
UI_JS = ROOT / "ui" / "workflows2d.js"
UI_CSS = ROOT / "ui" / "workflows2d.css"
FIXTURES = ROOT / "tests" / "fixtures"
PLANETS = ("api", "ops", "docs", "site", "data", "infra")


def fixtures():
    out = {"FIX": json.loads((FIXTURES / "workflow-demo.json").read_text(encoding="utf-8"))}
    out.update({k: v for k, v in json.loads((FIXTURES / "workflow-variants.json").read_text(encoding="utf-8")).items()
                if k[:1].isupper()})
    return out


def macros_js():
    """const FIX = {...}; const FIX_DONE = {...}; ... every fixture as a literal, re-timed so its "updated" is now."""
    lines = []
    for name, value in fixtures().items():
        lines.append(f"const {name} = (() => {{ const w = {json.dumps(value)}; w.updated = new Date().toISOString(); return w; }})();")
    return "\n".join(lines) + "\n"


def render_page(tmp):
    """A page on disk over a work root with the fixtures' planets (api, ops, docs, ...) and no agent set up."""
    root = Path(tmp, "work")
    for name in PLANETS:
        (root / name).mkdir(parents=True)
        (root / name / "main.py").write_text("print('fixture')\n", encoding="utf-8")
    proc = test_game.render_fixture(tmp, detect_runners=False)
    if proc.returncode:
        raise AssertionError(proc.stderr[-2000:])
    return Path(tmp, "page.html")


class Source(unittest.TestCase):
    def setUp(self):
        self.js = UI_JS.read_text(encoding="utf-8")
        self.css = UI_CSS.read_text(encoding="utf-8")

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_the_file_parses(self):
        run = subprocess.run(["node", "--check", str(UI_JS)], capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)

    def test_the_template_sheet_lists_the_engines_built_in_templates(self):
        block = self.js[self.js.index("const WF_TEMPLATES=["):]
        block = block[:block.index("}];") + 2]
        got = [dict(zip(("name", "label", "desc", "steps"), m)) for m in re.findall(
            r"\{name:'([^']+)',label:'([^']+)',desc:'([^']+)',steps:(\d+)\}", block)]
        want = [t for t in workflows.list_templates(None) if not t["saved"]]
        self.assertEqual([(g["name"], g["label"], g["desc"], int(g["steps"])) for g in got],
                         [(t["name"], t["label"], t["desc"], t["steps"]) for t in want])

    def test_agent_text_never_becomes_markup(self):
        """workflow and show text reaches the page through textContent or the canvas only."""
        for word in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
            self.assertNotIn(word, self.js, word)
        # and no link an agent could write: no href, no window.open, no location set from data
        for word in ("href", "window.open", "location="):
            self.assertNotIn(word, self.js, word)

    def test_the_stylesheet_writes_its_colours_as_rgb(self):
        """A hex colour in ui/*.css becomes a theme variable (render-game.py, the seams); a status colour must not move."""
        css = re.sub(r"/\*.*?\*/", "", self.css, flags=re.S)
        self.assertEqual(re.findall(r"#[0-9a-fA-F]{3,8}\b", css), [])
        self.assertNotIn("style=", self.js.replace("e.style", "").replace(".style.", ""))   # no inline style attribute in markup

    def test_neutral_words_only_and_a_private_skins_route_colours_live_in_that_skin(self):
        """No skin but the neutral one speaks in these files: every other skin on disk (the owner's private one stays home
        and never ships) keeps its names, banners and route colours (its "route2d" block) to itself."""
        low = (self.js + self.css).lower()
        neutral = (ROOT / "skins" / "neutral.json").read_text(encoding="utf-8").lower()
        for path in sorted((ROOT / "skins").glob("*.json")):
            if path.stem.startswith("neutral") or path.stem.endswith(("-3d", "-ship3d", "-planets3d")):
                continue   # 3D data (<name>-3d, baked ship, baked planets), not a 2D skin: no names, sky or radio blocks
            skin = json.loads(path.read_text(encoding="utf-8"))
            words = [v for block in ("names", "sky", "radio") for v in (skin.get(block) or {}).values() if isinstance(v, str)]
            for value in words:
                if value.lower() not in neutral:
                    with self.subTest(skin=path.name, word=value):
                        self.assertNotIn(value.lower(), low)
            route = skin.get("route2d")
            if route is None:
                continue
            with self.subTest(skin=path.name):
                self.assertTrue(set(route) <= {"done", "working", "failed", "medal", "ribbon", "ribbon_dark"}, route)
                for value in route.values():
                    self.assertRegex(value, r"^#[0-9A-Fa-f]{6}$")
                    self.assertNotIn(value.lower(), low)
                    self.assertNotIn(value.lower(), neutral)
        # the medal's disc and ribbon come from the skin, the neutral ones written here (gold disc, done-green ribbon)
        self.assertIn("disc=wfSkinCol('medal')||'#F2C14E', ribA=wfSkinCol('ribbon')||'#3FBF72', ribB=wfSkinCol('ribbon_dark')||'#2F9F5C'", self.js)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class Driver(unittest.TestCase):
    """The page script in test_game's stub DOM, on a fixture with the planets the fixtures name."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="cm-wfmap-")
        page = render_page(cls.tmp.name)
        cls.js = Path(cls.tmp.name, "page.js")
        test_game.page_js(page, cls.js)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def drive(self, body, live=False):
        test_game.run_page_driver(self, self.js, macros_js() + "  const clone = (o) => JSON.parse(JSON.stringify(o));\n" + body,
                                  location={"protocol": "http:", "hostname": "127.0.0.1"} if live else None,
                                  env_extra={"HARNESS_STAGE": "1280x800"})

    def test_the_store_rows_and_chips(self):
        self.drive(r"""
  applyTasks({ tasks: [], workflows: [FIX] });
  ok('the chip after the working planet\'s name', CM.labelChip('area:ops') === 'Fix the phone labels · 2 of 4' && CM.labelChip('area:api') === ''
    && __wfAudit().chips['area:ops'] === 'Fix the phone labels · 2 of 4');
  const row = CM.wfRows()[0];
  ok('a row: the seed\'s eight keys, then its stops in plain words', Object.keys(row).slice(0, 8).join() === 'id,title,done,total,state,place,sentence,step'
    && row.stops.map((x) => x.short).join('|') === 'Find labels|Fix slots|Look at shots|Write it up'
    && row.stops.map((x) => x.word).join('|') === 'done|120 of 800|you, later|waiting' && row.progress.done === 120 && row.night === false && row.by === 'Claude Code');
  ok('labels of at most 14 characters, the full words kept', row.stops.every((x) => x.short.length <= 14) && row.stops[2].label === 'Look at the phone shots');
  // a /state reply (a render 15 s older) brings its own list: the /tasks one stands
  applyWorld(Object.assign({}, W, { chars: W.chars, quests: W.quests || [], workflows: [] }));
  ok('the /tasks list stands over a render\'s', CM.wfList()[0] === FIX && Array.isArray(W.workflows) && W.workflows.length === 0);
  const night = { id: 'w-20261012-0400-night-run-abc123', title: 'Night run · 1 task', derived: 'night', state: 'done', updated: new Date().toISOString(),
    done: 1, total: 1, by: { name: 'night queue', seat: null }, steps: [{ key: 'n1', label: 'fix the adder', kind: 'task', state: 'done', planet_uid: 'area:api', place: 'api' }] };
  const done = clone(FIX_DONE); done.updated = new Date().toISOString();
  applyTasks({ tasks: [], workflows: [done, night] });
  ok('a night run is a row that offers Replay', CM.wfRows()[1].night === true && CM.wfRows()[1].stops.length === 1);
  ok('a medal where a workflow finished today (its last step\'s planet), none for the night run', JSON.stringify(__wfAudit().medals) === '{"area:docs":true}');
  done.updated = new Date(Date.now() - 25 * 3600 * 1000).toISOString(); applyTasks({ tasks: [], workflows: [done] });
  ok('...and none a day later', JSON.stringify(__wfAudit().medals) === '{}');
  applyTasks({ tasks: [], workflows: [FIX] }); draw(1000);
  ok('the overview stays literal: no ribbon, no gate, no show', __wfAudit().mode === 'overview' && JSON.stringify(__wfAudit().overview) === '{"ribbons":0,"gates":0,"shows":0}');
  CM.selectWorkflow(FIX.id); draw(1100); const a = __wfAudit();
  ok('selected: two legs (repeats merged), four gates, the strip of four', a.ribbons[0].segments === 2 && a.gates.length === 4 && a.strip.slots === 4
    && a.strip.links.join() === 'done,working,waiting' && a.strip.medals === 1 && a.ribbons[0].legs.join() === 'working,waiting');
  ok('the planets off the route are dimmed', a.dimmed.length === MOONS.length - 3 && !a.dimmed.includes('area:ops'));
  ok('the numbered tags say the step and its state', a.tags.map((t) => t.text).join('|') === '1  Find the cut labels · done|2  Fix the label slots · 120 of 800|3  Look at the phone shots · you approve|4  Write it up · waiting');
  ok('Esc closes the selection, and only then says no', CM.backLayer('esc') === true && CM.selectedWorkflow() === null && CM.backLayer('has') === false);
""")

    def test_change_calls_in_moves_the_agent_and_finishes_with_facts(self):
        self.drive(r"""
  const calls = []; radioCall = (c) => calls.push(c);
  const buzz = []; CM.haptic = (k) => buzz.push(k);
  applyTasks({ tasks: [], workflows: [FIX] }); CM.selectWorkflow(FIX.id);
  const f = clone(FIX); f.steps[1].state = 'done'; f.steps[2].state = 'needs_you'; f.current = ['approve']; f.done = 2; f.state = 'needs_you';
  applyTasks({ tasks: [], workflows: [f] }); draw(1000);
  ok('one radio line for the change that matters most', calls.length === 1 && calls[0].name === 'Claude Code' && calls[0].line === 'Step 3 of 4: Look at the phone shots · ops');
  ok('one vibration while its card is open', buzz.join() === 'done');
  ok('gate 2 is done', __wfAudit().gates[1].state === 'done' && __wfAudit().gates[2].state === 'needs_you');
  const g = clone(f); g.steps[2].state = 'done'; g.steps[3].state = 'working'; g.current = ['docs']; g.done = 3; g.state = 'working';
  applyTasks({ tasks: [], workflows: [g] }); draw(1100);
  ok('a second change within 3 s waits for its window', calls.length === 1 && WF.radio.pend.has(FIX.id) && WF.radio.timers.has(FIX.id));
  WF.radio.last.set(FIX.id, Date.now() - 2000); wfRadioFlush(FIX.id);
  ok('a flush 2 s after the last line still holds it', calls.length === 1 && WF.radio.pend.has(FIX.id));
  WF.radio.last.set(FIX.id, Date.now() - 3100); wfRadioFlush(FIX.id);
  ok('...and says it once 3 s have passed, clearing the hold and its timer', calls.length === 2 && !WF.radio.pend.has(FIX.id) && !WF.radio.timers.has(FIX.id)
    && calls[1].line === 'Step 3 of 4: Look at the phone shots · ops');
  wfRadioFlush(FIX.id); ok('nothing held, nothing said', calls.length === 2);
  const c = __wfAudit().crawl;
  ok('the work moved to docs: the agent crawls from ops', c.active === true && c.from === 'area:ops' && c.to === 'area:docs' && __wfAudit().agentAt === 'area:ops'
    && __wfAudit().ribbons[0].filled === 3);
  WF.crawl.get(FIX.id).t0 -= 2100;
  ok('...and is at docs after 2 s', __wfAudit().agentAt === 'area:docs' && __wfAudit().crawl.active === false);
  const done = clone(FIX_DONE); done.updated = new Date().toISOString();
  applyTasks({ tasks: [], workflows: [done] });
  const a = __wfAudit();
  ok('the finish: the band, the banner\'s word', a.band === 'done' && FX.toast && FX.toast.word === 'WORKFLOW DONE' && FX.toast.title === 'Fix the phone labels');
  ok('the tally counts facts, never points', JSON.stringify(a.tally.final) === '{"steps":4,"retries":0,"checks":1,"elapsed_s":840}'
    && JSON.stringify(a.tally.lines) === '["What ran","Time","Who","Checks"]' && String(a.tally) === '4 steps · 0 retries · 1 check passed · 14 min');
  WF.band.tally.t0 = performance.now() - 50;
  const early = wfTallyNow(WF.band.tally);
  ok('it counts up at 30 a second or faster', early.elapsed_s > 0 && early.elapsed_s < 840 && early.steps <= 4);
  wfBandTick(); ok('a tick inside the count-up does not settle it', WF.band.settled !== true);
  WF.band.t0 = performance.now() - 1100; wfBandTick(); ok('the tick after it writes the final facts and settles', WF.band.settled === true);
  WF.band.until = Date.now() - 1; wfTickFrame(); ok('the lane\'s timer ends the band without a 2D frame', WF.band === null && __wfAudit().band === null);
  applyTasks({ tasks: [], workflows: [FIX] }); applyTasks({ tasks: [], workflows: [FIX_FAILED] });
  ok('a failed workflow raises the failed band', __wfAudit().band === 'failed');
  // a seat that runs the workflow: the end of its crawl asks for a new layout, made after layoutSystem, not inside it
  let placed = 0; const realPlace = place; place = () => { placed++; };
  const mine = clone(FIX); mine.by = { name: 'Claude Code', seat: 'seat:me' }; units.push({ uid: 'seat:me', kind: 'seat', status: 'WORKING', name: 'me', configured: true });
  applyTasks({ tasks: [], workflows: [mine] }); const moved = clone(mine); moved.steps[1].state = 'done'; moved.steps[2].state = 'done'; moved.steps[3].state = 'working'; moved.current = ['docs'];
  applyTasks({ tasks: [], workflows: [moved] }); WF.crawl.get(FIX.id).t0 -= 2100;
  const dock = CM.dockOverride(units.find((u) => u.uid === 'seat:me'));
  ok('the dock override answers without laying out again', dock === 'area:docs' && placed === 0 && WF.needPlace === true);
  draw(1500); ok('...the next frame lays out once', placed === 1 && WF.needPlace === false);
  place = realPlace; units.pop();
""")

    def test_replay_runs_on_the_scene_clock_with_the_engines_frames(self):
        frames = [[f["key"], f["state"]] for f in workflows.summary_frames(fixtures()["FIX"])]
        failed = fixtures()["FIX_FAILED"]
        fframes = workflows.summary_frames(failed)
        folds = [workflows.fold(fframes[:k], failed["steps"], detail=True) for k in range(len(fframes) + 1)]
        self.drive("  const PY_FRAMES = " + json.dumps(frames) + ";\n  const F_FRAMES = " + json.dumps(fframes)
                   + ";\n  const F_FOLDS = " + json.dumps(folds) + ";\n" + r"""
  // the fold is workflows.fold: from empty details each frame sets what it carries, at every point of the history
  const D = ['says', 'why', 'progress', 'output', 'artifact'];
  ok('every prefix of the frames folds as the engine folds it (states, says, why, progress, output, artifact)', F_FOLDS.every((P, k) => {
    const v = wfFold(FIX_FAILED, F_FRAMES, k);
    return v.steps.every((s) => s.state === P.steps[s.key] && D.every((d) => JSON.stringify(s[d] === undefined ? null : s[d]) === JSON.stringify(P.detail[s.key][d])))
      && (k < F_FRAMES.length || v.state === P.state); }));
  applyTasks({ tasks: [], workflows: [FIX] });
  ok('the frames of a workflow with no events are the engine\'s', JSON.stringify(wfSummaryFrames(FIX).map((x) => [x.key, x.state])) === JSON.stringify(PY_FRAMES));
  CM.wfReplay(FIX.id, { speed: 10 });
  const r = __wfAudit().replay;
  ok('replay ×10 drives the scene clock', CM.sceneClock.scale === 10 && r.frames === PY_FRAMES.length && r.endState === FIX.state && CM.selectedWorkflow() === FIX.id);
  ok('at its start nothing has happened, and every reader sees the replay', CM.wfList()[0].steps.every((s) => s.state === 'waiting'));
  wfReplaySeek(15);
  ok('half way: the work is under way', CM.wfList()[0].steps[0].state === 'done');
  wfReplaySeek(30);
  ok('at its end the live states', CM.wfList()[0].steps.map((s) => s.state).join() === FIX.steps.map((s) => s.state).join() && CM.wfList()[0].state === FIX.state);
  wfReplaySeek(0); WF.replay.playing = true; CM.sceneClock.scale = 10;
  for (let k = 0; k < 6; k++) { WF.replay.last -= 250; wfTickFrame(); }
  ok('the lane\'s timer plays it at ×10 with no 2D frame (3D, Today, a phone sheet)', Math.abs(__wfAudit().replay.t - 15) < 0.6 && CM.wfList()[0].steps[0].state === 'done');
  for (let k = 0; k < 8; k++) { WF.replay.last -= 250; wfTickFrame(); }
  ok('...to its end: it stops there and the world clock is back', __wfAudit().replay.t === 30 && __wfAudit().replay.playing === false && CM.sceneClock.scale === 1
    && CM.wfList()[0].state === FIX.state);
  wfReplayPlay(false);
  ok('pause: the world\'s clock stops, the replay stays', CM.sceneClock.scale === 0 && !!__wfAudit().replay);
  ok('Esc leaves the replay first, then the selection', CM.backLayer('esc') === true && __wfAudit().replay === null && CM.sceneClock.scale === 1
    && CM.selectedWorkflow() === FIX.id && CM.wfList()[0] === FIX && CM.backLayer('esc') === true && CM.selectedWorkflow() === null);
""")

    def test_shows_pointers_view_requests_and_the_seat_that_runs_it(self):
        self.drive(r"""
  applyTasks({ tasks: [], workflows: [FIX_SHOWS], shows: FIX_SHOWS.shows }); draw(1000);
  ok('no show on the overview', __wfAudit().shows.drawn === 0);
  const fu = CM.focusUid; CM.focusUid = () => 'area:ops'; draw(1100);
  const s = __wfAudit().shows;
  ok('in focus: the card and the chart, the author first', s.drawn === 1 && s.cards === 1 && s.charts === 1 && s.items.every((i) => i.text.startsWith('Claude Code: '))
    && s.items.find((i) => i.op === 'card').w <= 240 && s.items.find((i) => i.op === 'chart').w <= 160 && s.items.find((i) => i.op === 'chart').h <= 60);
  ok('...and the gates of the steps there', __wfAudit().gates.map((x) => x.key).join() === 'fix,approve');
  CM.focusUid = fu; draw(1200);
  CM.selectWorkflow(null);
  ok('a point action: the arrow\'s target and its words in the status line', CM.action({ action: 'point', target: 'area:ops', text: 'look here', seconds: 20 }) === true
    && __wfAudit().points[0].target === 'area:ops' && __wfAudit().caption.includes('look here'));
  ok('a point with no target is not taken', CM.action({ action: 'point' }) === false && CM.action({ action: 'warp' }) === false);
  lastInput = performance.now();
  CM.action({ action: 'focus', target: 'area:ops' });
  ok('a view request right after input asks first', __wfAudit().view.last === 'asked:focus:area:ops' && __wfAudit().view.toasts === 1 && __wfAudit().view.setting === 'idle');
  lastInput = performance.now() - 61000;
  CM.action({ action: 'focus', target: 'area:ops' });
  ok('after a minute idle it moves the view', __wfAudit().view.last === 'focus:area:ops');
  CM.action({ action: 'follow', workflow: FIX_SHOWS.id });
  ok('follow selects the workflow', CM.selectedWorkflow() === FIX_SHOWS.id && __wfAudit().view.last === 'follow:' + FIX_SHOWS.id);
  ok('the setting row', CM.settingsExtra.filter((r) => r.key === 'agents-move-view').length === 1);
  const mine = clone(FIX); mine.by = { name: 'Claude Code', seat: 'seat:me' };
  units.push({ uid: 'seat:me', kind: 'seat', status: 'WORKING', name: 'me', configured: true });
  applyTasks({ tasks: [], workflows: [mine] });
  ok('the seat that runs it docks where it works', CM.dockOverride(units.find((u) => u.uid === 'seat:me')) === 'area:ops' && CM.dockOverride({ uid: 'seat:other' }) === null);
  units.pop();
""")


    def test_shows_are_routed_by_each_ops_own_anchor_and_name_their_author(self):
        self.drive(r"""
  // a show as the bridge stores it: no workflow field, no show-level anchor, each op with its own (bridge.py _clean_op)
  const show = { id: 's-1', by: 'Claude Code', at: null, ttl: 300, expires_in: 300, ops: [
    { op: 'card', at: 'area:ops', title: 'Label check', rows: [['cut', '6']], text: 'two fit now', bar: [2, 6] },
    { op: 'chart', at: 'area:docs', label: 'label overlaps', values: [9, 4, 0], kind: 'bars' },
    { op: 'ring', at: 'area:ops', state: 'working', progress: { done: 1, total: 3 } },
    { op: 'beam', from: 'area:api', to: 'area:ops' }, { op: 'route', stops: ['area:api', 'area:ops', 'area:docs'] },
    { op: 'fx', at: 'area:ops', preset: 'sparkle', color: '#4ADE80', count: 24, life: 1.5, text: 'fixed' },
    { op: 'label', at: 'area:site', text: 'not on the route' }] };
  applyTasks({ tasks: [], workflows: [FIX], shows: [show] }); draw(1000);
  ok('the overview draws none', __wfAudit().shows.drawn === 0);
  const fu = CM.focusUid; CM.focusUid = () => 'area:ops'; draw(1100);
  let it = __wfAudit().shows.items;
  ok('focus on ops: the ops anchored there (and the beam and route that touch it), not docs\' chart nor site\'s label',
    it.map((x) => x.op).join() === 'card,ring,beam,route,fx' && __wfAudit().shows.drawn === 1);
  ok('every drawn op paints its author first, and the audit says the line it painted', it.every((x) => x.drawn === x.text && /^Claude Code: /.test(x.drawn))
    && it.find((x) => x.op === 'ring').drawn === 'Claude Code: working' && it.find((x) => x.op === 'beam').drawn === 'Claude Code: link');
  CM.focusUid = fu; CM.selectWorkflow(FIX.id); draw(1200); it = __wfAudit().shows.items;
  ok('a selected workflow shows the ops on its route (api, ops, docs) with no workflow field on the show', it.map((x) => x.op).join() === 'card,chart,ring,beam,route,fx'
    && __wfAudit().shows.cards === 1 && __wfAudit().shows.charts === 1);
  CM.selectWorkflow(null);
""")

    def test_the_chrome_lane_takes_over_the_fallbacks_and_the_glass_is_cleared_only_after_drawing(self):
        self.drive(r"""
  CM.chromeReady = false;   // the chrome module is in this page and ready at once: start from before it, to see the takeover
  applyTasks({ tasks: [], workflows: [] });
  draw(900); const c0 = WF.ovClears || 0; draw(1000); draw(1100);
  ok('nothing drawn on the glass, nothing cleared', (WF.ovClears || 0) === c0 && WF.ovDirty === false);
  applyTasks({ tasks: [], workflows: [FIX] }); CM.selectWorkflow(FIX.id); draw(1200); draw(1300);
  ok('after a frame that drew, the next one clears it once', WF.ovDirty === true && (WF.ovClears || 0) === c0 + 1);
  ok('the fallback holds the card until the chrome module is in', WF.card.host === 'box');
  const cards = [], slots = []; CM.railCard = (el, o) => cards.push([el, o]); CM.stripSet = (slot, c) => slots.push([slot, c]); CM.chromeReady = true;
  draw(1400);
  ok('chrome ready: the same card goes to the rail, the route to the status bar', cards.length === 1 && cards[0][0] === WF.card.el && cards[0][1].back === 'Workflows'
    && WF.card.host === 'rail' && slots.some((x) => x[0] === 'route' && x[1] === WF.strip.el) && WF.card.pushed === false);
  CM.selectWorkflow(null);
  ok('closing hands the rail back', cards[cards.length - 1][0] === null && slots[slots.length - 1][0] === 'route' && slots[slots.length - 1][1] === null);
""")

    def test_the_poll_and_the_paged_replay_as_a_page_the_bridge_served(self):
        self.drive(r"""
  ok('a page the bridge served is live', LIVE === true);
  const timers = []; setTimeout = (f, ms) => { timers.push({ f: f, ms: ms }); return timers.length; }; clearTimeout = () => {};
  const calls = []; let reply = {};
  api = async (path) => { if (/^\/workflows|^\/workflow\/replay/.test(path)) calls.push(path); return typeof reply === 'function' ? reply(path) : reply; };
  applyTasks({ tasks: [], workflows: [FIX] });
  ok('a working workflow arms the 1 s poll', WF.poll.on === true && WF.poll.timer > 0 && timers[timers.length - 1].ms === 1000);
  WF.tasksAt = 0; document.hidden = true; const n0 = timers.length;
  await wfPollTick();
  ok('a hidden page asks nothing and arms nothing (the page coming back arms it)', calls.length === 0 && WF.poll.timer === 0 && timers.length === n0);
  document.hidden = false;
  const moved = clone(FIX); moved.steps[1].state = 'done'; moved.current = ['approve']; moved.steps[2].state = 'working';
  reply = { workflows: [moved], shows: [], now: '2026-10-13T01:00:00Z' };
  const rev0 = cmWorld().rev; await wfPollTick();
  ok('a tick asks for the changes and lands them', calls[0] === '/workflows' && CM.wfList()[0].steps[1].state === 'done' && WF.poll.since === '2026-10-13T01:00:00Z'
    && cmWorld().rev === rev0 + 1 && WF.poll.n === 1 && WF.poll.timer > 0);
  reply = { workflows: [], shows: [], now: '2026-10-13T01:00:01Z' }; WF.poll.timer = 0;
  await wfPollTick();
  ok('an unchanged reply changes nothing but where the next one starts', calls[1] === '/workflows?since=2026-10-13T01%3A00%3A00Z' && cmWorld().rev === rev0 + 1
    && WF.poll.since === '2026-10-13T01:00:01Z');
  const done = clone(FIX_DONE); reply = { workflows: [done], shows: [], now: '2026-10-13T01:00:02Z' }; WF.poll.timer = 0;
  await wfPollTick();
  ok('a finished workflow ends the poll', WF.poll.on === false && WF.poll.timer === 0 && __wfAudit().band === 'done');
  // replay reads every page of the frames
  const all = wfSummaryFrames(FIX), half = Math.ceil(all.length / 2); calls.length = 0;
  reply = (path) => /&page=1$/.test(path) ? { frames: all.slice(half), page: 1, pages: 2 } : { frames: all.slice(0, half), page: 0, pages: 2 };
  applyTasks({ tasks: [], workflows: [FIX] });
  CM.wfReplay(FIX.id); await settle();
  ok('replay reads both pages, in order', calls.join() === '/workflow/replay?id=' + encodeURIComponent(FIX.id) + ',/workflow/replay?id=' + encodeURIComponent(FIX.id) + '&page=1'
    && __wfAudit().replay.frames === all.length && __wfAudit().replay.src === 'events');
""", live=True)


# headless Chrome: run each job (a window size, phone or not, one async expression) on one page, report each value
CDP_JOBS = r"""
import { readFileSync } from 'node:fs';
const [port, url, jobsFile, origin] = process.argv.slice(2);
const jobs = JSON.parse(readFileSync(jobsFile, 'utf8'));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let list;
for (let i = 0; i < 100 && !list; i++) { try { list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json(); } catch (e) { await sleep(100); } }
const target = list.find((t) => t.type === 'page');
const ws = new WebSocket(target.webSocketDebuggerUrl);
const pending = new Map(); let id = 0, blocked = 0, errors = [];
const send = (method, params = {}) => new Promise((res, rej) => { const i = ++id; pending.set(i, { res, rej }); ws.send(JSON.stringify({ id: i, method, params })); });
let onLoad = null;
ws.onmessage = (m) => {
  const j = JSON.parse(m.data);
  if (j.id && pending.has(j.id)) { const p = pending.get(j.id); pending.delete(j.id); if (j.error) p.rej(new Error(JSON.stringify(j.error))); else p.res(j.result); }
  else if (j.method === 'Fetch.requestPaused') {
    if (/^(file|data|about):/.test(j.params.request.url) || (origin && j.params.request.url.startsWith(origin + '/'))) send('Fetch.continueRequest', { requestId: j.params.requestId });
    else { blocked++; send('Fetch.failRequest', { requestId: j.params.requestId, errorReason: 'BlockedByClient' }); }
  } else if (j.method === 'Runtime.exceptionThrown') errors.push(j.params.exceptionDetails.exception?.description || j.params.exceptionDetails.text);
  else if (j.method === 'Runtime.consoleAPICalled' && j.params.type === 'error') errors.push('console: ' + j.params.args.map((a) => a.value ?? a.description).join(' '));
  else if (j.method === 'Page.loadEventFired' && onLoad) onLoad();
};
await new Promise((r) => { ws.onopen = r; });
await send('Fetch.enable', { patterns: [{ urlPattern: '*' }] });
await send('Runtime.enable'); await send('Page.enable');
const out = [];
for (const job of jobs) {
  errors = []; blocked = 0;
  await send('Emulation.setDeviceMetricsOverride', { width: job.w, height: job.h, deviceScaleFactor: 1, mobile: !!job.mobile });
  await send('Emulation.setTouchEmulationEnabled', { enabled: !!job.mobile });
  const loaded = new Promise((r) => { onLoad = r; });
  await send('Page.navigate', { url });
  await loaded; await sleep(1500);
  const r = await send('Runtime.evaluate', { returnByValue: true, awaitPromise: true, expression: job.expr });
  out.push({ name: job.name, value: r.exceptionDetails ? null : r.result.value,
    exception: r.exceptionDetails ? (r.exceptionDetails.exception?.description || r.exceptionDetails.text) : null, blocked, errors: errors.slice(0, 5) });
}
console.log('RESULT ' + JSON.stringify(out));
ws.close();
process.exit(0);
"""

DESK_EXPR = r"""(async () => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms)), out = {};
  typing = () => true;           // a page on disk reloads itself every 15 s unless a box has focus (render-game.py)
  setView('map'); await sleep(500);
  applyTasks({ tasks: [], workflows: [FIX] }); await sleep(400);
  out.overview = __wfAudit().overview; out.chip = __wfAudit().chips['area:ops'];
  CM.selectWorkflow(FIX.id); await sleep(1600);
  const a = __wfAudit();
  out.sel = { seg: a.ribbons[0].segments, gates: a.gates.length, slots: a.strip.slots, links: a.strip.links.join(), medals: a.strip.medals,
    strip: document.querySelectorAll('.wf-slot').length, card: !!document.querySelector('.wf-card') };   // the rail and the status bar or the fallback boxes
  // the working leg in pixels: cyan around its middle on the map canvas (blue well over red)
  const m = a.ribbons[0].mids.find((x) => x.state === 'working'), d = DPR, g = document.getElementById('c').getContext('2d');
  const px = g.getImageData(Math.round(m.x * d) - 8, Math.round(m.y * d) - 8, 17, 17).data; let cyan = 0;
  for (let i = 0; i < px.length; i += 4) cyan = Math.max(cyan, px[i + 2] - px[i]);
  out.cyan = cyan;
  out.deskActions = [...document.querySelectorAll('.wf-card [data-wf-action]')].map((e) => e.dataset.wfAction).sort().join();
  applyTasks({ tasks: [], workflows: [FIX_GATE] }); await sleep(400);
  out.gateActions = [...document.querySelectorAll('.wf-card [data-wf-action]')].map((e) => e.dataset.wfAction).sort().join();
  applyTasks({ tasks: [], workflows: [FIX_PLANNED] }); await sleep(400);
  out.planned = { ghost: __wfAudit().ribbons[0].ghost, start: !!document.querySelector('.wf-card [data-wf-action=start]') };
  CM.selectWorkflow(null); await sleep(200);
  out.closed = { card: !document.querySelector('.wf-card'), strip: !document.querySelector('.wf-slot') };
  // a finish while Today is up: the receipt band settles to its final facts on the module's own timer
  setView('today'); applyTasks({ tasks: [], workflows: [FIX] }); applyTasks({ tasks: [], workflows: [FIX_DONE] }); await sleep(1600);
  out.receipt = [...document.querySelectorAll('#wfband [data-row]')].map((e) => e.textContent);
  setView('map'); await sleep(300);
  // every op of a show paints its author: the lines the audit reports are the lines on the canvas
  const painted = [], ft = CanvasRenderingContext2D.prototype.fillText;
  CanvasRenderingContext2D.prototype.fillText = function (t) { painted.push(String(t)); return ft.apply(this, arguments); };
  const show = { id: 's-all', by: 'Claude Code', at: null, ttl: 300, expires_in: 300, ops: [
    { op: 'card', at: 'area:ops', title: 'Label check', rows: [['cut', '6']], bar: [2, 6] }, { op: 'chart', at: 'area:ops', label: 'label overlaps', values: [9, 4, 0], kind: 'spark' },
    { op: 'ring', at: 'area:ops', state: 'working', progress: { done: 1, total: 3 } }, { op: 'beam', from: 'area:api', to: 'area:ops' },
    { op: 'route', stops: ['area:api', 'area:ops', 'area:docs'] }, { op: 'fx', at: 'area:ops', preset: 'sparkle', color: '#4ADE80', count: 24, life: 3, text: 'fixed' },
    { op: 'label', at: 'area:ops', text: 'the slots' }, { op: 'marker', at: 'area:ops', icon: 'pin', text: 'here' }] };
  applyTasks({ tasks: [], workflows: [FIX], shows: [show] }); const fu = CM.focusUid; CM.focusUid = () => 'area:ops'; await sleep(500);
  const items = __wfAudit().shows.items; CanvasRenderingContext2D.prototype.fillText = ft; CM.focusUid = fu;
  out.painted = { ops: items.map((i) => i.op).join(), every: items.every((i) => painted.includes(i.drawn) && i.drawn.startsWith('Claude Code: ')) };
  // the bridge's control reply as the page's own poll hands it on (before the ship flies anywhere: a flight holds the queue)
  if (window.CONTROL) { acceptActions(window.CONTROL); await sleep(800);
    out.point = { target: (__wfAudit().points[0] || {}).target, line: (document.getElementById('sb-readout') || document.getElementById('wfstrip')).innerText }; }   // the chrome module's status bar says it
  activate(unitByUid('area:ops')); await sleep(700);
  const b = document.querySelector('[data-wf-new]'); out.cardButton = !!b; if (b) b.click(); await sleep(200);
  const n = document.getElementById('wfnew');
  out.connect = !!n && !n.hidden && /Connect an agent/.test(n.innerText) && !document.querySelector('[data-wf-create]');
  out.err = __wfAudit().err;
  return out; })()"""

PHONE_EXPR = r"""(async () => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms)), out = {};
  typing = () => true;
  applyTasks({ tasks: [], workflows: [FIX_GATE] }); CM.selectWorkflow(FIX_GATE.id); await sleep(700);
  out.mob = MOB; out.phone = __wfAudit().card.phone;
  out.actions = [...document.querySelectorAll('[data-wf-action]')].map((e) => e.dataset.wfAction).sort().join();
  applyTasks({ tasks: [], workflows: [FIX] }); await sleep(400);
  out.away = /On your computer/.test(document.querySelector('.wf-card').innerText) && !/: on your computer/.test(document.querySelector('.wf-card').innerText);
  out.noOwner = !document.querySelector('[data-wf-action=stop],[data-wf-action=start],[data-wf-action=retry],[data-wf-action=skip],[data-wf-action=new]');
  CM.selectWorkflow(null); setView('map'); activate(unitByUid('area:ops')); await sleep(700);
  out.noTemplate = !document.querySelector('[data-wf-new]');
  out.err = __wfAudit().err;
  return out; })()"""


@unittest.skipUnless(shutil.which("node"), "node not installed")
class Browser(Rig):
    """Headless Chrome on a page on disk; the point comes from a test bridge's control queue (POST /control as an
    agent, GET /control as the page reads it), its reply handed to the page as the page's own poll hands it on."""

    def test_the_map_the_card_the_phone_sheet_and_a_point_from_the_bridge(self):
        chrome = test_game.find_chrome()
        if not chrome:
            self.skipTest("needs a Chrome or Chromium binary (set CHROME_BIN)")
        time.sleep(0.6)            # the control queue's own gap since the bridge started
        status, got = self.http("POST", "/control", {"request_id": "r1", "action": {"type": "point", "target": "area:ops",
                                                                                     "text": "look here", "seconds": 20}}, self.agent)
        self.assertEqual(status, 202, got)
        status, control = self.http("GET", "/control?after=0", headers=self.agent)
        self.assertEqual(status, 200, control)
        self.assertEqual([c["action"] for c in control["commands"]],
                         [{"type": "point", "target": "area:ops", "text": "look here", "seconds": 20, "by": "an agent"}])   # the bridge: a control names its author
        with tempfile.TemporaryDirectory(prefix="cm-wfmap-c-", ignore_cleanup_errors=True) as tmp:
            page = render_page(tmp)
            head = macros_js() + "window.CONTROL = " + json.dumps({"boot_id": control["boot_id"], "commands": control["commands"]}) + ";\n"
            jobs = [{"name": "desk", "w": 1600, "h": 900, "mobile": False, "expr": "(() => {" + head + "return " + DESK_EXPR + "; })()"},
                    {"name": "phone", "w": 390, "h": 844, "mobile": True, "expr": "(() => {" + macros_js() + "return " + PHONE_EXPR + "; })()"}]
            runner, jobs_file, profile = Path(tmp, "jobs.mjs"), Path(tmp, "jobs.json"), Path(tmp, "profile")
            runner.write_text(CDP_JOBS, encoding="utf-8")
            jobs_file.write_text(json.dumps(jobs), encoding="utf-8")
            browser = subprocess.Popen(
                [chrome, "--headless=new", "--no-sandbox", "--disable-gpu", "--remote-debugging-port=0", f"--user-data-dir={profile}",
                 "--no-first-run", "--no-default-browser-check", "--remote-allow-origins=*", "--allow-file-access-from-files",
                 "--disable-background-networking", "--disable-component-update", "--no-pings", "--hide-scrollbars",
                 "--host-resolver-rules=MAP * ~NOTFOUND", "--proxy-server=http://127.0.0.1:9", "--proxy-bypass-list=<-loopback>", "about:blank"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                port_file = profile / "DevToolsActivePort"
                deadline = time.time() + 30
                while time.time() < deadline and not (port_file.exists() and port_file.read_text().strip()):
                    if browser.poll() is not None:
                        self.fail(f"{chrome} exited with {browser.returncode}")
                    time.sleep(0.1)
                port = port_file.read_text().splitlines()[0]
                run = subprocess.run(["node", str(runner), port, page.as_uri(), str(jobs_file)], capture_output=True, text=True,
                                     timeout=240)
            finally:
                browser.terminate()
                try:
                    browser.wait(10)
                except subprocess.TimeoutExpired:
                    browser.kill()
                    browser.wait()
        line = next((ln for ln in run.stdout.splitlines() if ln.startswith("RESULT ")), None)
        self.assertIsNotNone(line, (run.stdout + run.stderr)[-3000:])
        res = {r["name"]: r for r in json.loads(line[len("RESULT "):])}
        for name, r in res.items():
            self.assertIsNone(r["exception"], (name, r))
            self.assertEqual((r["errors"], r["blocked"]), ([], 0), (name, r))
        desk, phone = res["desk"]["value"], res["phone"]["value"]
        self.assertEqual(desk["overview"], {"ribbons": 0, "gates": 0, "shows": 0}, desk)
        self.assertEqual(desk["chip"], "Fix the phone labels · 2 of 4")
        self.assertEqual(desk["sel"], {"seg": 2, "gates": 4, "slots": 4, "links": "done,working,waiting", "medals": 1, "strip": 4, "card": True})
        self.assertGreaterEqual(desk["cyan"], 60, desk)                    # #6FB7FF: blue 255 over red 111
        self.assertEqual(desk["deskActions"], "stop")
        self.assertEqual(desk["gateActions"], "gate-fail,gate-pass,stop")
        self.assertEqual(desk["planned"], {"ghost": True, "start": True})
        self.assertEqual(desk["closed"], {"card": True, "strip": True})
        self.assertEqual(desk["receipt"], ["4 of 4 steps done · no retries", "14 min", "Claude Code · second opinion by codex", "1 second opinion passed"])
        self.assertEqual(desk["painted"], {"ops": "card,chart,ring,beam,route,fx,label,marker", "every": True})
        self.assertTrue(desk["cardButton"] and desk["connect"], desk)
        self.assertEqual(desk["point"]["target"], "area:ops")
        self.assertIn("look here", desk["point"]["line"])
        self.assertFalse(desk["err"])
        self.assertTrue(phone["mob"] and phone["phone"], phone)
        self.assertEqual(phone["actions"], "gate-fail,gate-pass")
        self.assertTrue(phone["away"] and phone["noOwner"] and phone["noTemplate"], phone)
        self.assertFalse(phone["err"])


SLOW = ["/bin/sh", "-c", "cat >/dev/null; sleep 5; echo made > made.txt; echo '{\"type\":\"result\"}'"]

LIVE_DESK = r"""(async () => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms)), out = {}, until = async (f, ms) => { const t0 = Date.now(); while (Date.now() - t0 < ms) { try { if (f()) return true; } catch (e) {} await sleep(100); } return false; };
  setView('map'); await sleep(500);
  activate(unitByUid('area:ops')); await sleep(700);
  document.querySelector('[data-wf-new]').click(); await sleep(300);
  document.querySelector('[data-wf-tpl=fix-and-check]').click();
  document.getElementById('wfgoal').value = 'Write made.txt';
  document.querySelector('[data-wf-create]').click();
  out.created = await until(() => __wfAudit().selected && __wfAudit().selected.state === 'planned' && __wfAudit().ribbons[0] && __wfAudit().ribbons[0].ghost === true
    && !!document.querySelector('.wf-card [data-wf-action=start]'), 10000);
  out.id = CM.selectedWorkflow();
  document.querySelector('.wf-card [data-wf-action=start]').click();
  out.started = await until(() => __wfAudit().state === 'working', 15000);
  out.polled = await until(() => __wfAudit().poll.on === true && __wfAudit().poll.n >= 2, 8000);
  out.pollErr = __wfAudit().poll.err;
  out.after = await until(() => __wfAudit().state !== 'working', 30000);
  out.state = __wfAudit().state; await sleep(1500);
  out.pollOff = __wfAudit().poll.on === false && __wfAudit().poll.n >= 2;
  out.err = __wfAudit().err;
  return out; })()"""

LIVE_PHONE = r"""(async () => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms)), out = {}, until = async (f, ms) => { const t0 = Date.now(); while (Date.now() - t0 < ms) { try { if (f()) return true; } catch (e) {} await sleep(100); } return false; };
  out.arrived = await until(() => CM.wfList().some((w) => w.id === window.GATE_ID && w.state === 'needs_you'), 10000);
  CM.selectWorkflow(window.GATE_ID); await sleep(500);
  out.mob = MOB; out.actions = [...document.querySelectorAll('[data-wf-action]')].map((e) => e.dataset.wfAction).sort().join();
  document.querySelector('[data-wf-action=gate-pass]').click();
  out.done = await until(() => __wfAudit().state === 'done', 10000);
  out.err = __wfAudit().err;
  return out; })()"""


@unittest.skipUnless(shutil.which("node"), "node not installed")
class LiveBridge(unittest.TestCase):
    """The real page served by bridge.py (its UI cookie, Origin and fetch headers), one fake agent that takes 5 s."""

    def test_create_start_the_poll_and_the_phones_approve(self):
        chrome = test_game.find_chrome()
        if not chrome:
            self.skipTest("needs a Chrome or Chromium binary (set CHROME_BIN)")
        import http.client
        with tempfile.TemporaryDirectory(prefix="cm-wfmap-l-", ignore_cleanup_errors=True) as tmp:
            root = Path(tmp, "work")
            for name in PLANETS:
                (root / name).mkdir(parents=True)
                (root / name / "main.py").write_text("print('fixture')\n", encoding="utf-8")
            proc = test_game.render_fixture(tmp, detect_runners=False, runners={"slow": {"cmd": SLOW, "max_minutes": 1}},
                                            workflows_dir=str(Path(tmp, "workflows")))
            self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
            page, cfg, live = Path(tmp, "page.html"), Path(tmp, "fixture.json"), Path(tmp, "live.json")
            sock = __import__("socket").socket(); sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]; sock.close()
            env = dict(os.environ, CONTAINER_CONFIG=str(cfg), CONTAINER_OUT=str(page))
            env.pop("BRIDGE_PORT", None)
            bridge = subprocess.Popen([sys.executable, str(ROOT / "bridge.py"), "--port", str(port), "--seats", str(Path(tmp, "no-seats.json")),
                                       "--game", str(page), "--renderer", str(ROOT / "render-game.py"), "--live-file", str(live),
                                       "--log-file", str(Path(tmp, "bridge.log"))], cwd=tmp, env=env,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            browser = None
            try:
                deadline = time.time() + 30
                while time.time() < deadline:
                    try:
                        if json.loads(live.read_text(encoding="utf-8")).get("pid") == bridge.pid:
                            break
                    except (OSError, ValueError):
                        pass
                    self.assertIsNone(bridge.poll(), "the bridge exited")
                    time.sleep(0.1)
                token = json.loads(live.read_text(encoding="utf-8"))["agent_token"]

                def call(path, body, extra=None):
                    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
                    conn.request("POST", path, body=json.dumps(body), headers=dict({"Authorization": "Bearer " + token,
                                                                                    "Content-Type": "application/json"}, **(extra or {})))
                    r = conn.getresponse(); got = json.loads(r.read() or b"{}"); conn.close()
                    return r.status, got
                # the phone's workflow: an agent's note step, then the owner's gate
                ops = str(root / "ops")
                status, made = call("/agent/workflow", {"title": "Phone gate", "by": {"name": "Claude Code"}, "steps": [
                    {"key": "read", "label": "Read it", "at": ops, "kind": "note"},
                    {"key": "ok", "label": "Look at it", "at": ops, "kind": "gate", "after": ["read"]}]})
                self.assertEqual(status, 201, made)
                status, got = call("/agent/workflow/step", {"id": made["id"], "key": "read", "state": "done"}, {"X-Workflow-Token": made["token"]})
                self.assertEqual(status, 200, got)
                profile = Path(tmp, "profile")
                runner, jobs_file = Path(tmp, "jobs.mjs"), Path(tmp, "jobs.json")
                runner.write_text(CDP_JOBS, encoding="utf-8")
                jobs_file.write_text(json.dumps([
                    {"name": "desk", "w": 1600, "h": 900, "mobile": False, "expr": LIVE_DESK},
                    {"name": "phone", "w": 390, "h": 844, "mobile": True, "expr": "(() => { window.GATE_ID = " + json.dumps(made["id"]) + "; return " + LIVE_PHONE + "; })()"}]),
                    encoding="utf-8")
                browser = subprocess.Popen(
                    [chrome, "--headless=new", "--no-sandbox", "--disable-gpu", "--remote-debugging-port=0", f"--user-data-dir={profile}",
                     "--no-first-run", "--no-default-browser-check", "--remote-allow-origins=*", "--disable-background-networking",
                     "--disable-component-update", "--no-pings", "--hide-scrollbars", "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
                     "about:blank"],     # the bridge's own address resolves; no dead proxy ("<-loopback>" would send it there)
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                port_file = profile / "DevToolsActivePort"
                deadline = time.time() + 30
                while time.time() < deadline and not (port_file.exists() and port_file.read_text().strip()):
                    if browser.poll() is not None:
                        self.fail(f"{chrome} exited with {browser.returncode}")
                    time.sleep(0.1)
                origin = f"http://127.0.0.1:{port}"
                run = subprocess.run(["node", str(runner), port_file.read_text().splitlines()[0], origin + "/", str(jobs_file), origin],
                                     capture_output=True, text=True, timeout=300)
                # the bridge's own word on the phone's gate
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
                conn.request("GET", "/workflow?id=" + made["id"], headers={"Authorization": "Bearer " + token})
                detail = json.loads(conn.getresponse().read()); conn.close()
            finally:
                if browser is not None:
                    browser.terminate()
                    try:
                        browser.wait(10)
                    except subprocess.TimeoutExpired:
                        browser.kill()
                        browser.wait()
                bridge.send_signal(__import__("signal").SIGINT)
                try:
                    bridge.wait(10)
                except subprocess.TimeoutExpired:
                    bridge.kill()
                    bridge.wait()
        line = next((ln for ln in run.stdout.splitlines() if ln.startswith("RESULT ")), None)
        self.assertIsNotNone(line, (run.stdout + run.stderr)[-3000:])
        res = {r["name"]: r for r in json.loads(line[len("RESULT "):])}
        for name, r in res.items():
            self.assertIsNone(r["exception"], (name, r))
            self.assertEqual((r["errors"], r["blocked"]), ([], 0), (name, r))
        desk, phone = res["desk"]["value"], res["phone"]["value"]
        # Create: a planned workflow, selected, its ghost route and Start; Start: its agent works; the 1 s poll runs while it
        # works and stops once it does not
        self.assertTrue(desk["created"] and desk["started"], desk)
        self.assertTrue(desk["polled"] and desk["after"] and desk["pollOff"], desk)
        self.assertEqual(desk["pollErr"], "")
        self.assertNotEqual(desk["state"], "working")
        self.assertFalse(desk["err"])
        # the phone: the gate's two buttons only, Approve pressed, the bridge passed the gate and the page shows it done
        self.assertTrue(phone["arrived"] and phone["mob"], phone)
        self.assertEqual(phone["actions"], "gate-fail,gate-pass")
        self.assertTrue(phone["done"], phone)
        self.assertEqual(detail["workflow"]["state"], "done")
        self.assertTrue(any(e.get("ev") == "gate" and e.get("key") == "ok" for e in detail["events"]), detail["events"][-5:])
        self.assertFalse(phone["err"])


if __name__ == "__main__":
    unittest.main()
