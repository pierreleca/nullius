"""The pages of task sets: a benchmark's "Task sets" section, the page of a dataset's tasks (its listing a page at a
time, its state before a first listing, saving the ticked ids as a set or as a new revision), and the job page of a
run of a set (the set named, "Save as task set" for the rows its outcome filter shows). The listings are made by a
driver of the test's own that describes 250 tasks; no key, no network."""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import unittest
from contextlib import ExitStack
from html import unescape
from pathlib import Path
from unittest.mock import patch

from nullius import workspace
from nullius.standard import definitions, install, job, jsonl, sets, units
from tests.test_standard_units_listing import DRIVER
from .test_routes import web

FIXTURES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests", "fixtures", "standard", "catalogue")
_GET = definitions.get


def visible(html: str) -> str:
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", html)).split())


class Case(unittest.TestCase):
    """A profile and a catalogue of the test's own; ``described``, a benchmark of 250 tasks whose driver says what
    each is about (kind alpha, beta or gamma; size small or large), and the fixture's fake bench."""

    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.tmp = Path(tempfile.mkdtemp(prefix="nullius-set-pages-")); self.addCleanup(shutil.rmtree, self.tmp, True)
        here = self.tmp / "described"
        here.mkdir()
        (here / "driver.py").write_text(DRIVER)
        self.started = here / "started"
        fake = _GET("benchmark", "fakebench", FIXTURES)
        self.b = dict(fake, key="described", label="Described bench", _here=str(here), datasets=[{"id": "all", "units": 250}],
                      driver={"command": [sys.executable, "{here}/driver.py"], "describe": True})
        self.fake = fake
        self.stack.enter_context(patch.dict(os.environ, {"NULLIUS_CATALOGUE": str(self.tmp / "catalogue"),
                                                        "NULLIUS_HOME": str(self.tmp / "profile")}))
        self.stack.enter_context(patch.object(definitions, "get", self.get_def))
        self.stack.enter_context(patch.dict(web.app.config, TESTING=True))
        self.client = web.app.test_client()
        self.csrf = self.token()

    def get_def(self, kind, key, root=None):
        if kind == "benchmark" and key == "described":
            return self.b
        return _GET(kind, key, FIXTURES if key in ("fakebench", "fakememo") else root)

    def token(self):
        r = self.client.get("/new-run"); self.addCleanup(r.close)
        return json.loads(re.search(r'<script id="nb-schema" type="application/json">(.*?)</script>', r.get_data(as_text=True), re.S).group(1))["csrf"]

    def get(self, url, status=200):
        r = self.client.get(url); self.addCleanup(r.close)
        self.assertEqual(r.status_code, status, url)
        return r

    def post(self, url, body, status=200):
        r = self.client.post(url, data=json.dumps(body), content_type="application/json",
                             headers={"Origin": "http://localhost", "X-CSRF-Token": self.csrf}); self.addCleanup(r.close)
        self.assertEqual(r.status_code, status, (url, r.get_data(as_text=True)))
        return r.get_json()

    def listing(self, **args):
        q = "&".join(f"{k}={v}" for k, v in args.items() if k != "cat") + "".join(f"&cat={c}" for c in args.get("cat", []))
        return self.get(f"/benchmarks/described/tasks/all/listing?{q}").get_json()

    def starts(self) -> int:
        return len(self.started.read_text().splitlines()) if self.started.exists() else 0


