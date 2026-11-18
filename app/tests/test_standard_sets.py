"""Task sets (``standard/sets.py``): a named list of a benchmark's units in the
user catalogue, with an id and revisions, and no option. The content hash says
the same for the same ids in any order and changes with an id or the dataset;
a change of membership adds a revision and keeps the earlier ones; a name that
would leave the benchmark's folder is refused; a set is made from a file or
from a job's trials by outcome or by name; an entry comes in from its file or
from a job that ran it. ``run --set`` takes the set's dataset, refuses what
contradicts it, runs under any options it is given, refuses ids its driver does
not list under them, and records the set in the job's config. No key, no
network: the runs are a control's."""
from __future__ import annotations

import copy
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from nullius import progress
from nullius.harbor import feed
from nullius.standard import cli, definitions, install, job, merge, run, sets, shard

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "standard", "catalogue")
NOP = {"nop": {"source": "nullius", "from": "nothing said"}}
MODE = {"mode": {"summary": "a mode the listing follows", "values": ["plain", "hard"], "default": "plain"}}
DATASET = ["good", "wrong", "slow", "model"]


def read(path):
    with open(path) as f:
        return json.load(f)


class Scratch(unittest.TestCase):
    """A catalogue of its own (``NULLIUS_CATALOGUE``), the fake benchmark with a control and an option."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nullius-sets-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cat = os.path.join(self.tmp, "catalogue")
        self.jobs = os.path.join(self.tmp, "jobs")
        self.b = dict(definitions.get("benchmark", "fakebench", ROOT), controls=NOP, options=MODE)
        for patch in (mock.patch.dict(os.environ, {"NULLIUS_CATALOGUE": self.cat}), mock.patch.object(install, "HOME", os.path.join(self.tmp, "home")),
                      mock.patch.object(cli, "_bench", return_value=self.b), mock.patch.object(cli, "_find_bench", return_value=self.b),
                      mock.patch.object(cli, "_exit_on_term"), mock.patch.object(cli.settings, "load"),
                      mock.patch.object(progress, "ECHO", None)):
            patch.start()
            self.addCleanup(patch.stop)

    def file(self, text, name="units.txt"):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def make(self, name, text, *extra):
        code, out, err = self.cli("set", "create", "fakebench", name, "--dataset", "all", "--units", self.file(text), *extra)
        self.assertEqual(code, 0, err)
        return sets.load("fakebench", name)

    def trials(self, job_dir):
        return sorted(d for d in os.listdir(job_dir) if "__u" in d)


class Hash(Scratch):
    def test_the_same_ids_in_another_order_or_from_a_file_with_comments_hash_the_same(self):
        a = self.make("a", "good\nmodel\n")
        b = self.make("b", "# the same two\n\n  model  \r\ngood\n")
        self.assertEqual(sets.revision(a)["hash"], sets.revision(b)["hash"])
        self.assertNotEqual(a["id"], b["id"])                        # two entries of equal lists stay two sets
        self.assertEqual(sets.content_hash("fakebench", "all", ["good", "model"]),
                         sets.content_hash("fakebench", "all", ["model", "good"]))

    def test_it_changes_with_an_id_or_the_dataset(self):
        base = sets.content_hash("fakebench", "all", ["good", "model"])
        for other in (sets.content_hash("fakebench", "all", ["good", "slow"]),
                      sets.content_hash("fakebench", "responding", ["good", "model"])):
            self.assertNotEqual(base, other)
        self.assertEqual(sets.revision(self.make("plain", "good\nmodel\n"))["hash"], base)

    def test_a_file_changed_by_hand_is_refused_where_it_is_read(self):
        self.make("edited", "good\nmodel\n")
        path = sets.path_of("fakebench", "edited")
        entry = read(path)
        entry["revisions"][0]["units"].append("slow")
        with open(path, "w") as f:
            json.dump(entry, f)
        with self.assertRaisesRegex(sets.Refused, "changed by hand"):
            sets.load("fakebench", "edited")


class NoOption(Scratch):
    def test_set_create_takes_no_benchmark_option(self):
        for source in (["--units", self.file("good\n")], ["--search", "good"]):
            with self.subTest(source=source[0]):
                out, err = io.StringIO(), io.StringIO()
                with redirect_stdout(out), redirect_stderr(err), self.assertRaises(SystemExit) as stop:
                    cli.main(["set", "create", "fakebench", "dev", "--dataset", "all", *source, "--bench-option", "mode=hard"])
                self.assertEqual((stop.exception.code, out.getvalue()), (2, ""))
                self.assertIn("usage:", err.getvalue())
                self.assertIn("unrecognized arguments: --bench-option", err.getvalue())
        self.assertFalse(os.path.exists(self.cat))

    def test_the_entry_holds_no_option_and_an_earlier_entry_that_does_is_refused_where_it_is_read(self):
        entry = self.make("dev", "good\nmodel\n")
        self.assertEqual(sorted(read(sets.path_of("fakebench", "dev"))),
                         ["benchmark", "dataset", "description", "id", "name", "revisions", "schema"])
        self.assertEqual(sorted(sets.record(entry, sets.revision(entry))), ["hash", "id", "name", "revision", "units"])
        for field, value in (("benchmark_options", {"mode": "hard"}), ("setup", "yours"), ("benchmark_version", "1")):
            with self.subTest(field=field):
                path = sets.path_of("fakebench", "dev")
                with open(path, "w") as f:
                    json.dump(dict(entry, schema=sets.SCHEMA, **{field: value}), f)
                with self.assertRaisesRegex(sets.Refused, f"holds {field}, which a task set does not hold"):
                    sets.load("fakebench", "dev")
                code, out, err = self.cli("set", "show", "fakebench", "dev")
                self.assertEqual((code, out), (2, ""))
                self.assertIn(f"holds {field}", err)


class Revisions(Scratch):
    def test_a_change_of_membership_adds_a_revision_and_keeps_the_earlier_ones(self):
        self.make("dev", "good\nmodel\n")
        code, out, err = self.cli("set", "update", "fakebench", "dev", "--add", "slow", "--note", "one more")
        self.assertEqual(code, 0, err)
        self.assertIn("revision 2, 3 units", out)
        code, out, err = self.cli("set", "update", "fakebench", "dev", "--remove", "good")
        self.assertEqual(code, 0, err)
        entry = sets.load("fakebench", "dev")
        self.assertEqual([(r["revision"], r["units"]) for r in entry["revisions"]],
                         [(1, ["good", "model"]), (2, ["good", "model", "slow"]), (3, ["model", "slow"])])
        self.assertEqual(entry["revisions"][1]["note"], "one more")
        self.assertEqual(entry["revisions"][0]["note"], "from units.txt")

    def test_an_update_that_changes_nothing_adds_none(self):
        self.make("dev", "good\nmodel\n")
        for args in (["--add", "good"], ["--units", self.file("model\n# again\ngood\n", "same.txt")]):
            with self.subTest(args=args):
                code, out, err = self.cli("set", "update", "fakebench", "dev", *args)
                self.assertEqual(code, 0, err)
                self.assertIn("no change", out)
                self.assertEqual(len(sets.load("fakebench", "dev")["revisions"]), 1)

    def test_name_at_two_reads_the_second(self):
        self.make("dev", "good\n")
        self.cli("set", "update", "fakebench", "dev", "--add", "model")
        self.cli("set", "update", "fakebench", "dev", "--add", "slow")
        code, out, err = self.cli("set", "show", "fakebench", "dev@2", "--json")
        self.assertEqual(code, 0, err)
        shown = json.loads(out)
        self.assertEqual((shown["revision"]["revision"], shown["revision"]["units"]), (2, ["good", "model"]))
        self.assertEqual([r["revision"] for r in shown["revisions"]], [1, 2, 3])
        code, out, err = self.cli("set", "show", "fakebench", "dev@9")
        self.assertEqual((code, out), (3, ""))
        self.assertIn("no revision 9", err)

    def test_removing_what_the_set_does_not_hold_or_all_it_holds_is_refused(self):
        self.make("dev", "good\n")
        for args, said in ((["--remove", "nope"], "does not hold nope"), (["--remove", "good"], "no unit")):
            code, out, err = self.cli("set", "update", "fakebench", "dev", *args)
            self.assertEqual((code, out), (2, ""))
            self.assertIn(said, err)
        self.assertEqual(len(sets.load("fakebench", "dev")["revisions"]), 1)


class Names(Scratch):
    def test_a_name_that_would_leave_the_benchmarks_folder_is_refused(self):
        for name in ("a/b", "..", "", ".hidden", "../up", "a\\b", "x..y"):
            with self.subTest(name=name):
                code, out, err = self.cli("set", "create", "fakebench", name, "--dataset", "all", "--units", self.file("good\n"))
                self.assertEqual((code, out), (2, ""))
                self.assertIn("not a task set's name", err)
        self.assertFalse(os.path.exists(self.cat))

    def test_links_cannot_escape_through_parent_folders_or_an_entry(self):
        entry = self.make("dev", "good\n")
        exported = self.file(json.dumps(read(sets.path_of("fakebench", "dev"))), "export.json")
        for relative in ("sets", "sets/fakebench", "sets/fakebench/dev.json"):
            with self.subTest(path=relative):
                path = os.path.join(self.cat, relative)
                outside = os.path.join(self.tmp, "outside")
                os.rename(path, outside)
                os.symlink(outside, path)
                try:
                    for action in (lambda: sets.load("fakebench", "dev"),
                                   lambda: sets.write(entry),
                                   lambda: sets.update("fakebench", "dev", add=["model"]),
                                   lambda: sets.import_path(exported)):
                        with self.assertRaisesRegex(sets.Refused, "outside"):
                            action()
                    if relative != "sets/fakebench/dev.json":
                        with self.assertRaisesRegex(sets.Refused, "outside"):
                            sets.create(self.b, "new", dataset="all", units=["good"])
                    else:
                        with self.assertRaisesRegex(sets.Refused, "outside"):
                            sets.create(self.b, "dev", dataset="all", units=["good"])
                    with mock.patch.object(sets.os, "listdir", wraps=os.listdir) as listed:
                        try:
                            found, bad = sets.entries()
                        except sets.Refused as e:
                            self.assertIn("outside", str(e))
                        else:
                            self.assertEqual(found, [])
                            self.assertTrue(any("outside" in problem for problem in bad))
                        self.assertNotIn(mock.call(path), listed.call_args_list)
                finally:
                    os.unlink(path)
                    os.rename(outside, path)
                self.assertEqual(sets.load("fakebench", "dev"), entry)
                self.assertFalse(os.path.exists(sets.path_of("fakebench", "new")))

    def test_creating_a_name_that_exists_is_refused(self):
        first = self.make("dev", "good\n")
        code, out, err = self.cli("set", "create", "fakebench", "dev", "--dataset", "all", "--units", self.file("model\n"))
        self.assertEqual((code, out), (2, ""))
        self.assertIn("exists already", err)
        self.assertEqual(sets.load("fakebench", "dev"), first)


class RenameAndDelete(Scratch):
    def test_a_rename_keeps_the_id_and_the_revisions_and_the_id_finds_it(self):
        first = self.make("dev", "good\nmodel\n", "--description", "two of them")
        self.cli("set", "update", "fakebench", "dev", "--add", "slow")
        code, out, err = self.cli("set", "rename", "fakebench", "dev", "misses")
        self.assertEqual((code, err), (0, ""))
        self.assertTrue(out.startswith("renamed dev to misses ("), out)
        self.assertIn("its id and 2 revisions kept", out)
        got = sets.load("fakebench", "misses")
        self.assertEqual((got["id"], got["description"], [r["revision"] for r in got["revisions"]]),
                         (first["id"], "two of them", [1, 2]))
        self.assertEqual(sorted(os.listdir(os.path.join(self.cat, "sets", "fakebench"))), ["misses.json"])
        self.assertEqual(sets.by_id("fakebench", first["id"], "dev")["name"], "misses")
        # another set made under the old name is another set: the id still finds the renamed one
        self.make("dev", "good\n")
        self.assertEqual(sets.by_id("fakebench", first["id"], "dev")["name"], "misses")
        self.assertIsNone(sets.by_id("fakebench", "0" * 32, "dev"))

    def test_a_name_taken_empty_or_with_a_slash_and_a_set_not_there_are_refused(self):
        dev = self.make("dev", "good\n")
        other = self.make("other", "model\n")
        for new, code, said in (("other", 2, "exists already"), ("dev", 2, "exists already"), ("", 2, "not a task set's name"),
                                ("a/b", 2, "not a task set's name"), ("..", 2, "not a task set's name")):
            with self.subTest(new=new):
                got = self.cli("set", "rename", "fakebench", "dev", new)
                self.assertEqual(got[:2], (code, ""))
                self.assertIn(said, got[2])
        got = self.cli("set", "rename", "fakebench", "nowhere", "x")
        self.assertEqual(got[:2], (3, ""))
        self.assertIn("no task set 'nowhere'", got[2])
        self.assertEqual((sets.load("fakebench", "dev"), sets.load("fakebench", "other")), (dev, other))
        self.assertEqual(sorted(os.listdir(os.path.join(self.cat, "sets", "fakebench"))), ["dev.json", "other.json"])

    def test_a_delete_removes_the_file_and_says_what_it_held(self):
        self.make("dev", "good\nmodel\n")
        self.cli("set", "update", "fakebench", "dev", "--add", "slow")
        path = sets.path_of("fakebench", "dev")
        code, out, err = self.cli("set", "delete", "fakebench", "dev")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out.strip(), f"deleted {path}: dev, 3 units, 2 revisions; jobs that ran it keep their record")
        self.assertFalse(os.path.exists(path))
        code, out, err = self.cli("set", "delete", "fakebench", "dev")
        self.assertEqual((code, out), (3, ""))
        self.assertIn("no task set 'dev'", err)

    def test_a_delete_of_a_link_removes_the_link_and_not_the_set_it_leads_to(self):
        self.make("dev", "good\n")
        folder = os.path.join(self.cat, "sets", "fakebench")
        os.symlink(os.path.join(folder, "dev.json"), os.path.join(folder, "alias.json"))
        code, out, err = self.cli("set", "delete", "fakebench", "alias")
        self.assertEqual(code, 0, err)
        self.assertIn("could not read", out)
        self.assertEqual(sorted(os.listdir(folder)), ["dev.json"])
        self.assertEqual(sets.revision(sets.load("fakebench", "dev"))["units"], ["good"])

    def test_an_entry_of_the_earlier_format_is_refused_plainly_and_can_be_deleted(self):
        entry = self.make("old", "good\n")
        path = sets.path_of("fakebench", "old")
        with open(path, "w") as f:
            json.dump(dict(entry, schema=sets.SCHEMA, benchmark_options={"mode": "hard"}, setup="yours"), f)
        with self.assertRaises(sets.Earlier) as refused:
            sets.load("fakebench", "old")
        self.assertIn(path, str(refused.exception))
        said = sets.plain(refused.exception, "fakebench", "old")
        self.assertEqual(said, "The task set old is of an earlier format, which kept a run's options: delete it and make it again.")
        self.assertNotIn(self.tmp, said)
        code, out, err = self.cli("set", "rename", "fakebench", "old", "new")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("earlier format", err)
        code, out, err = self.cli("set", "delete", "fakebench", "old")
        self.assertEqual((code, out.strip(), err), (0, f"deleted {path}: an entry this version could not read", ""))
        self.assertFalse(os.path.exists(path))


class FromAJob(Scratch):
    def job(self, name="graded"):
        """A job as a run writes it: good passed, wrong failed, slow rate limited, a unit whose id has a slash passed."""
        d = os.path.join(self.jobs, name)
        os.makedirs(d)
        other = dict(definitions.control(self.b, "nop"), _kind="control")
        units = ["good", "wrong", "slow", "group/model"]
        job.write_config(d, job=name, bench=self.b, other=other, model="", provider="openai", dataset="all", units=units,
                         concurrency=1, granted=False, bench_options={"mode": "hard"})
        ends = {"good": ({"reward": 1.0}, None), "wrong": ({"reward": 0.0}, None), "slow": (None, ("RateLimitError", "429")),
                "group/model": ({"reward": 1.0}, None)}
        results = []
        for place, u in enumerate(units):
            score, exc = ends[u]
            trial = os.path.join(d, f"{u.rpartition('/')[2]}__u{place:04d}")
            os.makedirs(trial)
            results.append(job.write_trial(trial, job=name, bench=self.b, other=other, model="", unit=u, t0=1.0, t1=2.0,
                                           produce=(1.0, 2.0), score=score, exception=exc, calls=[], trajectory=None))
        job.write_result(d, name, 1.0, 3.0, results, len(units))
        return d

    def test_outcome_takes_the_trials_of_those_outcomes_by_their_full_unit_ids(self):
        d = self.job()
        for name, outcome, want in (("passed", "passed", ["good", "group/model"]), ("misses", "failed,infra", ["wrong", "slow"]),
                                    ("limited", "rate_limited", ["slow"])):
            with self.subTest(outcome=outcome):
                code, out, err = self.cli("set", "create", "fakebench", name, "--from-job", d, "--outcome", outcome)
                self.assertEqual(code, 0, err)
                entry = sets.load("fakebench", name)
                self.assertEqual(sets.revision(entry)["units"], want)
                self.assertEqual(entry["dataset"], "all")
                self.assertNotIn("benchmark_options", entry)       # the job ran under mode=hard; the set holds none
                self.assertEqual(sets.revision(entry)["hash"], sets.content_hash("fakebench", "all", want))
                self.assertIn(f"outcomes {outcome}", sets.revision(entry)["note"])

    def test_unit_takes_the_ones_named(self):
        d = self.job()
        code, out, err = self.cli("set", "create", "fakebench", "two", "--from-job", d, "--unit", "group/model", "--unit", "wrong")
        self.assertEqual(code, 0, err)
        self.assertEqual(sets.revision(sets.load("fakebench", "two"))["units"], ["wrong", "group/model"])
        code, out, err = self.cli("set", "create", "fakebench", "none", "--from-job", d, "--unit", "model")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("no trial of model", err)                       # the trial's name is not its unit's id
        code, out, err = self.cli("set", "create", "fakebench", "bad", "--from-job", d, "--outcome", "sideways")
        self.assertEqual(code, 2)
        self.assertIn("--outcome sideways", err)


class Import(Scratch):
    def test_an_entry_comes_in_from_its_file_and_a_conflicting_revision_is_refused(self):
        entry = self.make("dev", "good\nmodel\n")
        self.cli("set", "update", "fakebench", "dev", "--add", "slow")
        exported = os.path.join(self.tmp, "dev.json")
        shutil.copy(sets.path_of("fakebench", "dev"), exported)
        elsewhere = os.path.join(self.tmp, "elsewhere")
        with mock.patch.dict(os.environ, {"NULLIUS_CATALOGUE": elsewhere}):
            code, out, err = self.cli("set", "import", exported)
            self.assertEqual(code, 0, err)
            self.assertEqual(read(sets.path_of("fakebench", "dev")), read(exported))
            code, out, err = self.cli("set", "import", exported)
            self.assertEqual(code, 0, err)
            self.assertIn("here already", out)
        # here, the same id with a revision 2 of another list
        other = read(exported)
        other["revisions"][1]["units"] = ["good", "wrong"]
        other["revisions"][1]["hash"] = sets.content_hash("fakebench", "all", ["good", "wrong"])
        with open(exported, "w") as f:
            json.dump(other, f)
        code, out, err = self.cli("set", "import", exported)
        self.assertEqual((code, out), (2, ""))
        self.assertIn("the same id and revision with another hash", err)
        self.assertEqual(sets.load("fakebench", "dev")["id"], entry["id"])
        self.assertEqual(sets.revision(sets.load("fakebench", "dev"))["units"], ["good", "model", "slow"])

    def test_import_checks_every_consumed_field_before_storing_an_entry_or_job_record(self):
        entry = dict(self.make("dev", "good\n"), schema=sets.SCHEMA)
        cases = [("entry", k, None, True) for k in ("id", "name", "description", "benchmark", "dataset")]
        cases += [("revision", k, None, True) for k in sets.REVISION]
        cases += [("entry", k, v, False) for k, v in (
            ("id", 1), ("name", []), ("description", {}), ("benchmark", False), ("dataset", {}),
            ("benchmark_options", {}), ("setup", "yours"))]
        cases += [("revision", k, v, False) for k, v in (
            ("revision", True), ("date", None), ("date", []), ("date", ""), ("note", None),
            ("note", {}), ("units", [1]), ("units", [{}]), ("units", "good"), ("hash", []))]
        rec = sets.record(entry, sets.revision(entry))
        jobs = [(k, None, True) for k in sets.RECORD]
        jobs += [(k, v, False) for k, v in (("id", 1), ("name", []), ("revision", True), ("revision", 0),
                                            ("units", [1]), ("units", "good"), ("units", []), ("hash", []),
                                            ("hash", "0" * 64))]
        source_job = os.path.join(self.tmp, "source-job")
        os.makedirs(source_job)
        for index, (section, field, value, missing) in enumerate(cases + [("job",) + c for c in jobs]):
            elsewhere = os.path.join(self.tmp, f"elsewhere-{index}")
            with self.subTest(section=section, field=field, value=value, missing=missing):
                if section == "job":
                    got = dict(rec)
                    target = got
                else:
                    got = copy.deepcopy(entry)
                    target = got if section == "entry" else got["revisions"][0]
                if missing:
                    del target[field]
                else:
                    target[field] = value
                if section == "job":
                    self.file(json.dumps({"runner": "standard", "datasets": [{"name": "fakebench/all"}],
                                          "standard": {"benchmark": "fakebench", "task_set": got}}),
                              "source-job/config.json")
                    path = source_job
                else:
                    path = self.file(json.dumps(got), "incoming.json")
                with mock.patch.dict(os.environ, {"NULLIUS_CATALOGUE": elsewhere}):
                    code, out, err = self.cli("set", "import", path)
                self.assertEqual((code, out), (2, ""), err)
                self.assertTrue(err.startswith("refused: "), err)
                self.assertFalse(os.path.exists(elsewhere))

    def test_an_entry_comes_in_from_a_job_that_ran_it(self):
        entry = self.make("dev", "model\ngood\n", "--description", "two of them")
        code, out, err = self.cli("run", "fakebench", "nop", "--jobs", self.jobs, "--set", "dev", "--bench-option", "mode=hard",
                                  "--name", "ran")
        self.assertEqual(code, 0, out + err)
        elsewhere = os.path.join(self.tmp, "elsewhere")
        with mock.patch.dict(os.environ, {"NULLIUS_CATALOGUE": elsewhere}):
            code, out, err = self.cli("set", "import", os.path.join(self.jobs, "ran"))
            self.assertEqual(code, 0, err)
            got = sets.load("fakebench", "dev")
            # the record holds the set's id, name, revision, hash and units: the rest is the job's or the import's
            self.assertEqual({k: got[k] for k in ("id", "name", "benchmark", "dataset", "description")},
                             {**{k: entry[k] for k in ("id", "name", "benchmark", "dataset")}, "description": ""})
            self.assertEqual([{k: r[k] for k in ("revision", "units", "hash")} for r in got["revisions"]],
                             [{k: r[k] for k in ("revision", "units", "hash")} for r in entry["revisions"]])
            self.assertEqual(got["revisions"][0]["note"], "from the job ran")
            # the entry exported from here then adds nothing there: the same id, revision and hash
            code, out, err = self.cli("set", "import", sets.path_of("fakebench", "dev", self.cat))
            self.assertEqual(code, 0, err)
            self.assertIn("here already", out)
        code, out, err = self.cli("set", "import", self.jobs)
        self.assertEqual((code, out), (3, ""))
        self.assertIn("no job", err)
        # another set under the name: refused, the entry here unchanged
        shutil.rmtree(os.path.join(self.cat, "sets"))
        self.make("dev", "model\ngood\n")
        code, out, err = self.cli("set", "import", os.path.join(self.jobs, "ran"))
        self.assertEqual(code, 2)
        self.assertIn("another id", err)


class Running(Scratch):
    def run_cli(self, *args):
        return self.cli("run", "fakebench", "nop", "--jobs", self.jobs, "-j", "2", *args)

    def test_an_empty_set_is_refused_before_any_unit(self):
        code, out, err = self.run_cli("--set", "", "--name", "empty")
        self.assertEqual(code, 2, out + err)
        self.assertIn("not a task set's name", out)
        self.assertFalse(os.path.exists(self.jobs))

    def test_each_contradiction_is_refused_before_any_unit_with_a_plain_message(self):
        self.make("dev", "good\nmodel\n")
        for args, said in ((["--dataset", "responding"], "--dataset responding: the task set dev is cut from the dataset all"),
                           (["--units", self.file("good\n")], "the set lists the units, give one or the other"),
                           (["--set", "nowhere"], "no task set 'nowhere' of fakebench")):
            with self.subTest(args=args):
                code, out, err = self.run_cli(*(["--set", "dev"] if "--set" not in args else []), *args)
                self.assertEqual(code, 2, out + err)
                self.assertEqual(len(out.strip().splitlines()), 1, out)
                self.assertTrue(out.startswith("refused: "), out)
                self.assertIn(said, out)
                self.assertFalse(os.path.exists(self.jobs))           # no job was begun
        code, out, err = self.run_cli("--set", "dev", "--dataset", "all", "--bench-option", "mode=hard", "--name", "agrees")
        self.assertEqual(code, 0, out + err)

    def test_two_runs_under_two_values_of_an_option_both_start_and_record_the_same_set(self):
        entry = self.make("dev", "good\nwrong\n")
        rev = sets.revision(entry)
        records = []
        for value in ("plain", "hard"):
            with self.subTest(value=value):
                code, out, err = self.run_cli("--set", "dev", "--bench-option", f"mode={value}", "--name", value)
                self.assertEqual(code, 0, out + err)
                self.assertEqual(self.trials(os.path.join(self.jobs, value)), ["good__u0000", "wrong__u0001"])
                cfg = read(os.path.join(self.jobs, value, "config.json"))
                self.assertEqual(cfg["standard"]["benchmark_options"], {"mode": value})
                records.append(cfg["standard"]["task_set"])
        self.assertEqual(records[0], records[1])
        self.assertEqual({k: records[0][k] for k in ("id", "revision", "hash")},
                         {"id": entry["id"], "revision": 1, "hash": rev["hash"]})
        self.assertEqual(rev["hash"], sets.content_hash("fakebench", "all", ["good", "wrong"]))   # no option in it

    def listing_by_mode(self):
        """The fake benchmark whose driver leaves slow and model out of its listing under mode=hard."""
        here = os.path.join(self.tmp, "fakebench")
        shutil.copytree(self.b["_here"], here)
        path = os.path.join(here, "driver.py")
        with open(path) as f:
            text = f.read()
        text = text.replace('            units.remove("slow")\n',
                            '            units.remove("slow")\n'
                            '        if os.environ.get("NULLIUS_OPTION_MODE") == "hard":\n'
                            '            units = [u for u in units if u not in ("slow", "model")]\n')
        with open(path, "w") as f:
            f.write(text)
        self.b["_here"] = here

    def test_ids_the_driver_does_not_list_under_the_runs_options_refuse_it_before_any_unit(self):
        self.listing_by_mode()
        self.make("dev", "good\nslow\nmodel\nwrong\n")
        code, out, err = self.run_cli("--set", "dev", "--bench-option", "mode=hard", "--name", "hard")
        self.assertEqual(code, 2, out + err)
        self.assertIn("2 of the 4 ids of the task set dev@1 are not listed by Fake bench's dataset all under the run's "
                      "options (mode=hard): slow, model", out)
        job_dir = os.path.join(self.jobs, "hard")
        self.assertEqual(self.trials(job_dir), [])
        self.assertFalse(os.path.exists(os.path.join(job_dir, "config.json")))
        code, out, err = self.run_cli("--set", "dev", "--bench-option", "mode=plain", "--name", "plain")
        self.assertEqual(code, 0, out + err)

    def test_the_message_names_the_first_five_ids_not_listed(self):
        self.make("dev", "good\n" + "".join(f"gone{i}\n" for i in range(7)))
        record = sets.record(sets.load("fakebench", "dev"), sets.revision(sets.load("fakebench", "dev")))
        r = run.Run(self.b, definitions.control(self.b, "nop"), model="", provider="openai", jobs_root=self.jobs, env={},
                    say=lambda s: None, task_set=record)
        with self.assertRaises(run.Refused) as e:
            r.start()
        self.assertIn("7 of the 8 ids of the task set dev@1 are not listed by Fake bench's dataset all under the run's "
                      "options (mode=plain): gone0, gone1, gone2, gone3, gone4, ...", str(e.exception))
        self.assertEqual(self.trials(r.job_dir), [])
        self.assertFalse(os.path.exists(os.path.join(r.job_dir, "config.json")))

    def test_the_units_run_are_the_sets_and_limit_and_shard_apply_after(self):
        self.make("dev", "model\nwrong\nslow\n")
        code, out, err = self.run_cli("--set", "dev", "--name", "whole")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.trials(out.strip().splitlines()[-1]), ["model__u0003", "slow__u0002", "wrong__u0001"])
        # the set, then the shard, then the limit: shard 1 of 2 holds wrong and model of the three, and the limit keeps wrong
        self.assertEqual([shard.of(u, 2) for u in DATASET], [1, 1, 2, 1])
        code, out, err = self.run_cli("--set", "dev", "--shard", "1/2", "--limit", "1", "--name", "part-shard-1of2")
        self.assertEqual(code, 0, out + err)
        part = out.strip().splitlines()[-1]
        self.assertEqual(self.trials(part), ["wrong__u0001"])
        rec = read(os.path.join(part, "config.json"))["standard"]["shard"]
        self.assertEqual((rec["units_total"], rec["planned"], rec["limit"]), (3, ["wrong"], 1))
        with open(os.path.join(part, feed.FEED)) as f:
            self.assertEqual(next(e for e in map(json.loads, f) if e["event"] == "job.start")["tasks"], ["wrong"])

    def test_the_config_records_the_set_its_revision_and_the_runs_options(self):
        self.b["datasets"] = [*self.b["datasets"], {"id": "responding", "units": 3}]
        self.make("dev", "good\nmodel\n", "--dataset", "responding")
        self.cli("set", "update", "fakebench", "dev", "--add", "slow")
        code, out, err = self.run_cli("--set", "dev@1", "--bench-option", "mode=hard", "--name", "first")
        self.assertEqual(code, 0, out + err)
        cfg = read(os.path.join(self.jobs, "first", "config.json"))
        entry = sets.load("fakebench", "dev")
        self.assertEqual(cfg["standard"]["task_set"], sets.record(entry, sets.revision(entry, 1)))
        self.assertEqual(cfg["standard"]["task_set"],
                         {"id": entry["id"], "name": "dev", "revision": 1, "hash": entry["revisions"][0]["hash"],
                          "units": ["good", "model"]})
        self.assertEqual(cfg["standard"]["benchmark_options"], {"mode": "hard"})
        self.assertNotIn("units_file", cfg["standard"])
        self.assertEqual(cfg["datasets"], [{"name": "fakebench/responding", "n_tasks": 2}])

    def test_a_run_without_a_set_records_none(self):
        code, out, err = self.run_cli("--name", "plain")
        self.assertEqual(code, 0, out + err)
        cfg = read(os.path.join(self.jobs, "plain", "config.json"))
        self.assertEqual(sorted(cfg["standard"]), ["backend", "benchmark", "benchmark_options", "control", "kind", "needs",
                                                   "permissions_granted", "plugged", "provider", "retries", "unsandboxed"])


class Listing(Scratch):
    def test_list_and_show_say_each_set_and_its_revisions(self):
        self.make("dev", "good\nmodel\n", "--description", "two of them")
        self.make("all-four", "\n".join(DATASET))
        code, out, err = self.cli("set", "list", "--json")
        self.assertEqual(code, 0, err)
        rows = json.loads(out)
        self.assertEqual([(r["name"], r["revision"], r["units"]) for r in rows], [("all-four", 1, 4), ("dev", 1, 2)])
        code, out, err = self.cli("set", "list", "fakebench")
        self.assertEqual(code, 0, err)
        self.assertIn("two of them", out)
        code, out, err = self.cli("set", "show", "fakebench", "dev")
        self.assertEqual(code, 0, err)
        self.assertTrue(out.startswith("dev: 2 units of fakebench all, revision 1 of 1"), out)
        self.assertEqual(out.strip().splitlines()[-2:], ["  good", "  model"])
        code, out, err = self.cli("set", "list", "otherbench", "--json")
        self.assertEqual((code, json.loads(out)), (0, []))


if __name__ == "__main__":
    unittest.main()
