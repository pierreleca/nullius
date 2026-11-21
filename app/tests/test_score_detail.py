"""A standard trial's detail as its page shows it (``parse.score_detail``): the driver's plain values with their
names, each object of plain values one level down as flags, nothing renamed and nothing of any one benchmark known."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from nullius.runs import parse


class ScoreDetail(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        os.makedirs(os.path.join(self.dir, "verifier"))

    def detail(self, detail, leave_out=()):
        with open(os.path.join(self.dir, "verifier", "score.json"), "w") as f:
            json.dump({"reward": 0.0, "detail": detail}, f)
        return parse.score_detail(self.dir, leave_out)

    def test_plain_values_keep_their_names_and_order_and_read_as_written(self):
        got = self.detail({"zeta": "as written", "count": 3, "share": 0.45, "seen": True, "missed": False, "gone": None,
                           "names": ["a", 2, None], "empty": []})
        self.assertEqual([(f["name"], f["text"]) for f in got["fields"]],
                         [("zeta", "as written"), ("count", "3"), ("share", "0.45"), ("seen", "yes"), ("missed", "no"),
                          ("gone", "none"), ("names", "a, 2, none"), ("empty", "none")])
        self.assertEqual(got["groups"], [])

    def test_an_object_of_plain_values_is_a_list_of_flags(self):
        got = self.detail({"first": 1, "checks": {"b": True, "a": False, "c": "not taken", "d": None}})
        self.assertEqual([f["name"] for f in got["fields"]], ["first"])
        self.assertEqual(got["groups"], [{"name": "checks", "flags": [
            {"name": "b", "held": True, "text": ""}, {"name": "a", "held": False, "text": ""},
            {"name": "c", "held": None, "text": "not taken"}, {"name": "d", "held": None, "text": "none"}]}])

    def test_anything_deeper_is_its_json_in_one_cell_cut_past_the_cells_length(self):
        deep = {"inner": {"list": [1, {"x": 2}]}}
        got = self.detail({"deep": deep, "rows": [{"kind": "front"}], "long": "x" * (parse.DETAIL_CELL + 1),
                           "short": "y" * parse.DETAIL_CELL, "nothing": {}})
        cells = {f["name"]: f for f in got["fields"]}
        self.assertEqual(json.loads(cells["deep"]["text"]), deep)
        self.assertEqual(json.loads(cells["rows"]["text"]), [{"kind": "front"}])
        self.assertEqual((cells["long"]["text"], cells["long"]["cut"]), ("x" * (parse.DETAIL_CELL + 1), "x" * parse.DETAIL_CELL))
        self.assertEqual(cells["short"]["cut"], "")
        self.assertEqual(cells["nothing"]["text"], "{}")
        self.assertEqual(got["groups"], [])

    def test_the_names_the_page_shows_elsewhere_are_left_out(self):
        got = self.detail({"steps": 3, "steps_limit": 10, "ended": "done", "seed": 7}, ("steps", "steps_limit", "ended"))
        self.assertEqual([f["name"] for f in got["fields"]], ["seed"])
        self.assertIsNone(self.detail({"steps": 3}, ("steps",)))

    def test_no_detail_no_view(self):
        for detail in (None, {}, [1, 2], "said"):
            with self.subTest(detail=detail):
                self.assertIsNone(self.detail(detail))
        os.remove(os.path.join(self.dir, "verifier", "score.json"))
        self.assertIsNone(parse.score_detail(self.dir))

    def test_a_linked_score_is_not_read(self):
        outside = os.path.join(self.dir, "outside.json")
        with open(outside, "w") as f:
            json.dump({"detail": {"seed": 1}}, f)
        os.symlink(outside, os.path.join(self.dir, "verifier", "score.json"))
        self.assertIsNone(parse.score_detail(self.dir))

    def test_credential_shaped_values_are_redacted(self):
        key = "sk-" + "a1B2c3D4e5F6g7H8i9J0" * 2
        got = self.detail({"said": f"the key {key}", "flags": {key: True}})
        self.assertNotIn(key, json.dumps(got))


if __name__ == "__main__":
    unittest.main()