class TheTasksPagesData(Case):
    def test_each_search_category_and_page_gives_what_the_listings_function_gives(self):
        units.listing(self.b, "all")                         # in the cache: the page reads it
        before = self.starts()
        for args, search, cats, page in (({"q": "quokka"}, "quokka", {}, 1),
                                         ({"cat": ["kind=alpha"]}, "", {"kind": ["alpha"]}, 1),
                                         ({"q": "number", "cat": ["kind=beta", "size=small"]}, "number", {"kind": ["beta"], "size": ["small"]}, 1),
                                         ({"page": 2}, "", {}, 2),
                                         ({"q": "wombat"}, "wombat", {}, 1)):
            with self.subTest(args=args):
                got = self.listing(**args)
                self.assertTrue(got.pop("ok")); self.assertTrue(got.pop("ready"))
                self.assertEqual(got, units.find(self.b, "all", search=search, categories=cats, page=page))
        self.assertEqual(self.listing(q="wombat")["total"], 0)
        self.assertEqual(len(self.listing(page=2)["tasks"]), 100)
        self.assertEqual(self.listing(ids=1, cat=["kind=alpha"])["ids"], [t["id"] for t in units.find(self.b, "all", categories={"kind": ["alpha"]}, size=None)["tasks"]])
        self.assertEqual(self.starts(), before)             # every answer from the cache: no driver started

    def test_an_unknown_category_is_refused_plainly(self):
        units.listing(self.b, "all")
        r = self.client.get("/benchmarks/described/tasks/all/listing?cat=colour=red"); self.addCleanup(r.close)
        self.assertEqual(r.status_code, 400)
        self.assertIn("no such category", r.get_json()["error"])

    def test_the_rows_of_chosen_ids_say_which_the_listing_does_not_hold(self):
        units.listing(self.b, "all")
        got = self.post("/benchmarks/described/tasks/all/rows", {"units": ["t007", "nowhere"]})
        self.assertEqual([t["title"] for t in got["tasks"]], ["Open the spreadsheet"])
        self.assertEqual(got["unknown"], ["nowhere"])


class AFirstListing(Case):
    """Each answer of the state function: in the cache, shown at once; light, made out of the request with what it
    fetches said; full, never started, with what it needs said; refused, why. No request starts a fetch or an
    install inside itself."""

    def no_fetch_no_install(self):
        def refuse(*a, **k):
            raise AssertionError("a request fetched, installed or started a driver")
        for target, name in ((install, "upstream"), (install, "dataset"), (install, "driver_argv"), (jsonl.Proc, "start")):
            self.stack.enter_context(patch.object(target, name, refuse))

    def test_in_the_cache_the_page_is_shown_at_once(self):
        units.listing(self.b, "all")
        self.no_fetch_no_install()
        got = self.listing()
        self.assertTrue(got["ready"]); self.assertEqual((got["total"], got["of"]), (250, 250))

    def test_a_light_listing_is_made_out_of_the_request_and_says_what_it_fetches(self):
        self.b["upstream"] = {"repository": "https://example.org/upstream.git", "tested": "a" * 40}
        self.b["driver"] = dict(self.b["driver"], list_command=[sys.executable, "{here}/driver.py"])
        self.stack.enter_context(patch.object(units.shutil, "which", lambda w: "/usr/bin/" + w))
        self.no_fetch_no_install()
        html = self.get("/benchmarks/described/tasks/all").get_data(as_text=True)
        self.assertIn('id="tasks-state"', html)
        got = self.listing()
        self.assertEqual((got["ready"], got["state"], got["preparing"]), (False, "light", False))
        self.assertEqual(got["say"], "Preparing the list of tasks: fetching the upstream https://example.org/upstream.git at aaaaaaaaaaaa. "
                                     "Ids can be pasted meanwhile.")
        # the page's prepare starts it in a thread of its own and answers at once, while it is still being made
        release, made = threading.Event(), []
        def slow(bench, dataset, options=None):
            release.wait(10); made.append(threading.current_thread().name)
        self.stack.enter_context(patch.object(units, "listing", slow))
        t0 = time.monotonic()
        got = self.post("/benchmarks/described/tasks/all/prepare", {})
        self.assertLess(time.monotonic() - t0, 5)
        self.assertTrue(got["preparing"])
        self.assertTrue(self.listing()["preparing"])
        self.post("/benchmarks/described/tasks/all/prepare", {})          # asked twice, made once
        release.set()
        for _ in range(100):
            if made:
                break
            time.sleep(0.02)
        self.assertEqual(len(made), 1)
        self.assertNotEqual(made[0], threading.current_thread().name)
        self.assertEqual(self.starts(), 0)

    def test_a_listing_that_needs_the_full_environment_is_never_started_and_says_what_it_needs(self):
        self.stack.enter_context(patch.object(units, "state", lambda b, d, o=None: {
            "state": "full", "fetch": [{"what": "environment", "text": "the driver's environment, installed with uv"}], "reason": ""}))
        self.no_fetch_no_install()
        called = []
        self.stack.enter_context(patch.object(units, "listing", lambda *a, **k: called.append(a)))
        got = self.listing()
        self.assertFalse(got["ready"])
        self.assertEqual(got["say"], "The list of tasks needs the benchmark's own environment on this machine (the driver's environment, "
                                     "installed with uv): run the dataset once, or list it with the units command, then reload. "
                                     "Ids can still be pasted.")
        self.assertFalse(self.post("/benchmarks/described/tasks/all/prepare", {})["preparing"])
        rows = self.post("/benchmarks/described/tasks/all/rows", {"units": ["t001"]})
        self.assertEqual((rows["ready"], [t["id"] for t in rows["tasks"]]), (False, ["t001"]))        # pasted ids, kept as they are
        self.assertEqual(called, [])

    def test_a_refused_listing_says_why(self):
        self.stack.enter_context(patch.object(units, "state", lambda b, d, o=None: {"state": "refused", "fetch": [], "reason": "git is not on this machine"}))
        self.no_fetch_no_install()
        got = self.listing()
        self.assertEqual(got["say"], "The list of tasks cannot be made here: git is not on this machine. Ids can still be pasted.")


