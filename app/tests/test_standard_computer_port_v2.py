"""The computer port's version 2 (``agent.computer/v2``) in the shared code: no python and no read; one observation
for each step, fixed as the step ends, which screenshot returns without a new picture; upload and prepare in the window
before the task alone, inside the agent's folder; a step's files. On a fake desktop whose screen changes with each
action, under a loop of a few lines that counts steps as OSWorld's run_single_example does, either handing its own
observation (as OSWorld's) or none (as the kit's). No VM, no docker, no network, no model."""
from __future__ import annotations

import base64
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from unittest import mock

from nullius.standard import computer_port as cp
from tests.test_standard_computer_port import PNG, Client, FakeDesktop, error, image, value

FOLDER = "/agent"
STUB = "import sys, types\nsys.modules['pyautogui'] = types.SimpleNamespace(FAILSAFE=True)\n"


def shot(n):
    return PNG + b"#%d" % n


class Desktop(FakeDesktop):
    """The fake desktop with absolute paths under its root, a screen that changes with each action run, and the
    folder made as the bridge asks. Its python has a stand-in pyautogui, so that a prepare's actions run."""

    def __init__(self, root):
        super().__init__(root)
        self.screen_no = 0

    def at(self, path):
        assert path.startswith("/") and ".." not in path.split("/"), path
        return os.path.join(self.root, path.lstrip("/"))

    def python(self, code):
        if code == cp.MAKE_FOLDER.format(folder=FOLDER):
            with self.op("python", code="folder"):
                shutil.rmtree(self.at(FOLDER), True)
                os.makedirs(self.at(FOLDER))
                return {"status": "success", "output": "made\n", "error": "", "returncode": 0}
        return super().python(STUB + code)

    def act(self, action):
        super().act(action)
        self.screen_no += 1                                      # the screen moves on, whatever the action

    def capture(self):
        with self.op("capture"):
            return shot(self.screen_no)

    def captures(self):
        return self.kinds().count("capture")


class Kept(Desktop):
    """A desktop whose benchmark keeps its own observation, as OSWorld's loop does: the bridge never captures."""
    last = None

    def observation(self):
        return self.last


def loop(agent, desktop, steps, pause, ran, go, hands):
    """OSWorld's loop in a few lines: the start picture, then one predict a step, its actions run with the pause after
    each and, when the loop ``hands`` its observation, a picture after each action that the next predict gets."""
    agent.reset(None, vm_ip="127.0.0.1")
    go.wait(30)
    obs = {"screenshot": desktop.capture()} if hands else None
    done, step = False, 0
    while not done and step < steps:
        _, actions = agent.predict("the task", obs)
        for action in actions:
            desktop.act(action)
            time.sleep(pause)
            ran.append(action)
            if hands:
                obs = {"screenshot": desktop.capture()}
                desktop.last = obs["screenshot"]
            done = action in ("DONE", "FAIL")
            if done:
                break
        step += 1
    ran.append(("evaluated", step))


class Port2(unittest.TestCase):
    def start(self, steps=5, hands=True, window=True, given=True):
        """A unit of version 2: the bridge over the desktop, its server and the loop. ``given``: the task is given at
        once (the window closed), as the run does just after the agent's session opens."""
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        root = os.path.join(self.tmp, "desktop")
        os.mkdir(root)
        self.desktop = (Kept if hands else Desktop)(root)
        self.prepared = os.path.join(self.tmp, "prepared.jsonl")
        self.bridge = cp.Bridge(self.desktop, steps, 0.01, (1920, 1080), 30, version=2, folder=FOLDER, window=window,
                                prepared=self.prepared)
        folder = os.path.join(self.tmp, "socket")
        os.mkdir(folder, 0o700)
        self.socket, self.log = os.path.join(folder, "s"), os.path.join(self.tmp, "calls.jsonl")
        self.server = cp.Server(self.bridge, self.socket, self.log)
        self.addCleanup(self.server.close)
        self.ran, self.go = [], threading.Event()
        self.go.set()
        self.loop = threading.Thread(target=self._loop, args=(steps, hands), daemon=True)
        self.loop.start()
        self.addCleanup(self.bridge.finish)
        c = Client(self.socket)
        self.addCleanup(c.close)
        c.initialize()
        c.call("screenshot")                                     # served once the loop's first predict has begun
        if given:
            self.bridge.close_window()
        return c

    def _loop(self, steps, hands):
        try:
            loop(self.bridge, self.desktop, steps, 0.01, self.ran, self.go, hands)
        finally:
            self.bridge.finish()

    def evaluated(self, seconds=10):
        self.loop.join(seconds)
        self.assertFalse(self.loop.is_alive(), "the loop did not end")
        return self.ran[-1][1]

    def left(self, c):
        return value(c.call("info"))["steps_left"]

    def put(self, path, data):
        with open(self.desktop.at(path), "wb") as f:
            f.write(data)


