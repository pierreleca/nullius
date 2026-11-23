"""Cua Driver Bench's entry: its declarations, and its driver over a stand-in for its desktop module, with no engine
and no container. The stand-in has the module's names, arguments, returns and exceptions; its ``python`` really runs
the code it is given, with a stand-in ``pyautogui`` that records each call, so that the port's own code runs
unchanged; its launcher returns a task's text and, at the trial's end, a row this file writes in the launcher's
shape. Covered: the units and their order, the attempt's run index, the task's text, the options, each way a trial
ends and the verdict then, a harness error, and the desktop's removal after a failure at each stage."""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

from nullius.catalogue import catalog
from nullius.standard.protocols import computer_port as port
from nullius.standard.contract import definitions
from nullius.standard.runner import payload, retries
from nullius.standard.runner import run as standard_run

ENTRY = os.path.join(definitions.CATALOGUE, "benchmarks", "cua-driver-bench")


def _module(name, file):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ENTRY, file))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


driver = _module("cua_driver_bench_driver_under_test", "driver.py")

with open(os.path.join(ENTRY, "standard.json"), encoding="utf-8") as _f:
    DEFINITION = json.load(_f)
with open(os.path.join(ENTRY, "benchmark.json"), encoding="utf-8") as _f:
    LISTING = json.load(_f)
with open(os.path.join(ENTRY, "upstream-settings.json"), encoding="utf-8") as _f:
    SETTINGS = json.load(_f)

# A task list in the shape of the bench's: ten tasks, two of them in the foreground alone; the words are this file's
TASKS = [("CDB-G02", "multi_app"), ("CDB-G03", "multi_app"), ("CDB-G04", "multi_app"), ("MB-09", "precision"),
         ("MB-10", "precision"), ("MB-11", "precision"), ("IR-01", "interruptions"), ("IR-02", "interruptions"),
         ("IR-03", "interruptions"), ("IR-04", "interruptions")]
UNITS = [f"{t}-foreground" for t, _ in TASKS]
BACKGROUND = [f"{t}-background" for t, _ in TASKS if t not in ("MB-10", "MB-11")]
# all of them: each task's foreground unit, then its background unit when it has one
BOTH = [u for t, _ in TASKS for u in (f"{t}-foreground", f"{t}-background") if u in UNITS + BACKGROUND]
BRIEF = "A list is open on the screen.\n\nPut its items in the order shown, then press Done.\n"


def task_list():
    return {"schema": "cdb-taskset/1", "name": "v2", "limits": {"timeout_s": 360, "max_turns": 45},
            "tasks": [{"id": t, "category": c, "split": "public", "set": "v2", "title": f"Task {i} of the list",
                       "apps": [f"App {i}", "A second app"] if c == "multi_app" else [f"App {i}"],
                       "conditions": ["foreground"] if t in ("MB-10", "MB-11") else ["foreground", "background"]}
                      for i, (t, c) in enumerate(TASKS, 1)]}


def seed(task, run_index):
    """The seed rule the task list states: the first 8 hex digits of sha256("TASK:INDEX"), modulo 10^6."""
    return int(hashlib.sha256(f"{task}:{run_index}".encode()).hexdigest()[:8], 16) % 1000000


CRITERIA = ("key_focus", "window_raised", "typing", "pointer", "clipboard", "space", "dialog", "activation")


def row(task, run_index, condition="foreground", *, checker=False, score=0.25, checks=None, violations=(),
        timed_out=False, harness_error=None):
    """A trial's row in the shape the launcher writes it: an early one with its harness error, or a whole one."""
    r = {"task": task, "run_index": run_index, "seed": seed(task, run_index), "condition": condition,
         "mode": "mode-of-the-task", "platform": "linux-xfce", "label": "", "started": 1000.0, "screen": [1920, 1080]}
    if harness_error:
        return dict(r, harness_error=harness_error, finished=1010.0)
    checks = checks if checks is not None else {"first": {"passed": checker, "weight": 0.5, "detail": "as found"},
                                                "second": {"passed": True, "weight": 0.5, "detail": "kept"}}
    guard = [dict(v) for v in violations]
    r.update(pointer_parked={"target": [1000, 12], "read_back": [1000.0, 12.0], "ok": True, "method": "xdotool"},
             sentinel_front_at_start=None, frames={"Desktop": [0, 0, 1920, 1080]}, agent_exit=0, agent_wall_s=12.5,
             timed_out=timed_out,
             evaluation={"passed": checker, "score": 1.0 if checker else score, "checks": checks, "diagnostics": {}},
             checker_passed=checker,
             gui_only={"violation": bool(guard), "violations": guard, "mentions": [], "frontmost_seen": []},
             passed=checker and not guard,
             disturbance={"front_changes": 0, "samples": 40, "available": False},
             background={"measured": False, "focus_steals": 0, "clean": False},
             a15={"condition": condition, "user_window_held": False, "background_completion": None,
                  "background_completion_pointer_still": None, "success_regardless_of_focus": checker, "focus_steals": 0,
                  "user_input_disrupted": None, "user_activity": None, "overlay_owners": []},
             a22={"rule": "A22", "condition": condition, "criteria": {c: c != "typing" or None for c in CRITERIA},
                  "unmeasured": ["typing"], "user_undisturbed": False, "background_completion": None},
             finished=1100.0)
    return r


def bg_row(task, run_index, *, checker=False, violations=(), disturbed=(), unmeasured=(), measured=True, samples=50):
    """A background trial's row as the launcher writes it: the eight measures held but those ``disturbed`` (False)
    and those ``unmeasured`` (None); ``measured`` False for a trial too short for the bench to measure at all, whose
    measures still read as held, as the bench writes them. The pass rule is the bench's: the check, no violation, the
    measures measured and every one held."""
    r = row(task, run_index, "background", checker=checker, violations=violations)
    criteria = {c: None if c in unmeasured else c not in disturbed for c in CRITERIA}
    held = measured and all(v is True for v in criteria.values())
    r.update(disturbance={"front_changes": 0, "samples": samples, "available": measured},
             background={"measured": measured, "focus_steals": 0, "clean": measured and not disturbed},
             a22={"rule": "A22", "condition": "background", "criteria": criteria,
                  "unmeasured": sorted(unmeasured), "user_undisturbed": held,
                  "background_completion": bool(r["passed"] and held)})
    r["a15"].update(background_completion=bool(r["passed"] and held), user_window_held=held)
    return r


STUB = r'''
import json, os
FAILSAFE = True
_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "input.jsonl")


def _named(name):
    def call(*args, **kwargs):
        with open(_LOG, "a") as f:
            f.write(json.dumps({"call": name, "args": list(args), "kwargs": kwargs}) + "\n")
    return call


for _name in ("click", "moveTo", "dragTo", "scroll", "hscroll", "write", "hotkey", "press", "mouseDown", "mouseUp"):
    globals()[_name] = _named(_name)


def size():
    return (1920, 1080)
'''
PNG = b"\x89PNG\r\n\x1a\n" + b"stand-in"


