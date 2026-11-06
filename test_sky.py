#!/usr/bin/env python3
"""test_sky.py — the sky rollup (sky.py): every source, severity then age, tiers, running work kept
out of the contacts, settled today, the quest/ledger de-dupe, absent sources, malformed rows, and
the time a large world takes."""
import calendar
import json
import time
import unittest
from datetime import datetime, timedelta

import sky

NOW = calendar.timegm((2026, 10, 11, 12, 0, 0))           # noon UTC: the same calendar day in most zones
TODAY = datetime.fromtimestamp(NOW).date()                 # sky counts calendar days in local time
MIDNIGHT = datetime(TODAY.year, TODAY.month, TODAY.day).timestamp()
DAY = 86400
KEYS = {"id", "source", "state", "title", "since", "age_days", "tier", "planet_uid", "open", "voice"}


def iso(seconds_ago):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW - seconds_ago))


def day_ago(days):
    return (TODAY - timedelta(days=days)).isoformat()


def areas():
    return [{"uid": "area:api", "kind": "area", "name": "api", "path": "/ws/api", "alias_of": ""},
            {"uid": "area:notes", "kind": "area", "name": "notes", "path": "/ws/notes", "alias_of": ""},
            {"uid": "area:link", "kind": "area", "name": "link", "path": "/ws/link", "alias_of": "/ws/api"}]


def t3ship(tid, state=None, status="IDLE", app="T3", **t3):
    ship = {"uid": "ship:t3:" + tid, "kind": "ship", "source": "t3", "name": "proj · " + tid,
            "status": status, "cwd": "/ws/api/sub", "pane": "voice of " + tid, "app": app,
            "updated_at": iso(3600), "docked_at": "area:api"}
    if state is not None:
        ship["t3"] = dict({"state": state, "since": iso(2 * DAY), "settled_at": None, "parent": None,
                           "app": app, "open_url": None}, **t3)
    return ship


def ids(out):
    return [c["id"] for c in out["contacts"]]


def by_id(out):
    return {c["id"]: c for c in out["contacts"]}


class EmptySky(unittest.TestCase):
    def test_an_empty_world_gives_the_empty_shape(self):
        self.assertEqual(sky.build({}, NOW), {
            "contacts": [], "hyper": {"n": 0, "by_source": {"t3": 0, "task": 0, "seat": 0}},
            "parked": 0, "settled_today": 0, "derelicts": 0, "by_source": {}, "oldest_days": None})

    def test_a_world_whose_sources_are_empty_has_no_contacts(self):
        world = {"chars": [], "quests": [], "agent_tasks": [], "tasks": {"items": []},
                 "today": {"ledger": [], "night": None, "needs": [], "done": []}}
        out = sky.build(world, NOW)
        self.assertEqual(out["contacts"], [])
        self.assertEqual(out["hyper"]["n"], 0)
        self.assertIsNone(out["oldest_days"])

    def test_absent_sources_give_nothing_and_a_failed_today_page_is_no_source(self):
        for world in ({"chars": None}, {"today": {"error": "today page could not be built"}},
                      {"tasks": None, "agent_tasks": None, "quests": None}, None, "world", []):
            self.assertEqual(sky.build(world, NOW), sky.empty(), world)

    def test_now_may_be_left_out(self):
        self.assertEqual(sky.build({})["contacts"], [])
        seat = {"kind": "seat", "uid": "seat:a", "name": "a", "status": "WAITING"}
        self.assertEqual(ids(sky.build({"chars": [seat]})), ["seat:a"])