class Saving(Case):
    def save(self, body, status=200):
        return self.post("/benchmarks/described/tasks/all/save", body, status)

    def test_the_chosen_ids_become_a_set(self):
        got = self.save({"name": "dev", "description": "four of them", "units": ["t001", "t002", "t003", "t004"]})
        self.assertEqual((got["revision"], got["open"]), (1, "/benchmarks/described/tasks/all?set=dev&rev=1"))
        entry = sets.load("described", "dev")
        self.assertEqual((entry["dataset"], entry["description"], sets.revision(entry)["units"]), ("all", "four of them", ["t001", "t002", "t003", "t004"]))

    def test_a_name_that_exists_an_empty_name_and_a_slash_are_refused_with_a_plain_message(self):
        self.save({"name": "dev", "units": ["t001"]})
        for name, said in (("dev", "A task set named dev exists already: choose another name."),
                           ("", "Give the task set a name."),
                           ("a/b", "A task set's name holds no slash: letters, digits, '.', '_' and '-'.")):
            with self.subTest(name=name):
                self.assertEqual(self.save({"name": name, "units": ["t002"]}, 400), {"ok": False, "error": said})
        self.assertEqual(sets.revision(sets.load("described", "dev"))["units"], ["t001"])
        self.assertEqual(sorted(os.listdir(self.tmp / "catalogue" / "sets" / "described")), ["dev.json"])

    def test_from_a_set_a_changed_membership_adds_a_revision_and_an_unchanged_one_none(self):
        self.save({"name": "dev", "units": ["t001", "t002"]})
        html = self.get("/benchmarks/described/tasks/all?set=dev").get_data(as_text=True)
        self.assertEqual(json.loads(re.search(r'<script id="tasks-chosen-ids" type="application/json">(.*?)</script>', html).group(1)), ["t001", "t002"])
        self.assertIn("Save as a new revision", html)
        same = self.save({"set": "dev", "note": "", "units": ["t002", "t001"]})
        self.assertEqual((same["added"], same["revision"]), (False, 1))
        more = self.save({"set": "dev", "note": "one more", "units": ["t001", "t002", "t003"]})
        self.assertEqual((more["added"], more["revision"]), (True, 2))
        entry = sets.load("described", "dev")
        self.assertEqual([(r["revision"], r["note"]) for r in entry["revisions"]], [(1, ""), (2, "one more")])
        # the earlier revision opens read only: its members ticked, nothing to save
        html = self.get("/benchmarks/described/tasks/all?set=dev&rev=1").get_data(as_text=True)
        self.assertIn('data-writable="false"', html)
        self.assertIn("Revision 1 is read only.", visible(html))
        self.assertNotIn('id="tasks-revise"', html)

    def test_saving_needs_the_form_token_and_a_tick(self):
        r = self.client.post("/benchmarks/described/tasks/all/save", data=json.dumps({"name": "x", "units": ["t001"]}),
                             content_type="application/json"); self.addCleanup(r.close)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.save({"name": "x", "units": []}, 400)["error"], "Tick at least one task.")


