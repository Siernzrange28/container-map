"""The 3D view's words, the radar's names, the first-entry Controls list, the ship's tint and the phone's view switch.

A newcomer who enters the 3D view reads every HUD word without a glossary: "0 agents working" (no multiplier glyph),
"1 of 2 folders healthy", "1 needs you", the radar called "Map". A folder's name on the radar never sits over a dot, the
pilot's arrow or another name. The keys list opens by itself once on a first entry (remembered in localStorage, a
failing store never throws) and stays one click away. The ship's hull takes a quarter of the region's sky colour, so it
reads the same over any nebula (the owner's skin keeps the whole sky colour). On a phone the view switch rides the
bottom edge, 44 px tall, away from the ship and the stick.

Node runs the very same world3d/*.js on a stub registry; the page itself is read from source.
"""
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

import testenv  # noqa: F401  (first: clears what steers the tool)

ROOT = Path(__file__).resolve().parent
HUD = (ROOT / "world3d" / "hud.js").read_text(encoding="utf-8")
CORE = (ROOT / "world3d" / "core.js").read_text(encoding="utf-8")
CAMERA = (ROOT / "world3d" / "camera.js").read_text(encoding="utf-8")
CRAFT = (ROOT / "world3d" / "craft.js").read_text(encoding="utf-8")
CSS = re.sub(r"/\*.*?\*/", "", (ROOT / "ui" / "hud3d.css").read_text(encoding="utf-8"), flags=re.S)


def rule(selector):
    """The declarations of the one CSS rule whose selector list is exactly `selector`."""
    for m in re.finditer(r"(?m)^([^{}\n/][^{}]*)\{([^}]*)\}", CSS):
        if m.group(1).strip() == selector:
            return m.group(2)
    raise AssertionError("no rule for " + selector)


def node(script):
    p = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    if p.returncode:
        raise AssertionError(p.stderr[-2000:])
    return json.loads(p.stdout)


class HudWords(unittest.TestCase):
    def test_the_hud_says_plain_words(self):
        self.assertIn("word: attn ? (attn === 1 ? ' folders needs you' : ' folders need you')", HUD)
        self.assertIn("word: ' needs you: ' + w", HUD)   # exactly one folder: its name and what it waits on, in one line
        self.assertIn("text(H.anum, String(working))", HUD)
        self.assertIn("'AI agent working now' : 'AI agents working now'", HUD)
        self.assertIn("text(H.nword, ' ' + needNoun(w.counts, ny))", HUD)
        self.assertIn("text(H.rlabel, 'Map')", HUD)
        for gone in ("'×' + working", "NORTH UP", "RADAR", "text(H.gsub, 'folders"):
            self.assertNotIn(gone, HUD, gone)
        # the audit string and the older core HUD say the same words as the screen
        self.assertIn("gauge: (total - clear) ? (total - clear) + ' of ' + total + ' folders '", HUD)
        self.assertEqual(CORE.count("'all ' + total + ' folders: nothing to do'"), 2)
        self.assertNotIn("' of ' + total + ' clear'", CORE)

    def test_no_shouting_caps_on_the_sentences(self):
        for sel in (".gauge3d .gcap,.gauge3d .gsub", ".steps3d .cap", ".agents3d .word", ".need3d", ".flow3d .t1,.flow3d .t2"):
            self.assertNotIn("uppercase", rule(sel), sel)

    def test_the_agents_count_needs_no_glyph_in_the_page_either(self):
        self.assertNotRegex(HUD, r"text\(H\.anum, '[^']")