class Sources(unittest.TestCase):
    def test_seats_waiting_and_draft_are_contacts_working_is_hyper(self):
        chars = areas() + [
            {"kind": "seat", "uid": "seat:w", "name": "w", "status": "WAITING", "cwd": "/ws/api/x",
             "line": "Allow this edit?", "pane": "tail"},
            {"kind": "seat", "uid": "seat:d", "name": "d", "status": "DRAFT", "cwd": "/elsewhere"},
            {"kind": "seat", "uid": "seat:r", "name": "r", "status": "WORKING"},
            # a bare shell whose pane reads as working is an empty berth (the page never hides it), not work in warp
            {"kind": "seat", "uid": "seat:sh", "name": "sh", "status": "WORKING", "shell": True, "command": "bash"},
            {"kind": "seat", "uid": "seat:i", "name": "i", "status": "IDLE"},
            {"kind": "seat", "uid": "seat:e", "name": "e", "status": "ERROR"},
            {"kind": "seat", "uid": "seat:far", "name": "far", "status": "WAITING", "remote": True}]
        out = sky.build({"chars": chars}, NOW)
        got = by_id(out)
        self.assertEqual(sorted(got), ["seat:d", "seat:w"])
        w = got["seat:w"]
        self.assertEqual((w["source"], w["state"], w["planet_uid"], w["open"], w["voice"]),
                         ("seat", "blocked", "area:api", {"uid": "seat:w"}, "Allow this edit?"))
        self.assertEqual((got["seat:d"]["state"], got["seat:d"]["planet_uid"]), ("ready", None))
        self.assertEqual(out["hyper"], {"n": 1, "by_source": {"t3": 0, "task": 0, "seat": 1}})

    def test_a_seat_with_a_stamp_is_aged_by_it(self):
        seat = {"kind": "seat", "uid": "seat:a", "name": "a", "status": "WAITING",
                "window_activity": NOW - 3 * DAY}
        c = sky.build({"chars": [seat]}, NOW)["contacts"][0]
        self.assertEqual((c["age_days"], c["tier"], c["since"]), (3, "aging", iso(3 * DAY)))

    def test_a_seat_as_the_page_builds_it_is_aged_by_its_window_activity(self):
        # render-game.py keeps tmux's #{window_activity} (epoch) as the seat's "activity"
        seat = {"kind": "seat", "uid": "seat:a", "name": "a", "status": "WAITING", "activity": NOW - 9 * DAY}
        c = sky.build({"chars": [seat]}, NOW)["contacts"][0]
        self.assertEqual((c["age_days"], c["tier"], c["since"]), (9, "raider", iso(9 * DAY)))
        seat["since"] = iso(2 * DAY)
        c = sky.build({"chars": [seat]}, NOW)["contacts"][0]
        self.assertEqual((c["age_days"], c["tier"]), (2, "aging"))

    def test_t3_states(self):
        chars = areas() + [
            t3ship("wait", "waiting"), t3ship("fail", "failed"),
            t3ship("ready", "ready", open_url="https://host:1/bridge?focus=env:ready"),
            t3ship("run", "working"), t3ship("snz", "snoozed"),
            t3ship("set", "settled", settled_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(MIDNIGHT + 60))),
            t3ship("old", "settled", settled_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(MIDNIGHT - 3600)))]
        out = sky.build({"chars": chars}, NOW)
        got = by_id(out)
        self.assertEqual(sorted(got), ["t3:fail", "t3:ready", "t3:wait"])
        self.assertEqual([got[k]["state"] for k in ("t3:wait", "t3:fail", "t3:ready")],
                         ["blocked", "failed", "ready"])
        self.assertEqual(got["t3:ready"]["open"], {"url": "https://host:1/bridge?focus=env:ready"})
        self.assertEqual(got["t3:wait"]["open"], {"uid": "ship:t3:wait"})
        self.assertEqual((got["t3:wait"]["planet_uid"], got["t3:wait"]["voice"], got["t3:wait"]["age_days"]),
                         ("area:api", "voice of wait", 2))
        self.assertEqual(out["hyper"]["by_source"]["t3"], 1)
        self.assertEqual((out["parked"], out["settled_today"]), (1, 1))

    def test_t3_ships_from_before_the_state_field_go_by_their_status(self):
        chars = [t3ship("a", status="WAITING"), t3ship("b", status="WORKING"), t3ship("c", status="IDLE")]
        out = sky.build({"chars": chars}, NOW)
        self.assertEqual([(c["id"], c["state"]) for c in out["contacts"]], [("t3:a", "blocked")])
        self.assertEqual(out["contacts"][0]["since"], iso(3600))       # updated_at when no t3.since
        self.assertEqual(out["hyper"]["by_source"]["t3"], 1)

    def test_one_thread_in_two_databases_counts_once_and_the_live_one_wins(self):
        chars = [t3ship("dup", "ready", app="T3 (v2)"), t3ship("dup", "waiting", app="T3")]
        out = sky.build({"chars": chars}, NOW)
        self.assertEqual([(c["id"], c["state"]) for c in out["contacts"]], [("t3:dup", "blocked")])
        chars = [t3ship("dup", "working", app="T3"), t3ship("dup", "working", app="T3 (v2)")]
        self.assertEqual(sky.build({"chars": chars}, NOW)["hyper"]["n"], 1)

    def test_a_subagent_raises_its_hand_on_the_parent(self):
        child = t3ship("kid", "waiting", parent="mom", since=iso(DAY + 60))
        child["pane"] = "may I run the tests?"
        chars = [t3ship("mom", "working"), child, t3ship("kid2", "working", parent="mom"),
                 t3ship("kid3", "ready", parent="mom"), t3ship("orphan", "waiting", parent="gone")]
        out = sky.build({"chars": chars}, NOW)
        got = by_id(out)
        self.assertEqual(sorted(got), ["t3:mom", "t3:orphan"])
        mom = got["t3:mom"]
        self.assertEqual((mom["state"], mom["voice"], mom["age_days"]), ("blocked", "may I run the tests?", 1))
        self.assertEqual(out["hyper"]["by_source"]["t3"], 1)           # kid2; the parent waits on its child

    def test_a_grandchild_folds_into_the_root_and_a_loop_of_parents_loses_no_thread(self):
        chars = [t3ship("top", "ready"), t3ship("mid", "working", parent="top"),
                 t3ship("low", "waiting", parent="mid"),
                 t3ship("a", "waiting", parent="b"), t3ship("b", "failed", parent="a")]
        out = sky.build({"chars": chars}, NOW)
        self.assertEqual({c["id"]: c["state"] for c in out["contacts"]},
                         {"t3:top": "blocked", "t3:a": "blocked", "t3:b": "failed"})
        self.assertEqual(out["hyper"]["by_source"]["t3"], 1)           # mid

    def test_background_agent_ships_are_hyper_seats_and_decks_are_neither(self):
        chars = [{"kind": "ship", "source": "process", "uid": "ship:process:1", "status": "WORKING"},
                 {"kind": "ship", "source": "deck", "uid": "ship:deck:m", "status": "WORKING"},
                 {"kind": "ship", "source": "deck", "uid": "ship:deck:n", "status": "STALE"}]
        out = sky.build({"chars": chars}, NOW)
        self.assertEqual((out["contacts"], out["hyper"]["by_source"]["seat"]), ([], 1))

    def test_map_threads(self):
        tasks = [{"id": "t-review", "status": "review", "title": "fix the parser", "target": "/ws/api/src",
                  "created": iso(5 * DAY), "ended": iso(2 * DAY), "check": {"verdict": "PASS"}},
                 {"id": "t-stopped", "status": "stopped", "title": "s", "created": iso(DAY)},
                 {"id": "t-failed", "status": "failed", "title": "f", "retry_due": False,
                  "last_verdict": {"verdict": "FAIL"}},
                 {"id": "t-lost", "status": "lost", "title": "l"},
                 {"id": "t-retry", "status": "failed", "retry_due": True},
                 {"id": "t-run", "status": "running"}, {"id": "t-chk", "status": "checking"},
                 {"id": "t-stop", "status": "stopping"}, {"id": "t-new", "status": "new"},
                 {"id": "t-m", "status": "merged"}, {"id": "t-x", "status": "discarded"},
                 {"id": "t-d", "status": "done"}, {"id": "t-e", "status": "error"}]
        out = sky.build({"chars": areas(), "agent_tasks": tasks}, NOW)
        got = by_id(out)
        self.assertEqual({k: v["state"] for k, v in got.items()},
                         {"task:t-review": "review", "task:t-stopped": "review",
                          "task:t-failed": "failed", "task:t-lost": "failed"})
        r = got["task:t-review"]
        self.assertEqual((r["since"], r["age_days"], r["planet_uid"], r["open"], r["voice"]),
                         (iso(2 * DAY), 2, "area:api", {"uid": "task:t-review"},
                          "fix the parser · second opinion: passed"))
        self.assertEqual(got["task:t-failed"]["voice"], "f · second opinion: found problems")
        self.assertEqual(out["hyper"]["by_source"]["task"], 5)

    def test_crons_without_a_fresh_receipt(self):
        chars = areas() + [
            {"kind": "cron", "uid": "cron:1", "name": "backup", "verdict": "STALE", "area": "area:notes",
             "receipt": "backup.log 9d stale (mtime)", "receipt_age": 9 * DAY, "fired_age": 10 * DAY},
            {"kind": "cron", "uid": "cron:2", "name": "sync", "verdict": "NO RECEIPT", "area": "area:gone"},
            {"kind": "cron", "uid": "cron:3", "name": "pull", "verdict": "NEVER", "area": None,
             "fired_age": 3600},
            {"kind": "cron", "uid": "cron:4", "name": "ok", "verdict": "RUNNING", "area": "area:api"},
            {"kind": "cron", "uid": "cron:5", "name": "late", "verdict": "LATE", "area": "area:api"}]
        got = by_id(sky.build({"chars": chars}, NOW))
        self.assertEqual({k: v["state"] for k, v in got.items()},
                         {"cron:1": "stale", "cron:2": "stale", "cron:3": "failed"})
        c = got["cron:1"]
        self.assertEqual((c["title"], c["age_days"], c["tier"], c["planet_uid"], c["open"], c["voice"]),
                         ("job backup: late", 9, "raider", "area:notes", {"uid": "cron:1"},
                          "job backup: late"))                  # the job and its verdict in words, never the receipt
        self.assertEqual((got["cron:2"]["title"], got["cron:3"]["title"]), ("job sync: no log", "job pull: never ran"))
        self.assertEqual((got["cron:2"]["planet_uid"], got["cron:2"]["since"]), (None, None))
        self.assertEqual(got["cron:3"]["since"], iso(3600))

    def test_board_items_for_a_person(self):
        board = {"generated": iso(4 * DAY), "items": [
            {"id": "old-site", "area": "api", "finding": "old-site has not changed in 210 days", "who": "owner"},
            {"id": "nm", "area": "api", "finding": "node_modules is rebuildable", "who": "agent"},
            {"id": "dated", "area": "nowhere", "path": "/ws/notes/x", "finding": "f", "opened": day_ago(20)},
            {"area": "api", "finding": "no id"}]}
        got = by_id(sky.build({"chars": areas(), "tasks": board}, NOW))
        self.assertEqual(sorted(got), ["board:dated", "board:old-site"])
        o = got["board:old-site"]
        self.assertEqual((o["state"], o["title"], o["planet_uid"], o["age_days"], o["open"]),
                         ("stale", "old-site has not changed in 210 days", "area:api", 4, None))
        self.assertEqual((got["board:dated"]["age_days"], got["board:dated"]["planet_uid"]), (20, "area:notes"))

    def test_blocked_claims(self):
        stamp = (TODAY - timedelta(days=2)).strftime("%Y%m%d")
        needs = [{"id": f"T-{stamp}-0001-seat", "title": "wire the relay", "note": "waiting on his go", "at": "10:00"},
                 {"id": "T-49", "title": "hand-numbered", "note": "", "at": "09:00"}]
        got = by_id(sky.build({"today": {"needs": needs}}, NOW))
        c = got[f"fleet:T-{stamp}-0001-seat"]
        self.assertEqual((c["state"], c["title"], c["voice"], c["age_days"]),
                         ("blocked", "wire the relay", "waiting on his go", 2))
        self.assertEqual((got["fleet:T-49"]["since"], got["fleet:T-49"]["voice"]), (None, "hand-numbered"))

    def test_an_old_blocked_claim_ages_into_a_raider_instead_of_leaving(self):
        # today["needs"] keeps the last week for the Today page; today["blocked"] holds every blocked claim
        old = (TODAY - timedelta(days=40)).strftime("%Y%m%d")
        new = (TODAY - timedelta(days=2)).strftime("%Y%m%d")
        recent = {"id": f"T-{new}-0001-a", "title": "this week", "note": "", "at": "10:00"}
        ancient = {"id": f"T-{old}-0001-b", "title": "forty days on", "note": "waiting on his ok", "at": "00:10"}
        got = by_id(sky.build({"today": {"needs": [recent], "blocked": [recent, ancient]}}, NOW))
        self.assertEqual(sorted(got), sorted([f"fleet:T-{new}-0001-a", f"fleet:T-{old}-0001-b"]))
        c = got[f"fleet:T-{old}-0001-b"]
        self.assertEqual((c["state"], c["age_days"], c["tier"], c["voice"]), ("blocked", 40, "raider", "waiting on his ok"))
        # an empty list is an answer (no blocked claim at all), not a reason to fall back to needs
        self.assertEqual(ids(sky.build({"today": {"needs": [recent], "blocked": []}}, NOW)), [])
        # a world from before "blocked" existed keeps reading needs
        self.assertEqual(ids(sky.build({"today": {"needs": [recent]}}, NOW)), [f"fleet:T-{new}-0001-a"])

    def test_ledger_rows_are_aged_from_opened_not_from_the_stored_age(self):
        rows = [{"id": "L-001", "lane": "money", "title": "pick a broker", "opened": day_ago(50),
                 "age_days": 43, "blocks": None, "estimate": True},
                {"id": "L-002", "lane": "infra", "title": "approve the move", "opened": day_ago(7),
                 "age_days": 0, "blocks": "the export", "estimate": False},
                {"id": "L-003", "lane": "x", "title": "undated row", "opened": "sometime", "age_days": 12}]
        got = by_id(sky.build({"today": {"ledger": rows}}, NOW))
        self.assertEqual({k: (v["state"], v["age_days"], v["tier"]) for k, v in got.items()},
                         {"ledger:L-001": ("stale", 50, "raider"), "ledger:L-002": ("blocked", 7, "aging"),
                          "ledger:L-003": ("stale", 12, "raider")})
        self.assertEqual((got["ledger:L-001"]["voice"], got["ledger:L-001"]["open"]), ("pick a broker", None))
        self.assertIsNone(got["ledger:L-003"]["since"])

    def test_quests(self):
        quests = [{"id": "q-aaaaaaaaaa", "days": 81, "src": "claims.md:3", "text": "the cert renewal is awaiting owner"},
                  {"id": "q-bbbbbbbbbb", "days": -1, "src": "receipts.md:9", "text": "undated: needs a human"}]
        got = by_id(sky.build({"quests": quests}, NOW))
        self.assertEqual({k: (v["state"], v["age_days"], v["tier"]) for k, v in got.items()},
                         {"quest:q-aaaaaaaaaa": ("stale", 81, "raider"), "quest:q-bbbbbbbbbb": ("stale", None, "fresh")})

    def test_night_items(self):
        night = {"date": day_ago(1), "ran": 4, "ready": 1, "failed": 1, "source": "json", "line": None,
                 "items": [{"id": "n1", "title": "tidy the docs", "repo": "/ws/notes/repo", "verdict": "ready", "minutes": 9},
                           {"id": "n2", "title": "bump deps", "repo": "api", "verdict": "FAILED", "minutes": 30},
                           {"id": "n3", "title": "unsandboxed", "repo": "", "verdict": "refused"},
                           {"id": "n4", "title": "kept already", "repo": "", "verdict": "kept"}]}
        got = by_id(sky.build({"chars": areas(), "today": {"night": night}}, NOW))
        self.assertEqual({k: v["state"] for k, v in got.items()},
                         {"night:n1": "ready", "night:n2": "failed", "night:n3": "blocked"})
        self.assertEqual((got["night:n1"]["planet_uid"], got["night:n2"]["planet_uid"], got["night:n1"]["age_days"]),
                         ("area:notes", "area:api", 1))
        self.assertEqual(got["night:n2"]["voice"], "bump deps — FAILED")

    def test_night_from_its_digest_line_alone(self):
        line = "night queue 10-10: 2 ran, 2 ready for your keep/drop, 0 FAILED"
        night = {"date": "10-10", "ran": 2, "ready": 2, "failed": 0, "items": [], "source": "digest", "line": line}
        out = sky.build({"today": {"night": night}}, NOW)
        self.assertEqual([(c["id"], c["state"], c["title"]) for c in out["contacts"]],
                         [("night:10-10", "ready", line)])
        self.assertEqual(out["contacts"][0]["age_days"], (TODAY - TODAY.replace(month=10, day=10)).days)
        night.update(failed=1)
        self.assertEqual(sky.build({"today": {"night": night}}, NOW)["contacts"][0]["state"], "failed")
        night.update(failed=0, ready=0)
        self.assertEqual(sky.build({"today": {"night": night}}, NOW)["contacts"], [])

    def test_derelicts_are_counted_never_drawn(self):
        chars = [{"kind": "runner", "name": n, "status": s}
                 for n, s in (("a", "DEAD"), ("b", "DOWN"), ("c", "FROZEN"), ("d", "LIVE"), ("e", "BROKEN"))]
        out = sky.build({"chars": chars}, NOW)
        self.assertEqual((out["derelicts"], out["contacts"]), (3, []))

    def test_settled_today_counts_settled_threads_and_todays_done_claims(self):
        chars = [t3ship("s", "settled", settled_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(MIDNIGHT + 30)))]
        today = {"done": [{"id": "T-1", "title": "a", "at": "09:00"}, {"id": "T-2", "title": "b", "at": "10:00"}]}
        self.assertEqual(sky.build({"chars": chars, "today": today}, NOW)["settled_today"], 3)


