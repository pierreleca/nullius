"""The pages of task sets: a benchmark's "Task sets" section, the page of a dataset's tasks (its listing a page at a
time, its state before a first listing, saving the ticked ids as a set or as a new revision), and the job page of a
run of a set (the set named, "Save as task set" for the rows its outcome filter shows). The listings are made by a
driver of the test's own that describes 250 tasks; no key, no network."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
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
from nullius.standard import definitions, install, job, jsonl, recipe, sets, units
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


class TheTaskFilters(unittest.TestCase):
    def filtered(self, action):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node is required for the task filters")
        script = Path(__file__).resolve().parents[1] / "static/catalogue/tasks.js"
        program = r"""
const assert = require('node:assert/strict'), elements = new Map();
function element(id) {
  if (!elements.has(id)) elements.set(id, {
    dataset: {}, textContent: '', innerHTML: '', listeners: {}, attributes: {},
    classList: {toggle() {}},
    addEventListener(event, callback) { this.listeners[event] = callback; },
    setAttribute(key, value) { this.attributes[key] = value; },
    querySelector(selector) { return element(id + selector); }
  });
  return elements.get(id);
}
element('tasks-page').dataset = {set: 'dev', writable: 'true', listing: '/listing', rows: '/rows'};
element('tasks-chosen-ids').textContent = '["alpha","beta"]';
const tasks = ['alpha', 'beta'].map(id => ({id, title: id, categories: {kind: id}}));
const requests = [];
async function request(url) {
  requests.push(url);
  const query = new URL(url, 'http://localhost').searchParams;
  const found = query.has('q') ? [] : query.has('cat') ? tasks.slice(1) : tasks;
  return {json: async () => url === '/rows' ? {tasks, unknown: []}
    : {ok: true, ready: true, total: found.length, of: 2, pages: 1, page: 1,
       categories: {}, described: true, tasks: found}};
}
require('node:vm').runInNewContext(require('node:fs').readFileSync(process.argv[1], 'utf8'), {
  document: {getElementById: element, querySelectorAll: () => [], querySelector: () => null},
  URLSearchParams, nulliusFetch: request, setTimeout: callback => { callback(); return 1; }, clearTimeout() {}
});
async function flush() { for (let n = 0; n < 20; n++) await Promise.resolve(); }
(async () => {
  await flush();
  assert.equal(element('tasks-show').attributes['aria-pressed'], 'true');
  assert.equal(element('tasks-show').textContent, 'Show all');
  const before = requests.length;
  if (process.argv[2] === 'search') {
    element('tasks-q').listeners.input({target: {value: 'absent'}});
  } else {
    element('tasks-cats').listeners.click({target: {closest: () => ({dataset: {field: 'kind', value: 'beta'}})}});
  }
  await flush();
  assert.equal(requests.slice(before).some(url => url === '/rows'), false);
  assert.equal(element('tasks-show').attributes['aria-pressed'], 'false');
  assert.equal(element('tasks-show').textContent, 'Show selected');
  assert.equal(element('tasks-chosen').textContent, '2 chosen');
  const rows = element('tasks-rows').innerHTML;
  if (process.argv[2] === 'search') assert.equal(rows, '');
  else { assert.ok(rows.includes('data-id="beta"')); assert.ok(!rows.includes('data-id="alpha"')); }
  element('tasks-show').listeners.click();
  await flush();
  assert.equal(element('tasks-show').attributes['aria-pressed'], 'true');
  assert.ok(element('tasks-rows').innerHTML.includes('data-id="alpha"'));
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
        got = subprocess.run([node, "-e", program, str(script), action], stdin=subprocess.DEVNULL,
                             capture_output=True, text=True, timeout=10)
        self.assertEqual(got.returncode, 0, got.stderr)

    def test_search_leaves_selected_mode_and_keeps_the_selection(self):
        self.filtered("search")

    def test_a_category_leaves_selected_mode_and_keeps_the_selection(self):
        self.filtered("category")


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

    def test_the_count_and_the_way_to_save_close_the_rows_and_a_read_only_revision_has_neither(self):
        def bar(html):
            rows = html.index('id="tasks-rows"')
            got = re.search(r'<div class="tasks-savebar" id="tasks-savebar">(.*?)</div>', html, re.S)
            return got and (got.start() > rows and got.start() < html.index('id="tasks-save-title"'), visible(got.group(1)))
        self.assertEqual(bar(self.get("/benchmarks/described/tasks/all").get_data(as_text=True)), (True, "0 chosen Save as task set"))
        self.save({"name": "dev", "units": ["t001"]})
        self.save({"set": "dev", "note": "", "units": ["t001", "t002"]})
        self.assertEqual(bar(self.get("/benchmarks/described/tasks/all?set=dev").get_data(as_text=True)), (True, "0 chosen Save"))
        self.assertIsNone(bar(self.get("/benchmarks/described/tasks/all?set=dev&rev=1").get_data(as_text=True)))


class RenameAndDelete(Case):
    """The set's page renames and deletes it, each asked in place, from a browser on this machine."""

    def setUp(self):
        super().setUp()
        self.entry = sets.create(self.b, "dev", dataset="all", units=["t001", "t002"], description="two of them")
        sets.update("described", "dev", add=["t003"])

    def form(self, action, data, csrf=True):
        r = self.client.post(f"/benchmarks/described/tasks/all/{action}", data=dict(data, **({"csrf": self.csrf} if csrf else {})),
                             headers={"Origin": "http://localhost"}); self.addCleanup(r.close)
        with self.client.session_transaction() as session:
            said = [m for _, m in session.pop("_flashes", [])]
        return r, said

    def test_the_sets_page_offers_both_and_the_delete_names_the_set_its_tasks_and_revisions(self):
        for query in ("?set=dev", "?set=dev&rev=1"):
            with self.subTest(query=query):
                html = self.get("/benchmarks/described/tasks/all" + query).get_data(as_text=True)
                ask = re.search(r'<details class="bench-delete" name="tasks-set-action">(.*?)</details>', html, re.S)
                self.assertEqual(visible(ask.group(1)).replace(" :", ":"), "Delete Delete the task set dev: 3 tasks, 2 revisions? Runs made from it stay. "
                                                        "Yes, delete Keep it")
                rename = re.search(r'<details class="bench-delete tasks-rename" name="tasks-set-action">(.*?)</details>', html, re.S)
                self.assertIn('action="/benchmarks/described/tasks/all/rename"', rename.group(1))
                self.assertIn('name="name" value="dev"', rename.group(1))
        self.assertNotIn("tasks-set-action", self.get("/benchmarks/described/tasks/all").get_data(as_text=True))

    def test_a_rename_keeps_the_id_and_the_revisions_and_opens_the_new_name(self):
        r, said = self.form("rename", {"set": "dev", "name": "misses"})
        self.assertEqual((r.status_code, r.headers["Location"]), (302, "/benchmarks/described/tasks/all?set=misses"))
        self.assertEqual(said, ["Renamed dev to misses. Runs made from it follow the new name."])
        got = sets.load("described", "misses")
        self.assertEqual((got["id"], got["description"], [r["revision"] for r in got["revisions"]]), (self.entry["id"], "two of them", [1, 2]))
        self.assertFalse(os.path.exists(sets.path_of("described", "dev")))
        self.assertIn("<h1>", self.get(r.headers["Location"]).get_data(as_text=True))
        # from an earlier revision, the same revision under the new name
        r, _ = self.form("rename", {"set": "misses", "name": "dev", "rev": "1"})
        self.assertEqual(r.headers["Location"], "/benchmarks/described/tasks/all?set=dev&rev=1")

    def test_a_name_taken_empty_or_with_a_slash_is_refused_as_at_creation(self):
        sets.create(self.b, "other", dataset="all", units=["t009"])
        for new, message in (("other", "A task set named other exists already: choose another name."),
                             ("", "Give the task set a name."),
                             ("a/b", "A task set's name holds no slash: letters, digits, '.', '_' and '-'."),
                             ("dev", "The task set is named dev already.")):
            with self.subTest(new=new):
                r, said = self.form("rename", {"set": "dev", "name": new})
                self.assertEqual((r.status_code, r.headers["Location"], said), (302, "/benchmarks/described/tasks/all?set=dev", [message]))
        self.assertEqual(sorted(os.listdir(self.tmp / "catalogue" / "sets" / "described")), ["dev.json", "other.json"])
        self.assertEqual(sets.load("described", "dev")["id"], self.entry["id"])

    def test_a_linked_destination_is_refused_on_the_page_without_a_path(self):
        before = sets.load("described", "dev")
        outside = self.tmp / "outside.json"
        outside.write_text("{}")
        alias = self.tmp / "catalogue" / "sets" / "described" / "alias.json"
        alias.symlink_to(outside)
        r = self.client.post("/benchmarks/described/tasks/all/rename",
                             data={"csrf": self.csrf, "set": "dev", "name": "alias", "rev": "1"},
                             headers={"Origin": "http://localhost"}, follow_redirects=True)
        self.addCleanup(r.close)
        self.assertEqual((r.status_code, r.request.query_string), (200, b"set=dev&rev=1"))
        html = r.get_data(as_text=True)
        self.assertIn("The task set dev cannot be read: nullius standard set show described dev says why.", visible(html))
        self.assertNotIn(str(self.tmp), html)
        self.assertEqual(sets.load("described", "dev"), before)
        self.assertTrue(alias.is_symlink())
        self.assertEqual(outside.read_text(), "{}")

    def test_a_delete_removes_the_set_and_opens_the_benchmarks_sets(self):
        r, said = self.form("delete", {"set": "dev"})
        self.assertEqual((r.status_code, r.headers["Location"]), (302, "/benchmarks/described#task-sets"))
        self.assertEqual(said, ["Deleted the task set dev. Runs made from it stay."])
        self.assertFalse(os.path.exists(sets.path_of("described", "dev")))
        r, said = self.form("delete", {"set": "dev"})
        self.assertEqual((r.headers["Location"], said), ("/benchmarks/described/tasks/all?set=dev", ["There is no task set dev."]))
        html = self.get("/benchmarks/described/tasks/all?set=dev").get_data(as_text=True)
        self.assertIn("There is no task set dev.", visible(html))
        self.assertNotIn("tasks-set-action", html)

    def test_each_change_needs_the_form_token(self):
        for action, data in (("rename", {"set": "dev", "name": "x"}), ("delete", {"set": "dev"})):
            with self.subTest(action=action):
                r, _ = self.form(action, data, csrf=False)
                self.assertEqual(r.status_code, 403)
        self.assertEqual(sets.load("described", "dev")["id"], self.entry["id"])

    def test_an_entry_of_the_earlier_format_is_refused_without_its_path_and_can_be_deleted(self):
        path = sets.path_of("described", "dev")
        with open(path) as f:
            earlier = dict(json.load(f), benchmark_options={"mode": "hard"}, setup="yours")
        with open(path, "w") as f:
            json.dump(earlier, f)
        html = self.get("/benchmarks/described/tasks/all?set=dev").get_data(as_text=True)
        self.assertIn("The task set dev is of an earlier format, which kept a run's options: delete it and make it again.", visible(html))
        self.assertNotIn(str(self.tmp), html)
        self.assertNotIn("tasks-rename", html)
        ask = re.search(r'<details class="bench-delete" name="tasks-set-action">(.*?)</details>', html, re.S)
        self.assertEqual(visible(ask.group(1)).replace(" ?", "?"), "Delete Delete the task set dev? Runs made from it stay. Yes, delete Keep it")
        r, said = self.form("rename", {"set": "dev", "name": "new"})
        self.assertEqual(said, ["The task set dev is of an earlier format, which kept a run's options: delete it and make it again."])
        r, said = self.form("delete", {"set": "dev"})
        self.assertEqual(said, ["Deleted the task set dev. Runs made from it stay."])
        self.assertFalse(os.path.exists(path))


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


class TheBuilderAfterAChange(BuilderCase):
    """A deleted set is no longer offered and a saved recipe that names it is refused plainly; a renamed one is offered
    under its new name."""

    RECIPE = {"version": 1, "harness": "standard", "name": "fake-0101-120000", "title": "t", "benchmark": "fakebench", "dataset": "all",
              "other": "fake", "provider": "openai", "model": "gpt-5-mini", "concurrency": 1, "unsandboxed": True, "where": "local"}

    def offered(self):
        html = self.get("/new-run").get_data(as_text=True)
        schema = json.loads(re.search(r'<script id="nb-schema" type="application/json">(.*?)</script>', html, re.S).group(1))
        fake = next(b for b in schema["standard"]["benchmarks"] if b["key"] == "fakebench")
        return [(s["name"], s["units"], s["revision"]) for s in fake["datasets"][0]["sets"]]

    def errors(self, ref):
        return self.post("/new-run/runner", {"recipe": dict(self.RECIPE, task_sets={"all": ref})})["errors"]

    def test_a_renamed_set_is_offered_under_its_new_name(self):
        sets.rename("fakebench", "dev", "pair")
        self.assertEqual(self.offered(), [("pair", 2, 1)])
        self.assertEqual(self.errors("pair@1"), [])

    def test_a_deleted_set_is_not_offered_and_a_saved_recipe_naming_it_is_refused_plainly(self):
        sets.delete("fakebench", "dev")
        self.assertEqual(self.offered(), [])
        self.assertEqual([e["message"] for e in self.errors("dev@1")], ["Fake bench has no task set dev: choose another, or all of all's tasks."])

    def test_a_saved_recipe_naming_an_entry_of_the_earlier_format_is_refused_without_its_path(self):
        path = sets.path_of("fakebench", "dev")
        with open(path) as f:
            earlier = dict(json.load(f), benchmark_options={"mode": "hard"})
        with open(path, "w") as f:
            json.dump(earlier, f)
        self.assertEqual(self.offered(), [])
        said = [e["message"] for e in self.errors("dev@1")]
        self.assertEqual(said, ["The task set dev is of an earlier format, which kept a run's options: delete it and make it again."])


class TheRecipeCore(BuilderCase):
    """The builder's core keeps a chosen dataset's set, NAME@REV, in the recipe: read back from a saved recipe, dropped
    with its dataset, and dropped when the benchmark changes."""

    def test_build_a_run_carries_the_dataset_and_the_open_set(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node is required for the recipe core")
        self.b["datasets"].append({"id": "more", "units": 250})
        sets.create(self.b, "picked", dataset="more", units=["t001"])
        html = self.get("/new-run").get_data(as_text=True)
        schema = json.loads(re.search(r'<script id="nb-schema" type="application/json">(.*?)</script>', html, re.S).group(1))
        schema["standard"]["benchmarks"].append(recipe._bench_page(self.b))
        core = Path(__file__).resolve().parents[1] / "static/builder/command-builder-core.js"
        for query, task_sets in (("", {}), ("?set=picked", {"more": "picked@1"})):
            with self.subTest(query=query):
                html = self.get("/benchmarks/described/tasks/more" + query).get_data(as_text=True)
                link = unescape(re.search(r'href="([^"]+)">Build a run', html).group(1))
                program = (f"const C=require({json.dumps(str(core))})({json.dumps(schema)});"
                           f"const query=Object.fromEntries(new URL({json.dumps(link)},'http://localhost').searchParams);"
                           "const r=C.prefill(query);"
                           "process.stdout.write(JSON.stringify([r.benchmark,r.datasets,r.task_sets||{}]));")
                result = subprocess.run([node, "-e", program], stdin=subprocess.DEVNULL,
                                        check=True, capture_output=True, text=True, timeout=10)
                self.assertEqual(json.loads(result.stdout), ["described", ["more"], task_sets])

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

    def test_a_renamed_set_is_named_by_its_new_name_with_its_link(self):
        sets.rename("fakebench", "dev", "trio")
        sets.create(self.fake, "dev", dataset="all", units=["good"])            # another set under the old name
        html = self.page("of-a-set")
        self.assertIn("task set trio, revision 1", self.meta(html))
        self.assertIn('href="/benchmarks/fakebench/tasks/all?set=trio&amp;rev=1"', html)
        self.assertNotIn("/tasks/all?set=dev", html)

    def test_a_deleted_set_is_named_without_a_link(self):
        sets.delete("fakebench", "dev")
        html = self.page("of-a-set")
        self.assertIn("task set dev, revision 1", self.meta(html))
        self.assertNotIn("/tasks/all?set=", html)

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
