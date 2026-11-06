#!/usr/bin/env python3
"""test_folder_kinds.py — what each folder is: one body per purpose, the condition
overlays, the data and media guesses, the owner's glyph, the skin's planet palettes, the one surface function the 2D art
and the 3D bake share, and the backdrop recipe.

Run: python3 -m unittest -q test_folder_kinds   (node needed for the page half; else those tests skip)
"""
import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

import testenv  # noqa: F401  (first: clears what steers the tool)
import test_game

ROOT = Path(__file__).resolve().parent

# bodyArt exactly as master c89af9f drew it (render-game.py, before the surface moved into ui/kinds.js surfaceAt), kept
# here as the reference the refactor must reproduce cell for cell; only its name and its cache key's first word differ
OLD_BODY_ART = r"""
function oldBodyArt(r,style,theme,seed,ph,sq,fade){
  r=Math.max(2,Math.ceil(r/2)*2); sq=sq||1; fade=fade|0;
  const s=seed%997, grey=SHADE.grey;
  return artCache(['OLDb',r,style,theme,s,ph,sq,fade,GB?1:0,THEME_NAME].join('|'),()=>{
    const art=document.createElement('canvas'); art.width=art.height=r*2+8;
    const g=art.getContext('2d'),mid=r+4,P=STYLE[style]||themePal(theme),TL=tileOf(style,theme);
    const phase=ph*TAU/PHASES, glow=P.glow;
    for(const e of sphereGeo(r,sq)){
      const X=mid+e.x,Y=mid+e.y;
      if(e.edge){ g.fillStyle=GB?'#0f380f':SHADE.edge; g.fillRect(X,Y,2,2); continue; }
      const lon=e.lon+phase, lat=e.lat, lw=((lon%TAU)+TAU)%TAU;
      let lvl=Math.max(0,Math.min(4,Math.floor(e.light*4+.6))), base, emit=null;
      if(style==='city'){
        const n=sfbm(lon,lat,s,2.0);
        base=n>.53?(n>.70?'#b6f59a':P.b):(n<.36?mixPixel(P.a,'#062a8a',.45):P.a);
        if(Math.abs(lat)>1.18) base=P.c;
        const cl=sfbm(e.lon+phase*1.45,lat*1.25,s+17,3.1);
        if(cl>.64){ base=P.c; lvl=Math.min(4,lvl+1); }
        else if(n>.53&&e.light<.08&&hash3(Math.floor(lw*10),Math.floor(lat*10),3,s)>.78) emit=P.d;
      }else if(style==='ocean'){
        const n=sfbm(lon+Math.sin(lat*3+s)*.5,lat,s,2.4);
        base=n>.56?P.b:(n<.38?mixPixel(P.a,'#003a8c',.45):P.a);
        if(sfbm(e.lon+phase*1.6,lat*1.4,s+23,3.4)>.67){ base=P.c; lvl=Math.min(4,lvl+1); }
      }else if(style==='desert'){
        const v=Math.sin(lat*12+sfbm(lon,lat,s,1.6)*7);
        base=v>.2?P.a:(v>-.5?mixPixel(P.a,P.b,.5):P.b);
        if(sfbm(e.lon+phase*1.3,lat,s+29,2.6)>.7) base=P.c;
      }else if(style==='ice'){
        const n=sfbm(lon,lat,s,2.8); base=P.a;
        if(Math.abs(n-.5)<.04) base=P.b;
        if(Math.abs(lat)>1.0) base=P.c;
      }else if(style==='toxic'){
        const n=sfbm(lon,lat,s,2.6); base=n>.58?P.b:(n<.36?P.c:P.a);
      }else if(style==='blight'){
        const n=sfbm(lon,lat,s,2.2); base=P.a;
        if(Math.abs(n-.5)<.06) base=P.b;
        if(Math.abs(n-.5)<.02) emit=P.c;
      }else if(style==='gas'){
        const lg=e.lon+phase*(1+.35*Math.sin(lat*4));
        const v=lat*5.5+(sfbm(lg,lat,s,1.5)-.5)*2.4, bands=[P.a,P.b,P.d,P.c,P.a,P.d];
        base=bands[((Math.floor(v)%6)+6)%6];
        const dl=Math.hypot(((((lg-1.2)%TAU)+TAU)%TAU)-Math.PI,(lat-.35)*2.2);
        if(dl<.32) base='#ff3b8a';
      }else if(style==='star'){
        const n=sfbm(lon,lat,s,3.6); base=n>.6?P.c:(n<.4?P.b:P.a);
        lvl=e.rr>.82?2:(e.rr>.5?3:4);
        if(TL) base=tileTex(TL,e,phase,r,s);
      }else{
        const n=sfbm(lon,lat,s,2.4); base=n>.52?P.b:P.a;
        if(n>.7) base=P.c;
        if(style==='rock'&&sfbm(lon,lat,s+5,5.5)<.3){ base=P.b; lvl=Math.max(0,lvl-1); }
      }
      if(TL&&style!=='star'){ base=tileTex(TL,e,phase,r,s); emit=null; lvl=Math.max(0,Math.min(4,Math.floor(e.light*4+.6))); }
      if(fade&&!GB){ base=mixPixel(base,grey,fade*.19); if(emit) emit=mixPixel(emit,grey,fade*.19); if(fade>=3&&lvl>2) lvl=2; }
      const cs=shades(base); g.fillStyle=(emit&&!GB)?emit:cs[lvl]; g.fillRect(X,Y,2,2);
      if(!emit&&style!=='star'&&e.rr>.84){
        if(e.light>.25){ g.fillStyle=cs[4]; g.fillRect(X,Y,2,2); }
        else if(!GB&&e.light<0){ g.fillStyle=mixPixel(glow,SHADE.rim,.45); g.fillRect(X,Y,2,2); }
      }
    }
    return art; });
}
"""

# a canvas that records every cell the art paints (fillStyle and place), for the comparisons below
RECORDER = r"""
  // a canvas that records every cell the art paints (fillStyle and place) and gives them back as pixels (getImageData reads
  // the recorded cells, last paint wins), so the audit measures what was painted, as it does on a real canvas
  const recHex = (c) => (/^#[0-9a-f]{6}$/i.test(c) ? [1, 3, 5].map((i) => parseInt(c.slice(i, i + 2), 16)) : null);
  const recCanvas = () => { const cells = []; let fs = ''; const cv = { width: 0, height: 0, toDataURL: () => 'data:,', cells: cells };
    const g = new Proxy({}, { get: (o, k) => k === 'fillRect' ? ((x, y, w, h) => cells.push(fs + '@' + x + ',' + y + ',' + w + ',' + h))
      : k === 'getImageData' ? ((x0, y0, w, h) => { const data = new Uint8ClampedArray(w * h * 4);
          cells.forEach((c) => { const [col, at] = c.split('@'), rgb = recHex(col), [x, y, cw, ch] = at.split(',').map(Number); if (!rgb) return;
            for (let yy = y; yy < y + ch; yy++) for (let xx = x; xx < x + cw; xx++) { if (xx < x0 || yy < y0 || xx >= x0 + w || yy >= y0 + h) continue;
              const i = ((yy - y0) * w + (xx - x0)) * 4; data[i] = rgb[0]; data[i + 1] = rgb[1]; data[i + 2] = rgb[2]; data[i + 3] = 255; } });
          return { data: data, width: w, height: h }; })
      : k === 'cells' ? cells : (k in o ? o[k] : (...a) => {}), set: (o, k, v) => { if (k === 'fillStyle') fs = String(v); o[k] = v; return true; } });
    cv.getContext = () => g; return cv; };
  const realCreate = document.createElement;
  document.createElement = (tag) => (tag === 'canvas' ? recCanvas() : realCreate(tag));
"""