class Tools(Port2):
    def test_the_tool_list_has_no_python_and_no_read_and_names_prepare_and_a_steps_files(self):
        c = self.start(steps=3)
        listed = {t["name"]: t for t in c.ask("tools/list")["result"]["tools"]}
        self.assertEqual(list(listed), ["info", "screenshot", "upload", "prepare", "click", "move", "drag", "scroll",
                                        "type", "key", "wait", "step", "done", "fail"])
        self.assertNotIn("maxItems", listed["step"]["inputSchema"]["properties"]["files"])
        self.assertNotIn("maxItems", listed["prepare"]["inputSchema"]["properties"]["files"])
        self.assertEqual(sorted(listed["prepare"]["inputSchema"]["properties"]), ["actions", "files"])
        init = c.initialize()["result"]
        self.assertEqual(init["serverInfo"], {"name": "computer", "version": "2"})
        self.assertIn("upload and prepare work inside folder (/agent)", init["instructions"])
        self.assertEqual(value(c.call("info")), {"screen": [1920, 1080], "steps": 3, "steps_left": 3, "pause": 0.01,
                                                 "password": "password", "folder": FOLDER})
        for gone in ("python", "read"):
            answer = c.ask("tools/call", {"name": gone, "arguments": {}})
            self.assertEqual(answer["error"]["message"], f"no tool {gone!r}")

    def test_version_1_keeps_its_tools_and_its_info(self):
        self.assertEqual(cp.names(1), cp.NAMES)
        self.assertEqual([t["name"] for t in cp.listed((1920, 1080))], cp.NAMES)
        self.assertEqual(cp.tools((1920, 1080)), cp.tools((1920, 1080), 1))
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        b = cp.Bridge(FakeDesktop(tmp), 3, 1.0, (1920, 1080), 30)
        self.assertEqual(b.info(), {"screen": [1920, 1080], "steps": 3, "steps_left": 3, "pause": 1.0,
                                    "python_seconds": 30, "password": "password"})
        self.assertFalse(b.window)

    def test_a_version_2_bridge_needs_a_plain_absolute_folder(self):
        for folder in (None, "", "agent", "/", "/agent/", "/a/../b"):
            with self.subTest(folder=folder), self.assertRaises(ValueError):
                cp.Bridge(FakeDesktop(self.id()), 1, 0, (10, 10), 1, version=2, folder=folder)
        with self.assertRaises(ValueError):
            cp.Bridge(FakeDesktop(self.id()), 1, 0, (10, 10), 1, version=3, folder=FOLDER)


