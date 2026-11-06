// ui/a11y.js — the screen reader and the keyboard: #a11ymap, one polite live region, roving tabindex.
// Inlined into the page's main script right after the CM registry (render-game.py, the seams): declare functions and
// assign into CM only, nothing that runs page code at load (start-up goes in CM.onInit). Real backslashes here: this
// file is read as it is, not through a Python string. Neutral words only.
//
// #a11ymap is an off-screen role=list mirror of the canvas: each planet (name, what it holds, what waits there, an Open
// button), the agents, the selected workflow's steps, in angle order around the sun; one item takes Tab (roving tabindex),
// the arrows walk the list, Enter opens that planet as a click would. One aria-live=polite region (#a11ylive) says radio calls
// and step changes, at most one line every 3 s (CM.say; a newer line waiting replaces an older one).

const A11Y = { sig: '', at: 0, last: 0, queued: null, timer: 0, focus: 0, radio: '', radioT: 0, hud: null };

function a11yItems() {
  const items = [], st = LY && LY.star ? LY.star : { x: 0, y: 0 };
  if (CM.mode3d()) items.push({ uid: '', kind: 'leave', text: 'Leave 3D' });   // in 3D: the same list, and the way out first
  const ang = (x, y) => { const a = Math.atan2(y - st.y, x - st.x); return a < -Math.PI / 2 ? a + 2 * Math.PI : a; };   // from the top, clockwise
  MOONS.slice().sort((a, b) => ang(a.x, a.y) - ang(b.x, b.y)).forEach((m) => {
    const n = typeof moonCounts === 'function' ? moonCounts(m) : null, line = n && typeof moonCountLine === 'function' ? moonCountLine(n) : '';
    let purpose = ''; try { const L = planetLook(m); purpose = (L && (L.name || (PURPOSE[L.purpose] || {}).name)) || ''; } catch (e) {}
    items.push({ uid: m.uid, kind: 'planet', text: [CM.labelText(m.uid) || m.label, purpose, line || 'nothing waits', m.allies ? m.allies + ' agent' + (m.allies > 1 ? 's' : '') + ' working' : ''].filter(Boolean).join(', ') });
  });
  units.filter((u) => u && isShip(u) && !u.remote).slice(0, 24).forEach((u) => {
    const s = typeof chromeAgentState === 'function' ? chromeAgentState(u)[0] : String(u.status || '').toLowerCase();
    items.push({ uid: u.uid, kind: 'agent', text: ['Agent ' + CM.shortTitle(String(u.alias || u.name || ''), 40), s, u.cwd ? 'in ' + (typeof chromePlaceOf === 'function' ? chromePlaceOf(u.cwd) : shortPath(u.cwd)) : ''].filter(Boolean).join(', ') });
  });
  const sel = typeof CM.selectedWorkflow === 'function' ? CM.selectedWorkflow() : null, w = sel ? CM.wfList().find((x) => x && String(x.id) === String(sel)) : null;
  if (w) (Array.isArray(w.steps) ? w.steps : []).forEach((s, i) => { if (!s || typeof s !== 'object') return;
    items.push({ uid: s.planet_uid || '', kind: 'step', text: 'Step ' + (i + 1) + ' of ' + w.steps.length + ': ' + String(s.label || s.key || '') + ', ' + (CM.stateWord(String(s.state || '')) || String(s.state || '')) + (s.planet_uid ? ', at ' + CM.labelText(s.planet_uid) : '') }); });
  return items;
}
function a11yRender() {
  const box = document.getElementById('a11ymap'); if (!box) return;
  // in 3D (or on the way in) the list is made once, with Leave 3D on top, and left alone while the view flies
  const in3d = CM.mode3d() || (typeof P3 !== 'undefined' && P3 && P3.mode === 'entering');
  if (in3d && A11Y.in3d) return; A11Y.in3d = in3d && CM.mode3d();
  const items = a11yItems(), sig = items.map((i) => i.uid + '|' + i.text).join('\n');
  if (sig === A11Y.sig) return; A11Y.sig = sig;
  const ae = document.activeElement, had = !!ae && ae !== document.body && typeof ae.getAttribute === 'function' && box.contains(ae), keep = had ? ae.getAttribute('data-uid') : null;
  box.textContent = '';
  items.forEach((it, i) => {
    const li = document.createElement('div'); li.setAttribute('role', 'listitem'); li.setAttribute('data-uid', it.uid); li.setAttribute('data-kind', it.kind);
    li.tabIndex = -1; li.textContent = it.text;
    if (it.kind === 'leave') { const b = document.createElement('button'); b.type = 'button'; b.tabIndex = -1; b.textContent = 'Leave'; b.setAttribute('aria-label', 'Leave 3D'); li.appendChild(b); }
    else if (it.kind !== 'step' && it.uid) { const b = document.createElement('button'); b.type = 'button'; b.tabIndex = -1; b.textContent = 'Open'; b.setAttribute('aria-label', 'Open ' + it.text.split(',')[0]); li.appendChild(b); }
    box.appendChild(li);
  });
  const list = box.querySelectorAll('[role=listitem]'); if (!list.length) return;
  let at = keep ? Array.from(list).findIndex((e) => e.getAttribute('data-uid') === keep) : -1; if (at < 0) at = Math.min(A11Y.focus, list.length - 1);
  A11Y.focus = at; list[at].tabIndex = 0; if (had) list[at].focus();
}
function a11yMove(to) {
  const list = document.querySelectorAll('#a11ymap [role=listitem]'); if (!list.length) return;
  const i = Math.max(0, Math.min(list.length - 1, to));
  list.forEach((e, k) => { e.tabIndex = k === i ? 0 : -1; }); A11Y.focus = i; list[i].focus();
}
function a11yOpen(li) {
  if (li && li.getAttribute('data-kind') === 'leave') { if (typeof p3Leave === 'function') p3Leave('a11y'); return; }
  const uid = li && li.getAttribute('data-uid'), u = uid ? unitByUid(uid) : null; if (!u) return;
  setView('map'); if (typeof humanMove === 'function') humanMove(); activate(u);
}
function a11yKeys(e) {
  const li = e.target && e.target.closest ? e.target.closest('[role=listitem]') : null; if (!li) return;
  const list = Array.from(document.querySelectorAll('#a11ymap [role=listitem]')), i = list.indexOf(li);
  const k = e.key; let took = true;
  if (k === 'ArrowDown' || k === 'ArrowRight') a11yMove(i + 1);
  else if (k === 'ArrowUp' || k === 'ArrowLeft') a11yMove(i - 1);
  else if (k === 'Home') a11yMove(0);
  else if (k === 'End') a11yMove(list.length - 1);
  else if (k === 'Enter' || k === ' ') a11yOpen(li);
  else took = false;
  if (took) { e.preventDefault(); e.stopPropagation(); }   // the map's own keys (flight on the arrows) stay asleep while the list has the keys
}
// one line into the polite region, at most one every 3 s; a newer line waiting replaces an older one
CM.say = function (text) {
  text = String(text == null ? '' : text).replace(/\s+/g, ' ').trim(); if (!text) return;
  const now = Date.now(), wait = A11Y.last + 3000 - now;
  if (wait <= 0) { if (A11Y.timer) { clearTimeout(A11Y.timer); A11Y.timer = 0; } A11Y.queued = null; a11yPut(text); return; }   // a line said now drops the one that waited
  A11Y.queued = text; if (!A11Y.timer) A11Y.timer = setTimeout(() => { A11Y.timer = 0; const t = A11Y.queued; A11Y.queued = null; if (t) a11yPut(t); }, wait);
};
function a11yPut(text) { const r = document.getElementById('a11ylive'); if (!r) return; A11Y.last = Date.now(); r.textContent = text; }
// the radio's calls are said once each, whole: its line types itself out 2 letters per 40 ms, so a call is read 250 ms after the
// last letter (the radio's own box carries no aria-live on the desktop: #a11ylive is the one region)
function a11yRadio() {
  clearTimeout(A11Y.radioT);
  A11Y.radioT = setTimeout(() => {
    const n = document.getElementById('radion'), w = document.getElementById('radiow'), l = document.getElementById('radiol');
    const text = [n && n.textContent, w && w.textContent, l && l.textContent].filter(Boolean).join(', ');
    if (text && text !== A11Y.radio) { A11Y.radio = text; CM.say(text); }
  }, 250);
}
// one live region: the page's other polite boxes (the radio card, the phone's toast, the 3D HUD's line) carry none on the desktop;
// what the 3D HUD writes to its line is said through CM.say instead
function a11yOneRegion() {
  if (typeof chromeOn === 'function' && !chromeOn()) return;   // a phone keeps its toast
  document.querySelectorAll('[aria-live]').forEach((el) => {
    if (el.id === 'a11ylive' || !el.hasAttribute('aria-live')) return;
    el.removeAttribute('aria-live');
    // the radio's words come through a11yRadio once typed; the phone's toast never writes on a desktop; any other box
    // (the 3D HUD's line, a sheet's message) is forwarded as it changes
    if (el.id === 'radio' || el.id === 'mtoast' || typeof MutationObserver !== 'function') return;
    new MutationObserver(() => { const t = el.textContent; if (t && t.trim()) CM.say(t); }).observe(el, { childList: true, characterData: true, subtree: true });
  });
}
CM.onInit.push(() => {
  const box = document.getElementById('a11ymap');
  if (box) { box.addEventListener('keydown', a11yKeys); box.addEventListener('click', (e) => { const b = e.target && e.target.closest && e.target.closest('#a11ymap button'); if (b) a11yOpen(b.closest('[role=listitem]')); });
    box.addEventListener('focusin', (e) => { const li = e.target && e.target.closest && e.target.closest('[role=listitem]'); if (li) { const list = Array.from(box.querySelectorAll('[role=listitem]')); A11Y.focus = Math.max(0, list.indexOf(li)); } }); }
  const radio = document.getElementById('radiol');
  if (radio && typeof MutationObserver === 'function') new MutationObserver(a11yRadio).observe(radio, { childList: true, characterData: true, subtree: true });
  a11yOneRegion(); a11yRender(); setInterval(() => { a11yOneRegion(); a11yRender(); }, 2000);
});
