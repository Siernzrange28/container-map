// ui/phone.js — the phone and the tablet: Today first screen, the peek sheet, the tabs, fit-all home, Back (CM.backLayer).
// Inlined into the page's main script right after the CM registry (render-game.py, the seams): declare functions and
// assign into CM only, nothing that runs page code at load (start-up goes in CM.onInit). Real backslashes here: this
// file is read as it is, not through a Python string. Neutral words only.
//
// What this module owns (the seams): CM.sheetCard (the phone sheet: a 35% peek, then full), CM.haptic,
// window.__phoneAudit, the Today page's actions (renderToday itself is in render-game.py, one function for the desktop
// and the phone) and the phone's chrome: the top line, the 3D chip, the tab badges, + Task, the walls the focus and labels module lays out around.

// ---- haptics (T1: an idea taken from common mobile patterns, no code) ----
// One short word per event: a tap 10 ms, a step done 30 ms, a failure two hard knocks, "needs you" a soft double. A phone
// only, a visible page only, the Settings row "Vibrate" (on unless the user turned it off), and at most one every 3 s, so a
// burst of steps finishing together is one buzz. the 2D workflows module calls it from the workflow sheet, the flight module through ctx.page.haptic.
const HAPTIC = { last: -1e9, gap: 3000, pat: { tap: 10, done: 30, fail: [60, 40, 60], needs: [20, 60, 20] } };
function hapticOn() { try { return localStorage.getItem('cm.vibrate') !== 'off'; } catch (e) { return true; } }
function hapticSet(on) { try { localStorage.setItem('cm.vibrate', on ? 'on' : 'off'); } catch (e) {} }
CM.haptic = function (kind) {
  if (typeof MOB === 'undefined' || !MOB) return;
  const vs = typeof document !== 'undefined' ? document.visibilityState : undefined;
  if (typeof vs === 'string' && vs !== 'visible') return;   // a page in the background never buzzes
  const p = Object.prototype.hasOwnProperty.call(HAPTIC.pat, kind) ? HAPTIC.pat[kind] : null;
  if (p == null || !hapticOn()) return;
  const now = Date.now(); if (now - HAPTIC.last < HAPTIC.gap) return;
  HAPTIC.last = now;
  try { if (typeof navigator !== 'undefined' && navigator.vibrate) navigator.vibrate(p); } catch (e) {}
};

// ---- the phone sheet (CM.sheetCard): an element shown in the card sheet (#panel), a 35% peek first, the grip pulls it
// full. opts {title, back:'For you'|'Workflows'|'Agents', onBack, full}. sheetCard(null) puts it away. the chrome module's railCard
// delegates here on a phone; on a desktop this hands the element to the rail (railCard) once the chrome module is in,
// else shows it in the card. Back (the phone's back gesture, Esc) is a layer at priority 20 (CM.backLayers).
const PSHEET = { el: null, opts: null, n: 0 };
function sheetIsUp() {
  const P = document.getElementById('panel'), B = document.getElementById('pbody');
  return !!(PSHEET.el && P && B && P.classList.contains('on') && B.contains(PSHEET.el));
}
CM.sheetCard = function (el, opts) {
  PSHEET.n++;
  if (el == null) {
    if (sheetIsUp()) { PSHEET.el = null; PSHEET.opts = null; closeAll(); if (MOB) mobSync(); }
    else { PSHEET.el = null; PSHEET.opts = null; }
    return;
  }
  opts = opts && typeof opts === 'object' ? opts : {};
  if (!MOB && CM.chromeReady && typeof CM.railCard === 'function') { CM.railCard(el, opts); return; }
  const P = document.getElementById('panel'), B = document.getElementById('pbody'); if (!P || !B) return;
  closeAll();
  B.textContent = '';
  if (opts.title || opts.back) {
    const hd = document.createElement('div'); hd.className = 'sch';
    if (opts.back) {
      const bk = document.createElement('button'); bk.type = 'button'; bk.className = 'scb'; bk.textContent = '‹ ' + String(opts.back);
      bk.addEventListener('click', () => { if (typeof opts.onBack === 'function') opts.onBack(); else CM.sheetCard(null); });
      hd.appendChild(bk);
    }
    if (opts.title) { const t = document.createElement('b'); t.textContent = String(opts.title); hd.appendChild(t); }
    B.appendChild(hd);
  }
  B.appendChild(el);
  PSHEET.el = el; PSHEET.opts = opts;
  P.classList.remove('seat'); P.classList.add('on'); P.setAttribute('aria-hidden', 'false');
  document.getElementById('wrap').classList.add('panel-open');
  if (typeof MP === 'object') MP.full = !!opts.full;
  syncPanelW();
  if (MOB) mobSync();
};
function menuUp() { const m = document.getElementById('msheet'); return !!(m && m.classList && m.classList.contains('on')); }
function sheetBack(how) {
  if (how === 'has') return sheetIsUp();
  if ((how === 'back' || how === 'esc') && menuUp()) return false;   // the long-press menu is over the sheet: mobBack closes it first (S2)
  if ((how === 'back' || how === 'esc') && sheetIsUp()) {
    const o = PSHEET.opts || {};
    if (typeof o.onBack === 'function') { try { o.onBack(); } catch (e) {} if (sheetIsUp()) CM.sheetCard(null); }
    else CM.sheetCard(null);
    return true;
  }
  return false;
}