class Observation(Port2):
    def test_before_the_first_step_screenshot_is_the_loops_start_picture_and_takes_no_new_one(self):
        c = self.start(steps=3)
        first, again = image(c.call("screenshot")), image(c.call("screenshot"))
        self.assertEqual((first, again), (shot(0), shot(0)))
        self.assertEqual(self.desktop.captures(), 1)             # the loop's own: the bridge took none

    def test_two_screenshots_with_no_step_between_are_the_same_and_a_step_that_acts_changes_them(self):
        for hands in (True, False):
            with self.subTest(hands=hands):
                c = self.start(steps=5, hands=hands)
                before = image(c.call("screenshot"))
                got = c.call("click", x=1, y=1)
                after = image(c.call("screenshot"))
                self.assertEqual((image(got), after, image(c.call("screenshot"))), (after, after, after))
                self.assertNotEqual(before, after)
                got = c.call("step", actions=["pyautogui.press('a')", "pyautogui.press('b')"], screenshot=True)
                self.assertEqual(image(got), image(c.call("screenshot")))
                self.assertNotEqual(image(got), after)
                self.assertIsNone(image(c.call("step", actions=["pyautogui.press('c')"])))

    def test_an_empty_step_is_one_step_and_leaves_the_observation_as_it_was(self):
        for hands in (True, False):
            with self.subTest(hands=hands):
                c = self.start(steps=5, hands=hands)
                c.call("click", x=1, y=1)
                seen, captures = image(c.call("screenshot")), self.desktop.captures()
                got = c.call("step", actions=[], screenshot=True)
                self.assertEqual((value(got), image(got)), ({"steps_left": 3, "done": False}, seen))
                self.assertEqual(self.desktop.captures(), captures)

    def test_wait_is_one_step_and_a_fresh_observation(self):
        for hands in (True, False):
            with self.subTest(hands=hands):
                c = self.start(steps=5, hands=hands)
                before = image(c.call("screenshot"))
                got = c.call("wait")
                self.assertEqual(value(got), {"steps_left": 4, "done": False})
                self.assertNotEqual(image(got), before)
                self.assertEqual(image(c.call("screenshot")), image(got))
                self.assertEqual(self.ran, ["WAIT"])

    def test_with_its_own_observation_the_bridge_takes_no_picture_and_without_one_it_takes_one_a_step(self):
        c = self.start(steps=5, hands=True)
        for _ in range(3):
            c.call("click", x=1, y=1)
            c.call("screenshot")
        self.assertEqual(self.desktop.captures(), 4)             # the loop's start picture and one per action
        c = self.start(steps=5, hands=False)
        for _ in range(3):
            c.call("step", actions=["pyautogui.press('a')", "pyautogui.press('b')"])
            c.call("screenshot")
        self.assertEqual(self.desktop.captures(), 4)             # the start picture and one as each step ended
        self.assertEqual(self.desktop.kinds()[-1], "capture")

    def test_a_step_whose_picture_could_not_be_taken_says_so_and_screenshot_too(self):
        c = self.start(steps=5, hands=False)
        self.desktop.fail = {"capture"}
        self.assertIn("the step was run; the screen could not be taken", error(c.call("click", x=1, y=1)))
        self.assertIn("there is no observation", error(c.call("screenshot")))
        self.desktop.fail = set()
        self.assertEqual(image(c.call("click", x=1, y=1)), image(c.call("screenshot")))