class TheBenchmarksSection(Case):
    def section(self, key):
        html = self.get(f"/benchmarks/{key}").get_data(as_text=True)
        got = re.search(r'<section class="bench-section" id="task-sets".*?</section>', html, re.S)
        self.assertIsNotNone(got)
        return got.group(0)

    def test_a_benchmark_with_no_set_shows_each_datasets_link(self):
        osw = _GET("benchmark", "osworld1")
        part = self.section("osworld1")
        for d in osw["datasets"]:
            self.assertIn(f'href="/benchmarks/osworld1/tasks/{d["id"]}"', part)
        self.assertNotIn("<table", part)

    def test_it_lists_the_benchmarks_sets_and_each_datasets_link(self):
        osw = _GET("benchmark", "osworld1")
        sets.create(osw, "chrome-tabs", dataset="test_nogdrive", units=["a", "b", "c"], description="three of them")
        sets.update("osworld1", "chrome-tabs", add=["d"])
        text = visible(self.section("osworld1"))
        self.assertIn("Task sets 1", text)
        label = next(d.get("label") or d["id"] for d in osw["datasets"] if d["id"] == "test_nogdrive")
        self.assertRegex(text, re.escape(f"chrome-tabs three of them {label} 4 2 ") + r"\d{4}-\d\d-\d\d")
        self.assertIn('href="/benchmarks/osworld1/tasks/test_nogdrive?set=chrome-tabs"', self.section("osworld1"))


class BuilderCase(Case):
    """The fixture catalogue's benchmarks in the builder; a set of the fake bench's dataset."""

    def setUp(self):
        super().setUp()
        load = definitions.load
        self.stack.enter_context(patch.object(definitions, "load", lambda kind, root=None, every=False: load(kind, FIXTURES, every=every)))
        sets.create(self.fake, "dev", dataset="all", units=["good", "wrong"], description="two of them")


class TheBuilder(BuilderCase):
    """The dataset step offers each dataset's sets; the Review step, compiled by the server, names the set and its
    number of tasks."""

    def test_the_schema_offers_the_sets_and_review_names_the_chosen_one(self):
        html = self.get("/new-run").get_data(as_text=True)
        schema = json.loads(re.search(r'<script id="nb-schema" type="application/json">(.*?)</script>', html, re.S).group(1))
        fake = next(b for b in schema["standard"]["benchmarks"] if b["key"] == "fakebench")
        self.assertEqual([(s["name"], s["units"], s["revision"]) for s in fake["datasets"][0]["sets"]], [("dev", 2, 1)])
        recipe = {"version": 1, "harness": "standard", "name": "fake-0101-120000", "title": "t", "benchmark": "fakebench", "dataset": "all",
                  "other": "fake", "provider": "openai", "model": "gpt-5-mini", "concurrency": 1, "unsandboxed": True, "where": "local",
                  "task_sets": {"all": "dev@1"}}
        out = self.post("/new-run/runner", {"recipe": recipe})
        self.assertEqual(out["errors"], [])
        self.assertIn(["Dataset", "all · task set dev, revision 1 · 2 tasks"], [r[1:] for r in out["review"]])
        self.assertTrue(out["confirm"].startswith("Run all 2 tasks of Fake bench"))


