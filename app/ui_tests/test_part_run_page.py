"""A job planned on a whole and begun on a part of it, on its page: how many of the plan have run, the tasks not
run as rows with no attempt under an outcome of their own, the rest and a set's tasks not run each one click from
the question Run selected asks, and the runs list's count over the trials run."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from nullius.harbor import feed
from nullius.standard import sets
from ui_tests.test_capped_job import BENCH
from ui_tests import test_rerun_page as rerun_page, test_task_set_pages as task_set_pages
from ui_tests.test_rerun_page import text, trial_name

PLAN = [f"t-{k}" for k in range(6)]


class PartRunPage(unittest.TestCase):
    run_of, open, page, post, interrupted = (rerun_page.RerunPage.__dict__[k] for k in ("run_of", "open", "page", "post", "interrupted"))

    def setUp(self):
        rerun_page.RerunPage.setUp(self)
        self.stack.enter_context(patch.dict(os.environ, {"NULLIUS_CATALOGUE": str(self.tmp / "catalogue")}))

    def begun_on_a_part(self, d: Path):
        """The job as ``run --start-with 3`` leaves it: six tasks planned, as its feed and its config say, the first
        three run."""
        writer = feed.Writer(str(d / "events.jsonl"), "r1")
        writer.emit("job.start", n_total=6, tasks=PLAN)
        for k in range(6):
            writer.emit("trial.queued", f"t-{k}__u{k:04d}", agent="fake", position=k)
        writer.emit("job.end")
        cfg = json.loads((d / "config.json").read_text())
        cfg["standard"]["start_with"] = {"count": 3, "units": PLAN[:3]}
        cfg["datasets"][0]["n_tasks"] = 6
        (d / "config.json").write_text(json.dumps(cfg))
        top = json.loads((d / "result.json").read_text())
        top["n_total_trials"] = 6
        (d / "result.json").write_text(json.dumps(top))

    def test_the_page_says_how_many_of_the_plan_ran_and_lists_the_rest_with_no_attempt(self):
        d = self.run_of()
        self.begun_on_a_part(d)
        self.open(d.parent)
        html = self.page("/job/r1?view=results")
        self.assertEqual(text(html, r'<span id="plan-run".*?</span>'), "3 of 6 planned tasks run")
        self.assertEqual(text(html, r'<dt>tasks run</dt><dd id="tasks-run".*?</dd>'), "tasks run 3 of 6")
        self.assertIn('id="tasks-run" class="warn"', html)
        self.assertEqual(text(html, r'<div class="row tot">.*?</div>'), "3 Trials of 6 planned")
        self.assertEqual(text(html, r'<a class="fx" data-o="not_run".*?</a>'), "Not run 3")
        self.assertNotIn('data-o="no_result"', html)
        rest = [f"t-{k}__u{k:04d}" for k in (3, 4, 5)]
        rows = {n: text(html, rf'<tr data-outcome="not_run"[^>]*data-trial="{n}".*?</tr>') for n in rest}
        self.assertEqual(rows, {n: f"{n[:3]} Not run" for n in rest})
        self.assertEqual(re.findall(r'<input class="pick-row" type="checkbox" value="([^"]+)" data-first', html), rest)
        self.assertEqual(text(html, r'<span class="cnt" id="trial-count">.*?</span>'), "6")
        self.assertRegex(html, r'<button class="btn sm ghost" id="pick-rest" type="button" aria-haspopup="dialog" '
                               r'data-pick="t-3__u0003 t-4__u0004 t-5__u0005">Run the rest · 3</button>')
        self.assertNotIn('id="plan-sets"', html)                                 # no set names one
        # the runs list: solved over the trials run, and how many of the plan
        runs = self.page("/runs")
        self.assertIn('title="Begun on a part of its plan: the rates and the mean are over the tasks run">3 of 6 tasks run</span>', runs)
        self.assertIn("3 on disk of 6 planned · begun on a part of its plan, 3 of 6 tasks run", self.page("/runs?view=all"))

    def test_a_set_s_tasks_not_run_and_the_rest_go_to_the_question_as_ticks(self):
        d = self.run_of()
        self.begun_on_a_part(d)
        both = dict(BENCH, datasets=[{"id": "all"}, {"id": "other"}], label="Fake bench")
        sets.create(both, "late", dataset="all", units=["t-4", "t-0"])
        sets.create(both, "done", dataset="all", units=["t-1"])                  # every task of it has run
        sets.create(both, "elsewhere", dataset="other", units=["t-5"])           # another dataset
        self.open(d.parent)
        html = self.page("/job/r1?view=results")
        menu = re.search(r'<details class="pop plan-sets" id="plan-sets">.*?</details>', html, re.S)[0]
        self.assertEqual(re.findall(r'data-pick="([^"]*)">([^<]*)<b>(\d+)</b>', menu), [("t-4__u0004", "late, revision 1", "1")])
        self.assertEqual(text(html, r'<summary class="btn sm ghost" aria-haspopup="true">.*?</summary>'), "Run a set")
        # what a set's or the rest's click ticks is posted as Run selected posts it: the path a re-run takes
        r = self.post("r1", {"trials": ["t-4__u0004"]})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertIn("--unit t-4__u0004 --from-run", self.asked[-1][1]["script"])
        r = self.post("r1", {"trials": ["t-3__u0003", "t-4__u0004", "t-5__u0005"]})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertIn("--unit t-3__u0003 --unit t-4__u0004 --unit t-5__u0005 --from-run", self.asked[-1][1]["script"])

    def test_on_a_host_the_later_parts_go_back_there(self):
        d = self.run_of(host="bench")
        self.begun_on_a_part(d)
        self.open(d.parent)
        self.assertIn('data-first="On bench, in this job:', self.page("/job/r1?view=results"))
        r = self.post("r1", {"trials": ["t-5__u0005"]})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        ((ex, part),) = self.asked
        self.assertEqual((ex.plan.host.label, part["trials"]), ("bench", ["t-5__u0005"]))

    def test_one_unrun_row_selects_every_planned_attempt_by_tick_set_or_rest(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node is required for the selection controls")
        d = self.run_of(repeat=2)
        self.begun_on_a_part(d)
        shutil.rmtree(d / trial_name(2))
        for k in range(2):
            for attempt in (1, 2):
                dest = d / f"{trial_name(k)}r{attempt}"
                shutil.copytree(d / trial_name(k), dest)
                result = json.loads((dest / "result.json").read_text())
                result["trial_name"] = dest.name
                (dest / "result.json").write_text(json.dumps(result))
            shutil.rmtree(d / trial_name(k))
        cfg = json.loads((d / "config.json").read_text())
        cfg["standard"]["start_with"] = {"count": 2, "units": PLAN[:2]}
        (d / "config.json").write_text(json.dumps(cfg))
        top = json.loads((d / "result.json").read_text())
        top["n_total_trials"] = 12
        (d / "result.json").write_text(json.dumps(top))
        (d / "events.jsonl").unlink()
        writer = feed.Writer(str(d / "events.jsonl"), "r1")
        writer.emit("job.start", n_total=12, tasks=PLAN)
        for k in range(6):
            for attempt in (1, 2):
                writer.emit("trial.queued", f"t-{k}__u{k:04d}r{attempt}", agent="fake", position=k)
        writer.emit("job.end")
        sets.create(dict(BENCH, datasets=[{"id": "all"}]), "late", dataset="all", units=["t-4", "t-0"])
        self.open(d.parent)
        html = self.page("/job/r1?view=results")
        self.assertEqual(text(html, r'<span id="plan-run".*?</span>'), "2 of 6 planned tasks run")
        rows = re.findall(r'<tr data-outcome="not_run".*?</tr>', html, re.S)
        self.assertEqual(len(rows), 4)
        self.assertEqual([text(row, r".*") for row in rows], [f"t-{k} Not run" for k in range(2, 6)])
        ticks = [dict(value=re.search(r'value="([^"]+)"', row)[1],
                      trials=re.search(r'data-trials="([^"]+)"', row)[1]) for row in rows]
        picks = re.findall(r'data-pick="([^"]+)"', html)
        board = Path(__file__).resolve().parents[1] / "static/runs/board.js"
        program = r"""