@unittest.skipUnless(shutil.which("node"), "node required")
class RadarNames(unittest.TestCase):
    """FH.radarLabelSpot: the first of eight spots clear of every reserved box, else no name."""

    SCRIPT = (
        "globalThis.CM3D={module(){},provides(){}};"
        "require(%s);const spot=CM3D.fh.radarLabelSpot;" % json.dumps(str(ROOT / "world3d" / "hud.js"))
    )

    def test_a_name_never_overlaps_a_marker_or_leaves_the_radar(self):
        out = node(self.SCRIPT + """
let seed = 12345; const rnd = () => (seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648;
const W = 232, H = 188, cx = W / 2, cy = H / 2, bad = [];
let placed = 0, dropped = 0;
for (let trial = 0; trial < 400; trial++) {
  // the pilot's arrow with a stalk, a few dots and craft, as radarDraw reserves them
  const own = (rnd() - 0.5) * 60, boxes = [{x: cx - 10, y: cy - Math.max(own, 0) - 11, w: 20, h: Math.abs(own) + 22}];
  const dots = [];
  const n = 1 + Math.floor(rnd() * 8);
  for (let i = 0; i < n; i++) { const x = 10 + rnd() * (W - 20), y = 10 + rnd() * (H - 20), r = 3.5 + rnd() * 7.5; dots.push([x, y, r]); boxes.push({x: x - r - 3, y: y - r - 3, w: 2 * (r + 3), h: 2 * (r + 3)}); }
  for (let i = 0; i < 2; i++) boxes.push({x: 10 + rnd() * (W - 30), y: 10 + rnd() * (H - 30), w: 16, h: 18});
  // sometimes a dot right under the pilot
  if (trial % 5 === 0) { dots.push([cx + 3, cy + 2, 6]); boxes.push({x: cx - 6, y: cy - 7, w: 18, h: 18}); }
  const names = [];
  dots.forEach((d) => {
    const lw = 30 + rnd() * 50, b = spot(d[0], d[1], d[2], lw, boxes.concat(names), W, H);
    if (!b) { dropped++; return; }
    placed++; names.push(b);
    // the dot's own box is in `boxes` too: a name starts r + 4 from the centre, past the box's r + 3
    const hit = boxes.concat(names.slice(0, -1)).some((q) => !(b.x > q.x + q.w || b.x + b.w < q.x || b.y > q.y + q.h || b.y + b.h < q.y));
    const inside = b.x > 2 && b.x + b.w < W - 2 && b.y > 2 && b.y + b.h < H - 2;
    if (hit || !inside) bad.push({trial, b, hit, inside});
  });
}
console.log(JSON.stringify({bad: bad.slice(0, 3), badN: bad.length, placed, dropped}));
""")
        self.assertEqual(out["badN"], 0, out["bad"])
        self.assertGreater(out["placed"], 400, "most names find a spot")

    def test_a_name_beside_the_pilots_arrow_moves_off_it_or_is_left_off(self):
        out = node(self.SCRIPT + """
const arrow = {x: 106, y: 83, w: 20, h: 22};
// a dot just left of the arrow: the right spot is the arrow's, so the name goes left, above or below
const b = spot(100, 94, 5, 48, [arrow, {x: 92, y: 86, w: 16, h: 16}], 232, 188);
const hemmed = spot(100, 94, 5, 48, [arrow, {x: 92, y: 86, w: 16, h: 16}, {x: 0, y: 0, w: 232, h: 188}], 232, 188);
console.log(JSON.stringify({b, hemmed, over: b ? !(b.x > 126 || b.x + b.w < 106 || b.y > 105 || b.y + b.h < 83) : null}));
""")
        self.assertIsNotNone(out["b"])
        self.assertFalse(out["over"], "the name sits clear of the arrow")
        self.assertIsNone(out["hemmed"], "no free spot, no name")

    def test_the_radar_reserves_the_pilot_the_dots_the_sun_and_the_other_craft(self):
        body = HUD[HUD.index("function radarDraw("):HUD.index("// ---- pooled markers and arrows") if "// ---- pooled markers and arrows" in HUD else len(HUD)]
        for needle in ("var ownS = clamp((A.alt || 0)", "(w.craft || []).forEach(function (cr) {\n      if (cr.role === 'carrier') return;\n      var q = P(cr.x, cr.y), s = clamp(20",
                       "o.kind !== 'foe'", "radarLabelSpot(q[0], q[1], r,"):
            self.assertIn(needle, body, needle)


