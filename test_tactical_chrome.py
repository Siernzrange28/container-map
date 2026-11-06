#!/usr/bin/env python3
"""test_tactical_chrome.py — the vector look's chrome (theme packs with "render": "vector"). Its stylesheet is its
own block and every rule in it waits behind html[data-render="vector"], so no other look changes; its colours are
the spec's rgb() tokens, which the pack rewriter never touches; the words are ours. In the page (the stub DOM of
test_game) the vector look builds the data card, the mission card and the rails, and the classic look keeps its own.

Run: python3 -m unittest -q test_tactical_chrome   (node needed for the page half; else it is skipped)
"""
import re
import shutil
import tempfile
import unittest
from pathlib import Path

import testenv  # noqa: F401  (first: clears what steers the tool)
from test_game import page_js, render_fixture, run_page_driver

ROOT = Path(__file__).resolve().parent
SRC = (ROOT / "render-game.py").read_text(encoding="utf-8")
GATES = ('html[data-render="vector"]', 'html.m[data-render="vector"]')
MINE = ("#tacrail", "#tacfoot", "#tvmission")      # the new elements: hidden in every look but this one
GAME_WORDS = r"\b(?:IVCS|SATO|UNSA|SetDef)\b|Call of Duty|Infinite Warfare|Activision"


