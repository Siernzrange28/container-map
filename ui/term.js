// ---- THE LIVE TERMINAL (a seat's panel, on the desktop). The pane's screen drawn by xterm.js from GET /term
// (server-sent events: one "full" frame with the history, then a "frame" per change with k = rows that scrolled off
// the top), and what is typed into it sent to POST /keys as xterm hands it over (tmux key names on the bridge's
// side). A seat that is not configured for talk is drawn the same and is view only. With no bridge, no EventSource,
// no xterm file, or a phone, the panel keeps its 2-second text snapshot (#plive).
const TERM = { es: null, term: null, seat: null, uid: null, gen: 0, rows: [], queue: [], busy: false,
  loading: null, live: false, sentAt: 0 };
const TERM_GAP = 30;          // ms between two POST /keys: under the bridge's 40 a second per seat, even with key repeat
const TERM_FONT = "'JetBrains Mono','Cascadia Mono','DejaVu Sans Mono',Menlo,Consolas,monospace";

function termAvailable() {
  return !!(typeof LIVE !== 'undefined' && LIVE && DESK && !REMOTE && window.EventSource && V3D && V3D.xterm);
}
function termLoad() {
  if (window.Terminal) return Promise.resolve();
  if (TERM.loading) return TERM.loading;
  TERM.loading = new Promise((ok, no) => {
    const s = document.createElement('script'); s.nonce = NONCE; s.src = '/vendor/' + V3D.xterm; s.async = false;
    s.onload = () => (window.Terminal ? ok() : no(new Error('the terminal script did not load')));
    s.onerror = () => { TERM.loading = null; no(new Error('could not load the terminal')); };
    document.head.appendChild(s);
  });
  return TERM.loading;
}
function termNote(text) {
  const el = document.getElementById('termnote'); if (el) el.textContent = text || '';
}
function termMark(live) {
  TERM.live = live;
  const rev = document.getElementById('panerev'); if (rev) rev.textContent = live ? 'live' : 'snapshot';
  const host = document.getElementById('pterm'), pre = document.getElementById('plive');
  if (host) host.hidden = !live && !TERM.term;
  if (pre) pre.hidden = !!TERM.term;
}
// the desktop rail widens while a terminal is open (ui/term.css); the map is told its stage changed size
function termWide(on) {
  const wrap = document.getElementById('wrap'); if (!wrap || wrap.classList.contains('term-wide') === on) return;
  wrap.classList.toggle('term-wide', on);
  setTimeout(() => dispatchEvent(new Event('resize')), 0);
}
function termStop() {
  TERM.gen++;
  termWide(false);
  if (TERM.es) { TERM.es.close(); TERM.es = null; }
  if (TERM.term) { try { TERM.term.dispose(); } catch (e) {} TERM.term = null; }
  TERM.seat = null; TERM.uid = null; TERM.rows = []; TERM.queue = []; TERM.busy = false; TERM.live = false;
}
// back to the snapshot: the stream could not be opened, or the pane went away
function termFallback(why) {
  const gen = TERM.gen;
  termStop();
  const host = document.getElementById('pterm'), pre = document.getElementById('plive');
  if (host) { host.hidden = true; host.textContent = ''; }
  if (pre) pre.hidden = false;
  const rev = document.getElementById('panerev'); if (rev) rev.textContent = 'snapshot';
  termNote(why ? 'live view off: ' + why : '');
  return gen;
}
// the font that puts the pane's columns across the panel (xterm's cell is about 0.6 of the font size; the real width
// is measured after one frame and the size corrected once)
function termFit() {
  const t = TERM.term, host = document.getElementById('pterm'); if (!t || !host) return;
  const width = host.clientWidth - 16; if (width <= 0) return;
  const guess = Math.max(8, Math.min(15, Math.floor(width / (t.cols * 0.6) * 10) / 10));
  if (Math.abs((t.options.fontSize || 0) - guess) > 0.05) t.options.fontSize = guess;
  requestAnimationFrame(() => {
    const screen = host.querySelector('.xterm-screen'); if (!screen || !TERM.term) return;
    const used = screen.getBoundingClientRect().width;
    if (used > width + 1) TERM.term.options.fontSize = Math.max(7, Math.floor(guess * width / used * 10) / 10);
  });
}
function termCursor(j) {
  return '\x1b[0m\x1b[' + (j.cy + 1) + ';' + (j.cx + 1) + 'H' + (j.cursor ? '\x1b[?25h' : '\x1b[?25l');
}
function termApply(j) {
  const t = TERM.term; if (!t) return;
  if (j.t === 'full') {
    if (t.cols !== j.w || t.rows !== j.h) t.resize(j.w, j.h);
    t.reset();
    let out = '';
    for (const line of j.history || []) out += '\x1b[0m' + line + '\r\n';
    out += j.screen.map(r => '\x1b[0m' + r).join('\r\n');
    t.write(out + termCursor(j));
    TERM.rows = j.screen.slice();
    termFit(); termMark(true);
    return;
  }
  if (j.t !== 'frame') return;
  let out = '\x1b[0m', before = TERM.rows;
  const pushed = j.rows || [];
  if (j.k > 0 && pushed.length === j.k) {
    // the rows that went into tmux's history go into xterm's, in order: each chunk is drawn from the top row
    // down and pushed off by line feeds on the bottom row (a burst bigger than the screen takes several chunks)
    for (let at = 0; at < pushed.length; at += t.rows) {
      const chunk = pushed.slice(at, at + t.rows);
      chunk.forEach((row, i) => { out += '\x1b[' + (i + 1) + ';1H\x1b[0m\x1b[2K' + row; });
      out += '\x1b[0m\x1b[' + t.rows + ';1H' + '\n'.repeat(chunk.length);
    }
    before = [];                                   // every screen row is drawn again below
  }
  for (let i = 0; i < j.screen.length; i++) {
    if (before[i] !== j.screen[i]) out += '\x1b[' + (i + 1) + ';1H\x1b[0m\x1b[2K' + j.screen[i];
  }
  t.write(out + termCursor(j));
  TERM.rows = j.screen.slice();
}
function termConnect(gen) {
  if (gen !== TERM.gen || !TERM.seat) return;
  const es = new EventSource('/term?seat=' + encodeURIComponent(TERM.seat));
  TERM.es = es;
  es.addEventListener('term', ev => {
    if (gen !== TERM.gen) { es.close(); return; }
    let j; try { j = JSON.parse(ev.data); } catch (e) { return; }
    if (j.t === 'end') {
      es.close(); TERM.es = null;
      if (j.why === 'timeout') { termConnect(gen); return; }    // a stream lives ten minutes; the next one starts over
      termFallback(j.why);
      return;
    }
    termApply(j);
    if (j.t !== 'beat') termNote(TERM.queue.length ? 'typing…' : '');
  });
  es.onerror = () => {
    if (gen !== TERM.gen) return;
    if (es.readyState === 2) { TERM.es = null; termFallback('the bridge refused the stream'); return; }
    termNote('reconnecting…');                       // EventSource tries again by itself
  };
}
function termSend(body) {
  return api('/keys', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
}
// typing and pastes leave in the order they were made, one request at a time; typing that piles up while one
// is out goes as one request (at most 4000 characters)
function termQueue(item) {
  const last = TERM.queue[TERM.queue.length - 1];
  if (item.data != null && last && last.data != null && last.data.length + item.data.length <= 4000) last.data += item.data;
  else TERM.queue.push(item);
  termFlush();
}
function termFlush() {
  if (TERM.busy || !TERM.queue.length || !TERM.seat) return;
  const gen = TERM.gen, wait = TERM.sentAt + TERM_GAP - Date.now();
  if (wait > 0) { TERM.busy = true; setTimeout(() => { if (gen === TERM.gen) { TERM.busy = false; termFlush(); } }, wait); return; }
  const item = TERM.queue.shift(); TERM.busy = true; TERM.sentAt = Date.now();
  let retry = 0;
  termSend(Object.assign({ seat: TERM.seat }, item))
    .then(() => { if (gen === TERM.gen) termNote(''); })
    .catch(e => {
      if (gen !== TERM.gen) return;
      if (e.status === 429) { TERM.queue.unshift(item); retry = 250; termNote('typing faster than the bridge takes; catching up'); return; }
      TERM.queue = []; termNote(e.message);            // refused (view only, gone): what was queued is not typed
    })
    .finally(() => {
      if (gen !== TERM.gen) return;
      if (retry) setTimeout(() => { if (gen === TERM.gen) { TERM.busy = false; termFlush(); } }, retry);
      else { TERM.busy = false; termFlush(); }
    });
}
function termTalkable() { return !!(openUnit && openUnit.uid === TERM.uid && openUnit.talkable); }
function termType(data) {
  if (!termTalkable()) { termNote('view only' + (openUnit && openUnit.talk_reason ? ': ' + openUnit.talk_reason : '')); return; }
  termQueue({ data });
}
function termPaste(ev) {
  const text = (ev.clipboardData && ev.clipboardData.getData('text/plain')) || '';
  ev.preventDefault(); ev.stopPropagation();
  if (!text) return;
  if (!termTalkable()) { termNote('view only'); return; }
  if (text.length > 16384) { termNote('that paste is over 16384 characters'); return; }
  termQueue({ paste: text });
}
function termCopy() {
  const t = TERM.term; if (!t || !t.hasSelection()) return false;
  const text = t.getSelection();
  if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).catch(() => {});
  termNote('copied'); return true;
}
// open the live terminal for this seat's panel (called after the panel's HTML is in place)
function termOpen(u) {
  termStop();
  const host = document.getElementById('pterm');
  if (!host || !termAvailable()) return;
  const gen = TERM.gen;
  termLoad().then(() => {
    if (gen !== TERM.gen || !openUnit || openUnit.uid !== u.uid || !document.body.contains(host)) return;
    TERM.seat = seatTarget(u); TERM.uid = u.uid;
    const term = new Terminal({ cols: 80, rows: 24, fontSize: 13, fontFamily: TERM_FONT, scrollback: 5000,
      cursorBlink: true, cursorInactiveStyle: 'outline', convertEol: false,
      theme: { background: '#07090d', foreground: '#cfd8e3', cursor: '#58a6ff', selectionBackground: '#2f4a6b' } });
    TERM.term = term;
    termWide(true);
    host.hidden = false; host.textContent = '';
    term.open(host);
    term.onData(termType);
    // Ctrl/Cmd+C with a selection copies (without one it is the program's Ctrl-C); Ctrl/Cmd+V is the browser's
    // paste, which termPaste sends as one paste
    term.attachCustomKeyEventHandler(ev => {
      if (ev.type !== 'keydown') return true;
      const mod = ev.ctrlKey || ev.metaKey, key = String(ev.key || '').toLowerCase();
      if (mod && key === 'c' && (ev.shiftKey || term.hasSelection())) { termCopy(); ev.preventDefault(); return false; }
      if (mod && key === 'v') return false;
      return true;
    });
    host.addEventListener('paste', termPaste, true);
    TERM.rows = [];
    termConnect(gen);
  }).catch(e => { if (gen === TERM.gen) termFallback(e.message); });
}
function termFocus() { if (TERM.term) TERM.term.focus(); }
addEventListener('resize', () => { if (TERM.term) termFit(); });
