// ui/chrome.js — the desktop chrome: the right rail (CM.railCard), the status bar (CM.stripSet), the ? sheet, the composer sheet,
// the machines chip, window.__chromeAudit.
// Inlined into the page's main script right after the CM registry (render-game.py, the seams): declare functions and
// assign into CM only, nothing that runs page code at load (start-up goes in CM.onInit). Real backslashes here: this
// file is read as it is, not through a Python string. Neutral words only.
//
// The frame: the map owns the window minus a 400 px rail on the right and a 44 px status
// bar under the map; nothing of the page's own sits over the map but the two small chips bottom-left (machines, legend)
// and what the owner opens. The rail has three tabs (For you, Agents, Workflows); a card (a planet, an agent, a task, the
// composer, a workflow) replaces the tabs with a back link. The status bar: the headline counts (W.counts), the readout
// that hovering rewrites (no floating tooltip), Fly in 3D, the assistant as one line, "?". A phone (html.m) and a touch
// keeps its own layout; there CM.railCard hands the card to CM.sheetCard when the phone module has it. A touch tablet has this frame with
// finger-sized targets, the rail closed below 1280 px.

const RAIL = { tab: 'foryou', card: null, home: null, opts: null, closedPref: null, forced: false, sig: '', tabsSig: '', html: '', from: null };
const STRIP = { counts: null, readout: null, route: null, ask: null };
const STRIP_UNKNOWN = [];               // slots nobody owns, asked for anyway (__chromeAudit().strip.unknown)
const CHROME = { on: false, readout: '', readoutKey: '', countsSig: '', machinesSig: '', legendSig: '', help: 'see', thumbs: new Map(), t0: Date.now(), panelHandled: false, wfSig: null, inert: [], helpFrom: null };
const RAIL_TABS = [['foryou', 'For you'], ['agents', 'AI agents'], ['workflows', 'Workflows']];
const RAIL_NAME = { foryou: 'For you', agents: 'AI agents', workflows: 'Workflows' };
const CHROME_FLIGHT = ['w', 'a', 's', 'd', 'arrowup', 'arrowdown', 'arrowleft', 'arrowright'];
const PRESSI = { down: new Set(), since: 0, shownAt: 0, seen: false };

// the desktop layout is on: a desktop or a touch tablet (a tablet keeps the desktop layout with touch targets), never a phone
function chromeOn() { return !MOB; }
CM.chromeOn = chromeOn;
function chromeEl(id) { return document.getElementById(id); }
// a title as a sentence starts (a task's or a contact's words; names of agents and folders keep their case)
function chromeCap(t) { t = String(t == null ? '' : t); return t ? t.charAt(0).toUpperCase() + t.slice(1) : t; }
function chromeIsEl(x) { return !!x && typeof x === 'object' && x.nodeType === 1; }   // an element (not instanceof: the page also runs in a plain test harness)
function chromeHtmlEsc(s) { return esc(String(s == null ? '' : s)); }

// ---- small pictures: a planet's own body, the sun, a craft (cached data URLs, the rows redraw often) ----
function chromeThumb(uid, size) {
  size = size || 34;
  const sun = !uid || uid === BOARD.uid || uid === 'star', m = sun ? null : MOONS.find((x) => x.uid === uid);
  let L = null; try { L = m && typeof planetLook === 'function' ? planetLook(m) : null; } catch (e) { L = null; }
  const key = uid + '|' + size + '|' + THEME_NAME + '|' + (GB ? 1 : 0) + '|' + (L ? L.purpose + ':' + (L.nightLights ? 1 : 0) : '');   // a changed purpose draws a new body
  if (CHROME.thumbs.has(key)) return CHROME.thumbs.get(key);
  let url = '';
  try {
    if (sun) url = chromeSunIcon(size);
    else if (L) url = purposeIcon(L.purpose, size * 2, { seed: (unitByUid(m.uid) || {}).path || m.label, night: L.nightLights }).toDataURL();   // the body alone: the row's rule says its state
  } catch (e) { url = ''; }
  const html = url ? '<img src="' + url + '" alt="">' : '';
  if (CHROME.thumbs.size > 400) CHROME.thumbs.clear();
  CHROME.thumbs.set(key, html);
  return html;
}
function chromeSunIcon(size) {
  const c = document.createElement('canvas'); c.width = c.height = size; const g = c.getContext('2d'), r = size * 0.34, x = size / 2;
  const glow = g.createRadialGradient(x, x, r * 0.6, x, x, size / 2); glow.addColorStop(0, 'rgba(245,179,66,.35)'); glow.addColorStop(1, 'rgba(245,179,66,0)');
  g.fillStyle = glow; g.fillRect(0, 0, size, size);
  const body = g.createRadialGradient(x - r * 0.3, x - r * 0.3, r * 0.1, x, x, r); body.addColorStop(0, 'rgb(255,247,214)'); body.addColorStop(0.55, 'rgb(245,179,66)'); body.addColorStop(1, 'rgb(180,68,28)');
  g.fillStyle = body; g.beginPath(); g.arc(x, x, r, 0, Math.PI * 2); g.fill();
  return c.toDataURL();
}
// a craft in a status colour: the agent's white dart, a red raider for a problem, amber cargo for what waits
function chromeCraftIcon(kind, size) {
  size = size || 34; const key = 'craft|' + kind + '|' + size;
  if (CHROME.thumbs.has(key)) return CHROME.thumbs.get(key);
  const c = document.createElement('canvas'); c.width = c.height = size * 2; const g = c.getContext('2d'); g.scale(2, 2);
  const s = size, cx0 = s / 2, cy0 = s / 2;
  if (kind === 'agent') {
    g.fillStyle = 'rgba(111,183,255,.18)'; g.beginPath(); g.arc(cx0 - s * 0.28, cy0, s * 0.16, 0, Math.PI * 2); g.fill();
    g.fillStyle = 'rgb(111,183,255)'; g.beginPath(); g.arc(cx0 - s * 0.26, cy0, s * 0.07, 0, Math.PI * 2); g.fill();
    g.fillStyle = 'rgb(201,211,225)'; g.beginPath(); g.moveTo(cx0 + s * 0.36, cy0); g.lineTo(cx0 - s * 0.18, cy0 - s * 0.3); g.lineTo(cx0 - s * 0.06, cy0); g.lineTo(cx0 - s * 0.18, cy0 + s * 0.3); g.closePath(); g.fill();
    g.fillStyle = 'rgb(255,255,255)'; g.beginPath(); g.moveTo(cx0 + s * 0.36, cy0); g.lineTo(cx0 - s * 0.2, cy0 - s * 0.07); g.lineTo(cx0 - s * 0.2, cy0 + s * 0.07); g.closePath(); g.fill();
    g.fillStyle = 'rgb(47,127,191)'; g.beginPath(); g.ellipse(cx0 + s * 0.08, cy0, s * 0.09, s * 0.04, 0, 0, Math.PI * 2); g.fill();
  } else if (kind === 'drone') {   // a job that is late or never ran: the round drone with one eye (COMPS-2D craft)
    g.fillStyle = 'rgba(255,90,95,.22)'; g.beginPath(); g.arc(cx0, cy0, s * 0.42, 0, Math.PI * 2); g.fill();
    g.strokeStyle = 'rgb(110,20,36)'; g.lineWidth = 2; g.beginPath(); g.moveTo(cx0 - s * 0.16, cy0 + s * 0.1); g.lineTo(cx0 - s * 0.3, cy0 + s * 0.3); g.moveTo(cx0 + s * 0.16, cy0 + s * 0.1); g.lineTo(cx0 + s * 0.3, cy0 + s * 0.3); g.stroke();
    const b = g.createRadialGradient(cx0 - s * 0.06, cy0 - s * 0.08, s * 0.02, cx0, cy0, s * 0.24); b.addColorStop(0, 'rgb(224,106,120)'); b.addColorStop(0.6, 'rgb(197,69,85)'); b.addColorStop(1, 'rgb(90,16,32)');
    g.fillStyle = b; g.beginPath(); g.arc(cx0, cy0, s * 0.22, 0, Math.PI * 2); g.fill();
    g.fillStyle = 'rgb(40,6,12)'; g.beginPath(); g.arc(cx0 + s * 0.05, cy0, s * 0.09, 0, Math.PI * 2); g.fill();
    g.fillStyle = 'rgb(255,208,208)'; g.beginPath(); g.arc(cx0 + s * 0.06, cy0 - s * 0.01, s * 0.045, 0, Math.PI * 2); g.fill();
  } else if (kind === 'problem') {
    g.fillStyle = 'rgba(255,90,95,.25)'; g.beginPath(); g.arc(cx0, cy0, s * 0.42, 0, Math.PI * 2); g.fill();
    g.fillStyle = 'rgb(90,42,52)'; g.strokeStyle = 'rgb(224,72,79)'; g.lineWidth = 1.5;
    g.beginPath(); g.moveTo(cx0 + s * 0.34, cy0); g.lineTo(cx0 - s * 0.26, cy0 - s * 0.3); g.lineTo(cx0 - s * 0.12, cy0); g.lineTo(cx0 - s * 0.26, cy0 + s * 0.3); g.closePath(); g.fill(); g.stroke();
    g.fillStyle = 'rgb(255,208,208)'; g.beginPath(); g.arc(cx0 + s * 0.06, cy0, s * 0.06, 0, Math.PI * 2); g.fill();
  } else {
    g.fillStyle = 'rgba(255,181,71,.18)'; g.beginPath(); g.arc(cx0, cy0, s * 0.42, 0, Math.PI * 2); g.fill();
    g.fillStyle = 'rgb(154,168,188)'; g.fillRect(cx0 - s * 0.3, cy0 - s * 0.14, s * 0.5, s * 0.28);
    g.fillStyle = 'rgb(100,112,130)'; for (let i = 0; i < 4; i++) g.fillRect(cx0 - s * 0.27 + i * s * 0.12, cy0 - s * 0.12, s * 0.02, s * 0.24);
    g.fillStyle = 'rgb(255,181,71)'; g.beginPath(); g.arc(cx0 + s * 0.3, cy0, s * 0.07, 0, Math.PI * 2); g.fill();
  }
  const html = '<img src="' + c.toDataURL() + '" alt="">';
  CHROME.thumbs.set(key, html); return html;
}

// ---- words the chrome says ----
function chromeNum(n) { n = Number(n) || 0; return n.toLocaleString('en-US'); }
function chromePurposeName(uid) {
  const m = MOONS.find((x) => x.uid === uid); if (!m) return '';
  try { const L = planetLook(m); return (L && (L.name || (PURPOSE[L.purpose] || {}).name)) || ''; } catch (e) { return ''; }
}
// the seconds since the map was last built from the files ("Mon Oct 12, 12:02 AM", the render's own clock), else since load
function chromeUpdatedAgo() {
  const g = String(W.generated || ''), m = /([A-Z][a-z]{2}) (\d{1,2}), (\d{1,2}):(\d{2}) ([AP]M)/.exec(g);
  let at = NaN;
  if (m) {
    const mon = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'].indexOf(m[1]), now = new Date();
    let h = Number(m[3]) % 12; if (m[5] === 'PM') h += 12;
    const d = new Date(now.getFullYear(), mon, Number(m[2]), h, Number(m[4]));
    if (d.getTime() - now.getTime() > 86400000) d.setFullYear(d.getFullYear() - 1);
    at = d.getTime();
  }
  if (!Number.isFinite(at)) at = CHROME.t0;
  return Math.max(0, (Date.now() - at) / 1000);
}
function chromeUpdatedLine() { const s = chromeUpdatedAgo(); return 'Updated ' + (s < 60 ? 'just now' : CM.relTime(s)) + ' · from the files on this machine'; }
// a seat's or ship's status as one plain word, and its colour class
function chromeAgentState(u) {
  const st = String((u && u.status) || '').toUpperCase();
  if (st === 'WORKING' || st === 'LIVE') return ['working', 'c'];
  if (st === 'WAITING' || st === 'BLOCKED') return ['waits for you', 'a'];
  if (st === 'DRAFT') return ['your draft waits', 'a'];
  if (st === 'LIMITED') return ['out of its limit', 'r'];
  if (st === 'ERROR' || st === 'DOWN' || st === 'DEAD' || st === 'BROKEN') return ['stopped', 'r'];
  if (st === 'COLD' || st === 'STALE') return ['no news', 'w'];
  return ['idle', 'w'];
}
// the sky's item on an agent (a thread that replied waits for your review): the contact whose open target is this unit
function chromeUnitContact(u) {
  if (!u || !u.uid || !skyOk()) return null;
  const uid = String(u.uid), tail = idTail(uid);
  return skyContacts().find((c) => (c.open && typeof c.open === 'object' && c.open.uid === uid) || c.id === uid || 'ship:' + c.id === uid || (c.source === 't3' && idTail(c.id) === tail && uid.indexOf('ship:t3:') === 0)) || null;
}
// a seat's or ship's state with the sky's word first: an agent that waits for your answer or review says so, else its own status
function chromeShipState(u) {
  const c = chromeUnitContact(u), b = c ? CM.bucket(c) : '';
  if (b === 'answer' || b === 'review') return [CM.contactWord(c) || (b === 'answer' ? 'needs your answer' : 'to review'), 'a', c];
  const s = chromeAgentState(u); return [s[0], s[1], null];
}
// one contact by its id, from the sky or from the review list the tasks synthesize (the rows and their buttons agree)
function chromeContact(id) {
  id = String(id || ''); if (!id) return null;
  return skyContacts().find((x) => x.id === id) || (reviewContacts() || []).find((x) => x.id === id) || null;
}
// a ticker line as plain words: a dispatch's id becomes "a dispatch", its upper-case state a word, "5.5min" "5.5 min"
function chromeNewsLine(l) {
  let s = String(typeof l === 'string' ? l : (l && (l.text || l.line)) || '');
  s = s.replace(/\bdispatch\s+d-\d{8}-[\w.-]*\s*:?\s*/i, 'A dispatch ').replace(/\bd-\d{8}-[\w.-]*/g, 'a dispatch');
  s = s.replace(/\bDONE\b/g, 'finished').replace(/\bFAILED\b/g, 'failed').replace(/\bRUNNING\b/g, 'running').replace(/\bSTALE\b/g, 'late');
  s = s.replace(/(\d+(?:\.\d+)?)\s*min\b/g, '$1 min').replace(/(\d+(?:\.\d+)?)\s*(?:sec|s)\b/g, '$1 s');
  return CM.plainText(CM.cleanLine(s), 110);
}
// an agent's name as the rail says it: a session's own name as it is ("claude-code"), a thread named by its task as a sentence
function chromeAgentName(u) { const n = CM.shortTitle(String((u && (u.alias || u.name)) || 'an agent'), 48); return /\s/.test(n) ? chromeCap(n) : n; }
function chromeAgentDoing(u) {
  const t3 = u && u.t3 && typeof u.t3 === 'object' ? u.t3 : null;
  const raw = (t3 && (t3.title || t3.task)) || u.title || u.line || '';
  return CM.shortTitle(String(raw || ''), 60);
}
function chromePlaceOf(path) {
  path = String(path || ''); if (!path) return '';
  const areas = MOONS.map((m) => ({ m: m, p: String((unitByUid(m.uid) || {}).path || '') })).filter((a) => a.p).sort((a, b) => b.p.length - a.p.length);
  const a = areas.find((x) => path === x.p || path.startsWith(x.p + '/'));
  return a ? a.m.label : shortPath(path).split('/').pop();
}
function chromePlanetOfPath(path) {
  path = String(path || ''); if (!path) return null;
  const areas = MOONS.map((m) => ({ m: m, p: String((unitByUid(m.uid) || {}).path || '') })).filter((a) => a.p).sort((a, b) => b.p.length - a.p.length);
  const a = areas.find((x) => path === x.p || path.startsWith(x.p + '/'));
  return a ? a.m.uid : null;
}
// what a contact's row adds after its place: the agent waits, the tests, the second opinion
function chromeContactDetail(c) {
  if (!c) return '';
  if (c.source === 'task') {
    const t = agentTasks().find((x) => 'task:' + x.id === c.id || String(x.id) === idTail(c.id));
    if (t) {
      const v = t.verify, bits = [];
      if (t.verify_cmd) bits.push(v && v.rc === 0 ? 'passed your project\'s tests' : v && v.rc != null ? 'failed your project\'s tests' : 'your project\'s tests have not run yet');
      const ch = t.check; if (ch && ch.verdict && !ch.stale) bits.push(CM.opinionLine(ch.verdict, '').replace(/^second opinion: /, 'second opinion '));
      return bits.join(' · ');
    }
  }
  if (CM.bucket(c) === 'answer') return c.source === 'seat' || c.source === 't3' ? 'the agent waits' : '';
  return '';
}

