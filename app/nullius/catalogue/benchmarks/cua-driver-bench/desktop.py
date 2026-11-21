"""Cua Driver Bench's Linux desktop for one trial: one container of the bench's image, the bench's own launcher run in
it whole and unchanged, a gate in the launcher's agent slot, and the operations the computer port's server needs.

**The image** (:func:`image`) is a name the machine holds, inspected and never pulled, or ``build``: this folder's
``env/desktop.Dockerfile`` on Cua's public Linux image at a digest, the bench's folder at the entry's pin from the
upstream's checkout, the bench's two provisioning scripts, and the port's Python (``env/desktop-requirements.txt`` by
hash), built once under a lock and kept by a tag of its inputs' hash. Either holds the port's Python at
:data:`PORT_PYTHON`, apart from the bench's own Python.

**A trial**, in this order, everything after the start through ``docker exec``:

- :meth:`Desktop.start`: the image run with the bench README's flags (``-e CUA_RESOLUTION=1920x1080``), no network,
  the job's label and the machine's caps; ready once the window manager runs, whose environment becomes
  :attr:`Desktop.env`, the session's;
- :meth:`Desktop.put_bench`: the pin's folder copied over the image's checkout as ``cua``, ``node_modules`` kept;
- :meth:`Desktop.launch`: the launcher started as ``cua`` in the session's environment through ``exec -i``, its agent
  command the gate ``sh -c 'touch GATE; exec cat >/dev/null'``, which inherits the launcher's input: a pipe this
  object holds. The launcher resets the task, places the windows, arms its sentinel and runs the gate under its
  guard; ``launch`` answers the trial's ``brief.md`` once the gate's file exists;
- :meth:`Desktop.python`, :meth:`Desktop.upload`, :meth:`Desktop.read`, :meth:`Desktop.capture`: the port's
  operations, as ``cua``, the code in the session's environment on :data:`PORT_PYTHON`;
- :meth:`Desktop.finish`: the pipe closed, so the gate ends; the launcher then runs the bench's check and writes
  ``row.json``, which ``finish`` answers. The launcher's guard may end the gate first (a terminal started outside
  the launcher, or one in front): :meth:`Desktop.launcher_ended` says so;
- :meth:`Desktop.collect` copies the trial's folder out, plain files alone; :meth:`Desktop.remove` removes the
  container and checks that it is gone.

Every call has a bound, a start that fails removes what it made, and nothing is printed: the driver's standard output
is its channel. Standard library only, and the engine's program.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import posixpath
import secrets
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from typing import Dict, List, Optional, Tuple

from nullius.standard import container

HERE = os.path.dirname(os.path.abspath(__file__))
SCREEN = (1920, 1080)
FOLDER = "/tmp/agent"                                   # the agent's own folder in the desktop
USER = "cua"                                            # the desktop's user, as the bench's scripts name it
HOME = "/home/cua"
BENCH_DIR = "libs/cua-driver-bench"                     # the bench's folder in the upstream's checkout
BENCH_IN = HOME + "/cua/" + BENCH_DIR                   # its checkout in the image, as the bench's README places it
HARNESS = "automated-eval/macos_bench"                  # the launcher's folder, under the bench's
LAUNCHER = "launch/xfce_session.py"                     # the Linux launcher, under HARNESS
OUT = "/tmp/trial"                                      # the trial's folder in the desktop (the launcher's --out)
GATE = "/tmp/trial-gate"                                # the gate's file, made as the gate starts
GATE_COMMAND = ["sh", "-c", 'touch "$1"; exec cat >/dev/null', "sh", GATE]
LAUNCHER_TIMEOUT = 6 * 3600                             # the launcher's own limit: the agent's time is Nullius's
WINDOW_MANAGER = "xfwm4"                                # the session's process whose environment is the session's
PORT_PYTHON = "/opt/nullius/computer/bin/python3"       # the port's Python, with pyautogui and Pillow
PORT_XAUTHORITY = "/opt/nullius/computer/Xauthority"    # an authority file that matches no display, for python-xlib
SKIP = ("node_modules", ".git", "__pycache__")          # never copied from the upstream's folder
KEEP = "node_modules"                                   # kept in the image's checkout: the provisioning installed them
NOT_SESSION = ("_", "PWD", "OLDPWD", "SHLVL")           # of the window manager's environment, its own process's
IMAGE_KEY = "cua-driver-bench"
ENVIRONMENT = "desktop"
DOCKERFILE = "env/desktop.Dockerfile"                  # beside this file; at the top of the build's context
REQUIREMENTS = "env/desktop-requirements.txt"

RUN_SECONDS = 120
START_SECONDS = 180
PUT_SECONDS = 120
LAUNCH_SECONDS = 300
PYTHON_SECONDS = 90
FILE_SECONDS = 120                                      # an upload or a read
CAPTURE_SECONDS = 60
FINISH_SECONDS = 420
COLLECT_SECONDS = 300
REMOVE_SECONDS = 120
INSPECT_SECONDS = 60
BUILD_SECONDS = 6 * 3600
ENGINE_GRACE = 30                                       # the engine's own time, beyond the desktop's bound
POLL = 0.5
OUTPUT_LIMIT = 4 * 1024 * 1024                          # of each of output and error, as the reply gives them
READ_LIMIT = 12 * 1024 * 1024                           # the largest file `read` gives
COLLECT_BYTES = 64 * 1024 * 1024
TAIL = 4000                                             # of the launcher's own output, kept for an error's words

# Run as the desktop's user: wait for the window manager, then its environment, and whether the port's Python is there.
_SESSION = r"""
import json, os, sys, time
name, port, end = sys.argv[1], sys.argv[2], time.monotonic() + float(sys.argv[3])
while True:
    for pid in sorted((p for p in os.listdir("/proc") if p.isdigit()), key=int):
        try:
            with open(f"/proc/{pid}/comm") as f:
                if f.read().strip() != name:
                    continue
            with open(f"/proc/{pid}/environ", "rb") as f:
                raw = f.read()
        except OSError:
            continue
        env = dict(e.split("=", 1) for e in raw.decode("utf-8", "replace").split("\0") if "=" in e)
        print(json.dumps({"env": env, "port": os.access(port, os.X_OK)}))
        sys.exit(0)
    if time.monotonic() > end:
        sys.exit(1)
    time.sleep(0.2)
