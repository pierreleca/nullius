"""Chosen tasks of an ended job run again in that job (``standard/rerun.py``),
against the fake benchmark and agent: the new try takes the trial's place,
the earlier tries stay readable, the job's result is recomputed and each
re-run recorded. No key, no network: the provider is a local fake endpoint."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from nullius.harbor import feed
from nullius.runs import classify, live, parse
from nullius.standard import acp, definitions, install, merge, pace as pacing, rerun, run
from tests import test_standard_run as base

ROOT = base.ROOT
UNITS = {"name": "units.txt", "sha256": "0" * 64, "units": ["good", "wrong", "model"]}
PRICES = {"m-1": {"input": 1.0, "output": 10.0, "max_output": 10}}     # a call: 11 in, 3 out, 0.000041 USD
CALL = 0.000041


def digest(root) -> dict:
    root = Path(root)
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def said(text):
    """The fake agent's prompt, turned into another for the re-run's tries (``acp.Client.prompt``)."""
    real = acp.Client.prompt

    def prompt(client, sid, given, timeout=600):
        for want in text.get(given, [given]):
            out = real(client, sid, want, timeout=timeout)
        return out
    return mock.patch.object(acp.Client, "prompt", prompt)


class RerunRecords(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.root = self.tmp / "catalogue"
        dest = self.root / "benchmarks/fakebench"
        shutil.copytree(Path(ROOT) / "benchmarks/fakebench", dest)
        spec = json.loads((dest / "standard.json").read_text())
        spec["controls"] = {"nop": {"source": "nullius", "from": "Empty answer."}}
        (dest / "standard.json").write_text(json.dumps(spec))
        self.bench = definitions.get("benchmark", "fakebench", str(self.root))
        self.other = definitions.control(self.bench, "nop")
        self.enterContext(mock.patch.object(install, "HOME", str(self.tmp / "installs")))

    def job(self):
        return Path(run.run(self.bench, self.other, model="", provider="custom", jobs_root=str(self.tmp),
                            name="job", concurrency=1, units_file=dict(UNITS, units=["good", "wrong"]),
                            env={}, say=lambda s: None))

    def test_a_linked_archive_is_refused_before_any_trial_moves(self):
        path = self.job()
        outside = self.tmp / "outside"
        outside.mkdir()
        (path / "retried").symlink_to(outside, target_is_directory=True)
        before = digest(path)
        for exists in (True, False):
            with self.subTest(target_exists=exists):
                if not exists:
                    outside.rmdir()
                with self.assertRaisesRegex(run.Refused, "retried.*link"):
                    rerun.rerun(str(path), ["good", "wrong"], root=str(self.root), env={}, say=lambda s: None)
                self.assertEqual(digest(path), before)
                self.assertEqual(list(outside.iterdir()) if outside.exists() else [], [])
                self.assertEqual(outside.exists(), exists)
                self.assertTrue((path / "retried").is_symlink())

    def test_stop_emits_cancellation_before_restoring_the_previous_try(self):
        path = self.job()
        name = "good__u0000"
        result = path / name / "result.json"
        original = json.loads(result.read_text())
        original["agent_result"]["cost_usd"] = 0.25
        result.write_text(json.dumps(original))
        writer = feed.Writer(str(path / "events.jsonl"), "job")
        writer.seq = rerun._last_seq(str(path / "events.jsonl"))
        writer.emit("trial.end", name, outcome="unresolved", reward=0.0, cost_usd=0.25)

        def stop(r, *args):
            r._stopped.set()
            raise OSError("the control was stopped")

        with mock.patch.object(run.Run, "control_answer", stop):
            rerun.rerun(str(path), ["good"], root=str(self.root), env={}, say=lambda s: None)
        events = live.read_events(path / "events.jsonl").events
        ends = [e.data for e in events if e.event == "trial.end" and e.trial == name]
        self.assertEqual([e["outcome"] for e in ends[-2:]], ["cancelled", "unresolved"])
        self.assertTrue(ends[-1]["back"])
        self.assertEqual(json.loads(result.read_text()), original)
        self.assertFalse((path / "retried").exists())
        board = live.fold(events).snapshot()
        card = next(t for t in board["trials"] if t["trial"] == name)
        self.assertEqual((board["state"], card["state"], card["outcome"]), ("finished", "done", "unresolved"))
        self.assertEqual((card.get("attempt", 1), board["totals"]["cost_usd"]), (1, 0.25))


class RerunTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.up = ThreadingHTTPServer(("127.0.0.1", 0), base.Echo)
        threading.Thread(target=cls.up.serve_forever, daemon=True).start()
        cls.env = {"MODEL_API_URL": f"http://127.0.0.1:{cls.up.server_address[1]}/v1", "MODEL_API_KEY": "sk-real"}

    @classmethod
    def tearDownClass(cls):
        cls.up.shutdown()

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._home = install.HOME
        install.HOME = os.path.join(self.tmp, "home")
        self.addCleanup(setattr, install, "HOME", self._home)
        self.addCleanup(shutil.rmtree, self.tmp, True)
        base.Echo.status, base.Echo.refused, base.Echo.limited, base.Echo.retry_after, base.Echo.listed = 200, None, 0, None, None
        self.addCleanup(setattr, pacing, "FIRST", pacing.FIRST)
        pacing.FIRST = 0.5

    def job(self, name="job", **kw) -> str:
        b, o = definitions.get("benchmark", "fakebench", ROOT), definitions.get("agent", "fake", ROOT)
        kw = dict(dict(units_file=UNITS, prices=PRICES, concurrency=2), **kw)
        return run.run(b, o, model="m-1", provider="custom", jobs_root=self.tmp, env=self.env, say=lambda s: None,
                       unsandboxed=True, name=name, **kw)

    def again(self, job_dir, ids, **kw) -> dict:
        return rerun.rerun(job_dir, ids, env=self.env, say=lambda s: None, root=ROOT, **kw)

    def read(self, *path) -> dict:
        with open(os.path.join(*path)) as f:
            return json.load(f)

    def trials(self, job_dir):
        j = parse.parse_job(job_dir)
        return {t.trial_name.split("__")[0]: t for t in j.trials}, j

    def test_a_task_that_failed_then_passes(self):
        job_dir = self.job()
        before, j0 = self.trials(job_dir)
        first, config = self.read(job_dir, "result.json"), Path(job_dir, "config.json").read_bytes()
        self.assertEqual(before["model"].outcome, classify.FAILED_VERIFICATION)
        with said({"model hi": ["model hi", "write answer.txt 42"]}):     # a paid call, then the answer
            got = self.again(job_dir, ["model"], reason="the provider failed")
        name = before["model"].trial_name
        after, j1 = self.trials(job_dir)
        self.assertEqual(after["model"].outcome, classify.RESOLVED)
        self.assertEqual((j0.n_resolved, j1.n_resolved), (1, 2))
        self.assertEqual(len(j1.trials), 3)                                  # the try before is not a trial of its own
        self.assertEqual([(n, t.trial_name, t.outcome) for n, t in parse.tries(after["model"])],
                         [(1, f"{name}.try-1", classify.FAILED_VERIFICATION), (2, name, classify.RESOLVED)])
        self.assertAlmostEqual(j0.total_cost_usd, CALL)
        self.assertAlmostEqual(j1.total_cost_usd, 2 * CALL)                 # the job's cost: every try's
        res = self.read(job_dir, "result.json")
        self.assertEqual((res["started_at"], res["finished_at"], res["n_total_trials"]),
                         (first["started_at"], first["finished_at"], first["n_total_trials"]))
        self.assertEqual(res["standard"]["passed"], 2)
        self.assertEqual(res["stats"]["n_retries"], 1)
        self.assertEqual(Path(job_dir, "config.json").read_bytes(), config)      # the settings are the job's
        (entry,) = res["reruns"]
        self.assertEqual(entry["reason"], "the provider failed")
        self.assertEqual(entry["trials"], [name])
        self.assertEqual(entry["replaced"], [{"trial": name, "unit": "model", "try": 1, "was": {"reward": 0.0}, "now": {"reward": 1.0}}])
        self.assertIsNone(entry["max_usd"])
        self.assertAlmostEqual(entry["spent_usd"], CALL)
        self.assertNotIn("stopped", entry)
        self.assertEqual(got["replaced"], entry["replaced"])
        mark = self.read(job_dir, name, "result.json")["standard"]
        self.assertEqual((mark["retries"], mark["rerun"]["by"], mark["rerun"]["reason"], mark["rerun"]["replaced"]),
                         (1, "hand", "the provider failed", {"reward": 0.0}))
        self.assertEqual(mark["rerun"]["at"], entry["at"])
        self.assertNotIn("rerun", self.read(job_dir, "retried", f"{name}.try-1", "result.json")["standard"])
        # the feed goes on numbered, and its board reads the job ended, the trial at its second attempt
        with open(os.path.join(job_dir, "events.jsonl")) as f:
            seqs = [json.loads(line)["seq"] for line in f]
        self.assertEqual(seqs, list(range(1, len(seqs) + 1)))
        board = live.load(live.feed_path(job_dir)).snapshot()
        self.assertEqual(board["state"], "finished")
        card = next(t for t in board["trials"] if t["trial"] == name)
        self.assertEqual((card["outcome"], card["attempt"], card["retried"]), ("resolved", 2, ["unresolved"]))
        self.assertFalse(rerun.running(job_dir))

    def test_a_task_that_passed_then_fails(self):
        job_dir = self.job()
        before, j0 = self.trials(job_dir)
        with said({"write answer.txt 42": ["write answer.txt 41"]}):
            self.again(job_dir, ["good"])
        after, j1 = self.trials(job_dir)
        self.assertEqual((before["good"].outcome, after["good"].outcome), (classify.RESOLVED, classify.FAILED_VERIFICATION))
        self.assertEqual((j0.n_resolved, j1.n_resolved), (1, 0))
        res = self.read(job_dir, "result.json")
        self.assertEqual(res["standard"]["passed"], 0)
        self.assertEqual(res["reruns"][0]["replaced"][0]["was"], {"reward": 1.0})
        self.assertNotIn("reason", res["reruns"][0])
        self.assertEqual([t.outcome for _, t in parse.tries(after["good"])], [classify.RESOLVED, classify.FAILED_VERIFICATION])

    def test_two_tasks_one_of_them_twice_in_a_row(self):
        job_dir = self.job()
        before, _ = self.trials(job_dir)
        with said({"write answer.txt 41": ["write answer.txt 42"]}):
            self.again(job_dir, ["good", "wrong", "good"])                  # a task named twice runs once
        self.again(job_dir, ["wrong"], reason="once more")
        after, j = self.trials(job_dir)
        wrong, good = before["wrong"].trial_name, before["good"].trial_name
        self.assertEqual(sorted(os.listdir(os.path.join(job_dir, "retried"))),
                         sorted([f"{good}.try-1", f"{wrong}.try-1", f"{wrong}.try-2"]))
        self.assertEqual([(n, t.trial_name, t.outcome) for n, t in parse.tries(after["wrong"])],
                         [(1, f"{wrong}.try-1", classify.FAILED_VERIFICATION), (2, f"{wrong}.try-2", classify.RESOLVED),
                          (3, wrong, classify.FAILED_VERIFICATION)])
        self.assertEqual([t.n_retries for _, t in parse.tries(after["wrong"])], [0, 1, 2])
        reruns = self.read(job_dir, "result.json")["reruns"]
        self.assertEqual([(e["trials"], [x["try"] for x in e["replaced"]]) for e in reruns], [([good, wrong], [1, 1]), ([wrong], [2])])
        self.assertEqual(reruns[1]["replaced"][0]["was"], {"reward": 1.0})
        self.assertEqual(self.read(job_dir, "result.json")["stats"]["n_retries"], 3)
        self.assertEqual(j.n_retries, 3)

    def test_a_stop_during_a_rerun_puts_the_try_before_back(self):
        """Stop is a SystemExit in the CLI, landing on the re-run's own thread: whether the try was waiting or
        running, the trial is the one it was, and the job reads as before."""
        job_dir = self.job()
        before, j0 = self.trials(job_dir)
        trials = {t.trial_name: digest(os.path.join(job_dir, t.trial_name)) for t in j0.trials}
        real = run.Run.unit
        for where in ("waiting", "running"):
            with self.subTest(where=where):
                def unit(r, i, u, rr=1, retry=0, where=where):
                    if retry:
                        if where == "running":       # broken off as it ran: a folder, no result
                            os.makedirs(os.path.join(r.job_dir, r.trial_name(i, u, rr), "workspace"))
                        raise SystemExit(143)
                    return real(r, i, u, rr, retry)
                with mock.patch.object(run.Run, "unit", unit), self.assertRaises(SystemExit):
                    self.again(job_dir, ["model"])
                after, j1 = self.trials(job_dir)
                self.assertEqual({t.trial_name: digest(os.path.join(job_dir, t.trial_name)) for t in j1.trials}, trials)
                self.assertFalse(os.path.exists(os.path.join(job_dir, "retried")))
                self.assertEqual([(t.trial_name, t.outcome, t.n_retries) for t in j1.trials],
                                 [(t.trial_name, t.outcome, t.n_retries) for t in j0.trials])
                self.assertAlmostEqual(j1.total_cost_usd, j0.total_cost_usd)
                self.assertEqual(self.read(job_dir, "result.json")["reruns"][-1]["replaced"], [])
                self.assertTrue(self.read(job_dir, "result.json")["reruns"][-1]["stopped"])
                self.assertFalse(rerun.running(job_dir))

    def test_a_stop_after_the_try_began_leaves_the_board_on_the_try_before(self):
        """The try had said its phase on the feed when the Stop came: the try before is back on disk, and the feed
        says so, so the board reads the card's earlier outcome at its first attempt, and the job ended."""
        job_dir = self.job()
        before, _ = self.trials(job_dir)
        name = before["model"].trial_name
        real = run.Run.unit

        def unit(r, i, u, rr=1, retry=0):
            if retry:
                r.feed.emit("trial.phase", r.trial_name(i, u, rr), phase="agent_run")
                raise SystemExit(143)
            return real(r, i, u, rr, retry)
        with mock.patch.object(run.Run, "unit", unit), self.assertRaises(SystemExit):
            self.again(job_dir, ["model"])
        board = live.load(live.feed_path(job_dir)).snapshot()
        self.assertEqual(board["state"], "finished")
        card = next(t for t in board["trials"] if t["trial"] == name)
        self.assertEqual((card["state"], card["outcome"], card["reward"]), ("done", "unresolved", 0.0))
        self.assertNotIn("attempt", card)
        self.assertAlmostEqual(board["totals"]["cost_usd"], CALL)            # the try before, counted once

    def test_each_refusal_moves_nothing(self):
        job_dir = self.job()
        real = definitions.get

        def newer(kind, ref, root=None):         # the agent's definition moved on to another version since the run
            got = real(kind, ref, root)
            return dict(got, upstream={"version": "0.2"}) if got and kind == "agent" else got
        whole = digest(job_dir)
        cases = [
            ("no task", [], {}, "no task named"),
            ("an id it does not hold", ["model", "nosuch"], {}, "holds no trial or unit nosuch"),
            ("a changed agent", ["model"], {"get": newer}, "the job ran fake with no version, and fake resolves to 0.2 now"),
        ]
        for what, ids, kw, words in cases:
            with self.subTest(what), mock.patch.object(definitions, "get", kw.get("get", real)):
                with self.assertRaises(run.Refused) as e:
                    self.again(job_dir, ids)
                self.assertIn(words, str(e.exception))
                self.assertEqual(digest(job_dir), whole)
        with self.subTest("a re-run under way"):
            held = rerun._hold(job_dir)
            try:
                self.assertTrue(rerun.running(job_dir))
                whole = digest(job_dir)
                with self.assertRaises(run.Refused) as e:
                    self.again(job_dir, ["model"])
                self.assertIn("is still running", str(e.exception))
                self.assertEqual(digest(job_dir), whole)
            finally:
                held.close()
        with self.subTest("a job that has not ended"):
            os.rename(os.path.join(job_dir, "result.json"), os.path.join(self.tmp, "result.json"))
            whole = digest(job_dir)
            with self.assertRaises(run.Refused) as e:
                self.again(job_dir, ["model"])
            self.assertIn("is still running", str(e.exception))
            self.assertEqual(digest(job_dir), whole)

    def test_a_merged_job_reruns_in_place_and_leaves_its_shards_as_they_were(self):
        """``merge`` hard-links the shards' files into the merged job: a re-run renames a trial's folder and writes
        its new try into a new one, so no shard's file changes. A merge of shards that differed is refused."""
        shards = [self.job(f"shard-{i}", shard=(i, 2), launch="one") for i in (1, 2)]
        before = {s: digest(s) for s in shards}
        out = os.path.join(self.tmp, "merged")
        merge.merge(shards, out)
        name = next(t for t in merge._trials(out) if t.startswith("wrong__"))
        with said({"write answer.txt 41": ["write answer.txt 42"]}):
            self.again(out, ["wrong"])
        self.assertEqual({s: digest(s) for s in shards}, before)
        self.assertEqual(self.read(out, name, "result.json")["standard"]["retries"], 1)
        self.assertEqual(self.read(out, "retried", f"{name}.try-1", "result.json")["standard"]["shard_job"],
                         next(s for s in ("shard-1", "shard-2") if os.path.isdir(os.path.join(self.tmp, s, name))))
        self.assertEqual(self.read(out, "result.json")["standard"]["passed"], 2)
        forced = os.path.join(self.tmp, "forced")
        merge.merge([shards[0], self.job("other", shard=(2, 2), launch="another")], forced, force=True)
        whole = digest(forced)
        with self.assertRaises(run.Refused) as e:
            self.again(forced, ["good"])
        self.assertIn("merges shards that differed", str(e.exception))
        self.assertEqual(digest(forced), whole)

    def test_a_rerun_stops_at_its_cap_and_records_its_spend(self):
        job_dir = self.job(concurrency=1)
        before, _ = self.trials(job_dir)
        got = self.again(job_dir, ["model", "good"], max_usd=0.00001, concurrency=1)
        after, _ = self.trials(job_dir)
        self.assertEqual(after["model"].outcome, classify.SPENDING_CAP)          # cut off at the re-run's cap
        self.assertEqual(after["good"].n_retries, 0)                         # never begun: it stands as it was
        self.assertEqual(got["max_usd"], 0.00001)
        self.assertEqual(got["spent_usd"], 0.0)
        self.assertTrue(got["stopped"])
        self.assertEqual([x["trial"] for x in got["replaced"]], [before["model"].trial_name])
        self.assertIsNone(self.read(job_dir, "result.json").get("budget"))   # the first run had none: the re-run's is its own
        self.assertTrue(os.path.isfile(os.path.join(job_dir, ".work", "rerun-1", "ledger", "ledger.json")))

    def test_a_job_never_run_again_keeps_the_result_a_run_writes(self):
        job_dir = self.job()
        self.assertEqual(sorted(self.read(job_dir, "result.json")), ["finished_at", "id", "n_total_trials", "served", "started_at", "stats"])
        self.assertFalse(os.path.exists(os.path.join(job_dir, "retried")))


if __name__ == "__main__":
    unittest.main()
