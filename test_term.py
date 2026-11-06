#!/usr/bin/env python3
"""The live terminal: GET /term (a pane's screen as server-sent events) and POST /keys (typing into a configured
seat). Fake tmux, a temporary root and an ephemeral loopback server, as in test_bridge."""

from __future__ import annotations

import http.client
import json
import os
import threading
import unittest

import testenv  # noqa: F401  (first: clears what steers the tool)
import bridge
import test_bridge as tb
import vendor3d


class TermTmux(tb.FakeTmux):
    """FakeTmux plus what the live terminal asks: the numbers-and-capture call, history captures, chained sends."""

    def __init__(self):
        super().__init__()
        self.numbers = {"%0": [0, 2000, 2, 1, 80, 3, 0, 1, 0, 0]}
        self.history = {"%0": []}
        self.calls = []

    def __call__(self, args, timeout=8):
        self.calls.append(list(args))
        if args[0] == "display-message" and ";" in args:
            pane = args[args.index("-t") + 1]
            numbers = self.numbers.get(pane)
            item = next((value for value in self.windows.values() if value["pane"] == pane), None)
            if numbers is None or item is None:
                return self.result(args, 1, stderr="missing")
            screen = item["screen"].split("\n")[:numbers[5]]
            screen += [""] * (numbers[5] - len(screen))
            above = self.history.get(pane, [])[-min(int(args[-1].lstrip("-")), numbers[0]):] if numbers[0] else []
            head = "\x1f".join(str(value) for value in numbers)
            return self.result(args, stdout=head + "\n" + "\n".join(above + screen) + "\n")
        if args[0] == "capture-pane" and "-E" in args:
            pane = args[args.index("-t") + 1]
            return self.result(args, stdout="\n".join(self.history.get(pane, [])) + "\n")
        if args[0] in ("send-keys", "set-buffer"):
            self.sent.append(list(args))
            return self.result(args)
        return super().__call__(args, timeout)


class TermKeysTests(unittest.TestCase):
    def test_keys_become_tmux_key_names(self):
        cases = {
            "hi\r": [("text", "hi"), ("key", "Enter")],
            "\x1b[A\x1bOB\x1b[C\x1b[D": [("key", "Up"), ("key", "Down"), ("key", "Right"), ("key", "Left")],
            "\x1b[1;5D\x1b[3;5~": [("key", "C-Left"), ("key", "C-DC")],
            "\x03\x1b\x7f\t\x1b[Z": [("key", "C-c"), ("key", "Escape"), ("key", "BSpace"), ("key", "Tab"),
                                     ("key", "BTab")],
            "\x1bx\x1b;": [("key", "M-x"), ("key", "M-\\;")],
            "é漢 ok": [("text", "é漢 ok")],
            "\x1b[5~\x1b[6~\x1b[15~": [("key", "PPage"), ("key", "NPage"), ("key", "F5")],
        }
        for data, wanted in cases.items():
            with self.subTest(data=data):
                self.assertEqual(bridge.term_keys(data), wanted)

    def test_reports_and_unknown_sequences_are_never_typed(self):
        for data in ("\x1b[<0;10;5M", "\x1b[I", "\x1b[O", "\x1b[?1;2c", "\x1b[200~", "\x9b", "\x1b[1;2;3A"):
            with self.subTest(data=data):
                self.assertEqual(bridge.term_keys(data), [])

    def test_a_trailing_semicolon_survives_tmux(self):
        self.assertEqual(bridge.tmux_literal("a;"), ["a", "\\;"])
        self.assertEqual(bridge.tmux_literal(";;"), ["\\;", "\\;"])
        self.assertEqual(bridge.tmux_literal("b\\;"), ["b\\", "\\;"])
        self.assertEqual(bridge.tmux_literal("a;b"), ["a;b"])

    def test_a_row_keeps_colour_and_nothing_else(self):
        raw = ("\x1b[31mred\x1b[39m \x1b]8;;http://x\x1b\\link\x1b]8;;\x1b\\ \x1b[?25l\x07bell\x1b(B"
               "\x1b]52;c;ZXZpbA==\x07")
        self.assertEqual(bridge.term_line(raw), "\x1b[31mred\x1b[39m link bell")