class Window(Port2):
    def test_upload_and_prepare_work_in_the_window_and_take_no_step(self):
        c = self.start(steps=3, given=False)
        self.assertEqual(value(c.call("upload", path="/agent/run.py", data=base64.b64encode(b"print(1)").decode())),
                         {"size": 8})
        self.assertEqual(value(c.call("upload", path="x", data=base64.b64encode(b"abc").decode())), {"size": 3})
        with open(self.desktop.at("/agent/x"), "rb") as f:
            self.assertEqual(f.read(), b"abc")                    # a relative path is the folder's
        got = c.call("prepare", actions=["open('agent/out.txt', 'w').write(open('agent/run.py').read() * 2)", "WAIT"],
                     files=["/agent/out.txt", "/agent/none"])
        self.assertEqual(value(got), {"files": {"/agent/out.txt": base64.b64encode(b"print(1)print(1)").decode(),
                                                "/agent/none": None}})
        self.assertIsNone(image(got))
        self.assertEqual(value(c.call("prepare", actions=[])), {"files": {}})
        self.assertEqual(self.left(c), 3)
        self.assertEqual(self.ran, [])
        with open(self.prepared) as f:
            said = [json.loads(line) for line in f]
        self.assertEqual([(s["actions"], s["files"]) for s in said],
                         [(["open('agent/out.txt', 'w').write(open('agent/run.py').read() * 2)", "WAIT"],
                           ["/agent/out.txt", "/agent/none"]), ([], [])])

    def test_an_acting_call_in_the_window_is_refused_and_takes_no_step(self):
        c = self.start(steps=3, given=False)
        for tool, args in (("click", {"x": 1, "y": 1}), ("step", {"actions": []}), ("wait", {}), ("done", {})):
            with self.subTest(tool=tool):
                self.assertIn("the task has not been given yet", error(c.call(tool, **args)))
        self.assertEqual(self.left(c), 3)
        self.bridge.close_window()
        self.assertEqual(value(c.call("click", x=1, y=1)), {"steps_left": 2, "done": False})
        self.assertEqual(self.ran, ["pyautogui.click(1, 1, clicks=1, button='left')"])

    def test_once_the_task_is_given_upload_and_prepare_are_refused(self):
        c = self.start(steps=3, given=False)
        c.call("prepare", actions=[])
        self.bridge.close_window()
        self.assertEqual(error(c.call("upload", path="/agent/a", data="YQ==")),
                         "the task has been given: upload is taken only before it")
        self.assertEqual(error(c.call("prepare", actions=["print(1)"])),
                         "the task has been given: prepare is taken only before it")
        self.assertEqual(self.left(c), 3)
        self.assertEqual(len(self.desktop.kinds()), 2)           # the folder made, the start picture

    def test_the_lifelines_task_given_closes_the_window(self):
        c = self.start(steps=3, given=False)
        read, write = os.pipe()
        with mock.patch.object(cp.os, "kill"):
            reader = threading.Thread(target=cp.lifeline, args=(read, self.bridge), daemon=True)
            reader.start()
            os.write(write, cp.TASK_GIVEN + b"\n")
            deadline = time.monotonic() + 5
            while self.bridge.window and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertIn("the task has been given", error(c.call("prepare", actions=[])))
            os.close(write)
            reader.join(5)
        os.close(read)

    def test_with_no_window_upload_and_prepare_are_refused_from_the_start(self):
        c = self.start(steps=3, window=False, given=False)
        self.assertIn("the task has been given", error(c.call("upload", path="/agent/a", data="YQ==")))
        self.assertEqual(value(c.call("click", x=1, y=1)), {"steps_left": 2, "done": False})

    def test_paths_outside_the_folder_are_refused_at_once_and_take_no_step(self):
        c = self.start(steps=3, given=False)
        for path in ("/etc/passwd", "/agent", "/agent/", "/agent/../x", "../x", "./x", "/agentx/y", "/agent//x"):
            with self.subTest(path=path):
                self.assertIn("is a path inside /agent", error(c.call("upload", path=path, data="YQ==")))
                self.assertIn("files[1] is a path inside /agent",
                              error(c.call("prepare", actions=[], files=["/agent/a", path])))
        self.bridge.close_window()
        self.assertIn("files[0] is a path inside /agent", error(c.call("step", actions=["x()"], files=["/tmp/a"])))
        self.assertEqual(self.left(c), 3)
        self.assertEqual((self.ran, len(self.desktop.kinds())), ([], 2))

    def test_a_prepare_action_the_desktop_did_not_end_in_time_stops_it(self):
        c = self.start(steps=3, given=False)
        self.desktop.silent = True
        self.assertEqual(error(c.call("prepare", actions=["print(1)", "print(2)"])),
                         "the desktop did not end actions[0] in time; the actions after it were not run")
        self.assertEqual(self.left(c), 3)


