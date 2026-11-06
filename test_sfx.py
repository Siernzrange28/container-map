#!/usr/bin/env python3
"""test_sfx.py — the page's sound and voice script (sfx.py).

The text is checked as text (the window.cmSfx contract, nothing fetched, the secure-page guard, the
rate limit, safe to inline ahead of the page script), then run in node against stand-ins for
WebAudio, speech and storage: cues wait for a gesture and keep their gap, the mute and the voice
toggle persist, speech prefers a voice on the device, and listening exists only on a secure page,
answers only navigation words and never touches the recogniser otherwise.

Run: python3 test_sfx.py   (node needed only for the second half; else skipped)
"""
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import sfx

CONTRACT = ("cue", "enabled", "setEnabled", "speak", "voiceOut", "setVoiceOut", "listen")
LISTEN = ("supported", "reason", "start", "stop", "onCommand")
CUE_NAMES = {"warp-in", "lock", "settle", "empty-sky", "alert"}
COMMANDS = {"next", "prev", "open", "briefing", "sky"}
# words that would change something: none may be a voice command or part of one
MUTATIONS = ("settle", "snooze", "merge", "delete", "remove", "send", "reply", "allow", "deny",
             "approve", "kill", "stop", "drop", "keep", "push", "run", "start", "launch", "close")


class Script(unittest.TestCase):
    def setUp(self):
        self.js = sfx.script()

    def test_the_script_is_text(self):
        self.assertIsInstance(self.js, str)
        self.assertTrue(self.js.strip(), "the sound script is empty")

    def test_the_script_never_closes_its_own_element(self):
        self.assertNotIn("</script", self.js.lower())

    def test_the_script_is_the_same_on_every_call(self):
        # the render loop leaves an unchanged page alone; a script that moved would rewrite it
        self.assertEqual(self.js, sfx.script())

    def test_the_contract_keys_are_present(self):
        api = re.search(r"w\.cmSfx = \{(.*?)\n  \};", self.js, re.S)
        self.assertIsNotNone(api, "window.cmSfx is not assigned")
        for key in CONTRACT:
            self.assertRegex(api.group(1), r"\n    " + key + r":", key)
        listen = re.search(r"var listen = \{(.*?)\n  \};", self.js, re.S)
        self.assertIsNotNone(listen, "no listen object")
        for key in LISTEN:
            self.assertRegex(listen.group(1), r"\n    " + key + r":", "listen." + key)

    def test_nothing_is_fetched_or_played_from_a_file(self):
        low = self.js.lower()
        for word in ("fetch(", "xmlhttprequest", "http", "websocket", "eventsource", "sendbeacon",
                     "import(", "new audio(", ".src", "worker("):
            self.assertNotIn(word, low, word)

    def test_no_loop_of_its_own(self):
        for word in ("requestAnimationFrame", "setInterval", "setTimeout"):
            self.assertNotIn(word, self.js, word)

    def test_the_rate_limit_is_at_least_one_and_a_half_seconds(self):
        found = re.findall(r"\bvar GAP_MS = (\d+);", self.js)
        self.assertEqual(len(found), 1, "one GAP_MS constant")
        self.assertGreaterEqual(int(found[0]), 1500)
        self.assertEqual(int(found[0]), sfx.GAP_MS)

    def test_speech_recognition_is_reached_only_through_the_secure_page_guard(self):
        lines = [line for line in self.js.splitlines() if "SpeechRecognition" in line]
        self.assertTrue(lines, "no recogniser lookup")
        for line in lines:
            self.assertRegex(line, r"^\s*var Recognition = secure \? \(", line.strip())
        self.assertRegex(self.js, r"\n  var secure = w\.isSecureContext === true;\n")
        self.assertLess(self.js.index("isSecureContext"), self.js.index("SpeechRecognition"))
        self.assertNotRegex(self.js, r"new (?:webkit)?SpeechRecognition\b")    # built only from the guarded name

    def test_it_is_safe_to_inline_ahead_of_the_page_script(self):
        # the bridge swaps the first line starting "const W = "; the page fills "__WORLD__" after
        # this slot; a tag inside would split the nonce cuts; "<!--" changes how a script parses
        self.assertNotRegex(self.js, r"(?m)^const W = ")
        self.assertNotRegex(self.js, r"__[A-Z0-9_]+__")
        for tag in ("<script", "<style", "</style", "<!--"):
            self.assertNotIn(tag, self.js.lower(), tag)
        self.assertNotIn("@", self.js, "a template token was left unfilled")
        self.assertTrue(self.js.startswith("(function () {"), "one function scope, no page-level names")
        self.assertTrue(self.js.rstrip().endswith("})();"))

    def test_cues_are_the_contract_names_short_and_quiet(self):
        self.assertEqual(set(sfx.CUES), CUE_NAMES)
        for name, tones in sfx.CUES.items():
            self.assertTrue(tones, name)
            for wave, start, end, offset, length, gain in tones:
                with self.subTest(cue=name, wave=wave, start=start):
                    self.assertIn(wave, ("sine", "square", "triangle", "sawtooth"))
                    self.assertTrue(20 <= start <= 20000)
                    self.assertTrue(end == 0 or 20 <= end <= 20000)
                    self.assertGreaterEqual(offset, 0)
                    self.assertGreaterEqual(length, 0.04)       # longer than the 10 ms attack
                    self.assertLessEqual(offset + length, 1.2)    # a cue, never a loop
                    self.assertTrue(0 < gain <= 0.08)             # no louder than the page's chime

    def test_voice_commands_are_navigation_only(self):
        self.assertEqual(set(sfx.COMMANDS), COMMANDS)
        self.assertEqual(set(sfx.GRAMMAR.values()), COMMANDS)
        for phrase in sfx.GRAMMAR:
            with self.subTest(phrase=phrase):
                self.assertRegex(phrase, r"^[a-z]+(?: [a-z]+)*$")    # the form the page reduces speech to
                for word in phrase.split():
                    self.assertNotIn(word, MUTATIONS)

    def test_the_storage_keys_and_defaults(self):
        self.assertEqual(sfx.SOUND_KEY, "cm.sfx")
        self.assertRegex(self.js, r'sound = read\(SOUND_KEY\) !== "off", voice = read\(VOICE_KEY\) === "on"')
        self.assertGreaterEqual(sfx.SPEAK_QUEUE, 1)
        self.assertLessEqual(sfx.SPEAK_QUEUE, 4)