class Ordering(unittest.TestCase):
    def test_an_undated_row_with_its_own_age_sorts_by_that_age(self):
        rows = [{"id": "new", "title": "new", "opened": day_ago(1)}, {"id": "old", "title": "old", "age_days": 100}]
        self.assertEqual(ids(sky.build({"today": {"ledger": rows}}, NOW)), ["ledger:old", "ledger:new"])

    def world(self):
        return {"chars": [{"kind": "seat", "uid": "seat:new", "name": "n", "status": "WAITING", "window_activity": NOW - 60},
                          {"kind": "cron", "uid": "cron:9", "name": "c", "verdict": "STALE", "receipt_age": 30 * DAY}],
                "agent_tasks": [{"id": "r1", "status": "review", "ended": iso(1 * DAY)},
                                {"id": "r2", "status": "review", "ended": iso(4 * DAY)},
                                {"id": "r3", "status": "review"},
                                {"id": "f1", "status": "failed", "ended": iso(2 * DAY)}],
                "today": {"ledger": [{"id": "L-9", "title": "old call", "opened": day_ago(60)}],
                          "night": {"date": day_ago(0), "ready": 1, "failed": 0, "items": [], "line": "1 ready"}}}

    def test_severity_first_then_oldest_first_and_undated_last(self):
        out = sky.build(self.world(), NOW)
        self.assertEqual(ids(out), ["seat:new", "task:f1", "task:r2", "task:r1", "task:r3",
                                    "night:" + day_ago(0), "ledger:L-9", "cron:9"])
        states = [c["state"] for c in out["contacts"]]
        self.assertEqual(states, sorted(states, key=sky.STATES.index))

    def test_rollup_counts(self):
        out = sky.build(self.world(), NOW)
        self.assertEqual(out["by_source"], {"seat": 1, "task": 4, "night": 1, "ledger": 1, "cron": 1})
        self.assertEqual(out["oldest_days"], 60)

    def test_the_same_id_twice_is_one_contact(self):
        tasks = [{"id": "x", "status": "review", "title": "first"}, {"id": "x", "status": "failed", "title": "second"}]
        out = sky.build({"agent_tasks": tasks}, NOW)
        self.assertEqual([(c["id"], c["title"]) for c in out["contacts"]], [("task:x", "first")])