@unittest.skipUnless(shutil.which("node"), "node required")
class ControlsOnce(unittest.TestCase):
    """camera.js's flightGuideBuild: the keys list opens on a first entry only."""

    def build(self, store):
        start = CAMERA.index("  function flightGuideBuild() {")
        end = CAMERA.index("  function flightGuideUpdate(")
        fn = CAMERA[start:end]
        script = """
const make = (tag) => { const e = {tag, children: [], style: {}, open: false, textContent: '', className: '', hidden: false,
  appendChild(c) { this.children.push(c); return c; }, setAttribute() {}, addEventListener() {}}; return e; };
const body = make('body'), doc = {createElement: make, body};
const FN = %s;
const run = (g) => { const S = {ctx: {quality: {phone: false}, page: {}}}; new Function('g', 'S', 'publishFlightInput', 'clearTimeout', FN + '; flightGuideBuild(); return S;')(g, S, () => {}, () => {}); return S.guide; };
const mem = {}; const okStore = {getItem: (k) => (k in mem ? mem[k] : null), setItem: (k, v) => { mem[k] = String(v); }};
const bad = {getItem() { throw new Error('denied'); }, setItem() { throw new Error('denied'); }};
const out = {};
const first = run({document: doc, localStorage: okStore}); out.first = {open: first.help.open, firstOpen: first.firstOpen, saved: mem['cm3d.controlsSeen']};
const second = run({document: doc, localStorage: okStore}); out.second = {open: second.help.open, firstOpen: second.firstOpen};
const g3 = {document: doc, localStorage: bad}; const third = run(g3); out.denied = {open: third.help.open};
const fourth = run(g3); out.deniedAgain = {open: fourth.help.open};
const none = run({document: doc}); out.noStore = {open: none.help.open};
console.log(JSON.stringify(out));
""" % json.dumps(fn)
        return node(script)

    def test_opens_once_then_stays_reachable(self):
        out = self.build(None)
        self.assertEqual(out["first"], {"open": True, "firstOpen": True, "saved": "1"})
        self.assertEqual(out["second"], {"open": False, "firstOpen": False}, "a returning flyer finds it closed")
        self.assertTrue(out["denied"]["open"], "a store that throws still shows it, without an error")
        self.assertFalse(out["deniedAgain"]["open"], "and only once per page")
        self.assertTrue(out["noStore"]["open"], "no store at all: shown once, no error")

    def test_the_list_is_one_click_away_and_flying_closes_it(self):
        self.assertIn("title.textContent = 'Controls'", CAMERA)
        self.assertIn("S.guide.firstOpen = false; S.guide.help.open = false;", CAMERA)
        self.assertEqual(CAMERA.count("S.guide.firstOpen = false; S.guide.help.open = false;"), 2, "a key and a touch close it")
        self.assertRegex(CAMERA, r"cm3d\.controlsSeen")

    def test_the_first_entry_list_steps_aside_by_itself(self):
        # on a phone the list covers the top of the HUD: it must not wait for a touch to go
        self.assertRegex(CAMERA, r"if \(guide\.firstOpen\) \{\s*if \(guide\.openAt == null\) guide\.openAt = S\.t;\s*"
                                 r"else if \(S\.t - guide\.openAt > 8\) \{ guide\.firstOpen = false; guide\.help\.open = false; \}")

    def test_the_list_sits_beside_the_button_not_over_the_ship(self):
        r = rule(".flight3d-controls")
        self.assertIn("right:100%", r)
        self.assertNotIn("translateX(-50%)", r)


class ShipTint(unittest.TestCase):
    def test_the_neutral_hull_takes_a_quarter_of_the_sky(self):
        neutral = json.loads((ROOT / "skins" / "neutral-3d.json").read_text(encoding="utf-8"))["look3d"]
        self.assertEqual(neutral["skyRim"], 0.25)
        self.assertIn("look.skyRim != null && look.skyRim < 1", CRAFT)
        self.assertIn("lerpColors(S.rimBase, rimU, Math.max(0, look.skyRim))", CRAFT)

    def test_every_other_skin_keeps_the_whole_sky_colour(self):
        others = [p for p in (ROOT / "skins").glob("*-3d.json") if p.name != "neutral-3d.json"]
        for path in others:
            self.assertNotIn("skyRim", path.read_text(encoding="utf-8"), path.name)


class PhoneViewSwitch(unittest.TestCase):
    def test_the_switch_rides_the_bottom_edge_for_a_thumb(self):
        r = rule("html.m #pov-switch.in3d")
        self.assertIn("top:auto", r)
        self.assertRegex(r, r"bottom:calc\(14px \+ env\(safe-area-inset-bottom")
        self.assertIn("left:auto!important", r, "the page script writes an inline left")
        self.assertIn("right:calc(12px", r)
        self.assertNotIn("18%", r)
        b = rule("html.m #pov-switch.in3d button")
        self.assertRegex(b, r"min-height:44px")
        # clear of the STEER label (left) and of BRAKE and LOCK above it
        self.assertRegex(r, r"max-width:calc\(100vw - 132px\)")


if __name__ == "__main__":
    unittest.main()