def write_tree(root, files):
    """files: {relative path: size}; every file a few bytes old."""
    for rel, size in files.items():
        path = Path(root, rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * size)


class GuessesAndSettings(unittest.TestCase):
    """districts.py: Data and Media guessed from the files, the owner's kind and glyph read from areas.json."""

    def build(self, files, areas=None):
        import config as C
        import districts
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        root = os.path.realpath(tmp)
        work = Path(root, "work")
        for name, tree in files.items():
            write_tree(work / name, tree)
        areas_path = Path(root, "areas.json")
        areas_path.write_text(json.dumps({"version": 1, "areas": areas or {}}), encoding="utf-8")
        cfg = json.loads(json.dumps(C.DEFAULTS))
        cfg.update({"work_root": str(work), "processes": False, "crontab_file": str(Path(root, "no-crontab")),
                    "areas_json": str(areas_path), "tags_md": "", "cron_syslog_file": ""})
        chars, _, _ = districts.build(time.time(), [], {}, cfg)
        return {c["name"]: c for c in chars if c["kind"] == "area"}

    def test_eighty_percent_csv_is_data_and_mostly_images_is_media(self):
        got = self.build({
            "Tables": {**{f"t{i}.csv": 40 for i in range(8)}, "load.py": 40, "clean.py": 40},
            "Shots": {**{f"s{i}.png": 40 for i in range(5)}, "a.mp4": 40, "notes.md": 40},
            "Code": {"a.py": 10, "b.py": 10, "c.md": 10},
            "Notes": {"a.md": 10, "b.md": 10, "c.txt": 10},
        })
        self.assertEqual(got["Tables"]["area_kind"], "data")
        self.assertTrue(got["Tables"]["kind_guessed"])
        self.assertEqual(got["Shots"]["area_kind"], "media")
        self.assertEqual(got["Code"]["area_kind"], "code")
        self.assertEqual(got["Notes"]["area_kind"], "knowledge")

    def test_the_owner_rules_kind_and_glyph_and_a_word_the_map_does_not_know_is_no_ruling(self):
        got = self.build({"Money": {"a.py": 10, "b.py": 10}, "Odd": {"a.md": 10, "b.md": 10}, "Plain": {"x.md": 10}},
                         areas={"Money": {"kind": "data", "glyph": "money", "guessed": False},
                                "Odd": {"kind": "spaceship", "glyph": "rocket"}})
        self.assertEqual((got["Money"]["area_kind"], got["Money"]["kind_guessed"], got["Money"]["glyph"]), ("data", False, "money"))
        self.assertEqual(got["Odd"]["area_kind"], "knowledge", "an unknown kind falls back to the guess")
        self.assertTrue(got["Odd"]["kind_guessed"])
        self.assertNotIn("glyph", got["Odd"], "an unknown glyph is dropped")
        self.assertNotIn("glyph", got["Plain"], "no glyph key unless the owner gave one")

    def test_config_planets_may_say_data_and_media(self):
        import config as C
        self.assertIn("data", C.AREA_KINDS)
        self.assertIn("media", C.AREA_KINDS)
        self.assertEqual(len(C.AREA_GLYPHS), 12)


class PlanetPalettes(unittest.TestCase):
    """themepacks: the optional "planets" block, five colours and four thresholds per purpose."""

    def skin(self, planets):
        return {"name": "x", "names": {"carrier": "a", "merged": "b", "worst": "c", "wingmate": "d"},
                "sprites": json.loads((ROOT / "skins" / "neutral.json").read_text(encoding="utf-8"))["sprites"],
                "planets": planets}

    def test_a_four_colour_entry_is_refused(self):
        import themepacks as tp
        errs = tp.validate_skin(self.skin({"code": {"colours": ["#000000", "#111111", "#222222", "#333333"],
                                                     "thresholds": [.2, .4, .6, .8]}}), "x")
        self.assertEqual(errs, ["planets.code: colours: exactly five #rrggbb, dark to light"])

    def test_thresholds_ascend_inside_zero_one_and_purposes_are_known(self):
        import themepacks as tp
        five = ["#000000", "#111111", "#222222", "#333333", "#444444"]
        self.assertTrue(tp.validate_skin(self.skin({"code": {"colours": five, "thresholds": [.2, .6, .4, .8]}}), "x"))
        self.assertTrue(tp.validate_skin(self.skin({"code": {"colours": five, "thresholds": [0, .4, .6, .8]}}), "x"))
        self.assertTrue(tp.validate_skin(self.skin({"spaceship": {"colours": five, "thresholds": [.2, .4, .6, .8]}}), "x"))
        self.assertEqual(tp.validate_skin(self.skin({"code": {"colours": five, "thresholds": [.2, .4, .6, .8], "glow": "#ffffff"}}), "x"), [])

    def test_the_skins_here_carry_all_nine_purposes(self):
        import themepacks as tp
        # <name>-3d.json: 3D data, not a skin; <name>-ship3d.json / <name>-planets3d.json: its baked ship and planet maps
        paths = sorted(p for p in (ROOT / "skins").glob("*.json") if not p.stem.endswith(("-3d", "-ship3d", "-planets3d")))
        self.assertTrue(paths)
        for path in paths:
            skin = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(tp.validate_skin(skin, path.stem), [], path.name)
            self.assertEqual(sorted(skin["planets"]), sorted(tp.PURPOSES), path.name)

    def test_a_theme_may_add_the_purpose_surfaces_aurora_crystal_and_asteroid(self):
        import themepacks as tp
        pack = json.loads((ROOT / "themes" / "synthwave.json").read_text(encoding="utf-8"))
        self.assertEqual(tp.validate(pack, "synthwave"), [], "the shipped pack, without them")
        pack["style"]["aurora"] = {"a": "#102040", "b": "#30e0a0", "c": "#f070d0", "glow": "#40ffb0"}
        pack["style"]["crystal"] = {"a": "#20a0a0", "b": "#105060", "c": "#e0ffff", "d": "#60e0e0", "glow": "#80fff0"}
        pack["tiles"]["asteroid"] = pack["tiles"]["rock"]
        self.assertEqual(tp.validate(pack, "synthwave"), [], "aurora and crystal styles, an asteroid tile")
        pack["style"]["lava"] = pack["style"]["rock"]
        self.assertTrue(any(e.startswith("style: exactly") for e in tp.validate(pack, "synthwave")), "an unknown surface is refused")
        del pack["style"]["lava"]
        del pack["style"]["gas"]
        self.assertTrue(any(e.startswith("style: exactly") for e in tp.validate(pack, "synthwave")), "the old surfaces stay required")

    def test_a_theme_pack_may_bring_its_own_planets(self):
        import themepacks as tp
        pack = json.loads((ROOT / "themes" / "classic.json").read_text(encoding="utf-8"))
        pack["planets"] = {"data": {"colours": ["#000000", "#111111", "#222222", "#333333"], "thresholds": [.2, .4, .6, .8]}}
        self.assertIn("planets.data: colours: exactly five #rrggbb, dark to light", tp.validate(pack, "classic"))
        pack["planets"]["data"]["colours"].append("#444444")
        self.assertEqual(tp.validate(pack, "classic"), [])