// ---- the rail ----
function railOpen() { const w = chromeEl('wrap'); return !!w && !w.classList.contains('rail-closed'); }
function railSetOpen(on, save) {
  const w = chromeEl('wrap'); if (!w) return;
  const was = !w.classList.contains('rail-closed');
  w.classList.toggle('rail-closed', !on);
  if (save) { RAIL.closedPref = !on; try { localStorage.setItem('rail', on ? 'on' : 'off'); } catch (e) {} }
  if (was !== !!on) { try { fit(); } catch (e) {} if (on) railRender(true); }
}
// ]: the rail closes with what it shows: a task card or the composer goes first (N opens it again), a planet card stays the page's.
// [ did the old left dashboard, which is retired: on the desktop it does nothing
CM.railToggle = () => {
  if (RAIL.card && RAIL.card === chromeEl('side')) railBack();
  else if (typeof composerOpen === 'function' && composerOpen() && typeof sideShow === 'function') sideShow(false);   // the page's own composer state (hiding its rail closes it)
  railSetOpen(!railOpen(), true);
};
function railTab(tab) {
  if (!RAIL_NAME[tab]) tab = 'foryou';
  RAIL.tab = tab; try { sessionStorage.setItem('railTab', tab); } catch (e) {}
  if (RAIL.card) CM.railCard(null);
  if (!railOpen()) railSetOpen(true, false);
  railRender(true);
}
CM.railTab = railTab;
function railTabsHtml() {
  const c = CM.counts(), rows = CM.wfRows().filter((w) => w.state !== 'done' && w.state !== 'stopped');
  const pill = (n, cls, noun) => n > 0 ? ' <span class="cnt ' + cls + '"' + (noun ? ' data-count="' + noun + '"' : '') + '>' + n + '</span>' : '';
  const n = { foryou: pill(Number(c.need_you) || 0, 'amber', 'need_you'), agents: pill(Number(c.working) || 0, '', 'working'), workflows: pill(rows.length, '', '') };
  return RAIL_TABS.map(([k, name]) => '<button type="button" role="tab" id="rtab-' + k + '" data-rtab="' + k + '" aria-controls="railbody" aria-selected="' + (RAIL.tab === k) + '" tabindex="' + (RAIL.tab === k ? 0 : -1) + '">' + name + n[k] + '</button>').join('');
}
const RAIL_OPEN = new Set();   // the sections the owner opened past their first rows
const RAIL_ROWS = 5;
function railSec(title, n, rows, more, noun, tail) {
  if (!rows.length) return '';
  const key = RAIL.tab + ':' + title, all = RAIL_OPEN.has(key), over = rows.length - RAIL_ROWS;
  const shown = over > 1 && !all ? rows.slice(0, RAIL_ROWS) : rows;
  const fold = over > 1 ? '<button type="button" class="fold" data-act="sec-more" data-sec="' + attr(key) + '"><span>' + (all ? 'Show fewer' : 'Show ' + over + ' more') + '</span><span class="s"></span><span class="chev">' + (all ? '⌃' : '⌄') + '</span></button>' : '';
  return '<section class="sec"><div class="sec-h"><h3>' + chromeHtmlEsc(title) + (n != null ? '<span class="n"' + (noun ? ' data-count="' + noun + '"' : '') + '>' + n + '</span>' : '') + '</h3>' +
    (more ? '<span class="more">' + chromeHtmlEsc(more) + '</span>' : '') + '</div>' + shown.join('') + fold + (tail || '') + '</section>';
}
function railRow(o) {
  // o: {cls, data, th, title, meta (html), age, acts (html), hot, bar}
  return '<div class="row ' + (o.cls || '') + (o.hot ? ' hot' : '') + '" role="button" tabindex="0" ' + (o.data || '') + '><div class="th">' + (o.th || '') + '</div>' +
    '<div class="tx"><div class="t">' + chromeHtmlEsc(o.title) + '</div>' + (o.meta ? '<div class="m">' + o.meta + '</div>' : '') +
    (o.bar != null ? '<div class="bar"><i data-w="' + Math.round(o.bar * 100) + '"></i></div>' : '') + '</div>' +
    '<span class="age">' + chromeHtmlEsc(o.age || '') + '</span>' + (o.acts ? '<div class="acts">' + o.acts + '</div>' : '') + '</div>';
}
function chromeMeta(word, cls, rest) {
  return [word ? '<b class="' + (cls || '') + '">' + chromeHtmlEsc(word) + '</b>' : ''].concat((rest || []).filter(Boolean).map(chromeHtmlEsc)).filter(Boolean).join(' · ');
}
// a contact's title and its one state word; a job's title says how it is doing ("job backup: late"), so that is its word
function chromeContactParts(c) {
  const word = CM.contactWord(c), title = CM.shortTitle((c && (c.title || c.voice)) || '', 48), m = /^job (.+?): (.+)$/i.exec(title);
  return m ? ['Job ' + m[1], m[2]] : [chromeCap(title) || word, word];
}
function chromeGroupContacts(list) {
  const out = [], by = new Map();
  list.forEach((c) => { const [t, w] = chromeContactParts(c), k = t + '|' + w + '|' + (c.planet_uid || '');
    const g = by.get(k); if (g) { g.n++; return; } const o = { c: c, n: 1 }; by.set(k, o); out.push(o); });
  return out;
}
function contactRowHtmlRail(c, hot, n) {
  const b = CM.bucket(c), cls = b === 'problem' ? 'r' : 'a', wcls = b === 'problem' ? 'r' : '';
  const where = c.planet_uid ? CM.labelText(c.planet_uid) : '';
  const [title, word] = chromeContactParts(c);
  const acts = hot ? '<button type="button" class="cmbtn amber" data-act="open" data-cid="' + attr(c.id) + '">' + (b === 'answer' ? 'Answer' : b === 'review' ? 'Review' : 'Open') + '</button>' +
    (c.planet_uid ? '<button type="button" class="cmbtn" data-act="show" data-uid="' + attr(c.planet_uid) + '">Show on map</button>' : '') : '';
  return railRow({ cls: cls, hot: hot, data: 'data-cid="' + attr(c.id) + '"', th: chromeThumb(c.planet_uid || 'star', 34), title: title,
    meta: chromeMeta(word, wcls, [n > 1 ? '×' + n : '', where && where !== c.planet_uid ? where : CM.word('this machine'), chromeContactDetail(c)]), age: (n > 1 && CM.ageWords(c.age_days) ? 'oldest ' : '') + CM.ageWords(c.age_days), acts: acts });
}
function railForYouHtml() {
  if (!skyOk()) {
    // no sky to read: the tasks back from their agents, as before
    const back = agentTasks().filter((t) => TASK_OPEN.includes(t.status));
    return back.length ? railSec('To review', back.length, back.map((t) => railTaskRow(t))) : '<div class="quiet"><b>' + chromeHtmlEsc(CM.word('empty sky')) + '</b></div>';
  }
  const C = skyContacts(), c = CM.counts(), age = (x) => Number(x.age_days) || 0, byAge = (a, b) => age(b) - age(a);
  const ans = C.filter((x) => CM.bucket(x) === 'answer').sort(byAge);
  const rev = (reviewContacts() || []).slice();
  const prob = C.filter((x) => CM.bucket(x) === 'problem').sort(byAge);
  // the oldest need-you item is the first row: its section goes first, its row carries the buttons
  const revFirst = rev.length && (!ans.length || age(rev[0]) > age(ans[0]));
  const secA = () => railSec('Needs your answer', ans.length, ans.map((x, i) => contactRowHtmlRail(x, !revFirst && i === 0)), '', 'answer');
  const secR = () => railSec('To review', rev.length, rev.map((x, i) => contactRowHtmlRail(x, revFirst && i === 0)), rev.length > 1 ? 'from AI agents (programs that do tasks for you) · oldest first' : 'from an AI agent (a program that does tasks for you)', 'review');
  let h = revFirst ? secR() + secA() : secA() + secR();
  h += railSec('Problems', prob.length, chromeGroupContacts(prob).map((g) => contactRowHtmlRail(g.c, false, g.n)), prob.length > 1 ? 'oldest first' : '', 'problems');
  // the folds: decisions open the board (Q), on hold and done today say how many
  const od = c.oldest_decision || {}, folds = [];
  if (c.decisions) folds.push('<button type="button" class="fold" data-act="decisions"><span>Decisions</span><span class="s" data-count="decisions">' + chromeHtmlEsc(c.decisions + (c.decisions === 1 ? ' question' : ' questions') + ' for you, found in your files' + (Number.isInteger(od.days) ? ' · ' + (od.days < 1 ? 'from today' : CM.ageWords(od.days) + ' old') : '')) + '</span><span class="chev">›</span></button>');
  if (c.on_hold) folds.push('<button type="button" class="fold" data-act="onhold"><span>On hold</span><span class="s" data-count="on_hold">' + chromeHtmlEsc(c.on_hold + ' parked') + '</span><span class="chev">›</span></button>');
  if (c.done_today) folds.push('<button type="button" class="fold" data-act="done"><span>Done today</span><span class="s" data-count="done_today"><b>' + chromeHtmlEsc(chromeDoneWords()) + '</b></span><span class="chev">›</span></button>');
  if (folds.length) h += '<section class="sec">' + folds.join('') + '</section>';
  if (!h) h = '<div class="quiet"><b>' + chromeHtmlEsc(CM.word('empty sky')) + '</b>Nothing needs your answer, nothing is back for review, nothing is broken.</div>';
  return h;
}
function chromeDoneWords() {
  const today = CM.dayOf(Date.now() / 1000);
  const wf = CM.wfList().filter((w) => w && w.state === 'done' && CM.dayOf(w.updated) === today).length;
  const tk = agentTasks().filter((t) => t && ['merged', 'done'].includes(t.status) && CM.dayOf(t.ended) === today).length;
  return [wf ? wf + ' workflow' + (wf === 1 ? '' : 's') : '', tk ? tk + ' task' + (tk === 1 ? '' : 's') : ''].filter(Boolean).join(', ') || 'finished today';
}
function railTaskRow(t) {
  const band = CM.TASK_BAND[t.status] || 'waiting', cls = band === 'broken' ? 'r' : band === 'needs' ? 'a' : band === 'working' ? 'c' : band === 'done' ? 'g' : '';
  const ch = t.check, op = ch && ch.verdict && !ch.stale ? CM.opinionLine(ch.verdict, ch.runner ? String(agentLine(ch.runner, ch.model)).split(' · ')[0] : '') : '';
  const tries = t.retries ? 'attempt ' + Math.max(1, t.attempts || 1) + ' of ' + (1 + t.retries) : '';
  return railRow({ cls: cls, data: 'data-tid="' + attr(t.id) + '"', th: chromeThumb(chromePlanetOfPath(t.target) || 'star', 34), title: chromeCap(CM.shortTitle(t.title || '', 48)) || 'A task',
    meta: chromeMeta(taskWord(t), cls === 'a' ? '' : (cls || 'w'), [chromePlaceOf(t.target), TASK_WORKING.includes(t.status) ? tries : '', op]), age: CM.agoOf(t.ended || t.started || t.created).replace(/ ago$/, '') });
}
function railAgentsHtml() {
  const work = workingRows(), seen = new Set();
  const rows = work.map((r) => {
    if (r.kind === 'task') { seen.add('task:' + r.t.id); return railTaskRow(r.t); }
    if (r.kind === 'wf') { const w = r.w || {}, st = r.step || {}, row = CM.wfRows().find((x) => x.id === w.id) || {};
      return railRow({ cls: 'c', data: 'data-wf="' + attr(w.id || '') + '"', th: chromeThumb(st.planet_uid || 'star', 34), title: chromeCap(CM.shortTitle(w.title || '', 48)) || 'A workflow',
        meta: chromeMeta('working', 'c', [row.total ? row.step + ' of ' + row.total : '', row.place || String(st.place || '')]), age: '' }); }
    const u = r.u || {}; seen.add(u.uid);
    return railRow({ cls: 'c', data: 'data-uid="' + attr(u.uid || '') + '"', th: chromeCraftIcon('agent', 34), title: chromeAgentName(u),
      meta: chromeMeta('working', 'c', [chromeAgentDoing(u), u.cwd ? chromePlaceOf(u.cwd) : '']), age: u.t3 && u.t3.since ? CM.agoOf(u.t3.since).replace(/ ago$/, '') : '' });
  });
  const idle = units.filter((u) => u && isShip(u) && !u.remote && !seen.has(u.uid) && !warpHidden(u)).slice(0, 12).map((u) => {
    const s = chromeShipState(u);
    return railRow({ cls: s[1] === 'a' ? 'a' : s[1] === 'r' ? 'r' : '', data: 'data-uid="' + attr(u.uid) + '"', th: chromeCraftIcon('agent', 34), title: chromeAgentName(u),
      meta: chromeMeta(s[0], s[1] === 'w' ? 'w' : s[1], [chromeAgentDoing(u), u.cwd ? chromePlaceOf(u.cwd) : '']), age: u.idle != null ? CM.ageWords(u.idle) : '' });
  });
  const shut = agentTasks().filter((t) => t && !TASK_WORKING.includes(t.status) && t.status !== 'new' && !(t.status === 'failed' && t.retry_due))
    .sort((a, b) => String(b.ended || b.started || b.created || '').localeCompare(String(a.ended || a.started || a.created || ''))).slice(0, 12).map(railTaskRow);
  const news = (Array.isArray(W.ticker) ? W.ticker : []).slice(-4).reverse().map(chromeNewsLine).filter(Boolean);
  let h = railSec('Working', work.length, rows, '', 'working') + railSec('Idle', idle.length, idle) + railSec('Recent', shut.length, shut, shut.length > 1 ? 'newest first' : '');
  if (news.length) h += '<section class="sec"><div class="sec-h"><h3>' + (shut.length ? 'Notes' : 'Recent') + '</h3></div>' + news.map((s) => '<div class="note">' + chromeHtmlEsc(s) + '</div>').join('') + '</section>';
  if (!work.length) h = '<div class="quiet"><b>No agent is working</b>' + chromeHtmlEsc(idle.length ? idle.length + ' idle. Start one with New task (N).' : 'Start one with New task (N).') + '</div>' + h;
  const rs = (W.task_runners || []).map((r) => r.label || r.name).filter(Boolean);
  h += '<div class="note">' + chromeHtmlEsc(rs.length ? 'Agents for tasks: ' + rs.join(' · ') : 'No agent is connected yet: see ? › Set up.') + '</div>';
  return h;
}
function railWorkflowsHtml() {
  const all = CM.wfRows(), row = (w) => {
    const st = String(w.state || ''), cls = st === 'failed' ? 'r' : st === 'needs_you' ? 'a' : st === 'done' ? 'g' : st === 'working' ? 'c' : '';
    const word = CM.stateWord(st) || st;
    const wf = CM.wfList().find((x) => x && String(x.id) === w.id) || {}, steps = Array.isArray(wf.steps) ? wf.steps : [], at = steps[w.step - 1] || {};
    return railRow({ cls: cls, data: 'data-wf="' + attr(w.id) + '"', th: chromeThumb(at.planet_uid || 'star', 34), title: chromeCap(CM.shortTitle(w.title, 48)) || 'A workflow',
      meta: chromeMeta(word, cls === 'a' ? '' : cls, [w.total ? (st === 'done' ? w.done : w.step) + ' of ' + w.total : '', w.place]), age: wf.updated ? CM.agoOf(wf.updated).replace(/ ago$/, '') : '' });
  };
  // by what they ask of the user: waiting on the user, at work, failed, finished
  const by = (f) => all.filter(f).map(row), fin = ['done', 'stopped'];
  let h = railSec('Needs you', null, by((w) => w.state === 'needs_you')) + railSec('Running', null, by((w) => !['needs_you', 'failed'].concat(fin).includes(w.state)))
    + railSec('Failed', null, by((w) => w.state === 'failed')) + railSec('Finished', null, by((w) => fin.includes(w.state)));
  if (!all.length) h = '<div class="quiet"><b>No workflow yet</b>A workflow runs its steps across your planets; each step shows on the map where it works, and you approve what needs you.</div>';
  if (typeof CM.wfNewSheet === 'function') h += '<section class="sec"><button type="button" class="fold" data-wf-new=""><span>New workflow</span><span class="s">pick a template and a goal</span><span class="chev">›</span></button></section>';
  return h;
}
function railSig() {
  const c = CM.counts(), C = skyOk() ? skyContacts() : [];
  // every word a row shows is in the signature: an agent's name and what it does, a task's title and place, the ticker's lines
  return [RAIL.tab, JSON.stringify(c), C.map((x) => x.id + ':' + x.state + ':' + x.age_days + ':' + (x.title || '')).join(','),
    agentTasks().map((t) => t.id + ':' + t.status + ':' + (t.check && t.check.verdict) + ':' + (t.title || '') + ':' + (t.target || '') + ':' + (t.attempts || 0) + ':' + (t.ended || t.started || t.created || '')).join(','),
    JSON.stringify(CM.wfRows()), units.filter((u) => u && isShip(u)).map((u) => u.uid + ':' + u.status + ':' + (u.alias || u.name || '') + ':' + chromeAgentDoing(u) + ':' + (u.cwd || '') + ':' + (u.idle == null ? '' : u.idle) + ':' + (u.t3 && u.t3.since || '')).join(','),
    THEME_NAME, (W.ticker || []).map((l) => typeof l === 'string' ? l : (l && (l.text || l.line)) || '').join('\n'), (W.task_runners || []).map((r) => (r && (r.label || r.name)) || '').join(','), MOONS.length].join('|');
}
function railRender(force) {
  if (!CHROME.on) return;
  const tabs = chromeEl('railtabs'), body = chromeEl('railbody'); if (!tabs || !body) return;
  const sig = railSig(); if (!force && sig === RAIL.sig) return; RAIL.sig = sig;
  const th = railTabsHtml(); if (th !== RAIL.tabsSig) { RAIL.tabsSig = th; tabs.innerHTML = th; }
  const h = RAIL.tab === 'agents' ? railAgentsHtml() : RAIL.tab === 'workflows' ? railWorkflowsHtml() : railForYouHtml();
  if (h !== RAIL.html) { const top = body.scrollTop; RAIL.html = h; body.innerHTML = h; body.scrollTop = top;
    body.querySelectorAll('.bar i[data-w]').forEach((i) => { i.style.width = i.getAttribute('data-w') + '%'; }); }
}
function railClick(e) {
  const t = e.target && e.target.closest ? e.target : null; if (!t) return;
  const tab = t.closest('[data-rtab]'); if (tab) { railTab(tab.getAttribute('data-rtab')); return; }
  const a = t.closest('[data-act]');
  if (a) {
    const k = a.getAttribute('data-act');
    if (k === 'back') { railBack(); return; }
    if (k === 'newtask') { openComposer(null); return; }   // + Task: the composer, aimed at the planet in focus or open, else the root
    if (k === 'open') { const c = chromeContact(a.getAttribute('data-cid')); if (c) openContact(c); else if (String(a.getAttribute('data-cid') || '').indexOf('task:') === 0) openTask(a.getAttribute('data-cid').slice(5)); return; }
    if (k === 'show') { const u = unitByUid(a.getAttribute('data-uid')); if (u) flyOpen(u); return; }
    if (k === 'decisions') { setView('map'); toggleQuests(true); return; }
    if (k === 'onhold') { setView('today'); return; }
    if (k === 'done') { railTab('agents'); return; }
    if (k === 'problems-all') { setView('map'); showStar(); return; }
    if (k === 'sec-more') { const key = a.getAttribute('data-sec'); if (RAIL_OPEN.has(key)) RAIL_OPEN.delete(key); else RAIL_OPEN.add(key); railRender(true); return; }
  }
  const row = t.closest('.row'); if (!row || t.closest('button,a,input,select,textarea,summary')) return;
  if (row.dataset.cid) { const c = chromeContact(row.dataset.cid);
    if (c) openContact(c); else if (row.dataset.cid.indexOf('task:') === 0) openTask(row.dataset.cid.slice(5)); return; }
  if (row.dataset.tid) { openTask(row.dataset.tid); return; }
  if (row.dataset.wf) { CM.selectWorkflow(row.dataset.wf || null); return; }
  if (row.dataset.uid) { const u = unitByUid(row.dataset.uid); if (u) { setView('map'); humanMove(); activate(u); } }
}
function railKeys(e) {
  const tab = e.target && e.target.closest && e.target.closest('[role=tab]');
  if (tab && (e.key === 'ArrowRight' || e.key === 'ArrowLeft')) {
    e.preventDefault(); e.stopPropagation();
    const keys = RAIL_TABS.map((x) => x[0]), i = keys.indexOf(tab.getAttribute('data-rtab')), next = keys[(i + (e.key === 'ArrowRight' ? 1 : keys.length - 1)) % keys.length];
    railTab(next); const b = chromeEl('rtab-' + next); if (b) b.focus(); return;
  }
  const row = e.target && e.target.closest && e.target.closest('.row');
  if (row && (e.key === 'Enter' || e.key === ' ') && e.target === row) { e.preventDefault(); e.stopPropagation(); row.click(); }
}