class WindowClosure(unittest.TestCase):
    def setUp(self):
        tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.desktop = Desktop(tmp)
        self.bridge = cp.Bridge(self.desktop, 3, 0, (100, 100), 30, version=2, folder=FOLDER, window=True)
        self.bridge._begin(None)
        self.addCleanup(self.bridge.finish)

    def submit(self, tool):
        args = {"actions": ["print(1)"], "files": ["/agent/a"]} if tool == "prepare" else {
            "path": "/agent/a", "data": "YQ=="}
        replies = []
        call = cp.Call(tool, args, lambda c, v, i, e: replies.append((v, i, e)))
        cp.checked(call, self.bridge.screen, self.bridge.version, self.bridge.folder)
        self.bridge.submit(call)
        return call, replies

    def test_closing_refuses_queued_upload_and_prepare_as_calls_sent_after_it(self):
        pending = [self.submit(tool) for tool in cp.WINDOW]
        before = list(self.desktop.events)
        self.bridge.close_window()
        for call, replies in pending:
            _, later = self.submit(call.tool)
            self.assertEqual(replies, later)
            self.assertEqual(replies, [(None, None, f"the task has been given: {call.tool} is taken only before it")])
        self.assertEqual(list(self.bridge.calls), [])
        self.assertEqual(self.desktop.events, before)
        self.assertEqual(self.bridge.used, 0)

    def test_a_dequeued_call_is_refused_if_it_has_not_started_before_closure(self):
        for tool in cp.WINDOW:
            with self.subTest(tool=tool):
                self.bridge.window = True
                call, replies = self.submit(tool)
                self.assertIs(self.bridge._next(), call)
                before = list(self.desktop.events)
                self.bridge.close_window()
                self.bridge._serve(call)
                _, later = self.submit(tool)
                self.assertEqual(replies, later)
                self.assertEqual(self.desktop.events, before)

    def test_closing_waits_for_running_upload_and_prepare_even_when_they_fail(self):
        for tool in cp.WINDOW:
            for fails in (False, True):
                with self.subTest(tool=tool, fails=fails):
                    self.bridge.window = True
                    call, replies = self.submit(tool)
                    entered, release, waiting, closed = (threading.Event() for _ in range(4))
                    operation = "upload" if tool == "upload" else ("python" if fails else "read")
                    original = getattr(self.desktop, operation)
                    wait = self.bridge.change.wait

                    def blocked(*args):
                        entered.set()
                        if not release.wait(5):
                            raise RuntimeError("the test did not release the desktop")
                        if fails:
                            raise RuntimeError("the desktop operation failed")
                        return original(*args)

                    def waited(*args):
                        waiting.set()
                        return wait(*args)

                    def close():
                        self.bridge.close_window()
                        closed.set()

                    with mock.patch.object(self.desktop, operation, blocked), \
                            mock.patch.object(self.bridge.change, "wait", waited):
                        serving = threading.Thread(target=lambda: self.bridge._serve(self.bridge._next()))
                        closing = threading.Thread(target=close)
                        serving.start()
                        try:
                            self.assertTrue(entered.wait(5))
                            closing.start()
                            self.assertTrue(waiting.wait(1), "closure did not wait for the running call")
                            self.assertFalse(closed.is_set())
                            self.assertFalse(self.bridge.window)
                            _, later = self.submit(tool)
                            self.assertIn("the task has been given", later[0][2])
                        finally:
                            release.set()
                            serving.join(5)
                            if closing.ident is not None:
                                closing.join(5)
                        self.assertFalse(serving.is_alive() or closing.is_alive())
                    self.assertTrue(closed.is_set())
                    self.assertEqual(len(replies), 1)
                    self.assertEqual(replies[0][2] is not None, fails)
                    self.assertEqual(self.bridge.used, 0)


