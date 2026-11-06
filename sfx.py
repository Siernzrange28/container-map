#!/usr/bin/env python3
"""sfx.py — the page's sound and voice, as the text of one inline script.

render-game.py puts script() in <script id="cm-sfx">, ahead of the page script. The script defines

    window.cmSfx = {cue(name), enabled(), setEnabled(b), speak(text), voiceOut(), setVoiceOut(b),
                    armed(), voiceReady(), cues,
                    listen: {supported, reason, note, local, error, commands, start(), stop(),
                             listening(), parse(text), onCommand: fn(command, heard)|null,
                             onMiss: fn(heard)|null}}

and the page calls it only behind a guard (window.cmSfx && ...), so an empty slot is a silent page.

- Cues are synthesised with WebAudio (no sound files, nothing fetched). Like the page's chime they need
  a user gesture first: the first key or pointer press makes the audio context, and until it runs a
  cue is dropped, never queued. At most one cue plays per GAP_MS. Cues are on unless muted; the mute is
  kept in localStorage SOUND_KEY ("off" = muted) and also silences speech.
- speak(text) reads one line aloud with speechSynthesis, only when the person turned it on
  (VOICE_KEY = "on"; off by default), and only with a voice that runs on the device: a line can carry
  private titles, and a network voice would send it to the browser's vendor. voiceReady() says
  whether the device has such a voice now, so a toggle can say why it stays silent.
- listen is push-to-talk speech recognition for page navigation only (COMMANDS): it never changes
  anything by voice. It exists only on a secure page where the browser has a recogniser; otherwise
  `supported` is false and `reason` says why. The recogniser is the browser's own, which may send
  the audio to its vendor; where the browser says its on-device recogniser is ready for the language
  (SpeechRecognition.available with processLocally), that one is used and `local` is true. `note`
  says which, in words a toggle can show. Nothing is ever installed or downloaded for it.

The script runs in its own function scope and adds one global, window.cmSfx, so none of its names
can collide with the page script's.
"""
from __future__ import annotations

import json

GAP_MS = 1500                # at most one cue per this many milliseconds
SOUND_KEY = "cm.sfx"         # localStorage: "off" mutes cues and speech; anything else is on
VOICE_KEY = "cm.sfx.voice"   # localStorage: "on" reads lines aloud; anything else is off
SPEAK_CHARS = 160            # a spoken line is cut to this many characters
SPEAK_QUEUE = 4              # lines waiting to be spoken at most (the radio holds 4); more are dropped

# Each cue is a few tones: [wave, start Hz, end Hz (0 = steady), offset s, length s, peak gain].
# Short and quiet (the page's chime peaks at 0.08); none loops.
CUES = {
    "warp-in": [["triangle", 1400, 220, 0, 0.32, 0.05], ["sine", 110, 0, 0.26, 0.22, 0.05]],
    "lock": [["square", 1320, 0, 0, 0.05, 0.03], ["square", 1760, 0, 0.07, 0.06, 0.03]],
    "settle": [["triangle", 523.25, 0, 0, 0.16, 0.06], ["triangle", 659.25, 0, 0.09, 0.16, 0.06],
               ["triangle", 783.99, 0, 0.18, 0.16, 0.06], ["triangle", 1046.5, 0, 0.27, 0.32, 0.06]],
    "empty-sky": [["sine", 392, 0, 0, 0.45, 0.04], ["sine", 587.33, 0, 0, 0.45, 0.04],
                  ["sine", 523.25, 0, 0.4, 0.6, 0.05], ["sine", 783.99, 0, 0.4, 0.6, 0.05]],
    "alert": [["square", 988, 0, 0, 0.12, 0.04], ["square", 740, 0, 0.16, 0.14, 0.04]],
}

# What a person may say, after lower-casing and dropping everything but letters and single spaces,
# and the command it means. Navigation only: nothing here changes state.
COMMANDS = ("next", "prev", "open", "briefing", "sky")
GRAMMAR = {
    "next": "next", "next contact": "next", "next one": "next",
    "previous": "prev", "prev": "prev", "previous contact": "prev", "previous one": "prev",
    "open": "open", "open it": "open", "open this": "open", "open contact": "open",
    "briefing": "briefing", "brief me": "briefing", "sitrep": "briefing", "today": "briefing",
    "sky": "sky", "show sky": "sky", "show the sky": "sky",
}