// CM.railCard(el, opts): show el as the rail's content with a back link; null gives the tabs back. opts: {title, back:'For you'|
// 'Workflows'|'Agents', onBack, open (a test: false closes the card), onClose (when it is replaced or closed)}. On a phone the card
// goes to the phone module's sheet (CM.sheetCard); a touch tablet shows it in the rail as a drawer over the map's right edge.
function railGiveBack() {
  const el = RAIL.card, h = RAIL.home, o = RAIL.opts || {};
  RAIL.card = null; RAIL.home = null; RAIL.opts = null;
  if (!el) return;
  try { if (el.contains(document.activeElement)) document.activeElement.blur(); } catch (e) {}   // a closed card keeps no keys (the arrows fly again)
  if (el.id === 'panel') {   // the page's own card leaves the rail closed: another card or the tabs own the rail now, and the next tick never takes it back
    CHROME.panelHandled = true;
    if (el.classList.contains('on')) { el.classList.remove('on'); el.setAttribute('aria-hidden', 'true'); const w = chromeEl('wrap'); if (w) w.classList.remove('panel-open'); }
  }
  if (typeof o.onClose === 'function') { try { o.onClose(); } catch (e) { console.error('railCard onClose', e); } }
  if (h && h.parent && h.parent.isConnected) h.parent.insertBefore(el, h.next && h.next.parentNode === h.parent ? h.next : null);
  else if (el.parentNode) el.parentNode.removeChild(el);
}
CM.railCard = function (el, opts) {
  if (MOB && typeof CM.sheetCard === 'function') return CM.sheetCard(el, opts);
  const rail = chromeEl('rail'), body = chromeEl('railcardbody'), card = chromeEl('railcard'); if (!rail || !body || !card) return;
  if (el && !chromeIsEl(el)) return;
  if (RAIL.card && RAIL.card !== el) railGiveBack();
  if (!el) {
    rail.classList.remove('card'); card.hidden = true; chromeEl('railtabs').hidden = false; chromeEl('railbody').hidden = false; { const nb = chromeEl('railnew'); if (nb) nb.hidden = false; }
    if (RAIL.forced) { RAIL.forced = false; if (RAIL.closedPref !== false && innerWidth < 1280) railSetOpen(false, false); }
    railRender(true); return;
  }
  if (RAIL.card !== el) { RAIL.home = el.parentNode ? { parent: el.parentNode, next: el.nextSibling } : null; body.textContent = ''; body.appendChild(el); RAIL.card = el; body.scrollTop = 0; }
  RAIL.opts = Object.assign({}, opts || {});
  const back = RAIL.opts.back || RAIL_NAME[RAIL.tab] || 'For you';
  RAIL.opts.back = back;
  const bb = chromeEl('railback'); if (bb) { bb.textContent = '← ' + back; bb.setAttribute('aria-label', 'Back to ' + back); }
  rail.classList.add('card'); card.hidden = false; chromeEl('railtabs').hidden = true; chromeEl('railbody').hidden = true; { const nb = chromeEl('railnew'); if (nb) nb.hidden = true; }
  if (chromeOn() && !railOpen()) { RAIL.forced = true; railSetOpen(true, false); }
};
function railBack() {
  const o = RAIL.opts || {}, back = o.back, el = RAIL.card;
  const tab = Object.keys(RAIL_NAME).find((k) => RAIL_NAME[k] === back) || RAIL.tab;
  RAIL.tab = tab;
  if (typeof o.onBack === 'function') { try { o.onBack(); } catch (e) { console.error('railCard onBack', e); } }
  if (RAIL.card === el && el) CM.railCard(null);
  railRender(true);
}
// the cards of the page's own: the planet/agent/machine card (#panel, filled by show/renderCard), the task and the composer (#side)
// the page's card (#panel) is taken once per opening (a fresh .on): a card that replaced it, or CM.railCard(null), holds until the
// panel opens again; a panel the rail let go is closed (railGiveBack), so the page and the rail agree on what is open
function chromePanelSync() {
  if (!CHROME.on || !chromeOn()) return;
  const P = chromeEl('panel'); if (!P) return;
  const on = P.classList.contains('on');
  if (!on) { CHROME.panelHandled = false; if (RAIL.card === P) CM.railCard(null); return; }
  if (RAIL.card === P || CHROME.panelHandled || VIEW !== 'map') return;
  CHROME.panelHandled = true;
  RAIL.from = openUnit && openUnit.uid ? { uid: String(openUnit.uid), name: String(openUnit.name || '') } : null;   // where a task card opened from (its back link)
  CM.railCard(P, { open: () => P.classList.contains('on'), onBack: () => closeAll(), back: RAIL_NAME[RAIL.tab] });
}
// the task card and the composer (#side): the back link goes where the card was opened from: the planet card a Review button sat on,
// else the tab that was showing
CM.taskCard = function () {
  if (!CHROME.on || !chromeOn()) return;
  const S = chromeEl('side'); if (!S) return;
  const leave = () => { if (composerOpen()) { chromeEl('composer').classList.remove('on'); S.classList.remove('compose'); AIM = null; } if (openTaskId != null) { stopTaskPoll(); openTaskId = null; }
    if (typeof sideShow === 'function' && typeof sideOpen === 'function' && sideOpen()) sideShow(false); };   // the page's old rail state follows (K and N read it)
  let back, onBack = leave;
  if (RAIL.card === S && RAIL.opts) { back = RAIL.opts.back; onBack = RAIL.opts.onBack || leave; }
  else if (RAIL.card && RAIL.card.id === 'panel' && RAIL.from && RAIL.from.name && unitByUid(RAIL.from.uid)) {
    const from = RAIL.from; back = from.name;
    onBack = () => { leave(); const u = unitByUid(from.uid); if (u) { setView('map'); activate(u); } };
  } else back = RAIL_NAME[RAIL.tab];
  CM.railCard(S, { back: back, open: () => composerOpen() || openTaskId != null, onBack: onBack, onClose: leave });
};

