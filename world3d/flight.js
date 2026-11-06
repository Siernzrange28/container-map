// Free flight in map units: the nose's attitude (or the older persistent pitch rate), XYZ travel and swept sphere contacts.
// Pure simulation; no renderer, page globals, clocks or input devices.
(function (g) {
  'use strict';
  var C = g.CM3D = g.CM3D || {}, RAD = Math.PI / 180;
  function clamp(x, lo, hi) { return Math.max(lo, Math.min(hi, x)); }
  function finite(x, fallback) { return typeof x === 'number' && isFinite(x) ? x : fallback; }
  function create(p) {
    p = p || {};
    var speed = Math.max(0, finite(p.speed, 0)), heading = finite(p.heading, 0);
    return { x: finite(p.x, 0), y: finite(p.y, 0), z: finite(p.z, 0), pitch: 0, pitchRate: 0,
      vx: Math.cos(heading) * speed, vy: 0, vz: Math.sin(heading) * speed,
      speed: speed, climb: 0, contact: null, contacts: 0, edge: null };
  }
  function bodyList(bodies) {
    return (bodies || []).filter(function (b) {
      return b && isFinite(b.x) && isFinite(b.y) && isFinite(b.r) && b.r > 0;
    });
  }
  function normal(s, b, radius) {
    var dx = s.x - b.x, dy = s.y, dz = s.z - b.y, d = Math.hypot(dx, dy, dz);
    if (d < 1e-8) { dx = 0; dy = 1; dz = 0; d = 1; }
    var n = { x: dx / d, y: dy / d, z: dz / d };
    s.x = b.x + n.x * (radius + 0.001); s.y = n.y * (radius + 0.001); s.z = b.y + n.z * (radius + 0.001);
    var inward = s.vx * n.x + s.vy * n.y + s.vz * n.z;
    if (inward < 0) { s.vx -= inward * n.x; s.vy -= inward * n.y; s.vz -= inward * n.z; }
  }
  function move(s, dx, dy, dz, bodies) {
    // Resolve a swept segment, then slide the unspent displacement along the surface.
    for (var pass = 0; pass < 5; pass++) {
      var hit = null, first = 1, distance2 = dx * dx + dy * dy + dz * dz;
      bodies.forEach(function (b) {
        var r = b.r + 14, ox = s.x - b.x, oy = s.y, oz = s.z - b.y;
        if (ox * ox + oy * oy + oz * oz < r * r) {
          normal(s, b, r); ox = s.x - b.x; oy = s.y; oz = s.z - b.y;
        }
        if (distance2 < 1e-14) return;
        var dot = ox * dx + oy * dy + oz * dz, c = ox * ox + oy * oy + oz * oz - r * r;
        var disc = dot * dot - distance2 * c;
        if (dot >= 0 || disc < 0) return;
        var t = (-dot - Math.sqrt(disc)) / distance2;
        if (t >= 0 && t < first) { first = t; hit = b; }
      });
      s.x += dx * first; s.y += dy * first; s.z += dz * first;
      if (!hit) return;
      var nx = s.x - hit.x, ny = s.y, nz = s.z - hit.y, length = Math.hypot(nx, ny, nz) || 1;
      nx /= length; ny /= length; nz /= length;
      normal(s, hit, hit.r + 14);
      s.contact = hit.uid || hit.label || 'body'; s.contacts++;
      var remaining = 1 - first; dx *= remaining; dy *= remaining; dz *= remaining;
      var inward = Math.min(0, dx * nx + dy * ny + dz * nz);
      dx -= inward * nx; dy -= inward * ny; dz -= inward * nz;
    }
  }
  function contain(s, bounds) {
    if (!bounds) return;
    [['x', 'vx', 'x0', 'x1'], ['z', 'vz', 'z0', 'z1'], ['y', 'vy', 'y0', 'y1']].forEach(function (a) {
      var low = finite(bounds[a[2]], -Infinity), high = finite(bounds[a[3]], Infinity), value = clamp(s[a[0]], low, high);
      if (value !== s[a[0]]) { s[a[0]] = value; s[a[1]] = 0; s.edge = a[0]; }
    });
  }
  function shift(s, dx, dy, dz, bodies, bounds) {
    move(s, dx, dy, dz, bodyList(bodies)); contain(s, bounds);
    s.speed = Math.hypot(s.vx, s.vy, s.vz); s.climb = s.vy;
  }
  // input.attitude (degrees, positive climbs): the nose's target, not a rate (flight-rs: 10% of the way a 30 Hz tick, 4
  // degrees a tick at most, so it levels itself when the target is 0); the ship then moves straight along the nose at
  // input.speed with no inertia, sliding round bodies; past input.ceiling (|y|, u) the target never points further out.
  // Without attitude: the older rate flight below (input.pitch a rate, inertial velocity).
  function stepAttitude(s, input, count, h, bodies, bounds) {
    var want = clamp(finite(input.attitude, 0), -85, 85), ceiling = finite(input.ceiling, Infinity);
    if (Math.abs(s.y) > ceiling && want * s.y > 0) want = 0;
    var heading = finite(input.heading, 0), speed = Math.max(0, finite(input.speed, 0));
    var k = 1 - Math.pow(0.9, 30 * h), cap = 4 * 30 * h;
    for (var i = 0; i < count; i++) {
      var before = s.pitch;
      s.pitch = clamp(s.pitch + clamp((want - s.pitch) * k, -cap, cap), -85, 85);
      s.pitchRate = (s.pitch - before) / h;
      var angle = s.pitch * RAD, cp = Math.cos(angle);
      s.vx = Math.cos(heading) * cp * speed; s.vy = Math.sin(angle) * speed; s.vz = Math.sin(heading) * cp * speed;
      move(s, s.vx * h, s.vy * h, s.vz * h, bodies);
      contain(s, bounds);
    }
  }
  function step(s, input, dt, bodies, bounds) {
    input = input || {};
    dt = clamp(finite(dt, 0), 0, 0.25);
    if (!dt || input.paused) return s;
    bodies = bodyList(bodies); s.contact = null; s.edge = null;
    var count = Math.ceil(dt * 120), h = dt / count;
    if (input.attitude != null) {
      stepAttitude(s, input, count, h, bodies, bounds);
      s.speed = Math.hypot(s.vx, s.vy, s.vz); s.climb = s.vy;
      return s;
    }
    for (var i = 0; i < count; i++) {
      var want = clamp(finite(input.pitch, 0), -1, 1) * 48;
      s.pitchRate += (want - s.pitchRate) * (1 - Math.exp(-12 * h));
      s.pitch = clamp(s.pitch + s.pitchRate * h, -85, 85);
      if (input.level) { s.pitch *= Math.exp(-4 * h); s.pitchRate = 0; }
      var angle = s.pitch * RAD, heading = finite(input.heading, 0), speed = Math.max(0, finite(input.speed, 0));
      var cp = Math.cos(angle), tx = Math.cos(heading) * cp * speed, ty = Math.sin(angle) * speed, tz = Math.sin(heading) * cp * speed;
      var response = input.brake ? 9 : 4, decay = Math.exp(-response * h), k = (1 - decay) / response;
      var dx = tx * h + (s.vx - tx) * k, dy = ty * h + (s.vy - ty) * k, dz = tz * h + (s.vz - tz) * k;
      s.vx = tx + (s.vx - tx) * decay; s.vy = ty + (s.vy - ty) * decay; s.vz = tz + (s.vz - tz) * decay;
      move(s, dx, dy, dz, bodies);
      contain(s, bounds);
    }
    s.speed = Math.hypot(s.vx, s.vy, s.vz); s.climb = s.vy;
    return s;
  }
  function spawnHeight(x, z, bodies) {
    var height = 0;
    bodyList(bodies).forEach(function (b) {
      var r = b.r + 24, d2 = (x - b.x) * (x - b.x) + (z - b.y) * (z - b.y);
      if (d2 < r * r) height = Math.max(height, Math.sqrt(r * r - d2));
    });
    return height;
  }
  C.flightPhysics = { create: create, step: step, shift: shift, spawnHeight: spawnHeight };
})(typeof globalThis !== 'undefined' ? globalThis : this);