class StandIn:
    """The desktop module's interface, over folders of the test's own: what each call was given, what it returns,
    which call fails (``fail``: a call's name -> the exception it raises)."""

    def __init__(self, tmp):
        self.tmp = tmp
        self.stub, self.home = os.path.join(tmp, "stub"), os.path.join(tmp, "desktop", "home")
        os.makedirs(self.stub)
        os.makedirs(self.home)                                       # where the desktop's code runs
        with open(os.path.join(self.stub, "pyautogui.py"), "w") as f:
            f.write(STUB)
        self.made, self.images, self.launched, self.benches, self.clients, self.collected = [], [], [], [], [], []
        self.fail, self.ended, self.brief = {}, threading.Event(), BRIEF
        self.rows = lambda task, run_index, condition: row(task, run_index, condition)
        m = self.module = types.ModuleType("cdb_desktop_stand_in")
        m.SCREEN, m.FOLDER = (1920, 1080), os.path.join(tmp, "desktop", "agent")

        class DesktopError(Exception):
            pass

        class HarnessError(Exception):
            def __init__(self, message, row=None):
                super().__init__(message)
                self.row = row

        class Refused(Exception):
            pass
        m.DesktopError, m.HarnessError, m.Refused = DesktopError, HarnessError, Refused
        stand = self

        def image(option, upstream, log_dir):
            stand.images.append((option, upstream, log_dir))
            stand.raise_if("image")
            return f"image-{option}", "sha256:" + "1" * 64

        def end_clients(closing=True):
            stand.clients.append(closing)
            return 0

        class Desktop:
            @classmethod
            def start(cls, reference, *, job, seconds=180, memory_mb=None):
                stand.raise_if("start")                     # a failed start leaves nothing
                d = cls(reference, job)
                d.memory_mb = memory_mb
                stand.made.append(d)
                return d

            def __init__(self, reference, job):
                self.reference, self.job, self.removes, self.captures = reference, job, 0, 0
                self.folder = os.path.join(stand.tmp, "desktop", f"trial-{len(stand.made)}")
                os.makedirs(self.folder)
                self.row = None

            def put_bench(self, folder):
                stand.benches.append(folder)
                stand.raise_if("put_bench")

            def launch(self, task, run_index, condition, *, seconds=300):
                stand.launched.append((task, run_index, condition))
                with open(os.path.join(self.folder, "brief.md"), "w") as f:
                    f.write(stand.brief)
                if "launch" in stand.fail:
                    e = stand.fail["launch"]
                    if isinstance(e, HarnessError) and e.row is not None:
                        with open(os.path.join(self.folder, "row.json"), "w") as f:
                            json.dump(e.row, f)
                    raise e
                self.task = (task, run_index, condition)
                return stand.brief

            def launcher_ended(self):
                return stand.ended.is_set()

            def python(self, code, seconds=90):
                stand.raise_if("python", code)
                try:
                    p = subprocess.run([sys.executable, "-I", "-c", f"import sys\nsys.path.insert(0, {stand.stub!r})\n"
                                        + code], capture_output=True, text=True, timeout=seconds, stdin=subprocess.DEVNULL,
                                       cwd=stand.home)
                except subprocess.TimeoutExpired:
                    return None
                return {"status": "success", "output": p.stdout, "error": p.stderr, "returncode": p.returncode}

            def upload(self, path, data):
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "wb") as f:
                    f.write(data)

            def read(self, path):
                if not os.path.lexists(path):
                    return None
                if os.path.islink(path) or not os.path.isfile(path):
                    raise Refused(f"{path} is not a plain file")
                if os.path.getsize(path) > 12 * 2 ** 20:
                    raise Refused(f"{path} is over 12 MiB")
                with open(path, "rb") as f:
                    return f.read()

            def capture(self):
                self.captures += 1
                return PNG

            def finish(self, *, seconds=420):
                if self.row is None:
                    stand.raise_if("finish")
                    self.row = stand.rows(*self.task)
                    with open(os.path.join(self.folder, "row.json"), "w") as f:
                        json.dump(self.row, f)
                return self.row

            def collect(self, dest, *, max_bytes=64 * 2 ** 20):
                """As the module's: plain files alone, no link followed in the trial's folder or written through in
                ``dest``, a file that does not fit in ``max_bytes`` left out."""
                stand.collected.append(max_bytes)
                stand.raise_if("collect")
                os.makedirs(dest, exist_ok=True)
                kept, total = [], 0
                for name in sorted(os.listdir(self.folder)):
                    src = os.path.join(self.folder, name)
                    if os.path.islink(src) or not os.path.isfile(src) or total + os.path.getsize(src) > max_bytes:
                        continue
                    try:
                        fd = os.open(os.path.join(dest, name), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
                    except OSError:
                        continue
                    with os.fdopen(fd, "wb") as out, open(src, "rb") as f:
                        shutil.copyfileobj(f, out)
                    kept.append(name)
                    total += os.path.getsize(src)
                return kept

            def remove(self):
                self.removes += 1
                stand.raise_if("remove")

        m.image, m.end_clients, m.Desktop = image, end_clients, Desktop

    def raise_if(self, name, code=None):
        e = self.fail.get(name)
        if e is not None and (code is None or "pyautogui.click" in code):
            raise e

    def input(self):
        """The pyautogui calls the desktop's code made, in order."""
        path = os.path.join(self.stub, "input.jsonl")
        if not os.path.exists(path):
            return []
        with open(path) as f:
            return [json.loads(line) for line in f]


class Client:
    """An MCP client over the unit's socket: one call at a time, its answer waited for."""

    def __init__(self, path):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(30)
        self.sock.connect(path)
        self.reader, self.n = self.sock.makefile("rb"), 0
        self.ask("initialize", {"protocolVersion": port.PROTOCOL, "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})

    def ask(self, method, params):
        self.n += 1
        self.sock.sendall(port.frame({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params}))
        while True:
            m = json.loads(self.reader.readline())
            if m.get("id") == self.n:
                return m

    def call(self, tool, arguments=None):
        """``(isError, value)``: the result's first text, read as JSON when it is."""
        r = self.ask("tools/call", {"name": tool, "arguments": arguments or {}})["result"]
        text = next((c["text"] for c in r["content"] if c["type"] == "text"), None)
        try:
            value = json.loads(text) if text is not None else None
        except ValueError:
            value = text
        return r["isError"], value

    def close(self):
        self.reader.close()
        self.sock.close()


def until(done, seconds=10.0):
    t = time.monotonic() + seconds
    while not done():
        if time.monotonic() > t:
            raise AssertionError("not within the time")
        time.sleep(0.02)


# -- the declarations ---------------------------------------------------------------------------------------------------
class Declarations(unittest.TestCase):
    def test_the_definition_holds_and_declares_the_foreground_of_ten_tasks(self):
        d = dict(DEFINITION, _here=ENTRY)
        self.assertEqual(definitions.problems(DEFINITION, "benchmark"), [])
        self.assertEqual(definitions.settings_problems([d]), [])
        self.assertEqual(DEFINITION["needs"], ["agent.computer/v1"])
        self.assertEqual(DEFINITION["upstream"]["tested"], "c6350de75e054811c0042da34fc9087b39d63151")
        # one dataset of both conditions and the two conditions as its own task sets; the foreground first, a run's
        # when it names none
        self.assertEqual([(x["id"], x["units"], x.get("subset_of"), x["path"]) for x in DEFINITION["datasets"]],
                         [("linux_foreground", 10, "linux", "libs/cua-driver-bench/taskpack/v2-taskset.json"),
                          ("linux_background", 8, "linux", "libs/cua-driver-bench/taskpack/v2-taskset.json"),
                          ("linux", 18, None, "libs/cua-driver-bench/taskpack/v2-taskset.json")])
        self.assertEqual(len({x["sha256"] for x in DEFINITION["datasets"]}), 1)
        self.assertEqual(list(driver.CONDITIONS), [x["id"] for x in DEFINITION["datasets"]])
        self.assertEqual(DEFINITION["limits"]["turn_seconds"], driver.AGENT_SECONDS)     # what the builder shows
        drv = DEFINITION["driver"]
        self.assertTrue(drv["participant"] and drv["unit_end"] and drv["describe"] and drv["task_given"])
        self.assertNotIn("install", drv)                             # the standard library and the nullius package
        # the command names the files a host's snapshot takes: each of them in the entry's folder, the driver first,
        # then the desktop module, which the driver takes from its first argument
        self.assertEqual(drv["command"][:2], ["python3", "{here}/driver.py"])
        named = [w[len("{here}/"):] for w in drv["command"][2:]]
        self.assertEqual(named, ["desktop.py", "reaper.py", "env/desktop.Dockerfile", "env/desktop-requirements.txt"])
        self.assertTrue(all(os.path.isfile(os.path.join(ENTRY, n)) for n in named), named)
        # a host's snapshot of the entry: the driver loads the desktop module by its path, which no import names, and
        # the module builds its image from the two files of its env folder
        files = set(payload._entry(pathlib.Path(ENTRY)))
        self.assertTrue({"standard.json", "benchmark.json", "driver.py", *named} <= files, sorted(files))
        self.assertEqual(DEFINITION["mcp_servers"]["computer"]["command"], ["python3"])
        self.assertEqual(DEFINITION["limits"]["scored_cuts"], ["time", "calls", "end"])
        self.assertTrue(DEFINITION["limits"]["attempts"])
        self.assertEqual(DEFINITION["score"]["kind"], "pass")
        self.assertEqual(set(DEFINITION["controls"]), {"nop"})
        self.assertNotIn("leaderboard", DEFINITION)

    def test_the_options_and_their_defaults(self):
        opts = DEFINITION["options"]
        self.assertEqual({n: o["default"] for n, o in opts.items()}, {"max_steps": driver.STEPS, "pause": driver.PAUSE,
                                                                     "desktop_image": "build",
                                                                     "desktop_memory": driver.MEMORY})
        for name, good, bad in (("max_steps", ("1", "45", "999"), ("0", "1000", "default")),
                                ("pause", ("0", "1.0", "2.5"), ("10", "-1", "1.234")),
                                ("desktop_memory", ("8g", "1g", "512m", "999g"), ("0g", "8", "8G", "50m", "1000g", "8gb")),
                                ("desktop_image", ("build", "desktop-copy:7", "cdb-desktop"), ("ghcr.io/trycua/linux:24.04", "Build", ""))):
            for v in good:
                self.assertIsNone(definitions.option_problem(opts[name], v), (name, v))
            for v in bad:
                self.assertIsNotNone(definitions.option_problem(opts[name], v), (name, v))
        self.assertTrue(opts["max_steps"]["limit"])
        self.assertEqual(opts["pause"]["setups"], ["agent"])
        # the port's, as the builder's Limits shows a bound for an agent's run; the null control is given it and
        # takes no step
        self.assertNotIn("setups", opts["max_steps"])
        self.assertNotIn("setups", opts["desktop_image"])            # the null control's desktop too
        self.assertNotIn("setups", opts["desktop_memory"])
        vals, bad = definitions.bench_options_of(DEFINITION, "linux_foreground", {}, "yours", kind="control")
        self.assertEqual((vals, bad), ({"max_steps": "45", "desktop_image": "build", "desktop_memory": "8g"}, []))
        vals, bad = definitions.bench_options_of(DEFINITION, "linux_foreground", {}, "yours", kind="agent")
        self.assertEqual((vals, bad), ({"max_steps": "45", "pause": "1.0", "desktop_image": "build",
                                        "desktop_memory": "8g"}, []))
        self.assertEqual(definitions.option_needs(DEFINITION, {"desktop_image": "build"})["host"], ["docker"])

    def test_the_page_says_what_the_entry_runs_and_what_it_cannot_enforce(self):
        cat = catalog.load(catalog.ROOT)
        b = cat.benchmark("cua-driver-bench")
        self.assertIsNotNone(b)
        self.assertEqual(catalog.validate(cat), [])
        self.assertEqual((b.category, b.origin, b.harness), ("Computer use", "official", ["standard"]))
        self.assertIn("cua-driver-bench", {x.key for x in cat.listed_benchmarks})
        said = " ".join(LISTING["good_to_know"] + LISTING["limitations"] + LISTING["upstream_limitations"])
        for words in ("any agent that holds the computer port's proof", "published comparison",
                      "cannot enforce", "FSL-1.1-MIT", "Nothing of the bench is copied",
                      "simulated user's window stays in front", "The background's pass is the bench's full one",
                      "adds passes of two different measures", "passes no background task"):
            self.assertIn(words, said)
        self.assertNotIn("not offered yet", said)
        self.assertEqual(LISTING["datasets"][0]["tasks"], 18)
        # the pages count each trial's task checks by name from the driver's file; the measures are the detail's
        self.assertEqual(b.report["file"], os.path.join("verifier", "report.json"))
        self.assertEqual((b.report["gained"], b.report["words"]["gained"]), ({"pass": ["passed"], "fail": ["failed"]}, "Checks"))
        self.assertEqual(b.report["words"]["gained_term"], "The task's named checks:")
        self.assertNotIn("held", b.report)                               # no tests that a change could break
        # the Artifacts tab lists the launcher's folder copied into the trial and the loop's two records of actions
        self.assertEqual([(r["label"], r["path"]) for r in b.records], [
            ("Trial folder", "verifier/cdb"), ("Actions run", "verifier/computer-actions.jsonl"),
            ("Actions before the task", "verifier/computer-prepare.jsonl")])
        self.assertEqual({r["path"] for r in b.records}, {driver.EVIDENCE, driver.ACTIONS, driver.PREPARED})

    def test_the_settings_name_where_the_bench_states_its_rules(self):
        rows = {(r["entry"], r["setting"]): r for r in SETTINGS}
        prompt = next(r for (e, s), r in rows.items() if e.endswith("claude_arms.py"))
        self.assertIn(driver.RULES.split(". ")[0], prompt["default"])
        self.assertTrue(any(e.endswith("run_bench.py") and "tell-budget" in s for e, s in rows))
        launcher = [s for e, s in rows if e.endswith("launch/xfce_session.py")]
        for flag in ("--task", "--run-index", "--condition", "--out", "--label", "--timeout", "--no-enforce"):
            self.assertTrue(any(s.startswith(flag) for s in launcher), flag)


# -- the driver -----------------------------------------------------------------------------------------------------------
class Driver(unittest.TestCase):
    """Units of the driver over the stand-in, each trial in a folder of its own; every test ends with each desktop
    started removed once."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(dir=os.environ.get("TMPDIR"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.stand = StandIn(self.tmp)
        self.addCleanup(self.each_removed_once)
        up = os.path.join(self.tmp, "upstream")
        self.upstream = up
        os.makedirs(os.path.join(up, "libs", "cua-driver-bench", "taskpack"))
        self.dataset = os.path.join(up, "libs", "cua-driver-bench", "taskpack", "v2-taskset.json")
        with open(self.dataset, "w") as f:
            json.dump(task_list(), f)
        for t, _ in TASKS:
            self.spec(t)
        self.enterContext(mock.patch.dict(os.environ, {"NULLIUS_DATASET": self.dataset, "NULLIUS_UPSTREAM": up,
                                                       "NULLIUS_OPTION_PAUSE": "0"}))
        for name in ("NULLIUS_OPTION_MAX_STEPS", "NULLIUS_OPTION_DESKTOP_IMAGE", "NULLIUS_OPTION_DESKTOP_MEMORY"):
            os.environ.pop(name, None)
        self.enterContext(mock.patch.object(driver, "_DESKTOP", self.stand.module))
        self.enterContext(mock.patch.object(driver, "WATCH_SECONDS", 0.02))
        self.enterContext(mock.patch.dict(driver.OPEN, {}, clear=True))
        self.addCleanup(driver.close_all)
        self.n = 0

    def spec(self, task, **extra):
        folder = os.path.join(self.upstream, driver.TASK_SPECS, task)
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "task.json"), "w") as f:
            json.dump(dict({"id": task, "timeout_s": 360, "max_turns": 45}, **({"browser": True} if task == "CDB-G04" else {}),
                           **extra), f)

    def each_removed_once(self):
        self.assertEqual([d.removes for d in self.stand.made], [1] * len(self.stand.made))

    def workspace(self):
        self.n += 1
        ws = os.path.join(self.tmp, "job", f"trial-{self.n}", "workspace")
        os.makedirs(ws)
        return ws

    def unit(self, unit="MB-09-foreground", given=True, **kw):
        ws = self.workspace()
        s = driver.setup(unit, ws, **dict({"attempt": 1, "participant": "agent"}, **kw))
        if given and kw.get("participant", "agent") == "agent":
            driver.task_given(unit, ws)
        return ws, s

    def verifier(self, ws, name):
        return os.path.join(os.path.dirname(ws), "verifier", name)

    def progress(self, ws):
        try:
            with open(self.verifier(ws, "computer-progress.json")) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    # -- the listing ----------------------------------------------------------------------------------------------
    def test_the_fixtures_seed_rule_gives_the_launchers_seeds_of_run_index_0(self):
        self.assertEqual([seed(t, 0) for t, _ in TASKS], [909557, 99589, 482045, 790113, 227159, 160721, 517139,
                                                          565606, 410255, 816494])

    def test_the_ten_units_in_the_task_lists_order(self):
        self.assertEqual(driver.units("linux_foreground"), {"units": UNITS})
        self.assertEqual(driver.units(), {"units": UNITS})
        self.assertEqual(len(UNITS), DEFINITION["datasets"][0]["units"])
        with self.assertRaises(ValueError):
            driver.units("linux_elsewhere")

    def test_the_eighteen_units_in_pairs_and_the_two_lists_cut_from_them(self):
        self.assertEqual(driver.units("linux_background"), {"units": BACKGROUND})
        got = driver.units("linux")["units"]
        self.assertEqual(got, BOTH)
        self.assertEqual(got[:5], ["CDB-G02-foreground", "CDB-G02-background", "CDB-G03-foreground",
                                   "CDB-G03-background", "CDB-G04-foreground"])
        self.assertEqual(got[8:12], ["MB-10-foreground", "MB-11-foreground", "IR-01-foreground", "IR-01-background"])
        declared = {x["id"]: x["units"] for x in DEFINITION["datasets"]}
        self.assertEqual((len(got), len(BACKGROUND)), (declared["linux"], declared["linux_background"]))
        self.assertEqual(set(UNITS) | set(BACKGROUND), set(got))       # each list inside the whole, nothing more
        self.assertEqual([u for u in got if u.endswith("-background")], BACKGROUND)
        # sorted by name, as a job's table sorts its trials, each task's units still stand together
        tasks = [u.rpartition("-")[0] for u in sorted(got)]
        self.assertEqual(tasks, sorted(tasks))
        self.assertEqual(sorted(got)[:3], ["CDB-G02-background", "CDB-G02-foreground", "CDB-G03-background"])
        # no double underscore, after which the live board would show the condition alone: each name reads whole
        self.assertEqual([u for u in got if "__" in u], [])
        said = driver.describe("linux")["units"]
        self.assertEqual(list(said), BOTH)
        fg, bg = said["IR-02-foreground"], said["IR-02-background"]
        self.assertEqual((fg["title"], fg["text"]), (bg["title"], bg["text"]))         # one task, two conditions
        self.assertEqual((fg["categories"], bg["categories"]), ({"category": "interruptions", "condition": "foreground"},
                                                                 {"category": "interruptions", "condition": "background"}))

    def test_describe_reads_the_task_list_alone(self):
        with mock.patch.object(driver, "_DESKTOP", None), mock.patch.dict(os.environ, {"NULLIUS_UPSTREAM": "/nowhere"}):
            got = driver.describe("linux_foreground")["units"]
            self.assertIsNone(driver._DESKTOP)
        self.assertEqual(list(got), UNITS)
        self.assertEqual(got["CDB-G02-foreground"], {"title": "Task 1 of the list", "text": "Task 1 of the list. "
                                                      "Applications: App 1; A second app.",
                                                      "categories": {"category": "multi_app", "condition": "foreground"}})
        self.assertEqual(got["IR-04-foreground"]["categories"], {"category": "interruptions", "condition": "foreground"})

    def test_a_unit_the_task_list_does_not_hold_is_refused_before_any_desktop(self):
        for unit in ("MB-10-background", "MB-11-background", "XX-99-foreground", "MB-09", "../MB-09-foreground",
                     "MB-09-sideways", "foreground-MB-09", "background-IR-02",         # the condition comes last
                     "MB-09__foreground", "IR-02__background", "foreground__MB-09",    # another joint, another case
                     "MB-09_foreground", "MB-09-Foreground", "MB-09--foreground", "MB-09-foreground-"):
            with self.subTest(unit=unit), self.assertRaises(ValueError):
                driver.setup(unit, self.workspace(), attempt=1, participant="agent")
        self.assertEqual((self.stand.images, self.stand.made), ([], []))

    # -- a trial ----------------------------------------------------------------------------------------------------
    def test_an_agents_trial_is_served_scored_and_its_desktop_removed(self):
        self.stand.rows = lambda task, i, c: row(task, i, c, checker=True)
        ws, s = self.unit(given=False)
        pipe, sock = s["mcp"][0]["args"]
        self.assertEqual(s["mcp"], [{"name": "computer", "server": "computer", "args": [pipe, sock]}])
        self.assertEqual(pipe, os.path.join(os.path.dirname(port.__file__), "computer_pipe.py"))
        self.assertTrue(os.path.isfile(pipe))
        self.assertEqual((s["agent_seconds"], s["progress"]), (360, os.path.join("verifier", "computer-progress.json")))
        self.assertEqual(self.stand.images, [("build", self.upstream, os.path.join(os.path.dirname(ws), "verifier"))])
        self.assertEqual(self.stand.benches, [os.path.join(self.upstream, "libs", "cua-driver-bench")])
        self.assertEqual(self.stand.launched, [("MB-09", 0, "foreground")])
        self.assertEqual(self.stand.made[0].job, "job")
        c = Client(sock)
        failed, info = c.call("info")
        self.assertEqual((failed, info["screen"], info["steps"], info["folder"], info["password"]),
                         (False, [1920, 1080], 45, self.stand.module.FOLDER, ""))
        self.assertIn("not been given", str(c.call("click", {"x": 10, "y": 20})[1]))     # the window before the task
        driver.task_given("MB-09-foreground", ws)
        self.assertEqual(c.call("click", {"x": 10, "y": 20}), (False, {"steps_left": 44, "done": False}))
        self.assertEqual(c.call("done", {"message": "ordered"}), (False, {"steps_left": 43, "done": True}))
        c.close()
        self.assertEqual([x["call"] for x in self.stand.input()], ["click"])
        self.assertEqual(self.stand.input()[0]["args"], [10, 20])
        got = driver.score("MB-09-foreground", ws, [], "ordered")
        self.assertEqual((got["reward"], got["passed"]), (1.0, True))
        d = got["detail"]
        self.assertEqual((d["steps"], d["steps_limit"], d["ended"], d["condition"], d["image"]),
                         (2, 45, "done", "foreground", "sha256:" + "1" * 64))
        self.assertEqual((d["checker passed"], d["checks"], d["violations"], d["partial score"], d["timed out"]),
                         (True, {"first": True, "second": True}, [], 1.0, False))
        self.assertNotIn("measures held", d)                           # the foreground's pass takes no measure
        self.assertEqual(sorted(os.listdir(self.verifier(ws, "cdb"))), ["brief.md", "row.json"])
        self.assertEqual(self.stand.collected, [64 * 2 ** 20])        # the trial's folder within 64 MiB
        with open(self.verifier(ws, os.path.join("cdb", "row.json"))) as f:
            self.assertEqual(json.load(f)["task"], "MB-09")
        with open(self.verifier(ws, "computer-actions.jsonl")) as f:
            actions = [json.loads(line) for line in f]
        self.assertEqual([(a["step"], a["action"], a["returncode"]) for a in actions],
                         [(1, "pyautogui.click(10, 20, clicks=1, button='left')", 0)])
        with open(self.verifier(ws, "computer-calls.jsonl")) as f:
            self.assertEqual([json.loads(line)["tool"] for line in f], ["info", "click", "click", "done"])
        self.assertEqual(self.progress(ws), {"steps": 2, "steps_limit": 45, "ended": "done", "open": 0})
        self.assertFalse(os.path.exists(sock))
        self.assertEqual(driver.OPEN, {})
        self.assertEqual(self.stand.made[0].removes, 1)

    def test_the_run_index_and_the_seed_come_from_the_attempt(self):
        for attempt, index in ((1, 0), (3, 2), (None, 0)):
            with self.subTest(attempt=attempt):
                ws, _ = self.unit("CDB-G02-foreground", attempt=attempt)
                got = driver.score("CDB-G02-foreground", ws, [], "")
                self.assertEqual(self.stand.launched[-1], ("CDB-G02", index, "foreground"))
                self.assertEqual(got["detail"]["seed"], seed("CDB-G02", index))

    def test_the_task_text_is_the_launchers_then_the_server_and_the_benchs_two_rules(self):
        _, s = self.unit()
        self.assertEqual(s["prompt"], BRIEF.strip() + "\n\n" + driver.USE + "\n\nOperate only the applications the task "
                         "names. Do not use the network.\nYou have about 6 minutes and at most 45 turns.")
        self.assertIn("MCP server named computer", s["prompt"])
        _, s = self.unit("CDB-G04-foreground")                       # the task with a local site: the bench's variant
        self.assertTrue(s["prompt"].endswith("Operate only the applications the task names. Do not use the internet; "
                                             "the task's localhost site is allowed.\nYou have about 6 minutes and at most 45 turns."))
        self.spec("IR-01", timeout_s=600)
        os.environ["NULLIUS_OPTION_MAX_STEPS"] = "30"
        _, s = self.unit("IR-01-foreground")
        self.assertTrue(s["prompt"].endswith("You have about 10 minutes and at most 30 turns."))
        self.assertEqual(s["agent_seconds"], 600)
        for word in ("bench", "score", "test", "grade", "checker"):
            self.assertNotIn(word, driver.USE.lower())

    def test_the_options_reach_the_loop_and_the_image(self):
        os.environ.update(NULLIUS_OPTION_MAX_STEPS="7", NULLIUS_OPTION_PAUSE="0.05", NULLIUS_OPTION_DESKTOP_IMAGE="desktop-copy:7")
        ws, s = self.unit()
        u = driver.OPEN["MB-09-foreground"]
        self.assertEqual((u.bridge.steps, u.bridge.pause), (7, 0.05))
        self.assertEqual(self.stand.images[-1][0], "desktop-copy:7")
        self.assertEqual(self.stand.made[-1].reference, "image-desktop-copy:7")
        self.assertEqual(self.stand.made[-1].memory_mb, 8192)        # the engine's memory limit, by default
        c = Client(s["mcp"][0]["args"][1])
        self.assertEqual(c.call("info")[1]["steps"], 7)
        c.close()
        driver.score("MB-09-foreground", ws, [], "")
        os.environ["NULLIUS_OPTION_DESKTOP_MEMORY"] = "512m"
        ws, _ = self.unit(participant="control")
        self.assertEqual(self.stand.made[-1].memory_mb, 512)
        driver.score("MB-09-foreground", ws, [], "")
        os.environ["NULLIUS_OPTION_MAX_STEPS"] = "0"
        with self.assertRaises(ValueError):
            self.unit()
        os.environ.update(NULLIUS_OPTION_MAX_STEPS="7", NULLIUS_OPTION_DESKTOP_MEMORY="8")
        with self.assertRaises(ValueError):
            self.unit(participant="control")
        self.assertEqual(len(self.stand.made), 2)                    # refused before any desktop

    def test_the_pause_follows_each_action_and_each_wait(self):
        os.environ["NULLIUS_OPTION_PAUSE"] = "0.3"
        ws, s = self.unit()
        c = Client(s["mcp"][0]["args"][1])
        t0 = time.monotonic()
        failed, said = c.call("step", {"actions": ["pyautogui.click(1, 1)", "WAIT", "pyautogui.click(2, 2)"]})
        took = time.monotonic() - t0
        c.close()
        driver.score("MB-09-foreground", ws, [], "")
        self.assertFalse(failed, said)
        with open(self.verifier(ws, "computer-actions.jsonl")) as f:
            first, second = [json.loads(line) for line in f]
        self.assertGreaterEqual(second["start"] - first["end"], 0.59)      # the first click's pause, then the wait's
        self.assertGreaterEqual(took, 0.89)                                # and the pause after the last click

    # -- how a trial ends -------------------------------------------------------------------------------------------
    def end_by(self, calls, **kw):
        ws, s = self.unit(**kw)
        c = Client(s["mcp"][0]["args"][1])
        answers = [c.call(tool, args) for tool, args in calls]
        c.close()
        return ws, answers, driver.score(kw.get("unit", "MB-09-foreground"), ws, [], "")

    def test_done_fail_and_a_step_with_an_end_end_the_task(self):
        for calls, ended in (([("done", {})], "done"), ([("fail", {"reason": "no list"})], "fail"),
                             ([("step", {"actions": ["pyautogui.press('enter')"], "end": "done"})], "done")):
            with self.subTest(ended=ended, tool=calls[0][0]):
                ws, answers, got = self.end_by(calls)
                self.assertTrue(answers[-1][1]["done"])
                self.assertEqual((got["detail"]["ended"], got["detail"]["steps"]), (ended, 1))
                self.assertEqual(self.progress(ws)["ended"], ended)

    def test_the_steps_run_out_at_the_budget(self):
        os.environ["NULLIUS_OPTION_MAX_STEPS"] = "2"
        ws, answers, got = self.end_by([("click", {"x": 1, "y": 1}), ("wait", {}), ("click", {"x": 2, "y": 2})])
        self.assertEqual([a[0] for a in answers], [False, False, True])
        self.assertIn("used up", str(answers[2][1]))
        self.assertEqual((got["detail"]["steps"], got["detail"]["steps_limit"], got["detail"]["ended"]), (2, 2, "steps"))
        self.assertEqual(len(self.stand.input()), 1)                 # the wait ran no action

    def test_the_turns_end_ends_the_task_as_the_desktop_stands(self):
        ws, answers, got = self.end_by([("click", {"x": 5, "y": 5})])
        self.assertEqual((got["detail"]["ended"], got["detail"]["steps"], got["passed"]), ("turn", 1, False))
        self.assertIn("first", got["detail"]["why"])

    def test_a_check_passed_with_a_violation_fails(self):
        front = {"t_s": 41.0, "kind": "front", "detail": "a terminal"}
        self.stand.rows = lambda task, i, c: row(task, i, c, checker=True, violations=[front])
        _, _, got = self.end_by([("done", {})])
        self.assertEqual((got["reward"], got["passed"]), (0.0, False))
        d = got["detail"]
        self.assertEqual((d["checker passed"], d["violations"]), (True, ["front at 41 s: a terminal"]))
        self.assertEqual(d["why"], "the guard saw front: a terminal")
        self.assertEqual(list(d)[:2], ["why", "condition"])            # the trial's page shows the reason first

    def test_the_foreground_keeps_its_pass_and_its_detail(self):
        # the row's own pass (the check and no violation), whatever its block of measures says; no background field
        for checker, completion in ((True, None), (False, True)):
            with self.subTest(checker=checker):
                self.stand.rows = lambda task, i, c: dict(row(task, i, c, checker=checker),
                                                          a22=dict(row(task, i, c)["a22"], background_completion=completion))
                ws, _, got = self.end_by([("done", {})])
                self.assertEqual(got["passed"], checker)
                # in the order the trial's page shows them; the launcher's whole row stays in its records
                self.assertEqual([k for k in got["detail"] if k != "why"], [
                    "condition", "seed", "partial score", "checker passed", "violations", "timed out", "image", "checks",
                    "steps", "steps_limit", "ended"])
                with open(self.verifier(ws, os.path.join("cdb", "row.json"))) as f:
                    self.assertEqual(json.load(f)["a22"]["criteria"], row("MB-09", 0)["a22"]["criteria"])
                with open(self.verifier(ws, "report.json")) as f:
                    said = json.load(f)
                self.assertEqual((said["passed"], said["failed"]), (["first", "second"] if checker else ["second"],
                                                                     [] if checker else ["first"]))

    # -- the background ---------------------------------------------------------------------------------------------
    def background(self, rows, calls=(("done", {}),), **kw):
        """One trial of IR-02-background whose launcher writes ``rows``' row: its verdict and what the pages read."""
        self.stand.rows = rows
        ws, s = self.unit("IR-02-background", **kw)
        if "mcp" in s:
            c = Client(s["mcp"][0]["args"][1])
            for tool, args in calls:
                c.call(tool, args)
            c.close()
        got = driver.score("IR-02-background", ws, [], "")
        with open(self.verifier(ws, "report.json")) as f:
            return got, json.load(f)

    def test_a_background_trial_runs_the_launcher_in_its_condition_and_passes_as_the_bench_does(self):
        got, said = self.background(lambda task, i, c: bg_row(task, i, checker=True))
        self.assertEqual(self.stand.launched, [("IR-02", 0, "background")])
        self.assertEqual((got["reward"], got["passed"]), (1.0, True))
        d = got["detail"]
        self.assertEqual((d["condition"], d["checker passed"], d["violations"], d["measured"], d["samples"]),
                         ("background", True, [], True, 50))
        self.assertEqual(list(d["measures held"].items()), [(c, True) for c in CRITERIA])   # the eight, by name
        self.assertEqual([k for k in d], ["condition", "seed", "partial score", "checker passed", "violations", "measured",
                                          "samples", "timed out", "image", "checks", "measures held", "steps",
                                          "steps_limit", "ended"])
        self.assertEqual((said["condition"], said["seed"], said["score"]), ("background", seed("IR-02", 0), 1.0))
        # the pages count the task's checks; the measures are the detail's
        self.assertEqual((said["passed"], said["failed"]), (["first", "second"], []))

    def test_the_background_verdict_uses_a22_and_preserves_a15(self):
        r = bg_row("IR-02", 0, checker=True, disturbed=["pointer"])
        r["a15"]["background_completion"] = True
        self.stand.rows = lambda task, i, c: r
        ws, _ = self.unit("IR-02-background", participant="control")
        got = driver.score("IR-02-background", ws, [], "")
        self.assertEqual((got["reward"], got["passed"]), (0.0, False))
        self.assertEqual(got["detail"]["measures held"]["pointer"], False)
        with open(self.verifier(ws, os.path.join("cdb", "row.json"))) as f:
            self.assertEqual(json.load(f)["a15"], r["a15"])                     # the bench's older block, in its row

    def test_each_measure_that_does_not_hold_fails_a_passed_check(self):
        for m in CRITERIA:
            with self.subTest(measure=m):
                got, said = self.background(lambda task, i, c: bg_row(task, i, checker=True, disturbed=[m]))
                d = got["detail"]
                self.assertEqual((got["reward"], got["passed"]), (0.0, False))
                # the task's own success stays, whatever the disturbance
                self.assertEqual((d["checker passed"], d["violations"]), (True, []))
                self.assertEqual(d["measures held"], {c: c != m for c in CRITERIA})
                self.assertEqual(d["why"], f"the simulated user was disturbed: {m}")
                self.assertEqual((said["passed"], said["failed"]), (["first", "second"], []))

    def test_a_trial_too_short_to_be_measured_fails_as_the_row_has_it(self):
        got, said = self.background(lambda task, i, c: bg_row(task, i, checker=True, measured=False, samples=3))
        d = got["detail"]
        self.assertEqual((got["passed"], d["measured"], d["samples"], d["checker passed"]), (False, False, 3, True))
        # the row reads them held; unmeasured, none of them counts, and the page says so
        self.assertEqual(d["measures held"], {c: "not measured" for c in CRITERIA})
        self.assertEqual(d["why"], "the bench did not measure the simulated user's window (3 samples)")
        self.assertEqual(said["failed"], [])
        got, said = self.background(lambda task, i, c: bg_row(task, i, checker=True, unmeasured=["typing"]))
        self.assertEqual((got["passed"], got["detail"]["measures held"]), (False, {c: "not measured" if c == "typing" else True
                                                                                   for c in CRITERIA}))
        self.assertEqual(got["detail"]["why"], "the bench did not take the measure: typing")

    def test_a_failed_check_or_a_violation_fails_the_background_whatever_its_measures(self):
        got, said = self.background(lambda task, i, c: bg_row(task, i))
        self.assertEqual((got["passed"], got["detail"]["measures held"]), (False, {c: True for c in CRITERIA}))
        self.assertEqual(got["detail"]["why"], "the task's checker did not pass: first")
        self.assertEqual(said["failed"], ["first"])
        front = {"t_s": 3.0, "kind": "front", "detail": "a terminal"}
        got, _ = self.background(lambda task, i, c: bg_row(task, i, checker=True, violations=[front], disturbed=["pointer", "key_focus"]))
        self.assertEqual((got["passed"], got["detail"]["violations"]), (False, ["front at 3 s: a terminal"]))
        self.assertEqual([c for c, held in got["detail"]["measures held"].items() if not held], ["key_focus", "pointer"])
        self.assertEqual(got["detail"]["why"], "the guard saw front: a terminal; the simulated user was disturbed: key_focus, pointer")

    def test_the_null_control_runs_the_background_with_no_server(self):
        got, said = self.background(lambda task, i, c: bg_row(task, i), participant="control")
        self.assertEqual(self.stand.launched, [("IR-02", 0, "background")])
        self.assertEqual((got["reward"], got["detail"]["steps"], got["detail"]["measures held"]), (0.0, 0, {c: True for c in CRITERIA}))
        self.assertEqual(said["passed"], ["second"])

    def test_the_launchers_end_ends_the_loop_and_the_calls(self):
        guard = {"t_s": 20.0, "kind": "process", "detail": "a terminal", "pid": 4242}
        self.stand.rows = lambda task, i, c: row(task, i, c, violations=[guard])
        ws, s = self.unit()
        c = Client(s["mcp"][0]["args"][1])
        self.assertFalse(c.call("click", {"x": 1, "y": 1})[0])
        self.stand.ended.set()                                       # the guard stopped the gate; the launcher ended
        until(lambda: self.progress(ws).get("ended") == driver.LAUNCHER)
        failed, said = c.call("click", {"x": 2, "y": 2})
        self.assertTrue(failed)
        self.assertIn("ended", str(said))
        c.close()
        got = driver.score("MB-09-foreground", ws, [], "")
        self.assertEqual((got["passed"], got["detail"]["ended"], got["detail"]["steps"]), (False, driver.LAUNCHER, 1))
        self.assertIn("process: a terminal", got["detail"]["why"])
        self.assertEqual(len(self.stand.input()), 1)

    def test_the_launchers_end_reads_on_the_pages_in_the_form_of_their_other_ends(self):
        from nullius.runs import parse
        trial = os.path.join(self.tmp, "parsed", "MB-09-foreground__0001")
        os.makedirs(os.path.join(trial, "verifier"))
        with open(os.path.join(trial, "verifier", "score.json"), "w") as f:
            json.dump({"reward": 0.0, "detail": {"ended": driver.LAUNCHER}}, f)
        with open(os.path.join(trial, "result.json"), "w") as f:
            json.dump({"task_name": "cua-driver-bench/MB-09-foreground", "trial_name": "MB-09-foreground__0001",
                       "source": "cua-driver-bench", "standard": {"benchmark": "cua-driver-bench"},
                       "verifier_result": {"rewards": {"reward": 0.0}}}, f)
        t = parse.parse_trial("job", trial)
        self.assertEqual((t.ended_by, t.ended_label), (driver.LAUNCHER, driver.LAUNCHER))
        self.assertEqual(driver.LAUNCHER[:1], driver.LAUNCHER[:1].upper())     # as Done, Step limit, Task end read
        self.assertNotIn(driver.LAUNCHER.lower(), parse.ENDED_LABELS)

    def test_the_launchers_end_stops_a_step_between_its_actions(self):
        ws, s = self.unit()
        u = driver.OPEN["MB-09-foreground"]
        acting = u.d.python

        def slow(code, seconds=90):
            r = acting(code, seconds)
            if "pyautogui.click" in code:
                self.stand.ended.set()
                until(u.stopped.is_set)
            return r
        u.d.python = slow
        c = Client(s["mcp"][0]["args"][1])
        failed, said = c.call("step", {"actions": ["pyautogui.click(1, 1)", "pyautogui.click(2, 2)"]})
        c.close()
        self.assertTrue(failed)
        self.assertIn("not run to its end", str(said))
        self.assertEqual(len(self.stand.input()), 1)
        driver.score("MB-09-foreground", ws, [], "")

    # -- the verdict's errors ---------------------------------------------------------------------------------------
    def test_a_harness_error_in_the_row_is_the_environments_and_tried_again(self):
        self.stand.rows = lambda task, i, c: row(task, i, c, harness_error="evaluate: evaluator_no_result")
        ws, _ = self.unit()
        with self.assertRaisesRegex(driver.EnvironmentSetupError, "^the bench's launcher reports a harness error"
                                                                  ".*evaluator_no_result"):
            driver.score("MB-09-foreground", ws, [], "")
        self.assertTrue(retries.retryable({"exception_info": {"exception_type": "EnvironmentSetupError"}}))
        self.assertTrue(os.path.isfile(self.verifier(ws, os.path.join("cdb", "row.json"))))

    def test_a_row_the_launcher_wrote_without_its_check_is_the_environments(self):
        early = ("task", "run_index", "seed", "condition", "mode", "platform", "label", "started", "screen",
                 "pointer_parked", "sentinel_front_at_start", "frames")
        self.stand.rows = lambda task, i, c: dict({k: v for k, v in row(task, i, c).items() if k in early}, finished=1100.0)
        ws, _ = self.unit()
        with self.assertRaisesRegex(driver.EnvironmentSetupError, "^the bench's launcher ended without its check"):
            driver.score("MB-09-foreground", ws, [], "")
        self.assertTrue(os.path.isfile(self.verifier(ws, os.path.join("cdb", "row.json"))))

    def test_a_row_of_another_trial_or_none_is_the_verifiers(self):
        self.stand.rows = lambda task, i, c: row(task, i + 1, c)
        ws, _ = self.unit()
        with self.assertRaisesRegex(driver.VerifierError, "^the trial's row is of run_index 1, not 0"):
            driver.score("MB-09-foreground", ws, [], "")
        self.stand.fail["finish"] = self.stand.module.DesktopError("the launcher did not end in 420 s")
        ws, _ = self.unit()
        with self.assertRaisesRegex(driver.VerifierError, "^the trial's row could not be read: the launcher"):
            driver.score("MB-09-foreground", ws, [], "")
        self.assertTrue(os.path.isfile(self.verifier(ws, os.path.join("cdb", "brief.md"))))     # the folder copied all the same

    def test_the_null_control_runs_the_launcher_with_no_server(self):
        ws, s = self.unit(participant="control")
        self.assertEqual(set(s), {"prompt"})
        self.assertIsNone(driver.OPEN["MB-09-foreground"].bridge)
        with self.assertRaises(ValueError):
            driver.task_given("MB-09-foreground", ws)
        got = driver.score("MB-09-foreground", ws, [], "")
        self.assertEqual((got["reward"], got["detail"]["steps"], got["detail"]["steps_limit"], got["detail"]["ended"]),
                         (0.0, 0, 45, ""))
        self.assertEqual(self.stand.launched, [("MB-09", 0, "foreground")])

    # -- the desktop removed after a failure at each stage ------------------------------------------------------------
    def test_a_setup_that_fails_at_each_stage_leaves_no_desktop(self):
        m = self.stand.module
        harness = m.HarnessError("the launcher ended first", row=row("MB-09", 0, harness_error="sentinel did not start"))
        for stage, e in (("image", m.DesktopError("no engine")), ("start", m.DesktopError("no window manager")),
                         ("put_bench", m.DesktopError("copy failed")), ("launch", harness),
                         ("launch", m.DesktopError("no gate in 300 s"))):
            with self.subTest(stage=stage, e=str(e)):
                self.stand.fail = {stage: e}
                made = len(self.stand.made)
                ws = self.workspace()
                with self.assertRaises(Exception) as got:
                    driver.setup("MB-09-foreground", ws, attempt=1, participant="agent")
                self.assertEqual(driver.OPEN, {})
                self.assertEqual(len(self.stand.made), made + (stage not in ("image", "start")))
                self.assertTrue(all(d.removes == 1 for d in self.stand.made))
                if e is harness:
                    self.assertIn("sentinel did not start", str(got.exception))
                    self.assertTrue(os.path.isfile(self.verifier(ws, os.path.join("cdb", "row.json"))))
                else:
                    self.assertIs(got.exception, e)

    def test_a_server_that_cannot_start_leaves_no_desktop_and_no_socket(self):
        folders = []
        made = tempfile.mkdtemp

        def mkdtemp(**kw):
            folders.append(made(**kw))
            return folders[-1]
        with mock.patch.object(driver.tempfile, "mkdtemp", side_effect=mkdtemp), \
                mock.patch.object(port, "Server", side_effect=OSError("no socket")):
            with self.assertRaises(OSError):
                self.unit(given=False)
        self.assertEqual(driver.OPEN, {})
        self.assertFalse(os.path.exists(folders[0]))

    def test_a_copy_that_fails_keeps_the_verdict(self):
        self.stand.rows = lambda task, i, c: row(task, i, c, checker=True)
        self.stand.fail["collect"] = self.stand.module.DesktopError("a link in the folder")
        ws, _ = self.unit()
        got = driver.score("MB-09-foreground", ws, [], "")
        self.assertTrue(got["passed"])
        self.assertFalse(os.path.lexists(self.verifier(ws, "cdb")))

    def test_the_evidence_is_copied_within_the_drivers_bound_and_no_link_is_followed(self):
        self.assertEqual(driver.EVIDENCE_BYTES, 64 * 2 ** 20)
        ws, _ = self.unit()
        trial = self.stand.made[-1].folder
        outside = os.path.join(self.tmp, "outside")
        os.makedirs(outside)
        with open(os.path.join(outside, "elsewhere.txt"), "w") as f:
            f.write("not of the trial")
        os.symlink(os.path.join(outside, "elsewhere.txt"), os.path.join(trial, "link.txt"))
        with open(os.path.join(trial, "large.log"), "wb") as f:
            f.write(b"x" * 8192)
        for name in ("a.log", "b.log"):
            with open(os.path.join(trial, name), "wb") as f:
                f.write(b"x" * 3072)
        dest = self.verifier(ws, "cdb")
        os.makedirs(dest)
        os.symlink(os.path.join(outside, "written.txt"), os.path.join(dest, "row.json"))
        with mock.patch.object(driver, "EVIDENCE_BYTES", 4096):
            got = driver.score("MB-09-foreground", ws, [], "")
        self.assertEqual(self.stand.collected, [4096])
        copied = sorted(n for n in os.listdir(dest) if not os.path.islink(os.path.join(dest, n)))   # the planted link stays one
        self.assertEqual(copied, ["a.log", "brief.md"])
        total = sum(os.path.getsize(os.path.join(dest, name)) for name in copied)
        self.assertEqual(total, 3072 + len(BRIEF.encode()))
        self.assertLessEqual(total, 4096)
        self.assertFalse(os.path.lexists(os.path.join(dest, "b.log")))
        self.assertEqual(sorted(os.listdir(outside)), ["elsewhere.txt"])
        self.assertFalse(os.path.lexists(os.path.join(dest, "link.txt")))

    def test_the_report_replaces_a_link_and_writes_nothing_outside_the_trial(self):
        self.stand.rows = lambda task, i, c: row(task, i, c, checker=True)
        outside = os.path.join(self.tmp, "outside.json")
        with open(outside, "w") as f:
            f.write("not of the trial")
        ws, _ = self.unit()
        os.symlink(outside, self.verifier(ws, "report.json"))
        got = driver.score("MB-09-foreground", ws, [], "")
        self.assertTrue(got["passed"])
        with open(outside) as f:
            self.assertEqual(f.read(), "not of the trial")
        self.assertFalse(os.path.islink(self.verifier(ws, "report.json")))
        with open(self.verifier(ws, "report.json")) as f:
            self.assertEqual(json.load(f)["passed"], ["first", "second"])
        self.assertEqual([n for n in os.listdir(os.path.dirname(outside)) if n.startswith(".")], [])
        self.assertEqual([n for n in os.listdir(self.verifier(ws, "")) if n.startswith(".")], [])

    def test_a_linked_verifier_folder_leaves_the_report_unwritten_and_the_verdict_whole(self):
        self.stand.rows = lambda task, i, c: row(task, i, c, checker=True)
        ws, _ = self.unit()
        folder = os.path.dirname(self.verifier(ws, "report.json"))
        elsewhere = os.path.join(self.tmp, "elsewhere")
        shutil.copytree(folder, elsewhere, symlinks=True)
        shutil.rmtree(folder)
        os.symlink(elsewhere, folder)
        got = driver.score("MB-09-foreground", ws, [], "")
        self.assertTrue(got["passed"])
        self.assertFalse(os.path.lexists(os.path.join(elsewhere, "report.json")))

    def test_the_actions_record_replaces_a_link_and_writes_nothing_outside_the_trial(self):
        for link in (os.symlink, os.link):
            with self.subTest(link=link.__name__):
                outside = os.path.join(self.tmp, f"outside-{link.__name__}.jsonl")
                with open(outside, "w") as f:
                    f.write("not of the trial\n")
                ws, s = self.unit()
                link(outside, self.verifier(ws, "computer-actions.jsonl"))
                c = Client(s["mcp"][0]["args"][1])
                failed, said = c.call("step", {"actions": ["pyautogui.click(1, 1)", "pyautogui.click(2, 2)"]})
                c.close()
                self.assertFalse(failed, said)
                driver.score("MB-09-foreground", ws, [], "")
                with open(outside) as f:
                    self.assertEqual(f.read(), "not of the trial\n")
                self.assertFalse(os.path.islink(self.verifier(ws, "computer-actions.jsonl")))
                with open(self.verifier(ws, "computer-actions.jsonl")) as f:
                    self.assertEqual([json.loads(line)["action"] for line in f],
                                     ["pyautogui.click(1, 1)", "pyautogui.click(2, 2)"])
                self.assertEqual([n for n in os.listdir(self.verifier(ws, "")) if n.startswith(".")], [])

    def test_a_linked_verifier_folder_leaves_the_actions_unrecorded_and_the_steps_going(self):
        self.stand.rows = lambda task, i, c: row(task, i, c, checker=True)
        ws, s = self.unit()
        folder = os.path.dirname(self.verifier(ws, "computer-actions.jsonl"))
        elsewhere = os.path.join(self.tmp, "elsewhere")
        shutil.copytree(folder, elsewhere, symlinks=True)
        shutil.rmtree(folder)
        os.symlink(elsewhere, folder)
        c = Client(s["mcp"][0]["args"][1])
        self.assertEqual(c.call("click", {"x": 1, "y": 1}), (False, {"steps_left": 44, "done": False}))
        self.assertEqual(c.call("click", {"x": 2, "y": 2}), (False, {"steps_left": 43, "done": False}))
        c.close()
        got = driver.score("MB-09-foreground", ws, [], "")
        self.assertTrue(got["passed"])
        self.assertEqual(got["detail"]["steps"], 2)
        self.assertFalse(os.path.lexists(os.path.join(elsewhere, "computer-actions.jsonl")))
        self.assertEqual([x["args"] for x in self.stand.input()], [[1, 1], [2, 2]])

    def test_a_removal_that_fails_fails_the_score(self):
        ws, _ = self.unit(participant="control")
        u = driver.OPEN["MB-09-foreground"]
        d = u.d
        with mock.patch.object(d, "remove", side_effect=self.stand.module.DesktopError("the container is still there")) as remove:
            with self.assertRaisesRegex(Exception, "still there"):
                driver.score("MB-09-foreground", ws, [], "")
            self.assertIs(u.d, d)
            driver.end("MB-09-foreground", ws)
            self.assertEqual(remove.call_count, 2)
            self.assertIs(u.d, d)
        driver.close_all()
        self.assertIsNone(u.d)
        self.assertEqual(d.removes, 1)
        driver.close_all()
        self.assertEqual(d.removes, 1)
        self.assertEqual(driver.OPEN, {})

    def test_a_desktop_that_fails_during_the_task_is_the_environments(self):
        ws, s = self.unit()
        self.stand.fail["python"] = self.stand.module.DesktopError("the engine went away")
        self.stand.fail["finish"] = self.stand.module.DesktopError("the row is gone")
        c = Client(s["mcp"][0]["args"][1])
        self.assertTrue(c.call("click", {"x": 1, "y": 1})[0])
        c.close()
        with self.assertRaisesRegex(driver.EnvironmentSetupError, "^the desktop failed during the task: "
                                                                  ".*the engine went away"):
            driver.score("MB-09-foreground", ws, [], "")
        self.assertEqual(self.stand.made[-1].removes, 1)
        self.assertEqual(driver.OPEN, {})
        self.assertFalse(os.path.exists(s["mcp"][0]["args"][1]))
        self.assertTrue(os.path.isfile(self.verifier(ws, os.path.join("cdb", "brief.md"))))

    def test_a_desktop_gone_between_two_steps_is_the_environments(self):
        ws, s = self.unit()
        u = driver.OPEN["MB-09-foreground"]
        self.stand.fail["finish"] = self.stand.module.DesktopError("the launcher wrote no row.json")

        def gone(code, seconds=90):
            raise self.stand.module.DesktopError("no such container")
        u.d.python = gone                                            # no action ran: the loop saw nothing
        with self.assertRaisesRegex(driver.EnvironmentSetupError, "^the desktop failed during the task: "
                                                                  ".*no such container"):
            driver.score("MB-09-foreground", ws, [], "")
        self.assertEqual(self.stand.made[-1].removes, 1)

    def test_a_failed_score_probe_is_the_environments_and_a_healthy_one_is_the_verifiers(self):
        self.stand.fail["finish"] = self.stand.module.DesktopError("the launcher wrote no row.json")
        for reply, error in ((None, driver.EnvironmentSetupError),
                             ({"returncode": -9}, driver.EnvironmentSetupError),
                             ({"returncode": 0}, driver.VerifierError)):
            with self.subTest(reply=reply):
                ws, _ = self.unit(participant="control")
                d = driver.OPEN["MB-09-foreground"].d
                with mock.patch.object(d, "python", return_value=reply) as probe:
                    with self.assertRaises(error):
                        driver.score("MB-09-foreground", ws)
                probe.assert_called_once_with("pass", driver.PYTHON_SECONDS)
                self.assertEqual(d.removes, 1)
                self.assertTrue(os.path.isfile(self.verifier(ws, os.path.join("cdb", "brief.md"))))

    def test_a_unit_never_scored_goes_at_its_end_at_the_next_setup_and_at_the_drivers_exit(self):
        ws, s = self.unit()
        self.assertEqual(driver.end("MB-09-foreground", ws), {})
        self.assertEqual(self.stand.made[-1].removes, 1)
        self.assertFalse(os.path.exists(s["mcp"][0]["args"][1]))
        self.unit()
        self.unit("IR-02-foreground")                               # the one before never reached score
        self.assertEqual([d.removes for d in self.stand.made], [1, 1, 0])
        driver.close_all()
        self.assertEqual(self.stand.clients[-1], True)
        self.assertEqual(driver.OPEN, {})

    # -- the window before the task ------------------------------------------------------------------------------------
    def test_the_window_takes_uploads_and_prepare_and_files_sent_once_serve_the_run(self):
        data = b"a helper of the agent's own\n"
        digest = hashlib.sha256(data).hexdigest()
        folder = self.stand.module.FOLDER
        ws, s = self.unit(given=False)
        c = Client(s["mcp"][0]["args"][1])
        self.assertEqual(c.call("upload", {"path": "helper.txt", "data": base64.b64encode(data).decode(),
                                           "sha256": digest}), (False, {"size": len(data)}))
        failed, said = c.call("prepare", {"actions": ["open('made.txt', 'w').write('yes')"], "files": ["helper.txt"]})
        self.assertFalse(failed, said)
        self.assertEqual(base64.b64decode(said["files"]["helper.txt"]), data)
        self.assertTrue(os.path.isfile(os.path.join(self.stand.home, "made.txt")))     # run in the desktop
        driver.task_given("MB-09-foreground", ws)
        self.assertIn("has been given", str(c.call("upload", {"path": "late.txt", "data": "eA=="})[1]))
        c.close()
        driver.score("MB-09-foreground", ws, [], "")
        with open(self.verifier(ws, "computer-prepare.jsonl")) as f:
            self.assertEqual(json.loads(f.readline())["files"], ["helper.txt"])
        ws, s = self.unit("IR-01-foreground", given=False)           # the next unit of the run takes it by its digest
        c = Client(s["mcp"][0]["args"][1])
        self.assertEqual(c.call("upload", {"path": "helper.txt", "sha256": digest}), (False, {"size": len(data)}))
        c.close()
        with open(os.path.join(folder, "helper.txt"), "rb") as f:
            self.assertEqual(f.read(), data)
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "job", ".work", "cdb-held", digest)))
        driver.score("IR-01-foreground", ws, [], "")