// ---- the planet card: a name, what it holds, one sentence, three verbs, then
// Problems / Waiting for you / Agents here, the purpose row with Change, and everything else under More. renderCard calls it with
// what it read (probs = cardProblems(u), the indices the card's own data-copen / data-cfix buttons use).
function chromeCardRow(o) {
  return '<div class="row ' + (o.cls || '') + '"' + (o.data ? ' ' + o.data : '') + ' role="' + (o.data ? 'button' : 'group') + '" tabindex="' + (o.data ? 0 : -1) + '"><div class="th">' + (o.th || '') + '</div>' +
    '<div class="tx"><div class="t">' + chromeHtmlEsc(o.title) + '</div>' + (o.meta ? '<div class="m">' + o.meta + '</div>' : '') +
    (o.bar != null ? '<div class="bar"><i data-w="' + Math.round(Math.max(0, Math.min(1, o.bar)) * 100) + '"></i></div>' : '') + '</div>' +
    '<span class="age">' + chromeHtmlEsc(o.age || '') + '</span>' + (o.acts ? '<div class="acts">' + o.acts + '</div>' : '') + '</div>';
}
function chromeCardSec(title, n, rows) {
  if (!rows.length) return '';
  const cut = rows.length > 5 ? 4 : rows.length;   // the first four, the rest one click away: Agents here and the purpose row stay in view
  return '<section class="sec"><div class="sec-h"><h3>' + chromeHtmlEsc(title) + '<span class="n">' + n + '</span></h3></div>' + rows.slice(0, cut).join('') +
    (cut < rows.length ? '<details class="pc-rest"><summary class="fold"><span>Show ' + (rows.length - cut) + ' more</span><span class="s"></span><span class="chev">⌄</span></summary>' + rows.slice(cut).join('') + '</details>' : '') + '</section>';
}
function chromeProbParts(p) {
  // a problem's title and its one state word: "Nightly backup" · late; a failed task's title · failed
  if (p.kind === 'contact') return chromeContactParts(p.contact || {});
  if (p.kind === 'cron') { const m = /^job (.*): (.*)$/.exec(String(p.key || '')); return m ? ['Job ' + m[1], m[2]] : [String(p.key || 'a job'), 'late']; }
  if (p.kind === 'wreck') { const m = /^task “(.*)” failed$/.exec(String(p.key || '')); return [m ? m[1] : String(p.key || 'a task'), 'failed']; }
  return [CM.shortTitle(String(p.key || ''), 48) || 'a problem', p.kind === 'item' ? 'decision' : 'problem'];
}
function chromePlanetCardHtml(u, ctx) {
  const m = ctx.m, probs = ctx.probs || [], area = u.kind === 'area', E = chromeHtmlEsc;
  const P = [], Wt = [];
  probs.forEach((p, i) => { const b = p.kind === 'contact' ? CM.bucket(p.contact || {}) : p.kind === 'item' ? 'decision' : 'problem'; (b === 'problem' ? P : Wt).push({ p: p, i: i, b: b }); });
  const nP = P.reduce((a, x) => a + (Number.isInteger(x.p.n) ? x.p.n : 1), 0), nW = Wt.reduce((a, x) => a + (Number.isInteger(x.p.n) ? x.p.n : 1), 0);
  const seats = ctx.seatsHere || [], threads = ctx.threads || [], workT = threads.filter((t) => TASK_WORKING.includes(t.status) || t.status === 'new'), workS = seats.filter((c) => c.status === 'WORKING');
  const nA = workT.length + workS.length;
  // the sub line and the sentence
  const purpose = area ? chromePurposeName(u.uid) : 'Folder in ' + String(u.area || '').replace('area:', '');
  const bytes = m && Number(m.bytes) > 0 ? fmtBytes(m.bytes) : (u.size && u.size !== '—' ? String(u.size) : '');
  const sub = [purpose, u.files == null ? '' : chromeNum(u.files) + ' file' + (u.files === 1 ? '' : 's'), bytes, u.age == null ? '' : 'last change ' + CM.daysAgo(u.age).replace(/ ago$/, ' ago')].filter(Boolean).join(' · ');
  let say = '';
  if (u.alias_of) say = 'A shortcut to ' + E(shortPath(u.alias_of)) + '; nothing of its own lives here.';
  else {
    // what waits in the count's own nouns (need you, decisions), as the status bar, Today and the badge say it
    const nNeed = Wt.filter((x) => x.b !== 'decision').reduce((a, x) => a + (Number.isInteger(x.p.n) ? x.p.n : 1), 0), nDec = nW - nNeed;
    const own = moonCountLine({ need_you: nNeed, decisions: nDec });
    const bits = [nP ? '<b class="r">' + nP + ' ' + CM.countNoun('problems', nP) + '</b>' : '', own ? '<b class="a">Waiting on you: ' + own + '</b>' : ''].filter(Boolean);
    if (bits.length) say = bits.join('. ') + '. ' + (nA ? (nA === 1 ? 'An agent is working here.' : nA + ' agents are working here.') : nP ? 'Nobody is on ' + (nP === 1 ? 'it' : 'them') + ' yet.' : '');
    else if (nA) say = '<b class="c">' + (nA === 1 ? 'An agent is working here' : nA + ' agents are working here') + '</b>. Nothing waits for you.';
    else if (u.age == null) say = 'Empty: no files yet.';
    else if (u.age >= 30) say = 'Quiet: nothing written in ' + E(CM.ageWords(u.age)) + '. Fine if it is finished.';
    else say = 'Nothing waits here. Last change ' + E(CM.daysAgo(u.age)) + '.';
    if (m && m.trend && m.trend.worse) say += ' Got worse this week: ' + E(m.trend.why) + '.';
    if (m && m.wellR && m.bloat >= 30) say += ' ' + m.bloat + '% of it can be rebuilt (caches).';
  }
  const th = area ? chromeThumb(u.uid, 64) : (typeof filePlanet === 'function' ? filePlanet(u.name, true, unitTheme(u)) : '');
  let h = '<div class="card pc" data-card="planet" data-uid="' + attr(u.uid) + '"><div class="pc-hero"><div class="pic">' + th + '</div><div><h2 class="pc-name">' + E(u.name) + '</h2><div class="pc-sub">' + E(sub) + '</div></div></div>' +
    '<div class="pc-say">' + say + '</div><div class="pc-btns">' +
    (area && !MOB ? '<button type="button" class="cmbtn big lead" data-wf-new="' + attr(u.uid) + '">Run a workflow here</button>' : '') +   // the workflow lane answers any [data-wf-new]
    (area ? '<button type="button" class="cmbtn big" data-act="fly3d" data-target="' + attr(u.uid) + '">Fly in 3D</button>' : '') +
    '<button type="button" class="cmbtn big" data-cact="thread">New task</button></div><div class="pc-body">';
  // Problems: each with its verbs (open it, or send an agent on it)
  h += chromeCardSec('Problems', nP, P.map(({ p, i }) => { const [title, word] = chromeProbParts(p);
    const job = p.kind === 'cron' || (p.contact && p.contact.source === 'cron'), c0 = p.contact || {};
    return chromeCardRow({ cls: 'r', th: chromeCraftIcon(job ? 'drone' : 'problem', 34), title: title, meta: chromeMeta(word, 'r', [p.n > 1 ? '×' + p.n : '', job ? 'a scheduled job' : '', chromeContactDetail(c0)]), age: p.age != null ? CM.ageWords(p.age) : '',
      acts: p.kind === 'contact' ? '<button type="button" class="cmbtn" data-copen="' + i + '">' + (p.contact && p.contact.source === 'cron' ? 'Open the job' : 'Open') + '</button>' + (p.contact && p.contact.source !== 'task' ? '<button type="button" class="cmbtn" data-cfix="' + i + '">Send an agent</button>' : '')
        : '<button type="button" class="cmbtn" data-cfix="' + i + '">Send an agent</button>' }); }));
  // Waiting for you: answers, reviews, decisions
  h += chromeCardSec('Waiting for you', nW, Wt.map(({ p, i, b }) => { const [title, word] = chromeProbParts(p), c = p.contact || {};
    const tid = c.source === 'task' ? idTail(c.id) : '';
    const acts = b === 'review' && tid ? '<button type="button" class="cmbtn amber" data-act="task" data-tid="' + attr(tid) + '">Review changes</button>'
      : '<button type="button" class="cmbtn amber" data-copen="' + i + '">' + (b === 'answer' ? 'Answer' : 'Open') + '</button>';
    return chromeCardRow({ cls: 'a', th: chromeCraftIcon('waiting', 34), title: title, meta: chromeMeta(word, '', [chromeContactDetail(c)]), age: p.age != null ? CM.ageWords(p.age) : '', acts: acts }); }));
  // Agents here: the ones at work first
  const agentRows = workT.map((t) => chromeCardRow({ cls: 'c agent', data: 'data-ctid="' + attr(t.id) + '"', th: chromeCraftIcon('agent', 34), title: agentLine(t.runner, t.model).split(' · ')[0] || 'an agent',
    meta: chromeMeta(taskWord(t), 'c', [chromeCap(CM.shortTitle(t.title || '', 40)), t.retries ? 'attempt ' + Math.max(1, t.attempts || 1) + ' of ' + (1 + t.retries) : '', CM.agoOf(t.started || t.created).replace(/ ago$/, '')]) }))
    .concat(seats.slice().sort((a, b) => (b.status === 'WORKING') - (a.status === 'WORKING')).slice(0, 6).map((c) => { const s = chromeShipState(c);
      return chromeCardRow({ cls: s[1] === 'c' ? 'c agent' : s[1] === 'a' ? 'a' : '', data: 'data-cuid="' + attr(c.uid) + '"', th: chromeCraftIcon('agent', 34), title: chromeAgentName(c),
        meta: chromeMeta(s[0], s[1] === 'w' ? 'w' : s[1], [chromeAgentDoing(c)]) }); }));
  h += chromeCardSec('Agents here', workT.length + seats.length, agentRows);
  // the purpose row (what it holds, and Change) and the sub-folders
  const pr = area && typeof CM.purposeRow === 'function' ? CM.purposeRow(u.uid) : null, rooms = area ? (u.rooms || []) : [];
  const hidden = rooms.filter((r) => /^\./.test(String(r.name || ''))).length;
  h += '<div class="pc-det">' + (pr && pr.text ? '<div class="r"><span>What it holds</span><span data-purpose-text="' + attr(pr.text) + '">' + E(pr.text) + (pr.canChange ? ' · <button type="button" class="lnk" data-act="purpose">Change</button>' : '') + '</span></div>' : '') +
    (area ? '<div class="r"><span>Sub-folders</span><span>' + (rooms.length - hidden) + (hidden ? ' · ' + hidden + ' hidden' : '') + '</span></div>' : '') +
    (u.kind === 'dir' && u.children_total != null ? '<div class="r"><span>Inside</span><span>' + chromeNum(u.children_total) + ' entries</span></div>' : '') + '</div>';
  // More: the folders and files inside, the facts behind the card, what an agent reads
  let more = '';
  if (area) {
    const vis = rooms.filter((r) => !/^\./.test(String(r.name || ''))).sort((a, b) => (b.files || 0) - (a.files || 0)), hid = rooms.filter((r) => /^\./.test(String(r.name || '')));
    const room = (r) => '<div class="crow" data-cuid="' + attr(r.uid) + '">' + filePlanet(r.name, true, unitTheme(u)) + '<span class="n">' + E(r.name) + '</span><span class="m">' + (r.files == null ? '' : chromeNum(r.files) + ' files · ') + E(cardAge(r.age)) + '</span></div>';
    if (vis.length) more += '<h4>Sub-folders</h4>' + vis.map(room).join('');
    if (hid.length) more += '<h4>Hidden folders (' + hid.length + ')</h4>' + hid.map(room).join('');
    const files = u.root_files || [];
    if (files.length) more += '<h4>Files · ' + (u.root_files_total || files.length) + ' · click one to read it</h4><table class="tbl ctbl">' + files.slice(0, 14).map((f) => fileRow(f[0], u.path + '/' + f[0], [f[1], cardAge(f[2])])).join('') + '</table>';
  } else {
    const kids = u.children || [];
    if (kids.length) more += '<h4>Inside · click a file to read it</h4><table class="tbl ctbl">' + kids.slice(0, 14).map((k) => ((k[4] & 1) || String(k[0]).indexOf(' → ') >= 0)
      ? '<tr><td class="fname">' + filePlanet(k[0], true, unitTheme(u)) + E(k[0]) + '</td><td>' + (k[1] == null ? '' : chromeNum(k[1]) + ' files') + '</td><td>' + E(k[2] || '') + '</td></tr>'
      : fileRow(k[0], u.path + '/' + k[0], [k[2] || '', cardAge(k[3])])).join('') + '</table>';
  }
  const d = ctx.district || { rows: [], extra: '' };
  more += '<h4>The folder</h4><dl>' + d.rows.map((r) => '<dt>' + E(r[0]) + '</dt><dd>' + E(r[1]) + '</dd>').join('') + '</dl>' + String(d.extra || '').replace(/<div class="say"><button type="button" class="newtask"[\s\S]*?<\/div>/, '') + (ctx.verbs || '');
  more += '<h4>For an agent</h4><div class="pc-btns"><button type="button" class="cmbtn" data-cact="brief">Copy brief for an agent</button><button type="button" class="cmbtn" data-cact="json">Copy JSON</button>' +
    (W.opener && DESK ? '<button type="button" class="cmbtn" data-cact="open">Open the folder</button>' : '') + '</div>';
  h += '<details class="pc-more"><summary>More about ' + E(u.name) + '</summary>' + more + '</details></div></div>';
  return h;
}
// the agent card (an agent session, a T3 thread, a scheduled job, another machine): plain words on top, the internals (window,
// pane, command, the raw screen and the box that writes to it) only under More, closed (P26)
function chromeUnitCardHtml(u, rows, extra) {
  const E = chromeHtmlEsc, ship = isShip(u), machine = u.kind === 'machine' || u.kind === 'portal';
  const sx = ship ? chromeShipState(u) : null, s = ship ? [sx[0], sx[1]] : machine ? [chromeMachineState(u)[0], chromeMachineState(u)[0] === 'answers' ? 'g' : 'w'] : [String(u.status || '').toLowerCase(), 'w'];
  const item = sx && sx[2];   // the sky's item on this agent: what waits for you, and its action
  const kindWord = ship ? (u.source === 't3' ? 'Agent in T3' : u.kind === 'seat' ? 'Agent in a terminal' : 'Agent') : machine ? 'Another machine' : u.kind === 'cron' ? 'A scheduled job' : u.kind === 'proc' ? 'A running program' : u.kind === 'channel' ? 'A link between machines' : 'Part of the map';
  const where = u.cwd ? 'in ' + chromePlaceOf(u.cwd) : u.area ? 'in ' + String(u.area).replace('area:', '') : '';
  const sub = [kindWord, where].filter(Boolean).join(' · ');
  const t3 = typeof t3Url === 'function' ? t3Url(u) : null;
  let say = '';
  if (ship && item) {   // it waits for you: the sky's words, then what to do
    const b = CM.bucket(item), voice = CM.plainText(String(item.voice || ''), 80);
    say = '<b class="a">' + E(chromeCap(s[0])) + '</b>' + (voice ? ': ' + E(chromeCap(voice)) : '') + '. ' + E(b === 'answer' ? 'It asks you something: answer it where it runs.' : t3 ? 'Read what it did in T3; the row clears when you do.' : 'Read what it did where it runs; the row clears when you do.'); }
  else if (ship) { const doing = chromeAgentDoing(u), named = CM.shortTitle(String(u.alias || u.name || u.uid), 40), since = u.t3 && u.t3.since ? CM.agoOf(u.t3.since) : u.idle != null ? CM.daysAgo(u.idle) : '';
    say = '<b class="' + (s[1] === 'w' ? '' : s[1]) + '">' + E(chromeCap(s[0])) + '</b>' + (doing && doing !== named ? ': ' + E(doing) : '') + (since && s[0] !== 'working' ? E(' · last news ' + since) : '') + '.';
    if (u.status === 'WAITING') say += ' It asks you something: answer below.'; }
  else if (machine) say = '<b class="' + (s[0] === 'answers' ? 'g' : s[0] === 'asleep' ? '' : 'r') + '">' + E(s[0] === 'answers' ? 'It answers' : 'It is ' + s[0]) + '</b>' +
    E(u.idle != null ? ' · last news ' + CM.daysAgo(u.idle) : '') + '.' + (u.kind === 'portal' ? ' Its gate crosses to its own map.' : ' Its folders show on its own map.');
  else if (u.kind === 'cron') say = '<b class="' + (BAD_VERDICTS.includes(u.verdict) ? 'r' : 'g') + '">' + E(u.verdict ? CM.verdictWord(u.verdict) : String(u.status || '').toLowerCase()) + '</b>' + E(u.when || u.schedule ? ' · runs ' + String(u.when || u.schedule) : '') + E(typeof u.receipt_age === 'number' ? ' · last ran ' + CM.relTime(u.receipt_age) : '') + '.';
  const icon = ship ? chromeCraftIcon('agent', 64) : machine ? chromeMachineIcon(u, 64) : chromeCraftIcon(u.kind === 'cron' && BAD_VERDICTS.includes(u.verdict) ? 'problem' : 'waiting', 64);
  const acts = [t3 ? '<a class="cmbtn big lead" data-t3open="1" href="' + attr(t3) + '" target="_blank" rel="noopener noreferrer">Open in T3</a>' : '',
    u.gx != null && !machine ? '<button type="button" class="cmbtn big" data-act="show" data-uid="' + attr(u.uid) + '">Show on map</button>' : '',
    ship && u.kind === 'seat' && !u.remote && u.composable ? '<button type="button" class="cmbtn big" data-act="write">Write to it</button>' : ''].filter(Boolean).join('');
  // the permission an agent asks for stays on top (it waits for you); the raw screen, the ids and the send box go under More
  const permit = /<div id="permitbox"[\s\S]*?<\/div><\/div>/.exec(extra || '');
  let rest = (permit ? String(extra).replace(permit[0], '') : String(extra || '')).replace(/<div class="bar"><i[^>]*><\/i><\/div>/, '').replace(/<b>CAUSE<\/b><br>/, '<b>Why:</b> ');
  // a terminal seat's screen is the point of its card: the live terminal (ui/term.js) and its bar sit on top, open
  const screen = /<div class="terminalbar">(?:(?!<div class="terminalbar">)[\s\S])*?<div class="live term" id="pterm" hidden><\/div><pre class="live" id="plive"[^>]*>[\s\S]*?<\/pre>/.exec(rest);
  if (screen) rest = rest.replace(screen[0], '');
  const facts = '<dl>' + rows.map((r) => '<dt>' + E(r[0]) + '</dt><dd' + (r[0] === 'status' ? ' id="pstatus"' : '') + '>' + E(r[1]) + '</dd>').join('') + '</dl>';
  const keepOpen = machine;   // a machine's own forms (cross, re-render) are its verbs: they stay visible
  // the item that waits for you, as a row with its action (a review row opens this card: the card shows the review item)
  const wait = item ? chromeCardSec('Waiting for you', 1, [chromeCardRow({ cls: 'a', th: chromeCraftIcon('waiting', 34), title: chromeCap(CM.shortTitle(item.title || item.voice || '', 48)) || chromeCap(s[0]),
    meta: chromeMeta(CM.contactWord(item), '', [item.planet_uid ? CM.labelText(item.planet_uid) : '']), age: CM.ageWords(item.age_days),
    acts: t3 ? '<a class="cmbtn amber" data-t3open="1" href="' + attr(t3) + '" target="_blank" rel="noopener noreferrer">Open in T3</a>' : (u.gx != null ? '<button type="button" class="cmbtn amber" data-act="show" data-uid="' + attr(u.uid) + '">Show on map</button>' : '') })]) : '';
  return '<div class="card pc" data-card="' + (ship ? 'agent' : machine ? 'machine' : 'unit') + '" data-uid="' + attr(u.uid) + '"><div class="pc-hero"><div class="pic">' + icon + '</div><div><h2 class="pc-name">' + E(ship ? chromeAgentName(u) : CM.shortTitle(String(u.alias || u.name || u.uid), 40)) + '</h2><div class="pc-sub">' + E(sub) + '</div></div></div>' +
    (say ? '<div class="pc-say">' + say + '</div>' : '') + (acts ? '<div class="pc-btns">' + acts + '</div>' : '') + '<div class="pc-body">' + wait +
    (permit ? '<div class="pc-ask">' + permit[0].replace('<div id="permitbox" hidden>', '<div id="permitbox" hidden><b>It asks:</b>') + '</div>' : '') +
    (screen ? '<div class="pc-term">' + screen[0] + '</div>' : '') +
    (keepOpen ? (rest.replace(/<[^>]*>/g, '').trim() || /<(form|button)\b/.test(rest) ? '<div class="pc-det">' + rest + '</div>' : '') + '<details class="pc-more"><summary>More</summary>' + facts + '</details>' : '<details class="pc-more"><summary>More: the window, its screen, write to it</summary>' + facts + rest + '</details>') + '</div></div>';
}

