"""A job planned on a whole and begun on a part of it, on its page: how many of the plan have run, the tasks not
run as rows with no attempt under an outcome of their own, the rest and a set's tasks not run each one click from
the question Run selected asks, and the runs list's count over the trials run."""
from __future__ import annotations

import json
import os
import re
import unittest
from pathlib import Path
from unittest.mock import patch

from nullius.harbor import feed
from nullius.standard import sets
from ui_tests.test_capped_job import BENCH
from ui_tests import test_rerun_page as rerun_page
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
        self.assertIn('title="Begun on a part of its plan: solved over the trials run">3 of 6 tasks run</span>', runs)

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