class Tiers(unittest.TestCase):
    def test_whole_days_zero_is_fresh_one_to_seven_aging_eight_and_more_raider(self):
        # whole days, the number a contact shows: a date-only stamp (a ledger row's opened) has no
        # time of day, so its age is only ever whole calendar days, and every source ages the same way
        cases = [(0, 0, "fresh"), (0.99, 0, "fresh"), (1, 1, "aging"), (7.5, 7, "aging"), (8, 8, "raider"),
                 (-2, 0, "fresh")]                                   # a stamp from the future is fresh
        for days, age, tier in cases:
            c = sky.build({"agent_tasks": [{"id": "t", "status": "review", "ended": iso(days * DAY)}]},
                          NOW)["contacts"][0]
            self.assertEqual((c["age_days"], c["tier"]), (age, tier), days)

    def test_an_undated_contact_is_fresh_with_no_age(self):
        c = sky.build({"agent_tasks": [{"id": "t", "status": "review"}]}, NOW)["contacts"][0]
        self.assertEqual((c["since"], c["age_days"], c["tier"]), (None, None, "fresh"))


class QuestLedgerDedupe(unittest.TestCase):
    def test_a_quest_that_is_a_ledger_row_is_left_to_the_ledger(self):
        ledger = [{"id": "L-1", "title": "Decide: renew the TLS cert or let it lapse?", "opened": day_ago(10)},
                  {"id": "L-2", "title": "move the backups to cold storage", "opened": day_ago(9)},
                  {"id": "L-3", "title": "pick a name", "opened": day_ago(8)}]
        quests = [{"id": "q-1", "days": 10, "text": "decide renew the tls cert or let it lapse"},           # same, normalized
                  {"id": "q-1b", "days": 9, "text": "  MOVE the backups -- to cold storage!  "},          # same, normalized
                  {"id": "q-2", "days": 9, "text": "audit the job that will move the backups to cold storage"},
                  {"id": "q-3", "days": 8, "text": "pick a name for the tool, awaiting owner"},
                  {"id": "q-4", "days": 81, "text": "an unrelated parked line, needs a human"}]
        out = sky.build({"quests": quests, "today": {"ledger": ledger}}, NOW)
        # only an equal normalized title is the same loop; a line that merely mentions a title stays, and
        # by_source keeps any double count in sight
        self.assertEqual(sorted(i for i in ids(out) if i.startswith("quest:")), ["quest:q-2", "quest:q-3", "quest:q-4"])
        self.assertEqual(out["by_source"], {"ledger": 3, "quest": 3})

    def test_titles_in_any_script_compare_by_their_letters(self):
        ledger = [{"id": "L", "title": "批准预算", "opened": day_ago(3)}, {"id": "M", "title": "Überprüfen Sie das Budget"}]
        quests = [{"id": "q-1", "days": 3, "text": "批准预算"}, {"id": "q-2", "days": 3, "text": "ÜBERPRÜFEN sie das budget!"},
                  {"id": "q-3", "days": 3, "text": "批准"}]
        self.assertEqual(sorted(ids(sky.build({"quests": quests, "today": {"ledger": ledger}}, NOW))),
                         ["ledger:L", "ledger:M", "quest:q-3"])

    def test_a_ledger_row_that_does_not_show_leaves_its_quest_alone(self):
        ledger = [{"title": "move the backups to cold storage", "opened": day_ago(9)}]      # no id: skipped
        quests = [{"id": "q-2", "days": 9, "text": "Move the backups to cold storage."}]
        self.assertEqual(ids(sky.build({"quests": quests, "today": {"ledger": ledger}}, NOW)), ["quest:q-2"])