// ---- the status bar: CM.stripSet(slot, content), slot 'counts' | 'readout' | 'route' | 'ask' (the assistant's line: the seam brief's fourth slot, named so the export scan passes it) ----
CM.stripSet = function (slot, content) {
  if (!Object.prototype.hasOwnProperty.call(STRIP, slot)) { if (!STRIP_UNKNOWN.includes(String(slot))) STRIP_UNKNOWN.push(String(slot)); console.warn('CM.stripSet: no slot named ' + String(slot)); return; }
  STRIP[slot] = content == null ? null : content;
  if (slot === 'counts') { CHROME.countsSig = ''; chromeCounts(); }
  else if (slot === 'route') chromeRoute();
  else if (slot === 'ask') chromeAskBar();
  else { CHROME.readoutKey = ''; chromeReadout(); }
};
function chromePut(host, content) {
  if (!host) return;
  if (chromeIsEl(content)) { if (content.parentNode !== host || host.childNodes.length !== 1) { host.textContent = ''; host.appendChild(content); } }
  else host.textContent = String(content);
}
function chromeCounts() {
  const host = chromeEl('sb-counts'); if (!host) return;
  if (STRIP.counts != null) { if (CHROME.countsSig !== 'strip') { CHROME.countsSig = 'strip'; } chromePut(host, STRIP.counts); return; }
  const c = CM.counts(), sig = JSON.stringify([c.need_you, c.answer, c.review, c.decisions, c.problems]); if (sig === CHROME.countsSig) return; CHROME.countsSig = sig;
  const col = { need_you: 'amber', decisions: 'amber', problems: 'red' };
  host.innerHTML = CM.COUNT_NOUNS.map((k) => { const n = Number.isInteger(c[k]) ? c[k] : 0;
    return '<span class="c" data-count="' + k + '"><b class="' + (n ? col[k] : 'zero') + '">' + n + '</b>' + chromeHtmlEsc(CM.countNoun(k, n, c)) + '</span>'; }).join('<i class="dot"></i>');
  host.setAttribute('aria-label', CM.countLine(c) + '. Open For you');
}
// what the readout says about one thing on the map (the SF2 strip rule: hovering rewrites the strip, nothing floats)
function chromeReadoutFor(u) {
  if (!u) return null;
  const B = (s, cls) => '<b' + (cls ? ' class="' + cls + '"' : '') + '>' + chromeHtmlEsc(s) + '</b>', E = chromeHtmlEsc, J = (a) => a.filter(Boolean).join(' · ');
  if (u === BOARD) { const c = CM.counts(), od = c.oldest_decision || {};
    return B(CM.word('this machine')) + ' · ' + E(J([chromeNum((W.summary || {}).areas != null ? W.summary.areas : MOONS.length) + ' planets', c.decisions ? c.decisions + ' ' + CM.countNoun('decisions', c.decisions) + (Number.isInteger(od.days) ? ', oldest ' + CM.ageWords(od.days) : '') : '', 'click for every planet'])); }
  if (u.kind === 'area') {
    const m = MOONS.find((x) => x.uid === u.uid), n = m ? moonCounts(m) : null, line = n ? moonCountLine(n) : '';
    return B(CM.labelText(u.uid) || u.name) + ' · ' + E(J([chromePurposeName(u.uid), line || 'nothing waits here',
      u.age != null ? 'last change ' + CM.daysAgo(u.age) : '', m && m.allies ? m.allies + ' agent' + (m.allies > 1 ? 's' : '') + ' working' : '']));
  }
  if (u.kind === 'task') return B(CM.shortTitle(u.name || '', 60) || 'a task') + ' · ' + E(J([taskWord({ status: u.status, retry_due: u.retry_due }), chromePlaceOf(u.target), u.runner ? 'by ' + agentLine(u.runner, u.model) : '']));
  if (isShip(u)) { const s = chromeShipState(u); return B(chromeAgentName(u)) + ' · ' + B(s[0], s[1] === 'w' ? '' : s[1]) + E(J(['', chromeAgentDoing(u), u.cwd ? 'in ' + chromePlaceOf(u.cwd) : ''])); }
  if (u.kind === 'cron') return B(String(u.name || 'a job')) + ' · ' + E(J(['a job' + (u.area ? ' in ' + shortPath(String((unitByUid(u.area) || {}).path || u.area)) : ''), u.verdict ? CM.verdictWord(u.verdict) : '', typeof u.receipt_age === 'number' ? 'last ran ' + CM.relTime(u.receipt_age) : 'no log yet', u.when || u.schedule ? 'runs ' + String(u.when || u.schedule) : '']));
  if (u.kind === 'dir') return B(String(u.name || '')) + ' · ' + E(J(['folder in ' + String(u.area || '').replace('area:', ''), u.files == null ? '' : chromeNum(u.files) + ' files', u.age != null ? 'last change ' + CM.daysAgo(u.age) : '']));
  if (u.kind === 'machine' || u.kind === 'portal') return B(String(u.name || u.machine || '')) + ' · ' + E(J(['another machine', chromeMachineState(u)[0]]));
  if (u.kind === 'proc') return B(String(u.name || '')) + ' · ' + E(J(['a running program', u.elapsed ? 'for ' + u.elapsed : '']));
  if (u.contact) return E(CM.contactLine(u.contact));
  return B(String(u.name || u.uid || '')) + (u.kind ? ' · ' + E(String(u.kind)) : '');
}
function chromeReadout() {
  const host = chromeEl('sb-readout'); if (!host) return;
  let h = null, key = '';
  const hv = hover && VIEW === 'map' ? hover : null;
  if (hv) { key = 'h:' + (hv === BOARD ? 'star' : hv.uid) + ':' + (hv.status || '') + ':' + Math.floor(Date.now() / 4000); if (key !== CHROME.readoutKey) h = chromeReadoutFor(hv); }
  else if (STRIP.readout != null) { key = 'o:' + (chromeIsEl(STRIP.readout) ? 'el' : String(STRIP.readout)); if (key !== CHROME.readoutKey) { CHROME.readoutKey = key; chromePut(host, STRIP.readout); CHROME.readout = host.textContent; } return; }
  else { const ou = openUnit && openUnit.kind === 'area' && RAIL.card && RAIL.card.id === 'panel' ? unitByUid(openUnit.uid) : null;
    if (ou) { key = 'f:' + ou.uid + ':' + Math.floor(Date.now() / 4000); if (key !== CHROME.readoutKey) h = chromeReadoutFor(ou); }
    else { const line = chromeUpdatedLine(); key = 'b:' + line; if (key !== CHROME.readoutKey) h = chromeHtmlEsc(line); } }
  if (key === CHROME.readoutKey) return;
  CHROME.readoutKey = key; host.innerHTML = h || ''; CHROME.readout = host.textContent;
}
function chromeRoute() {
  const r = chromeEl('sb-route'), o = chromeEl('sb-readout'); if (!r || !o) return;
  if (STRIP.route == null) { r.hidden = true; r.textContent = ''; o.hidden = false; return; }
  chromePut(r, STRIP.route); r.hidden = false; o.hidden = true;
}
// the assistant as one line in the status bar: #mate moves into the slot on a desktop and back on a phone
function chromeAskBar() {
  const host = chromeEl('sb-ask'), mate = chromeEl('mate'), wrap = chromeEl('wrap'); if (!host || !mate || !wrap) return;
  if (STRIP.ask != null) { if (mate.parentNode === host) wrap.appendChild(mate); chromePut(host, STRIP.ask); return; }
  if (chromeOn() && CHROME.on) { if (mate.parentNode !== host) { host.textContent = ''; host.appendChild(mate); } }
  else if (mate.parentNode !== wrap) { const show = chromeEl('mateshow'); wrap.insertBefore(mate, show && show.parentNode === wrap ? show : null); }
}
function chromeMateOpen(on) {
  const m = chromeEl('mate'); if (!m) return;
  m.classList.toggle('open', !!on);
  if (on) { const nh = m.querySelector('.nh'); if (nh && !chromeEl('mateclose')) { const b = document.createElement('button'); b.type = 'button'; b.id = 'mateclose'; b.textContent = 'Close'; b.addEventListener('click', () => chromeMateOpen(false)); nh.appendChild(b); }
  }
}

// ---- Fly in 3D: the status bar's button and the planet card's (CM.enter3d is the 3D lane's) ----
function chromeFly(target) {
  const t = target || (typeof CM.focusUid === 'function' && CM.focusUid()) || (openUnit && openUnit.kind === 'area' ? openUnit.uid : null);
  CM.enter3d(t ? { target: t } : {});
}
// "Press I to fly in 3D": once a session, after 3 s of keyboard flight on the map (the entry is findable, never automatic)
function chromePressKey(e, down) {
  if (!CHROME.on || !chromeOn()) return;
  const k = String(e.key || '').toLowerCase(); if (!CHROME_FLIGHT.includes(k)) return;
  if (down) {
    if (typing() || modKey(e) || VIEW !== 'map' || CM.mode3d()) return;
    if (!PRESSI.down.size) PRESSI.since = performance.now();
    PRESSI.down.add(k);
    if (!e.repeat && PRESSI.shownAt) chromePressHide();   // a new press after it showed: the user read it and flies on
  } else { chromePressTick(); PRESSI.down.delete(k); if (!PRESSI.down.size) PRESSI.since = 0; }   // a held key that ends past 3 s counts too
}
function chromePressTick() {
  if (PRESSI.shownAt && performance.now() - PRESSI.shownAt > 6000) chromePressHide();
  if (PRESSI.seen || !PRESSI.since || CM.mode3d() || VIEW !== 'map') return;
  if (performance.now() - PRESSI.since >= 3000) {
    PRESSI.seen = true; try { sessionStorage.setItem('cm.pressI', '1'); } catch (e) {}
    const el = chromeEl('pressi'); if (el) { el.hidden = false; PRESSI.shownAt = performance.now(); }
  }
}
function chromePressHide() { const el = chromeEl('pressi'); if (el) el.hidden = true; PRESSI.shownAt = 0; }

