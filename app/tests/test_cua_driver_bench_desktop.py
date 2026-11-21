"""Cua Driver Bench's desktop module against a stand-in for the engine's program: the bounds of each call, the removal
after a failed start, the session read from the window manager's process, the pin's folder put over the image's
checkout with its node_modules kept, the launcher started with its gate and ended by closing its input, read's
refusals, links, paths and sizes in collect, and the check in remove. No engine runs: the stand-in runs each
``exec`` here, in folders of the test, and a stand-in launcher plays the bench's."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import threading
import time
import unittest
from unittest import mock

from nullius.standard import container, definitions

ENTRY = os.path.join(os.path.dirname(definitions.KITS), os.pardir, "catalogue", "benchmarks", "cua-driver-bench")
ENTRY = os.path.normpath(ENTRY)
spec = importlib.util.spec_from_file_location("cua_driver_bench_desktop", os.path.join(ENTRY, "desktop.py"))
desktop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(desktop)

# The engine's stand-in: a container is a folder under STATE; `exec` runs its command here (python3 is this Python),
# with the -e values as its environment and -w as its folder; `cp NAME:PATH -` writes a tar of PATH, or the archive
# that BEHAVIOUR names. BEHAVIOUR (a JSON file) says how run, rm, image inspect and build answer.
FAKE = r'''#!{python}
import hashlib, json, os, shutil, sys, tarfile, time
STATE = {state!r}
argv = sys.argv[1:]
with open(os.path.join(STATE, "calls.jsonl"), "a") as f:
    f.write(json.dumps(argv) + "\n")
try:
    with open(os.path.join(STATE, "behaviour.json")) as f:
        how = json.load(f)
except OSError:
    how = {{}}
boxes = os.path.join(STATE, "containers")
os.makedirs(boxes, exist_ok=True)
def gone(name):
    sys.stderr.write(f"Error response from daemon: No such container: {{name}}\n")
    sys.exit(1)
cmd = argv[0]
if cmd == "context":
    print("[]")
elif cmd == "run":
    if how.get("run") == "hang":
        time.sleep(60)
    if how.get("run") == "fail":
        sys.stderr.write("docker: no such image\n")
        sys.exit(125)
    name = argv[argv.index("--name") + 1]
    os.makedirs(os.path.join(boxes, name))
    cid = hashlib.sha256(name.encode()).hexdigest()
    with open(os.path.join(boxes, name, "id"), "w") as f:
        f.write(cid)
    if how.get("run") == "created_before_id":
        time.sleep(60)
    if "--cidfile" in argv:
        with open(argv[argv.index("--cidfile") + 1], "x") as f:
            f.write(cid)
    if how.get("run") == "created_fail":
        sys.exit(125)
    if how.get("run") == "created_hang":
        time.sleep(60)
    print(cid)
elif cmd == "exec":
    rest, env, cwd = argv[1:], {{"PATH": os.environ.get("PATH", "/usr/bin:/bin")}}, None
    while rest[0].startswith("-"):
        flag = rest.pop(0)
        if flag == "-e":
            k, v = rest.pop(0).split("=", 1)
            env[k] = v
        elif flag == "-w":
            cwd = rest.pop(0)
        elif flag == "-u":
            rest.pop(0)
    name, run = rest[0], rest[1:]
    if not os.path.isdir(os.path.join(boxes, name)):
        gone(name)
    if run[0] == "python3":
        run[0] = sys.executable
    if cwd:
        os.chdir(cwd)
    os.execve(shutil.which(run[0], path=env["PATH"]) or run[0], run, env)
elif cmd == "cp":
    name, path = argv[1].split(":", 1)
    if not os.path.isdir(os.path.join(boxes, name)):
        gone(name)
    if how.get("cp_archive"):
        with open(how["cp_archive"], "rb") as f:
            sys.stdout.buffer.write(f.read())
    else:
        with tarfile.open(fileobj=sys.stdout.buffer, mode="w|") as t:
            t.add(path, arcname=os.path.basename(path))
elif cmd == "rm":
    name = argv[-1]
    for box in os.listdir(boxes):
        identity = os.path.join(boxes, box, "id")
        if os.path.isfile(identity) and open(identity).read() == name:
            name = box
            break
    if how.get("rm") == "fail":
        sys.stderr.write("Error response from daemon: removal in progress\n")
        sys.exit(1)
    if how.get("rm") != "stays":
        shutil.rmtree(os.path.join(boxes, name), ignore_errors=True)
    print(name)
elif cmd == "container" and argv[1] == "inspect":
    name = argv[-1]
    for box in os.listdir(boxes):
        identity = os.path.join(boxes, box, "id")
        if os.path.isfile(identity) and open(identity).read() == name:
            name = box
            break
    if not os.path.isdir(os.path.join(boxes, name)):
        gone(name)
    print("abc")
elif cmd == "logs":
    print("the desktop's last lines")
elif cmd == "image" and argv[1] == "inspect":
    ref = argv[-1]
    images = how.get("images", {{}})
    if ref not in images:
        sys.stderr.write(f"Error response from daemon: No such image: {{ref}}\n")
        sys.exit(1)
    print(images[ref])
elif cmd == "build":
    ref = argv[argv.index("-t") + 1]
    ctx = argv[-1]
    print("building", ref, sorted(os.listdir(ctx)))
    if how.get("build") == "fail":
        sys.exit(1)
    how.setdefault("images", {{}})[ref] = "sha256:built"
    with open(os.path.join(STATE, "behaviour.json"), "w") as f:
        json.dump(how, f)
else:
    sys.stderr.write(f"the stand-in does not serve {{argv}}\n")
    sys.exit(2)
'''

# The bench's launcher's stand-in, at the launcher's place in the stand-in bench: it writes brief.md first, then, as
# the mode file of its folder says, ends at once with a harness error ("harness"), never starts the agent command
# ("stall"), stops the command as the guard would ("guard"), ignores the end of the command ("deaf"), or runs it
# until it ends ("normal"); then it writes row.json and exits.
LAUNCHER = r'''
import argparse, json, os, subprocess, sys, time
print("launcher diagnostic", flush=True)
split = sys.argv.index("--")
ap = argparse.ArgumentParser()
for a in ("--task", "--condition", "--out", "--label"):
    ap.add_argument(a, default="")
ap.add_argument("--run-index", type=int, required=True)
ap.add_argument("--timeout", type=float, default=360)
args = ap.parse_args(sys.argv[1:split])
mode = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "mode")).read().strip()
os.makedirs(args.out, exist_ok=True)
open(os.path.join(args.out, "brief.md"), "w").write(f"Do {args.task}.\n")
row = {"task": args.task, "run_index": args.run_index, "seed": 1000 + args.run_index, "condition": args.condition,
       "timeout": args.timeout, "env_display": os.environ.get("DISPLAY"), "cwd": os.getcwd()}
try:
    if mode == "harness":
        row["harness_error"] = "BenchLab did not start"
        sys.exit(3)
    if mode == "stall":
        time.sleep(60)
    agent = subprocess.Popen(sys.argv[split + 1:], start_new_session=True)
    if mode == "guard":
        time.sleep(0.5)
        agent.kill()
        row["gui_only"] = {"violation": True}
    if mode == "deaf":
        agent.wait()
        time.sleep(60)
    row["agent_exit"] = agent.wait()
    row["passed"] = False
finally:
    open(os.path.join(args.out, "row.json"), "w").write(json.dumps(row))
'''

WM_ENV = {"DISPLAY": ":7", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/session/bus", "HOME": "/home/x", "_": "/usr/bin/wm",
          "PWD": "/", "SHLVL": "0", "USER": "cua"}


class Engine:
    """The stand-in engine on PATH, its desktop's folders under one temporary root."""

    def __init__(self, test: unittest.TestCase):
        self.root = tempfile.mkdtemp(prefix="cdb-desktop-test-")
        test.addCleanup(shutil.rmtree, self.root, True)
        self.state = os.path.join(self.root, "engine")
        bin_dir = os.path.join(self.root, "bin")
        os.makedirs(self.state)
        os.makedirs(bin_dir)
        self.program = os.path.join(bin_dir, "docker")
        with open(self.program, "w") as f:
            f.write(FAKE.format(python=sys.executable, state=self.state))
        os.chmod(self.program, 0o755)
        inside = os.path.join(self.root, "inside")
        self.paths = {"HOME": os.path.join(inside, "home"), "BENCH_IN": os.path.join(inside, "home", "bench"),
                      "OUT": os.path.join(inside, "trial"), "GATE": os.path.join(inside, "trial-gate"),
                      "FOLDER": os.path.join(inside, "agent"), "PORT_PYTHON": sys.executable,
                      "PORT_XAUTHORITY": os.path.join(inside, "Xauthority")}
        os.makedirs(self.paths["HOME"])
        patches = [mock.patch.object(container, "DOCKER", self.program), mock.patch.object(container, "_context", lambda: {}),
                   mock.patch.object(container, "capped", lambda task=None: []),
                   mock.patch.dict(os.environ, {"PATH": bin_dir + os.pathsep + os.environ.get("PATH", os.defpath)}),
                   mock.patch.object(desktop, "GATE_COMMAND", ["sh", "-c", 'touch "$1"; exec cat >/dev/null', "sh",
                                                               self.paths["GATE"]])]
        patches += [mock.patch.object(desktop, k, v) for k, v in self.paths.items()]
        for p in patches:
            p.start()
            test.addCleanup(p.stop)
        for k in ("HOME", "BENCH_IN", "OUT", "GATE", "FOLDER"):    # nothing of the test reaches beyond its root
            test.assertTrue(getattr(desktop, k).startswith(self.root))
        test.assertEqual(container.DOCKER, self.program)
        self.wm = None
        self.behave()

    def behave(self, **how):
        with open(os.path.join(self.state, "behaviour.json"), "w") as f:
            json.dump(how, f)

    def calls(self):
        try:
            with open(os.path.join(self.state, "calls.jsonl")) as f:
                return [json.loads(line) for line in f]
        except OSError:
            return []

    def running(self, name):
        return os.path.isdir(os.path.join(self.state, "containers", name))

    def window_manager(self, test, env=WM_ENV):
        """A process named as the session's window manager, ``env`` its environment."""
        self.wm = subprocess.Popen([sys.executable, "-I", "-c", "import time\nwith open('/proc/self/comm', 'w') as f:\n"
                                    "    f.write('fakewm')\ntime.sleep(60)"], env=env, stdin=subprocess.DEVNULL)
        test.addCleanup(self._end_wm)
        return mock.patch.object(desktop, "WINDOW_MANAGER", "fakewm")

    def _end_wm(self):
        if self.wm is not None and self.wm.poll() is None:
            self.wm.kill()
            self.wm.wait()

    def bench(self, mode="normal"):
        """A stand-in of the upstream's checkout: the bench's folder with the stand-in launcher."""
        up = os.path.join(self.root, "upstream")
        harness = os.path.join(up, desktop.BENCH_DIR, desktop.HARNESS)
        os.makedirs(os.path.join(harness, "launch"), exist_ok=True)
        with open(os.path.join(harness, desktop.LAUNCHER), "w") as f:
            f.write(LAUNCHER)
        with open(os.path.join(harness, "launch", "mode"), "w") as f:
            f.write(mode)
        tool = os.path.join(harness, "launch", "tool.sh")
        with open(tool, "w") as f:
            f.write("#!/bin/sh\n")
        os.chmod(tool, 0o755)
        return up


