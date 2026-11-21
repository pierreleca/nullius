"""Evidence views are bounded, redacted, and confined to named trial files."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from nullius.catalogue import catalog
from nullius.runs import artifacts


class ArtifactViews(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.trial = Path(self.tmp.name) / "trial"
        self.trial.mkdir()

    def test_redacts_before_paging_and_retains_later_content(self):
        secret = "sk-abcdefgh1234567890"
        (self.trial / "result.json").write_text("a" * (artifacts.CHUNK_SIZE - 3) + " " + secret + "\n" + "z" * 9000)
        first = artifacts.read(self.trial, "result")
        second = artifacts.read(self.trial, "result", 2)
        self.assertLessEqual(len(first["text"]), artifacts.CHUNK_SIZE)
        self.assertNotIn("abcdefgh", first["text"] + second["text"])
        self.assertIn("[REDACTED]", first["text"] + second["text"])
        self.assertIn("zzz", second["text"])
        self.assertIn(secret, (self.trial / "result.json").read_text())

    def test_osworld_calls_are_available_before_a_score_and_not_duplicated_after_it(self):
        calls = self.trial / ".work/osworld/computer-calls.jsonl"
        calls.parent.mkdir(parents=True)
        calls.write_text('{"tool":"click"}\n')
        for scored in (False, True):
            with self.subTest(scored=scored):
                if scored:
                    copy = self.trial / "verifier/osworld/computer-calls.jsonl"
                    copy.parent.mkdir(parents=True)
                    copy.write_bytes(calls.read_bytes())
                records = [r for r in artifacts.listing(self.trial)
                           if r["key"].startswith("osworld-") and r["exists"]]
                self.assertEqual([r["key"] for r in records], ["osworld-runner-calls"])
                self.assertEqual(artifacts.read(self.trial, "osworld-runner-calls")["text"], calls.read_text())

    def test_unknown_keys_and_escaping_symlinks_are_refused(self):
        outside = Path(self.tmp.name) / "outside.txt"
        outside.write_text("not a trial record")
        (self.trial / "result.json").symlink_to(outside)
        for key in ("result", "../outside.txt", "verifier/report.json"):
            with self.subTest(key=key), self.assertRaises(LookupError):
                artifacts.read(self.trial, key)
        self.assertFalse(artifacts.listing(self.trial)[0]["exists"])

    def test_empty_absent_and_clamped_page_are_distinct(self):
        records = artifacts.listing(self.trial)
        self.assertTrue(all(not r["exists"] and r["size"] is None for r in records))
        (self.trial / "result.json").write_text("")
        self.assertTrue(artifacts.read(self.trial, "result", 100)["empty"])
        self.assertEqual(artifacts.read(self.trial, "result", "invalid")["page"], 1)
        self.assertEqual(artifacts.listing(self.trial)[0]["size"], 0)


    def test_the_artifact_manifest_is_listed_and_read_redacted(self):
        # Harbor's artifacts/manifest.json — the last of a trial's nine files
        # to reach a page. Listed by name like the rest, read through the
        # same redaction, and never reachable under another key.
        keys = [r["key"] for r in artifacts.listing(self.trial)]
        self.assertIn("manifest", keys)
        (self.trial / "artifacts").mkdir()
        (self.trial / "artifacts" / "manifest.json").write_text(json.dumps([
            {"source": "/logs/artifacts", "destination": "artifacts/logs/artifacts",
             "type": "directory", "status": "empty", "service": None},
            {"source": "/root/.env", "destination": "artifacts/env",
             "type": "file", "status": "ok", "service": "MODEL_API_KEY=sk-abcdefgh1234567890"},
        ]))
        record = next(r for r in artifacts.listing(self.trial) if r["key"] == "manifest")
        self.assertEqual((record["filename"], record["exists"]), ("artifacts/manifest.json", True))
        page = artifacts.read(self.trial, "manifest")
        self.assertIn('"status": "empty"', page["text"])
        self.assertIn("[REDACTED]", page["text"])
        self.assertNotIn("abcdefgh", page["text"])
        with self.assertRaises(LookupError):
            artifacts.read(self.trial, "artifacts/manifest.json")


class DeclaredRecords(unittest.TestCase):
    """A benchmark's own records, as its benchmark.json declares them: listed after Nullius's when they exist,
    read like them, and never a way out of the trial."""

    RECORDS = [{"label": "The row", "path": "verifier/own/row.json"}, {"label": "Its folder", "path": "verifier/own"},
               {"label": "Never written", "path": "verifier/later.txt"}]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.trial = Path(self.tmp.name) / "trial"
        (self.trial / "verifier" / "own").mkdir(parents=True)
        (self.trial / "verifier" / "own" / "row.json").write_text('{"seed": 7}')
        (self.trial / "verifier" / "own" / "events.jsonl").write_text("a\nb\n")
        (self.trial / "verifier" / "own" / "deeper").mkdir()
        self.outside = Path(self.tmp.name) / "outside.txt"
        self.outside.write_text("not of the trial")

    def own(self):
        return [r for r in artifacts.listing(self.trial, self.RECORDS) if r["key"] not in artifacts.FILES]

    def test_the_files_and_a_folders_files_are_listed_after_nullius_s_and_read_like_them(self):
        rows = artifacts.listing(self.trial, self.RECORDS)
        self.assertEqual([r["key"] for r in rows[:len(artifacts.FILES)]], list(artifacts.FILES))
        self.assertEqual([(r["key"], r["label"], r["part"], r["filename"], r["size"]) for r in self.own()], [
            ("r0", "The row", "", "verifier/own/row.json", 11),
            ("r1-events.jsonl", "Its folder", "events.jsonl", "verifier/own/events.jsonl", 4),
            ("r1-row.json", "Its folder", "row.json", "verifier/own/row.json", 11)])
        page = artifacts.read(self.trial, "r1-events.jsonl", 1, self.RECORDS)
        self.assertEqual((page["label"], page["filename"], page["text"]), ("Its folder", "verifier/own/events.jsonl", "a\nb\n"))
        self.assertEqual(artifacts.whole(self.trial, "r0", self.RECORDS), ('{"seed": 7}', False))
        self.assertEqual(artifacts.describe("r0", self.RECORDS), ("The row", "verifier/own/row.json"))

    def test_a_declared_record_is_redacted_as_the_others_are(self):
        (self.trial / "verifier" / "own" / "row.json").write_text("key sk-abcdefgh1234567890")
        self.assertNotIn("abcdefgh", artifacts.read(self.trial, "r0", 1, self.RECORDS)["text"])

    def test_no_key_leaves_the_record_or_the_list(self):
        for key in ("r1-..", "r1-.", "r1-../own/row.json", "r1-deeper", "r3", "r9", "r01", "r0-x", "r", "verifier/own/row.json"):
            with self.subTest(key=key), self.assertRaises((LookupError, OSError)):
                artifacts.read(self.trial, key, 1, self.RECORDS)
        with self.assertRaises(LookupError):
            artifacts.read(self.trial, "r0")                       # no record declared: no key of one

    def test_a_link_anywhere_on_the_way_is_not_followed(self):
        own = self.trial / "verifier" / "own"
        (own / "linked.txt").symlink_to(self.outside)
        (own / "inside.txt").symlink_to(own / "row.json")
        self.assertEqual([r["part"] for r in self.own()], ["", "events.jsonl", "row.json"])
        for key in ("r1-linked.txt", "r1-inside.txt"):
            with self.subTest(key=key), self.assertRaises(OSError):
                artifacts.read(self.trial, key, 1, self.RECORDS)
        (own / "row.json").unlink()
        (own / "row.json").symlink_to(self.outside)
        self.assertEqual([r["key"] for r in self.own()], ["r1-events.jsonl"])
        with self.assertRaises(OSError):
            artifacts.whole(self.trial, "r0", self.RECORDS)
        elsewhere = Path(self.tmp.name) / "elsewhere"
        (self.trial / "verifier").rename(elsewhere)
        (self.trial / "verifier").symlink_to(elsewhere)          # the verifier folder itself a link
        self.assertEqual(self.own(), [])
        with self.assertRaises(OSError):
            artifacts.whole(self.trial, "r1-events.jsonl", self.RECORDS)

    def test_a_pipe_is_neither_listed_nor_waited_on(self):
        os.mkfifo(self.trial / "verifier" / "own" / "pipe")
        records = self.RECORDS + [{"label": "A pipe", "path": "verifier/own/pipe"}]
        self.assertNotIn("r1-pipe", [r["key"] for r in artifacts.listing(self.trial, records)])
        self.assertNotIn("r3", [r["key"] for r in artifacts.listing(self.trial, records)])
        got = subprocess.run([sys.executable, "-c", "import sys; from nullius.runs import artifacts\n"
                              "try:\n artifacts.whole(sys.argv[1], 'r0', [{'label': 'p', 'path': 'verifier/own/pipe'}])\n"
                              "except LookupError:\n print('refused')", str(self.trial)],
                             stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=5,
                             env=dict(os.environ, PYTHONPATH=str(Path(artifacts.__file__).parents[2])))
        self.assertEqual(got.stdout.strip(), "refused", got.stderr)

    def test_a_folder_lists_at_most_its_bound(self):
        for i in range(5):
            (self.trial / "verifier" / "own" / f"f{i}.txt").write_text("x")
        with mock.patch.object(artifacts, "FOLDER_FILES", 3):
            self.assertEqual([r["part"] for r in self.own()], ["", "events.jsonl", "f0.txt", "f1.txt"])

    def test_the_benchmark_is_found_by_its_key_its_datasets_and_its_report_sources(self):
        good = {"label": " Its row ", "path": "verifier/own/row.json"}
        bench = catalog.Benchmark(key="bench", title="B", datasets=["bench-small"], report={"sources": ["Bench-Harbor"]},
                                  records=[good, {"label": "", "path": "verifier/x"}])
        other = catalog.Benchmark(key="other", title="O")
        with mock.patch.object(catalog, "load", return_value=catalog.Catalog([bench, other], [])), \
                mock.patch.object(catalog, "signature", return_value=("declared-test",)):
            for source in ("bench", "bench-small", "bench-harbor", " BENCH "):
                with self.subTest(source=source):
                    self.assertEqual(artifacts.declared(source), [{"label": "Its row", "path": "verifier/own/row.json"}])
            for source in ("other", "", None):
                with self.subTest(source=source):
                    self.assertEqual(artifacts.declared(source), [])

    def test_the_catalogue_takes_only_a_label_and_a_path_under_verifier(self):
        for path in ("verifier/a", "verifier/a/b.json"):
            self.assertTrue(catalog.valid_record({"label": "A", "path": path}), path)
        for record in ({"label": "A", "path": p} for p in ("verifier", "verifier/", "verifier/../x", "verifier/./x",
                                                          "verifier//x", "/verifier/x", "agent/x", "verifier\\x",
                                                          "../verifier/x", 3)):
            self.assertFalse(catalog.valid_record(record), record)
        for record in ({"path": "verifier/x"}, {"label": " ", "path": "verifier/x"}, "verifier/x"):
            self.assertFalse(catalog.valid_record(record), record)
        bench = catalog.Benchmark(key="bench", title="B", category="C", records=[{"label": "A", "path": "verifier/../x"}])
        self.assertIn("bench: records are each {label, path under verifier/}", catalog.validate(catalog.Catalog([bench], [])))


if __name__ == "__main__":
    unittest.main()
