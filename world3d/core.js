// world3d/core.js — the 3D view's core: the module registry, one renderer per session, the frame, the two clocks,
// the render-scale governor, enter and leave (the match frame, then a cut to a chase pose; the camera lane replaces the cut
// with its timeline), context loss, the shader warm-up with flat stand-ins, the error box, the test hooks, and stand-in
// bodies (sky, sun, planets, problem craft, the owner's ship) for whatever no module draws yet.
//
// The 3D bundle is one classic script of its own (vendor3d.py: these files in a fixed order, core first, then the skin's 3D
// data as CM3D.skin3d), loaded on the first entry into 3D after three.js. The page reaches it through window.CM3D and its
// CM hooks (render-game.py, "THE 3D VIEW, page side"); a module never reads page globals: everything comes in `world`
// (the page's cmWorld()) and `ctx`.
//
//   CM3D.module(name, {build(scene, world, ctx), update(world, dt, ctx), rebuild(scene, world, ctx), dispose()})
//       registers (or replaces) a module. build runs on every entry, and again (after dispose) whenever world.rev moves,
//       unless the module has rebuild, which then runs instead. update runs every frame; its dt is the WORLD clock
//       (ctx.sceneDt: 0 while the world is paused); camera and input read ctx.dt (the real clock).
//   CM3D.provides(...names)   a module draws these, so the core's stand-in does not: 'sky', 'sun', 'planets', 'foes',
//       'ship', 'hud' (the core's reticle, counts and target line), 'camera' (the core stops moving the camera after the
//       first frame), 'transition' (the entry and exit timeline: see CM3D.transition below).
//   CM3D.transition = ctx.transition   the seam the camera lane's timeline plugs into. Without 'transition' provided the
//       core holds the matched first frame MATCH_HOLD s (or while hold=match), then cuts to the chase pose, and leaves at
//       once. With it provided, after the first frame the screen is the provider's: phase() is 'enter' (mode 'entering'),
//       and a user leave (I or M, Back, the pause card) makes phase() 'exit' (mode 'leaving') instead of leaving. The
//       provider drives the camera, calls step(name) on every frame it does (its own phase name: 'dive', 'orbit', ...)
//       and done(rig) at the end: the core then goes to 'fp' (cutting to rig unless 'camera' is provided too) or off.
//       match() is the first frame's pose {x, y, h, fov, offset{x, y}} to start a dive from. A provider that stops
//       reporting (no step and no done for HANDOFF_WAIT s of view time) is finished by the core (audit.handoff.timedOut),
//       so the view never sticks half way. Context loss and errors always leave at once.
//   ctx = {THREE, renderer, scene, camera, lights{sun, ambient}, skin3d, quality{phone, aa, tier}, audit, page{haptic,
//       settings}, sky:null, assets:{}, hover:null, world, dt, sceneDt, sceneT, t, mode, rig, lock, reduced, debug,
//       hudHidden, transition, ship{alt, pitch, rollDeg}, renderInset(camera, rect, opts), project(x, y, z),
//       toWorld(x2d, y2d, alt)}
//   ctx.ship   the camera lane's cosmetic attitude of the owner's ship (px, py and the heading stay the 2D truth): alt in
//       u (null: the core's clearance altitude), pitch in degrees, rollDeg (null: the 2D barrel roll's angle). The core
//       reports them in audit.ship {x, y, alt, hdg, spd, bank, pitch, rollDeg, uturn, boost, brake} and poses its own
//       stand-in ship by them.
//   ctx.glareK   0-1, how much of the sun's corona, glare and flare to draw (the camera lane ramps it in over the entry:
//       0 through the dive and the first half of the orbit); 1 without a camera lane.
//   ctx.audit: what window.__3dAudit() reports. The core owns the top level; a module may write only its namespaces
//       (NS below; any module may write ext.*). A write outside them throws inside that module and is caught like any
//       other fault: counted in errors[], shown in #err3d, the module quarantined after its third.
//   ctx.renderInset(camera, rect, opts)   one extra view this frame, drawn after the main one into rect {x, y, w, h}
//       (CSS px from the top left) with its own scissor; opts {scene, clear (a colour), border (a CSS colour)}.
//
// Test hooks: __3dStep(n, dt), __3dPixel(x, y), __3dProject(uid), __3dSunSide(uid), __3dPick(x, y),
// __3dApproach(uid), __3dHold(on), __3dDebug {spawnContact, clearContacts, clouds, planets, cycleComms, hideHud, fps}; window.__3dAudit is the page's (it asks
// CM3D.audit() once a session has started). Neutral words only: a skin's own names and shapes come with its 3D data.
(function (g) {
  'use strict';
  if (g.CM3D && g.CM3D.v === 1 && g.CM3D.core === 1) return;

  // ---------------------------------------------------------------- the registry
  var order = [], mods = {}, provided = {}, demos = {};
  var NAME = /^[a-z][a-z0-9]{0,23}$/;
  function module(name, def) {
    if (typeof name !== 'string' || !NAME.test(name)) throw new Error('CM3D.module: not a module name: ' + String(name));
    def = def || {};
    ['build', 'update', 'rebuild', 'dispose'].forEach(function (k) {
      if (def[k] != null && typeof def[k] !== 'function') throw new Error('CM3D.module ' + name + ': ' + k + ' is not a function');
    });
    if (!Object.prototype.hasOwnProperty.call(mods, name)) order.push(name);
    mods[name] = def;
    if (S) S.built[name] = false;            // a module registered while the view is up is built on the next frame
    return def;
  }
  function provides() { for (var i = 0; i < arguments.length; i++) provided[String(arguments[i])] = true; }
  function isProvided(name) { return provided[name] === true; }

  // ---------------------------------------------------------------- the audit and its namespaces
  // which top-level keys of the audit each module may write; the core writes everything else
  var NS = {
    space: ['scene', 'sky', 'fx', 'flare', 'atmosphere'], routes: ['scene', 'sky', 'fx', 'flare', 'atmosphere'],
    fx: ['scene', 'sky', 'fx', 'flare', 'atmosphere'],
    craft: ['models', 'craft'], hullkit: ['models', 'craft'],
    camera: ['camera', 'hud', 'transition', 'input', 'lock'], hud: ['camera', 'hud', 'transition', 'input', 'lock']
  };
  var SPACES = ['scene', 'sky', 'fx', 'models', 'craft', 'camera', 'hud', 'transition', 'input', 'ext', 'compile', 'clock',
    'parity', 'widthShare', 'radiusPx', 'buffer'];
  var A = {};
  function resetAudit() {
    var keep = { errors: A.errors || [], lost: A.lost || 0, reason: A.reason || '' };
    Object.keys(A).forEach(function (k) { delete A[k]; });
    SPACES.forEach(function (k) { A[k] = {}; });
    A.mode = 'off'; A.reason = keep.reason; A.renderer = null; A.three = null; A.calls = 0; A.triangles = 0; A.textures = 0;
    A.dpr = 0; A.scale = 1; A.fps = 0; A.lock = null; A.errors = keep.errors; A.lost = keep.lost; A.demo = null;
    A.flare = null; A.atmosphere = null; A.foeAngleDelta = 0; A.shipMoved = false;
    A.ship = { x: 0, y: 0, alt: 0, hdg: 0, spd: 0, bank: 0, pitch: 0, rollDeg: 0, uturn: null, boost: false, brake: false };
    A.focus = null; A.skin = ''; A.look = ''; A.handoff = { phase: null, name: '', by: null, timedOut: false };
    A.clock = { scale: 1, t: 0 }; A.compile = { pending: 0, placeholders: 0, failed: 0, ms: 0 }; A.buffer = { w: 0, h: 0 };
    A.hud.visible = true;
  }
  resetAudit();
  var proxies = {};
  function denied(name, key) { return new TypeError('3D module "' + name + '" may not write audit.' + key + ' (its part: ' + (NS[name] || []).concat(['ext']).join(', ') + ')'); }
  function readOnly(name, path, obj) {
    return new Proxy(obj, {
      get: function (o, k) { var v = o[k]; return v && typeof v === 'object' ? readOnly(name, path + '.' + String(k), v) : v; },
      set: function () { throw denied(name, path); },
      deleteProperty: function () { throw denied(name, path); },
      defineProperty: function () { throw denied(name, path); }
    });
  }
  function auditFor(name) {
    if (!name) return A;
    if (proxies[name]) return proxies[name];
    var allow = {};
    (NS[name] || []).concat(['ext']).forEach(function (k) { allow[k] = true; });
    proxies[name] = new Proxy(A, {
      get: function (o, k) { var v = o[k]; return v && typeof v === 'object' && !allow[k] ? readOnly(name, String(k), v) : v; },
      set: function (o, k, v) { if (!allow[k]) throw denied(name, String(k)); o[k] = v; return true; },
      deleteProperty: function (o, k) { if (!allow[k]) throw denied(name, String(k)); delete o[k]; return true; },
      defineProperty: function (o, k, d) { if (!allow[k]) throw denied(name, String(k)); Object.defineProperty(o, k, d); return true; }
    });
    return proxies[name];
  }

  // ---------------------------------------------------------------- faults: caught, counted, shown, quarantined
  var faults = {}, CURRENT = null, QUARANTINE = 3;
  function fault(name, phase, err) {
    var msg = String((err && err.message) || err || 'error').slice(0, 240);
    var rec = faults[name] || (faults[name] = { module: name, phase: phase, message: msg, count: 0, quarantined: false });
    rec.count++; rec.phase = phase; rec.message = msg; rec.quarantined = rec.count >= QUARANTINE;
    A.errors = Object.keys(faults).map(function (k) { var f = faults[k]; return { module: f.module, phase: f.phase, message: f.message, count: f.count, quarantined: f.quarantined }; });
    showErrors();
    try { if (g.console && g.console.warn) g.console.warn('3D module ' + name + ' (' + phase + '):', msg); } catch (e) { /* no console */ }
  }
  function guard(name, phase, fn) {
    var rec = faults[name];
    if (rec && rec.quarantined) return false;
    var was = CURRENT; CURRENT = name;
    try { fn(); return true; } catch (e) { fault(name, phase, e); return false; } finally { CURRENT = was; }
  }

  // ---------------------------------------------------------------- the render-scale governor (pure: no THREE, no DOM)
  /* port: nebreck (MIT) */
  // AdaptiveResolution.ts:10-97 re-written: a frame over 0.1 s is ignored (a stall is not the cost of drawing); the frame
  // time's EMA (0.1) starts at the first accepted frame; above 1/52 s the slow timer runs, below 1/58 s the fast one, in
  // between both stop; 0.8 s slow -> ratio x0.85 (not under the floor), 4 s fast -> x1.08 (not over max). Plain float
  // seconds, += and >=, no epsilon: the tested path (test_3d_core.py::test_governor_path) depends on exactly that.
  function govNew(opts) {
    opts = opts || {};
    return { ratio: 1, ema: null, slowFor: 0, fastFor: 0, floor: opts.floor != null ? opts.floor : 0.5, max: opts.max != null ? opts.max : 1, shifts: 0 };
  }
  function govStep(gv, dt) {
    if (!(dt > 0) || dt > 0.1) return gv.ratio;
    gv.ema = gv.ema === null ? dt : gv.ema + (dt - gv.ema) * 0.1;
    if (gv.ema > 1 / 52) { gv.slowFor += dt; gv.fastFor = 0; }
    else if (gv.ema < 1 / 58) { gv.fastFor += dt; gv.slowFor = 0; }
    else { gv.slowFor = 0; gv.fastFor = 0; }
    if (gv.slowFor >= 0.8) { gv.ratio = Math.max(gv.floor, gv.ratio * 0.85); gv.slowFor = 0; gv.shifts++; }
    else if (gv.fastFor >= 4) { gv.ratio = Math.min(gv.max, gv.ratio * 1.08); gv.fastFor = 0; gv.shifts++; }
    return gv.ratio;
  }
  // a drawn frame's gap (s) fed to the governor under a frame cap (30 or 60 a second, else none): a capped frame is
  // judged against its own budget (33 ms at 30 is on time, fed as 16.7 ms); a stall over 0.1 s is ignored as it came,
  // before that scaling could bring it under the limit
  function govFeed(gv, gap, cap) {
    cap = Number(cap);
    return govStep(gv, gap > 0.1 ? gap : (cap === 30 || cap === 60) ? gap * cap / 60 : gap);
  }

  // ---------------------------------------------------------------- session state
  var S = null;            // the view while it is up (or was: mode 'off' after it left)
  var R = { renderer: null, canvas: null, aa: null, lost: false, disposing: false };   // one renderer per session
  var P = null;            // the page adapter (PAGE3D)
  var started = false, losses = 0, debugRev = 0, debug = { extra: [], cleared: false, comms: 0, spawned: 0 };
  var held = false;        // __3dHold: frames draw, no time passes
  var captionShown = false; // the first-person caption shows on the first entry of a session only
  var FOE_RATE = 0.24;     // rad/s of a stand-in problem craft on its orbit (the 2D orbit's 1/4200 ms)
  var SAMPLE_WINDOW = 0.4; // s of real time foeAngleDelta and shipMoved look back over
  var MATCH_HOLD = 0.25;   // s the first frame (the 2D frame rebuilt in 3D) stays before the cut
  // the first frame looks straight down through a long lens from far away: the 2D map is a flat (orthographic) picture,
  // and at 55 degrees a sphere off the axis draws up to 1/cos(46 deg) = 1.46 times wider along the radius than its
  // 2D disc (the checker measured 2.4 px off at 1600x900); at 4 degrees the corner of the screen is 3.7 degrees off
  // the axis, 1.002 times (0.1 px on a 50 px planet). The dive (the camera lane) opens the lens to FOV.
  var MATCH_FOV = 4;
  var HANDOFF_WAIT = 0.6;  // s of view time a transition provider may go without step()/done() before the core ends it
  var USER_LEAVE = { key: 1, back: 1, pause: 1 };   // leaves the exit timeline plays for; any other leave is at once
  var FOV = 55;            // vertical, degrees
  var CHASE = { back: 50, up: 9, ahead: 40, ease: 8 };   // u, u, u, 1/s
  // the sun's near field, in sun radii: its glare sprite's radius (world3d/space.js draws the glare 8 r wide). No eye
  // goes inside 1.05 of it during an entry (the camera lane's [min-clearance] counts the sun at this radius), so a ship
  // the 2D map docks at the sun launches first, to LAUNCH_PAD u beyond that (the first-entry orbit's 40 u and room)
  var SUN_NEAR = 4, CLEAR_K = 1.05, LAUNCH_PAD = 60;
  // the launch point's open space: LAUNCH_ROOM u from every planet's surface, and LAUNCH_RUN u of clear flight ahead of it
  var LAUNCH_ROOM = 300, LAUNCH_RUN = 600;
  // the shader warm-up's race (T3): 2.5 s, or until the entry's timeline (dive, orbit, the cockpit's rise) is over when that
  // is later, so the stand-ins never pop back to real materials mid-dive; RACE_CAP s at most (a paused entry)
  var RACE_S = 2.5, RACE_CAP = 30;
  var SENT = {
    worldPaused: 'World paused · press . to resume', worldSlow: 'World at a quarter of its speed · let go of , for full speed',
    caption: 'Cockpit view · Esc pauses · I returns to the map'
  };

  function nowS() { return (g.performance && g.performance.now ? g.performance.now() : Date.now()) / 1000; }
  function doc() { return g.document; }
  function setting(key, dflt) { try { return P && P.settings ? String(P.settings.get(key, dflt)) : dflt; } catch (e) { return dflt; } }
  function flags() { try { return (P && P.flags && P.flags()) || {}; } catch (e) { return {}; } }
  function params() { return (P && P.params) || {}; }
  function phone() { return !!flags().phone; }

  // ---------------------------------------------------------------- DOM overlays of the core (styles set from JS: the CSP)
  var FONT = '"Barlow Condensed","Arial Narrow",sans-serif', MONO = '"Share Tech Mono",ui-monospace,Menlo,monospace';
  function overlay(id, css) {
    var d = doc(); if (!d || !d.body) return null;
    var el = d.getElementById(id);
    if (!el) { el = d.createElement('div'); el.id = id; d.body.appendChild(el); }
    var base = { position: 'fixed', zIndex: '52', pointerEvents: 'none', color: '#E8EEF6', fontFamily: FONT, display: 'none' };
    Object.keys(base).forEach(function (k) { el.style[k] = base[k]; });
    Object.keys(css || {}).forEach(function (k) { el.style[k] = css[k]; });
    return el;
  }
  function showErrors() {
    if (!A.errors.length) return;
    var el = overlay('err3d', { left: '12px', bottom: '12px', maxWidth: '46vw', padding: '8px 10px', background: 'rgba(74,18,22,.92)',
      border: '1px solid #FF5A5F', fontSize: '14px', lineHeight: '1.35', whiteSpace: 'pre-wrap' });
    if (!el) return;
    el.setAttribute('role', 'status');
    el.textContent = A.errors.map(function (f) {
      return '3D part "' + f.module + '" failed in ' + f.phase + ': ' + f.message + (f.count > 1 ? ' (' + f.count + ' times)' : '') +
        (f.quarantined ? ' · switched off, the rest keeps drawing' : '');
    }).join('\n');
    el.style.display = S && S.mode !== 'off' && !S.hudHidden ? 'block' : 'none';   // hideHud(true): a clean shot, the box too
  }
  function note(text) {
    var el = overlay('note3d', { left: '50%', top: '14px', transform: 'translateX(-50%)', padding: '4px 12px', background: 'rgba(11,18,32,.85)',
      border: '1px solid #22304A', fontSize: '16px', fontWeight: '600', letterSpacing: '.04em' });
    if (!el) return;
    el.textContent = text || '';
    el.style.display = text && S && S.mode !== 'off' && !S.hudHidden ? 'block' : 'none';
  }
  function perfBox() {
    return overlay('perf3d', { left: '12px', top: '12px', padding: '4px 8px', background: 'rgba(5,7,13,.8)', border: '1px solid #22304A',
      fontFamily: MONO, fontSize: '13px' });
  }
  function pauseCard(on) {
    var d = doc(); if (!d || !d.body) return;
    var el = d.getElementById('pause3d');
    if (!el) {
      el = d.createElement('div'); el.id = 'pause3d'; el.setAttribute('role', 'dialog'); el.setAttribute('aria-label', 'Paused');
      var h = d.createElement('b'), p = d.createElement('div'), go = d.createElement('button'), back = d.createElement('button');
      h.textContent = 'Paused'; p.textContent = 'Esc continues · I returns to the map';
      go.type = 'button'; go.textContent = 'Continue'; back.type = 'button'; back.textContent = 'Return to map';
      go.addEventListener('click', function () { togglePause(); });
      back.addEventListener('click', function () { leave('pause'); if (P && P.left) P.left(''); });
      [h, p, go, back].forEach(function (x) { el.appendChild(x); });
      [go, back].forEach(function (b) {
        var st = { font: '600 18px ' + FONT, color: '#E8EEF6', background: 'transparent', border: '1px solid #6FB7FF', padding: '8px 18px', margin: '14px 6px 0', cursor: 'pointer' };
        Object.keys(st).forEach(function (k) { b.style[k] = st[k]; });
      });
      h.style.font = '600 28px ' + FONT; h.style.display = 'block'; h.style.letterSpacing = '.06em';
      p.style.font = '400 15px ' + FONT; p.style.color = '#9AA8BC'; p.style.marginTop = '6px';
      var css = { position: 'fixed', inset: '0', zIndex: '53', display: 'none', alignItems: 'center', justifyContent: 'center', flexDirection: 'column',
        background: 'rgba(5,7,13,.55)', color: '#E8EEF6', textAlign: 'center', fontFamily: FONT };
      Object.keys(css).forEach(function (k) { el.style[k] = css[k]; });
      d.body.appendChild(el);
    }
    el.style.display = on ? 'flex' : 'none';
  }
  function hideOverlays() {
    var d = doc(); if (!d) return;
    ['note3d', 'perf3d', 'err3d', 'pause3d', 'inset3d', 'hud3d'].forEach(function (id) { var el = d.getElementById(id); if (el) el.style.display = 'none'; });
  }

  // ---------------------------------------------------------------- the renderer (one per session; a lost one is replaced)
  function disposeRenderer() {
    if (R.canvas) R.canvas.removeEventListener('webglcontextlost', onLost, false);   // a loss we cause is not a loss
    if (R.renderer) {
      R.disposing = true;
      try { if (phone() || R.lost) R.renderer.forceContextLoss(); } catch (e) { /* already lost */ }
      try { R.renderer.dispose(); } catch (e) { /* a lost context */ }
      R.disposing = false;
    }
    if (R.canvas && R.canvas.parentNode) R.canvas.parentNode.removeChild(R.canvas);
    R.renderer = null; R.canvas = null; R.lost = false;
  }
  function onLost(e) {
    if (e && e.preventDefault) e.preventDefault();
    if (R.disposing) return;
    R.lost = true; losses++; A.lost = losses;
    if (S && S.mode !== 'off') {
      var why = losses >= 2 ? 'context-lost' : 'lost-once';
      leave('context-lost');
      if (P && P.left) P.left(why);
    }
  }
  function ensureRenderer() {
    // smooth edges by default on a phone too: the phone draws at DPR 1, so on a 3x screen an edge without
    // multisampling steps in 3-pixel stairs; 4x MSAA on a 390x844 buffer is cheap on a phone's tiled GPU (Off in Settings)
    var THREE = g.THREE, aa = setting('antialias', 'on') !== 'off';
    if (R.renderer && !R.lost && R.aa === aa) return R.renderer;
    disposeRenderer();
    var d = doc(), canvas = d.createElement('canvas');
    canvas.id = 'c3d'; canvas.setAttribute('aria-label', 'The map in 3D'); canvas.tabIndex = -1;
    // imageRendering: the page's own canvas rule is 'pixelated' (the 2D map's pixel art); the 3D drawing buffer is the
    // render scale times the screen (the governor goes down to half), so it is stretched smoothly, not in blocks
    var css = { position: 'fixed', left: '0', top: '0', width: '100vw', height: '100vh', zIndex: '49', display: 'none', touchAction: 'none', outline: 'none', background: '#070A12', imageRendering: 'auto' };
    Object.keys(css).forEach(function (k) { canvas.style[k] = css[k]; });
    d.body.appendChild(canvas);
    canvas.addEventListener('webglcontextlost', onLost, false);
    var renderer;
    try {
      renderer = new THREE.WebGLRenderer({ canvas: canvas, antialias: aa, alpha: false, powerPreference: 'high-performance',
        preserveDrawingBuffer: params().probe === '1' });
    } catch (e) {
      if (canvas.parentNode) canvas.parentNode.removeChild(canvas);
      throw e;
    }
    if (!renderer.capabilities || renderer.capabilities.isWebGL2 === false) {
      renderer.dispose(); if (canvas.parentNode) canvas.parentNode.removeChild(canvas);
      throw new Error('no WebGL 2');
    }
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.setClearColor(0x070a12, 1);
    renderer.info.autoReset = false;
    R.renderer = renderer; R.canvas = canvas; R.aa = aa; R.lost = false;
    return renderer;
  }

  // ---------------------------------------------------------------- the world the modules see (the page's, plus debug)
  // {w, key}: key moves with the page's rev and with every debug switch (both only grow), so a module rebuilds on either
  function world() {
    var w = P.world(), key = (Number(w.rev) || 0) + debugRev;
    if (!debug.extra.length && !debug.cleared) return { w: w, key: key };
    var foes = debug.cleared ? [] : (w.foes || []).slice();
    debug.extra.forEach(function (f) { foes.push(f); });
    var out = {}; Object.keys(w).forEach(function (k) { out[k] = w[k]; });
    out.foes = foes; out.rev = key;
    return { w: out, key: key };
  }

  // ---------------------------------------------------------------- stand-ins (only for what no module provides)
  // What shows until the space, craft and camera lanes draw their own, built to the look sheet (neutral
  // "Nightglass"): a region nebula behind 4,000 stars in two magnitudes, a spiral sun with its corona and a three-piece
  // flare, painted planets lit by the sun in five toon bands with a warm band on the terminator, a rim and an
  // atmosphere tinted by health, cloud shells (thick where work runs), night-side lights where agents work, problem
  // craft with engine glow, the owner's dart, and a small HUD (reticle, "N need you", "N of M clear", the target ahead).
  var BASE = { objs: [], planets: {}, clouds: [], foeMesh: null, foeGlow: null, foeList: [], ship: null, stars: null, nebula: null,
    sun: null, sunDisc: null, flare: null };
  var SKY_R = 30000;          // u: the star shell and the nebula around the eye
  // the region skies, neutral (colours: deep, mid, core)
  var SKY = {
    code: ['#123A6B', '#1FB5A8', '#F2C14E'], notes: ['#3B1F5C', '#C0569C', '#E6C8E8'], data: ['#4A2418', '#C77A2E', '#4E4A8C'],
    media: ['#0F5040', '#3FD6A0', '#A04090'], reference: ['#3A2E1C', '#B89A5A', '#E2CC92'], config: ['#10304A', '#4C9CD0', '#CFEFFF'],
    shared: ['#133848', '#3FB0C8', '#9FE4F0'], archive: ['#2A1E12', '#7A5A30', '#B08A58'], unsorted: ['#161A22', '#3A4250', '#6A7484']
  };
  var HEALTH_RIM = { broken: 0xff5a5f, waiting: 0xffb547, ok: 0x8fc8ff, quiet: 0x6b7684 };
  function seeded(seed) { var s = (Number(seed) >>> 0) || 7; return function () { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296; }; }
  function rgbHex(c) { return Array.isArray(c) ? ((c[0] & 255) << 16) | ((c[1] & 255) << 8) | (c[2] & 255) : 0x8a93a6; }
  function hexRgb(h) { var v = parseInt(String(h).replace('#', ''), 16) || 0; return [v >> 16 & 255, v >> 8 & 255, v & 255]; }
  function baseAdd(o) { S.scene.add(o); BASE.objs.push(o); return o; }
  function canvasTexture(THREE, c) { var t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; return t; }
  function glowTexture(THREE, inner, outer) {
    var d = doc(), c = d.createElement('canvas'); c.width = c.height = 128;
    var x = c.getContext('2d'), gr = x.createRadialGradient(64, 64, 0, 64, 64, 64);
    gr.addColorStop(0, inner); gr.addColorStop(0.25, inner); gr.addColorStop(1, outer);
    x.fillStyle = gr; x.fillRect(0, 0, 128, 128);
    return canvasTexture(THREE, c);
  }
  // value noise in three dimensions on a seeded lattice (the nebula bake; the GPU has its own copy in the shaders)
  function noise3(seed) {
    var rnd = seeded(seed), perm = new Uint8Array(512), i;
    for (i = 0; i < 256; i++) perm[i] = i;
    for (i = 255; i > 0; i--) { var j = Math.floor(rnd() * (i + 1)), t = perm[i]; perm[i] = perm[j]; perm[j] = t; }
    for (i = 0; i < 256; i++) perm[i + 256] = perm[i];
    var val = new Float32Array(256); for (i = 0; i < 256; i++) val[i] = rnd();
    function h(x, y, z) { return val[perm[perm[perm[x & 255] + (y & 255)] + (z & 255)]]; }
    function n(x, y, z) {
      var xi = Math.floor(x), yi = Math.floor(y), zi = Math.floor(z), xf = x - xi, yf = y - yi, zf = z - zi;
      var u = xf * xf * (3 - 2 * xf), v = yf * yf * (3 - 2 * yf), w = zf * zf * (3 - 2 * zf);
      var a = h(xi, yi, zi) + (h(xi + 1, yi, zi) - h(xi, yi, zi)) * u, b = h(xi, yi + 1, zi) + (h(xi + 1, yi + 1, zi) - h(xi, yi + 1, zi)) * u;
      var c = h(xi, yi, zi + 1) + (h(xi + 1, yi, zi + 1) - h(xi, yi, zi + 1)) * u, d = h(xi, yi + 1, zi + 1) + (h(xi + 1, yi + 1, zi + 1) - h(xi, yi + 1, zi + 1)) * u;
      var e = a + (b - a) * v, f = c + (d - c) * v;
      return e + (f - e) * w;
    }
    return function fbm(x, y, z, oct) { var s = 0, amp = 0.5, k; for (k = 0; k < oct; k++) { s += amp * n(x, y, z); x = x * 2.03 + 1.7; y = y * 2.03 + 9.2; z = z * 2.03 + 3.1; amp *= 0.5; } return s / (1 - Math.pow(0.5, oct)); };
  }
  function smooth(a, b, x) { var t = Math.max(0, Math.min(1, (x - a) / (b - a))); return t * t * (3 - 2 * t); }
  // one region's nebula as an equirectangular picture (row 0 = straight up, as three's sphere maps it): two cloud layers
  // in the region's deep and mid colours, gathered along a band across the sky, and a few bright cores
  function bakeNebula(THREE, cols, seed, w, h) {
    var c = doc().createElement('canvas'); c.width = w; c.height = h;
    var x = c.getContext('2d'), img = x.createImageData(w, h), px = img.data, fbm = noise3(seed);
    var A = hexRgb(cols[0]), B = hexRgb(cols[1]), C = hexRgb(cols[2]), base = [7, 10, 18], rnd = seeded(seed * 31 + 5);
    var bn = [rnd() - 0.5, 0.9 + rnd() * 0.4, rnd() - 0.5], bl = Math.hypot(bn[0], bn[1], bn[2]);
    bn = [bn[0] / bl, bn[1] / bl, bn[2] / bl];
    for (var j = 0; j < h; j++) {
      var th = (j + 0.5) / h * Math.PI, sy = Math.cos(th), sr = Math.sin(th);
      for (var i = 0; i < w; i++) {
        var ph = (i + 0.5) / w * Math.PI * 2, dx = -Math.cos(ph) * sr, dz = Math.sin(ph) * sr;
        var band = Math.exp(-Math.pow((dx * bn[0] + sy * bn[1] + dz * bn[2]) / 0.45, 2));
        var q = fbm(dx * 1.6, sy * 1.6, dz * 1.6, 4), q2 = fbm(dx * 3.6 + 5.3, sy * 3.6, dz * 3.6, 3);
        var dens = q * (0.5 + 0.7 * band);
        var a = smooth(0.3, 0.7, dens) * 0.9, b = smooth(0.52, 0.82, dens * 0.75 + q2 * 0.35) * 0.62, k = smooth(0.66, 0.92, q2 * (0.4 + 0.8 * band)) * 0.42;
        var o = (j * w + i) * 4;
        for (var ch = 0; ch < 3; ch++) {
          var v = base[ch] + (A[ch] - base[ch]) * a;
          v = v + (B[ch] - v) * b;
          v = v + C[ch] * k;
          px[o + ch] = Math.max(0, Math.min(255, v));
        }
        px[o + 3] = 255;
      }
    }
    x.putImageData(img, 0, 0);
    var t = canvasTexture(THREE, c); t.wrapS = THREE.RepeatWrapping; return t;
  }
  var SKY_VERT = 'varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }';
  var SKY_FRAG = 'uniform sampler2D tA; uniform sampler2D tB; uniform float uMix; uniform vec4 uGrade; varying vec2 vUv;\n' +
    'void main(){ vec3 c = mix(texture2D(tA, vUv).rgb, texture2D(tB, vUv).rgb, uMix); c = mix(c, uGrade.rgb, uGrade.a);\n' +
    '  gl_FragColor = vec4(c, 1.0);\n  #include <colorspace_fragment>\n}';
  // the sky's region: the purpose of the planet nearest the ship; problem-heavy (5 or more problems) grades it red
  function region(w) {
    var sp = P.ship(), best = null, bd = Infinity;
    (w.planets || []).forEach(function (p) { var d = Math.hypot(p.x - sp.x, p.y - sp.y) - p.r; if (d < bd) { bd = d; best = p; } });
    var purpose = best && SKY[best.purpose] ? best.purpose : 'unsorted';
    return { key: purpose, alert: !!(best && best.counts && best.counts.problems >= 5), planned: !!w.selectedWorkflow };
  }
  function skyBuild(w) {
    var THREE = S.THREE, ph = phone(), r = region(w), seed = (Number(params().seed) || 7) * 131 + 17;
    var tex = bakeNebula(THREE, SKY[r.key], seed + r.key.length * 7, ph ? 192 : 384, ph ? 96 : 192);
    var mat = new THREE.ShaderMaterial({ uniforms: { tA: { value: tex }, tB: { value: tex }, uMix: { value: 0 },
      uGrade: { value: new THREE.Vector4(0, 0, 0, 0) } }, vertexShader: SKY_VERT, fragmentShader: SKY_FRAG,
      side: THREE.BackSide, depthWrite: false, depthTest: false });
    var m = new THREE.Mesh(new THREE.SphereGeometry(SKY_R * 1.1, 48, 24), mat);
    m.renderOrder = -20; m.frustumCulled = false; m.userData.noStandIn = true;
    BASE.nebula = baseAdd(m);
    S.sky = { key: r.key, seed: seed, next: S.realT + 1, fade: 1, grade: 0, planned: 0, alertOn: r.alert, plannedOn: r.planned,
      alertColor: new THREE.Color(0x3a0e12), planColor: new THREE.Color(0x0b1a1a) };   // problem-heavy, planned
    A.sky.region = r.key; A.sky.standIn = true;
  }
  // once a second: a new region bakes into the second slot and fades in over 2 s; the alert and planned grades ease
  function skyUpdate(w) {
    if (!BASE.nebula || !S.sky) return;
    var u = BASE.nebula.material.uniforms, sk = S.sky, dt = S.ctx ? S.ctx.dt : 0;
    if (S.realT >= sk.next) {
      sk.next = S.realT + 1;
      var r = region(w);
      if (r.key !== sk.key && sk.fade >= 1) {
        u.tB.value = bakeNebula(S.THREE, SKY[r.key], sk.seed + r.key.length * 7, phone() ? 192 : 384, phone() ? 96 : 192);
        u.uMix.value = 0; sk.fade = 0; sk.key = r.key; A.sky.region = r.key;
      }
      sk.alertOn = r.alert; sk.plannedOn = r.planned;
    }
    if (sk.fade < 1) {
      sk.fade = Math.min(1, sk.fade + dt / 2); u.uMix.value = sk.fade;
      if (sk.fade >= 1) { var old = u.tA.value; u.tA.value = u.tB.value; u.uMix.value = 0; if (old !== u.tA.value) old.dispose(); }
    }
    sk.grade += ((sk.alertOn ? 0.25 : 0) - sk.grade) * Math.min(1, dt / 2);
    sk.planned += ((sk.plannedOn && !sk.alertOn ? 0.15 : 0) - sk.planned) * Math.min(1, dt / 2);
    var g = u.uGrade.value, c = sk.grade >= sk.planned ? sk.alertColor : sk.planColor;
    g.set(c.r, c.g, c.b, Math.max(sk.grade, sk.planned));
    A.sky.alert = sk.grade > 0.02;
  }
  // the sun: a spiral disc (two arms of glowing dust round a white core) facing the eye, a corona six times its radius,
  // and a three-piece flare across the screen when it is in view; both swell with the agents at work
  function spiralTexture(THREE) {
    var d = doc(), c = d.createElement('canvas'), N = 256; c.width = c.height = N;
    var x = c.getContext('2d'), rnd = seeded(41);
    var core = x.createRadialGradient(N / 2, N / 2, 0, N / 2, N / 2, N / 2);
    core.addColorStop(0, 'rgba(255,248,226,1)'); core.addColorStop(0.12, 'rgba(255,232,176,0.95)');
    core.addColorStop(0.3, 'rgba(255,170,90,0.35)'); core.addColorStop(1, 'rgba(255,120,40,0)');
    x.fillStyle = core; x.fillRect(0, 0, N, N);
    x.globalCompositeOperation = 'lighter';
    for (var arm = 0; arm < 2; arm++) {
      for (var k = 0; k < 900; k++) {
        var t = rnd(), ang = arm * Math.PI + t * Math.PI * 3.1 + (rnd() - 0.5) * 0.5, rr = (0.1 + t * 0.85) * N / 2 * (0.92 + rnd() * 0.16);
        var px = N / 2 + Math.cos(ang) * rr, py = N / 2 + Math.sin(ang) * rr, s = 1 + rnd() * 2.6 * (1 - t);
        var hot = 1 - t;
        x.fillStyle = 'rgba(' + Math.round(255) + ',' + Math.round(150 + 90 * hot) + ',' + Math.round(70 + 120 * hot) + ',' + (0.10 + 0.25 * hot).toFixed(3) + ')';
        x.beginPath(); x.arc(px, py, s, 0, Math.PI * 2); x.fill();
      }
    }
    return canvasTexture(THREE, c);
  }
  function flareTexture(THREE, kind) {
    var d = doc(), c = d.createElement('canvas'), N = 128; c.width = c.height = N;
    var x = c.getContext('2d'), g;
    if (kind === 'ring') {
      g = x.createRadialGradient(64, 64, 34, 64, 64, 62);
      g.addColorStop(0, 'rgba(120,200,255,0)'); g.addColorStop(0.55, 'rgba(160,220,255,0.35)'); g.addColorStop(1, 'rgba(120,200,255,0)');
    } else if (kind === 'hex') {
      g = x.createRadialGradient(64, 64, 0, 64, 64, 60);
      g.addColorStop(0, 'rgba(255,200,140,0.5)'); g.addColorStop(1, 'rgba(255,160,90,0)');
    } else {
      g = x.createRadialGradient(64, 64, 0, 64, 64, 64);
      g.addColorStop(0, 'rgba(255,240,210,1)'); g.addColorStop(0.2, 'rgba(255,190,120,0.6)'); g.addColorStop(1, 'rgba(255,154,60,0)');
    }
    x.fillStyle = g;
    if (kind === 'hex') { x.beginPath(); for (var i = 0; i < 6; i++) { var a = i * Math.PI / 3; x.lineTo(64 + Math.cos(a) * 60, 64 + Math.sin(a) * 60); } x.closePath(); x.fill(); }
    else x.fillRect(0, 0, N, N);
    return canvasTexture(THREE, c);
  }
  function buildSun(w) {
    var THREE = S.THREE, ph = phone(), st = w.star || { x: 0, y: 0, r: 150 };
    var sm = new THREE.ShaderMaterial({ uniforms: { uCenter: { value: new THREE.Vector3(st.x, 0, st.y) }, uT: { value: 0 },
      uCore: { value: new THREE.Color(0xfff8e2) }, uMid: { value: new THREE.Color(0xffc46a) }, uEdge: { value: new THREE.Color(0xe0602a) } },
      vertexShader: PLANET_VERT, fragmentShader: SUN_FRAG });
    var sun = new THREE.Mesh(new THREE.SphereGeometry(st.r, ph ? 24 : 48, ph ? 16 : 32), sm);
    sun.position.set(st.x, 0, st.y); sun.userData.uid = 'star'; sun.userData.kind = 'star'; sun.userData.baseColor = 0xffe8b0; sun.userData.mat = sm;
    var disc = new THREE.Sprite(new THREE.SpriteMaterial({ map: spiralTexture(THREE), blending: THREE.AdditiveBlending, depthWrite: false, transparent: true }));
    disc.scale.set(st.r * 3.4, st.r * 3.4, 1); sun.add(disc); BASE.sunDisc = disc;
    var corona = new THREE.Sprite(new THREE.SpriteMaterial({ map: glowTexture(THREE, 'rgba(255,154,60,0.5)', 'rgba(255,154,60,0)'),
      blending: THREE.AdditiveBlending, depthWrite: false, transparent: true }));
    corona.scale.set(st.r * 6, st.r * 6, 1); sun.add(corona);
    if (THREE.Lensflare && THREE.LensflareElement && !ph) {
      /* port: lensflare-vanilla (CC0) */
      var fl = new THREE.Lensflare();
      fl.addElement(new THREE.LensflareElement(flareTexture(THREE, 'glow'), 420, 0, new THREE.Color(0xffe8b0)));
      fl.addElement(new THREE.LensflareElement(flareTexture(THREE, 'ring'), 140, 0.55));
      fl.addElement(new THREE.LensflareElement(flareTexture(THREE, 'hex'), 70, 0.85));
      fl.userData.noStandIn = true; fl.traverse(function (o) { o.userData.noStandIn = true; });
      sun.add(fl); BASE.flare = fl; A.flare = null;
    }
    BASE.sun = baseAdd(sun); A.scene.sun = 1;
  }
  function sunUpdate(w) {
    if (!BASE.sun) return;
    var st = w.star || { r: 150 }, swell = 1 + Math.min(10, Number(st.load) || 0) / 20;
    if (BASE.sunDisc) { BASE.sunDisc.scale.set(st.r * 3.4 * swell, st.r * 3.4 * swell, 1); if (!w.reduced) BASE.sunDisc.material.rotation += (S.ctx ? S.ctx.sceneDt : 0) * 0.06; }
    if (!w.reduced && BASE.sun.userData.mat) BASE.sun.userData.mat.uniforms.uT.value = S.sceneT;
    A.scene.sunSwell = Math.round(swell * 100) / 100;
  }
  // the planets: purpose colour painted in three masses (continents or bands from a seeded noise), five toon bands of
  // sunlight like the 2D bodies, a warm band along the terminator, lights on the night side where agents work, and a rim
  // in the colour of the planet's health
  var NOISE_GLSL = 'float h3(vec3 p){ p = fract(p * 0.3183099 + vec3(0.71, 0.113, 0.419)); p *= 17.0; return fract(p.x * p.y * p.z * (p.x + p.y + p.z)); }\n' +
    'float n3(vec3 x){ vec3 i = floor(x), f = fract(x); f = f * f * (3.0 - 2.0 * f);\n' +
    '  return mix(mix(mix(h3(i), h3(i + vec3(1,0,0)), f.x), mix(h3(i + vec3(0,1,0)), h3(i + vec3(1,1,0)), f.x), f.y),\n' +
    '             mix(mix(h3(i + vec3(0,0,1)), h3(i + vec3(1,0,1)), f.x), mix(h3(i + vec3(0,1,1)), h3(i + vec3(1,1,1)), f.x), f.y), f.z); }\n' +
    'float fbm(vec3 p){ float s = 0.0, a = 0.5; for (int i = 0; i < 4; i++){ s += a * n3(p); p = p * 2.03 + vec3(1.7, 9.2, 3.1); a *= 0.5; } return s / 0.9375; }\n';
  var PLANET_VERT = 'varying vec3 vN; varying vec3 vP; varying vec3 vO;\n' +
    'void main(){ vO = normalize(position); vec4 wp = modelMatrix * vec4(position, 1.0); vP = wp.xyz; vN = normalize(mat3(modelMatrix) * normal);\n' +
    '  gl_Position = projectionMatrix * viewMatrix * wp; }';
  var PLANET_FRAG = 'uniform vec3 uColor; uniform vec3 uDark; uniform vec3 uLight; uniform vec3 uRim; uniform vec3 uSun; uniform float uSeed;\n' +
    'uniform float uBands; uniform float uLights; uniform float uRimK; varying vec3 vN; varying vec3 vP; varying vec3 vO;\n' + NOISE_GLSL +
    'void main(){ vec3 o = normalize(vO) * 2.4 + vec3(uSeed);\n' +
    '  float warp = fbm(o * 0.9);\n' +
    '  float bands = 0.5 + 0.5 * sin((vO.y * 3.2 + warp * 1.4) * 6.2831);\n' +
    '  float cont = fbm(o + vec3(warp));\n' +
    '  float m = mix(cont, bands, uBands);\n' +
    '  vec3 alb = m < 0.44 ? uDark : (m < 0.62 ? uColor : uLight);\n' +
    '  vec3 N = normalize(vN), L = normalize(uSun - vP), V = normalize(cameraPosition - vP);\n' +
    '  float ndl = dot(N, L);\n' +
    '  float band = floor(clamp(ndl, 0.0, 1.0) * 4.0 + 0.6) / 4.0;\n' +
    '  vec3 col = alb * (0.07 + 0.93 * band);\n' +
    '  float term = 1.0 - smoothstep(0.0, 0.2, abs(ndl - 0.06));\n' +
    '  col = mix(col, alb * vec3(1.15, 0.62, 0.34) + vec3(0.10, 0.03, 0.0), term * 0.6);\n' +
    '  float night = smoothstep(0.0, -0.25, ndl);\n' +
    '  float city = step(0.74, n3(normalize(vO) * 46.0 + vec3(uSeed))) * step(0.42, cont);\n' +
    '  col += vec3(1.0, 0.72, 0.32) * city * night * uLights * 0.9;\n' +
    '  float fr = pow(1.0 - max(dot(N, V), 0.0), 3.0);\n' +
    '  col += uRim * fr * uRimK * (0.3 + 0.7 * clamp(ndl + 0.45, 0.0, 1.0));\n' +
    '  gl_FragColor = vec4(col, 1.0);\n  #include <colorspace_fragment>\n}';
  var CLOUD_FRAG = 'uniform vec3 uSun; uniform float uSeed; uniform float uCover; uniform float uT; varying vec3 vN; varying vec3 vP; varying vec3 vO;\n' + NOISE_GLSL +
    'void main(){ vec3 o = normalize(vO) * 3.1 + vec3(uSeed * 1.7, uT * 0.02, 0.0);\n' +
    '  float c = fbm(o + vec3(fbm(o * 0.7)));\n' +
    '  float a = smoothstep(1.0 - uCover, 1.12 - uCover, c);\n' +
    '  vec3 N = normalize(vN), L = normalize(uSun - vP);\n' +
    '  float ndl = dot(N, L), band = floor(clamp(ndl, 0.0, 1.0) * 3.0 + 0.6) / 3.0;\n' +
    '  vec3 col = vec3(0.92, 0.95, 1.0) * (0.06 + 0.94 * band);\n' +
    '  gl_FragColor = vec4(col, a * 0.82 * (0.35 + 0.65 * smoothstep(-0.35, 0.1, ndl)));\n  #include <colorspace_fragment>\n}';
  var ATMO_VERT = 'varying vec3 vN; varying vec3 vP;\n' +
    'void main(){ vec4 wp = modelMatrix * vec4(position, 1.0); vP = wp.xyz; vN = normalize(mat3(modelMatrix) * normal); gl_Position = projectionMatrix * viewMatrix * wp; }';
  var ATMO_FRAG = 'uniform vec3 uRim; uniform vec3 uSun; uniform float uK; varying vec3 vN; varying vec3 vP;\n' +
    'void main(){ vec3 N = normalize(vN), V = normalize(cameraPosition - vP), L = normalize(uSun - vP);\n' +
    '  float edge = pow(clamp(1.0 - abs(dot(N, V)), 0.0, 1.0), 2.4);\n' +
    '  float day = 0.35 + 0.65 * smoothstep(-0.5, 0.3, dot(N, L));\n' +
    '  gl_FragColor = vec4(uRim * edge * uK * day, 1.0);\n  #include <colorspace_fragment>\n}';
  // the sun's face is two log-spiral arms turning slowly round a white core, laid in the plane that faces the eye (so it reads
  // as a spiral disc from straight above on the match frame and from the side in flight), a boiling granulation over it,
  // and the limb darkened to a deep orange
  var SUN_FRAG = 'uniform vec3 uCenter; uniform float uT; uniform vec3 uCore; uniform vec3 uMid; uniform vec3 uEdge;\n' +
    'varying vec3 vN; varying vec3 vP; varying vec3 vO;\n' + NOISE_GLSL +
    'void main(){ vec3 N = normalize(vN), V = normalize(cameraPosition - vP), E = normalize(cameraPosition - uCenter);\n' +
    '  float mu = clamp(dot(N, V), 0.0, 1.0);\n' +
    '  vec3 up = abs(E.y) < 0.9 ? vec3(0.0, 1.0, 0.0) : vec3(1.0, 0.0, 0.0);\n' +
    '  vec3 X = normalize(cross(up, E)), Y = cross(E, X);\n' +
    '  vec2 q = vec2(dot(N, X), dot(N, Y));\n' +
    '  float rho = max(length(q), 0.02), th = atan(q.y, q.x);\n' +
    '  float arms = pow(0.5 + 0.5 * cos(2.0 * (th + 2.4 * log(rho)) - uT * 0.35), 2.0);\n' +
    '  float gran = fbm(normalize(vO) * 7.0 + vec3(0.0, uT * 0.05, 0.0));\n' +
    '  float heat = clamp(1.0 - rho * 0.9 + arms * 0.42 * smoothstep(0.08, 0.5, rho) + (gran - 0.5) * 0.3, 0.0, 1.0);\n' +
    '  vec3 col = mix(uEdge, uMid, smoothstep(0.12, 0.55, heat));\n' +
    '  col = mix(col, uCore, smoothstep(0.6, 0.92, heat));\n' +
    '  col *= 0.62 + 0.38 * pow(mu, 0.5);\n' +
    '  gl_FragColor = vec4(col, 1.0);\n  #include <colorspace_fragment>\n}';
  function linColor(THREE, hex) { return new THREE.Color(hex); }
  function shade(rgb, k, toward) { return rgb.map(function (v, i) { return Math.round(v + ((toward ? toward[i] : 0) - v) * k); }); }
  function buildPlanets(w) {
    var THREE = S.THREE, ph = phone(), pal = w.palette || {}, st = w.star || { x: 0, y: 0 };
    var sunV = new THREE.Vector3(st.x, 0, st.y);
    BASE.planets = {}; BASE.clouds = [];
    (w.planets || []).forEach(function (p) {
      var rgb = pal[p.purpose] || pal.unsorted || [138, 147, 166], colour = rgbHex(rgb);
      var working = (p.conditions || []).indexOf('working') >= 0, health = p.health || 'ok';
      var seed = ((Number(p.seed) >>> 0) % 997) / 37.3;
      var mat = new THREE.ShaderMaterial({
        uniforms: {
          uColor: { value: linColor(THREE, colour) }, uDark: { value: linColor(THREE, rgbHex(shade(rgb, 0.45, [20, 26, 52]))) },
          uLight: { value: linColor(THREE, rgbHex(shade(rgb, 0.32, [255, 248, 230]))) },
          uRim: { value: linColor(THREE, HEALTH_RIM[health] || HEALTH_RIM.ok) }, uRimK: { value: health === 'broken' || health === 'waiting' ? 1.5 : 0.8 },
          uSun: { value: sunV.clone() }, uSeed: { value: seed },
          uBands: { value: p.purpose === 'data' || p.purpose === 'archive' || p.purpose === 'shared' ? 1 : p.purpose === 'notes' ? 0.55 : 0 },
          uLights: { value: working ? 1 : 0 }
        },
        vertexShader: PLANET_VERT, fragmentShader: PLANET_FRAG
      });
      var m = new THREE.Mesh(new THREE.SphereGeometry(p.r, ph ? 24 : 48, ph ? 16 : 32), mat);
      m.position.set(p.x, 0, p.y); m.rotation.z = 0.41; m.userData.uid = p.uid; m.userData.kind = 'planet'; m.userData.baseColor = colour;
      BASE.planets[p.uid] = baseAdd(m);
      if (!ph || working) {                                     // clouds: a thin veil everywhere, thick where work runs
        var cm = new THREE.ShaderMaterial({ uniforms: { uSun: { value: sunV.clone() }, uSeed: { value: seed }, uCover: { value: working ? 0.55 : 0.24 }, uT: { value: 0 } },
          vertexShader: PLANET_VERT, fragmentShader: CLOUD_FRAG, transparent: true, depthWrite: false });
        var cl = new THREE.Mesh(new THREE.SphereGeometry(p.r * 1.03, ph ? 24 : 40, ph ? 16 : 24), cm);
        cl.userData.standIn = 'hide'; cl.userData.mat = cm; m.add(cl);   // mat: the real one, while a stand-in draws
        BASE.clouds.push(cl);
      }
      if (!ph || health === 'broken' || health === 'waiting') {   // the atmosphere glows in the planet's health colour
        var am = new THREE.ShaderMaterial({ uniforms: { uRim: { value: linColor(THREE, HEALTH_RIM[health] || HEALTH_RIM.ok) },
          uSun: { value: sunV.clone() }, uK: { value: health === 'broken' || health === 'waiting' ? 0.9 : 0.45 } },
          vertexShader: ATMO_VERT, fragmentShader: ATMO_FRAG, transparent: true, depthWrite: false, side: THREE.BackSide, blending: THREE.AdditiveBlending });
        var at = new THREE.Mesh(new THREE.SphereGeometry(p.r * 1.12, ph ? 24 : 40, ph ? 16 : 24), am);
        at.userData.standIn = 'hide'; m.add(at);
      }
    });
    A.scene.planets = (w.planets || []).length; A.scene.clouds = BASE.clouds.length;
  }
  function buildStars(w) {
    var THREE = S.THREE, ph = phone();
    var n = 4000, pos = new Float32Array(n * 3), col = new Float32Array(n * 3), rnd = seeded(Number(params().seed) || w.planets.length * 97 + 7);
    var cool = new THREE.Color(0xa9c1ff), warm = new THREE.Color(0xffe3b0);
    for (var i = 0; i < n; i++) {
      var u = rnd() * 2 - 1, a = rnd() * Math.PI * 2, s = Math.sqrt(1 - u * u);
      pos[i * 3] = Math.cos(a) * s * SKY_R; pos[i * 3 + 1] = u * SKY_R; pos[i * 3 + 2] = Math.sin(a) * s * SKY_R;
      var c = (i % 10 === 0) ? warm : cool, k = 0.55 + rnd() * 0.45;
      col[i * 3] = c.r * k; col[i * 3 + 1] = c.g * k; col[i * 3 + 2] = c.b * k;
    }
    var geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(pos, 3)); geo.setAttribute('color', new THREE.BufferAttribute(col, 3));
    var stars = new THREE.Points(geo, new THREE.PointsMaterial({ size: ph ? 1.5 : 2, sizeAttenuation: false, vertexColors: true, depthWrite: false }));
    stars.frustumCulled = false; stars.renderOrder = -10; stars.userData.noStandIn = true;
    // one star in ten is a brighter, bigger one (a second buffer, same directions): magnitude, not a flat noise
    var bright = new THREE.BufferGeometry(), bn = Math.floor(n / 10);
    bright.setAttribute('position', new THREE.BufferAttribute(pos.slice(0, bn * 3), 3));
    bright.setAttribute('color', new THREE.BufferAttribute(col.slice(0, bn * 3).map(function (v) { return Math.min(1, v * 1.25); }), 3));
    var big = new THREE.Points(bright, new THREE.PointsMaterial({ size: ph ? 2.5 : 3.5, sizeAttenuation: false, vertexColors: true, depthWrite: false }));
    big.frustumCulled = false; big.renderOrder = -10; stars.add(big);
    BASE.stars = baseAdd(stars); A.sky.stars = n; A.sky.standIn = true;
  }
  function buildBase(w) {
    var THREE = S.THREE;
    S.scene.background = new THREE.Color(0x070a12);
    if (!isProvided('sky')) { skyBuild(w); buildStars(w); }
    if (!isProvided('sun')) buildSun(w);
    if (!isProvided('planets')) buildPlanets(w);
    if (!isProvided('foes')) buildFoes(w);
    if (!isProvided('ship')) buildShip();
  }
  function buildFoes(w) {
    var THREE = S.THREE, list = (w.foes || []).filter(function (f) { return f && f.parent; });
    BASE.foeList = list;
    A.scene.foes = list.length;
    if (!list.length) { BASE.foeMesh = null; BASE.foeGlow = null; return; }
    // a crimson delta, nose along +X, with a raised spine and two tail fins: 8 faces
    var g3 = new THREE.BufferGeometry(), v = new Float32Array([
      7, 0, 0, -4, 0, 4.5, -2, 1.6, 0, 7, 0, 0, -2, 1.6, 0, -4, 0, -4.5, 7, 0, 0, -4, 0, -4.5, -4, 0, 4.5,
      -4, 0, 4.5, -4, 0, -4.5, -2, 1.6, 0,
      -1, 0.4, 2.2, -4, 0, 3.6, -4.2, 2.4, 3.2, -1, 0.4, -2.2, -4.2, 2.4, -3.2, -4, 0, -3.6,
      -1, 0.4, 2.2, -4.2, 2.4, 3.2, -4, 0, 3.6, -1, 0.4, -2.2, -4, 0, -3.6, -4.2, 2.4, -3.2]);
    g3.setAttribute('position', new THREE.BufferAttribute(v, 3)); g3.computeVertexNormals();
    var mesh = new THREE.InstancedMesh(g3, new THREE.MeshLambertMaterial({ color: 0x8a2a34, emissive: 0x5a1a22, side: THREE.DoubleSide, flatShading: true }), list.length);
    mesh.userData.uids = list.map(function (f) { return f.uid; }); mesh.userData.kind = 'foe'; mesh.userData.baseColor = 0x8a2a34;
    mesh.frustumCulled = false;
    var tell = new THREE.Color(0xff5a5f), late = new THREE.Color(0xffb547);
    list.forEach(function (f, i) { mesh.setColorAt(i, f.kind === 'cron' ? late : tell); });
    BASE.foeMesh = baseAdd(mesh);
    // their engines: one additive point per craft, behind the tail (one draw for all of them)
    var gg = new THREE.BufferGeometry(), gp = new Float32Array(list.length * 3), gc = new Float32Array(list.length * 3);
    list.forEach(function (f, i) { var c = f.kind === 'cron' ? late : tell; gc[i * 3] = c.r; gc[i * 3 + 1] = c.g; gc[i * 3 + 2] = c.b; });
    gg.setAttribute('position', new THREE.BufferAttribute(gp, 3)); gg.setAttribute('color', new THREE.BufferAttribute(gc, 3));
    var glow = new THREE.Points(gg, new THREE.PointsMaterial({ size: 9, map: glowTexture(THREE, 'rgba(255,255,255,1)', 'rgba(255,255,255,0)'), vertexColors: true,
      blending: THREE.AdditiveBlending, transparent: true, depthWrite: false }));
    glow.frustumCulled = false; glow.userData.noStandIn = true;
    BASE.foeGlow = baseAdd(glow);
    placeFoes(w, 0);
  }
  function placeFoes(w, t) {
    var mesh = BASE.foeMesh; if (!mesh) return;
    var THREE = S.THREE, M = new THREE.Matrix4(), Q = new THREE.Quaternion(), V = new THREE.Vector3(), E = new THREE.Euler(), big = new THREE.Vector3(1.6, 1.6, 1.6);
    var tail = new THREE.Vector3(), gp = BASE.foeGlow ? BASE.foeGlow.geometry.attributes.position : null;
    var byUid = {}; (w.planets || []).forEach(function (p) { byUid[p.uid] = p; });
    var count = {}, idx = {};
    BASE.foeList.forEach(function (f) { count[f.parent] = (count[f.parent] || 0) + 1; });
    BASE.foeList.forEach(function (f, i) {
      var p = byUid[f.parent] || w.star || { x: 0, y: 0, r: 100 }, n = count[f.parent], k = idx[f.parent] = (idx[f.parent] || 0) + 1;
      var er = p.r + 26 + (k > 8 ? 18 : 0), a = t * FOE_RATE * (1 + (k % 4) * 0.07) + k * Math.PI * 2 / Math.min(8, n) + (k > 8 ? 0.4 : 0);
      V.set(p.x + Math.cos(a) * er, Math.sin(a * 2 + k) * 6, p.y + Math.sin(a) * er);
      Q.setFromEuler(E.set(0.45, -(a + Math.PI / 2), 0, 'YXZ'));
      M.compose(V, Q, big); mesh.setMatrixAt(i, M);
      if (gp) { tail.set(-5, 0.6, 0).applyMatrix4(M); gp.setXYZ(i, tail.x, tail.y, tail.z); }
    });
    mesh.instanceMatrix.needsUpdate = true;
    if (gp) gp.needsUpdate = true;
  }
  function buildShip() {
    var THREE = S.THREE, grp = new THREE.Group();
    // a small dart, nose along +X, span 14 u: pearl body, ink violet wings, coral stripe, cyan engine glow
    var tri = function (pts, colour, glow) {
      var gg = new THREE.BufferGeometry(); gg.setAttribute('position', new THREE.BufferAttribute(new Float32Array(pts), 3)); gg.computeVertexNormals();
      var m = new THREE.Mesh(gg, new THREE.MeshLambertMaterial({ color: colour, emissive: colour, emissiveIntensity: glow || 0.35, side: THREE.DoubleSide, flatShading: true }));
      m.userData.baseColor = colour; grp.add(m); return m;
    };
    tri([6, 0, 0, -3, 1.4, 0, -3, 0, 1.3, 6, 0, 0, -3, 0, -1.3, -3, 1.4, 0, 6, 0, 0, -3, 0, 1.3, -3, -0.6, 0, 6, 0, 0, -3, -0.6, 0, -3, 0, -1.3], 0xe9edf3);
    tri([-3, 1.4, 0, -3, 0, -1.3, -3, -0.6, 0, -3, 1.4, 0, -3, -0.6, 0, -3, 0, 1.3], 0x2a2f3a, 0.2);     // the engine plate, graphite
    tri([2, 0.1, 0.8, -3.5, 1.1, 7, -2.5, 0.1, 0.8, 2, 0.1, -0.8, -2.5, 0.1, -0.8, -3.5, 1.1, -7], 0xe9edf3);
    tri([0.4, 0.25, 2.2, -3.2, 1.15, 6.4, -2.3, 0.25, 2.2, 0.4, 0.25, -2.2, -2.3, 0.25, -2.2, -3.2, 1.15, -6.4], 0x3b3f8f);
    tri([-3.5, 1.1, 7, -4.1, 1.15, 7.05, -2.5, 0.1, 0.8, -3.5, 1.1, -7, -2.5, 0.1, -0.8, -4.1, 1.15, -7.05], 0xff7a59, 0.6);
    tri([3.6, 0.75, 0, -2.6, 1.35, 0.25, -2.6, 1.35, -0.25], 0xff7a59, 0.6);
    tri([2.6, 0.55, 0.32, 0.4, 1.05, 0, 2.6, 0.55, -0.32], 0x4de8e0, 0.8);                               // the canopy
    var glow = new THREE.Sprite(new THREE.SpriteMaterial({ map: glowTexture(THREE, 'rgba(77,232,224,0.95)', 'rgba(77,232,224,0)'),
      blending: THREE.AdditiveBlending, depthWrite: false, transparent: true }));
    glow.position.set(-4.2, 0.3, 0); glow.scale.set(3, 3, 1); grp.add(glow);
    grp.rotation.order = 'YXZ'; grp.userData.uid = 'ship'; grp.userData.kind = 'ship';
    BASE.ship = baseAdd(grp);
    A.scene.ship = 1;
  }
  function updateBase(w, sceneDt) {
    var sp = P.ship(), cs = (S.ctx && S.ctx.ship) || {}, D = Math.PI / 180;
    if (BASE.ship) {
      var roll = cs.rollDeg != null && isFinite(cs.rollDeg) ? cs.rollDeg * D : (Number(sp.rollDeg) || 0) * D;
      BASE.ship.position.set(sp.x, cs.alt != null && isFinite(cs.alt) ? Number(cs.alt) : S.alt, sp.y);
      BASE.ship.rotation.y = -sp.hdg;
      BASE.ship.rotation.x = ((Number(sp.bank) || 0) + roll) * (w.reduced ? 0 : 1);
      BASE.ship.rotation.z = (Number(cs.pitch) || 0) * D;
      BASE.ship.visible = S.rig !== 'cockpit';
    }
    // the backdrop follows the eye, and grows past the near plane when the first frame looks down from far away
    var grow = Math.max(1, S.camera.near * 1.6 / SKY_R);
    [BASE.stars, BASE.nebula].forEach(function (o) { if (o) { o.position.copy(S.camera.position); o.scale.setScalar(grow); } });
    skyUpdate(w);
    sunUpdate(w);
    if (!w.reduced) {
      Object.keys(BASE.planets).forEach(function (uid) { BASE.planets[uid].rotation.y += sceneDt * 0.05; });
      BASE.clouds.forEach(function (c) { c.rotation.y += sceneDt * 0.03; c.userData.mat.uniforms.uT.value = S.sceneT; });
    }
    placeFoes(w, S.sceneT);
  }
  // a module that disposes its own materials marks them (userData.cmOwner = its name): the core never disposes those, so
  // a leave under the shader warm-up disposes each material once (the module at its dispose, never the warm-up's end too)
  function owned(m) { return !!(m && m.userData && m.userData.cmOwner); }
  function disposeMaterial(m) {
    if (!m || owned(m)) return;
    if (m.map && m.map.dispose) m.map.dispose();
    if (m.uniforms) Object.keys(m.uniforms).forEach(function (k) { var v = m.uniforms[k] && m.uniforms[k].value; if (v && v.isTexture) v.dispose(); });
    if (m.dispose) m.dispose();
  }
  // a mesh still waiting on the shader warm-up wears its stand-in and keeps its real material in userData.cmReal: both go,
  // and the warm-up's end then finds nothing to put back
  function disposeObject(o) {
    o.traverse(function (x) {
      if (x.geometry && x.geometry.dispose) x.geometry.dispose();
      var mats = Array.isArray(x.material) ? x.material : (x.material ? [x.material] : []);
      if (x.userData && x.userData.cmReal) { mats = mats.concat([x.userData.cmReal]); delete x.userData.cmReal; }
      mats.forEach(disposeMaterial);
      if (x.isLensflare && x.dispose) x.dispose();
    });
  }
  function disposeBase() {
    BASE.objs.forEach(function (o) { if (o.parent) o.parent.remove(o); disposeObject(o); });
    BASE.objs = []; BASE.planets = {}; BASE.clouds = []; BASE.foeMesh = null; BASE.foeGlow = null; BASE.foeList = []; BASE.ship = null;
    BASE.stars = null; BASE.nebula = null; BASE.sun = null; BASE.sunDisc = null; BASE.flare = null;
  }

  // ---------------------------------------------------------------- the stand-in HUD (until the HUD lane provides 'hud')
  // DOM over the canvas, styles from JS (the CSP), Barlow Condensed in edge-anchored units of height/240: a
  // reticle (ring and four ticks, 11% of the width) in the empty centre third, "N need you" top right, the gauge
  // "N of M clear" top left (planets with nothing failing or waiting), and the target line beside the reticle when a
  // planet sits under it ("ops · code · 4 problems · 8 s away"). Updated at 5 Hz.
  var SVGNS = 'http://www.w3.org/2000/svg';
  function hudEls() {
    var d = doc(); if (!d || !d.body) return null;
    var root = d.getElementById('hud3d');
    if (root) return root.__cm;
    root = d.createElement('div'); root.id = 'hud3d'; root.setAttribute('aria-hidden', 'true');
    var base = { position: 'fixed', inset: '0', zIndex: '51', pointerEvents: 'none', display: 'none', color: '#E8EEF6', fontFamily: FONT };
    Object.keys(base).forEach(function (k) { root.style[k] = base[k]; });
    var mk = function (tag, css, parent) { var e = d.createElement(tag); Object.keys(css).forEach(function (k) { e.style[k] = css[k]; }); (parent || root).appendChild(e); return e; };
    var svg = d.createElementNS(SVGNS, 'svg'); svg.setAttribute('viewBox', '-50 -50 100 100');
    var ring = d.createElementNS(SVGNS, 'circle'); ring.setAttribute('r', '30'); ring.setAttribute('fill', 'none'); ring.setAttribute('stroke', '#4DE8E0'); ring.setAttribute('stroke-width', '2.2');
    svg.appendChild(ring);
    [[0, -46, 0, -36], [0, 36, 0, 46], [-46, 0, -36, 0], [36, 0, 46, 0]].forEach(function (q) {
      var l = d.createElementNS(SVGNS, 'line'); l.setAttribute('x1', q[0]); l.setAttribute('y1', q[1]); l.setAttribute('x2', q[2]); l.setAttribute('y2', q[3]);
      l.setAttribute('stroke', '#4DE8E0'); l.setAttribute('stroke-width', '3'); svg.appendChild(l);
    });
    var dot = d.createElementNS(SVGNS, 'circle'); dot.setAttribute('r', '1.8'); dot.setAttribute('fill', '#4DE8E0'); svg.appendChild(dot);
    var ret = mk('div', { position: 'absolute', left: '50%', top: '50%', transform: 'translate(-50%,-50%)' }); ret.appendChild(svg);
    var need = mk('div', { position: 'absolute', textAlign: 'right', fontWeight: '600', letterSpacing: '.04em', textShadow: '0 1px 2px #000' });
    var needN = mk('b', { display: 'block', color: '#FFB547', lineHeight: '1' }, need), needW = mk('span', { display: 'block', color: '#C8D6E8', textTransform: 'uppercase', letterSpacing: '.14em' }, need);
    var gauge = mk('div', { position: 'absolute', fontWeight: '600', letterSpacing: '.06em', textShadow: '0 1px 2px #000' });
    var bar = mk('div', { position: 'relative', border: '1px solid #6FB7FF', background: 'rgba(11,18,32,.7)' }, gauge);
    var fill = mk('div', { position: 'absolute', left: '0', top: '0', bottom: '0' }, bar);
    var gtext = mk('div', { color: '#E8EEF6', marginTop: '4px' }, gauge);
    var tgt = mk('div', { position: 'absolute', background: 'rgba(11,18,32,.85)', border: '2px solid #4DE8E0', padding: '3px 8px', fontWeight: '600',
      letterSpacing: '.03em', whiteSpace: 'nowrap', display: 'none' });
    root.__cm = { root: root, ret: ret, svg: svg, need: need, needN: needN, needW: needW, gauge: gauge, bar: bar, fill: fill, gtext: gtext, tgt: tgt, at: -1, session: null };
    d.body.appendChild(root);
    return root.__cm;
  }
  function hudStandIn(w) {
    var H = hudEls(); if (!H) return;
    var show = S.phase === 'fp' && S.mode !== 'paused' && !S.hudHidden && !isProvided('hud');
    H.root.style.display = show ? 'block' : 'none';
    A.hud.standIn = true;
    // the 5 Hz clock is the session's (S.realT starts at 0 on every entry): a stamp from another session never holds it
    if (H.session !== S) { H.session = S; H.at = -1; }
    if (!show || (H.at >= 0 && S.realT - H.at < 0.2)) return;
    H.at = S.realT;
    var sz = size(), u = phone() ? Math.min(sz.w / 320, sz.h / 240) : sz.h / 240, planets = w.planets || [];
    // "N need you" is the page's one count (world.counts.need_you); while that is still the seed's empty stub
    // (total 0: no contact counted) and the planets carry contacts, the planets' own need-you contacts are summed instead
    var counts = w.counts || {}, perPlanet = planets.reduce(function (s, p) { return s + (((p.counts || {}).need_you) || 0); }, 0);
    var needYou = typeof counts.need_you === 'number' && !(!(counts.total > 0) && perPlanet > 0) ? counts.need_you : perPlanet;
    var clear = planets.filter(function (p) { return p.health !== 'broken' && p.health !== 'waiting'; }).length, total = planets.length;
    var rw = Math.round(sz.w * (phone() ? 0.2 : 0.11));
    H.svg.setAttribute('width', rw); H.svg.setAttribute('height', rw);
    H.need.style.right = Math.round(12 * u) + 'px'; H.need.style.top = Math.round(10 * u) + 'px';
    H.needN.style.fontSize = Math.max(18, Math.round(13 * u)) + 'px'; H.needW.style.fontSize = Math.max(12, Math.round(4 * u)) + 'px';
    H.needN.textContent = String(needYou); H.needW.textContent = 'waiting for you';
    H.gauge.style.left = Math.round(12 * u) + 'px'; H.gauge.style.top = Math.round(10 * u) + 'px';
    H.bar.style.width = Math.round(64 * u) + 'px'; H.bar.style.height = Math.max(6, Math.round(4 * u)) + 'px';
    var share = total ? clear / total : 1;
    H.fill.style.width = Math.round(share * 100) + '%';
    H.fill.style.background = share < 0.5 ? '#FF5A5F' : share < 0.85 ? '#FFB547' : '#4ADE80';
    H.gtext.style.fontSize = Math.max(12, Math.round(4.5 * u)) + 'px'; H.gtext.textContent = (total - clear) ? (total - clear) + ' folder' + (total - clear > 1 ? 's need' : ' needs') + ' you' : 'all ' + total + ' folders: nothing to do';
    // the target: the nearest planet whose disc covers the screen's centre (or comes within the reticle)
    var best = null, bd = Infinity, sp = P.ship(), cx = sz.w / 2, cy = sz.h / 2;
    planets.forEach(function (p) {
      var s = toScreen(new S.THREE.Vector3(p.x, 0, p.y)); if (!s.onScreen) return;
      var r = sphereRadiusPx(new S.THREE.Vector3(p.x, 0, p.y), p.r), off = Math.hypot(s.x - cx, s.y - cy);
      if (off > Math.max(r, rw / 2)) return;
      var dist = Math.hypot(p.x - sp.x, p.y - sp.y) - p.r;
      if (dist < bd) { bd = dist; best = { p: p, s: s, r: r }; }
    });
    H.tgt.style.fontSize = Math.max(12, Math.round(5 * u)) + 'px';
    if (best) {
      var c = best.p.counts || {}, probs = c.problems || 0, spd = Math.max(1, Number(sp.spd) || 200);
      var state = probs ? probs + ' problem' + (probs > 1 ? 's' : '') : (c.need_you || c.decisions) ? 'waiting for you' : 'nothing to do';
      var kind = best.p.purpose || 'folder', words = [best.p.label];
      if (String(kind).toLowerCase() !== String(best.p.label).toLowerCase()) words.push(kind);   // "notes · notes" says it once
      var eta = Math.max(0, Math.round(bd / spd));
      H.tgt.textContent = words.concat([state, eta <= 0 ? 'you are here' : eta <= 3 ? 'close by' : 'about ' + eta + ' seconds away']).join(' · ');
      H.tgt.style.borderColor = probs ? '#FF5A5F' : (c.need_you || c.decisions) ? '#FFB547' : '#4DE8E0';
      // beside the reticle, upper right; where that would run off the screen (a phone), over the reticle, centred (the
      // ship sits under it); it wraps rather than being cut off where even that is too narrow
      var pad = Math.round(8 * Math.max(1, u / 3));
      H.tgt.style.maxWidth = (sz.w - 2 * pad) + 'px'; H.tgt.style.whiteSpace = 'nowrap'; H.tgt.style.display = 'block';
      var tw = H.tgt.offsetWidth || 0, left = Math.round(cx + rw / 2 + 0.02 * sz.w), top = Math.round(cy - rw / 2 - 0.03 * sz.h);
      if (left + tw > sz.w - pad) {
        if (tw > sz.w - 2 * pad) { H.tgt.style.whiteSpace = 'normal'; tw = H.tgt.offsetWidth || tw; }
        left = Math.round(Math.max(pad, cx - tw / 2)); top = Math.round(cy - rw / 2 - 0.02 * sz.h - (H.tgt.offsetHeight || 0));
      }
      H.tgt.style.left = left + 'px'; H.tgt.style.top = top + 'px';
    } else H.tgt.style.display = 'none';
    A.hud.needYou = needYou; A.hud.gauge = (total - clear) ? (total - clear) + ' folder' + (total - clear > 1 ? 's need' : ' needs') + ' you' : 'all ' + total + ' folders: nothing to do'; A.hud.target = best ? { uid: best.p.uid, text: H.tgt.textContent } : null;
  }

  // ---------------------------------------------------------------- shader warm-up with flat stand-ins (T3)
  /* port: nova-lancer (MIT) */
  // Shader compilation races a deadline; unfinished meshes draw a flat material in their base colour.
  var READY = typeof WeakSet !== 'undefined' ? new WeakSet() : null;
  function standIn(THREE, real, o) {
    var m;
    if (o.userData.standIn === 'hide') m = new THREE.MeshBasicMaterial({ visible: false });   // an overlay waits unseen
    else {
      var c = o.userData.baseColor != null ? o.userData.baseColor
        : (real.color && real.color.isColor ? real.color.getHex() : (real.uniforms && real.uniforms.uColor && real.uniforms.uColor.value && real.uniforms.uColor.value.isColor ? real.uniforms.uColor.value.getHex() : 0x808080));
      m = new THREE.MeshBasicMaterial({ color: c, transparent: !!real.transparent, opacity: real.opacity != null ? real.opacity : 1,
        side: real.side, depthWrite: real.depthWrite !== false, vertexColors: !!real.vertexColors });
    }
    m.userData.cmStandIn = true;
    // a module that disposes its mesh under the warm-up disposes the stand-in it wears: that mesh is gone
    m.addEventListener('dispose', function () { m.userData.cmGone = true; });
    return m;
  }
  // a session is over once it has left (S stays, mode 'off') or another entry has begun (a new S)
  function over(session) { return !S || S !== session || S.mode === 'off'; }
  // three's compileAsync (WebGLRenderer r180: compile(), then every 10 ms properties.get(m).currentProgram.isReady()) with
  // two guards: a material disposed under it (a leave, a module rebuild) has no program and counts as done, where three's
  // own loop throws inside its timer and never settles; and the loop ends with the session. CM.compileStall (never
  // ready) and CM.compileFail (throws) are the test switches for the race's other two ends.
  function compileWait(session, tmp) {
    var r = R.renderer, f = flags();
    if (f.compileFail) return Promise.reject(new Error('compile failed (CM.compileFail)'));
    var set = r.compile(tmp, session.camera, session.scene);
    return new Promise(function (resolve) {
      var ext = r.extensions, parallel = !!(ext && (ext.has ? ext.has('KHR_parallel_shader_compile') : ext.get('KHR_parallel_shader_compile')));
      function poll() {
        if (over(session) || !R.renderer) { resolve('gone'); return; }
        if (!flags().compileStall) set.forEach(function (m) {
          var pr = r.properties.get(m).currentProgram;
          if (!pr || pr.isReady()) set.delete(m);
        });
        if (set.size) setTimeout(poll, 10); else resolve('ready');
      }
      if (parallel) poll(); else setTimeout(poll, 10);
    });
  }
  function compilePass() {
    if (!S || !READY) return;
    var THREE = S.THREE, list = [], mats = [];
    S.scene.traverse(function (o) {
      if (!o.isMesh || o.isSkinnedMesh || !o.material || Array.isArray(o.material) || o.userData.noStandIn) return;
      var m = o.material;
      if (m.isMeshBasicMaterial || m.userData.cmStandIn || READY.has(m)) return;
      list.push(o); if (mats.indexOf(m) < 0) mats.push(m);
    });
    if (!list.length) return;
    var session = S, C = A.compile, t0 = nowS() * 1000, tmp = new THREE.Group();
    C.pending += mats.length; C.placeholders += list.length;
    list.forEach(function (o) {
      var real = o.material; o.userData.cmReal = real; o.material = standIn(THREE, real, o);
      if (!real.userData.cmWatch) { real.userData.cmWatch = true; real.addEventListener('dispose', function () { real.userData.cmGone = true; }); }
      o.updateWorldMatrix(true, false);
      var c = o.isInstancedMesh ? new THREE.InstancedMesh(o.geometry, real, Math.max(1, o.count)) : new THREE.Mesh(o.geometry, real);
      c.matrixAutoUpdate = false; c.matrix.copy(o.matrixWorld); c.matrixWorld.copy(o.matrixWorld); tmp.add(c);
    });
    // how it ends: 'ready' (compiled: every material whose program links goes back; one that failed keeps its flat stand-in,
    // which at least draws), 'timeout' (2.5 s passed: the real materials go back and finish compiling on first use, the
    // spec's race), 'failed' (the compile threw: nothing is known to work, so every stand-in stays). A mesh that went away
    // meanwhile (disposed with the view or by its module, or the session over) gets nothing back: its real material is
    // disposed here unless a mesh still in use shares it.
    var done = false;
    var finish = function (how) {
      if (done) return; done = true;
      var kept = 0, bad = [], live = [], dead = [];
      if (how === 'ready') mats.forEach(function (m) { if (!runnable(m)) bad.push(m); });
      list.forEach(function (o) {
        var real = o.userData.cmReal;
        if (!real) return;                         // disposeObject already let both materials go
        if (o.material === real) { delete o.userData.cmReal; live.push(real); return; }   // its module put it back itself
        var sm = o.material && o.material.userData.cmStandIn ? o.material : null;
        if (over(session) || !sm || sm.userData.cmGone) {
          delete o.userData.cmReal;
          if (sm && !sm.userData.cmGone) sm.dispose();
          dead.push(real); return;
        }
        live.push(real);
        if (how === 'failed' || bad.indexOf(real) >= 0) { kept++; return; }
        sm.dispose(); o.material = real;
        delete o.userData.cmReal;
      });
      dead.forEach(function (m, i) { if (live.indexOf(m) < 0 && dead.indexOf(m) === i && !owned(m) && !m.userData.cmGone) disposeMaterial(m); });
      mats.forEach(function (m) { if (how !== 'failed' && bad.indexOf(m) < 0) READY.add(m); });
      if (S !== session) return;
      C.pending = Math.max(0, C.pending - mats.length); C.placeholders = Math.max(0, C.placeholders - (list.length - kept));
      C.failed = (C.failed || 0) + (how === 'failed' ? mats.length : bad.length);
      C.ms = Math.round(nowS() * 1000 - t0); C.last = how;
      if (kept) fault('compile', 'compile', new Error(kept + ' part' + (kept > 1 ? 's' : '') + ' of the scene did not compile; '
        + (kept > 1 ? 'they draw' : 'it draws') + ' flat'));
    };
    var wait = flags().slowCompile ? 2000 : 0;
    var later = function (how) { return function () { setTimeout(function () { finish(how); }, wait); }; };
    var compiling;
    try { compiling = compileWait(session, tmp); } catch (e) { compiling = Promise.reject(e); }
    compiling.then(later('ready'), later('failed'));
    // the race's end: RACE_S after it began, held while this session's entry timeline still runs (match, then the
    // provider's dive, orbit and cockpit rise), at most RACE_CAP
    // what ended it is recorded, not inferred from the clock (a late timer is not the timeline): held = it waited for the
    // timeline at least once; until = 'cap' at RACE_CAP, 'timeline' when it was held, else 'race'
    var held = false;
    var raceEnd = function () {
      if (done) return;
      var el = nowS() * 1000 - t0;
      if (S === session && S.mode !== 'off' && (S.phase === 'match' || S.phase === 'enter') && el < RACE_CAP * 1000) { held = true; setTimeout(raceEnd, 50); return; }
      if (S === session) C.window = { ms: Math.round(el), until: el >= RACE_CAP * 1000 ? 'cap' : held ? 'timeline' : 'race', held: held };
      later('timeout')();
    };
    setTimeout(raceEnd, RACE_S * 1000);
  }
  // a material's program linked (three keeps the link result in the program's diagnostics once it is first used)
  function runnable(m) {
    try {
      var pr = R.renderer.properties.get(m).currentProgram;
      if (!pr) return true;
      if (pr.getUniforms) pr.getUniforms();
      return !(pr.diagnostics && pr.diagnostics.runnable === false);
    } catch (e) { return false; }
  }

  // ---------------------------------------------------------------- modules: build, rebuild, update, dispose (guarded)
  function buildModules(w) {
    order.forEach(function (n) {
      var m = mods[n]; S.built[n] = true;
      if (m.build) guard(n, 'build', function () { m.build(S.scene, w, S.ctx); });
    });
  }
  function buildLate(w) {
    var any = false;
    order.forEach(function (n) {
      if (S.built[n] !== false) return;
      var m = mods[n]; S.built[n] = true; any = true;
      if (m.build) guard(n, 'build', function () { m.build(S.scene, w, S.ctx); });
    });
    if (any) compilePass();
  }
  function rebuild(w) {
    order.forEach(function (n) {
      var m = mods[n];
      if (m.rebuild) guard(n, 'rebuild', function () { m.rebuild(S.scene, w, S.ctx); });
      else {
        if (m.dispose) guard(n, 'dispose', function () { m.dispose(); });
        if (m.build) guard(n, 'build', function () { m.build(S.scene, w, S.ctx); });
      }
    });
    disposeBase(); buildBase(w);
    compilePass();
  }
  function disposeModules() {
    order.forEach(function (n) { var m = mods[n]; if (m.dispose) guard(n, 'dispose', function () { m.dispose(); }); });
  }

  // ---------------------------------------------------------------- camera: the match frame, the chase and cockpit poses
  function size() {
    var w = Math.max(1, g.innerWidth || 1), h = Math.max(1, g.innerHeight || 1);
    return { w: w, h: h };
  }
  // the 2D frame of this moment as a camera: straight down over the 2D camera's centre at the height where one 2D px
  // is one screen px (h = (stage height / 2 / ZOOM) / tan(fov / 2)), the stage's centre on the screen
  function matchPose() {
    var v = P.view(), sz = size(), cam = S.camera;
    cam.fov = MATCH_FOV;
    var t = Math.tan(cam.fov * Math.PI / 360), h = sz.h / (2 * Math.max(0.01, v.zoom) * t);
    var sx = v.left + (v.vw - v.panel) / 2, sy = v.top + (v.vh - v.sheet) / 2;
    cam.up.set(0, 0, -1); cam.position.set(v.cx, h, v.cy); cam.lookAt(v.cx, 0, v.cy);
    // everything drawn lies near the plane (the highest is a ship over the biggest body): the near plane at half the
    // height keeps the depth buffer fine at this distance
    cam.far = h * 2 + 90000; cam.near = Math.max(1, h * 0.5);
    cam.setViewOffset(sz.w, sz.h, sz.w / 2 - sx, sz.h / 2 - sy, sz.w, sz.h);
    cam.updateProjectionMatrix();
    S.match = { x: v.cx, y: v.cy, h: h, fov: cam.fov, offset: { x: sz.w / 2 - sx, y: sz.h / 2 - sy } };
  }
  function freePose() {
    var cam = S.camera;
    if (cam.view && cam.view.enabled) cam.clearViewOffset();
    cam.fov = FOV; cam.near = 1; cam.far = 90000; cam.up.set(0, 1, 0); cam.updateProjectionMatrix();
  }
  // the 2D track is the truth and runs over the bodies; in depth the ship rises over each one it crosses, so it never flies
  // through a body: it eases toward a smooth hill (a parabola 1.6 x wider than the body) and never sinks below the body's
  // own sphere plus CLEAR u. Cosmetic: px, py and FL never change.
  var CLEAR = 14;
  function clearance(w, x, y) {
    var want = 0, floor = 0, bodies = (w && w.planets ? w.planets : []).concat(w && w.star ? [w.star] : []);
    for (var i = 0; i < bodies.length; i++) {
      var b = bodies[i], R0 = (b.r || 0) + CLEAR, Rw = R0 * 1.6, d = Math.hypot(x - b.x, y - b.y);
      if (d < Rw) want = Math.max(want, R0 * (1 - d * d / (Rw * Rw)));
      if (d < R0) floor = Math.max(floor, Math.sqrt(R0 * R0 - d * d));
    }
    return { want: want, floor: floor };
  }
  function settleAlt(dt, snap) {
    var sp = P.ship(), c = clearance(S.world, sp.x, sp.y);
    S.alt = snap ? c.want : S.alt + (c.want - S.alt) * (1 - Math.exp(-Math.max(0, dt) * 6));
    if (S.alt < c.floor) S.alt = c.floor;
  }
  function shipPose(dt, snap) {
    var THREE = S.THREE, sp = P.ship(), f = new THREE.Vector3(Math.cos(sp.hdg), 0, Math.sin(sp.hdg)), ship = new THREE.Vector3(sp.x, S.alt, sp.y);
    var cam = S.camera;
    if (S.rig === 'cockpit') {
      cam.position.copy(ship); cam.up.set(0, 1, 0); cam.lookAt(ship.clone().addScaledVector(f, 100));
      S.offset = null; return;
    }
    var want = f.clone().multiplyScalar(-CHASE.back); want.y += CHASE.up;
    if (!S.offset || snap) S.offset = want.clone();
    else S.offset.lerp(want, 1 - Math.exp(-Math.max(0, dt) * CHASE.ease));
    cam.position.copy(ship).add(S.offset); cam.up.set(0, 1, 0);
    cam.lookAt(ship.clone().addScaledVector(f, CHASE.ahead));
  }
  function cut(rig) {
    freePose(); S.rig = rig || S.rig || 'chase'; S.offset = null;
    settleAlt(0, true); shipPose(0, true);
  }
  function camAudit() {
    if (isProvided('camera')) return;
    var c = S.camera, e = new S.THREE.Euler().setFromQuaternion(c.quaternion, 'YXZ');
    A.camera.rig = S.rig; A.camera.fov = c.fov;
    A.camera.pos = [Math.round(c.position.x * 10) / 10, Math.round(c.position.y * 10) / 10, Math.round(c.position.z * 10) / 10];
    A.camera.yaw = Math.round(e.y * 1800 / Math.PI) / 10; A.camera.pitch = Math.round(e.x * 1800 / Math.PI) / 10; A.camera.roll = Math.round(e.z * 1800 / Math.PI) / 10;
  }

  // ---------------------------------------------------------------- projection, sizes on screen, parity with the 2D frame
  function toScreen(v3) {
    var sz = size(), p = v3.clone().project(S.camera), cam = S.camera;
    var dir = new S.THREE.Vector3(); cam.getWorldDirection(dir);
    var behind = v3.clone().sub(cam.position).dot(dir) <= 0;
    return { x: (p.x + 1) / 2 * sz.w, y: (1 - p.y) / 2 * sz.h, behind: behind, onScreen: !behind && Math.abs(p.x) <= 1 && Math.abs(p.y) <= 1 };
  }
  // a sphere's size on the screen: the half-width of its outline across the line from the screen's axis (the ellipse's
  // minor semi-axis). A sphere off the axis draws as an ellipse stretched along that line; across it, its half-width is
  // f sin(a) / sqrt(cos^2(t) - sin^2(a)) (a: the sphere's angular radius, t: its angle off the axis, f: the focal length
  // in px), which for every planet on the plane under the matched camera is the same as its 2D radius
  function sphereRadiusPx(c, r) {
    var cam = S.camera, sz = size(), toC = c.clone().sub(cam.position), d = toC.length();
    if (d <= r) return sz.h;
    var dir = new S.THREE.Vector3(); cam.getWorldDirection(dir);
    var cosT = toC.dot(dir) / d;
    if (cosT <= 0) return 0;
    var sinA = r / d, q = cosT * cosT - sinA * sinA, f = (sz.h / 2) / Math.tan(cam.fov * Math.PI / 360) * (cam.zoom || 1);
    if (q <= 1e-9) return sz.h;                      // the outline is no longer closed on the screen
    return f * sinA / Math.sqrt(q);
  }
  function measure(w) {
    var sz = size(), rp = {}, ws = {};
    (w.planets || []).forEach(function (p) {
      var px = sphereRadiusPx(new S.THREE.Vector3(p.x, 0, p.y), p.r);
      rp[p.uid] = Math.round(px * 10) / 10; ws[p.uid] = Math.round(2 * px / sz.w * 1000) / 1000;
    });
    A.radiusPx = rp; A.widthShare = ws;
  }
  function lum(px) { return px ? 0.2126 * px[0] + 0.7152 * px[1] + 0.0722 * px[2] : 0; }
  // on the match frame: each planet on the stage, its 2D radius (r x zoom) against its 3D one, its colour at three points
  // against the 2D canvas, and whether its sun-facing half is the brighter one in both (the 2D bodies are lit
  // from the sun, each crescent facing it, so the lit test samples 0.42 r toward the sun and 0.42 r away from it)
  function parity(w) {
    var v = P.view(), maxPx = 0, deltas = [], lit2 = 0, lit3 = 0, n = 0, st = w.star || { x: 0, y: 0 };
    (w.planets || []).forEach(function (p) {
      var c = new S.THREE.Vector3(p.x, 0, p.y), s = toScreen(c), r2 = p.r * v.zoom, r3 = sphereRadiusPx(c, p.r);
      if (!s.onScreen || s.x - r2 < v.left || s.y - r2 < v.top || s.x + r2 > v.left + v.vw - v.panel || s.y + r2 > v.top + v.vh - v.sheet) return;
      maxPx = Math.max(maxPx, Math.abs(r3 - r2));
      if (r2 < 6) return;
      var sa = Math.atan2(st.y - p.y, st.x - p.x), ux = Math.cos(sa), uy = Math.sin(sa);
      var pts = [[0, 0], [-0.42, -0.42], [0.42, 0.42], [0.42 * ux, 0.42 * uy], [-0.42 * ux, -0.42 * uy]].map(function (o) { return [s.x + o[0] * r2, s.y + o[1] * r2]; });
      var a = pts.map(function (q) { return P.pixel2D(q[0], q[1]); }), b = pts.map(function (q) { return readPixel(q[0], q[1]); });
      if (a.some(function (x) { return !x; }) || b.some(function (x) { return !x; })) return;
      n++;
      a.slice(0, 3).forEach(function (x, i) { deltas.push((Math.abs(x[0] - b[i][0]) + Math.abs(x[1] - b[i][1]) + Math.abs(x[2] - b[i][2])) / 3); });
      if (lum(a[3]) > lum(a[4])) lit2++;
      if (lum(b[3]) > lum(b[4])) lit3++;
    });
    A.parity = { maxPx: Math.round(maxPx * 100) / 100, meanDelta: deltas.length ? Math.round(deltas.reduce(function (x, y) { return x + y; }, 0) / deltas.length * 10) / 10 : null,
      lightOk: n > 0 && lit2 * 2 > n && lit3 * 2 > n, planets: n, at: 'match' };
  }

  // ---------------------------------------------------------------- the frame
  function pixelRatio() {
    var base = phone() ? 1 : Math.min(g.devicePixelRatio || 1, 2), rs = setting('renderScale', 'auto');
    if (S.demo) return base;
    if (rs !== 'auto' && Number(rs) >= 25 && Number(rs) <= 100) return base * Number(rs) / 100;
    return base * S.gov.ratio;
  }
  function fitRenderer() {
    var sz = size(), pr = pixelRatio(), r = R.renderer;
    if (S.size.w !== sz.w || S.size.h !== sz.h || Math.abs(S.size.pr - pr) > 1e-6) {
      r.setPixelRatio(pr); r.setSize(sz.w, sz.h, true);
      S.size = { w: sz.w, h: sz.h, pr: pr };
      S.camera.aspect = sz.w / sz.h;
      if (S.camera.view && S.camera.view.enabled) S.camera.setViewOffset(sz.w, sz.h, S.camera.view.offsetX, S.camera.view.offsetY, sz.w, sz.h);
      S.camera.updateProjectionMatrix();
    }
  }
  function readPixel(x, y) {
    var r = R.renderer; if (!r) return null;
    var gl = r.getContext(), pr = r.getPixelRatio(), bw = gl.drawingBufferWidth, bh = gl.drawingBufferHeight;
    var bx = Math.floor(x * pr), by = Math.floor(y * pr);
    if (bx < 0 || by < 0 || bx >= bw || by >= bh) return null;
    var out = new Uint8Array(4);
    r.setRenderTarget(null);
    gl.readPixels(bx, bh - 1 - by, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, out);
    return [out[0], out[1], out[2], out[3]];
  }
  // ---------------------------------------------------------------- "Retro 3D" (Settings; off by default)
  /* port: psx-shader (MIT) */
  // lferreira457/threejs-psx-shader, PSXPipeline.js:73-83 (a low-resolution target, nearest-neighbour upscale) and
  // DitheringEffect.js:45-50 (a 4x4 ordered dither before quantising to 32 levels a channel), with Kodrin's and
  // Math-Man's credits in NOTICE: 's look, now the owner's choice instead of the classic theme's default. The
  // scene (and the cockpit pass, which draws into the same target) renders at RETRO_H lines; one full-screen triangle
  // brings it up. No vertex snap, so the DOM labels stay on their bodies; the HUD (DOM) is untouched.
  var RETRO_H = 240;
  var RETRO_FRAG = ['uniform sampler2D uTex; uniform vec2 uSize; varying vec2 vUv;',
    'float bayer(vec2 p){ int x = int(mod(p.x, 4.0)), y = int(mod(p.y, 4.0)), i = y * 4 + x;',
    '  int b[16]; b[0]=0; b[1]=8; b[2]=2; b[3]=10; b[4]=12; b[5]=4; b[6]=14; b[7]=6; b[8]=3; b[9]=11; b[10]=1; b[11]=9; b[12]=15; b[13]=7; b[14]=13; b[15]=5;',
    '  for (int k = 0; k < 16; k++) if (k == i) return (float(b[k]) + 0.5) / 16.0; return 0.5; }',
    'void main(){ vec2 cell = floor(vUv * uSize); vec3 c = texture2D(uTex, (cell + 0.5) / uSize).rgb;',
    '  vec3 d = pow(max(c, 0.0), vec3(1.0 / 2.2));',
    '  d = floor(d * 31.0 + bayer(cell)) / 31.0;',
    '  gl_FragColor = vec4(pow(clamp(d, 0.0, 1.0), vec3(2.2)), 1.0);',
    '  #include <colorspace_fragment>', '}'].join('\n');
  function retroOn() { return !S.demo && S.phase !== 'match' && setting('retro3d', 'off') === 'on'; }   // the first frame stays the 2D one
  function retroTarget() {
    var THREE = S.THREE, sz = size(), h = RETRO_H, w = Math.max(1, Math.round(h * sz.w / sz.h)), T = S.retro;
    if (!T) {
      var rt = new THREE.WebGLRenderTarget(w, h, { minFilter: THREE.NearestFilter, magFilter: THREE.NearestFilter, depthBuffer: true });
      rt.texture.colorSpace = THREE.SRGBColorSpace; rt.texture.generateMipmaps = false;
      var geo = new THREE.BufferGeometry();
      geo.setAttribute('position', new THREE.BufferAttribute(new Float32Array([-1, -1, 0, 3, -1, 0, -1, 3, 0]), 3));
      geo.setAttribute('uv', new THREE.BufferAttribute(new Float32Array([0, 0, 2, 0, 0, 2]), 2));
      var mat = new THREE.ShaderMaterial({ uniforms: { uTex: { value: rt.texture }, uSize: { value: new THREE.Vector2(w, h) } },
        vertexShader: 'varying vec2 vUv; void main(){ vUv = uv; gl_Position = vec4(position.xy, 0.0, 1.0); }', fragmentShader: RETRO_FRAG,
        depthTest: false, depthWrite: false });
      var quad = new THREE.Mesh(geo, mat); quad.frustumCulled = false;
      var sc = new THREE.Scene(); sc.add(quad);
      T = S.retro = { rt: rt, geo: geo, mat: mat, scene: sc, cam: new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1), w: w, h: h };
    }
    if (T.w !== w || T.h !== h) { T.rt.setSize(w, h); T.mat.uniforms.uSize.value.set(w, h); T.w = w; T.h = h; }
    return T;
  }
  function retroDispose() {
    var T = S && S.retro; if (!T) return;
    T.rt.dispose(); T.geo.dispose(); T.mat.dispose(); S.retro = null;
  }
  function draw() {
    var r = R.renderer;
    fitRenderer();
    r.info.reset();
    r.setScissorTest(false);
    var retro = retroOn() ? retroTarget() : (S.retro ? retroDispose() : null);
    r.setRenderTarget(retro ? retro.rt : null);
    r.render(S.scene, S.camera);
    if (retro) { r.setRenderTarget(null); r.render(retro.scene, retro.cam); }
    A.post = retro ? { retro: true, look: 'classic', rtHeight: RETRO_H, rtWidth: retro.w, dither: 4, levels: 32 } : { retro: false, look: 'clean', rtHeight: null };
    var inset = S.inset;                            // asked for during this frame's tick (a redraw between frames keeps it)
    if (inset && inset.camera && inset.rect) {
      var q = inset.rect, sz = size(), y = sz.h - q.y - q.h;
      r.setScissorTest(true); r.setScissor(q.x, y, q.w, q.h); r.setViewport(q.x, y, q.w, q.h);
      var isc = (inset.opts && inset.opts.scene) || S.scene, keepBg = isc.background, own = inset.opts && inset.opts.clear != null;
      if (own) { isc.background = null; r.setClearColor(inset.opts.clear, 1); }          // the inset's own backdrop colour
      guard('inset', 'draw', function () { r.render(isc, inset.camera); });
      if (own) { isc.background = keepBg; r.setClearColor(0x070a12, 1); }
      r.setScissorTest(false); r.setViewport(0, 0, sz.w, sz.h);
      if (inset.opts && inset.opts.border) {
        var d = doc(), bx = d && (d.getElementById('inset3d') || overlay('inset3d', {}));
        if (bx) { bx.style.left = q.x + 'px'; bx.style.top = q.y + 'px'; bx.style.width = q.w + 'px'; bx.style.height = q.h + 'px';
          bx.style.border = '1px solid ' + inset.opts.border; bx.style.boxSizing = 'border-box'; bx.style.display = 'block'; }
        S.insetBorder = true;
      }
      A.inset = { x: q.x, y: q.y, w: q.w, h: q.h };
    } else A.inset = null;
    if (S.insetBorder && !(inset && inset.opts && inset.opts.border)) { var ib = doc() && doc().getElementById('inset3d'); if (ib) ib.style.display = 'none'; S.insetBorder = false; }
    var gl = r.getContext();
    A.calls = r.info.render.calls; A.triangles = r.info.render.triangles; A.textures = r.info.memory.textures;
    A.dpr = Math.round(r.getPixelRatio() * 1000) / 1000; A.scale = Math.round(S.gov.ratio * 10000) / 10000;
    A.buffer = { w: gl.drawingBufferWidth, h: gl.drawingBufferHeight };
    if (S.phase === 'match' && S.world) parity(S.world);
  }
  function setMode(m) { S.mode = m; A.mode = m; if (S.ctx) S.ctx.mode = m; }
  function r2(v) { return Math.round((Number(v) || 0) * 100) / 100; }
  function deg(r) { return Math.round((Number(r) || 0) * 1800 / Math.PI) / 10; }
  // the owner's ship as the audit reports it: the 2D truth (x, y, heading in degrees 0-360 as __fleetAudit says it,
  // speed, bank) and the cosmetic attitude (altitude, pitch, roll angle: the camera lane's ctx.ship, else the core's)
  function shipAudit(sp) {
    var cs = (S.ctx && S.ctx.ship) || {};
    var alt = cs.alt != null && isFinite(cs.alt) ? Number(cs.alt) : S.alt;
    var roll = cs.rollDeg != null && isFinite(cs.rollDeg) ? Number(cs.rollDeg) : (Number(sp.rollDeg) || 0);
    A.ship = { x: r2(sp.x), y: r2(sp.y), alt: r2(alt), hdg: (deg(sp.hdg) + 360) % 360, spd: r2(sp.spd), bank: deg(sp.bank),
      pitch: r2(cs.pitch || 0), rollDeg: r2(roll), uturn: sp.uturnU != null ? r2(sp.uturnU) : null, boost: !!sp.boost, brake: !!sp.brake };
  }
  // ---- the hand-off of the entry and the exit (CM3D.transition)
  function handoffStart(phase) {
    S.phase = phase; S.handoff = { since: S.realT, last: S.realT, name: '' };
    A.handoff = { phase: phase, name: '', by: 'module', timedOut: false };
  }
  function finishEntry(rig) {
    S.phase = 'fp'; S.handoff = null; A.handoff.phase = null;
    if (!isProvided('camera')) cut(rig || 'chase'); else settleAlt(0, true);
    if (S.mode === 'entering') setMode('fp');
    if (!captionShown && !isProvided('transition')) { captionShown = true; S.captionUntil = S.realT + 2.4; }
  }
  function transitionDone(rig) {
    if (!S || S.mode === 'off') return false;
    if (S.phase === 'enter') { finishEntry(rig); return true; }
    if (S.phase === 'exit') { var why = S.leaveWhy; finishLeave(why); if (P && P.left) P.left(''); return true; }
    return false;
  }
  var TRANSITION = {
    phase: function () { return S && S.mode !== 'off' ? S.phase : null; },
    step: function (name) {
      if (!S || !S.handoff) return false;
      S.handoff.last = S.realT; S.handoff.name = String(name || '').slice(0, 24); A.handoff.name = S.handoff.name; return true;
    },
    done: function (rig) { return transitionDone(rig ? String(rig) : null); },
    match: function () { return S && S.match ? JSON.parse(JSON.stringify(S.match)) : null; }
  };
  // one step of everything but the drawing: dt is real seconds (0: no time passes); stepped: __3dStep, which also flies.
  // The ship holds still on the first frame, through an entry or exit timeline and while paused (the matched 2D frame
  // must not drift under it).
  function tick(dt, stepped) {
    var demo = S.phase === 'demo', w = null, ctx = S.ctx;
    S.inset = null;                                 // ctx.renderInset asks again every frame it wants one
    if (!demo && P.fly) P.fly(dt, stepped, S.phase !== 'fp' || S.mode === 'paused');
    if (!demo) {
      var got = world(); w = got.w;
      S.world = w; ctx.world = w;
      if (got.key !== S.rev) { S.rev = got.key; rebuild(w); }
      buildLate(w);
      A.focus = w.focus != null ? w.focus : null; A.skin = String(w.skin || ''); A.look = String(w.look || '');
    }
    // Input follows real time; world time follows the pause, slow and replay controls.
    var running = S.mode !== 'paused' && !S.worldPaused;
    var pageRate = P.sceneScale ? P.sceneScale() : 1;
    var scale = running ? pageRate / (S.slow ? 4 : 1) : 0;
    var sdt = dt * scale;
    S.realT = S.realT + dt;
    S.sceneT = S.sceneT + sdt;
    Object.assign(ctx, {dt: dt, sceneDt: sdt, sceneT: S.sceneT, t: S.realT, rig: S.rig, lock: A.lock});
    A.clock = {scale: scale, t: Math.round(1000 * S.sceneT) / 1000};
    if (demo) {
      if (S.demoDef && S.demoDef.update) guard('demo', 'update', function () { S.demoDef.update(sdt, ctx); });
      return;
    }
    // the first frame is the 2D frame; it holds (hold=match), or after MATCH_HOLD it cuts to the ship, or hands the screen
    // to the transition provider's timeline
    if (S.phase === 'match') {
      if (P.draw2D) P.draw2D();
      matchPose();
      if (params().hold !== 'match' && S.realT >= MATCH_HOLD) {
        if (isProvided('transition')) { handoffStart('enter'); settleAlt(0, true); }
        else finishEntry('chase');
      }
    } else if (S.phase === 'enter' || S.phase === 'exit') settleAlt(dt, false);
    else { settleAlt(dt, false); if (!isProvided('camera')) shipPose(dt, false); }
    if (w.ship && w.ship.flight3d) { ctx.ship.alt = w.ship.alt; ctx.ship.pitch = w.ship.pitch; }
    updateBase(w, sdt);
    order.forEach(function (n) { var m = mods[n]; if (m.update) guard(n, 'update', function () { m.update(w, sdt, ctx); }); });
    // a provider that stopped driving its timeline (or never did) is finished here: the view never sticks half way
    if (S.handoff && S.mode !== 'paused' && S.realT - S.handoff.last >= HANDOFF_WAIT) {
      A.handoff.timedOut = true; A.handoff.by = 'core';
      transitionDone(null);
      if (!S || S.mode === 'off') return;
    }
    if (!isProvided('transition')) { A.transition.phase = S.phase; A.transition.t = Math.round(S.realT * 1000) / 1000; }
    if (!isProvided('hud')) hudStandIn(w);
    camAudit();
    measure(w);
    // the look-back window of the two clocks: how far the world turned and whether the ship moved, over the last 0.4 s
    var sp = P.ship(), rt = nowS();
    shipAudit(sp);
    S.samples.push({ rt: rt, st: S.sceneT, x: sp.x, y: sp.y });
    while (S.samples.length > 2 && S.samples[1].rt <= rt - SAMPLE_WINDOW) S.samples.shift();
    var o = S.samples[0];
    A.foeAngleDelta = Math.round((S.sceneT - o.st) * FOE_RATE * 1e6) / 1e6;
    A.shipMoved = Math.hypot(sp.x - o.x, sp.y - o.y) > 0.5;
    var msg = S.mode === 'paused' ? '' : S.worldPaused ? SENT.worldPaused : S.slow ? SENT.worldSlow : (S.captionUntil > S.realT ? SENT.caption : '');
    if (msg !== S.noteText) { S.noteText = msg; note(msg); }
  }
  function frame(t, dt) {
    if (!S || S.mode === 'off') return;
    var real = held ? 0 : Math.max(0, Math.min(0.1, Number(dt) || 0));
    tick(real, false);
    if (!S || S.mode === 'off') return;
    var cap = setting('fpsCap', phone() ? '30' : 'display'), ms = Number(t) || nowS() * 1000;
    if (cap === '30' || cap === '60') {
      if (S.lastDraw && ms - S.lastDraw < 1000 / Number(cap) - 2) return;
    }
    var gap = S.lastDraw ? (ms - S.lastDraw) / 1000 : 0;
    S.lastDraw = ms;
    if (gap > 0 && !S.demo && !held) {
      govFeed(S.gov, gap, cap);
      S.fps = S.fps ? S.fps * 0.9 + (1 / gap) * 0.1 : 1 / gap; A.fps = Math.round(S.fps * 10) / 10;
    }
    try { draw(); } catch (e) { fault('core', 'draw', e); }
    if (S.perf && !S.hudHidden) {
      var el = perfBox();
      if (el) { el.style.display = 'block'; el.textContent = 'fps ' + Math.round(S.fps || 0) + ' · calls ' + A.calls + ' · DPR ' + A.dpr.toFixed(2) + ' · lost ' + losses; }
    }
  }

  // ---------------------------------------------------------------- enter, leave, pause
  function makeCtx() {
    var ctx = {
      THREE: S.THREE, renderer: R.renderer, scene: S.scene, camera: S.camera, lights: S.lights, skin3d: CM3D.skin3d || {},
      quality: { phone: phone(), aa: R.aa, tier: phone() ? 'low' : 'high' },
      page: { haptic: function (k) { try { if (P && P.haptic) P.haptic(k); } catch (e) { /* no haptics */ } }, settings: P.settings || null,
        flightInput: function (input) { if (P && P.flightInput) P.flightInput(input); },
        flightState: function () { return P && P.flightState ? P.flightState() : null; },
        // the planets' paint: the 2D art drawn this frame, the 2D turn, a surface sampler for the equirect bake
        art: function (uid) { try { return P && P.planetArt ? P.planetArt(uid) : null; } catch (e) { return null; } },
        phase: function (uid) { try { return P && P.planetPhase ? P.planetPhase(uid) : null; } catch (e) { return null; } },
        surface: function (uid, detail) { try { return P && P.planetSurface ? P.planetSurface(uid, detail) : null; } catch (e) { return null; } },
        // the 2D canvas's last frame and the world rectangle it shows {canvas, x, y, w, h} (the camera lane's 2D layer)
        openThread: function (thread) { if (P && P.openThread) P.openThread(thread); },
        map2D: function () { try { return P && P.map2D ? P.map2D() : null; } catch (e) { return null; } } },
      sky: null, assets: {}, hover: null, world: null, dt: 0, sceneDt: 0, sceneT: 0, t: 0, mode: S.mode, rig: S.rig, lock: null,
      reduced: !!flags().reduced, debug: debug, hudHidden: false, transition: TRANSITION, ship: { alt: null, pitch: 0, rollDeg: null }, glareK: 1,
      renderInset: function (camera, rect, opts) { S.inset = { camera: camera, rect: rect, opts: opts || {} }; },
      project: function (x, y, z) { return toScreen(new S.THREE.Vector3(x, y, z)); },
      toWorld: function (x2d, y2d, alt) { return new S.THREE.Vector3(x2d, alt || 0, y2d); }
    };
    Object.defineProperty(ctx, 'audit', { get: function () { return auditFor(CURRENT); }, enumerable: true });
    return ctx;
  }
  function demoPose(angle) {
    var cam = S.camera, d = 100;
    var at = { front34: [0.7, 0.35, 0.7], side: [1, 0, 0], top: [0, 1, 0.0001], rear: [-1, 0.2, 0] }[angle] || [0.7, 0.35, 0.7];
    cam.up.set(0, angle === 'top' ? 0 : 1, angle === 'top' ? -1 : 0);
    cam.position.set(at[0] * d, at[1] * d, at[2] * d); cam.lookAt(0, 0, 0);
  }
  function enter(page, opts) {
    opts = opts || {};
    if (S && S.mode !== 'off') return { ok: true };
    var THREE = g.THREE;
    if (!THREE || !THREE.WebGLRenderer) return { ok: false, reason: 'bundle-error', error: new Error('three.js is not loaded') };
    P = page;
    try { ensureRenderer(); } catch (e) { return { ok: false, reason: 'no-webgl2', error: e }; }
    resetAudit();
    var pr = params(), demo = pr.demo3d ? String(pr.demo3d) : '';
    S = { THREE: THREE, mode: 'entering', scene: new THREE.Scene(), camera: new THREE.PerspectiveCamera(FOV, 1, 1, 90000), built: {},
      rev: null, world: null, sceneT: 0, realT: 0, gov: govNew({ floor: phone() ? 0.6 : 0.5 }), size: { w: 0, h: 0, pr: 0 }, samples: [],
      rig: 'chase', phase: demo ? 'demo' : 'match', alt: 0, offset: null, worldPaused: false, slow: false, lastDraw: 0, fps: 0,
      perf: pr.perf3d === '1', demo: demo, demoDef: null, inset: null, noteText: '', captionUntil: 0, hudHidden: false,
      handoff: null, match: null, leaveWhy: '' };
    started = true;
    try {
      var sun = new THREE.PointLight(0xfff1d6, 2.6, 0, 0), amb = new THREE.AmbientLight(0x9fb4ff, 0.07);
      var sky = new THREE.HemisphereLight(0xbcd0ff, 0x10131c, 0.35);
      S.lights = { sun: sun, ambient: amb, sky: sky }; S.scene.add(sun); S.scene.add(amb); S.scene.add(sky);
      S.ctx = makeCtx();
      A.three = String(THREE.REVISION); A.renderer = R.renderer.capabilities.isWebGL2 === false ? 'webgl' : 'webgl2'; A.demo = demo || null;
      A.antialias = R.aa; A.phone = phone();
      try { A.preserveDrawingBuffer = !!R.renderer.getContext().getContextAttributes().preserveDrawingBuffer; } catch (e) { A.preserveDrawingBuffer = null; }
      R.canvas.style.display = 'block';
      if (demo) {
        S.scene.background = new THREE.Color(0x000000);
        demoPose(pr.angle);
        S.demoDef = demos[demo] || null;
        if (S.demoDef && S.demoDef.build) guard('demo', 'build', function () { S.demoDef.build(S.scene, S.ctx, pr); });
        else if (!S.demoDef) note('No 3D demo is named "' + demo + '"');
        setMode('fp');
      } else {
        var got = world(), w = got.w;
        S.world = w; S.ctx.world = w; S.rev = got.key;
        var st = w.star || { x: 0, y: 0 }; sun.position.set(st.x, 0, st.y);
        launch(w);
        buildBase(w);
        buildModules(w);
        A.retarget = null;
        if (opts.target) retarget(String(opts.target), true); else faceAhead(w);
        matchPose();
      }
      compilePass();
      S.lastDraw = 0;
      tick(0, false);
      draw();
    } catch (e) {
      var err = e; leave('error');
      return { ok: false, reason: 'error', error: err };
    }
    return { ok: true };
  }
  // a user leave (I or M, Back, the pause card) plays the transition provider's exit timeline when there is one; a second
  // leave during it skips to the end; context loss and errors leave at once
  function leave(why) {
    if (!S || S.mode === 'off') return;
    if (S.phase === 'exit') { finishLeave(USER_LEAVE[why] ? S.leaveWhy : why); return; }
    if (isProvided('transition') && USER_LEAVE[why] && S.phase !== 'demo') {
      if (S.mode === 'paused') { if (P && P.pause) P.pause(false); pauseCard(false); }
      S.leaveWhy = why; handoffStart('exit'); setMode('leaving');
      return;
    }
    finishLeave(why);
  }
  function finishLeave(why) {
    if (!S || S.mode === 'off') return;
    S.handoff = null; A.handoff.phase = null;
    setMode('leaving');
    if (S.demoDef && S.demoDef.dispose) guard('demo', 'dispose', function () { S.demoDef.dispose(); });
    disposeModules();
    disposeBase();
    retroDispose();
    S.scene.traverse(function (o) { if (o !== S.scene) disposeObject(o); });
    if (P && P.pause) P.pause(false);
    hideOverlays();
    if (R.canvas) R.canvas.style.display = 'none';
    if (phone() || why === 'context-lost') disposeRenderer();
    setMode('off');
    A.lock = null;
  }
  function togglePause() {
    if (!S || S.mode === 'off' || S.mode === 'leaving') return;
    var on = S.mode !== 'paused';
    setMode(on ? 'paused' : (S.phase === 'fp' ? 'fp' : 'entering'));
    if (!on && S.handoff) S.handoff.last = S.realT;   // the pause is not the provider going quiet
    if (P && P.pause) P.pause(on);
    pauseCard(on);
    note(on ? '' : S.noteText);
  }
  // atEntry: the entry's own retarget (opts.target) locks the target but never turns the ship to look across the sun's glare
  // (faceAhead's rule); in flight the owner's retarget turns as asked
  function retarget(uid, atEntry) {
    if (!S) return;
    A.lock = uid;
    var w = S.world || world().w, p = (w.planets || []).concat(w.moons || []).filter(function (x) { return x.uid === uid; })[0];
    if (p && P.face) {
      var sp = P.ship(), across = !!atEntry && crossesSun(w, sp.x, sp.y, p.x, p.y);
      if (!across) P.face(Math.atan2(p.y - sp.y, p.x - sp.x));
      if (atEntry) A.retarget = { uid: uid, turned: !across, acrossSun: across };
    } else if (atEntry) A.retarget = { uid: uid, turned: false, acrossSun: false };
  }
  // an entry with no target looks at something: unless a planet already lies in the middle of the view ahead (half the
  // chase camera's width, plus the planet's own angular radius) and far enough that the cruise does not pass it before
  // the entry is over (AHEAD_MIN), the ship turns to the nearest planet that far off (else the nearest it is not over);
  // the 2D heading follows, as a retarget's does. A phone in portrait sees only 13 degrees either side.
  var AHEAD_MIN = 700;     // u of open space to a planet's surface: 3.5 s of the 200 u/s cruise
  // the sun's near-field radius (SUN_NEAR r, times the clearance rule's 1.05) and whether the straight line from (x0, y0)
  // to (x1, y1) passes through it: the entry never turns the ship to look across the sun's glare
  function sunNear(w) { var st = w && w.star; return st && st.r > 0 ? st.r * SUN_NEAR * CLEAR_K : 0; }
  function crossesSun(w, x0, y0, x1, y1) {
    var R = sunNear(w); if (!R) return false;
    var st = w.star, vx = x1 - x0, vy = y1 - y0, L2 = vx * vx + vy * vy || 1;
    var t = Math.max(0, Math.min(1, ((st.x - x0) * vx + (st.y - y0) * vy) / L2));
    return Math.hypot(x0 + vx * t - st.x, y0 + vy * t - st.y) < R;
  }
  // a ship resting in the sun's near field (the 2D map keeps the owner's ship by the sun, where the carrier docks) launches
  // before the first frame: the 2D truth moves out along the line from the sun through the carrier (else through the
  // ship, else south) to SUN_NEAR r x 1.05 + LAUNCH_PAD, turned in 15 degree steps until the point has LAUNCH_ROOM u of open
  // space round it and LAUNCH_RUN u of clear flight ahead, heading away from the sun (else the roomiest of the 24); the
  // page's move keeps its 2D camera's centre, so the matched first frame and the exit's end are the 2D view the owner left.
  // The page's flight clamps the ship to the world's bounds (its bounds(): {x0, y0, x1, y1} in u) each frame: a launch point
  // outside them would be pulled back the next frame while the camera's compensation stayed, so out-of-bounds bearings are
  // skipped (a sparse map); none in bounds and the ship stays where it is.
  function launchPick(w, sp, bounds) {
    var st = w.star, R = sunNear(w);
    if (!R) return null;
    var dx = sp.x - st.x, dy = sp.y - st.y, d = Math.hypot(dx, dy), out = R + LAUNCH_PAD;
    if (d >= out) return null;
    var car = (w.craft || []).filter(function (c) { return c && c.role === 'carrier' && isFinite(c.x) && isFinite(c.y); })[0];
    var a0 = car && Math.hypot(car.x - st.x, car.y - st.y) > 1 ? Math.atan2(car.y - st.y, car.x - st.x) : d > 1 ? Math.atan2(dy, dx) : Math.PI / 2;
    var B = bounds && isFinite(bounds.x0) && isFinite(bounds.x1) && isFinite(bounds.y0) && isFinite(bounds.y1) ? bounds : null;
    var inside = function (x, y) { return !B || (x >= B.x0 && x <= B.x1 && y >= B.y0 && y <= B.y1); };
    var pick = null, best = null, skipped = 0;
    for (var k = 0; k < 24 && !pick; k++) {
      var a = a0 + (k % 2 ? 1 : -1) * Math.ceil(k / 2) * Math.PI / 12, x = st.x + Math.cos(a) * out, y = st.y + Math.sin(a) * out;
      if (!inside(x, y)) { skipped++; continue; }
      var x1 = x + Math.cos(a) * LAUNCH_RUN, y1 = y + Math.sin(a) * LAUNCH_RUN, room = Infinity;
      (w.planets || []).forEach(function (p) {
        if (!(p.r > 0)) return;
        var vx = x1 - x, vy = y1 - y, t = Math.max(0, Math.min(1, ((p.x - x) * vx + (p.y - y) * vy) / (vx * vx + vy * vy)));
        room = Math.min(room, Math.hypot(p.x - x, p.y - y) - p.r * CLEAR_K - LAUNCH_ROOM, Math.hypot(x + vx * t - p.x, y + vy * t - p.y) - p.r * CLEAR_K - 40);
      });
      if (room >= 0) pick = { x: x, y: y, a: a };
      else if (!best || room > best.room) best = { x: x, y: y, a: a, room: room };
    }
    if (!pick) pick = best;
    if (!pick) return { none: true, skipped: skipped, sunNear: R };
    pick.skipped = skipped; pick.sunNear = R;
    return pick;
  }
  function launch(w) {
    A.launch = null;
    if (!P.move) return;
    var sp = P.ship(), bounds = null;
    try { bounds = typeof P.bounds === 'function' ? P.bounds() : null; } catch (e) { bounds = null; }
    var pick = launchPick(w, sp, bounds);
    if (!pick) return;
    if (pick.none) { A.launch = { from: [r2(sp.x), r2(sp.y)], to: null, hdg: null, sunNear: r2(pick.sunNear), outOfBounds: pick.skipped }; return; }
    P.move(pick.x, pick.y, pick.a);
    A.launch = { from: [r2(sp.x), r2(sp.y)], to: [r2(pick.x), r2(pick.y)], hdg: (deg(pick.a) + 360) % 360, sunNear: r2(pick.sunNear), outOfBounds: pick.skipped };
  }
  function faceAhead(w) {
    A.faced = null;
    if (!P.face) return;
    var sp = P.ship(), sz = size(), half = Math.atan(Math.tan(FOV * Math.PI / 360) * sz.w / sz.h) * 0.5;
    var far = null, near = null, fd = Infinity, nd = Infinity, ahead = false;
    (w.planets || []).forEach(function (p) {
      var dx = p.x - sp.x, dy = p.y - sp.y, d = Math.hypot(dx, dy), gap = d - p.r;
      if (!(p.r > 0) || d < p.r + CLEAR + 40) return;
      if (crossesSun(w, sp.x, sp.y, p.x, p.y)) return;          // never a first view across the sun's glare
      var a = Math.atan2(dy, dx) - sp.hdg, off = Math.abs(Math.atan2(Math.sin(a), Math.cos(a)));
      if (gap >= AHEAD_MIN && off <= half + Math.asin(Math.min(1, p.r / d))) ahead = true;
      if (gap >= AHEAD_MIN && gap < fd) { fd = gap; far = p; }
      if (gap < nd) { nd = gap; near = p; }
    });
    // never a turn toward a planet the cruise would reach before the entry is over: then the heading holds
    var to = ahead ? null : (far || (near && nd >= AHEAD_MIN / 2 ? near : null));
    if (to) { P.face(Math.atan2(to.y - sp.y, to.x - sp.x)); A.faced = to.uid; }
  }
  function approach(uid) {
    if (!S || S.mode === 'off') return null;
    var w = S.world || world().w, p = (w.planets || []).concat(w.moons || []).filter(function (x) { return x.uid === uid; })[0];
    if (!p) return null;
    var r = p.r || 30, sp = P.ship(), dx = p.x - sp.x, dy = p.y - sp.y, n = Math.hypot(dx, dy);
    if (n < 1e-6) { dx = 0; dy = -1; n = 1; }
    dx /= n; dy /= n;
    // the distance is for the lens the cut below opens (freePose's FOV), never the lens of this moment: during the match
    // frame the camera still holds the 4 degree match lens, mid-dive whatever the camera lane has reached
    var sz = size(), hfov = 2 * Math.atan(Math.tan(FOV * Math.PI / 360) * sz.w / sz.h);
    var d = r / (0.34 * Math.tan(hfov / 2));
    P.place(p.x - dx * d, p.y - dy * d, Math.atan2(dy, dx));
    if (S.phase === 'match' || S.phase === 'enter') { S.phase = 'fp'; S.handoff = null; A.handoff.phase = null; }
    if (S.mode === 'entering') setMode('fp');
    cut('cockpit');
    A.lock = uid;
    return { x: p.x - dx * d, y: p.y - dy * d, d: Math.round(d * 10) / 10 };
  }

  // ---------------------------------------------------------------- the test hooks and debug switches (T4, T6)
  function step(n, dt) {
    if (!S || S.mode === 'off') return null;
    n = Math.max(1, Math.min(10000, Math.floor(Number(n) || 1))); dt = Math.max(0, Math.min(0.1, Number(dt) || 0));
    for (var i = 0; i < n; i++) tick(dt, true);
    try { draw(); } catch (e) { fault('core', 'draw', e); }
    return audit();
  }
  function pixel(x, y) {
    if (!S || S.mode === 'off' || !R.renderer) return null;
    try { draw(); } catch (e) { fault('core', 'draw', e); return null; }
    return readPixel(Number(x), Number(y));
  }
  function find(uid) {
    var w = S.world || world().w, p = (w.planets || []).concat(w.moons || [], w.craft || [], w.machines || []).filter(function (x) { return x.uid === uid; })[0];
    if (p) return { v: new S.THREE.Vector3(p.x, 0, p.y), r: p.r || 0 };
    if (uid === 'star' && w.star) return { v: new S.THREE.Vector3(w.star.x, 0, w.star.y), r: w.star.r };
    if (uid === 'ship') { var sp = P.ship(), alt = S.ctx.ship.alt == null ? S.alt : S.ctx.ship.alt;
      return { v: new S.THREE.Vector3(sp.x, alt, sp.y), r: 7 }; }
    var hit = null;
    S.scene.traverse(function (o) {
      if (hit) return;
      if (o.userData && o.userData.uid === uid) {
        var r = 0;
        if (o.geometry) { if (!o.geometry.boundingSphere) o.geometry.computeBoundingSphere(); var sc = o.getWorldScale(new S.THREE.Vector3()); r = o.geometry.boundingSphere.radius * Math.max(sc.x, sc.y, sc.z); }
        hit = { v: o.getWorldPosition(new S.THREE.Vector3()), r: r }; return;
      }
      if (o.isInstancedMesh && o.userData.uids) {
        var i = o.userData.uids.indexOf(uid);
        if (i >= 0) { var M = new S.THREE.Matrix4(); o.getMatrixAt(i, M); hit = { v: new S.THREE.Vector3().setFromMatrixPosition(M).applyMatrix4(o.matrixWorld), r: 0 }; }
      }
    });
    return hit;
  }
  function project(uid) {
    if (!S || S.mode === 'off') return null;
    var f = find(String(uid)); if (!f) return null;
    var s = toScreen(f.v); s.x = Math.round(s.x * 10) / 10; s.y = Math.round(s.y * 10) / 10;
    s.r = f.r ? Math.round(sphereRadiusPx(f.v, f.r) * 10) / 10 : 0;
    return s;
  }
  // which side of a body faces the sun, on the screen: two canvas points inside its disc, 0.6 of its radius from the
  // centre toward the sun's side and away from it (the terminator check reads __3dPixel at both), the screen direction
  // to the sun, and whether the sun is behind the eye (then the direction is the sun's, projected through the body)
  function sunSide(uid) {
    if (!S || S.mode === 'off') return null;
    var f = find(String(uid)), w = S.world || world().w, st = w.star;
    if (!f || !st) return null;
    var c = toScreen(f.v), sunV = new S.THREE.Vector3(st.x, 0, st.y), s = toScreen(sunV), r = f.r ? sphereRadiusPx(f.v, f.r) : 0;
    var dx, dy;
    if (!s.behind && Math.hypot(s.x - c.x, s.y - c.y) > 1e-3) { dx = s.x - c.x; dy = s.y - c.y; }
    else { var q = toScreen(f.v.clone().add(sunV.clone().sub(f.v).normalize().multiplyScalar(Math.max(1, f.r || 1)))); dx = q.x - c.x; dy = q.y - c.y; }
    var n = Math.hypot(dx, dy) || 1; dx /= n; dy /= n;
    var k = 0.6 * r, rd = function (v) { return Math.round(v * 10) / 10; };
    return { uid: String(uid), x: rd(c.x), y: rd(c.y), r: rd(r), dir: [Math.round(dx * 1000) / 1000, Math.round(dy * 1000) / 1000],
      lit: [rd(c.x + dx * k), rd(c.y + dy * k)], dark: [rd(c.x - dx * k), rd(c.y - dy * k)], sunBehind: !!s.behind, onScreen: c.onScreen };
  }
  function pick(x, y) {
    if (!S || S.mode === 'off') return null;
    var sz = size(), rc = new S.THREE.Raycaster(), ndc = new S.THREE.Vector2(x / sz.w * 2 - 1, -(y / sz.h) * 2 + 1);
    S.camera.updateMatrixWorld();
    rc.setFromCamera(ndc, S.camera);
    var hits = rc.intersectObjects(S.scene.children, true);
    var shown = function (o) { for (var x = o; x; x = x.parent) if (x.visible === false) return false; return true; };
    for (var i = 0; i < hits.length; i++) {
      var h = hits[i], o = h.object;
      if (o.isPoints || !shown(o)) continue;          // the star field and anything hidden (the own ship in the cockpit)
      if (o.isInstancedMesh && o.userData.uids && h.instanceId != null) return { uid: o.userData.uids[h.instanceId] || null, instanceId: h.instanceId, distance: Math.round(h.distance) };
      while (o && !(o.userData && o.userData.uid)) o = o.parent;
      if (o) return { uid: o.userData.uid, instanceId: h.instanceId != null ? h.instanceId : null, distance: Math.round(h.distance) };
    }
    return { uid: null, instanceId: null };
  }
  function audit() {
    var out;
    try { out = JSON.parse(JSON.stringify(A)); } catch (e) { out = { mode: A.mode, errors: A.errors.slice() }; }
    out.mode = S ? S.mode : 'off';
    mirrorCraft(out);
    return out;
  }
  //.11 lists scene.craft {own, agents, carrier} (and the 3D craft module checks read scene.foeSilhouettes, scene.formation,
  // scene.dropLines, scene.blobShadows) while the craft module may write only craft.* and models.*: the snapshot copies
  // those keys under scene, never over a key the space module wrote itself
  function mirrorCraft(out) {
    var c = out.craft, s = out.scene;
    if (!c || !s || c.own == null) return;
    if (s.craft === undefined) s.craft = { own: c.own, agents: c.agents, carrier: c.carrier };
    ['foeSilhouettes', 'formation', 'dropLines', 'blobShadows'].forEach(function (k) { if (s[k] === undefined && c[k] !== undefined) s[k] = c[k]; });
  }
  var DEBUG = {
    spawnContact: function (uid) {
      debug.spawned++;
      debug.extra.push({ uid: 'debug:' + debug.spawned, parent: String(uid || ''), kind: 'contact', source: 'debug', label: 'Test contact', urgency: 'fresh', days: 0 });
      debug.cleared = false; debugRev++; return debug.extra.length;
    },
    clearContacts: function () { debug.extra = []; debug.cleared = true; debugRev++; return 0; },
    clouds: function (on) { debug.noClouds = on === false; return !debug.noClouds; },   // the planets' clouds off: the bare surface for the pixel checks
    planets: function (real) { debug.paintedPlanets = real === false; return !debug.paintedPlanets; },   // the painted planets instead of a skin's baked ones
    cycleComms: function () { debug.comms++; if (typeof CM3D.onDebug === 'function') guard('onDebug', 'comms', function () { CM3D.onDebug('comms', debug.comms); }); return debug.comms; },
    hideHud: function (on) {
      on = !!on; A.hud.visible = !on;
      if (S) { S.hudHidden = on; if (S.ctx) S.ctx.hudHidden = on; }
      var d = doc(); if (d) ['note3d', 'perf3d', 'hud3d', 'err3d'].forEach(function (id) { var el = d.getElementById(id); if (el && on) el.style.display = 'none'; });
      if (!on && S) { note(S.noteText); showErrors(); }
      return A.hud.visible;
    },
    fps: function (on) { if (S) S.perf = !!on; if (!on) { var el = perfBox(); if (el) el.style.display = 'none'; } return !!on; },
    reset: function () { debug.extra = []; debug.cleared = false; debugRev++; }
  };
  function settingsChanged(key) {
    if (!S || S.mode === 'off') return;
    if (key === 'renderScale') S.size.pr = -1;        // fitRenderer applies it on the next frame
  }

  // ---------------------------------------------------------------- the public face
  var CM3D = g.CM3D = {
    v: 1, core: 1,
    module: module,
    modules: function () { return order.slice(); },
    get: function (name) { return Object.prototype.hasOwnProperty.call(mods, name) ? mods[name] : null; },
    provides: provides, provided: isProvided,
    demo: function (name, def) { if (!NAME.test(String(name))) throw new Error('CM3D.demo: not a demo name: ' + String(name)); demos[name] = def || {}; return def; },
    build: function (scene, w, ctx) { order.forEach(function (n) { var m = mods[n]; if (m.build) guard(n, 'build', function () { m.build(scene, w, ctx); }); }); },
    update: function (w, dt, ctx) { order.forEach(function (n) { var m = mods[n]; if (m.update) guard(n, 'update', function () { m.update(w, dt, ctx); }); }); },
    dispose: function () { order.forEach(function (n) { var m = mods[n]; if (m.dispose) guard(n, 'dispose', function () { m.dispose(); }); }); },
    enter: enter, leave: leave, frame: frame, togglePause: togglePause, retarget: retarget, settingsChanged: settingsChanged,
    transition: TRANSITION,
    owns: function () { return !!S && S.mode !== 'off'; },
    started: function () { return started; },
    paused: function () { return !!S && S.mode === 'paused'; },
    worldPause: function () { if (S && S.mode !== 'off') S.worldPaused = !S.worldPaused; return S ? S.worldPaused : false; },
    worldSlow: function (on) { if (S) S.slow = !!on && S.mode !== 'off'; },
    audit: audit, fault: fault,
    onKey: null, onDebug: null, skin3d: (g.CM3D && g.CM3D.skin3d) || {},
    govNew: govNew, govStep: govStep, govFeed: govFeed,
    // for test_3d_core.py: the audit proxy a module gets, and a guarded call as the frame makes it
    _core: { auditFor: auditFor, guard: guard, A: A, NS: NS, faults: faults, QUARANTINE: QUARANTINE, launchPick: launchPick, crossesSun: crossesSun }
  };
  // carry modules that registered on an older registry (a re-run of the bundle in the same page)
  if (g.__cm3dPrev && g.__cm3dPrev.modules) { /* nothing: the registry above is the only one */ }
  g.__3dStep = function (n, dt) { return step(n, dt); };
  g.__3dPixel = function (x, y) { return pixel(x, y); };
  g.__3dProject = function (uid) { return project(uid); };
  g.__3dSunSide = function (uid) { return sunSide(uid); };
  g.__3dPick = function (x, y) { return pick(Number(x), Number(y)); };
  g.__3dApproach = function (uid) { return approach(String(uid)); };
  g.__3dHold = function (on) { held = !!on; return held; };
  g.__3dDebug = DEBUG;
  // a built-in demo page: one stand-in planet in the sun's light (checks the ?demo3d= mechanism itself)
  CM3D.demo('core', {
    build: function (scene, ctx, pr) {
      var THREE = ctx.THREE, r = 30;
      var m = new THREE.Mesh(new THREE.SphereGeometry(r, 48, 32), new THREE.MeshToonMaterial({ color: 0x4e79c8 }));
      m.userData.uid = 'demo:planet'; scene.add(m);
      ctx.lights.sun.position.set(-400, 300, 200);
      var want = Number(pr.px) || 96, sz = size(), d = r / (want / sz.h * Math.tan(ctx.camera.fov * Math.PI / 360));
      ctx.camera.position.setLength(d); ctx.camera.lookAt(0, 0, 0);
      ctx.audit.ext.demo = 'core';
    }
  });
})(typeof globalThis !== 'undefined' ? globalThis : this);