@unittest.skipUnless(shutil.which("node"), "node not installed")
class PageKinds(unittest.TestCase):
    """The page half, in the stub DOM: surfaceAt, the seeds, the bodies, the glyph and the audit."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        work = Path(cls.tmp.name, "work")
        write_tree(work / "Money", {"a.py": 30, "b.py": 30, "c.py": 30})
        write_tree(work / "Tables", {**{f"t{i}.csv": 30 for i in range(8)}, "x.py": 30, "y.py": 30})
        write_tree(work / "Pics", {f"p{i}.png": 30 for i in range(4)})
        write_tree(work / "Notes", {"a.md": 30, "b.md": 30})
        Path(work, "Empty").mkdir()
        areas = Path(cls.tmp.name, "areas.json")
        areas.write_text(json.dumps({"version": 1, "areas": {"Money": {"kind": "code", "glyph": "money", "guessed": False}}}), encoding="utf-8")
        run = test_game.render_fixture(cls.tmp.name, areas_json=str(areas))
        if run.returncode:
            raise AssertionError(run.stderr[-2000:])
        cls.js = Path(cls.tmp.name, "page.js")
        test_game.page_js(Path(cls.tmp.name, "page.html"), cls.js)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def drive(self, body):
        test_game.run_page_driver(self, self.js, body)

    def test_surface_at_reproduces_the_old_body_art_cell_for_cell(self):
        self.drive(OLD_BODY_ART + RECORDER + r"""
  BODY_ART.clear();
  let same = 0, total = 0, diff = [];
  for (const style of ['city', 'ocean', 'desert']) for (const seed of [7, 4242]) for (const ph of [0, 37]) {
    const a = bodyArt(40, style, null, seed, ph, style === 'desert' ? .92 : 1, 0), b = oldBodyArt(40, style, null, seed, ph, style === 'desert' ? .92 : 1, 0);
    total++; if (a.cells.length > 300 && a.cells.join('|') === b.cells.join('|')) same++; else diff.push(style + '/' + seed + '/' + ph + ' ' + a.cells.length + ' vs ' + b.cells.length);
  }
  ok('3 styles x 2 seeds x 2 phases: the same cells as master drew (' + same + ' of ' + total + ') ' + diff.join(', '), same === 12 && total === 12);
  // the other old styles and a faded body too, so no branch moved
  let rest = true; for (const style of ['ice', 'toxic', 'blight', 'gas', 'star', 'rock', 'moon']) {
    const a = bodyArt(30, style, 'code', 99, 11, 1, 2), b = oldBodyArt(30, style, 'code', 99, 11, 1, 2); if (a.cells.join('|') !== b.cells.join('|')) rest = false; }
  ok('every other old style, faded, is unchanged as well', rest);
