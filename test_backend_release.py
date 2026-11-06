"""Portable release checks for phone privacy, assistant setup and Python-unpacked zips."""
import copy
import gc
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import warnings

import testenv  # noqa: F401
import bridge
import config
import districts
import wingmate

ROOT = Path(__file__).resolve().parent
ASSISTANT_HINT = {True: "Ask the AI chat helper", False: "AI chat helper: not set up (optional)"}
ASSISTANT_NEEDS = "claude, codex or gemini"


class PhoneFolderPrivacy(unittest.TestCase):
    def test_private_folders_are_counts_only_everywhere_in_the_payload(self):
        for listed in ("vault", "notes/vault", "/srv/projects/notes/vault"):
            with self.subTest(private=listed):
                rooms = []
                for name, filename in ((".private", "tax-return.pdf"),
                                       ("vault", "passwords.txt"), ("normal", "readme.txt")):
                    rooms.append({"uid": "dir:notes/" + name, "kind": "dir", "name": name,
                                  "path": "/srv/projects/notes/" + name, "area": "area:notes",
                                  "files": 1, "size": "100B", "children_total": 1,
                                  "hidden": name.startswith("."), "children": [[filename, None]],
                                  "root_files": [[filename, "100B", 0]],
                                  "newest_path": filename, "git": {"subject": filename}})
                nested = {"uid": "dir:notes/vault/inner", "kind": "dir", "name": "inner",
                          "path": "/srv/projects/notes/vault/inner", "area": "area:notes"}
                world = {"chars": [{"uid": "area:notes", "kind": "area", "name": "notes",
                                     "path": "/srv/projects/notes", "rooms": rooms,
                                     "newest_path": "/srv/projects/notes/vault/passwords.txt",
                                     "root_files": [["public.md", "10B", 0]]}, *rooms, nested]}
                original = copy.deepcopy(world)
                cfg = {"work_root": "/srv/projects", "private": [listed]}
                for source in (world, districts.page_world(world)):
                    sent = bridge.phone_world(source, cfg)
                    text = json.dumps(sent)
                    for secret in ("tax-return.pdf", "passwords.txt", "inner"):
                        self.assertNotIn(secret, text)
                    for public in ("readme.txt", "public.md", ".private", "vault"):
                        self.assertIn(public, text)
                    private = next(c for c in sent["chars"] if c.get("name") == "vault")
                    self.assertEqual((private["files"], private["children_total"]), (1, 1))
                    self.assertNotIn("children", private)
                self.assertEqual(world, original, "the local map keeps its file lists")

    def test_private_planet_drops_root_files_and_descendants(self):
        world = {"chars": [{"kind": "area", "uid": "area:vault", "name": "vault",
                             "path": "/srv/projects/vault", "files": 2,
                             "root_files": [["passwords.txt"]], "rooms": [{"name": "inner"}]},
                            {"kind": "dir", "uid": "dir:vault/inner", "name": "inner",
                             "area": "area:vault", "path": "/srv/projects/vault/inner",
                             "children": [["passwords.txt"]]}]}
        sent = bridge.phone_world(world, {"private": ["vault"]})
        self.assertEqual(len(sent["chars"]), 1)
        self.assertEqual(sent["chars"][0]["files"], 2)
        self.assertNotIn("passwords.txt", json.dumps(sent))
        self.assertNotIn("inner", json.dumps(sent))

    def test_private_conversation_filter_still_hides_ship_aliases(self):
        world = {"constellations": [{"lane": "Health", "label": "Health", "private": True, "count": 1,
                                     "threads": [{"id": "t3:secret-thread", "title": "private chat"}],
                                     "private_ids": ["t3:secret-thread"],
                                     "private_titles": ["private chat"]}],
                 "chars": [{"uid": "ship:t3:secret-thread", "name": "private chat"},
                           {"uid": "ship:t3:public-thread", "name": "public chat"}]}
        sent = bridge.phone_world(world, {})
        self.assertNotIn("secret-thread", json.dumps(sent))
        self.assertNotIn("private chat", json.dumps(sent))
        self.assertIn("public chat", json.dumps(sent))