// ---- the machines chip (other machines are not drawn on the 2D map) and the legend chip ----
function chromeMachineState(u) {
  const st = String((u && u.status) || '').toUpperCase();
  if (/^(LIVE|UP|ONLINE|OK|WARM|IDLE|WORKING|SYNCED)$/.test(st)) return ['answers', 'rgb(74,222,128)'];
  if (/^(DOWN|ERROR|DEAD|BROKEN)$/.test(st)) return ['does not answer', 'rgb(255,90,95)'];
  return ['asleep', 'rgb(107,118,132)'];
}
// another machine: a small galaxy (the planet kinds module's machineArt) when it is there, else a plain disc in its state's colour
function chromeMachineIcon(u, size) {
  try { if (typeof machineArt === 'function') { const n = Math.round(size / 2), a = machineArt(n, seedOf(String(u.name || '')), KIND_STATUS.sun); if (a && a.toDataURL) return '<img src="' + a.toDataURL() + '" alt="">'; } } catch (e) {}
  const c = document.createElement('canvas'); c.width = c.height = size; const g = c.getContext('2d'); g.fillStyle = chromeMachineState(u)[1]; g.beginPath(); g.arc(size / 2, size / 2, size * 0.3, 0, Math.PI * 2); g.fill();
  return '<img src="' + c.toDataURL() + '" alt="">';
}
// the other machines: this one is the sun ("This machine"), never a chip of its own
function chromeMachines() { const me = String(W.name || '').toLowerCase(); return units.filter((u) => u && u.kind === 'machine' && !u.remote && String(u.name || '').toLowerCase() !== me); }
function chromeMachinesRender() {
  const el = chromeEl('machines'); if (!el) return;
  const list = chromeMachines(), sig = list.map((u) => u.uid + ':' + u.status).join(',');
  if (sig === CHROME.machinesSig) return; CHROME.machinesSig = sig;
  el.hidden = !list.length || !chromeOn();
  el.innerHTML = list.length ? '<span class="h">Machines</span>' + list.map((u) => { const s = chromeMachineState(u);
    return '<button type="button" data-uid="' + attr(u.uid) + '" title="' + attr(String(u.name) + ': ' + s[0]) + '"><i class="sw" data-c="' + attr(s[1]) + '"></i>' + chromeHtmlEsc(String(u.name)) + (s[0] === 'answers' ? '' : ' · ' + chromeHtmlEsc(s[0])) + '</button>'; }).join('') : '';
  el.querySelectorAll('i.sw[data-c]').forEach((i) => { i.style.background = i.getAttribute('data-c'); });
}
function chromeLegendOff() { try { return localStorage.getItem('legendChip') === 'off'; } catch (e) { return false; } }
function chromeLegendRender() {
  const el = chromeEl('legendchip'); if (!el) return;
  const off = chromeLegendOff() || !chromeOn() || typeof CM.legendChip !== 'function', sig = (off ? 'off' : 'on') + '|' + THEME_NAME;
  if (sig === CHROME.legendSig) return; CHROME.legendSig = sig;
  el.hidden = off;
  if (!off) {
    const L = CM.legendChip(), sw = (c) => '<i class="sw" data-c="' + c + '"></i>';
    el.innerHTML = '<div class="l1">' + L.items.map((it) => { let src = ''; try { src = it.icon().toDataURL(); } catch (e) {} return '<span>' + (src ? '<img src="' + src + '" alt="">' : '') + chromeHtmlEsc(it.name) + '</span>'; }).join('') + '</div>' +
      '<div class="l2"><span>' + sw('rgb(255,90,95)') + 'has a problem</span><span>' + sw('rgb(255,181,71)') + 'waiting for you (the number says how many)</span><span><i class="g c">◜</i> an AI agent is working</span><span><i class="g f">❄</i> no changes in 30+ days</span><span><i class="g h"></i>mostly temporary files</span></div>' +
      '<button type="button" class="lx" aria-label="Hide the legend (? › Settings brings it back)" title="Hide (? › Settings brings it back)">×</button>';
    el.querySelectorAll('i.sw[data-c]').forEach((i) => { i.style.background = i.getAttribute('data-c'); });
    el.querySelectorAll('i.g.c').forEach((i) => { i.style.color = 'rgb(111,183,255)'; });
    el.querySelectorAll('i.g.f').forEach((i) => { i.style.color = 'rgb(216,244,255)'; });
    el.querySelectorAll('i.g.h').forEach((i) => { i.style.color = 'rgb(176,190,210)'; });
  }
  chromeChipStack();
}
// the crumb (top left of the map) while a planet is open or a workflow is selected: "Esc · All planets › ops"; Esc or a click leaves.
// In that view the legend and machines chips step aside (the comps' focus view shows the planet alone)
function chromeFocusName() {
  if (RAIL.card && RAIL.card.id === 'panel' && openUnit && (openUnit.kind === 'area' || openUnit.kind === 'dir')) return String(openUnit.name || '');
  const sel = typeof CM.selectedWorkflow === 'function' ? CM.selectedWorkflow() : null;
  if (sel) { const w = CM.wfRows().find((x) => x.id === String(sel)); if (w) return 'Workflow · ' + CM.shortTitle(w.title, 40); }
  return '';
}
function chromeCrumb() {
  const el = chromeEl('crumb'); if (!el) return;
  const name = chromeOn() && VIEW === 'map' && !CM.mode3d() ? chromeFocusName() : '';
  if (el.getAttribute('data-name') !== name) { const was = el.getAttribute('data-name'); el.setAttribute('data-name', name);
    el.innerHTML = name ? '<kbd>Esc</kbd> All planets <span class="sep">›</span> <b>' + chromeHtmlEsc(name) + '</b>' : ''; el.setAttribute('aria-label', name ? 'Back to all planets' : '');
    if (was != null && typeof CM.say === 'function') CM.say(name ? name + ' is open' : 'All planets'); }   // a focus change is said, never the first paint
  el.hidden = !name;
  const w = chromeEl('wrap'); if (w) w.classList.toggle('in-focus', !!name);
}
// the machines chip sits on top of the legend chip
function chromeChipStack() {
  const w = chromeEl('wrap'), lg = chromeEl('legendchip'); if (!w) return;
  const q = chromeEl('quests'); w.classList.toggle('board-open', !!q && q.classList.contains('on'));
  const h = lg && !lg.hidden ? lg.offsetHeight + 8 : 0; w.style.setProperty('--legendh', h + 'px');
}
function chromeChipClick(e) {
  const t = e.target && e.target.closest ? e.target : null; if (!t) return;
  if (t.closest('#legendchip .lx')) { try { localStorage.setItem('legendChip', 'off'); } catch (err) {} chromeLegendRender(); return; }
  const b = t.closest('#machines [data-uid]'); if (b) { const u = unitByUid(b.getAttribute('data-uid')); if (u) { setView('map'); show(u); } }
}

// ---- the "?" sheet: what you see, keys, settings, set up (CM.openHelp; kinds.js's '?' and L step aside for it) ----
const HELP_TABS = [['see', 'What you see'], ['keys', 'Keys'], ['settings', 'Settings'], ['setup', 'Set up']];
function chromeSettingRows() {
  const onOff = [{ value: 'on', label: 'On' }, { value: 'off', label: 'Off' }], rows = [];
  if (typeof THEME_NAMES !== 'undefined' && THEME_NAMES.length > 1) rows.push({ key: 'theme', group: 'Map', label: 'Look (V switches)', options: THEME_NAMES.map((n) => ({ value: n, label: n })), get: () => THEME_NAME, set: (v) => { if (!GB) applyTheme(v, true); } });
  rows.push({ key: 'sky', group: 'Map', label: 'Show on the map (Y)', options: [{ value: '1', label: 'Only what needs me' }, { value: '0', label: 'Everything' }], get: () => (SKY ? '1' : '0'), set: (v) => setSky(v === '1') });
  rows.push({ key: 'flows', group: 'Map', label: 'Folder links (F)', options: onOff, get: () => (FLOWS ? 'on' : 'off'), set: (v) => { FLOWS = v === 'on'; } });
  rows.push({ key: 'legend-chip', group: 'Map', label: 'Legend on the map', options: onOff, get: () => (chromeLegendOff() ? 'off' : 'on'), set: (v) => { try { localStorage.setItem('legendChip', v === 'off' ? 'off' : 'on'); } catch (e) {} CHROME.legendSig = ''; chromeLegendRender(); } });
  rows.push({ key: 'rail', group: 'Map', label: 'Side panel ( ] )', options: [{ value: 'on', label: 'Open' }, { value: 'off', label: 'Closed' }], get: () => (railOpen() ? 'on' : 'off'), set: (v) => railSetOpen(v === 'on', true) });
  if (typeof setZoom === 'function') {   // the debug zoom (P6): the whole map, or a fixed step; - and = step it, 0 fits
    const steps = [['fit', 'The whole map'], ['0.5', '50%'], ['0.75', '75%'], ['1', '100%'], ['1.5', '150%'], ['2', '200%']];
    rows.push({ key: 'zoom', group: 'Map', label: 'Zoom (- and =, 0 fits)', options: steps.map((x) => ({ value: x[0], label: x[1] })),
      get: () => (typeof HOME !== 'undefined' && HOME && HOME.on ? 'fit' : String(steps.slice(1).map((x) => Number(x[0])).reduce((a, b) => (Math.abs(b - ZOOM) < Math.abs(a - ZOOM) ? b : a)))),
      set: (v) => { if (v === 'fit') { if (typeof zoomHome === 'function') zoomHome(); } else setZoom(Number(v)); } });
  }
  if (cmSfxOk() && typeof window.cmSfx.enabled === 'function') rows.push({ key: 'sound', group: 'Sound', label: 'Sound (X)', options: onOff, get: () => (window.cmSfx.enabled() ? 'on' : 'off'), set: (v) => { if ((v === 'on') !== !!window.cmSfx.enabled()) toggleSound(); } });
  if (cmSfxOk() && typeof window.cmSfx.voiceOut === 'function') rows.push({ key: 'voice', group: 'Sound', label: 'Read the calls aloud (Shift+X)', options: onOff, get: () => (window.cmSfx.voiceOut() ? 'on' : 'off'), set: (v) => { if ((v === 'on') !== !!window.cmSfx.voiceOut()) toggleVoiceOut(); } });
  const seen = new Set(rows.map((r) => r.key));
  (Array.isArray(CM.settingsExtra) ? CM.settingsExtra : []).forEach((r) => {
    if (!r || typeof r !== 'object' || typeof r.key !== 'string' || !r.key || seen.has(r.key) || typeof r.get !== 'function' || typeof r.set !== 'function') return;
    let opts = null; try { opts = r.options; } catch (e) { opts = null; }
    if (!Array.isArray(opts) || !opts.length) return;
    seen.add(r.key); rows.push(r);
  });
  return rows;
}
function chromeSettingsHtml() {
  const rows = chromeSettingRows(), groups = [];
  rows.forEach((r) => { const g = String(r.group || 'More'); if (!groups.includes(g)) groups.push(g); });
  const order = ['Map', 'Sound', '3D view', 'Workflows']; groups.sort((a, b) => (order.indexOf(a) < 0 ? 9 : order.indexOf(a)) - (order.indexOf(b) < 0 ? 9 : order.indexOf(b)));
  return groups.map((g) => '<div class="grp">' + chromeHtmlEsc(g) + '</div>' + rows.filter((r) => String(r.group || 'More') === g).map((r) => {
    let cur = ''; try { cur = String(r.get()); } catch (e) { cur = ''; }
    const id = 'set-' + String(r.key).replace(/[^\w-]/g, '_'), opts = r.options;
    return '<div class="srow" data-setting="' + attr(r.key) + '"><label for="' + attr(id) + '">' + chromeHtmlEsc(r.label || r.key) + '</label><select id="' + attr(id) + '" data-key="' + attr(r.key) + '">' +
      opts.map((o) => { const v = String(o && typeof o === 'object' ? o.value : o), l = String(o && typeof o === 'object' ? (o.label != null ? o.label : o.value) : o); return '<option value="' + attr(v) + '"' + (v === cur ? ' selected' : '') + '>' + chromeHtmlEsc(l) + '</option>'; }).join('') + '</select></div>';
  }).join('')).join('');
}
function chromeKeysHtml() {
  const K = (keys, words) => '<div class="krow"><span>' + chromeHtmlEsc(words) + '</span>' + keys.map((k) => '<kbd>' + chromeHtmlEsc(k) + '</kbd>').join('') + '</div>';
  const a = [K(['W', 'A', 'S', 'D'], 'Fly the ship (or the arrows)'), K(['Shift'], 'Go faster while flying'), K(['I'], 'Fly in 3D, and back'), K(['E'], 'Open what you are next to'),
    K(['Esc'], 'Close, then go back'), K(['C'], 'Center on your ship'), K(['0'], 'Show the whole map'), K(['-', '='], 'Zoom out and in (or the wheel)'), K(['J'], 'Next item for me'), K(['O'], 'Open it')];
  const b = [K(['T'], 'Today'), K(['M'], 'Back to the map'), K(['N'], 'New task'), K(['K'], 'Agents'), K(['Q'], 'Decisions'), K(['/'], 'Ask ' + mateName()),
    K(['Y'], 'Only what needs me, or everything'), K(['F'], 'Folder links'), K(['V'], 'Next look'), K([']'], 'Side panel'), K(['H'], 'More stats'), K(['X'], 'Sound'), K(['?'], 'This sheet')];
  return '<div class="cols"><div>' + a.join('') + '</div><div>' + b.join('') + '</div></div>';
}
function chromeSeeHtml() {
  let grid = '';
  try {
    grid = '<div class="lgrid">' + PURPOSE_KEYS.map((k) => { const P = PURPOSE[k]; let src = ''; try { src = purposeIcon(k, 40).toDataURL(); } catch (e) {}
      return '<div>' + (src ? '<img src="' + src + '" alt="">' : '<span></span>') + '<b>' + chromeHtmlEsc(P.name) + '</b><small>' + chromeHtmlEsc(P.look + '. ' + P.rule.charAt(0).toUpperCase() + P.rule.slice(1) + '.') + '</small></div>'; }).join('') + '</div>';
  } catch (e) { grid = ''; }
  const sw = (c) => '<i class="sw" data-c="' + c + '"></i>';
  return '<button type="button" class="cmbtn" data-act="guide">Take the quick tour</button><h3>Planets are your folders</h3><p>Each top folder is a planet around the sun, which is this machine. Its body says what the folder holds; you can change that from its card.</p>' + grid +
    '<h3>Colour means state</h3><div class="cols"><div class="krow"><span>' + sw('rgb(255,90,95)') + 'Red: something is broken</span></div><div class="krow"><span>' + sw('rgb(255,181,71)') + 'Amber: it waits for your answer, review or decision</span></div>' +
    '<div class="krow"><span>' + sw('rgb(111,183,255)') + 'Blue: an agent is working there</span></div><div class="krow"><span>' + sw('rgb(74,222,128)') + 'Green: done</span></div>' +
    '<div class="krow"><span>❄ Frosted, faded: nothing written in 30 days or more</span></div><div class="krow"><span>┅ Dashed shell: most of it can be rebuilt (caches)</span></div></div>' +
    '<h3>Ships</h3><p>The white ships are your agents: docked at the planet they work in, with a blue ring that fills as they go; idle ones wait by the main session near the sun. Problems circling a planet show when you open it.</p>' +
    '<h3>Where to look</h3><p>The side panel lists what needs you, oldest first. The bar under the map counts it; point at anything on the map and the bar says what it is.</p>';
}
function chromeSetupHtml() {
  const rs = (W.task_runners || []).filter((r) => r && r.role !== 'check'), chk = (W.task_runners || []).filter((r) => r && r.role === 'check');
  const wm = W.wingmate || {}, models = (typeof MATE !== 'undefined' && MATE.models) || [];
  let h = '<h3>Connect an agent</h3>' + (rs.length
    ? '<div class="card">Agents that take tasks: <b>' + chromeHtmlEsc(rs.map((r) => r.label || r.name).join(' · ')) + '</b>' + (chk.length ? '<br>Second opinions: ' + chromeHtmlEsc(chk.map((r) => r.label || r.name).join(' · ')) : '') + '</div>'
    : '<div class="card"><b>No agent yet.</b> Install Claude Code or Codex on this computer and the map finds it by itself; or name one under "runners" in container.json. Then New task (N) can send it work.</div>');
  h += '<h3>Your phone</h3><div class="card" id="setupphone">' + (DESK ? 'Pair a phone to see this map and answer from it.<div class="mrow"><button type="button" class="cmbtn" data-act="pair">Pair a phone</button></div><div class="msg" id="setupphonemsg"></div>' : 'Pairing happens on the computer that runs the map.') + '</div>';
  h += '<h3>' + chromeHtmlEsc(mateName()) + ', your assistant</h3><div class="card">' + chromeHtmlEsc(wm.available ? 'Ask in the bar under the map: where something is, what a planet means, what to do next.' : 'It explains what is on the map. Set a model for it to answer questions too.') +
    (models.length ? '<details><summary>Details: the model that answers</summary>' + models.map((m) => '<label class="mrow"><input type="radio" name="matemodel" value="' + attr(m.name) + '"' + (m.name === MATE.model ? ' checked' : '') + '> ' + chromeHtmlEsc(m.label || m.name) + '</label>').join('') + '</details>' : '') + '</div>';
  return h;
}
CM.openHelp = function (tab) {
  const el = chromeEl('helpsheet'); if (!el) return;
  if (tab) CHROME.help = HELP_TABS.some((x) => x[0] === tab) ? tab : 'see';
  const body = CHROME.help === 'keys' ? chromeKeysHtml() : CHROME.help === 'settings' ? chromeSettingsHtml() : CHROME.help === 'setup' ? chromeSetupHtml() : chromeSeeHtml();
  el.innerHTML = '<div class="hh" role="tablist">' + HELP_TABS.map(([k, n]) => '<button type="button" role="tab" data-htab="' + k + '" aria-selected="' + (CHROME.help === k) + '">' + n + '</button>').join('') +
    '<button type="button" class="hx" data-act="close">Close <kbd>Esc</kbd></button></div><div class="hb" data-help="' + CHROME.help + '">' + body + '</div>';
  el.querySelectorAll('i.sw[data-c]').forEach((i) => { i.style.background = i.getAttribute('data-c'); });
  if (el.hidden) { CHROME.helpFrom = document.activeElement; chromeInert(true); }
  el.hidden = false;
  const hb = el.querySelector('.hb'); if (hb) { hb.tabIndex = -1; try { hb.focus({ preventScroll: true }); } catch (e) {} }
};
CM.closeHelp = function () {
  const el = chromeEl('helpsheet'); if (!el || el.hidden) return;
  el.hidden = true; chromeInert(false);
  const f = CHROME.helpFrom; CHROME.helpFrom = null; if (f && f.isConnected && typeof f.focus === 'function' && f !== document.body) { try { f.focus({ preventScroll: true }); } catch (e) {} }
};
// the sheet is modal: everything else in the page is inert while it is open, and Tab stays inside it
function chromeInert(on) {
  const w = chromeEl('wrap'), hs = chromeEl('helpsheet'); if (!w) return;
  if (on) { CHROME.inert = Array.from(w.children).filter((c) => c !== hs && c.id !== 'a11ylive' && !c.inert); CHROME.inert.forEach((c) => { c.inert = true; }); }
  else { CHROME.inert.forEach((c) => { c.inert = false; }); CHROME.inert = []; }
}
function chromeHelpFocusables() {
  const el = chromeEl('helpsheet'); if (!el) return [];
  return Array.from(el.querySelectorAll('button,select,input,a[href],textarea,[tabindex]:not([tabindex="-1"])')).filter((e) => !e.disabled && !e.closest('[hidden]') && !!chromeBox(e));
}
function chromeHelpKeys(e) {
  if (e.key !== 'Tab' || !chromeHelpOpen()) return;
  const f = chromeHelpFocusables(); if (!f.length) { e.preventDefault(); return; }
  const i = f.indexOf(document.activeElement), next = e.shiftKey ? (i <= 0 ? f[f.length - 1] : f[i - 1]) : (i < 0 || i >= f.length - 1 ? f[0] : f[i + 1]);
  e.preventDefault(); next.focus();
}
function chromeHelpOpen() { const el = chromeEl('helpsheet'); return !!el && !el.hidden; }
function chromeHelpClick(e) {
  const t = e.target && e.target.closest ? e.target : null; if (!t) return;
  const tab = t.closest('[data-htab]'); if (tab) { CM.openHelp(tab.getAttribute('data-htab')); return; }
  const a = t.closest('[data-act]'); if (!a) return;
  const k = a.getAttribute('data-act');
  if (k === 'guide') { CM.closeHelp(); CM.startGuide(); }
  else if (k === 'close') CM.closeHelp();
  else if (k === 'pair') chromePair(a);
}
function chromeHelpChange(e) {
  const s = e.target && e.target.closest && e.target.closest('select[data-key]');
  if (s) { const r = chromeSettingRows().find((x) => x.key === s.getAttribute('data-key')); if (r) { try { r.set(s.value); } catch (err) { console.error('setting', err); } } return; }
  const m = e.target && e.target.name === 'matemodel' ? e.target : null;
  if (m && typeof MATE !== 'undefined') { MATE.model = m.value; try { localStorage.setItem('mateModel', m.value); } catch (err) {} if (typeof matePillDraw === 'function') matePillDraw(); }
}
async function chromePair(btn) {
  const out = chromeEl('setupphonemsg'); if (!out) return;
  btn.disabled = true; out.className = 'msg'; out.textContent = 'Asking for a code…';
  try {
    const j = await api('/phone/pair', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    out.innerHTML = 'Code <span class="code">' + chromeHtmlEsc(String(j.code).replace(/(.{4})(.{4})/, '$1 $2')) + '</span><br>Open ' + chromeHtmlEsc(j.url) + ' on the phone. One use, ' + Math.round((j.expires_in || 600) / 60) + ' minutes.';
  } catch (err) { out.className = 'msg err'; out.textContent = CM.errorLine(Number(err && err.status) || 0); }
  btn.disabled = false;
}

// ---- __chromeAudit: what the chrome is, read back from the page ----
function chromeBox(el) { if (!el || !el.getClientRects || !el.getClientRects().length) return null; const r = el.getBoundingClientRect(); if (!(r.width > 1 && r.height > 1)) return null;
  const cs = getComputedStyle(el); if (cs.visibility === 'hidden' || Number(cs.opacity) < 0.05) return null; return r; }
CM.panelSync = () => chromePanelSync();
const CHROME_IDS = ['rail', 'statusbar', 'machines', 'legendchip', 'pressi', 'crumb', 'helpsheet', 'dash', 'hud', 'comms', 'ticker', 'nums', 'topbar', 'taskbar', 'quick', 'theme', 'keyline', 'help', 'legend',
  'mate', 'mateshow', 'panel', 'side', 'composer', 'radio', 'permits', 'gohome', 'quests', 'prompter', 'today', 'tacrail', 'tacfoot', 'staticbanner', 'demobanner'];
function chromeMapFree() {
  const st = STAGE.getBoundingClientRect(); let x0 = st.left, y0 = st.top, x1 = st.right, y1 = st.bottom;
  ['rail', 'statusbar', 'panel', 'side', 'dash', 'today'].forEach((id) => {
    const r = chromeBox(chromeEl(id)); if (!r) return;
    if (r.right <= x0 || r.left >= x1 || r.bottom <= y0 || r.top >= y1) return;
    if (r.height >= 0.8 * (y1 - y0)) { if (r.left > (x0 + x1) / 2) x1 = Math.min(x1, r.left); else x0 = Math.max(x0, r.right); }
    else if (r.width >= 0.8 * (x1 - x0)) { if (r.top > (y0 + y1) / 2) y1 = Math.min(y1, r.top); else y0 = Math.max(y0, r.bottom); }
  });
  return { x: Math.round(x0), y: Math.round(y0), w: Math.round(Math.max(0, x1 - x0)), h: Math.round(Math.max(0, y1 - y0)) };
}
// WCAG contrast of every visible piece of text in the chrome against what is under it (its backgrounds composited, a gradient by its
// lightest and darkest stop, the map's base under a glass chip)
function chromeRgba(s) { const m = /rgba?\(([^)]+)\)/.exec(String(s || '')); if (!m) return null; const p = m[1].split(/[ ,/]+/).filter(Boolean).map(Number); return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1]; }
function chromeLum(c) { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); }
function chromeOver(top, under) { const a = top[3]; return [top[0] * a + under[0] * (1 - a), top[1] * a + under[1] * (1 - a), top[2] * a + under[2] * (1 - a), 1]; }
function chromeBgs(el) {
  // the layers under el, nearest first: each a list of candidate colours (one for a flat colour, the stops of a gradient)
  const layers = [];
  for (let e = el; e && e.nodeType === 1; e = e.parentElement) {
    const cs = getComputedStyle(e), bc = chromeRgba(cs.backgroundColor), img = cs.backgroundImage || '';
    if (img && img !== 'none' && /gradient/.test(img)) { const stops = (img.match(/rgba?\([^)]+\)/g) || []).map(chromeRgba).filter(Boolean); if (stops.length) layers.push(stops); }
    if (bc && bc[3] > 0) layers.push([bc]);
    if (bc && bc[3] >= 1) break;
  }
  return layers;
}
function chromeContrastOf(el, colour) {
  const cs = getComputedStyle(el), fg0 = chromeRgba(colour || cs.color); if (!fg0) return null;
  let op = 1; for (let e = el; e && e.nodeType === 1; e = e.parentElement) op *= Number(getComputedStyle(e).opacity);
  const layers = chromeBgs(el).reverse();   // farthest first
  let cands = [[5, 7, 13, 1]];
  layers.forEach((stops) => { const next = []; cands.forEach((u) => stops.forEach((s) => next.push(chromeOver(s, u)))); cands = next.length > 16 ? [next.reduce((a, b) => (chromeLum(a) > chromeLum(b) ? a : b)), next.reduce((a, b) => (chromeLum(a) < chromeLum(b) ? a : b))] : next; });
  let worst = 99;
  cands.forEach((bg) => { const fg = chromeOver([fg0[0], fg0[1], fg0[2], fg0[3] * op], bg), l1 = chromeLum(fg), l2 = chromeLum(bg); worst = Math.min(worst, (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05)); });
  return worst;
}
function chromeContrast() {
  let min = 99, at = null, n = 0;
  ['rail', 'statusbar', 'machines', 'legendchip', 'pressi', 'crumb', 'helpsheet'].forEach((id) => {
    const root = chromeEl(id); if (!chromeBox(root)) return;
    [root].concat(Array.from(root.querySelectorAll('*'))).forEach((el) => {   // the root's own words too (#pressi, #crumb)
      const ph = /^(INPUT|TEXTAREA)$/.test(el.tagName) && !!el.placeholder && !el.value;   // a placeholder is text the owner reads
      if (!ph && !Array.from(el.childNodes).some((c) => c.nodeType === 3 && c.textContent.trim())) return;
      if (!chromeBox(el) || el.closest('[hidden]') || el.closest('details:not([open])>*:not(summary)')) return;
      if (el.tagName === 'OPTION' || el.closest('svg') || el.disabled || el.closest(':disabled')) return;   // a greyed control is no text to read
      const r = ph ? chromeContrastOf(el, getComputedStyle(el, '::placeholder').color) : chromeContrastOf(el); if (r == null) return; n++;
      if (r < min) { min = r; at = { id: el.id || (el.className && String(el.className)) || el.tagName.toLowerCase(), text: (ph ? el.placeholder : el.textContent).trim().slice(0, 40), ratio: Math.round(r * 100) / 100 }; }
    });
  });
  return { min: n ? Math.round(min * 100) / 100 : 0, worst: at, texts: n };
}
window.__chromeAudit = function () {
  const visible = CHROME_IDS.filter((id) => !!chromeBox(chromeEl(id)));
  const tops = ['rail', 'statusbar', 'machines', 'legendchip', 'pressi', 'crumb', 'radio', 'permits', 'gohome', 'staticbanner', 'demobanner'].map((id) => ({ id: id, r: chromeBox(chromeEl(id)) })).filter((x) => x.r);
  let overlaps = 0; const pairs = [];
  for (let i = 0; i < tops.length; i++) for (let j = i + 1; j < tops.length; j++) { const a = tops[i].r, b = tops[j].r;
    if (a.left < b.right - 1 && b.left < a.right - 1 && a.top < b.bottom - 1 && b.top < a.bottom - 1) { overlaps++; pairs.push(tops[i].id + '/' + tops[j].id); } }
  const S = chromeEl('side'), cf = chromeEl('cfolder');
  let pa = null; if (MOB && typeof window.__phoneAudit === 'function') { try { pa = window.__phoneAudit(); } catch (e) { pa = null; } }
  if (pa && Number.isFinite(pa.overlaps)) { overlaps += pa.overlaps; (pa.overlapPairs || []).forEach((x) => pairs.push(x)); }
  return { mapFree: chromeMapFree(), visible: visible, rail: { tab: RAIL.tab, content: RAIL.card ? 'card' : 'tabs', card: RAIL.card ? (RAIL.card.id || 'el') : null, open: railOpen(), back: RAIL.opts ? RAIL.opts.back : null },
    contrast: chromeContrast(), settings: { rows: chromeSettingRows().map((r) => r.key), shown: Array.from(document.querySelectorAll('#helpsheet [data-setting]')).map((e) => e.getAttribute('data-setting')) },
    readout: (() => { const hv = hover && VIEW === 'map' ? hover : null; if (hv) { const d = document.createElement('div'); d.innerHTML = chromeReadoutFor(hv) || ''; return d.textContent; }
      return STRIP.readout != null ? (chromeIsEl(STRIP.readout) ? STRIP.readout.textContent : String(STRIP.readout)) : CHROME.readout; })(),
    route: STRIP.route != null, strip: { unknown: STRIP_UNKNOWN.slice() }, machines: chromeMachines().map((u) => String(u.name)),
    composer: pa && pa.composer ? pa.composer : { sheet: composerOpen() && !!S && (RAIL.card === S || MOB), target: cf ? cf.value : '' },
    overlaps: overlaps, overlapPairs: pairs, counts: (chromeEl('sb-counts') || {}).textContent || '', pressI: { shown: !(chromeEl('pressi') || {}).hidden, seen: PRESSI.seen },
    help: chromeHelpOpen() ? CHROME.help : null, ready: !!CM.chromeReady };
};