# Stand-ins for the browser: every scenario runs the script in a fresh vm context and records what it
# did; the facts go back as one JSON object.
HARNESS = r"""
const vm = require('vm');
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');
const grammar = JSON.parse(process.argv[3]);

function browser(opts) {
  opts = opts || {};
  const listeners = {}, docListeners = {}, store = Object.assign({}, opts.store || {});
  const log = { contexts: 0, resumes: 0, oscillators: 0, spoken: [], cancels: 0, recs: [], touched: 0,
                asked: [], installs: 0, stops: 0, cut: 0 };
  let now = 0;
  const param = () => ({ setValueAtTime() {}, exponentialRampToValueAtTime() {} });
  class Ctx {
    constructor() { log.contexts += 1; this.state = 'suspended'; this.currentTime = 0; this.destination = {}; log.ctx = this; }
    resume() { log.resumes += 1; if (!opts.stuck) this.state = 'running'; return Promise.resolve(); }
    createOscillator() { log.oscillators += 1; return { type: '', frequency: param(), connect() {}, start() {}, stop() { log.stops += 1; } }; }
    createGain() { return { gain: param(), connect() {}, disconnect() { log.cut += 1; } }; }
  }
  const synth = {
    speaking: false, pending: false, voices: opts.voices || [],
    getVoices() { return this.voices; },
    all: [],
    speak(u) { log.spoken.push({ text: u.text, voice: u.voice ? u.voice.name : null }); this.speaking = true; this.last = u; this.all.push(u); },
    cancel() { log.cancels += 1; this.speaking = false; },
  };
  function Utterance(text) { this.text = text; this.voice = null; this.lang = ''; }
  class Rec {
    constructor() { log.recs.push(this); this.started = 0; this.stopped = 0; }
    start() { this.started += 1; }
    stop() { this.stopped += 1; if (this.onend) this.onend(); }
  }
  if (opts.available) {          // the on-device recogniser's answer for the language, as Chrome gives it
    Rec.available = (o) => { log.asked.push(o); return Promise.resolve(opts.available); };
    Rec.install = () => { log.installs += 1; return Promise.resolve(true); };
  }
  const win = {
    addEventListener(name, fn) { (listeners[name] = listeners[name] || []).push(fn); },
    localStorage: { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); } },
    performance: { now: () => now },
    navigator: { language: opts.lang || 'en-US' },
    document: { visibilityState: 'visible',
                addEventListener(name, fn) { (docListeners[name] = docListeners[name] || []).push(fn); } },
    AudioContext: Ctx,
    speechSynthesis: synth,
    SpeechSynthesisUtterance: Utterance,
    isSecureContext: opts.secure !== false,
  };
  if (opts.recogniser !== false) {
    for (const name of ['SpeechRecognition', 'webkitSpeechRecognition']) {
      Object.defineProperty(win, name, { configurable: true, enumerable: true,
        get() { log.touched += 1; return name === 'webkitSpeechRecognition' ? Rec : undefined; } });
    }
  }
  win.window = win;
  vm.createContext(win);
  const before = new Set(Object.getOwnPropertyNames(win));
  vm.runInContext(src, win);
  const added = Object.getOwnPropertyNames(win).filter((k) => !before.has(k));
  return {
    win, log, store, synth, added, sfx: win.cmSfx,
    fire(name, ev) { (listeners[name] || []).forEach((f) => f(ev || {})); },
    hide() { win.document.visibilityState = 'hidden'; (docListeners.visibilitychange || []).forEach((f) => f({})); },
    at(t) { now = t; },
    listeners,
  };
}

const facts = {};

// the contract, the defaults, and one global only
{
  const b = browser();
  const s = b.sfx;
  facts.shape = {
    added: b.added,
    fns: ['cue', 'enabled', 'setEnabled', 'speak', 'voiceOut', 'setVoiceOut', 'armed', 'voiceReady'].filter((k) => typeof s[k] === 'function'),
    listenFns: ['start', 'stop', 'listening', 'parse'].filter((k) => typeof s.listen[k] === 'function'),
    onCommand: s.listen.onCommand, onMiss: s.listen.onMiss,
    supportedType: typeof s.listen.supported, cues: s.cues.slice().sort(), commands: s.listen.commands.slice().sort(),
    enabled: s.enabled(), voiceOut: s.voiceOut(),
    gestures: ['keydown', 'pointerdown', 'pointerup', 'touchend'].filter((k) => (b.listeners[k] || []).length),
  };
  // a second copy of the script keeps the first one
  const first = b.win.cmSfx;
  vm.runInContext(src, b.win);
  facts.shape.secondLoadKeepsFirst = b.win.cmSfx === first;
}

// nothing before a gesture; then one cue per gap
{
  const b = browser();
  const s = b.sfx;
  const r = {};
  r.beforeGesture = s.cue('lock');
  r.contextsBefore = b.log.contexts;
  r.oscillatorsBefore = b.log.oscillators;
  r.armedBefore = s.armed();
  b.fire('keydown');
  r.contextsAfter = b.log.contexts;
  r.armedAfter = s.armed();
  b.at(10000); r.first = s.cue('lock'); r.oscillators = b.log.oscillators;
  b.at(10000 + 1499); r.tooSoon = s.cue('alert');
  b.at(10000 + 1500); r.atGap = s.cue('settle');
  b.at(20000); r.unknown = s.cue('fanfare'); r.proto = s.cue('constructor'); r.notString = s.cue(null);
  b.fire('pointerdown'); b.fire('keydown');
  r.contextsAfterMore = b.log.contexts;
  facts.cue = r;
}

// a context that never starts drops cues (no burst later) and does not use up the gap
{
  const b = browser({ stuck: true });
  const s = b.sfx;
  b.fire('pointerdown');
  b.at(5000);
  facts.stuck = { cue: s.cue('warp-in'), oscillators: b.log.oscillators, resumes: b.log.resumes };
}

// the mute: persisted, read back by a new page, and followed across tabs
{
  const b = browser();
  const s = b.sfx;
  b.fire('keydown'); b.at(5000);
  s.cue('settle');
  const r = { stopsBefore: b.log.stops, cutBefore: b.log.cut };
  Object.assign(r, { set: s.setEnabled(false), stored: b.store['cm.sfx'], cue: s.cue('lock'), enabled: s.enabled() });
  r.cut = b.log.cut; r.stopsAfter = b.log.stops;
  const again = browser({ store: { 'cm.sfx': 'off' } });
  r.newPageEnabled = again.sfx.enabled();
  r.unmute = again.sfx.setEnabled(true); r.storedOn = again.store['cm.sfx'];
  // a cue cut short by a suspended context is still cut by a mute later: audio time, not the wall clock, says it ended
  const pause = browser();
  pause.fire('keydown'); pause.at(5000); pause.sfx.cue('empty-sky');
  pause.log.ctx.currentTime = 0.1; pause.at(11000); pause.sfx.cue('lock');
  pause.sfx.setEnabled(false); r.cutAfterPause = pause.log.cut;
  const later = browser();
  later.fire('keydown'); later.at(5000); later.sfx.cue('empty-sky');
  later.log.ctx.currentTime = 30; later.at(11000); later.sfx.cue('lock');
  later.sfx.setEnabled(false); r.cutAfterEnd = later.log.cut;
  const tab = browser();
  tab.fire('keydown'); tab.at(5000); tab.sfx.cue('alert');
  tab.fire('storage', { key: 'cm.sfx', newValue: 'off' }); r.otherTabMuted = tab.sfx.enabled(); r.otherTabCut = tab.log.cut;
  tab.fire('storage', { key: null, newValue: null }); r.clearIgnored = tab.sfx.enabled();
  tab.fire('storage', { key: 'cm.sfx.voice', newValue: 'on' }); r.otherTabVoice = tab.sfx.voiceOut();
  facts.mute = r;
}

// speaking aloud: opt-in, after a gesture, trimmed, a device voice first, a queue of at most a few lines
{
  const voices = [
    { name: 'remote-en', lang: 'en-US', localService: false, default: true },
    { name: 'local-fr', lang: 'fr-FR', localService: true },
    { name: 'local-en', lang: 'en-US', localService: true },
  ];
  const b = browser({ voices });
  const s = b.sfx;
  const r = {};
  r.offByDefault = s.speak('hello');
  r.setOn = s.setVoiceOut(true); r.stored = b.store['cm.sfx.voice'];
  r.beforeGesture = s.speak('hello');
  b.fire('pointerdown');
  r.spoke = s.speak('  one\n\tline   here  ');
  r.first = b.log.spoken[0];
  r.long = s.speak('x'.repeat(500)); r.longLength = b.log.spoken[1] && b.log.spoken[1].text.length;
  r.blank = s.speak('   ');
  r.third = s.speak('three'); r.fourth = s.speak('four'); r.fifth = s.speak('five');
  b.synth.last.onend();
  r.afterOneEnds = s.speak('six');
  b.synth.speaking = false;
  r.afterQuiet = s.speak('seven');
  r.muted = (s.setEnabled(false), s.speak('eight'));
  s.setEnabled(true);
  const cancels = b.log.cancels;
  s.setVoiceOut(false);
  r.offCancels = b.log.cancels > cancels;
  r.offStored = b.store['cm.sfx.voice'];
  r.afterOff = s.speak('nine');
  facts.speak = r;
  r.ready = s.voiceReady();
  const onlyRemote = browser({ voices: [voices[0]], store: { 'cm.sfx.voice': 'on' } });
  onlyRemote.fire('keydown');
  facts.speakRemoteOnly = { ok: onlyRemote.sfx.speak('hi'), spoken: onlyRemote.log.spoken.length, ready: onlyRemote.sfx.voiceReady() };
  const noVoices = browser({ store: { 'cm.sfx.voice': 'on' } });
  noVoices.fire('keydown');
  facts.speakNoVoices = { ok: noVoices.sfx.speak('hi'), ready: noVoices.sfx.voiceReady() };
  // a cancelled line that reports its end late does not free a place a newer line holds
  const late = browser({ voices, store: { 'cm.sfx.voice': 'on' } });
  late.fire('keydown');
  late.sfx.speak('old');
  const old = late.synth.last;
  late.sfx.setVoiceOut(false); late.sfx.setVoiceOut(true);
  const fill = ['b', 'c', 'd', 'e'].map((t) => late.sfx.speak(t));
  old.onerror({ error: 'canceled' });
  facts.speakLate = { fill, afterLateEnd: late.sfx.speak('f') };
  // pressing to talk stops the radio voice first, so the recogniser does not hear it
  const talk = browser({ voices, store: { 'cm.sfx.voice': 'on' } });
  talk.fire('keydown'); talk.sfx.speak('a line');
  const cancelsBefore = talk.log.cancels;
  talk.sfx.listen.start();
  facts.talkStopsVoice = { cancelled: talk.log.cancels > cancelsBefore, speakAgain: talk.sfx.speak('next line') };
  const french = browser({ voices, lang: 'fr-FR', store: { 'cm.sfx.voice': 'on' } });
  french.fire('keydown'); french.sfx.speak('salut');
  facts.speakFrench = french.log.spoken[0];
}

// listening: never on an insecure page, and the recogniser is not even looked up there
{
  const b = browser({ secure: false });
  const l = b.sfx.listen;
  facts.insecure = { supported: l.supported, reason: l.reason, touched: b.log.touched, start: l.start(), recs: b.log.recs.length };
  const none = browser({ recogniser: false });
  facts.missing = { supported: none.sfx.listen.supported, reason: none.sfx.listen.reason, start: none.sfx.listen.start() };
}

// listening on a secure page: push-to-talk, navigation words only
{
  const b = browser({ lang: 'de-DE' });
  const l = b.sfx.listen;
  const got = [], missed = [];
  l.onCommand = (name, heard) => got.push([name, heard]);
  l.onMiss = (heard) => missed.push(heard);
  const r = { supported: l.supported, reason: l.reason, note: l.note };
  r.start = l.start(); r.again = l.start(); r.listening = l.listening();
  const rec = b.log.recs[0];
  r.rec = rec && { started: rec.started, lang: rec.lang, continuous: rec.continuous, interim: rec.interimResults, alts: rec.maxAlternatives };
  r.local = l.local; r.recLocal = rec && rec.processLocally === true;
  const said = (...alts) => rec.onresult({ resultIndex: 0, results: [alts.map((t) => ({ transcript: t }))] });
  said(' Next contact. ');
  said('merge it', 'settle');
  said('sett le', 'Open');
  said();
  r.got = got; r.missed = missed;
  r.stop = l.stop(); r.stopped = rec.stopped; r.listeningAfter = l.listening(); r.stopIdle = l.stop();
  l.onCommand = () => { throw new Error('page error'); };
  l.start(); b.log.recs[1].onresult({ resultIndex: 0, results: [[{ transcript: 'sky' }]] });
  r.survivesThrow = l.listening();
  b.log.recs[1].onerror({ error: 'not-allowed' }); r.error = l.error;
  b.log.recs[1].onend(); r.endedAfterError = !l.listening();
  l.start(); b.hide(); r.hiddenStops = b.log.recs[2].stopped === 1 && !l.listening();
  facts.listen = r;
  // pairs, since '__proto__' as an object key would set the prototype; anything but a string or null
  // (a function found on the prototype, say) would print as null, so it is named instead
  const parsedAs = (text) => { const v = l.parse(text); return v === null || typeof v === 'string' ? v : 'not a command: ' + typeof v; };
  const parsed = [];
  for (const phrase of Object.keys(grammar)) parsed.push([phrase, parsedAs(phrase)]);
  for (const phrase of ['Brief me!', '  OPEN  ', 'show-the-sky', 'settle', 'snooze one hour', 'merge', 'delete it',
                        'send', '', 'next please', 'constructor', '__proto__', 'toString', 'hasOwnProperty']) {
    parsed.push([phrase, parsedAs(phrase)]);
  }
  parsed.push(['<null>', parsedAs(null)]);
  facts.parse = parsed;
}

// the on-device recogniser: used only when the browser says it is ready; never installed from the page
const tick = () => new Promise((done) => setImmediate(done));
(async () => {
  const b = browser({ available: 'available', lang: 'en-GB' });
  const l = b.sfx.listen;
  const r = { before: l.local };
  await tick();
  Object.assign(r, { local: l.local, note: l.note, asked: b.log.asked, installs: b.log.installs });
  l.start(); r.processLocally = b.log.recs[0].processLocally === true;
  b.log.recs[0].onerror({ error: 'language-not-supported' }); b.log.recs[0].onend();
  r.afterMissingPack = { local: l.local, note: l.note };
  l.start(); r.nextStartLocal = b.log.recs[1].processLocally === true;
  facts.local = r;
  // the answer arrives while a session runs: that session keeps its label; the next one is local
  const m = browser({ available: 'available' });
  const ml = m.sfx.listen;
  ml.start();
  await tick();
  facts.localMidSession = { during: [ml.local, ml.note], runningLocal: m.log.recs[0].processLocally === true };
  m.log.recs[0].onend();
  facts.localMidSession.after = [ml.local, ml.note];
  ml.start(); facts.localMidSession.nextLocal = m.log.recs[1].processLocally === true;
  const d = browser({ available: 'downloadable' });
  await tick();
  d.sfx.listen.start();
  facts.downloadable = { local: d.sfx.listen.local, processLocally: d.log.recs[0].processLocally === true,
                         installs: d.log.installs };
  const insecure = browser({ available: 'available', secure: false });
  await tick();
  facts.localInsecure = { asked: insecure.log.asked.length, local: insecure.sfx.listen.local };
  process.stdout.write(JSON.stringify(facts));
})().catch((e) => { console.error((e && e.stack) || e); process.exit(1); });
"""