class TermReadTests(unittest.TestCase):
    def setUp(self):
        self.fake = TermTmux()
        self.app = object.__new__(bridge.BridgeApp)
        self.app.tmux_runner = self.fake

    def test_one_call_gives_numbers_history_and_screen(self):
        self.fake.windows["@0"]["screen"] = "one\ntwo\nthree"
        self.fake.numbers["%0"] = [2, 2000, 1, 2, 80, 3, 0, 1, 0, 0]
        self.fake.history["%0"] = ["h1", "\x1b[32mh2\x1b[0m"]
        look = self.app.term_read("%0")
        self.assertEqual(look["above"], ["h1", "\x1b[32mh2\x1b[0m"])
        self.assertEqual(look["screen"], ["one", "two", "three"])
        self.assertEqual((look["cx"], look["cy"], look["w"], look["h"]), (1, 2, 80, 3))
        self.assertEqual(len(self.fake.calls), 1)

    def test_scroll_count_from_growth_and_at_the_limit(self):
        scrolled = bridge.BridgeApp.term_scrolled
        base = {"w": 80, "h": 3, "alt": False, "hist": 5, "limit": 2000, "screen": ["a", "b", "c"],
                "above": ["1", "2", "3", "4", "5"]}
        self.assertEqual(scrolled(base, dict(base, hist=7, above=["1", "2", "3", "4", "5", "a", "b"])), 2)
        self.assertIsNone(scrolled(base, dict(base, hist=1, above=["1"])))       # history was cleared
        self.assertIsNone(scrolled(base, dict(base, hist=80)))                   # more than the rows read with it
        self.assertIsNone(scrolled(base, dict(base, w=100)))                     # resized
        self.assertIsNone(scrolled(base, dict(base, alt=True)))                  # full-screen program
        full = dict(base, hist=2000, limit=2000, above=["p", "q", "x"])
        after = dict(full, above=["x", "a", "b"], screen=["c", "d", "e"])
        self.assertEqual(scrolled(full, after), 2)
        self.assertIsNone(scrolled(full, dict(full, above=["y", "z", "w"])))     # cannot be told: start over

    def test_a_still_screen_at_the_limit_never_scrolls(self):
        still = {"w": 80, "h": 3, "alt": False, "hist": 2000, "limit": 2000, "screen": ["", "", "c"],
                 "above": ["", "", ""]}
        self.assertEqual(bridge.BridgeApp.term_scrolled(still, dict(still)), 0)
        none = dict(still, hist=0, limit=0, above=[])                  # tmux history-limit 0
        self.assertEqual(bridge.BridgeApp.term_scrolled(none, dict(none, screen=["x", "y", "z"])), 0)

    def test_a_paste_ending_in_a_semicolon_keeps_it(self):
        self.assertEqual(bridge.tmux_arg("hello;"), "hello\\;")
        self.assertEqual(bridge.tmux_arg("a;b"), "a;b")


