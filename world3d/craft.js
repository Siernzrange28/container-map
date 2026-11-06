// world3d/craft.js — the craft: the owner's ship, the agents in formation, the carrier, problems on foePos, waiting beacons,
// one glow buffer, trails.
// Registered on the 3D view's registry (world3d/core.js); reads only `world` (cmWorld()) and `ctx`, never page globals.
/* port: yuka (MIT) — offset pursuit, arrive and follow-path steering (OffsetPursuitBehavior.js:57-89, ArriveBehavior.js:57-90,
   FollowPathBehavior.js:52-80, Vehicle.update), rewritten on plain arrays */
/* port: spacekit (MIT) — drop-lines from each craft to the map plane (Orbit.ts:585-620, getLinesToEcliptic) */
//
// What it draws, all from the skin's 3D data (skins/<skin>-3d.json through ctx.skin3d, a sparse overlay on the neutral
// file) and the world: the owner's ship at world.ship (hidden in the cockpit rig: the camera is in it), the
// carrier at the sun's dock ring, every agent (a working one holds beside its planet, an idle one docks at the carrier, the
// agents of the followed workflow hold V slots behind the owner and the working one flies to its step's planet),
// problems on the 2D foePos orbit with 1-4 glowing tells coloured by kind, waiting beacons over their planet, and, in
// one additive Points buffer, every engine glow, lamp, tell and the ring beacon that replaces a craft smaller than
// 12 px on screen. Nothing moves or flashes unless the data changed (rev) or the owner acted.
// Audit (this module's namespaces only): ctx.audit.models.* and ctx.audit.craft.*.
(function (g) {
  'use strict';
  var CM3D = g.CM3D;
  if (!CM3D) return;
  var HK = CM3D.hull, TAU = Math.PI * 2, C = {};

  // ---------------------------------------------------------------- pure helpers (node tests read these)
  C.seedOf = function (s) { var h = 7; s = String(s || ''); for (var i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0; return h; };
  // the 2D page's orbit for problem i of n around a planet (render-game.py foePos), t in ms; y keeps the 2D squash so
  // the first 3D frame lands on the 2D picture from straight above
  C.foePos = function (m, i, n, t, reduced) {
    n = Math.max(1, Math.min(8, n));
    var er = m.r + 26, a = i * TAU / n + (C.seedOf(m.uid) % 7);
    return {a: a, x: m.x + Math.cos(a) * er, y: m.y + Math.sin(a) * er * 0.78};
  };
  // a problem -> the problem craft that shows it and the colour of its tells. cmWorld() gives every problem contact the
  // kind 'contact' and its producer in `source` (cron, task, t3, night, seat), and a problem of the map's own its kind
  // in both (cron, wreck): the source decides first, a generic kind never does
  C.foeClass = function (f) {
    f = f || {};
    var kind = String(f.kind || '').toLowerCase();
    if (kind === 'contact' || kind === 'problem') kind = '';
    var k = [String(f.source || '').toLowerCase(), kind, String(f.state || '').toLowerCase()].join(' ');
    if (/file|big|bloat|disk|size/.test(k)) return {model: 'rock', tell: 'big_file'};
    if (/wreck|derelict|lost|abandon|orphan|dead|frozen/.test(k)) return {model: 'derelict', tell: 'task_failed'};
    if (/cron|late|stale|job|timer|schedule/.test(k)) return {model: 'drone', tell: 'job_late'};
    return {model: 'raider', tell: 'task_failed'};
  };
  // 1-4 tells: more urgent, more lamps (urgency 0-1, or a number of days, or a tier word)
  C.tellCount = function (f) {
    var u = f && f.urgency;
    if (typeof u === 'number' && isFinite(u)) return Math.max(1, Math.min(4, u <= 1 ? 1 + Math.floor(u * 3.999) : 1 + Math.floor(Math.log2(1 + u))));
    var tier = String((f && (f.tier || u)) || '');
    return tier === 'raider' ? 3 : tier === 'aging' ? 2 : 1;
  };
  // V slots behind the leader in its own frame (x right, y up, z back), k = 0, 1, 2 (at most three). A slot stays
  // a little behind the leader, so a chase camera (view.back u behind it) sees the wingman as far away as the leader and
  // never bigger. Every slot stays on the leader's plane (|y| <= 1.4 u: no drop-line of its own, 's 2 u). On a
  // narrow screen (view.aspect, view.fov) the slot first closes in toward the leader, and where even a wingspan of
  // clearance does not fit beside it the slot moves ahead along the plane until it does: in perspective the frame is
  // wider further away, so the wingman flies an echelon up and to the side of the leader, in the frame, never over it.
  C.formationSlot = function (k, span, view) {
    var s = span || 14, side = k % 2 === 0 ? 1 : -1, row = Math.floor(k / 2) + 1;
    var lat = s * (1.45 + 0.55 * (row - 1)), y = s * 0.05 * row, z = s * 0.3 * row;
    if (view && view.aspect > 0) {
      var back = view.back || 50, half = Math.tan((view.fov || 55) * Math.PI / 360) * view.aspect, k85 = 0.85 * half;
      var room = function (zz) { return k85 * Math.max(10, back - zz) - 0.5 * s; };   // the wingtip inside the frame
      var least = s * (0.8 + 0.35 * (row - 1));                                       // clear of the leader's wings
      if (lat > room(z)) {
        if (room(z) >= least) lat = room(z);
        else { lat = least; z = back - (least + 0.5 * s) / k85; }
      }
    }
    return [side * lat, y, z];
  };
  // ---- steering (yuka, MIT): a vehicle is {pos, vel, maxSpeed, mass}
  function v3(x, y, z) { return [x || 0, y || 0, z || 0]; }
  function vsub(a, b) { return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]; }
  function vadd(a, b) { return [a[0] + b[0], a[1] + b[1], a[2] + b[2]]; }
  function vmul(a, s) { return [a[0] * s, a[1] * s, a[2] * s]; }
  function vlen(a) { return Math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2]); }
  C.arrive = function (v, target, decel, tolerance) {
    var d = vsub(target, v.pos), dist = vlen(d);
    if (dist <= (tolerance || 0)) return vmul(v.vel, -1);
    var speed = Math.min(dist / decel, v.maxSpeed);
    return vsub(vmul(d, speed / dist), v.vel);
  };
  C.seek = function (v, target) {
    var d = vsub(target, v.pos), l = vlen(d) || 1;
    return vsub(vmul(d, v.maxSpeed / l), v.vel);
  };
  // offset pursuit: arrive at the slot's predicted position (look-ahead = distance / (max speed + leader speed))
  C.offsetPursuit = function (v, leader, offsetWorld, decel) {
    var to = vsub(offsetWorld, v.pos), look = vlen(to) / (v.maxSpeed + vlen(leader.vel));
    return C.arrive(v, vadd(offsetWorld, vmul(leader.vel, look)), decel || 0.3);
  };
  // follow a path: seek each waypoint, step to the next within `next`, arrive at the last
  C.followPath = function (v, path, next, decel) {
    while (path.i < path.pts.length - 1 && vlen(vsub(path.pts[path.i], v.pos)) < next) path.i++;
    var last = path.i >= path.pts.length - 1;
    return last ? C.arrive(v, path.pts[path.pts.length - 1], decel) : C.seek(v, path.pts[path.i]);
  };
  C.stepVehicle = function (v, force, dt) {
    v.vel = vadd(v.vel, vmul(force, dt / (v.mass || 1)));
    var s = vlen(v.vel);
    if (s > v.maxSpeed) v.vel = vmul(v.vel, v.maxSpeed / s);
    v.pos = vadd(v.pos, vmul(v.vel, dt));
    return v;
  };
  // the travel of an agent from where it is to a planet's orbit: lift, cross, arrive (<= 3.5 s, overshoot
  // < 0.05 r). D = straight distance; the cruise speed grows with D so a long hop still lands in time.
  C.travelPlan = function (from, to) {
    var d = vsub(to, from), D = vlen(d), lift = Math.min(240, 0.16 * D);
    return {pts: [vadd(from, [d[0] * 0.25, lift, d[2] * 0.25]), vadd(from, [d[0] * 0.62, lift * 0.8, d[2] * 0.62]), to.slice()], i: 0,
            maxSpeed: Math.max(260, D / 1.55), decel: 0.22, next: Math.max(12, 0.05 * D), D: D, to: to.slice()};
  };
  C.travelStep = function (v, plan, dt) {
    v.maxSpeed = plan.maxSpeed;
    var f = C.followPath(v, plan, plan.next, plan.decel);
    C.stepVehicle(v, f, dt);
    var dir = vsub(plan.to, plan.pts[plan.pts.length - 2]), l = vlen(dir) || 1;
    var past = ((v.pos[0] - plan.to[0]) * dir[0] + (v.pos[1] - plan.to[1]) * dir[1] + (v.pos[2] - plan.to[2]) * dir[2]) / l;
    plan.overshoot = Math.max(plan.overshoot || 0, past);
    return vlen(vsub(plan.to, v.pos));
  };
  // own engine glow scale by the flight state (feel3d.glow: boost, brake, cooldown, start flash)
  C.glowScale = function (ship, feel, flashT) {
    var gl = (feel && feel.glow) || {boost: 1.6, brake: 0.5, cooldown: 0.7, flash: 1}, k = 1;
    if (ship && ship.boost) k = gl.boost;
    else if (ship && ship.brake) k = gl.brake;
    else if (ship && typeof ship.meter === 'number' && ship.meter < 0.999) k = gl.cooldown;
    if (flashT > 0 && gl.flash > 1) k = Math.max(k, 1 + (gl.flash - 1) * flashT);
    return k;
  };
  C.flameK = function (state) { return /work|run|busy|check/.test(String(state || '')) ? 1.5 : 0; };
  // one livery per agent: its colour on the band, its initial on the decal, a stripe pattern so two agents of one
  // colour still differ
  C.liveries = function (agents) {
    var seen = {}, out = {};
    agents.forEach(function (a, i) {
      var colour = /^#[0-9a-f]{6}$/i.test(String(a.colour || '')) ? a.colour.toUpperCase() : '#6FB7FF';
      var letter = String(a.letter || a.label || a.uid || '?').replace(/[^A-Za-z0-9]/g, '').charAt(0).toUpperCase() || '?';
      var pattern = 0, id;
      do { id = colour + '|' + letter + '|' + pattern; pattern++; } while (seen[id]);
      seen[id] = 1;
      out[a.uid] = {colour: colour, letter: letter, pattern: pattern - 1, livery: id};
    });
    return out;
  };
  // the whole-mesh look's shade step for a face whose normal in view space is n: a light fixed to the camera, upper left
  C.rampStep = function (n) {
    var s = 1 / Math.sqrt(3), d = -n[0] * s + n[1] * s + n[2] * s;
    return Math.max(0, Math.min(9, Math.floor(15.68 * d) - 6));
  };
  // rock level of detail by its size on screen: 476 / 160 / 60 faces at >= 56 / >= 24 / smaller
  // how far the craft's painted gradients spread (look3d.gradK, else 2:.5 "painted per-face gradients")
  C.gradK = function (look) { var k = look && Number(look.gradK); return k > 0 ? k : 2; };
  C.rockLod = function (px) { return px >= 56 ? 0 : px >= 24 ? 1 : 2; };
  C.FOE_BANK = 0.55;   // rad: how far a problem craft banks into its orbit turn (about 31 degrees)
  C.FOE_SCALE = 1.7;   // problem craft fly a size up on their hull (raider 20 u, drone 12 u): legible beside a planet
  C.ROCK_SCALE = 1.4;  // a big-file rock is 9 u x this (its LOD steps by its size on screen: 476 / 160 / 60 faces)
  C.TELL_PX = 16;      // a tell's lamp never draws smaller than this (CSS px): a disc of about 9 px with a white core and a
                       // glint in its colour, so the colour reads on a craft 30 px across and over a red-tinted planet
  C.BEACON_PX = 18;    // the ring beacon's point (CSS px); its ring is drawn at radius 0.74 with a 0.13 soft edge
  C.ringPx = function (size) { return +(size * 0.87).toFixed(1); };   // the ring's outer diameter on screen (CSS px)
  // a glow point's size on screen (CSS px) for a world size at a distance, as the glow shader draws it
  C.glowPx = function (size, dist, fovDeg, height) { return size * height / (2 * Math.tan(fovDeg * Math.PI / 360) * Math.max(1, dist)); };
  // the owner's ship's attitude for this frame from the core's seam (core.js ctx.ship, the camera lane writes it): alt in
  // u (null: the core's altitude, read back from audit.ship.alt, or audit.altitude on an older core), pitch in degrees,
  // rollDeg (null: no roll of its own). Returns {alt, pitch, roll} with the angles in radians.
  C.attitude = function (ctx, ship) {
    var cs = (ctx && ctx.ship) || {}, au = (ctx && ctx.audit) || {}, D = Math.PI / 180, fin = function (v) { return v != null && isFinite(v); };
    var alt = fin(cs.alt) ? Number(cs.alt) : fin(ship && ship.alt) ? Number(ship.alt)
      : (au.ship && fin(au.ship.alt)) ? Number(au.ship.alt) : fin(au.altitude) ? Number(au.altitude) : 0;
    return {alt: alt, pitch: fin(cs.pitch) ? Number(cs.pitch) * D : 0, roll: fin(cs.rollDeg) ? Number(cs.rollDeg) * D : 0};
  };
  // how far a wingman tilts its top toward the camera: none when the camera looks down on it at 23 degrees or more, up
  // to about 29 degrees as the view goes flat, so it is always seen from 23 degrees above its wings. c = the camera's
  // position in the craft's own frame (x right, y up, nose -z); returns the rotation as axis x angle (radians) in that
  // frame, about (up x c), so the top turns toward the camera
  C.showTilt = function (c) {
    var l = Math.hypot(c[0], c[1], c[2]);
    if (!(l > 1e-6)) return [0, 0, 0];
    var elev = Math.asin(Math.max(-1, Math.min(1, c[1] / l))), a = Math.max(0, Math.min(0.5, 0.40 - elev));
    var ax = [c[2], 0, -c[0]], al = Math.hypot(ax[0], ax[2]);
    return al > 1e-6 && a > 0 ? [ax[0] / al * a, 0, ax[2] / al * a] : [0, 0, 0];
  };
  C.shadowAlpha = function (alt) { return 0.55 + (0.12 - 0.55) * Math.min(1, Math.abs(alt) / 400); };
  C.projPx = function (size, dist, fovDeg, height) {
    return size * height / (2 * Math.max(1e-3, dist) * Math.tan(fovDeg * Math.PI / 360));
  };
  CM3D.craftKit = C;

  // ---------------------------------------------------------------- module state
  var S = null;          // everything built for one entry

  function col(THREE, hex) { var c = new THREE.Color(); c.setStyle(String(hex || '#ffffff')); return c; }

  // the one additive Points buffer: engine glows, lamps, tells, ring beacons and the emissive strips' bloom (shape 0 glow,
  // 1 ring, 2 lamp, 3 soft bloom, 4 tell lamp)
  var GLOW_VERT = [
    'attribute vec3 aCol; attribute float aSize; attribute float aShape; attribute float aPx;',
    'uniform float uScale; uniform float uDpr;',
    'varying vec3 vCol; varying float vShape;',
    'void main(){',
    '  vec4 mv = modelViewMatrix * vec4(position, 1.0);',
    '  gl_Position = projectionMatrix * mv;',
    '  float s = aPx > 0.5 ? aSize * uDpr : aSize * uScale / max(1.0, -mv.z);',
    '  gl_PointSize = clamp(s, 0.0, 256.0);',
    '  vCol = aCol; vShape = aShape;',
    '}'].join('\n');
  var GLOW_FRAG = [
    'varying vec3 vCol; varying float vShape;',
    'void main(){',
    '  vec2 p = gl_PointCoord * 2.0 - 1.0; float r = length(p); vec3 c;',
    '  if (vShape < 0.5) {',
    '    float core = smoothstep(0.32, 0.0, r);',
    '    float halo = exp(-r * r * 4.5) * 0.75;',
    '    float spk = max(0.0, 1.0 - abs(p.x) * 14.0) * max(0.0, 1.0 - abs(p.y)) + max(0.0, 1.0 - abs(p.y) * 14.0) * max(0.0, 1.0 - abs(p.x));',
    '    c = vCol * (halo + spk * 0.45) + vec3(1.0) * core * 0.9;',
    '  } else if (vShape < 1.5) {',
    '    float ring = smoothstep(0.13, 0.0, abs(r - 0.74));',
    '    float core = smoothstep(0.30, 0.18, r);',
    '    c = vCol * ring * 1.2 + vec3(1.0) * core;',
    '  } else if (vShape < 2.5) {',
    '    float dot0 = smoothstep(0.55, 0.15, r);',
    '    c = vCol * dot0 + vec3(1.0) * smoothstep(0.25, 0.0, r) * 0.6;',
    '  } else if (vShape < 3.5) {',
    '    c = vCol * exp(-r * r * 5.0) * 0.7 + vec3(1.0) * smoothstep(0.2, 0.0, r) * 0.18;   // a soft bloom (emissive strips)',
    '  } else {',
    '    float disc = smoothstep(0.62, 0.48, r), hot = smoothstep(0.24, 0.0, r);   // a tell: a lamp disc, hot core, glint',
    '    float gl = (max(0.0, 1.0 - abs(p.x) * 9.0) + max(0.0, 1.0 - abs(p.y) * 9.0)) * smoothstep(1.0, 0.5, r);',
    '    c = vCol * (disc * 1.25 + gl * 0.7) + vec3(1.0) * hot * 0.85;',
    '  }',
    '  if (r > 1.0) discard;',
    '  gl_FragColor = vec4(c, 1.0);',
    '}'].join('\n');
  function makeGlow(THREE, cap) {
    var geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(cap * 3), 3));
    geo.setAttribute('aCol', new THREE.BufferAttribute(new Float32Array(cap * 3), 3));
    geo.setAttribute('aSize', new THREE.BufferAttribute(new Float32Array(cap), 1));
    geo.setAttribute('aShape', new THREE.BufferAttribute(new Float32Array(cap), 1));
    geo.setAttribute('aPx', new THREE.BufferAttribute(new Float32Array(cap), 1));
    geo.setDrawRange(0, 0);
    var mat = new THREE.ShaderMaterial({uniforms: {uScale: {value: 600}, uDpr: {value: 1}}, vertexShader: GLOW_VERT, fragmentShader: GLOW_FRAG,
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending});
    var pts = new THREE.Points(geo, mat);
    pts.frustumCulled = false; pts.renderOrder = 20;
    return {obj: pts, geo: geo, n: 0, cap: cap, tmp: new THREE.Color(),
      begin: function () { this.n = 0; },
      add: function (x, y, z, hex, size, shape, px) {
        if (this.n >= this.cap) return;
        var i = this.n++, a = this.geo.attributes;
        a.position.array[i * 3] = x; a.position.array[i * 3 + 1] = y; a.position.array[i * 3 + 2] = z;
        if (typeof hex === 'string') this.tmp.setStyle(hex); else this.tmp.copy(hex);
        a.aCol.array[i * 3] = this.tmp.r; a.aCol.array[i * 3 + 1] = this.tmp.g; a.aCol.array[i * 3 + 2] = this.tmp.b;
        a.aSize.array[i] = size; a.aShape.array[i] = shape || 0; a.aPx.array[i] = px ? 1 : 0;
      },
      end: function () {
        var a = this.geo.attributes;
        ['position', 'aCol', 'aSize', 'aShape', 'aPx'].forEach(function (k) { a[k].needsUpdate = true; });
        this.geo.setDrawRange(0, this.n);
      }};
  }
  // the bloom of a hull's emissive strips (its anchors.strips, hullkit.stripPoints): a soft halo on each point, just off
  // the hull along its faces' normal, through the object's world matrix (or none: the points as they are); k scales the
  // size (an agent's state, the hero view); returns how many it drew
  C.STRIP_GLOW = 1.0;   // the bloom's strength (its colour is the strip's own)
  function stripGlows(glow, built, mtx, k, THREE, max) {
    var list = (built && built.anchors && built.anchors.strips) || [], drawn = 0, v = stripGlows.v || (stripGlows.v = new THREE.Vector3());
    for (var i = 0; i < list.length && drawn < (max || 24); i++) {
      var e = list[i], off = 0.12 + e[3] * 0.5;
      v.set(e[0] + e[4] * off, e[1] + e[5] * off, e[2] + e[6] * off);
      if (mtx) v.applyMatrix4(mtx);
      glow.add(v.x, v.y, v.z, e[7] || '#4DE8E0', Math.max(0.8, Math.min(2.4, e[3] * 5)) * (k || 1) * C.STRIP_GLOW, 3);
      drawn++;
    }
    return drawn;
  }
  // one dynamic line buffer (drop-lines, trails): pairs of points with a colour each
  function makeLines(THREE, cap, additive, opacity) {
    var geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(cap * 6), 3));
    geo.setAttribute('color', new THREE.BufferAttribute(new Float32Array(cap * 6), 3));
    geo.setDrawRange(0, 0);
    var mat = new THREE.LineBasicMaterial({vertexColors: true, transparent: true, opacity: opacity, depthWrite: false,
      blending: additive ? THREE.AdditiveBlending : THREE.NormalBlending});
    var obj = new THREE.LineSegments(geo, mat);
    obj.frustumCulled = false;
    return {obj: obj, geo: geo, n: 0, cap: cap, c: new THREE.Color(),
      begin: function () { this.n = 0; },
      seg: function (a, b, hexA, hexB, kA, kB) {
        if (this.n >= this.cap) return;
        var i = this.n++, P = this.geo.attributes.position.array, Cc = this.geo.attributes.color.array;
        P[i * 6] = a[0]; P[i * 6 + 1] = a[1]; P[i * 6 + 2] = a[2]; P[i * 6 + 3] = b[0]; P[i * 6 + 4] = b[1]; P[i * 6 + 5] = b[2];
        this.c.setStyle(hexA); Cc[i * 6] = this.c.r * (kA == null ? 1 : kA); Cc[i * 6 + 1] = this.c.g * (kA == null ? 1 : kA); Cc[i * 6 + 2] = this.c.b * (kA == null ? 1 : kA);
        this.c.setStyle(hexB || hexA); Cc[i * 6 + 3] = this.c.r * (kB == null ? 1 : kB); Cc[i * 6 + 4] = this.c.g * (kB == null ? 1 : kB); Cc[i * 6 + 5] = this.c.b * (kB == null ? 1 : kB);
      },
      end: function () { this.geo.attributes.position.needsUpdate = true; this.geo.attributes.color.needsUpdate = true; this.geo.setDrawRange(0, this.n * 2); }};
  }
  // blob shadows on the map plane: one instanced disc, its alpha falling with the craft's altitude (sm64 Shadow.js idea)
  var SH_VERT = ['attribute float aAlpha; varying float vA; varying vec2 vP;',
    'void main(){ vA = aAlpha; vP = position.xz; gl_Position = projectionMatrix * viewMatrix * modelMatrix * instanceMatrix * vec4(position, 1.0); }'].join('\n');
  var SH_FRAG = ['varying float vA; varying vec2 vP;',
    'void main(){ float r = length(vP) * 2.0; float a = vA * smoothstep(1.0, 0.35, r); gl_FragColor = vec4(0.0, 0.0, 0.02, a); }'].join('\n');
  function makeShadows(THREE, cap) {
    var geo = new THREE.CircleGeometry(0.5, 20); geo.rotateX(-Math.PI / 2);
    geo.setAttribute('aAlpha', new THREE.InstancedBufferAttribute(new Float32Array(cap), 1));
    var mat = new THREE.ShaderMaterial({vertexShader: SH_VERT, fragmentShader: SH_FRAG, transparent: true, depthWrite: false});
    var obj = new THREE.InstancedMesh(geo, mat, cap); obj.count = 0; obj.frustumCulled = false; obj.renderOrder = 2;
    var m = new THREE.Matrix4(), q = new THREE.Quaternion(), s = new THREE.Vector3(), p = new THREE.Vector3();
    return {obj: obj, n: 0, cap: cap,
      begin: function () { this.n = 0; },
      add: function (x, z, size, alpha) {
        if (this.n >= this.cap) return;
        p.set(x, 0.4, z); s.set(size, 1, size); m.compose(p, q, s);
        obj.setMatrixAt(this.n, m); geo.attributes.aAlpha.array[this.n] = alpha; this.n++;
      },
      end: function () { obj.count = this.n; obj.instanceMatrix.needsUpdate = true; geo.attributes.aAlpha.needsUpdate = true; }};
  }
  // engine flames: one dynamic additive buffer of cones, each from a nozzle backward (length = how busy the craft is;
  // web-flight-simulator jetFlame idea, no bloom)
  function makeFlames(THREE, cap) {
    var SIDES = 6, perCone = SIDES * 3, geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(cap * perCone * 3), 3));
    geo.setAttribute('color', new THREE.BufferAttribute(new Float32Array(cap * perCone * 3), 3));
    geo.setDrawRange(0, 0);
    var mat = new THREE.MeshBasicMaterial({vertexColors: true, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, side: THREE.DoubleSide});
    var obj = new THREE.Mesh(geo, mat); obj.frustumCulled = false; obj.renderOrder = 19;
    var c = new THREE.Color(), base = new THREE.Vector3(), tip = new THREE.Vector3(), u = new THREE.Vector3(), w = new THREE.Vector3(), q = new THREE.Vector3();
    return {obj: obj, n: 0, cap: cap,
      begin: function () { this.n = 0; },
      // nozzle position, backward unit vector, radius, length, colour
      add: function (pos, back, r, len, hex) {
        if (this.n >= this.cap || len <= 0.01) return;
        var P = geo.attributes.position.array, Cc = geo.attributes.color.array, o = this.n * perCone * 3;
        base.set(pos[0], pos[1], pos[2]); tip.set(back[0], back[1], back[2]).multiplyScalar(len).add(base);
        u.set(back[0], back[1], back[2]); w.set(0, 1, 0); if (Math.abs(u.dot(w)) > 0.9) w.set(1, 0, 0);
        q.crossVectors(u, w).normalize(); w.crossVectors(q, u).normalize();
        c.setStyle(hex);
        for (var i = 0; i < SIDES; i++) {
          var a0 = i / SIDES * TAU, a1 = (i + 1) / SIDES * TAU;
          var p0x = base.x + (q.x * Math.cos(a0) + w.x * Math.sin(a0)) * r, p0y = base.y + (q.y * Math.cos(a0) + w.y * Math.sin(a0)) * r, p0z = base.z + (q.z * Math.cos(a0) + w.z * Math.sin(a0)) * r;
          var p1x = base.x + (q.x * Math.cos(a1) + w.x * Math.sin(a1)) * r, p1y = base.y + (q.y * Math.cos(a1) + w.y * Math.sin(a1)) * r, p1z = base.z + (q.z * Math.cos(a1) + w.z * Math.sin(a1)) * r;
          var k = o + i * 9;
          P[k] = p0x; P[k + 1] = p0y; P[k + 2] = p0z; P[k + 3] = p1x; P[k + 4] = p1y; P[k + 5] = p1z; P[k + 6] = tip.x; P[k + 7] = tip.y; P[k + 8] = tip.z;
          Cc[k] = 0.18 + c.r * 0.82; Cc[k + 1] = 0.18 + c.g * 0.82; Cc[k + 2] = 0.18 + c.b * 0.82;   // hot at the nozzle, the colour's own
          Cc[k + 3] = Cc[k]; Cc[k + 4] = Cc[k + 1]; Cc[k + 5] = Cc[k + 2];
          Cc[k + 6] = 0; Cc[k + 7] = 0; Cc[k + 8] = 0;
        }
        this.n++;
      },
      end: function () { geo.attributes.position.needsUpdate = true; geo.attributes.color.needsUpdate = true; geo.setDrawRange(0, this.n * perCone); }};
  }

  // ---------------------------------------------------------------- geometry cache per entry
  function buildModel(name, lod) {
    var rec = S.models[name];
    if (!rec) return null;
    var key = name + ':' + (lod || 0);
    if (S.built[key]) return S.built[key];
    var b = HK.flightBuilt(HK.build(rec, {lod: lod || 0, models: S.models}));
    b.geo = HK.toThree(S.THREE, b, {atlas: S.kit, gradK: C.gradK(S.look)});
    S.built[key] = b;
    return b;
  }
  function modelAudit(b) {
    var cm = HK.colourMasses(b);
    return {tris: b.stats.tris, bevels: b.stats.bevels, atlasPanels: b.stats.atlasPanels, panelPx: (b.stats.panels || []).map(function (p) {
      return S.kit && S.kit.panels[p] ? S.kit.panels[p].px : 0; }), parts: b.stats.parts.slice(), colourMasses: cm.count, span: b.stats.span, length: b.stats.length};
  }
  // a mesh of a built hull with its own colours (an agent's livery), sharing the hull material
  function meshOf(b, colours, letter) {
    var geo = (colours || letter) ? HK.toThree(S.THREE, b, {atlas: S.kit, colours: colours, letter: letter, gradK: C.gradK(S.look)}) : b.geo;
    var m = new S.THREE.Mesh(geo, S.mat);
    m.userData.ownGeo = geo !== b.geo;
    m.onBeforeRender = function () { S.mat.uniforms.uThrust.value = this.userData.thrust ? 1 : 0; S.mat.uniformsNeedUpdate = true; };
    return m;
  }
  // the whole-mesh look (a skin may carry meshes kept whole, its meshes3d block, with the look's name): flat faces in a
  // two-colour checker dither, lit faces stepped on a 10-colour ramp by a light fixed to the camera, animated faces
  // cycling at 8 Hz
  var SN_VERT = ['attribute vec3 aColA; attribute vec3 aColB; varying vec3 vA; varying vec3 vB;',
    'void main(){ vA = aColA; vB = aColB; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }'].join('\n');
  var SN_FRAG = ['varying vec3 vA; varying vec3 vB;',
    'void main(){ float k = mod(floor(gl_FragCoord.x) + floor(gl_FragCoord.y), 2.0); gl_FragColor = vec4(mix(vA, vB, k), 1.0);',
    '  #include <colorspace_fragment>', '}'].join('\n');
  function wholeMesh(name) {
    var sn = S.whole, m = sn && sn.meshes && sn.meshes[name];
    if (!m) return null;
    var THREE = S.THREE, pal = sn.palette16.map(function (h) { return col(THREE, h); });
    var faces = [];
    m.faces.forEach(function (f) { for (var i = 1; i + 1 < f.v.length; i++) faces.push({v: [f.v[0], f.v[i], f.v[i + 1]], f: f}); });
    var n = faces.length, pos = new Float32Array(n * 9), A = new Float32Array(n * 9), B = new Float32Array(n * 9);
    faces.forEach(function (t, k) { for (var j = 0; j < 3; j++) { var p = m.v[t.v[j]]; pos[k * 9 + j * 3] = p[0]; pos[k * 9 + j * 3 + 1] = p[1]; pos[k * 9 + j * 3 + 2] = p[2]; } });
    var geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    geo.setAttribute('aColA', new THREE.BufferAttribute(A, 3));
    geo.setAttribute('aColB', new THREE.BufferAttribute(B, 3));
    geo.computeBoundingSphere();
    var mat = new THREE.ShaderMaterial({vertexShader: SN_VERT, fragmentShader: SN_FRAG, side: THREE.DoubleSide});
    var mesh = new THREE.Mesh(geo, mat);
    var anim = faces.filter(function (t) { return t.f.kind === 'anim'; }).length;
    mesh.userData.whole = {faces: faces, pal: pal, name: name, anim: anim, scale: m.scale || 1};
    if (m.scale) mesh.scale.setScalar(m.scale);
    paintWhole(mesh, 0);
    return mesh;
  }
  var nTmp = null, mTmp = null;
  function paintWhole(mesh, t) {
    var u = mesh.userData.whole, geo = mesh.geometry, A = geo.attributes.aColA.array, B = geo.attributes.aColB.array, THREE = S.THREE;
    if (!nTmp) { nTmp = new THREE.Vector3(); mTmp = new THREE.Matrix3(); }
    var cam = S.camera;
    if (cam) { mesh.modelViewMatrix.multiplyMatrices(cam.matrixWorldInverse, mesh.matrixWorld); mTmp.getNormalMatrix(mesh.modelViewMatrix); }
    var frame = 0;   // asset palette frames have no live activity telemetry
    u.faces.forEach(function (tf, k) {
      var f = tf.f, pair;
      if (f.kind === 'anim') { var hx = f.frames[frame]; pair = [hx, hx]; }
      else if (f.kind === 'lit') {
        nTmp.set(f.n[0], f.n[1], f.n[2]); if (cam) nTmp.applyMatrix3(mTmp).normalize();
        var step = C.rampStep([nTmp.x, nTmp.y, nTmp.z]);
        pair = f.pairs[step];
      } else pair = f.pair;
      var a = typeof pair[0] === 'number' ? u.pal[pair[0]] : col(THREE, pair[0]), b = typeof pair[1] === 'number' ? u.pal[pair[1]] : col(THREE, pair[1]);
      for (var j = 0; j < 3; j++) { A[k * 9 + j * 3] = a.r; A[k * 9 + j * 3 + 1] = a.g; A[k * 9 + j * 3 + 2] = a.b; B[k * 9 + j * 3] = b.r; B[k * 9 + j * 3 + 1] = b.g; B[k * 9 + j * 3 + 2] = b.b; }
    });
    geo.attributes.aColA.needsUpdate = true; geo.attributes.aColB.needsUpdate = true;
  }

  // whole meshes by role, made as needed and hidden when a frame does not use them (each carries its own colours: they
  // depend on how it faces the camera)
  function poolBegin() { Object.keys(S.pools).forEach(function (k) { S.pools[k].used = 0; }); }
  function poolTake(name) {
    var P = S.pools[name] || (S.pools[name] = {list: [], used: 0});
    if (P.used >= P.list.length) { var m = wholeMesh(name); if (!m) return null; S.root.add(m); P.list.push(m); }
    var mesh = P.list[P.used++]; mesh.visible = true; return mesh;
  }
  function poolEnd(t) {
    Object.keys(S.pools).forEach(function (k) {
      var P = S.pools[k];
      P.list.forEach(function (m, i) { m.visible = i < P.used; if (m.visible) { m.updateMatrixWorld(true); paintWhole(m, t); } });
    });
  }

  // ---------------------------------------------------------------- the module
  function yawOf(h) { return -(h || 0) - Math.PI / 2; }
  function fwdOf(h) { return [Math.cos(h || 0), 0, Math.sin(h || 0)]; }
  function hdgTo(a, b) { return Math.atan2(b[2] - a[2], b[0] - a[0]); }
  function planetOf(world, uid) { return (world.planets || []).find(function (p) { return p.uid === uid; }) || null; }

  // the look the owner picked: ctx.look3d (a test or a caller), else the page's setting 'look3d' (the "?" > Settings row
  // "3D ship look", stored as cm3d.look3d), else the skin's default; a look the skin does not carry falls back to the kit
  C.lookOf = function (ctx, sk) {
    var v = ctx && ctx.look3d, st = ctx && ctx.page && ctx.page.settings;
    if (!v && st) { try { v = typeof st.get === 'function' ? st.get('look3d', '') : st.look3d; } catch (e) { v = ''; } }
    return String(v || (sk && sk.look3d && sk.look3d.default) || 'kit');
  };
  // the audit's role names in the order writes them (the bundle's JSON has its keys sorted)
  var ROLE_ORDER = ['own_near', 'drone', 'buoy', 'flagship', 'city'];
  C.rolesInOrder = function (roles) {
    var out = {};
    ROLE_ORDER.forEach(function (k) { if (roles && roles[k] != null) out[k] = roles[k]; });
    Object.keys(roles || {}).forEach(function (k) { if (!(k in out)) out[k] = roles[k]; });
    return out;
  };

  // the baked owner's ship (the skin's ship3d): one mesh per material in glTF axes (y up, nose
  // -z), scaled to the hull kit's span so every camera frames it as it framed the kit's; its engines are [x, y, z, r] at
  // that scale. Its textures load on their own (data URIs); until then the hull shows its plain colours.
  function b64Array(s, T) {
    var bin = atob(s), u = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i);
    return new T(u.buffer);
  }
  function bakedShip(THREE, d, span, look) {
    if (!d || !d.parts || !d.parts.length || !(d.span > 0) || typeof atob !== 'function') return null;
    var k = (span || 14) / d.span, obj = new THREE.Group(), textures = [], mats = {}, tris = 0;
    var loader = THREE.TextureLoader ? new THREE.TextureLoader() : null;
    var white = new THREE.DataTexture(new Uint8Array([255, 255, 255, 255]), 1, 1); white.needsUpdate = true; textures.push(white);
    function tex(uri) {
      if (!uri || !loader) return null;
      var t = loader.load(uri); t.flipY = false; t.anisotropy = 4; t.colorSpace = THREE.SRGBColorSpace;
      t.wrapS = t.wrapT = THREE.RepeatWrapping;   // glTF's default sampler repeats (the dark metal panels tile)
      textures.push(t); return t;
    }
    // each material is the map's own hull shader (HK.material: the sun key, toon bands, the sky-tinted rim, emissives)
    // with the ship's painted texture where the hull kit has its decal atlas, so it is lit as every other craft is
    Object.keys(d.mats || {}).forEach(function (n) {
      var m = d.mats[n];
      mats[n] = {mat: HK.material(THREE, tex(m.map) || white, look), color: m.color || [1, 1, 1], emit: m.emit ? 1 : 0};
    });
    d.parts.forEach(function (pt) {
      var M = mats[pt.mat] || {mat: HK.material(THREE, white, look), color: [0.8, 0.8, 0.8], emit: 0};
      var pos = b64Array(pt.pos, Float32Array), n = pos.length / 3, col = new Float32Array(n * 3), emit = new Float32Array(n);
      for (var i = 0; i < n; i++) { col[i * 3] = M.color[0]; col[i * 3 + 1] = M.color[1]; col[i * 3 + 2] = M.color[2]; emit[i] = M.emit; }
      var geo = new THREE.BufferGeometry();
      geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
      geo.setAttribute('normal', new THREE.BufferAttribute(b64Array(pt.nrm, Float32Array), 3));
      geo.setAttribute('uv', new THREE.BufferAttribute(pt.uv ? b64Array(pt.uv, Float32Array) : new Float32Array(n * 2), 2));
      geo.setAttribute('color', new THREE.BufferAttribute(col, 3));
      geo.setAttribute('aEmit', new THREE.BufferAttribute(emit, 1));
      geo.setAttribute('aNs', new THREE.BufferAttribute(new Float32Array(n * 3), 3));
      geo.setAttribute('aThrust', new THREE.BufferAttribute(new Float32Array(n), 1));
      geo.setIndex(new THREE.BufferAttribute(b64Array(pt.idx, Uint16Array), 1));
      geo.computeBoundingSphere();
      var mesh = new THREE.Mesh(geo, M.mat);
      // the shared hull material's sun, rim tint, flash and glow carry over each frame (the craft sets them on S.mat).
      // While the core's shader warm-up has the mesh in its stand-in (userData.cmReal holds this material), the real
      // material takes them, so it is current the frame it comes back
      mesh.onBeforeRender = function () {
        var own = (this.userData && this.userData.cmReal) || this.material;
        if (!S || !S.mat || !own || !own.uniforms || !own.uniforms.uSunPos) return;
        var a = S.mat.uniforms, b = own.uniforms;
        ['uSunPos', 'uSunCol', 'uAmb', 'uRim'].forEach(function (u) { b[u].value.copy(a[u].value); });
        ['uRimPow', 'uRimK', 'uFill', 'uHot', 'uFlash', 'uGlow', 'uSpec', 'uShine'].forEach(function (u) { b[u].value = a[u].value; });
        own.uniformsNeedUpdate = true;
      };
      mesh.name = 'baked-' + pt.mat; obj.add(mesh); tris += pt.tris || 0;
    });
    obj.scale.setScalar(k); obj.name = 'baked-ship';
    var engines = (d.engines || []).map(function (e) { return [e[0] * k, e[1] * k, e[2] * k, Math.max(e[3] * k, 0.75)]; });
    return {obj: obj, textures: textures, engines: engines, tris: tris, parts: d.parts.length, source: d.source || ''};
  }

  function build(scene, world, ctx) {
    if (!ctx || !ctx.THREE) return;
    var THREE = ctx.THREE, sk = (ctx.assets && ctx.assets.skin3d) || HK.resolve(ctx.skin3d || {}, CM3D.neutral3d || {});
    S = {THREE: THREE, scene: scene, root: new THREE.Group(), models: sk.models3d || {}, look: sk.look3d || {}, feel: sk.feel3d || {},
         whole: sk.meshes3d || null, wholeLook: (sk.meshes3d && sk.meshes3d.look) || null, built: {}, agents: {}, rev: -1, T: (world && world.t) || 0, camera: ctx.camera, first: true,
         lookMode: C.lookOf(ctx, sk), flashT: 0, wasBoost: false, travel: null, wf: null, trails: {}, sk: sk, lookT: 0};
    S.root.name = 'craft';
    S.inv = new THREE.Matrix4(); S.tq = new THREE.Quaternion(); S.tv = new THREE.Vector3();
    scene.add(S.root);
    S.kit = (ctx.assets && ctx.assets.kit) || HK.atlas(THREE, sk.atlas3d || {});
    S.mat = HK.material(THREE, S.kit.texture, S.look);
    if (S.lookMode !== 'kit' && S.lookMode !== S.wholeLook) S.lookMode = 'kit';
    // the craft every scene has: the owner's ship and the carrier
    var own = buildModel('own', 0), wing = buildModel('wingman', 0), carrier = buildModel('carrier', 0);
    S.own = {obj: new THREE.Group(), built: own, mesh: own ? meshOf(own) : null, wholeFar: null, wholeNear: null};
    if (S.own.mesh) S.own.obj.add(S.own.mesh);
    if (S.lookMode === S.wholeLook) {
      var roles = S.whole.roles || {};
      S.own.wholeFar = wholeMesh(S.whole.own || 'own');
      S.own.wholeNear = wholeMesh(roles.own_near);
      [S.own.wholeFar, S.own.wholeNear].forEach(function (m) { if (m) { m.visible = false; S.own.obj.add(m); } });
      if (S.own.wholeFar && S.own.mesh) S.own.mesh.visible = false;
    }
    // the kit look flies the skin's baked ship when it has one (the hull kit's stays built for its anchors and audit)
    var baked = S.lookMode === 'kit' && sk.ship3d ? bakedShip(THREE, sk.ship3d, own && own.stats && own.stats.span, S.look) : null;
    if (baked) { S.own.baked = baked; S.own.obj.add(baked.obj); if (S.own.mesh) S.own.mesh.visible = false; }
    S.root.add(S.own.obj);
    S.carrier = {obj: new THREE.Group(), built: carrier, mesh: carrier ? meshOf(carrier) : null};
    if (S.carrier.mesh) S.carrier.obj.add(S.carrier.mesh);
    S.root.add(S.carrier.obj);
    // problems: one instanced mesh per kind (the kinds this skin carries; rocks from the MIT set)
    S.foeKinds = {};
    ['raider', 'drone', 'derelict'].forEach(function (k) {
      var b = buildModel(k, 0);
      if (!b) return;
      var im = new THREE.InstancedMesh(b.geo, S.mat, 48); im.count = 0; im.frustumCulled = false;
      S.root.add(im); S.foeKinds[k] = {im: im, built: b};
    });
    S.pools = {};
    var flag = buildModel('flagship', 0);
    S.flag = flag ? {built: flag, mesh: meshOf(flag)} : null;
    if (S.flag) { S.flag.mesh.visible = false; S.root.add(S.flag.mesh); }
    S.rocks = rocksFor(ctx);
    if (S.rocks) {
      S.rockIm = {};
      Object.keys(S.rocks.models).forEach(function (rk) {
        S.rocks.models[rk].geos.forEach(function (geo, lv) {
          var im = new THREE.InstancedMesh(geo, S.mat, 32); im.count = 0; im.frustumCulled = false;
          S.root.add(im); S.rockIm[rk + ':' + lv] = im;
        });
      });
    }
    var beacon = buildModel('beacon', 0);
    if (beacon) { S.beaconIm = new THREE.InstancedMesh(beacon.geo, S.mat, 32); S.beaconIm.count = 0; S.beaconIm.frustumCulled = false; S.root.add(S.beaconIm); S.beaconBuilt = beacon; }
    S.glow = makeGlow(THREE, 640); S.root.add(S.glow.obj);
    S.flames = makeFlames(THREE, 64); S.root.add(S.flames.obj);
    S.drops = makeLines(THREE, 96, false, 0.55); S.root.add(S.drops.obj);
    S.trailLines = makeLines(THREE, 640, true, 0.9); S.root.add(S.trailLines.obj);
    S.shadows = makeShadows(THREE, 96); S.root.add(S.shadows.obj);
    // audit: the models this skin built (models.*)
    var A = ctx.audit || {};
    var M = A.models || (A.models = {});
    ['ownNear', 'flagship'].forEach(function (k) { delete M[k]; });   // a look switched in flight leaves none of the last look's
    if (own) M.own = modelAudit(own);
    if (wing) M.wingman = modelAudit(wing);
    if (carrier) M.carrier = modelAudit(carrier);
    // the whole-mesh look: models.own is the own ship's mesh (its triangles, its animated faces); the mesh on screen
    // (the near one inside 400 u of a chase camera) is models.own.shownMesh / shownTris, the near mesh models.ownNear
    if (S.lookMode === S.wholeLook && S.own.wholeFar) {
      M.own.tris = S.own.wholeFar.geometry.attributes.position.count / 3;
      M.own.animFaces = S.own.wholeFar.userData.whole.anim;
      M.own.look = S.wholeLook;
      M.own.mesh = S.own.wholeFar.userData.whole.name;
      if (S.own.wholeNear) M.ownNear = {mesh: S.own.wholeNear.userData.whole.name, tris: S.own.wholeNear.geometry.attributes.position.count / 3};
    }
    M.own = M.own || {};
    if (S.own.baked) M.own.baked = {tris: S.own.baked.tris, parts: S.own.baked.parts, source: S.own.baked.source};
    M.own.rim = !!(S.mat && S.mat.userData.rim);
    M.foes = {};
    Object.keys(S.foeKinds).forEach(function (k) { M[k] = modelAudit(S.foeKinds[k].built); });
    if (S.rocks) M.rock = {tris: S.rocks.models[Object.keys(S.rocks.models)[0]].faces[0], lods: S.rocks.lods.slice(), kinds: Object.keys(S.rocks.models).sort()};
    if (beacon) M.beacon = modelAudit(beacon);
    if (S.whole && S.whole.roles) {
      M.pixelRoles = C.rolesInOrder(S.whole.roles);   // the craft roles in audit order
      M.wholeMeshes = {};
      Object.keys(S.whole.meshes || {}).forEach(function (k) { var m = S.whole.meshes[k]; M.wholeMeshes[k] = {tris: m.tris, cap: m.cap || 0}; });
    }
    if (S.flag) M.flagship = modelAudit(S.flag.built);
    if (S.lookMode === S.wholeLook && S.whole.roles && S.whole.meshes[S.whole.roles.flagship]) {
      var fm = S.whole.meshes[S.whole.roles.flagship];
      M.flagship = {tris: fm.tris, cap: fm.cap || 0, mesh: S.whole.roles.flagship, look: S.wholeLook};
    }
    var cr = A.craft || (A.craft = {});
    // the problem silhouettes this skin carries (craft.foeKindsShown: how many of them are on screen now)
    cr.foeSilhouettes = Object.keys(S.foeKinds).length + (S.rocks ? 1 : 0);
    sync(world, ctx);
    S.first = false;
  }

  // the generated rocks (world3d/rocks.js): geometries per kind and level, shared with the space module through ctx.assets
  function rocksFor(ctx) {
    var src = CM3D.rocks, THREE = ctx.THREE;
    if (!src || !src.models) return null;
    var out = {lods: src.lods || [476, 160, 60], models: {}};
    Object.keys(src.models).forEach(function (k) {
      var r = src.models[k], pal = r.palette_rgb, geos = [], faces = [];
      (r.lods || [{faces: r.faces, face_palette_index: r.face_palette_index}]).forEach(function (L) {
        var V = r.vertices, F = L.faces, n = F.length, pos = new Float32Array(n * 9), nrm = new Float32Array(n * 9), cl = new Float32Array(n * 9), uv = new Float32Array(n * 6), em = new Float32Array(n * 3);
        var c = new THREE.Color();
        F.forEach(function (f, i) {
          var a = V[f[0]], b = V[f[1]], d = V[f[2]], nn = HK.triNormal([a[0], a[1], a[2], b[0], b[1], b[2], d[0], d[1], d[2]]);
          var p = pal[L.face_palette_index[i]] || [128, 128, 128];
          c.setRGB(p[0] / 255, p[1] / 255, p[2] / 255, THREE.SRGBColorSpace);
          [a, b, d].forEach(function (q, j) {
            pos.set(q, i * 9 + j * 3); nrm[i * 9 + j * 3] = nn[0]; nrm[i * 9 + j * 3 + 1] = nn[1]; nrm[i * 9 + j * 3 + 2] = nn[2];
            cl[i * 9 + j * 3] = c.r; cl[i * 9 + j * 3 + 1] = c.g; cl[i * 9 + j * 3 + 2] = c.b;
            uv[i * 6 + j * 2] = S.kit.white[0]; uv[i * 6 + j * 2 + 1] = S.kit.white[1];
          });
        });
        var geo = new THREE.BufferGeometry();
        geo.setAttribute('position', new THREE.BufferAttribute(pos, 3)); geo.setAttribute('normal', new THREE.BufferAttribute(nrm, 3));
        geo.setAttribute('color', new THREE.BufferAttribute(cl, 3)); geo.setAttribute('uv', new THREE.BufferAttribute(uv, 2));
        geo.setAttribute('aEmit', new THREE.BufferAttribute(em, 1)); geo.setAttribute('aNs', new THREE.BufferAttribute(HK.creaseNormals(pos, nrm), 3));
        geo.computeBoundingSphere();
        geos.push(geo); faces.push(n);
      });
      out.models[k] = {geos: geos, faces: faces};
    });
    ctx.assets = ctx.assets || {};
    ctx.assets.rocks = out;
    return out;
  }

  // the world's data changed (or the first build): agents in and out (a new one warps in from the carrier), the
  // followed workflow, problems and waiting items per planet
  function sync(world, ctx) {
    S.rev = world.rev;
    S.world = world;
    var THREE = S.THREE, agents = (world.craft || []).filter(function (c) { return c && c.role !== 'carrier'; });
    var carrierCraft = (world.craft || []).find(function (c) { return c && c.role === 'carrier'; });
    var star = world.star || {x: 0, y: 0, r: 150};
    S.dock = carrierCraft ? [carrierCraft.x, 0, carrierCraft.y] : [star.x + star.r * 1.9, 0, star.y - star.r * 1.35];
    // the followed workflow and its agent (by.seat names a craft; else one craft stands for the workflow itself)
    var wf = null, sel = world.selectedWorkflow;
    if (sel) wf = (world.workflows || []).find(function (w) { return w && (w.id === sel || w === sel); }) || (typeof sel === 'object' ? sel : null);
    S.wf = wf;
    var wfAgent = null;
    if (wf) {
      var seat = wf.by && wf.by.seat;
      wfAgent = seat ? agents.find(function (a) { return a.uid === seat || a.label === seat || String(a.uid).indexOf(':' + seat) >= 0; }) : null;
      if (!wfAgent) {
        wfAgent = {uid: 'wf:' + wf.id, role: 'agent', label: String((wf.by && wf.by.name) || 'agent'), letter: String((wf.by && wf.by.name) || 'A').charAt(0),
                   colour: '#6FB7FF', state: wf.state === 'working' ? 'working' : 'idle', planet: null, x: S.dock[0], y: S.dock[2], hdg: 0, virtual: true};
        agents = agents.concat([wfAgent]);
      }
    }
    S.wfAgent = wfAgent;
    var lv = C.liveries(agents), seen = {};
    var M = (ctx.audit && ctx.audit.models) || {};
    M.agents = {};
    agents.forEach(function (a) {
      seen[a.uid] = 1;
      var rec = S.agents[a.uid];
      if (!rec) {
        var b = buildModel('wingman', 0) || buildModel('own', 1);
        var L = lv[a.uid];
        var colours = {livery: L.colour};
        var mesh = meshOf(b, colours, L.letter);
        var obj = new THREE.Group(); obj.add(mesh); S.root.add(obj);
        var p0 = [a.x || S.dock[0], 0, a.y || S.dock[2]];
        rec = S.agents[a.uid] = {uid: a.uid, obj: obj, mesh: mesh, built: b, v: {pos: p0.slice(), vel: [0, 0, 0], maxSpeed: 300, mass: 0.08},
                                 hdg: a.hdg || 0, warp: (!S.first && !a.virtual) ? 0.0001 : 0, livery: L, orbitA: null, at: null, snap: S.first};
        if (rec.warp) rec.v.pos = S.dock.slice();
      }
      rec.data = a;
      rec.livery = lv[a.uid];
      M.agents[a.uid] = {livery: rec.livery.livery, colour: rec.livery.colour, letter: rec.livery.letter, tris: rec.built.stats.tris};
    });
    Object.keys(S.agents).forEach(function (uid) {
      if (seen[uid]) return;
      var r = S.agents[uid]; S.root.remove(r.obj); if (r.mesh.userData.ownGeo) r.mesh.geometry.dispose(); delete S.agents[uid];
    });
    // formation: the followed workflow's agents hold V slots (at most three)
    S.formation = [];
    if (wf && wfAgent) S.formation.push(wfAgent.uid);
    // the step the workflow's agent works at: the working one, else the next one not done
    S.wfTarget = null;
    if (wf && Array.isArray(wf.steps) && wf.state !== 'planned' && wf.state !== 'done') {   // a planned or finished one: the V
      var steps = wf.steps, cur = Array.isArray(wf.current) ? wf.current : [];
      var st = steps.find(function (x) { return cur.indexOf(x.key) >= 0 && x.state !== 'done'; }) ||
               steps.find(function (x) { return x.state === 'working' || x.state === 'needs_you' || x.state === 'failed'; });
      if (!st) st = steps.find(function (x) { return x.state !== 'done' && x.state !== 'skipped'; });
      if (st && st.planet_uid) S.wfTarget = planetOf(world, st.planet_uid);
    }
    // the flagship: the followed workflow if it has six steps or more, else the first running one that has
    S.flagshipAt = null;
    var big = (wf && Array.isArray(wf.steps) && wf.steps.length >= 6) ? wf : (world.workflows || []).find(function (w) {
      return w && Array.isArray(w.steps) && w.steps.length >= 6 && w.state !== 'done' && w.state !== 'planned'; });
    if (big) {
      var bs = big.steps.find(function (x) { return x.state === 'working' || x.state === 'needs_you'; }) || big.steps.find(function (x) { return x.state !== 'done'; }) || big.steps[0];
      S.flagshipAt = planetOf(world, bs && bs.planet_uid) || (world.planets || [])[0] || null;
    }
    // problems and waiting items, grouped by planet
    S.foes = []; var per = {};
    (world.foes || []).forEach(function (f) { if (f && f.parent) (per[f.parent] = per[f.parent] || []).push(f); });
    Object.keys(per).forEach(function (pid) {
      var p = planetOf(world, pid); if (!p) return;
      var list = per[pid], n = Math.min(8, list.length);
      list.slice(0, n).forEach(function (f, i) { var cl = C.foeClass(f); S.foes.push({f: f, p: p, i: i, n: n, cls: cl, tells: C.tellCount(f), leader: i === 0 && list.length >= 5, count: list.length}); });
    });
    S.waiting = (world.waiting || []).map(function (w, i) { return {w: w, p: planetOf(world, w.parent || w.planet) , i: i}; });
    var cr = ctx.audit.craft;
    cr.leaders = S.foes.filter(function (x) { return x.leader; }).map(function (x) { return {planet: x.p.uid, count: x.count}; });
    M.foes = {};
    S.foes.forEach(function (x) { M.foes[x.f.uid] = {kind: x.cls.model, tell: x.cls.tell, tells: x.tells}; });
  }

  function update(world, dt, ctx) {
    if (!S || !ctx) return;
    // the owner switched the 3D ship look in Settings: the craft are built again in it (checked twice a second)
    S.lookT += Math.max(0, ctx.dt != null ? ctx.dt : (dt || 0));
    if (S.lookT >= 0.5) {
      S.lookT = 0;
      var want = C.lookOf(ctx, S.sk);
      if (want !== 'kit' && want !== S.wholeLook) want = 'kit';
      if (want !== S.lookMode) { var sc = S.scene, w0 = S.world; dispose(); build(sc, w0, ctx); if (!S) return; }
    }
    var THREE = S.THREE;
    S.camera = ctx.camera || S.camera;
    if (world && world.rev !== S.rev) sync(world, ctx);
    var liveShip = (world && world.ship) || (S.world && S.world.ship) || {};   // the core passes this frame's world: the ship moves every frame
    world = S.world;
    var sdt = ctx.sceneDt != null ? ctx.sceneDt : dt;
    S.T += sdt * 1000;
    var t = S.T, reduced = !!world.reduced, cam = S.camera, look = S.look, feel = S.feel;
    var H = (ctx.renderer && ctx.renderer.domElement && ctx.renderer.domElement.clientHeight) || 900;
    var fov = (cam && cam.fov) || 55, dpr = (ctx.renderer && ctx.renderer.getPixelRatio && ctx.renderer.getPixelRatio()) || 1;
    S.glow.obj.material.uniforms.uScale.value = (H * dpr) / (2 * Math.tan(fov * Math.PI / 360));
    S.glow.obj.material.uniforms.uDpr.value = dpr;
    // the light: the star; the rim: the region's sky (ctx.sky, written by the space module), else the skin's own
    var star = world.star || {x: 0, y: 0, r: 150};
    S.mat.uniforms.uSunPos.value.set(star.x, Math.max(star.r, 400), star.y);
    var tint = (ctx.sky && ctx.sky.tint) || look.rim || '#8FB4FF';
    if (tint !== S.tint) {
      var rimU = S.mat.uniforms.uRim.value; rimU.setStyle(typeof tint === 'string' ? tint : '#8FB4FF'); S.tint = tint;
      // a skin's look3d.skyRim (0..1) is how much of the region's sky colours the hull: the rest is the skin's own rim, so a
      // ship reads the same over a magenta nebula as over a gold one (no key: the sky's colour whole)
      if (look.skyRim != null && look.skyRim < 1) {
        if (!S.rimBase) S.rimBase = new THREE.Color();
        S.rimBase.setStyle(look.rim || '#8FB4FF'); rimU.lerpColors(S.rimBase, rimU, Math.max(0, look.skyRim));
      }
    }
    S.glow.begin(); S.flames.begin(); S.drops.begin(); S.shadows.begin(); S.trailLines.begin();
    var cr = ctx.audit.craft, camPos = cam ? cam.position : null;
    var beacons = 0, beaconPx = 0, warpIn = 0, flameWork = 0, flameIdle = 0, idleRate = 0, flameShown = false, stripBudget = 160;
    function dist(p) { return camPos ? Math.hypot(p[0] - camPos.x, p[1] - camPos.y, p[2] - camPos.z) : 1e9; }
    // a ring beacon (agent colour ring, white core) where a craft is under 12 px; counted, and its size reported, only
    // when it is on the screen
    function ring(pos, hex) {
      S.glow.add(pos[0], pos[1], pos[2], hex, C.BEACON_PX, 1, true);
      var pr = ctx.project ? ctx.project(pos[0], pos[1], pos[2]) : null;
      if (!pr || pr.onScreen) { beacons++; beaconPx = Math.max(beaconPx, C.ringPx(C.BEACON_PX)); }
    }
    function offPlane(pos, hex, size) {
      if (Math.abs(pos[1]) <= 2) return 0;
      S.drops.seg(pos, [pos[0], 0, pos[2]], hex, hex, 0.9, 0.25);
      S.shadows.add(pos[0], pos[2], size * (1 + Math.abs(pos[1]) / 220), C.shadowAlpha(pos[1]));
      return 1;
    }
    // own ship
    // the ship: x, y and the heading are the 2D truth (the world's ship, this frame's); its attitude is the camera lane's
    // ctx.ship {alt, pitch in degrees, rollDeg} (core.js: alt null = the core's own altitude, which lifts the ship over
    // the bodies; R/F move ctx.ship.alt), so a craft off the plane gets its drop-line and shadow whoever lifted it
    var ship = liveShip, att = C.attitude(ctx, liveShip);
    var alt = att.alt;
    var sp = [ship.x || 0, alt, ship.y || 0], sh = ship.hdg || 0;
    var o = S.own.obj;
    o.position.set(sp[0], sp[1], sp[2]);
    o.rotation.set(0, 0, 0); o.rotateY(yawOf(sh)); o.rotateZ(-(ship.bank || 0) - att.roll); if (att.pitch) o.rotateX(att.pitch);
    var rig = ctx.rig || 'cockpit';
    var ownSize = (S.own.built && S.own.built.stats.span) || 14;
    var nearOwn = dist(sp) < 400;
    if (S.lookMode === S.wholeLook && S.own.wholeFar) {
      var near = rig === 'chase' && nearOwn && S.own.wholeNear;
      S.own.wholeFar.visible = !near; if (S.own.wholeNear) S.own.wholeNear.visible = !!near;
      var vis = near ? S.own.wholeNear : S.own.wholeFar;
      paintWhole(vis, t / 1000);
      ctx.audit.models.own.shownTris = vis.geometry.attributes.position.count / 3;
      ctx.audit.models.own.shownMesh = vis.userData.whole.name;
    }
    o.visible = rig !== 'cockpit';
    if (ship.boost && !S.wasBoost) S.flashT = 1;
    S.wasBoost = !!ship.boost;
    S.flashT = Math.max(0, S.flashT - sdt * 4);
    var flk = reduced ? 1 : 0.9 + 0.1 * (Math.floor(t / 33) % 2);   // engines flicker 0.9x every other tick
    var gk = C.glowScale(ship, feel, reduced ? 0 : S.flashT);
    cr.ownGlow = +gk.toFixed(3);
    var ownMoving = !!ship.boost || Math.abs(ship.spd || 0) > 1;
    if (S.own.mesh) S.own.mesh.userData.thrust = ownMoving;
    var ownGlowHex = (look.glow && look.glow.own) || '#4DE8E0';
    if (o.visible && S.own.built) {
      o.updateMatrixWorld(true);
      var back = fwdOf(sh).map(function (v) { return -v; });
      ((S.own.baked && S.own.baked.engines.length ? S.own.baked.engines : S.own.built.anchors.engines) || []).forEach(function (e) {
        var w = new THREE.Vector3(e[0], e[1], e[2]).applyMatrix4(o.matrixWorld);
        S.glow.add(w.x, w.y, w.z, ownGlowHex, (e[3] || 0.3) * 5.5 * gk * (ownMoving ? flk : 1), 0);
        if (ownMoving) S.flames.add([w.x, w.y, w.z], back, (e[3] || 0.3) * 0.9, (e[3] || 0.3) * 7 * gk * flk, ownGlowHex);
      });
      var px = C.projPx(ownSize, dist(sp), fov, H);
      if (px < 12) { o.visible = false; ring(sp, '#ffffff'); }
      else if (S.own.mesh && S.own.mesh.visible) stripBudget -= stripGlows(S.glow, S.own.built, o.matrixWorld, 1, THREE, stripBudget);   // the kit hull's strips (not a whole mesh's)
    }
    offPlane(sp, '#E8EEF6', ownSize);
    // carrier
    var co = S.carrier.obj, dock = S.dock;
    co.position.set(dock[0], 0, dock[2]); co.rotation.set(0, yawOf(Math.atan2(star.y - dock[2], star.x - dock[0]) + Math.PI / 2), 0);
    if (S.carrier.built) {
      co.updateMatrixWorld(true);
      var chex = (look.glow && look.glow.carrier) || '#FFD58A', cback = [0, 0, 0];
      var cb = new THREE.Vector3(0, 0, 1).transformDirection(co.matrixWorld); cback = [cb.x, cb.y, cb.z];
      (S.carrier.built.anchors.engines || []).forEach(function (e) {
        var w = new THREE.Vector3(e[0], e[1], e[2]).applyMatrix4(co.matrixWorld);
        S.glow.add(w.x, w.y, w.z, chex, e[3] * 4.5, 0);
        // The carrier is docked: no exhaust trail without a real flight.
      });
      var blink = 1;
      (S.carrier.built.anchors.lights || []).forEach(function (e) {
        var w = new THREE.Vector3(e[0], e[1], e[2]).applyMatrix4(co.matrixWorld);
        S.glow.add(w.x, w.y, w.z, (S.carrier.built.recipe.mats.tip || S.carrier.built.recipe.mats.lamp || {c: '#FFB547'}).c, 7 * blink, 2);
      });
    }
    // agents
    var wfA = S.wfAgent, slotK = 0, leader = {pos: sp, vel: fwdOf(sh).map(function (v) { return v * (ship.spd || 0); })};
    var dockK = 0, travel = null;
    Object.keys(S.agents).forEach(function (uid) {
      var r = S.agents[uid], a = r.data, v = r.v, target = null, mode = 'dock', hdgWant = null;
      var inForm = S.formation.indexOf(uid) >= 0;
      var workP = null;
      if (inForm && uid === (wfA && wfA.uid)) workP = S.wfTarget;
      else if (!inForm && /^work|running/.test(String(a.state || '')) && a.planet) workP = planetOf(world, a.planet);
      if (workP) {
        mode = 'work';
        var orR = workP.r * 1.35 + 12;
        if (r.at !== workP.uid) {
          // a new planet: plan the hop from here to the orbit point facing the agent
          var ang = Math.atan2(v.pos[2] - workP.y, v.pos[0] - workP.x);
          var entry = [workP.x + Math.cos(ang) * orR, 0, workP.y + Math.sin(ang) * orR];
          r.at = workP.uid; r.orbitA = ang; r.arrived = false;
          r.plan = C.travelPlan(v.pos.slice(), entry); r.plan.t = 0; r.plan.from = r.prevAt || null; r.plan.toUid = workP.uid; r.plan.r = workP.r;
        }
        if (r.snap) { r.arrived = true; r.snap = false; }
        if (!r.arrived && r.plan) {
          r.plan.t += sdt;
          var left = C.travelStep(v, r.plan, sdt);
          if (left < Math.max(1.5, 0.02 * workP.r) && !r.plan.doneAt) { r.plan.doneAt = r.plan.t; r.arrived = true; v.vel = [0, 0, 0]; }
          if (r.plan.t > 6) { r.arrived = true; v.pos = r.plan.to.slice(); }
          travel = r.plan; travel.pos = v.pos.slice();
          var vv = Math.hypot(v.vel[0], v.vel[2]); if (vv > 1) hdgWant = Math.atan2(v.vel[2], v.vel[0]);
        } else {
          // Work status holds position; only a new target starts another flight.
          v.vel = [0, 0, 0];
          v.pos = [workP.x + Math.cos(r.orbitA) * orR, 0, workP.y + Math.sin(r.orbitA) * orR];
          hdgWant = r.orbitA + Math.PI / 2;
        }
      } else if (inForm) {
        mode = 'form';
        if (r.at) { r.prevAt = r.at; r.at = null; }
        var chaseBack = rig === 'chase' && cam ? Math.max(30, dist(sp)) : ((feel.chase && feel.chase.cruise) || 50);
        var off = C.formationSlot(slotK++, ownSize, {aspect: (cam && cam.aspect) || 16 / 9, fov: fov, back: chaseBack});
        var cs = Math.cos(yawOf(sh)), sn = Math.sin(yawOf(sh));
        var wpos = [sp[0] + off[0] * cs + off[2] * sn, sp[1] + off[1], sp[2] - off[0] * sn + off[2] * cs];
        v.maxSpeed = Math.max(420, (ship.spd || 0) * 1.6);
        if (r.snap) { v.pos = wpos.slice(); v.vel = [0, 0, 0]; r.snap = false; }
        C.stepVehicle(v, C.offsetPursuit(v, leader, wpos, 0.25), sdt);
        hdgWant = sh;
      } else {
        // idle: docked along the carrier's side
        mode = 'dock';
        if (r.at) { r.prevAt = r.at; r.at = null; }
        var k = dockK++, cos = Math.cos(co.rotation.y), sin = Math.sin(co.rotation.y);
        var lx = (k % 2 ? -1 : 1) * 38, lz = -40 + Math.floor(k / 2) * 18;
        var dp = [dock[0] + lx * cos + lz * sin, 0, dock[2] - lx * sin + lz * cos];
        if (r.snap) { v.pos = dp.slice(); v.vel = [0, 0, 0]; r.snap = false; }
        v.maxSpeed = 500; C.stepVehicle(v, C.arrive(v, dp, 0.3), sdt);
        hdgWant = Math.atan2(star.y - dock[2], star.x - dock[0]) + Math.PI / 2;
      }
      if (hdgWant != null) { var dh = Math.atan2(Math.sin(hdgWant - r.hdg), Math.cos(hdgWant - r.hdg)); r.hdg += dh * Math.min(1, sdt * 6); }
      var ob = r.obj;
      ob.position.set(v.pos[0], v.pos[1], v.pos[2]);
      ob.rotation.set(0, yawOf(r.hdg), 0);
      // a wingman tilts its top toward the camera as far as the view is flat (C.showTilt), so its wings read as a planform
      // and not as a line on a low chase view (the 3D craft module check: the phone's wingman read as a line); level again off the V
      var tiltW = [0, 0, 0];
      if (mode === 'form' && camPos) {
        ob.updateMatrixWorld(true);
        var cl = camPos.clone().applyMatrix4(S.inv.copy(ob.matrixWorld).invert());
        tiltW = C.showTilt([cl.x, cl.y, cl.z]);
      }
      var tl = r.tilt || (r.tilt = [0, 0, 0]), ke = Math.min(1, sdt * 3);
      for (var ti = 0; ti < 3; ti++) tl[ti] += (tiltW[ti] - tl[ti]) * ke;
      var tang = Math.hypot(tl[0], tl[1], tl[2]);
      if (tang > 1e-4) ob.quaternion.multiply(S.tq.setFromAxisAngle(S.tv.set(tl[0] / tang, tl[1] / tang, tl[2] / tang), tang));
      r.tiltAng = tang;
      // warp-in from the carrier: stretched 1 -> 4 -> 1 along its nose over 0.5 s, with a burst
      if (r.warp > 0) {
        r.warp += sdt;
        var wt = Math.min(1, r.warp / 0.5);
        r.mesh.scale.set(1, 1, 1 + 3 * Math.sin(Math.PI * wt));
        if (wt < 0.6) S.glow.add(v.pos[0], v.pos[1], v.pos[2], '#CFE3FF', 22 * (1 - wt), 0);
        if (wt >= 1) { r.warp = 0; r.mesh.scale.set(1, 1, 1); } else warpIn++;
      }
      var span = r.built.stats.span, d = dist(v.pos), px = C.projPx(span, d, fov, H);
      var lcol = r.livery.colour;
      ob.visible = true;
      if (px < 12) { ob.visible = false; ring(v.pos, lcol); }
      // Engines burn while working or flying; parked craft keep only a steady lamp. Colour = its own or space blue /
      // planet red (a skin's glow.space / glow.planet)
      var moving = Math.hypot(v.vel[0], v.vel[1], v.vel[2]) > 1;
      var fk = mode === 'work' ? C.flameK('working') : moving ? 1 : 0;
      // the flame this agent burns this frame (its own engines, its state, the flicker): drawn when it is on the screen
      // as a mesh, and what the audit reports either way (a far agent's ring beacon carries no flame)
      if (r.mesh) r.mesh.userData.thrust = moving;
      var idleK = C.flameK('idle'), burn = 0;
      (r.built.anchors.engines || []).forEach(function (e) { burn = Math.max(burn, (e[3] || 0.3) * 6 * fk * flk); });
      var nearPlanet = (world.planets || []).some(function (p) { return Math.hypot(p.x - v.pos[0], p.y - v.pos[2]) < p.r * 2.2; });
      var ghex = look.glow && look.glow.space ? (nearPlanet ? look.glow.planet : look.glow.space) : lcol;
      if (ob.visible) {
        ob.updateMatrixWorld(true);
        var bk = new THREE.Vector3(0, 0, 1).transformDirection(ob.matrixWorld);
        (r.built.anchors.engines || []).forEach(function (e) {
          var w = new THREE.Vector3(e[0], e[1], e[2]).applyMatrix4(ob.matrixWorld);
          S.glow.add(w.x, w.y, w.z, ghex, (e[3] || 0.3) * 5 * (0.6 + 0.4 * fk) * (fk ? flk : 1), 0);
          if (fk) S.flames.add([w.x, w.y, w.z], [bk.x, bk.y, bk.z], (e[3] || 0.3) * 0.85, (e[3] || 0.3) * 6 * fk * flk, ghex);
        });
        var blink = 1, mats = r.built.recipe.mats || {};
        (r.built.anchors.lights || []).forEach(function (e, li) {
          var w = new THREE.Vector3(e[0], e[1], e[2]).applyMatrix4(ob.matrixWorld);
          S.glow.add(w.x, w.y, w.z, li === 0 ? '#FF4D5E' : '#3DFF9E', 1.5 * blink, 2);   // nav lamps: points of light, not balls
        });
        if (stripBudget > 0) stripBudget -= stripGlows(S.glow, r.built, ob.matrixWorld, 0.9, THREE, Math.min(16, stripBudget));
      }
      // the audit: the longest flame of a working agent and of an idle one, as burnt this frame (never a constant)
      r.flameLen = +burn.toFixed(3);
      if (mode === 'work') {
        flameWork = Math.max(flameWork, burn); if (ob.visible) flameShown = true;
        (r.built.anchors.engines || []).forEach(function (e) { idleRate = Math.max(idleRate, (e[3] || 0.3) * 6 * idleK * flk); });
      } else if (mode === 'dock' && fk === idleK) flameIdle = Math.max(flameIdle, burn);
      offPlane(v.pos, lcol, span);
      // trail: the last 1.2 s of a moving agent
      var tr = S.trails[uid] || (S.trails[uid] = []);
      var moving = Math.hypot(v.vel[0], v.vel[1], v.vel[2]) > 30;
      if (moving && !reduced) { tr.push([v.pos[0], v.pos[1], v.pos[2], t]); }
      while (tr.length && (t - tr[0][3] > 1200 || tr.length > 40)) tr.shift();
      for (var i = 1; i < tr.length; i++) {
        var a0 = (tr[i - 1][3] - (t - 1200)) / 1200, a1 = (tr[i][3] - (t - 1200)) / 1200;
        S.trailLines.seg(tr[i - 1], tr[i], lcol, lcol, 0.5 * Math.max(0, a0), 0.5 * Math.max(0, a1));
      }
    });
    poolBegin();
    // problems on the 2D orbit, tells glowing by kind
    var foeHex = (look.glow && look.glow.foe) || '#FF5A5F', tellHex = look.tells || {};
    var im = {}, mtx = new THREE.Matrix4(), q = new THREE.Quaternion(), sc = new THREE.Vector3(), pv = new THREE.Vector3(), eu = new THREE.Euler();
    Object.keys(S.foeKinds).forEach(function (k) { im[k] = 0; });
    var rockN = {}, nearRock = Infinity;
    var flick = reduced ? 1 : 0.75 + 0.25 * Math.sin(t / 90);
    var shownKinds = {}, tellPx = Infinity;
    S.foes.forEach(function (x) {
      var P = C.foePos(x.p, x.i, x.n, t, reduced), pos = [P.x, 0, P.y], hdg = P.a + Math.PI / 2;
      var scale = (x.leader ? 1.6 : 1) * C.FOE_SCALE;
      // each problem craft banks into its turn round the planet (the planet is on its right), so a camera level with
      // the map plane sees its top or belly, never a sliver edge-on
      eu.set(0, yawOf(hdg), -C.FOE_BANK, 'YXZ'); q.setFromEuler(eu); pv.set(pos[0], pos[1], pos[2]); sc.set(scale, scale, scale);
      shownKinds[x.cls.model] = 1;
      var kind = x.cls.model, tells = [], built = null;
      if (kind === 'rock' && S.rocks) {
        var rk = S.rocks.models.grey ? (x.i % 3 === 0 ? 'crater' : x.i % 3 === 1 ? 'grey' : 'orange') : Object.keys(S.rocks.models)[0];
        if (!S.rocks.models[rk]) rk = Object.keys(S.rocks.models)[0];
        var rs = 9 * (x.leader ? 1.6 : 1) * C.ROCK_SCALE, lv = C.rockLod(C.projPx(rs * 2, dist(pos), fov, H));   // rocks keep their size
        sc.set(rs, rs, rs); eu.set(x.i, 0, 0); q.setFromEuler(eu);
        mtx.compose(pv, q, sc);
        var key = rk + ':' + lv, ri = S.rockIm[key], rd = dist(pos);
        rockN[key] = rockN[key] || 0;
        if (ri && rockN[key] < ri.instanceMatrix.count) { ri.setMatrixAt(rockN[key]++, mtx); }
        if (rd < nearRock) {   // the audit reports the rock nearest the camera
          nearRock = rd; ctx.audit.models.rock.tris = S.rocks.models[rk].faces[lv]; ctx.audit.models.rock.lod = lv;
        }
        tells = [[0, rs * 1.05 / scale, 0]];   // just over the rock (the loop below scales tells by the craft scale)
      } else if (kind === 'drone' && S.lookMode === S.wholeLook && S.whole.roles && S.whole.roles.drone && poolTake(S.whole.roles.drone)) {
        var P0 = S.pools[S.whole.roles.drone], zm = P0.list[P0.used - 1], zs = (S.whole.meshes[S.whole.roles.drone].scale || 1) * scale;
        zm.position.set(pos[0], pos[1], pos[2]); zm.quaternion.copy(q); zm.scale.setScalar(zs);
        tells = [[0, 2.4, 0]];
      } else {
        if (!S.foeKinds[kind]) kind = 'raider';
        var fk2 = S.foeKinds[kind]; if (!fk2) return;
        mtx.compose(pv, q, sc);
        if (im[kind] < fk2.im.instanceMatrix.count) fk2.im.setMatrixAt(im[kind]++, mtx);
        built = fk2.built; tells = built.anchors.tells || [];
      }
      var thex = tellHex[x.cls.tell] || foeHex;
      for (var k2 = 0; k2 < x.tells; k2++) {
        var tp = tells[k2 % Math.max(1, tells.length)] || [0, 1, 0], ring = Math.floor(k2 / Math.max(1, tells.length));
        var lp = new THREE.Vector3(tp[0] * scale, tp[1] * scale + ring * 1.2, tp[2] * scale).applyQuaternion(q).add(pv);
        // a solid lamp disc in the tell's colour (the colour is the message: amber late, red failed, white big file), never
        // under C.TELL_PX on screen, inside a world-sized glow
        var tw = 3.4 * scale * flick, tpx = C.glowPx(tw, dist([lp.x, lp.y, lp.z]), fov, H), dpx = Math.max(C.TELL_PX, tpx * 0.8);
        S.glow.add(lp.x, lp.y, lp.z, thex, tw, 0);
        S.glow.add(lp.x, lp.y, lp.z, thex, dpx, 4, true);
        tellPx = Math.min(tellPx, dpx);
      }
      if (built) (built.anchors.engines || []).forEach(function (e) {
        var lp = new THREE.Vector3(e[0] * scale, e[1] * scale, e[2] * scale).applyQuaternion(q).add(pv);
        S.glow.add(lp.x, lp.y, lp.z, foeHex, (e[3] || 0.3) * 4.2 * scale, 0);
      });
      offPlane(pos, foeHex, 8 * scale);
    });
    Object.keys(S.foeKinds).forEach(function (k) { var X = S.foeKinds[k].im; X.count = im[k]; X.instanceMatrix.needsUpdate = true; });
    if (S.rockIm) Object.keys(S.rockIm).forEach(function (key) { var X = S.rockIm[key]; X.count = rockN[key] || 0; X.instanceMatrix.needsUpdate = true; });
    // Waiting beacons hold over their planet (the whole-mesh look: the skin's buoy mesh).
    if (S.beaconIm) {
      var bn = 0, bhex = look.beacon || '#FFB547', buoy = S.lookMode === S.wholeLook && S.whole.roles ? S.whole.roles.buoy : null;
      S.waiting.forEach(function (x, i) {
        var p = x.p || {x: star.x, y: star.y, r: star.r};
        var pos = [p.x + (i % 3 - 1) * 16, p.r * 1.25 + 14 + Math.floor(i / 3) * 14, p.y];
        eu.set(0, i, 0); q.setFromEuler(eu); pv.set(pos[0], pos[1], pos[2]); sc.set(1, 1, 1);
        var bm = buoy ? poolTake(buoy) : null;
        if (bm) { bm.position.copy(pv); bm.quaternion.copy(q); bm.scale.setScalar(S.whole.meshes[buoy].scale || 1); }
        else { mtx.compose(pv, q, sc); if (bn < S.beaconIm.instanceMatrix.count) S.beaconIm.setMatrixAt(bn++, mtx); }
        S.glow.add(pos[0], pos[1], pos[2], bhex, 16, 0);
      });
      S.beaconIm.count = bn; S.beaconIm.instanceMatrix.needsUpdate = true;
    }
    // a workflow of six steps or more flies a flagship over the planet its step works at (the whole-mesh look: the skin's mesh)
    var flagOn = !!S.flagshipAt, fpos = null;
    if (flagOn) {
      var fp = S.flagshipAt, fa = 0.6;
      fpos = [fp.x + Math.cos(fa) * (fp.r + 90), fp.r + 70, fp.y + Math.sin(fa) * (fp.r + 90)];
      var fh = fa + Math.PI / 2, fsn = S.lookMode === S.wholeLook && S.whole.roles ? S.whole.roles.flagship : null, fm = fsn ? poolTake(fsn) : null;
      if (fm) { fm.position.set(fpos[0], fpos[1], fpos[2]); fm.rotation.set(0, yawOf(fh), 0); fm.scale.setScalar(S.whole.meshes[fsn].scale || 1); }
      else if (S.flag) {
        S.flag.mesh.visible = true; S.flag.mesh.position.set(fpos[0], fpos[1], fpos[2]); S.flag.mesh.rotation.set(0, yawOf(fh), 0);
        S.flag.mesh.updateMatrixWorld(true);
        var fb = new THREE.Vector3(0, 0, 1).transformDirection(S.flag.mesh.matrixWorld);
        (S.flag.built.anchors.engines || []).forEach(function (e) {
          var w = new THREE.Vector3(e[0], e[1], e[2]).applyMatrix4(S.flag.mesh.matrixWorld);
          S.glow.add(w.x, w.y, w.z, (look.glow && look.glow.carrier) || '#FFD58A', e[3] * 4.5, 0);
          // A workflow marker has no flight telemetry.
        });
      }
      offPlane(fpos, '#E8EEF6', 40);
    }
    if (S.flag && !(flagOn && !(S.lookMode === S.wholeLook && S.whole.roles && S.whole.roles.flagship))) S.flag.mesh.visible = false;
    // the whole-mesh look's city blocks on the code planet the owner focuses
    var city = S.lookMode === S.wholeLook && S.whole.roles ? S.whole.roles.city : null, fpl = world.focus ? planetOf(world, world.focus) : null;
    if (city && fpl && fpl.purpose === 'code') {
      for (var ci = 0; ci < 5; ci++) {
        var cm = poolTake(city); if (!cm) break;
        var ca = ci * 1.2566 + 0.4, tilt = 0.32, nx = Math.sin(tilt) * Math.cos(ca), ny = Math.cos(tilt), nz = Math.sin(tilt) * Math.sin(ca);
        cm.position.set(fpl.x + nx * fpl.r, ny * fpl.r, fpl.y + nz * fpl.r);
        cm.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), new THREE.Vector3(nx, ny, nz));
        cm.scale.setScalar((S.whole.meshes[city].scale || 1) * Math.max(0.6, fpl.r / 70));
      }
    }
    poolEnd(t / 1000);
    S.glow.end(); S.flames.end(); S.drops.end(); S.shadows.end(); S.trailLines.end();
    // audit (craft.* and models.*)
    cr.own = 1; cr.carrier = S.carrier.built ? 1 : 0;
    cr.ownPos = sp.map(function (v) { return Math.round(v * 10) / 10; }); cr.ownShown = !!o.visible;
    cr.agents = Object.keys(S.agents).filter(function (u) { return !S.agents[u].data.virtual; }).length;
    cr.craftAgents = Object.keys(S.agents).length;
    cr.foes = S.foes.length; cr.waiting = S.waiting.length; cr.foeKindsShown = Object.keys(shownKinds).length;
    cr.dropLines = S.drops.n; cr.blobShadows = S.shadows.n;
    cr.beacons = beacons; cr.beaconPx = beaconPx; cr.warpIn = warpIn;
    cr.flameLen = +flameWork.toFixed(3);
    // an idle agent's drawn flame; with none idle on screen, the working agent's own engines at the idle rate
    cr.idleFlameLen = +(flameIdle || idleRate).toFixed(3);
    cr.idleFlameFrom = flameIdle ? 'idle agent' : idleRate ? 'idle rate' : null;
    cr.flameShown = flameShown;   // the working agent's flame is on the screen (not a ring beacon)
    cr.glowPoints = S.glow.n;
    cr.tellPx = isFinite(tellPx) ? +tellPx.toFixed(1) : null;   // the smallest tell drawn this frame (CSS px)
    cr.formation = {slots: S.formation.length, uids: S.formation.slice(), at: S.formation.map(function (u) {
      var r = S.agents[u]; return r ? r.v.pos.map(function (v) { return Math.round(v * 10) / 10; }) : null; }),
      shown: S.formation.map(function (u) { var r = S.agents[u]; return !!(r && r.obj.visible); }),
      tilt: S.formation.map(function (u) { var r = S.agents[u]; return r ? Math.round((r.tiltAng || 0) * 573) / 10 : null; })};   // degrees
    cr.travel = travel ? {to: travel.toUid, t: +travel.t.toFixed(3), doneAt: travel.doneAt == null ? null : +travel.doneAt.toFixed(3),
                          overshoot: +(travel.overshoot || 0).toFixed(3), r: travel.r, D: +travel.D.toFixed(1),
                          pos: (travel.pos || []).map(function (v) { return +v.toFixed(1); })} : (cr.travel || null);
    cr.look = S.lookMode;
    cr.flagship = flagOn ? 1 : 0;
  }

  // each geometry and material once: the hull material is shared by every hull mesh, and a mesh still under the core's
  // shader warm-up wears a stand-in with its own material kept in userData.cmReal. The materials are marked as this
  // module's (userData.cmOwner) so the warm-up's end never disposes one again
  function dispose() {
    if (!S) return;
    S.scene.remove(S.root);
    var gone = new Set(), once = function (x) { if (x && typeof x.dispose === 'function' && !gone.has(x)) { gone.add(x); if (x.isMaterial && x.userData) x.userData.cmOwner = 'craft'; x.dispose(); } };
    S.root.traverse(function (o) {
      once(o.geometry);
      (Array.isArray(o.material) ? o.material : o.material ? [o.material] : []).forEach(once);
      if (o.userData && o.userData.cmReal) once(o.userData.cmReal);
    });
    Object.keys(S.built).forEach(function (k) { once(S.built[k].geo); });
    if (S.own && S.own.baked) S.own.baked.textures.forEach(once);
    once(S.mat);
    S = null;
  }

  // ---------------------------------------------------------------- ?demo3d=ships (the core's demo route calls this)
  // One model of the skin alone on black, seen from `angle` (front34 | side | top | rear | rear34), its projected extent
  // `px` pixels when px is given (else framed to the view), lit by a white key from the camera's upper left with a faint
  // neutral rim, so the pixels show the skin's own colours: the silhouette and colour-mass checks of read it.
  var DIRS = {front34: [0.62, 0.38, -0.69], side: [1, 0, 0], top: [0, 1, 0.0001], rear: [0, 0.22, 1], rear34: [-0.55, 0.32, 0.77], hero: [-0.72, 0.3, -0.62]};
  var demo = {
    angles: Object.keys(DIRS),
    build: function (scene, world, ctx, q) {
      q = q || {};
      var THREE = ctx.THREE, sk = HK.resolve(ctx.skin3d || {}, CM3D.neutral3d || {}), name = q.model || 'own';
      var kit = (ctx.assets && ctx.assets.kit) || HK.atlas(THREE, sk.atlas3d || {}), lk = sk.look3d || {};
      // a measured view (px given) is lit white with a faint grey rim, its shadow floor and emissives as the paint is
      // (no hot core), so the pixels are the skin's own colours; the hero view (no px) is lit as in flight: the skin's
      // warm key, its ambient, its backlit rim and painted gradients
      var mat = HK.material(THREE, kit.texture, q.px ? {sun: '#ffffff', ambient: '#141414', rim: '#303030', rimK: 0.5, fill: 0.16, glowK: 1.15, hot: 0}
        : {sun: lk.sun || '#FFF1DC', ambient: lk.ambient || '#0A0C14', rim: lk.rim || '#8FB4FF', rimPow: lk.rimPow || 3, rimK: lk.rimK, fill: lk.fill});
      var b = HK.build(sk.models3d[name], {lod: +(q.lod || 0), models: sk.models3d});
      var geo = HK.toThree(THREE, b, {atlas: kit, letter: q.letter || 'K', gradK: q.px ? 1 : C.gradK(lk)});
      geo.computeBoundingBox();
      var mesh = new THREE.Mesh(geo, mat), ctr = geo.boundingBox.getCenter(new THREE.Vector3());
      mesh.position.copy(ctr).multiplyScalar(-1);
      var holder = new THREE.Group(); holder.add(mesh); scene.add(holder);
      if (!scene.background) scene.background = new THREE.Color('#000000');
      var cam = ctx.camera, dir = new THREE.Vector3().fromArray(DIRS[q.angle] || DIRS.front34).normalize();
      var H = (ctx.renderer && ctx.renderer.domElement.clientHeight) || 900;
      var size = geo.boundingBox.getSize(new THREE.Vector3()), ext = Math.max(size.x, size.y, size.z);
      var d = q.px ? ext * H / (2 * (+q.px) * Math.tan((cam.fov || 55) * Math.PI / 360)) : ext * 1.1;
      cam.position.copy(dir).multiplyScalar(d);
      cam.up.set(0, Math.abs(dir.y) > 0.99 ? 0 : 1, Math.abs(dir.y) > 0.99 ? -1 : 0);
      cam.near = Math.max(0.01, d - ext * 2); cam.far = d + ext * 2; cam.updateProjectionMatrix(); cam.lookAt(0, 0, 0);
      // the measured view's white key sits at the camera's upper left. The hero's warm key is high on the screen's right
      // at the craft's own depth (in the view's own frame, worked out from the angle, not from the camera's state), so the
      // top and one flank are lit and the other flank is in shadow with the sky-tinted rim along its silhouette
      var key;
      if (q.px) key = cam.position.clone().add(new THREE.Vector3(-d * 0.4, d * 0.6, 0).applyQuaternion(cam.quaternion));
      else {
        var F = dir.clone().negate(), upW = new THREE.Vector3(0, Math.abs(dir.y) > 0.99 ? 0 : 1, Math.abs(dir.y) > 0.99 ? -1 : 0);
        var Rt = new THREE.Vector3().crossVectors(F, upW).normalize(), Up = new THREE.Vector3().crossVectors(Rt, F).normalize();
        key = Rt.multiplyScalar(0.69).add(Up.multiplyScalar(0.74)).add(F.multiplyScalar(0.05)).normalize().multiplyScalar(d * 10);
      }
      mat.uniforms.uSunPos.value.copy(key.multiplyScalar(100));
      // the hero view (no px) shows the engines as they burn in flight: the glow and the flame of the skin's own colour
      var engines = 0;
      if (!q.px && (b.anchors.engines || []).length) {
        var ghex = (lk.glow && (name === 'own' ? lk.glow.own : name === 'carrier' || name === 'flagship' ? lk.glow.carrier : lk.glow.foe)) || '#4DE8E0';
        var gl = makeGlow(THREE, 48), fl = makeFlames(THREE, 8);
        gl.begin(); fl.begin();
        b.anchors.engines.forEach(function (e) {
          var p = [e[0] - ctr.x, e[1] - ctr.y, e[2] - ctr.z], r = e[3] || 0.3;
          gl.add(p[0], p[1], p[2], ghex, r * 5.5, 0);
          fl.add(p, [0, 0, 1], r * 0.9, r * 7, ghex);
          engines++;
        });
        // the emissive strips bloom as they do in flight (the same points, the hull centred on the origin)
        stripGlows(gl, b, new THREE.Matrix4().makeTranslation(-ctr.x, -ctr.y, -ctr.z), 1, THREE, 32);
        gl.end(); fl.end();
        gl.obj.material.uniforms.uScale.value = (H * ((ctx.renderer && ctx.renderer.getPixelRatio && ctx.renderer.getPixelRatio()) || 1)) / (2 * Math.tan((cam.fov || 55) * Math.PI / 360));
        gl.obj.material.uniforms.uDpr.value = (ctx.renderer && ctx.renderer.getPixelRatio && ctx.renderer.getPixelRatio()) || 1;
        holder.add(fl.obj); holder.add(gl.obj);
      }
      // colourMasses and coverage are measured on the drawn pixels once the canvas has its size (update below); until then
      // they are null, and hullMasses is the hull's paint by area (what the geometry says, whatever is drawn)
      var rec = {model: name, angle: q.angle || 'front34', px: +(q.px || 0), tris: b.stats.tris, colourMasses: null, coverage: null,
                 hullMasses: HK.colourMasses(b).count, engines: engines, measured: 0};
      // a demo page writes ext.* (the core's rule for anything that is not a module); a bare audit takes models.demo too
      try { if (ctx.audit.ext) ctx.audit.ext.ships = rec; } catch (e) { /* a read-only audit */ }
      try { (ctx.audit.models = ctx.audit.models || {}).demo = rec; } catch (e) { /* models are the craft module's */ }
      demo.last = {holder: holder, mesh: mesh, geo: geo, mat: mat, scene: scene, rec: rec, ticks: 0};
      return demo.last;
    },
    // the audit's numbers from the pixels: on the 3rd and the 20th frame the scene is drawn once more and read back at
    // once (no preserveDrawingBuffer needed), so a hull that draws wrong reports what it draws
    update: function (dt, ctx) {
      var L = demo.last;
      if (!L || !ctx || !ctx.renderer || !ctx.camera) return;
      L.ticks++;
      if (L.ticks !== 3 && L.ticks !== 20) return;
      ctx.renderer.render(L.scene, ctx.camera);
      var m = demo.measure(ctx.renderer), rec = L.rec;
      rec.colourMasses = m.colourMasses; rec.coverage = m.coverage; rec.bboxPx = m.bbox; rec.pixels = m.pixels;
      rec.masses = m.masses.slice(0, 5); rec.measured++;
      try { if (ctx.audit.ext) ctx.audit.ext.ships = rec; } catch (e) { /* a read-only audit */ }
    },
    // silhouette coverage (silhouette pixels / its bounding box) and colour masses (pixels grouped by hue in 30-degree
    // bins, colours under 0.15 saturation one bin; a mass holds >= 4% of the silhouette) of what is on the canvas
    measure: function (renderer) {
      var gl = renderer.getContext(), W = gl.drawingBufferWidth, H = gl.drawingBufferHeight, px = new Uint8Array(W * H * 4);
      gl.readPixels(0, 0, W, H, gl.RGBA, gl.UNSIGNED_BYTE, px);
      var n = 0, x0 = W, x1 = -1, y0 = H, y1 = -1, bins = {};
      for (var y = 0; y < H; y++) for (var x = 0; x < W; x++) {
        var i = (y * W + x) * 4, r = px[i], g2 = px[i + 1], b = px[i + 2];
        if (r + g2 + b < 18) continue;
        n++; if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y;
        var mx = Math.max(r, g2, b), mn = Math.min(r, g2, b), sat = mx ? (mx - mn) / mx : 0, l = 0.299 * r + 0.587 * g2 + 0.114 * b, h = 0;
        if (mx !== mn) { if (mx === r) h = 60 * (((g2 - b) / (mx - mn)) % 6); else if (mx === g2) h = 60 * ((b - r) / (mx - mn) + 2); else h = 60 * ((r - g2) / (mx - mn) + 4); }
        if (h < 0) h += 360;
        var k = sat < 0.15 ? 'grey' : 'h' + (Math.round(h / 30) % 12), B = bins[k] || (bins[k] = {n: 0, l: 0, s: 0, hx: 0, hy: 0});
        B.n++; B.l += l; B.s += sat; B.hx += Math.cos(h * Math.PI / 180); B.hy += Math.sin(h * Math.PI / 180);
      }
      var masses = Object.keys(bins).map(function (k) { var B = bins[k]; return {k: k, share: +(B.n / Math.max(1, n)).toFixed(3), luma: Math.round(B.l / B.n),
        sat: +(B.s / B.n).toFixed(2), hue: k === 'grey' ? null : Math.round((Math.atan2(B.hy, B.hx) * 180 / Math.PI + 360) % 360)}; })
        .sort(function (a, b) { return b.share - a.share; });
      return {pixels: n, bbox: [x1 - x0 + 1, y1 - y0 + 1], coverage: +(n / Math.max(1, (x1 - x0 + 1) * (y1 - y0 + 1))).toFixed(3),
              masses: masses, colourMasses: masses.filter(function (m) { return m.share >= 0.04; }).length};
    }
  };
  CM3D.demos = CM3D.demos || {};
  CM3D.demos.ships = demo;
  // the core's demo route (?demo3d=ships) calls def.build(scene, ctx, params)
  if (typeof CM3D.demo === 'function') CM3D.demo('ships', {build: function (scene, ctx, q) { demo.build(scene, null, ctx, q); },
    update: function (dt, ctx) { demo.update(dt, ctx); }});
  // the core's stand-in problem craft and ship give way to these
  if (typeof CM3D.provides === 'function') CM3D.provides('foes', 'ship');

  // a new world (rev moved) is synced in place: agents keep their flight, a new one warps in, problems and waiting move
  CM3D.module('craft', {build: build, update: update, dispose: dispose,
    rebuild: function (scene, world, ctx) { if (!S || S.scene !== scene) { if (S) dispose(); build(scene, world, ctx); } else sync(world, ctx); }});
})(typeof globalThis !== 'undefined' ? globalThis : this);