class AssistantSetup(unittest.TestCase):
    def test_each_supported_cli_can_answer_without_the_others(self):
        for name in ("claude", "codex", "gemini"):
            spec = wingmate.spec_for({}, which=lambda n: "/bin/" + n if n == name else None)
            self.assertIsNotNone(spec, name)
            command = wingmate.model_spec(spec)["cmd"]
            self.assertEqual(command[0], "/bin/" + name)
            if name == "gemini":
                self.assertIn("plan", command)
        self.assertIsNone(wingmate.spec_for({}, which=lambda n: None))
        self.assertIsNone(wingmate.spec_for({"wingmate": {"cmd": ["claude", "-p"]}},
                                          which=lambda n: None))

    def test_rendered_input_explains_requirements_and_disables_without_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "work/notes").mkdir(parents=True)
            cfg = folder / "config.json"
            cfg.write_text(json.dumps({"work_root": str(folder / "work"), "processes": False,
                                       "tmux_session": "none", "decision_scan": False,
                                       "crontab_file": str(folder / "none")}))
            for available in (False, True):
                out = folder / ("ready.html" if available else "disabled.html")
                # Patch discovery before wingmate binds its default `which` argument.
                script = ("import runpy,shutil,sys; original=shutil.which; "
                          f"shutil.which=lambda n: ('/bin/codex' if n=='codex' and {available!r} else None) "
                          "if n in ('claude','codex','gemini') else original(n); "
                          "script=sys.argv.pop(1); runpy.run_path(script,run_name='__main__')")
                env = dict(os.environ, CONTAINER_OUT=str(out))
                run = subprocess.run([sys.executable, "-c", script, str(ROOT / "render-game.py"),
                                      "--config", str(cfg)], cwd=tmp, env=env,
                                     capture_output=True, text=True, timeout=120)
                self.assertEqual(run.returncode, 0, run.stderr[-3000:])
                page = out.read_text()
                tag = re.search(r'<input id="mateq"[^>]+>', page).group()
                self.assertIn(f'placeholder="{ASSISTANT_HINT[available]}"', tag)
                form = re.search(r'<form id="matef"[^>]*>', page).group()
                self.assertIn(ASSISTANT_NEEDS, form, "the tooltip names what the assistant needs")
                self.assertNotIn("__MATE", page)
                self.assertEqual(" disabled" in tag, not available)
                self.assertNotIn("__MATEDISABLED__", page)


