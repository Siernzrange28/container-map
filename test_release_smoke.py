#!/usr/bin/env python3
"""test_release_smoke.py — the 3D view survives the release:

  - NOTICE names every source a port marker /* port: <Key> (<licence>) */ in ui/*.js and world3d/*.js points at, with
    that licence, and the vendored three.js with its licence file;
  - an offline install (an sdist, pip --no-index into a venv that sees the system's site-packages) renders the bench
    page (bench.py's generated workspace; a one-folder page where bench.py is not shipped) that names the two 3D scripts
    and writes them beside it, and its bridge serves them from /vendor/ (immutable, JavaScript);
  - the public export (public-export.sh into a scratch folder: its leak, image and name scans pass) renders a neutral
    page that enters 3D in a headless Chrome (WebGL 2 in software), with only the neutral bundle beside it.

Each part skips when what it needs is missing (pip and setuptools; public-export.sh, which only the private repo has;
node and a Chrome). Run: python3 -m unittest -q test_release_smoke
"""
import http.client
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from pathlib import Path

import testenv  # noqa: F401  (first: clears what steers the tool)
import test_game
try:
    import bench     # the generated bench workspace (the private repo's; the public export ships without it)
except ImportError:
    bench = None

ROOT = Path(__file__).resolve().parent
EXPORT = ROOT / "public-export.sh"
MARKER_RE = re.compile(r"/\*\s*port:\s*([a-z0-9][a-z0-9.-]*)\s*\(([^)]+)\)\s*\*/")
# the permissive sources the 3D view's code was written from: each must have its NOTICE entry
TRACED = ("three", "nova-lancer", "nebreck", "galaxy-shader", "deathstar", "spacekit", "yuka",
          "cosmos", "deep-fold", "webgl-crt", "space-3d", "ashima-noise", "glsl-atmosphere", "psx-shader",
          "urp-psx", "urp-psx-forked", "three-quarks", "procedural-planets",
          "postprocessing", "tsl-textures", "three-geospatial", "instanced-mesh", "space-truckers", "lensflare-vanilla")


def notice_entries(text):
    """{Key: Licence line} from NOTICE: an entry is a block whose lines hold 'Key:' and 'Licence:'."""
    out = {}
    for block in re.split(r"\n\s*\n", text):
        key = re.search(r"(?m)^\s*Key:\s*(\S+)\s*$", block)
        lic = re.search(r"(?m)^\s*Licence:\s*(.+)$", block)
        if key and lic:
            out[key.group(1)] = lic.group(1).strip()
    return out


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def ready(module):
    return subprocess.run([sys.executable, "-c", f"import {module}"], capture_output=True).returncode == 0


class Notice(unittest.TestCase):
    def test_notice_covers_ports(self):
        notice = (ROOT / "NOTICE").read_text(encoding="utf-8")
        entries = notice_entries(notice)
        markers = []
        for folder in ("ui", "world3d"):
            for path in sorted((ROOT / folder).glob("*.js")):
                for key, lic in MARKER_RE.findall(path.read_text(encoding="utf-8")):
                    markers.append((f"{folder}/{path.name}", key, lic.strip()))
        self.assertTrue(any(key == "nebreck" for _, key, _ in markers), "the governor's port marker is in world3d/core.js")
        for where, key, lic in markers:
            with self.subTest(marker=f"{where}: {key} ({lic})"):
                self.assertIn(key, entries, f"{where} marks a port from {key!r}, which NOTICE does not list")
                self.assertTrue(entries[key].startswith(lic.split()[0]), f"NOTICE says {entries[key]!r} for {key}, the marker {lic!r}")
        for key in TRACED:
            with self.subTest(source=key):
                self.assertIn(key, entries)
        keys = re.findall(r"(?m)^\s*Key:\s*(\S+)\s*$", notice)
        self.assertEqual(len(keys), len(set(keys)), "a Key is listed twice")
        self.assertIn("vendor/three-r180.js", notice)
        self.assertTrue((ROOT / "vendor" / "LICENSE-three.txt").is_file())
        for text in ("Permission is hereby granted, free of charge", "BSD 3-Clause License", "zlib License"):
            self.assertIn(text, notice)