"""

# Run as the desktop's user: the image's checkout emptied but for its node_modules, then the pin's folder from stdin.
_PUT = r"""
import io, os, sys, tarfile
target, keep = sys.argv[1], sys.argv[2]
def prune(d):
    held = False
    for e in os.scandir(d):
        if e.is_dir(follow_symlinks=False):
            if e.name == keep or prune(e.path):
                held = True
            else:
                os.rmdir(e.path)
        else:
            os.unlink(e.path)
    return held
os.makedirs(target, exist_ok=True)
prune(target)
with tarfile.open(fileobj=io.BytesIO(sys.stdin.buffer.read())) as t:
    names = t.getnames()
    t.extractall(target, filter="data")
print(len(names))
"""

# Run as the desktop's user: the request on stdin, the code in its own session, killed with its group past its bound.
_RUN = r"""
import json, os, signal, subprocess, sys, tempfile
req = json.loads(sys.stdin.buffer.read())
out, err = tempfile.TemporaryFile(), tempfile.TemporaryFile()
p = subprocess.Popen([req["python"], "-c", req["code"]], env=req["env"], cwd=req["cwd"], stdin=subprocess.DEVNULL,
                     stdout=out, stderr=err, start_new_session=True)
try:
    rc, late = p.wait(timeout=req["seconds"]), False
except subprocess.TimeoutExpired:
    os.killpg(p.pid, signal.SIGKILL)
    rc, late = p.wait(), True
def text(f):
    f.seek(0)
    return f.read(req["limit"]).decode("utf-8", "replace")