class StepFiles(Port2):
    def test_a_step_returns_each_file_null_for_a_missing_one_and_an_error_for_one_too_large(self):
        c = self.start(steps=3)
        self.put("/agent/small", b"hello")
        self.put("/agent/big", b"x" * (cp.READ_BYTES + 1))
        got = c.call("step", actions=["pyautogui.press('a')"], files=["/agent/small", "/agent/missing", "big"])
        self.assertEqual(value(got), {"steps_left": 2, "done": False, "files": {
            "/agent/small": base64.b64encode(b"hello").decode(), "/agent/missing": None,
            "big": {"error": f"over {cp.READ_BYTES} bytes"}}})
        self.desktop.fail = {"read"}
        got = c.call("step", actions=[], files=["/agent/small"])
        self.assertEqual(value(got), {"steps_left": 1, "done": False, "files": {"/agent/small": {
            "error": "the desktop could not read it: RuntimeError: read failed in the desktop"}}})

    def test_forty_small_files_come_back_in_one_step(self):
        c = self.start(steps=3)
        names = [f"/agent/f{i}" for i in range(40)]
        for i, name in enumerate(names):
            self.put(name, b"file %d" % i)
        got = value(c.call("step", actions=["pyautogui.press('a')"], files=names))
        self.assertEqual(got, {"steps_left": 2, "done": False,
                               "files": {name: base64.b64encode(b"file %d" % i).decode()
                                         for i, name in enumerate(names)}})
        self.assertEqual(self.left(c), 2)

    def test_files_that_would_take_the_answer_past_its_bound_are_errors_and_the_others_come(self):
        c = self.start(steps=3)
        big = b"y" * (11 * 1024 * 1024)                          # 11 MiB: two of them pass 24 MiB in base64
        for name in ("a", "b", "c"):
            self.put(f"/agent/{name}", big)
        self.put("/agent/small", b"hi")
        got = c.call("step", seconds=60, actions=["pyautogui.press('a')"], screenshot=True,
                     files=["/agent/a", "/agent/b", "/agent/small", "/agent/c"])
        self.assertFalse(got["isError"])
        over = {"error": f"the answer would pass {cp.LINE_BYTES} bytes with this file"}
        self.assertEqual(value(got)["files"], {"/agent/a": base64.b64encode(big).decode(), "/agent/b": over,
                                               "/agent/small": base64.b64encode(b"hi").decode(), "/agent/c": over})
        self.assertEqual(image(got), shot(1))
        self.assertEqual(self.left(c), 2)

    def test_the_files_are_read_once_the_actions_and_their_pauses_have_run(self):
        c = self.start(steps=3)
        c.call("step", actions=["pyautogui.press('a')", "WAIT"], files=["/agent/a"])
        kinds = self.desktop.kinds()
        self.assertEqual(kinds[kinds.index("action"):], ["action", "capture", "action", "capture", "read"])

    def test_every_call_that_reaches_the_desktop_lowers_steps_left_but_upload_and_prepare_in_the_window(self):
        c = self.start(steps=9, given=False)
        c.call("upload", path="/agent/a", data="YQ==")
        c.call("prepare", actions=["print(1)"])
        c.call("screenshot")
        self.assertEqual(self.left(c), 9)
        self.bridge.close_window()
        calls = [("click", {"x": 1, "y": 1}), ("move", {"x": 1, "y": 1}), ("drag", {"x": 1, "y": 1, "to_x": 2, "to_y": 2}),
                 ("scroll", {"x": 1, "y": 1, "dy": 1}), ("type", {"text": "a"}), ("key", {"keys": ["a"]}),
                 ("wait", {}), ("step", {"actions": [], "files": ["/agent/a"]})]
        for i, (tool, args) in enumerate(calls, 1):
            self.assertEqual(value(c.call(tool, **args))["steps_left"], 9 - i, tool)
            c.call("screenshot")
            self.assertEqual(self.left(c), 9 - i, tool)
        self.assertEqual(value(c.call("done")), {"steps_left": 0, "done": True})
        self.assertEqual(self.evaluated(), 9)

    def test_a_step_with_an_end_runs_its_actions_and_ends_the_task_as_in_version_1(self):
        c = self.start(steps=5)
        got = c.call("step", actions=["pyautogui.press('a')"], end="done", files=["/agent/a"], screenshot=True)
        self.assertEqual(value(got), {"steps_left": 4, "done": True, "files": {"/agent/a": None}})
        self.assertEqual(image(got), shot(1))
        self.assertEqual(self.evaluated(), 1)
        self.assertEqual(self.ran, ["pyautogui.press('a')", "DONE", ("evaluated", 1)])
        self.assertEqual(error(c.call("screenshot")), "the task has ended (done)")

    def test_the_budget_and_the_turns_end_hold_as_in_version_1(self):
        c = self.start(steps=1)
        self.assertEqual(value(c.call("click", x=1, y=1)), {"steps_left": 0, "done": True})
        self.assertEqual(error(c.call("click", x=1, y=1)), "the steps are used up")
        self.assertEqual(value(c.call("info"))["steps_left"], 0)
        c = self.start(steps=4)
        c.call("click", x=1, y=1)
        self.bridge.end_turn()
        self.assertEqual(self.evaluated(), 4)
        self.assertEqual(error(c.call("click", x=1, y=1)), "the agent's turn has ended")


if __name__ == "__main__":
    unittest.main()