def started(test, engine, **kw):
    with engine.window_manager(test):
        d = desktop.Desktop.start("bench:image", job="job-1", name="cdb-test-1", **kw)
    def removed():
        with contextlib.suppress(Exception):
            d.remove()
    test.addCleanup(removed)
    return d


class Start(unittest.TestCase):
    def test_session_read_shares_the_start_deadline(self):
        e = Engine(self)
        session = "import json, time; time.sleep(0.6); print(json.dumps({'env': {}, 'port': True}))"
        self.addCleanup(desktop.Desktop("late-session").remove)
        with mock.patch.object(desktop, "_SESSION", session):
            before = time.monotonic()
            with self.assertRaises(desktop.DesktopError):
                desktop.Desktop.start("bench:image", name="late-session", seconds=0.15)
        self.assertLess(time.monotonic() - before, 0.6)
        self.assertFalse(e.running("late-session"))

    def test_a_desktop_starts_with_the_readme_flags_and_reads_the_session_from_the_window_manager(self):
        e = Engine(self)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            d = started(self, e)
        run = next(c for c in e.calls() if c[0] == "run")
        self.assertEqual(run[:3], ["run", "-d", "--name"])
        for flag in (["--network", "none"], ["-e", "CUA_RESOLUTION=1920x1080"], ["--label", "nullius.job=job-1"]):
            self.assertIn(flag, [run[i:i + 2] for i in range(len(run) - 1)])
        self.assertEqual(run[-1], "bench:image")
        self.assertEqual(d.env, {k: v for k, v in WM_ENV.items() if k not in ("_", "PWD", "SHLVL")})
        self.assertEqual(d.port_env()["XAUTHORITY"], desktop.PORT_XAUTHORITY)
        self.assertEqual(out.getvalue(), "")                       # the driver's channel is never written

    def test_a_failed_start_removes_what_it_made(self):
        e = Engine(self)
        e.behave(run="fail")
        with self.assertRaises(desktop.DesktopError):
            desktop.Desktop.start("bench:image", name="cdb-test-1")
        self.assertFalse([c for c in e.calls() if c[0] == "rm"])
        e.behave()
        with self.assertRaises(desktop.DesktopError) as caught:   # no window manager within the bound
            desktop.Desktop.start("bench:image", name="cdb-test-2", seconds=1.5)
        self.assertIn("had no", str(caught.exception))
        self.assertFalse(e.running("cdb-test-2"))
        with e.window_manager(self), mock.patch.object(desktop, "PORT_PYTHON", os.path.join(e.root, "none")):
            with self.assertRaises(desktop.DesktopError) as caught:
                desktop.Desktop.start("bench:image", name="cdb-test-3")
        self.assertIn("port's layer", str(caught.exception))
        self.assertFalse(e.running("cdb-test-3"))

    def test_a_start_that_does_not_answer_is_bounded_and_removed(self):
        e = Engine(self)
        e.behave(run="created_hang")
        t = time.monotonic()
        with mock.patch.object(desktop, "RUN_SECONDS", 1):
            with self.assertRaises(desktop.DesktopError):
                desktop.Desktop.start("bench:image", name="cdb-test-1")
        self.assertLess(time.monotonic() - t, 15)
        self.assertTrue([c for c in e.calls() if c[0] == "rm"])
        self.assertFalse(e.running("cdb-test-1"))

    def test_a_name_collision_does_not_remove_the_existing_container(self):
        e = Engine(self)
        existing = os.path.join(e.state, "containers", "existing")
        os.makedirs(existing)
        record = os.path.join(existing, "record")
        with open(record, "w") as f:
            f.write("earlier trial")
        with self.assertRaises(desktop.DesktopError):
            desktop.Desktop.start("bench:image", name="existing")
        self.assertTrue(e.running("existing"))
        with open(record) as f:
            self.assertEqual(f.read(), "earlier trial")
        self.assertFalse([c for c in e.calls() if c[0] == "rm"])

    def test_a_failed_run_removes_the_container_it_created_by_id(self):
        e = Engine(self)
        e.behave(run="created_fail")
        with self.assertRaises(desktop.DesktopError):
            desktop.Desktop.start("bench:image", name="unfinished")
        self.assertFalse(e.running("unfinished"))
        removal = next(c for c in e.calls() if c[0] == "rm")
        self.assertRegex(removal[-1], r"^[0-9a-f]{64}$")

    def test_a_run_cut_before_it_wrote_the_id_removes_the_container_by_the_name_the_start_gave(self):
        e = Engine(self)
        e.behave(run="created_before_id")
        with mock.patch.object(desktop, "RUN_SECONDS", 1):
            with self.assertRaises(desktop.DesktopError):
                desktop.Desktop.start("bench:image", job="job-1")
        run = next(c for c in e.calls() if c[0] == "run")
        made = run[run.index("--name") + 1]
        self.assertRegex(made, r"^nullius-cdb-[0-9a-f]{8}$")
        self.assertFalse(e.running(made))
        self.assertIn(["rm", "-f", made], e.calls())

    def test_an_interrupted_start_removes_what_it_made(self):
        e = Engine(self)
        real = desktop._engine

        def interrupted(args, **kw):
            if args[0] == "exec":
                raise KeyboardInterrupt
            return real(args, **kw)
        with mock.patch.object(desktop, "_engine", interrupted):
            with self.assertRaises(KeyboardInterrupt):
                desktop.Desktop.start("bench:image", name="cdb-test-1")
        self.assertFalse(e.running("cdb-test-1"))

    def test_shutdown_during_start_still_removes_what_it_made(self):
        e = Engine(self)
        real = desktop._engine

        def shutdown(args, **kw):
            if args[0] == "exec":
                desktop.end_clients()
                raise KeyboardInterrupt
            return real(args, **kw)
        with mock.patch.object(desktop, "_CLIENTS_CLOSED", False), mock.patch.object(desktop, "_engine", shutdown):
            with self.assertRaises(KeyboardInterrupt):
                desktop.Desktop.start("bench:image", name="unfinished")
            self.assertFalse(e.running("unfinished"))
            self.assertEqual([c[0] for c in e.calls()], ["run", "rm", "container"])
            with self.assertRaises(desktop.DesktopError):
                real(["logs", "unfinished"], timeout=1)