# -- a whole run --------------------------------------------------------------------------------------------------------
# The desktop module's stand-in as a file the definition's command names, for a run whose driver is a process of its
# own: a trial whose row says passed, the port's code run with the recording pyautogui
MODULE = r'''
import json, os, subprocess, sys
ROOT = __ROOT__
ROW = json.loads(__ROW__)
SCREEN, FOLDER = (1920, 1080), os.path.join(ROOT, "agent")


class DesktopError(Exception):
    pass


class HarnessError(Exception):
    def __init__(self, message, row=None):
        super().__init__(message)
        self.row = row


class Refused(Exception):
    pass


def image(option, upstream, log_dir):
    return option, "sha256:" + "2" * 64


def end_clients(closing=True):
    return 0


class Desktop:
    @classmethod
    def start(cls, reference, *, job=None, seconds=180, memory_mb=None):
        return cls()

    def put_bench(self, folder):
        return 0

    def launch(self, task, run_index, condition, *, seconds=300):
        self.row = dict(ROW, task=task, run_index=run_index, condition=condition)
        return "Press the button on the screen.\n"

    def launcher_ended(self):
        return False

    def python(self, code, seconds=90):
        stub = "import sys\nsys.path.insert(0, %r)\n" % os.path.join(ROOT, "stub")
        p = subprocess.run([sys.executable, "-I", "-c", stub + code], capture_output=True, text=True, timeout=seconds,
                           stdin=subprocess.DEVNULL, cwd=ROOT)
        return {"status": "success", "output": p.stdout, "error": p.stderr, "returncode": p.returncode}

    def upload(self, path, data):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        return len(data)

    def read(self, path):
        if not os.path.isfile(path):
            return None
        with open(path, "rb") as f:
            return f.read()

    def capture(self):
        return b"\x89PNG\r\n\x1a\n" + b"stand-in"

    def finish(self, *, seconds=420):
        return self.row

    def collect(self, dest, *, max_bytes=64 * 2 ** 20):
        os.makedirs(dest, exist_ok=True)
        with open(os.path.join(dest, "row.json"), "w") as f:
            json.dump(self.row, f)
        return ["row.json"]

    def remove(self):
        with open(os.path.join(ROOT, "removed"), "a") as f:
            f.write("1\n")
'''
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "standard")