class Text(unittest.TestCase):
    def test_every_contact_has_the_contract_keys_and_lengths(self):
        long = "word " * 80
        world = {"chars": areas() + [
            {"kind": "seat", "uid": "seat:a", "name": long, "status": "WAITING", "line": long},
            t3ship("t", "waiting"),
            {"kind": "cron", "uid": "cron:1", "name": "c", "verdict": "NEVER", "receipt": long}],
            "agent_tasks": [{"id": "t", "status": "review", "title": long}],
            "tasks": {"items": [{"id": "b", "finding": long, "who": "owner"}]},
            "quests": [{"id": "q-1", "days": 3, "text": "parked " * 40}],
            "today": {"needs": [{"id": "T-20261001-0101-a", "title": long, "note": long}],
                      "ledger": [{"id": "L-1", "title": long, "opened": day_ago(3)}],
                      "night": {"date": day_ago(1), "items": [{"id": "n", "title": long, "verdict": "ready"}]}},
            "workflows": [{"id": "w-20261001-0101-a-a1b2c3", "title": long, "state": "needs_you", "updated": day_ago(1),
                           "steps": [{"key": "ok", "label": long, "kind": "gate", "state": "needs_you",
                                      "planet_uid": None}]}]}
        out = sky.build(world, NOW)
        self.assertEqual(set(out["by_source"]), set(sky.SOURCES))
        for c in out["contacts"]:
            self.assertEqual(set(c), KEYS, c)
            self.assertIn(c["source"], sky.SOURCES)
            self.assertIn(c["state"], sky.STATES)
            self.assertIn(c["tier"], sky.TIERS)
            self.assertTrue(c["id"].startswith(c["source"] + ":"), c["id"])
            self.assertLessEqual(len(c["title"]), 120)
            self.assertLessEqual(len(c["voice"]), 80)
            self.assertNotIn("\n", c["title"] + c["voice"])
            self.assertTrue(c["open"] is None or set(c["open"]) in ({"uid"}, {"url"}), c["open"])
        json.dumps(out)
        self.assertTrue(out["contacts"][0]["title"].endswith("…"))


