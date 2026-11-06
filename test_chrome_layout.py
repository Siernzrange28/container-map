#!/usr/bin/env python3
"""test_chrome_layout.py — the desktop chrome: the map owns the window minus a 400 px
rail and a 44 px status bar, the retired boxes keep their ids hidden, a card opens in the rail with a back link, hovering
rewrites the status bar's readout (no floating tooltip), the "?" sheet lists the settings rows the 3D lanes push, the composer
is the rail's card, the "Press I to fly in 3D" chip shows once, the screen reader has its list and one polite region.

Run: python3 -m unittest -q test_chrome_layout   (node and Chrome needed for the browser half; else those tests skip)
"""
import re
import unittest
from pathlib import Path

import testenv  # noqa: F401  (first: clears what steers the tool)
import test_game

ROOT = Path(__file__).resolve().parent
SRC = test_game.RENDER_SRC
CHROME_JS = (ROOT / "ui" / "chrome.js").read_text(encoding="utf-8")
CHROME_CSS = (ROOT / "ui" / "chrome.css").read_text(encoding="utf-8")
A11Y_JS = (ROOT / "ui" / "a11y.js").read_text(encoding="utf-8")
A11Y_CSS = (ROOT / "ui" / "a11y.css").read_text(encoding="utf-8")
RETIRED = ("dash", "hud", "comms", "ticker", "nums", "topbar", "taskbar", "quick", "theme", "keyline", "help", "legend", "mateshow")
DESK = "html:not(.m)"


def markup():
    start = SRC.index('<body><div id="wrap">')
    return SRC[start:SRC.index("</div><script id=\"cm-sfx\">", start)]


class ChromeSourceTests(unittest.TestCase):
    def test_the_new_boxes_are_in_the_markup_with_walls_and_no_inline_style(self):
        m = markup()
        for box in ("rail", "statusbar"):
            self.assertRegex(m, rf'<[a-z]+ id="{box}" data-wall="{box}"')
        self.assertIn('<div id="a11ymap" role="list"', m)
        self.assertRegex(m, r'id="a11ylive"[^>]*aria-live="polite"')
        self.assertIn('data-chip="press-i"', m)
        self.assertIn('<button type="button" class="sbbtn" data-act="fly3d"', m)
        new = m[m.index('<aside id="rail"'):m.index('<div id="a11ymap"')]
        self.assertNotIn("style=", new, "the served page's CSP refuses style attributes in markup")

    def test_every_retired_box_keeps_its_id_and_the_desktop_hides_it(self):
        m = markup()
        for box in RETIRED:
            if box in ("topbar", "keyline"):
                self.assertIn(f"'{box}'", SRC, box + " is built by the page")
            else:
                self.assertRegex(m, rf'id="{box}"', box)
        rule = next(r for r in CHROME_CSS.split("\n") if r.startswith(DESK + " :is(#dash"))
        for box in RETIRED:
            self.assertIn("#" + box, rule.split("{")[0])
        self.assertIn("display:none!important", rule)

    def test_the_seams_are_this_lanes(self):
        for hook in ("CM.railCard = function (el, opts)", "CM.stripSet = function (slot, content)", "window.__chromeAudit = function ()",
                     "CM.openHelp = function (tab)", "CM.chromeReady = true"):
            self.assertIn(hook, CHROME_JS)
        self.assertIn("CM.say = function (text)", A11Y_JS)
        # chrome.js runs nothing at load: its start-up is in CM.onInit
        top = [ln for ln in CHROME_JS.split("\n") if re.match(r"^[a-zA-Z_$][\w$.]*\(", ln) and not ln.startswith("CM.onInit.push(")]
        self.assertEqual(top, [], "no call at the top level")

    def test_colours_are_tokens_the_theme_rewriter_leaves_alone(self):
        for name, css in (("chrome.css", CHROME_CSS), ("a11y.css", A11Y_CSS)):
            bodies = "".join(re.findall(r"\{([^{}]*)\}", css))
            self.assertIsNone(re.search(r"#[0-9a-fA-F]{3,8}\b", bodies), name + ": colours as rgb()")
        for token in ("rgb(255,90,95)", "rgb(255,181,71)", "rgb(111,183,255)", "rgb(74,222,128)", "rgb(232,238,246)", "rgb(154,168,188)"):
            self.assertIn(token, CHROME_CSS)

    def test_the_plates_keep_out_of_the_chips_and_the_old_tail_holds(self):
        line = next(ln for ln in SRC.split("\n") if ln.startswith("const PLATE_OVERLAYS=["))
        ids = re.findall(r"'([\w-]+)'", line)
        self.assertEqual(ids[-2:], ["quests", "radio"])
        for chip in ("machines", "legendchip", "pressi", "crumb"):
            self.assertIn(chip, ids)

    def test_the_fixes_of_the_review_round_are_in_the_source(self):
        # S8: the rail's signature carries the words a row shows; the thumbnail cache key carries the purpose it drew; one contact resolver
        self.assertIn("chromeAgentDoing(u) + ':' + (u.cwd || '')", CHROME_JS)
        self.assertIn("(t.title || '') + ':' + (t.target || '')", CHROME_JS)
        self.assertIn("(L ? L.purpose + ':' + (L.nightLights ? 1 : 0) : '')", CHROME_JS)
        self.assertIn("if (k === 'open') { const c = chromeContact(a.getAttribute('data-cid'));", CHROME_JS)
        self.assertNotIn("skyContacts().find((x) => x.id === a.getAttribute('data-cid'))", CHROME_JS)
        # S7: the seam's fourth slot by its name; S2: the list's keys ahead of the 3D view; S6: one path for ] and [
        self.assertNotIn("STRIP_ALIAS", CHROME_JS)   # the slot is 'ask' everywhere: no alias named after a skin's character
        self.assertIn("if(A11Y_LIST_KEYS.test(String(e.key||''))&&e.target&&e.target.closest&&e.target.closest('#a11ymap')){ e.stopImmediatePropagation(); if(e.key!=='Tab'&&typeof a11yKeys==='function') a11yKeys(e); } },true);\naddEventListener('keydown',e=>{ if(!typing()&&!modKey(e)&&CM.key3d(e))", SRC)
        self.assertIn("if(CM.chromeReady&&CM.chromeOn&&CM.chromeOn()) return;   // the desktop's rail answers ] and [", SRC)
        # S10: the retired boxes' painters rest while hidden; S13: the agent card's words are the desktop's; K3: check-only runners are no hand
        for line in ("function chromeHides(id){", "if(!chromeHides('comms')) commsTick(); if(!chromeHides('ticker')) stripTick();",
                     "if(!tb||chromeHides('ticker')) return;", "!VEC&&!MOB ? chromeUnitCardHtml(u,rows,extra)",
                     "runners=(W.task_runners||[]).filter(r=>r&&r.role!=='check')", '<button type="button" id="railnew" class="cmbtn" data-act="newtask"',
                     "&&!(CM.chromeReady&&CM.chromeOn&&CM.chromeOn())) mateDraw(performance.now())"):
            self.assertIn(line, SRC, line)
        # C7: titles as sentences; C10: the composer's copy names no config file; C9: the board's words
        self.assertIn("CM.shortTitle(taskTitle(t),140,true)", SRC)   # taskTitle: a title the person renamed wins
        self.assertIn("E(ship ? chromeAgentName(u) : CM.shortTitle(", CHROME_JS)
        connect = re.search(r'<div class="connect" id="cconnect" hidden>(.*?)</div>', SRC).group(1)
        self.assertNotIn("container.json", connect)
        self.assertIn("Set up", connect)
        self.assertIn('<div id="quests"><h2>Decisions</h2>', SRC)
        self.assertNotIn("<b>HOW THIS WORKS</b>", SRC)
        # S5: the crumb's separator reads; S9: tablet targets; C2: a narrow window's rail
        self.assertIn("#crumb .sep{color:rgb(127,141,162)}", CHROME_CSS)
        self.assertIn("html.t #helpsheet .srow select{height:44px}", CHROME_CSS)
        self.assertIn("@media (max-width:1100px){html:not(.m) #wrap,html:not(.m) #wrap.side,html:not(.m) #wrap.dash{--railw:320px}}", CHROME_CSS)
        # S3: one region, the radio read whole; the H key is on the sheet
        self.assertIn("function a11yOneRegion()", A11Y_JS)
        self.assertIn("A11Y.radioT = setTimeout(", A11Y_JS)
        self.assertIn("K(['H'], 'More stats')", CHROME_JS)

    def test_no_raw_words_in_the_chrome(self):
        for word in ("no agent yet", "NEW TASK", "Goal:", "COMMS", "RECEIPTS", "bogeys", "in warp"):
            self.assertNotIn(word, CHROME_JS + A11Y_JS, word)
        self.assertNotIn("no agent yet", SRC)