// ---- start-up ----
// the 3D view covers the page (its canvas is fixed over everything): the chrome waits, so its work never takes a frame from it
function chrome3dBusy() { return CM.mode3d() || (typeof P3 !== 'undefined' && P3 && P3.mode === 'entering'); }
function chromeTick() {
  if (!CHROME.on || chrome3dBusy()) return;
  const on = chromeOn();
  chromeAskBar();
  if (!on) { if (RAIL.card && RAIL.card.id === 'panel') CM.railCard(null); return; }
  chromePanelSync();
  if (RAIL.card && RAIL.opts && typeof RAIL.opts.open === 'function') { let still = true; try { still = !!RAIL.opts.open(); } catch (e) { still = false; } if (!still) CM.railCard(null); }
  chromeCounts(); chromeReadout(); chromePressTick(); chromeCrumb();
}
function chromeSlowTick() {
  if (!CHROME.on || chrome3dBusy()) return;
  if (chromeOn()) { railRender(false); chromeMachinesRender(); chromeLegendRender(); chromeChipStack(); chromePurposeRefresh(); chromeSayWorkflows(); }
  else { const m = chromeEl('machines'), l = chromeEl('legendchip'); if (m) m.hidden = true; if (l) l.hidden = true; CHROME.machinesSig = ''; CHROME.legendSig = ''; }
}
// a workflow's step change is said once through the one live region: "Fix the phone labels: step 2 of 4, Lint, working, at ops";
// the first reading of the list says nothing (the page just opened)
function chromeSayWorkflows() {
  if (typeof CM.wfList !== 'function' || typeof CM.say !== 'function') return;
  const now = new Map();
  (CM.wfList() || []).forEach((w) => {
    if (!w || typeof w !== 'object' || w.id == null) return;
    const steps = Array.isArray(w.steps) ? w.steps : [], i = steps.findIndex((s) => s && /^(working|needs_you|failed)$/.test(String(s.state || ''))), s = i >= 0 ? steps[i] : null;
    const key = s ? i + ':' + String(s.state) : 'w:' + String(w.state || '');
    const title = chromeCap(CM.shortTitle(String(w.title || ''), 48)) || 'A workflow';
    const line = s ? title + ': step ' + (i + 1) + ' of ' + steps.length + ', ' + String(s.label || s.key || '') + ', ' + (CM.stateWord(String(s.state || '')) || String(s.state || '')) + (s.planet_uid ? ', at ' + CM.labelText(s.planet_uid) : '')
      : title + ': ' + (CM.stateWord(String(w.state || '')) || String(w.state || ''));
    now.set(String(w.id), { key: key, line: line });
  });
  if (CHROME.wfSig) now.forEach((v, id) => { const o = CHROME.wfSig.get(id); if (!o || o.key !== v.key) CM.say(v.line); });
  CHROME.wfSig = now;
}
// the purpose row on an open planet card follows a change made from its sheet (the planet kinds module's sheet, the bridge's POST /area/set)
function chromePurposeRefresh() {
  const P = chromeEl('panel'); if (!P || !P.classList.contains('on') || !openUnit || openUnit.kind !== 'area') return;
  const row = P.querySelector('[data-purpose-text]'); if (!row || typeof CM.purposeRow !== 'function') return;
  const now = CM.purposeRow(openUnit.uid).text; if (now && row.getAttribute('data-purpose-text') !== now) { const u = unitByUid(openUnit.uid); if (u) renderCard(u); }
}
CM.onInit.push(() => {
  CHROME.on = true;
  try { const t = sessionStorage.getItem('railTab'); if (RAIL_NAME[t]) RAIL.tab = t; PRESSI.seen = sessionStorage.getItem('cm.pressI') === '1'; } catch (e) {}
  let pref = null; try { pref = localStorage.getItem('rail'); } catch (e) {}
  RAIL.closedPref = pref === 'off' ? true : pref === 'on' ? false : null;
  const w = chromeEl('wrap'); if (w) w.classList.toggle('rail-closed', pref === 'off' || (pref == null && innerWidth < 1280));
  const rail = chromeEl('rail'); if (rail) { rail.addEventListener('click', railClick); rail.addEventListener('keydown', railKeys); }
  const sb = chromeEl('statusbar');
  if (sb) sb.addEventListener('click', (e) => {
    const t = e.target && e.target.closest ? e.target : null; if (!t) return;
    if (t.closest('[data-act=fly3d]')) { chromeFly(null); return; }
    if (t.closest('#sb-help')) { if (chromeHelpOpen()) CM.closeHelp(); else CM.openHelp(); return; }
    if (t.closest('#sb-counts')) { railTab('foryou'); }
  });
  // the planet card's buttons (in the rail; the same card in a phone's sheet)
  const pb = chromeEl('pbody');
  if (pb) pb.addEventListener('click', (e) => {
    const a = e.target && e.target.closest ? e.target.closest('[data-act]') : null; if (!a || !openUnit) return;
    const k = a.getAttribute('data-act');
    if (k === 'fly3d') { chromeFly(a.getAttribute('data-target') || openUnit.uid); return; }
    if (k === 'purpose') { if (typeof CM.openPurposeSheet === 'function') CM.openPurposeSheet(openUnit.uid); return; }
    if (k === 'show') { const u = unitByUid(a.getAttribute('data-uid') || openUnit.uid); if (u) { humanMove(); const ap = approach(u); pilot = { source: 'human', x: ap.x, y: ap.y }; } return; }
    if (k === 'write') { if (typeof TERM !== 'undefined' && TERM.term) { termFocus(); return; }   // the live terminal takes typing as it is
      const d = a.closest('.pc') && a.closest('.pc').querySelector('details.pc-more'); if (d) d.open = true; const ta = chromeEl('sayi'); if (ta) setTimeout(() => ta.focus(), 30); return; }
    if (k === 'task') { const id = a.getAttribute('data-tid'); if (id) openTask(id); return; }
    if (k === 'contact') { const c = chromeContact(a.getAttribute('data-cid')); if (c) openContact(c); return; }
  });
  ['machines', 'legendchip'].forEach((id) => { const el = chromeEl(id); if (el) el.addEventListener('click', chromeChipClick); });
  { const cr = chromeEl('crumb'); if (cr) cr.addEventListener('click', () => { if (RAIL.card && RAIL.card.id === 'panel') closeAll(); else if (typeof CM.selectWorkflow === 'function') CM.selectWorkflow(null); }); }
  const hs = chromeEl('helpsheet'); if (hs) { hs.addEventListener('click', chromeHelpClick); hs.addEventListener('change', chromeHelpChange); hs.addEventListener('keydown', chromeHelpKeys); }
  // the assistant's answers open above its bar when its line takes the keys (P17) and while it talks; Esc or Close puts them away
  const mf = chromeEl('matef'); if (mf) mf.addEventListener('submit', () => { if (chromeOn()) chromeMateOpen(true); }, true);
  const mq = chromeEl('mateq'); if (mq) { mq.addEventListener('keydown', (e) => { if (e.key === 'Escape' && chromeEl('mate').classList.contains('open')) chromeMateOpen(false); });
    mq.addEventListener('focus', () => { if (chromeOn() && CHROME.on) chromeMateOpen(true); }); }
  if (typeof cv !== 'undefined' && cv) cv.addEventListener('pointerdown', () => { const m = chromeEl('mate'); if (m && m.classList.contains('open')) chromeMateOpen(false); });
  // keys: ] the side panel, ? and L the sheet, K the agents, flight for the "Press I" chip
  addEventListener('keydown', (e) => {
    chromePressKey(e, true);
    if (typing() || modKey(e) || e.repeat) return;
    if (e.key === '?') { e.preventDefault(); if (chromeHelpOpen() && CHROME.help === 'see') CM.closeHelp(); else CM.openHelp('see'); return; }
    if (!chromeOn()) return;
    if ((e.key === 'l' || e.key === 'L') && VIEW === 'map') { if (chromeHelpOpen()) CM.closeHelp(); else CM.openHelp('see'); }
    if (e.key === ']') CM.railToggle();
  });
  addEventListener('keyup', (e) => chromePressKey(e, false));
  addEventListener('blur', () => { PRESSI.down.clear(); PRESSI.since = 0; });
  // Esc closes the "?" sheet first, then a task or composer card of the rail (the planet card closes in the page's own Esc, closeAll,
  // where the focus view ends too). A capture listener, ahead of the page's own keys, so one Esc does one thing
  addEventListener('keydown', (e) => {
    if (e.key !== 'Escape' || modKey(e)) return;
    let took = false;
    if (chromeHelpOpen()) { CM.closeHelp(); took = true; }
    else if (!typing() && chromeOn() && RAIL.card && RAIL.card.id !== 'panel' && !CM.mode3d()) { railBack(); took = true; }
    if (took) { e.preventDefault(); e.stopImmediatePropagation(); }
  }, true);
  const P = chromeEl('panel'); if (P && typeof MutationObserver === 'function') new MutationObserver(() => chromePanelSync()).observe(P, { attributes: true, attributeFilter: ['class'] });
  addEventListener('resize', () => { if (RAIL.closedPref == null) { const w2 = chromeEl('wrap'); if (w2 && !RAIL.card) w2.classList.toggle('rail-closed', innerWidth < 1280); } chromeChipStack(); });
  chromeAskBar(); chromeCounts(); chromeReadout(); railRender(true); chromeMachinesRender(); chromeLegendRender(); chromePanelSync();
  setInterval(chromeTick, 100); setInterval(chromeSlowTick, 1000);
  try { fit(); } catch (e) {}
  CM.mapFree = chromeMapFree;   // the one rect the focus camera needs (ui/focus.js), without the whole audit's contrast walk
  CM.chromeReady = true;
});