class Bench(unittest.TestCase):
    def test_links_and_special_files_are_refused_before_hashing_or_copying(self):
        e = Engine(self)
        up = e.bench()
        bench = desktop.bench_folder(up)
        outside = os.path.join(e.root, "outside")
        with open(outside, "wb") as f:
            f.write(b"private fixture bytes")
        bad = os.path.join(bench, "bad")
        real_open = open

        def checked_open(path, *args, **kw):
            self.assertNotEqual(os.fspath(path), bad, "unsafe benchmark input was opened")
            return real_open(path, *args, **kw)

        for kind in ("file link", "directory link", "fifo"):
            if kind == "fifo":
                os.mkfifo(bad)
            else:
                os.symlink(outside if kind == "file link" else e.root, bad)
            try:
                for operation in (lambda: desktop.tag(up),
                                  lambda: desktop._build("fixture:image", bench, None, time.monotonic() + 5)):
                    with self.subTest(kind=kind, operation=operation), mock.patch("builtins.open", checked_open):
                        with self.assertRaises(desktop.DesktopError):
                            operation()
            finally:
                os.unlink(bad)
        self.assertFalse([c for c in e.calls() if c[0] == "build"])

    def test_the_pin_goes_over_the_checkout_and_its_node_modules_stay(self):
        e = Engine(self)
        d = started(self, e)
        up = e.bench()
        app = os.path.join(desktop.BENCH_IN, "taskpack", "app")
        os.makedirs(os.path.join(app, "node_modules", "electron"))
        with open(os.path.join(app, "node_modules", "electron", "shim"), "w") as f:
            f.write("kept")
        with open(os.path.join(desktop.BENCH_IN, "stale.py"), "w") as f:
            f.write("gone")
        os.makedirs(os.path.join(desktop.BENCH_IN, "old", "deep"))
        n = d.put_bench(up)                                         # the checkout's root names the bench's folder
        self.assertEqual(n, len(desktop._bench_files(desktop.bench_folder(up))))
        self.assertTrue(os.path.isfile(os.path.join(app, "node_modules", "electron", "shim")))
        self.assertFalse(os.path.exists(os.path.join(desktop.BENCH_IN, "stale.py")))
        self.assertFalse(os.path.exists(os.path.join(desktop.BENCH_IN, "old")))
        tool = os.path.join(desktop.BENCH_IN, desktop.HARNESS, "launch", "tool.sh")
        self.assertTrue(os.stat(tool).st_mode & stat.S_IXUSR)
        self.assertEqual(d.put_bench(os.path.join(up, desktop.BENCH_DIR)), n)   # or the folder itself
        with self.assertRaises(desktop.DesktopError):
            d.put_bench(e.root)

    def test_the_tag_follows_the_recipe_and_the_bench(self):
        e = Engine(self)
        up = e.bench()
        first = desktop.tag(up)
        self.assertRegex(first, r"^nullius/env-cua-driver-bench-desktop:[0-9a-f]{12}$")
        recipe = os.path.join(e.root, "recipe")
        for rel in (desktop.DOCKERFILE, desktop.REQUIREMENTS):
            os.makedirs(os.path.dirname(os.path.join(recipe, rel)), exist_ok=True)
            shutil.copyfile(os.path.join(desktop.HERE, rel), os.path.join(recipe, rel))
        with mock.patch.object(desktop, "HERE", recipe):
            self.assertEqual(desktop.tag(up), first)
            for rel in (desktop.DOCKERFILE, desktop.REQUIREMENTS):
                with self.subTest(recipe=rel):
                    path = os.path.join(recipe, rel)
                    with open(path, "rb") as f:
                        original = f.read()
                    with open(path, "ab") as f:
                        f.write(b"\n# changed recipe input\n")
                    try:
                        self.assertNotEqual(desktop.tag(up), first)
                    finally:
                        with open(path, "wb") as f:
                            f.write(original)
                    self.assertEqual(desktop.tag(up), first)
        os.makedirs(os.path.join(up, desktop.BENCH_DIR, "node_modules"))
        with open(os.path.join(up, desktop.BENCH_DIR, "node_modules", "x"), "w") as f:
            f.write("not the pin")
        self.assertEqual(desktop.tag(up), first)
        with open(os.path.join(up, desktop.BENCH_DIR, "README.md"), "w") as f:
            f.write("changed")
        self.assertNotEqual(desktop.tag(up), first)