NOT_SECURE = "voice needs a secure page: open the map on this machine or over a secure address"
NO_RECOGNISER = "this browser has no speech recognition"
NOTE = "speech is recognised by the browser, which may send what you say to its speech service"
NOTE_LOCAL = "speech is recognised on this device"

_ENGINE = r"""(function () {
  "use strict";
  var w = window;
  if (w.cmSfx) return;
  var GAP_MS = @GAP_MS@;
  var CUES = @CUES@;
  var GRAMMAR = @GRAMMAR@;
  var COMMANDS = @COMMANDS@;
  var SOUND_KEY = @SOUND_KEY@, VOICE_KEY = @VOICE_KEY@;
  var SPEAK_CHARS = @SPEAK_CHARS@, SPEAK_QUEUE = @SPEAK_QUEUE@;
  var own = Object.prototype.hasOwnProperty;
  function has(map, key) { return typeof key === "string" && own.call(map, key); }
  function read(key) { try { return w.localStorage.getItem(key); } catch (e) { return null; } }
  function write(key, value) { try { w.localStorage.setItem(key, value); } catch (e) {} }
  function clock() { var p = w.performance; return p && typeof p.now === "function" ? p.now() : Date.now(); }
  function call(fn) {
    if (typeof fn !== "function") return;
    try { fn.apply(null, Array.prototype.slice.call(arguments, 1)); } catch (e) {}
  }

  // ---- cues: WebAudio needs a user gesture first; until the context runs, a cue is dropped
  var sound = read(SOUND_KEY) !== "off", voice = read(VOICE_KEY) === "on";
  var ctx = null, gesture = false, last = -Infinity, playing = [];
  function wake() {
    try {
      if (ctx.state !== "running" && ctx.state !== "closed") {
        var p = ctx.resume(); if (p && typeof p.catch === "function") p.catch(function () {});
      }
    } catch (e) {}
  }
  function arm() {
    gesture = true;
    try {
      if (!ctx) { var Ctx = w.AudioContext || w.webkitAudioContext; if (!Ctx) return; ctx = new Ctx(); }
    } catch (e) { return; }
    wake();
  }
  ["keydown", "pointerdown", "pointerup", "touchend"].forEach(function (name) {
    w.addEventListener(name, arm, {capture: true, passive: true});
  });
  function armed() { return gesture && !!ctx && ctx.state === "running"; }
  function tone(t) {
    var o = ctx.createOscillator(), g = ctx.createGain(), t0 = ctx.currentTime + t[3], t1 = t0 + t[4];
    o.type = t[0];
    o.frequency.setValueAtTime(t[1], t0);
    if (t[2]) o.frequency.exponentialRampToValueAtTime(t[2], t1);
    g.gain.setValueAtTime(0.0001, t0);
    g.gain.exponentialRampToValueAtTime(t[5], t0 + 0.01);
    g.gain.exponentialRampToValueAtTime(0.0001, t1);
    o.connect(g); g.connect(ctx.destination);
    o.start(t0); o.stop(t1 + 0.02);
    playing.push([o, g, t1 + 0.02]);
  }
  function silence() {          // a mute also cuts the cue that is still sounding
    var nodes = playing; playing = [];
    nodes.forEach(function (n) {
      try { n[1].disconnect(); } catch (e) {}
      try { n[0].stop(); } catch (e) {}
    });
  }
  function cue(name) {
    if (!sound || !has(CUES, name)) return false;
    if (!armed()) { if (gesture && ctx) wake(); return false; }
    var now = clock();
    if (now - last < GAP_MS) return false;
    var heard = ctx.currentTime;  // audio time, which stands still while the context is suspended
    playing = playing.filter(function (n) { return n[2] > heard; });
    try { CUES[name].forEach(tone); } catch (e) { return false; }
    last = now;
    return true;
  }

  // ---- voice out: opt-in, a voice on the device only, a few lines queued at most
  var queued = 0, held = [], round = 0;
  function pickVoice(synth) {
    var all = [];
    try { all = synth.getVoices() || []; } catch (e) {}
    var lang = String((w.navigator && w.navigator.language) || "").toLowerCase(), base = lang.split("-")[0];
    function score(v) {
      var l = String(v.lang || "").toLowerCase().replace("_", "-");
      return (l === lang ? 4 : 0) + (base && l.split("-")[0] === base ? 2 : 0) + (v["default"] ? 1 : 0);
    }
    var best = null;
    for (var i = 0; i < all.length; i++) {
      var v = all[i];
      if (v && v.localService && (!best || score(v) > score(best))) best = v;
    }
    return best;
  }
  try { if (w.speechSynthesis) w.speechSynthesis.getVoices(); } catch (e) {}    // the list loads late in some browsers
  function voiceReady() { return !!(w.speechSynthesis && pickVoice(w.speechSynthesis)); }
  function hush() {
    queued = 0; held = []; round += 1;
    try { if (w.speechSynthesis) w.speechSynthesis.cancel(); } catch (e) {}
  }
  function speak(text) {
    var synth = w.speechSynthesis, Utterance = w.SpeechSynthesisUtterance;
    if (!sound || !voice || !gesture || !synth || typeof Utterance !== "function") return false;
    var line = String(text == null ? "" : text).replace(/\s+/g, " ").trim().slice(0, SPEAK_CHARS);
    if (!line) return false;
    var v = pickVoice(synth);
    if (!v) return false;
    try { if (!synth.speaking && !synth.pending) { queued = 0; held = []; round += 1; } } catch (e) {}
    if (queued >= SPEAK_QUEUE) return false;
    var u;
    try { u = new Utterance(line); u.voice = v; u.lang = v.lang; } catch (e) { return false; }
    var done = false, mine = round;
    u.onend = u.onerror = function () {
      if (done) return;
      done = true;
      if (mine !== round) return;          // a line from before a cancel: its place was already freed
      queued = Math.max(0, queued - 1);
      var k = held.indexOf(u); if (k >= 0) held.splice(k, 1);
    };
    queued += 1; held.push(u);
    try { synth.speak(u); } catch (e) { u.onend(); return false; }
    return true;
  }
  function setEnabled(b) {
    sound = !!b; write(SOUND_KEY, sound ? "on" : "off");
    if (!sound) { silence(); hush(); }
    return sound;
  }
  function setVoiceOut(b) { voice = !!b; write(VOICE_KEY, voice ? "on" : "off"); if (!voice) hush(); return voice; }
  w.addEventListener("storage", function (e) {
    if (!e || typeof e.key !== "string") return;
    if (e.key === SOUND_KEY) { sound = e.newValue !== "off"; if (!sound) { silence(); hush(); } }
    else if (e.key === VOICE_KEY) { voice = e.newValue === "on"; if (!voice) hush(); }
  });

  // ---- voice in: push-to-talk, navigation words only, never on an insecure page
  function parse(text) {
    var said = String(text == null ? "" : text).toLowerCase().replace(/[^a-z ]+/g, " ").replace(/\s+/g, " ").trim();
    return has(GRAMMAR, said) ? GRAMMAR[said] : null;
  }
  function speechLang() {
    var lang = String((w.navigator && w.navigator.language) || "");
    return /^en\b/i.test(lang) ? lang : "en-US";
  }
  var secure = w.isSecureContext === true;
  var Recognition = secure ? (w.SpeechRecognition || w.webkitSpeechRecognition || null) : null;
  var rec = null, localLater = null;
  function setLocal(on) { listen.local = !!on; listen.note = on ? @NOTE_LOCAL@ : @NOTE@; }
  function ended(r) {           // a session is over: an on-device answer that came during it applies now
    if (rec !== r) return;
    rec = null;
    if (localLater !== null) { setLocal(localLater); localLater = null; }
  }
  var listen = {
    supported: typeof Recognition === "function",
    reason: !secure ? @NOT_SECURE@ : typeof Recognition !== "function" ? @NO_RECOGNISER@ : null,
    note: @NOTE@,
    local: false,
    commands: COMMANDS.slice(),
    onCommand: null,
    onMiss: null,
    error: null,
    parse: parse,
    listening: function () { return !!rec; },
    start: function () {
      if (!listen.supported || rec) return false;
      if (queued || held.length) hush();      // the recogniser would hear the radio voice
      var r;
      try { r = new Recognition(); } catch (e) { listen.error = String((e && e.message) || e); return false; }
      r.lang = speechLang();
      r.continuous = false; r.interimResults = false; r.maxAlternatives = 3;
      if (listen.local) { try { r.processLocally = true; } catch (e) { setLocal(false); } }
      r.onresult = function (e) {
        var results = (e && e.results) || [], heard = "", name = null, hit = "";
        for (var i = (e && e.resultIndex) || 0; i < results.length; i++) {
          var alts = results[i];
          for (var k = 0; alts && k < alts.length; k++) {
            var t = alts[k] && alts[k].transcript;
            if (typeof t !== "string") continue;
            if (!heard) heard = t;
            if (!name) { name = parse(t); if (name) hit = t; }
          }
        }
        if (name) call(listen.onCommand, name, hit.trim());
        else call(listen.onMiss, heard.trim());
      };
      r.onerror = function (e) {
        listen.error = String((e && e.error) || "error");
        if (listen.error === "language-not-supported") { localLater = null; setLocal(false); }
      };
      r.onend = function () { ended(r); };
      rec = r; listen.error = null;
      try { r.start(); } catch (e) { ended(r); listen.error = String((e && e.message) || e); return false; }
      return true;
    },
    stop: function () {
      if (!rec) return false;
      try { rec.stop(); } catch (e) { ended(rec); }
      return true;
    }
  };
  // the on-device recogniser, only when the browser says it is ready (never installed from here)
  if (listen.supported && typeof Recognition.available === "function") {
    try {
      var ready = Recognition.available({langs: [speechLang()], processLocally: true});
      if (ready && typeof ready.then === "function") {
        ready.then(function (state) {
          var on = state === "available";
          if (rec) localLater = on; else setLocal(on);      // never relabel a session already running
        }, function () {});
      }
    } catch (e) {}
  }
  if (w.document && typeof w.document.addEventListener === "function") {
    w.document.addEventListener("visibilitychange", function () {
      if (w.document.visibilityState === "hidden") listen.stop();
    });
  }

  w.cmSfx = {
    cue: cue,
    enabled: function () { return sound; },
    setEnabled: setEnabled,
    speak: speak,
    voiceOut: function () { return voice; },
    setVoiceOut: setVoiceOut,
    armed: armed,
    voiceReady: voiceReady,
    cues: Object.keys(CUES),
    listen: listen
  };
})();
"""


def _js(value):
    """A JSON literal that is also safe inside an inline script ('<' never appears raw)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":")).replace("<", "\\u003c")


def script():
    """The JavaScript for the page's cm-sfx slot ("" = no sound, no voice). Never contains a
    closing script tag; the same text on every call (the render loop compares pages)."""
    fills = {
        "@GAP_MS@": str(int(GAP_MS)), "@SPEAK_CHARS@": str(int(SPEAK_CHARS)),
        "@SPEAK_QUEUE@": str(int(SPEAK_QUEUE)),
        "@CUES@": _js(CUES), "@GRAMMAR@": _js(GRAMMAR), "@COMMANDS@": _js(list(COMMANDS)),
        "@SOUND_KEY@": _js(SOUND_KEY), "@VOICE_KEY@": _js(VOICE_KEY),
        "@NOT_SECURE@": _js(NOT_SECURE), "@NO_RECOGNISER@": _js(NO_RECOGNISER), "@NOTE@": _js(NOTE),
        "@NOTE_LOCAL@": _js(NOTE_LOCAL),
    }
    text = _ENGINE
    for token, value in fills.items():
        text = text.replace(token, value)
    return text