# the steps run in one page of the fixture (two folders) at 1600x900 unless a test says otherwise
SETUP = "setView('map'); await sleep(500);"


class ChromeBrowserTests(unittest.TestCase):
    setUpClass = classmethod(test_game.HardeningBrowserTests.setUpClass.__func__)
    tearDownClass = classmethod(test_game.HardeningBrowserTests.tearDownClass.__func__)
    run_steps = test_game.HardeningBrowserTests.run_steps

    def test_a_changed_finding_keeps_its_grey_quote_without_counting_as_unfinished(self):
        body = ("setView('map'); const t={id:'invented:changed',title:'Invented changed thread',"
                "state:'ready',unfinished_n:0,children:0,resume:{kind:'claude',command:'claude --resume invented'},"
                "unfinished:[{what:'Check invented draft',evidence:'Invented quote',owes:'him',changed_since_read:true}]};"
                "CM.openThread(t); await sleep(300); const q=document.querySelector('.finding-changed blockquote');"
                "return {colour:getComputedStyle(q).color,opacity:getComputedStyle(q).opacity,"
                "hue:CM.constellationColour(t),text:q.closest('article').textContent};")
        got = self.run_steps(1600, 900, [{"name": "changed", "eval": body}])["changed"]
        self.assertEqual((got["colour"], got["opacity"], got["hue"]),
                         ("rgb(100, 116, 139)", "0.65", "#64748B"))
        self.assertIn("Changed since read", got["text"])
        self.assertIn("Invented quote", got["text"])
        self.assertIn("0 unfinished", got["text"])

    def test_the_map_is_free_and_the_card_opens_in_the_rail(self):
        steps = [{"name": "map", "eval": SETUP + " const c = __chromeAudit(); return { free: c.mapFree, visible: c.visible, rail: c.rail, cls: document.documentElement.className,"
                  " stage: (r => [r.left, r.top, r.width, r.height])(document.getElementById('stage').getBoundingClientRect()),"
                  " bar: (r => [r.left, r.top, r.width, r.height])(document.getElementById('statusbar').getBoundingClientRect()),"
                  " rail2: (r => [r.left, r.top, r.width, r.height])(document.getElementById('rail').getBoundingClientRect()), ready: CM.chromeReady };"},
                 {"name": "card", "eval": "activate(unitByUid(MOONS[0].uid)); await sleep(400); const c = __chromeAudit();"
                  " return { rail: c.rail, free: c.mapFree, inRail: !!document.querySelector('#rail #panel #pbody .pc'), panelW: PANEL_W,"
                  " cut: [...document.querySelectorAll('#rail *')].filter(e => e.scrollWidth > e.clientWidth + 1).map(e => e.tagName + '.' + e.className),"
                  " back: document.getElementById('railback').textContent, fly: !!document.querySelector('#rail [data-act=fly3d]'),"
                  " crumb: document.getElementById('crumb').hidden ? null : document.getElementById('crumb').textContent, name: unitByUid(MOONS[0].uid).name };"},
                 {"name": "esc", "eval": "document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); await sleep(300);"
                  " return { rail: __chromeAudit().rail, panelHome: document.getElementById('panel').parentNode.id, crumb: document.getElementById('crumb').hidden };"}]
        got = self.run_steps(1600, 900, steps)
        m, card, esc = got["map"], got["card"], got["esc"]
        self.assertEqual(m["cls"], "")
        self.assertTrue(m["ready"])
        self.assertEqual(m["stage"], [0, 0, 1200, 856])
        self.assertEqual(m["bar"], [0, 856, 1200, 44])
        self.assertEqual(m["rail2"], [1200, 0, 400, 900])
        self.assertGreaterEqual(m["free"]["w"], 1150)
        self.assertGreaterEqual(m["free"]["h"], 840)
        for box in ("dash", "hud", "comms", "ticker", "topbar", "taskbar"):
            self.assertNotIn(box, m["visible"])
        self.assertEqual(m["rail"]["content"], "tabs")
        self.assertEqual(card["rail"]["content"], "card")
        self.assertTrue(card["inRail"], "the planet card is the rail's content")
        self.assertGreaterEqual(card["free"]["w"], 1150)
        self.assertEqual(card["panelW"], 0, "a card in the rail covers none of the map")
        self.assertEqual(card["cut"], [], "nothing in the rail is cut")
        self.assertEqual(card["back"], "← For you")
        self.assertTrue(card["fly"])
        self.assertTrue(card["crumb"] and card["crumb"].endswith(card["name"]), card["crumb"])
        self.assertTrue(esc["crumb"], "the crumb goes with the card")
        self.assertEqual(esc["rail"]["content"], "tabs", "Esc closes the card and gives the tabs back")
        self.assertEqual(esc["panelHome"], "wrap", "the card's box goes back where it lives")

    def test_counts_contrast_a11y_and_the_settings_rows(self):
        steps = [{"name": "a", "eval": SETUP + " const c = __chromeAudit(), w = __wordsAudit();"
                  " return { contrast: c.contrast, rows: c.settings.rows, a11y: document.querySelectorAll('#a11ymap [role=listitem]').length, moons: MOONS.length,"
                  " live: document.querySelectorAll('[aria-live]:not([aria-live=off])').length, words: w.ok, bar: w.surfaces.filter(s => s.where === 'sb-counts').map(s => s.noun) };"},
                 {"name": "sheet", "eval": "CM.openHelp('settings'); await sleep(200); const a = __chromeAudit();"
                  " return { help: a.help, shown: a.settings.shown, cut: [...document.querySelectorAll('#helpsheet *')].filter(e => e.scrollWidth > e.clientWidth + 1).length };"},
                 {"name": "closed", "eval": "document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); await sleep(200); return __chromeAudit().help;"}]
        got = self.run_steps(1600, 900, steps)
        a = got["a"]
        self.assertGreaterEqual(a["contrast"]["min"], 4.5, a["contrast"])
        self.assertGreaterEqual(a["a11y"], a["moons"])
        self.assertEqual(a["live"], 1, "one polite region on the desktop")
        self.assertTrue(a["words"], "every count on screen is W.counts' number")
        self.assertEqual(a["bar"], ["need_you", "decisions", "problems"])
        three_d = ["render-scale", "fps-cap", "antialias", "hud-aspect", "glass", "reduce-flashing"]
        for key in three_d:
            self.assertIn(key, a["rows"])
            self.assertIn(key, got["sheet"]["shown"])
        self.assertEqual(got["sheet"]["help"], "settings")
        self.assertEqual(got["sheet"]["cut"], 0)
        self.assertIsNone(got["closed"], "Esc closes the sheet")

    def test_hover_rewrites_the_readout_and_nothing_floats(self):
        steps = [{"name": "h", "eval": SETUP + " const m = MOONS[0], r = cv.getBoundingClientRect();"
                  " cv.dispatchEvent(new PointerEvent('pointermove', { clientX: r.left + (m.x - camx) * ZOOM, clientY: r.top + (m.y - camy) * ZOOM, pointerType: 'mouse', bubbles: true }));"
                  " await sleep(400); return { readout: __chromeAudit().readout, shown: document.getElementById('sb-readout').textContent, name: CM.labelText(m.uid), hover: !!hover,"
                  " tip: !!document.querySelector('.tip:not([hidden])') };"},
                 {"name": "gone", "eval": "cv.dispatchEvent(new PointerEvent('pointerleave', { pointerType: 'mouse' })); await sleep(300); return __chromeAudit().readout;"},
                 {"name": "strip", "eval": "CM.stripSet('readout', 'look here'); await sleep(150); const a = __chromeAudit().readout; CM.stripSet('route', 'Step 1 · Step 2');"
                  " await sleep(150); const r = { readout: a, route: __chromeAudit().route, hidden: document.getElementById('sb-readout').hidden };"
                  " CM.stripSet('route', null); CM.stripSet('readout', null); await sleep(150); r.back = __chromeAudit().readout; r.again = document.getElementById('sb-readout').hidden; return r;"}]
        got = self.run_steps(1600, 900, steps)
        h = got["h"]
        self.assertTrue(h["hover"])
        self.assertIn(h["name"], h["readout"])
        self.assertEqual(h["readout"], h["shown"])
        self.assertFalse(h["tip"])
        self.assertTrue(got["gone"].startswith("Updated"), got["gone"])
        s = got["strip"]
        self.assertEqual(s["readout"], "look here")
        self.assertTrue(s["route"] and s["hidden"], "a route takes the middle")
        self.assertTrue(s["back"].startswith("Updated") and s["again"] is False)

    def test_fly_in_3d_entries_and_the_press_i_chip_once(self):
        steps = [{"name": "fly", "eval": SETUP + " window.__e3 = []; CM.enter3d = (o) => __e3.push(o || {});"
                  " document.querySelector('#statusbar [data-act=fly3d]').click(); activate(unitByUid(MOONS[0].uid)); await sleep(200);"
                  " document.querySelector('#rail [data-act=fly3d]').click(); return { n: __e3.length, target: __e3[1] && __e3[1].target, uid: MOONS[0].uid };"},
                 {"name": "chip", "eval": "closeAll(); await sleep(200); const k = (t) => dispatchEvent(new KeyboardEvent(t, { key: 'ArrowRight', bubbles: true }));"
                  " k('keydown'); await sleep(3200); k('keyup'); await sleep(150); const e = document.querySelector('[data-chip=press-i]');"
                  " const first = { shown: !!e && e.offsetParent !== null, text: e.innerText };"
                  " k('keydown'); await sleep(3200); k('keyup'); await sleep(150); return { first, again: e.offsetParent !== null };"}]
        got = self.run_steps(1600, 900, steps)
        self.assertEqual(got["fly"]["n"], 2)
        self.assertEqual(got["fly"]["target"], got["fly"]["uid"])
        self.assertTrue(got["chip"]["first"]["shown"])
        self.assertEqual(got["chip"]["first"]["text"], "Press I to fly in 3D")
        self.assertFalse(got["chip"]["again"], "once a session")

    def test_the_composer_is_the_rails_card_and_says_connect_an_agent(self):
        steps = [{"name": "none", "eval": SETUP + " W.task_runners = []; openComposer(null); await sleep(300); const t = document.body.innerText;"
                  " return { connect: /Connect an agent/.test(t), old: /no agent yet/i.test(t), sheet: __chromeAudit().composer.sheet, rail: __chromeAudit().rail.content };"},
                 {"name": "aim", "eval": "document.getElementById('ccancel').click(); await sleep(200); const u = unitByUid(MOONS[0].uid); activate(u); openComposer(u); await sleep(300);"
                  " const c = __chromeAudit().composer; return { c, path: u.path, cut: [...document.querySelectorAll('#composer option,#composer label')].filter(e => e.scrollWidth > e.clientWidth + 1).length };"},
                 {"name": "cancel", "eval": "document.getElementById('ccancel').click(); await sleep(300); return __chromeAudit().rail.content;"}]
        got = self.run_steps(1600, 900, steps)
        self.assertTrue(got["none"]["connect"])
        self.assertFalse(got["none"]["old"])
        self.assertTrue(got["none"]["sheet"])
        self.assertEqual(got["none"]["rail"], "card")
        self.assertTrue(got["aim"]["c"]["sheet"])
        self.assertEqual(got["aim"]["c"]["target"], got["aim"]["path"])
        self.assertEqual(got["aim"]["cut"], 0)
        self.assertEqual(got["cancel"], "tabs")

    def test_a_card_that_replaces_the_planet_card_holds_and_null_gives_the_tabs_back(self):
        # S1: the page's panel is taken once per opening; another card, then null, hold over several 100 ms ticks
        steps = [{"name": "p", "eval": SETUP + " activate(unitByUid(MOONS[0].uid)); await sleep(400); return __chromeAudit().rail.card;"},
                 {"name": "other", "eval": "const d = document.createElement('div'); d.id = 'wfcard-test'; d.textContent = 'a workflow'; CM.railCard(d, { back: 'Workflows' });"
                  " await sleep(500); const a = __chromeAudit(); return { card: a.rail.card, back: a.rail.back, panelOn: document.getElementById('panel').classList.contains('on'), home: document.getElementById('panel').parentNode.id };"},
                 {"name": "none", "eval": "CM.railCard(null); await sleep(500); const a = __chromeAudit(); return { content: a.rail.content, panelOn: document.getElementById('panel').classList.contains('on') };"},
                 {"name": "again", "eval": "activate(unitByUid(MOONS[0].uid)); await sleep(400); return __chromeAudit().rail.card;"}]
        got = self.run_steps(1600, 900, steps)
        self.assertEqual(got["p"], "panel")
        self.assertEqual((got["other"]["card"], got["other"]["back"]), ("wfcard-test", "Workflows"))
        self.assertFalse(got["other"]["panelOn"], "the displaced planet card is closed")
        self.assertEqual(got["other"]["home"], "wrap")
        self.assertEqual(got["none"], {"content": "tabs", "panelOn": False})
        self.assertEqual(got["again"], "panel", "a fresh opening is taken again")

    def test_the_screen_readers_list_keeps_its_keys_in_3d_and_one_region_says_one_whole_line(self):
        # S2: with the 3D view swallowing every key, Enter on "Leave 3D" still leaves; S3: one live region, a line said now drops
        # the one that waited; the radio's typed line is read once, whole
        steps = [{"name": "k", "eval": SETUP + " window.__left = 0; p3Leave = () => { __left++; }; CM.mode3d = () => true; CM.key3d = () => true;"
                  " A11Y.in3d = false; a11yRender(); const li = document.querySelector('#a11ymap [role=listitem]'); li.focus();"
                  " li.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));"
                  " const tab = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true }); li.dispatchEvent(tab);"
                  " const r = { first: li.textContent, left: __left, tabKept: !tab.defaultPrevented }; CM.mode3d = () => false; CM.key3d = () => false; return r;"},
                 {"name": "live", "eval": "const live = [...document.querySelectorAll('[aria-live]:not([aria-live=off])')].map((e) => e.id);"
                  " A11Y.last = 0; CM.say('first line'); CM.say('second line'); const a = document.getElementById('a11ylive').textContent;"
                  " A11Y.last = 0; CM.say('third line'); return { live, a, b: document.getElementById('a11ylive').textContent, timer: A11Y.timer, queued: A11Y.queued };"},
                 {"name": "radio", "eval": "A11Y.last = 0; A11Y.radio = ''; const l = document.getElementById('radiol'); document.getElementById('radion').textContent = 'SCOUT'; document.getElementById('radiow').textContent = 'finished';"
                  " const text = 'the tests pass'; for (let i = 2; i <= text.length; i += 2) { l.textContent = text.slice(0, i); await sleep(40); } l.textContent = text; await sleep(500);"
                  " return document.getElementById('a11ylive').textContent;"}]
        got = self.run_steps(1600, 900, steps)
        self.assertTrue(got["k"]["first"].startswith("Leave 3D"))
        self.assertEqual(got["k"]["left"], 1, "Enter on Leave 3D leaves")
        self.assertTrue(got["k"]["tabKept"], "Tab is not swallowed")
        self.assertEqual(got["live"]["live"], ["a11ylive"])
        self.assertEqual((got["live"]["a"], got["live"]["b"]), ("first line", "third line"))
        self.assertEqual((got["live"]["timer"], got["live"]["queued"]), (0, None), "the waiting line is dropped with its timer")
        self.assertEqual(got["radio"], "SCOUT, finished, the tests pass")

    def test_focus_and_workflow_step_changes_are_said(self):
        # S4: a planet opening and closing, and a workflow's step change, go through CM.say
        steps = [{"name": "open", "eval": SETUP + " A11Y.last = 0; activate(unitByUid(MOONS[0].uid)); await sleep(400); return { said: document.getElementById('a11ylive').textContent, name: unitByUid(MOONS[0].uid).name };"},
                 {"name": "close", "eval": "A11Y.last = 0; closeAll(); await sleep(400); return document.getElementById('a11ylive').textContent;"},
                 {"name": "wf", "eval": "const wf = { id: 'w-test', title: 'fix the labels', state: 'working', steps: [{ key: 'lint', label: 'Lint', state: 'done' }, { key: 'test', label: 'Test', state: 'working', planet_uid: MOONS[0].uid }] };"
                  " CM.wfList = () => [wf]; chromeSayWorkflows(); A11Y.last = 0; wf.steps[1].state = 'done'; wf.state = 'done'; chromeSayWorkflows(); const a = document.getElementById('a11ylive').textContent;"
                  " A11Y.last = 0; wf.steps[0].state = 'failed'; chromeSayWorkflows(); return { a, b: document.getElementById('a11ylive').textContent };"}]
        got = self.run_steps(1600, 900, steps)
        self.assertEqual(got["open"]["said"], got["open"]["name"] + " is open")
        self.assertEqual(got["close"], "All planets")
        self.assertEqual(got["wf"]["a"], "Fix the labels: done")
        self.assertTrue(got["wf"]["b"].startswith("Fix the labels: step 1 of 2, Lint, "), got["wf"]["b"])

    def test_the_contrast_audit_reads_the_crumb_and_the_placeholders_and_the_strip_takes_ask(self):
        # S5: the crumb and a placeholder are measured (the composer's are the worst text in the rail); S7: the seam's fourth slot
        steps = [{"name": "c", "eval": SETUP + " activate(unitByUid(MOONS[0].uid)); await sleep(400); const sep = document.querySelector('#crumb .sep');"
                  " return { sep: chromeContrastOf(sep), min: __chromeAudit().contrast.min, texts: __chromeAudit().contrast.texts };"},
                 {"name": "ph", "eval": "openComposer(unitByUid(MOONS[0].uid)); await sleep(400); const a = __chromeAudit().contrast; const t = document.getElementById('ctext');"
                  " return { min: a.min, ph: chromeContrastOf(t, getComputedStyle(t, '::placeholder').color), worst: a.worst, rootText: chromeContrastOf(document.getElementById('pressi')) };"},
                 {"name": "strip", "eval": "document.getElementById('ccancel').click(); CM.stripSet('ask', 'hello there'); await sleep(100); const a = document.getElementById('sb-ask').textContent;"
                  " const el = document.createElement('b'); el.textContent = 'a box'; CM.stripSet('ask', el); const b = document.getElementById('sb-ask').firstChild === el;"
                  " CM.stripSet('ask', null); await sleep(150); const c = document.getElementById('mate').parentNode.id; CM.stripSet('bogus', 'x');"
                  " return { a, b, c, unknown: __chromeAudit().strip.unknown };"}]
        got = self.run_steps(1600, 900, steps)
        self.assertGreaterEqual(got["c"]["sep"], 4.5)
        self.assertGreaterEqual(got["c"]["min"], 4.5)
        self.assertGreaterEqual(got["ph"]["ph"], 4.5, "the composer's placeholder reads")
        self.assertGreaterEqual(got["ph"]["min"], 4.5, got["ph"]["worst"])
        self.assertIsNotNone(got["ph"]["rootText"])
        self.assertEqual(got["strip"]["a"], "hello there")
        self.assertTrue(got["strip"]["b"])
        self.assertEqual(got["strip"]["c"], "sb-ask", "null gives the assistant's line back")
        self.assertEqual(got["strip"]["unknown"], ["bogus"])

    def test_the_side_panel_keys_have_one_path_and_the_retired_painters_rest(self):
        # S6: ] toggles the rail only (the old listener closed the composer); S10: hidden boxes are not painted
        steps = [{"name": "keys", "eval": SETUP + " openComposer(null); await sleep(300); document.activeElement.blur(); const k = (key) => dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }));"
                  " const w = document.getElementById('wrap'); const dash0 = w.classList.contains('dash'); k(']'); await sleep(100);"
                  " const r = { open1: composerOpen(), side1: sideOpen(), rail1: __chromeAudit().rail.open }; k(']'); await sleep(100); r.content = __chromeAudit().rail.content; r.rail2 = __chromeAudit().rail.open;"
                  " k('['); await sleep(100); r.dash = w.classList.contains('dash') === dash0; r.rail3 = __chromeAudit().rail.open; k('['); await sleep(100); return r;"},
                 {"name": "rest", "eval": "window.__n = { comms: 0, strip: 0, ticker: 0, mate: 0 }; commsTick = () => __n.comms++; stripTick = () => __n.strip++; tickerTick = () => __n.ticker++; mateDraw = () => __n.mate++;"
                  " const before = { tb: document.getElementById('tkbody').textContent }; await sleep(1300); hudTick(); tickerTick(); return { n: __n, tb: document.getElementById('tkbody').textContent === before.tb,"
                  " hidden: ['hud', 'comms', 'ticker'].map((i) => getComputedStyle(document.getElementById(i)).display) };"}]
        got = self.run_steps(1600, 900, steps)
        k = got["keys"]
        self.assertFalse(k["open1"] or k["side1"] or k["rail1"], "] closes the composer with the rail (never left open behind it)")
        self.assertEqual((k["rail2"], k["content"]), (True, "tabs"), "] opens the rail again on its tabs")
        self.assertTrue(k["dash"], "[ no longer opens the old dashboard")
        self.assertTrue(k["rail3"], "[ is retired with the dashboard: the rail stays")
        r = got["rest"]
        self.assertEqual(r["hidden"], ["none", "none", "none"])
        self.assertEqual((r["n"]["comms"], r["n"]["strip"], r["n"]["mate"]), (0, 0, 0), r["n"])
        self.assertTrue(r["tb"], "the ticker's line does not rotate while hidden")

    def test_the_rail_redraws_when_words_change_and_the_review_button_resolves_a_task(self):
        # S8: a task's title, a ticker line: the rail follows them with no state change; C1: a dispatch's id becomes words
        steps = [{"name": "t", "eval": SETUP + " CM.railTab('agents'); W.agent_tasks = [{ id: 't-words', title: 'first words here', status: 'review', target: unitByUid(MOONS[0].uid).path, created: new Date().toISOString(), ended: new Date().toISOString() }];"
                  " W.ticker = ['Dispatch d-20261001-12003-deepseek: DONE · 5.5min']; await sleep(1300); const a = document.getElementById('rail').innerText;"
                  " W.agent_tasks[0].title = 'second words here'; W.ticker = ['Routine line 42 all quiet']; await sleep(1300); const b = document.getElementById('rail').innerText;"
                  " return { a, b, line: chromeNewsLine('Dispatch d-20261001-12003-deepseek: DONE · 5.5min') };"},
                 {"name": "btn", "eval": "W.agent_tasks[0].status = 'review'; railRender(true); const row = document.querySelector('#rail .row[data-tid=t-words]'); row && row.click(); await sleep(300);"
                  " return { card: __chromeAudit().rail.card, title: document.getElementById('ttitle').textContent, back: document.getElementById('railback').textContent };"}]
        got = self.run_steps(1600, 900, steps)
        self.assertIn("First words here", got["t"]["a"])
        self.assertIn("A dispatch finished · 5.5 min", got["t"]["a"])
        self.assertNotIn("d-20261001", got["t"]["a"])
        self.assertIn("Second words here", got["t"]["b"])
        self.assertNotIn("First words here", got["t"]["b"])
        self.assertIn("Routine line 42 all quiet", got["t"]["b"])
        self.assertEqual(got["t"]["line"], "A dispatch finished · 5.5 min")
        self.assertEqual(got["btn"]["card"], "side")
        self.assertEqual(got["btn"]["title"], "Second words here", "the task card's title is a sentence (C7)")
        self.assertEqual(got["btn"]["back"], "← AI agents", "back to the tab it opened from (C8)")

    def test_a_task_opened_from_a_planet_card_goes_back_to_it(self):
        # C8: the back link is where the card was opened from
        steps = [{"name": "t", "eval": SETUP + " const u = unitByUid(MOONS[0].uid); W.agent_tasks = [{ id: 't-from', title: 'a task from the planet', status: 'review', target: u.path, created: new Date().toISOString(), ended: new Date().toISOString() }];"
                  " activate(u); await sleep(400); openTask('t-from'); await sleep(300); const a = { card: __chromeAudit().rail.card, back: document.getElementById('railback').textContent };"
                  " railBack(); await sleep(400); return { a, card: __chromeAudit().rail.card, name: u.name, crumb: document.getElementById('crumb').textContent };"}]
        got = self.run_steps(1600, 900, steps)
        self.assertEqual(got["t"]["a"], {"card": "side", "back": "← " + got["t"]["name"]})
        self.assertEqual(got["t"]["card"], "panel", "back reopens the planet card")
        self.assertTrue(got["t"]["crumb"].endswith(got["t"]["name"]))

    def test_plus_task_the_help_sheet_traps_focus_and_a_check_only_runner_is_no_hand(self):
        # S13: + Task opens the composer; the ? sheet is modal (inert page, Tab inside); K3: a check-only runner leaves the empty state
        steps = [{"name": "plus", "eval": SETUP + " const b = document.getElementById('railnew'); const r = { shown: !!b && b.offsetParent !== null, text: b.textContent }; b.click(); await sleep(300);"
                  " r.sheet = __chromeAudit().composer.sheet; r.hiddenNow = b.hidden; document.getElementById('ccancel').click(); await sleep(200); r.backAgain = !b.hidden; return r;"},
                 {"name": "help", "eval": "CM.openHelp('keys'); await sleep(200); const el = document.getElementById('helpsheet'); const f = chromeHelpFocusables(); f[f.length - 1].focus();"
                  " const tab = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true }); document.activeElement.dispatchEvent(tab);"
                  " const r = { inert: ['stage', 'rail', 'statusbar'].map((i) => document.getElementById(i).inert), wrapped: document.activeElement === f[0], hKey: /More stats/.test(el.textContent), zoom: __chromeAudit().settings.rows.includes('zoom') };"
                  " CM.closeHelp(); await sleep(100); r.after = ['stage', 'rail', 'statusbar'].map((i) => document.getElementById(i).inert); return r;"},
                 {"name": "k3", "eval": "W.task_runners = [{ name: 'checker', label: 'Checker', role: 'check' }]; openComposer(null); await sleep(300);"
                  " const r = { connect: !document.getElementById('cconnect').hidden, go: document.getElementById('cgo').disabled, opts: [...document.querySelectorAll('#crunner option')].map((o) => o.value), gap: document.querySelector('#composer .cfield.cwide').getBoundingClientRect().height };"
                  " document.getElementById('ccancel').click(); return r;"}]
        got = self.run_steps(1600, 900, steps)
        self.assertEqual((got["plus"]["shown"], got["plus"]["text"]), (True, "+ Task"))
        self.assertTrue(got["plus"]["sheet"] and got["plus"]["hiddenNow"] and got["plus"]["backAgain"])
        self.assertEqual(got["help"]["inert"], [True, True, True])
        self.assertTrue(got["help"]["wrapped"], "Tab from the last control goes to the first")
        self.assertTrue(got["help"]["hKey"] and got["help"]["zoom"])
        self.assertEqual(got["help"]["after"], [False, False, False])
        self.assertTrue(got["k3"]["connect"] and got["k3"]["go"])
        self.assertEqual(got["k3"]["opts"], [""])
        self.assertLess(got["k3"]["gap"], 70, "the Folder field is as tall as its words (C10)")

    def test_the_board_wears_the_rails_type_and_the_chips_step_aside(self):
        # C9
        steps = [{"name": "b", "eval": SETUP + " toggleQuests(true); await sleep(300); const q = document.getElementById('quests'); const cs = getComputedStyle(q);"
                  " const r = { on: q.classList.contains('on'), font: cs.fontFamily, radius: cs.borderRadius, legend: getComputedStyle(document.getElementById('legendchip')).display, h2: q.querySelector('h2').textContent, how: q.querySelector('.how b').textContent };"
                  " q.classList.remove('on'); await sleep(1200); r.legendBack = getComputedStyle(document.getElementById('legendchip')).display; return r;"}]
        got = self.run_steps(1600, 900, steps)["b"]
        self.assertTrue(got["on"])
        self.assertIn("Barlow Condensed", got["font"])
        self.assertEqual(got["radius"], "14px")
        self.assertEqual(got["legend"], "none", "no chip shows through the board")
        self.assertNotEqual(got["legendBack"], "none")
        self.assertEqual((got["h2"], got["how"]), ("Decisions", "How this works"))

    def test_a_small_window_keeps_sixty_percent_of_the_map_with_a_card_open(self):
        # C2: at 1024x768 and 1180x820 (a desktop window, and a touch tablet) an open card leaves the map 60% of the window
        probe = ("setView('map'); await sleep(300); activate(unitByUid(MOONS[0].uid)); await sleep(500); const a = __chromeAudit();"
                 " return { share: (a.mapFree.w * a.mapFree.h) / (innerWidth * innerHeight), content: a.rail.content, rail: document.getElementById('rail').getBoundingClientRect().width,"
                 " cut: [...document.querySelectorAll('#rail *')].filter(e => e.scrollWidth > e.clientWidth + 1).length, cls: document.documentElement.className };")
        for width, height in ((1024, 768), (1180, 820)):
            for mobile in (False, True):
                got = self.run_steps(width, height, [{"name": "c", "eval": probe}], mobile=mobile)["c"]
                with self.subTest(size=f"{width}x{height}", tablet=mobile):
                    self.assertEqual(got["content"], "card")
                    self.assertGreaterEqual(got["share"], 0.6, got)
                    self.assertEqual(got["cut"], 0)
                    self.assertEqual(got["rail"], 320 if width <= 1100 else 400)

    def test_a_tablets_controls_are_finger_sized(self):
        # S9: every button and select in the chrome is at least 44 px tall on html.t
        probe = ("setView('map'); await sleep(300); const small = (root) => [...document.querySelectorAll(root + ' button, ' + root + ' select, ' + root + ' summary, ' + root + ' a.cmbtn')]"
                 " .filter((e) => e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden').map((e) => [e.id || e.className || e.tagName, Math.round(e.getBoundingClientRect().height), Math.round(e.getBoundingClientRect().width)]).filter((x) => x[1] < 44 || x[2] < 44);"
                 " const r = { cls: document.documentElement.className, tabs: small('#rail').concat(small('#statusbar')) }; activate(unitByUid(MOONS[0].uid)); await sleep(500); r.card = small('#rail');"
                 " document.querySelector('#rail details.pc-more').open = true; await sleep(100); r.more = small('#rail'); closeAll(); await sleep(200); CM.openHelp('settings'); await sleep(300); r.help = small('#helpsheet');"
                 " CM.openHelp('setup'); await sleep(200); r.setup = small('#helpsheet'); CM.closeHelp(); return r;")
        got = self.run_steps(1024, 768, [{"name": "t", "eval": probe}], mobile=True)["t"]
        self.assertEqual(got["cls"].split(), ["t"])
        for part in ("tabs", "card", "more", "help", "setup"):
            self.assertEqual(got[part], [], part)

    def test_a_narrow_window_starts_with_the_rail_closed_and_a_card_opens_it(self):
        steps = [{"name": "n", "eval": SETUP + " const a = __chromeAudit(); return { open: a.rail.open, free: a.mapFree };"},
                 {"name": "card", "eval": "activate(unitByUid(MOONS[0].uid)); await sleep(300); const a = __chromeAudit(); return { open: a.rail.open, content: a.rail.content };"},
                 {"name": "back", "eval": "closeAll(); await sleep(300); return __chromeAudit().rail.open;"}]
        got = self.run_steps(1100, 800, steps)
        self.assertFalse(got["n"]["open"])
        self.assertEqual(got["n"]["free"]["w"], 1100)
        self.assertEqual(got["card"], {"open": True, "content": "card"})
        self.assertFalse(got["back"], "it closes again with the card it opened for")

    def test_a_phone_keeps_its_own_layout_and_a_tablet_takes_the_desktops(self):
        probe = ("setView('map'); await sleep(400); const d = (i) => getComputedStyle(document.getElementById(i)).display;"
                 " return { cls: document.documentElement.className, rail: d('rail'), bar: d('statusbar'), mateHome: document.getElementById('mate').parentNode.id };")
        phone = self.run_steps(390, 844, [{"name": "p", "eval": probe}], mobile=True)["p"]
        self.assertIn("m", phone["cls"].split())
        self.assertEqual((phone["rail"], phone["bar"]), ("none", "none"))
        self.assertEqual(phone["mateHome"], "wrap")
        # a touch tablet keeps the desktop layout with touch targets: the status bar, the assistant's line in it, and
        # the rail closed below 1280 px
        tablet = self.run_steps(1024, 768, [{"name": "t", "eval": probe}], mobile=True)["t"]
        self.assertEqual(tablet["cls"].split(), ["t"])
        self.assertEqual((tablet["rail"], tablet["bar"], tablet["mateHome"]), ("none", "flex", "sb-ask"))


if __name__ == "__main__":
    unittest.main()