class TodaySourcesFingerprint(unittest.TestCase):
    def test_a_list_of_git_folders_is_fingerprinted_folder_by_folder(self):
        # "today": {"git_dirs": [...]} once raised a TypeError in every recurring render
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp, "repo")
            (repo / ".git").mkdir(parents=True)
            (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
            quiet = lambda args, timeout=10: ""
            for dirs in ([], [str(repo)], [str(repo), str(Path(tmp, "gone"))]):
                cfg = {"today": {"git_dirs": dirs}, "work_root": tmp}
                districts.loop_inputs(cfg, str(Path(tmp, "game.html")), str(ROOT), 1e9, quiet)
            cfg = {"today": {"git_dirs": [str(repo)]}, "work_root": tmp}
            before = districts.loop_inputs(cfg, str(Path(tmp, "game.html")), str(ROOT), 1e9, quiet)
            (repo / ".git" / "HEAD").write_text("ref: refs/heads/other-branch-name\n")
            after = districts.loop_inputs(cfg, str(Path(tmp, "game.html")), str(ROOT), 1e9, quiet)
            self.assertNotEqual(before, after, "a checkout in a listed repo is a change")


class ReleaseContent(unittest.TestCase):
    def test_playbook_defines_optional_integrations_and_terms(self):
        text = (ROOT / "playbooks/meta.md").read_text()
        for term in ("Meta thread:", "Seat:", "Carrier:", "no separate chat file", "optional"):
            self.assertIn(term, text)
        self.assertNotIn("the owner", text)

    def test_directory_readers_close_their_handles(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "tags.md").write_text("| `notes/` | 1K | now | **LIVE** | notes |\n")
            (folder / "areas.json").write_text('{"version":1,"areas":{}}')
            (folder / "AGENTS.md").write_text("Instructions\n")
            (folder / "dispatch").mkdir()
            (folder / "dispatch/a.md").write_text(str(folder))
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always", ResourceWarning)
                self.assertEqual(districts.load_container_tags(str(folder / "tags.md"))[0]["notes"]["tag"], "LIVE")
                self.assertEqual(districts.load_areas_json(str(folder / "areas.json"))["version"], 1)
                self.assertEqual(districts.door_of(str(folder))["lines"], 1)
                self.assertEqual(districts.dispatch_mentions({"area:notes": str(folder)},
                                 str(folder / "dispatch"), str(folder))["area:notes"], 1)
                gc.collect()
            self.assertEqual([str(w.message) for w in caught if issubclass(w.category, ResourceWarning)], [])

    def test_version_and_public_changelog(self):
        import cli
        self.assertEqual(cli.VERSION, "1.0.0")
        text = (ROOT / "CHANGELOG.md").read_text()
        self.assertEqual(re.findall(r"(?m)^## (\S+)", text), [cli.VERSION])
        self.assertIn("folders", text)
        self.assertIn("phone", text)


@unittest.skipUnless((ROOT / "public-export.sh").is_file() and shutil.which("bash"),
                     "needs the source repo's export script and bash")
class PythonUnpackedRelease(unittest.TestCase):
    def test_release_zip_renders_without_executable_script_permissions(self):
        with tempfile.TemporaryDirectory(prefix="cm-zip-smoke-") as tmp:
            folder = Path(tmp)
            env = dict(os.environ, EXPORT_DIST=str(folder / "dist"))
            export = subprocess.run(["bash", str(ROOT / "public-export.sh"), str(folder / "export"), "--zip"],
                                    cwd=ROOT, env=env, capture_output=True, text=True, timeout=900)
            self.assertEqual(export.returncode, 0, (export.stdout + export.stderr)[-4000:])
            archive = next((folder / "dist").glob("*.zip"))
            unpack = subprocess.run([sys.executable, "-m", "zipfile", "-e", str(archive), str(folder / "unpacked")],
                                    capture_output=True, text=True, timeout=120)
            self.assertEqual(unpack.returncode, 0, unpack.stderr)
            tree = next((folder / "unpacked").iterdir())
            self.assertTrue((tree / "CHANGELOG.md").is_file())
            self.assertFalse((tree / "CHANGELOG.private.md").exists())
            # zipfile -e does not restore executable bits. Exercise the CLI's bash call anyway.
            (tree / "world.sh").chmod(0o644)
            home = folder / "home"
            home.mkdir()
            root = folder / "work"
            (root / "notes").mkdir(parents=True)
            (root / "notes/public.md").write_text("hello\n")
            env = {"PATH": "/usr/bin:/bin", "HOME": str(home), "LANG": "C.UTF-8"}
            for args in (("doctor", str(root)), ("demo", "--no-serve"),
                         ("render", str(root), "--out", str(folder / "map.html"))):
                run = subprocess.run([sys.executable, str(tree / "cli.py"), *args], cwd=tree, env=env,
                                     capture_output=True, text=True, timeout=300)
                self.assertEqual(run.returncode, 0, (args, (run.stdout + run.stderr)[-3000:]))
            self.assertTrue((folder / "map.html").is_file())


if __name__ == "__main__":
    unittest.main()
