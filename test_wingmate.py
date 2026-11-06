#!/usr/bin/env python3
"""test_wingmate.py — the wingmate's brain: the brief, the flattening, and the action lines a builder may end with."""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

import wingmate as G


class WingmateTests(unittest.TestCase):
    def test_actions_are_parsed_and_stripped(self):
        text = ("Projects needs a dark mode.\n\nDRAFT /srv/projects/app :: claude/haiku :: Add a dark-mode toggle to settings; "
                "persist it; run the tests.\nCHECK 20261007-045737-reply-with-the-single-wo-2b9059 :: codex\nGOTO area:Projects\n")
        clean, goto, acts = G.actions(text)
        self.assertEqual(clean, "Projects needs a dark mode.")
        self.assertEqual(goto, "area:Projects")
        self.assertEqual(acts[0], {"kind": "draft", "path": "/srv/projects/app", "runner": "claude", "model": "haiku",
                                   "text": "Add a dark-mode toggle to settings; persist it; run the tests.", "verify": None})
        self.assertEqual(acts[1], {"kind": "check", "id": "20261007-045737-reply-with-the-single-wo-2b9059", "runner": "codex"})
        clean2, _, acts2 = G.actions("Do it.\nDRAFT /w/x :: codex :: add tests :: VERIFY python3 -m unittest -q\n")
        self.assertEqual((clean2, acts2[0]["text"], acts2[0]["verify"]), ("Do it.", "add tests", "python3 -m unittest -q"))
        self.assertEqual(G.actions("just words")[2], [])
        self.assertEqual(G.actions("DRAFT nowhere :: bad agent name! :: x")[2], [])

    def test_actions_are_capped_at_four_unless_a_caller_asks_for_more(self):
        lines = "".join(f"CHECK 20261013-1200{i:02d}-t-{i:06x} :: codex\n" for i in range(9))
        self.assertEqual(len(G.actions(lines)[2]), 4)
        self.assertEqual(len(G.actions(lines, limit=60)[2]), 9)

    def test_ask_returns_actions_and_plain_text(self):
        spec = {"name": "Pip", "cmd": ["x"], "env": {}, "max_seconds": 5}
        res = G.ask(spec, "SNAP", "build it", runner=lambda cmd, text, timeout, env: (0, "**Sure.**\nDRAFT /w/a :: codex :: do the thing", ""))
        self.assertEqual((res["answer"], res["actions"][0]["runner"], res["actions"][0]["text"], res["error"]), ("Sure.", "codex", "do the thing", None))
        self.assertIn("DRAFT <absolute folder path>", G.prompt("Pip", "SNAP", "q"))

    def test_brief_names_paths_and_agents(self):
        world = {"name": "X", "root_path": "/r", "chars": [{"kind": "area", "uid": "area:A", "name": "A", "path": "/r/A", "files": 3, "age": 1, "life": "LIVE"},
                                                         {"kind": "dir", "uid": "dir:A/b", "name": "b", "path": "/r/A/b", "area": "area:A", "files": 1, "age": 0}],
                 "task_runners": [{"name": "claude", "label": "Claude", "models": [{"name": "haiku", "label": "Claude Haiku 5.5"}]},
                                  {"name": "minimax", "role": "check", "models": [{"name": "m3", "label": "MiniMax M3"}]}]}
        b = G.brief(world, [])
        self.assertIn("A [area:A] /r/A", b)
        self.assertIn("b [dir:A/b] /r/A/b", b)
        self.assertIn("claude (Claude; models: haiku = Claude Haiku 5.5)", b)
        self.assertIn("minimax (minimax; models: m3 = MiniMax M3) — checks only", b)

    def test_spec_for_reads_models_and_a_default(self):
        cfg = {"wingmate": {
            "name": "Zed", "cmd": ["/x/wrap", "-p"], "env": {"BASE": "1", "bad key": "x"}, "default": "sonnet",
            "models": [
                {"name": "haiku", "label": "Claude Haiku 5.5", "family": "claude", "env": {"PICK": "haiku"}},
                {"name": "sonnet", "label": "Claude Sonnet 5.5", "family": "claude", "args": ["--model", "sonnet"]},
                {"name": "codex", "label": "Codex (OpenAI)", "family": "openai", "cmd": ["~/bin/codex", "exec", "-"]},
                {"name": "haiku", "label": "a duplicate name is dropped"},
                {"name": "bad name!", "label": "so is an unusable one"},
                {"label": "and one without a name"},
                {"name": "odd", "family": "martian"},
            ]}}
        spec = G.spec_for(cfg, which=lambda name: None)
        self.assertEqual([m["name"] for m in spec["models"]], ["haiku", "sonnet", "codex", "odd"])
        self.assertEqual((spec["name"], spec["default"], spec["model"], spec["env"]), ("Zed", "sonnet", "Claude Sonnet 5.5", {"BASE": "1"}))
        self.assertEqual([m["family"] for m in spec["models"]], ["claude", "claude", "openai", "other"])
        self.assertEqual(spec["models"][3]["label"], "odd")                       # no label: the name
        self.assertEqual(spec["models"][2]["cmd"], [os.path.expanduser("~/bin/codex"), "exec", "-"])
        cfg["wingmate"]["default"] = "nope"                                       # an unknown default falls back to the first
        self.assertEqual(G.spec_for(cfg, which=lambda name: None)["default"], "haiku")
        self.assertEqual(G.listing(spec)[2], {"name": "codex", "label": "Codex (OpenAI)", "family": "openai"})

    def test_a_legacy_config_is_one_model(self):
        cfg = {"wingmate": {"cmd": ["/x/claude-worker", "-p"], "env": {"CLAUDE_WORKER_MODEL": "haiku"}, "model": "Claude Haiku 5.5"}}
        spec = G.spec_for(cfg, which=lambda name: None)
        self.assertEqual([(m["name"], m["label"]) for m in spec["models"]], [("default", "Claude Haiku 5.5")])
        self.assertEqual((spec["default"], spec["model"], spec["cmd"], spec["env"]),
                         ("default", "Claude Haiku 5.5", ["/x/claude-worker", "-p"], {"CLAUDE_WORKER_MODEL": "haiku"}))
        merged = G.model_spec(spec, "default")
        self.assertEqual((merged["cmd"], merged["env"], merged["model"]),      # a claude command: no tools
                         (["/x/claude-worker", "-p", "--tools", "", "--strict-mcp-config"],
                          {"CLAUDE_WORKER_MODEL": "haiku"}, "Claude Haiku 5.5"))
        bare = {"name": "Pip", "cmd": ["x"], "env": {}}                        # a hand-built spec with no list at all
        self.assertEqual(G.listing(bare), [{"name": "default", "label": "default", "family": "other"}])
        self.assertEqual(G.model_spec(bare)["cmd"], ["x"])

    def test_nothing_configured_detects_claude_and_codex(self):
        both = G.spec_for({}, which=lambda name: f"/bin/{name}" if name in ("claude", "codex") else None)
        self.assertEqual([(m["name"], m["label"], m["family"]) for m in both["models"]],
                         [("haiku", "Claude Haiku 5.5", "claude"), ("sonnet", "Claude Sonnet 5.5", "claude"),
                          ("opus", "Claude Opus 5.5", "claude"), ("codex", "Codex (OpenAI)", "openai")])
        self.assertEqual((both["default"], both["cmd"]), ("haiku", ["/bin/claude", "-p"]))
        self.assertEqual(G.model_spec(both, "opus")["cmd"],
                         ["/bin/claude", "-p", "--model", "opus", "--tools", "", "--strict-mcp-config"])
        self.assertEqual(G.model_spec(both, "codex")["cmd"], ["/bin/codex", "exec", "--sandbox", "read-only", "--skip-git-repo-check", "-"])
        only_claude = G.spec_for({}, which=lambda name: "/bin/claude" if name == "claude" else None)
        self.assertEqual([m["name"] for m in only_claude["models"]], ["haiku", "sonnet", "opus"])
        self.assertIsNone(G.spec_for({}, which=lambda name: None))
        self.assertIsNone(G.spec_for({"wingmate": {"off": True}}, which=lambda name: "/bin/claude"))
        self.assertIsNone(G.spec_for({"guide": {"cmd": ["/x/old"]}}, which=lambda name: None))   # the old key is not read

    def test_model_spec_merges_the_base_with_the_models_overrides(self):
        spec = G.spec_for({"wingmate": {"cmd": ["/x/wrap", "-p"], "env": {"BASE": "1", "PICK": "none"}, "models": [
            {"name": "a", "env": {"PICK": "a"}}, {"name": "b", "args": ["--model", "b"]},
            {"name": "c", "cmd": ["/y/other", "-"], "args": ["--x"], "env": {"ONLY": "c"}}]}}, which=lambda name: None)
        a, b, c = (G.model_spec(spec, n) for n in "abc")
        self.assertEqual((a["cmd"], a["env"]), (["/x/wrap", "-p"], {"BASE": "1", "PICK": "a"}))      # env merged over the base
        self.assertEqual((b["cmd"], b["env"]), (["/x/wrap", "-p", "--model", "b"], {"BASE": "1", "PICK": "none"}))   # args appended
        self.assertEqual((c["cmd"], c["env"]), (["/y/other", "-", "--x"], {"BASE": "1", "PICK": "none", "ONLY": "c"}))   # cmd replaced
        self.assertEqual(G.model_spec(spec)["model"], "a")                          # no name = the default
        self.assertIsNone(G.model_spec(spec, "d"))
        self.assertIsNone(G.model_spec(None, "a"))
        self.assertEqual(spec["env"], {"BASE": "1", "PICK": "none"})                # the base itself is not changed

    def test_a_claude_command_answers_with_no_tools_and_no_mcp_servers(self):
        flags, cm = ["--tools", "", "--strict-mcp-config"], "/home/u/tools/claude-worker"
        spec = G.spec_for({"wingmate": {"cmd": [cm, "-p"], "env": {"BASE": "1"}, "models": [
            {"name": "haiku", "family": "claude", "env": {"CLAUDE_WORKER_MODEL": "haiku"}},
            {"name": "opus", "family": "claude", "args": ["--model", "opus"]},
            {"name": "own", "family": "claude", "args": ["--tools", "Read"]},
            {"name": "by-cmd", "family": "claude", "cmd": ["/y/claude", "-p"], "args": ["--model", "b"]},
            {"name": "codex", "family": "openai", "cmd": ["/x/codex", "exec", "-"]},
            {"name": "wrapped", "family": "claude", "cmd": ["/y/wrap", "-p"]},
            {"name": "other", "family": "other"},
            {"name": "gem", "family": "google", "cmd": ["/x/claude-lookalike", "-p"]}]}},
            which=lambda name: None)
        cmd = lambda name: G.model_spec(spec, name)["cmd"]
        self.assertEqual(cmd("haiku"), [cm, "-p"] + flags)              # the wrapper skips permissions
        self.assertEqual(cmd("opus"), [cm, "-p", "--model", "opus"] + flags)     # args still applied
        self.assertEqual(cmd("own"), [cm, "-p", "--tools", "Read", "--strict-mcp-config"])   # owner's flag stays
        self.assertEqual(G.locked_down(["claude", "-p", "--strict-mcp-config"]),
                         ["claude", "-p", "--strict-mcp-config", "--tools", ""])
        self.assertEqual(cmd("by-cmd"), ["/y/claude", "-p", "--model", "b"] + flags)   # a model's own cmd counts
        self.assertEqual(cmd("codex"), ["/x/codex", "exec", "-"])                  # codex is read-only already
        self.assertEqual(cmd("wrapped"), ["/y/wrap", "-p"])                      # not a claude command
        self.assertEqual(cmd("other"), [cm, "-p"] + flags)                       # whatever the family
        self.assertEqual(cmd("gem"), ["/x/claude-lookalike", "-p"] + flags)
        self.assertEqual(spec["cmd"], [cm, "-p"])                                # the base is unchanged
        self.assertEqual(spec["models"][1]["args"], ["--model", "opus"])
        calls = []
        G.ask(spec, "SNAP", "q", "haiku", lambda c, text, timeout, env: calls.append(c) or (0, "x", ""))
        self.assertEqual(calls, [[cm, "-p"] + flags])                            # the wingmate runs locked down too
        self.assertEqual(G.locked_down(["claude", "-p"]), ["claude", "-p"] + flags)
        self.assertEqual(G.locked_down([]), [])
        self.assertEqual(G.locked_down(["/x/codex", "exec", "-"]), ["/x/codex", "exec", "-"])

    def test_the_legacy_single_command_config_is_locked_down_too(self):
        flags = ["--tools", "", "--strict-mcp-config"]
        spec = G.spec_for({"wingmate": {"cmd": ["claude-worker", "-p"]}}, which=lambda name: None)
        self.assertEqual(spec["models"][0]["family"], "other")           # no family in a legacy config
        self.assertEqual(G.model_spec(spec)["cmd"], ["claude-worker", "-p"] + flags)
        with_env = G.spec_for({"wingmate": {"cmd": ["/x/claude-worker", "-p"], "model": "Haiku",
                                            "env": {"CLAUDE_WORKER_MODEL": "haiku"}}}, which=lambda n: None)
        got = G.model_spec(with_env)
        self.assertEqual((got["cmd"], got["env"]), (["/x/claude-worker", "-p"] + flags,
                                                    {"CLAUDE_WORKER_MODEL": "haiku"}))
        calls = []
        G.ask(spec, "SNAP", "q", runner=lambda c, text, timeout, env: calls.append(c) or (0, "x", ""))
        self.assertEqual(calls, [["claude-worker", "-p"] + flags])
        other = G.spec_for({"wingmate": {"cmd": ["/x/some-other-model", "-p"]}}, which=lambda n: None)
        self.assertEqual(G.model_spec(other)["cmd"], ["/x/some-other-model", "-p"])

    def test_ask_runs_the_chosen_model_and_names_it(self):
        spec = G.spec_for({"wingmate": {"cmd": ["/x/wrap", "-p"], "models": [
            {"name": "haiku", "label": "Claude Haiku 5.5", "family": "claude", "env": {"CLAUDE_WORKER_MODEL": "haiku"}},
            {"name": "opus", "label": "Claude Opus 5.5", "family": "claude", "env": {"CLAUDE_WORKER_MODEL": "opus"}},
            {"name": "codex", "label": "Codex (OpenAI)", "family": "openai", "cmd": ["/x/codex", "exec", "-"]}]}}, which=lambda name: None)
        calls = []

        def runner(cmd, text, timeout, env):
            calls.append({"cmd": cmd, "env": env, "timeout": timeout, "prompt": text})
            return 0, "Docs is your notes.", ""

        res = G.ask(spec, "SNAP", "where are my notes?", runner=runner)             # no model = the default
        self.assertEqual((res["answer"], res["model"], res["family"], res["error"]), ("Docs is your notes.", "Claude Haiku 5.5", "claude", None))
        self.assertEqual(calls[0]["env"]["CLAUDE_WORKER_MODEL"], "haiku")
        res = G.ask(spec, "SNAP", "where are my notes?", "opus", runner)
        self.assertEqual((res["model"], res["family"], calls[1]["cmd"], calls[1]["env"]["CLAUDE_WORKER_MODEL"]), ("Claude Opus 5.5", "claude", ["/x/wrap", "-p"], "opus"))
        res = G.ask(spec, "SNAP", "q", model="codex", runner=runner)
        self.assertEqual((res["model"], res["family"], calls[2]["cmd"], calls[2]["env"]), ("Codex (OpenAI)", "openai", ["/x/codex", "exec", "-"], calls[2]["env"]))
        self.assertFalse("BRIDGE_LIVE_PATH" in calls[2]["env"])      # never None: always the clean environment
        self.assertIn("QUESTION\nq", calls[2]["prompt"])
        self.assertEqual(calls[2]["timeout"], G.DEFAULT_SECONDS)

    def test_ask_refuses_an_unknown_model_without_running_anything(self):
        spec = G.spec_for({"wingmate": {"cmd": ["/x/wrap", "-p"]}}, which=lambda name: None)
        calls = []
        res = G.ask(spec, "SNAP", "q", "gpt-9", runner=lambda *a: calls.append(a) or (0, "x", ""))
        self.assertEqual((res["rc"], res["error"], res["answer"], res["model"], calls), (2, "no such model", "", None, []))
        res = G.ask(spec, "SNAP", "q" * (G.MAX_QUESTION + 1), runner=lambda *a: calls.append(a) or (0, "x", ""))
        self.assertEqual((res["rc"], calls), (2, []))                                # the question limits still hold
        failed = G.ask(spec, "SNAP", "q", runner=lambda *a: (3, "", "boom"))
        self.assertEqual((failed["rc"], failed["error"], failed["model"]), (3, "the wingmate failed: boom", "default"))

    def test_the_name_is_the_configs_else_the_skins_else_scout(self):
        import json
        import tempfile
        which = lambda name: None                                                  # noqa: E731
        self.assertEqual(G.spec_for({"wingmate": {"cmd": ["/x/w"]}}, which=which)["name"], "Scout")
        self.assertEqual(G.spec_for({"wingmate": {"cmd": ["/x/w"], "name": "Own"}}, which=which)["name"], "Own")
        self.assertEqual(G.default_name({}), "Scout")
        with tempfile.TemporaryDirectory() as tmp:
            import themepacks
            skin = themepacks.load_skin("neutral", [os.path.join(os.path.dirname(os.path.abspath(__file__)), "skins")])
            skin["name"] = "mine"
            skin["names"]["wingmate"] = "Pip"
            with open(os.path.join(tmp, "mine.json"), "w", encoding="utf-8") as handle:
                json.dump(skin, handle)
            cfg = {"skin": "mine", "skins_dir": tmp, "wingmate": {"cmd": ["/x/w"]}}
            self.assertEqual(G.default_name(cfg), "Pip")
            self.assertEqual(G.spec_for(cfg, which=which)["name"], "Pip")
        self.assertEqual(G.model_spec({"cmd": ["x"]})["name"], "Scout")                # a hand-built spec with no name

    def test_manual_names_the_theme_key_and_the_packs(self):
        line = next(l for l in G.MANUAL.splitlines() if l.startswith("THEMES:"))
        for word in ("V cycles", "texture pack", "classic", "earthen", "blueprint", "synthwave", "phosphor", "themes/<name>.json", "theme"):
            self.assertIn(word, line)
        controls = next(l for l in G.MANUAL.splitlines() if l.startswith("CONTROLS:"))
        self.assertNotRegex(controls, r"\bV\b")                                       # the key was free: no other control claims it
        self.assertLess(len(G.MANUAL), G.MAX_BRIEF // 2)                              # the brief still has room for the snapshot

    def test_manual_names_what_the_page_shows_under_the_active_skin(self):
        self.assertIn("BLIGHT-class = the most troubled root", G.MANUAL)       # the neutral words
        self.assertIn("(its plate reads CARRIER)", G.MANUAL)
        self.assertNotIn("{", G.MANUAL)
        own = G.manual({"carrier": "THE ARK", "worst": "DUSK", "merged": "x", "wingmate": "y"})
        self.assertIn("DUSK-class = the most troubled root", own)
        self.assertIn("(its plate reads THE ARK)", own)
        self.assertEqual(G.manual({"worst": 5, "carrier": ""}), G.MANUAL, "not text: the default")
        self.assertIn("DUSK-class", G.prompt("Pip", "SNAP", "q", {"worst": "DUSK"}))
        with tempfile.TemporaryDirectory() as tmp:
            import themepacks
            here = os.path.dirname(os.path.abspath(__file__))
            skin = themepacks.load_skin("neutral", [os.path.join(here, "skins")])
            skin["name"] = "mine"
            skin["names"].update(carrier="THE ARK", worst="DUSK")
            with open(os.path.join(tmp, "mine.json"), "w", encoding="utf-8") as handle:
                json.dump(skin, handle)
            cfg = {"skin": "mine", "skins_dir": tmp, "wingmate": {"cmd": ["/x/w"]}}
            spec = G.spec_for(cfg, which=lambda name: None)
            self.assertEqual((spec["words"]["carrier"], spec["words"]["worst"]), ("THE ARK", "DUSK"))
            seen = []
            res = G.ask(spec, "SNAP", "q",
                        runner=lambda cmd, text, timeout, env: seen.append(text) or (0, "ok", ""))
            self.assertIsNone(res["error"])
            self.assertIn("DUSK-class", seen[0])
            self.assertIn("(its plate reads THE ARK)", seen[0])
            self.assertNotIn("BLIGHT-class", seen[0])


if __name__ == "__main__":
    unittest.main()


class Environment(unittest.TestCase):
    """Lane H1: the model command reads the page's text, so it gets the clean agent environment."""

    NAMES = ("BRIDGE_LIVE_PATH", "BRIDGE_PORT", "CONTAINER_ROOT", "CONTAINER_CONFIG", "GIT_DIR",
             "GIT_INDEX_FILE", "GIT_AUTHOR_NAME", "PICK", "KEEP")
    DUMP = f"import os,sys; sys.stdout.write(','.join(sorted(k for k in os.environ if k in {NAMES!r})))"

    def test_the_model_command_does_not_inherit_the_maps_own_environment(self):
        spec = G.spec_for({"wingmate": {"cmd": [sys.executable, "-c", self.DUMP],
                                        "models": [{"name": "m", "env": {"PICK": "1"}}]}},
                          which=lambda name: None)
        mine = {"BRIDGE_LIVE_PATH": "/x/run/bridge-1.json", "BRIDGE_PORT": "1", "CONTAINER_ROOT": "/x",
                "CONTAINER_CONFIG": "/x/c.json", "GIT_DIR": "/nonexistent/.git",
                "GIT_INDEX_FILE": "/nonexistent/index", "GIT_AUTHOR_NAME": "me", "KEEP": "1"}
        with mock.patch.dict(os.environ, mine):
            res = G.ask(spec, "SNAP", "where are my notes?")        # the real runner, a real process
        self.assertIsNone(res["error"], res)
        self.assertEqual(sorted(res["answer"].split(",")), ["GIT_AUTHOR_NAME", "KEEP", "PICK"])