class Malformed(unittest.TestCase):
    def test_values_no_table_can_hold_are_skipped(self):
        chars = [{"kind": "seat", "uid": "seat:a", "status": []}, {"kind": "seat", "uid": "seat:b", "status": {}},
                 {"kind": "cron", "uid": "cron:1", "verdict": ["STALE"]},
                 {"kind": "ship", "source": "t3", "uid": "ship:t3:x", "status": ["WAITING"], "app": []},
                 {"kind": "ship", "source": "t3", "uid": "ship:t3:y", "t3": {"state": "waiting", "app": {"a": 1}}},
                 {"kind": "runner", "status": ["DEAD"]}]
        tasks = [{"id": "t", "status": ["review"]}]
        night = {"date": day_ago(1), "items": [{"id": "n", "verdict": ["ready"]}]}
        out = sky.build({"chars": chars, "agent_tasks": tasks, "today": {"night": night}}, NOW)
        self.assertEqual(ids(out), ["t3:y"])

    def test_numbers_too_big_for_a_float_or_a_date(self):
        huge = 10 ** 400
        world = {"chars": [{"kind": "seat", "uid": "seat:a", "status": "WAITING", "window_activity": huge},
                           {"kind": "cron", "uid": "cron:1", "verdict": "STALE", "receipt_age": huge, "fired_age": 1e308}],
                 "agent_tasks": [{"id": "t", "status": "review", "ended": huge, "created": float("inf")}],
                 "quests": [{"id": "q-1", "days": 1_000_000_000, "text": "a parked line"},
                            {"id": "q-2", "days": huge, "text": "another parked line"}],
                 "today": {"ledger": [{"id": "L-1", "title": "decision", "opened": huge},
                                      {"id": "L-2", "title": "other", "opened": None, "age_days": huge}],
                           "night": {"date": "02-30", "ready": huge, "items": []}}}
        out = sky.build(world, NOW)
        # 7: the night's "ready" count of 10**400 is no count, so the night has no contact
        self.assertEqual(len(out["contacts"]), 7)
        self.assertNotIn("night", out["by_source"])
        for c in out["contacts"]:
            self.assertIn(c["tier"], sky.TIERS)
            self.assertTrue(c["age_days"] is None or 0 <= c["age_days"] < 10 ** 6, c)
        json.dumps(out)
        self.assertEqual(sky.build({}, huge)["contacts"], [])
        self.assertEqual(sky.build({}, float("nan"))["contacts"], [])

    def test_an_int_too_long_to_print_is_no_text(self):
        big = 10 ** 5000                         # str() refuses past 4300 digits by default
        world = {"agent_tasks": [{"id": "t", "status": "review", "title": big}, {"id": big, "status": "review"}],
                 "tasks": {"items": [{"id": big, "finding": "x"}]},
                 "today": {"night": {"date": big, "failed": big, "ready": 1, "items": []},
                           "ledger": [{"id": big, "title": "x"}]}}
        out = sky.build(world, NOW)
        self.assertEqual([(c["id"], c["title"]) for c in out["contacts"]],
                         [("task:t", "a task"), ("night:night", "1 ready")])   # no words of its own: "a task"

    def test_a_now_no_clock_can_show_is_the_current_time(self):
        for now in (1e308, -5.0, 0, 10 ** 400):
            self.assertEqual(sky.build({"agent_tasks": [{"id": "t", "status": "review"}]}, now)["by_source"],
                             {"task": 1}, now)

    def test_a_long_id_with_no_name_still_fits_the_title(self):
        seat = {"kind": "seat", "uid": "seat:" + "x" * 300, "name": " ", "status": "WAITING"}
        c = sky.build({"chars": [seat]}, NOW)["contacts"][0]
        self.assertLessEqual(len(c["title"]), 120)
        self.assertLessEqual(len(c["voice"]), 80)

    def test_a_done_row_without_an_id_is_not_a_settled_one(self):
        today = {"done": [{}, {"id": None}, {"id": ["T-1"]}, {"id": "T-2", "title": "b", "at": "10:00"}]}
        self.assertEqual(sky.build({"today": today}, NOW)["settled_today"], 1)

    def test_random_hostile_worlds_never_raise(self):
        import random
        rnd = random.Random(20261011)
        pool = [None, True, False, 0, -1, 7, 10 ** 400, 10 ** 5000, "批准预算", 1e308, float("nan"), float("inf"), "", " ", "x", "…",
                "2026-13-45", "2026-10-01", "10-10", "02-30", "T-2026", "T-20261011-2599-x", iso(DAY), [], {}, [None],
                {"a": 1}, ["x"], "WAITING", "DRAFT", "WORKING", "STALE", "NEVER", "NO RECEIPT", "review", "failed",
                "waiting", "ready", "settled", "snoozed", "working", "refused", "DEAD", "T3", "T3 (v2)", "/ws/api",
                "area:api", "ship:t3:a", "ship:t3:", "a" * 500]
        keys = ["kind", "uid", "name", "status", "source", "cwd", "line", "pane", "since", "window_activity", "activity",
                "t3", "app", "docked_at", "updated_at", "verdict", "receipt_age", "fired_age", "receipt", "area",
                "remote", "tool", "path", "alias_of"]
        kinds = ["seat", "ship", "cron", "runner", "area", None, []]
        t3keys = ["state", "since", "settled_at", "parent", "app", "open_url", "thread_id"]

        def val():
            v = rnd.choice(pool)
            return dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v

        def row(keyset, extra=None):
            r = {k: val() for k in rnd.sample(keyset, rnd.randint(0, len(keyset)))}
            r.update(extra or {})
            return r if rnd.random() > 0.05 else rnd.choice(pool)

        for _ in range(400):
            chars = []
            for _ in range(rnd.randint(0, 12)):
                c = row(keys, {"kind": rnd.choice(kinds)})
                if isinstance(c, dict) and rnd.random() < 0.5:
                    c["t3"] = row(t3keys) if rnd.random() < 0.8 else val()
                    c["source"], c["kind"] = "t3", "ship"
                    c["uid"] = "ship:t3:" + rnd.choice("abc")
                    if isinstance(c["t3"], dict) and rnd.random() < 0.5:
                        c["t3"]["parent"] = rnd.choice("abc")
                chars.append(c)
            task_keys = ["id", "status", "title", "text", "target", "created", "started", "ended", "retry_due",
                         "check", "last_verdict"]
            today = {"needs": [row(["id", "title", "note", "at"]) for _ in range(rnd.randint(0, 3))],
                     "done": [row(["id", "title", "at"]) for _ in range(rnd.randint(0, 3))],
                     "ledger": [row(["id", "title", "opened", "age_days", "blocks", "lane"]) for _ in range(rnd.randint(0, 4))],
                     "night": row(["date", "ran", "ready", "failed", "items", "line", "source"])}
            if isinstance(today["night"], dict) and rnd.random() < 0.6:
                today["night"]["items"] = [row(["id", "title", "repo", "verdict", "minutes"]) for _ in range(rnd.randint(0, 3))]
            world = {"chars": chars if rnd.random() > 0.05 else val(),
                     "agent_tasks": [row(task_keys) for _ in range(rnd.randint(0, 5))],
                     "tasks": {"generated": val(), "items": [row(["id", "who", "area", "path", "finding", "opened", "at"])
                                                             for _ in range(rnd.randint(0, 4))]} if rnd.random() > 0.1 else val(),
                     "quests": [row(["id", "days", "text", "src"]) for _ in range(rnd.randint(0, 4))],
                     "today": today if rnd.random() > 0.05 else val()}
            out = sky.build(world, rnd.choice([NOW, None, NOW + 0.5, 1e308, -5.0]))
            for c in out["contacts"]:
                self.assertEqual(set(c), KEYS)
                self.assertLessEqual(len(c["title"]), 120)
                self.assertLessEqual(len(c["voice"]), 80)
            json.dumps(out)

    def test_rows_that_are_not_the_shape_their_source_writes_are_skipped(self):
        nan = float("nan")
        world = {
            "chars": [None, 3, "seat", {}, {"kind": "seat", "status": "WAITING"},                  # no uid
                      {"kind": "seat", "uid": "seat:ok", "status": "WAITING", "since": "garbage", "cwd": 5},
                      {"kind": "ship", "source": "t3", "uid": "ship:t3:", "status": "WAITING"},   # no thread id
                      {"kind": "ship", "source": "t3", "uid": "ship:t3:z", "t3": {"state": 5}},
                      {"kind": "ship", "source": "t3", "uid": "ship:t3:y", "t3": "x", "status": "WAITING"},
                      {"kind": "cron", "uid": "cron:1", "verdict": "STALE", "receipt_age": "x", "fired_age": nan},
                      {"kind": "cron", "verdict": "NEVER"},
                      {"kind": "area", "uid": 7, "path": None}, {"kind": "runner", "status": None}],
            "agent_tasks": [None, {"id": None, "status": "review"}, {"id": "t", "status": 4},
                            {"id": "ok", "status": "review", "ended": "yesterday", "check": "PASS"}],
            "tasks": {"items": "nope"},
            "quests": [{"text": None}, {"id": 5, "days": "x", "text": "a parked line"}, "q"],
            "today": {"needs": [None, {"id": 7}, {"title": "no id"}],
                      "ledger": [{"id": {"x": 1}, "title": "t"}, {"id": "L-1", "title": None},
                                 {"id": "L-2", "title": "kept", "opened": "2026-13-45", "age_days": True}],
                      "night": {"date": 7, "items": [None, {"verdict": None}, {"verdict": "ready"}]},
                      "done": "x"}}
        out = sky.build(world, NOW)
        got = by_id(out)
        self.assertEqual(sorted(got), ["cron:1", "ledger:L-2", "night:7:1", "quest:q-" + sky._title_hash("a parked line")[:10],
                                       "seat:ok", "t3:y", "task:ok"])
        self.assertIsNone(got["cron:1"]["since"])
        self.assertIsNone(got["ledger:L-2"]["age_days"])
        self.assertIsNone(got["task:ok"]["since"])
        self.assertEqual(got["task:ok"]["voice"], "a task")
        json.dumps(out)

    def test_other_containers_in_the_wrong_type(self):
        for world in ({"chars": "x", "agent_tasks": {}, "tasks": [], "quests": 3, "today": "x"},
                      {"today": {"ledger": "x", "needs": {}, "night": "x", "done": None}}):
            self.assertEqual(sky.build(world, NOW), sky.empty())


