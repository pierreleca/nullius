"""A recipe's plan begun on a part (``start_with``): all of it, a number of its tasks or a task set of its dataset;
compiled to ``--start-with``, said in Review as what it plans and what it begins, refused where the run would
refuse it; the set it begins with carried to a host, and a run there begun on it. No key, no network: the runs
are the fake benchmark's control."""
from __future__ import annotations

import io
import json
import os
import shutil
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from nullius.standard import cli, payload, recipe, rerun, sets
from tests.test_task_set_recipe import Scratch


class StartWith(Scratch):
    def test_confirmation_counts_every_limited_plan_and_its_start(self):
        for datasets, limit, planned, begun in ((["all", "more"], None, 8, 5),
                                               (["all", "more"], 2, 4, 3),
                                               (["all"], 2, 2, 1)):
            with self.subTest(datasets=datasets, limit=limit):
                out = self.compile(datasets=datasets, limit=limit, start_with={"all": "1"})
                self.assertEqual(out["errors"], [])
                self.assertTrue(out["confirm"].startswith(f"Plan {planned} tasks of Fake bench and begin {begun} with "),
                                out["confirm"])
                self.assertTrue(next(r[2] for r in out["review"] if r[1] == "Scope").startswith(f"{planned} planned, {begun} to begin"))

    def test_a_number_or_a_set_compiles_to_the_option_and_review_says_planned_and_to_begin(self):
        sets.create(self.b, "first", dataset="all", units=["good", "slow"])
        for given, word, said in (("2", "2", "all · 4 planned, 2 to begin (the first 2)"),
                                  ("set:first", "set:first@1", "all · 4 planned, 2 to begin (task set first, revision 1)")):
            with self.subTest(given=given):
                out = self.compile(start_with={"all": given})
                self.assertEqual(out["errors"], [])
                self.assertIn(f"--dataset all --start-with {word} -j 1", out["shards"][0]["command"])
                self.assertEqual(next(r for r in out["review"] if r[1] == "Dataset")[2], said)
                self.assertTrue(next(r[2] for r in out["review"] if r[1] == "Scope").startswith("4 planned, 2 to begin"))
                self.assertTrue(out["confirm"].startswith("Plan 4 tasks of Fake bench and begin 2 with "), out["confirm"])
        # the plan a set or a limit cuts is what is planned
        sets.create(self.b, "dev", dataset="all", units=["good", "wrong", "slow"])
        out = self.compile(task_sets={"all": "dev"}, start_with={"all": "set:first"})
        self.assertEqual(out["errors"], [])
        self.assertIn("--set dev@1 --start-with set:first@1", out["shards"][0]["command"])
        self.assertIn("· 3 planned, 2 to begin", next(r for r in out["review"] if r[1] == "Dataset")[2])
        out = self.compile(limit=3, start_with={"all": "1"})
        self.assertIn("--limit 3 --start-with 1", out["shards"][0]["command"])
        self.assertIn("· 3 planned, 1 to begin", next(r for r in out["review"] if r[1] == "Dataset")[2])

    def test_what_the_run_would_refuse_is_refused_on_its_step(self):
        sets.create(self.b, "other-ds", dataset="more", units=["good"])
        sets.create(self.b, "dev", dataset="all", units=["good"])
        sets.create(self.b, "wide", dataset="all", units=["good", "slow"])
        for kw, step, said in (({"start_with": {"all": "5"}}, "dataset", "Start with 5 tasks: all plans 4."),
                               ({"start_with": {"all": "0"}}, "dataset", "Start with a number of tasks of at least 1."),
                               ({"start_with": {"all": "set:other-ds"}}, "dataset",
                                "Start with: the task set other-ds is cut from the dataset more, not all."),
                               ({"start_with": {"all": "set:nosuch"}}, "dataset", "Fake bench has no task set nosuch to start with"),
                               ({"start_with": {"all": "first.txt"}}, "dataset", "Start with all of the tasks, a number of them, or a task set."),
                               ({"start_with": {"more": "1"}}, "dataset", "Start with 1 is for more, a dataset not chosen."),
                               ({"task_sets": {"all": "dev"}, "start_with": {"all": "set:wide"}}, "dataset",
                                "Start with: the task set wide names 1 of all's tasks outside the task set dev."),
                               ({"start_with": {"all": "1"}, "where": ["local", "bench"]}, "where",
                                "A run begun on a part of its plan runs on one machine")):
            with self.subTest(kw=kw):
                out = self.compile(**kw)
                self.assertIn(recipe.STEP[step], [e["step"] for e in out["errors"]], out["errors"])
                self.assertTrue(any(e["message"].startswith(said) for e in out["errors"]), out["errors"])

    def test_a_recipe_without_it_compiles_as_before(self):
        self.assertEqual(self.compile(), self.compile(start_with={}))
        self.assertNotIn("--start-with", self.compile()["shards"][0]["command"])


class OnAHost(Scratch):
    def test_the_snapshot_carries_the_set_begun_with_and_the_run_there_plans_the_whole(self):
        entry = sets.create(self.b, "first", dataset="all", units=["wrong"])
        r = self.recipe(start_with={"all": "set:first@1"})
        self.assertEqual(payload.recipe_sets(r), [("fakebench", "first")])
        self.assertEqual(payload.recipe_sets(self.recipe(start_with={"all": "2"})), [])
        empty = self.tmp / "empty-package"
        shutil.copytree(Path(payload.PACKAGE), empty, ignore=shutil.ignore_patterns("__pycache__", "catalogue", "kits"))
        _, here = payload.build(str(self.tmp / "cache"), package=empty, catalogue=str(self.cat), sets=payload.recipe_sets(r))
        run_catalogue = self.tmp / "host" / "catalogue"
        shutil.copytree(Path(here) / "app/nullius/catalogue", run_catalogue)
        shutil.rmtree(self.cat)
        jobs = self.tmp / "host" / "jobs"
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, {"NULLIUS_CATALOGUE": str(run_catalogue)}), \
                mock.patch.object(cli, "_bench", return_value=self.b), mock.patch.object(cli, "_exit_on_term"), \
                mock.patch.object(cli.settings, "load"), redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["run", "fakebench", "nop", "--dataset", "all", "--start-with", "set:first@1", "-j", "1",
                             "--name", "begun", "--jobs", str(jobs)])
        self.assertEqual(code, 0, out.getvalue() + err.getvalue())
        cfg = json.loads((jobs / "begun" / "config.json").read_text())
        self.assertEqual(cfg["standard"]["start_with"], {"task_set": sets.record(entry, sets.revision(entry, 1)), "units": ["wrong"]})
        self.assertEqual(cfg["datasets"][0]["n_tasks"], 4)
        self.assertEqual(sorted(d for d in os.listdir(jobs / "begun") if "__u" in d), ["wrong__u0001"])
        self.assertEqual(len(rerun.unfinished(str(jobs / "begun"))), 3)
        self.assertIn("1 of 4 planned tasks run; the rest in this job: nullius standard rerun", out.getvalue())