// ---- + Task aims at what is in view: the planet in focus, else the card's folder, else the nearest body ----
function mobAim() {
  const f = CM.focusUid(), fu = f && unitByUid(f);
  if (fu && (fu.kind === 'area' || fu.kind === 'dir')) return fu;
  const P = document.getElementById('panel');
  if (P && P.classList.contains('on') && openUnit && openUnit !== BOARD && (openUnit.kind === 'area' || openUnit.kind === 'dir')) return openUnit;
  return near && near !== BOARD ? near : null;
}

// ---- the phone's chrome, mirrored from the page's state on every mobSync: the top line (the three counts, Silkscreen
// numerals, each a tap), the walls the map lays out around (data-wall: the focus and labels module's homeCovers reads them) ----
function phoneLine() {
  const el = document.getElementById('mline'); if (!el) return;
  const c = CM.counts(), keys = CM.COUNT_NOUNS || ['need_you', 'decisions', 'problems'];
  const parts = keys.filter((k) => Number.isInteger(c[k]) && c[k] > 0).map((k) => '<button type="button" class="mc ' + (k === 'problems' ? 'r' : 'a') +
    '" data-num="' + k + '" data-count="' + k + '"><b>' + c[k] + '</b>' + esc(CM.countNoun(k, c[k], c)) + '</button>');
  const h = parts.length ? parts.join('<i aria-hidden="true">·</i>') : '<span class="mc0">' + esc(CM.word('empty sky')) + '</span>';
  if (el.innerHTML !== h) el.innerHTML = h;
  // the owner's numbers ("13 need you · 53 decisions · 1 problem") must fit beside More and the 3D chip: a step
  // tighter (14 px words), then the tightest (13 px, no dots); measured only when the words or the width change
  const key = h + '|' + innerWidth + '|' + (MOB_LAND ? 1 : 0);
  if (phoneLine.key === key || !el.getClientRects || !el.getClientRects().length) return;
  phoneLine.key = key; el.classList.remove('t1', 't2', 't3');
  const over = () => el.scrollWidth > el.clientWidth + 1;
  if (over()) { el.classList.add('t1'); if (over()) { el.classList.remove('t1'); el.classList.add('t2'); if (over()) { el.classList.remove('t2'); el.classList.add('t3'); } } }   // t3: two lines (1533 need you · 153 decisions · 15 problems)
}
function phoneSync(tab) {
  const home = document.querySelector('#tab-today .tl');
  if (home) home.textContent = (W.today || {}).configured === false ? 'For you' : 'Today';
  phoneLine();
  const P = document.getElementById('panel');
  if (P) { const on = P.classList.contains('on'); if (on !== P.hasAttribute('data-wall')) { if (on) P.setAttribute('data-wall', ''); else P.removeAttribute('data-wall'); } }
}

// ---- a planet's thumbnail, the body the map draws (ui/kinds.js purposeIcon), for Today's rows and the route stops.
// Painted once per planet, look and size (an <img> of a data: URL, which the page's policy allows); '' -> a dot ----
const THUMBS = new Map();
CM.planetThumb = function (uid, size) {
  size = Math.max(12, Math.min(64, Number(size) || 34));
  const dot = '<i class="tth" aria-hidden="true"></i>';
  if (typeof MOONS === 'undefined' || typeof purposeIcon !== 'function' || typeof planetLook !== 'function') return dot;
  const m = MOONS.find((x) => x && x.uid === uid); if (!m) return dot;
  let L = null; try { L = planetLook(m); } catch (e) { L = null; }
  if (!L || !L.purpose) return dot;
  const u = (typeof unitByUid === 'function' && unitByUid(uid)) || m, seed = String(u.path || m.label || uid);
  const key = [uid, size, L.purpose, seed, L.nightLights ? 1 : 0, typeof GB !== 'undefined' && GB ? 1 : 0, typeof THEME_NAME !== 'undefined' ? THEME_NAME : ''].join('|');
  let src = THUMBS.get(key);
  if (!src) {
    try { src = thumbArt(L.purpose, Math.round(size * 2), seed).toDataURL(); } catch (e) { return dot; }
    if (THUMBS.size > 200) THUMBS.clear();
    THUMBS.set(key, src);
  }
  return '<img class="tth" src="' + attr(src) + '" alt="" width="' + size + '" height="' + size + '">';
};

