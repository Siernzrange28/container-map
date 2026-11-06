"""The public world library, static transport and original neutral craft."""
import base64
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import themepacks
import vendor3d

ROOT = Path(__file__).resolve().parent
KINDS = {"code", "notes", "reference", "data", "media", "config", "shared", "archive", "unsorted"}


class PublicWorlds(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.skin = vendor3d.skin3d("neutral")
        cls.man = cls.skin["planets3d"]

    def test_library_is_public_and_covers_every_kind_with_variants(self):
        self.assertEqual(set(self.man["kinds"]), KINDS)
        self.assertNotIn("planets", self.man)
        for variants in self.man["kinds"].values():
            self.assertGreaterEqual(len(variants), 12)
            self.assertEqual(len({v["a"] for v in variants}), len(variants))
        self.assertEqual(themepacks.validate_skin3d(json.loads((ROOT / "skins/neutral-3d.json").read_text()), "neutral-3d"), [])

    def test_every_map_exists_with_its_digest_and_fits_the_budget(self):
        names = {self.man["sky"]} | {p[k] for ps in self.man["kinds"].values() for p in ps for k in ("a", "n") if p[k]}
        total = 0
        reviewed = (ROOT / "reviewed-images.txt").read_text() if (ROOT / "reviewed-images.txt").exists() else ""
        for name in names:
            raw, mime = vendor3d.serve_map(name)
            self.assertEqual(hashlib.sha256(raw).hexdigest()[:8], name.split("-")[-1].split(".")[0])
            if reviewed:
                self.assertIn(hashlib.sha256(raw).hexdigest(), reviewed)
            self.assertIn(mime, ("image/webp", "image/jpeg"))
            total += len(raw)
        self.assertLess(total, 5_000_000)

    @unittest.skipUnless(shutil.which("node"), "needs node")
    def test_pick_is_deterministic_for_any_uid_every_kind_and_alias(self):
        script = "globalThis.CM3D={module(){},provides(){},demo(){}};require('./world3d/space.js');const m=" + json.dumps(self.man) + ";" + """
          const pick=CM3D.space.pickWorld,out=[];
          for(const kind of Object.keys(m.kinds).concat(Object.keys(m.aliases),['future-kind'])) {
            for(const uid of ['', 'area:one', 'folder with spaces', '文件夹/🌍', '__proto__', '42']) {
              const a=pick(m,{uid,kind}), b=pick(m,{uid,kind});
              out.push(!!a && a===b && (m.kinds[m.aliases[kind]||kind]||m.kinds.unsorted).includes(a));
            }
          }
          const seen=new Set();for(let i=0;i<200;i++)seen.add(pick(m,{uid:'folder-'+i,kind:'code'}).a);
          const p={a:'private-map'};
          console.log(JSON.stringify({ok:out.every(Boolean),variants:seen.size,exact:pick({planets:{owner:p}}, {uid:'owner'})===p,
            absent:pick({planets:{owner:p}}, {uid:'new'})===null}));
        """
        run = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True, check=True)
        got = json.loads(run.stdout)
        self.assertEqual(got, {"ok": True, "variants": 12, "exact": True, "absent": True})

    def test_static_export_has_identical_maps_and_local_file_transport(self):
        with tempfile.TemporaryDirectory() as tmp:
            vendor3d.write_beside(str(Path(tmp, "map.html")))
            folder = Path(tmp, vendor3d.ASSET_DIR)
            name = self.man["kinds"]["notes"][0]["a"]
            raw, _ = vendor3d.serve_map(name)
            self.assertEqual((folder / name).read_bytes(), raw)
            self.assertIn(base64.b64encode(raw).decode(), (folder / (name + ".js")).read_text())
            bundle = next(folder.glob("cm3d-neutral-*.js")).read_text()
            self.assertIn("currentScript", bundle)
            self.assertIn("mapBase", bundle)

    def test_neutral_hero_and_allies_share_the_cargo_skiff(self):
        models = self.skin["models3d"]
        own, ally = models["own"], models["wingman"]
        self.assertEqual(own["span"], 24)
        self.assertEqual(ally["from"], "own")
        self.assertEqual(ally["lod"], 1)
        parts = {p["name"]: p for p in own["parts"]}
        self.assertEqual(parts["scow"]["op"], "slab")
        self.assertEqual(parts["fan-ring"]["op"], "torus")
        self.assertNotIn("mirror", parts["tower"])   # the bridge tower sits to one side
        self.assertEqual(len(own["engines"]), 1)
        self.assertEqual(own["mats"]["emit"]["c"], "#FFC46B")
        self.assertNotIn("wings", parts)
        self.assertNotIn("tail-fins", parts)


if __name__ == "__main__":
    unittest.main()