class TermHTTPTests(unittest.TestCase):
    """test_bridge's app and loopback server (its setUp and helpers, none of its tests)."""

    def tmux(self):
        fake = TermTmux()
        self.app.tmux_runner = fake
        return fake

    def stream(self, seat, headers, frames):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        conn.request("GET", f"/term?seat={seat}", headers=headers)
        response = conn.getresponse()
        events = []
        if response.status == 200:
            while len(events) < frames:
                line = response.fp.readline().decode()
                if line.startswith("data: "):
                    events.append(json.loads(line[6:]))
        else:
            events = json.loads(response.read())
        conn.close()
        return response.status, response.getheader("Content-Type"), events

    def test_stream_needs_the_ui_session(self):
        self.tmux()
        status, _, data = self.stream("hub", {}, 1)
        self.assertEqual(status, 403)

    def test_stream_sends_a_full_frame_then_changes(self):
        fake = self.tmux()
        fake.windows["@0"]["screen"] = "\x1b[31mhello\x1b[0m\nworld\n"
        fake.numbers["%0"] = [1, 2000, 0, 1, 80, 3, 0, 1, 0, 0]
        fake.history["%0"] = ["older"]
        status, kind, events = self.stream("hub", self.ui_headers(), 1)
        self.assertEqual((status, kind), (200, "text/event-stream; charset=utf-8"))
        first = events[0]
        self.assertEqual(first["t"], "full")
        self.assertEqual((first["w"], first["h"], first["history"]), (80, 3, ["older"]))
        self.assertEqual(first["screen"], ["\x1b[31mhello\x1b[0m", "world", ""])
        reads = [args for args in fake.calls if args[0] == "display-message"]
        self.assertTrue(any(args[-1] == f"-{bridge.TERM_HISTORY}" for args in reads))   # one capture for both
        self.assertFalse(any("-E" in args for args in fake.calls))

    def test_events_follow_scrolling_and_end_when_the_pane_goes(self):
        fake = self.tmux()
        fake.windows["@0"]["screen"] = "a\nb\nc"
        times = iter([0.0, 0.0] + [float(n) * 0.1 for n in range(1, 400)])
        events = self.app.term_events("hub", clock=lambda: next(times), sleep=lambda _s: None)
        self.assertEqual(next(events)["t"], "full")
        fake.numbers["%0"][0] = 2
        fake.history["%0"] = ["a", "b"]
        fake.windows["@0"]["screen"] = "c\nd\ne"
        frame = next(events)
        self.assertEqual((frame["t"], frame["k"], frame["screen"]), ("frame", 2, ["c", "d", "e"]))
        self.assertEqual(frame["rows"], ["a", "b"])               # the rows themselves, for the page's scrollback
        self.assertEqual(next(events)["t"], "beat")          # nothing changed for TERM_BEAT seconds
        del fake.windows["@0"]
        self.assertEqual(next(events)["t"], "end")
        events.close()

    def test_keys_type_into_a_configured_seat(self):
        fake = self.tmux()
        wake = self.app.term_wake.setdefault("%0", threading.Event())
        status, _, data = self.http("POST", "/keys", headers=self.ui_headers(origin=True),
                                    value={"seat": "hub", "data": "zqx;\r\x1b[A\x03"})
        self.assertEqual(status, 200, data)
        sent = fake.sent[-1]
        self.assertEqual(sent, ["send-keys", "-t", "%0", "-l", "--", "zqx", "\\;", ";",
                                "send-keys", "-t", "%0", "Enter", ";",
                                "send-keys", "-t", "%0", "Up", ";",
                                "send-keys", "-t", "%0", "C-c"])
        self.assertTrue(wake.is_set())                          # an open stream looks again at once
        with open(self.log, encoding="utf-8") as handle:
            rows = [json.loads(line) for line in handle if '"keys"' in line]
        self.assertEqual(rows[-1]["enter"], 1)
        self.assertNotIn("zqx", json.dumps(rows[-1]))               # metadata only, never what was typed

    def test_paste_goes_in_as_one_bracketed_paste(self):
        fake = self.tmux()
        status, _, data = self.http("POST", "/keys", headers=self.ui_headers(origin=True),
                                    value={"seat": "hub", "paste": "line one\nline two;"})
        self.assertEqual(status, 200, data)
        sent = fake.sent[-1]
        self.assertEqual(sent[0], "set-buffer")
        self.assertIn("line one\nline two\\;", sent)              # tmux reads a final '\\;' as ';'
        tail = sent[sent.index(";") + 1:]
        self.assertEqual(tail[:3], ["paste-buffer", "-p", "-d"])
        self.assertEqual(tail[-2:], ["-t", "%0"])

    def test_keys_refused_for_a_runtime_pane_a_cross_origin_page_and_a_flood(self):
        fake = self.tmux()
        status, _, _ = self.http("POST", "/keys", headers=self.ui_headers(origin=True),
                                 value={"seat": "world", "data": "x"})
        self.assertEqual(status, 403)
        status, _, _ = self.http("POST", "/keys", headers=self.ui_headers(),
                                 value={"seat": "hub", "data": "x"})
        self.assertEqual(status, 403)                            # no Origin: not the page's own fetch
        headers = self.ui_headers(origin=True)
        codes = [self.http("POST", "/keys", headers=headers, value={"seat": "hub", "data": "x"})[0]
                 for _ in range(bridge.TERM_KEYS_RATE[0] + 1)]
        self.assertEqual(codes[-1], 429)
        self.assertEqual(sum(1 for args in fake.sent if args[0] == "send-keys"), bridge.TERM_KEYS_RATE[0])

    def test_keys_body_is_exact(self):
        self.tmux()
        headers = self.ui_headers(origin=True)
        for value in ({"seat": "hub"}, {"seat": "hub", "data": "x", "paste": "y"}, {"seat": "hub", "data": ""},
                      {"seat": "hub", "data": "x" * (bridge.TERM_KEYS_MAX + 1)}):
            with self.subTest(value=str(value)[:40]):
                status, _, _ = self.http("POST", "/keys", headers=headers, value=value)
                self.assertIn(status, (400, 413))


for _name, _value in vars(tb.BridgeTests).items():
    if not _name.startswith(("test", "__")) and _name not in vars(TermHTTPTests):
        setattr(TermHTTPTests, _name, _value)


class TermPhoneTests(unittest.TestCase):
    def test_the_phone_listener_has_no_terminal(self):
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "bridge.py"), encoding="utf-8") as handle:
            source = handle.read()
        phone = source[source.index("class RemoteHandler(Handler):"):]
        self.assertNotIn('"/term"', phone)
        self.assertNotIn('"/keys"', phone)
        self.assertIsNone(re_match_remote_api("/term?seat=hub"))
        self.assertIsNone(re_match_remote_api("/keys"))


def re_match_remote_api(path):
    import re
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "render-game.py"), encoding="utf-8") as handle:
        source = handle.read()
    found = re.search(r"const REMOTE_API = /(.+)/;", source)
    return re.match(found.group(1).replace("[/]", "/"), path)


class TermVendorTests(unittest.TestCase):
    def test_xterm_is_served_by_name_with_its_stylesheet(self):
        text = vendor3d.xterm_text()
        name = vendor3d.xterm_name(text)
        self.assertRegex(name, r"\Axterm-6-[0-9a-f]{8}\.js\Z")
        self.assertEqual(vendor3d.serve(name), text)
        self.assertIsNone(vendor3d.serve("xterm-6-00000000.js"))
        self.assertIn("xterm-css", text)
        self.assertNotIn("sourceMappingURL", text)
        self.assertEqual(vendor3d.page_refs()["xterm"], name)


if __name__ == "__main__":
    unittest.main()