class PublicMarksAreItsOwn(unittest.TestCase):
    """An outside IP review (2026-11-06) found two marks that read as a known game's: gold milestone rings beside a
    zero-padded counter in the 3D HUD, and a yellow-green hexagon cage round a craft as it rolls on the 2D map. The
    public look draws flat segments with a plain number and a pale corkscrew wake; the other two stay behind a skin key
    (feel3d.milestoneRings, fx2d.roll) that no public skin sets."""

    def test_the_public_skins_set_neither_key(self):
        for name in ("neutral.json", "neutral-3d.json"):
            skin = json.loads((ROOT / "skins" / name).read_text(encoding="utf-8"))
            self.assertNotIn("fx2d", skin, name)
            self.assertNotIn("milestoneRings", skin.get("feel3d") or {}, name)

    def test_the_rings_and_the_cage_draw_only_when_a_skin_asks(self):
        hud = (ROOT / "world3d" / "hud.js").read_text(encoding="utf-8")
        self.assertIn("ringStyle ? ringsSvg(3, ms) : milestonesSvg(3, ms)", hud)
        self.assertIn("ringStyle ? ('00' + done).slice(-3) : String(done)", hud)
        segments = hud[hud.index("function milestonesSvg("):hud.index("function ringsSvg(")]
        for mark in ("ellipse", "Gradient"):
            self.assertNotIn(mark, segments)
        page = (ROOT / "render-game.py").read_text(encoding="utf-8")
        self.assertIn("fx2d.roll==='deflector') ? fxRollDeflector(x,y,ang,P,t) : fxRollWake(x,y,ang,P,t)", page)
        wake = page[page.index("function fxRollWake("):page.index("function fxRollDeflector(")]
        for mark in ("ffff66", "80ff80", "TAU/6"):
            self.assertNotIn(mark, wake)