const [file, values, picks] = process.argv.slice(1).map(JSON.parse), elements = new Map();
function element(id) {
  if (!elements.has(id)) elements.set(id, {dataset: {}, listeners: {}, textContent: '',
    addEventListener(k, fn) { this.listeners[k] = fn; }, click() { this.listeners.click(); },
    replaceChildren() {}, showModal() {}, closest() { return null; }});
  return elements.get(id);
}
const ticks = values.map(x => Object.assign(element(x.value), {value: x.value,
  dataset: {first: '', trials: x.trials}, getAttribute() { return 'Choose ' + x.value; }}));
const buttons = picks.map(pick => Object.assign(element('button:' + pick), {dataset: {pick}})), posted = [];
element('form').elements = {reason: {value: ''}, max_usd: {value: ''}};
const source = require('node:fs').readFileSync(file, 'utf8');
new Function('$', '$$', 'rows', 'document', 'nulliusFetch', 'URL', 'location',
  source.slice(source.indexOf('  const ticks ='), source.lastIndexOf('  apply();')))(
  element, s => s === '#trial-rows .pick-row' ? ticks : buttons, [], {createElement: () => ({})},
  async (url, options) => { posted.push(JSON.parse(options.body)); return {ok: true, json: async () => ({ok: true})}; },
  URL, {href: 'http://localhost/job/r1', assign() {}});