// First visit: six small stops anchored to the live map and its actual controls.
const GUIDE_STEPS = [
  { title: 'Planets are your folders', text: 'Each planet is a folder on your computer. Its colour and shape tell you what is inside. Tap a planet to look closer.' },
  { title: 'The sun is this machine', text: 'This machine is the computer running your map. Your folders circle it.' },
  { title: 'Ships are your agents', text: 'An agent is an AI helper that works on a task. Its ship works beside the folder it is changing. You check its changes when it finishes.' },
  { title: 'Give an agent a job', text: 'Choose + Task, pick a folder and say what you want. For example: “Write a welcome section in notes-site.” The agent works on a separate copy so you can check it first.' },
  { title: 'You decide what to keep', text: 'Finished work waits in For you → Review. Read the changes, then choose Merge to keep them. You can also ask for changes.' },
  { title: 'Fly in 3D', text: 'Press I or tap 3D to fly through your folders. W/S moves the nose, A/D turns, Shift boosts, C brakes and Q/E tilts. Double-tap Q or E for a roll. Press I to return to the map.' }
];
const GUIDE = { step: -1, el: null, from: null, inert: [], timer: null, target: null };
function guideMap() { if (MOB) mtab('map'); else setView('map'); }
function guideBox(el) {
  if (!el || !el.getClientRects().length || getComputedStyle(el).display === 'none') return null;
  const r = el.getBoundingClientRect(); return { x: r.left, y: r.top, w: r.width, h: r.height };
}
function guideWorldPoint(p, r) {
  if (!p) return null;
  const st = chromeEl('stage').getBoundingClientRect();
  return { x: st.left + (p.x - camx) * ZOOM - r, y: st.top + (p.y - camy) * ZOOM - r, w: r * 2, h: r * 2 };
}
function guideTarget() {
  const s = GUIDE.step;
  if (s === 0) {
    const list = MOONS.map(m => ({ m, p: guideWorldPoint(m, Math.max(22, m.r * ZOOM + 8)) }))
      .filter(o => o.p.x > 8 && o.p.x + o.p.w < innerWidth - 8 && o.p.y > 130 && o.p.y < innerHeight * .62);
    return list.length ? list.sort((a, b) => b.p.x - a.p.x)[0].p : guideWorldPoint(MOONS[0], 32);
  }
  if (s === 1) return guideWorldPoint(LY.star, 48);
  if (s === 2) {
    const a = typeof FC !== 'undefined' && FC.agents && FC.agents[0];
    if (a) { const st = chromeEl('stage').getBoundingClientRect(); return { x: st.left + a.x - 24, y: st.top + a.y - 24, w: 48, h: 48 }; }
    return guideBox(document.querySelector(MOB ? '#tabs [data-tab="threads"]' : '#rtab-agents'));
  }
  if (s === 3) return guideBox(chromeEl(MOB ? 'mfab' : 'railnew'));
  if (s === 4) return guideBox(document.querySelector(MOB ? '#today [data-sec="needs"] .ta .tbtn' : '#rail [data-act="open"]')) ||
    guideBox(document.querySelector(MOB ? '#tab-today' : '#rtab-foryou'));
  return guideBox(MOB ? chromeEl('m3d') : document.querySelector('#statusbar [data-act="fly3d"]'));
}
function guidePosition() {
  if (!GUIDE.el || GUIDE.step < 0) return;
  const box = GUIDE.el.querySelector('.guidecard'), focus = GUIDE.el.querySelector('.guidefocus'), line = GUIDE.el.querySelector('path');
  box.classList.toggle('guide-high', MOB && GUIDE.step === 3);
  const p = guideTarget(); GUIDE.target = p; if (!p) { focus.hidden = true; line.setAttribute('d', ''); return; }
  GUIDE.target = p; focus.hidden = false;
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const x = clamp(p.x, 4, innerWidth - 12), y = clamp(p.y, 4, innerHeight - 12);
  const w = Math.min(p.w, innerWidth - x - 4), h = Math.min(p.h, innerHeight - y - 4);
  Object.assign(focus.style, { left: x + 'px', top: y + 'px', width: w + 'px', height: h + 'px' });
  // A phone's + Task is at the bottom: put that stop's card above it. Other stops keep the map in view.
  const b = box.getBoundingClientRect(), tx = x + w / 2, ty = y + h / 2;
  const sx = clamp(tx, b.left + 20, b.right - 20), sy = ty < b.top ? b.top : ty > b.bottom ? b.bottom : b.top + b.height / 2;
  const ex = tx < b.left ? x + w : tx > b.right ? x : tx, ey = ty < b.top ? y + h : ty > b.bottom ? y : ty;
  line.setAttribute('d', 'M' + sx + ',' + sy + ' L' + ex + ',' + ey);
}
function guideStep(n) {
  if (n >= GUIDE_STEPS.length) { CM.closeGuide(); return; }
  GUIDE.step = Math.max(0, n);
  closeAll(); guideMap();
  if (GUIDE.step === 2) {
    const t = agentTasks().find(t => TASK_WORKING.includes(t.status)) || agentTasks()[0];
    const u = t && (W.chars || []).find(u => u.kind === 'area' && u.path === t.target);
    if (u) activate(u);
  } else if (GUIDE.step === 4) {
    if (MOB) mtab('today'); else CM.railTab('foryou');
  } else {
    if (typeof zoomHome === 'function') zoomHome();
    if (GUIDE.step === 3 && !MOB) CM.railTab('foryou');
  }
  const s = GUIDE_STEPS[GUIDE.step], el = GUIDE.el;
  el.dataset.step = String(GUIDE.step);
  el.querySelector('h2').textContent = s.title; el.querySelector('.guidetext').textContent = s.text + (GUIDE.step === 2 && W.demo ? ' Here a simple script plays the agent; no AI or login is needed.' : '') + (MOB && GUIDE.step === 4 && (W.today || {}).configured !== false ? ' On this map, open Today to find Review.' : '');
  el.querySelector('.guidecount').textContent = (GUIDE.step + 1) + ' of ' + GUIDE_STEPS.length;
  el.querySelector('[data-guide="back"]').disabled = GUIDE.step === 0;
  const next = el.querySelector('[data-guide="next"]'); next.textContent = GUIDE.step === GUIDE_STEPS.length - 1 ? 'Explore the map' : 'Next';
  next.focus({ preventScroll: true }); guidePosition();
}
CM.startGuide = function () {
  if (GUIDE.el) { guideStep(0); return; }
  CM.closeHelp(); if (typeof msheetClose === 'function') msheetClose();
  CM.setPov('map');
  GUIDE.from = document.activeElement;
  const el = document.createElement('div'); el.id = 'firstguide'; el.setAttribute('role', 'dialog'); el.setAttribute('aria-modal', 'true');
  el.setAttribute('aria-labelledby', 'guidetitle'); el.setAttribute('aria-describedby', 'guidetext');
  el.innerHTML = '<div class="guidefocus" aria-hidden="true"></div><svg aria-hidden="true"><path/></svg><section class="guidecard">' +
    '<div class="guidehead"><span>Welcome to your map</span><button type="button" data-guide="close" aria-label="Close guide">Close</button></div>' +
    '<h2 id="guidetitle"></h2><p id="guidetext" class="guidetext"></p><div class="guidefoot"><span class="guidecount"></span>' +
    '<button type="button" data-guide="back">Back</button><button type="button" data-guide="next">Next</button></div></section>';
  GUIDE.el = el; chromeEl('wrap').appendChild(el);
  GUIDE.inert = Array.from(chromeEl('wrap').children).filter(c => c !== el && !c.inert);
  GUIDE.inert.forEach(c => { c.inert = true; });
  el.addEventListener('click', e => { const b = e.target.closest('[data-guide]'); if (!b) return;
    const a = b.dataset.guide; if (a === 'close') CM.closeGuide(); else guideStep(GUIDE.step + (a === 'back' ? -1 : 1)); });
  try { localStorage.setItem('cm.guide.v1', 'seen'); } catch (e) {}
  guideStep(0); GUIDE.timer = setInterval(guidePosition, 100);
};
CM.closeGuide = function () {
  if (!GUIDE.el) return;
  GUIDE.el.remove(); GUIDE.el = null; GUIDE.step = -1; clearInterval(GUIDE.timer); GUIDE.timer = null;
  GUIDE.inert.forEach(c => { c.inert = false; }); GUIDE.inert = [];
  const f = GUIDE.from; if (f && f.isConnected && typeof f.focus === 'function') f.focus({ preventScroll: true });
};
CM.guideAudit = () => ({ shown: !!GUIDE.el, step: GUIDE.step, target: GUIDE.target, title: GUIDE.step < 0 ? '' : GUIDE_STEPS[GUIDE.step].title,
  card: GUIDE.el ? guideBox(GUIDE.el.querySelector('.guidecard')) : null });
CM.onInit.push(() => {
  addEventListener('keydown', e => {
    if (!GUIDE.el) return;
    if (e.key === 'Escape') { e.preventDefault(); e.stopImmediatePropagation(); CM.closeGuide(); }
    else if (e.key === 'Enter') { e.preventDefault(); e.stopImmediatePropagation();
      const a = document.activeElement && document.activeElement.dataset.guide;
      if (a === 'close') CM.closeGuide(); else guideStep(GUIDE.step + (a === 'back' ? -1 : 1));
    } else if (e.key === 'Tab') {
      const all = Array.from(GUIDE.el.querySelectorAll('button:not(:disabled)')), i = all.indexOf(document.activeElement);
      e.preventDefault(); e.stopImmediatePropagation(); all[(i + (e.shiftKey ? -1 : 1) + all.length) % all.length].focus();
    } else { e.stopImmediatePropagation(); }
  }, true);
  addEventListener('resize', guidePosition);
  let seen = false; try { seen = localStorage.getItem('cm.guide.v1') === 'seen'; } catch (e) {}
  const asked = window.CM_SHOW_GUIDE === true || new URLSearchParams(location.search).get('tour') === '1';
  const automated = navigator.webdriver || /HeadlessChrome/.test(navigator.userAgent || '');
  if (asked || (!seen && !automated)) setTimeout(() => CM.startGuide(), 600);
});