class Performance(unittest.TestCase):
    def test_a_two_thousand_unit_world_builds_in_under_200_ms(self):
        chars = [{"uid": f"area:a{i}", "kind": "area", "name": f"a{i}", "path": f"/ws/a{i}", "alias_of": ""}
                 for i in range(200)]
        chars += [{"kind": "cron", "uid": f"cron:{i}", "name": f"c{i}", "area": f"area:a{i % 150}",
                   "verdict": ("STALE", "RUNNING", "NEVER", "NO RECEIPT")[i % 4], "receipt_age": i * 600,
                   "receipt": "log stale"} for i in range(600)]
        chars += [{"kind": "seat", "uid": f"seat:{i}", "name": f"s{i}", "cwd": f"/ws/a{i % 150}/deep/er/still",
                   "status": ("WAITING", "IDLE", "WORKING", "DRAFT")[i % 4], "line": "a line"} for i in range(300)]
        chars += [t3ship(f"t{i}", ("waiting", "working", "ready", "settled", "snoozed", "failed")[i % 6],
                         parent=f"t{i - 1}" if i % 10 == 9 else None) for i in range(400)]
        chars += [{"kind": "runner", "name": f"r{i}", "status": ("LIVE", "DEAD")[i % 2]} for i in range(50)]
        tasks = [{"id": f"k{i}", "status": ("review", "running", "failed", "merged")[i % 4], "title": "t",
                  "target": f"/ws/a{i % 150}/src", "ended": iso(i * 900)} for i in range(300)]
        board = {"generated": iso(DAY), "items": [{"id": f"b{i}", "area": f"a{i % 150}", "finding": "f",
                                                   "who": ("owner", "agent")[i % 2]} for i in range(150)]}
        quests = [{"id": f"q-{i}", "days": i, "text": f"parked line number {i} awaiting owner"} for i in range(16)]
        ledger = [{"id": f"L-{i}", "title": f"decision row {i} about something", "opened": day_ago(i)} for i in range(13)]
        world = {"chars": chars, "agent_tasks": tasks, "tasks": board, "quests": quests,
                 "today": {"ledger": ledger, "needs": [{"id": "T-20261009-0100-a", "title": "x"}] * 3,
                           "night": {"date": day_ago(1), "ready": 2, "failed": 0, "items": [], "line": "2 ready"}}}
        units = len(chars) + len(tasks) + len(board["items"]) + len(quests) + len(ledger)
        self.assertGreaterEqual(units, 2000)
        sky.build(world, NOW)
        best = min(self._timed(world) for _ in range(3))
        self.assertLess(best, 0.2, f"sky.build took {best * 1000:.0f} ms on {units} units")

    def test_a_two_thousand_deep_line_of_subagents_builds_in_under_200_ms(self):
        chars = [t3ship(str(i), "waiting", parent=str(i - 1) if i else None) for i in range(2000)]
        sky.build({"chars": chars}, NOW)
        start = time.perf_counter()
        out = sky.build({"chars": chars}, NOW)
        took = time.perf_counter() - start
        self.assertEqual(ids(out), ["t3:0"])
        self.assertLess(took, 0.2, f"{took * 1000:.0f} ms")

    @staticmethod
    def _timed(world):
        start = time.perf_counter()
        out = sky.build(world, NOW)
        took = time.perf_counter() - start
        assert out["contacts"]
        return took


if __name__ == "__main__":
    unittest.main()
