"""The benchmark's detail on a trial's page: the plain values of its score's detail as a short table, each object of
flags one level down as a list of what held and what did not, under the driver's own names. A long value is cut with
its whole text behind a disclosure; every value is text, never markup; a trial whose score has no detail has no panel.
The runner fixture is copied and its score written by each test; nothing is run."""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import unittest
from contextlib import ExitStack
from html import unescape
from unittest.mock import patch

from nullius.runs import parse
from .test_routes import APP_DIR, web

NATIVE = APP_DIR / "tests" / "fixtures" / "native"


def text_of(html):
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", html)).split())


class TrialDetail(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(web.app.config, TESTING=True))
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = os.path.join(tmp.name, "parts-arm")
        shutil.copytree(NATIVE / "parts-arm", self.root)
        self.client = web.app.test_client()

    def page(self, detail, standard=None):
        """The trial page's HTML with ``detail`` as t1's score's detail; ``standard``, its record's standard block."""
        with open(os.path.join(self.root, "t1", "verifier", "score.json"), "w") as f:
            json.dump({"reward": 0.0, "detail": detail}, f)
        if standard is not None:
            at = os.path.join(self.root, "t1", "result.json")
            with open(at) as f:
                res = json.load(f)
            with open(at, "w") as f:
                json.dump(dict(res, standard=standard), f)
        job = parse.parse_job(self.root)
        with patch.object(web.shared, "_jobs_for_request", return_value=[job]):
            response = self.client.get(f"/trial/{job.job_id}/t1")
        self.addCleanup(response.close)
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True)

    def panel(self, html):
        self.assertIn('id="trial-detail"', html)
        return html.split('id="trial-detail">', 1)[1].split('</section>', 1)[0].split('\n</div>\n', 1)[0]

    def test_plain_values_are_a_table_and_flags_a_list_under_the_drivers_names(self):
        html = self.panel(self.page({"condition": "the second", "seed": 565606, "measured": False,
                                     "checks": {"kept": True, "applied": False}, "measures": {"pointer": "not taken"}}))
        said = text_of(html)
        self.assertIn("The benchmark's detail condition the second seed 565606 measured no", said)
        self.assertIn("checks ✓ kept × applied measures · pointer: not taken", said)
        self.assertRegex(html, r'<span class="ok">✓</span> kept')
        self.assertRegex(html, r'<span class="bad">×</span> applied')
        lists = re.findall(r"<ul[^>]*>", html)
        self.assertEqual(len(lists), 2)
        for ul in lists:                                     # each flag's mark is its only one: no bullet before it
            self.assertIn("list-style:none", ul)

    def test_a_long_value_is_cut_with_its_whole_text_behind_a_disclosure(self):
        long = "word " * 60
        html = self.panel(self.page({"why": long}))
        cut = long[:parse.DETAIL_CELL]
        self.assertIn(f"<details><summary>{cut}…</summary>{long}</details>", html)

    def test_values_and_names_are_text_never_markup(self):
        html = self.page({"<b>name</b>": "<script>alert(1)</script>", "flags": {"<i>flag</i>": True}})
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
        self.assertIn("&lt;b&gt;name&lt;/b&gt;", html)
        self.assertIn("&lt;i&gt;flag&lt;/i&gt;", html)

    def test_what_the_page_shows_elsewhere_is_left_out(self):
        html = self.page({"steps": 4, "steps_limit": 45, "ended": "done", "seed": 1},
                         standard={"benchmark": "sums", "unit": "t1"})
        said = text_of(self.panel(html))
        self.assertIn("Steps 4 of 45", text_of(html))
        self.assertIn('<span class="faint ended-by" title="What ended the agent\'s work">Done</span>', html)
        self.assertEqual(said, "The benchmark's detail seed 1")

    def test_an_end_is_left_out_of_the_panel_even_when_it_is_empty(self):
        for ended in ("", "done"):
            with self.subTest(ended=ended):
                html = self.page({"ended": ended, "seed": 1}, standard={"benchmark": "sums", "unit": "t1"})
                self.assertEqual(text_of(self.panel(html)), "The benchmark's detail seed 1")

    def test_a_score_without_a_detail_has_no_panel(self):
        for detail in ({}, None, ["a"]):
            with self.subTest(detail=detail):
                self.assertNotIn('id="trial-detail"', self.page(detail))
        os.remove(os.path.join(self.root, "t1", "verifier", "score.json"))
        job = parse.parse_job(self.root)
        with patch.object(web.shared, "_jobs_for_request", return_value=[job]):
            response = self.client.get(f"/trial/{job.job_id}/t1")
        self.addCleanup(response.close)
        self.assertNotIn('id="trial-detail"', response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