class ThroughARun(unittest.TestCase):
    """The entry run by Nullius as a whole: its driver a process of its own over the stand-in module, the scripted
    computer agent that looks, clicks once and then holds its turn, and the agent's time cut short."""

    def run_one(self, the_row, module=MODULE, retries=0, dataset="linux_foreground"):
        """The first unit of ``dataset`` run by Nullius, the launcher's row ``the_row``: the job's folder, its trial
        as the pages parse it and the stand-in's folder."""
        from nullius.runs import parse
        from nullius import ondisk
        from nullius.standard.requirements import install
        tmp = tempfile.mkdtemp(dir=os.environ.get("TMPDIR"))
        self.addCleanup(shutil.rmtree, tmp, True)
        entry, desk, up = (os.path.join(tmp, n) for n in ("entry", "desktop", "upstream"))
        for d in (entry, os.path.join(desk, "stub"), os.path.join(up, "libs", "cua-driver-bench", "taskpack")):
            os.makedirs(d)
        with open(os.path.join(desk, "stub", "pyautogui.py"), "w") as f:
            f.write(STUB)
        shutil.copyfile(os.path.join(ENTRY, "driver.py"), os.path.join(entry, "driver.py"))
        with open(os.path.join(entry, "desktop.py"), "w") as f:
            f.write(module.replace("__ROOT__", repr(desk)).replace("__ROW__", repr(json.dumps(the_row))))
        data = os.path.join(up, DEFINITION["datasets"][0]["path"])
        with open(data, "w") as f:
            json.dump(task_list(), f)
        spec = os.path.join(up, driver.TASK_SPECS, "CDB-G02")
        os.makedirs(spec)
        with open(os.path.join(spec, "task.json"), "w") as f:
            json.dump({"id": "CDB-G02", "timeout_s": 360, "max_turns": 45}, f)
        bench = dict(DEFINITION, _here=entry, datasets=[dict(x, sha256=ondisk.sha256_of(data)) for x in DEFINITION["datasets"]],
                     driver=dict(DEFINITION["driver"], command=[sys.executable, "{here}/driver.py", "{here}/desktop.py"]),
                     mcp_servers={"computer": dict(DEFINITION["mcp_servers"]["computer"], command=[sys.executable])})
        other = definitions.get("agent", "fake", os.path.join(FIXTURES, "catalogue"))
        other = dict(other, _here=FIXTURES, start={"acp": [sys.executable, "{here}/fake_computer_agent.py", "hang"]})
        with mock.patch.object(install, "HOME", os.path.join(tmp, "install")), \
                mock.patch.object(install, "upstream_dir", return_value=up), \
                mock.patch.object(install, "upstream", return_value=up):
            job = standard_run.run(bench, other, model="m", provider="custom", jobs_root=os.path.join(tmp, "jobs"),
                                   dataset=dataset, limit=1, concurrency=1, proving=True, unsandboxed=True,
                                   retries=retries, timeouts={"setup": 60, "agent": 3},
                                   bench_options={"desktop_image": "cdb-stand-in"},
                                   env={"MODEL_API_URL": "http://127.0.0.1:9/v1"}, say=lambda _: None)
        return job, parse.parse_job(job).trials[0], desk

    @staticmethod
    def records(job, trial, desk):
        """A scored trial's record, its verdict, its actions and how often its desktop was removed."""
        folder = os.path.join(job, trial.trial_name)
        with open(os.path.join(folder, "result.json")) as f:
            record = json.load(f)
        with open(os.path.join(folder, "verifier", "computer-actions.jsonl")) as f:
            actions = [json.loads(line) for line in f]
        with open(os.path.join(folder, "verifier", "score.json")) as f:
            verdict = json.load(f)
        with open(os.path.join(desk, "removed")) as f:
            removed = f.read()
        return record, verdict, actions, removed

    def test_a_check_passed_when_the_agents_time_ran_out_counts(self):
        job, trial, desk = self.run_one(row("CDB-G02", 0, checker=True))
        record, verdict, actions, removed = self.records(job, trial, desk)
        self.assertEqual(record["standard"].get("cut"), "time")              # the agent's time ended its turn
        self.assertEqual((trial.exception_type, trial.reward), ("", 1.0))   # and the row's pass stands
        self.assertEqual((verdict["reward"], verdict["passed"], verdict["detail"]["checker passed"]), (1.0, True, True))
        self.assertEqual((verdict["detail"]["steps"], verdict["detail"]["violations"]), (1, []))
        self.assertEqual([a["step"] for a in actions], [1])                 # the click, the agent's one step
        self.assertEqual(removed, "1\n")
        # the pages read the task's checks by name, from the entry's report block
        self.assertEqual((trial.f2p_pass, trial.f2p_fail, trial.vocab.gained), (2, 0, "Checks"))

    def test_a_background_trial_whose_click_moved_the_users_pointer_fails_with_its_check_passed(self):
        job, trial, desk = self.run_one(bg_row("CDB-G02", 0, checker=True, disturbed=["pointer"]),
                                        dataset="linux_background")
        record, verdict, actions, removed = self.records(job, trial, desk)
        self.assertEqual(record["standard"]["unit"], "CDB-G02-background")
        self.assertEqual((trial.exception_type, trial.reward, trial.outcome), ("", 0.0, "failed_verification"))
        d = verdict["detail"]
        self.assertEqual((d["condition"], d["checker passed"], d["measures held"]["pointer"]), ("background", True, False))
        # the pages count the task's checks alone: both passed; the measure that failed is the detail's
        self.assertEqual((trial.f2p_pass, trial.f2p_fail, trial.f2p_failures), (2, 0, []))
        self.assertEqual((trial.p2p_pass, trial.p2p_fail, trial.is_regression), (0, 0, False))
        self.assertEqual(removed, "1\n")

    def test_the_environments_errors_at_score_are_recorded_as_the_environments(self):
        failing = MODULE + ("\n_python = Desktop.python\n\n\ndef _failing(self, code, seconds=90):\n"
                            "    if 'click' in code:\n        raise DesktopError('the engine went away')\n"
                            "    return _python(self, code, seconds)\n\n\nDesktop.python = _failing\n")
        for case, the_row, module in (("harness error", row("CDB-G02", 0, harness_error="evaluate: no result"), MODULE),
                                      ("desktop gone", row("CDB-G02", 0, checker=True), failing)):
            with self.subTest(case):
                job, trial, _ = self.run_one(the_row, module)
                with open(os.path.join(job, trial.trial_name, "result.json")) as f:
                    said = (json.load(f).get("exception_info") or {}).get("exception_message") or ""
                self.assertEqual(trial.exception_type, "EnvironmentSetupError", said)
                self.assertIn("no result" if case == "harness error" else "the engine went away", said)


if __name__ == "__main__":
    unittest.main()