class Image(unittest.TestCase):
    def test_a_name_is_inspected_and_never_pulled(self):
        e = Engine(self)
        e.behave(images={"osw:1": "sha256:one"})
        self.assertEqual(desktop.image("osw:1", e.bench()), ("osw:1", "sha256:one"))
        with self.assertRaises(desktop.DesktopError):
            desktop.image("osw:2", e.bench())
        self.assertFalse([c for c in e.calls() if c[0] in ("pull", "build", "run")])

    def test_build_builds_once_from_the_recipe_and_the_pin(self):
        e = Engine(self)
        up = e.bench()
        logs = os.path.join(e.root, "logs")
        real = desktop._spawn

        def checked_build(args, **kw):
            if args[0] == "build":
                for rel, source in desktop._bench_files(desktop.bench_folder(up)):
                    copied = os.path.join(args[-1], "bench", rel)
                    with open(source, "rb") as src, open(copied, "rb") as dst:
                        self.assertEqual(dst.read(), src.read(), rel)
                    self.assertEqual(os.stat(copied).st_mode & 0o111, os.stat(source).st_mode & 0o111, rel)
            return real(args, **kw)

        with mock.patch.dict(os.environ, {"TMPDIR": e.root}), mock.patch.object(desktop, "_spawn", checked_build):
            tempfile.tempdir = None
            try:
                ref, found = desktop.image("build", up, logs)
                again = desktop.image("build", up, logs)
                e.behave(build="fail")
                with mock.patch.object(desktop, "tag", lambda upstream: "nullius/env-other:1"):
                    with self.assertRaises(desktop.DesktopError):
                        desktop.image("build", up, logs)
            finally:
                tempfile.tempdir = None
        self.assertEqual((ref, found), (desktop.tag(up), "sha256:built"))
        self.assertEqual(again, (ref, found))
        builds = [c for c in e.calls() if c[0] == "build"]
        self.assertEqual([c[c.index("-t") + 1] for c in builds], [ref, "nullius/env-other:1"])    # once each
        with open(os.path.join(logs, "env-desktop.log")) as f:
            said = f.read()
        self.assertIn("'bench'", said)                               # the pin's folder is in the build's context
        self.assertIn("'desktop.Dockerfile'", said)
        self.assertTrue(os.path.exists(os.path.join(e.root, "nullius-cua-driver-bench-desktop.lock")))

    def test_the_builds_context_has_the_same_modes_from_any_checkout(self):
        e = Engine(self)
        up = e.bench()
        bench = desktop.bench_folder(up)
        tool = os.path.join(bench, desktop.HARNESS, "launch", "tool.sh")
        first = desktop.tag(up)
        self.addCleanup(os.umask, os.umask(0o002))             # a checkout and a build under another umask
        for root, dirs, files in os.walk(bench):
            for name in dirs:
                os.chmod(os.path.join(root, name), 0o775)
            for name in files:
                path = os.path.join(root, name)
                os.chmod(path, 0o775 if path == tool else 0o664)
        self.assertEqual(desktop.tag(up), first)
        seen = {}
        real = desktop._spawn

        def recorded(args, **kw):
            if args[0] == "build":
                for root, dirs, files in os.walk(args[-1]):
                    for name in dirs + files:
                        path = os.path.join(root, name)
                        seen[os.path.relpath(path, args[-1])] = (os.path.isdir(path), stat.S_IMODE(os.stat(path).st_mode))
            return real(args, **kw)
        with mock.patch.dict(os.environ, {"TMPDIR": e.root}), mock.patch.object(desktop, "_spawn", recorded):
            tempfile.tempdir = None
            try:
                desktop.image("build", up)
            finally:
                tempfile.tempdir = None
        executable = os.path.join("bench", os.path.relpath(tool, bench))
        self.assertIn(executable, seen)
        self.assertIn("desktop-requirements.txt", seen)
        for rel, (folder, mode) in seen.items():
            self.assertEqual(mode, 0o755 if folder or rel == executable else 0o644, rel)
        os.chmod(tool, 0o664)                                      # the executable bit is in the image: in the tag
        self.assertNotEqual(desktop.tag(up), first)