// the body at full detail (one art pixel a device pixel, where the legend's icon draws 40 across), lit from the upper left
function thumbArt(p, S, seed) {
  if (typeof kindsBody !== 'function' || typeof kindsCanvas !== 'function' || typeof PURPOSE !== 'object' || !PURPOSE[p]) return purposeIcon(p, S, { seed: seed });
  const key = ['b5thumb', p, S, seed, typeof GB !== 'undefined' && GB ? 1 : 0, typeof THEME_NAME !== 'undefined' ? THEME_NAME : ''].join('|');
  const make = () => {
    const c = kindsCanvas(S, S), g = c.getContext('2d'), r = S * (p === 'reference' || p === 'shared' ? .26 : p === 'archive' ? .3 : .4), q = 1;
    const L = { purpose: p, seeds: kindsSeeds(seed), pal: planetPal(p), conditions: [], nightLights: false, progress: null, health: 'ok', glyph: PURPOSE[p].glyph };
    g.imageSmoothingEnabled = false; const la = Math.round(KIND_LIGHT.steps * 5 / 8) % KIND_LIGHT.steps;
    kindsBody(g, S / 2, S / 2, r, L, { q: q, n: Math.max(4, Math.round(r)), ph: 7, la: la, t: 0 });
    return c; };
  return typeof artCache === 'function' ? artCache(key, make) : make();
}

// ---- Today's actions (render-game.py's #today listener calls these) ----
function todayMap() { if (MOB) mtab('map'); else setView('map'); }
function todayShow(uid) { const u = unitByUid(uid); todayMap(); if (u) { humanMove(); activate(u); } }
function todayWf(id) { return CM.wfList().find((w) => w && String(w.id) === String(id)) || null; }
function todayFollow(id) {
  const w = todayWf(id); CM.selectWorkflow(id); todayMap();
  const at = w && Array.isArray(w.steps) ? w.steps.find((s) => s && (s.state === 'working' || s.state === 'needs_you')) : null;
  CM.enter3d(at && at.planet_uid ? { target: String(at.planet_uid), workflow: String(id) } : { workflow: String(id) });
}
function todayReplay(id) { CM.selectWorkflow(id); todayMap(); if (typeof CM.replay === 'function') CM.replay(id); }
// a workflow row: the workflow module's own card when it shows one (CM.selectWorkflow -> railCard -> sheetCard), else
// the read-only sheet below with the row's fields (the 2D workflows module fills the real one: "until then show the row's fields")
function todayWorkflow(id) {
  if (!MOB) { setView('map'); CM.selectWorkflow(id); return; }
  const before = PSHEET.n;
  CM.selectWorkflow(id);
  if (PSHEET.n === before) CM.sheetCard(wfSheetEl(id), { back: 'Today' });
}
const WF_WORD = { done: ['g', 'done'], working: ['c', 'working'], needs_you: ['a', 'needs you'], failed: ['r', 'failed'], skipped: ['', 'skipped'], waiting: ['', 'waiting'], ready: ['', 'waiting'] };
function wfSheetEl(id) {
  const w = todayWf(id) || {}, r = CM.wfRows().find((x) => x.id === String(id)) || { title: '', done: 0, total: 0, state: '', place: '', step: 0 };
  const steps = (Array.isArray(w.steps) ? w.steps : []).filter((x) => x && typeof x === 'object');
  const box = document.createElement('div'); box.className = 'wfs';
  const sw = WF_WORD[r.state] || ['', String(r.state || '').replace(/_/g, ' ')];
  const by = w.by && typeof w.by === 'object' && w.by.name ? String(w.by.name) : '';
  let h = '<h2 class="wft">' + esc(CM.shortTitle(r.title || '', 60) || 'a workflow') + '</h2>' +
    '<div class="wfm"><b class="' + sw[0] + '">' + esc(sw[1]) + '</b> · ' + (r.step || r.done) + ' of ' + r.total + (r.place ? ' · ' + esc(r.place) : '') + (by ? ' · by ' + esc(by) : '') + '</div>' +
    '<div class="tdr"><i class="l' + Math.round((r.total ? r.done / r.total : 0) * 20) + '"></i></div>' +
    (r.sentence ? '<div class="tnow">' + esc(CM.plainText(r.sentence, 140)) + '</div>' : '') + '<ol class="wfst">';
  steps.forEach((s) => {
    const ww = WF_WORD[s.state] || ['', String(s.state || '')], pr = s.progress && s.progress.total > 0 ? Math.max(0, Math.min(1, s.progress.done / s.progress.total)) : null;
    const out = [s.output, s.artifact].filter((x) => typeof x === 'string' && x.trim()).map((x) => String(x).split('/').pop());
    h += '<li class="st-' + (s.state || 'waiting') + '">' + CM.planetThumb(String(s.planet_uid || ''), 30) + '<div><b>' + esc(CM.shortTitle(s.label || '', 60)) + '</b>' +
      '<small>' + [s.place ? esc(s.place) : '', '<span class="' + ww[0] + '">' + esc(pr != null ? s.progress.done + ' of ' + s.progress.total : ww[1]) + '</span>'].filter(Boolean).join(' · ') + '</small>' +
      (s.says ? '<div class="wfsay">' + esc(CM.plainText(s.says, 140)) + '</div>' : '') +
      (pr != null ? '<div class="tdr sm"><i class="l' + Math.round(pr * 20) + '"></i></div>' : '') +
      (out.length ? '<div class="wfout">' + out.map(esc).join(' · ') + '</div>' : '') + '</div></li>';
  });
  h += '</ol><div class="wfb"></div><div class="mut">Start, retry, skip and stop are on your computer.</div>';
  box.innerHTML = h;
  const btns = box.querySelector('.wfb'), cur = steps.find((s) => s.state === 'working' || s.state === 'needs_you') || steps[0];
  const button = (label, run) => { const b = document.createElement('button'); b.type = 'button'; b.className = 'tbtn'; b.textContent = label; b.addEventListener('click', run); btns.appendChild(b); };
  if (cur && cur.planet_uid) button('Show on map', () => todayShow(String(cur.planet_uid)));
  if (w.state === 'done' || w.derived) button('Replay', () => todayReplay(id));
  return box;
}

