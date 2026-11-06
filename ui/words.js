// ui/words.js — the plain words and the one count: CM.counts, CM.word, CM.stateWord, CM.shortTitle, CM.relTime, CM.contactLine,
// CM.opinionLine, window.__wordsAudit.
// Inlined into the page's main script right after the CM registry (render-game.py, the seams): declare functions and
// assign into CM only, nothing that runs page code at load (start-up goes in CM.onInit). Real backslashes here: this
// file is read as it is, not through a Python string. Neutral words only.
//
// The words live in words.py (WORDS, inlined as `WORDS`): CM.word(k) reads it, and the helpers below read its prefixed
// rows ("state:", "contact:", "verdict:", "count:", "count1:", "http:", "touch:"). Each helper here has a twin in words.py
// that test_plain_words.py runs on the same inputs. The counts are sky.counts() (W.counts, the render's and every
// /tasks reply's); every surface that prints a count with its noun marks itself data-count="<key>" so __wordsAudit()
// can read them all back and compare them with W.counts.

CM.COUNT_NOUNS = ['need_you', 'decisions', 'problems'];   // the headline, in this order
CM.DECISION_SOURCES = ['ledger', 'board', 'quest', 'fleet'];

CM.counts = () => (W.counts && typeof W.counts === 'object' ? W.counts : {});
// colour means state only: red broken, amber needs you or a decision, cyan working,
// green done, grey waiting. A task's stored status -> its colour; the state word wears it, not only a bullet beside it
CM.STATE_COL = { broken: '#ff5a5f', needs: '#ffb547', working: '#6fb7ff', done: '#4ade80', waiting: '#9aa8bc' };
CM.TASK_BAND = { new: 'working', running: 'working', checking: 'working', stopping: 'working', review: 'needs', stopped: 'needs',
  failed: 'broken', lost: 'broken', error: 'broken', merged: 'done', done: 'done', discarded: 'waiting' };
