// world3d/space.js — the sky regions, stars and dust, the sun, the planets painted by surfaceAt, atmospheres, clouds, night
// lights, the flare, moons on focus with the sub-folder belt, the other machines as far galaxies, the region light and mood
//. Registered on the 3D view's registry (world3d/core.js); reads only `world` (cmWorld()) and `ctx`, never page
// globals (the planets' 2D art and surface come through ctx.page.art / ctx.page.surface, the page adapter).
//
// The first frame is the 2D frame: while the camera looks straight down the matched lens a planet shows its own
// 2D art (as the 2D drew it this frame, lit from the sun) projected straight down onto its sphere, its pole leaning toward
// the viewer as the 2D bodies' does; as the camera dives the art gives way to the planet's surface baked from the same
// surfaceAt into an equirect texture (three toon bands, the 2D's own light), the pole stands up and the clouds, the air and
// the rims fade in. One light rule in both: from the planet toward the sun, lifted 12 degrees, so the dive needs no
// light slerp. Permissive sources credited in NOTICE:
/* port: galaxy-shader (MIT) */
// the sun as a GPU spiral disc in one Points draw, stars streaming in along fixed arms, a black-hole shell that piles them into a ring, heavy-tailed sizes, additive, rim fade (GalaxyShader.ts:32-75)
/* port: nebreck (MIT) */
// the HDR sun core and corona sprite (Sun.ts:30-62), camera-wrapped space dust (SpaceDust.ts:54-69), the nebula sky as a far shell coloured by a palette (NebulaSkybox.ts:38-79)
/* port: space-3d (Unlicense) */
// the direction-only domain-warped nebula with the displacement started at zero, baked once into a cube (nebula.glsl:33-58, space-3d.js:146-257); stars stay live points
/* port: spacekit (MIT) */
// stars at infinity (gl_Position.z = w, shaders.ts:182-186), the back-face atmosphere shell (shaders.ts:218-241), moons built on focus and disposed on leave (SpaceObject.ts:496-541)
/* port: glsl-atmosphere (Unlicense) */
// single-scatter Rayleigh + Mie for the nearest planet (index.glsl:49-103) with both fixes the research found (the march starts at the shell, a miss is not a hit) and its exposure map
/* port: procedural-planets (MIT) */
// the cloud shell's density and speed knobs (atmosphere.js:189-232) as one noise shell
/* port: lensflare-vanilla (CC0 1.0) */
// ghosts on the sun-to-centre line, opacity times (1 - occlusion), occlusion by an analytic ray-sphere test eased with 1-exp(-dt*8), gated on view-space z, static under reduced motion (LensFlare.js)
/* port: cosmos (MIT) */
// other machines as far log-spiral galaxies tilted by a seed (galaxy.glsl.ts:202-232)
/* port: instanced-mesh (MIT) */
// one RGBA per instance with alpha as opacity (InstancedMesh2.ts:728-769), raycast that returns instanceId (Raycasting.ts:19-74), hover lifting one instance (fastRaycasting.ts:27-33), on THREE.InstancedMesh
/* port: tsl-textures (MIT) */
// the private sun's photosphere: the lookup vector turned six times by pos*scale, seed inside the scale step so it churns rather than scrolls (photosphere.js:33-41)
// Per-region environment records control fog, light direction and ambient colour.
/* port: three-geospatial (MIT) */
// dithering on the gradient materials against banding (ditheringEffect.frag)
// Activity lights the night side; distant moons fade smoothly as the camera moves away.
// video o9 #20 - a second light from the sky's bright side; video o8 #3 - the sky grades red while a region is in trouble.
(function (g) {
  'use strict';
  var CM3D = g.CM3D;
  if (!CM3D || !CM3D.module) return;
  CM3D.provides('sky', 'sun', 'planets');

  var TAU = Math.PI * 2, D2R = Math.PI / 180;
  var LIGHT_EL = 12 * D2R, LIGHT_STEPS = 48, TILT = 23 * D2R;   // the 2D bodies' light lift and axial tilt (ui/kinds.js)
  var STEP_LOOK = { 0: [0.5, 0.8], 1: [0.2, 0.55] };            // window thresholds by toon band, idle and at work (KIND_WIN)

  // ------------------------------------------------------------------ the neutral look ("Nightglass")
  // A skin's 3D file may carry sky3d and env3d blocks ('s keys); a key it lacks falls through to these.
  var NEUTRAL = {
    sky3d: {
      base: '#070A12',
      regions: {
        code: ['#123A6B', '#1FB5A8', '#F2C14E'], notes: ['#3B1F5C', '#C0569C', '#E6C8E8'], data: ['#4A2418', '#C77A2E', '#4E4A8C'],
        media: ['#0F5040', '#3FD6A0', '#A04090'], reference: ['#3A2E1C', '#B89A5A', '#E2CC92'], config: ['#10304A', '#4C9CD0', '#CFEFFF'],
        shared: ['#133848', '#3FB0C8', '#9FE4F0'], archive: ['#2A1E12', '#7A5A30', '#B08A58'], unsorted: ['#161A22', '#3A4250', '#6A7484'],
        // trouble: the.12 grade colour, the problem craft's crimson, the broken status red as the hot core
        problem: ['#3A0E12', '#8A2A34', '#FF5A5F'], planning: ['#0B1A1A', '#1F4A44', '#5FA898']
      },
      nebula: { code: 1, notes: 1, data: 1, media: 1, reference: 1, config: 1, shared: 1, archive: 1, unsorted: 1, problem: 1, planning: 1 },
      alert: '#3A0E12', alertMix: 0.25, planned: '#0B1A1A', plannedMix: 0.15,
      stars: { cool: '#A9C1FF', warm: '#FFE3B0', bright: '#E8F0FF', warmEvery: 10, brightEvery: 8, px: [2, 4] },
      rim: null, rimI: 0.4,
      sun: { core: '#FFE8B0', mid: '#FF9A3C', edge: '#E0602A', corona: '#FF9A3C', kind: 'disc' },
      // [place on the sun-to-centre line (0 the sun, 0.5 the centre), size px, colour, shape (0 glow, 1 ring, 2 hex, 3 ringed disc,
      // 4 streak), alpha]: a glow and a streak at the sun, three ghosts (T9 neutral)
      flare: [[0, 200, '#FFE8B0', 0, 0.4], [0, 420, '#9CC8FF', 4, 0.4], [0.21, 78, '#7FA8FF', 2, 0.6], [0.33, 34, '#B8FFD8', 0, 0.5], [0.74, 100, '#80C0FF', 3, 0.5]],
      dust: '#9AB4E0', look: ''
    },
    env3d: {
      code: { fog: '#0B1A2C', near: 3000, far: 16000, lightDir: [0.4, 0.8, 0.2], light: '#FFF1DC', ambient: '#0E1626' },
      notes: { fog: '#160E22', near: 3000, far: 16000, lightDir: [0.3, 0.8, -0.3], light: '#FFE8E8', ambient: '#160E1E' },
      reference: { fog: '#1A140C', near: 3000, far: 16000, lightDir: [0.5, 0.7, 0.2], light: '#FFE9C8', ambient: '#16120C' },
      data: { fog: '#1C100A', near: 3000, far: 16000, lightDir: [-0.3, 0.8, 0.3], light: '#FFE4C4', ambient: '#1A1008' },
      media: { fog: '#081C18', near: 3000, far: 16000, lightDir: [0.2, 0.8, 0.4], light: '#E8FFF4', ambient: '#0A1A16' },
      config: { fog: '#0A1824', near: 3000, far: 16000, lightDir: [0.4, 0.8, -0.2], light: '#EEF8FF', ambient: '#0C1620' },
      shared: { fog: '#0A1A20', near: 3000, far: 16000, lightDir: [0.3, 0.8, 0.3], light: '#E8FAFF', ambient: '#0A161C' },
      archive: { fog: '#16100A', near: 3000, far: 16000, lightDir: [0.5, 0.7, -0.2], light: '#FFE6C8', ambient: '#140E08' },
      unsorted: { fog: '#10141A', near: 3000, far: 16000, lightDir: [0.4, 0.8, 0.1], light: '#F0F2F6', ambient: '#0E1116' }
    }
  };
  var HEALTH = { broken: '#FF5A5F', waiting: '#FFB547', ok: '#8FC8FF', quiet: '#6B7684' };
  var WORKING = '#6FB7FF', FROST = '#D8F4FF', IDLE_GREY = '#6B7684';

  // ------------------------------------------------------------------ pure helpers (exported on CM3D.space for the tests)
  function clamp(v, a, b) { return v < a ? a : v > b ? b : v; }
  function smoothstep(a, b, x) { var t = clamp((x - a) / (b - a), 0, 1); return t * t * (3 - 2 * t); }
  function hexRgb(h) { var v = parseInt(String(h || '#000000').replace('#', '').slice(0, 6), 16) || 0; return [v >> 16 & 255, v >> 8 & 255, v & 255]; }
  function rgbHex(c) { return '#' + c.map(function (v) { return Math.round(clamp(v, 0, 255)).toString(16).padStart(2, '0'); }).join('').toUpperCase(); }
  function mixRgb(a, b, k) { return [a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k]; }
  function luma(c) { return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]; }
  // a sub-folder's alpha in the belt by the days since its last write: 1 within a week, 0.45 at a month or more, linear between
  function beltAlpha(days) { if (days == null || !isFinite(days)) return 1; return days <= 7 ? 1 : days >= 30 ? 0.45 : 1 - 0.55 * (days - 7) / 23; }
  // a moon (and its label) fades out between 6.4 and 8 orbit radii of camera distance to its planet (cosmosjourneyer, idea)
  function moonOpacity(d, a) { return 1 - smoothstep(6.4 * a, 8 * a, d); }
  // cloud density 0.2 + 0.6 x activity: 1 while a step or agent works there, else decaying with the days since the last write
  function activity(working, days) { return working ? 1 : Math.exp(-Math.max(0, days == null ? 3 : days) / 7); }
  function cloudDensity(working, days) { return 0.2 + 0.6 * activity(working, days); }
  // the black-hole shell of the sun's disc: 0.10 r, swelling to 0.20 r with ten agents at work
  function sunShell(r, working) { return 0.10 * r * (1 + Math.min(Math.max(Number(working) || 0, 0), 10) / 10); }
  // the 2D light: from the planet toward the sun in 48 steps (kindsLightBucket), lifted 12 degrees toward the viewer, who in
  // 3D is up (+Y): the same vector on the match frame and in flight
  function lightBucket(px, py, sx, sy) { var a = Math.atan2(sy - py, sx - px); return ((Math.round(a / TAU * LIGHT_STEPS) % LIGHT_STEPS) + LIGHT_STEPS) % LIGHT_STEPS; }
  function lightDir(bucket) { var a = bucket / LIGHT_STEPS * TAU, ce = Math.cos(LIGHT_EL); return [ce * Math.cos(a), Math.sin(LIGHT_EL), ce * Math.sin(a)]; }
  function skyTable(skin3d) {
    var s = (skin3d && skin3d.sky3d) || {}, n = NEUTRAL.sky3d, out = {};
    Object.keys(n).forEach(function (k) { out[k] = s[k] != null ? s[k] : n[k]; });
    out.regions = {}; Object.keys(n.regions).forEach(function (k) { out.regions[k] = (s.regions && s.regions[k]) || n.regions[k]; });
    if (s.regions) Object.keys(s.regions).forEach(function (k) { out.regions[k] = s.regions[k]; });
    out.nebula = {}; Object.keys(n.nebula).forEach(function (k) { out.nebula[k] = s.nebula && s.nebula[k] != null ? s.nebula[k] : n.nebula[k]; });
    if (s.nebula) Object.keys(s.nebula).forEach(function (k) { out.nebula[k] = s.nebula[k]; });
    Object.keys(s).forEach(function (k) { if (!(k in out)) out[k] = s[k]; });   // a skin's own keys (bases, dusts)
    return out;
  }
  // a region's sky: base and three colours (deep, mid, core) and whether it has a nebula at all (a skin may keep a region black)
  function regionPalette(skin3d, region) {
    var t = skyTable(skin3d), key = t.regions[region] ? region : 'unsorted', c = t.regions[key];
    var base = (t.bases && t.bases[key]) || t.base;
    return { region: key, base: base, colours: c.slice(0, 4), dust: (t.dusts && t.dusts[key]) || null, nebula: t.nebula[key] !== 0 && t.nebula[key] !== false };
  }
  // A region supplies lighting and fog, with neutral values filling absent skin properties.
  function envFor(skin3d, purpose) {
    var defaults = NEUTRAL.env3d, overrides = (skin3d && skin3d.env3d) || {};
    var region = defaults[purpose] || overrides[purpose] ? purpose : 'unsorted';
    var fallback = defaults[region] || defaults.unsorted;
    var custom = overrides[region] || {};
    return ['fog', 'near', 'far', 'lightDir', 'light', 'ambient'].reduce(function (record, field) {
      record[field] = custom[field] == null ? fallback[field] : custom[field];
      return record;
    }, {purpose: region});
  }
  // ray from o along unit d against a sphere (c, r): the near distance, or -1
  function raySphere(o, d, c, r) {
    var ox = o[0] - c[0], oy = o[1] - c[1], oz = o[2] - c[2], b = ox * d[0] + oy * d[1] + oz * d[2], q = ox * ox + oy * oy + oz * oz - r * r, h = b * b - q;
    if (h < 0) return -1; var t = -b - Math.sqrt(h); return t > 0 ? t : (-b + Math.sqrt(h) > 0 ? 0 : -1);
  }
  // the PCG-style integer hash both the GPU and these helpers use, so CPU and GPU agree on the sky's bright side
  function hash3(x, y, z, s) {
    var h = (Math.imul(x | 0, 374761393) + Math.imul(y | 0, 668265263) + Math.imul(z | 0, 1274126177) + Math.imul(s | 0, 1442695041)) | 0;
    h = Math.imul(h ^ (h >>> 13), 1274126177); h ^= h >>> 16; return (h >>> 0) / 4294967295;
  }
  function vnoise(x, y, z, s) {
    var xi = Math.floor(x), yi = Math.floor(y), zi = Math.floor(z), xf = x - xi, yf = y - yi, zf = z - zi;
    var u = xf * xf * (3 - 2 * xf), v = yf * yf * (3 - 2 * yf), w = zf * zf * (3 - 2 * zf), L = function (a, b, t) { return a + (b - a) * t; };
    var c = function (i, j, k) { return hash3(xi + i, yi + j, zi + k, s); };
    return L(L(L(c(0, 0, 0), c(1, 0, 0), u), L(c(0, 1, 0), c(1, 1, 0), u), v), L(L(c(0, 0, 1), c(1, 0, 1), u), L(c(0, 1, 1), c(1, 1, 1), u), v), w);
  }
  // the recipe's two layers as two lobes of gas on the sky (the bake's input): a direction at a fixed bearing from the layer's
  // offset (the second lobe across the sky from the first, so most headings face one), a lean of the long axis, its widths,
  // the fan, the gain. Lobe 0 is the brighter: the rim light comes from its side (T3), the sky demo looks at it
  function lobesOf(R) {
    var L = (R && R.layers) || [], seed = (R && R.seed) >>> 0;
    return [0, 1].map(function (i) {
      var l = L[i] || L[0] || { offset: [500, 0, 500], scale: 1.6, falloff: 4, intensity: 1.1 }, o = l.offset || [0, 0, 0];
      var az = Math.atan2(o[2], o[0]) + (i ? Math.PI * (0.8 + 0.4 * hash3(i, 2, 3, seed)) : 0), el = clamp(o[1] / 1000, -1, 1) * 0.38;
      var dir = [Math.cos(el) * Math.cos(az), Math.sin(el), Math.cos(el) * Math.sin(az)];
      var lean = (hash3(i, 5, 7, seed) - 0.5) * 1.6, up = [0, 1, 0], ax0 = [-Math.sin(az), 0, Math.cos(az)];
      var axis = [ax0[0] * Math.cos(lean) + up[0] * Math.sin(lean), up[1] * Math.sin(lean), ax0[2] * Math.cos(lean) + up[2] * Math.sin(lean)];
      return { dir: dir, axis: axis, sx: 0.78 + 0.2 * clamp(((l.scale || 1.6) - 1.2) / 0.8, 0, 1), sy: 0.3 + 0.08 * clamp(((l.falloff || 4) - 3) / 2, 0, 1),
        fan: 0.7 + 0.45 * hash3(i, 9, 1, seed), gain: (i ? 0.8 : 1.15) * (l.intensity || 1.1) / 1.15 };
    });
  }
  // the lobes' soft mask at one direction (no noise): the far stars crowd the gas
  function lobeMask(d, lobes) {
    var best = 0;
    lobes.forEach(function (l) {
      var c = l.dir, a = l.axis, ad = a[0] * c[0] + a[1] * c[1] + a[2] * c[2], A = [a[0] - c[0] * ad, a[1] - c[1] * ad, a[2] - c[2] * ad], al = Math.hypot(A[0], A[1], A[2]) || 1;
      A = [A[0] / al, A[1] / al, A[2] / al];
      var B = [c[1] * A[2] - c[2] * A[1], c[2] * A[0] - c[0] * A[2], c[0] * A[1] - c[1] * A[0]];
      var z = d[0] * c[0] + d[1] * c[1] + d[2] * c[2]; if (z < 0) return;
      var ax = Math.atan2(d[0] * A[0] + d[1] * A[1] + d[2] * A[2], z), ay = Math.atan2(d[0] * B[0] + d[1] * B[1] + d[2] * B[2], z), sy = Math.max(0.02, l.sy * (1 + l.fan * ax));
      best = Math.max(best, Math.exp(-(Math.pow(ax / l.sx, 2) + Math.pow(ay / sy, 2))) * Math.min(1, l.gain));
    });
    return best;
  }
  function brightSide(R) { return lobesOf(R)[0].dir.slice(); }
  function fallbackRecipe(seed) {
    var s = (Number(seed) >>> 0) || 7, rnd = function () { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296; };
    var layer = function () { return { offset: [rnd() * 2000 - 1000, rnd() * 2000 - 1000, rnd() * 2000 - 1000], scale: 1.2 + rnd() * 0.8, falloff: 3 + rnd() * 2, intensity: 1 + rnd() * 0.3 }; };
    return { seed: (Number(seed) >>> 0) || 7, layers: [layer(), layer()], warpSteps: 4, cut: 0.46, gain: 2.6, maxAlpha: 0.55 };
  }

  // ------------------------------------------------------------------ GLSL
  var GL_HASH = [
    'float cmH3(ivec3 c, int s){',
    '  uint h = uint(c.x) * 374761393u + uint(c.y) * 668265263u + uint(c.z) * 1274126177u + uint(s) * 1442695041u;',
    '  h = (h ^ (h >> 13u)) * 1274126177u; h ^= h >> 16u; return float(h) / 4294967295.0; }',
    'float cmVN(vec3 p, int s){ vec3 i = floor(p), f = p - i; vec3 u = f * f * (3.0 - 2.0 * f); ivec3 c = ivec3(i);',
    '  return mix(mix(mix(cmH3(c, s), cmH3(c + ivec3(1,0,0), s), u.x), mix(cmH3(c + ivec3(0,1,0), s), cmH3(c + ivec3(1,1,0), s), u.x), u.y),',
    '             mix(mix(cmH3(c + ivec3(0,0,1), s), cmH3(c + ivec3(1,0,1), s), u.x), mix(cmH3(c + ivec3(0,1,1), s), cmH3(c + ivec3(1,1,1), s), u.x), u.y), u.z); }',
    'float cmFbm(vec3 p, int s){ float a = 0.5, t = 0.0; for (int i = 0; i < 4; i++){ t += a * cmVN(p, s + i * 7); p = p * 2.03 + vec3(1.7, 9.2, 3.1); a *= 0.5; } return t / 0.9375; }',
    'vec3 cmS2L(vec3 c){ return mix(c / 12.92, pow((c + 0.055) / 1.055, vec3(2.4)), step(0.04045, c)); }'
  ].join('\n') + '\n';
  /* port: ashima-noise (MIT) */
  // 3D simplex noise (Ian McEwan, Ashima Arts; webgl-noise), with the fbm and ridged sums the nebula, the clouds and the
  // planets' detail are built from
  var GL_SIMPLEX = [
    'vec3 cmM289(vec3 x){ return x - floor(x * (1.0 / 289.0)) * 289.0; }',
    'vec4 cmM289(vec4 x){ return x - floor(x * (1.0 / 289.0)) * 289.0; }',
    'vec4 cmPerm(vec4 x){ return cmM289(((x * 34.0) + 10.0) * x); }',
    'vec4 cmTIS(vec4 r){ return 1.79284291400159 - 0.85373472095314 * r; }',
    'float cmSN(vec3 v){ const vec2 C = vec2(1.0 / 6.0, 1.0 / 3.0); const vec4 D = vec4(0.0, 0.5, 1.0, 2.0);',
    '  vec3 i = floor(v + dot(v, C.yyy)), x0 = v - i + dot(i, C.xxx), g = step(x0.yzx, x0.xyz), l = 1.0 - g;',
    '  vec3 i1 = min(g.xyz, l.zxy), i2 = max(g.xyz, l.zxy), x1 = x0 - i1 + C.xxx, x2 = x0 - i2 + C.yyy, x3 = x0 - D.yyy;',
    '  i = cmM289(i);',
    '  vec4 p = cmPerm(cmPerm(cmPerm(i.z + vec4(0.0, i1.z, i2.z, 1.0)) + i.y + vec4(0.0, i1.y, i2.y, 1.0)) + i.x + vec4(0.0, i1.x, i2.x, 1.0));',
    '  float n_ = 0.142857142857; vec3 ns = n_ * D.wyz - D.xzx; vec4 j = p - 49.0 * floor(p * ns.z * ns.z);',
    '  vec4 x_ = floor(j * ns.z), y_ = floor(j - 7.0 * x_), x = x_ * ns.x + ns.yyyy, y = y_ * ns.x + ns.yyyy, h = 1.0 - abs(x) - abs(y);',
    '  vec4 b0 = vec4(x.xy, y.xy), b1 = vec4(x.zw, y.zw), s0 = floor(b0) * 2.0 + 1.0, s1 = floor(b1) * 2.0 + 1.0, sh = -step(h, vec4(0.0));',
    '  vec4 a0 = b0.xzyw + s0.xzyw * sh.xxyy, a1 = b1.xzyw + s1.xzyw * sh.zzww;',
    '  vec3 p0 = vec3(a0.xy, h.x), p1 = vec3(a0.zw, h.y), p2 = vec3(a1.xy, h.z), p3 = vec3(a1.zw, h.w);',
    '  vec4 nm = cmTIS(vec4(dot(p0, p0), dot(p1, p1), dot(p2, p2), dot(p3, p3))); p0 *= nm.x; p1 *= nm.y; p2 *= nm.z; p3 *= nm.w;',
    '  vec4 m = max(0.5 - vec4(dot(x0, x0), dot(x1, x1), dot(x2, x2), dot(x3, x3)), 0.0); m = m * m;',
    '  return 105.0 * dot(m * m, vec4(dot(p0, x0), dot(p1, x1), dot(p2, x2), dot(p3, x3))); }',
    'float cmF6(vec3 p){ float a = 0.5, s = 0.0; for (int i = 0; i < 6; i++){ s += a * cmSN(p); p = p * 2.03 + vec3(1.7, 9.2, 3.1); a *= 0.5; } return s; }',
    'float cmF4(vec3 p){ float a = 0.5, s = 0.0; for (int i = 0; i < 4; i++){ s += a * cmSN(p); p = p * 2.07 + vec3(5.3, 1.3, 7.7); a *= 0.5; } return s; }',
    'float cmRidge(vec3 p){ float a = 0.5, s = 0.0; for (int i = 0; i < 5; i++){ float n = 1.0 - abs(cmSN(p)); s += a * n * n; p = p * 2.11 + vec3(3.1, 4.7, 1.9); a *= 0.5; } return s; }'
  ].join('\n') + '\n';
  // the nebula bake (space-3d: direction only, baked once into a cube; nebreck: a far shell coloured by a palette): the recipe's
  // two layers are two lobes of gas (a soft mask round a direction, its width growing along its axis into a fan, its edge
  // domain-warped by fbm with ridged filaments and a hot core, cut by ridged dust lanes), plus a low haze. Data only, per
  // direction: r, g = each lobe's luminance (1 - exp(-1.9 density)), b = the dust that cuts them, a = the haze. The palette is
  // applied live, so a region change cross-fades two palettes over the same bake and never re-bakes.
  var BAKE_VERT = 'varying vec3 vDir; void main(){ vDir = position; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }';
  var BAKE_FRAG = GL_SIMPLEX + [
    'uniform vec3 uC0; uniform vec3 uC1; uniform vec3 uX0; uniform vec3 uX1; uniform vec4 uS0; uniform vec4 uS1; uniform float uSeed; uniform float uWarp;',
    'varying vec3 vDir;',
    'float lobe(vec3 d, vec3 c, vec3 a, vec4 S, float k, out float dust){ dust = 0.0; if (S.w <= 0.0) return 0.0;',
    '  vec3 b = cross(c, a); float z = dot(d, c), x = dot(d, a), y = dot(d, b);',
    '  float ax = atan(x, max(z, 1e-3)), ay = atan(y, max(z, 1e-3)), sx = S.x, sy = max(S.y * (1.0 + S.z * ax), 0.03);',
    '  float pre = (ax / (sx * 2.2)) * (ax / (sx * 2.2)) + (ay / (sy * 2.2)) * (ay / (sy * 2.2));',
    '  if (z < -0.1 || pre > 4.0) return 0.0;',
    '  vec3 q = d * 1.9 + k * 13.1 + uSeed;',               // the displacement starts at zero (space-3d), then the warp
    '  vec3 w = vec3(cmF4(q * 0.8), cmF4(q * 0.8 + vec3(5.2, 1.3, 2.8)), cmF4(q * 0.8 + vec3(9.1, 4.4, 7.3)));',
    '  vec3 qq = q + w * 0.55 * uWarp; float n1 = cmF6(qq * 0.85), n2 = cmRidge(qq * 2.2 + 3.0);',
    '  float ex = ax / sx + n1 * 0.45, ey = ay / sy + n1 * 0.6 + w.x * 0.4, m = exp(-(ex * ex + ey * ey)), core = exp(-(ex * ex + ey * ey) * 3.5);',
    '  float dens = m * (0.42 + 0.75 * n1) + sqrt(m) * 0.42 * n2 * n2 + core * 0.55; dens = max(dens - 0.12, 0.0) * S.w;',
    '  float rd = cmRidge(qq * 1.5 + 11.0);',
    '  dust = (smoothstep(0.55, 0.85, rd) * 0.75 + smoothstep(0.15, 0.5, cmF4(qq * 1.2 + 21.0)) * 0.45) * smoothstep(0.05, 0.5, m);',
    '  dens *= 1.0 - 0.8 * clamp(dust, 0.0, 1.0); dust *= smoothstep(0.15, 0.6, m);',
    '  return 1.0 - exp(-dens * 1.45); }',
    'void main(){ vec3 d = normalize(vDir); float d0, d1;',
    '  float l0 = lobe(d, uC0, uX0, uS0, 0.0, d0), l1 = lobe(d, uC1, uX1, uS1, 1.0, d1);',
    '  float hz = smoothstep(-0.2, 0.8, cmF4(d * 1.3 + 4.0 + uSeed));',
    '  gl_FragColor = vec4(l0, l1, clamp(max(d0, d1), 0.0, 1.0), hz); }'
  ].join('\n');
  // the sky shown: the bake coloured by the region's palette in display space (deep -> mid -> core ramps per lobe, two
  // palettes cross-faded), the trouble / planning grade over it; at infinity (z = w), drawn first. uFlat (1 on the first
  // frame) dims the sky toward the 2D backdrop (55% of the nebula stays: the matched frame reads as the 2D map's painted
  // space, never a black screen); it eases off in the dive
  var SKY_VERT = 'varying vec3 vDir; void main(){ vDir = position; vec4 p = projectionMatrix * vec4((viewMatrix * vec4(position, 0.0)).xyz, 1.0); gl_Position = p.xyww; }';
  var SKY_FRAG = GL_HASH + [
    'uniform samplerCube uCube; uniform float uHas;',
    'uniform vec3 uBaseA; uniform vec3 uA0; uniform vec3 uA1; uniform vec3 uA2; uniform vec3 uA3; uniform float uNebA; uniform vec3 uDustA;',
    'uniform vec3 uBaseB; uniform vec3 uB0; uniform vec3 uB1; uniform vec3 uB2; uniform vec3 uB3; uniform float uNebB; uniform vec3 uDustB;',
    'uniform float uMix; uniform vec4 uGrade; uniform float uGain; uniform float uFlat; uniform vec3 uFlatC;',
    '#include <common>',
    '#include <dithering_pars_fragment>',
    'varying vec3 vDir;',
    'vec3 ramp(vec3 c0, vec3 c1, vec3 c2, vec3 c3, float l){ vec3 c = c0; c = mix(c, c1, smoothstep(0.28, 0.7, l)); c = mix(c, c2, smoothstep(0.66, 0.9, l)); c = mix(c, c3, smoothstep(0.86, 1.0, l)); return c * (0.45 + 0.55 * l); }',
    'vec3 paint(vec3 base, vec3 c0, vec3 c1, vec3 c2, vec3 c3, float neb, vec3 dc, vec4 d){',
    '  vec3 acc = mix(base, mix(base, c0, 0.6), d.a * neb);',
    '  float l0 = clamp(d.r * uGain, 0.0, 1.0) * neb, l1 = clamp(d.g * uGain, 0.0, 1.0) * neb;',
    '  vec3 k1 = ramp(c0, mix(c0, c1, 0.6), c1, c2, l1); acc = mix(acc, k1, smoothstep(0.0, 0.35, l1)) + k1 * l1 * 0.06;',
    '  vec3 k0 = ramp(c0, c1, c2, c3, l0); acc = mix(acc, k0, smoothstep(0.0, 0.35, l0)) + k0 * l0 * 0.06;',
    '  acc = mix(acc, dc, d.b * 0.6 * neb); return acc; }',
    'void main(){ vec4 d = uHas > 0.5 ? textureCube(uCube, normalize(vDir)) : vec4(0.0);',
    '  vec3 col = mix(paint(uBaseA, uA0, uA1, uA2, uA3, uNebA, uDustA, d), paint(uBaseB, uB0, uB1, uB2, uB3, uNebB, uDustB, d), uMix);',
    '  col = mix(col, uGrade.rgb, uGrade.a);',
    '  col = mix(col, mix(uFlatC, col, 0.55), uFlat);',
    '  gl_FragColor = vec4(cmS2L(col), 1.0);',
    '  #include <colorspace_fragment>',
    '  #include <dithering_fragment>',
    '}'
  ].join('\n');
  // stars at infinity: one point per star, size in px (no attenuation), a soft core and halo
  // three layers in one draw (a dense dim far layer that crowds the gas, the ~4,000 named stars with one in eight bright, a
  // few four-point sparkles): aKind 0 dot, 1 halo star, 2 sparkle; sizes in px at any distance, no twinkle
  var STAR_VERT = [
    'attribute float aSize; attribute vec3 aCol; attribute float aKind; uniform float uDpr; uniform float uFade; varying vec3 vCol; varying float vKind;',
    'void main(){ vCol = aCol * uFade; vKind = aKind; vec4 p = projectionMatrix * vec4((viewMatrix * vec4(position, 0.0)).xyz, 1.0); gl_Position = p.xyww;',
    '  gl_PointSize = aSize * uDpr; }'
  ].join('\n');
  var STAR_FRAG = [
    'uniform vec3 uHalo; varying vec3 vCol; varying float vKind;',
    'void main(){ vec2 q = gl_PointCoord * 2.0 - 1.0; float r = length(q); if (r > 1.0) discard; vec3 c;',
    '  if (vKind < 0.5) c = vCol * smoothstep(1.0, 0.2, r);',
    '  else if (vKind < 1.5){ float core = smoothstep(0.55, 0.0, r), halo = exp(-r * r * 4.0) * 0.9; c = vCol * core + uHalo * halo * length(vCol); }',
    '  else { float core = exp(-r * r * 30.0), ray = max(exp(-abs(q.x) * 60.0) * (1.0 - abs(q.y)), exp(-abs(q.y) * 60.0) * (1.0 - abs(q.x)));',
    '    c = vCol * (core + ray * 0.85); }',
    '  gl_FragColor = vec4(c, 1.0);',
    '  #include <colorspace_fragment>',
    '}'
  ].join('\n');
  // space dust: motes wrapped round the eye, each a short streak along the flight (speed cue), faded by distance
  var DUST_VERT = [
    'attribute vec3 aSeed; attribute float aEnd; uniform vec3 uEye; uniform vec3 uVel; uniform float uBox; uniform float uStreak; varying float vA;',
    'void main(){ vec3 p = uEye + mod(aSeed - uEye + uBox * 0.5, uBox) - uBox * 0.5; float d = length(p - uEye) / (uBox * 0.5);',
    '  p -= uVel * uStreak * aEnd; vA = (1.0 - smoothstep(0.55, 1.0, d)) * smoothstep(0.02, 0.12, d) * (1.0 - aEnd * 0.85);',
    '  gl_Position = projectionMatrix * viewMatrix * vec4(p, 1.0); }'
  ].join('\n');
  var DUST_FRAG = [
    'uniform vec3 uCol; uniform float uAlpha; varying float vA;',
    '#include <common>',
    '#include <dithering_pars_fragment>',
    'void main(){ gl_FragColor = vec4(uCol * vA * uAlpha, 1.0);',
    '  #include <colorspace_fragment>',
    '  #include <dithering_fragment>',
    '}'
  ].join('\n');
  // the sun's spiral disc (galaxy-shader): every star placed on the GPU from its index, streaming in along fixed arms,
  // pushed onto the black-hole shell inside uBH; an integer hash (no sin hash: phones differ); alpha fades at the rim
  var DISC_VERT = [
    'attribute float aIndex; uniform float uCount; uniform float uT; uniform float uR; uniform float uBH; uniform float uArms; uniform float uTurns;',
    'uniform vec3 uC; uniform vec3 uU; uniform vec3 uV; uniform vec3 uN; uniform float uPx; uniform float uDpr; uniform vec3 uCore; uniform vec3 uMid; uniform vec3 uEdge;',
    'varying vec3 vCol; varying float vA;',
    'uint cmHu(uint x){ x ^= x >> 16u; x *= 0x7feb352du; x ^= x >> 15u; x *= 0x846ca68bu; x ^= x >> 16u; return x; }',
    'float cmR(float i, uint k){ return float(cmHu(uint(i) * 2654435761u + k * 374761393u)) / 4294967295.0; }',
    'void main(){ float per = uCount / uArms, arm = floor(aIndex / per), idx = mod(aIndex, per);',
    '  idx = mod(idx - fract(uT) * per, per);',
    '  float rad = idx / per, ang = rad * 6.2831853 * uTurns + arm * 6.2831853 / uArms;',
    '  rad = pow(rad, 0.85) * (1.0 + (cmR(aIndex, 1u) * 2.0 - 1.0) * 0.18); ang += (cmR(aIndex, 2u) * 2.0 - 1.0) * (0.12 + 0.16 * rad);',
    '  float R = rad * uR * 1.55; vec3 p = uC + (uU * cos(ang) + uV * sin(ang)) * R + uN * sin((cmR(aIndex, 3u) * 2.0 - 1.0) * 0.087) * R;',
    '  vec3 v = p - uC; float d = length(v); float onShell = 0.0; if (d < uBH && d > 0.001){ p = uC + v * (uBH / d); onShell = 1.0; }',
    '  float s = 4.0 * pow(cmR(aIndex, 7u) + 0.1, 3.0) * pow(cmR(aIndex, 9u) + 0.1, 3.0);',
    '  vec4 mv = modelViewMatrix * vec4(p, 1.0); gl_Position = projectionMatrix * mv;',
    '  gl_PointSize = clamp(uR * 0.016 * s * uPx / max(1.0, -mv.z), 1.0, 2.6) * uDpr;',
    '  float heat = clamp(1.0 - rad, 0.0, 1.0); vCol = mix(mix(uEdge, uMid, smoothstep(0.1, 0.55, heat)), uCore, smoothstep(0.6, 0.95, heat));',
    '  vCol = mix(vCol, vec3(1.0, 0.97, 0.9), onShell * 0.8);',
    '  vA = (1.0 - smoothstep(0.6, 1.0, rad)) * (0.3 + 0.5 * onShell) * (0.5 + 0.5 * heat); }'
  ].join('\n');
  var DISC_FRAG = [
    'varying vec3 vCol; varying float vA;',
    'void main(){ vec2 q = gl_PointCoord - 0.5; float r = length(q) * 2.0; float a = (1.0 - smoothstep(0.5, 1.0, r)) * vA; if (a <= 0.003) discard;',
    '  gl_FragColor = vec4(vCol * a, 1.0);',
    '  #include <colorspace_fragment>',
    '}'
  ].join('\n');
  // the private sun's photosphere (tsl-textures): the lookup vector turned six times by pos * scale, the seed inside the
  // scale step, then one noise; churn colours from the skin, rim to core
  var PHOTO_VERT = 'varying vec3 vO; varying vec3 vN; varying vec3 vP; void main(){ vO = normalize(position); vec4 wp = modelMatrix * vec4(position, 1.0); vP = wp.xyz; vN = normalize(mat3(modelMatrix) * normal); gl_Position = projectionMatrix * viewMatrix * wp; }';
  var PHOTO_FRAG = GL_HASH + [
    'uniform float uSeed; uniform float uScale; uniform vec3 uBg; uniform vec3 uFg; uniform vec3 uRimC; varying vec3 vO; varying vec3 vN; varying vec3 vP;',
    'vec3 rotE(vec3 v, vec3 a){ float cx = cos(a.x), sx = sin(a.x), cy = cos(a.y), sy = sin(a.y), cz = cos(a.z), sz = sin(a.z);',
    '  v = vec3(v.x, v.y * cx - v.z * sx, v.y * sx + v.z * cx); v = vec3(v.x * cy + v.z * sy, v.y, -v.x * sy + v.z * cy); return vec3(v.x * cz - v.y * sz, v.x * sz + v.y * cz, v.z); }',
    'void main(){ vec3 pos = vO, v = pos; float s = exp(uScale + 1.0);',
    '  for (int i = 0; i < 6; i++){ v = rotE(v, pos * s); s *= 1.1 + 0.05 * sin(uSeed * s); }',
    '  float k = cmVN(v * 3.0 + 7.0, 11); vec3 col = mix(uBg, uFg, k);',
    '  float mu = clamp(dot(normalize(vN), normalize(cameraPosition - vP)), 0.0, 1.0); col = mix(uRimC, col, smoothstep(0.0, 0.6, mu));',
    '  gl_FragColor = vec4(col * 1.25, 1.0);',
    '  #include <colorspace_fragment>',
    '}'
  ].join('\n');
  // a far galaxy (another machine): a tilted log-spiral of points at infinity (cosmos galaxy.glsl.ts), sizes in px
  var GAL_VERT = [
    'attribute float aIndex; uniform float uCount; uniform vec3 uDir; uniform vec3 uU; uniform vec3 uV; uniform float uSpread; uniform float uDpr; uniform float uSeed;',
    'uniform vec3 uCol; varying vec3 vCol; varying float vA;',
    'uint cmHu(uint x){ x ^= x >> 16u; x *= 0x7feb352du; x ^= x >> 15u; x *= 0x846ca68bu; x ^= x >> 16u; return x; }',
    'float cmR(float i, uint k){ return float(cmHu(uint(i) * 2654435761u + k * 374761393u + uint(uSeed))) / 4294967295.0; }',
    'void main(){ float arm = mod(aIndex, 2.0), rad = pow(cmR(aIndex, 1u), 0.7);',
    '  float a = arm * 3.14159 + log(max(rad, 0.05)) * 2.4 + (cmR(aIndex, 2u) - 0.5) * 0.9 * (1.0 - rad);',
    '  vec2 q = vec2(cos(a), sin(a)) * rad; if (cmR(aIndex, 5u) < 0.25){ q *= 0.25; }',
    '  vec3 dir = normalize(uDir + (uU * q.x + uV * q.y) * uSpread);',
    '  vec4 p = projectionMatrix * vec4((viewMatrix * vec4(dir, 0.0)).xyz, 1.0); gl_Position = p.xyww;',
    '  gl_PointSize = (1.0 + 2.0 * pow(cmR(aIndex, 3u), 4.0)) * uDpr; vCol = mix(uCol, vec3(1.0, 0.95, 0.85), 1.0 - rad); vA = 0.35 + 0.65 * (1.0 - rad); }'
  ].join('\n');
  var GAL_FRAG = 'varying vec3 vCol; varying float vA; void main(){ vec2 q = gl_PointCoord - 0.5; if (length(q) > 0.5) discard; gl_FragColor = vec4(vCol * vA, 1.0);\n  #include <colorspace_fragment>\n}';
  // the planets. One mapping from a surface normal to the body's own longitude and latitude (the 2D's: the pole leans 23
  // degrees toward the viewer; on the first frame the viewer is straight up, and the pole stands up as the camera leaves the
  // map), the bake looked up there; then the comps' light (soft terminator with a hint of the 2D's three bands, a sunset band,
  // 26% limb darkening, an inner rim of the air on the lit limb), swirled clouds with their shadows, an ocean glint, the night
  // side's windows and glows from the bake's emissive part, a fine noise over the bake so a near planet never shows texels.
  var PLANET_VERT = 'varying vec3 vN; varying vec3 vP; void main(){ vec4 wp = modelMatrix * vec4(position, 1.0); vP = wp.xyz; vN = normalize(mat3(modelMatrix) * normal); gl_Position = projectionMatrix * viewMatrix * wp; }';
  var PLANET_FRAG = GL_SIMPLEX + [
    'uniform sampler2D uSurf; uniform float uSurfOn; uniform vec2 uSurfSize;',
    'uniform sampler2D uArt; uniform float uArtOn; uniform vec3 uArtRect; uniform vec2 uArtPx;',
    'uniform float uMapK; uniform float uPhase; uniform float uTilt; uniform vec3 uL;',
    'uniform vec3 uFlat2; uniform vec3 uRimCol; uniform float uRimI; uniform vec3 uRimDir;',
    'uniform vec3 uAtm; uniform float uAtmK;',
    'uniform float uWork; uniform float uCity; uniform vec3 uGlow; uniform vec3 uHalo; uniform float uHaloOn; uniform float uQuiet; uniform float uDim;',
    'uniform vec3 uFrost; uniform vec3 uFogC; uniform vec2 uFogR; uniform float uFogK; uniform float uWinAll;',
    'uniform float uSeedF; uniform float uCloud; uniform float uCloudOp; uniform float uCloudSpin; uniform float uGlint; uniform float uGas; uniform float uDetail;',
    'varying vec3 vN; varying vec3 vP;',
    'vec4 secN(float s, float iu, float iv){ return texture2D(uSurf, vec2((iu + 0.5) / uSurfSize.x, (s * uSurfSize.y + iv + 0.5) / (uSurfSize.y * 4.0))); }',
    'vec4 secL(float s, float u, float v){ float vv = clamp(v * uSurfSize.y, 0.5, uSurfSize.y - 0.5); return texture2D(uSurf, vec2(u, (s * uSurfSize.y + vv) / (uSurfSize.y * 4.0))); }',
    // view normal -> body longitude (with the turn) and latitude
    'vec2 lonLat(vec3 N){ float al = 1.5707963 * (1.0 - uMapK);',
    '  vec3 n = vec3(N.x, N.y * cos(al) - N.z * sin(al), N.y * sin(al) + N.z * cos(al));',
    '  float cT = cos(uTilt), sT = sin(uTilt), by = n.z * cT - n.y * sT, bz = n.z * sT + n.y * cT;',
    '  return vec2(atan(n.x, bz) + uPhase, asin(clamp(by, -1.0, 1.0))); }',
    'vec3 bodyDir(vec2 ll){ return vec3(cos(ll.y) * sin(ll.x), sin(ll.y), cos(ll.y) * cos(ll.x)); }',
    'float clouds(vec3 B){ float ca = cos(uCloudSpin), sa = sin(uCloudSpin); vec3 r = vec3(B.x * ca - B.z * sa, B.y, B.x * sa + B.z * ca);',
    '  vec3 cs = r * 2.1 + uSeedF * 1.7; vec3 w = vec3(cmF4(cs), cmF4(cs + 4.7), cmF4(cs + 8.3));',
    '  return smoothstep(0.08, 0.55, cmF6(cs * 1.3 + w * 1.6) - 0.28 + 0.62 * uCloud); }',
    'void main(){',
    '  vec3 N = normalize(vN), V = normalize(cameraPosition - vP), L = normalize(uL);',
    '  vec2 ll = lonLat(N); vec3 B = bodyDir(ll);',
    '  float u = fract(ll.x / 6.2831853), v = ll.y / 3.1415927 + 0.5;',
    '  float iu = min(floor(u * uSurfSize.x), uSurfSize.x - 1.0), iv = clamp(floor(v * uSurfSize.y), 0.0, uSurfSize.y - 1.0);',
    '  vec4 day = uSurfOn > 0.5 ? secL(2.0, u, v) : vec4(uFlat2, 0.5);',
    '  vec3 alb = day.rgb;',
    '  float dn = uGas > 0.5 ? cmF4(vec3(B.x * 2.0, B.y * 34.0, B.z * 2.0) + uSeedF) : cmF4(B * 24.0 + uSeedF);',
    '  alb *= 1.0 + uDetail * dn * 0.55;',
    '  float ndl = dot(N, L), mu = max(dot(N, V), 0.0), fl = 1.0 - uMapK;',
    '  float cl = 0.0, sh = 0.0;',
    '  if (uCloudOp > 0.0){ cl = clouds(B) * uCloudOp; sh = clouds(bodyDir(lonLat(normalize(N - L * 0.014)))) * uCloudOp; }',
    '  alb *= 1.0 - 0.35 * sh;',
    '  float diff = smoothstep(-0.12, 1.0, ndl); diff = mix(diff, floor(diff * 3.0 + 0.5) / 3.0, 0.22);',
    '  vec3 c = alb * vec3(1.0, 0.96, 0.9) * diff * 1.04;',
    '  float tb = exp(-pow((ndl - 0.03) / 0.10, 2.0));',
    '  c += alb * vec3(1.0, 0.42, 0.18) * tb * 0.45;',                                   // the sunset band
    '  c = mix(c, vec3(0.95, 0.96, 1.0) * (diff * 1.12 + tb * vec3(0.9, 0.45, 0.25) * 0.6), cl);',
    '  if (uGlint > 0.5 && day.a < 0.45){ vec3 H = normalize(L + V); c += (1.0 - cl) * pow(max(dot(N, H), 0.0), 60.0) * 0.55 * smoothstep(0.0, 0.2, ndl) * vec3(1.0, 0.9, 0.75); }',
    '  c *= mix(0.74, 1.0, pow(mu, 0.45));',                                             // limb darkening
    '  float night = 1.0 - smoothstep(-0.12, 0.08, ndl);',
    '  c += alb * 0.025 * night;',
    '  vec4 em = uSurfOn > 0.5 ? secN(3.0, iu, iv) : vec4(0.0); float code = em.a * 255.0;',
    '  if (code > 0.5){',
    '    if (code < 100.5){ vec4 eL = secL(3.0, u, v); float wA = clamp(eL.a * 2.55, 0.0, 1.0); vec3 aC = eL.rgb / max(eL.a * 2.55, 0.25);',
    '      c = mix(c, aC * (0.55 + 0.45 * night), 0.8 * wA * (1.0 - cl * 0.6)); }',   // an aurora on both sides, its edge soft
    '    else if (code < 127.5) c = mix(c, em.rgb, 0.85);',
    '    else { float w = (code - 128.0) / 127.0, th = mix(uWork > 0.5 ? 0.55 : 0.8, uWork > 0.5 ? 0.2 : 0.5, night);',
    '      bool gap = uWork > 0.5 || uWinAll > 0.5 || mod(iu + iv, 2.0) < 0.5;',
    '      vec2 f = fract(vec2(u * uSurfSize.x, v * uSurfSize.y)), jc = vec2(fract(sin(iu * 12.9898 + iv * 78.233) * 43758.5453), fract(sin(iu * 39.346 + iv * 11.135) * 24634.6345)) * 0.44 + 0.28;',
    '      float dotK = smoothstep(0.3, 0.1, length(f - jc)) + 0.25 * smoothstep(0.5, 0.2, length(f - jc));',
    '      if (w > th && gap && night > 0.15) c += em.rgb * (1.2 + uWork * 0.8) * night * (1.0 - cl * 0.7) * dotK; } }',
    '  if (uHaloOn > 0.5) c += uHalo * night * (pow(1.0 - mu, 2.0) * 0.6 + 0.07);',          // a working body without windows glows at night
    '  else if (uCity > 0.5 && uWork > 0.5) c += uGlow * night * 0.05;',
    '  c += uAtm * pow(1.0 - mu, 2.6) * (0.1 + 0.75 * smoothstep(-0.25, 0.6, ndl)) * uAtmK * fl;',   // the inner rim of the air
    '  c = mix(c, uFrost * (0.25 + 0.75 * diff), clamp(pow(1.0 - mu, 2.0) * 2.5, 0.0, 1.0) * 0.55 * uQuiet * fl);',
    '  c += uRimCol * uRimI * pow(1.0 - mu, 2.5) * clamp(dot(N, normalize(uRimDir)), 0.0, 1.0) * fl;',  // the sky-side rim (T3)
    '  float fz = smoothstep(uFogR.x, uFogR.y, length(vP - cameraPosition)); c = mix(c, uFogC, fz * uFogK * fl);',
    '  if (uArtOn > 0.5 && uMapK > 0.0){',                                     // the 2D art, projected straight down
    '    vec2 a = (vP.xz - uArtRect.xy) / uArtRect.z;',
    '    if (a.x >= 0.0 && a.y >= 0.0 && a.x < uArtPx.x && a.y < uArtPx.y){',
    '      vec4 art = texture2D(uArt, vec2((floor(a.x) + 0.5) / uArtPx.x, 1.0 - (floor(a.y) + 0.5) / uArtPx.y));',
    '      c = mix(c, art.rgb, uMapK * step(0.5, art.a)); } }',
    '  c *= uDim;',
    '  gl_FragColor = vec4(c, 1.0);',
    '  #include <colorspace_fragment>',
    '}'
  ].join('\n');
  // the nearest planet's air (glsl-atmosphere, both fixes): in planet-radius units, the shell 1.10 r. Added over the planet and
  // its outer glow: blue on the lit limb, reddened where the light skims the terminator
  var ATMO_FRAG = [
    'uniform vec3 uC; uniform float uR; uniform vec3 uL; uniform vec3 uKRlh; uniform float uKMie; uniform float uOp; uniform int uI; uniform int uJ; uniform float uSunI;',
    'varying vec3 vN; varying vec3 vP;',
    '#ifndef PI',
    '#define PI 3.141592653589793',
    '#endif',
    'vec2 ATM_rsi(vec3 r0, vec3 rd, float sr){ float a = dot(rd, rd), b = 2.0 * dot(rd, r0), c = dot(r0, r0) - sr * sr, d = b * b - 4.0 * a * c;',
    '  if (d < 0.0) return vec2(1e5, -1e5); return vec2((-b - sqrt(d)) / (2.0 * a), (-b + sqrt(d)) / (2.0 * a)); }',
    'vec3 ATM_scatter(vec3 r, vec3 r0, vec3 pSun, float iSun, float rP, float rA, vec3 kRlh, float kMie, float shRlh, float shMie, float g){',
    '  vec2 p = ATM_rsi(r0, r, rA); if (p.x > p.y) return vec3(0.0);',
    '  vec2 pl = ATM_rsi(r0, r, rP); float tEnd = (pl.x <= pl.y && pl.x > 0.0) ? min(p.y, pl.x) : p.y;',   // fix 2: a miss is not a hit
    '  float tStart = max(p.x, 0.0); if (tEnd <= tStart) return vec3(0.0);',                                  // fix 1: start at the shell
    '  float iStep = (tEnd - tStart) / float(uI), iTime = tStart; vec3 totalRlh = vec3(0.0), totalMie = vec3(0.0); float iOdRlh = 0.0, iOdMie = 0.0;',
    '  float mu = dot(r, pSun), mumu = mu * mu, gg = g * g;',
    '  float pRlh = 3.0 / (16.0 * PI) * (1.0 + mumu), pMie = 3.0 / (8.0 * PI) * ((1.0 - gg) * (mumu + 1.0)) / (pow(1.0 + gg - 2.0 * mu * g, 1.5) * (2.0 + gg));',
    '  for (int i = 0; i < 8; i++){ if (i >= uI) break;',
    '    vec3 iPos = r0 + r * (iTime + iStep * 0.5); float iHeight = length(iPos) - rP;',
    '    float odStepRlh = exp(-iHeight / shRlh) * iStep, odStepMie = exp(-iHeight / shMie) * iStep; iOdRlh += odStepRlh; iOdMie += odStepMie;',
    '    float jStep = ATM_rsi(iPos, pSun, rA).y / float(uJ), jTime = 0.0, jOdRlh = 0.0, jOdMie = 0.0;',
    '    for (int j = 0; j < 4; j++){ if (j >= uJ) break; vec3 jPos = iPos + pSun * (jTime + jStep * 0.5); float jHeight = max(length(jPos) - rP, -0.05);',
    '      jOdRlh += exp(min(-jHeight / shRlh, 30.0)) * jStep; jOdMie += exp(min(-jHeight / shMie, 30.0)) * jStep; jTime += jStep; }',
    '    vec3 attn = exp(-(kMie * (iOdMie + jOdMie) + kRlh * (iOdRlh + jOdRlh)));',
    '    totalRlh += odStepRlh * attn; totalMie += odStepMie * attn; iTime += iStep; }',
    '  return iSun * (pRlh * kRlh * totalRlh + pMie * kMie * totalMie); }',
    'void main(){ vec3 ro = (cameraPosition - uC) / uR, rd = normalize(vP - cameraPosition);',
    '  vec3 c = ATM_scatter(rd, ro, normalize(uL), uSunI, 1.0, 1.10, uKRlh, uKMie, 0.028, 0.008, 0.758);',
    '  c = 1.0 - exp(-1.6 * c);',                                            // the exposure map (example.frag:27)
    '  gl_FragColor = vec4(c * uOp, 1.0);',
    '  #include <colorspace_fragment>',
    '}'
  ].join('\n');
  // every planet's outer air (spacekit's back-face shell, the comps' glow): a back-side sphere a little over the body, its
  // glow from the eye ray's closest pass to the centre ((1 - x)^3.2 across the shell), bright on the sun side with a forward
  // scatter when the eye looks toward the sun through it; in the air's colour (the health colour when something is wrong)
  var SHELL_FRAG = [
    'uniform vec3 uRim; uniform vec3 uL; uniform float uK; uniform vec3 uC; uniform float uR; uniform float uRa; varying vec3 vN; varying vec3 vP;',
    'void main(){ vec3 D = normalize(vP - cameraPosition); float t = dot(uC - cameraPosition, D); vec3 P = cameraPosition + D * t; float h = length(P - uC);',
    '  float x = clamp((h - uR) / (uRa - uR), 0.0, 1.0); if (h < uR) x = 0.0; float glow = pow(1.0 - x, 3.2);',
    '  vec3 Ls = normalize(uL), nr = normalize(P - uC + vec3(1e-4)); float lit = smoothstep(-0.3, 0.55, dot(nr, Ls)), fs = pow(max(dot(D, Ls), 0.0), 6.0) * 0.9;',
    '  float nl = dot(nr, Ls), dusk = exp(-pow((nl + 0.05) / 0.2, 2.0));',             // the air reddens where the light skims it
    '  vec3 col = uRim * (lit * (1.0 - 0.75 * dusk) + fs) + vec3(1.0, 0.42, 0.16) * dusk * 0.85;',
    '  gl_FragColor = vec4(col * glow * uK, 1.0);',
    '  #include <colorspace_fragment>',
    '}'
  ].join('\n');
  // the realistic planets (a skin's planets3d manifest and locally baked world maps)
  // crates/game/src/planet.wesl. Everything a planet shows in one pass of a few texture reads: the baked albedo (with
  // the night lights in its alpha), relief from the baked surface normal (shorter over water: the sea mask), a sun glint
  // on open water, two drifting cloud fields from one shared sky map, city lights where work runs, and the air in front
  // of the ground in closed form (an exponential layer of scale height H: a column of depth0 / (cos z + sqrt(2H/pi))
  // along a ray leaving it), no ray march and no noise sums; up close, one read of a small noise volume per pixel keeps
  // the ground from going soft. The halo pass (REAL_HALO_FRAG) draws the glowing limb on the back-faced shell. The body
  // frame is the painted planets' own (toBody = lonLat's turn, tilt and phase as one rotation), so the first frame's 2D
  // art, the pole standing up in the dive and the turn are unchanged; the map's layers (health air, work lights, the
  // quiet frost, the sky-side rim, the region fog, a quiet folder's fade) ride on top.
  var REAL_VERT = 'varying vec3 vP; varying vec3 vC; void main(){ vec4 wp = modelMatrix * vec4(position, 1.0); vP = wp.xyz; vC = (modelMatrix * vec4(0.0, 0.0, 0.0, 1.0)).xyz; gl_Position = projectionMatrix * viewMatrix * wp; }';
  var REAL_COMMON = [
    '#ifndef PI',
    '#define PI 3.141592653589793',
    '#endif',
    'uniform vec4 uAirSky; uniform vec4 uAir;',
    // optical depth (zenith depths) out of the air along a ray at zenith cosine mu
    'float rColumn(float mu){ float k = sqrt(2.0 * uAir.x / PI); return uAirSky.w * (1.0 + k) / (max(mu, 0.0) + k); }',
    // Rayleigh's phase plus a little forward scattering, so a back-lit limb flares
    'float rPhase(float c){ float g = 0.7; float hg = (1.0 - g * g) / pow(1.0 + g * g - 2.0 * g * c, 1.5); return 0.75 * (1.0 + c * c) + 0.06 * hg; }'
  ].join('\n') + '\n';
  var REAL_FRAG = REAL_COMMON + [
    'uniform samplerCube uAlb; uniform samplerCube uRel; uniform samplerCube uSkyMap; uniform sampler3D uNoise;',
    'uniform float uReady; uniform float uHasRel; uniform float uHasSky; uniform float uHasNoise; uniform float uTexel; uniform float uAge;',
    'uniform vec4 uSurfK; uniform vec4 uCloudC; uniform float uCloudSpin;',
    'uniform sampler2D uArt; uniform float uArtOn; uniform vec3 uArtRect; uniform vec2 uArtPx;',
    'uniform float uMapK; uniform float uPhase; uniform float uTilt; uniform vec3 uL; uniform vec3 uFlat2;',
    'uniform vec3 uRimCol; uniform float uRimI; uniform vec3 uRimDir; uniform vec3 uHalo; uniform float uHaloOn; uniform float uQuiet; uniform vec3 uFrost;',
    'uniform vec3 uFogC; uniform vec2 uFogR; uniform float uFogK; uniform float uDim;',
    'varying vec3 vP; varying vec3 vC;',
    // a world vector into the body's frame (y the pole): the painted planets' lonLat turn as one rotation
    'vec3 toBody(vec3 v){ float al = 1.5707963 * (1.0 - uMapK);',
    '  vec3 n = vec3(v.x, v.y * cos(al) - v.z * sin(al), v.y * sin(al) + v.z * cos(al));',
    '  float cT = cos(uTilt), sT = sin(uTilt), by = n.z * cT - n.y * sT, bz = n.z * sT + n.y * cT, c = cos(uPhase), s = sin(uPhase);',
    '  return vec3(n.x * c + bz * s, by, bz * c - n.x * s); }',
    'vec3 spinY(vec3 p, float a){ float c = cos(a), s = sin(a); return vec3(c * p.x + s * p.z, p.y, c * p.z - s * p.x); }',
    // cloud opacity from a field's rank: the top `cover` of the sky is cloud, mostly a veil, opaque deep in the decks
    'float cloudAt(float rank){ float cut = 1.0 - uSurfK.z; return smoothstep(cut - 0.03, cut + 0.12, rank) * (0.42 + 0.58 * smoothstep(cut + 0.05, cut + 0.5, rank)); }',
    'void main(){',
    '  vec3 Nw = normalize(vP - vC), Vw = normalize(cameraPosition - vP);',
    '  vec3 sd = toBody(Nw), sl = toBody(normalize(uL)), sv = toBody(Vw);',
    '  float fl = 1.0 - uMapK, ready = step(0.5, uReady);',
    '  vec4 surf = ready > 0.5 ? texture(uAlb, sd) : vec4(uFlat2, 0.0);',
    '  vec3 albedo = surf.rgb; float la = dot(albedo, vec3(0.2126, 0.7152, 0.0722));',
    '  albedo = mix(albedo, vec3(la), uAge * 0.4) * (1.0 - uAge * 0.15);',          // a quiet folder fades toward grey
    '  float water = 0.0; vec3 n = sd;',
    '  if (uHasRel > 0.5 && ready > 0.5){ vec3 rel = texture(uRel, sd).rgb * 2.0 - 1.0; float len = length(rel); water = 1.0 - smoothstep(0.55, 0.8, len); n = rel / max(len, 1e-4); }',
    // close up, when one texel spans several pixels: one read of the noise volume, one noise cell per map texel
    '  float detail = 0.5, cdet = 0.5, towns = 1.0;',
    '  float mag = uHasNoise > 0.5 ? smoothstep(1.2, 3.5, uTexel / max(length(fwidth(sd)), 1e-7)) : 0.0;',
    '  if (mag > 0.01){ vec3 p = sd / uTexel; if (uHasRel < 0.5) p = vec3(p.x * 0.25, p.y * 2.0, p.z * 0.25);',
    '    vec2 nz = textureLod(uNoise, p * 0.125, 0.0).rg; detail = mix(0.5, nz.r, mag); cdet = mix(0.5, nz.g, mag); towns = mix(1.0, smoothstep(0.56, 0.72, nz.r) * 2.5, mag); }',
    '  albedo *= 1.0 + (detail - 0.5) * 0.34 * (1.0 - water);',
    '  float ndls = dot(sd, sl), ndl = max(dot(n, sl), 0.0) * smoothstep(-0.03, 0.1, ndls);',
    '  vec4 sk = vec4(0.0, 0.0, 0.5, 1.0);',
    '  if (uSurfK.z > 0.0 && uHasSky > 0.5) sk = texture(uSkyMap, spinY(sd, uCloudSpin) * vec3(1.0, uCloudC.w > 1.5 ? -1.0 : 1.0, 1.0));',
    '  bool which = fract(uCloudC.w * 0.5) > 0.25;',
    '  float deck = cloudAt(which ? sk.g : sk.r), other = which ? sk.r : sk.g;',
    '  float veil = smoothstep(0.55, 0.95, other) * 0.3 * min(uSurfK.z * 3.0, 1.0), cv = 1.0 - (1.0 - deck) * (1.0 - veil);',
    '  float cloud = clamp(cv + (cdet - 0.5) * 1.2 * cv * (1.0 - cv), 0.0, 1.0) * uSurfK.w, shade = 1.0 - 0.3 * deck * uSurfK.w;',
    '  float calm = smoothstep(0.15, 0.85, other);',
    '  vec3 col = albedo * (ndl * shade);',
    '  vec3 hv = normalize(sl + sv); float ndh = max(dot(sd, hv), 0.0), fres = 0.02 + 0.98 * pow(1.0 - max(dot(sd, sv), 0.0), 5.0);',
    '  col += vec3(water * uSurfK.x * pow(ndh, mix(90.0, 400.0, calm)) * mix(0.5, 1.6, calm) * (0.35 + fres) * smoothstep(0.0, 0.08, ndls) * (1.0 - cloud));',
    '  col = mix(col, uCloudC.rgb * 1.25 * pow(max(ndls + 0.03, 0.0), 0.8) * (0.9 + 0.2 * cdet), cloud);',
    '  float mu = max(dot(sd, sv), 0.0);',
    '  if (uAir.w > 0.0) col *= mix(1.0, pow(mu, 0.55), uAir.w);',                  // gas giants darken toward the edge
    '  vec3 sun = vec3(1.0, 0.985, 0.96) * uAir.z;',
    '  col = col * sun + albedo * 0.02;',
    '  float night = 1.0 - smoothstep(-0.16, 0.06, ndls);',
    '  col += vec3(1.0, 0.6, 0.28) * (surf.a * night * (1.0 - 0.8 * cloud) * uSurfK.y * towns * 2.2);',   // the lights where work runs
    '  if (uHaloOn > 0.5) col += uHalo * night * (pow(1.0 - mu, 2.0) * 0.6 + 0.07);',   // a working world without cities glows at night
    // the air in front of the ground: blue haze toward the limb, the sunlight through it reddened at the terminator
    '  vec3 ext = exp(-uAirSky.rgb * rColumn(mu) * 0.6), tsun = exp(-uAirSky.rgb * rColumn(ndls));',
    '  col = col * mix(vec3(1.0), ext, fl) + sun * tsun * rPhase(dot(sl, -sv)) * (1.0 - ext) * smoothstep(-0.22, 0.25, ndls) * fl;',
    '  col = mix(col, uFrost * (0.25 + 0.75 * ndl), clamp(pow(1.0 - mu, 2.0) * 2.5, 0.0, 1.0) * 0.55 * uQuiet * fl);',
    '  col += uRimCol * uRimI * 0.5 * pow(1.0 - mu, 2.5) * clamp(dot(Nw, normalize(uRimDir)), 0.0, 1.0) * fl;',
    '  float fz = smoothstep(uFogR.x, uFogR.y, length(vP - cameraPosition)); col = mix(col, uFogC, fz * uFogK * fl);',
    '  if (uArtOn > 0.5 && uMapK > 0.0){',                                     // the 2D art, projected straight down
    '    vec2 a = (vP.xz - uArtRect.xy) / uArtRect.z;',
    '    if (a.x >= 0.0 && a.y >= 0.0 && a.x < uArtPx.x && a.y < uArtPx.y){',
    '      vec4 art = texture2D(uArt, vec2((floor(a.x) + 0.5) / uArtPx.x, 1.0 - (floor(a.y) + 0.5) / uArtPx.y));',
    '      col = mix(col, art.rgb, uMapK * step(0.5, art.a)); } }',
    '  col *= uDim;',
    '  gl_FragColor = vec4(col, 1.0);',
    '  #include <colorspace_fragment>',
    '}'
  ].join('\n');
  // the halo: the glow of the air round the disc, drawn on the shell's far side; from the point of the view ray nearest
  // the centre (how high the ray passes over the ground), a chord of depth0 exp(-alt/H) sqrt(2 pi b / H)
  var REAL_HALO_FRAG = REAL_COMMON + [
    'uniform vec3 uC; uniform float uR; uniform vec3 uL; uniform float uK; uniform vec3 uFogC; uniform vec2 uFogR;',
    'varying vec3 vP;',
    'void main(){ vec3 o = (cameraPosition - uC) / uR, d = normalize(vP - cameraPosition);',
    '  vec3 p = o + d * max(-dot(o, d), 0.0); float b = length(p), h = uAir.x, alt = b - 1.0;',
    '  if (alt < 0.0){ gl_FragColor = vec4(0.0, 0.0, 0.0, 1.0); return; }',             // the ray meets the ground: the surface pass has it
    '  vec3 l = normalize(uL); float ndl = dot(p / b, l), dens = exp(-alt / h);',
    '  float tau = uAirSky.w * dens * sqrt(2.0 * PI * b / h), k = sqrt(2.0 * h / PI);',
    '  vec3 tsun = exp(-uAirSky.rgb * (uAirSky.w * dens * (1.0 + k) / (max(ndl, 0.0) + k)));',
    '  vec3 glow = vec3(1.0, 0.985, 0.96) * uAir.z * tsun * rPhase(dot(l, d)) * (1.0 - exp(-uAirSky.rgb * tau)) * smoothstep(-0.32, 0.22, ndl);',
    '  float edge = 1.0 - smoothstep(uAir.y - 1.5 * h, uAir.y, b), fz = smoothstep(uFogR.x, uFogR.y, length(vP - cameraPosition));',
    '  gl_FragColor = vec4(glow * edge * uK * (1.0 - fz), 1.0);',
    '  #include <colorspace_fragment>',
    '}'
  ].join('\n');
  // rings: the reference shelf, the shared station ring (radial bands from the palette, the sun side brighter)
  var RING_VERT = 'varying vec3 vP; varying vec2 vUv; void main(){ vUv = uv; vec4 wp = modelMatrix * vec4(position, 1.0); vP = wp.xyz; gl_Position = projectionMatrix * viewMatrix * wp; }';
  var RING_FRAG = GL_HASH + [
    'uniform vec3 uC; uniform float uR0; uniform float uR1; uniform vec3 uK0; uniform vec3 uK1; uniform vec3 uK2; uniform vec3 uL; uniform float uOp; uniform float uLamps; uniform float uT;',
    'varying vec3 vP; varying vec2 vUv;',
    'void main(){ float r = length(vP - uC), f = clamp((r - uR0) / (uR1 - uR0), 0.0, 1.0);',
    '  float b = cmVN(vec3(f * 18.0, 0.5, 0.5), 3); vec3 col = b < 0.4 ? uK0 : (b < 0.7 ? uK1 : uK2);',
    '  float lit = 0.45 + 0.55 * smoothstep(-0.2, 0.4, dot(normalize(vP - uC), normalize(uL)));',
    '  float edge = smoothstep(0.0, 0.06, f) * (1.0 - smoothstep(0.94, 1.0, f)); float gapc = step(0.32, abs(fract(f * 3.0) - 0.5)) * 0.25 + 0.75;',
    '  vec3 c = col * lit * gapc; if (uLamps > 0.5){ float a = atan(vP.z - uC.z, vP.x - uC.x) + uT * 0.15; float lm = step(0.97, fract(a * 12.0 / 6.2831853)) * step(0.45, f) * step(f, 0.62); c += vec3(1.0, 0.9, 0.7) * lm; }',
    '  gl_FragColor = vec4(c, edge * uOp);',
    '  #include <colorspace_fragment>',
    '}'
  ].join('\n');
  // a condition ring facing the eye: the working arc (filling with progress), the empty folder's dashed outline
  var CRING_FRAG = [
    'uniform vec3 uCol; uniform vec3 uDim; uniform float uFill; uniform float uDash; uniform float uOp; varying vec2 vUv;',
    'void main(){ vec2 q = vUv - 0.5; float a = atan(q.x, q.y); float f = (a < 0.0 ? a + 6.2831853 : a) / 6.2831853;',
    '  vec3 c = f <= uFill ? uCol : uDim; float on = 1.0; if (uDash > 0.5) on = step(0.5, fract(f * 28.0));',
    '  if (on < 0.5) discard; gl_FragColor = vec4(c, uOp);',
    '  #include <colorspace_fragment>',
    '}'
  ].join('\n');
  var CRING_VERT = 'varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }';
  // the haze of a mostly rebuildable folder, as the 2D draws it (ui/kinds.js: a soft grey glow round the body, brightest
  // near 1.3 r, and a dashed ring at 1.28 r): here a smooth glow on a shell that shows only toward its rim (no latitude
  // bands: on a close planet they read as scanlines over its face); the dashed ring is its own camera-facing ring
  var HAZE_FRAG = [
    'uniform vec3 uC; uniform float uOp; varying vec3 vN; varying vec3 vP;',
    'void main(){ vec3 V = normalize(cameraPosition - vP); float e = pow(1.0 - abs(dot(normalize(vN), V)), 2.4);',
    '  gl_FragColor = vec4(vec3(0.6, 0.66, 0.74) * e * uOp, 1.0);',
    '  #include <colorspace_fragment>',
    '}'
  ].join('\n');
  // the flare: a few sprites on the sun-to-centre line, one Points draw, shapes in the shader (glow, ring, hex, disc)
  var FLARE_VERT = [
    'attribute float aK; attribute float aSize; attribute vec3 aCol; attribute float aShape; attribute float aAlpha; uniform vec2 uSun; uniform float uDpr; uniform float uOp;',
    'varying vec3 vCol; varying float vShape;',
    'void main(){ vec2 p = uSun * (1.0 - 2.0 * aK); gl_Position = vec4(p, 0.0, 1.0); gl_PointSize = aSize * uDpr; vCol = aCol * uOp * aAlpha; vShape = aShape; }'
  ].join('\n');
  var FLARE_FRAG = [
    'varying vec3 vCol; varying float vShape;',
    'void main(){ vec2 q = (gl_PointCoord - 0.5) * 2.0; float r = length(q), a = 0.0;',
    '  if (vShape < 0.5) a = pow(max(0.0, 1.0 - r), 2.2);',
    '  else if (vShape < 1.5) a = smoothstep(0.82, 0.9, r) * (1.0 - smoothstep(0.9, 1.0, r));',
    '  else if (vShape < 2.5){ float h = max(abs(q.x) * 0.866 + abs(q.y) * 0.5, abs(q.y)); a = (1.0 - smoothstep(0.84, 0.92, h)) * (0.25 + 0.5 * smoothstep(0.3, 0.88, h)); }',
    '  else if (vShape < 3.5) a = smoothstep(0.55, 0.86, r) * (1.0 - smoothstep(0.86, 1.0, r)) * 0.8 + (1.0 - smoothstep(0.0, 0.9, r)) * 0.12;',
    '  else a = exp(-pow(q.y * 16.0, 2.0)) * pow(max(0.0, 1.0 - abs(q.x)), 1.5);',
    '  if (a <= 0.002) discard; gl_FragColor = vec4(vCol * a, 1.0);',
    '  #include <colorspace_fragment>',
    '}'
  ].join('\n');

  // ------------------------------------------------------------------ session state
  var T = null;
  var BAKES = {};          // surface key -> {data: Uint8Array, w, h, rows} (kept for the page's life: a re-entry reuses them)
  var DEMO = null;

  function audit(ctx) { return ctx.audit; }
  function own(x) { T.own.push(x); return x; }
  function add(parent, o) { parent.add(o); return o; }
  function lin(THREE, hex) { return new THREE.Color(hex); }
  function vec3c(THREE, hex) { var c = hexRgb(hex); return new THREE.Vector3(c[0] / 255, c[1] / 255, c[2] / 255); }   // display-space colour
  function phoneQ(ctx) { return !!(ctx.quality && ctx.quality.phone); }
  function shader(THREE, opts) { var m = new THREE.ShaderMaterial(opts); keep(m); return m; }
  // every material this module makes is its own: it disposes them (once, at dispose) and marks them so the core's shader
  // warm-up never disposes one again when a leave comes before the warm-up's end
  function mark(m) { if (m && m.userData) m.userData.cmOwner = 'space'; return m; }
  function keep(m) { mark(m); T.mats.push(m); return m; }

  // ---- the sky: the recipe's nebula baked into a cube (one face a frame on a phone), coloured live by region
  function buildSky(scene, world, ctx) {
    var THREE = ctx.THREE, ph = phoneQ(ctx), R = world.sky && world.sky.layers ? world.sky : fallbackRecipe((world.planets || []).length * 97 + 7);
    var size = ph ? 512 : 1024, lobes = lobesOf(R), V3 = function (a) { return new THREE.Vector3(a[0], a[1], a[2]); };
    var rt = new THREE.WebGLCubeRenderTarget(size, { generateMipmaps: true, minFilter: THREE.LinearMipmapLinearFilter, magFilter: THREE.LinearFilter, type: THREE.UnsignedByteType });
    var bake = mark(new THREE.ShaderMaterial({ uniforms: {
      uC0: { value: V3(lobes[0].dir) }, uC1: { value: V3(lobes[1].dir) }, uX0: { value: V3(lobes[0].axis) }, uX1: { value: V3(lobes[1].axis) },
      uS0: { value: new THREE.Vector4(lobes[0].sx, lobes[0].sy, lobes[0].fan, lobes[0].gain) }, uS1: { value: new THREE.Vector4(lobes[1].sx, lobes[1].sy, lobes[1].fan, lobes[1].gain) },
      uSeed: { value: ((R.seed >>> 0) % 1000) / 37.0 }, uWarp: { value: 1 } },
      vertexShader: BAKE_VERT, fragmentShader: BAKE_FRAG, side: THREE.BackSide, depthTest: false, depthWrite: false }));
    var bakeScene = new THREE.Scene(), box = new THREE.Mesh(new THREE.BoxGeometry(2, 2, 2), bake);
    bakeScene.add(box);
    var cam = new THREE.CubeCamera(0.1, 4, rt);
    var fc = hexRgb(R.base || '#050508');
    var m = shader(THREE, { uniforms: {
      uCube: { value: rt.texture }, uHas: { value: 0 }, uGain: { value: 1.0 },
      uBaseA: { value: new THREE.Vector3() }, uA0: { value: new THREE.Vector3() }, uA1: { value: new THREE.Vector3() }, uA2: { value: new THREE.Vector3() }, uA3: { value: new THREE.Vector3() }, uNebA: { value: 1 }, uDustA: { value: new THREE.Vector3() },
      uBaseB: { value: new THREE.Vector3() }, uB0: { value: new THREE.Vector3() }, uB1: { value: new THREE.Vector3() }, uB2: { value: new THREE.Vector3() }, uB3: { value: new THREE.Vector3() }, uNebB: { value: 1 }, uDustB: { value: new THREE.Vector3() },
      uMix: { value: 0 }, uGrade: { value: new THREE.Vector4(0, 0, 0, 0) }, uFlat: { value: 0 }, uFlatC: { value: new THREE.Vector3(fc[0] / 255, fc[1] / 255, fc[2] / 255) } },
      vertexShader: SKY_VERT, fragmentShader: SKY_FRAG, side: THREE.BackSide, depthWrite: false, depthTest: false, dithering: true });
    var sky = new THREE.Mesh(new THREE.BoxGeometry(2, 2, 2), m);
    sky.renderOrder = -30; sky.frustumCulled = false; sky.userData.noStandIn = true; sky.name = 'cm3d-sky';
    add(T.root, sky);
    T.sky = { mesh: sky, mat: m, rt: rt, bake: bake, bakeScene: bakeScene, box: box, cam: cam, faces: 0, size: size, recipe: R, seed: R.seed, lobes: lobes,
      bright: lobes[0].dir.slice(), region: null, from: null, fade: 1, alertMix: 0, plannedMix: 0, alert: false, planned: false };
    own(rt); own(bake); own(box.geometry); own(sky.geometry);
  }
  // a region's palette into one of the sky's two slots: base, four ramp colours (deep, mid, hot, core; a three-colour region
  // repeats its core), the dust colour (the base pushed toward deep violet, or the skin's), whether it has a nebula at all
  function setPal(u, pre, pal) {
    var b = hexRgb(pal.base), c = pal.colours.map(hexRgb), dc = pal.dust ? hexRgb(pal.dust) : mixRgb(b, [14, 4, 16], 0.6);
    u['uBase' + pre].value.set(b[0] / 255, b[1] / 255, b[2] / 255);
    var at = c.length >= 4 ? [0, 1, 2, 3] : c.length === 3 ? [0, 1, 1, 2] : [0, 0, 1, 1];      // a three-colour region: its core only at the top
    at.forEach(function (j, i) { var x = c[Math.min(j, c.length - 1)]; u['u' + pre + i].value.set(x[0] / 255, x[1] / 255, x[2] / 255); });
    u['uDust' + pre].value.set(dc[0] / 255, dc[1] / 255, dc[2] / 255);
    u['uNeb' + pre].value = pal.nebula ? 1 : 0;
  }
  // a cube face or all six this frame (the bake: progressive on a phone, the mobile GPU watchdog)
  function bakeSkyStep(ctx, all) {
    var s = T.sky; if (!s || s.faces >= 6) return;
    var r = ctx.renderer, keep = r.getRenderTarget(), keepAuto = r.autoClear;
    var cams = s.cam.children;
    if (s.cam.coordinateSystem !== r.coordinateSystem) { s.cam.coordinateSystem = r.coordinateSystem; s.cam.updateCoordinateSystem(); }
    s.cam.updateMatrixWorld(true);
    var gen = s.rt.texture.generateMipmaps; s.rt.texture.generateMipmaps = false;
    r.autoClear = true;
    var n = all ? 6 : 1;
    while (n-- > 0 && s.faces < 6) {
      var i = s.faces;
      if (i === 5) s.rt.texture.generateMipmaps = gen;
      r.setRenderTarget(s.rt, i); r.render(s.bakeScene, cams[i]); s.faces++;
    }
    s.rt.texture.generateMipmaps = gen;
    r.setRenderTarget(keep); r.autoClear = keepAuto;
    if (s.faces >= 6) s.mat.uniforms.uHas.value = 1;
  }
  // the region: the purpose of the planet nearest the ship; trouble (5 or more problems there) grades it red over 2 s, back over
  // 2.5 s once it falls under 5; a selected planned workflow grades it toward the planning tint
  function nearestPlanet(world) {
    var sp = world.ship || { x: 0, y: 0 }, best = null, bd = Infinity;
    (world.planets || []).forEach(function (p) { var d = Math.hypot(p.x - sp.x, p.y - sp.y) - p.r; if (d < bd) { bd = d; best = p; } });
    return best;
  }
  function plannedSelected(world) {
    var id = world.selectedWorkflow; if (!id) return false;
    return (world.workflows || []).some(function (w) { return w && w.id === id && w.state === 'planned'; });
  }
  function updateSky(world, ctx, dt) {
    var s = T.sky; if (!s) return;
    var THREE = ctx.THREE, u = s.mat.uniforms, table = skyTable(ctx.skin3d), near = nearestPlanet(world);
    var key = near && table.regions[near.purpose] ? near.purpose : 'unsorted';
    if (s.region == null) { s.region = key; setPal(u, 'A', regionPalette(ctx.skin3d, key)); setPal(u, 'B', regionPalette(ctx.skin3d, key)); u.uMix.value = 0; s.fade = 1; }
    else if (key !== s.region && s.fade >= 1) { setPal(u, 'B', regionPalette(ctx.skin3d, key)); s.region = key; s.fade = 0; u.uMix.value = 0; }
    if (s.fade < 1) {                                    // a uniform cross-fade over 2 s, never a re-bake
      s.fade = Math.min(1, s.fade + dt / 2); u.uMix.value = s.fade;
      if (s.fade >= 1) { setPal(u, 'A', regionPalette(ctx.skin3d, s.region)); u.uMix.value = 0; }
    }
    var probs = near && near.counts ? near.counts.problems || 0 : 0;
    s.alert = probs >= 5; s.planned = plannedSelected(world);
    var am = table.alertMix || 0.25, pm = table.plannedMix || 0.15;
    if (s.alert) s.alertMix = Math.min(am, s.alertMix + am * dt / 2); else s.alertMix = Math.max(0, s.alertMix - am * dt / 2.5);
    if (s.planned && !s.alert) s.plannedMix = Math.min(pm, s.plannedMix + pm * dt / 2); else s.plannedMix = Math.max(0, s.plannedMix - pm * dt / 2.5);
    var gc = hexRgb(s.alertMix >= s.plannedMix ? table.alert : table.planned), ga = Math.max(s.alertMix, s.plannedMix);
    u.uGrade.value.set(gc[0] / 255, gc[1] / 255, gc[2] / 255, ga);
    u.uFlat.value = T.mapK;                              // the first frame: the 2D backdrop's faint veil
    var pal = regionPalette(ctx.skin3d, s.region), tint = rgbHex(mixRgb(hexRgb(pal.colours[1]), hexRgb(table.alert), s.alertMix * 2));
    ctx.sky = { region: s.region, tint: tint, alert: s.alert, mix: Math.round(s.alertMix * 1000) / 1000, planned: s.planned };
    var A = audit(ctx);
    A.sky.region = s.region; A.sky.alert = s.alert; A.sky.mix = ctx.sky.mix; A.sky.tint = tint; A.sky.planned = s.planned;
    A.scene.sky = { seed: s.seed, faces: s.faces, stars: T.stars ? T.stars.count : 0, size: s.size, region: s.region, layers: Math.min(2, (s.recipe.layers || []).length),
      palette: pal.colours.slice(), base: pal.base, nebula: pal.nebula };
  }

  // ---- stars at infinity (spacekit), in one draw: ~4,000 named stars (one in eight bright with a halo, one in ten warm), a far
  // layer of dim 1 px dust stars that crowds the nebula's lobes (the comps: stars crowd the gas, so the sky never reads flat),
  // and a few four-point sparkles near the lobes; no twinkle
  function buildStars(scene, world, ctx) {
    var THREE = ctx.THREE, t = skyTable(ctx.skin3d).stars || NEUTRAL.sky3d.stars, ph = phoneQ(ctx), n = 4000, far = ph ? 2500 : 6000, sparks = 5;
    var R = T.sky ? T.sky.recipe : fallbackRecipe(7), lobes = T.sky ? T.sky.lobes : lobesOf(R);
    var s = (R.seed >>> 0) || 7, rnd = function () { s = (s + 0x6D2B79F5) | 0; var x = Math.imul(s ^ s >>> 15, 1 | s); x = x + Math.imul(x ^ x >>> 7, 61 | x) ^ x; return ((x ^ x >>> 14) >>> 0) / 4294967296; };
    var N = n + far + sparks, pos = new Float32Array(N * 3), col = new Float32Array(N * 3), size = new Float32Array(N), kind = new Float32Array(N);
    var cool = hexRgb(t.cool), warm = hexRgb(t.warm), bright = hexRgb(t.bright || t.cool), px = t.px || [2, 4], dim = hexRgb(t.far || '#9FB4FF');
    var we = t.warmEvery || 10, be = t.brightEvery || 8, i = 0, guard = 0;
    var dirAt = function () { var u = rnd() * 2 - 1, a = rnd() * TAU, r = Math.sqrt(1 - u * u); return [Math.cos(a) * r, u, Math.sin(a) * r]; };
    for (var k = 0; k < n; k++, i++) {
      var d = dirAt(); pos.set(d, i * 3);
      var isB = k % be === 0, c = isB ? bright : (k % we === 3 ? warm : cool), lum = isB ? 1 : 0.4 + 0.45 * Math.pow(rnd(), 2);
      col[i * 3] = c[0] / 255 * lum; col[i * 3 + 1] = c[1] / 255 * lum; col[i * 3 + 2] = c[2] / 255 * lum;
      size[i] = isB ? px[1] + rnd() * 1.5 : px[0] * (0.75 + 0.35 * rnd()); kind[i] = isB ? 1 : 0;
    }
    while (i < n + far && guard++ < far * 30) {
      var e = dirAt(), mk = lobeMask(e, lobes);
      if (rnd() > 0.5 + 0.5 * mk) continue;
      pos.set(e, i * 3); var b2 = (0.22 + 0.4 * rnd()) * (0.7 + 0.5 * mk);
      col[i * 3] = dim[0] / 255 * b2; col[i * 3 + 1] = dim[1] / 255 * b2; col[i * 3 + 2] = dim[2] / 255 * b2; size[i] = 1 + rnd() * 0.6; kind[i] = 0; i++;
    }
    for (var q = 0; q < sparks; q++, i++) {
      var l = lobes[q % 2], o = [(rnd() - 0.5) * 0.9, (rnd() - 0.5) * 0.6, (rnd() - 0.5) * 0.9], v = [l.dir[0] + o[0], l.dir[1] + o[1], l.dir[2] + o[2]], vl = Math.hypot(v[0], v[1], v[2]);
      pos.set([v[0] / vl, v[1] / vl, v[2] / vl], i * 3); col.set([0.87 * 0.8, 0.91 * 0.8, 1.0 * 0.8], i * 3); size[i] = 18 + rnd() * 26; kind[i] = 2;
    }
    var geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(pos.subarray(0, i * 3), 3)); geo.setAttribute('aCol', new THREE.BufferAttribute(col.subarray(0, i * 3), 3));
    geo.setAttribute('aSize', new THREE.BufferAttribute(size.subarray(0, i), 1)); geo.setAttribute('aKind', new THREE.BufferAttribute(kind.subarray(0, i), 1));
    var halo = hexRgb(t.halo || t.cool);
    var m = shader(THREE, { uniforms: { uDpr: { value: 1 }, uFade: { value: 1 }, uHalo: { value: new THREE.Vector3(halo[0] / 255 * 0.35, halo[1] / 255 * 0.35, halo[2] / 255 * 0.35) } },
      vertexShader: STAR_VERT, fragmentShader: STAR_FRAG, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending });
    var pts = new THREE.Points(geo, m); pts.frustumCulled = false; pts.renderOrder = -20; pts.name = 'cm3d-stars';
    add(T.root, pts); own(geo);
    T.stars = { pts: pts, mat: m, count: n, far: i - n - sparks, sparkles: sparks };
  }
  // ---- dust: motes round the eye streaking with the flight
  function buildDust(scene, world, ctx) {
    var THREE = ctx.THREE, n = phoneQ(ctx) ? 220 : 520, box = 900, seed = new Float32Array(n * 6), end = new Float32Array(n * 2);
    var s = 12345, rnd = function () { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296; };
    for (var i = 0; i < n; i++) { var x = rnd() * box, y = rnd() * box, z = rnd() * box; seed.set([x, y, z, x, y, z], i * 6); end[i * 2] = 0; end[i * 2 + 1] = 1; }
    var geo = new THREE.BufferGeometry(), pos = new Float32Array(n * 6);
    geo.setAttribute('position', new THREE.BufferAttribute(pos, 3)); geo.setAttribute('aSeed', new THREE.BufferAttribute(seed, 3)); geo.setAttribute('aEnd', new THREE.BufferAttribute(end, 1));
    var dc = hexRgb(skyTable(ctx.skin3d).dust || '#9AB4E0');
    var m = shader(THREE, { uniforms: { uEye: { value: new THREE.Vector3() }, uVel: { value: new THREE.Vector3() }, uBox: { value: box }, uStreak: { value: 0.05 },
      uCol: { value: new THREE.Vector3(dc[0] / 255, dc[1] / 255, dc[2] / 255) }, uAlpha: { value: 0 } },
      vertexShader: DUST_VERT, fragmentShader: DUST_FRAG, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, dithering: true });
    var lines = new THREE.LineSegments(geo, m); lines.frustumCulled = false; lines.name = 'cm3d-dust';
    add(T.root, lines); own(geo);
    T.dust = { lines: lines, mat: m };
  }
  // ---- the sun: neutral = spiral disc + HDR core + corona; a skin may ask for a photosphere sphere with orbit rings instead
  function glowTexture(THREE, inner, outer, stops) {
    var c = g.document.createElement('canvas'); c.width = c.height = 128;
    var x = c.getContext('2d'), gr = x.createRadialGradient(64, 64, 0, 64, 64, 64);
    gr.addColorStop(0, inner); gr.addColorStop(stops || 0.22, inner); gr.addColorStop(1, outer); x.fillStyle = gr; x.fillRect(0, 0, 128, 128);
    var t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; own(t); return t;
  }
  // a radial glow with optional thin rays (stops [[offset, css colour]]), as a texture the session owns
  function glowTex(THREE, stops, o) {
    o = o || {}; var N = o.size || 256, c = g.document.createElement('canvas'); c.width = c.height = N;
    var x = c.getContext('2d'), gr = x.createRadialGradient(N / 2, N / 2, 0, N / 2, N / 2, N / 2);
    stops.forEach(function (st) { gr.addColorStop(st[0], st[1]); });
    x.fillStyle = gr; x.beginPath(); x.arc(N / 2, N / 2, N / 2, 0, TAU); x.fill();
    if (o.spikes) {
      x.save(); x.translate(N / 2, N / 2); x.globalCompositeOperation = 'lighter';
      for (var i = 0; i < o.spikes; i++) {
        x.save(); x.rotate((o.spikeRot || 0) + i * TAU / o.spikes);
        var sg = x.createLinearGradient(0, 0, N / 2, 0), a = o.spikeA || 0.6, w = N * (o.spikeW || 0.018);
        sg.addColorStop(0, 'rgba(255,255,255,' + a + ')'); sg.addColorStop(0.35, 'rgba(255,255,255,' + a * 0.35 + ')'); sg.addColorStop(1, 'rgba(255,255,255,0)');
        x.fillStyle = sg; x.beginPath(); x.moveTo(0, -w); x.lineTo(N / 2, 0); x.lineTo(0, w); x.closePath(); x.fill(); x.restore();
      }
      x.restore();
    }
    var t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; own(t); return t;
  }
  function rgba(hex, a) { var c = hexRgb(hex); return 'rgba(' + c[0] + ',' + c[1] + ',' + c[2] + ',' + a + ')'; }
  function buildSun(scene, world, ctx) {
    var THREE = ctx.THREE, st = world.star || { x: 0, y: 0, r: 150, load: 0 }, sun = skyTable(ctx.skin3d).sun || NEUTRAL.sky3d.sun, ph = phoneQ(ctx);
    var grp = new THREE.Group(); grp.position.set(st.x, 0, st.y); grp.name = 'cm3d-sun'; add(T.root, grp);
    var S = { grp: grp, r: st.r, kind: sun.kind || 'disc', calls: null, frame: -1, layers: [] };
    if (S.kind === 'photosphere') {
      var pm = shader(THREE, { uniforms: { uSeed: { value: 1 }, uScale: { value: 1.2 }, uBg: { value: lin(THREE, sun.edge || '#E0301A') }, uFg: { value: lin(THREE, sun.churn || '#F48826') },
        uRimC: { value: lin(THREE, sun.rim || '#E0301A') } }, vertexShader: PHOTO_VERT, fragmentShader: PHOTO_FRAG });
      var sph = new THREE.Mesh(new THREE.SphereGeometry(st.r, ph ? 24 : 48, ph ? 16 : 32), pm); own(sph.geometry);
      sph.userData.uid = 'star'; sph.userData.kind = 'star'; sph.userData.baseColor = 0xf48826; grp.add(sph); S.photo = pm; S.mesh = sph;
      sph.onAfterRender = function (r) { sunDrawn(S, r); };
      S.layers.push('photosphere');
      (sun.rings || [1.6, 2.0]).forEach(function (k) {
        var rg = new THREE.RingGeometry(st.r * k - 1.2, st.r * k + 1.2, 128, 1); own(rg);
        var rm = new THREE.MeshBasicMaterial({ color: lin(THREE, sun.ringColor || '#A04030'), side: THREE.DoubleSide, transparent: true, opacity: 0.85, depthWrite: false });
        keep(rm); var ring = new THREE.Mesh(rg, rm); ring.rotation.x = -Math.PI / 2; ring.userData.noStandIn = true; grp.add(ring);
      });
      S.layers.push('rings');
    } else {
      var n = ph ? 6000 : 24000, idx = new Float32Array(n); for (var i = 0; i < n; i++) idx[i] = i;
      var geo = new THREE.BufferGeometry(); geo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(n * 3), 3)); geo.setAttribute('aIndex', new THREE.BufferAttribute(idx, 1));
      own(geo);
      var dm = shader(THREE, { uniforms: { uCount: { value: n }, uT: { value: 0 }, uR: { value: st.r }, uBH: { value: sunShell(st.r, st.load) }, uArms: { value: 2 }, uTurns: { value: 1.15 },
        uC: { value: new THREE.Vector3(st.x, 0, st.y) }, uU: { value: new THREE.Vector3(1, 0, 0) }, uV: { value: new THREE.Vector3(0, 0, 1) }, uN: { value: new THREE.Vector3(0, 1, 0) },
        uPx: { value: 800 }, uDpr: { value: 1 }, uCore: { value: lin(THREE, sun.core) }, uMid: { value: lin(THREE, sun.mid) }, uEdge: { value: lin(THREE, sun.edge) } },
        vertexShader: DISC_VERT, fragmentShader: DISC_FRAG, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending });
      var disc = new THREE.Points(geo, dm); disc.frustumCulled = false; disc.name = 'cm3d-sun-disc'; disc.userData.uid = 'star';
      disc.onAfterRender = function (r) { sunDrawn(S, r); };
      add(T.root, disc); S.disc = disc; S.dm = dm; S.layers.push('disc', 'shell');
      // the HDR core (nebreck Sun.ts): a white-hot sprite over the shell, sized with it, so a busy machine's sun swells
      var core = new THREE.Sprite(new THREE.SpriteMaterial({ map: glowTexture(THREE, 'rgba(255,250,236,1)', 'rgba(255,214,150,0)', 0.3), blending: THREE.AdditiveBlending,
        depthWrite: false, transparent: true, dithering: true }));
      keep(core.material); core.renderOrder = 2; grp.add(core); S.core = core; S.layers.push('core');
      // an invisible sphere the size of the sun for picking and the flare's own test
      var hit = new THREE.Mesh(new THREE.SphereGeometry(st.r * 0.55, 16, 12), new THREE.MeshBasicMaterial({ visible: false }));
      own(hit.geometry); keep(hit.material); hit.userData.uid = 'star'; hit.userData.kind = 'star'; hit.userData.noStandIn = true; grp.add(hit);
    }
    // the corona (6 r, sixteen soft rays: the comps' sun) and a wide low glare (8 r), both additive sprites in the skin's colour
    var cc = hexRgb(sun.corona || '#FF9A3C'), rays = sun.rays != null ? sun.rays : 16;
    var corona = new THREE.Sprite(new THREE.SpriteMaterial({ map: glowTex(THREE, [[0, 'rgba(255,240,210,1)'], [S.kind === 'disc' ? 0.05 : 0.12, 'rgba(255,200,120,0.85)'], [0.3, 'rgba(' + cc.join(',') + ',0.3)'],
      [0.6, 'rgba(' + cc.join(',') + ',0.08)'], [1, 'rgba(' + cc.join(',') + ',0)']], { size: 512, spikes: rays, spikeA: S.kind === 'disc' ? 0.22 : 0.32, spikeW: 0.012 }), blending: THREE.AdditiveBlending,
      depthWrite: false, transparent: true, dithering: true }));
    keep(corona.material); corona.scale.set(st.r * 6, st.r * 6, 1); corona.renderOrder = 1; grp.add(corona); S.corona = corona; S.layers.push('corona');
    var glare = new THREE.Sprite(new THREE.SpriteMaterial({ map: glowTex(THREE, [[0, 'rgba(255,230,190,0.5)'], [0.25, 'rgba(255,170,90,0.14)'], [1, 'rgba(255,120,60,0)']], { size: 256 }),
      blending: THREE.AdditiveBlending, depthWrite: false, transparent: true, dithering: true, opacity: 0.4 }));
    keep(glare.material); glare.scale.set(st.r * 8, st.r * 8, 1); glare.renderOrder = 1; grp.add(glare); S.glare = glare; S.layers.push('glare');
    S.coronaOp = corona.material.opacity; S.glareOp = glare.material.opacity;
    T.sun = S;
  }
  // the corona and the glare: the entry's ramp (ctx.glareK, 0 through the dive and the orbit's first half), and never wider
  // than a fixed angle of the view. The comps' sun sits 20 radii away; the owner's map keeps planets 4-5 sun radii out, where
  // a 6 r corona and an 8 r glare would fill the screen: the corona reaches at most max(14 deg, 1.5 x the disc's angular
  // radius), the glare max(20 deg, 2 x), each under 40 / 44 deg, so the eye is never inside either sprite
  function sunGlow(r, d) {
    var a = Math.asin(Math.min(1, r / Math.max(d, r))), c = Math.min(40 * D2R, Math.max(14 * D2R, 1.5 * a)), gl = Math.min(44 * D2R, Math.max(20 * D2R, 2 * a));
    return { corona: Math.min(r * 6, 2 * d * Math.tan(c)), glare: Math.min(r * 8, 2 * d * Math.tan(gl)) };
  }
  // ctx.coronaK (the camera lane's: up over the dive) scales the corona on its own; the glare follows ctx.glareK
  function glowUpdate(S, st, ctx) {
    var k = ctx.glareK == null ? 1 : clamp(Number(ctx.glareK) || 0, 0, 1), d = ctx.camera.position.distanceTo(S.grp.position), gw = sunGlow(st.r, d);
    var ck = ctx.coronaK == null ? k : Math.max(k, clamp(Number(ctx.coronaK) || 0, 0, 1));
    S.corona.scale.set(gw.corona, gw.corona, 1); S.glare.scale.set(gw.glare, gw.glare, 1);
    S.corona.material.opacity = S.coronaOp * ck; S.glare.material.opacity = S.glareOp * k;
    S.corona.visible = ck > 0.002; S.glare.visible = k > 0.002;
    S.glowK = k; S.coronaK = ck; S.glowPx = { corona: Math.round(gw.corona), glare: Math.round(gw.glare), d: Math.round(d) };
  }
  // the sun body's draws in the frame last drawn (one Points for the disc, one sphere for a photosphere), written as it draws
  function sunDrawn(S, r) {
    var f = r.info.render.frame; if (S.frame !== f) { S.frame = f; S.calls = 0; } S.calls++;
    var A0 = T && T.ctx && T.ctx.audit; if (A0 && A0.scene) { A0.scene.calls = A0.scene.calls || {}; A0.scene.calls.sun = S.calls; }
  }
  function updateSun(world, ctx, dt) {
    var S = T.sun; if (!S) return;
    var st = world.star || { r: 150, load: 0, x: 0, y: 0 }, A = audit(ctx), cam = ctx.camera, dpr = ctx.renderer.getPixelRatio();
    var bh = sunShell(st.r, st.load);
    if (S.dm) {
      var u = S.dm.uniforms; u.uBH.value = bh; u.uR.value = st.r; u.uDpr.value = dpr;
      u.uPx.value = (ctx.renderer.domElement.clientHeight || g.innerHeight || 900) / 2 / Math.tan(cam.fov * D2R / 2);
      if (!world.reduced) u.uT.value += dt * 0.035;
      // the disc faces the eye: a spiral seen whole, on the map from above and from a pilot in flight alike
      var c = new ctx.THREE.Vector3(st.x, 0, st.y), toCam = cam.position.clone().sub(c).normalize(), n = toCam.clone();
      var uu = new ctx.THREE.Vector3(0, 0, 1).cross(n); if (uu.lengthSq() < 1e-6) uu.set(1, 0, 0); uu.normalize();
      var vv = n.clone().cross(uu).normalize(); u.uN.value.copy(n); u.uU.value.copy(uu); u.uV.value.copy(vv);
      if (S.core) S.core.scale.setScalar(bh * 3.0);
    }
    if (S.photo) { if (!world.reduced) S.photo.uniforms.uSeed.value += dt * (0.05 + 0.04 * Math.min(10, Number(st.load) || 0)); }
    glowUpdate(S, st, ctx);
    A.scene.sun = 1; A.scene.sunR = st.r; A.scene.sunBH = Math.round(bh * 1000) / 1000; A.scene.sunLayers = S.layers.slice();
    A.scene.sunGlow = { k: Math.round(S.glowK * 1000) / 1000, ck: Math.round((S.coronaK == null ? S.glowK : S.coronaK) * 1000) / 1000, corona: S.glowPx.corona, glare: S.glowPx.glare, eye: S.glowPx.d };
    A.scene.calls = A.scene.calls || {}; if (S.calls != null) A.scene.calls.sun = S.calls;
    A.scene.bands = T.bandStats || {};
  }

  // ---- the planets
  function rgb01(c) { return [c[0] / 255, c[1] / 255, c[2] / 255]; }
  function planetPurposeCore(surf) { return surf && surf.core ? surf.core : 1; }
  function surfaceKey(surf, w, h) { return surf.key + '|' + w + 'x' + h; }
  // the equirect bake of one planet: four stacked sections (night, terminator, day + height, emissive + its code), from the
  // page's surfaceAt sampler; rows are baked a few at a time under a time budget, the planet the owner aims at first
  function bakeFor(P, w, h) {
    var key = surfaceKey(P.surf, w, h), b = BAKES[key];
    if (!b) { b = BAKES[key] = { data: new Uint8Array(w * h * 4 * 4), w: w, h: h, rows: 0, key: key }; }
    return b;
  }
  function bakeRows(P, budgetMs) {
    var b = P.bake; if (!b || b.rows >= b.h) return true;
    var t0 = (g.performance && g.performance.now) ? g.performance.now() : Date.now(), w = b.w, h = b.h, d = b.data, S = P.surf;
    while (b.rows < h) {
      var j = b.rows, lat = ((j + 0.5) / h - 0.5) * Math.PI;
      for (var i = 0; i < w; i++) {
        var lon = (i + 0.5) / w * TAU, s = S.at(lon, lat);
        for (var sec = 0; sec < 3; sec++) {
          var o = ((sec * h + j) * w + i) * 4, c = s[sec];
          d[o] = c[0]; d[o + 1] = c[1]; d[o + 2] = c[2]; d[o + 3] = sec === 2 ? Math.round(clamp(s[7], 0, 1) * 255) : 255;
        }
        var oe = ((3 * h + j) * w + i) * 4, e = s[3];
        if (e) {
          d[oe] = e[0]; d[oe + 1] = e[1]; d[oe + 2] = e[2];
          d[oe + 3] = s[4] ? 128 + Math.round(clamp(s[5], 0, 1) * 127) : s[6] > 0 ? Math.max(1, Math.round(clamp(s[6], 0, 1) * 100)) : 127;
        } else { d[oe] = d[oe + 1] = d[oe + 2] = 0; d[oe + 3] = 0; }
      }
      b.rows++;
      var now = (g.performance && g.performance.now) ? g.performance.now() : Date.now();
      if (now - t0 > budgetMs) break;
    }
    if (b.rows >= h && !b.tamed) { b.tamed = true; tameBands(b, P); }
    return b.rows >= h;
  }
  // The 2D bake's bands as the 3D surface takes them (the 2D sprite keeps its own look: ui/kinds.js is untouched). The
  // ruled bands (notes: page-white lines over the ocean) came out as broad white stripes at hero size; the comps' planets
  // are painted worlds (COMPS-3D "Planet surface"). Here each band is thinned to the middle third of its rows, broken
  // along its length (about three fifths of the way round stays), and its paint held to BAND_CAP x the ocean beside it;
  // the gas giant's bands (data) keep their width and lose half their contrast toward the column's mean. All under the
  // shader's light and clouds, nothing emissive. The stats go to the audit (scene.bands) for the checks.
  var BAND_CAP = 1.14, BAND_HI = 1.18;
  function lumaOf(d, o) { return 0.2126 * d[o] + 0.7152 * d[o + 1] + 0.0722 * d[o + 2]; }
  function tameBands(b, P) {
    var prof = (P.surf && P.surf.profile) || [], ruled = prof.indexOf('bands') >= 0, gas = prof.indexOf('bands-warp') >= 0;
    if (!ruled && !gas) return;
    var w = b.w, h = b.h, d = b.data, seed = (Number(P.p && P.p.seed) >>> 0) || 1, st = { kind: ruled ? 'ruled' : 'gas', ratio: 0, kept: 0, dropped: 0, bands: 0 };
    var lum = new Float32Array(h), srt = new Float32Array(h), band = new Uint8Array(h);
    for (var sec = 0; sec < 3; sec++) {
      for (var i = 0; i < w; i++) {
        var o0 = (sec * h) * w * 4 + i * 4, j, o;
        if (gas) {                                             // half the contrast: each texel toward the column's mean
          var mr = 0, mg = 0, mb = 0;
          for (j = 0; j < h; j++) { o = o0 + j * w * 4; mr += d[o]; mg += d[o + 1]; mb += d[o + 2]; }
          mr /= h; mg /= h; mb /= h;
          for (j = 0; j < h; j++) { o = o0 + j * w * 4; d[o] = Math.round(d[o] + (mr - d[o]) * 0.5); d[o + 1] = Math.round(d[o + 1] + (mg - d[o + 1]) * 0.5); d[o + 2] = Math.round(d[o + 2] + (mb - d[o + 2]) * 0.5); }
          continue;
        }
        for (j = 0; j < h; j++) { lum[j] = lumaOf(d, o0 + j * w * 4); srt[j] = lum[j]; }
        Array.prototype.sort.call(srt, function (x, y) { return x - y; });
        var base = srt[Math.floor(h * 0.3)] || 1;              // the ocean: the column's lower third of brightness
        for (j = 0; j < h; j++) band[j] = lum[j] > base * BAND_HI ? 1 : 0;
        var lon = i / w * TAU;
        for (j = 0; j < h; j++) {
          if (!band[j]) continue;
          var j0 = j, j1 = j; while (j1 + 1 < h && band[j1 + 1]) j1++;
          var n = j1 - j0 + 1, keep = Math.max(1, Math.floor(n / 3)), jc = (j0 + j1) / 2, lo = jc - keep / 2, hi = jc + keep / 2;
          var up = j0 - 1, dn = j1 + 1, rep = up >= 0 && (dn >= h || (j0 - up) <= (dn - j1)) ? up : dn < h ? dn : -1;   // the nearest ocean row
          var on = Math.sin(lon * 3 + jc * 0.37 + (seed % 97) * 0.1) > -0.2;                                        // about 3/5 of the way round
          if (sec === 2) st.bands++;
          for (var k = j0; k <= j1; k++) {
            o = o0 + k * w * 4;
            if (rep < 0) continue;
            var ro = o0 + rep * w * 4, rl = lumaOf(d, ro);
            if (!on || k < lo || k > hi) { d[o] = d[ro]; d[o + 1] = d[ro + 1]; d[o + 2] = d[ro + 2]; if (sec === 2) st.dropped++; continue; }
            var l = lum[k], cap = Math.max(rl, base) * BAND_CAP, t = l > cap && l > rl ? Math.min(1, (l - cap) / (l - rl)) : 0;
            d[o] = Math.round(d[o] + (d[ro] - d[o]) * t); d[o + 1] = Math.round(d[o + 1] + (d[ro + 1] - d[o + 1]) * t); d[o + 2] = Math.round(d[o + 2] + (d[ro + 2] - d[o + 2]) * t);
            if (sec === 2) { st.kept++; st.ratio = Math.max(st.ratio, lumaOf(d, o) / Math.max(1, rl, base)); }
          }
          j = j1;
        }
      }
    }
    st.ratio = Math.round(st.ratio * 1000) / 1000;
    T.bandStats = T.bandStats || {}; T.bandStats[P.uid] = st;
  }
  // how each purpose's body takes the flight light: clouds (their most opacity; density comes from the work there), an ocean
  // glint, gas-giant streaks, how much fine noise rides over the bake, the share of the outer air
  var LOOK = {
    code: { cloud: 0.6, glint: 0, gas: 0, detail: 0.32, air: 1 }, notes: { cloud: 0.8, glint: 1, gas: 0, detail: 0.26, air: 1 },
    reference: { cloud: 0.3, glint: 0, gas: 0, detail: 0.36, air: 0.7 }, data: { cloud: 0.22, glint: 0, gas: 1, detail: 0.24, air: 0.9 },
    media: { cloud: 0.45, glint: 0, gas: 0, detail: 0.3, air: 1 }, config: { cloud: 0, glint: 0, gas: 0, detail: 0.18, air: 0.55 },
    shared: { cloud: 0, glint: 0, gas: 0, detail: 0.2, air: 0.5 }, archive: { cloud: 0, glint: 0, gas: 0, detail: 0.42, air: 0.35 },
    unsorted: { cloud: 0, glint: 0, gas: 0, detail: 0.42, air: 0.35 }
  };
  function lookOf(P) { return LOOK[(P.surf && P.surf.purpose) || P.p.purpose] || LOOK.unsorted; }
  // the air's colour: the purpose's own (its bright colour toward sky blue), toward the health colour when something there is
  // wrong or waits, grey when the folder is quiet
  function airColour(P) {
    var base = P.surf ? P.surf.atm : [143, 211, 255], h = P.p.health;
    if (h === 'broken' || h === 'waiting') return rgbHex(mixRgb(base, hexRgb(HEALTH[h]), 0.7));
    if (h === 'quiet') return rgbHex(mixRgb(base, hexRgb(HEALTH.quiet), 0.6));
    return rgbHex(base);
  }
  function planetMaterial(THREE, P, ctx) {
    var C = P.surf ? P.surf.palette : [[40, 44, 52], [70, 75, 85], [110, 115, 125], [150, 155, 165], [200, 204, 210]];
    var day = rgb01(C[2]), health = HEALTH[P.p.health] || HEALTH.ok, k = lookOf(P);
    var m = shader(THREE, { uniforms: {
      uSurf: { value: null }, uSurfOn: { value: 0 }, uSurfSize: { value: new THREE.Vector2(1, 1) },
      uArt: { value: null }, uArtOn: { value: 0 }, uArtRect: { value: new THREE.Vector3(0, 0, 1) }, uArtPx: { value: new THREE.Vector2(1, 1) },
      uMapK: { value: 1 }, uPhase: { value: 0 }, uTilt: { value: TILT }, uL: { value: new THREE.Vector3(1, 0.2, 0) },
      uFlat2: { value: new THREE.Color().setRGB(day[0], day[1], day[2], THREE.SRGBColorSpace) },
      uRimCol: { value: lin(THREE, '#8FB4FF') }, uRimI: { value: 0.4 }, uRimDir: { value: new THREE.Vector3(0, 1, 0) },
      uAtm: { value: lin(THREE, airColour(P)) }, uAtmK: { value: (P.surf && P.surf.rim ? 1 : 0.6) * (P.p.health === 'broken' || P.p.health === 'waiting' ? 1.25 : 1) },
      uWork: { value: 0 }, uCity: { value: P.surf && P.surf.city ? 1 : 0 }, uGlow: { value: lin(THREE, P.surf ? rgbHex(P.surf.glow) : '#FFC35C') },
      uHalo: { value: lin(THREE, health) }, uHaloOn: { value: 0 }, uQuiet: { value: P.surf && P.surf.quiet ? 1 : 0 }, uDim: { value: 1 },
      uFrost: { value: lin(THREE, FROST) }, uFogC: { value: new THREE.Color(0, 0, 0) }, uFogR: { value: new THREE.Vector2(3000, 16000) }, uFogK: { value: 1 }, uWinAll: { value: 0 },
      uSeedF: { value: ((Number(P.p.seed) >>> 0) % 997) / 37.3 }, uCloud: { value: 0.2 }, uCloudOp: { value: 0 }, uCloudSpin: { value: 0 },
      uGlint: { value: k.glint }, uGas: { value: k.gas }, uDetail: { value: k.detail } },
      vertexShader: PLANET_VERT, fragmentShader: PLANET_FRAG });
    return m;
  }
  // ---- the realistic planets (a skin's planets3d manifest): which planets have baked maps, their
  // material and halo, the maps' loading (the sky map first, then the planets nearest the eye, one at a time, each map
  // decoded off the page's thread by createImageBitmap and cut into a cube's six faces), the close-range noise volume
  // (built in slices between frames). Maps come from the bundle's directory, live or static. A failed
  // map or a skin without planets3d keeps the painted planets (the surfaceAt bake), which never runs for a planet that
  // has maps. A phone takes every map at most 256 texels a face (the albedo; the relief and sky at most 128 and 256).
  var REAL_CAP = { a: 256, n: 128, sky: 256 };
  function pickWorld(m, planet) {
    if (!m || !planet) return null;
    // Private manifests retain exact uid selection. Public libraries contain no folder ids.
    if (m.planets) return m.planets[planet.uid] || null;
    var kind = planet.purpose || planet.kind || 'unsorted';
    kind = m.aliases && m.aliases[kind] || kind;
    var list = m.kinds && (m.kinds[kind] || m.kinds.unsorted);
    if (!Array.isArray(list) || !list.length) return null;
    var h = 0x811c9dc5, uid = String(planet.uid == null ? '' : planet.uid);
    for (var i = 0; i < uid.length; i++) { h ^= uid.charCodeAt(i); h = Math.imul(h, 0x01000193); }
    return list[(h >>> 0) % list.length];
  }
  function realSpec(ctx, planet) {
    var m = ctx.skin3d && ctx.skin3d.planets3d, p = pickWorld(m, planet);
    if (!p || !p.a || !p.look || !m.sky) return null;
    if (!g.location || !/^(https?|file):$/.test(g.location.protocol) || typeof g.fetch !== 'function' || typeof g.createImageBitmap !== 'function') return null;
    if (ctx.debug && ctx.debug.paintedPlanets) return null;                  // __3dDebug: the painted planets, for a check
    return p;
  }
  // one map: fetched, decoded with its alpha as stored (the lights) and no colour conversion, cut into six faces
  function loadCube(THREE, name, srgb, cap) {
    var opts = { premultiplyAlpha: 'none', colorSpaceConversion: 'none' };
    var url = (CM3D.mapBase || '/vendor/') + name;
    var bytes = g.location.protocol === 'file:' ? new Promise(function (resolve, reject) {
      var script = g.document.createElement('script'); script.src = url + '.js';
      script.onload = function () {
        script.remove();
        var encoded = CM3D.mapBytes && CM3D.mapBytes[name];
        if (!encoded) { reject(new Error(name + ': no map bytes')); return; }
        var raw = g.atob(encoded), data = new Uint8Array(raw.length);
        for (var i = 0; i < raw.length; i++) data[i] = raw.charCodeAt(i);
        delete CM3D.mapBytes[name]; resolve(new Blob([data]));
      };
      script.onerror = function () { script.remove(); reject(new Error(url + ': map script failed')); };
      g.document.head.appendChild(script);
    }) : g.fetch(url, { credentials: 'same-origin' }).then(function (r) {
      if (!r.ok) throw new Error(url + ': ' + r.status);
      return r.blob();
    });
    return bytes.then(function (b) { return g.createImageBitmap(b, opts); }).then(function (sheet) {
      var n = Math.floor(sheet.width / 3), out = cap && n > cap ? cap : n, fo = { premultiplyAlpha: 'none', colorSpaceConversion: 'none' };
      if (out !== n) { fo.resizeWidth = out; fo.resizeHeight = out; fo.resizeQuality = 'high'; }
      var faces = [];
      try {
        for (var f = 0; f < 6; f++) faces.push(g.createImageBitmap(sheet, (f % 3) * n, Math.floor(f / 3) * n, n, n, fo));
      } catch (e) { faces.push(Promise.reject(e)); }
      // every face settles before anything is kept or thrown: a face that failed takes the others and the sheet with it
      return Promise.all(faces.map(function (p) { return p.then(function (b) { return { b: b }; }, function (e) { return { e: e }; }); })).then(function (rs) {
        shut(sheet);
        var fs = rs.map(function (r) { return r.b; }), bad = rs.filter(function (r) { return r.e; })[0];
        if (bad || fs.length !== 6) { fs.forEach(shut); throw bad ? bad.e : new Error(name + ': not six faces'); }
        var t = new THREE.CubeTexture(fs);
        t.colorSpace = srgb ? THREE.SRGBColorSpace : THREE.NoColorSpace; t.flipY = false; t.premultiplyAlpha = false;
        t.generateMipmaps = true; t.minFilter = THREE.LinearMipmapLinearFilter; t.magFilter = THREE.LinearFilter;
        t.needsUpdate = true; t.userData.cmBytes = Math.round(fs[0].width * fs[0].width * 4 * 6 * 4 / 3); t.userData.cmName = name;
        // the decoded faces go with the texture (dispose() frees only the GPU copy); a map is disposed only when its
        // session ends, never while a planet may still read it
        t.addEventListener('dispose', function () { fs.forEach(shut); });
        return t;
      });
    });
  }
  function shut(b) { if (b && typeof b.close === 'function') b.close(); }
  // 64^3 texels of tiling value noise, two channels (ground, clouds), each two octaves (8 and 16 cells across) mixed,
  // as flight-rs planet.rs noise_volume: the lattice is hashed once per octave, the texels filled 8 slices a call
  function realHash(x, y, seed) {
    var h = (Math.imul(x, 0x8da6b343) ^ Math.imul(y, 0xd8163841) ^ Math.imul(seed, 0xcb1ab31f)) >>> 0;
    h = (h ^ (h >>> 13)) >>> 0; h = Math.imul(h, 0x5bd1e995) >>> 0; h = (h ^ (h >>> 15)) >>> 0;
    return (h & 0xffff) / 65535;
  }
  function noiseStart() {
    var N = 64, oct = [];
    [9151, 7193].forEach(function (seed) {
      [[8, seed, 0.62], [16, seed + 1, 0.38]].forEach(function (o) {
        var c = o[0], L = new Float32Array(c * c * c);
        for (var k = 0; k < c; k++) for (var j = 0; j < c; j++) for (var i = 0; i < c; i++) L[(k * c + j) * c + i] = realHash(i + k * 131, j, o[1]);
        oct.push({ c: c, L: L, w: o[2] });
      });
    });
    return { N: N, z: 0, oct: oct, data: new Uint8Array(N * N * N * 2) };
  }
  function noiseSlices(V, count) {
    var N = V.N, s = function (t) { return t * t * (3 - 2 * t); };
    for (var z = V.z; z < Math.min(N, V.z + count); z++) {
      for (var y = 0; y < N; y++) for (var x = 0; x < N; x++) {
        for (var ch = 0; ch < 2; ch++) {
          var v = 0;
          for (var q = 0; q < 2; q++) {
            var O = V.oct[ch * 2 + q], c = O.c, L = O.L, st = c / N, fx = x * st, fy = y * st, fz = z * st;
            var ix = Math.floor(fx), iy = Math.floor(fy), iz = Math.floor(fz), u = s(fx - ix), w = s(fy - iy), t = s(fz - iz);
            var x1 = (ix + 1) % c, y1 = (iy + 1) % c, z1 = (iz + 1) % c;
            var at = function (i, j, k) { return L[(k * c + j) * c + i]; };
            var p0 = (at(ix, iy, iz) * (1 - u) + at(x1, iy, iz) * u) * (1 - w) + (at(ix, y1, iz) * (1 - u) + at(x1, y1, iz) * u) * w;
            var p1 = (at(ix, iy, z1) * (1 - u) + at(x1, iy, z1) * u) * (1 - w) + (at(ix, y1, z1) * (1 - u) + at(x1, y1, z1) * u) * w;
            v += (p0 * (1 - t) + p1 * t) * O.w;
          }
          V.data[((z * N + y) * N + x) * 2 + ch] = Math.round(v * 255);
        }
      }
    }
    V.z = Math.min(N, V.z + count);
    return V.z >= N;
  }
  function realMaterial(THREE, P, ctx) {
    var spec = P.real, lk = spec.look, m = planetMaterial(THREE, P, ctx), u = m.uniforms;
    // the painted planets' uniforms stay (updatePlanets sets them all), the realistic ones join them
    u.uAlb = { value: null }; u.uRel = { value: null }; u.uSkyMap = { value: null }; u.uNoise = { value: null };
    u.uReady = { value: 0 }; u.uHasRel = { value: 0 }; u.uHasSky = { value: 0 }; u.uHasNoise = { value: 0 };
    u.uTexel = { value: Math.PI / 2 / (spec.face || 256) }; u.uAge = { value: 0 };
    u.uAirSky = { value: new THREE.Vector4(lk.sky[0], lk.sky[1], lk.sky[2], lk.depth) };
    u.uAir = { value: new THREE.Vector4(lk.height, lk.shell, 1.0, lk.limb) };
    u.uSurfK = { value: new THREE.Vector4(lk.gloss, 0, lk.cloud, 0) };
    u.uCloudC = { value: new THREE.Vector4(lk.cloud_rgb[0], lk.cloud_rgb[1], lk.cloud_rgb[2], lk.variant) };
    m.vertexShader = REAL_VERT; m.fragmentShader = REAL_FRAG;
    return m;
  }
  function realHalo(THREE, P) {
    var lk = P.real.look;
    return shader(THREE, { uniforms: { uC: { value: P.grp.position.clone() }, uR: { value: P.r3 }, uL: { value: new THREE.Vector3(1, 0, 0) }, uK: { value: 0 },
      uAirSky: { value: new THREE.Vector4(lk.sky[0], lk.sky[1], lk.sky[2], lk.depth) }, uAir: { value: new THREE.Vector4(lk.height, lk.shell, 1.0, lk.limb) },
      uFogC: { value: new THREE.Color(0, 0, 0) }, uFogR: { value: new THREE.Vector2(3000, 16000) } },
      vertexShader: PLANET_VERT, fragmentShader: REAL_HALO_FRAG, transparent: true, depthWrite: false, side: THREE.BackSide, blending: THREE.AdditiveBlending });
  }
  // a map, once a session (planets rebuilt on a health change share it); a session over before it arrives drops it
  function realTex(ctx, name, srgb, cap) {
    var R = T.real, mine = T;
    if (!R.tex[name]) {
      R.tex[name] = loadCube(ctx.THREE, name, srgb, cap).then(function (t) {
        if (T !== mine) { t.dispose(); throw new Error('session over'); }
        own(t); R.bytes += t.userData.cmBytes; R.maps++; return t;
      });
      R.tex[name].catch(function () {});
    }
    return R.tex[name];
  }
  // the next planet's maps: the aimed planet first, then nearest the eye; one planet at a time
  function realPump(world, ctx) {
    var R = T.real; if (!R || R.busy) return;
    var ph = phoneQ(ctx), cap = function (k) { return ph ? REAL_CAP[k] : 0; }, mine = T;
    if (!R.sky) {
      var sk = ctx.skin3d.planets3d.sky; R.sky = 'loading'; R.busy = true;
      realTex(ctx, sk, false, cap('sky')).then(function (t) {
        if (T !== mine) return; R.sky = t; R.busy = false;
        Object.keys(T.planets).forEach(function (u) { var P = T.planets[u]; if (P.real) { P.mat.uniforms.uSkyMap.value = t; P.mat.uniforms.uHasSky.value = 1; } });
      }, function (e) { if (T !== mine) return; R.sky = 'failed'; R.busy = false; R.errors.push(String(e && e.message || e)); });
      return;
    }
    var cam = ctx.camera, target = ctx.lock || world.focus || null, best = null, bd = Infinity;
    Object.keys(T.planets).forEach(function (uid) {
      var P = T.planets[uid]; if (!P.real || P.realState) return;
      var d = uid === target ? -1 : cam.position.distanceTo(P.grp.position) / P.r3;
      if (d < bd) { bd = d; best = P; }
    });
    if (!best) return;
    var P = best, spec = P.real; P.realState = 'loading'; R.busy = true;
    var t0 = g.performance.now();
    Promise.all([realTex(ctx, spec.a, true, cap('a')), spec.n ? realTex(ctx, spec.n, false, cap('n')) : Promise.resolve(null)]).then(function (ts) {
      if (T !== mine) return;
      R.busy = false; R.ms.push(Math.round(g.performance.now() - t0));
      if (T.planets[P.uid] !== P) return;                                     // rebuilt meanwhile: the new one asks again (cached)
      var u = P.mat.uniforms; u.uAlb.value = ts[0]; u.uRel.value = ts[1]; u.uHasRel.value = ts[1] ? 1 : 0; u.uReady.value = 1; P.realState = 'ready';
      if (!R.noise) R.noise = noiseStart();
    }, function (e) {
      if (T !== mine) return;
      R.busy = false; R.errors.push(String(e && e.message || e));
      if (T.planets[P.uid] === P) realFallback(ctx, P);
    });
  }
  // a planet whose maps did not come: the painted planet after all (its material and air shell swapped, its bake started)
  function realFallback(ctx, P) {
    var THREE = ctx.THREE, old = P.mat, oldS = P.sm;
    P.real = null; P.realState = 'failed';
    P.mat = planetMaterial(THREE, P, ctx);
    // the 2D art stays bound: updatePlanets rebinds it only when the page draws a new canvas
    ['uArt', 'uArtOn', 'uArtRect', 'uArtPx'].forEach(function (k) { P.mat.uniforms[k].value = old.uniforms[k].value; });
    swapMat(P.mesh, P.mat);
    P.sm = shellMaterial(THREE, P); swapMat(P.shell, P.sm); P.shell.scale.setScalar(P.r3 * 1.075);
    [old, oldS].forEach(function (m) { drop(T.mats, m); if (!m.userData.cmGone) m.dispose(); });
    startBake(THREE, P);
  }
  // a mesh may wear the warm-up's stand-in (core.js keeps its real material in userData.cmReal): the swap goes there
  function swapMat(mesh, m) { if (mesh.userData.cmReal) mesh.userData.cmReal = m; else mesh.material = m; }
  // the noise volume: a few slices a frame once the first planet's maps are in, then one 3D texture all planets read
  function realNoiseStep(ctx) {
    var R = T.real; if (!R || !R.noise || R.noiseTex) return;
    if (!noiseSlices(R.noise, 8)) return;
    var THREE = ctx.THREE, N = R.noise.N, t = new THREE.Data3DTexture(R.noise.data, N, N, N);
    t.format = THREE.RGFormat; t.type = THREE.UnsignedByteType; t.wrapS = t.wrapT = t.wrapR = THREE.RepeatWrapping;
    t.minFilter = THREE.LinearFilter; t.magFilter = THREE.LinearFilter; t.generateMipmaps = false; t.unpackAlignment = 1; t.needsUpdate = true;
    own(t); R.noiseTex = t; R.noise.data = null;
  }
  function realUpdate(P, world, ctx) {
    var u = P.mat.uniforms, R = T.real, mk = T.mapK;
    if (R.noiseTex && !u.uHasNoise.value) { u.uNoise.value = R.noiseTex; u.uHasNoise.value = 1; }
    if (R.sky && R.sky.isTexture && !u.uHasSky.value) { u.uSkyMap.value = R.sky; u.uHasSky.value = 1; }
    var lk = P.real.look, h = P.p.health, bad = h === 'broken' || h === 'waiting';
    // the air over the disc is the world's own (a health tint there turns blue seas grey); the glow round the limb takes
    // the health colour where something is wrong or waits (the painted planets' kRlhFor, as relative scattering), a
    // little of it when the folder is quiet, and thickens when it needs the owner
    var k = bad ? 0.6 : h === 'quiet' ? 0.4 : 0, hk = kRlhFor(h), hm = Math.max(hk[0], hk[1], hk[2]);
    u.uAirSky.value.set(lk.sky[0], lk.sky[1], lk.sky[2], lk.depth);
    // the night lights: brighter where work runs; clouds: the world's cover, a little more where work runs (the painted
    // planets' density), none on the first frame
    u.uSurfK.value.set(lk.gloss, lk.lights * (P.working ? 1.3 : 0.45), clamp(lk.cloud * (0.8 + 0.4 * P.density), 0, 0.95),
      ctx.debug && ctx.debug.noClouds ? 0 : lk.cloud_max * (1 - mk));
    u.uAge.value = P.days == null ? 0 : clamp(P.days / 30, 0, 1);
    u.uHaloOn.value = P.working && !lk.has_lights ? 1 : 0;
    var su = P.sm.uniforms; su.uR.value = P.r3;
    su.uAirSky.value.set(lk.sky[0] + (hk[0] / hm - lk.sky[0]) * k, lk.sky[1] + (hk[1] / hm - lk.sky[1]) * k, lk.sky[2] + (hk[2] / hm - lk.sky[2]) * k,
      lk.depth * (bad ? 1.4 : 1));
    su.uK.value = 1 - mk; su.uFogC.value.copy(u.uFogC.value); su.uFogR.value.copy(u.uFogR.value);
    P.shell.visible = mk < 0.999;
  }
  function buildPlanet(world, ctx, p) {
    var THREE = ctx.THREE, ph = phoneQ(ctx), surf = ctx.page && ctx.page.surface ? ctx.page.surface(p.uid, ph ? 20 : 40) : null;
    var P = { uid: p.uid, p: p, surf: surf, grp: new THREE.Group(), r: p.r, core: 1, art: null, artTex: null, artCanvas: null, phase: 0, phaseSet: false,
      bake: null, tex: null, texRows: -1, shell: null, extras: [], cring: null, haze: null, outline: null, working: false, days: null, density: 0.2 };
    P.core = planetPurposeCore(surf);
    var r3 = p.r * P.core; P.r3 = r3;
    P.grp.position.set(p.x, 0, p.y); P.grp.name = 'cm3d-planet:' + p.uid; add(T.root, P.grp);
    P.real = realSpec(ctx, p); P.realState = null;
    if (P.real && !T.real) T.real = { tex: {}, sky: null, busy: false, bytes: 0, maps: 0, ms: [], errors: [], noise: null, noiseTex: null };
    P.mat = P.real ? realMaterial(THREE, P, ctx) : planetMaterial(THREE, P, ctx);
    var shape = P.real ? 'sphere' : surf && surf.shape ? surf.shape : 'sphere';
    var mesh = new THREE.Mesh(T.body(shape, ph, (Number(p.seed) >>> 0) || 1), P.mat); mesh.scale.setScalar(r3); mesh.userData.uid = p.uid; mesh.userData.kind = 'planet';
    var pal = world.palette && world.palette[p.purpose]; mesh.userData.baseColor = pal ? (pal[0] << 16 | pal[1] << 8 | pal[2]) : 0x8a93a6;
    P.grp.add(mesh); P.mesh = mesh;
    if (surf && !P.real) startBake(THREE, P);          // a planet with baked maps never runs the surfaceAt bake
    // the outer air: every planet (the nearest painted one also gets the live single-scatter shell over it; a realistic
    // planet's halo is its own, in closed form)
    var sm = P.real ? realHalo(THREE, P) : shellMaterial(THREE, P);
    var sh = new THREE.Mesh(T.sphere(ph), sm); sh.scale.setScalar(r3 * (P.real ? P.real.look.shell : 1.075)); sh.userData.standIn = 'hide'; sh.renderOrder = 4; P.grp.add(sh); P.shell = sh; P.sm = sm;
    buildExtras(world, ctx, P);
    buildConditions(world, ctx, P);
    T.planets[p.uid] = P;
    return P;
  }
  function startBake(THREE, P) {
    var ph = phoneQ(T.ctx), W = ph ? 128 : 256, H = ph ? 64 : 128;
    P.bake = bakeFor(P, W, H);
    P.tex = new THREE.DataTexture(P.bake.data, W, H * 4, THREE.RGBAFormat, THREE.UnsignedByteType);
    // linear within a section (the fine noise and the light do the rest); the emissive section is read texel by texel
    P.tex.colorSpace = THREE.SRGBColorSpace; P.tex.magFilter = THREE.LinearFilter; P.tex.minFilter = THREE.LinearFilter; P.tex.generateMipmaps = false;
    P.tex.wrapS = THREE.RepeatWrapping; P.tex.flipY = false; P.tex.needsUpdate = true; own(P.tex);
    P.mat.uniforms.uSurf.value = P.tex; P.mat.uniforms.uSurfSize.value.set(W, H);
    if (P.bake.rows >= H) { P.mat.uniforms.uSurfOn.value = 1; P.texRows = H; }
  }
  function shellMaterial(THREE, P) {
    return shader(THREE, { uniforms: { uRim: { value: lin(THREE, airColour(P)) }, uL: { value: new THREE.Vector3(1, 0, 0) }, uK: { value: 0 },
      uC: { value: P.grp.position.clone() }, uR: { value: P.r3 }, uRa: { value: P.r3 * 1.075 } },
      vertexShader: PLANET_VERT, fragmentShader: SHELL_FRAG, transparent: true, depthWrite: false, side: THREE.BackSide, blending: THREE.AdditiveBlending });
  }
  // the purpose's parts: the reference shelf, the shared station ring and dock, the archive's rubble belt and crates
  function buildExtras(world, ctx, P) {
    var THREE = ctx.THREE, s = P.surf, p = P.p; if (!s) return;
    var prof = s.profile || [], C = s.palette.map(function (c) { return lin(THREE, rgbHex(c)); });
    // the parts lie in the body's equator (normal +Y in P.eq), which turns with the pole each frame
    P.eq = new THREE.Group(); P.grp.add(P.eq);
    var ringFor = function (r0, r1, lamps, tilt) {
      var geo = new THREE.RingGeometry(r0, r1, 96, 1); own(geo);
      var m = shader(THREE, { uniforms: { uC: { value: new THREE.Vector3(p.x, 0, p.y) }, uR0: { value: r0 }, uR1: { value: r1 }, uK0: { value: C[1] }, uK1: { value: C[2] },
        uK2: { value: C[3] }, uL: { value: new THREE.Vector3(1, 0, 0) }, uOp: { value: 0.92 }, uLamps: { value: lamps ? 1 : 0 }, uT: { value: 0 } },
        vertexShader: RING_VERT, fragmentShader: RING_FRAG, transparent: true, depthWrite: false, side: THREE.DoubleSide });
      var ring = new THREE.Mesh(geo, m); ring.userData.standIn = 'hide'; ring.renderOrder = 2; ring.rotation.x = -Math.PI / 2 + (tilt || 0) * 0.3;
      P.eq.add(ring); P.extras.push({ kind: 'ring', mesh: ring, mat: m }); return ring;
    };
    if (prof.indexOf('ring') >= 0) {
      if (s.purpose === 'shared') ringFor(p.r * 1.12, p.r * 1.45, true, 0.25);
      else ringFor(p.r * 1.45, p.r * 1.95, false, 0.32);
    }
    if (prof.indexOf('dock') >= 0) {
      var dg = new THREE.CylinderGeometry(p.r * 0.05, p.r * 0.05, p.r * 0.7, 8); own(dg);
      var dmat = new THREE.MeshLambertMaterial({ color: C[2], emissive: C[0] }); keep(dmat);
      // the dock: an arm off the sun-away side with a lit port at its end (the pivot turns it away from the sun)
      var pivot = new THREE.Group(); P.grp.add(pivot);
      var dock = new THREE.Mesh(dg, dmat); dock.rotation.z = Math.PI / 2; dock.position.set(-p.r * 0.75, 0, 0); pivot.add(dock);
      var port = new THREE.Mesh(new THREE.SphereGeometry(p.r * 0.09, 10, 8), new THREE.MeshBasicMaterial({ color: C[4] })); own(port.geometry); keep(port.material);
      port.position.set(-p.r * 1.12, 0, 0); pivot.add(port); P.extras.push({ kind: 'dock', mesh: pivot });
    }
    if (prof.indexOf('rubble') >= 0 || prof.indexOf('crates') >= 0) {
      // the belt: grains as one instanced rock field, crates as one instanced box field, tilted like the 2D's flattened belt
      var rock = T.rock(), n = 70, rm = new THREE.MeshLambertMaterial({ color: 0xffffff, flatShading: true }); keep(rm);
      var belt = new THREE.InstancedMesh(rock, rm, n), M = new THREE.Matrix4(), Q = new THREE.Quaternion(), E = new THREE.Euler(), V = new THREE.Vector3(), Sx = new THREE.Vector3(), col = new THREE.Color();
      var seed = (Number(p.seed) >>> 0) || 1;
      for (var i = 0; i < n; i++) {
        var a = hash3(i, 1, 2, seed) * TAU, rr = p.r * (0.75 + hash3(i, 3, 4, seed) * 0.7), sz = p.r * (0.025 + 0.05 * Math.pow(hash3(i, 5, 6, seed), 3));
        V.set(Math.cos(a) * rr, (hash3(i, 7, 8, seed) - 0.5) * p.r * 0.08, Math.sin(a) * rr); Q.setFromEuler(E.set(a, a * 2, a * 3)); Sx.setScalar(sz);
        M.compose(V, Q, Sx); belt.setMatrixAt(i, M); col.copy(C[i % 5 === 0 ? 3 : i % 2 ? 1 : 2]); belt.setColorAt(i, col);
      }
      belt.userData.noStandIn = true; P.eq.add(belt); P.extras.push({ kind: 'belt', mesh: belt });
      if (prof.indexOf('crates') >= 0) {
        var bg = new THREE.BoxGeometry(1, 0.7, 0.8); own(bg);
        var bm = new THREE.MeshLambertMaterial({ color: lin(THREE, rgbHex(s.glow)), emissive: lin(THREE, rgbHex(s.glow)), emissiveIntensity: 0.15 }); keep(bm);
        var crates = new THREE.InstancedMesh(bg, bm, 8);
        for (var k = 0; k < 8; k++) {
          var ca = hash3(k, 7, 2, seed) * TAU, cr = p.r * (0.85 + hash3(k, 8, 4, seed) * 0.55), cs = p.r * 0.07;
          V.set(Math.cos(ca) * cr, (hash3(k, 9, 1, seed) - 0.5) * p.r * 0.06, Math.sin(ca) * cr); Q.setFromEuler(E.set(0, -ca, 0.2)); Sx.setScalar(cs);
          M.compose(V, Q, Sx); crates.setMatrixAt(k, M);
        }
        crates.userData.noStandIn = true; P.eq.add(crates); P.extras.push({ kind: 'crates', mesh: crates });
      }
    }
  }
  // the conditions drawn over a body in 3D: the working arc, the rebuildable haze, the empty folder's dashed outline
  function buildConditions(world, ctx, P) {
    var THREE = ctx.THREE, p = P.p, cond = p.conditions || [];
    var ringMat = function (col, dash, op) {
      return shader(THREE, { uniforms: { uCol: { value: lin(THREE, col) }, uDim: { value: lin(THREE, rgbHex(mixRgb(hexRgb(col), [5, 7, 13], 0.7))) }, uFill: { value: 1 },
        uDash: { value: dash ? 1 : 0 }, uOp: { value: op } }, vertexShader: CRING_VERT, fragmentShader: CRING_FRAG, transparent: true, depthWrite: false, side: THREE.DoubleSide });
    };
    var R = P.r3;
    P.cringMat = ringMat(WORKING, false, 0.95);
    var arc = new THREE.Mesh(new THREE.RingGeometry(R * 1.22, R * 1.27, 96, 1), P.cringMat); own(arc.geometry);
    arc.userData.standIn = 'hide'; arc.visible = false; arc.renderOrder = 5; P.grp.add(arc); P.cring = arc;
    var out = new THREE.Mesh(new THREE.RingGeometry(R * 1.06, R * 1.09, 96, 1), ringMat(IDLE_GREY, true, 0.85)); own(out.geometry);
    out.userData.standIn = 'hide'; out.visible = cond.indexOf('empty') >= 0; out.renderOrder = 5; P.grp.add(out); P.outline = out;
    var hz = shader(THREE, { uniforms: { uC: { value: new THREE.Vector3(p.x, 0, p.y) }, uOp: { value: 0.32 } }, vertexShader: PLANET_VERT, fragmentShader: HAZE_FRAG,
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending });
    var haze = new THREE.Mesh(T.sphere(phoneQ(ctx)), hz); haze.scale.setScalar(R * 1.28); haze.userData.standIn = 'hide'; haze.visible = cond.indexOf('rebuildable') >= 0;
    P.grp.add(haze); P.haze = haze;
    var hring = new THREE.Mesh(new THREE.RingGeometry(R * 1.265, R * 1.295, 96, 1), ringMat('#9AA8BC', true, 0.6)); own(hring.geometry);
    hring.userData.standIn = 'hide'; hring.visible = haze.visible; hring.renderOrder = 5; P.grp.add(hring); P.hazeRing = hring;
  }
  // a retired planet (gone from the world, or rebuilt on a change): its own textures and materials go now and leave the
  // teardown's lists, so nothing leaks until the view ends and nothing is disposed twice there
  function drop(list, x) { var i = list.indexOf(x); if (i >= 0) list.splice(i, 1); }
  function disposePlanet(P) {
    if (P.grp.parent) P.grp.parent.remove(P.grp);
    delete T.planets[P.uid];
    if (P.artTex) { P.artTex.dispose(); P.artTex = null; P.artCanvas = null; }
    if (P.tex) { drop(T.own, P.tex); P.tex.dispose(); P.tex = null; }
    [P.mat, P.sm].forEach(function (m) { if (!m) return; drop(T.mats, m); if (!m.userData.cmGone) m.dispose(); });
    P.mat = null; P.sm = null;
  }
  // which planets work: their own condition, a working workflow step there, an agent docked at work
  function workingSet(world) {
    var s = {};
    (world.planets || []).forEach(function (p) { if ((p.conditions || []).indexOf('working') >= 0) s[p.uid] = true; });
    (world.workflows || []).forEach(function (w) { (w && w.steps || []).forEach(function (st) { if (st && st.state === 'working' && st.planet_uid) s[st.planet_uid] = true; }); });
    (world.craft || []).forEach(function (c) { if (c && c.planet && /^working$/i.test(String(c.state || ''))) s[c.planet] = true; });
    return s;
  }
  function stepProgress(world, uid) {
    var best = null;
    (world.workflows || []).forEach(function (w) { (w && w.steps || []).forEach(function (st) {
      if (st && st.state === 'working' && st.planet_uid === uid && st.progress && st.progress.total > 0) { var f = clamp(st.progress.done / st.progress.total, 0, 1); best = best == null ? f : Math.max(best, f); } }); });
    return best;
  }
  // a planet's days since its last write: its freshest sub-folder's, at least 30 when it is quiet
  function planetDays(world, p) {
    var d = p.days != null && isFinite(p.days) ? Number(p.days) : null;
    if (d == null) (world.moons || []).forEach(function (m) { if (m.parent === p.uid && m.days != null && isFinite(m.days)) d = d == null ? m.days : Math.min(d, m.days); });
    if ((p.conditions || []).indexOf('quiet') >= 0) d = Math.max(d == null ? 30 : d, 30);
    return d;
  }
  function syncPlanets(world, ctx) {
    var seen = {}, list = world.planets || [];
    list.forEach(function (p) {
      seen[p.uid] = true; var P = T.planets[p.uid];
      var key = [p.x, p.y, p.r, p.purpose, p.health, (p.conditions || []).join('+'), realSpec(ctx, p) ? 'real' : 'painted'].join('|');
      if (P && P.key !== key) { disposePlanet(P); P = null; }
      if (!P) { P = buildPlanet(world, ctx, p); P.key = key; }
      P.p = p;
    });
    Object.keys(T.planets).forEach(function (uid) { if (!seen[uid]) disposePlanet(T.planets[uid]); });
    var work = workingSet(world);
    Object.keys(T.planets).forEach(function (uid) {
      var P = T.planets[uid]; P.working = !!work[uid]; P.days = planetDays(world, P.p); P.density = cloudDensity(P.working, P.days);
      P.progress = stepProgress(world, uid);
    });
  }
  // how much of the first frame is left: 1 while the camera looks straight down (the 2D frame), 0 once it looks out (flight)
  // how much the view is still the 2D map's (1 looking straight down, the matched first frame): the 2D art on the planets,
  // the sky's veil, the flare's and dust's fade. It is gone by 64 degrees down (was 55): the dive shows painted planets by
  // 0.8 s (comps/3d-transition.png), the 2D layer (world3d/camera.js) carries the 2D look over its first part
  function mapK(ctx) {
    var dir = new ctx.THREE.Vector3(); ctx.camera.getWorldDirection(dir);
    return smoothstep(0.9, 0.998, -dir.y);
  }
  function updatePlanets(world, ctx, dt) {
    var THREE = ctx.THREE, A = audit(ctx), st = world.star || { x: 0, y: 0 }, cam = ctx.camera, mk = T.mapK, sp = world.ship || { x: 0, y: 0 };
    var budget = phoneQ(ctx) ? 4 : 7, target = ctx.lock || world.focus || null;
    // the bake: the aimed planet first, then by distance from the ship
    var order = Object.keys(T.planets).map(function (u) { return T.planets[u]; }).filter(function (P) { return P.bake && P.bake.rows < P.bake.h; });
    order.sort(function (a, b) { var da = a.uid === target ? -1 : Math.hypot(a.p.x - sp.x, a.p.y - sp.y), db = b.uid === target ? -1 : Math.hypot(b.p.x - sp.x, b.p.y - sp.y); return da - db; });
    var spent = 0, t0 = g.performance.now();
    for (var i = 0; i < order.length && spent < budget; i++) { bakeRows(order[i], budget - spent); spent = g.performance.now() - t0; }
    var clouds = {}, night = {}, near = T.nearest, rim = T.rimDir, rimCol = T.rimCol, rimI = T.rimI, env = T.env, work = workingSet(world);
    Object.keys(T.planets).forEach(function (uid) {
      var P = T.planets[uid], p = P.p, u = P.mat.uniforms;
      var wp = (world.planets || []).filter(function (q) { return q.uid === uid; })[0]; if (wp) P.p = p = wp;
      P.working = !!work[uid]; P.days = planetDays(world, p); P.density = cloudDensity(P.working, P.days); P.progress = stepProgress(world, uid);
      if (P.bake && P.texRows !== P.bake.rows && (P.bake.rows >= P.bake.h || P.bake.rows - P.texRows > 24)) {
        P.tex.needsUpdate = true; P.texRows = P.bake.rows; if (P.bake.rows >= P.bake.h) u.uSurfOn.value = 1;
      }
      var L = lightDir(lightBucket(p.x, p.y, st.x, st.y)); u.uL.value.set(L[0], L[1], L[2]);
      // the 2D art and turn while any of the first frame shows
      if (mk > 0.001 && ctx.page && ctx.page.art) {
        var art = ctx.page.art(uid);
        if (art && art.canvas) {
          if (P.artCanvas !== art.canvas) {
            if (P.artTex) P.artTex.dispose();
            P.artTex = new THREE.CanvasTexture(art.canvas); P.artTex.colorSpace = THREE.SRGBColorSpace; P.artTex.magFilter = THREE.NearestFilter;
            P.artTex.minFilter = THREE.NearestFilter; P.artTex.generateMipmaps = false; P.artCanvas = art.canvas; u.uArt.value = P.artTex;
            u.uArtPx.value.set(art.canvas.width, art.canvas.height);
          }
          u.uArtRect.value.set(art.x0, art.y0, art.q); u.uArtOn.value = 1;
        }
      }
      if (mk > 0.5 || !P.phaseSet) {
        var phs = ctx.page && ctx.page.phase ? ctx.page.phase(uid) : null;
        if (phs) { P.phase = phs.phase; P.period = phs.period; P.phaseSet = true; }
      } else if (!world.reduced && P.period) P.phase += dt * TAU / P.period;
      u.uPhase.value = P.phase; u.uMapK.value = mk;
      u.uWork.value = P.working ? 1 : 0; u.uHaloOn.value = P.working && !(P.surf && P.surf.city) ? 1 : 0;
      u.uDim.value = (p.conditions || []).indexOf('empty') >= 0 ? 0.35 : 1;
      if (rim) { u.uRimDir.value.copy(rim); u.uRimCol.value.copy(rimCol); u.uRimI.value = rimI; }
      if (env) { u.uFogC.value.copy(env.fogC); u.uFogR.value.set(env.near, env.far); }
      // clouds and air: none on the first frame, then by the work there (density 0.2 + 0.6 x activity, twice as fast at work)
      var lk = lookOf(P);
      u.uCloud.value = P.density; u.uCloudOp.value = ctx.debug && ctx.debug.noClouds ? 0 : lk.cloud * (0.75 + 0.25 * P.density) * (1 - mk);   // __3dDebug.clouds(false): the bare surface for the checks
      if (!world.reduced) u.uCloudSpin.value += dt * (P.working ? 0.05 : 0.025);
      var isNear = uid === (near && near.uid) && T.atmo, bad = p.health === 'broken' || p.health === 'waiting';
      var su = P.sm.uniforms; su.uL.value.copy(u.uL.value); su.uC.value.copy(P.grp.position);
      su.uK.value = (bad ? 0.8 : 0.6 * lk.air) * (isNear ? 0.6 : 1) * (1 - mk);
      P.shell.visible = mk < 0.999 && su.uK.value > 0.001;
      if (P.real) realUpdate(P, world, ctx);
      clouds[uid] = { density: Math.round(P.density * 1000) / 1000, speed: P.working ? 2 : 1 };
      night[uid] = !!P.working;
      // the parts turn with the pole: the body's pole in world space is Rx(-al) (0, -sin T, cos T) = (0, sin(al - T), cos(al - T))
      if (P.eq) { var al = (1 - mk) * Math.PI / 2; T.tmpV.set(0, Math.sin(al - TILT), Math.cos(al - TILT)); P.eq.quaternion.setFromUnitVectors(T.up, T.tmpV); }
      P.extras.forEach(function (x) {
        if (x.kind === 'ring') { var mu = x.mat.uniforms; mu.uL.value.copy(u.uL.value); if (!world.reduced) mu.uT.value += dt; }
        // Storage and archive markers stay at their data positions.
        if (x.kind === 'dock') x.mesh.rotation.y = -Math.atan2(-(st.y - p.y), -(st.x - p.x)) + Math.PI;
      });
      // the working arc fills with the step's progress and faces the eye; the outline and haze face it too
      P.cring.visible = P.working; P.cringMat.uniforms.uFill.value = P.progress == null ? 0.25 : Math.max(0.08, P.progress);
      [P.cring, P.outline, P.hazeRing].forEach(function (o) { if (o && o.visible) o.quaternion.copy(cam.quaternion); });
    });
    A.scene.planets = Object.keys(T.planets).length; A.scene.clouds = clouds; A.scene.nightLights = night;
    A.scene.planetLight = 'sun+12';
    A.scene.mapK = Math.round(mk * 1000) / 1000;
    var baked = 0, total = 0; Object.keys(T.planets).forEach(function (u) { var P = T.planets[u]; if (P.bake) { total++; if (P.bake.rows >= P.bake.h) baked++; } });
    A.scene.bakes = { done: baked, of: total };
  }
  // the live single-scatter air: only the planet nearest the eye (glsl-atmosphere at 8/4 steps; 4/2 on a phone)
  function buildAtmo(ctx) {
    var THREE = ctx.THREE, ph = phoneQ(ctx);
    var m = shader(THREE, { uniforms: { uC: { value: new THREE.Vector3() }, uR: { value: 1 }, uL: { value: new THREE.Vector3(1, 0, 0) }, uKRlh: { value: new THREE.Vector3(2.2, 5.2, 9.0) },
      uKMie: { value: 1.2 }, uOp: { value: 1 }, uI: { value: ph ? 4 : 8 }, uJ: { value: ph ? 2 : 4 }, uSunI: { value: 6 } }, vertexShader: PLANET_VERT, fragmentShader: ATMO_FRAG,
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending });
    var mesh = new THREE.Mesh(T.sphere(ph), m); mesh.userData.standIn = 'hide'; mesh.renderOrder = 4; mesh.visible = false; mesh.name = 'cm3d-air';
    add(T.root, mesh);
    T.atmo = { mesh: mesh, mat: m, steps: ph ? '4/2' : '8/4' };
  }
  // Rayleigh coefficients from a planet's health colour: healthy air scatters blue (and reddens at the terminator), a planet
  // that waits on the owner glows amber, a broken one red; optical depth over the 0.022 r scale height ~ Earth's
  function kRlhFor(health) {
    if (health === 'broken') return [5.2, 1.6, 1.4];
    if (health === 'waiting') return [4.8, 3.4, 1.3];
    if (health === 'quiet') return [2.0, 2.3, 2.8];
    return [1.6, 3.8, 6.6];
  }
  function updateAtmo(world, ctx) {
    var a = T.atmo; if (!a) return;
    var cam = ctx.camera, best = null, bd = Infinity, A = audit(ctx);
    Object.keys(T.planets).forEach(function (uid) { var P = T.planets[uid], d = cam.position.distanceTo(P.grp.position) / P.r3; if (d < bd) { bd = d; best = P; } });
    T.nearest = best && bd < 14 ? best : null;
    var P = T.nearest, show = !!P && T.mapK < 0.999 && !P.real;     // a realistic planet's air is in its own passes
    a.mesh.visible = show;
    if (P) {
      var u = a.mat.uniforms; u.uC.value.copy(P.grp.position); u.uR.value = P.r3; u.uL.value.copy(P.mat.uniforms.uL.value);
      var k = kRlhFor(P.p.health); u.uKRlh.value.set(k[0], k[1], k[2]); u.uOp.value = 0.85 * (1 - T.mapK);
      a.mesh.position.copy(P.grp.position); a.mesh.scale.setScalar(P.r3 * 1.10);
      var side = cam.position.distanceTo(P.grp.position) < P.r3 * 1.10 ? ctx.THREE.BackSide : ctx.THREE.FrontSide;
      if (a.mat.side !== side) { a.mat.side = side; a.mat.needsUpdate = true; }
    }
    A.atmosphere = { live: show ? 1 : 0, steps: show ? a.steps : null, uid: P ? P.uid : null, shell: P && P.real ? P.real.look.shell : 1.10, closedForm: !!(P && P.real) };
  }

  // ---- the region light and mood: the sky-side rim (T3) and the environment record per region (T5), eased over 2 s
  function buildRim(ctx) {
    var THREE = ctx.THREE, t = skyTable(ctx.skin3d), b = T.sky ? T.sky.bright : [0, 1, 0];
    var light = new THREE.DirectionalLight(0xffffff, t.rimI != null ? t.rimI : 0.4); light.name = 'cm3d-rim';
    light.position.set(b[0], b[1], b[2]); light.target.position.set(0, 0, 0);
    add(T.root, light); add(T.root, light.target);
    T.rimLight = light; T.rimDir = new THREE.Vector3(b[0], b[1], b[2]).normalize(); T.rimCol = new THREE.Color(0xffffff); T.rimI = light.intensity;
  }
  function updateRim(world, ctx) {
    if (!T.rimLight) return;
    var t = skyTable(ctx.skin3d), A = audit(ctx);
    var hex = t.rim || (ctx.sky && ctx.sky.tint) || '#8FB4FF';
    T.rimLight.color.set(hex); T.rimCol.set(hex); T.rimI = t.rimI != null ? t.rimI : 0.4; T.rimLight.intensity = T.rimI;
    A.scene.lights = { rim: 1, rimI: T.rimI, rimColor: String(hex).toUpperCase(), rimDir: T.rimDir.toArray().map(function (v) { return Math.round(v * 1000) / 1000; }) };
  }
  function buildEnv(scene, ctx) {
    var THREE = ctx.THREE;
    T.fog = new THREE.Fog(0x000000, 3000, 16000); T.keepFog = scene.fog; scene.fog = T.fog;
    T.env = { purpose: null, from: null, to: null, t: 1, fogC: new THREE.Color(0, 0, 0), near: 3000, far: 16000, light: new THREE.Color(0xffffff), ambient: new THREE.Color(0x000000) };
  }
  function envLerp(a, b, k) {
    return { fog: mixRgb(a.fog, b.fog, k), near: a.near + (b.near - a.near) * k, far: a.far + (b.far - a.far) * k, light: mixRgb(a.light, b.light, k),
      ambient: mixRgb(a.ambient, b.ambient, k), lightDir: mixRgb(a.lightDir, b.lightDir, k) };
  }
  function envRaw(e) { return { fog: hexRgb(e.fog), near: e.near, far: e.far, light: hexRgb(e.light), ambient: hexRgb(e.ambient), lightDir: e.lightDir.slice() }; }
  function updateEnv(world, ctx, dt) {
    var E = T.env; if (!E) return;
    var near = nearestPlanet(world), purpose = near ? near.purpose : 'unsorted', want = envFor(ctx.skin3d, purpose);
    if (E.purpose !== want.purpose) {
      var now = E.to ? envLerp(E.from, E.to, E.t) : envRaw(want);
      E.from = now; E.to = envRaw(want); E.purpose = want.purpose; E.t = E.cur ? 0 : 1;
    }
    E.t = Math.min(1, E.t + dt / 2);
    var cur = E.cur = envLerp(E.from, E.to, E.t);
    E.fogC.setRGB(cur.fog[0] / 255, cur.fog[1] / 255, cur.fog[2] / 255, ctx.THREE.SRGBColorSpace); E.near = cur.near; E.far = cur.far;
    T.fog.color.copy(E.fogC); T.fog.near = cur.near; T.fog.far = cur.far;
    var lights = ctx.lights || {};
    if (lights.sun) lights.sun.color.setRGB(cur.light[0] / 255, cur.light[1] / 255, cur.light[2] / 255, ctx.THREE.SRGBColorSpace);
    if (lights.ambient) { lights.ambient.color.setRGB(cur.ambient[0] / 255, cur.ambient[1] / 255, cur.ambient[2] / 255, ctx.THREE.SRGBColorSpace); lights.ambient.intensity = 1.0; }
    if (lights.sky && cur.lightDir) lights.sky.position.set(cur.lightDir[0], cur.lightDir[1], cur.lightDir[2]);
    var A = audit(ctx), f = cur.fog.map(function (v) { return Math.round(v / 255 * 10000) / 10000; });
    A.scene.env = { purpose: E.purpose, fog: rgbHex(cur.fog), near: Math.round(cur.near), far: Math.round(cur.far), t: Math.round(E.t * 1000) / 1000 };
    A.scene.fog = f;
  }

  // ---- the flare (T9): ghosts on the sun-to-centre line, faded by an analytic occlusion test against the planets
  function buildFlare(ctx) {
    var THREE = ctx.THREE, list = skyTable(ctx.skin3d).flare || NEUTRAL.sky3d.flare, n = list.length;
    var k = new Float32Array(n), s = new Float32Array(n), c = new Float32Array(n * 3), sh = new Float32Array(n);
    var al = new Float32Array(n);
    list.forEach(function (f, i) { k[i] = f[0]; s[i] = f[1]; var cc = hexRgb(f[2]); c[i * 3] = cc[0] / 255; c[i * 3 + 1] = cc[1] / 255; c[i * 3 + 2] = cc[2] / 255; sh[i] = f[3]; al[i] = f[4] == null ? 1 : f[4]; });
    var geo = new THREE.BufferGeometry(); geo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(n * 3), 3));
    geo.setAttribute('aK', new THREE.BufferAttribute(k, 1)); geo.setAttribute('aSize', new THREE.BufferAttribute(s, 1)); geo.setAttribute('aCol', new THREE.BufferAttribute(c, 3));
    geo.setAttribute('aShape', new THREE.BufferAttribute(sh, 1)); geo.setAttribute('aAlpha', new THREE.BufferAttribute(al, 1)); own(geo);
    var m = shader(THREE, { uniforms: { uSun: { value: new THREE.Vector2() }, uDpr: { value: 1 }, uOp: { value: 0 } }, vertexShader: FLARE_VERT, fragmentShader: FLARE_FRAG,
      transparent: true, depthWrite: false, depthTest: false, blending: THREE.AdditiveBlending });
    var pts = new THREE.Points(geo, m); pts.frustumCulled = false; pts.renderOrder = 90; pts.name = 'cm3d-flare';
    add(T.root, pts);
    T.flare = { pts: pts, mat: m, occ: 0, opacity: 0, count: list.filter(function (f) { return f[0] > 0; }).length };
  }
  function updateFlare(world, ctx, dt) {
    var F = T.flare; if (!F) return;
    var THREE = ctx.THREE, cam = ctx.camera, st = world.star || { x: 0, y: 0, r: 150 }, A = audit(ctx);
    var c = new THREE.Vector3(st.x, 0, st.y), view = c.clone().applyMatrix4(cam.matrixWorldInverse), front = view.z < -1;
    var ndc = c.clone().project(cam), onScreen = front && Math.abs(ndc.x) < 1.15 && Math.abs(ndc.y) < 1.15;
    // the sun disc sampled at nine points across its face (in the plane facing the eye); the share a planet blocks
    var occ = 0;
    if (front) {
      var o = cam.position.toArray(), to = c.clone().sub(cam.position), dist = to.length(), dir = to.clone().normalize();
      var up = Math.abs(dir.y) < 0.95 ? new THREE.Vector3(0, 1, 0) : new THREE.Vector3(1, 0, 0), ux = up.clone().cross(dir).normalize(), uy = dir.clone().cross(ux).normalize();
      var pts = [[0, 0]], hits = 0, rr = st.r * 0.5;
      for (var i = 0; i < 8; i++) pts.push([Math.cos(i * TAU / 8), Math.sin(i * TAU / 8)]);
      pts.forEach(function (q) {
        var tgt = c.clone().addScaledVector(ux, q[0] * rr).addScaledVector(uy, q[1] * rr), d = tgt.clone().sub(cam.position), len = d.length(); d.divideScalar(len);
        var blocked = Object.keys(T.planets).some(function (uid) { var P = T.planets[uid], t = raySphere(o, d.toArray(), P.grp.position.toArray(), P.r3); return t >= 0 && t < len; });
        if (blocked) hits++;
      });
      occ = hits / pts.length;
    } else occ = 1;
    var k = 1 - Math.exp(-Math.max(0, dt) * 8);
    F.occ += (occ - F.occ) * k;
    var edge = onScreen ? 1 - smoothstep(0.75, 1.15, Math.max(Math.abs(ndc.x), Math.abs(ndc.y))) : 0;
    F.opacity = (1 - F.occ) * edge * (1 - T.mapK) * (ctx.glareK == null ? 1 : clamp(Number(ctx.glareK) || 0, 0, 1));
    F.mat.uniforms.uSun.value.set(ndc.x, ndc.y); F.mat.uniforms.uOp.value = F.opacity; F.mat.uniforms.uDpr.value = ctx.renderer.getPixelRatio();
    F.pts.visible = F.opacity > 0.002;
    A.flare = { occlusion: Math.round(F.occ * 1000) / 1000, opacity: Math.round(F.opacity * 1000) / 1000, onScreen: onScreen, ghosts: F.count, front: front };
  }

  // ---- other machines as far galaxies (T10), at fixed bearings from a hash of the name, red when down
  function nameHash(s) { var h = 0x811c9dc5; s = String(s || ''); for (var i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 0x01000193); } return h >>> 0; }
  function buildMachines(world, ctx) {
    var THREE = ctx.THREE, list = world.machines || [];
    if (T.machines) T.machines.forEach(function (m) { if (m.pts.parent) m.pts.parent.remove(m.pts); m.pts.geometry.dispose(); });
    T.machines = [];
    list.forEach(function (mc, k) {
      var h = nameHash(mc.label || mc.uid), th = (h % 3600) / 3600 * TAU, el = (((h >>> 12) % 1000) / 1000 - 0.5) * 0.7;
      var dir = new THREE.Vector3(Math.cos(th) * Math.cos(el), Math.sin(el), Math.sin(th) * Math.cos(el)).normalize();
      var up = Math.abs(dir.y) < 0.9 ? new THREE.Vector3(0, 1, 0) : new THREE.Vector3(1, 0, 0), u = up.clone().cross(dir).normalize(), v = dir.clone().cross(u).normalize();
      var tilt = 0.55 + ((h >>> 20) % 100) / 100 * 0.3; v.multiplyScalar(tilt);
      var n = 900, idx = new Float32Array(n); for (var i = 0; i < n; i++) idx[i] = i;
      var geo = new THREE.BufferGeometry(); geo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(n * 3), 3)); geo.setAttribute('aIndex', new THREE.BufferAttribute(idx, 1));
      var down = /down|lost|off/.test(String(mc.state || ''));
      var m = shader(THREE, { uniforms: { uCount: { value: n }, uDir: { value: dir }, uU: { value: u }, uV: { value: v }, uSpread: { value: 0.035 }, uDpr: { value: 1 },
        uSeed: { value: h % 100000 }, uCol: { value: lin(THREE, down ? '#FF5A5F' : '#F5B342') } }, vertexShader: GAL_VERT, fragmentShader: GAL_FRAG,
        transparent: true, depthWrite: false, blending: THREE.AdditiveBlending });
      var pts = new THREE.Points(geo, m); pts.frustumCulled = false; pts.renderOrder = -15; pts.userData.uid = mc.uid; pts.name = 'cm3d-machine:' + mc.uid;
      add(T.root, pts);
      T.machines.push({ uid: mc.uid, label: String(mc.label || mc.uid), dir: dir, pts: pts, mat: m, down: down });
    });
    T.machineSig = JSON.stringify(list.map(function (m) { return [m.uid, m.label, m.state]; }));
  }
  function updateMachines(world, ctx) {
    var sig = JSON.stringify((world.machines || []).map(function (m) { return [m.uid, m.label, m.state]; }));
    if (sig !== T.machineSig) buildMachines(world, ctx);
    var A = audit(ctx), dpr = ctx.renderer.getPixelRatio();
    T.machines.forEach(function (m) { m.mat.uniforms.uDpr.value = dpr; });
    A.scene.machines = T.machines.length;
    A.scene.machinesList = T.machines.map(function (m) { return { uid: m.uid, label: m.label, dir: m.dir.toArray().map(function (v) { return Math.round(v * 1000) / 1000; }), down: m.down }; });
    ctx.machines3d = T.machines.map(function (m) { return { uid: m.uid, label: m.label, dir: m.dir.clone() }; });
  }

  // ---- moons on focus (spacekit): the first 12 sub-folders as stations on their 2D orbits, the rest as one instanced belt
  // whose rocks carry one RGBA each (instanced-mesh technique 4), pickable by instanceId and lifted on hover (technique 5)
  var MOON_STATIONS = 12;
  function rgbaMaterial(THREE, base) {
    var m = mark(new THREE.MeshLambertMaterial(Object.assign({ color: 0xffffff, transparent: true, depthWrite: false, flatShading: true }, base || {})));
    m.onBeforeCompile = function (sh) {
      sh.vertexShader = 'attribute vec4 instanceRGBA;\nvarying vec4 vRGBA;\n' + sh.vertexShader.replace('#include <begin_vertex>', '#include <begin_vertex>\n  vRGBA = instanceRGBA;');
      sh.fragmentShader = 'varying vec4 vRGBA;\n' + sh.fragmentShader.replace('#include <color_fragment>',
        '#include <color_fragment>\n  vec3 cmLinRGBA = mix(vRGBA.rgb / 12.92, pow((vRGBA.rgb + 0.055) / 1.055, vec3(2.4)), step(0.04045, vRGBA.rgb));\n  diffuseColor.rgb *= cmLinRGBA; diffuseColor.a *= vRGBA.a;');
    };
    m.customProgramCacheKey = function () { return 'cm3d-rgba'; };
    keep(m); return m;
  }
  function setRGBAAt(attr, i, r, gg, b, a) { attr.setXYZW(i, r, gg, b, a); attr.addUpdateRange(i * 4, 4); attr.needsUpdate = true; }
  function focusTarget(world, ctx) {
    var t = ctx.lock || world.focus || null;
    if (t && T.planets[t]) return t;
    // near enough to a planet that its moons would show (inside 8 orbit radii of its outermost station)
    var cam = ctx.camera, best = null, bd = Infinity;
    Object.keys(T.planets).forEach(function (uid) { var P = T.planets[uid], d = cam.position.distanceTo(P.grp.position) / P.r3; if (d < bd) { bd = d; best = uid; } });
    return best && bd < 5 ? best : null;
  }
  function moonsOf(world, uid) { return (world.moons || []).filter(function (m) { return m.parent === uid; }); }
  function buildMoons(world, ctx, uid) {
    disposeMoons();
    var THREE = ctx.THREE, P = T.planets[uid]; if (!P) return;
    var all = moonsOf(world, uid), stations = all.slice(0, MOON_STATIONS), rest = all.slice(MOON_STATIONS), pal = world.palette || {}, cam = ctx.camera;
    var M = { uid: uid, stations: null, belt: null, list: stations, rest: rest, sig: JSON.stringify(all.map(function (m) { return [m.uid, m.purpose, m.days, m.bytes]; })), hover: null, items: [] };
    if (stations.length) {
      var sg = T.stationGeo(), sm = rgbaMaterial(THREE, { depthWrite: true }), inst = new THREE.InstancedMesh(sg, sm, stations.length);
      var attr = new THREE.InstancedBufferAttribute(new Float32Array(stations.length * 4), 4); attr.setUsage(THREE.DynamicDrawUsage);
      var geo = sg.clone(); geo.setAttribute('instanceRGBA', attr); inst.geometry = geo; own(geo);
      stations.forEach(function (m, i) { var c = pal[m.purpose] || pal.unsorted || [138, 147, 166]; var s = mixRgb(c, [200, 206, 214], 0.35); attr.setXYZW(i, s[0] / 255, s[1] / 255, s[2] / 255, 1); });
      inst.userData.uids = stations.map(function (m) { return m.uid; }); inst.userData.kind = 'moon'; inst.userData.noStandIn = true; inst.frustumCulled = false;
      inst.name = 'cm3d-stations'; add(T.root, inst); M.stations = inst; M.sAttr = attr;
    }
    if (rest.length) {
      var rock = T.rock(), bm = rgbaMaterial(THREE), belt = new THREE.InstancedMesh(rock, bm, rest.length);
      var battr = new THREE.InstancedBufferAttribute(new Float32Array(rest.length * 4), 4); battr.setUsage(THREE.DynamicDrawUsage);
      var bgeo = rock.clone(); bgeo.setAttribute('instanceRGBA', battr); belt.geometry = bgeo; own(bgeo);
      // the arc of the belt starts at the eye's left and runs round the near side, so the first rocks are in view
      var toCam = cam.position.clone().sub(P.grp.position); toCam.y = 0; if (toCam.lengthSq() < 1e-6) toCam.set(0, 0, 1); toCam.normalize();
      var a0 = Math.atan2(toCam.z, toCam.x) - Math.PI / 2, Rb = P.r3 * 2.05, mat = new THREE.Matrix4(), Q = new THREE.Quaternion(), E = new THREE.Euler(), V = new THREE.Vector3(), Sx = new THREE.Vector3();
      M.base = [];
      rest.forEach(function (m, i) {
        var c = pal[m.purpose] || pal.unsorted || [138, 147, 166], a = beltAlpha(m.days), ang = a0 + i * (TAU / Math.max(rest.length, 1));
        var size = P.r3 * (0.035 + 0.022 * Math.log10(1 + Math.max(0, Number(m.bytes) || 0)) / 3);
        var h = hash3(i, 3, 5, (Number(P.p.seed) >>> 0) || 1);
        V.set(Math.cos(ang) * Rb, Math.sin(ang * 2 + h * 3) * P.r3 * 0.12 - P.r3 * 0.1, Math.sin(ang) * Rb); Q.setFromEuler(E.set(h * 6, h * 9, h * 3)); Sx.setScalar(size);
        mat.compose(V, Q, Sx); belt.setMatrixAt(i, mat);
        battr.setXYZW(i, c[0] / 255, c[1] / 255, c[2] / 255, a); M.base.push([c[0] / 255, c[1] / 255, c[2] / 255, a]);
      });
      belt.position.copy(P.grp.position); belt.instanceMatrix.needsUpdate = true; belt.computeBoundingSphere();
      belt.userData.uids = rest.map(function (m) { return m.uid; }); belt.userData.kind = 'folder'; belt.userData.noStandIn = true; belt.frustumCulled = false;
      belt.name = 'cm3d-belt'; add(T.root, belt); M.belt = belt; M.bAttr = battr;
    }
    T.moons = M;
  }
  function disposeMoons() {
    var M = T && T.moons; if (!M) return;
    [M.stations, M.belt].forEach(function (o) {
      if (!o) return; if (o.parent) o.parent.remove(o);
      o.geometry.dispose(); if (o.dispose) o.dispose();
      drop(T.own, o.geometry);
      if (o.material) { drop(T.mats, o.material); o.material.dispose(); }   // its material leaves the teardown's list too (it went twice)
    });
    if (T.ctx && T.ctx.hover && T.ctx.hover.from === 'belt') T.ctx.hover = null;
    T.moons = null;
  }
  function updateMoons(world, ctx, dt) {
    var A = audit(ctx), uid = focusTarget(world, ctx), M = T.moons;
    var sig = uid ? JSON.stringify(moonsOf(world, uid).map(function (m) { return [m.uid, m.purpose, m.days, m.bytes]; })) : '';
    if (!uid) { if (M) disposeMoons(); }
    else if (!M || M.uid !== uid || M.sig !== sig) buildMoons(world, ctx, uid);
    M = T.moons;
    var fade = {}, beltOut = null;
    if (M) {
      var THREE = ctx.THREE, P = T.planets[M.uid], cam = ctx.camera, d = P ? cam.position.distanceTo(P.grp.position) : Infinity, mat = new THREE.Matrix4(), Q = new THREE.Quaternion(), V = new THREE.Vector3(), Sx = new THREE.Vector3(), E3 = new THREE.Euler();
      if (M.stations && P) {
        var seed = (Number(P.p.seed) >>> 0) || 1;
        M.list.forEach(function (m, i) {
          var dx = (m.x - P.p.x), dz = (m.y - P.p.y), a = Math.max(P.r3 * 1.3, Math.hypot(dx, dz)), op = moonOpacity(d, a);
          var alt = (hash3(i, 9, 2, seed) - 0.5) * P.r3 * 0.4;
          V.set(P.p.x + dx, alt, P.p.y + dz); Q.setFromEuler(E3.set(0.38, i, 0.12)); Sx.setScalar(P.r3 * 0.11);
          mat.compose(V, Q, Sx); M.stations.setMatrixAt(i, mat);
          M.sAttr.setW(i, op); fade[m.uid] = Math.round(op * 1000) / 1000;
        });
        M.stations.instanceMatrix.needsUpdate = true; M.sAttr.needsUpdate = true;
      }
      if (M.belt) {
        var b = M.belt, at = M.bAttr, arr = at.array, items = [];
        // These are folder markers, not measured orbital traffic.
        b.updateMatrixWorld(true);
        M.rest.forEach(function (m, i) {
          b.getMatrixAt(i, mat); V.setFromMatrixPosition(mat).applyMatrix4(b.matrixWorld);
          var s = ctx.project(V.x, V.y, V.z), base = M.base[i];
          var want = [base[0], base[1], base[2], beltAlpha(m.days)], err = 0;
          for (var k = 0; k < 4; k++) { var got = arr[i * 4 + k], ref = k < 3 && M.hover && M.hover.i === i ? Math.min(1, want[k] * 1.3) : want[k]; err = Math.max(err, Math.abs(got - ref)); }
          var cur = arr[i * 4] + arr[i * 4 + 1] + arr[i * 4 + 2], bs = base[0] + base[1] + base[2];
          items.push({ uid: m.uid, name: m.label, purpose: m.purpose, days: m.days, x: Math.round(s.x * 10) / 10, y: Math.round(s.y * 10) / 10, rgbaErr: Math.round(err * 100000) / 100000,
            lift: Math.round((bs > 0 ? cur / bs - 1 : 0) * 1000) / 1000, alpha: Math.round(arr[i * 4 + 3] * 1000) / 1000, onScreen: !!s.onScreen });
        });
        beltOut = { count: M.rest.length, calls: M.beltCalls != null ? M.beltCalls : 1, colorItemSize: at.itemSize, items: items, hover: M.hover ? { uid: M.hover.uid, name: M.hover.name, instanceId: M.hover.i } : null, planet: M.uid };
      }
    }
    A.scene.moons = M && M.stations ? M.list.length : 0; A.scene.moonFade = fade; A.scene.moonsOf = M ? M.uid : null;
    A.scene.belt = beltOut || { count: 0, calls: 0, colorItemSize: 4, items: [], hover: null, planet: null };
    ctx.moonLabels = M ? M.list.map(function (m) { return { uid: m.uid, name: m.label, opacity: fade[m.uid] }; }) : [];
  }
  // hover over one belt rock: its RGB times 1.3 (clamped), only that instance re-written; leaving restores it
  function hoverBelt(hit) {
    var M = T && T.moons; if (!M || !M.belt) return null;
    var i = hit && hit.uid != null && hit.instanceId != null && M.belt.userData.uids[hit.instanceId] === hit.uid ? hit.instanceId : -1;
    if (M.hover && M.hover.i !== i) { var b0 = M.base[M.hover.i]; setRGBAAt(M.bAttr, M.hover.i, b0[0], b0[1], b0[2], b0[3]); M.hover = null; }
    if (i >= 0 && (!M.hover || M.hover.i !== i)) {
      var b = M.base[i], m = M.rest[i];
      setRGBAAt(M.bAttr, i, Math.min(1, b[0] * 1.3), Math.min(1, b[1] * 1.3), Math.min(1, b[2] * 1.3), b[3]);
      M.hover = { i: i, uid: m.uid, name: m.label };
    }
    if (T.ctx) T.ctx.hover = M.hover ? { uid: M.hover.uid, name: M.hover.name, kind: 'folder', from: 'belt' } : (T.ctx.hover && T.ctx.hover.from === 'belt' ? null : T.ctx.hover);
    return M.hover;
  }

  // ---- shared geometries (one per session)
  function sessionGeos(THREE) {
    var cache = {};
    T.sphere = function (ph) { var k = ph ? 's1' : 's0'; if (!cache[k]) { cache[k] = own(new THREE.SphereGeometry(1, ph ? 24 : 48, ph ? 16 : 32)); } return cache[k]; };
    // a body's own shape (ui/kinds.js PURPOSE.shape): a sphere; a gem (config: flat facets that each catch the light whole);
    // a lump (archive, not sorted: the sphere pushed in and out by a seeded noise, 0.9-1.06 r)
    T.body = function (shape, ph, seed) {
      if (shape === 'gem') {
        if (!cache.gem) { var gg = new THREE.IcosahedronGeometry(1, 1); gg.computeVertexNormals(); cache.gem = own(gg); }
        return cache.gem;
      }
      if (shape === 'lumpy') {
        var k = 'lump' + (ph ? 1 : 0) + ':' + seed;
        if (!cache[k]) {
          var lg = new THREE.SphereGeometry(1, ph ? 24 : 48, ph ? 16 : 32), pa = lg.attributes.position, s = seed % 9973;
          for (var i = 0; i < pa.count; i++) {
            var x = pa.getX(i), y = pa.getY(i), z = pa.getZ(i), n = 0.65 * vnoise(x * 1.7 + 3, y * 1.7, z * 1.7, s) + 0.35 * vnoise(x * 4.1, y * 4.1 + 7, z * 4.1, s + 1);
            var r = 0.9 + 0.16 * n; pa.setXYZ(i, x * r, y * r, z * r);
          }
          lg.computeVertexNormals(); cache[k] = own(lg);
        }
        return cache[k];
      }
      return T.sphere(ph);
    };
    T.rock = function () {
      if (cache.rock) return cache.rock;
      var src = T.ctx && T.ctx.assets && T.ctx.assets.rocks, geo = null;
      if (src) { var r0 = Array.isArray(src) ? src[0] : (src.geometry || src.isBufferGeometry ? src : (src.crater || src.grey || src.orange)); geo = r0 && (r0.isBufferGeometry ? r0 : r0.geometry) || null; }
      if (!geo) {                                                      // the fallback: an icosahedron, its corners pushed about
        geo = new THREE.IcosahedronGeometry(1, 0); var p = geo.attributes.position;
        for (var i = 0; i < p.count; i++) { var k = 0.75 + hash3(Math.round(p.getX(i) * 9), Math.round(p.getY(i) * 9), Math.round(p.getZ(i) * 9), 5) * 0.45; p.setXYZ(i, p.getX(i) * k, p.getY(i) * k, p.getZ(i) * k); }
        geo.computeVertexNormals(); own(geo);
      }
      cache.rock = geo; return geo;
    };
    T.stationGeo = function () {
      if (cache.station) return cache.station;
      // a saucer station (the comps' sub-folder station, o8 item 4): two shallow discs round a hub, a spire under it, the rim
      // a thin bright band; one lathe, so every station is one instance of one geometry
      var prof = [[0, -1.25], [0.12, -1.2], [0.14, -0.5], [0.42, -0.34], [1.0, -0.12], [1.18, -0.02], [1.18, 0.04], [1.0, 0.14], [0.55, 0.3], [0.36, 0.52], [0.18, 0.62], [0, 0.66]];
      var lathe = new THREE.LatheGeometry(prof.map(function (q) { return new THREE.Vector2(q[0], q[1]); }), 16);
      var geo = lathe.toNonIndexed(); lathe.dispose(); geo.computeVertexNormals();
      cache.station = own(geo); return geo;
    };
  }

  // ------------------------------------------------------------------ the module
  function build(scene, world, ctx) {
    if (T) dispose();
    var THREE = ctx.THREE;
    T = { scene: scene, ctx: ctx, root: new THREE.Group(), own: [], mats: [], planets: {}, moons: null, machines: [], mapK: 1, nearest: null };
    T.root.name = 'cm3d-space'; scene.add(T.root);
    sessionGeos(THREE); T.tmpV = new THREE.Vector3(); T.up = new THREE.Vector3(0, 1, 0);
    buildSky(scene, world, ctx);
    buildStars(scene, world, ctx);
    buildDust(scene, world, ctx);
    buildSun(scene, world, ctx);
    buildEnv(scene, ctx);
    buildRim(ctx);
    buildAtmo(ctx);
    buildFlare(ctx);
    syncPlanets(world, ctx);
    buildMachines(world, ctx);
    bakeSkyStep(ctx, !phoneQ(ctx));
    T.rev = world.rev;
  }
  // a new rev (the page's data moved): sync in place, nothing rebuilt that did not change, the bakes kept
  function rebuild(scene, world, ctx) {
    if (!T || T.scene !== scene) { build(scene, world, ctx); return; }
    T.ctx = ctx; syncPlanets(world, ctx); T.rev = world.rev;
  }
  function update(world, dt, ctx) {
    if (!T) return;
    T.ctx = ctx;
    var A = audit(ctx);
    bakeSkyStep(ctx, false);
    T.mapK = mapK(ctx);
    updateSky(world, ctx, ctx.dt || 0);
    updateRim(world, ctx);
    updateEnv(world, ctx, ctx.dt || 0);
    var dpr = ctx.renderer.getPixelRatio();
    if (T.stars) T.stars.mat.uniforms.uDpr.value = dpr;
    if (T.dust) {
      var u = T.dust.mat.uniforms, sp = world.ship || {}, spd = Number(sp.spd) || 0, h = Number(sp.hdg) || 0;
      // the 3D flight's own velocity (its climb and dive too); boosting draws the motes out into speed lines
      var vel = Array.isArray(sp.velocity) && sp.velocity.length === 3 ? sp.velocity : null;
      u.uEye.value.copy(ctx.camera.position);
      if (vel) u.uVel.value.set(Number(vel[0]) || 0, Number(vel[1]) || 0, Number(vel[2]) || 0); else u.uVel.value.set(Math.cos(h) * spd, 0, Math.sin(h) * spd);
      u.uStreak.value = world.reduced ? 0 : sp.boost ? 0.16 : 0.05; u.uAlpha.value = (1 - T.mapK) * clamp(0.35 + spd / 400, 0.35, 1);
      T.dust.lines.visible = T.mapK < 0.999;
      A.scene.dustStreak = u.uStreak.value;
    }
    updateSun(world, ctx, dt);
    syncPlanetsIfNeeded(world, ctx);
    updateAtmo(world, ctx);
    updatePlanets(world, ctx, dt);
    if (T.real) {
      realPump(world, ctx); realNoiseStep(ctx);
      var R = T.real, st = {}; Object.keys(T.planets).forEach(function (u) { var P = T.planets[u]; st[u] = P.real ? P.realState || 'waiting' : P.realState === 'failed' ? 'failed' : 'painted'; });
      A.scene.realPlanets = { maps: R.maps, mb: Math.round(R.bytes / 1048576 * 10) / 10, loadMs: R.ms.slice(), noise: !!R.noiseTex, sky: !!(R.sky && R.sky.isTexture), errors: R.errors.slice(-4), planets: st };
    } else A.scene.realPlanets = null;
    updateFlare(world, ctx, ctx.dt || 0);
    updateMachines(world, ctx);
    updateMoons(world, ctx, dt);
    if (T.moons && T.moons.belt && !T.moons.belt.onAfterRender.cm) {
      // the belt's draws in the frame last drawn (a stepped run draws once, at its end), written to the audit as it draws
      var M = T.moons, R0 = ctx.renderer;
      M.belt.onAfterRender = function () {
        var f = R0.info.render.frame; if (M.drawFrame !== f) { M.drawFrame = f; M.beltCalls = 0; } M.beltCalls++;
        var A0 = T && T.ctx && T.ctx.audit; if (A0 && A0.scene && A0.scene.belt) A0.scene.belt.calls = M.beltCalls;
      };
      M.belt.onAfterRender.cm = 1;
    }
    A.scene.ditheredMaterials = countDithered(ctx);
    var mem = ctx.renderer.info && ctx.renderer.info.memory;            // the moons' build and dispose leave no geometry behind (K3)
    A.scene.memory = mem ? { geometries: mem.geometries, textures: mem.textures } : null;
  }
  function syncPlanetsIfNeeded(world, ctx) {
    var painted = !!(ctx.debug && ctx.debug.paintedPlanets);
    if (world.rev !== T.rev || painted !== !!T.painted) { syncPlanets(world, ctx); T.rev = world.rev; T.painted = painted; }
  }
  function countDithered(ctx) {
    var n = 0, seen = [];
    ctx.scene.traverse(function (o) { var m = o.material; if (m && !Array.isArray(m) && m.dithering && seen.indexOf(m) < 0) { seen.push(m); n++; } });
    return n;
  }
  function dispose() {
    if (!T) return;
    disposeMoons();
    if (T.scene && T.fog && T.scene.fog === T.fog) T.scene.fog = T.keepFog || null;
    if (T.root.parent) T.root.parent.remove(T.root);
    // each geometry, texture, target and material once (a material is in T.mats and may sit on a machine; a texture may be
    // a material's map and in T.own)
    var gone = new Set(), once = function (x) { if (x && typeof x.dispose === 'function' && !gone.has(x)) { gone.add(x); x.dispose(); } };
    T.root.traverse(function (o) { once(o.geometry); });
    Object.keys(T.planets).forEach(function (u) { var P = T.planets[u]; once(P.artTex); once(P.tex); });
    T.mats.forEach(function (m) { once(m.map); once(m); });
    T.own.forEach(once);
    if (T.sky) { once(T.sky.rt); once(T.sky.bake); }
    if (T.machines) T.machines.forEach(function (m) { once(m.pts.geometry); once(m.pts.material); });
    T = null;
  }
  CM3D.module('space', { build: build, rebuild: rebuild, update: update, dispose: dispose });

  // __3dPick(x, y, {hover: true}) hovers as the pointer would: the core's pick, then the belt hover on its answer
  var basePick = g.__3dPick;
  if (typeof basePick === 'function' && !basePick.cmSpace) {
    g.__3dPick = function (x, y, opts) {
      var r = basePick(x, y);
      if (opts && opts.hover && T) hoverBelt(r && r.instanceId != null ? r : null);
      return r;
    };
    g.__3dPick.cmSpace = true;
  }

  // ------------------------------------------------------------------ demo pages: ?demo3d=sky, ?demo3d=belt
  // sky: the region's cube bake alone (no planets, no sun), the camera on the sky's bright side
  CM3D.demo('sky', {
    build: function (scene, ctx, pr) {
      var THREE = ctx.THREE, region = String(pr.region || 'code');
      T = { scene: scene, ctx: ctx, root: new THREE.Group(), own: [], mats: [], planets: {}, machines: [], mapK: 0 };
      scene.add(T.root); sessionGeos(THREE);
      buildSky(scene, { sky: null, planets: [] }, ctx);
      bakeSkyStep(ctx, true);
      var u = T.sky.mat.uniforms, pal = regionPalette(ctx.skin3d, region);
      setPal(u, 'A', pal); setPal(u, 'B', pal); u.uMix.value = 0;
      var b = T.sky.bright, cam = ctx.camera; cam.position.set(0, 0, 0); cam.up.set(0, 1, 0); cam.lookAt(b[0], b[1], b[2]); cam.near = 0.1; cam.far = 10; cam.updateProjectionMatrix();
      scene.background = null;
      DEMO = { kind: 'sky', region: region, palette: pal };
      CM3D.space.demo = { kind: 'sky', region: pal.region, base: pal.base, colours: pal.colours.slice(), nebula: pal.nebula, seed: T.sky.seed, faces: T.sky.faces };
    },
    update: function () {},
    dispose: function () { dispose(); DEMO = null; }
  });
  // belt: three rocks on black, purposes code, notes and data at alpha 1, 0.45 and 1 (&alpha=1: all 1) [belt-pixels]
  CM3D.demo('belt', {
    build: function (scene, ctx, pr) {
      var THREE = ctx.THREE;
      T = { scene: scene, ctx: ctx, root: new THREE.Group(), own: [], mats: [], planets: {}, machines: [], mapK: 0 };
      scene.add(T.root); sessionGeos(THREE);
      scene.background = new THREE.Color(0x000000);
      if (ctx.lights) { if (ctx.lights.ambient) ctx.lights.ambient.intensity = 0; if (ctx.lights.sky) ctx.lights.sky.intensity = 0; if (ctx.lights.sun) ctx.lights.sun.intensity = 0; }
      var key = new THREE.DirectionalLight(0xffffff, 2.2); key.position.set(0, 0, 10); T.root.add(key); T.root.add(key.target);
      var cols = [[78, 121, 200], [58, 160, 184], [208, 138, 60]], alphas = String(pr.alpha) === '1' ? [1, 1, 1] : [1, 0.45, 1];
      var geo = new THREE.SphereGeometry(1, 32, 16); own(geo);
      var mat = rgbaMaterial(THREE, { flatShading: false }), inst = new THREE.InstancedMesh(geo, mat, 3);
      var attr = new THREE.InstancedBufferAttribute(new Float32Array(12), 4), g2 = geo.clone(); g2.setAttribute('instanceRGBA', attr); inst.geometry = g2; own(g2);
      var M = new THREE.Matrix4(), cam = ctx.camera;
      cam.aspect = Math.max(1, g.innerWidth || 1) / Math.max(1, g.innerHeight || 1);
      cam.position.set(0, 0, 100); cam.up.set(0, 1, 0); cam.lookAt(0, 0, 0); cam.near = 1; cam.far = 1000; cam.updateProjectionMatrix(); cam.updateMatrixWorld(true);
      var sz = Math.tan(cam.fov * D2R / 2) * 100;
      [-1, 0, 1].forEach(function (k, i) { M.makeScale(sz * 0.22, sz * 0.22, sz * 0.22); M.setPosition(k * sz * 0.9, 0, 0); inst.setMatrixAt(i, M);
        attr.setXYZW(i, cols[i][0] / 255, cols[i][1] / 255, cols[i][2] / 255, alphas[i]); });
      inst.userData.uids = ['demo:rock1', 'demo:rock2', 'demo:rock3']; T.root.add(inst);
      var where = function () {
        cam.updateMatrixWorld(true);
        var pts = [-1, 0, 1].map(function (k) { var p = new THREE.Vector3(k * sz * 0.9, 0, 0).project(cam); return { x: (p.x + 1) / 2 * g.innerWidth, y: (1 - p.y) / 2 * g.innerHeight }; });
        CM3D.space.demo = { kind: 'belt', rocks: pts.map(function (p, i) { return { x: Math.round(p.x), y: Math.round(p.y), rgb: cols[i], alpha: alphas[i] }; }) };
      };
      where(); DEMO = { kind: 'belt', where: where };
    },
    update: function () { if (DEMO && DEMO.where) DEMO.where(); },
    dispose: function () { dispose(); }
  });

  // ------------------------------------------------------------------ the public face (pure helpers for tests, a few reads)
  CM3D.space = {
    pickWorld: pickWorld,
    NEUTRAL: NEUTRAL, beltAlpha: beltAlpha, moonOpacity: moonOpacity, cloudDensity: cloudDensity, activity: activity, sunShell: sunShell,
    lightBucket: lightBucket, lightDir: lightDir, regionPalette: regionPalette, envFor: envFor, skyTable: skyTable, raySphere: raySphere,
    hash3: hash3, vnoise: vnoise, lobesOf: lobesOf, lobeMask: lobeMask, brightSide: brightSide, kRlhFor: kRlhFor, hexRgb: hexRgb, rgbHex: rgbHex,
    // where a planet is drawn (x, y, z, radius) for the routes and effects: its centre and the radius of its body
    planet: function (uid) { var P = T && T.planets[uid]; return P ? { x: P.grp.position.x, y: P.grp.position.y, z: P.grp.position.z, r: P.r3 } : null; },
    mapK: function () { return T ? T.mapK : 0; },
    hover: function (hit) { return hoverBelt(hit); },
    demo: null
  };
})(typeof globalThis !== 'undefined' ? globalThis : this);