// ---- the map in miniature on the desktop Today: every planet at its place squeezed into the strip, the sun in the middle,
// the ones with something for the user named; painted while Today is up ----
const MINI = { c: null, key: '' };
function todayMini() {
  const c = document.getElementById('tminic'); if (!c || MOB || typeof MOONS === 'undefined' || !MOONS.length || !c.getClientRects().length) return false;
  const r = c.getBoundingClientRect(), dpr = Math.min(2, window.devicePixelRatio || 1), W0 = Math.round(r.width), H0 = Math.round(r.height);
  if (!(W0 > 40 && H0 > 40)) return false;
  if (c.width !== W0 * dpr || c.height !== H0 * dpr) { c.width = W0 * dpr; c.height = H0 * dpr; }
  const sx = LY && LY.star ? LY.star.x : 0, sy = LY && LY.star ? LY.star.y : 0;
  // what the strip shows: the canvas, its size, each planet's place, look and counts; the same again paints nothing (S8)
  const mk = [W0, H0, dpr, sx, sy, typeof KIND_NEB === 'object' && KIND_NEB ? KIND_NEB.state : '', typeof THEME_NAME !== 'undefined' ? THEME_NAME : '', typeof GB !== 'undefined' && GB ? 1 : 0]
    .concat(MOONS.map((m) => { const n = typeof moonCounts === 'function' ? moonCounts(m) : null; return [m.uid, Math.round(m.x), Math.round(m.y), m.r || 0, n ? [n.problems || 0, n.need_you || 0, n.decisions || 0].join(',') : '', CM.labelText(m.uid)].join(':'); })).join('|');
  if (MINI.c === c && MINI.key === mk) return false;
  MINI.c = c; MINI.key = mk;
  const g = c.getContext('2d'); g.setTransform(dpr, 0, 0, dpr, 0, 0);
  let RX = 1, RY = 1; MOONS.forEach((m) => { RX = Math.max(RX, Math.abs(m.x - sx) + (m.r || 0)); RY = Math.max(RY, Math.abs(m.y - sy) + (m.r || 0)); });
  const kx = (W0 / 2 - 34) / RX, ky = (H0 / 2 - 22) / RY;
  g.fillStyle = '#05070d'; g.fillRect(0, 0, W0, H0);
  // this machine's own sky (ui/kinds.js bakes it for the map), brighter here than behind the map, else a plain glow
  if (typeof KIND_NEB === 'object' && KIND_NEB && KIND_NEB.state === 'ready' && KIND_NEB.canvas) {
    g.save(); g.globalAlpha = Math.min(1, (KIND_NEB.alpha || .5) * 1.8); g.drawImage(KIND_NEB.canvas, 0, 0, W0, H0); g.restore(); }
  else { const bg = g.createRadialGradient(W0 / 2, H0 / 2, 4, W0 / 2, H0 / 2, W0 * .6);
    bg.addColorStop(0, '#1b1530'); bg.addColorStop(.45, '#0d1224'); bg.addColorStop(1, '#05070d'); g.fillStyle = bg; g.fillRect(0, 0, W0, H0); }
  const haze = g.createRadialGradient(W0 / 2, H0 / 2, 2, W0 / 2, H0 / 2, W0 * .35);
  haze.addColorStop(0, 'rgba(245,179,66,.16)'); haze.addColorStop(1, 'rgba(245,179,66,0)'); g.fillStyle = haze; g.fillRect(0, 0, W0, H0);
  for (let i = 0; i < 70; i++) { const a = Math.sin(i * 12.9898) * 43758.5453, b = Math.sin(i * 78.233) * 12345.678;
    g.fillStyle = i % 7 ? 'rgba(207,220,255,.55)' : 'rgba(255,255,255,.9)'; g.fillRect((a - Math.floor(a)) * W0, (b - Math.floor(b)) * H0, i % 9 ? 1 : 1.6, i % 9 ? 1 : 1.6); }
  const sun = g.createRadialGradient(W0 / 2, H0 / 2, 1, W0 / 2, H0 / 2, 16);
  sun.addColorStop(0, '#fff7d6'); sun.addColorStop(.35, '#f5b342'); sun.addColorStop(.7, 'rgba(226,112,42,.35)'); sun.addColorStop(1, 'rgba(226,112,42,0)');
  g.fillStyle = sun; g.beginPath(); g.arc(W0 / 2, H0 / 2, 16, 0, Math.PI * 2); g.fill();
  g.font = '600 12px "Barlow Condensed", sans-serif'; g.textAlign = 'center'; g.lineWidth = 3; g.strokeStyle = '#05070d'; g.lineJoin = 'round';
  g.fillStyle = '#f5b342'; g.strokeText(CM.word('this machine'), W0 / 2, H0 / 2 + 24); g.fillText(CM.word('this machine'), W0 / 2, H0 / 2 + 24);
  MOONS.forEach((m) => {
    const x = W0 / 2 + (m.x - sx) * kx, y = H0 / 2 + (m.y - sy) * ky, rr = Math.max(6, Math.min(13, (m.r || 40) * .11));
    let L = null; try { L = planetLook(m); } catch (e) {}
    if (L && typeof purposeIcon === 'function') { try { g.drawImage(purposeIcon(L.purpose, 48, { seed: String((unitByUid(m.uid) || m).path || m.label) }), x - rr * 1.32, y - rr * 1.32, rr * 2.64, rr * 2.64); } catch (e) {} }
    else { g.fillStyle = '#6b7684'; g.beginPath(); g.arc(x, y, rr, 0, Math.PI * 2); g.fill(); }
    const n = typeof moonCounts === 'function' ? moonCounts(m) : null, k = n ? (n.problems || 0) + (n.need_you || 0) + (n.decisions || 0) : 0;
    if (n && (n.problems || n.need_you)) {
      const name = CM.labelText(m.uid), col = n.problems ? '#ff5a5f' : '#ffb547';
      g.strokeStyle = col; g.globalAlpha = .55; g.lineWidth = 1; g.beginPath(); g.arc(x, y, rr + 3, 0, Math.PI * 2); g.stroke(); g.globalAlpha = 1;
      g.lineWidth = 3; g.strokeStyle = '#05070d'; g.fillStyle = '#e8eef6'; g.strokeText(name, x, y + rr + 13); g.fillText(name, x, y + rr + 13);
      const tw = g.measureText(name).width; g.fillStyle = col; g.beginPath(); g.arc(x + tw / 2 + 9, y + rr + 9, 6, 0, Math.PI * 2); g.fill();
      g.fillStyle = '#05070d'; g.font = '600 12px "Barlow Condensed", sans-serif'; g.fillText(String(k), x + tw / 2 + 9, y + rr + 13); g.font = '600 12px "Barlow Condensed", sans-serif';
    }
  });
  return true;
}

