"""The computer kit of version 2 (``agent-computer-v2``): its definition, its server (version 2's tools, the window
before the task, a step's files, version 1's admission), the reference and the null control of ``late`` and of
version 1's checks, over the fake desktops of version 1's tests, with no container. The docker tier of version 1's
kit covers the desktop itself."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

from nullius.standard import definitions, ports
from tests.test_computer_kit_driver import FakeDesktop

KIT = os.path.join(definitions.KITS, "agent-computer-v2")
spec = importlib.util.spec_from_file_location("computer_kit_v2_driver_under_test", os.path.join(KIT, "driver.py"))
driver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(driver)
v1, checks, desktop = driver.v1, driver.checks, driver.desktop


class Fake(FakeDesktop):
    """Version 1's fake desktop, which also makes the agent's folder as the bridge asks."""

    def python(self, code, seconds=None):
        if "os.makedirs(p, 0o700)" in code:
            return {"status": "success", "output": "made\n", "error": "", "returncode": 0}
        return super().python(code, seconds)


class Definition(unittest.TestCase):
    def test_the_kit_proves_version_2_with_nine_checks_and_is_told_the_task(self):
        kit = definitions.kit("agent.computer/v2")
        self.assertEqual((kit["key"], kit["needs"], kit["datasets"]), ("agent-computer-v2", ["agent.computer/v2"],
                                                                        [{"id": "kit", "units": 9}]))
        self.assertEqual(ports.PORTS["agent.computer/v2"]["kit"], "agent-computer-v2")
        self.assertTrue(kit["driver"]["task_given"] and kit["driver"]["unit_end"])
        self.assertEqual(definitions.problems(kit, "benchmark"), [])
        self.assertEqual(driver.units("kit")["units"], list(checks.CHECKS) + ["late"])
        self.assertEqual(set(kit["check_details"]), set(driver.UNITS))
        self.assertEqual(set(kit["basic"]) | set(kit["skills"]), set(driver.UNITS))


class Units(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(dir=os.environ.get("TMPDIR"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

        def start(image, name, *, job=None, seconds=None):
            return Fake(name, list(v1.OPEN.values())[-1].values)
        self.enterContext(mock.patch.object(desktop, "image", return_value="img"))
        self.enterContext(mock.patch.object(desktop.Desktop, "start", side_effect=start))
        self.enterContext(mock.patch.object(v1.REAPER, "tell"))
        self.enterContext(mock.patch.object(v1, "PAUSE", 0.01))
        self.enterContext(mock.patch.object(driver, "LATE_SECONDS", 0.05))
        self.enterContext(mock.patch.object(desktop, "_CLIENTS_CLOSED", False))
        self.addCleanup(v1.close_all)

    def unit(self, name):
        ws = os.path.join(self.tmp, "job", f"{name}-{len(FakeDesktop.made)}", "workspace")
        os.makedirs(ws)
        return ws, driver.setup(name, ws)

    def client(self, s):
        c = v1.Client(s["mcp"][0]["args"][0])
        self.addCleanup(c.close)
        return c

    def test_the_server_is_version_2s_with_the_window_before_the_task(self):
        ws, s = self.unit("click")
        u, c = v1.OPEN["click"], self.client(s)
        names = [t["name"] for t in c.ask("tools/list", {})["result"]["tools"]]
        self.assertNotIn("python", names)
        self.assertIn("prepare", names)
        x, y = v1.centre(u.boxes[u.expected["target"]])
        failed, said = c.call("click", {"x": x, "y": y})
        self.assertTrue(failed)
        self.assertIn("the task has not been given yet", said)
        self.assertEqual(c.call("prepare", {"actions": ["print(1)"], "files": ["/tmp/agent/a"]}),
                         (False, {"files": {"/tmp/agent/a": None}}))
        self.assertEqual(driver.task_given("click", ws), {})
        self.assertEqual(c.call("step", {"actions": [f"pyautogui.click({x}, {y})"], "files": ["/tmp/agent/a"]}),
                         (False, {"steps_left": u.steps - 1, "done": False, "files": {"/tmp/agent/a": None}}))
        self.assertTrue(c.call("prepare", {"actions": []})[0])
        got = driver.score("click", ws, [], "done")
        self.assertTrue(got["passed"], got)
        self.assertEqual(got["detail"]["calls"], ["click", "prepare", "step", "prepare"])
        with open(os.path.join(os.path.dirname(ws), "verifier", "computer-prepare.jsonl")) as f:
            self.assertEqual([json.loads(line)["actions"] for line in f], [["print(1)"]])

    def test_input_sent_by_prepare_fails_the_check(self):
        ws, s = self.unit("click")
        u, c = v1.OPEN["click"], self.client(s)
        x, y = v1.centre(u.boxes[u.expected["target"]])
        c.call("prepare", {"actions": [f"pyautogui.click({x}, {y})"]})
        driver.task_given("click", ws)
        got = driver.score("click", ws, [], "done")
        self.assertFalse(got["passed"])
        self.assertIn("outside every action", got["detail"]["why"])

    def test_the_reference_passes_version_1s_checks_given_its_task_as_it_starts(self):
        for unit in ("click", "type", "limit", "recover"):            # the fake desktop logs no field values
            with self.subTest(unit=unit):
                ws, s = self.unit(unit)
                got = driver.score(unit, ws, [], driver.oracle(unit, ws)["answer"])
                self.assertTrue(got["passed"], got)

    def test_late_passes_for_the_reference_which_looks_again_by_steps(self):
        ws, s = self.unit("late")
        self.assertIn("appears on the screen a few seconds after", s["prompt"])
        got = driver.score("late", ws, [], driver.oracle("late", ws)["answer"])
        self.assertTrue(got["passed"], got)
        self.assertIn("wait", got["detail"]["calls"])

    def test_late_fails_a_click_sent_with_no_look_by_a_step_once_the_button_is_up(self):
        ws, s = self.unit("late")
        u, c = v1.OPEN["late"], self.client(s)
        driver.task_given("late", ws)
        self.assertTrue(u.up.wait(5))
        c.call("screenshot")                                                    # the start picture: no button
        self.assertFalse(c.call("click", dict(zip("xy", v1.centre(u.boxes[u.expected["target"]]))))[0])
        got = driver.score("late", ws, [], "done")
        self.assertFalse(got["passed"])
        self.assertIn("without looking again by a step", got["detail"]["why"])

    def test_late_fails_the_null_control_and_draws_no_button_when_the_task_is_never_given(self):
        ws, s = self.unit("late")
        u = v1.OPEN["late"]
        t = time.monotonic()
        got = driver.score("late", ws, [], "")
        self.assertLess(time.monotonic() - t, 5)
        self.assertFalse(got["passed"])
        self.assertIsNone(u.shown)


if __name__ == "__main__":
    unittest.main()