class Trial(unittest.TestCase):
    def launched(self, mode="normal", **kw):
        e = Engine(self)
        d = started(self, e)
        d.put_bench(e.bench(mode))
        return e, d

    def test_brief_read_shares_the_launch_deadline(self):
        e, d = self.launched()
        mark = os.path.join(e.root, "reading")
        read = f"import time; open({mark!r}, 'w').close(); time.sleep(0.8)\n" + desktop._READ
        with mock.patch.object(desktop, "_READ", read), mock.patch.object(desktop, "POLL", 0.01):
            before = time.monotonic()
            with self.assertRaises(desktop.DesktopError):
                d.launch("IR-02", 0, "foreground", seconds=0.4)
        self.assertTrue(os.path.isfile(mark))
        self.assertLess(time.monotonic() - before, 0.8)

    def test_row_read_shares_the_finish_deadline(self):
        e, d = self.launched()
        d.launch("IR-02", 0, "foreground")
        read = "import time; time.sleep(0.6)\n" + desktop._READ
        with mock.patch.object(desktop, "_READ", read):
            before = time.monotonic()
            with self.assertRaises(desktop.DesktopError):
                d.finish(seconds=0.15)
        self.assertLess(time.monotonic() - before, 0.6)

    def test_waiting_for_the_lock_shares_the_launch_and_finish_deadlines(self):
        e, d = self.launched()
        for operation in (lambda: d.launch("IR-02", 0, "foreground", seconds=0.05),
                          lambda: d.finish(seconds=0.05)):
            errors = []

            def invoke():
                try:
                    operation()
                except Exception as error:
                    errors.append(error)
            with self.subTest(operation=operation):
                d._lock.acquire()
                worker = threading.Thread(target=invoke)
                worker.start()
                try:
                    worker.join(0.3)
                    blocked = worker.is_alive()
                finally:
                    d._lock.release()
                    worker.join(3)
                self.assertFalse(worker.is_alive())
                self.assertFalse(blocked)
                self.assertEqual(len(errors), 1)
                self.assertIsInstance(errors[0], desktop.DesktopError)

    def test_a_trial_from_the_gate_to_the_row(self):
        e, d = self.launched()
        with tempfile.TemporaryFile() as out:
            saved = os.dup(1)
            try:
                sys.stdout.flush()
                os.dup2(out.fileno(), 1)
                brief = d.launch("IR-02", 3, "foreground")
            finally:
                sys.stdout.flush()
                os.dup2(saved, 1)
                os.close(saved)
            out.seek(0)
            self.assertEqual(out.read(), b"")
        self.assertIn("launcher diagnostic", d.launcher_output())
        self.assertEqual(brief, "Do IR-02.\n")
        self.assertTrue(os.path.exists(desktop.GATE))
        self.assertFalse(d.launcher_ended())
        launch = next(c for c in e.calls() if c[0] == "exec" and desktop.LAUNCHER in c)
        tail = launch[launch.index(desktop.LAUNCHER) + 1:]
        self.assertEqual(tail[:tail.index("--")], ["--task", "IR-02", "--run-index", "3", "--condition", "foreground",
                                                   "--out", desktop.OUT, "--timeout", str(desktop.LAUNCHER_TIMEOUT)])
        self.assertEqual(tail[tail.index("--") + 1:], desktop.GATE_COMMAND)
        self.assertIn("-i", launch[:launch.index("cdb-test-1")])
        row = d.finish()
        self.assertEqual((row["task"], row["run_index"], row["seed"], row["agent_exit"]), ("IR-02", 3, 1003, 0))
        self.assertEqual(row["env_display"], ":7")                   # the session's environment
        self.assertEqual(row["cwd"], os.path.join(desktop.BENCH_IN, desktop.HARNESS))
        self.assertIs(d.finish(), row)
        self.assertTrue(d.launcher_ended())
        d.remove()
        self.assertFalse(e.running("cdb-test-1"))

    def test_the_ports_operations_as_the_desktops_user(self):
        e, d = self.launched()
        d.launch("IR-02", 0, "foreground")
        before = len(e.calls())
        r = d.python("import os, sys; print(os.environ['DISPLAY'], os.environ['XAUTHORITY'], os.getcwd()); "
                     "sys.stderr.write('e'); sys.exit(4)")
        self.assertEqual(r, {"status": "success", "output": f":7 {desktop.PORT_XAUTHORITY} {desktop.HOME}\n",
                             "error": "e", "returncode": 4})
        path = os.path.join(desktop.FOLDER, "a", "b.bin")
        self.assertEqual(d.upload(path, b"\x00data"), 5)
        self.assertEqual(d.read(path), b"\x00data")
        self.assertIsNone(d.read(os.path.join(desktop.FOLDER, "none")))
        with self.assertRaises(desktop.Refused):
            d.read(desktop.FOLDER)
        fifo = os.path.join(desktop.FOLDER, "fifo")
        os.mkfifo(fifo)
        with self.assertRaises(desktop.Refused):
            d.read(fifo)
        with mock.patch.object(desktop, "READ_LIMIT", 4):
            with self.assertRaises(desktop.Refused):
                d.read(path)
        with self.assertRaises(desktop.DesktopError):
            d.upload("relative/path", b"")
        png = b"\x89PNG\r\n\x1a\n" + b"\0" * 8
        with mock.patch.object(desktop, "_CAPTURE", f"import sys; sys.stdout.buffer.write({png!r})"):
            self.assertEqual(d.capture(), png)
        with mock.patch.object(desktop, "_CAPTURE", "print('no picture')"):
            with self.assertRaises(desktop.DesktopError):
                d.capture()
        calls = e.calls()[before:]
        self.assertEqual(len(calls), 9)
        for call in calls:
            self.assertEqual(call[0], "exec")
            self.assertEqual(call[call.index("-u") + 1], desktop.USER)
        d.finish()

    def test_code_past_its_bound_is_none_and_its_group_is_killed(self):
        e, d = self.launched()
        d.launch("IR-02", 0, "foreground")
        mark = os.path.join(e.root, "child")
        code = (f"import subprocess, time\np = subprocess.Popen(['sleep', '60'])\nopen({mark!r}, 'w').write(str(p.pid))\n"
                "time.sleep(60)")
        t = time.monotonic()
        self.assertIsNone(d.python(code, seconds=1.5))
        self.assertLess(time.monotonic() - t, 10)
        with open(mark) as f:
            pid = int(f.read())
        for _ in range(50):
            try:
                with open(f"/proc/{pid}/stat") as f:
                    if f.read().split(")")[-1].split()[0] == "Z":
                        break
            except OSError:                                          # gone
                break
            time.sleep(0.1)
        else:
            self.fail("the code's child outlived its bound")
        d.finish()

    def test_a_harness_error_before_the_gate_is_said_with_its_row(self):
        e, d = self.launched("harness")
        with self.assertRaises(desktop.HarnessError) as caught:
            d.launch("CDB-G02", 0, "foreground")
        self.assertEqual(caught.exception.row["harness_error"], "BenchLab did not start")
        self.assertTrue(d.launcher_ended())

    def test_a_gate_that_never_starts_is_bounded(self):
        e, d = self.launched("stall")
        t = time.monotonic()
        with self.assertRaises(desktop.DesktopError):
            d.launch("IR-02", 0, "foreground", seconds=2)
        self.assertLess(time.monotonic() - t, 10)
        d.remove()
        self.assertFalse(e.running("cdb-test-1"))

    def test_the_guard_ends_the_launcher_and_finish_gives_its_row(self):
        e, d = self.launched("guard")
        d.launch("IR-02", 0, "foreground")
        for _ in range(100):
            if d.launcher_ended():
                break
            time.sleep(0.05)
        self.assertTrue(d.launcher_ended())
        self.assertEqual(d.finish()["gui_only"], {"violation": True})

    def test_a_launcher_that_does_not_end_is_bounded(self):
        e, d = self.launched("deaf")
        d.launch("IR-02", 0, "foreground")
        t = time.monotonic()
        with self.assertRaises(desktop.DesktopError):
            d.finish(seconds=1.5)
        self.assertLess(time.monotonic() - t, 10)
        d.remove()                                                   # its client ended, its container gone
        self.assertTrue(d.launcher_ended())
        self.assertFalse(e.running("cdb-test-1"))

    def test_remove_twice_and_from_another_thread(self):
        e, d = self.launched()
        d.launch("IR-02", 0, "foreground")
        errors = []

        def remove():
            try:
                d.remove()
            except Exception as x:                                   # noqa: BLE001 - said by the test
                errors.append(x)
        threads = [threading.Thread(target=remove) for _ in range(2)]
        [t.start() for t in threads]
        [t.join(30) for t in threads]
        d.remove()
        self.assertEqual(errors, [])
        self.assertFalse(e.running("cdb-test-1"))
        self.assertTrue(d.launcher_ended())

    def test_end_clients_ends_a_launchers_client(self):
        e, d = self.launched()
        d.launch("IR-02", 0, "foreground")
        try:
            self.assertGreaterEqual(desktop.end_clients(closing=False), 1)
            self.assertTrue(d.launcher_ended())
        finally:
            desktop.end_clients(closing=False)