CM.taskCol = (status) => CM.STATE_COL[CM.TASK_BAND[status] || 'waiting'];
CM.stateWord = (s) => (typeof s === 'string' ? CM.word('state:' + s).replace(/^state:/, '') : '');
// with the counts (c), "need_you" says its kind when every one of them is the same kind: "finished task to review", "question for you"
CM.countNoun = (k, n, c) => {
  if (k === 'need_you' && c && typeof c === 'object' && n > 0) {
    const r = c.review > 0 ? c.review : 0, a = c.answer > 0 ? c.answer : 0;
    if (r === n && !a) return n === 1 ? 'finished task to review' : 'finished tasks to review';
    if (a === n && !r) return n === 1 ? 'question for you' : 'questions for you';
  }
  const key = (n === 1 ? 'count1:' : 'count:') + k, w = CM.word(key);
  return w === key ? String(k) : w;
};
// "13 need you · 53 decisions · 1 problem"; zeros left out; none at all: "Nothing waiting on you"
CM.countLine = (c, keys) => {
  c = c && typeof c === 'object' ? c : CM.counts();
  const parts = (keys || CM.COUNT_NOUNS).filter((k) => Number.isInteger(c[k]) && c[k] > 0).map((k) => c[k] + ' ' + CM.countNoun(k, c[k], c));
  return parts.length ? parts.join(' · ') : CM.word('empty sky');
};
// sky.bucket(): a decision source (any state) is a decision; else blocked -> answer, review/ready -> review, the rest a problem
CM.bucket = (c) => {
  if (!c || typeof c !== 'object') return 'problem';
  if (CM.DECISION_SOURCES.includes(c.source)) return 'decision';
  return c.state === 'blocked' ? 'answer' : (c.state === 'review' || c.state === 'ready') ? 'review' : 'problem';
};
// sky.counts() in the page: the same buckets from a sky, the threads and the workflows (tests and a page that has no
// W.counts use it; the page itself reads W.counts)
CM.countsOf = (sky, tasks, workflows, now) => {
  sky = sky && typeof sky === 'object' ? sky : {};
  const contacts = Array.isArray(sky.contacts) ? sky.contacts.filter((c) => c && typeof c === 'object') : [];
  const out = { need_you: 0, answer: 0, review: 0, decisions: 0, problems: 0, working: 0, on_hold: 0, done_today: 0,
    oldest_decision: { days: null, source: null }, total: contacts.length };
  const plural = { decision: 'decisions', answer: 'answer', problem: 'problems', review: 'review' };
  let oldest = null;
  contacts.forEach((c) => { const b = CM.bucket(c); out[plural[b]]++;
    if (b === 'decision' && Number.isInteger(c.age_days) && (!oldest || c.age_days > oldest[0])) oldest = [c.age_days, c.source]; });
  out.need_you = out.answer + out.review;
  if (oldest) out.oldest_decision = { days: oldest[0], source: oldest[1] };
  const n = (v) => (Number.isInteger(v) && v > 0 ? v : 0), by = (sky.hyper && sky.hyper.by_source) || {};
  let working = n(by.seat) + n(by.t3);
  if (Array.isArray(tasks)) working += tasks.filter((t) => t && (['running', 'checking', 'stopping', 'new'].includes(t.status) || (t.status === 'failed' && t.retry_due))).length;
  else working += n(by.task);
  const authors = new Set();
  (Array.isArray(workflows) ? workflows : []).forEach((w) => { if (!w || typeof w !== 'object') return;
    if ((Array.isArray(w.steps) ? w.steps : []).some((s) => s && s.kind === 'note' && s.state === 'working')) {
      const b = w.by && typeof w.by === 'object' ? w.by : {}; authors.add(String(b.seat || b.name || w.id || '')); } });
  out.working = working + authors.size;
  out.on_hold = n(sky.parked);
  // done today = settled today + threads accepted today + workflows finished today (sky.counts' three terms)
  const today = CM.dayOf(now === undefined ? Date.now() / 1000 : now);
  const doneWf = (Array.isArray(workflows) ? workflows : []).filter((w) => w && typeof w === 'object' && w.state === 'done'
    && today && CM.dayOf(w.updated) === today).length;
  const doneTasks = (Array.isArray(tasks) ? tasks : []).filter((t) => t && typeof t === 'object'
    && ['merged', 'done'].includes(t.status) && today && CM.dayOf(t.ended) === today).length;
  out.done_today = n(sky.settled_today) + doneWf + doneTasks;
  return out;
};
// the local calendar day ("2026-10-12") of a stamp, sky.py _when + _today_stamp: a date-only stamp keeps its date; epoch
// seconds (2000 or later) and ISO stamps (a naive one is UTC) give the local day they fall on; anything else ''
CM.MIN_EPOCH = 946684800;
CM.ISO_RX = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:[.,](\d+))?)?)?(Z|[+-]\d{2}(?::?\d{2})?)?$/i;
CM.dayOf = (v) => {
  const pad = (x) => (x < 10 ? '0' : '') + x;
  const local = (ms) => { const d = new Date(ms); return Number.isFinite(d.getTime()) ? d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) : ''; };
  if (typeof v === 'number') return Number.isFinite(v) && v >= CM.MIN_EPOCH ? local(v * 1000) : '';
  if (typeof v !== 'string') return '';
  const t = v.trim();
  if (/^\d{4}-\d{2}-\d{2}$/.test(t)) return t;
  const m = CM.ISO_RX.exec(t); if (!m) return '';
  let ms = Date.UTC(+m[1], +m[2] - 1, +m[3], +(m[4] || 0), +(m[5] || 0), +(m[6] || 0), Number(((m[7] || '') + '000').slice(0, 3)));
  const z = m[8];
  if (z && z.toUpperCase() !== 'Z') { const dg = z.slice(1).replace(':', ''), off = (+dg.slice(0, 2) * 60 + +(dg.slice(2, 4) || 0)) * 60000; ms -= z[0] === '-' ? -off : off; }
  return local(ms);
};

