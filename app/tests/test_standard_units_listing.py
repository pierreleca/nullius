"""The listing of a dataset's tasks (``standard/units.py``, ``nullius standard units``): a search by the words
of an id, a title or a text; category values, either within a field and both across fields, each value
counted; 100 tasks a page with the total on every page; a driver without ``describe`` listed and searched by
its ids; the listing cached by its options; ``set create --search`` and ``--category`` storing the ids the
listing gives and the filter in its note; OSWorld1's ``describe`` from its task files. No key, no network:
each driver is a script of the test's own, or the fake benchmark's."""
from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from nullius import paths
from nullius.standard import cli, definitions, sets, units

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "standard", "catalogue")
OSWORLD1 = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "nullius", "catalogue",
                        "benchmarks", "osworld1")
# 250 tasks: t000 ... t249; kind by i % 3, size by i % 2; under mode=short only the first 20
DRIVER = r'''import json, os, sys
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "started"), "a") as f:
    f.write("started\n")
KINDS = ("alpha", "beta", "gamma")
N = 20 if os.environ.get("NULLIUS_OPTION_MODE") == "short" else 250
IDS = [f"t{i:03d}" for i in range(N)]
def about(i):
    title = "Open the spreadsheet" if i == 7 else f"Task number {i}"
    text = "Rename the folder called Quokka" if i == 11 else f"Do thing {i} on the desktop"
    return {"title": title, "text": text, "categories": {"kind": KINDS[i % 3], "size": "small" if i % 2 else "large"}}
for line in sys.stdin:
    req = json.loads(line)
    op = req.pop("op")
    if op == "units":
        out = {"units": IDS}
    elif op == "describe":
        out = {"units": {u: about(i) for i, u in enumerate(IDS)}}
    else:
        out = {"error": "no such op"}
    print(json.dumps(out), flush=True)
'''
MODE = {"mode": {"summary": "how many tasks the listing gives", "values": ["long", "short"], "default": "long"}}