""")

    def test_seeds_profiles_and_the_data_bands(self):
        self.drive(r"""
  const a = kindsSeeds('/srv/maps/ops'), b = kindsSeeds('/srv/maps/ops'), c = kindsSeeds('/srv/maps/api');
  ok('one path gives the same six seeds twice', a.length === 6 && JSON.stringify(a) === JSON.stringify(b) && a.every((v) => v >= .1 && v <= 100.1));
  ok('another path gives other seeds', JSON.stringify(a) !== JSON.stringify(c));
  ok('every purpose is two layers or more, nine bodies, distinct profiles',
    PURPOSE_KEYS.length === 9 && PURPOSE_KEYS.every((k) => PURPOSE[k].profile.length >= 2) && new Set(PURPOSE_KEYS.map((k) => PURPOSE[k].profile.join())).size === 9
    && new Set(PURPOSE_KEYS.map((k) => PURPOSE[k].surface)).size === 9 && !PURPOSE_KEYS.some((k) => /blight|toxic|ice/.test(PURPOSE[k].surface)));
  ok('data bodies list bands-warp', PURPOSE.data.profile.includes('bands-warp'));
  // 64 x 32 samples of the Data body's height field: it varies with latitude, hardly with longitude
  const P = planetPal('data'), H = [];
  for (let j = 0; j < 32; j++) { const row = []; const lat = (-.5 + (j + .5) / 32) * Math.PI * .96;
    for (let i = 0; i < 64; i++) row.push(surfaceAt('data', P, (i + .5) / 64 * TAU, lat, 4242).h); H.push(row); }
  const variance = (xs) => { const m = xs.reduce((s, v) => s + v, 0) / xs.length; return xs.reduce((s, v) => s + (v - m) * (v - m), 0) / xs.length; };
  const along = (j) => variance(H[j]), across = (i) => variance(H.map((r) => r[i]));
  const lonVar = H.map((r, j) => along(j)).reduce((s, v) => s + v, 0) / 32, latVar = H[0].map((v, i) => across(i)).reduce((s, v) => s + v, 0) / 64;
  ok('latitude variance ' + latVar.toFixed(4) + ' is 3x or more the longitude variance ' + lonVar.toFixed(4), latVar >= 3 * lonVar && latVar > .005);
  ok('a purpose surface answers with a palette colour and its index', (() => { const s = surfaceAt('code', planetPal('code'), 1, .2, 7); return /^#[0-9a-f]{6}$/i.test(s.c) && s.k >= 0 && s.k <= 4; })());
""")

    def test_planet_looks_conditions_and_the_glyph(self):
        self.drive(r"""
  const look = (name) => planetLook(MOONS.find((m) => m.label === name));
  ok('purposes from the files and the owner', look('Money').purpose === 'code' && look('Tables').purpose === 'data' && look('Pics').purpose === 'media'
    && look('Notes').purpose === 'notes' && look('Empty').conditions.includes('empty'));
  ok('the owner\'s glyph reaches the look', look('Money').glyph === 'money' && look('Money').glyphSet === true && look('Tables').glyph === 'data' && look('Tables').glyphSet === false);
  ok('every look has three toon bands and the neutral palette', MOONS.every((m) => { const L = planetLook(m); return L.toonBands === 3 && L.palette === 'neutral' && L.pal.colours.length === 5; }));
  const s = Object.fromEntries(__spriteAudit().map((x) => [x.uid, x]));
  ok('__spriteAudit carries the glyph', s['area:Money'].glyph === 'money' && s['area:Money'].glyphSet === true);
  ok('__spriteAudit: one surface per purpose, and the sun', Object.values(s).filter((x) => x.kind === 'area').every((x) => x.surface === PURPOSE[x.purpose].surface) && !!s.star);
  ok('every planet is lit from the sun (within 20 degrees)', Object.values(s).filter((x) => x.kind === 'area').every((x) => Math.abs(x.litAngle - x.sunAngle) < 20));
  // a workflow step working at a planet lights its night side; the others stay dark
  const uid = MOONS.find((m) => m.label === 'Tables').uid, other = MOONS.find((m) => m.label === 'Notes').uid;
  W.workflows = [{ id: 'w1', title: 'x', state: 'working', steps: [{ key: 'a', state: 'working', planet_uid: uid, progress: { done: 30, total: 120 } }] }];
  FRAME_T += 1;
  const L1 = planetLook(MOONS.find((m) => m.uid === uid)), L2 = planetLook(MOONS.find((m) => m.uid === other));
  ok('night lights and the working ring where the step works', L1.nightLights === true && L1.conditions.includes('working') && Math.abs(L1.progress - .25) < 1e-9 && L2.nightLights === false);
  W.workflows = []; FRAME_T += 1;
  ok('the old type ids now name purposes in plain words', TYPE_NAME.city === 'Code' && TYPE_NAME.gas === 'Data' && TYPE_NAME.blight === 'most troubled'
    && MOONS.find((m) => m.label === 'Tables').type === 'gas' && /^guessed from the files: /.test(MOONS.find((m) => m.label === 'Tables').typeWhy));
  const row = CM.purposeRow(MOONS.find((m) => m.label === 'Money').uid);
  ok('the card row: purpose and who set it', row.text === 'Code · set by you' && CM.purposeRow(uid).text === 'Data · guessed from the files');
  ok('a frame draws with every body', (() => { try { draw(1500); return true; } catch (e) { out.push('  ' + e.stack); return false; } })());
""")

    def test_the_composer_draws_the_profile_and_every_layer_seed(self):
        # editing a purpose's profile or any of its six seeds changes the cells drawn (the composer runs the list, each layer on
        # its own seed); the first version of the art drew a hand-made body per purpose from the first seed alone and passed every check
        self.drive(RECORDER + r"""
  const cells = (L) => { BODY_ART.clear(); return purposeArt(30, L, 0, 10).cells.join('|'); };
  const notes = planetLook(MOONS.find((m) => m.label === 'Notes')), data = planetLook(MOONS.find((m) => m.label === 'Tables'));
  const a = cells(notes);
  const keep = PURPOSE.notes.profile; PURPOSE.notes.profile = ['rock', 'craters']; const b = cells(planetLook(MOONS.find((m) => m.label === 'Notes'))); PURPOSE.notes.profile = keep;
  ok('a profile of other layers draws other cells', a.length > 1000 && a !== b);
  let seedsMatter = true; for (let i = 1; i < 6; i++) { const sd = notes.seeds.slice(); sd[i] = sd[i] + 7.7; if (cells(Object.assign({}, notes, { seeds: sd })) === a) seedsMatter = (i >= notes.profile.length) && seedsMatter; }
  ok('each layer of the profile draws on its own seed (seeds 2.. of the six)', seedsMatter && cells(Object.assign({}, notes, { seeds: [notes.seeds[0], 1, 2, 3, 4, 5] })) !== a);
  const d1 = cells(data), keepd = PURPOSE.data.profile; PURPOSE.data.profile = ['base']; const d2 = cells(planetLook(MOONS.find((m) => m.label === 'Tables'))); PURPOSE.data.profile = keepd;
  ok('Data without bands-warp and storm is another body', d1 !== d2);
  BODY_ART.clear();
""")

    def test_a_working_agent_lights_its_planet_and_idle_windows_stay_on_the_night_side(self):
        self.drive(RECORDER + r"""
  const m = MOONS.find((x) => x.label === 'Money'), other = MOONS.find((x) => x.label === 'Notes');
  FRAME_T += 1;
  ok('no work: no night lights, no ring', planetLook(m).nightLights === false && !planetLook(m).conditions.includes('working'));
  const seat = { uid: 'seat:t1', kind: 'ship', name: 'fixer', status: 'WORKING', docked_at: m.uid };
  units.push(seat); FRAME_T += 1;
  const L = planetLook(m);
  ok('a WORKING agent docked at a planet lights its night side and gets the working ring', L.nightLights === true && L.conditions.includes('working') && L.agents === 1
    && planetLook(other).nightLights === false);
  ok('the audit says so', (() => { const r = __spriteAudit().find((x) => x.uid === m.uid); return r.nightLights === true && r.conditions.includes('working') && r.agents === 1; })());
  seat.status = 'IDLE'; FRAME_T += 1;
  ok('an idle agent there does not', planetLook(m).nightLights === false);
  units.splice(units.indexOf(seat), 1); FRAME_T += 1;
  // windows: an idle city lights some on its night side only; work there lights several times more
  BODY_ART.clear();
  const la = 12, a = la / KIND_LIGHT.steps * TAU, n = 24, mid = n + 2;
  const idle = Object.assign({}, planetLook(m), { conditions: [], nightLights: false, seeds: kindsSeeds('/srv/fixed/code') }), busy = Object.assign({}, idle, { conditions: ['working'], nightLights: true });
  const P = idle.pal, ai = purposeArt(n, idle, 0, la), ab = purposeArt(n, busy, 0, la), mi = kindsMeta(ai), mb = kindsMeta(ab);
  // the windows the art lit, read back with the pixel each one is in and its toon band (0 night, 1 terminator, 2 day)
  const wi = mi.winAt.filter((w) => w[2] === 0), wb = mb.winAt.filter((w) => w[2] === 0);
  const night = (p) => (p[0] - mid + .5) * Math.cos(a) + (p[1] - mid + .5) * Math.sin(a) < 0;
  const at = (art, p) => { const c = art.cells.find((x) => x.endsWith('@' + p[0] + ',' + p[1] + ',1,1')); return c ? c.split('@')[0] : null; };
  ok('an idle city has lit windows (' + wi.length + '), all on its night side, a few at dusk, none by day', wi.length >= 3 && wi.every(night) && mi.winBands[1] < wi.length && mi.winBands[2] === 0);
  ok('work there lights more (night band ' + wb.length + ' vs ' + wi.length + ', with the terminator ' + mb.windows + ' vs ' + mi.windows + ') and none on the day band',
    wb.length > 1.4 * wi.length && mb.windows >= 2 * mi.windows && wb.every(night) && mb.winBands[2] === 0);
  ok('each lit window is the colour drawn in its pixel', mi.winAt.length > 0 && mi.winAt.every((w) => at(ai, w) !== null));
  // the lights are points: no lit window touches another (a run or a blob of gold read as a cage of streets)
  const touch = (list) => list.some((p, i) => list.some((q, j) => j !== i && Math.abs(p[0] - q[0]) <= 1 && Math.abs(p[1] - q[1]) <= 1));
  ok('lit windows are points, none touching another', !touch(mi.winAt) && !touch(mb.winAt));
  // the streets never glow: a window is never drawn on a street cell
  const sv = (p) => { const nx = (p[0] - mid + .5) / n, ny = (p[1] - mid + .5) / n, d = Math.hypot(nx, ny), nz = Math.sqrt(Math.max(0, 1 - d * d));
    const cT = Math.cos(KIND_TILT), sT = Math.sin(KIND_TILT), by = ny * cT - nz * sT, bz = ny * sT + nz * cT;
    return surfaceAt('code', P, Math.atan2(nx, bz), Math.asin(Math.max(-1, Math.min(1, by))), idle.seeds[0], { detail: n, seeds: kindsLayerSeeds(0, idle.seeds), tilt: KIND_TILT }); };
  ok('the streets never glow', mi.winAt.every((w) => { const c = sv(w); return !c.street || c.avenue; }));
  BODY_ART.clear();
""")

    def test_a_layer_is_in_the_profile_only_where_it_left_a_mark(self):
        """The audit's profile is read from the pixels: each surface layer's own changed cells, the
        atmosphere's rim pixels, the parts' dots. A layer that only ran (or a name no layer has) is not in it; the sun and a
        machine say what they painted, not a fixed list."""
        self.drive(RECORDER + r"""
  const look = (name) => planetLook(MOONS.find((m) => m.label === name));
  const meta = (L, n) => { BODY_ART.clear(); return kindsMeta(purposeArt(n || 24, L, 5, 30)); };
  const code = look('Money');
  const full = meta(code);
  ok('a code body: base, city, lights and atmosphere each marked cells (' + JSON.stringify(full.cells) + ')', full.layers.join() === 'base,city,lights,atmosphere'
    && full.cells.base > 100 && full.cells.city > 50 && full.cells.lights > 3 && full.cells.atmosphere > 5);
  // a layer taken out of the list is gone from what was drawn; a name no layer has never appears
  ok('without "lights" there are no windows and no lights layer', (() => { const m = meta(Object.assign({}, code, { profile: ['base', 'city', 'atmosphere'] })); return m.windows === 0 && !m.layers.includes('lights') && m.layers.includes('city'); })());
  ok('a name no layer has is not a layer of the body', (() => { const m = meta(Object.assign({}, code, { profile: ['base', 'city', 'unicorn'] })); return m.layers.join() === 'base,city'; })());
  // a layer whose surface writes nothing leaves no mark, so the audit no longer lists it (a flag without the layer)
  const keep = KIND_LAYERS.city.surf; KIND_LAYERS.city.surf = () => {};
  const dead = meta(code); KIND_LAYERS.city.surf = keep;
  ok('a city layer that draws nothing is not in the profile', !dead.layers.includes('city') && dead.layers.includes('base') && !(dead.cells.city > 0));
  GB = true; const mono = meta(code); GB = false;
  ok('the mono palette draws no atmosphere, and the audit does not list one', !mono.layers.includes('atmosphere') && !(mono.cells.atmosphere > 0));
  // S4: windows are never painted in the mono palette (emission is skipped), so "lights" is no layer there and counts 0 cells;
  // in colour the lights layer's cells are the windows on the canvas, exactly
  ok('the mono palette paints no windows: no lights layer, 0 cells (' + JSON.stringify(mono.cells) + ')', mono.windows === 0 && !mono.layers.includes('lights') && !(mono.cells.lights > 0));
  ok('in colour the lights layer\'s cells are its windows: ' + full.cells.lights + ' = ' + full.windows, full.cells.lights === full.windows && full.windows > 3);
  // every emissive-only mark is no mark in mono: a layer that only sets a glow leaves no layer there
  { const only = Object.assign({}, KIND_LAYERS.lights); KIND_LAYERS.glowonly = { surf(c, X) { c.e = '#ffffff'; c.en = true; c.w = 1; } };
    GB = true; const g = meta(Object.assign({}, code, { profile: ['base', 'glowonly'] })); GB = false; const gc = meta(Object.assign({}, code, { profile: ['base', 'glowonly'] })); delete KIND_LAYERS.glowonly;
    ok('a glow-only layer: no layer in mono (' + g.layers.join() + '), a layer in colour (' + gc.layers.join() + ')', !g.layers.includes('glowonly') && gc.layers.includes('glowonly')); }
  // every purpose's first two layers always mark cells, and the nine bodies are nine different profiles
  const seen = new Set();
  PURPOSE_KEYS.forEach((p) => { const L = { purpose: p, seeds: kindsSeeds('/srv/rows/' + p), pal: planetPal(p), conditions: [], nightLights: false, health: 'ok', profile: PURPOSE[p].profile.slice() };
    const m = meta(L, 14), parts = PURPOSE[p].profile.filter((k) => KIND_PARTS[k]);
    ok(p + ': at least two layers left a mark (' + m.layers.join('+') + (parts.length ? ' and parts ' + parts.join('+') : '') + ')', m.layers.length + parts.length >= 2);
    seen.add(m.layers.join('+') + '|' + parts.join('+')); });
  ok('nine purposes, nine different profiles in the pixels', seen.size === 9);
  // the parts count where they painted: a body's ring, dock and crates are in KIND_PARTS_DRAWN only when dots were drawn
  { const L = look('Money'); L.profile = ['ring', 'dock', 'core']; L.purpose = 'shared'; L.pal = planetPal('shared');
    const g = new Proxy({}, { get: (o, k) => (k === 'createRadialGradient' || k === 'createLinearGradient') ? () => ({ addColorStop() {} }) : (k in o ? o[k] : () => {}), set: (o, k, v) => { o[k] = v; return true; } });
    kindsBody(g, 100, 100, 30, L, { q: 2, n: 15, ph: 3, la: 10, t: 0 });
    ok('a shared body paints its ring and its dock', KIND_PARTS_DRAWN.includes('ring') && KIND_PARTS_DRAWN.includes('dock'));
    const kr = KIND_PARTS.dock.front; KIND_PARTS.dock.front = () => {}; kindsBody(g, 100, 100, 30, L, { q: 2, n: 15, ph: 3, la: 10, t: 0 }); KIND_PARTS.dock.front = kr;
    ok('a dock that paints nothing is not drawn', !KIND_PARTS_DRAWN.includes('dock') && KIND_PARTS_DRAWN.includes('ring')); }
  // the sun and the machines: what the draw functions painted
  draw(1000);
  let rows = Object.fromEntries(__spriteAudit().map((x) => [x.uid, x]));
  ok('the sun (pixel look): corona, rays, photosphere and limb, three limb steps', rows.star.layers.join() === 'corona,rays,photosphere,limb' && rows.star.toonBands === 3);
  GB = true; draw(1100); rows = Object.fromEntries(__spriteAudit().map((x) => [x.uid, x])); GB = false;
  ok('the mono palette paints neither corona nor rays (glowAt draws nothing there), and the audit says so', rows.star.layers.join() === 'photosphere,limb');
  // S4: the layers are the paint calls that happened, read through a spy on the context: no drawImage, no photosphere (and no
  // limb, which is its surface's steps); a corona whose fill is skipped is no layer either
  { const realDraw = ctx.drawImage, realFill = ctx.fill; let draws = 0, fillsN = 0; ctx.drawImage = (...a) => { draws++; }; ctx.fill = (...a) => { fillsN++; };
    draw(1150); rows = Object.fromEntries(__spriteAudit().map((x) => [x.uid, x]));
    ok('with the spy in place the sun still lists corona, rays, photosphere, limb and the spy saw its fill and drawImage', rows.star.layers.join() === 'corona,rays,photosphere,limb' && draws > 0 && fillsN > 0);
    ctx.drawImage = (...a) => { throw new Error('no photosphere'); }; let threw = false;
    try { draw(1200); } catch (e) { threw = true; } ctx.drawImage = (...a) => {};
    const realGlow = glowAt; glowAt = () => {}; draw(1250); rows = Object.fromEntries(__spriteAudit().map((x) => [x.uid, x])); glowAt = realGlow;
    ok('a corona that paints nothing is not a layer: ' + rows.star.layers.join(), rows.star.layers.join() === 'rays,photosphere,limb' && threw);
    ctx.drawImage = realDraw; ctx.fill = realFill; }
  draw(1300);
  applyTheme('tactical', false); vecStar(1200, String(W.name || 'CONTAINER'), [], 0); rows = Object.fromEntries(__spriteAudit().map((x) => [x.uid, x]));
  ok('the vector sun paints a glow, a streak, a disc and rings: no stepped surface, no toon bands', rows.star.layers.join() === 'glow,streak,disc,rings' && rows.star.toonBands === 0);
  applyTheme(THEME_CFG, false);
  const gm = kindsMeta(machineArt(20, 7, '#F5B342'));
  ok('a machine is arms and a bulge by its pixels (' + JSON.stringify(gm.cells) + ')', gm.layers.join() === 'arms,bulge' && gm.cells.arms > 20 && gm.cells.bulge > 5 && gm.bands === 3);
  BODY_ART.clear();
""")

    def test_every_crescent_faces_the_sun_measured_from_the_painted_light(self):
        """T1: the lit side of every body is within 20 degrees of the sun, read back from the pixels painted on the canvas (the
        light that fell on each cell over its albedo, windows and the rim left out; never the band numbers, never the surface's
        own colours), over seeds, phases, light buckets and sizes from the smallest the map draws (5 art pixels, C5) up; the
        same body painted with the sun moved 90 degrees measures 90 degrees away; a body painted lit from the wrong side (the
        pixels, not the metadata) fails the measure, so it cannot pass by being the sun's angle."""
        self.drive(RECORDER + r"""
  const wrap = (a) => ((a + 540) % 360) - 180, sunOf = (la) => la / KIND_LIGHT.steps * 360;
  const worst = {}; let runs = 0, moved = 999, fromPixels = 0;
  const mk = (p, i) => ({ purpose: p, seeds: kindsSeeds('/srv/bench/' + p + i), pal: planetPal(p), conditions: [], nightLights: false, health: 'ok', profile: PURPOSE[p].profile.slice() });
  PURPOSE_KEYS.forEach((p) => { let w = 0, wc = '';
    for (let i = 0; i < 4; i++) for (const ph of [0, 41]) for (const la of [0, 12, 31]) for (const n of [5, 9, 25]) {
      BODY_ART.clear();
      const m = kindsMeta(purposeArt(n, mk(p, i), ph, la)), e = Math.abs(wrap(m.litLuma - sunOf(la))); runs++; if (m.litFrom === 'pixels') fromPixels++;
      if (e > w) { w = e; wc = p + i + ' ph' + ph + ' la' + la + ' n' + n; }
      // the same body, the sun a quarter turn on: the measured side turns with it (a measurement, not the input angle)
      if (n === 9) { BODY_ART.clear(); const m2 = kindsMeta(purposeArt(n, mk(p, i), ph, (la + 12) % KIND_LIGHT.steps)); const d = Math.abs(wrap(m2.litLuma - m.litLuma - 90)); if (90 - d < moved) moved = 90 - d; } }
    worst[p] = [w, wc]; });
  // every crystal seed too (the facets lean the light), over more phases and buckets
  { let wc = 0, wcase = '';
    for (let i = 0; i < 14; i++) for (const ph of [0, 24, 72]) for (const la of [0, 12, 24, 40]) { BODY_ART.clear();
      const m = kindsMeta(purposeArt(14, mk('config', i), ph, la)), e = Math.abs(wrap(m.litLuma - sunOf(la))); runs++; if (m.litFrom === 'pixels') fromPixels++; if (e > wc) { wc = e; wcase = 'config' + i + ' ph' + ph + ' la' + la; } }
    worst.crystals = [wc, wcase]; }
  // the smallest size the map draws, every purpose, every bucket: the sizes under it are never asked for (kindsArtN, the core)
  { let ws = 0, wsc = '';
    for (const p of PURPOSE_KEYS) for (let i = 0; i < 6; i++) for (const ph of [0, 24, 72]) for (const la of [0, 7, 12, 19, 31, 40]) { BODY_ART.clear();
      const m = kindsMeta(purposeArt(KIND_MIN_N, mk(p, i), ph, la)), e = Math.abs(wrap(m.litLuma - sunOf(la))); runs++; if (m.litFrom === 'pixels') fromPixels++; if (e > ws) { ws = e; wsc = p + i + ' ph' + ph + ' la' + la; } }
    worst.smallest = [ws, wsc]; }
  const over = Object.entries(worst).filter(([p, v]) => v[0] >= 20);   // after EVERY sweep (S6)
  ok(runs + ' bodies: the worst crescent is ' + Math.max(...Object.values(worst).map((v) => v[0])).toFixed(1) + ' degrees from the sun ' + JSON.stringify(worst), over.length === 0 && runs > 700);
  ok('every one was measured from the canvas pixels, not the colours handed to it (' + fromPixels + ' of ' + runs + ')', fromPixels === runs);
  ok('the sun moved a quarter turn moves the measured side with it, by 60 degrees or more (the least it moved: ' + moved.toFixed(1) + ')', moved > 60);
  ok('the map never asks for a body under ' + KIND_MIN_N + ' art pixels', kindsArtN(1) === KIND_MIN_N && kindsArtN(4) === KIND_MIN_N && KIND_MIN_N === 5);
  // the review's crystal (config13, 25 art pixels, phase 72, bucket 0) and the review's small archive (4 art pixels is never drawn; 5 is)
  BODY_ART.clear();
  { const m = kindsMeta(purposeArt(25, mk('config', 13), 72, 0)); ok('the review\'s crystal: ' + Math.abs(wrap(m.litLuma)).toFixed(1) + ' degrees', Math.abs(wrap(m.litLuma)) < 20); }
  BODY_ART.clear();
  { const m = kindsMeta(purposeArt(5, mk('archive', 1), 0, 31)); ok('the review\'s archive1 at the smallest size: ' + Math.abs(wrap(m.litLuma - sunOf(31))).toFixed(1) + ' degrees', Math.abs(wrap(m.litLuma - sunOf(31))) < 20); }
  // a body painted lit from the wrong side: the pixels say so, whatever the running numbers say. kindsBand is what paints the
  // light; reversed (the day band dark, the night band bright) the measure must swing to the far side
  { const realBand = kindsBand; kindsBand = (c, band) => realBand(c, 2 - band); BODY_ART.clear();
    const m = kindsMeta(purposeArt(12, mk('notes', 2), 7, 12)); kindsBand = realBand; BODY_ART.clear();
    ok('painted from the wrong side the measured side is 140 degrees or more off the sun (' + Math.abs(wrap(m.litLuma - sunOf(12))).toFixed(1) + ')', Math.abs(wrap(m.litLuma - sunOf(12))) > 140); }
  // the audit row: measured, so it moves with the sun (a planet put on the other side of the star lights its other side)
  draw(1000);
  const m0 = MOONS.find((m) => m.label === 'Money'); let r0 = __spriteAudit().find((x) => x.uid === m0.uid);
  const was = [m0.x, m0.y]; m0.x = LY.star.x - (m0.x - LY.star.x); m0.y = LY.star.y - (m0.y - LY.star.y); KIND_DRAWN.delete(m0.uid);
  const r1 = __spriteAudit().find((x) => x.uid === m0.uid); m0.x = was[0]; m0.y = was[1]; KIND_DRAWN.delete(m0.uid);
  ok('the audit row follows the sun: ' + r0.litAngle + ' then ' + r1.litAngle + ' (sun ' + r0.sunAngle + ' then ' + r1.sunAngle + ')', Math.abs(wrap(r0.litAngle - r0.sunAngle)) < 20 && Math.abs(wrap(r1.litAngle - r1.sunAngle)) < 20
    && Math.abs(wrap(r1.litAngle - r0.litAngle)) > 150);
  ok('...and it reports the band-index angle too, which is a different number', typeof r0.litBandAngle === 'number' && r0.litAngle !== r0.sunAngle);
""")

    def test_the_backdrop_bakes_again_when_the_theme_changes_and_never_twice_for_one_recipe(self):
        """T5 + gap (f): the baked 2D backdrop belongs to a theme; a theme change drops it (applyTheme) and bakes the new colours;
        a theme the owner already saw comes back from the cache; A -> B -> A while A's first bake still runs leaves one live bake;
        the 30% ceiling is read again when the bodies change. The bake runs on a small grid here, by hand-pumped idle slices."""
        self.drive(r"""
  KIND_NEB_GRID[0] = 64; KIND_NEB_GRID[1] = 32;
  const queue = []; global.requestIdleCallback = (fn) => { queue.push(fn); return queue.length; };
  global.ImageData = function () {};
  const realCanvas = kindsCanvas; let capCalls = 0;
  kindsCanvas = (w, h) => { const px = new Uint8ClampedArray(w * h * 4); const g = { createImageData: (a, b) => ({ data: new Uint8ClampedArray(a * b * 4) }),
    putImageData: (img) => { px.set(img.data.subarray(0, px.length)); }, getImageData: () => { capCalls++; return { data: px }; }, drawImage() {}, imageSmoothingEnabled: true };
    return { width: w, height: h, getContext: () => g }; };
  const pump = () => { let n = 0; while (queue.length && n++ < 5000) queue.shift()({ didTimeout: false, timeRemaining: () => 50 }); return n; };
  const bd = () => __spriteAudit().backdrop;
  // the context the backdrop paints on records its blits (S6: a stub that copied nothing let a removed blit pass)
  const blits = []; const gfx = { save() {}, restore() {}, fillRect() {}, drawImage(c, x, y, w, h) { blits.push({ c: c, x: x, y: y, w: w, h: h }); }, globalAlpha: 1, imageSmoothingEnabled: false };
  FRAME_T += 1; kindsBakeSoon(); pump();
  ok('classic: one bake, ready, its seed is the recipe\'s', bd().state === 'ready' && bd().seed === CM.nebulaRecipe().seed && bd().bakes === 1 && JSON.stringify(bd().colours) === JSON.stringify(SPACE.nebula.slice(0, 3)));
  const classicColours = bd().colours;
  applyTheme('synthwave', false);
  ok('a theme change drops the old backdrop at once (applyTheme)', bd().state === 'idle' && bd().seed === null && KIND_NEB.canvas === null);
  CM.drawBackdrop2D({ save() {}, restore() {}, fillRect() {}, drawImage() {} });
  ok('...and the next frame starts the new bake', bd().state === 'baking' && bd().bakes === 2);
  pump();
  ok('synthwave: ready with synthwave\'s colours', bd().state === 'ready' && JSON.stringify(bd().colours) !== JSON.stringify(classicColours) && JSON.stringify(bd().colours) === JSON.stringify(CM.nebulaRecipe().colours.slice(0, 3)));
  applyTheme('classic', false); CM.drawBackdrop2D({ save() {}, restore() {}, fillRect() {}, drawImage() {} });
  ok('back to classic: from the cache, ready at once, no new bake', bd().state === 'ready' && bd().bakes === 2 && JSON.stringify(bd().colours) === JSON.stringify(classicColours) && queue.length === 0);
  // a ready backdrop is blitted onto the frame: the baked canvas, twice as wide as the view is high, at the ceiling's alpha
  FRAME_T += 1; blits.length = 0; CM.drawBackdrop2D(gfx);
  const wantW = typeof VW === 'number' ? Math.max(VW, VH * 2) : VH * 2;   // the harness's VW is a stub, not a number
  ok('the frame got the baked canvas blitted (' + blits.length + ' blit, ' + (blits[0] && blits[0].w) + 'x' + (blits[0] && blits[0].h) + ' for a view ' + VH + ' high)', blits.length === 1 && blits[0].c === KIND_NEB.canvas && blits[0].h === VH && blits[0].w === wantW
    && CM.backdropOn() && gfx.globalAlpha === KIND_NEB.alpha);
  { const keep = KIND_NEB.canvas; KIND_NEB.canvas = null; blits.length = 0; FRAME_T += 1; CM.drawBackdrop2D(gfx); KIND_NEB.canvas = keep;
    ok('...and nothing is blitted while no canvas is ready', blits.length === 0 && !CM.backdropOn()); }
  // A -> B -> A while the first bake is still running: the old slice must not come back to life
  KIND_NEB.baked.clear(); applyTheme('earthen', false); CM.drawBackdrop2D({ save() {}, restore() {}, fillRect() {}, drawImage() {} });
  const b0 = bd().bakes; const first = queue.length;
  applyTheme('synthwave', false); CM.drawBackdrop2D({ save() {}, restore() {}, fillRect() {}, drawImage() {} });
  applyTheme('earthen', false); CM.drawBackdrop2D({ save() {}, restore() {}, fillRect() {}, drawImage() {} });
  const queued = queue.length; pump();
  ok('three theme changes while baking leave three bakes started and ONE live: ' + (bd().bakes - b0) + ' started, ' + queued + ' slices waiting, ready with earthen\'s colours',
    bd().bakes - b0 === 2 && bd().state === 'ready' && JSON.stringify(bd().colours) === JSON.stringify(CM.nebulaRecipe().colours.slice(0, 3)) && KIND_NEB.baked.size >= 1);
  // the recipe's identity includes the theme and every layer's numbers
  const k1 = kindsNebKey(); const keep = CM.nebulaRecipe; CM.nebulaRecipe = () => { const r = keep(); r.layers[0].scale += .1; return r; }; KIND_NKEY.space = null;
  ok('a recipe whose layer changed is another key', kindsNebKey() !== k1); CM.nebulaRecipe = keep; KIND_NKEY.space = null;
  ok('...and the theme\'s name is part of it', JSON.parse(kindsNebKey())[0] === THEME_NAME);
  // the ceiling follows the bodies
  draw(1500); CM.drawBackdrop2D({ save() {}, restore() {}, fillRect() {}, drawImage() {} });
  const c0 = capCalls; KIND_NEB.capKey = 'stale'; CM.drawBackdrop2D({ save() {}, restore() {}, fillRect() {}, drawImage() {} });
  ok('bodies that changed under a finished bake: the 30% ceiling is taken again, once', capCalls > c0 && KIND_NEB.capKey === kindsCapKey());
  const c1 = capCalls; CM.drawBackdrop2D({ save() {}, restore() {}, fillRect() {}, drawImage() {} });
  ok('...and not again while they stay the same', capCalls === c1);
  kindsCanvas = realCanvas;
""")

    def test_the_legend_chip_is_two_plain_lines_the_chrome_can_place(self):
        self.drive(r"""
  const c = CM.legendChip();
  ok('two lines, each 100 characters or fewer', c.lines.length === 2 && c.lines.every((l) => l.length > 20 && l.length <= 100) && c.text === c.lines.join('\n'));
  ok('plain text only: letters, digits, spaces and the middle dot (no arc, snowflake or dashed-line symbol the fonts lack)', c.lines.every((l) => /^[A-Za-z0-9 .,:+·'-]+$/.test(l)));
  ok('line one names the nine purposes, line two the five marks with their colours', PURPOSE_KEYS.every((k) => c.lines[0].includes(PURPOSE[k].name))
    && c.marks.length === 5 && ['broken', 'needs you', 'working', 'quiet 30+ days', 'rebuildable'].every((w) => c.lines[1].includes(w))
    && c.marks.every((m) => /^#[0-9a-fA-F]{6}$/.test(m.colour)) && c.items.length === 9);
""")

    def test_a_code_planet_reads_as_a_lit_blue_grey_world_not_a_grey_rock(self):
        """Gap (e): the most common purpose is a blue-grey city world with warm windows at every size; the Not sorted rock stays a
        plain grey, so the two are told apart at 14 px."""
        self.drive(RECORDER + r"""
  const hex = (c) => [1, 3, 5].map((i) => parseInt(c.slice(i, i + 2), 16));
  const cellsOf = (L, n, ph, la) => { BODY_ART.clear(); const art = purposeArt(n, L, ph, la); return { art: art, rgb: art.cells.map((c) => c.split('@')[0]).filter((c) => /^#[0-9a-f]{6}$/i.test(c)).map(hex) }; };
  const mean = (rgb) => rgb.reduce((a, c) => [a[0] + c[0] / rgb.length, a[1] + c[1] / rgb.length, a[2] + c[2] / rgb.length], [0, 0, 0]);
  const mk = (p, seed) => ({ purpose: p, seeds: kindsSeeds(seed), pal: planetPal(p), conditions: [], nightLights: false, health: 'ok', profile: PURPOSE[p].profile.slice() });
  const lit = (rgb) => rgb.filter((c) => c[0] + c[1] + c[2] > 330);   // the day side's pixels
  [24, 11, 7].forEach((n) => { const code = mean(lit(cellsOf(mk('code', '/srv/bench/api'), n, 7, 30).rgb)), rock = mean(lit(cellsOf(mk('unsorted', '/srv/bench/misc'), n, 7, 30).rgb));
    ok('code at ' + n + ' art pixels: blue over red by 28 or more on its lit side (' + code.map(Math.round) + '), the rock grey, within 20 (' + rock.map(Math.round) + ') and darker',
      code[2] - code[0] >= 28 && Math.abs(rock[2] - rock[0]) <= 20 && code[1] > rock[1] + 12); });
  // the day side is lit, not dim: the lit pixels of a small code planet average a mid blue-grey or lighter
  { const rgb = lit(cellsOf(mk('code', '/srv/bench/site'), 7, 19, 30).rgb), m = mean(rgb); ok('a 7 pixel code planet: the lit pixels average ' + m.map(Math.round) + ' (over 110 in blue)', rgb.length >= 8 && m[2] > 110); }
  // its windows are warm: red over blue on every lit window's own colour, orange to gold, never blue-white
  { const busy = Object.assign(mk('code', '/srv/bench/ops'), { conditions: ['working'], nightLights: true }), idle = mk('code', '/srv/bench/ops');
    [idle, busy].forEach((L, i) => { BODY_ART.clear(); const art = purposeArt(24, L, 0, 12), mt = kindsMeta(art), at = (p) => { const c = art.cells.find((x) => x.endsWith('@' + p[0] + ',' + p[1] + ',1,1')); return c ? hex(c.split('@')[0]) : null; };
      const cols = mt.winAt.map(at).filter(Boolean);
      ok((i ? 'working' : 'idle') + ' code: ' + cols.length + ' lit windows, every one warm (red over green over blue, red over blue by 40 or more)', cols.length >= 3 && cols.every((c) => c[0] - c[2] >= 40 && c[0] > c[1] && c[1] > c[2])); }); }
  BODY_ART.clear();
""")

    def test_every_theme_gives_the_nine_purposes_nine_palettes(self):
        self.drive(r"""
  const bad = [];
  // Media, Config, Archive and Not sorted once shared the theme's rock in every theme but classic; now each takes its own surface
  // (or toxic, ice, belt), so all nine differ wherever the theme's own surfaces differ (phosphor's ice and station are one green)
  THEME_NAMES.forEach((t) => { applyTheme(t); const pals = PURPOSE_KEYS.map((p) => planetPal(p).colours.join(','));
    const four = ['media', 'config', 'archive', 'unsorted'].map((p) => pals[PURPOSE_KEYS.indexOf(p)]);
    const surf = PURPOSE_KEYS.map((p) => JSON.stringify(STYLE[kindsSurface(p)] || null)), want = PACK && !PACK.planets && THEME_NAME !== 'classic' ? new Set(surf).size : 9;
    if (new Set(four).size !== 4 || new Set(pals).size < want) bad.push(t + ': ' + PURPOSE_KEYS.filter((p, i) => pals.indexOf(pals[i]) !== i).join(' '));
    if (kindsTile('media') && kindsTile('media') === kindsTile('unsorted')) bad.push(t + ': media takes the rock tile'); });
  applyTheme('classic');
  ok('nine palettes in every theme (' + THEME_NAMES.join(' ') + ') ' + bad.join('; '), bad.length === 0 && THEME_NAMES.length >= 5);
  ok('the old tables read the purpose palette (STYLE, THEMES, SECTORS aliases)', STYLE['p:data'].a === planetPal('data').colours[2]
    && THEMES['p:media'].hi === planetPal('media').colours[4] && sec('knowledge').purpose === 'notes' && KIND_STYLE['p:config'] === kindsSurface('config'));
""")

    def test_the_backdrop_recipe_is_this_machines(self):
        self.drive(r"""
  const a = CM.nebulaRecipe(), b = CM.nebulaRecipe();
  ok('one seed per machine and root, at most two layers, the theme\'s colours', a.seed === b.seed && a.seed === kindsFnv(String(W.name) + String(W.root_path))
    && a.layers.length >= 1 && a.layers.length <= 2 && a.colours.length === 3 && a.colours[0] === SPACE.nebula[0]);
  ok('the audit names the recipe seed, the bake size and the grid it samples', __spriteAudit().backdrop.recipeSeed === a.seed && __spriteAudit().backdrop.size.join('x') === '1024x512'
    && __spriteAudit().backdrop.grid.join('x') === '512x256');
  ok('no bake ran here: no seed (the check cannot pass on nothing)', __spriteAudit().backdrop.seed === null);
  ok('every row names its layers (the profile)', __spriteAudit().every((r) => Array.isArray(r.layers) && r.layers.join() === r.profile.join()));
  ok('the legend chip: two lines, nine purposes', CM.legendChip().lines.length === 2 && CM.legendChip().items.length === 9);
""")


if __name__ == "__main__":
    unittest.main()