// ---- titles and notes, as a person says them (words.py _clean_line / plain_text / short_title) ----
CM.TITLE_MAX = 48;
CM.RX = {
  bullet: /^\s*(?:[-*•>#]+\s+|\d{1,3}[.)]\s+)?(?:\[[ xX]\]\s+)?/,
  leadDate: /^(?:\d{4}-\d{2}-\d{2}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?\s*(?:[-–—:·|]\s*)?)+/,
  prefix: /^(?:(?:goal|task|mission|operation|objective|todo|to-do|ask|prompt|thread)\s*[:\-–—]\s*)+/i,
  ids: /\(\s*(?:T|w)-\d{8}[^)]*\)|\[\s*(?:T|w)-\d{8}[^\]]*\]|\b(?:T|w)-\d{8}(?:-\d{4})?(?:-[\w.]+)*|\b\d{8}-\d{6}-[a-z0-9-]+\b|\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{12,}\b/g,
  url: /\bhttps?:\/\/[^\s)\]]+/g,
  path: /(?<![\w.:/])(?:~|\.{1,2})?\/(?:[\w.@+-]+\/)+([\w.@+-]+)\/?/g,
  mark: /`+|\*+/g,
  // what a title cut at a fixed width leaves at its end: an opened bracket with no close, or the start of a task id
  tail: /\s*(?:[(\[]\s*)?\b(?:T|w)-\d{0,8}(?:-[\w.]*)?$|\s*[(\[][^)\]]*$/,
  clause: /,\s|;\s|\s[—–-]\s|:\s|\s\(/g,
};
CM.cleanLine = (text, cap) => {
  const R = CM.RX, s = String(text == null ? '' : text);
  let f = (s.split(/\r?\n/).find((l) => l.trim()) || '');
  const head = f.trim();
  f = f.replace(R.bullet, '').trim().replace(R.leadDate, '').trim().replace(R.prefix, '').trim();
  if (cap === undefined || cap === null) cap = f !== head;   // upper case only when a prefix came off the front
  f = f.replace(R.url, '').replace(R.ids, '').replace(R.path, '$1').replace(R.mark, '').replace(R.tail, '');
  f = f.replace(/\s+/g, ' ').trim().replace(/^[\s\-–—:·|,;]+|[\s\-–—:·|,;]+$/g, '').replace(/\(\s*\)/g, '').replace(/\s+/g, ' ').trim();
  return !cap || !f ? f : f.charAt(0).toUpperCase() + f.slice(1);
};
CM.plainText = (text, n) => {
  const R = CM.RX; n = n || 260;
  let s = String(text == null ? '' : text).replace(/\s+/g, ' ').trim();
  s = s.replace(R.url, '').replace(R.ids, '').replace(R.path, '$1').replace(/\(\s*\)|\[\s*\]/g, '');
  s = s.replace(/\s+/g, ' ').replace(/\s+([,.;:])/g, '$1').replace(/\s*·\s*(?=[,.;:]|·|$)/g, '').replace(/^[\s·\-–—:,;]+|[\s·\-–—:,;]+$/g, '');
  return s.length <= n ? s : s.slice(0, n - 1).trimEnd() + '…';
};
CM.shortTitle = (text, n, cap) => {
  n = n || CM.TITLE_MAX;
  const line = CM.cleanLine(text, cap);
  if (line.length <= n) return line;
  let best = '', m; const rx = new RegExp(CM.RX.clause.source, 'g');
  while ((m = rx.exec(line))) { if (m.index > n) break; const head = line.slice(0, m.index).replace(/[\s,;:\-–—]+$/, ''); if (head.length >= 16) best = head; }
  if (best) return best;
  let cut = line.slice(0, n - 1);
  if (cut.slice(Math.floor(n / 2)).includes(' ')) cut = cut.slice(0, cut.lastIndexOf(' '));
  return cut.replace(/[\s,;:\-–—]+$/, '') + '…';
};
CM.relTime = (seconds) => {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds)) return '';
  const s = Math.max(0, seconds);
  if (s < 60) return 'just now';
  if (s < 3600) return Math.floor(s / 60) + ' min ago';
  if (s < 86400) return Math.floor(s / 3600) + ' h ago';
  const d = Math.floor(s / 86400);
  if (d < 14) return d === 1 ? '1 day ago' : d + ' days ago';
  if (d < 70) return Math.floor(d / 7) + ' weeks ago';
  return Math.floor(d / 30) + ' months ago';
};
// whole days since, as a row says them: "today", "1 day ago", "5 days ago", "3 weeks ago" ('' when it is not a number)
CM.daysAgo = (days) => {
  const d = Number(days); if (days == null || days === '' || !Number.isFinite(d)) return '';
  return d < 1 ? 'today' : CM.relTime(Math.floor(d) * 86400);
};
// seconds since an ISO stamp or epoch milliseconds, as CM.relTime says it ('' when it is not one)
CM.agoOf = (iso) => { const t = typeof iso === 'number' ? iso : Date.parse(iso || ''); return Number.isFinite(t) ? CM.relTime((Date.now() - t) / 1000) : ''; };
// whole days as a row says them: "today", "1 day", "5 days", "3 weeks"
CM.ageWords = (days) => {
  const d = Number(days); if (days == null || !Number.isFinite(d)) return '';
  if (d < 1) return 'today'; if (d < 14) return Math.floor(d) === 1 ? '1 day' : Math.floor(d) + ' days';
  if (d < 70) return Math.floor(d / 7) + ' weeks'; return Math.floor(d / 30) + ' months';
};

// ---- sky contacts on a row ----
CM.contactWord = (c) => {
  if (!c || typeof c !== 'object') return '';
  if (CM.DECISION_SOURCES.includes(c.source)) return CM.word('contact:decision');
  const k = 'contact:' + String(c.state || ''), w = CM.word(k);
  return w === k ? String(c.state || '') : w;
};
// one plain line: what it is (a short title), its state word, where (the planet's name), how old
// "Export fills: what they did · to review · Trading · 1 day"; opts.noPlace / opts.noWord leave those out
CM.contactLine = (c, opts) => {
  if (!c || typeof c !== 'object') return '';
  opts = opts || {};
  const title = CM.shortTitle(c.title || c.voice || '', opts.n || 60) || CM.contactWord(c);
  const where = c.planet_uid && typeof CM.labelText === 'function' && !opts.noPlace ? CM.labelText(c.planet_uid) : '';
  // a job's title already says how it is doing ("job true: never ran"): no second state word
  const word = c.source === 'cron' || opts.noWord ? '' : CM.contactWord(c);
  return [title, word, where && where !== c.planet_uid ? where : '', CM.ageWords(c.age_days)].filter(Boolean).join(' · ');
};
CM.opinionLine = (verdict, model) => {
  const v = String(verdict == null ? '' : verdict).trim(); if (!v) return '';
  const who = typeof model === 'string' && model.trim() ? ' (' + model.trim() + ')' : '', low = v.toLowerCase();
  if (['checking', 'running', 'pending'].includes(low)) return 'second opinion' + who + ' running';
  const said = ['pass', 'passed', 'ok', 'approve', 'approved', 'lgtm'].includes(low) ? 'passed'
    : ['fail', 'failed', 'reject', 'rejected', 'changes', 'request_changes', 'problems', 'block'].includes(low) ? 'found problems'
    : (['error', 'unavailable', 'timeout', 'lost'].includes(low) || low.startsWith('no verdict')) ? 'gave no answer' : low;
  return 'second opinion' + who + ': ' + said;
};
// an HTTP status (0 = no answer at all) as one sentence; never "http 404"
CM.errorLine = (status) => {
  const k = 'http:' + (Number.isInteger(status) ? status : 0), w = CM.word(k);
  return w === k ? CM.word('http:') : w;
};
CM.verdictWord = (v) => { const k = 'verdict:' + String(v || ''), w = CM.word(k); return w === k ? String(v || '').toLowerCase() : w; };
CM.jobLine = (name, verdict) => 'job ' + String(name || 'a job') + ': ' + CM.verdictWord(verdict);
// identical lines folded, first seen first: [[line, n], ...]; grouped(line, n) -> "line ×n"
CM.groupCauses = (lines) => { const seen = new Map(); (lines || []).forEach((l) => { const k = String(l); seen.set(k, (seen.get(k) || 0) + 1); }); return Array.from(seen.entries()); };
CM.grouped = (line, n) => (Number.isInteger(n) && n > 1 ? line + ' ×' + n : String(line));
// the touch screen's words: CM.tx('Click a folder') -> 'Tap a folder' on a phone or a tablet; CM.tx(desk, touch) picks one
CM.isTouch = () => { try { return (typeof MOB !== 'undefined' && MOB) || (typeof touchUI === 'function' && touchUI()); } catch (e) { return false; } };
CM.tx = (desk, touch) => {
  if (!CM.isTouch()) return String(desk == null ? '' : desk);
  if (touch !== undefined) return String(touch);
  return String(desk == null ? '' : desk).replace(/\b(right-click|[Cc]licks?|[Hh]over)\b/g, (w) => { const k = 'touch:' + w, t = CM.word(k); return t === k ? w : t; })
    .replace(/\s*\((?:[A-Z]|Esc|Shift\+[A-Z])\)/g, '');
};
// ---- what to mark on the map now (words.py wf_marks, its twin): the planets of the steps a workflow's sentence speaks
// of. [{uid, why}], why 'failed' | 'needs_you' | 'working' | 'planned'; done, stopped or no steps: []
CM.MARK_WHY = ['failed', 'needs_you', 'working', 'planned'];
CM.wfMarks = (w) => {
  if (!w || typeof w !== 'object') return [];
  const steps = (Array.isArray(w.steps) ? w.steps : []).filter((x) => x && typeof x === 'object');
  if (!steps.length) return [];
  const byKey = new Map(steps.map((x) => [x.key, x]));
  const current = (Array.isArray(w.current) ? w.current : []).filter((k) => typeof k === 'string' && byKey.has(k)).map((k) => byKey.get(k));
  const inState = (st) => current.filter((x) => x.state === st).concat(steps.filter((x) => x.state === st && !current.includes(x)));
  const failed = steps.find((x) => x.state === 'failed') || null, needs = inState('needs_you'), working = inState('working');
  let marked, why;
  if (w.state === 'failed' || (failed && !working.length && !needs.length)) { marked = [failed || steps[steps.length - 1]]; why = 'failed'; }
  else if (needs.length) { marked = needs; why = 'needs_you'; }
  else if (working.length) { marked = working; why = 'working'; }
  else if (w.state === 'done' || w.state === 'stopped' || steps.every((x) => x.state === 'done' || x.state === 'skipped')) return [];
  else { marked = [steps.find((x) => x.state === 'waiting' || x.state === 'ready') || steps[0]]; why = 'planned'; }
  const out = [], seen = new Set();
  marked.forEach((x) => { const u = x.planet_uid; if (typeof u === 'string' && u && !seen.has(u)) { seen.add(u); out.push({ uid: u, why: why }); } });
  return out;
};
// every workflow's marks on the page, one per planet, the owner's first (a failure over a wait over work): Map uid -> why.
// The map module draws them (a ring, a glow); the 3D view lights the same planets
CM.markPlanets = () => {
  const out = new Map(), rank = (k) => CM.MARK_WHY.indexOf(k);
  (typeof CM.wfList === 'function' ? CM.wfList() : []).forEach((w) => CM.wfMarks(w).forEach((m) => {
    if (!out.has(m.uid) || rank(m.why) < rank(out.get(m.uid))) out.set(m.uid, m.why); }));
  return out;
};

// W.counts and W.sky are one snapshot: the render's (W.rendered is its revision, applyWorld) or a /tasks reply's (the
// bridge computes sky.build and sky.counts with the threads and workflows it has just listed, and stamps
// the reply with its revision `rev`, bridge.snapshot_rev: the same clock, never lower than the last reply's). The
// page keeps the newest revision it has seen (CM._snapRev): a reply that arrives late with an older revision, or a
// render older than the snapshot in hand, changes nothing (arrival time says nothing about freshness). A count is
// taken only whole and consistent: every key an integer, need_you = answer + review, the four buckets adding up to
// total, and total the number of contacts in the sky it came with. A reply without counts (an old bridge, a static
// page) changes nothing.
CM.COUNT_INTS = ['need_you', 'answer', 'review', 'decisions', 'problems', 'working', 'on_hold', 'done_today', 'total'];
CM._snapRev = 0;
CM.countsOk = (c) => {
  if (!c || typeof c !== 'object' || Array.isArray(c)) return false;
  if (!CM.COUNT_INTS.every((k) => Number.isInteger(c[k]) && c[k] >= 0)) return false;
  return c.need_you === c.answer + c.review && c.decisions + c.answer + c.review + c.problems === c.total;
};
// the count alone (a reply with no sky): the same rules, the revision kept when one is given
CM.takeCounts = (c, rev) => CM.takeSnapshot({ counts: c, rev: rev }).counts;
// {sky, counts, rev} -> {counts: moved, sky: moved, stale: refused as older}: W.sky and W.counts move together, or not at all
CM.takeSnapshot = (j) => {
  const out = { counts: false, sky: false, stale: false };
  if (!j || typeof j !== 'object' || !CM.countsOk(j.counts)) return out;
  const rev = typeof j.rev === 'number' && Number.isFinite(j.rev) ? j.rev : null;
  if (rev !== null && rev < CM._snapRev) { out.stale = true; return out; }
  const hasSky = j.sky && typeof j.sky === 'object' && !Array.isArray(j.sky) && Array.isArray(j.sky.contacts);
  if (hasSky && !j.sky.error && j.sky.contacts.length !== j.counts.total) return out;   // the two disagree: neither is taken
  out.counts = JSON.stringify(j.counts) !== JSON.stringify(W.counts || null);
  out.sky = hasSky && JSON.stringify(j.sky) !== JSON.stringify(W.sky || null);
  W.counts = j.counts; if (hasSky) W.sky = j.sky;
  if (rev !== null) CM._snapRev = rev;
  CM._countsAt = Date.now();
  return out;
};

// every count on screen with its noun, read back: {surfaces:[{where, noun, n, text}], counts, ok}. A surface is any
// element marked data-count="<counts key>" that is shown (has a box); its number is its <b>, else the first number
// in its text. A list that splits a count over its rows (the rail's System: each planet's problems, the ones circling
// the sun, and a "+N more" row with the rest) is marked data-parts="<counts key>" and each row data-part="<n>" (n is
// the number that row prints); the rows' sum is one more surface (sum: true), so a count the rows lose shows here.
// ok: each n equals W.counts[noun].
(typeof window !== 'undefined' ? window : globalThis).__wordsAudit = function () {
  const c = CM.counts(), out = [], shown = (el) => !!(el.getClientRects && el.getClientRects().length);
  const whereOf = (el) => (el.closest('[id]') || el).id || el.tagName.toLowerCase();
  document.querySelectorAll('[data-count]').forEach((el) => {
    if (!shown(el)) return;
    const noun = el.getAttribute('data-count'), b = el.querySelector('b'), m = /\d+/.exec(b ? b.textContent : el.textContent || '');
    if (!m) return;
    out.push({ where: whereOf(el), noun: noun, n: Number(m[0]), text: String(el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 80) });
  });
  document.querySelectorAll('[data-parts]').forEach((el) => {
    if (!shown(el)) return;
    const rows = Array.from(el.querySelectorAll('[data-part]')).filter(shown);
    const n = rows.reduce((a, r) => a + (Number(r.getAttribute('data-part')) || 0), 0);
    out.push({ where: whereOf(el), noun: el.getAttribute('data-parts'), n: n, sum: true, text: rows.length + ' rows' });
  });
  // every place a count sits at: each planet's buckets (the badge, the card, the rail's row and the overview's chip all read
  // them: CM.planetCounts) and what circles the sun. Their sum is the one count: each contact is in ONE place and ONE bucket
  const places = typeof CM.planetCounts === 'function' ? CM.planetCounts() : [], sums = { problems: 0, need_you: 0, decisions: 0 };
  places.forEach((p) => Object.keys(sums).forEach((k) => { sums[k] += (p.n && Number.isInteger(p.n[k]) ? p.n[k] : 0); }));
  const planetsOk = !places.length || Object.keys(sums).every((k) => sums[k] === c[k]);
  return { surfaces: out, counts: c, planets: places, planetSums: sums, planetsOk: planetsOk,
    ok: out.length > 0 && out.every((s) => s.n === c[s.noun]) && planetsOk };
};