// ---- a tablet's zoom column keeps clear of every wall ([data-wall]: a rail, a bar, a chip, a fallback box), whichever
// side it is on. From the corner (right 16, bottom 56) each wall that crosses the column pushes it: left of a wall at the
// screen's right edge or above one at its bottom edge; a floating box pushes it left while that stays on screen, else up.
// Pure: rects {x,y,w,h} in screen px -> {right, bottom} (S10) ----
function zoomAvoid(walls, vw, vh, zw, zh) {
  let right = 16, bottom = 56;
  const cross = (r, z) => r.x < z.x + z.w && r.x + r.w > z.x && r.y < z.y + z.h && r.y + r.h > z.y;
  const box = () => ({ x: vw - right - zw, y: vh - bottom - zh, w: zw, h: zh });
  for (let pass = 0; pass < 4; pass++) {
    const z = box(), hit = walls.filter((r) => r && r.w > 0 && r.h > 0 && cross(r, z));
    if (!hit.length) break;
    hit.forEach((r) => {
      const z2 = box(); if (!cross(r, z2)) return;
      const atRight = r.x + r.w >= vw - 2, atBottom = r.y + r.h >= vh - 2, left = Math.round(vw - r.x + 16), up = Math.round(vh - r.y + 12);
      if (atRight && !atBottom) right = Math.max(right, left);
      else if (atBottom && !atRight) bottom = Math.max(bottom, up);
      else if (left + zw <= vw - 16 && r.x > vw * .4) right = Math.max(right, left);
      else bottom = Math.max(bottom, up);
    });
  }
  return { right: right, bottom: bottom };
}
// the once-a-second upkeep: the desktop Today stops at the status bar and paints its map in miniature (never while the
// page is hidden), a tablet's zoom column keeps inside the map
function phoneTick() {
  if (MOB) return;
  if (typeof document !== 'undefined' && document.hidden) return;   // nothing to paint for no one (S8)
  const t = document.getElementById('today'), sb = document.getElementById('statusbar');
  const h = sb && sb.getClientRects().length ? Math.round(sb.getBoundingClientRect().height) : 0;
  if (t && t.style.bottom !== h + 'px') t.style.bottom = h + 'px';
  if (VIEW === 'today') todayMini();
  const z = document.getElementById('mzoom');
  if (z && document.documentElement.classList.contains('t') && z.getClientRects && z.getClientRects().length) {
    const zr = z.getBoundingClientRect(), walls = [];
    document.querySelectorAll('[data-wall]').forEach((w) => { if (w === z || !w.getClientRects().length) return; const r = w.getBoundingClientRect();
      walls.push({ x: r.left, y: r.top, w: r.width, h: r.height }); });
    const at = zoomAvoid(walls, innerWidth, innerHeight, Math.round(zr.width) || 44, Math.round(zr.height) || 140);
    if (z.style.right !== at.right + 'px') z.style.right = at.right + 'px';
    if (z.style.bottom !== at.bottom + 'px') z.style.bottom = at.bottom + 'px';
  }
}

