"""A task set in the builder's recipe and on a host: ``task_sets`` compiles to ``--set NAME@REV`` beside the
dataset, its count in the Review step; a set of another benchmark or dataset is refused; the runner snapshot a
host receives carries the recipe's sets, and a run started from it there reads the set and records it. No key,
no network: the runs are the fake benchmark's control."""
from __future__ import annotations

import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from nullius import progress
from nullius.standard import cli, definitions, install, payload, recipe, sets

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "standard", "catalogue")
NOP = {"nop": {"source": "nullius", "from": "nothing said"}}


class Scratch(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nullius-set-recipe-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cat = self.tmp / "catalogue"
        self.b = dict(definitions.get("benchmark", "fakebench", ROOT), controls=NOP,
                      datasets=[{"id": "all", "units": 4}, {"id": "more", "units": 4}])
        self.memo = definitions.get("benchmark", "fakememo", ROOT)
        for patch in (mock.patch.dict(os.environ, {"NULLIUS_CATALOGUE": str(self.cat)}),
                      mock.patch.object(install, "HOME", str(self.tmp / "home")), mock.patch.object(progress, "ECHO", None)):
            patch.start()
            self.addCleanup(patch.stop)
        self.defs = {"benchmark": [self.b, self.memo], "agent": [], "capability": []}

    def recipe(self, **kw):
        r = {"version": 1, "harness": "standard", "name": "fake-sets", "title": "t", "benchmark": "fakebench", "datasets": ["all"],
             "others": ["nop"], "provider": "openai", "model": "", "concurrency": 1, "where": "local"}
        r.update(kw)
        return r

    def compile(self, **kw):
        return recipe.compile(self.recipe(**kw), self.defs)


class TheRecipe(Scratch):
    def test_a_set_compiles_to_set_name_at_its_revision_and_review_names_it_and_its_count(self):
        sets.create(self.b, "dev", dataset="all", units=["good", "wrong"])
        sets.update("fakebench", "dev", add=["slow"])
        for ref, rev, n in (("dev", 2, 3), ("dev@1", 1, 2)):
            with self.subTest(ref=ref):
                out = self.compile(task_sets={"all": ref})
                self.assertEqual(out["errors"], [])
                self.assertIn(f"--dataset all --set dev@{rev} -j 1", out["shards"][0]["command"])
                dataset_row = next(r for r in out["review"] if r[1] == "Dataset")
                self.assertEqual(dataset_row[2], f"all · task set dev, revision {rev} · {n} tasks")
                self.assertTrue(next(r for r in out["review"] if r[1] == "Scope")[2].startswith(f"all {n} tasks"))
                self.assertTrue(out["confirm"].startswith(f"Run all {n} tasks of Fake bench with "), out["confirm"])

    def test_a_set_of_another_benchmark_or_dataset_or_a_dataset_not_chosen_is_refused(self):
        sets.create(self.memo, "theirs", dataset=self.memo["datasets"][0]["id"], units=["x"])
        sets.create(self.b, "other-ds", dataset="more", units=["good"])
        sets.create(self.b, "dev", dataset="all", units=["good"])
        for kw, said in (({"task_sets": {"all": "theirs"}}, "Fake bench has no task set theirs: choose another, or all of all's tasks."),
                         ({"task_sets": {"all": "other-ds"}}, "The task set other-ds is cut from the dataset more, not all."),
                         ({"task_sets": {"more": "dev"}}, "The task set dev is for more, a dataset not chosen."),
                         ({"task_sets": {"all": "dev@4"}}, "Task set dev@4: dev has no revision 4: its revisions are 1."),
                         ({"task_sets": {"all": "a/b"}}, "Task set a/b: 'a/b' is not a task set's name"),
                         ({"task_sets": ["dev"]}, "Task sets are a dataset with a set's name each.")):
            with self.subTest(kw=kw):
                out = self.compile(**kw)
                self.assertEqual([e["step"] for e in out["errors"]], [recipe.STEP["dataset"]])
                self.assertTrue(out["errors"][0]["message"].startswith(said), out["errors"][0]["message"])

    def test_a_recipe_without_a_set_compiles_as_before(self):
        sets.create(self.b, "dev", dataset="all", units=["good"])
        plain, empty = self.compile(), self.compile(task_sets={})
        self.assertEqual(plain, empty)
        self.assertEqual(plain["shards"][0]["command"],
                         'nullius standard run fakebench nop --provider openai --dataset all -j 1 '
                         '--name fake-sets --from-run --jobs "${NULLIUS_JOBS:-jobs_standard}"')
        self.assertEqual(next(r for r in plain["review"] if r[1] == "Dataset")[2], "all · 4 tasks")

    def test_the_builder_offers_each_datasets_sets(self):
        sets.create(self.b, "dev", dataset="all", units=["good", "wrong"], description="two")
        page = recipe._bench_page(self.b)
        self.assertEqual([[(s["name"], s["revision"], s["units"], s["description"]) for s in d["sets"]] for d in page["datasets"]],
                         [[("dev", 1, 2, "two")], []])


class OnAHost(Scratch):
    """What the app sends a host for a recipe with a set: the snapshot holds the set's entry; the run there, reading
    the copy of the snapshot's catalogue a host run reads, runs the set and records it."""

    def test_the_snapshot_carries_the_recipes_sets_and_the_run_there_records_the_set(self):
        entry = sets.create(self.b, "settings", dataset="all", units=["good", "wrong"])
        r = self.recipe(task_sets={"all": "settings@1"})
        self.assertEqual(payload.recipe_sets(r), [("fakebench", "settings")])
        self.assertEqual(payload.recipe_sets(self.recipe()), [])
        empty = self.tmp / "empty-package"
        shutil.copytree(Path(payload.PACKAGE), empty, ignore=shutil.ignore_patterns("__pycache__", "catalogue", "kits"))
        key0, _ = payload.build(str(self.tmp / "cache"), package=empty, catalogue=str(self.cat))
        key, here = payload.build(str(self.tmp / "cache"), package=empty, catalogue=str(self.cat), sets=payload.recipe_sets(r))
        self.assertNotEqual(key, key0)
        self.assertEqual(payload.build(str(self.tmp / "cache"), package=empty, catalogue=str(self.cat),
                                       sets=payload.recipe_sets(r)), (key, here))
        carried = Path(here) / "app/nullius/catalogue/sets/fakebench/settings.json"
        self.assertEqual(json.loads(carried.read_text())["id"], entry["id"])
        for name in ("gone", "../settings"):
            with self.assertRaises(ValueError):
                payload.build(str(self.tmp / "cache"), package=empty, catalogue=str(self.cat), sets=[("fakebench", name)])
        # on the host: the snapshot's catalogue copied for the run (execution: cp -R .../app/nullius/catalogue), read there
        run_catalogue = self.tmp / "host" / "runs" / "fake-sets" / "catalogue"
        shutil.copytree(Path(here) / "app/nullius/catalogue", run_catalogue)
        shutil.rmtree(self.cat)                               # nothing of this machine's catalogue is left to read
        jobs = self.tmp / "host" / "runs" / "fake-sets" / "jobs"
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, {"NULLIUS_CATALOGUE": str(run_catalogue)}), \
                mock.patch.object(cli, "_bench", return_value=self.b), mock.patch.object(cli, "_exit_on_term"), \
                mock.patch.object(cli.settings, "load"), redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["run", "fakebench", "nop", "--dataset", "all", "--set", "settings@1", "-j", "1", "--name", "fake-sets",
                             "--jobs", str(jobs)])
        self.assertEqual(code, 0, out.getvalue() + err.getvalue())
        cfg = json.loads((jobs / "fake-sets" / "config.json").read_text())
        self.assertEqual(cfg["standard"]["task_set"], sets.record(entry, sets.revision(entry, 1)))
        self.assertEqual(sorted(d for d in os.listdir(jobs / "fake-sets") if "__u" in d), ["good__u0000", "wrong__u0001"])


if __name__ == "__main__":
    unittest.main()
