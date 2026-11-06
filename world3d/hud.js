// world3d/hud.js — the HUD over the 3D view (; art: comps/3d-cruise.png, 3d-planet-workflow.png,
// 3d-transition.png, 3d-phone.png): the gauge "N of M folders healthy", the steps counter with its three milestone rings, agents
// working, the boost meter that shows only when used, "N need you" with its badges, the FLOW block and tube of the followed
// workflow, the reticle (a near box that leads and a far ring that lags) with aim-to-select and the dwell lock, the target
// label on a leader with arc brackets, a pool of at most 16 labelled markers (one shape and one colour per kind), named
// arrows to what is off the screen, comms with priorities and the red box for problems, the radar (the north-up map with
// altitude stalks), the mission card and its step cards, the callout tag on the followed agent, the LIVE inset, the phase
// plate, the first-entry caption and the entry's timeline bar, the target card, HELP arrows, the WORKFLOW COMPLETE band and
// the tally, and a live region a screen reader reads.
// Registered on the 3D view's registry (world3d/core.js); reads only `world` (cmWorld()) and `ctx`, never page globals,
// and what world3d/camera.js shares in CM3D.fh. DOM over the canvas, pixel-crisp, positions and sizes set from JS (the
// page's CSP), classes in ui/hud3d.css. Units: u = the HUD frame's height / 240 (phone portrait: min(width/320,
// height/240)), anchored to the frame's edges (the HUD frame follows the display aspect ratio).
(function (g) {
  'use strict';
  var C = g.CM3D;
  if (!C || typeof C.module !== 'function') return;
  var FH = C.fh = C.fh || {};
  var D2R = Math.PI / 180, R2D = 180 / Math.PI;
  C.provides('hud');

  // the neutral slots in percent of the HUD frame (x0, y0, x1, y1): SYNTHESIS §4's boxes as the comps place them; a skin's
  // feel3d.hudLayout replaces them (units "percent" the same, "fox320" = x, y, w, h on a 320 x 240 screen, x < 160 from
  // the left edge, else from the right). A phone in portrait has its own (comps/3d-phone.png).
  var LAYOUT = {
    units: 'percent', gauge: [1.5, 2.4, 19.4, 5.3], counter: [1.4, 6.9, 16, 14.2], agents: [80, 1.6, 98.5, 5.6],
    boost: [87.1, 7.6, 98.5, 9.6], need: [86, 10.7, 98.5, 14.4], tube: [0.8, 21.6, 22, 60], comms: [1.5, 80.7, 39.6, 97],
    radar: [80.6, 71.1, 98.5, 97], mission: [30.5, 1.3, 69.5, 13.8]
  };
  var PHONE = {
    units: 'percent', gauge: [3.1, 5.9, 44, 7.8], counter: [3.1, 11.6, 44, 18.8], agents: [50, 5.2, 97.4, 8.4],
    boost: [68, 17.4, 97.4, 18.9], need: [52, 12.6, 97.4, 16.2], tube: null, comms: [2.6, 65.5, 97.4, 75.5], radar: null,
    mission: [4, 18, 96, 30]
  };
  var COL = { red: '#FF5A5F', amber: '#FFB547', cyan: '#6FB7FF', green: '#4ADE80', grey: '#6B7684', teal: '#4DE8E0', text: '#E8EEF6', dim: '#9AA8BC' };
  var STATE_COL = { done: COL.green, working: COL.cyan, needs_you: COL.amber, failed: COL.red, blocked: COL.red, waiting: COL.grey, planned: COL.grey, skipped: COL.grey };
  var STATE_WORD = { done: 'done', working: 'working', needs_you: 'needs you', failed: 'failed', blocked: 'failed', waiting: 'next', planned: 'planned', skipped: 'skipped' };
  var SHAPE = { planet: 'ring', agent: 'tri', problem: 'spike', waiting: 'oct', artifact: 'cube', step: 'hex' };
  var MARK_COL = { planet: '#9FC4E8', agent: '#CFE3FF', problem: COL.red, waiting: COL.amber, artifact: '#3FD6C0', step: COL.cyan };
  var PRI = { locked: 0, need: 1, problem: 2, agent: 3, planet: 4, waiting: 2, artifact: 5, step: 5 };
  var SVGNS = 'http://www.w3.org/2000/svg';
  var H = null;                // the DOM (kept across entries; hidden when off)
  var S = null;                // this entry's state

  function clamp(v, a, b) { return v < a ? a : v > b ? b : v; }
  function num(v, d) { var x = Number(v); return isFinite(x) ? x : d; }
  function ease(k, dt) { return 1 - Math.exp(-k * Math.max(0, dt)); }
  function px(v) { return Math.round(v) + 'px'; }
  function r1(v) { return Math.round(v * 10) / 10; }
  function r3(v) { return Math.round(v * 1000) / 1000; }
  function doc() { return g.document; }
  function el(tag, cls, parent) { var e = doc().createElement(tag); if (cls) e.className = cls; if (parent) parent.appendChild(e); return e; }
  function svgEl(tag, attrs, parent) { var e = doc().createElementNS(SVGNS, tag); Object.keys(attrs || {}).forEach(function (k) { e.setAttribute(k, attrs[k]); }); if (parent) parent.appendChild(e); return e; }
  function sizeNow() { return { w: Math.max(1, g.innerWidth || 1), h: Math.max(1, g.innerHeight || 1) }; }
  function font(u, k) { return Math.max(12, Math.round(k * u)) + 'px'; }
  function text(e, t) { t = String(t); if (e.textContent !== t) e.textContent = t; }
  function show(e, on) { var v = on ? '' : 'none'; if (e.style.display !== v) e.style.display = v; }
  function say(t) { if (H && H.live && t && H.live.textContent !== t) H.live.textContent = t; }
  function hexA(hex, a) { var v = parseInt(String(hex || '#000').replace('#', ''), 16) || 0; return 'rgba(' + (v >> 16 & 255) + ',' + (v >> 8 & 255) + ',' + (v & 255) + ',' + a + ')'; }
  function plural(n, one, many) { return n + ' ' + (n === 1 ? one : many); }

  // ---------------------------------------------------------------- small drawings (SVG strings, our own)
  function shipIconSvg(colour) {
    return '<svg viewBox="0 0 40 25" width="100%" height="100%"><path d="M20 0 L23.2 9 L40 15 L22.4 12.5 L20 15.5 L17.6 12.5 L0 15 L16.8 9 Z" fill="#F4F8FF" stroke="#0A1020" stroke-width="1.2"/>' +
      '<path d="M20 2.5 L24.8 11 L20 10 L15.2 11 Z" fill="' + colour + '"/></svg>';
  }
  function hexSvg(fill, glyph) {
    var pts = []; for (var i = 0; i < 6; i++) { var a = Math.PI / 6 + i * Math.PI / 3; pts.push((12 + Math.cos(a) * 11.4).toFixed(2) + ',' + (12 + Math.sin(a) * 11.4).toFixed(2)); }
    return '<svg viewBox="0 0 24 24" width="100%" height="100%"><polygon points="' + pts.join(' ') + '" fill="' + fill + '" stroke="#1A0A00" stroke-width="1.4"/>' +
      '<polygon points="' + pts.join(' ') + '" fill="url(#cm3dhex)"/><defs><linearGradient id="cm3dhex" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fff" stop-opacity="0.5"/><stop offset="0.5" stop-color="#fff" stop-opacity="0"/></linearGradient></defs>' +
      '<text x="12" y="17" text-anchor="middle" font-family="Barlow Condensed,Arial Narrow,sans-serif" font-weight="600" font-size="15" fill="#FFFFFF">' + glyph + '</text></svg>';
  }
  // the followed workflow's milestones: three flat segments that fill (a skin's feel3d.milestoneRings: 1 draws rings)
  function milestonesSvg(n, filled) {
    var w = 30, h = 10, gap = 5, out = '<svg viewBox="0 0 ' + (n * w + (n - 1) * gap) + ' 24" width="100%" height="100%">';
    for (var i = 0; i < n; i++) {
      var x = i * (w + gap);
      out += i < filled
        ? '<rect x="' + x + '" y="7" width="' + w + '" height="' + h + '" rx="2" fill="#4DE8E0"/>'
        : '<rect x="' + (x + 1) + '" y="8" width="' + (w - 2) + '" height="' + (h - 2) + '" rx="2" fill="none" stroke="#3E5A6A" stroke-width="2"/>';
    }
    return out + '</svg>';
  }
  function ringsSvg(n, filled) {
    var s = 24, gap = 0.2, out = '<svg viewBox="0 0 ' + (s * (n + (n - 1) * gap)).toFixed(1) + ' ' + s + '" width="100%" height="100%"><defs><linearGradient id="cm3dring" x1="0" y1="0" x2="1" y2="1">' +
      '<stop offset="0" stop-color="#FFF6B0"/><stop offset="0.45" stop-color="#E8C020"/><stop offset="1" stop-color="#8A6A10"/></linearGradient></defs>';
    for (var i = 0; i < n; i++) {
      var cx = s / 2 + i * s * (1 + gap), cy = s / 2, rx = s * 0.32, ry = s * 0.42;
      out += i < filled
        ? '<ellipse cx="' + cx + '" cy="' + cy + '" rx="' + rx + '" ry="' + ry + '" fill="none" stroke="#000" stroke-opacity="0.6" stroke-width="5.2"/>' +
          '<ellipse cx="' + cx + '" cy="' + cy + '" rx="' + rx + '" ry="' + ry + '" fill="none" stroke="url(#cm3dring)" stroke-width="3.8"/>' +
          '<ellipse cx="' + (cx - rx * 0.35) + '" cy="' + (cy - ry * 0.55) + '" rx="1.2" ry="2.1" fill="#FFFFFF" opacity="0.9"/>'
        : '<ellipse cx="' + cx + '" cy="' + cy + '" rx="' + rx + '" ry="' + ry + '" fill="none" stroke="#3E5A6A" stroke-opacity="0.9" stroke-width="3.2"/>';
    }
    return out + '</svg>';
  }
  // a speaker's portrait (no character art: a helmet in the speaker's colour, a visor, a grid behind; comps/src/lib/hud.js)
  var PORTRAITS = {};
  function portraitURL(colour, alert) {
    var key = colour + (alert ? ':a' : '');
    if (PORTRAITS[key]) return PORTRAITS[key];
    var w = 96, h = 96, c = doc().createElement('canvas'); c.width = w * 2; c.height = h * 2;
    var x = c.getContext('2d'); x.scale(2, 2);
    var gr = x.createLinearGradient(0, 0, 0, h); gr.addColorStop(0, alert ? '#401418' : '#12403E'); gr.addColorStop(1, alert ? '#1A0608' : '#0A2A2C'); x.fillStyle = gr; x.fillRect(0, 0, w, h);
    x.strokeStyle = alert ? 'rgba(255,90,95,0.2)' : 'rgba(47,217,192,0.18)'; x.lineWidth = 1;
    for (var i = 0; i < w; i += 8) { x.beginPath(); x.moveTo(i, 0); x.lineTo(i, h); x.stroke(); x.beginPath(); x.moveTo(0, i); x.lineTo(w, i); x.stroke(); }
    gr = x.createLinearGradient(0, h * 0.7, 0, h); gr.addColorStop(0, '#D8DEE8'); gr.addColorStop(1, '#5A6070');
    x.fillStyle = gr; x.beginPath(); x.moveTo(w * 0.08, h); x.quadraticCurveTo(w * 0.12, h * 0.72, w * 0.5, h * 0.7); x.quadraticCurveTo(w * 0.88, h * 0.72, w * 0.92, h); x.fill();
    x.fillStyle = colour; x.fillRect(w * 0.28, h * 0.82, w * 0.44, h * 0.08);
    var cx = w * 0.5, cy = h * 0.44, r = w * 0.29;
    gr = x.createRadialGradient(cx - r * 0.35, cy - r * 0.45, r * 0.1, cx, cy, r * 1.05); gr.addColorStop(0, '#FFFFFF'); gr.addColorStop(0.25, colour); gr.addColorStop(1, '#0A1428');
    x.fillStyle = gr; x.beginPath(); x.ellipse(cx, cy, r, r * 1.06, 0, 0, Math.PI * 2); x.fill(); x.strokeStyle = 'rgba(0,0,0,0.6)'; x.lineWidth = 1.5; x.stroke();
    gr = x.createLinearGradient(cx - r, cy - r * 0.1, cx + r, cy + r * 0.4); gr.addColorStop(0, '#0A1A2A'); gr.addColorStop(0.45, '#123850'); gr.addColorStop(0.6, '#9FF0FF'); gr.addColorStop(0.75, '#123850'); gr.addColorStop(1, '#06101A');
    x.fillStyle = gr; x.beginPath(); x.ellipse(cx, cy + r * 0.12, r * 0.78, r * 0.42, 0, 0, Math.PI * 2); x.fill();
    x.fillStyle = 'rgba(255,255,255,0.75)'; x.beginPath(); x.ellipse(cx - r * 0.38, cy - r * 0.02, r * 0.16, r * 0.06, -0.4, 0, Math.PI * 2); x.fill();
    x.fillStyle = 'rgba(255,255,255,0.85)'; x.fillRect(cx - 1.5, cy - r * 1.02, 3, r * 0.5);
    x.fillStyle = '#E8D820'; x.fillRect(cx + r * 0.75, cy - r * 0.25, 4, r * 0.55);
    x.fillStyle = 'rgba(255,255,255,0.06)'; for (var y = 0; y < h; y += 3) x.fillRect(0, y, w, 1);
    PORTRAITS[key] = c.toDataURL();
    return PORTRAITS[key];
  }

  // ---------------------------------------------------------------- the DOM, built once
  function build() {
    if (H && H.root && H.root.isConnected) return H;
    var d = doc(), root = d.getElementById('hud3d');
    if (!root) { root = d.createElement('div'); root.id = 'hud3d'; d.body.appendChild(root); }
    root.innerHTML = ''; root.className = 'hud3d'; root.setAttribute('aria-hidden', 'true');
    H = { root: root };
    H.vig = el('div', 'vig3d', root);
    var fr = H.frame = el('div', 'hud3d-frame', root);
    // top left: the gauge and its words, the steps counter with the milestone rings
    H.gauge = el('div', 'gauge3d', fr); H.gbar = el('div', 'bar', H.gauge); H.gfill = el('div', 'fill', H.gbar); el('div', 'gloss', H.gbar);
    H.gcap = el('div', 'gcap', H.gauge); H.gnum = el('b', '', H.gcap); H.gword = el('span', '', H.gcap); H.gsub = el('div', 'gsub', H.gauge);
    H.counter = el('div', 'steps3d', fr); H.cnum = el('div', 'num', H.counter); H.rings = el('div', 'rings', H.counter); H.ccap = el('div', 'cap', H.counter);
    // top right: agents working, the boost meter, need you
    H.agents = el('div', 'agents3d', fr); H.aicon = el('i', 'icon', H.agents); H.anum = el('b', 'num', H.agents); H.aword = el('span', 'word', H.agents);
    H.boost = el('div', 'boost3d', fr); H.bfill = el('div', 'fill', H.boost); el('div', 'gloss', H.boost); H.blabel = el('span', 'label', H.boost); text(H.blabel, 'BOOST');
    H.need = el('div', 'need3d', fr); H.badges = el('span', 'badges', H.need); H.nnum = el('b', 'num', H.need); H.nword = el('span', 'word', H.need);
    // left: the FLOW block (the one-line mission block) and the tube
    H.block = el('div', 'flow3d', fr); H.pill = el('span', 'pill', H.block); text(H.pill, 'FLOW'); H.bt1 = el('div', 't1', H.block); H.bt2 = el('div', 't2', H.block);
    H.tube = el('div', 'tube3d', H.block); H.tfill = el('div', 'fill', H.tube); el('div', 'gloss', H.tube);
    // bottom: comms, the radar
    H.comms = el('div', 'comms3d', fr); H.cgauge = el('div', 'cgauge', H.comms); H.cgfill = el('div', 'fill', H.cgauge);
    H.cwho = el('div', 'who', H.comms);
    ['tl', 'tr', 'bl', 'br'].forEach(function (k) { el('i', 'corner ' + k, H.comms); });
    H.portrait = el('div', 'portrait', H.comms); H.pimg = el('img', '', H.portrait); H.pimg.alt = '';
    H.ctext = el('div', 'text', H.comms); H.csub = el('div', 'sub', H.comms);
    H.radar = el('div', 'radar3d', fr); H.rcv = el('canvas', '', H.radar); H.rlabel = el('div', 'label', H.radar); text(H.rlabel, 'Map');
    // the reticle, the lock brackets, the arc brackets and the target's leader: one SVG over the whole screen
    H.ret = el('div', 'reticle', root);
    H.rsvg = svgEl('svg', { width: '100%', height: '100%' }, H.ret);
    H.lead = svgEl('g', {}, H.rsvg); H.leadU = svgEl('polyline', { fill: 'none', stroke: '#000', 'stroke-opacity': '0.6', 'stroke-width': '4' }, H.lead);
    H.leadL = svgEl('polyline', { fill: 'none', 'stroke-width': '2' }, H.lead); H.leadDot = svgEl('circle', { r: '4', stroke: '#000' }, H.lead);
    H.arcs = svgEl('g', {}, H.rsvg); H.arcU = svgEl('path', { fill: 'none', stroke: '#000', 'stroke-opacity': '0.6', 'stroke-width': '6' }, H.arcs); H.arcL = svgEl('path', { fill: 'none', 'stroke-width': '3' }, H.arcs);
    H.brk = svgEl('g', {}, H.rsvg); H.brkU = svgEl('path', { fill: 'none', stroke: '#000', 'stroke-opacity': '0.65', 'stroke-width': '5.5' }, H.brk); H.brkPath = svgEl('path', { fill: 'none', 'stroke-width': '3' }, H.brk);
    H.far = svgEl('g', {}, H.rsvg);
    H.farU = svgEl('circle', { r: '30', fill: 'none', stroke: '#04140F', 'stroke-width': '5' }, H.far);
    H.farRing = svgEl('circle', { r: '30', fill: 'none', 'stroke-width': '2.4' }, H.far);
    H.farTicks = [[0, -46, 0, -36], [0, 36, 0, 46], [-46, 0, -36, 0], [36, 0, 46, 0]].map(function (q) {
      svgEl('line', { x1: q[0], y1: q[1], x2: q[2], y2: q[3], stroke: '#04140F', 'stroke-width': '6' }, H.far);
      return svgEl('line', { x1: q[0], y1: q[1], x2: q[2], y2: q[3], 'stroke-width': '3' }, H.far);
    });
    H.farDot = svgEl('circle', { r: '2.2' }, H.far);
    H.near = svgEl('g', {}, H.rsvg);
    var nd = 'M-14,-7 L-14,-14 L-7,-14 M7,-14 L14,-14 L14,-7 M14,7 L14,14 L7,14 M-7,14 L-14,14 L-14,7';
    svgEl('path', { d: nd, fill: 'none', stroke: '#04140F', 'stroke-width': '5' }, H.near);
    H.nearBox = svgEl('path', { d: nd, fill: 'none', 'stroke-width': '2.6' }, H.near);
    H.target = el('div', 'target3d', root); H.tt1 = el('div', 't1', H.target); H.tt2 = el('div', 't2', H.target);
    H.marks = el('div', 'marks3d', root); H.markPool = [];
    H.arrows = el('div', 'arrows3d', root); H.arrowPool = [];
    // more than two contacts behind: one pill at the bottom middle (comps/3d-cruise.png "2 problems behind you")
    H.behind = el('div', 'behind3d', root); H.behindChev = el('i', 'chev', H.behind); H.behindBox = el('div', 'box', H.behind);
    H.behindT1 = el('div', 't1', H.behindBox); H.behindT2 = el('div', 't2', H.behindBox);
    H.behindChev.innerHTML = '<svg width="26" height="30" viewBox="0 0 26 30"><path d="M3 2 L13 11 L23 2 L23 8 L13 17 L3 8 Z" stroke="#200000" stroke-width="1.2"/>' +
      '<path d="M3 12 L13 21 L23 12 L23 18 L13 27 L3 18 Z" stroke="#200000" stroke-width="1.2" opacity="0.7"/></svg>';
    H.callout = el('div', 'callout3d', root); H.ctag = el('span', 'tag', H.callout); H.cline = el('i', 'leader', H.callout);
    H.pip = el('div', 'pip3d', root); H.piplabel = el('span', 'label', H.pip);
    H.plate = el('div', 'plate3d', root); H.plateK = el('span', 'kicker', H.plate); H.plateT = el('b', 'title', H.plate);
    H.caption = el('div', 'caption3d', root); H.cap1 = el('b', 't1', H.caption); H.cap2 = el('span', 't2', H.caption);
    H.tbar = el('div', 'tbar3d', root); H.tbarFill = el('i', 'fill', H.tbar); H.tbarText = el('span', 'text', H.tbar);
    H.card = el('div', 'card3d', root); H.cardHead = el('div', 'head', H.card); H.cardInset = el('div', 'inset', H.card); H.cardText = el('div', 'text', H.card);
    text(H.cardHead, 'TARGET');
    H.mcard = el('div', 'mcard3d', root); H.mhead = el('div', 'head', H.mcard); H.mkick = el('span', 'kicker', H.mhead); H.mtitle = el('b', 'title', H.mhead);
    H.mright = el('span', 'right', H.mhead); H.mobj = el('div', 'objective', H.mcard); H.mtrack = el('div', 'track', H.mcard); H.magents = el('div', 'agents', H.mcard);
    text(H.mkick, 'WORKFLOW RUNNING');
    H.band = el('div', 'band3d', root); H.bandT = el('b', 'title', H.band); H.bandS = el('span', 'sub', H.band);
    var live = d.getElementById('a11y3d');
    if (!live) { live = d.createElement('div'); live.id = 'a11y3d'; live.className = 'sr3d'; live.setAttribute('role', 'status'); live.setAttribute('aria-live', 'polite'); d.body.appendChild(live); }
    H.live = live;
    return H;
  }

  // ---------------------------------------------------------------- the HUD frame and its slots
  function frameRect(sz, aspect, phone) {
    var w = sz.w, h = sz.h, fw = w;
    if (!phone && aspect === '16:9') fw = Math.min(w, h * 16 / 9);
    else if (!phone && aspect === '4:3') fw = Math.min(w, h * 4 / 3);
    return { x: (w - fw) / 2, y: 0, w: fw, h: h };
  }
  function layoutOf(skin3d, portrait) {
    if (portrait) return PHONE;
    var L = ((skin3d && skin3d.feel3d) || {}).hudLayout;
    if (!L || typeof L !== 'object') return LAYOUT;
    var out = {}; Object.keys(LAYOUT).forEach(function (k) { out[k] = LAYOUT[k]; });
    Object.keys(L).forEach(function (k) { out[k] = L[k]; });
    out.units = L.units === 'fox320' ? 'fox320' : 'percent';
    if (out.units === 'fox320') {
      if (!('need' in L) && L.running) out.need = L.running;
      Object.keys(LAYOUT).forEach(function (k) { if (k !== 'units' && !(k in L) && !(k === 'need' && out.need)) out[k] = null; });
    }
    return out;
  }
  // a slot's box in viewport px {x, y, w, h}, or null when the layout has none
  function slot(key, F, u) {
    var L = S.layout, v = L[key];
    if (!Array.isArray(v) || Array.isArray(v[0])) {
      if (L.units === 'fox320') { var p = LAYOUT[key]; return p ? { x: F.x + p[0] / 100 * F.w, y: F.y + p[1] / 100 * F.h, w: (p[2] - p[0]) / 100 * F.w, h: (p[3] - p[1]) / 100 * F.h } : null; }
      return null;
    }
    if (L.units === 'fox320') {
      var x = v[0], y = v[1], w = v[2] != null ? v[2] : 48, hh = v[3] != null ? v[3] : 12;
      var left = x < 160 ? F.x + x * u : F.x + F.w - (320 - x) * u;
      return { x: left, y: F.y + y * u, w: w * u, h: hh * u };
    }
    return { x: F.x + v[0] / 100 * F.w, y: F.y + v[1] / 100 * F.h, w: (v[2] - v[0]) / 100 * F.w, h: (v[3] - v[1]) / 100 * F.h };
  }
  function put(e, b, withH) {
    if (!b) { show(e, false); return; }
    e.style.left = px(b.x); e.style.top = px(b.y); e.style.width = px(b.w); if (withH) e.style.height = px(b.h);
  }

  // ---------------------------------------------------------------- where things are (the scene, by uid)
  function scenePos(ctx, uid) {
    var w = S.world, THREE = ctx.THREE;
    var p = (w.planets || []).filter(function (x) { return x.uid === uid; })[0];
    if (p) return { v: new THREE.Vector3(p.x, 0, p.y), r: p.r || 0, kind: 'planet', obj: p };
    if (uid === 'star' && w.star) return { v: new THREE.Vector3(w.star.x, 0, w.star.y), r: w.star.r || 0, kind: 'star', obj: w.star };
    var hit = S.where[uid];
    if (hit) return hit;
    var c = (w.craft || []).filter(function (x) { return x.uid === uid; })[0];
    if (c) return { v: new THREE.Vector3(c.x, 8, c.y), r: 7, kind: 'agent', obj: c };
    return null;
  }
  // one pass over the scene a frame: every object a module tagged with a uid (and each instance of an instanced mesh)
  function indexScene(ctx) {
    var out = {}, THREE = ctx.THREE, M = S.m4 || (S.m4 = new THREE.Matrix4());
    ctx.scene.traverse(function (o) {
      if (!o.visible) return;
      var ud = o.userData || {};
      if (o.isInstancedMesh && Array.isArray(ud.uids)) {
        for (var i = 0; i < ud.uids.length && i < o.count; i++) {
          o.getMatrixAt(i, M);
          out[ud.uids[i]] = { v: new THREE.Vector3().setFromMatrixPosition(M).applyMatrix4(o.matrixWorld), r: 6, kind: ud.kind || 'object' };
        }
      } else if (ud.uid && !out[ud.uid]) {
        out[ud.uid] = { v: o.getWorldPosition(new THREE.Vector3()), r: 6, kind: ud.kind || 'object', mesh: o };
      }
    });
    return out;
  }
  // is something hidden behind a planet (three's align-html-to-3d-w-hiding: a ray-sphere test from the eye)
  function occluded(ctx, v, selfUid) {
    var cam = ctx.camera.position, dx = v.x - cam.x, dy = v.y - cam.y, dz = v.z - cam.z, L = Math.hypot(dx, dy, dz);
    if (L < 1e-6) return false;
    dx /= L; dy /= L; dz /= L;
    var ps = S.world.planets || [];
    for (var i = 0; i < ps.length; i++) {
      var p = ps[i]; if (p.uid === selfUid) continue;
      var ox = p.x - cam.x, oy = -cam.y, oz = p.y - cam.z, t = ox * dx + oy * dy + oz * dz;
      if (t <= 0 || t >= L) continue;
      var d2 = ox * ox + oy * oy + oz * oz - t * t;
      if (d2 < p.r * p.r * 0.98) return true;
    }
    return false;
  }
  // a point in camera space (x right, y up, z toward the viewer): the off-screen bearing never goes through project()
  function camSpace(ctx, v) { var q = v.clone(); ctx.camera.updateMatrixWorld(); q.applyMatrix4(ctx.camera.matrixWorldInverse); return q; }
  function inCentre(x, y, sz, pad) { pad = pad || 0; return x > sz.w / 3 - pad && x < sz.w * 2 / 3 + pad && y > sz.h / 3 - pad && y < sz.h * 2 / 3 + pad; }
  function boxInCentre(b, sz) { return b.x < sz.w * 2 / 3 && b.x + b.w > sz.w / 3 && b.y < sz.h * 2 / 3 && b.y + b.h > sz.h / 3; }
  // a sphere's apparent radius in px (the half angle it fills)
  function radiusPx(ctx, p, sz) {
    var cam = ctx.camera, dd = Math.hypot(p.x - cam.position.x, cam.position.y, p.y - cam.position.z);
    return (sz.h / 2) / Math.tan(cam.fov * D2R / 2) * Math.tan(Math.asin(Math.min(0.999, p.r / Math.max(dd, p.r + 1e-3))));
  }

  // ---------------------------------------------------------------- the counts the HUD shows (the page's own words)
  function needYouOf(w) {
    var planets = w.planets || [], counts = w.counts || {};
    var perPlanet = planets.reduce(function (s, p) { return s + (((p.counts || {}).need_you) || 0); }, 0);
    return typeof counts.need_you === 'number' && !(!(counts.total > 0) && perPlanet > 0) ? counts.need_you : perPlanet;
  }
  // what waits for the person, said by its kind when it is one kind ("1 finished task to review"), else by its count (the twin of
  // CM.countNoun in ui/words.js)
  function needNoun(c, n) {
    c = c || {};
    var a = c.answer > 0 ? c.answer : 0, r = c.review > 0 ? c.review : 0;
    if (n > 0 && r === n && !a) return n === 1 ? 'finished task to review' : 'finished tasks to review';
    if (n > 0 && a === n && !r) return n === 1 ? 'question for you' : 'questions for you';
    return 'waiting for you';
  }
  function stateWords(p) {
    var c = p.counts || {}, probs = c.problems || 0, ny = c.need_you || 0, dec = c.decisions || 0;
    if (probs) return plural(probs, 'problem', 'problems');
    if (ny) return ny + ' ' + needNoun(c, ny);
    if (dec) return dec + (dec === 1 ? ' decision to make' : ' decisions to make');
    return 'nothing waiting for you';
  }
  // the gauge's one line: which folder and what when exactly one needs the person, else how many of how many
  function gaugeWords(planets, clear, total) {
    var attn = total - clear;
    if (attn === 1) {
      var one = planets.filter(function (p) { return p.health === 'broken' || p.health === 'waiting'; })[0], w = one ? stateWords(one) : '';
      if (one && w !== 'nothing waiting for you') { var nm = String(one.label); return { num: nm.length > 22 ? nm.slice(0, 21) + '\u2026' : nm, word: ' needs you: ' + w, named: ((one.counts || {}).need_you || 0) > 0 && !((one.counts || {}).problems) ? (one.counts || {}).need_you : 0 }; }
    }
    return { num: attn ? attn + ' of ' + total : total ? 'All ' + total : 'No', word: attn ? (attn === 1 ? ' folders needs you' : ' folders need you') : total ? ' folders: nothing to do' : ' folders yet' };
  }
  // how far a planet is, in words a newcomer reads at once (never "8 s away")
  function distanceWords(eta) { return eta <= 0 ? 'you are here' : eta <= 3 ? 'close by' : 'about ' + eta + ' seconds away'; }
  function kindColour(p) { var c = p.counts || {}; return c.problems ? COL.red : (c.need_you || c.decisions) ? COL.amber : COL.teal; }

  // ---------------------------------------------------------------- comms (priority, hold and drop)
  // 3 a problem or a failure, 2 needs you, 1 a step change, 0 an agent's chatter; a lower line than the one showing is
  // dropped, an equal or higher one replaces it; the whole sentence at once; held hold + perChar per character, cap
  function sayLine(line) {
    if (!S || !line || !line.text) return false;
    var R = S.feel.radio || { hold: 2, perChar: 0.04, cap: 8 }, cur = S.comms.current, pr = clamp(Math.round(num(line.priority, 0)), 0, 3);
    if (cur && S.t < cur.until && pr < cur.priority) { S.comms.dropped++; return false; }
    var t = String(line.text), hold = Math.min(num(R.cap, 8), num(R.hold, 2) + num(R.perChar, 0.04) * t.length);
    S.comms.current = { priority: pr, who: String(line.who || ''), text: t, sub: String(line.sub || ''), colour: line.colour || null, hold: r3(hold), at: S.t, until: S.t + hold };
    S.comms.said++;
    say((line.who ? line.who + ': ' : '') + t);
    return true;
  }
  C.comms = { say: function (line) { return sayLine(line); } };
  C.onDebug = function (what, n) { if (what === 'comms') sayLine({ priority: 0, who: 'Test', text: 'Test line ' + n + ' from the debug switch' }); };

  // ---------------------------------------------------------------- the lock (web-flight-simulator weaponSystem.js:263-292)
  // NONE -> LOCKING -> LOCKED inside a cone round the nose (10 degrees: cos 0.985) after the dwell (neutral 0.8 s); another
  // candidate starts over; the private skin locks only while E is held (its hold time). A lock stays when the nose
  // moves off it, until another candidate locks.
  function candidate(ctx) {
    var w = S.world, A = FH.att || {}, sp = w.ship || {}, hdg = (Number(sp.hdg) || 0) + (A.lead || 0) * D2R, pit = (A.pitch || 0) * D2R;
    var ox = Number(sp.x) || 0, oz = Number(sp.y) || 0, oy = A.alt || 0;
    var fx = Math.cos(hdg) * Math.cos(pit), fy = Math.sin(pit), fz = Math.sin(hdg) * Math.cos(pit);
    var cone = Math.cos(S.feel.lockS.cone * D2R), best = null, bestK = -2;
    (w.planets || []).forEach(function (p) {
      var dx = p.x - ox, dy = -oy, dz = p.y - oz, L = Math.hypot(dx, dy, dz);
      if (L < p.r) return;
      var k = (dx * fx + dy * fy + dz * fz) / L, ang = Math.asin(Math.min(1, p.r / L));
      // the cone widens by the body's own angular radius: aiming anywhere on its disc counts
      var coneK = Math.cos(Math.min(Math.PI / 2, Math.acos(cone) + ang * 0.6));
      if (k >= coneK && k > bestK) { bestK = k; best = p; }
    });
    return best;
  }
  function lockUpdate(ctx, dt) {
    var L = S.lock, c = candidate(ctx), ls = S.feel.lockS, need = ls.hold > 0 ? ls.hold : ls.dwell, holding = ls.hold > 0;
    S.cand = c ? c.uid : null;
    if (c && (L.state !== 'locked' || L.uid !== c.uid)) {
      if (L.state === 'locking' && L.uid === c.uid) { if (!holding || S.eHeld) L.ms += dt * 1000; else { L.state = L.prevLock ? 'locked' : 'none'; L.uid = L.prevLock; L.ms = 0; } }
      else if (!holding || S.eHeld) { L.prevLock = L.state === 'locked' ? L.uid : L.prevLock; L.state = 'locking'; L.uid = c.uid; L.ms = 0; }
      if (L.state === 'locking' && L.ms >= need * 1000) { L.state = 'locked'; L.prevLock = c.uid; say('Locked: ' + c.label); }
    } else if (!c && L.state === 'locking') { L.state = L.prevLock ? 'locked' : 'none'; L.uid = L.prevLock || null; L.ms = 0; }
    var p = L.uid ? (S.world.planets || []).filter(function (x) { return x.uid === L.uid; })[0] : null;
    if (L.uid && !p) { L.state = 'none'; L.uid = null; L.prevLock = null; }
    FH.lock = { state: L.state, uid: L.uid, kind: p ? 'planet' : null, label: p ? String(p.label) : '' };
  }
  function lockTo(uid, label) { var L = S.lock; L.state = 'locked'; L.uid = uid; L.prevLock = uid; L.ms = 0; say('Locked: ' + (label || uid)); }
  // L: lock what the nose is on at once (neutral) or start the hold (private); L on a locked planet far away flies there
  // (E was the lock key until the 3D flight took Q and E for the wings)
  function hudKey(e) {
    var k = String(e.key || '').toLowerCase();
    if (!S) return false;
    if (k === 'l') {
      if (e.repeat) return true;
      var L = S.lock, c = candidate(S.ctx);
      S.eHeld = true;
      if (L.state === 'locked' && (!c || c.uid === L.uid)) {
        var p = (S.world.planets || []).filter(function (x) { return x.uid === L.uid; })[0], sp = S.world.ship || {};
        if (p && Math.hypot(p.x - sp.x, p.y - sp.y) - p.r > 1500 && typeof FH.flyTo === 'function') FH.flyTo(p.uid);
        return true;
      }
      if (c && S.feel.lockS.hold <= 0) lockTo(c.uid, c.label);
      return true;
    }
    if (k === 'tab') {
      if (e.repeat) return true;
      var list = (S.world.planets || []).slice().sort(function (a, b) {
        var ca = a.counts || {}, cb = b.counts || {};
        return ((cb.need_you || 0) - (ca.need_you || 0)) || ((cb.problems || 0) - (ca.problems || 0)) || String(a.label).localeCompare(String(b.label));
      });
      if (!list.length) return true;
      var i = list.map(function (x) { return x.uid; }).indexOf(S.lock.uid), n = list[(i + 1) % list.length];
      lockTo(n.uid, n.label);
      return true;
    }
    return false;
  }
  function hudKeyUp(e) { if (String(e.key || '').toLowerCase() === 'l' && S) S.eHeld = false; }
  // a tap on the 3D canvas (the touch deck): lock the planet under it
  function tapLock(x, y) {
    if (!S) return;
    var hit = typeof g.__3dPick === 'function' ? g.__3dPick(x, y) : null;
    var p = hit && hit.uid ? (S.world.planets || []).filter(function (q) { return q.uid === hit.uid; })[0] : null;
    if (!p && S.cand) p = (S.world.planets || []).filter(function (q) { return q.uid === S.cand; })[0];
    if (p) lockTo(p.uid, p.label);
  }

  // ---------------------------------------------------------------- the radar (webglite scannerShips.ts:93-123, idea)
  // the north-up map round the ship on a teal glass grid; each contact a base dot on the plane and a stalk to its altitude
  // (capped at 18% of the radar's height), the dot's size by its altitude band; the followed workflow's route dashed amber
  // a folder's name beside its dot: the first of eight spots (right, left, above, below, then the corners) that keeps
  // clear of every reserved box (all dots, the pilot's arrow and its stalk, the other craft) and of the other names, and
  // stays inside the radar; no such spot and the name is left off, so a word never sits over a marker
  function radarFree(b, placed, W, Hh) {
    if (!(b.x > 2 && b.x + b.w < W - 2 && b.y > 2 && b.y + b.h < Hh - 2)) return false;
    for (var i = 0; i < placed.length; i++) { var q = placed[i]; if (!(b.x > q.x + q.w || b.x + b.w < q.x || b.y > q.y + q.h || b.y + b.h < q.y)) return false; }
    return true;
  }
  function radarLabelSpot(qx, qy, r, lw, placed, W, Hh) {
    var h = 14, gap = 4, x0 = qx + r + gap, x1 = qx - r - gap - lw, xc = qx - lw / 2, y0 = qy - 7, ya = qy - r - gap - h, yb = qy + r + gap;
    for (var i = 0; i < 8; i++) {
      var b = { x: i === 0 || i === 4 || i === 5 ? x0 : i === 1 || i === 6 || i === 7 ? x1 : xc, y: i < 2 ? y0 : i === 2 || i === 4 || i === 6 ? ya : yb, w: lw, h: h };
      if (radarFree(b, placed, W, Hh)) return b;
    }
    return null;
  }
  FH.radarLabelSpot = radarLabelSpot;
  function radarDraw(ctx, b, look) {
    var cv = H.rcv, dpr = Math.min(2, g.devicePixelRatio || 1), W = Math.max(40, Math.round(b.w)), Hh = Math.max(40, Math.round(b.h));
    if (cv.width !== Math.round(W * dpr) || cv.height !== Math.round(Hh * dpr)) { cv.width = Math.round(W * dpr); cv.height = Math.round(Hh * dpr); cv.style.width = W + 'px'; cv.style.height = Hh + 'px'; }
    var x = cv.getContext('2d'); x.setTransform(dpr, 0, 0, dpr, 0, 0); x.clearRect(0, 0, W, Hh);
    var w = S.world, sp = w.ship || {}, A = FH.att || {}, cx = W / 2, cy = Hh / 2;
    var range = Math.max(1600, S.radarRange || 2400), k = (Math.min(W, Hh) * 0.46) / range, stalkCap = Hh * 0.18;
    var P = function (vx, vy) { return [cx + (vx - sp.x) * k, cy + (vy - sp.y) * k]; };
    x.strokeStyle = '#2FD9C0'; x.lineWidth = 1;
    for (var i = 1; i < 8; i++) {
      x.globalAlpha = i === 4 ? 0.5 : 0.18;
      x.beginPath(); x.moveTo(0, Math.round(Hh * i / 8) + 0.5); x.lineTo(W, Math.round(Hh * i / 8) + 0.5); x.stroke();
      x.beginPath(); x.moveTo(Math.round(W * i / 8) + 0.5, 0); x.lineTo(Math.round(W * i / 8) + 0.5, Hh); x.stroke();
    }
    x.globalAlpha = 1;
    var stalks = [];
    var stalk = function (q, alt, col) {
      var s = clamp(alt * k * 1.4, -stalkCap, stalkCap);
      if (Math.abs(s) >= 1) { x.strokeStyle = col; x.globalAlpha = 0.85; x.lineWidth = 1.5; x.beginPath(); x.moveTo(q[0], q[1]); x.lineTo(q[0], q[1] - s); x.stroke(); x.globalAlpha = 0.6; x.fillStyle = col; x.fillRect(q[0] - 2, q[1] - 1, 4, 2); x.globalAlpha = 1; }
      return s;
    };
    var inside = function (q, m) { return q[0] > -m && q[0] < W + m && q[1] > -m && q[1] < Hh + m; };
    // the followed workflow's route, dashed amber, through its steps' planets
    var f = FH.follow;
    if (f && f.steps) {
      var pts = []; f.steps.forEach(function (st) { var p = (w.planets || []).filter(function (q) { return q.uid === st.planet; })[0]; if (p && (!pts.length || pts[pts.length - 1].uid !== p.uid)) pts.push(p); });
      if (pts.length > 1) { x.strokeStyle = '#FFAF00'; x.lineWidth = 2.2; x.setLineDash([6, 4]); x.beginPath(); pts.forEach(function (p, i) { var q = P(p.x, p.y); if (i) x.lineTo(q[0], q[1]); else x.moveTo(q[0], q[1]); }); x.stroke(); x.setLineDash([]); }
    }
    var st = w.star;
    if (st) { var q0 = P(st.x, st.y); if (inside(q0, 20)) { var rs = Math.max(5, Math.min(14, st.r * k)); x.fillStyle = '#FFB547'; x.beginPath(); x.arc(q0[0], q0[1], rs, 0, Math.PI * 2); x.fill(); x.strokeStyle = 'rgba(255,181,71,0.5)'; x.beginPath(); x.arc(q0[0], q0[1], rs + 4, 0, Math.PI * 2); x.stroke(); stalks.push({ kind: 'sun', stalk: 0 }); } }
    x.font = '600 18px "Barlow Condensed","Arial Narrow",sans-serif'; x.textBaseline = 'middle';
    // Reserve every marker before placing words: the pilot's arrow with its stalk, every dot, the sun, the other craft.
    var ownS = clamp((A.alt || 0) * k * 1.4, -stalkCap, stalkCap), placed = [{x: cx - 14, y: cy - Math.max(ownS, 0) - 15, w: 28, h: Math.abs(ownS) + 30}];
    (w.planets || []).forEach(function (p) { var q = P(p.x, p.y), r = Math.max(3.5, Math.min(11, p.r * k)) + 3; placed.push({x: q[0] - r, y: q[1] - r, w: r * 2, h: r * 2}); });
    if (st) { var sq = P(st.x, st.y), sr = Math.max(5, Math.min(14, st.r * k)) + 5; placed.push({x: sq[0] - sr, y: sq[1] - sr, w: sr * 2, h: sr * 2}); }
    (w.craft || []).forEach(function (cr) {
      if (cr.role === 'carrier') return;
      var q = P(cr.x, cr.y), s = clamp(20 * k * 1.4, -stalkCap, stalkCap); placed.push({x: q[0] - 8, y: q[1] - Math.max(s, 0) - 9, w: 16, h: Math.abs(s) + 18});
    });
    Object.keys(S.where).forEach(function (uid) {
      var o = S.where[uid]; if (o.kind !== 'foe') return;
      var q = P(o.v.x, o.v.z), s = clamp(o.v.y * k * 1.4, -stalkCap, stalkCap); placed.push({x: q[0] - 8, y: q[1] - Math.max(s, 0) - 9, w: 16, h: Math.abs(s) + 18});
    });
    (w.planets || []).forEach(function (p) {
      var c = p.counts || {}, col = c.problems ? COL.red : c.need_you ? COL.amber : '#5FA8D0', q = P(p.x, p.y), r = Math.max(3.5, Math.min(11, p.r * k));
      if (!inside(q, 12)) return;
      x.fillStyle = hexA(col, 0.35); x.strokeStyle = col; x.lineWidth = 1.5; x.beginPath(); x.arc(q[0], q[1], r, 0, Math.PI * 2); x.fill(); x.stroke();
      var lb = radarLabelSpot(q[0], q[1], r, x.measureText(String(p.label)).width, placed, W, Hh);
      if (lb) { placed.push(lb); x.fillStyle = '#BFEFE6'; x.fillText(String(p.label), lb.x, lb.y + 7); }
      stalks.push({ kind: 'planet', stalk: 0 });
    });
    (w.craft || []).forEach(function (cr) {
      if (cr.role === 'carrier') return;
      var q = P(cr.x, cr.y); if (!inside(q, 8)) return;
      var s = stalk(q, 20, cr.colour || '#CFE3FF'), band = 1.2;
      x.save(); x.translate(q[0], q[1] - s); x.rotate((Number(cr.hdg) || 0) + Math.PI / 2); x.fillStyle = cr.colour || '#CFE3FF'; x.strokeStyle = '#000';
      x.beginPath(); x.moveTo(0, -6 * band); x.lineTo(4.5 * band, 5); x.lineTo(0, 2.5); x.lineTo(-4.5 * band, 5); x.closePath(); x.fill(); x.stroke(); x.restore();
      stalks.push({ kind: 'agent', stalk: r1(Math.abs(s)) });
    });
    Object.keys(S.where).forEach(function (uid) {
      var o = S.where[uid]; if (o.kind !== 'foe') return;
      var q = P(o.v.x, o.v.z); if (!inside(q, 8)) return;
      var s = stalk(q, o.v.y, COL.red), band = Math.abs(o.v.y) > 120 ? 1.5 : Math.abs(o.v.y) > 30 ? 1.2 : 1;   // altitude band (tcrf §1.8.12)
      x.save(); x.translate(q[0], q[1] - s); x.rotate(Math.PI / 4); x.fillStyle = '#FF4040'; x.strokeStyle = '#000'; x.fillRect(-3.5 * band, -3.5 * band, 7 * band, 7 * band); x.strokeRect(-3.5 * band, -3.5 * band, 7 * band, 7 * band); x.restore();
      stalks.push({ kind: 'problem', stalk: r1(Math.abs(s)) });
    });
    // the own ship: an arrow at the middle, its stalk its altitude
    var own = stalk([cx, cy], A.alt || 0, look.ship);
    x.save(); x.translate(cx, cy - own); x.rotate((Number(sp.hdg) || 0) + Math.PI / 2);
    x.fillStyle = look.ship; x.strokeStyle = '#000'; x.lineWidth = 1; x.beginPath(); x.moveTo(0, -9); x.lineTo(6.8, 7.2); x.lineTo(0, 3.2); x.lineTo(-6.8, 7.2); x.closePath(); x.fill(); x.stroke(); x.restore();
    return { own: { stalk: r1(Math.abs(own)), alt: r1(A.alt || 0) }, contacts: stalks.length, range: range };
  }

  // ---------------------------------------------------------------- pooled markers and arrows
  var SHAPE_D = {
    ring: 'M0,-8 A8,8 0 1,1 -0.01,-8 Z', tri: 'M-11,-9 L11,-9 L0,10 Z', spike: 'M0,-10 L3,-3 L10,0 L3,3 L0,10 L-3,3 L-10,0 L-3,-3 Z',
    oct: 'M-3.6,-8.6 L3.6,-8.6 L8.6,-3.6 L8.6,3.6 L3.6,8.6 L-3.6,8.6 L-8.6,3.6 L-8.6,-3.6 Z', cube: 'M-6,-4 L4,-4 L4,6 L-6,6 Z M-6,-4 L-2,-8 L8,-8 L4,-4 M8,-8 L8,2 L4,6', hex: 'M0,-9 L8,-4.5 L8,4.5 L0,9 L-8,4.5 L-8,-4.5 Z'
  };
  function markEl(i) {
    var m = H.markPool[i];
    if (m) return m;
    m = { root: el('div', 'mark3d', H.marks) };
    m.svg = svgEl('svg', { width: '30', height: '30', viewBox: '-15 -15 30 30' }, m.root);
    m.under = svgEl('path', { fill: 'none', stroke: '#061020', 'stroke-width': '4.5' }, m.svg);
    m.shape = svgEl('path', { 'stroke-width': '2.2' }, m.svg);
    m.letter = svgEl('text', { x: '0', y: '0.5', 'text-anchor': 'middle', 'font-family': 'Barlow Condensed,Arial Narrow,sans-serif', 'font-weight': '600', 'font-size': '13', fill: '#FFFFFF' }, m.svg);
    m.text = el('span', 'name', m.root);
    H.markPool[i] = m; return m;
  }
  function arrowEl(i) {
    var a = H.arrowPool[i];
    if (a) return a;
    a = { root: el('div', 'arrow3d', H.arrows) };
    a.svg = svgEl('svg', { width: '40', height: '40', viewBox: '-20 -20 40 40' }, a.root);
    a.c1 = svgEl('polygon', { points: '-6,-12 8,0 -6,12', stroke: '#200000', 'stroke-width': '1.5' }, a.svg);
    a.c2 = svgEl('polygon', { points: '-17,-12 -3,0 -17,12', stroke: '#200000', 'stroke-width': '1.5', opacity: '0.6' }, a.svg);
    a.box = el('div', 'abox', a.root); a.t1 = el('div', 't1', a.box); a.t2 = el('div', 't2', a.box);
    H.arrowPool[i] = a; return a;
  }
  // the inset views (the LIVE inset and the target card): the planet's own material on a low sphere, a light, a grid
  function insetScene(ctx, uid, grid) {
    var THREE = ctx.THREE, key = uid + (grid ? ':g' : '');
    if (S.inset && S.inset.key === key) return S.inset;
    insetDispose();
    var p = (S.world.planets || []).filter(function (x) { return x.uid === uid; })[0]; if (!p) return null;
    var mesh = null; ctx.scene.traverse(function (o) { if (!mesh && o.userData && o.userData.uid === uid && o.isMesh) mesh = o; });
    var scene = new THREE.Scene(), mat = mesh ? (mesh.userData.cmReal || mesh.material) : new THREE.MeshLambertMaterial({ color: 0x4e79c8 });
    var sph = new THREE.Mesh(new THREE.SphereGeometry(p.r, 24, 16), mat); sph.position.set(p.x, 0, p.y); scene.add(sph);
    scene.add(new THREE.AmbientLight(0x9fb4ff, 0.3)); var dl = new THREE.DirectionalLight(0xfff1dc, 1.5); dl.position.set(p.x - p.r * 3, p.r * 2, p.y - p.r * 2); scene.add(dl);
    var extra = [];
    if (grid) { var gh = new THREE.GridHelper(p.r * 5, 10, 0x4de8e0, 0x1f7a78); gh.position.set(p.x, -p.r * 1.05, p.y); scene.add(gh); extra.push(gh); }
    var cam = new THREE.PerspectiveCamera(34, 16 / 9, Math.max(0.5, p.r * 0.1), p.r * 40);
    S.inset = { key: key, uid: uid, scene: scene, cam: cam, sph: sph, own: !mesh ? mat : null, extra: extra, p: p, yaw: 0.6 };
    return S.inset;
  }
  function insetDispose() {
    var I = S && S.inset; if (!I) return;
    I.sph.geometry.dispose(); if (I.own) I.own.dispose();
    I.extra.forEach(function (o) { if (o.geometry) o.geometry.dispose(); if (o.material) (Array.isArray(o.material) ? o.material : [o.material]).forEach(function (m) { m.dispose(); }); });
    S.inset = null;
  }
  function insetDraw(ctx, I, rect, dt, spin) {
    var p = I.p, d = p.r * 3.4;
    I.yaw += dt * (spin || 0.35);
    I.cam.aspect = rect.w / rect.h; I.cam.updateProjectionMatrix();
    I.cam.position.set(p.x + Math.cos(I.yaw) * d, p.r * 0.9, p.y + Math.sin(I.yaw) * d); I.cam.lookAt(p.x, 0, p.y);
    ctx.renderInset(I.cam, rect, { scene: I.scene, clear: 0x05070d });
    var n = 0; I.scene.traverse(function (o) { if ((o.isMesh || o.isLine || o.isLineSegments || o.isPoints) && o.visible) n++; });
    return n;
  }
  function arcPath(cx, cy, R, span, rot) {
    var d = '';
    for (var i = 0; i < 4; i++) {
      var a0 = (rot + i * 90 - span / 2) * D2R, a1 = (rot + i * 90 + span / 2) * D2R;
      d += 'M' + r1(cx + Math.cos(a0) * R) + ' ' + r1(cy + Math.sin(a0) * R) + ' A' + r1(R) + ' ' + r1(R) + ' 0 0 1 ' + r1(cx + Math.cos(a1) * R) + ' ' + r1(cy + Math.sin(a1) * R) + ' ';
    }
    return d;
  }
  function cornerPath(x, y, w, h, c) {
    return ['M', x, y + c, 'L', x, y, 'L', x + c, y, 'M', x + w - c, y, 'L', x + w, y, 'L', x + w, y + c, 'M', x + w, y + h - c, 'L', x + w, y + h, 'L', x + w - c, y + h,
      'M', x + c, y + h, 'L', x, y + h, 'L', x, y + h - c].map(function (v) { return typeof v === 'number' ? r1(v) : v; }).join(' ');
  }

  // ---------------------------------------------------------------- arrow placement (edge arrows that never cover the HUD)
  function rectOf(e) {
    if (!e || e.style.display === 'none' || !e.getBoundingClientRect) return null;
    var r = e.getBoundingClientRect(), x0 = r.left, y0 = r.top, x1 = r.right, y1 = r.bottom;
    if (r.width <= 0 && r.height <= 0 && !e.childNodes.length) return null;
    // a slot's words hang outside its box (the gauge's caption, the radar's title): the union with its shown children
    Array.prototype.forEach.call(e.children || [], function (c) {
      if (!c.getBoundingClientRect || (c.style && c.style.display === 'none')) return;
      var q = c.getBoundingClientRect(); if (q.width <= 0 || q.height <= 0) return;
      x0 = Math.min(x0, q.left); y0 = Math.min(y0, q.top); x1 = Math.max(x1, q.right); y1 = Math.max(y1, q.bottom);
    });
    return x1 > x0 && y1 > y0 ? { x: x0, y: y0, w: x1 - x0, h: y1 - y0 } : null;
  }
  function hudRects(list) { return list.map(rectOf).filter(Boolean); }
  // the left edge of the view switch while it shows in 3D, else the screen's right edge: read only when the screen size or
  // the switch's showing changes (no per-frame layout read)
  function povLeft(sz) {
    if (!H.povEl || !H.povEl.isConnected) H.povEl = g.document.getElementById('pov-switch');
    var pov = H.povEl, vis = !!(pov && !pov.hidden && pov.classList.contains('in3d')), key = sz.w + 'x' + sz.h + (vis ? 'v' : 'h');
    if (S.povKey !== key) { var r = vis ? pov.getBoundingClientRect() : null; S.povKey = r && r.width > 0 || !vis ? key : ''; S.povL = r && r.width > 0 ? r.left : sz.w - 12; }
    return S.povL;
  }
  // the touch deck's own controls (the stick, its STEER word, SELECT PLANET, BRAKE, BOOST) and the view switch while they show:
  // an arrow never sits on them (the stick's wide touch zone is not drawn, so it stays free)
  function touchRects() {
    var d = g.document, out = [];
    if (!H.deckEl || !H.deckEl.isConnected) H.deckEl = d.getElementById('deck3d');
    if (!H.povEl || !H.povEl.isConnected) H.povEl = d.getElementById('pov-switch');
    var deck = H.deckEl, pov = H.povEl;
    if (deck && deck.style.display === 'block') Array.prototype.forEach.call(deck.children, function (c) {
      if (c.className.indexOf('deck3d-stick-zone') >= 0) return;
      var r = rectOf(c); if (r) out.push(r);
    });
    if (pov && !pov.hidden && pov.classList.contains('in3d')) { var q = rectOf(pov); if (q) out.push(q); }
    return out;
  }
  // the labels shown this frame: the markers' names and the target's panel
  function labelRects(n) {
    var out = [];
    for (var i = 0; i < n && i < H.markPool.length; i++) { var r = rectOf(H.markPool[i].root); if (r) out.push(r); }
    var t = rectOf(H.target); if (t) out.push(t);
    return out;
  }
  function hit(a, b, pad) { pad = pad || 0; return a.x < b.x + b.w + pad && a.x + a.w + pad > b.x && a.y < b.y + b.h + pad && a.y + a.h + pad > b.y; }
  // a slot's box in viewport px, grown by the words that hang outside it (left, up, right, down); S.avoid collects the ones that show
  function avoidAdd(F, b, l, t, r, d) { S.avoid.push({ x: F.x + b.x - (l || 0), y: F.y + b.y - (t || 0), w: b.w + (l || 0) + (r || 0), h: b.h + (t || 0) + (d || 0) }); }
  // the nearest place for a box that clears every shown HUD slot (and the extra rect), below a slot or beside it; the box itself when it is clear
  function clearSpot(b, sz, extra, centre) {
    var list = S.avoid, i, j, c, best = null, bd = 1e9, pad = 6;
    function ok(q) {
      if (q.x < 8 || q.y < 8 || q.x + q.w > sz.w - 8 || q.y + q.h > sz.h - 8) return false;
      if (extra && hit(q, extra, pad)) return false;
      if (centre && boxInCentre(q, sz)) return false;
      return !hitAny(q, list, pad);
    }
    if (ok(b)) return b;
    for (i = 0; i < list.length; i++) {
      var r = list[i], cs = [[b.x, r.y + r.h + 8], [r.x + r.w - b.w, r.y + r.h + 8], [r.x - b.w - 8, b.y], [r.x + r.w + 8, b.y], [r.x - b.w - 8, r.y + r.h + 8]];
      for (j = 0; j < cs.length; j++) {
        c = { x: cs[j][0], y: cs[j][1], w: b.w, h: b.h };
        var dd = Math.abs(c.x - b.x) + Math.abs(c.y - b.y);
        if (dd < bd && ok(c)) { best = c; bd = dd; }
      }
    }
    return best || b;
  }
  function hitAny(a, list, pad) { for (var i = 0; i < list.length; i++) if (hit(a, list[i], pad)) return true; return false; }
  // the keys list camera.js opens on a newcomer's first entry (until the first key or tap): the target's label and the arrows
  // keep out of it; read only when it opens or the screen size changes
  function keysRect(sz) {
    if (!H.keysDet || !H.keysDet.isConnected) { var gi = g.document.getElementById('flight3d-instruments'); H.keysDet = gi ? gi.querySelector('details') : null; H.keysEl = gi ? gi.querySelector('.flight3d-controls') : null; }
    var open = !!(H.keysDet && H.keysDet.open && H.keysEl), key = open ? sz.w + 'x' + sz.h : '';
    if (S.keysKey !== key) {
      var r = open ? H.keysEl.getBoundingClientRect() : null;
      S.keysR = r && r.width > 0 ? { x: r.left, y: r.top, w: r.width, h: r.height } : null; S.keysKey = open && !S.keysR ? '' : key;
    }
    return open ? S.keysR : null;
  }
  // an arrow at angle th (0 up, clockwise) on the ellipse scaled k: its anchor, its word box on the inner side, and both
  function arrowAt(th, k, bw, bh, sz, rx, ry) {
    var ax = clamp(sz.w / 2 + Math.sin(th) * rx * k, 22, sz.w - 22), ay = clamp(sz.h / 2 - Math.cos(th) * ry * k, 22, sz.h - 22);
    var ix = -Math.sin(th), iy = Math.cos(th);
    var bx = ax + ix * 30 - bw / 2 + (ix > 0.3 ? bw / 2 : ix < -0.3 ? -bw / 2 : 0), by = ay + iy * 26 - bh / 2;
    bx = clamp(bx, 4, sz.w - bw - 4); by = clamp(by, 4, sz.h - bh - 4);
    // the chevrons' 40 px box turns with the bearing: its bounding box is up to 40 x sqrt(2) wide
    var x0 = Math.min(ax - 29, bx), y0 = Math.min(ay - 29, by), x1 = Math.max(ax + 29, bx + bw), y1 = Math.max(ay + 29, by + bh);
    return { th: th, k: k, ax: ax, ay: ay, box: { x: bx, y: by, w: bw, h: bh }, rect: { x: x0, y: y0, w: x1 - x0, h: y1 - y0 } };
  }
  // the nearest free slot: along the ellipse in 4 degree steps either way, the whole way round; at each angle clear of the
  // HUD's boxes (the slots, the callout, the band, the pill), the labels (the markers' names and the target's panel) and
  // the arrows already placed, out by 8 then 16% first when only a label is in the way (labels win). Nothing free anywhere:
  // null, and the arrow is not drawn (it folds into the audit's grouped count) rather than drawn over something
  function placeArrow(ang, bw, bh, sz, rx, ry, avoid, labels, placed) {
    var ks = [1, 1.08, 1.16];
    for (var d = 0; d <= 180; d += 4) {
      for (var sg = 1; sg >= -1; sg -= 2) {
        if ((!d || d === 180) && sg < 0) continue;
        var th = ang + sg * d * D2R;
        for (var i = 0; i < ks.length; i++) {
          var a = arrowAt(th, ks[i], bw, bh, sz, rx, ry);
          if (hitAny(a.rect, avoid, 4) || hitAny(a.rect, placed, 2)) { if (!i) break; continue; }   // the HUD or an arrow: along, not out
          if (!hitAny(a.rect, labels, 2)) { a.hits = false; return a; }
        }
      }
    }
    return null;
  }
  // the pill for the contacts behind (comps/3d-cruise.png: red chevrons, "2 problems behind you", the most urgent named):
  // the counts with their words, at the bottom middle (above the cockpit when it shows)
  function behindPill(list, sz, fu) {
    var P = 0, N = 0, NA = 0, NR = 0, A = 0, Hn = 0, other = 0;
    list.forEach(function (x) {
      var o = x.o, c = o.counts || {};
      if (o.kind === 'agent') { if (o.help) Hn++; else A++; return; }
      if (c.problems) P += c.problems;
      if (c.need_you) { N += c.need_you; NA += c.answer || 0; NR += c.review || 0; }
      if (!c.problems && !c.need_you) other++;
    });
    var parts = [];
    if (P) parts.push(plural(P, 'problem', 'problems'));
    if (N) parts.push(N + ' ' + needNoun({ answer: NA, review: NR }, N));
    if (Hn) parts.push(plural(Hn, 'stuck agent', 'stuck agents'));
    if (A) parts.push(plural(A, 'agent', 'agents'));
    if (other) parts.push(plural(other, 'target', 'targets'));
    var t1 = parts.length === 1 ? parts[0] + ' behind you' : 'Behind you: ' + parts.join(' · ');
    // the most urgent named: a stuck agent, then the most problems, then the most need-you, then the rest
    var urgency = function (o) { var c = o.counts || {}; return o.help ? 1e6 : c.problems ? 1e4 + c.problems : c.need_you ? 1e2 + c.need_you : o.kind === 'agent' ? 1 : 0; };
    var top = list.slice().sort(function (a, b) { return urgency(b.o) - urgency(a.o); })[0].o, t2 = top.kind === 'agent' ? top.name + ' · ' + (top.sub || '') : top.name + ' folder · ' + (top.sub || '');
    var col = P || Hn ? COL.red : N ? COL.amber : COL.cyan;
    show(H.behind, true);
    text(H.behindT1, t1.toUpperCase()); text(H.behindT2, t2);
    H.behindT1.style.fontSize = font(fu, 4.5); H.behindT2.style.fontSize = font(fu, 4);
    H.behindBox.style.borderLeftColor = col;
    Array.prototype.forEach.call(H.behindChev.querySelectorAll('path'), function (pth) { pth.setAttribute('fill', col); });
    var w = H.behind.offsetWidth || 260, h = H.behind.offsetHeight || 44, cock = num(FH.cockShare, 0);
    var bottom = cock > 0.01 ? sz.h * (1 - cock) - 10 : sz.h * 0.99;
    var x = clamp(sz.w * 0.5 - 13, 8, sz.w - w - 8), y = clamp(bottom - h, sz.h * 2 / 3 + 4, sz.h - h - 4);
    H.behind.style.left = px(x); H.behind.style.top = px(y);
    return { rect: { x: x, y: y, w: w, h: h }, audit: { count: list.length, uids: list.map(function (x) { return x.o.uid; }), text: [t1, t2], colour: col,
      rect: [Math.round(x), Math.round(y), Math.round(w), Math.round(h)] } };
  }

  C.module('hud', {
    build: function (scene, world, ctx) {
      build();
      S = {
        ctx: ctx, world: world, t: 0, feel: FH.feel || { radio: { hold: 2, perChar: 0.04, cap: 8 }, lockS: { dwell: 0.8, hold: 0, cone: 10 }, helpS: { period: 0.5, on: 0.25, total: 10 }, reticle: [600, 1200], noseLead: 25 },
        layout: LAYOUT, lock: { state: 'none', uid: null, ms: 0, prevLock: null }, cand: null, eHeld: false,
        comms: { current: null, dropped: 0, said: 0 }, seen: 0, where: {}, ret: { farYaw: null, farPitch: 0 },
        boost: { state: 'hidden', since: 0, fullAt: null, lastMeter: 1, drew: false, locked: false },
        mission: { cardUntil: 0, title: '', objective: '', stages: 0, shown: 0 }, plate: { text: '', at: -10 }, caption: { ms: 0 },
        band: { until: 0, text: '', sub: '' }, offFor: 0, pip: { visible: false, target: null, calls: 0 }, help: {}, last5: -1, inset: null,
        targetCardText: '', badgeN: -1, ringsKey: '', iconKey: '', gcapKey: '', gcapFs: '', povKey: '', povL: 0, keysKey: '', keysR: null, agentsKey: '', agentsW: 0, agentsH: 0
      };
      FH.hudKey = hudKey; FH.hudKeyUp = hudKeyUp; FH.tapLock = tapLock;
      H.root.style.display = 'none';
    },
    rebuild: function (scene, world, ctx) { if (S) S.world = world; },
    update: function (world, sdt, ctx) {
      if (!S) return;
      var dt = ctx.dt || 0, a = ctx.audit, sz = sizeNow(), phone = !!ctx.quality.phone, portrait = phone && sz.h > sz.w;
      var tablet = !phone && doc().documentElement.classList.contains('t');
      S.t = ctx.t; S.world = world; S.ctx = ctx; S.feel = FH.feel || S.feel;
      S.layout = layoutOf(ctx.skin3d, portrait);
      var look3d = (ctx.skin3d && ctx.skin3d.look3d) || {}, hl = look3d.hud || {};
      var look = { ship: hl.ship || COL.teal, gauge: hl.gauge || null, label: hl.label || null };
      var set = FH.set || { aspect: 'expand' }, alpha = clamp(num(FH.hudAlpha, 0), 0, 1);
      var tp = ctx.transition.phase(), flying = ctx.mode === 'fp' || ctx.mode === 'entering' || ctx.mode === 'leaving';
      var on = flying && ctx.mode !== 'paused' && !ctx.hudHidden && tp !== 'demo';
      show(H.root, on); if (on) H.root.style.display = 'block';
      H.root.style.opacity = String(alpha);
      var F = frameRect(sz, set.aspect, phone), u = portrait ? Math.min(sz.w / 320, sz.h / 240) : F.h / 240;
      var fu = portrait ? 3.4 : u;                       // the type unit: a phone's words stay readable
      H.frame.style.left = px(F.x); H.frame.style.top = px(F.y); H.frame.style.width = px(F.w); H.frame.style.height = px(F.h);
      var FR = { x: 0, y: 0, w: F.w, h: F.h };          // the frame's children are placed inside it
      S.where = indexScene(ctx); S.avoid = [];
      var w = world, planets = w.planets || [], hudOn = on && alpha > 0.02 && !FH.caption, fpOn = hudOn && ctx.mode === 'fp' && !FH.transPhase;
      var f = FH.follow;
      // ---- the gauge (o5 rainbow drain gauge: the share of folders with no problems; neutral red -> amber -> green)
      var gb = slot('gauge', FR, u);
      S.gwNamed = 0;
      var clear = planets.filter(function (p) { return p.health !== 'broken' && p.health !== 'waiting'; }).length, total = planets.length, share = total ? clear / total : 1;
      put(H.gauge, gb); show(H.gauge, !!(gb && hudOn));
      if (gb) {
        var bh = Math.max(12, Math.min(gb.h, 7.2 * u));
        H.gbar.style.width = px(gb.w); H.gbar.style.height = px(bh);
        H.gfill.style.clipPath = 'inset(0 ' + r1((1 - share) * 100) + '% 0 0)';
        if (look.gauge) H.gfill.style.background = look.gauge;
        H.gcap.style.left = px(gb.w + 2.6 * u); H.gcap.style.top = px(-0.6 * u);
        var attn = total - clear;
        var gw = gaugeWords(planets, clear, total);
        S.gwNamed = gw.named || 0;
        text(H.gnum, gw.num);
        text(H.gword, gw.word);
        show(H.gsub, false);                               // "1 folder needs you" / "All 7 folders: nothing to do" says it in one line
        if (portrait) { H.gcap.style.left = '0px'; H.gcap.style.top = px(bh + 13); }   // a clear line apart from the agents row
        // a long sentence ("customer-documentation needs you: 12 finished tasks to review") stays clear of the view switch on a
        // desk (a second line) and inside a phone's screen (one line, its type a little smaller); measured only when the words,
        // the room or the type unit change
        var capX = F.x + gb.x + (portrait ? 0 : gb.w + 2.6 * u), capMax = Math.max(120, portrait ? sz.w - capX - 12 : povLeft(sz) - 1.5 * u - capX);
        var capFs = font(fu, 4.8), capKey = gw.num + gw.word + '|' + Math.round(capMax) + '|' + capFs + (portrait ? 'p' : 'd');
        if (S.gcapKey !== capKey) {
          S.gcapKey = capKey; S.gcapFs = capFs;
          H.gcap.style.maxWidth = px(capMax); H.gcap.style.whiteSpace = portrait ? 'nowrap' : 'normal'; H.gcap.style.fontSize = capFs;
          if (portrait && H.gcap.scrollWidth > capMax) S.gcapFs = px(Math.max(12, parseFloat(capFs) * capMax / H.gcap.scrollWidth - 0.5));
        }
        H.gcap.style.fontSize = S.gcapFs;
        if (hudOn) {                                       // the bar and its caption (estimated from the characters: no layout read)
          var cfs = parseFloat(S.gcapFs) || 12, cw = (gw.num + ' ' + gw.word).length * cfs * 0.56, cl = portrait ? 1 : Math.max(1, Math.ceil(cw / capMax));
          if (portrait) avoidAdd(F, { x: gb.x, y: gb.y, w: Math.max(gb.w, Math.min(cw, capMax)), h: bh + 13 + cfs * 1.3 });
          else avoidAdd(F, { x: gb.x, y: gb.y - 0.6 * u, w: gb.w + 2.6 * u + Math.min(cw, capMax), h: Math.max(bh + 0.6 * u, cl * cfs * 1.25) });
        }
      }
      // ---- the steps counter with big digits and the three milestone rings of the followed workflow
      var cb = slot('counter', FR, u);
      put(H.counter, cb); show(H.counter, !!(cb && hudOn && f));
      if (cb) {
        var done = f ? f.done : 0, tot = f ? f.total : 0, ms = tot ? Math.floor(done / tot * 3 + 1e-9) : 0;
        var dpx = portrait ? 30 : Math.max(24, Math.round(15.7 * u));
        var ringStyle = !!(((ctx.skin3d && ctx.skin3d.feel3d) || {}).milestoneRings);
        H.cnum.style.fontSize = px(dpx); text(H.cnum, ringStyle ? ('00' + done).slice(-3) : String(done));
        var rk = (ringStyle ? 'r' : 's') + '3:' + ms; if (S.ringsKey !== rk) { S.ringsKey = rk; H.rings.innerHTML = ringStyle ? ringsSvg(3, ms) : milestonesSvg(3, ms); }
        var rh = portrait ? 22 : Math.round(10.2 * u); H.rings.style.height = px(rh); H.rings.style.width = px(rh * (ringStyle ? 3.4 : 4.2)); H.rings.style.left = px(dpx * (ringStyle ? 1.75 : 0.55 * String(done).length + 0.2) + 1.5 * u); H.rings.style.top = px(dpx * 0.12);
        H.ccap.style.top = px(dpx + 1.2 * u); H.ccap.style.fontSize = font(fu, 3.7);
        text(H.ccap, f ? 'steps done · milestones ' + ms + ' of 3' : 'steps done · no workflow followed');
        if (portrait) text(H.ccap, 'steps done');
        if (hudOn && f) avoidAdd(F, { x: cb.x, y: cb.y, w: cb.w, h: dpx + 1.2 * u + 5 * u });
      }
      // ---- agents working (an icon and the count), the boost meter, need you (badges and the count)
      var working = (w.craft || []).filter(function (c) { return c.role !== 'carrier' && String(c.state).indexOf('work') >= 0; }).length, ab = slot('agents', FR, u);
      put(H.agents, ab); show(H.agents, !!(ab && hudOn));
      if (ab) {
        if (S.iconKey !== look.ship) { S.iconKey = look.ship; H.aicon.innerHTML = shipIconSvg('#8FB0FF'); }
        var ih = portrait ? 18 : Math.round(6.4 * u); H.aicon.style.width = px(ih * 1.6); H.aicon.style.height = px(ih);
        H.anum.style.fontSize = font(fu, portrait ? 7 : 8); text(H.anum, String(working));
        H.aword.style.fontSize = font(fu, portrait ? 4.8 : 4.2); text(H.aword, portrait ? (working === 1 ? 'AI agent working now' : 'AI agents working now') : (working === 1 ? 'AI agent working right now' : 'AI agents working right now') + '\n(programs that do your tasks)');
        if (hudOn) {
          // its words never shrink under 12 px, so the box kept clear is the line's real size: read once per text and size
          var aKey = working + '|' + H.aword.style.fontSize + '|' + H.anum.style.fontSize + '|' + Math.round(ab.w) + '|' + sz.w + 'x' + sz.h + (portrait ? 'p' : 'd');
          if (S.agentsKey !== aKey) {
            var ar = H.agents.getBoundingClientRect(), ax1 = ar.right, ay1 = ar.bottom;
            Array.prototype.forEach.call(H.agents.children, function (c) { var r = c.getBoundingClientRect(); if (r.width > 0 && r.height > 0) { ax1 = Math.max(ax1, r.right); ay1 = Math.max(ay1, r.bottom); } });
            S.agentsKey = ar.width > 0 ? aKey : ''; S.agentsW = ax1 - ar.left; S.agentsH = ay1 - ar.top;
          }
          avoidAdd(F, { x: ab.x, y: ab.y, w: Math.max(ab.w, S.agentsW || 0), h: Math.max(ab.h, portrait ? 30 : 11 * u, S.agentsH || 0) });
        }
      }
      var sp = w.ship || {}, B = S.boost, meter = clamp(num(sp.meter, 1), 0, 1), using = !!(sp.boost || sp.brake);
      // the boost meter (sm64js Hud.js:130-232): hidden -> emphasized (1.5x, 1.5 s) -> docking (0.4 s) -> visible -> hidden
      // 3 s after it is full again; dimmed to half while the meter is locked (the heat must cool) or spent
      var lockout = !!((S.feel.meter || {}).lockout);
      if (using && meter < B.lastMeter - 1e-6) B.drew = true;
      if (!using && B.drew) { B.drew = false; if (lockout && meter < 1) B.locked = true; }
      if (meter >= 0.999) B.locked = false;
      if (typeof sp.locked === 'boolean' && sp.flight3d) B.locked = sp.locked;   // the 3D flight says when it is locked
      var spent = meter <= 0.001 && !sp.boost;
      B.lastMeter = meter;
      if (B.state === 'hidden' && (using || meter < 0.999)) { B.state = 'emphasized'; B.since = S.t; B.fullAt = null; }
      else if (B.state === 'emphasized' && S.t - B.since >= 1.5) { B.state = 'docking'; B.since = S.t; }
      else if (B.state === 'docking' && S.t - B.since >= 0.4) { B.state = 'visible'; B.since = S.t; }
      if (B.state !== 'hidden') {
        if (meter >= 0.999 && !using) { if (B.fullAt == null) B.fullAt = S.t; if (S.t - B.fullAt >= 3) { B.state = 'hidden'; B.fullAt = null; } }
        else B.fullAt = null;
      }
      var bb = slot('boost', FR, u);
      put(H.boost, bb);
      if (bb) {
        H.boost.style.height = px(Math.max(10, Math.min(bb.h, 5.4 * u)));
        H.bfill.style.width = r1(meter * 100) + '%';
        H.blabel.style.fontSize = font(fu, 3.4);
        var sc = B.state === 'emphasized' ? 1.5 : B.state === 'docking' ? 1.5 - 0.5 * clamp((S.t - B.since) / 0.4, 0, 1) : 1;
        H.boost.style.transform = sc !== 1 ? 'scale(' + sc + ')' : '';
        H.boost.style.opacity = B.locked || spent ? '0.5' : '1';
        H.boost.className = 'boost3d' + (B.locked ? ' locked' : '') + (meter < 0.25 ? ' low' : '');
        show(H.boost, B.state !== 'hidden' && hudOn);
        if (B.state !== 'hidden' && hudOn) { var bh2 = Math.max(10, Math.min(bb.h, 5.4 * u)) * 1.5, blw = 6 * Math.max(12, 3.4 * fu) * 0.9 + 10; avoidAdd(F, { x: bb.x, y: bb.y, w: bb.w, h: bh2 }, blw, 0, 0, 0); }
      }
      var ny = needYouOf(w), nb = slot('need', FR, u);
      put(H.need, nb); show(H.need, !!(nb && hudOn && !(S.gwNamed && S.gwNamed === ny)));   // the gauge line already names the one folder and its one kind of wait
      if (nb) {
        var nbad = Math.min(3, ny);
        if (S.badgeN !== nbad) { S.badgeN = nbad; H.badges.innerHTML = ''; for (var bi = 0; bi < nbad; bi++) { var hx = el('i', 'hex', H.badges); hx.innerHTML = hexSvg(hl.needBadge || '#E0901A', '!'); } }
        var hs = portrait ? 24 : Math.round(7.4 * u); Array.prototype.forEach.call(H.badges.childNodes, function (e2) { e2.style.width = e2.style.height = px(hs); });
        H.nnum.style.fontSize = font(fu, 5.2); H.nword.style.fontSize = font(fu, 5.2);
        text(H.nnum, String(ny)); text(H.nword, ' ' + needNoun(w.counts, ny));
        if (hudOn && !(S.gwNamed && S.gwNamed === ny)) avoidAdd(F, { x: nb.x, y: nb.y, w: nb.w, h: Math.max(nb.h, hs + 4) });
      }
      // ---- the FLOW block (deathstar HUD.ts:98-269: the one-line mission block while following) and the tube (o9's boss
      // tube): the followed workflow's title and step, the tube drains from the bottom as its steps are done
      var M = S.mission, tb = slot('tube', FR, u);
      if (f) { M.title = f.title; M.objective = f.sentence; M.stages = f.steps.length; }
      put(H.block, tb); show(H.block, !!(f && tb && fpOn));
      if (f && tb) {
        var pillH = Math.round(7 * u), tubeTop = pillH + 2 * u, tubeH = Math.max(40, tb.h - tubeTop);
        H.pill.style.height = px(pillH); H.pill.style.width = px(pillH * 3.1); H.pill.style.fontSize = font(fu, 4.4);
        H.bt1.style.left = px(pillH * 3.1 + 2.6 * u); H.bt1.style.fontSize = font(fu, 3.9); text(H.bt1, f.title);
        H.bt2.style.left = H.bt1.style.left; H.bt2.style.top = px(4.6 * u); H.bt2.style.fontSize = font(fu, 3.9);
        text(H.bt2, 'step ' + Math.max(1, f.index + 1) + ' of ' + f.total + (f.place ? ' · ' + f.place : ''));
        H.tube.style.top = px(tubeTop); H.tube.style.left = px(pillH * 1.55 - 2.4 * u / 2 - 0.75 * u); H.tube.style.width = px(Math.max(12, 5.6 * u)); H.tube.style.height = px(tubeH);
        H.tfill.style.clipPath = 'inset(' + r1(f.done / Math.max(1, f.total) * 100) + '% 0 0 0)';
      }
      // ---- events from the camera module's scan (rev diffs only): comms, plate, card, band
      (FH.events || []).forEach(function (ev) {
        if (ev.seq <= S.seen) return;
        S.seen = ev.seq;
        if (ev.priority != null && ev.text) sayLine({ priority: ev.priority, who: ev.who, text: ev.text, colour: ev.colour, sub: ev.sub || (f && ev.wf === f.id ? f.title + ' · step ' + Math.max(1, f.index + 1) + ' of ' + f.total : '') });
        if (!f || ev.wf !== f.id) return;
        if (ev.kind === 'step') { S.plate = { text: 'Step ' + ev.stepIndex + ' of ' + ev.total + ' · ' + ev.step + (ev.place ? ' in ' + ev.place : ''), at: S.t }; say(S.plate.text); }
        if (ev.kind === 'start') { M.cardUntil = S.t + 1.3 + (ev.entry ? 0.2 : 0); M.shown++; }
        if (ev.kind === 'complete') { S.band = { until: S.t + 3, text: 'WORKFLOW COMPLETE', sub: ev.title + ' · ' + ev.total + ' of ' + ev.total + ' steps done', at: S.t }; say(S.band.sub); }
      });
      // the mission card (the comp's workflow card): its title, a step card per step joined by state-coloured links
      var cardOn = hudOn && f && S.t < M.cardUntil;
      show(H.mcard, !!cardOn);
      if (cardOn) {
        var mbox = slot('mission', F, u) || { x: F.x + F.w * 0.305, y: F.y + F.h * 0.013, w: F.w * 0.39 };
        put(H.mcard, mbox);
        H.mhead.style.fontSize = font(fu, 4.6); H.mkick.style.fontSize = font(fu, 3.4); H.mright.style.fontSize = font(fu, 3.4);
        text(H.mtitle, f.title); text(H.mright, 'step ' + Math.max(1, f.index + 1) + ' of ' + f.total);
        H.mobj.style.fontSize = font(fu, 3.8); text(H.mobj, f.sentence || f.goal || ''); show(H.mobj, !!(f.sentence || f.goal));
        if (H.mtrack.childNodes.length !== f.steps.length || H.mtrack.__id !== f.id) {
          H.mtrack.innerHTML = ''; H.mtrack.__id = f.id;
          f.steps.forEach(function () { var sc2 = el('div', 'seg', H.mtrack); el('b', 'n', sc2); el('i', 'g', sc2); el('span', 'w', sc2); });
        }
        f.steps.forEach(function (st, i) {
          var s2 = H.mtrack.childNodes[i]; if (!s2) return;
          var state = st.state || 'waiting', col = STATE_COL[state] || COL.grey;
          s2.className = 'seg s-' + state + (i === f.index ? ' cur' : ''); s2.style.borderColor = col; s2.style.fontSize = font(fu, 3.4);
          text(s2.childNodes[0], (i + 1) + ' ' + String(st.label).split(' ')[0].toUpperCase());
          text(s2.childNodes[1], state === 'done' ? '✓' : state === 'needs_you' ? '!' : state === 'failed' || state === 'blocked' ? '×' : state === 'working' ? '' : '—');
          s2.childNodes[1].style.color = col;
          text(s2.childNodes[2], STATE_WORD[state] || state);
        });
        H.magents.style.fontSize = font(fu, 3.4);
        text(H.magents, f.agents.length ? 'With ' + f.agents.map(function (uid) { var c = (w.craft || []).filter(function (x) { return x.uid === uid; })[0]; return c ? c.label : uid; }).join(', ') : 'No agent docked at its folders yet');
      }
      // ---- the plate (video-x2 #20): 0.2 s in, 2.5 s hold, 0.3 s out
      var pa = S.t - S.plate.at, plateOn = hudOn && S.plate.text && pa < 3.0;
      show(H.plate, !!plateOn);
      if (plateOn) {
        var po = pa < 0.2 ? pa / 0.2 : pa > 2.7 ? Math.max(0, 1 - (pa - 2.7) / 0.3) : 1;
        H.plate.style.opacity = String(po);
        H.plate.style.left = px(F.x + F.w * 0.5); H.plate.style.top = px(F.y + F.h * (cardOn ? 0.16 : 0.12));
        var parts = S.plate.text.split(' · ');
        text(H.plateK, parts[0].toUpperCase()); text(H.plateT, parts.slice(1).join(' · '));
        H.plateK.style.fontSize = font(fu, 3.6); H.plateT.style.fontSize = font(fu, 6);
      }
      // ---- the band and the tally (o8 MISSION ACCOMPLISHED grammar: the band reveals from the middle out)
      var tally = FH.tally, bandOn = (hudOn && S.t < S.band.until) || (on && tally);
      show(H.band, !!bandOn);
      if (bandOn) {
        H.bandT.style.fontSize = font(fu, 9); H.bandS.style.fontSize = font(fu, 4.4);
        if (tally) { text(H.bandT, 'WHILE YOU FLEW'); text(H.bandS, plural(tally.finished, 'workflow', 'workflows') + ' done · ' + plural(tally.steps, 'step', 'steps') + ' finished'); }
        else { text(H.bandT, S.band.text); text(H.bandS, S.band.sub); }
        var ba = tally ? clamp((S.t - tally.at) / 0.5, 0, 1) : clamp((S.t - (S.band.at || 0)) / 0.5, 0, 1);
        H.band.style.clipPath = 'inset(0 ' + r1((1 - ba) * 50) + '% 0 ' + r1((1 - ba) * 50) + '%)';
        H.band.style.top = px(F.y + F.h * 0.41); H.band.style.minHeight = px(F.h * 0.11);
      }
      // ---- comms (priority queue; red for problem senders)
      var cur = S.comms.current;
      if (cur && S.t >= cur.until) { cur = S.comms.current = null; }
      var cmb = slot('comms', FR, u), pb = slot('portrait', FR, u);
      show(H.comms, !!(cmb && cur && hudOn));
      if (cmb && cur) {
        var red = cur.priority >= 3, scol = red ? COL.red : (cur.colour || (cur.priority === 2 ? COL.amber : cur.priority === 1 ? COL.cyan : '#7FA197'));
        put(H.comms, cmb);
        var boxH = Math.max(56, cmb.h), ps = pb ? Math.max(40, Math.min(pb.w, pb.h)) : Math.max(40, boxH - 16);
        if (portrait) { boxH = Math.max(72, cmb.h); ps = boxH - 16; }
        H.comms.style.height = px(boxH);
        H.comms.className = 'comms3d' + (red ? ' red' : ' p' + cur.priority);
        H.comms.style.borderColor = red ? '' : scol;
        H.portrait.style.width = H.portrait.style.height = px(ps); H.portrait.style.borderColor = red ? '' : scol;
        var src = portraitURL(scol, red); if (H.pimg.getAttribute('src') !== src) H.pimg.setAttribute('src', src);
        H.cgauge.style.width = px(ps); H.cgauge.style.top = px(-Math.max(12, 3.6 * u) - 4); H.cgauge.style.height = px(Math.max(8, 2.6 * u));
        H.cgfill.style.width = Math.round(clamp(1 - (S.t - cur.at) / Math.max(0.1, cur.hold), 0, 1) * 100) + '%';
        H.cwho.style.left = px(ps + 22); H.cwho.style.fontSize = font(fu, 4.6); H.cwho.style.top = px(-Math.max(16, 4.6 * u) - 6); text(H.cwho, (cur.who || '').toUpperCase());
        H.ctext.style.left = px(ps + 22); H.ctext.style.fontSize = font(fu, portrait ? 4.1 : 4.5); text(H.ctext, cur.text);
        if (hudOn) avoidAdd(F, cmb, 0, Math.max(16, 4.6 * u) + Math.max(12, 3.6 * u) + 14, 0, 0);
        H.csub.style.left = px(ps + 22); H.csub.style.fontSize = font(fu, 3.4); text(H.csub, (cur.sub || '').toUpperCase()); show(H.csub, !!cur.sub && !portrait);
      }
      // ---- the radar
      var rdb = slot('radar', FR, u), radarOn = !!(rdb && hudOn && !portrait);
      put(H.radar, rdb, true); show(H.radar, radarOn);
      var radar = { visible: radarOn, own: { stalk: 0 } };
      if (radarOn) { H.rlabel.style.fontSize = font(fu, 4.4); H.rlabel.style.top = px(-Math.max(16, 4.6 * u) - 2); var rr = radarDraw(ctx, { w: rdb.w - 4, h: rdb.h - 4 }, look); radar.own = rr.own; radar.contacts = rr.contacts; radar.range = rr.range; avoidAdd(F, rdb, 0, Math.max(16, 4.6 * u) + 4, 0, 0); }
      // ---- the reticle (vdinovi, the player ship's source, lines 178-203: two boxes 600 and 1200 u ahead, here a near box on the nose and a far
      // ring that lags it (video-o2 #9: the reticle lags the pilot), so in a turn the near one leads)
      var A = FH.att || {}, ret = S.ret, rd = S.feel.reticle || [600, 1200];
      var hdgN = (Number(sp.hdg) || 0) + (A.lead || 0) * D2R, pitN = (A.pitch || 0) * D2R;
      if (ret.farYaw == null || FH.transPhase) { ret.farYaw = hdgN; ret.farPitch = pitN; }
      ret.farYaw += Math.atan2(Math.sin(hdgN - ret.farYaw), Math.cos(hdgN - ret.farYaw)) * ease(8, dt); ret.farPitch += (pitN - ret.farPitch) * ease(8, dt);
      var ox = Number(sp.x) || 0, oz = Number(sp.y) || 0, oy = A.alt || 0;
      var nearP = ctx.project(ox + Math.cos(hdgN) * Math.cos(pitN) * rd[0], oy + Math.sin(pitN) * rd[0], oz + Math.sin(hdgN) * Math.cos(pitN) * rd[0]);
      var farP = ctx.project(ox + Math.cos(ret.farYaw) * Math.cos(ret.farPitch) * rd[1], oy + Math.sin(ret.farPitch) * rd[1], oz + Math.sin(ret.farYaw) * Math.cos(ret.farPitch) * rd[1]);
      var L = S.lock;
      lockUpdate(ctx, ctx.mode === 'fp' && !FH.transPhase ? dt : 0);
      var rc = look3d.reticle || {};
      var colIdle = rc.idle || COL.teal, colLocking = rc.locking || COL.amber, colLocked = rc.locked || COL.amber;
      var blink = L.state === 'locking' && Math.floor(L.ms / 125) % 2 === 1;   // 4 Hz
      var rcol = L.state === 'locked' ? colLocked : L.state === 'locking' ? colLocking : colIdle;
      var rw = Math.round(sz.w * (portrait ? 0.16 : 0.11));
      show(H.ret, fpOn);
      H.ret.style.left = '0px'; H.ret.style.top = '0px'; H.ret.style.width = px(sz.w); H.ret.style.height = px(sz.h);
      var cen = { x: sz.w / 2, y: sz.h / 2 }, fpx = farP.behind ? cen : farP, npx = nearP.behind ? cen : nearP;
      // both boxes stay in the centre third (the rule: the middle of the screen is the reticle's alone)
      var keep = function (p) { return { x: clamp(p.x, sz.w / 3 + rw * 0.3, sz.w * 2 / 3 - rw * 0.3), y: clamp(p.y, sz.h / 3 + rw * 0.3, sz.h * 2 / 3 - rw * 0.3) }; };
      fpx = keep(fpx); npx = keep(npx);
      var fk = rw / 92, nk = rw * 0.66 / 28;
      H.far.setAttribute('transform', 'translate(' + r1(fpx.x) + ',' + r1(fpx.y) + ') scale(' + r3(fk) + ')');
      H.near.setAttribute('transform', 'translate(' + r1(npx.x) + ',' + r1(npx.y) + ') scale(' + r3(nk) + ')');
      [H.farRing].concat(H.farTicks).forEach(function (e2) { e2.setAttribute('stroke', blink ? 'none' : rcol); });
      H.farDot.setAttribute('fill', rcol); H.nearBox.setAttribute('stroke', rcol);
      // ---- the target: the locked planet, else the one under the nose, else what the space lane says is hovered
      var tgtUid = L.state !== 'none' && L.uid ? L.uid : S.cand || (ctx.hover && ctx.hover.uid) || null;
      var tp2 = tgtUid ? (planets.filter(function (p) { return p.uid === tgtUid; })[0] || null) : null;
      var tgtText = '', tgt = null;
      if (tp2) {
        var s2 = ctx.project(tp2.x, 0, tp2.y);
        if (!s2.behind) tgt = { x: s2.x, y: s2.y, r: Math.max(14, radiusPx(ctx, tp2, sz)), on: s2.onScreen };
        // 5 Hz: name · purpose · state · N s away (x4 #16; cosmosjourneyer objectTargetCursor.ts: name and distance on aim)
        if (S.t - S.last5 >= 0.2 || S.lastTgt !== tgtUid) {
          S.last5 = S.t; S.lastTgt = tgtUid;
          var spd = Math.max(Number(sp.spd) || 0, (S.feel.speed || {}).cruise || 200), dist = Math.max(0, Math.hypot(tp2.x - ox, tp2.y - oz) - tp2.r);
          var words = [String(tp2.label)];
          if (portrait) words.push('folder');
          else if (tp2.purpose && String(tp2.purpose).toLowerCase() !== String(tp2.label).toLowerCase()) words.push(String(tp2.purpose) + ' folder');
          var eta = Math.round(dist / spd);
          S.tgtWords = words.concat([stateWords(tp2), distanceWords(eta)]);
          if (L.state === 'locked' && !phone) S.tgtWords.push('Enter opens it');
          S.tgtText = S.tgtWords.join(' · ');
        }
        tgtText = S.tgtText;
      }
      // lock brackets (L corners, the reticle's colour) while locking or locked; arc brackets on the target planet (comp:
      // four 28 degree arcs just outside the limb) and the label on a leader from the limb
      var acol = tp2 ? kindColour(tp2) : COL.teal, tOn = !!(tgtText && tgt && tgt.on && fpOn);
      if (tgt && tgt.on && L.state !== 'none' && fpOn) {
        var rr2 = tgt.r * 0.72, c = Math.max(8, rr2 * 0.3), bd = cornerPath(tgt.x - rr2, tgt.y - rr2, rr2 * 2, rr2 * 2, c);
        H.brkPath.setAttribute('d', bd); H.brkU.setAttribute('d', bd); H.brkPath.setAttribute('stroke', blink ? 'none' : rcol); H.brkU.style.display = blink ? 'none' : '';
        H.brk.style.display = '';
      } else H.brk.style.display = 'none';
      if (tOn) {
        var R = tgt.r + 10, ad = arcPath(tgt.x, tgt.y, R, 28, 45);
        H.arcL.setAttribute('d', ad); H.arcU.setAttribute('d', ad); H.arcL.setAttribute('stroke', COL.teal); H.arcs.style.display = '';
      } else H.arcs.style.display = 'none';
      show(H.target, tOn);
      var tRect = null;
      if (tOn) {
        var tw0 = S.tgtWords || [];
        text(H.tt1, String(tw0[0] || '').toUpperCase());
        H.tt2.innerHTML = '';
        tw0.slice(1).forEach(function (wd, i) { if (i) H.tt2.appendChild(doc().createTextNode(' · ')); var sp2 = doc().createElement(/problem|need|decision/.test(wd) ? 'b' : 'span'); sp2.textContent = wd; H.tt2.appendChild(sp2); });
        H.tt1.style.fontSize = font(fu, portrait ? 6.4 : 5.2); H.tt2.style.fontSize = font(fu, portrait ? 4.8 : 4.4);
        H.target.style.borderLeftColor = H.target.style.borderRightColor = acol;
        var tw = H.target.offsetWidth || 220, th = H.target.offsetHeight || 40;
        // from the limb on the side away from the middle: up-left at 200 degrees when the planet is right of centre, else
        // up-right at -20; the box 7% of the width beyond that point and 3% up (x4's offset), clamped, out of the middle
        var leftSide = tgt.x > sz.w / 2, ang = (leftSide ? 215 : -35) * D2R;
        var lx0 = tgt.x + Math.cos(ang) * tgt.r, ly0 = tgt.y + Math.sin(ang) * tgt.r;
        var bx = leftSide ? lx0 - sz.w * 0.07 - tw : lx0 + sz.w * 0.07, by = ly0 - sz.h * (portrait ? 0.1 : 0.05) - th / 2;
        bx = clamp(bx, 8, sz.w - tw - 8); by = clamp(by, 8, sz.h - th - 8);
        if (boxInCentre({ x: bx, y: by, w: tw, h: th }, sz)) { by = Math.min(by, sz.h / 3 - th - 6); if (boxInCentre({ x: bx, y: by, w: tw, h: th }, sz)) bx = leftSide ? sz.w / 3 - tw - 6 : sz.w * 2 / 3 + 6; }
        by = clamp(by, 8, sz.h - th - 8);
        var kr = keysRect(sz);                             // under the keys list, else beside it, never behind it
        if (kr && hit({ x: bx, y: by, w: tw, h: th }, kr, 6)) {
          if (kr.y + kr.h + 10 + th <= sz.h - 8) by = kr.y + kr.h + 10; else bx = clamp(kr.x + kr.w + 10, 8, sz.w - tw - 8);
        }
        // a planet that fills the view clamps the label into a corner: it never covers a HUD slot that shows (gauge, agents,
        // boost, need, counter, radar, comms), it moves below or beside the slot it hits
        var spot = clearSpot({ x: bx, y: by, w: tw, h: th }, sz, kr, false);
        bx = spot.x; by = spot.y;
        tRect = { x: bx, y: by, w: tw, h: th };
        H.target.className = 'target3d' + (leftSide ? ' left' : '');
        H.target.style.left = px(bx); H.target.style.top = px(by);
        var ex = leftSide ? bx + tw : bx, ey = by + th / 2, pts = r1(lx0) + ',' + r1(ly0) + ' ' + r1(ex) + ',' + r1(ey);
        H.leadL.setAttribute('points', pts); H.leadU.setAttribute('points', pts); H.leadL.setAttribute('stroke', acol);
        H.leadDot.setAttribute('cx', r1(lx0)); H.leadDot.setAttribute('cy', r1(ly0)); H.leadDot.setAttribute('fill', acol); H.lead.style.display = lx0 > 0 && lx0 < sz.w && ly0 > 0 && ly0 < sz.h ? '' : 'none';   // a planet that fills the view has no limb on screen: no leader from nowhere
      } else H.lead.style.display = 'none';
      // ---- markers: at most 16 (phone 6) DOM labels, one shape and one colour per kind, hidden behind planets and out of
      // the centre third (nebreck HudProjection.ts: pooled DOM markers), priority locked > need you > problem > agent > planet
      var marks = [], cand = [];
      planets.forEach(function (p) {
        var c2 = p.counts || {}, pri = L.uid === p.uid && L.state === 'locked' ? PRI.locked : c2.need_you ? PRI.need : c2.problems ? PRI.problem : PRI.planet;
        cand.push({ uid: p.uid, kind: 'planet', v: new ctx.THREE.Vector3(p.x, p.r * 1.15, p.y), name: String(p.label), pri: pri, self: p.uid });
      });
      (w.craft || []).forEach(function (cr) { if (cr.role === 'carrier') return; var o = scenePos(ctx, cr.uid); if (o) cand.push({ uid: cr.uid, kind: 'agent', v: o.v, name: String(cr.label), letter: String(cr.letter || cr.label || '?').charAt(0).toUpperCase(), pri: PRI.agent, colour: cr.colour }); });
      Object.keys(S.where).forEach(function (uid) { var o = S.where[uid]; if (o.kind === 'foe') cand.push({ uid: uid, kind: 'problem', v: o.v, name: '', pri: PRI.problem }); });
      (w.waiting || []).forEach(function (it) { var p = planets.filter(function (q) { return q.uid === it.parent; })[0]; if (p) cand.push({ uid: it.uid, kind: 'waiting', v: new ctx.THREE.Vector3(p.x + p.r * 0.8, p.r * 0.9, p.y), name: '', pri: PRI.waiting }); });
      if (f) f.steps.forEach(function (st, i) {
        var p = planets.filter(function (q) { return q.uid === st.planet; })[0]; if (!p) return;
        if (p.uid === tgtUid && tOn) return;           // the target's own steps: its label and the plate say them
        cand.push({ uid: 'step:' + f.id + ':' + st.key, kind: 'step', v: new ctx.THREE.Vector3(p.x - p.r * 0.9, p.r * 1.4 + i * 4, p.y), name: 'step ' + (i + 1) + ' · ' + (STATE_WORD[st.state] || st.state), letter: String(i + 1), pri: PRI.step, colour: STATE_COL[st.state] });
        if (st.artifact) cand.push({ uid: 'artifact:' + f.id + ':' + st.key, kind: 'artifact', v: new ctx.THREE.Vector3(p.x + p.r * 0.9, p.r * 1.4, p.y), name: '', pri: PRI.artifact });
      });
      var cap = portrait ? 6 : 16, shown = 0, nameBoxes = [], nfs = Math.max(12, Math.round(3.7 * fu));
      cand.sort(function (x, y) { return x.pri - y.pri; });
      if (fpOn) cand.forEach(function (m) {
        if (shown >= cap) return;
        if (m.kind === 'planet' && m.uid === tgtUid && tOn) return;     // the target has its label
        var s3 = ctx.project(m.v.x, m.v.y, m.v.z);
        if (!s3.onScreen || inCentre(s3.x, s3.y, sz, 16) || occluded(ctx, m.v, m.self)) return;
        var e2 = markEl(shown++), col = m.colour && (m.kind === 'agent' || m.kind === 'step') ? m.colour : MARK_COL[m.kind];
        e2.root.style.display = ''; e2.root.style.left = px(s3.x); e2.root.style.top = px(s3.y);
        var d = SHAPE_D[SHAPE[m.kind]];
        e2.shape.setAttribute('d', d); e2.under.setAttribute('d', d); e2.shape.setAttribute('stroke', m.kind === 'agent' ? '#061020' : col);
        e2.shape.setAttribute('fill', m.kind === 'problem' || m.kind === 'agent' ? col : m.kind === 'planet' ? 'rgba(5,7,13,0.35)' : 'none');
        e2.under.style.display = m.kind === 'agent' ? 'none' : '';
        e2.letter.textContent = m.kind === 'agent' || m.kind === 'step' ? m.letter : ''; e2.letter.setAttribute('y', m.kind === 'agent' ? '-1' : '4.5');
        e2.letter.setAttribute('fill', m.kind === 'step' ? col : '#FFFFFF');
        text(e2.text, m.name); e2.text.style.fontSize = font(fu, 3.7);
        // a name whose box would overlap a name already shown (the list is in priority order), the target's label or a HUD slot hides
        // its words and keeps its shape; the box is estimated from the characters and the type size (no layout read)
        var nshow = !!m.name;
        if (nshow) {
          var nw = m.name.length * nfs * 0.55 + 12, nh = nfs * 1.3, agentM = m.kind === 'agent';
          var nbx = { x: agentM ? s3.x - 15 : s3.x + 17, y: agentM ? s3.y - 38 : s3.y - nh / 2, w: nw, h: nh };
          if (hitAny(nbx, nameBoxes, 3) || hitAny(nbx, S.avoid, 2) || (tRect && hit(nbx, tRect, 2))) nshow = false; else nameBoxes.push(nbx);
        }
        e2.text.style.display = nshow ? '' : 'none';
        e2.root.className = 'mark3d k-' + m.kind;
        marks.push({ uid: m.uid, kind: m.kind, shape: SHAPE[m.kind] });
      });
      for (var mi = shown; mi < H.markPool.length; mi++) H.markPool[mi].root.style.display = 'none';
      // ---- the callout (video-x1 #13): one tag on the followed workflow's working agent (else its planet), its name and step,
      // a 2 px underline, a 45 degree leader up-right; never two
      var callout = null;
      if (f && fpOn) {
        var anchor = f.agent ? scenePos(ctx, f.agent) : null, ap = null;
        if (anchor) ap = ctx.project(anchor.v.x, anchor.v.y, anchor.v.z);
        if (!ap || !ap.onScreen) {
          var fp = planets.filter(function (p) { return p.uid === f.planet; })[0];
          if (fp) { var c0 = ctx.project(fp.x, 0, fp.y); if (c0.onScreen) { var rp = radiusPx(ctx, fp, sz); ap = { x: c0.x + rp * 0.71, y: c0.y - rp * 0.71, onScreen: true }; } }
        }
        if (ap && ap.onScreen) {
          var ctext = f.title + ' · step ' + Math.max(1, f.index + 1);
          text(H.ctag, ctext); H.ctag.style.fontSize = font(fu, 4.0);
          var tw2 = H.ctag.offsetWidth || 160, th2 = H.ctag.offsetHeight || 20, len = 60;
          var box = function (l) { var ex2 = ap.x + l * Math.SQRT1_2, ey2 = ap.y - l * Math.SQRT1_2; return { x: ex2, y: ey2 - th2, w: tw2, h: th2 }; };
          var bxx = box(len);
          while ((boxInCentre(bxx, sz) || (tOn && H.target.offsetWidth && Math.abs(bxx.y - parseFloat(H.target.style.top)) < th2 + 30 && bxx.x < parseFloat(H.target.style.left) + H.target.offsetWidth && bxx.x + tw2 > parseFloat(H.target.style.left))) && len < 420) { len += 20; bxx = box(len); }
          show(H.callout, true); H.callout.style.left = px(ap.x); H.callout.style.top = px(ap.y);
          H.cline.style.width = px(len); H.ctag.style.left = px(len * Math.SQRT1_2); H.ctag.style.top = px(-len * Math.SQRT1_2 - th2);
          callout = { text: ctext, leaderDeg: 45, leaderPx: len, anchor: f.agent || f.planet };
        }
      }
      if (!callout) show(H.callout, false);
      // ---- arrows to what is off the screen (nebreck HudProjection.ts:93-223: the camera-space bearing, atan2(x, y),
      // never project() behind the eye; deathstar HUD.ts:289-305 on an ellipse rx 0.42 W, ry 0.40 H); HELP for stuck agents
      var arrows = [], want = [], helpS = S.feel.helpS || { period: 0.5, on: 0.25, total: 10 };
      planets.forEach(function (p) {
        var c3 = p.counts || {};
        if ((L.state === 'locked' && L.uid === p.uid) || (f && f.planet === p.uid) || c3.need_you || c3.problems)
          want.push({ uid: p.uid, kind: 'planet', counts: c3, name: String(p.label), sub: (f && f.planet === p.uid) ? 'step ' + Math.max(1, f.index + 1) + ' works here' : stateWords(p), v: new ctx.THREE.Vector3(p.x, 0, p.y),
            col: (f && f.planet === p.uid) ? COL.cyan : c3.problems ? COL.red : c3.need_you ? COL.amber : COL.teal, pri: L.uid === p.uid ? 0 : (f && f.planet === p.uid) ? 1 : c3.need_you ? 2 : 3 });
      });
      (w.craft || []).forEach(function (cr) {
        if (cr.role === 'carrier') return;
        var stuck = /block|stuck|help|need|wait/.test(String(cr.state || ''));
        var o = scenePos(ctx, cr.uid); if (!o) return;
        if (stuck) { if (S.help[cr.uid] == null) S.help[cr.uid] = S.t; } else delete S.help[cr.uid];
        if (stuck || String(cr.state).indexOf('work') >= 0) want.push({ uid: cr.uid, kind: 'agent', name: stuck ? 'HELP · ' + String(cr.label) : String(cr.label), sub: stuck ? 'stuck, needs you' : 'working', v: o.v, col: cr.colour || '#CFE3FF', help: stuck, pri: stuck ? 0 : 4 });
      });
      want.sort(function (x, y) { return x.pri - y.pri; });
      var ai = 0, rx = sz.w * 0.42, ry = sz.h * 0.40, off = [], arrowGroup = null, overlaps = 0;
      if (fpOn) want.forEach(function (o) {
        var s4 = ctx.project(o.v.x, o.v.y, o.v.z);
        if (s4.onScreen) return;
        var q = camSpace(ctx, o.v), behind = q.z > 0;
        off.push({ o: o, behind: behind, ang: behind ? Math.atan2(q.x, q.y - 0.5 * q.z) : Math.atan2(q.x, q.y) });   // 0 = up, clockwise; dead behind = down
      });
      // the HUD's own boxes an arrow never sits on (it slides along the ellipse to the nearest free angle), and the labels
      // it gives way to (labels win: it moves outward first, then along); one pill for more than two contacts behind
      // the locked target and the followed workflow's planet keep their own arrows; the rest behind group when over two
      off.forEach(function (x) { x.own = x.o.pri <= 1 && !x.o.help; });
      var behindN = off.filter(function (x) { return x.behind && !x.own; }).length, grouped = behindN > 2;
      var avoid = fpOn && off.length ? hudRects([H.gauge, H.counter, H.agents, H.boost, H.need, H.block, H.comms, H.radar, H.mcard, H.plate, H.pip, H.callout, H.band]).concat(touchRects()) : [];
      var keysR = fpOn && off.length ? keysRect(sz) : null; if (keysR) avoid.push(keysR);
      // the ship itself (the chase camera shows it) is a box an arrow never sits on either
      if (fpOn && off.length) {
        var shipP = ctx.project(ox, oy, oz);
        [fpx, npx].forEach(function (rp) { if (rp) avoid.push({ x: rp.x - rw * 0.7, y: rp.y - rw * 0.7, w: rw * 1.4, h: rw * 1.4 }); });   // the reticle's own boxes
        if (tgt && tgt.on) avoid.push({ x: tgt.x - tgt.r - 12, y: tgt.y - tgt.r - 12, w: 2 * tgt.r + 24, h: 2 * tgt.r + 24 });   // the target planet
        if (shipP && !shipP.behind) { var shw = sz.w * (portrait ? 0.52 : 0.2), shh = shw * 0.36; avoid.push({ x: shipP.x - shw / 2 - 16, y: shipP.y - shh / 2 - 36, w: shw + 32, h: shh + 96 }); }
      }
      var labels = fpOn && off.length ? labelRects(shown) : [], placed = [], unplaced = 0;
      if (grouped) {
        var gb = behindPill(off.filter(function (x) { return x.behind && !x.own; }), sz, fu);
        avoid.push(gb.rect); arrowGroup = gb.audit;
      } else show(H.behind, false);
      off.forEach(function (x) {
        var o = x.o, behind = x.behind, ang2 = x.ang;
        if (grouped && behind && !x.own) { arrows.push({ uid: o.uid, name: o.name, angle: r1(((ang2 * R2D) + 360) % 360), behind: true, help: !!o.help, grouped: true }); return; }
        if (ai >= (portrait ? 4 : 8)) return;
        var e3 = arrowEl(ai++);
        e3.root.style.display = '';
        e3.c1.setAttribute('fill', o.col); e3.c2.setAttribute('fill', o.col);
        var visible = true;
        if (o.help) { var age = S.t - S.help[o.uid]; if (age < helpS.total) visible = (age % helpS.period) < helpS.on; }
        e3.svg.style.opacity = visible ? '1' : '0.15';
        // behind: the second line says which way to turn (the arrow sits on that side); the card never runs past the screen
        text(e3.t1, o.name.toUpperCase() + (behind ? ' · BEHIND YOU' : ''));
        text(e3.t2, behind ? 'turn ' + (Math.sin(ang2) >= 0 ? 'right' : 'left') + (o.sub ? ' · ' + o.sub : '') : (o.sub || ''));
        var amw = Math.max(120, sz.w - 24); if (e3.mw !== amw) { e3.mw = amw; e3.box.style.maxWidth = px(amw); }
        e3.t1.style.fontSize = font(fu, portrait ? 4.6 : 4.3); e3.t2.style.fontSize = font(fu, portrait ? 4.7 : 3.9);
        e3.box.style.borderColor = o.col;
        var bw = e3.box.offsetWidth || 120, bh2 = e3.box.offsetHeight || 30;
        var at = placeArrow(ang2, bw, bh2, sz, rx, ry, avoid, labels, placed);
        if (!at) {                                      // no free slot anywhere: not drawn, counted as grouped
          e3.root.style.display = 'none'; ai--; unplaced++;
          arrows.push({ uid: o.uid, name: o.name, angle: r1(((ang2 * R2D) + 360) % 360), behind: behind, help: !!o.help, grouped: true, placed: false });
          return;
        }
        placed.push(at.rect);
        e3.root.style.left = px(at.ax); e3.root.style.top = px(at.ay);
        // the chevrons point at the thing (its own bearing); the words sit on the inner side of them, toward the middle
        e3.svg.style.transform = 'rotate(' + r1(ang2 * R2D - 90) + 'deg)';
        e3.box.style.left = px(at.box.x - at.ax); e3.box.style.top = px(at.box.y - at.ay);
        e3.root.className = 'arrow3d' + (o.help ? ' help' : '') + (behind ? ' behind' : '');
        arrows.push({ uid: o.uid, name: o.name, angle: r1(((ang2 * R2D) + 360) % 360), at: r1(((at.th * R2D) + 360) % 360), k: at.k, behind: behind, help: !!o.help, grouped: false,
          rect: [Math.round(at.rect.x), Math.round(at.rect.y), Math.round(at.rect.w), Math.round(at.rect.h)] });
      });
      if (!fpOn) show(H.behind, false);
      for (var ar = ai; ar < H.arrowPool.length; ar++) H.arrowPool[ar].root.style.display = 'none';
      // the audit's overlaps are read off the final placements: arrow boxes on each other, the HUD's boxes or the labels
      for (var pi2 = 0; pi2 < placed.length; pi2++) {
        for (var pj = pi2 + 1; pj < placed.length; pj++) if (hit(placed[pi2], placed[pj], 0)) overlaps++;
        if (hitAny(placed[pi2], avoid, 0) || hitAny(placed[pi2], labels, 0)) overlaps++;
      }
      var groupedN = arrows.filter(function (x) { return x.grouped; }).length;
      // ---- the target card (tcrf §1.8.3: the sequel's TARGET monitor), 0.8 s during an entry from a planet's card: the
      // planet turning on a teal grid (its own inset), its name and kind, its counts, the working step's words or UNKNOWN
      var tc = FH.targetCard, cardP = tc ? planets.filter(function (p) { return p.uid === tc.uid; })[0] : null, insetUsed = false;
      show(H.card, !!(cardP && on));
      if (cardP && on) {
        var cw = Math.min(380, sz.w * 0.6), chh = Math.round(cw * 9 / 16), cx0 = (sz.w - cw) / 2, cy0 = sz.h * 0.14, headH = 26;
        H.card.style.left = px(cx0); H.card.style.top = px(cy0); H.card.style.width = px(cw);
        H.cardInset.style.height = px(chh);
        var I = insetScene(ctx, cardP.uid, true);
        if (I) { insetDraw(ctx, I, { x: Math.round(cx0 + 2), y: Math.round(cy0 + headH + 2), w: Math.round(cw - 4), h: chh - 4 }, dt, 1.4); insetUsed = true; }
        var cc = cardP.counts || {}, says = null;
        (w.workflows || []).forEach(function (wf) { (wf.steps || []).forEach(function (st) { if (st && st.planet_uid === cardP.uid && (st.state === 'working' || st.state === 'needs_you') && says === null) says = String(st.says || ''); }); });
        var lines = [String(cardP.label) + (cardP.purpose && cardP.purpose !== cardP.label ? ' · ' + cardP.purpose : ''),
          plural(cc.problems || 0, 'problem', 'problems') + ' · ' + (cc.need_you || 0) + ' need you · ' + plural(cc.decisions || 0, 'decision', 'decisions'),
          says === null ? 'Nothing running here' : 'Working: ' + (says || 'UNKNOWN')];
        S.targetCardText = lines.join('\n');
        text(H.cardText, S.targetCardText); H.cardText.style.fontSize = font(fu, 4.2); H.cardHead.style.fontSize = font(fu, 3.6);
      }
      // ---- the LIVE inset (video-x2 #7): the followed workflow's working planet when it has been off the screen over 1 s;
      // 320 x 180 at the top centre (tablet 240 x 135, phone none), the planet alone at low detail, no effects
      var pip = { visible: false, target: null, calls: 0 };
      var wp = f && f.planet ? planets.filter(function (p) { return p.uid === f.planet; })[0] : null;
      if (wp && fpOn && !phone) {
        var s5 = ctx.project(wp.x, 0, wp.y);
        S.offFor = s5.onScreen ? 0 : S.offFor + dt;
      } else S.offFor = 0;
      if (wp && S.offFor > 1 && !insetUsed) {
        var pw = tablet ? 240 : 320, phh = tablet ? 135 : 180, rect = { x: Math.round((sz.w - pw) / 2), y: Math.round(F.y + F.h * 0.025), w: pw, h: phh };
        var I2 = insetScene(ctx, wp.uid, false);
        if (I2) {
          pip.calls = insetDraw(ctx, I2, rect, dt, 0.35); pip.visible = true; pip.target = wp.uid; pip.rect = rect;
          H.pip.style.left = px(rect.x - 2); H.pip.style.top = px(rect.y - 2); H.pip.style.width = px(rect.w); H.pip.style.height = px(rect.h);
          text(H.piplabel, 'LIVE · ' + wp.label); H.piplabel.style.fontSize = font(fu, 3.6);
        }
      }
      show(H.pip, pip.visible);
      if (!pip.visible && !insetUsed && S.inset) insetDispose();
      // ---- the first-entry caption (video-o9 #43): during the establishing orbit, the HUD off; the entry's timeline bar
      var cap2 = FH.caption;
      show(H.caption, !!(cap2 && on));
      if (cap2 && on) {
        var cp = String(cap2.text).split(' · ');
        text(H.cap1, cp[0].toUpperCase()); text(H.cap2, cp.slice(1).join(' · '));
        H.cap1.style.fontSize = font(fu, 4.8); H.cap2.style.fontSize = font(fu, 3.7);
        H.caption.style.top = px(F.y + F.h * 0.02);
        S.caption.ms += dt * 1000; H.root.style.opacity = '1';
      }
      var en = FH.entry;
      show(H.tbar, !!(en && on && en.total > 0));
      if (en && on && en.total > 0) {
        H.tbar.style.top = px(F.y + F.h * (cap2 ? 0.087 : 0.03)); H.tbarText.style.fontSize = font(fu, 3.3);
        H.tbarFill.style.width = r1(clamp(en.el / en.total, 0, 1) * 100) + '%';
        text(H.tbarText, en.words + ' · ' + en.el.toFixed(1) + ' of ' + en.total.toFixed(2).replace(/0$/, '') + ' s');
        H.root.style.opacity = '1';
      }
      H.vig.style.opacity = hudOn ? '1' : '0';
      // ---- the audit: hud.* and lock
      a.lock = L.state === 'locked' ? L.uid : null;
      a.hud = {
        visible: on && !ctx.hudHidden, standIn: false, u: r3(u), frame: { x: Math.round(F.x), y: Math.round(F.y), w: Math.round(F.w), h: Math.round(F.h), aspect: set.aspect },
        needYou: ny, gauge: (total - clear) ? (total - clear) + ' of ' + total + ' folders ' + (total - clear === 1 ? 'needs' : 'need') + ' you' : 'all ' + total + ' folders: nothing to do', gaugeShare: r3(share), alpha: r3(alpha),
        reticle: { near: { x: r1(npx.x), y: r1(npx.y) }, far: { x: r1(fpx.x), y: r1(fpx.y) }, colour: rcol, size: rw },
        lock: { state: L.state, uid: L.uid, ms: Math.round(L.ms), candidate: S.cand },
        target: tgtText ? { uid: tgtUid, text: tgtText } : null, markers: marks, labels: marks.filter(function (m) { return m.kind === 'planet' || m.kind === 'agent'; }).length + (tOn ? 1 : 0),
        arrows: arrows, arrowGroup: arrowGroup, arrowOverlaps: overlaps, grouped: groupedN, unplaced: unplaced, help: { periodMs: Math.round(helpS.period * 1000), totalS: r1(helpS.total), active: Object.keys(S.help).length },
        comms: { current: cur ? { priority: cur.priority, who: cur.who, text: cur.text, hold: cur.hold } : null, dropped: S.comms.dropped, said: S.comms.said },
        radar: radar, boost: { state: B.state, locked: B.locked, meter: r3(meter) },
        mission: { stages: M.stages, objective: M.objective, title: M.title, card: !!cardOn, cards: M.shown },
        callout: callout || { text: '', leaderDeg: 45 }, pip: pip, plate: { text: S.plate.text, visible: !!plateOn },
        caption: { shownMs: Math.round(S.caption.ms), text: cap2 ? cap2.text : '' }, targetCard: { text: S.targetCardText, visible: !!(cardP && on) },
        band: { visible: !!bandOn, text: bandOn ? H.bandT.textContent : '' }
      };
    },
    dispose: function () {
      if (!S) return;
      insetDispose();
      if (H && H.root) H.root.style.display = 'none';
      if (FH.hudKey === hudKey) { FH.hudKey = null; FH.hudKeyUp = null; FH.tapLock = null; }
      FH.lock = null;
      S = null;
    }
  });
})(typeof globalThis !== 'undefined' ? globalThis : this);