@unittest.skipUnless(ready("pip") and ready("setuptools") and ready("venv"), "pip, setuptools and venv are needed")
class OfflineInstall(unittest.TestCase):
    def test_an_offline_install_renders_a_page_and_serves_the_3d_scripts(self):
        with tempfile.TemporaryDirectory(prefix="cm-rel-") as tmp:
            tree = Path(tmp, "tree")
            shutil.copytree(ROOT, tree, ignore=shutil.ignore_patterns(
                ".git", ".claude", "__pycache__", "*.egg-info", "build", "dist", ".container-map", "*.pyc"))
            run = subprocess.run([sys.executable, "setup.py", "-q", "sdist", "-d", str(Path(tmp, "sdist"))], cwd=tree,
                                 capture_output=True, text=True, timeout=300)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr[-3000:])
            archive = next(Path(tmp, "sdist").glob("*.tar.gz"))
            with tarfile.open(archive) as handle:
                try:
                    handle.extractall(Path(tmp, "unpacked"), filter="data")
                except TypeError:                     # python without extraction filters
                    handle.extractall(Path(tmp, "unpacked"))
            source = next(Path(tmp, "unpacked").iterdir())
            venv = Path(tmp, "venv")
            run = subprocess.run([sys.executable, "-m", "venv", "--system-site-packages", "--without-pip", str(venv)],
                                 capture_output=True, text=True, timeout=120)
            self.assertEqual(run.returncode, 0, run.stderr[-2000:])
            py = str(venv / "bin" / "python")
            if subprocess.run([py, "-c", "import pip"], capture_output=True).returncode:
                self.skipTest("the system pip is not visible from a --system-site-packages venv")
            run = subprocess.run([py, "-m", "pip", "install", "--no-index", "--no-build-isolation", "--disable-pip-version-check",
                                  "-q", str(source)], capture_output=True, text=True, timeout=300)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr[-3000:])
            where = subprocess.run([py, "-c", "import os, vendor3d; print(os.path.dirname(vendor3d.__file__))"],
                                   capture_output=True, text=True, cwd=tmp, timeout=60)
            self.assertEqual(where.returncode, 0, where.stderr)
            lib = Path(where.stdout.strip())
            self.assertTrue(str(lib).startswith(str(venv)), lib)
            for rel in ("render-game.py", "bridge.py", "vendor3d.py", "world3d/core.js", "vendor/three-r180.js", "vendor/LICENSE-three.txt"):
                self.assertTrue((lib / rel).is_file(), rel)
            manifest = json.loads((lib / "skins/neutral-planets3d.json").read_text())
            maps = {manifest["sky"]} | {p[k] for ps in manifest["kinds"].values() for p in ps for k in ("a", "n") if p[k]}
            for name in maps:
                self.assertTrue((lib / "skins/neutral-planets3d" / name).is_file(), name)
            # the bench page (twelve planets, problems, rooms, repos, crons), rendered by the installed copy;
            # where bench.py is not shipped (the public export), a stranger's one-folder first page
            out = Path(tmp, "site", "game.html")
            if bench is not None:
                cfg = bench.build_workspace(str(Path(tmp, "bench")), 1, [])
                env = dict(bench.env_for(cfg, str(Path(tmp, "bench"))), CONTAINER_OUT=str(out), HOME=tmp)
            else:
                Path(tmp, "work", "alpha").mkdir(parents=True)
                Path(tmp, "work", "alpha", "notes.md").write_text("notes\n", encoding="utf-8")
                cfg = test_game.fixture_config(tmp)
                env = dict(os.environ, CONTAINER_CONFIG=str(cfg), CONTAINER_OUT=str(out), HOME=tmp)
            env.pop("BRIDGE_PORT", None)
            run = subprocess.run([py, str(lib / "render-game.py")], cwd=tmp, env=env, capture_output=True, text=True, timeout=300)
            self.assertEqual(run.returncode, 0, run.stderr[-3000:])
            page = out.read_text(encoding="utf-8")
            if bench is not None:
                for planet in ("area:ops", "area:scripts", "area:api", "area:archive"):
                    self.assertIn(f'"{planet}"', page)
            refs = json.loads(re.search(r"(?m)^const V3D=(.*);$", page).group(1))
            beside = sorted(p.name for p in (out.parent / "cm3d").iterdir() if not p.name.startswith("cm3dp-"))
            self.assertEqual(beside, sorted([refs["three"], refs["bundle"]]))
            self.assertRegex(refs["bundle"], r"^cm3d-neutral-[0-9a-f]{8}\.js$")
            # ... and served by the installed bridge
            port, live = free_port(), Path(tmp, "live.json")
            proc = subprocess.Popen([py, str(lib / "bridge.py"), "--port", str(port), "--seats", str(Path(tmp, "no-seats.json")),
                                     "--game", str(out), "--renderer", str(lib / "render-game.py"), "--live-file", str(live),
                                     "--log-file", str(Path(tmp, "bridge.log"))], cwd=tmp, env=env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                deadline = time.time() + 30
                while time.time() < deadline:
                    try:
                        if json.loads(live.read_text(encoding="utf-8")).get("pid") == proc.pid:
                            break
                    except (OSError, ValueError):
                        pass
                    self.assertIsNone(proc.poll(), "the installed bridge exited")
                    time.sleep(0.1)
                for name in (refs["three"], refs["bundle"]):
                    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
                    conn.request("GET", "/vendor/" + name, headers={"Host": f"127.0.0.1:{port}"})
                    r = conn.getresponse()
                    data = r.read()
                    conn.close()
                    with self.subTest(name=name):
                        self.assertEqual(r.status, 200)
                        self.assertEqual(r.getheader("Cache-Control"), "public, max-age=31536000, immutable")
                        self.assertEqual(r.getheader("Content-Type"), "text/javascript; charset=utf-8")
                        self.assertEqual(data, (out.parent / "cm3d" / name).read_bytes())
            finally:
                proc.terminate()
                try:
                    proc.wait(10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()


ENTER_RUNNER = r"""
const [port, url] = process.argv.slice(2);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let list;
for (let i = 0; i < 100 && !list; i++) { try { list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json(); } catch (e) { await sleep(100); } }
const target = list.find((t) => t.type === 'page');
const ws = new WebSocket(target.webSocketDebuggerUrl);
const pending = new Map(); let id = 0, blocked = 0; const errors = [];
const send = (method, params = {}) => new Promise((res, rej) => { const i = ++id; pending.set(i, { res, rej }); ws.send(JSON.stringify({ id: i, method, params })); });
ws.onmessage = (m) => {
  const j = JSON.parse(m.data);
  if (j.id && pending.has(j.id)) { const p = pending.get(j.id); pending.delete(j.id); if (j.error) p.rej(new Error(JSON.stringify(j.error))); else p.res(j.result); }
  else if (j.method === 'Fetch.requestPaused') {
    if (/^(file|data|about):/.test(j.params.request.url)) send('Fetch.continueRequest', { requestId: j.params.requestId });
    else { blocked++; send('Fetch.failRequest', { requestId: j.params.requestId, errorReason: 'BlockedByClient' }); }
  } else if (j.method === 'Runtime.exceptionThrown') errors.push(j.params.exceptionDetails.exception?.description || j.params.exceptionDetails.text);
};
await new Promise((r) => { ws.onopen = r; });
await send('Fetch.enable', { patterns: [{ urlPattern: '*' }] });
await send('Runtime.enable'); await send('Page.enable');
await send('Emulation.setDeviceMetricsOverride', { width: 1280, height: 720, deviceScaleFactor: 1, mobile: false });
const loaded = new Promise((r) => ws.addEventListener('message', (m) => { if (JSON.parse(m.data).method === 'Page.loadEventFired') r(); }));
await send('Page.navigate', { url });
await loaded; await sleep(1500);
const r = await send('Runtime.evaluate', { returnByValue: true, awaitPromise: true, expression: `(async()=>{
  CM.enter3d({});
  for(let i=0;i<80&&__3dAudit().mode!=='fp';i++) await new Promise(r=>setTimeout(r,100));
  const a=__3dAudit();
  return {mode:a.mode, reason:a.reason, renderer:a.renderer, three:a.three, calls:a.calls, errors:a.errors,
    scripts:[...document.scripts].map(s=>s.src.split('/').pop()).filter(Boolean)};
})()` });
console.log('RESULT ' + JSON.stringify({ value: r.exceptionDetails ? null : r.result.value,
  exception: r.exceptionDetails ? (r.exceptionDetails.exception?.description || r.exceptionDetails.text) : null, blocked, errors: errors.slice(0, 3) }));
ws.close();
process.exit(0);
"""


@unittest.skipUnless(EXPORT.is_file() and shutil.which("bash"), "only the private repo carries public-export.sh")
class ExportEnters3D(unittest.TestCase):
    def test_the_neutral_export_enters_3d(self):
        chrome = test_game.find_chrome()
        if not (chrome and shutil.which("node")):
            self.skipTest("needs node and a Chrome or Chromium binary (set CHROME_BIN)")
        with tempfile.TemporaryDirectory(prefix="cm-rel-export-") as tmp:
            dest = Path(tmp, "export")
            run = subprocess.run(["bash", str(EXPORT), str(dest)], cwd=ROOT, capture_output=True, text=True, timeout=900)
            self.assertEqual(run.returncode, 0, run.stdout[-2000:] + run.stderr[-3000:])
            self.assertEqual(sorted(p.name for p in (dest / "skins").iterdir()), ["neutral-3d.json", "neutral-planets3d", "neutral-planets3d.json", "neutral.json"])
            for rel in ("vendor/three-r180.js", "vendor/LICENSE-three.txt", "world3d/core.js", "vendor3d.py", "NOTICE"):
                self.assertTrue((dest / rel).is_file(), rel)
            Path(tmp, "work", "alpha").mkdir(parents=True)
            Path(tmp, "work", "alpha", "notes.md").write_text("notes\n", encoding="utf-8")
            cfg = test_game.fixture_config(tmp)
            out = Path(tmp, "site", "game.html")
            env = dict(os.environ, CONTAINER_CONFIG=str(cfg), CONTAINER_OUT=str(out), HOME=tmp)
            env.pop("BRIDGE_PORT", None)
            run = subprocess.run([sys.executable, str(dest / "render-game.py")], cwd=tmp, env=env, capture_output=True,
                                 text=True, timeout=300)
            self.assertEqual(run.returncode, 0, run.stderr[-3000:])
            beside = sorted(p.name for p in (out.parent / "cm3d").iterdir() if not p.name.startswith("cm3dp-"))
            self.assertEqual(len(beside), 2)
            self.assertTrue(beside[0].startswith("cm3d-neutral-") and beside[1].startswith("three-r180-"), beside)
            runner = Path(tmp, "enter.mjs")
            runner.write_text(ENTER_RUNNER, encoding="utf-8")
            profile = Path(tmp, "profile")
            browser = subprocess.Popen(
                [chrome, "--headless=new", "--no-sandbox", "--remote-debugging-port=0", f"--user-data-dir={profile}",
                 "--no-first-run", "--no-default-browser-check", "--remote-allow-origins=*", "--allow-file-access-from-files",
                 "--enable-unsafe-swiftshader", "--use-angle=swiftshader", "--disable-background-networking",
                 "--disable-component-update", "--no-pings", "--hide-scrollbars", "--host-resolver-rules=MAP * ~NOTFOUND",
                 "--proxy-server=http://127.0.0.1:9", "--proxy-bypass-list=<-loopback>", "about:blank"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                port_file = profile / "DevToolsActivePort"
                deadline = time.time() + 30
                while time.time() < deadline and not (port_file.exists() and port_file.read_text().strip()):
                    if browser.poll() is not None:
                        self.fail(f"{chrome} exited with {browser.returncode}")
                    time.sleep(0.1)
                port = port_file.read_text().splitlines()[0]
                run = subprocess.run(["node", str(runner), port, out.as_uri()], capture_output=True, text=True, timeout=180)
            finally:
                browser.terminate()
                try:
                    browser.wait(10)
                except subprocess.TimeoutExpired:
                    browser.kill()
                    browser.wait()
            line = next((ln for ln in run.stdout.splitlines() if ln.startswith("RESULT ")), None)
            self.assertIsNotNone(line, (run.stdout + run.stderr)[-3000:])
            res = json.loads(line[len("RESULT "):])
            self.assertIsNone(res["exception"], res)
            self.assertEqual((res["errors"], res["blocked"]), ([], 0), res)
            got = res["value"]
            self.assertEqual((got["mode"], got["renderer"], got["three"], got["errors"]), ("fp", "webgl2", "180", []), got)
            self.assertGreater(got["calls"], 0)
            self.assertEqual(sorted(got["scripts"]), beside)


if __name__ == "__main__":
    unittest.main()