print(json.dumps({"timeout": late, "returncode": rc, "output": text(out), "error": text(err)}))
"""

_UPLOAD = r"""
import os, sys
path = sys.argv[1]
if os.path.dirname(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
with open(path, "wb") as f:
    f.write(sys.stdin.buffer.read())
print(os.path.getsize(path))
"""

_READ = r"""
import os, stat, sys
limit = int(sys.argv[2])
try:
    fd = os.open(sys.argv[1], os.O_RDONLY | os.O_NONBLOCK)
except OSError:
    sys.exit(3)
if not stat.S_ISREG(os.fstat(fd).st_mode):
    sys.exit(4)
with os.fdopen(fd, "rb") as f:
    data = f.read(limit + 1)
if len(data) > limit:
    sys.exit(5)
sys.stdout.buffer.write(data)
"""

_CAPTURE = r"""
import io, os, sys
from PIL import ImageGrab
b = io.BytesIO()
ImageGrab.grab(xdisplay=os.environ["DISPLAY"]).save(b, "PNG")
sys.stdout.buffer.write(b.getvalue())
"""


class DesktopError(Exception):
    """The engine or the desktop failed: it is gone, or answered nothing a request answers."""


class HarnessError(Exception):
    """The launcher ended with a ``harness_error`` before the agent's slot: no one's result. ``row`` is its row."""

    def __init__(self, message: str, row: dict):
        super().__init__(message)
        self.row = row


class Refused(Exception):
    """A read the desktop does not serve: a path that is no regular file, or a file past :data:`READ_LIMIT`. The
    agent's error, never the desktop's."""


_CLIENTS: set = set()                                   # the engine's clients running now, for end_clients
_CLIENTS_LOCK = threading.Lock()
_CLIENTS_CLOSED = False


def _spawn(args: List[str], *, cleanup: bool = False, **kw) -> subprocess.Popen:
    """One client of the engine started and counted, for :func:`end_clients`."""
    with _CLIENTS_LOCK:
        if _CLIENTS_CLOSED and not cleanup:
            raise DesktopError("the desktop's engine clients have been closed")
        try:
            p = subprocess.Popen([container.DOCKER, *args], env=container.client_env(), **kw)
        except FileNotFoundError:
            raise DesktopError(f"{container.DOCKER} is not installed on this machine")
        _CLIENTS.add(p)
    return p


def _done(p: subprocess.Popen) -> None:
    with _CLIENTS_LOCK:
        _CLIENTS.discard(p)


def _engine(args: List[str], *, input: Optional[bytes] = None, timeout: float,
            cleanup: bool = False) -> subprocess.CompletedProcess:
    """One call of the engine's program, its stdin the request's bytes or nothing, killed past ``timeout``
    (``subprocess.TimeoutExpired``, as ``subprocess.run`` raises it)."""
    p = _spawn(args, cleanup=cleanup, stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL, stdout=subprocess.PIPE,
               stderr=subprocess.PIPE)
    try:
        try:
            out, err = p.communicate(input, timeout=timeout)
        except BaseException:
            p.kill()
            p.communicate()
            raise
    finally:
        _done(p)
    return subprocess.CompletedProcess([container.DOCKER, *args], p.returncode, out, err)


def end_clients(*, closing: bool = True) -> int:
    """Every client of the engine still running, from any thread, killed and waited for; how many there were. A
    shutdown also prevents further launches; between trials, only the current clients are ended."""
    global _CLIENTS_CLOSED
    with _CLIENTS_LOCK:
        _CLIENTS_CLOSED = _CLIENTS_CLOSED or closing
        running = list(_CLIENTS)
    for p in running:
        try:
            p.kill()
            p.wait(timeout=10)
        except (OSError, subprocess.SubprocessError):
            pass
    return len(running)


def _said(r: subprocess.CompletedProcess) -> str:
    return (r.stderr or r.stdout or b"").decode("utf-8", "replace").strip()[-300:]


def _absent(r: subprocess.CompletedProcess) -> bool:
    """Whether the engine said the container does not exist."""
    return "no such container" in (r.stderr or b"").decode("utf-8", "replace").lower()


def _left(end: float) -> float:
    left = end - time.monotonic()
    if left <= 0:
        raise DesktopError("the desktop operation exceeded its deadline")
    return left


@contextlib.contextmanager
def _locked_until(lock, end: float):
    if not lock.acquire(timeout=_left(end)):
        raise DesktopError("the desktop operation exceeded its deadline waiting for its lock")
    try:
        yield
    finally:
        lock.release()


# -- the image ----------------------------------------------------------------------------------------------------
def bench_folder(upstream: str) -> str:
    """The bench's folder: ``upstream`` itself when it holds the launcher, else its ``libs/cua-driver-bench``."""
    for f in (upstream, os.path.join(upstream, BENCH_DIR)):
        if os.path.isfile(os.path.join(f, HARNESS, LAUNCHER)):
            return f
    raise DesktopError(f"no bench's folder in {upstream}: no {BENCH_DIR}/{HARNESS}/{LAUNCHER}")


def _bench_files(folder: str) -> List[Tuple[str, str]]:
    """The bench's folder as ``(relative path, path)`` in a fixed order, without :data:`SKIP`'s folders."""
    out = []
    if not stat.S_ISDIR(os.lstat(folder).st_mode):
        raise DesktopError(f"the bench's folder is not a plain directory: {folder}")
    for root, dirs, files in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if d not in SKIP)
        for name in dirs:
            path = os.path.join(root, name)
            if not stat.S_ISDIR(os.lstat(path).st_mode):
                raise DesktopError(f"the bench's folder contains a directory link: {path}")
        for name in sorted(files):
            path = os.path.join(root, name)
            if not stat.S_ISREG(os.lstat(path).st_mode):
                raise DesktopError(f"the bench's folder contains a file that is not plain: {path}")
            out.append((os.path.relpath(path, folder).replace(os.sep, "/"), path))
    return out


def tag(upstream: str) -> str:
    """The tag ``build`` gives: the sha256 of the recipe, the pins and the bench's folder, each path and its bytes
    (and a bench file's mode in the build's context)."""
    h = hashlib.sha256()
    for rel in (DOCKERFILE, REQUIREMENTS):
        with open(os.path.join(HERE, rel), "rb") as f:
            h.update(b"\0" + posixpath.basename(rel).encode() + b"\0" + hashlib.sha256(f.read()).digest())
    for rel, path in _bench_files(bench_folder(upstream)):
        with open(path, "rb") as f:
            h.update(b"\0bench/" + rel.encode() + b"\0" + b"%o" % _mode(path) + hashlib.sha256(f.read()).digest())
    return f"nullius/env-{IMAGE_KEY}-{ENVIRONMENT}:{h.hexdigest()[:12]}"


