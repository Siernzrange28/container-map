"""First visit: a guide anchored to real folders and controls, with no automatic tour in test browsers."""
import json
import tempfile
import unittest
from pathlib import Path

import testenv  # noqa: F401
import test_game
import constellations

ROOT = Path(__file__).resolve().parent


class DataTests(unittest.TestCase):
    def test_no_config_or_findings_means_no_groups(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(constellations.build(root, [], 0), ([], 0))
            candidates = [{"uid": "ship:t3:one", "name": "my task", "t3": {"state": "working"}}]
            self.assertEqual(constellations.build(root, candidates, 0), ([], 0))

    def test_findings_supply_the_persons_own_groups(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root, '.container-map/chat-scan/findings')
            folder.mkdir(parents=True)
            (folder / 'a.json').write_text(json.dumps({"id": "codex-tui:one", "source": "codex-tui",
                "lane": "My writing", "title": "Welcome", "unfinished": []}))
            groups, skipped = constellations.build(root, [], 0)
            self.assertEqual((skipped, [g['lane'] for g in groups]), (0, ['My writing']))
            self.assertEqual(groups[0]['count'], 1)

    def test_private_group_is_a_setting_for_any_name(self):
        config = {'constellations': [{'name': 'Personal notes', 'keywords': ['diary'], 'private': True}]}
        with tempfile.TemporaryDirectory() as root:
            groups, _ = constellations.build(root, [{'uid': 'ship:t3:one', 'name': 'diary',
                't3': {'state': 'ready'}}], 0, config)
            self.assertEqual(groups[0]['private_ids'], ['t3-local-wsl:one'])
            self.assertNotIn('threads', constellations.phone(groups)[0])


class GuideBrowserTests(unittest.TestCase):
    setUpClass = classmethod(test_game.HardeningBrowserTests.setUpClass.__func__)
    tearDownClass = classmethod(test_game.HardeningBrowserTests.tearDownClass.__func__)
    run_steps = test_game.HardeningBrowserTests.run_steps

    def test_automated_pages_do_not_show_a_guide_unasked(self):
        got = self.run_steps(1280, 720, [{'name': 'a', 'eval': 'return CM.guideAudit();'}])['a']
        self.assertFalse(got['shown'])

    def test_first_visit_once_help_replay_enter_escape_and_focus(self):
        got = self.run_steps(1280, 720, [
            {'name': 'first', 'eval': 'return CM.guideAudit();'},
            {'name': 'enter', 'eval': "dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true})); return CM.guideAudit();"},
            {'name': 'esc', 'eval': "dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true})); return {shown:CM.guideAudit().shown,seen:localStorage.getItem('cm.guide.v1'),inert:document.getElementById('stage').inert};"},
            {'name': 'help', 'eval': "CM.openHelp('see'); const hidden=getComputedStyle(document.getElementById('pov-switch')).visibility==='hidden'; document.querySelector('#helpsheet [data-act=guide]').click(); return {...CM.guideAudit(),cameraHidden:hidden};"},
            {'name': 'wrap', 'eval': "const buttons=[...document.querySelectorAll('#firstguide button:not(:disabled)')];buttons.at(-1).focus();dispatchEvent(new KeyboardEvent('keydown',{key:'Tab',bubbles:true}));return document.activeElement===buttons[0];"},
            {'name': 'done', 'eval': 'for(let i=0;i<6;i++)document.querySelector("#firstguide [data-guide=next]").click();return CM.guideAudit().shown;'}
        ], init='window.CM_SHOW_GUIDE=true;')
        self.assertEqual((got['first']['shown'], got['first']['step'], got['enter']['step']), (True, 0, 1))
        self.assertEqual(got['esc'], {'shown': False, 'seen': 'seen', 'inert': False})
        self.assertTrue(got['help']['shown'] and got['help']['cameraHidden'] and got['wrap'])
        self.assertFalse(got['done'])
        # A separate document in the same browser storage has already seen it.
        again = self.run_steps(1280, 720, [{'name': 'a', 'eval': 'return CM.guideAudit().shown;'}],
            init="localStorage.setItem('cm.guide.v1','seen');Object.defineProperty(navigator,'webdriver',{value:false});Object.defineProperty(navigator,'userAgent',{value:'Browser'});")
        self.assertFalse(again['a'])

    def test_every_step_fits_and_points_at_a_real_target_on_phone_and_desktop(self):
        for width, height, mobile in [(1280, 720, False), (390, 844, True)]:
            steps = []
            for i in range(6):
                steps.append({'name': str(i), 'eval': 'await sleep(750);const a=CM.guideAudit();const e=document.getElementById("firstguide");const cut=[...e.querySelectorAll("section,button,p,h2")].filter(e=>e.scrollWidth>e.clientWidth+1).length;document.querySelector("#firstguide [data-guide=next]").click();return {...a,cut};'})
            got = self.run_steps(width, height, steps, mobile=mobile, init='window.CM_SHOW_GUIDE=true;')
            for i in range(6):
                a = got[str(i)]
                with self.subTest(width=width, step=i):
                    self.assertEqual(a['step'], i)
                    self.assertEqual(a['cut'], 0)
                    self.assertIsNotNone(a['target'])
                    c = a['card']
                    self.assertGreaterEqual(c['x'], 0)
                    self.assertGreaterEqual(c['y'], 0)
                    self.assertLessEqual(c['x'] + c['w'], width)
                    self.assertLessEqual(c['y'] + c['h'], height)

    def test_for_you_works_without_personal_daily_sources(self):
        got = self.run_steps(390, 844, [{'name': 'home', 'eval': """
            W.today.configured=false;
            W.counts={...W.counts,need_you:1,working:1};
            W.agent_tasks=[{id:'r',title:'Write a welcome',status:'review',target:'/sample'},
                {id:'w',title:'Describe this folder',status:'running',target:'/sample'},
                {id:'m',title:'Fix a small problem',status:'merged',target:'/sample'}];
            W.sky={...W.sky,contacts:[{id:'task:r',source:'task',state:'review',title:'Write a welcome'}]};
            mtab('today'); renderToday();
            const el=document.getElementById('today');
            return {text:el.innerText,tab:document.querySelector('#tab-today .tl').textContent};
        """}], mobile=True)['home']
        self.assertEqual(got['tab'], 'For you')
        for text in ('For you', 'Write a welcome', 'Review', 'Running now', 'Describe this folder', 'Merged'):
            self.assertIn(text, got['text'])
        self.assertNotIn('Today is off', got['text'])

    def test_favicon_is_an_inline_original_orbit_mark(self):
        got = self.run_steps(390, 844, [{'name': 'icon', 'eval': 'const l=document.querySelector("link[rel=icon]");return {type:l.type,href:l.href};'}], mobile=True)['icon']
        self.assertEqual(got['type'], 'image/svg+xml')
        self.assertTrue(got['href'].startswith('data:image/svg+xml,'))
        self.assertIn('%3Cellipse', got['href'])


if __name__ == '__main__':
    unittest.main()