class Collect(unittest.TestCase):
    def archive(self, engine, *names):
        path = os.path.join(engine.root, "input.tar")
        with tarfile.open(path, "w") as archive:
            for name in names:
                info = tarfile.TarInfo("trial/" + name)
                info.size = len(b"replacement")
                archive.addfile(info, io.BytesIO(b"replacement"))
        engine.behave(cp_archive=path)

    def test_an_existing_hardlink_is_replaced_without_changing_its_other_name(self):
        e = Engine(self)
        d = started(self, e)
        dest = os.path.join(e.root, "dest")
        os.mkdir(dest)
        outside = os.path.join(e.root, "outside")
        with open(outside, "wb") as f:
            f.write(b"original")
        target = os.path.join(dest, "row.json")
        os.link(outside, target)
        self.archive(e, "row.json")
        self.assertEqual(d.collect(dest), ["row.json"])
        with open(outside, "rb") as f:
            self.assertEqual(f.read(), b"original")
        with open(target, "rb") as f:
            self.assertEqual(f.read(), b"replacement")
        self.assertNotEqual(os.stat(outside).st_ino, os.stat(target).st_ino)

    def test_an_existing_fifo_is_replaced_without_opening_it_for_writing(self):
        e = Engine(self)
        d = started(self, e)
        dest = os.path.join(e.root, "dest")
        os.mkdir(dest)
        target = os.path.join(dest, "row.json")
        os.mkfifo(target)
        self.archive(e, "row.json")
        real_open = os.open

        def checked_open(path, flags, *args, **kw):
            if os.fspath(path) in (target, "row.json"):
                self.assertFalse(flags & (os.O_WRONLY | os.O_RDWR), "the existing FIFO was opened for writing")
            return real_open(path, flags, *args, **kw)
        with mock.patch.object(os, "open", checked_open):
            self.assertEqual(d.collect(dest), ["row.json"])
        self.assertTrue(stat.S_ISREG(os.lstat(target).st_mode))
        with open(target, "rb") as f:
            self.assertEqual(f.read(), b"replacement")

    def test_directory_links_are_refused_before_creating_any_child(self):
        e = Engine(self)
        d = started(self, e)
        dest, outside = os.path.join(e.root, "dest"), os.path.join(e.root, "outside")
        os.makedirs(os.path.join(dest, "real"))
        os.mkdir(outside)
        os.symlink(outside, os.path.join(dest, "escape"))
        os.symlink(os.path.join(dest, "real"), os.path.join(dest, "alias"))
        self.archive(e, "escape/new/deep/file", "alias/new/deep/file")
        self.assertEqual(d.collect(dest), [])
        self.assertEqual(os.listdir(outside), [])
        self.assertEqual(os.listdir(os.path.join(dest, "real")), [])

    def test_a_destination_reached_through_a_link_is_taken_as_given_and_links_under_it_are_refused(self):
        e = Engine(self)
        d = started(self, e)
        disk, other = os.path.join(e.root, "disk"), os.path.join(e.root, "other")
        os.mkdir(disk)
        os.mkdir(other)
        alias = os.path.join(e.root, "alias")                 # a home or data folder moved by a link
        os.symlink(disk, alias)
        dest = os.path.join(alias, "new", "deep")
        os.makedirs(dest)
        os.symlink(other, os.path.join(dest, "escape"))
        self.archive(e, "row.json", "escape/file")
        self.assertEqual(d.collect(dest), ["row.json"])
        with open(os.path.join(disk, "new", "deep", "row.json"), "rb") as f:
            self.assertEqual(f.read(), b"replacement")
        self.assertEqual(os.listdir(other), [])

    def test_plain_files_alone_within_the_size_and_nothing_outside_dest(self):
        e = Engine(self)
        d = started(self, e)
        os.makedirs(os.path.join(desktop.OUT, "sub"))
        elsewhere = os.path.join(e.root, "outside")
        with open(elsewhere, "w") as f:
            f.write("not to be copied")
        for name, data in (("row.json", b"{}"), ("sub/log.txt", b"x" * 10), ("big.zip", b"y" * 100)):
            with open(os.path.join(desktop.OUT, name), "wb") as f:
                f.write(data)
        os.symlink(elsewhere, os.path.join(desktop.OUT, "link"))
        os.mkfifo(os.path.join(desktop.OUT, "fifo"))
        dest = os.path.join(e.root, "dest")
        kept = d.collect(dest, max_bytes=50)
        self.assertEqual(sorted(kept), ["row.json", "sub/log.txt"])
        self.assertEqual(sorted(os.listdir(dest)), ["row.json", "sub"])
        self.assertEqual(d.collect(dest), d.collect(dest))           # again, over what is there
        self.assertIn("big.zip", d.collect(dest))

    def test_an_archive_that_would_leave_dest_writes_nothing_outside(self):
        e = Engine(self)
        d = started(self, e)
        evil = os.path.join(e.root, "evil.tar")
        with tarfile.open(evil, "w") as t:
            def add(name, data=b"z", kind=tarfile.REGTYPE, link=""):
                info = tarfile.TarInfo(name)
                info.type, info.linkname, info.size = kind, link, len(data) if kind == tarfile.REGTYPE else 0
                t.addfile(info, io.BytesIO(data) if kind == tarfile.REGTYPE else None)
            add("trial/../escape")
            add("/abs/escape")
            add("trial/esc", kind=tarfile.SYMTYPE, link="/")
            add("trial/hard", kind=tarfile.LNKTYPE, link="trial/ok")
            add("trial/esc2/through", b"through a link in dest")
            add("trial/ok", b"fine")
        e.behave(cp_archive=evil)
        dest = os.path.join(e.root, "dest")
        os.makedirs(dest)
        os.symlink(e.root, os.path.join(dest, "esc2"))
        self.assertEqual(d.collect(dest), ["ok"])
        self.assertFalse(os.path.exists(os.path.join(e.root, "escape")))
        self.assertFalse(os.path.exists("/abs/escape"))
        self.assertFalse(os.path.exists(os.path.join(e.root, "through")))
        self.assertEqual(sorted(os.listdir(dest)), ["esc2", "ok"])

    def test_a_gone_desktop_is_an_error(self):
        e = Engine(self)
        d = desktop.Desktop("cdb-none")
        with self.assertRaises(desktop.DesktopError):
            d.collect(os.path.join(e.root, "dest"))


class Remove(unittest.TestCase):
    def test_removal_and_its_check_still_run_after_clients_close(self):
        e = Engine(self)
        d = started(self, e)
        with mock.patch.object(desktop, "_CLIENTS_CLOSED", False):
            desktop.end_clients()
            e.behave(rm="stays")
            with self.assertRaisesRegex(desktop.DesktopError, "still there"):
                d.remove()
            e.behave()
            d.remove()
            self.assertFalse(e.running("cdb-test-1"))
            self.assertEqual([c[0] for c in e.calls()[-4:]], ["rm", "container", "rm", "container"])

    def test_remove_checks_that_the_container_is_gone(self):
        e = Engine(self)
        d = started(self, e)
        e.behave(rm="fail")
        with self.assertRaises(desktop.DesktopError):
            d.remove()
        e.behave(rm="stays")
        with self.assertRaises(desktop.DesktopError):
            d.remove()
        e.behave()
        d.remove()
        d.remove()                                                   # gone already: no error
        self.assertFalse(e.running("cdb-test-1"))


if __name__ == "__main__":
    unittest.main()