class Scratch(unittest.TestCase):
    """A profile and a catalogue of the test's own; ``self.b`` a benchmark whose driver describes 250 tasks."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nullius-units-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cat = os.path.join(self.tmp, "catalogue")
        here = os.path.join(self.tmp, "described")
        os.makedirs(here)
        with open(os.path.join(here, "driver.py"), "w") as f:
            f.write(DRIVER)
        self.started = os.path.join(here, "started")
        fake = definitions.get("benchmark", "fakebench", ROOT)
        self.b = dict(fake, key="described", label="Described bench", _here=here, options=MODE,
                      datasets=[{"id": "all", "units": 250}],
                      driver={"command": [sys.executable, "{here}/driver.py"], "describe": True})
        self.plain = dict(fake)
        for patch in (mock.patch.dict(os.environ, {"NULLIUS_CATALOGUE": self.cat,
                                                   "NULLIUS_HOME": os.path.join(self.tmp, "profile")}),
                      mock.patch.object(cli, "_find_bench", side_effect=lambda key: {"described": self.b,
                                                                                     "fakebench": self.plain}.get(key)),
                      mock.patch.object(cli.settings, "load")):
            patch.start()
            self.addCleanup(patch.stop)

    def starts(self) -> int:
        if not os.path.exists(self.started):
            return 0
        with open(self.started) as f:
            return len(f.read().splitlines())

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def ids(self, got):
        return [t["id"] for t in got["tasks"]]


class Search(Scratch):
    def test_a_word_of_an_id_a_title_or_a_text_finds_its_tasks_whatever_the_case(self):
        for search, want in (("t042", ["t042"]), ("SPREADSHEET", ["t007"]), ("quokka folder", ["t011"]),
                             ("number 24", ["t024", "t124", "t224"] + [f"t{i}" for i in range(240, 250)])):
            with self.subTest(search=search):
                got = units.find(self.b, "all", search=search)
                self.assertEqual((self.ids(got), got["total"], got["of"]), (want, len(want), 250))
        t = units.find(self.b, "all", search="t011")["tasks"][0]
        self.assertEqual(t, {"id": "t011", "title": "Task number 11", "text": "Rename the folder called Quokka",
                             "categories": {"kind": "gamma", "size": "small"}})

    def test_no_match_gives_an_empty_page_and_a_total_of_0(self):
        got = units.find(self.b, "all", search="wombat")
        self.assertEqual((got["tasks"], got["total"], got["pages"], got["of"]), ([], 0, 1, 250))
        self.assertEqual(got["categories"], {"kind": [], "size": []})


class Categories(Scratch):
    def test_one_value_two_values_of_one_field_and_two_fields_together(self):
        one = units.find(self.b, "all", categories={"kind": ["alpha"]}, size=None)
        self.assertEqual(one["total"], 84)                                        # 0, 3, ..., 249
        self.assertTrue(all(t["categories"]["kind"] == "alpha" for t in one["tasks"]))
        either = units.find(self.b, "all", categories={"kind": ["alpha", "beta"]}, size=None)
        self.assertEqual(either["total"], 84 + 83)
        both = units.find(self.b, "all", categories={"kind": ["alpha"], "size": ["small"]}, size=None)
        self.assertEqual(both["total"], 42)                                       # the odd ones of 0, 3, ..., 249
        self.assertTrue(all(t["categories"] == {"kind": "alpha", "size": "small"} for t in both["tasks"]))

    def test_each_value_is_counted_over_the_search_and_the_other_fields_filters(self):
        got = units.find(self.b, "all")
        self.assertEqual(got["categories"], {"kind": [{"value": "alpha", "count": 84}, {"value": "beta", "count": 83},
                                                      {"value": "gamma", "count": 83}],
                                             "size": [{"value": "large", "count": 125}, {"value": "small", "count": 125}]})
        chosen = units.find(self.b, "all", categories={"kind": ["alpha"]})
        # the field chosen keeps its other values' counts; the other field is counted over the alpha tasks
        self.assertEqual(chosen["categories"]["kind"], got["categories"]["kind"])
        self.assertEqual(chosen["categories"]["size"], [{"value": "large", "count": 42}, {"value": "small", "count": 42}])
        searched = units.find(self.b, "all", search="number 24", categories={"size": ["small"]})
        self.assertEqual(self.ids(searched), ["t241", "t243", "t245", "t247", "t249"])
        self.assertEqual(searched["categories"]["size"], [{"value": "large", "count": 8}, {"value": "small", "count": 5}])

    def test_a_field_no_task_has_is_refused(self):
        with self.assertRaisesRegex(units.Refused, "--category colour: no such category; the categories are kind, size"):
            units.find(self.b, "all", categories={"colour": ["red"]})
        self.assertEqual(units.find(self.b, "all", categories={"kind": ["delta"]})["total"], 0)


class Pages(Scratch):
    def test_250_tasks_come_as_three_pages_with_the_same_total(self):
        pages = [units.find(self.b, "all", page=n) for n in (1, 2, 3, 4)]
        self.assertEqual([len(p["tasks"]) for p in pages], [100, 100, 50, 0])
        self.assertEqual({(p["total"], p["pages"]) for p in pages}, {(250, 3)})
        self.assertEqual(sum((self.ids(p) for p in pages), []), [f"t{i:03d}" for i in range(250)])
        with self.assertRaisesRegex(units.Refused, "page 0"):
            units.find(self.b, "all", page=0)


class WithoutDescribe(Scratch):
    def test_the_listing_gives_ids_and_a_search_matches_ids(self):
        got = units.find(self.plain, "all")
        self.assertEqual((self.ids(got), got["described"], got["categories"]), (["good", "wrong", "slow", "model"], False, {}))
        self.assertEqual(got["tasks"][0], {"id": "good", "title": "", "text": "", "categories": {}})
        self.assertEqual(self.ids(units.find(self.plain, "all", search="MOD")), ["model"])
        with self.assertRaisesRegex(units.Refused, "no such category; these tasks have none"):
            units.find(self.plain, "all", categories={"kind": ["alpha"]})
        code, out, err = self.cli("units", "fakebench", "--dataset", "all", "--search", "od")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out.splitlines(), ["2 of 4 tasks of all (its driver gives ids alone: a search reads the ids)",
                                            "  good", "  model"])


class Cache(Scratch):
    def test_the_second_listing_starts_no_driver_and_another_option_lists_again(self):
        units.find(self.b, "all")
        units.find(self.b, "all", search="t1", page=2)
        self.assertEqual(self.starts(), 1)
        short = units.find(self.b, "all", {"mode": "short"})
        self.assertEqual((short["total"], self.starts()), (20, 2))
        units.find(self.b, "all", {"mode": "long"})                  # the default, given: the same listing
        self.assertEqual(self.starts(), 2)
        self.assertTrue(units.cache_path(self.b, self.b["datasets"][0], {"mode": "long"}).startswith(paths.cache_dir()))
        with self.assertRaisesRegex(units.Refused, "mode"):
            units.find(self.b, "all", {"mode": "sideways"})
        with self.assertRaisesRegex(units.NotFound, "no dataset 'nope'"):
            units.find(self.b, "nope")

    def test_a_driver_that_cannot_list_is_refused_with_its_words(self):
        with open(os.path.join(self.b["_here"], "driver.py"), "w") as f:
            f.write("import sys\nprint('the data is not there', file=sys.stderr)\nsys.exit(3)\n")
        with self.assertRaisesRegex(units.Refused, "could not list the dataset all: .*the data is not there"):
            units.find(self.b, "all")
        self.assertEqual(os.listdir(os.path.join(paths.cache_dir(), units.FOLDER)), [])   # nothing kept, no work left


class CommandLine(Scratch):
    def test_the_command_prints_the_count_the_categories_and_the_tasks(self):
        code, out, err = self.cli("units", "described", "--dataset", "all", "--search", "number 24",
                                  "--category", "size=small")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out.splitlines(), ["5 of 250 tasks of all", "kind: alpha 2, beta 2, gamma 1", "size: large 8, small 5",
                                            "  t241  Task number 241", "  t243  Task number 243", "  t245  Task number 245",
                                            "  t247  Task number 247", "  t249  Task number 249"])
        code, out, err = self.cli("units", "described", "--dataset", "all", "--category", "kind=beta", "--json")
        got = json.loads(out)
        self.assertEqual((code, got["total"], got["pages"], len(got["tasks"])), (0, 83, 1, 83))

    def test_refusals_are_one_line_on_stderr(self):
        for args, code, said in ((["units", "nowhere", "--dataset", "all"], 3, "no benchmark 'nowhere'"),
                                 (["units", "described", "--dataset", "nope"], 3, "no dataset 'nope'"),
                                 (["units", "described", "--dataset", "all", "--category", "kind"], 2, "FIELD=VALUE"),
                                 (["units", "described", "--dataset", "all", "--bench-option", "mode=x"], 2, "mode")):
            with self.subTest(args=args):
                got, out, err = self.cli(*args)
                self.assertEqual((got, out), (code, ""))
                self.assertTrue(err.startswith("refused: ") and said in err and len(err.splitlines()) == 1, err)


class SetFromAFilter(Scratch):
    def test_the_ids_stored_are_the_ones_the_listing_gives_and_the_note_holds_the_filter(self):
        code, out, err = self.cli("set", "create", "described", "picked", "--dataset", "all", "--search", "number 24",
                                  "--category", "kind=alpha", "--category", "kind=beta")
        self.assertEqual(code, 0, err)
        entry = sets.load("described", "picked")
        listed = units.find(self.b, "all", search="number 24", categories={"kind": ["alpha", "beta"]}, size=None)
        self.assertEqual(sets.revision(entry)["units"], self.ids(listed))
        self.assertEqual(sets.revision(entry)["units"], ["t024", "t124", "t240", "t241", "t243", "t244", "t246", "t247", "t249"])
        self.assertEqual(sets.revision(entry)["note"],
                         'matched by --search "number 24" --category kind=alpha --category kind=beta')
        code, out, err = self.cli("set", "create", "described", "by-kind", "--dataset", "all", "--category", "kind=gamma")
        self.assertEqual(code, 0, err)
        self.assertEqual(len(sets.revision(sets.load("described", "by-kind"))["units"]), 83)

    def test_a_filter_that_matches_nothing_or_beside_another_source_is_refused(self):
        for args, said in ((["--search", "wombat"], "no task of all matches --search \"wombat\""),
                           (["--search", "t1", "--units", os.path.join(self.tmp, "x.txt")], "--units FILE, --from-job"),
                           ([], "--units FILE, --from-job")):
            with self.subTest(args=args):
                code, out, err = self.cli("set", "create", "described", "none", "--dataset", "all", *args)
                self.assertEqual((code, out), (2, ""))
                self.assertIn(said, err)
        self.assertFalse(os.path.exists(os.path.join(self.cat, "sets")))


class OSWorld1(unittest.TestCase):
    def test_describe_gives_each_tasks_instruction_and_domain_from_its_file(self):
        tmp = tempfile.mkdtemp(prefix="nullius-osworld1-")
        self.addCleanup(shutil.rmtree, tmp, True)
        long = "Please " + "open the settings and change the theme " * 4 + "now."
        tasks = {"chrome": {"c1": "Open a new tab."}, "vlc": {"v1": long}}
        for domain, ids in tasks.items():
            os.makedirs(os.path.join(tmp, "evaluation_examples", "examples", domain))
            for eid, instruction in ids.items():
                with open(os.path.join(tmp, "evaluation_examples", "examples", domain, eid + ".json"), "w") as f:
                    json.dump({"id": eid, "instruction": instruction}, f)
        meta = os.path.join(tmp, "test_small.json")
        with open(meta, "w") as f:
            json.dump({d: list(ids) for d, ids in tasks.items()}, f)
        import importlib.util
        spec = importlib.util.spec_from_file_location("osworld1_driver_describe", os.path.join(OSWORLD1, "driver.py"))
        driver = importlib.util.module_from_spec(spec)
        with mock.patch.dict(os.environ, {"NULLIUS_UPSTREAM": tmp, "NULLIUS_DATASET": meta}), \
                mock.patch.object(sys, "argv", ["driver.py"]):
            spec.loader.exec_module(driver)
            got = driver.describe(dataset="test_small")["units"]
            self.assertIs(driver.HANDLERS["describe"], driver.describe)
        self.assertEqual(list(got), ["chrome__c1", "vlc__v1"])
        self.assertEqual(got["chrome__c1"], {"title": "Open a new tab.", "text": "Open a new tab.",
                                             "categories": {"domain": "chrome"}})
        self.assertEqual(got["vlc__v1"]["text"], long)
        self.assertTrue(got["vlc__v1"]["title"].endswith(" ...") and len(got["vlc__v1"]["title"]) <= 104)
        self.assertTrue(long.startswith(got["vlc__v1"]["title"][:-4]))
        self.assertEqual(got["vlc__v1"]["categories"], {"domain": "vlc"})
        with open(os.path.join(OSWORLD1, "standard.json")) as f:
            self.assertIs(json.load(f)["driver"]["describe"], True)


if __name__ == "__main__":
    unittest.main()