@unittest.skipUnless(shutil.which("node"), "node not installed")
class Behaviour(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as tmp:
            js = Path(tmp, "sfx.js")
            js.write_text(sfx.script(), encoding="utf-8")
            harness = Path(tmp, "harness.js")
            harness.write_text(HARNESS, encoding="utf-8")
            cls.check = subprocess.run(["node", "--check", str(js)], capture_output=True, text=True,
                                       timeout=120)
            run = subprocess.run(["node", str(harness), str(js), json.dumps(sfx.GRAMMAR)],
                                 capture_output=True, text=True, timeout=120)
        if run.returncode:
            raise AssertionError("harness failed:\n" + (run.stdout + run.stderr)[-3000:])
        cls.facts = json.loads(run.stdout)

    def test_node_check_passes_on_the_emitted_script(self):
        self.assertEqual(self.check.returncode, 0, self.check.stderr[-2000:])

    def test_the_contract_in_a_page(self):
        shape = self.facts["shape"]
        self.assertEqual(shape["added"], ["cmSfx"], "the script adds one global only")
        self.assertEqual(shape["fns"], ["cue", "enabled", "setEnabled", "speak", "voiceOut", "setVoiceOut", "armed",
                                        "voiceReady"])
        self.assertEqual(shape["listenFns"], ["start", "stop", "listening", "parse"])
        self.assertIsNone(shape["onCommand"])
        self.assertIsNone(shape["onMiss"])
        self.assertEqual(shape["supportedType"], "boolean")
        self.assertEqual(set(shape["cues"]), CUE_NAMES)
        self.assertEqual(set(shape["commands"]), COMMANDS)
        self.assertTrue(shape["secondLoadKeepsFirst"])

    def test_sound_is_on_and_voice_off_by_default(self):
        shape = self.facts["shape"]
        self.assertTrue(shape["enabled"], "cues follow the page's chime: on until muted")
        self.assertFalse(shape["voiceOut"], "reading aloud is opt-in")

    def test_no_cue_before_a_gesture(self):
        cue = self.facts["cue"]
        self.assertEqual(self.facts["shape"]["gestures"], ["keydown", "pointerdown", "pointerup", "touchend"])
        self.assertFalse(cue["beforeGesture"])
        self.assertEqual(cue["contextsBefore"], 0, "no audio context before a gesture")
        self.assertEqual(cue["oscillatorsBefore"], 0)
        self.assertFalse(cue["armedBefore"])
        self.assertEqual(cue["contextsAfter"], 1)
        self.assertTrue(cue["armedAfter"])
        self.assertEqual(cue["contextsAfterMore"], 1, "one context for the page's life")

    def test_one_cue_per_gap(self):
        cue = self.facts["cue"]
        self.assertTrue(cue["first"])
        self.assertEqual(cue["oscillators"], len(sfx.CUES["lock"]))
        self.assertFalse(cue["tooSoon"], "a cue inside the gap is dropped")
        self.assertTrue(cue["atGap"], "the gap is GAP_MS exactly")
        self.assertFalse(cue["unknown"])
        self.assertFalse(cue["proto"], "a name off the prototype is not a cue")
        self.assertFalse(cue["notString"])

    def test_a_context_that_never_runs_queues_nothing(self):
        stuck = self.facts["stuck"]
        self.assertFalse(stuck["cue"])
        self.assertEqual(stuck["oscillators"], 0, "nothing scheduled on a suspended context to burst out later")
        self.assertGreaterEqual(stuck["resumes"], 2, "the cue asks the context to start again")

    def test_the_mute_persists_and_follows_other_tabs(self):
        mute = self.facts["mute"]
        self.assertFalse(mute["set"])
        self.assertEqual(mute["stored"], "off")
        self.assertFalse(mute["cue"])
        self.assertEqual(mute["cut"] - mute["cutBefore"], len(sfx.CUES["settle"]),
                         "the cue still sounding is cut, not left to finish")
        self.assertEqual(mute["stopsAfter"] - mute["stopsBefore"], len(sfx.CUES["settle"]))
        self.assertEqual(mute["otherTabCut"], len(sfx.CUES["alert"]), "a mute in another tab cuts it too")
        self.assertEqual(mute["cutAfterPause"], len(sfx.CUES["empty-sky"]) + len(sfx.CUES["lock"]),
                         "a cue held up by a suspended context is still cut")
        self.assertEqual(mute["cutAfterEnd"], len(sfx.CUES["lock"]), "a cue that has ended is let go")
        self.assertFalse(mute["enabled"])
        self.assertFalse(mute["newPageEnabled"], "a reload keeps the mute")
        self.assertTrue(mute["unmute"])
        self.assertEqual(mute["storedOn"], "on")
        self.assertFalse(mute["otherTabMuted"])
        self.assertFalse(mute["clearIgnored"])
        self.assertTrue(mute["otherTabVoice"])

    def test_speaking_is_opt_in_trimmed_and_capped(self):
        speak = self.facts["speak"]
        self.assertFalse(speak["offByDefault"])
        self.assertTrue(speak["setOn"])
        self.assertEqual(speak["stored"], "on")
        self.assertFalse(speak["beforeGesture"])
        self.assertTrue(speak["spoke"])
        self.assertEqual(speak["first"]["text"], "one line here")
        self.assertTrue(speak["long"])
        self.assertEqual(speak["longLength"], sfx.SPEAK_CHARS)
        self.assertFalse(speak["blank"])
        self.assertEqual((speak["third"], speak["fourth"], speak["fifth"]), (True, True, False),
                         "at most SPEAK_QUEUE lines wait")
        self.assertTrue(speak["afterOneEnds"], "a line that ends frees its place")
        self.assertTrue(speak["afterQuiet"], "a silent synthesiser clears a lost count")
        self.assertFalse(speak["muted"], "the mute silences speech too")
        self.assertTrue(speak["offCancels"], "turning voice off stops what is being read")
        self.assertEqual(speak["offStored"], "off")
        self.assertFalse(speak["afterOff"])

    def test_only_a_voice_on_the_device_speaks(self):
        self.assertEqual(self.facts["speak"]["first"]["voice"], "local-en")
        self.assertTrue(self.facts["speak"]["ready"])
        self.assertEqual(self.facts["speakFrench"]["voice"], "local-fr")
        self.assertEqual(self.facts["speakRemoteOnly"], {"ok": False, "spoken": 0, "ready": False},
                         "a line is never sent to a network voice")
        self.assertEqual(self.facts["speakNoVoices"], {"ok": False, "ready": False})

    def test_a_cancelled_line_ending_late_frees_no_place(self):
        late = self.facts["speakLate"]
        self.assertEqual(late["fill"], [True, True, True, True])
        self.assertFalse(late["afterLateEnd"])

    def test_pressing_to_talk_stops_the_radio_voice(self):
        talk = self.facts["talkStopsVoice"]
        self.assertTrue(talk["cancelled"])
        self.assertTrue(talk["speakAgain"], "speaking still works afterwards")

    def test_no_listening_on_an_insecure_page(self):
        insecure = self.facts["insecure"]
        self.assertFalse(insecure["supported"])
        self.assertIn("secure", insecure["reason"])
        self.assertEqual(insecure["touched"], 0, "the recogniser is not even looked up")
        self.assertFalse(insecure["start"])
        self.assertEqual(insecure["recs"], 0)
        missing = self.facts["missing"]
        self.assertFalse(missing["supported"])
        self.assertEqual(missing["reason"], sfx.NO_RECOGNISER)
        self.assertFalse(missing["start"])

    def test_push_to_talk_on_a_secure_page(self):
        listen = self.facts["listen"]
        self.assertTrue(listen["supported"])
        self.assertIsNone(listen["reason"])
        self.assertEqual(listen["note"], sfx.NOTE)
        self.assertTrue(listen["start"])
        self.assertFalse(listen["again"], "one recognition at a time")
        self.assertTrue(listen["listening"])
        self.assertEqual(listen["rec"], {"started": 1, "lang": "en-US", "continuous": False,
                                         "interim": False, "alts": 3})
        self.assertEqual(listen["got"], [["next", "Next contact."], ["open", "Open"]])
        self.assertEqual(listen["missed"], ["merge it", ""])
        self.assertTrue(listen["stop"])
        self.assertEqual(listen["stopped"], 1)
        self.assertFalse(listen["listeningAfter"])
        self.assertFalse(listen["stopIdle"])
        self.assertTrue(listen["survivesThrow"], "a page handler that throws does not break listening")
        self.assertEqual(listen["error"], "not-allowed")
        self.assertTrue(listen["endedAfterError"])
        self.assertTrue(listen["hiddenStops"], "a hidden page stops listening")
        self.assertFalse(listen["local"], "no on-device answer from the browser: not local")
        self.assertFalse(listen["recLocal"])

    def test_the_on_device_recogniser_only_when_the_browser_says_it_is_ready(self):
        local = self.facts["local"]
        self.assertFalse(local["before"], "local only once the browser has answered")
        self.assertTrue(local["local"])
        self.assertEqual(local["note"], sfx.NOTE_LOCAL)
        self.assertEqual(local["asked"], [{"langs": ["en-GB"], "processLocally": True}])
        self.assertEqual(local["installs"], 0, "nothing is installed from the page")
        self.assertTrue(local["processLocally"])
        self.assertEqual(local["afterMissingPack"], {"local": False, "note": sfx.NOTE},
                         "a missing language pack says so: the next press is not local")
        self.assertFalse(local["nextStartLocal"])
        self.assertEqual(self.facts["downloadable"], {"local": False, "processLocally": False, "installs": 0})
        mid = self.facts["localMidSession"]
        self.assertEqual(mid["during"], [False, sfx.NOTE], "a running session is never relabelled")
        self.assertFalse(mid["runningLocal"])
        self.assertEqual(mid["after"], [True, sfx.NOTE_LOCAL])
        self.assertTrue(mid["nextLocal"])
        self.assertEqual(self.facts["localInsecure"], {"asked": 0, "local": False})

    def test_the_grammar_answers_navigation_words_only(self):
        parsed = dict(self.facts["parse"])
        for phrase, command in sfx.GRAMMAR.items():
            self.assertEqual(parsed[phrase], command, phrase)
        self.assertEqual(parsed["Brief me!"], "briefing")
        self.assertEqual(parsed["  OPEN  "], "open")
        self.assertEqual(parsed["show-the-sky"], "sky")
        for phrase in ("settle", "snooze one hour", "merge", "delete it", "send", "", "next please",
                       "constructor", "__proto__", "toString", "hasOwnProperty", "<null>"):
            self.assertIsNone(parsed[phrase], phrase)


if __name__ == "__main__":
    unittest.main()