def _mode(path: str) -> int:
    """A file's mode in the build's context, whatever the checkout's: 755 when executable, else 644."""
    return 0o755 if os.stat(path).st_mode & 0o111 else 0o644


def _image_id(reference: str) -> Optional[str]:
    try:
        r = _engine(["image", "inspect", "--format", "{{.Id}}", reference], timeout=INSPECT_SECONDS)
    except subprocess.TimeoutExpired:
        raise DesktopError(f"{container.DOCKER} image inspect did not answer in {INSPECT_SECONDS} s")
    if r.returncode != 0:
        return None
    return r.stdout.decode("utf-8", "replace").strip() or None


def image(option: str, upstream: str, log_dir: Optional[str] = None) -> Tuple[str, str]:
    """``(reference, image id)`` of the desktop's image. ``option`` is ``build`` or the name of an image this machine
    holds; a name is inspected, never pulled. ``build`` builds the recipe once, under a lock across processes, its
    output in ``log_dir``'s ``env-desktop.log``; it may last hours (:data:`BUILD_SECONDS`)."""
    if option != "build":
        found = _image_id(option)
        if found is None:
            raise DesktopError(f"this machine holds no image {option}: name one it holds, or build")
        return option, found
    reference = tag(upstream)
    found = _image_id(reference)
    if found is not None:
        return reference, found
    import fcntl
    lock_path = os.path.join(tempfile.gettempdir(), f"nullius-{IMAGE_KEY}-{ENVIRONMENT}.lock")
    end = time.monotonic() + BUILD_SECONDS
    with open(lock_path, "a") as lock:
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() > end:
                    raise DesktopError(f"another build of {reference} held its lock for {BUILD_SECONDS} s")
                time.sleep(2)
        try:
            found = _image_id(reference)                # built by the process that held the lock
            if found is None:
                _build(reference, bench_folder(upstream), log_dir, end)
                found = _image_id(reference)
            if found is None:
                raise DesktopError(f"the build of {reference} ended, and the image is not there")
            return reference, found
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _build(reference: str, bench: str, log_dir: Optional[str], end: float) -> None:
    ctx = tempfile.mkdtemp(prefix="nullius-cdb-image-")
    try:
        for rel in (DOCKERFILE, REQUIREMENTS):
            shutil.copyfile(os.path.join(HERE, rel), os.path.join(ctx, posixpath.basename(rel)))
        for rel, path in _bench_files(bench):
            os.makedirs(os.path.dirname(os.path.join(ctx, "bench", rel)), exist_ok=True)
            shutil.copy2(path, os.path.join(ctx, "bench", rel))
        for root, dirs, files in os.walk(ctx):         # the builder's cache keys on modes: none of the checkout's
            for name in dirs:
                os.chmod(os.path.join(root, name), 0o755)
            for name in files:
                os.chmod(os.path.join(root, name), _mode(os.path.join(root, name)))
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        log = open(os.path.join(log_dir, f"env-{ENVIRONMENT}.log"), "ab") if log_dir else tempfile.TemporaryFile()
        with log:
            log.write(f"# {reference}\n".encode())
            log.flush()
            p = _spawn(["build", "--label", f"nullius.env={IMAGE_KEY}/{ENVIRONMENT}", "-t", reference,
                        "-f", os.path.join(ctx, posixpath.basename(DOCKERFILE)), ctx],
                       stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
            try:
                rc = p.wait(timeout=max(1.0, end - time.monotonic()))
            except BaseException:
                p.kill()
                p.wait()
                raise
            finally:
                _done(p)
            if rc != 0:
                log.seek(0)
                said = log.read()[-300:].decode("utf-8", "replace") if log_dir is None else f"see {log.name}"
                raise DesktopError(f"building {reference} failed (exit {rc}): {said}")
    except subprocess.TimeoutExpired:
        raise DesktopError(f"building {reference} did not end in {BUILD_SECONDS} s")
    finally:
        shutil.rmtree(ctx, ignore_errors=True)


def _bench_tar(folder: str) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        for rel, path in _bench_files(folder):
            info = t.gettarinfo(path, rel)
            if not info.isreg():
                continue                                # plain files alone: the pin's folder has no other
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            with open(path, "rb") as f:
                t.addfile(info, f)
    return buf.getvalue()


# -- a desktop ----------------------------------------------------------------------------------------------------
class Desktop:
    """One running desktop, by its container's name; one trial."""

    def __init__(self, name: str):
        self.name = name
        self.env: Dict[str, str] = {}
        self._launcher: Optional[subprocess.Popen] = None
        self._said: Optional[io.BufferedRandom] = None
        self._row: Optional[dict] = None
        self._lock = threading.Lock()
        self._reaper: Optional[subprocess.Popen] = None

    def _tell(self, word: str) -> None:
        """A line to this desktop's reaper: the container's id, or ``removed``, which lets the reaper go."""
        p = self._reaper
        with contextlib.suppress(AttributeError, OSError, ValueError, subprocess.TimeoutExpired):
            p.stdin.write(word.encode() + b"\n")
            p.stdin.flush()
            if word == "removed":
                p.stdin.close()
                p.wait(timeout=5)
                self._reaper = None

    @classmethod
    def start(cls, reference: str, *, job: Optional[str] = None, seconds: float = START_SECONDS,
              name: Optional[str] = None, memory_mb: Optional[int] = None) -> "Desktop":
        """Start a desktop of ``reference`` and wait until its window manager runs, at most ``seconds``. ``job``
        labels the container as a unit's are, so a sweep of the job removes it; ``memory_mb`` is the engine's
        memory limit of the container, the machine's own kept where lower. A failed or interrupted start
        removes what it made, and a reaper (``reaper.py``) removes the container if this process goes first."""
        end = time.monotonic() + seconds
        d = cls(name or f"nullius-cdb-{secrets.token_hex(4)}")
        with contextlib.suppress(OSError):
            d._reaper = subprocess.Popen([sys.executable, "-I", os.path.join(HERE, "reaper.py"), container.DOCKER,
                                          d.name], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL, start_new_session=True,
                                         env=container.client_env({"PATH": os.environ.get("PATH", os.defpath)}))
        label = ["--label", f"{container.LABEL}={job}"] if job else []
        identity = tempfile.TemporaryDirectory(prefix="nullius-cdb-start-")
        cidfile = os.path.join(identity.name, "cid")
        try:
            try:
                r = _engine(["run", "-d", "--name", d.name, "--network", "none", "-e", "CUA_RESOLUTION=1920x1080",
                             *label, "--cidfile", cidfile,
                             *container.capped({"memory_mb": memory_mb} if memory_mb else None), reference],
                            timeout=min(RUN_SECONDS, _left(end)))
            except subprocess.TimeoutExpired:
                raise DesktopError(f"starting the desktop {d.name}: {container.DOCKER} run did not answer in time")
            if r.returncode != 0:
                raise DesktopError(f"starting the desktop {d.name}: {_said(r)}")
            with contextlib.suppress(OSError), open(cidfile) as f:
                d._tell(f.read().strip())
            left = _left(end)
            try:
                r = _engine(["exec", "-u", USER, d.name, "python3", "-I", "-c", _SESSION, WINDOW_MANAGER, PORT_PYTHON,
                             str(left)], timeout=left)
            except subprocess.TimeoutExpired:
                r = None
            said = None
            if r is not None and r.returncode == 0:
                try:
                    said = json.loads(r.stdout.decode("utf-8", "replace").strip().splitlines()[-1])
                except (ValueError, IndexError):
                    said = None
            if not isinstance(said, dict) or not isinstance(said.get("env"), dict):
                try:
                    log = _said(_engine(["logs", "--tail", "20", d.name], timeout=min(30, _left(end))))
                except (subprocess.TimeoutExpired, DesktopError):
                    log = "its log could not be read"
                raise DesktopError(f"the desktop {d.name} had no {WINDOW_MANAGER} in {seconds:.0f} s: {log}")
            if not said.get("port"):
                raise DesktopError(f"the image {reference} has no {PORT_PYTHON}: it lacks the port's layer "
                                   f"(build this entry's {DOCKERFILE}, or its last stage on that image)")
            d.env = {k: v for k, v in said["env"].items() if k not in NOT_SESSION}
            _left(end)
        except BaseException:                           # an error or an interrupt: what this start made goes
            made = ""
            with contextlib.suppress(OSError):
                with open(cidfile) as f:
                    made = f.read().strip()
            d._tell(made)
            with contextlib.suppress(DesktopError, subprocess.TimeoutExpired):
                if made:
                    cls(made).remove()
                elif name is None:                      # a run cut before it wrote the id: the name is this start's
                    cls(d.name).remove()
                if made or name is not None:
                    d._tell("removed")
            if d._reaper is not None:
                with contextlib.suppress(OSError, ValueError):
                    d._reaper.stdin.close()
            raise
        finally:
            identity.cleanup()
        return d

    def _exec(self, argv: List[str], *, input: Optional[bytes] = None, timeout: float,
              env: Optional[Dict[str, str]] = None, workdir: Optional[str] = None) -> subprocess.CompletedProcess:
        flags = ["-i"] if input is not None else []
        for k, v in (env or {}).items():
            flags += ["-e", f"{k}={v}"]
        if workdir:
            flags += ["-w", workdir]
        return _engine(["exec", *flags, "-u", USER, self.name, *argv], input=input, timeout=timeout)

    def port_env(self) -> Dict[str, str]:
        """The session's environment, as the port's code runs in it: python-xlib is given an authority file when
        the session names none."""
        env = dict(self.env)
        env.setdefault("XAUTHORITY", PORT_XAUTHORITY)
        return env

    def put_bench(self, folder: str) -> int:
        """The pin's folder (the bench's folder, or an upstream's checkout that holds it) copied over the image's
        checkout as the desktop's user, ``node_modules`` kept; how many entries it held. 120 s at most."""
        data = _bench_tar(bench_folder(folder))
        try:
            r = self._exec(["python3", "-I", "-c", _PUT, BENCH_IN, KEEP], input=data, timeout=PUT_SECONDS,
                           env={"HOME": HOME})
        except subprocess.TimeoutExpired:
            raise DesktopError(f"the bench's folder was not copied into {self.name} in {PUT_SECONDS} s")
        if r.returncode != 0:
            raise DesktopError(f"the bench's folder could not be copied into {self.name}: {_said(r)}")
        return int(r.stdout.decode().strip() or 0)

    def launch(self, task: str, run_index: int, condition: str, *, seconds: float = LAUNCH_SECONDS) -> str:
        """Start the launcher with the gate as its agent command and answer the trial's ``brief.md`` once the gate's
        file exists. :class:`HarnessError` when the launcher ended first with a ``harness_error``; a
        :class:`DesktopError` when it ended first without one, or past ``seconds``."""
        end = time.monotonic() + seconds
        with _locked_until(self._lock, end):
            if self._launcher is not None:
                raise DesktopError(f"the launcher was started already in {self.name}")
            try:
                r = self._exec(["rm", "-rf", OUT, GATE], timeout=min(60, _left(end)))
            except subprocess.TimeoutExpired:
                raise DesktopError(f"the desktop {self.name} did not answer in time")
            if r.returncode != 0:
                raise DesktopError(f"the desktop {self.name} could not clear the trial's folder: {_said(r)}")
            flags = ["-i", "-w", f"{BENCH_IN}/{HARNESS}"]
            for k, v in self.env.items():
                flags += ["-e", f"{k}={v}"]
            self._said = tempfile.TemporaryFile()
            self._launcher = _spawn(["exec", *flags, "-u", USER, self.name, "python3", "-I", LAUNCHER,
                                     "--task", task, "--run-index", str(int(run_index)), "--condition", condition,
                                     "--out", OUT, "--timeout", str(LAUNCHER_TIMEOUT), "--", *GATE_COMMAND],
                                    stdin=subprocess.PIPE, stdout=self._said, stderr=subprocess.STDOUT)
        while True:
            if self._launcher.poll() is not None:
                row = self._read_row(timeout=_left(end))
                _left(end)
                if isinstance(row, dict) and row.get("harness_error"):
                    raise HarnessError(f"the launcher ended before the agent's slot: {row['harness_error']}", row)
                raise DesktopError(f"the launcher ended before the agent's slot (exit {self._launcher.returncode}): "
                                   f"{self.launcher_output()[-300:]}")
            left = end - time.monotonic()
            if left <= 0:
                raise DesktopError(f"the gate did not start in {seconds:.0f} s")
            try:
                r = self._exec(["test", "-e", GATE], timeout=min(left, 30))
            except subprocess.TimeoutExpired:
                raise DesktopError(f"the desktop {self.name} did not answer in time")
            if r.returncode == 0:
                break
            time.sleep(min(POLL, max(0.0, end - time.monotonic())))
        brief = self._read(f"{OUT}/brief.md", timeout=_left(end))
        _left(end)
        if brief is None:
            raise DesktopError(f"the launcher wrote no brief.md in {self.name}")
        return brief.decode("utf-8", "replace")

    def launcher_ended(self) -> bool:
        """Whether the launcher has exited, for example stopped by its guard; at once."""
        p = self._launcher
        return p is not None and p.poll() is not None

    def launcher_output(self) -> str:
        """The end of what the launcher wrote on its own output and errors."""
        f = self._said
        if f is None:
            return ""
        try:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - TAIL))
            return f.read().decode("utf-8", "replace")
        except (OSError, ValueError):
            return ""

    def python(self, code: str, seconds: float = PYTHON_SECONDS) -> Optional[dict]:
        """Run ``code`` as the desktop's user, in the session's environment, on :data:`PORT_PYTHON`:
        ``{status, output, error, returncode}``, or None when it did not end within ``seconds`` (its process group
        is then killed). An error of the code comes back in the reply."""
        req = json.dumps({"code": code, "seconds": seconds, "limit": OUTPUT_LIMIT, "env": self.port_env(),
                          "python": PORT_PYTHON, "cwd": HOME}).encode()
        try:
            r = self._exec(["python3", "-I", "-c", _RUN], input=req, timeout=seconds + ENGINE_GRACE)
        except subprocess.TimeoutExpired:
            return None
        try:
            said = json.loads(r.stdout.decode("utf-8", "replace").strip().splitlines()[-1])
        except (ValueError, IndexError):
            raise DesktopError(f"the desktop {self.name} did not run the code: {_said(r)}")
        if said.get("timeout"):
            return None
        return {"status": "success", "output": said["output"], "error": said["error"], "returncode": said["returncode"]}

    def upload(self, path: str, data: bytes) -> int:
        """Write ``data`` to the absolute ``path`` as the desktop's user, its folders made; the file's size after."""
        if not posixpath.isabs(path):
            raise DesktopError(f"upload to {path}: the path is not absolute")
        try:
            r = self._exec(["python3", "-I", "-c", _UPLOAD, path], input=data, timeout=FILE_SECONDS, env={"HOME": HOME})
        except subprocess.TimeoutExpired:
            raise DesktopError(f"upload to {path}: no answer in {FILE_SECONDS} s")
        if r.returncode != 0:
            raise DesktopError(f"upload to {path}: {_said(r)}")
        return int(r.stdout.decode().strip())

    def read(self, path: str) -> Optional[bytes]:
        """The bytes of ``path`` as the desktop's user reads them, or None when it cannot open it. A path that is no
        regular file, or a file past :data:`READ_LIMIT`, is :class:`Refused`."""
        return self._read(path, timeout=FILE_SECONDS)

    def _read(self, path: str, *, timeout: float) -> Optional[bytes]:
        try:
            r = self._exec(["python3", "-I", "-c", _READ, path, str(READ_LIMIT)], timeout=timeout,
                           env={"HOME": HOME})
        except subprocess.TimeoutExpired:
            raise DesktopError(f"read of {path}: no answer in {timeout} s")
        if r.returncode == 3:
            return None
        if r.returncode == 4:
            raise Refused(f"{path} is not a regular file")
        if r.returncode == 5:
            raise Refused(f"{path} is over {READ_LIMIT // (1024 * 1024)} MiB")
        if r.returncode != 0:
            raise DesktopError(f"read of {path}: {_said(r)}")
        return r.stdout

    def capture(self) -> bytes:
        """The whole screen as PNG; no input is sent."""
        try:
            r = self._exec([PORT_PYTHON, "-I", "-c", _CAPTURE], timeout=CAPTURE_SECONDS, env=self.port_env())
        except subprocess.TimeoutExpired:
            raise DesktopError(f"the capture: no answer in {CAPTURE_SECONDS} s")
        if r.returncode != 0 or not r.stdout.startswith(b"\x89PNG\r\n\x1a\n"):
            raise DesktopError(f"the capture: {_said(r)}")
        return r.stdout

    def _read_row(self, *, timeout: float = FILE_SECONDS) -> Optional[dict]:
        try:
            data = self._read(f"{OUT}/row.json", timeout=timeout)
        except (DesktopError, Refused):
            return None
        try:
            row = json.loads(data) if data is not None else None
        except ValueError:
            return None
        return row if isinstance(row, dict) else None

    def _close_pipe(self) -> None:
        p = self._launcher
        if p is not None and p.stdin is not None:
            with contextlib.suppress(OSError, ValueError):
                p.stdin.close()

    def finish(self, *, seconds: float = FINISH_SECONDS) -> dict:
        """Close the gate's pipe, wait for the launcher's exit (its check and ``row.json``), and answer the row; a
        second call answers the same row. A :class:`DesktopError` when the launcher does not end in ``seconds`` or
        wrote no row."""
        end = time.monotonic() + seconds
        with _locked_until(self._lock, end):
            if self._row is not None:
                return self._row
            p = self._launcher
            if p is None:
                raise DesktopError(f"no launcher was started in {self.name}")
            self._close_pipe()
        try:
            p.wait(timeout=_left(end))
        except subprocess.TimeoutExpired:
            raise DesktopError(f"the launcher did not end in {seconds:.0f} s after its gate was closed")
        finally:
            if p.poll() is not None:
                _done(p)
        row = self._read_row(timeout=_left(end))
        if row is None:
            raise DesktopError(f"the launcher wrote no row.json (exit {p.returncode}): {self.launcher_output()[-300:]}")
        with _locked_until(self._lock, end):
            self._row = self._row or row
            return self._row

    def collect(self, dest: str, *, max_bytes: int = COLLECT_BYTES) -> List[str]:
        """The trial's folder copied into ``dest`` on this machine: plain files alone, no link followed, nothing
        written outside ``dest``, at most ``max_bytes`` in all (a file that does not fit is left out). The names
        kept, relative to the trial's folder. ``dest`` is taken as given, a link on its own path included; a link
        under it is not followed."""
        try:
            os.makedirs(dest, exist_ok=True)
            root = os.path.realpath(dest)
            with _directory(root):
                pass
        except OSError as e:
            raise DesktopError(f"the collection destination could not be opened: {e}")
        top = posixpath.basename(OUT)
        errors = tempfile.TemporaryFile()
        p = _spawn(["cp", f"{self.name}:{OUT}", "-"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=errors)
        late = threading.Event()

        def stop():
            late.set()
            with contextlib.suppress(OSError):
                p.kill()
        timer = threading.Timer(COLLECT_SECONDS, stop)
        timer.daemon = True
        timer.start()
        kept, total = [], 0
        try:
            try:
                with tarfile.open(fileobj=p.stdout, mode="r|") as t:
                    for m in t:
                        rel = _inside(m.name, top)
                        if rel is None or not m.isreg() or total + m.size > max_bytes:
                            continue
                        src = t.extractfile(m)
                        if src is None or not _write_under(root, rel, src):
                            continue
                        kept.append(rel)
                        total += m.size
            except (tarfile.TarError, EOFError, OSError) as e:
                if not late.is_set():
                    p.wait()
                    if p.returncode == 0:
                        raise DesktopError(f"the trial's folder of {self.name} could not be read: {e}")
            p.wait()
        finally:
            timer.cancel()
            if p.poll() is None:
                p.kill()
                p.wait()
            _done(p)
            with contextlib.suppress(OSError):
                p.stdout.close()
        with errors:
            errors.seek(0)
            said = errors.read().decode("utf-8", "replace").strip()[-300:]
        if late.is_set():
            raise DesktopError(f"the trial's folder of {self.name} was not copied in {COLLECT_SECONDS} s")
        if p.returncode != 0:
            raise DesktopError(f"the trial's folder of {self.name} could not be copied: {said}")
        return kept

    def remove(self) -> None:
        """Close the gate's pipe, end the engine's client of the launcher, remove the container and check that it
        is gone. A container gone already is no error; one that stays is a :class:`DesktopError`. Callable twice
        and from another thread."""
        with self._lock:
            self._close_pipe()
            p = self._launcher
        if p is not None and p.poll() is None:
            with contextlib.suppress(OSError):
                p.kill()
            with contextlib.suppress(subprocess.SubprocessError, OSError):
                p.wait(timeout=10)
        if p is not None and p.poll() is not None:
            _done(p)
            if self._said is not None:                  # the launcher's words are kept no longer than its client
                with contextlib.suppress(OSError):
                    self._said.close()
        try:
            r = _engine(["rm", "-f", self.name], timeout=REMOVE_SECONDS, cleanup=True)
        except subprocess.TimeoutExpired:
            raise DesktopError(f"removing the desktop {self.name}: {container.DOCKER} rm did not answer in "
                               f"{REMOVE_SECONDS} s")
        if r.returncode != 0 and not _absent(r):
            raise DesktopError(f"removing the desktop {self.name}: {_said(r)}")
        try:
            r = _engine(["container", "inspect", "--format", "{{.Id}}", self.name], timeout=INSPECT_SECONDS,
                        cleanup=True)
        except subprocess.TimeoutExpired:
            raise DesktopError(f"the desktop {self.name}'s removal could not be checked")
        if r.returncode == 0 or not _absent(r):
            raise DesktopError(f"the desktop {self.name} is still there after its removal: {_said(r)}")
        self._tell("removed")


def _inside(name: str, top: str) -> Optional[str]:
    """An archive member's path under the trial's folder, or None for the folder itself and for any path that would
    leave it."""
    parts = [p for p in name.replace("\\", "/").split("/") if p not in ("", ".")]
    if parts and parts[0] == top:
        parts = parts[1:]
    if not parts or any(p == ".." for p in parts) or name.startswith("/"):
        return None
    return "/".join(parts)


@contextlib.contextmanager
def _directory(path: str):
    """Open each ancestor without following links before making its child."""
    fd = os.open(os.sep, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in os.path.abspath(path).split(os.sep)[1:]:
            if not part:
                continue
            with contextlib.suppress(FileExistsError):
                os.mkdir(part, dir_fd=fd)
            child = os.open(part, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def _write_under(root: str, rel: str, src) -> bool:
    """Replace ``root/rel`` with a fresh file. Refuse linked ancestors and entries that cannot be replaced."""
    path = os.path.join(root, *rel.split("/"))
    with contextlib.ExitStack() as stack:
        try:
            parent = stack.enter_context(_directory(os.path.dirname(path)))
            temporary = f".collect-{secrets.token_hex(8)}"
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644, dir_fd=parent)
        except OSError:
            return False
        try:
            with os.fdopen(fd, "wb") as f:
                shutil.copyfileobj(src, f, 1 << 20)
            try:
                os.replace(temporary, os.path.basename(path), src_dir_fd=parent, dst_dir_fd=parent)
            except OSError:
                return False
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temporary, dir_fd=parent)
    return True