def split_rules(css):
    """(selector, body) for every rule; comments dropped, @media blocks opened, @keyframes skipped."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    out, i = [], 0
    while i < len(css):
        j = css.find("{", i)
        if j < 0:
            break
        head = css[i:j].strip()
        if head.startswith("@keyframes"):
            depth, k = 1, j + 1
            while depth and k < len(css):
                depth += (css[k] == "{") - (css[k] == "}")
                k += 1
            i = k
            continue
        if head.startswith("@media"):
            i = j + 1
            continue
        k = css.find("}", j)
        out.append((head.lstrip("}").strip(), css[j + 1:k]))
        i = k + 1
        while i < len(css) and css[i] in "}\n ":
            i += 1
    return out


def top_level_parts(selector):
    parts, depth, cur = [], 0, ""
    for ch in selector:
        depth += (ch == "(") - (ch == ")")
        if ch == "," and depth == 0:
            parts.append(cur.strip())
            cur = ""
        else:
            cur += ch
    return parts + [cur.strip()]


class TacticalSheetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        Path(cls.tmp.name, "work", "Alpha").mkdir(parents=True)
        proc = render_fixture(cls.tmp.name)
        if proc.returncode:
            raise AssertionError(proc.stderr[-2000:])
        cls.html = Path(cls.tmp.name, "page.html").read_text(encoding="utf-8")
        cls.block = re.search(r'<style id="cm-tac">(.*?)</style>', cls.html, re.S).group(1)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_the_block_comes_after_the_main_sheet_and_before_the_faces(self):
        main, tac, faces = (self.html.index(x) for x in ("<style>", '<style id="cm-tac">', '<style id="cm-fonts">'))
        self.assertLess(main, tac)
        self.assertLess(tac, faces)
        self.assertNotIn("__TACCSS__", self.html)
        self.assertNotIn("$V", self.block)
        self.assertNotIn("$D", self.block)

    def test_every_rule_waits_behind_the_vector_look(self):
        rules = split_rules(self.block)
        self.assertGreater(len(rules), 100)
        for sel, body in rules:
            for part in top_level_parts(sel):
                if part in MINE:
                    self.assertEqual(body, "display:none", "a new element waits hidden: " + part)
                    continue
                self.assertTrue(part.startswith(GATES), "an ungated rule in the vector block: " + part)

    def test_the_main_sheet_carries_no_vector_rule(self):
        main = re.search(r"<style>(.*?)</style>", SRC, re.S).group(1)
        self.assertNotIn("data-render", main, "the vector look lives in its own block, never in the classic sheet")

    def test_the_block_has_no_hex_colour_for_the_pack_rewriter(self):
        self.assertIsNone(re.search(r"#[0-9a-fA-F]{3,8}\b(?![\w-])(?=[;,)\s}'\"/])", self.block.replace("%23", "")),
                          "the vector tokens are rgb(); a hex colour would be recoloured by every pack")
        self.assertIn("--tac-y:rgb(229,184,0)", self.block)
        self.assertIn("--tac-red:rgb(214,40,40)", self.block)

    def test_no_rounded_corner_and_the_chamfer(self):
        self.assertIn("*{border-radius:0!important}", self.block)
        self.assertIn("--tac-cut:polygon(10px 0,100% 0,100% 100%,0 100%,0 10px)", self.block)
        self.assertNotRegex(self.block, r"border-radius:\s*[1-9]")

    def test_data_images_are_inline_svg_without_a_hash(self):
        for uri in re.findall(r'url\("([^"]*)"\)', self.block):
            self.assertTrue(uri.startswith("data:image/svg+xml,"), uri[:40])
            self.assertNotIn("#", uri, "a raw # ends a data: URI")

    def test_the_words_are_ours(self):
        self.assertIsNone(re.search(GAME_WORDS, SRC, re.I), "no game name, logo or mark in the product")
        page = re.sub(r'<style id="cm-fonts">.*?</style>', "", self.html, flags=re.S)   # base64 is not words
        self.assertIsNone(re.search(GAME_WORDS, page, re.I))


class TacticalSourceTests(unittest.TestCase):
    def test_today_stays_extractable_without_the_flag(self):
        # test_today runs renderToday on its own, with a handful of helpers: the briefing look is CSS only
        body = SRC[SRC.index("function renderToday(){"):SRC.index("document.getElementById('today').addEventListener")]
        self.assertNotIn("VEC", body)
        self.assertNotRegex(body, r"\btac[A-Z]\w*\(")

    def test_every_legend_key_is_a_key_the_page_answers(self):
        keys = re.search(r"const TAC_KEYS=\{map:\[(.*?)\],\s*today:\[(.*?)\]\};", SRC, re.S)
        handlers = {
            "n": "k==='n'", "k": "k==='k'", "q": "k==='q'", "l": "k==='l'", "t": "k==='t'", "h": "k==='h'",
            "m": "k==='m'", "v": "e.key==='v'||e.key==='V'", "Escape": "e.key==='Escape'", "j": "k==='j'", "y": "k==='y'"}
        for group in keys.groups():
            for key, cap, word in re.findall(r"\['([^']+)','([^']+)','([^']+)'\]", group):
                self.assertIn(key, handlers, "a legend key without a handler: " + key)
                self.assertIn(handlers[key], SRC)
                self.assertTrue(word[0].isupper(), word)
        self.assertNotIn("'Targets'", SRC, "Q opens the board here: the legend says so")

    def test_the_gated_hooks_never_run_in_another_look(self):
        # the desktop chrome rests the retired boxes' painters while they are hidden; the tactical one still runs only in VEC
        self.assertIn("if(!chromeHides('comms')) commsTick(); if(!chromeHides('ticker')) stripTick(); if(VEC) tacTick();", SRC)
        self.assertIn("if(VEC) h=tacCardHead(u,probs,seatsHere,threads,st,tn);", SRC)
        self.assertIn("if(VEC) return '<div class=\"trow mc st-'", SRC)
        self.assertIn("if(VEC) tacMissionMeta(j);", SRC)


TAC_DRIVER = r"""
  const area = units.find((u) => u.kind === 'area');
  ok('fixture has a planet', !!area);
  // the classic look first: nothing of the vector chrome
  applyTheme('classic', false);
  ok('classic: VEC off', VEC === false && document.documentElement.getAttribute('data-render') === 'pixel');
  elCache.pbody = cap(); renderCard(area);
  const classicCard = String(elCache.pbody.innerHTML);
  ok('classic: the card is the classic one (the planet card of the comps, )', classicCard.includes('data-card="planet"') && classicCard.includes('New task') && !classicCard.includes('CLUSTER'));
  const t = T('20261010-201300-dark-mode-a1b2c3', 'review', { mode: 'worktree', base_branch: 'main', branch: 'cm/x',
    verify_cmd: 'make test', verify: { rc: 0 }, check: { verdict: 'PASS', runner: 'claude' }, attempts: 1, retries: 2,
    evidence: 'verified and checked', merge_blockers: [] });
  ok('classic: a list row is the classic row', !taskListRow(t).includes('OPERATION') && taskListRow(t).includes('trow'));
  // the vector look
  applyTheme('tactical', false);
  ok('tactical: VEC on', VEC === true && document.documentElement.getAttribute('data-render') === 'vector');
  elCache.pbody = cap(); renderCard(area);
  const card = String(elCache.pbody.innerHTML);
  ok('card: eyebrow KIND · CLUSTER and the name', /class="ckind">[A-Z]+ · CLUSTER</.test(card) && card.includes('class="cname"'));
  ok('card: the facts as a definition list, labels left, values right', card.includes('<dl class="tdl">') && card.includes('<dt>FILES</dt>') && card.includes('<dt>PROBLEMS</dt>'));
  ok('card: a fact the page lacks is left out, never a dash', !/<dd[^>]*>—<\/dd>/.test(card));
  ok('card: deploy and ask keep their hooks', card.includes('data-cact="thread"') && card.includes('Deploy agent <kbd class="tkey">N</kbd>') && card.includes('data-cact="mate"') && card.includes('Ask ' + esc(mateName())));
  ok('card: the tabs keep their hooks', card.includes('data-ctab="human"') && card.includes('data-ctab="agent"') && card.includes('data-cpane="human"'));
  // the mission card
  const full = tacMission(t, true);
  ok('mission: OPERATION, the title as a person says it (no id) and the runner', full.includes('<b>OPERATION THREAD</b>') && !full.includes('20261010-201300') && full.includes('claude'));
  ok('mission: the four rows', ['VERIFY', 'CHECK', 'ATTEMPTS', 'EVIDENCE'].every((r) => full.includes('<dt>' + r + '</dt>')));
  ok('mission: verified and checked is CONFIRMED', full.includes('>CONFIRMED<') && full.includes('1 OF 3'));
  ok('mission: the real merge control', full.includes('data-tact="merge"') && full.includes('MERGE INTO MAIN'));
  ok('mission: verified alone is UNCONFIRMED', tacEvidence({ evidence: 'verified' })[0] === 'UNCONFIRMED');
  ok('mission: no evidence', tacEvidence({ evidence: 'none' })[0] === 'NO EVIDENCE' && tacEvidence({})[0] === 'NO EVIDENCE');
  const blocked = tacMission(Object.assign({}, t, { merge_blockers: ['verify failed: exit 1'] }), true);
  ok('mission: a blocker in red instead of the merge', blocked.includes('mcbar bad') && blocked.includes('verify failed: exit 1') && !blocked.includes('data-tact="merge"'));
  ok('mission: banners by state', tacBand('failed') === 'red' && tacBand('review') === 'red' && tacBand('running') === 'run' && tacBand('merged') === 'won' && tacBand('discarded') === 'idle');
  const row = taskListRow(t);
  ok('mission: the list row keeps its hook and has no button', row.includes('data-tid="' + t.id + '"') && row.includes('trow mc st-red') && !row.includes('<button'));
  // the rails
  ['tacview', 'tacstamp', 'tacmark', 'taccount', 'tackeys'].forEach((id) => { elCache[id] = cap(); });
  VIEW = 'map'; tacTick();
  ok('rails: the view chip and the dossier stamp', elCache.tacview.textContent === 'MAP' && /^(SUN|MON|TUE|WED|THU|FRI|SAT) \d\d [A-Z]{3} \d{4} · \d{4} HRS$/.test(elCache.tacstamp.textContent));
  ok('rails: the map mark', elCache.tacmark.textContent === String(W.name || 'CONTAINER').toUpperCase());
  const skyCounts = String(elCache.taccount.innerHTML);
  ok('rails: the boxed counters carry the one count (W.counts), marked for the audit', ['need_you', 'decisions', 'problems', 'working'].every((k) => skyCounts.includes('data-tnum="' + k + '"') && skyCounts.includes('data-count="' + k + '"')));
  SKY = false; tacTick();
  const counts = String(elCache.taccount.innerHTML), sc = stripCounts();
  ok('rails: the same counters whatever the view, the strip numbers', counts === skyCounts && counts.includes('<b>' + sc.problems + '</b>'));
  SKY = true;
  ok('rails: the map legend', (String(elCache.tackeys.innerHTML).match(/data-key=/g) || []).length === 10 && elCache.tackeys.innerHTML.includes('data-key="Escape"')
    && elCache.tackeys.innerHTML.includes('data-key="y"') && elCache.tackeys.innerHTML.includes('data-key="j"'));
  VIEW = 'today'; tacTick();
  ok('rails: Today offers N, M, V only', (String(elCache.tackeys.innerHTML).match(/data-key="([^"]+)"/g) || []).join() === 'data-key="n",data-key="m",data-key="v"' && elCache.tacview.textContent === 'TODAY');
  VIEW = 'map';
  ok('stamp: a fixed date reads like a dossier', tacStamp(new Date(2026, 9, 10, 20, 13)) === 'SAT 10 OCT 2026 · 2013 HRS');
  ok('comms: states colour by family', tacComms('WORKING') === 'tf' && tacComms('WAITING') === 'ta' && tacComms('ERROR') === 'th' && tacComms('IDLE') === 'tq');
  // back to classic: the card and the rows are the classic ones again
  applyTheme('classic', false);
  elCache.pbody = cap(); renderCard(area);
  ok('back to classic: the classic card again', String(elCache.pbody.innerHTML) === classicCard);
"""


class TacticalPageTests(unittest.TestCase):
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
        page_js(Path(cls.tmp.name, "page.html"), cls.js)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_the_vector_look_builds_the_card_the_mission_and_the_rails(self):
        run_page_driver(self, self.js, TAC_DRIVER)


if __name__ == "__main__":
    unittest.main(verbosity=2)