// ---- __phoneAudit(): what a test reads back about the phone's and the tablet's chrome (the keys of the chrome module's __chromeAudit
// that apply, plus mapShare). Boxes: every shown [data-wall] and the page's fixed pieces, a box inside another counted
// with it. mapFree: the largest rectangle of the stage no box covers. mapShare: the part of the screen where the map is
// seen (the stage minus every box), sampled every 6 px. overlaps: pairs of boxes that cross by more than 2 px each way
// (the long-press sheet and its scrim are a modal over everything and are left out) ----
const PA_IDS = ['hud', 'tabs', 'mfab', 'panel', 'permits', 'side', 'today', 'mate', 'mateshow', 'pickbar', 'mzoom', 'quick', 'ticker', 'touchpad',
  'comms', 'dash', 'theme', 'taskbar', 'topbar', 'radio', 'gohome', 'legend', 'quests', 'prompter', 'mclose'];
function paBoxes() {
  const seen = new Set(), out = [], vw = innerWidth, vh = innerHeight;
  const add = (el) => {
    if (!el || seen.has(el) || el.id === 'msheet' || el.id === 'scrim') return; seen.add(el);
    let r = null, cs = null; try { r = el.getBoundingClientRect(); cs = getComputedStyle(el); } catch (e) { return; }
    if (!r || !(r.width > 1 && r.height > 1) || cs.display === 'none' || cs.visibility === 'hidden' || parseFloat(cs.opacity) < .05) return;
    const x0 = Math.max(0, r.left), y0 = Math.max(0, r.top), x1 = Math.min(vw, r.right), y1 = Math.min(vh, r.bottom);
    if (x1 - x0 < 1 || y1 - y0 < 1) return;
    out.push({ id: el.id || el.getAttribute('data-wall') || el.tagName.toLowerCase(), el: el, x: x0, y: y0, w: x1 - x0, h: y1 - y0 });
  };
  document.querySelectorAll('[data-wall]').forEach(add);
  PA_IDS.forEach((id) => add(document.getElementById(id)));
  return out.filter((b) => !out.some((o) => o !== b && o.el.contains(b.el)));
}
// a point of open map a tap can land on: inside the free rectangle, nothing within two finger widths, as far from
// every planet as the rectangle allows (screen px)
function paEmpty(rect) {
  if (typeof pickAt !== 'function' || typeof worldAt !== 'function' || !(rect.w > 80 && rect.h > 80)) return null;
  let best = null, far = -1;
  for (let y = rect.y + 30; y < rect.y + rect.h - 30; y += 20) for (let x = rect.x + 30; x < rect.x + rect.w - 30; x += 20) {
    const w = worldAt({ clientX: x, clientY: y }); if (pickAt(w.x, w.y, 2 * TAP_PX / ZOOM)) continue;
    let d = 1e9; MOONS.forEach((m) => { d = Math.min(d, Math.hypot(m.x - w.x, m.y - w.y) * ZOOM - (m.r || 0) * ZOOM); });
    if (LY && LY.star) d = Math.min(d, Math.hypot(LY.star.x - w.x, LY.star.y - w.y) * ZOOM - (LY.star.r || 0) * ZOOM);
    if (d > far) { far = d; best = [Math.round(x), Math.round(y)]; }
  }
  return best;
}
window.__phoneAudit = function () {
  const vw = innerWidth, vh = innerHeight, boxes = paBoxes(), st = document.getElementById('stage');
  let sr = { x: 0, y: 0, w: vw, h: vh };
  try { const r = st.getBoundingClientRect(); sr = { x: Math.max(0, r.left), y: Math.max(0, r.top), w: Math.min(vw, r.right) - Math.max(0, r.left), h: Math.min(vh, r.bottom) - Math.max(0, r.top) }; } catch (e) {}
  const inBox = (x, y, b) => x > b.x && x < b.x + b.w && y > b.y && y < b.y + b.h;
  let seenN = 0, all = 0;
  for (let y = 3; y < vh; y += 6) for (let x = 3; x < vw; x += 6) { all++;
    if (x >= sr.x && x <= sr.x + sr.w && y >= sr.y && y <= sr.y + sr.h && !boxes.some((b) => inBox(x, y, b))) seenN++; }
  // the largest free rectangle: its edges are the stage's or a box's
  const cut = boxes.filter((b) => b.x < sr.x + sr.w && b.x + b.w > sr.x && b.y < sr.y + sr.h && b.y + b.h > sr.y);
  const xs = [sr.x, sr.x + sr.w].concat(...cut.map((b) => [b.x, b.x + b.w])).filter((v) => v >= sr.x && v <= sr.x + sr.w).sort((a, b) => a - b);
  const ys = [sr.y, sr.y + sr.h].concat(...cut.map((b) => [b.y, b.y + b.h])).filter((v) => v >= sr.y && v <= sr.y + sr.h).sort((a, b) => a - b);
  let best = { x: 0, y: 0, w: 0, h: 0 };
  for (let i = 0; i < xs.length; i++) for (let j = i + 1; j < xs.length; j++) for (let k = 0; k < ys.length; k++) for (let l = k + 1; l < ys.length; l++) {
    const x = xs[i], y = ys[k], w = xs[j] - x, h = ys[l] - y; if (w * h <= best.w * best.h) continue;
    if (!cut.some((b) => b.x < x + w - .5 && b.x + b.w > x + .5 && b.y < y + h - .5 && b.y + b.h > y + .5)) best = { x: x, y: y, w: w, h: h };
  }
  let overlaps = 0; const pairs = [];
  for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) {
    const a = boxes[i], b = boxes[j], ox = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x), oy = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y);
    if (ox > 2 && oy > 2) { overlaps++; pairs.push(a.id + '/' + b.id); }
  }
  const P = document.getElementById('panel'), pOn = !!(P && P.classList.contains('on')), pr = pOn ? P.getBoundingClientRect() : null;
  const shown = (id) => { const e = document.getElementById(id); if (!e) return false; const cs = getComputedStyle(e); return cs.display !== 'none' && cs.visibility !== 'hidden' && e.getClientRects().length > 0; };
  const badge = (id) => { const e = document.getElementById(id); return e && !e.hidden ? (e.getAttribute('data-n') || 'dot') : ''; };
  const tab = MOB ? mobTab() : (typeof VIEW !== 'undefined' ? VIEW : '');
  const comp = composerOpen(), f = document.getElementById('cfolder'), side = document.getElementById('side');
  const sideBox = comp && side ? side.getBoundingClientRect() : null;
  // the visual viewport's bottom (the keyboard takes the rest); a pinch-zoomed page is not a keyboard (mobVV)
  const vv = window.visualViewport, zoomed = !!vv && Math.abs((vv.scale || 1) - 1) > .01, vvBottom = vv && !zoomed ? vv.offsetTop + vv.height : vh;
  const kb = document.documentElement.classList.contains('kb');
  return {
    phone: !!MOB, tablet: document.documentElement.classList.contains('t'), land: !!MOB_LAND, vw: vw, vh: vh, tab: tab,
    mapFree: { x: Math.round(best.x), y: Math.round(best.y), w: Math.round(best.w), h: Math.round(best.h) },
    mapShare: Math.round(seenN / Math.max(1, all) * 1000) / 1000,
    visible: boxes.map((b) => b.id), boxes: boxes.map((b) => ({ id: b.id, x: Math.round(b.x), y: Math.round(b.y), w: Math.round(b.w), h: Math.round(b.h) })),
    overlaps: overlaps, overlapPairs: pairs,
    rail: { tab: tab, content: sheetIsUp() || pOn ? 'card' : 'tabs' },
    // the composer is a sheet when it rises over the map from below and leaves the map's top in view (a phone, the phone module),
    // or anywhere on a wider screen; target: the folder it is aimed at
    composer: { sheet: comp && (!MOB || (tab === 'map' && !!sideBox && sideBox.bottom <= vvBottom + 1 && (kb || sideBox.top > vh * .15))), target: comp && f ? String(f.value || '') : '',
      bottom: sideBox ? Math.round(sideBox.bottom) : 0, vvBottom: Math.round(vvBottom), kb: kb },
    sheet: { on: pOn, full: pOn && !!(typeof MP === 'object' && MP.full), h: pr ? Math.round(pr.height) : 0, share: pr ? Math.round(pr.height / vh * 100) / 100 : 0, ours: sheetIsUp() },
    badges: { today: badge('tabn-today'), agents: badge('tabn-threads'), ask: badge('tabn-wingmate') },
    emptyPoint: paEmpty(best),
    fab: shown('mfab'), zoomStack: shown('mzoom'), chip3d: shown('m3d'), topLine: shown('hud') ? String((document.getElementById('mline') || {}).textContent || '').replace(/\s+/g, ' ').trim() : '',
  };
};