(async () => {
  ticks[0].checked = true; element('#rerun-open').click();
  const submit = () => element('form').listeners.submit({submitter: {value: 'run'}, preventDefault() {}});
  await submit();
  for (const button of buttons) { button.click(); await submit(); }
  process.stdout.write(JSON.stringify(posted));
})();
"""
        posted = json.loads(subprocess.run([node, "-e", program, json.dumps(str(board)), json.dumps(ticks), json.dumps(picks)],
                                          capture_output=True, text=True, check=True, timeout=10).stdout)
        expected = [[f"t-{k}__u{k:04d}r{attempt}" for k in units for attempt in (1, 2)]
                    for units in ([2], [4], range(2, 6))]
        self.assertEqual([body["trials"] for body in posted], expected)
        for body in posted:
            response = self.post("r1", body)
            self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
            self.assertEqual(self.asked[-1][1]["trials"], body["trials"])

    def test_a_job_run_whole_shows_none_of_it(self):
        d = self.run_of()
        self.open(d.parent)
        html = self.page("/job/r1?view=results")
        for absent in ('id="plan-run"', 'id="tasks-run"', 'id="pick-rest"', 'id="plan-sets"', "not_run"):
            self.assertNotIn(absent, html)
        self.assertNotIn("tasks run</span>", self.page("/runs"))

    def test_an_interrupted_job_keeps_no_result_and_offers_the_rest(self):
        d = self.run_of()
        self.interrupted(d)
        self.open(d.parent)
        html = self.page("/job/r1?view=results")
        self.assertEqual(text(html, r'<a class="fx" data-o="no_result".*?</a>'), "No result 3")
        self.assertNotIn('data-o="not_run"', html)
        self.assertIn(f'data-pick="{trial_name(2)} t-3__u0003 t-4__u0004">Run the rest · 3</button>', html)


class TheBuilder(task_set_pages.BuilderCase):
    """Start with in the builder: Review, compiled by the server, says what is planned and what begins; the core keeps
    the choice in the recipe, drops it with its dataset and its benchmark, and reads it back from a saved recipe."""

    def test_review_says_planned_and_to_begin(self):
        recipe = {"version": 1, "harness": "standard", "name": "fake-0101-120000", "title": "t", "benchmark": "fakebench", "dataset": "all",
                  "other": "fake", "provider": "openai", "model": "gpt-5-mini", "concurrency": 1, "unsandboxed": True, "where": "local",
                  "start_with": {"all": "set:dev@1"}}
        out = self.post("/new-run/runner", {"recipe": recipe})
        self.assertEqual(out["errors"], [])
        self.assertIn(["Dataset", "all · 4 planned, 2 to begin (task set dev, revision 1)"], [r[1:] for r in out["review"]])
        self.assertTrue(out["confirm"].startswith("Plan 4 tasks of Fake bench and begin 2 with "), out["confirm"])
        out = self.post("/new-run/runner", {"recipe": dict(recipe, start_with={"all": "9"})})
        self.assertEqual([e["message"] for e in out["errors"]], ["Start with 9 tasks: all plans 4."])

    def test_the_core_keeps_start_with_with_its_dataset(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node is required for the recipe core")
        html = self.get("/new-run").get_data(as_text=True)
        schema = json.loads(re.search(r'<script id="nb-schema" type="application/json">(.*?)</script>', html, re.S).group(1))
        core = Path(__file__).resolve().parents[1] / "static" / "builder" / "command-builder-core.js"
        program = (f"const C=require({json.dumps(str(core))})({json.dumps(schema)});\n"
                   "const r=C.fromJSON({version:1,harness:'standard',benchmark:'fakebench',datasets:['all'],others:['fake'],"
                   "start_with:{all:'set:dev@1',gone:'2',bad:3}});\n"
                   "const read=JSON.parse(JSON.stringify(r.start_with));\n"
                   "C.setStartWith(r,'all','');const cleared=r.start_with===undefined;\n"
                   "C.setStartWith(r,'all','3');const count=r.start_with.all;r.datasets=[];C.keepStartWith(r);const dropped=r.start_with===undefined;\n"
                   "r.datasets=['all'];C.setStartWith(r,'all','3');C.setStandardBenchmark(r,'fakememo');\n"
                   "process.stdout.write(JSON.stringify({read,cleared,count,dropped,other:r.start_with===undefined}));")
        got = json.loads(subprocess.run([node, "-"], input=program, check=True, capture_output=True, text=True).stdout)
        self.assertEqual(got, {"read": {"all": "set:dev@1"}, "cleared": True, "count": "3", "dropped": True, "other": True})

    def test_deselecting_a_dataset_removes_its_start_choice_before_review(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node is required for the dataset control")
        load = task_set_pages.definitions.load
        def with_more(kind, root=None, every=False):
            entries, errors = load(kind, root, every=every)
            if kind == "benchmark":
                entries = [dict(b, datasets=[*b["datasets"], {"id": "more", "units": 4}])
                           if b["key"] == "fakebench" else b for b in entries]
            return entries, errors
        self.stack.enter_context(patch.object(task_set_pages.definitions, "load", with_more))
        html = self.get("/new-run").get_data(as_text=True)
        schema = json.loads(re.search(r'<script id="nb-schema" type="application/json">(.*?)</script>', html, re.S)[1])
        folder = Path(__file__).resolve().parents[1] / "static/builder"
        handler = next(line.strip()[5:] for line in (folder / "command-builder.js").read_text().splitlines()
                       if "else if(d.stdDataset)" in line)
        program = (f"const C=require({json.dumps(str(folder / 'command-builder-core.js'))})({json.dumps(schema)});"
                   "const rec=C.createStandard('fakebench','fake');rec.datasets=['all','more'];"
                   "rec.start_with={all:'1',more:'2'};rec.model='gpt-5-mini';rec.concurrency=1;rec.unsandboxed=true;"
                   f"new Function('C','r','d',{json.dumps(handler)})(C,()=>rec,{{stdDataset:'all'}});"
                   "process.stdout.write(JSON.stringify(rec));")
        recipe = json.loads(subprocess.run([node, "-e", program], capture_output=True, text=True, check=True, timeout=10).stdout)
        self.assertEqual(recipe["start_with"], {"more": "2"})
        self.assertEqual(self.post("/new-run/runner", {"recipe": recipe})["errors"], [])