class TheRecipeCore(BuilderCase):
    """The builder's core keeps a chosen dataset's set, NAME@REV, in the recipe: read back from a saved recipe, dropped
    with its dataset, and dropped when the benchmark changes."""

    def test_task_sets_follow_the_chosen_datasets(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node is required for the recipe core")
        import subprocess
        html = self.get("/new-run").get_data(as_text=True)
        schema = json.loads(re.search(r'<script id="nb-schema" type="application/json">(.*?)</script>', html, re.S).group(1))
        core = Path(__file__).resolve().parents[1] / "static" / "builder" / "command-builder-core.js"
        program = (f"const C=require({json.dumps(str(core))})({json.dumps(schema)});\n"
                   "const r=C.fromJSON({version:1,harness:'standard',benchmark:'fakebench',datasets:['all'],others:['fake'],"
                   "task_sets:{all:'dev@1',gone:'x@1',bad:3}});\n"
                   "const read=JSON.parse(JSON.stringify(r.task_sets));\n"
                   "C.setTaskSet(r,'all','');const cleared=r.task_sets===undefined;\n"
                   "C.setTaskSet(r,'all','dev@1');r.datasets=[];C.keepTaskSets(r);const dropped=r.task_sets===undefined;\n"
                   "r.datasets=['all'];C.setTaskSet(r,'all','dev@1');C.setStandardBenchmark(r,'fakememo');\n"
                   "process.stdout.write(JSON.stringify({read,cleared,dropped,other:r.task_sets===undefined}));")
        got = json.loads(subprocess.run([node, "-"], input=program, check=True, capture_output=True, text=True).stdout)
        self.assertEqual(got, {"read": {"all": "dev@1"}, "cleared": True, "dropped": True, "other": True})


class TheJobPage(Case):
    """Jobs of the fake bench, one that ran a set and one that ran none."""

    def setUp(self):
        super().setUp()
        bench = self.fake
        self.entry = sets.create(bench, "dev", dataset="all", units=["good", "wrong", "slow"])
        record = sets.record(self.entry, sets.revision(self.entry))
        root = self.tmp / "jobs"
        other = {"key": "nop", "_kind": "control", "_ref": "nop"}
        for name, ts, results in (("of-a-set", record, {"good": 1.0, "wrong": 0.0, "slow": 1.0}), ("whole", None, {"good": 1.0})):
            d = root / name
            d.mkdir(parents=True)
            job.write_config(str(d), job=name, bench=bench, other=other, model="", provider="openai", dataset="all",
                             units=list(results), concurrency=1, granted=False, task_set=ts)
            rows = []
            for k, (u, r) in enumerate(results.items()):
                trial = d / f"{u}__u{k:04d}"
                trial.mkdir()
                rows.append(job.write_trial(str(trial), job=name, bench=bench, other=other, model="", unit=u,
                                            t0=1_760_000_000.0 + k, t1=1_760_000_005.0 + k, produce=(1_760_000_000.0 + k, 1_760_000_004.0 + k),
                                            score={"reward": r}, exception=None, calls=[], trajectory=None))
            job.write_result(str(d), name, 1_760_000_000.0, 1_760_000_060.0, rows, len(results))
        self.stack.enter_context(patch.object(web.shared, "WS_HOME", str(self.tmp / "ws")))
        self.stack.enter_context(patch.dict(web.shared.STATE, {"workspace": None, "roots": [], "label": "", "job_roots": []}))
        web.shared.open_workspace(workspace.create(str(self.tmp / "ws"), [str(root)], label="sets"))

    def page(self, name):
        return self.get(f"/job/{name}?view=results").get_data(as_text=True)

    def meta(self, html):
        return visible(re.search(r'<div class="run-meta".*?</div>', html, re.S).group(0)).replace(" ,", ",")

    def test_the_page_names_the_set_with_a_link_and_a_job_of_no_set_nothing_more(self):
        html = self.page("of-a-set")
        self.assertIn("task set dev, revision 1", self.meta(html))
        self.assertIn('href="/benchmarks/fakebench/tasks/all?set=dev&amp;rev=1"', html)
        self.assertNotIn("task set", self.meta(self.page("whole")))

    def test_a_set_not_in_the_catalogue_is_named_without_a_link(self):
        os.remove(sets.path_of("fakebench", "dev"))
        html = self.page("of-a-set")
        self.assertIn("task set dev, revision 1", self.meta(html))
        self.assertNotIn("/tasks/all?set=dev", html)

    def test_save_as_task_set_stores_the_full_unit_ids_of_the_rows_shown(self):
        self.assertIn("Save the tasks shown", self.page("of-a-set"))
        r = self.client.post("/job/of-a-set/task-set", data={"csrf": self.csrf, "name": "solved", "outcome": "resolved"},
                             headers={"Origin": "http://localhost"}); self.addCleanup(r.close)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers["Location"], "/benchmarks/fakebench/tasks/all?set=solved&rev=1")
        entry = sets.load("fakebench", "solved")
        self.assertEqual(sets.revision(entry)["units"], ["good", "slow"])
        r = self.client.post("/job/whole/task-set", data={"csrf": self.csrf, "name": "every"},
                             headers={"Origin": "http://localhost"}); self.addCleanup(r.close)
        self.assertEqual(sets.revision(sets.load("fakebench", "every"))["units"], ["good"])
        r = self.client.post("/job/whole/task-set", data={"csrf": self.csrf, "name": "every"},
                             headers={"Origin": "http://localhost"}); self.addCleanup(r.close)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(sets.revision(sets.load("fakebench", "every"))["units"], ["good"])        # a name taken: nothing written


if __name__ == "__main__":
    unittest.main()