// ---- start-up: the back layer, the Settings row, the buttons the markup carries, the timers ----
CM.onInit.push(() => {
  CM.backLayers.push({ pri: 20, fn: sheetBack });
  CM.settingsExtra.push({ key: 'vibrate', group: 'Phone', label: 'Vibrate', options: [{ value: 'on', label: 'On' }, { value: 'off', label: 'Off' }],
    get: () => (hapticOn() ? 'on' : 'off'), set: (v) => hapticSet(v !== 'off') });
  const m3d = document.getElementById('m3d'); if (m3d) m3d.addEventListener('click', () => CM.enter3d({}));
  const ml = document.getElementById('mline');
  if (ml) ml.addEventListener('click', (e) => { const b = e.target.closest && e.target.closest('[data-num]'); if (!b) return;
    const k = b.getAttribute('data-num');
    if (k === 'need_you') { mtab('today'); return; }
    const twin = document.querySelector('#nums [data-num="' + k + '"]'); if (twin) twin.click(); });   // the strip's own answer (the board, the worst planet)
  // Today's rows answer Enter and Space through the page's one document handler (role=button rows); the actions are buttons
  // the spec's __chromeAudit is the chrome module's; until it is in, the phone's audit answers to that name too (never over a real one)
  if (typeof window.__chromeAudit !== 'function') window.__chromeAudit = window.__phoneAudit;
  setInterval(phoneTick, 1000);
});
