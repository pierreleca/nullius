"""Cua Driver Bench's driver for the Nullius standard (JSON lines on stdin and stdout).

An agent benchmark on agent.computer/v1. Everything that sets a task up, guards it and checks it is the bench's own
code at the tested commit ($NULLIUS_UPSTREAM): its Linux launcher (automated-eval/macos_bench/launch/xfce_session.py),
run whole and unchanged inside one container of its desktop for each trial. The launcher's agent slot holds a gate, a
command that keeps the trial open while an agent acts through the computer port; this driver serves the port beside
it, with the shared code of the nullius package (standard/computer_port.py), and reads the row the launcher writes.
The desktop's container is ``desktop.py``'s (the definition's command names it first, then the files its image is
built from); this driver calls it alone, never the engine.

units    - every task of the dataset's condition, in the bench's task list's order (taskpack/v2-taskset.json), as
           CONDITION__ID: foreground__CDB-G02, ...
describe - each task's title, its applications and its category, from the task list alone
setup    - the desktop's image (the option desktop_image), a container of it, the pin's bench folder copied in, and
           the launcher started on the task with the run index of the attempt (attempt - 1), the condition and the
           gate. Once the gate runs, the prompt: the task's text as the launcher wrote it (brief.md), a sentence that
           names the computer server, and the bench's two rules as its runner tells its agent. For an agent (not the
           null control) also the port's server over the desktop, in this process, its window before the task open,
           and the loop that runs each step; the reply names the server (the port's pipe joined to its socket), the
           agent's time (the task's own, 360 s) and, as progress, the port's progress file inside the trial
task_given - the agent is about to be given its task: the window before it closes
score    - the turn's end; the loop ends, the gate's input closes, the launcher disarms its witness window, runs the
           task's checker and writes the row. The verdict is the row's: passed, with the checker's partial score, its
           named checks, the guard's violations and the eight measures of the user's window in the detail, beside the
           steps taken, the budget and what ended the loop. A row with a harness error is raised as the environment's
           error, run again by --retries; a row of another trial, or none, as the verifier's. The trial's folder in
           the desktop is copied to verifier/cdb (plain files, 64 MiB at most), and the desktop removed
end      - an agent's unit has ended, scored or not: its desktop, if it still has one, goes

The loop. One step at a time from the port's bridge: each action runs as the desktop's user with pyautogui and time
imported (desktop.Desktop.python), the pause after it, its code and outcome a line of verifier/computer-actions.jsonl.
The bridge takes each step's picture by capture. A step ends the task by done or fail; the steps run out at the
budget (max_steps); the turn's end stops the wait for the next call and the actions not yet run; the agent's time is
the run's, which ends the turn and goes on to score. The launcher may end the trial first, its guard having stopped
the gate (a terminal in front, or one started outside the launcher's own processes): the loop then takes no more
steps, every call is refused, and the progress file says so, so that the run ends the turn a minute later.

What is left when a unit never reaches score: a new setup ends the unit before it, and this driver's exit (its input
ended) ends the one still open. A driver killed outright leaves its desktop running; each has the job's label.
"""
import atexit
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
# driver.py [DESKTOP_PY [DOCKERFILE REQUIREMENTS]]: the definition's command may name the files, so that a host's snapshot
# takes them; the desktop module finds the other two beside itself
DESKTOP_FILE = (sys.argv[1] if len(sys.argv) > 1 and os.path.basename(sys.argv[1]) == "desktop.py"
                else os.path.join(HERE, "desktop.py"))

BENCH_DIR = os.path.join("libs", "cua-driver-bench")                          # the bench's folder in the upstream
TASK_SPECS = os.path.join(BENCH_DIR, "automated-eval", "macos_bench", "probes")  # TASK/task.json: its limits, its site
CONDITIONS = {"linux_foreground": "foreground"}       # each dataset's condition, as the launcher's --condition names it
TASK_ID = re.compile(r"^[A-Z][A-Z0-9]*-[A-Z0-9-]+$")
STEPS, PAUSE = "45", "1.0"                            # the options' defaults, as standard.json declares them
AGENT_SECONDS = 360                                   # the agent's time on a task, when its spec gives none
PYTHON_SECONDS = 90                                   # one action, or one of the bridge's own calls of the desktop
LOOP_SECONDS = PYTHON_SECONDS + 40                    # the loop's end, once the turn has ended (and its last pause)
WATCH_SECONDS = 0.5                                   # how often the loop looks whether the launcher has ended
EVIDENCE_BYTES = 64 * 2 ** 20                         # the trial's folder, copied
PROGRESS = os.path.join("verifier", "computer-progress.json")    # the port's progress file, inside the trial
CALLS = os.path.join("verifier", "computer-calls.jsonl")        # the port's call log
PREPARED = os.path.join("verifier", "computer-prepare.jsonl")   # each prepare call's actions
ACTIONS = os.path.join("verifier", "computer-actions.jsonl")    # each step's actions, as the loop ran them
EVIDENCE = os.path.join("verifier", "cdb")                      # the launcher's trial folder
HELD = os.path.join(".work", "cdb-held")              # in the job: the run's files sent once (computer_port.Held)
LAUNCHER = "launcher"                                 # what ended the loop when the launcher ended the trial first
USE = "Use the tools of the MCP server named computer, given to this session: they show and drive a desktop."
# The bench's two rules, in the words its runner gives its agent at the tested commit: the shared system prompt
# (automated-eval/macos_bench/claude_arms.py, SYSTEM_PROMPT, with system_prompt_for's variant for a task that has a
# local site) and the budget line of run_bench.py (--tell-budget, on by default)
RULES = "Operate only the applications the task names. Do not use the network."
RULES_SITE = "Operate only the applications the task names. Do not use the internet; the task's localhost site is allowed."
BUDGET = "You have about {minutes} minutes and at most {steps} turns."

_DESKTOP = None                                       # the desktop module, loaded at the first setup
_BRIDGE = None                                        # the bridge's class, made at the first agent's setup


def desktop():
    """The desktop module (``DESKTOP_FILE``), loaded once: a listing never needs it."""
    global _DESKTOP
    if _DESKTOP is None:
        spec = importlib.util.spec_from_file_location("nullius_cdb_desktop", DESKTOP_FILE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _DESKTOP = module
    return _DESKTOP


def _port():
    from nullius.standard import computer_port
    return computer_port


def _redact(text):
    from nullius.redact import redact
    return redact(text)


# -- the bench's task list, read with the standard library alone ------------------------------------------------------
def manifest():
    """The bench's task list ($NULLIUS_DATASET, taskpack/v2-taskset.json at the pin)."""
    with open(os.environ["NULLIUS_DATASET"], encoding="utf-8") as f:
        m = json.load(f)
    tasks = m.get("tasks") if isinstance(m, dict) else None
    if not isinstance(tasks, list) or not all(isinstance(t, dict) and TASK_ID.match(str(t.get("id", ""))) for t in tasks):
        raise ValueError("the bench's task list holds no list of tasks, each with a plain id")
    return m


def _condition(dataset):
    if dataset is None:
        return next(iter(CONDITIONS.values()))
    if dataset not in CONDITIONS:
        raise ValueError(f"no dataset {dataset!r} (the datasets: {', '.join(CONDITIONS)})")
    return CONDITIONS[dataset]


def _tasks(condition):
    return [t for t in manifest()["tasks"] if condition in (t.get("conditions") or [])]


def units(dataset=None, **_):
    condition = _condition(dataset)
    return {"units": [f"{condition}__{t['id']}" for t in _tasks(condition)]}


def describe(dataset=None, **_):
    """Each task from the task list alone: its title, its applications in the text, its category."""
    condition, out = _condition(dataset), {}
    for t in _tasks(condition):
        title = " ".join(str(t.get("title") or t["id"]).split())
        apps = [" ".join(str(a).split()) for a in t.get("apps") or [] if str(a).strip()]
        text = title + "." + (" Applications: " + "; ".join(apps) + "." if apps else "")
        out[f"{condition}__{t['id']}"] = {"title": title, "text": text,
                                          "categories": {"category": str(t.get("category") or "")}}
    return {"units": out}


def _task(unit):
    """``(condition, task id, the task's entry)`` of a unit id; a unit the task list does not hold is refused."""
    condition, _, task = str(unit).partition("__")
    entry = next((t for t in manifest()["tasks"] if t["id"] == task), None) if TASK_ID.match(task) else None
    if entry is None or condition not in CONDITIONS.values() or condition not in (entry.get("conditions") or []):
        raise ValueError(f"no unit {unit!r}")
    return condition, task, entry


def _spec(task):
    """The task's own spec in the bench (probes/TASK/task.json): its time and whether it has a local site."""
    path = os.path.join(os.environ["NULLIUS_UPSTREAM"], TASK_SPECS, task, "task.json")
    with open(path, encoding="utf-8") as f:
        spec = json.load(f)
    if not isinstance(spec, dict) or spec.get("id") != task:
        raise ValueError(f"the bench's spec of {task} is not the task's")
    return spec


def _seconds(spec, limits):
    for v in (spec.get("timeout_s"), (limits or {}).get("timeout_s")):
        if isinstance(v, int) and not isinstance(v, bool) and 0 < v <= 6 * 3600:
            return v
    return AGENT_SECONDS


def _option(name, default):
    return os.environ.get("NULLIUS_OPTION_" + name.upper()) or default


def _steps():
    v = _option("max_steps", STEPS)
    if not re.fullmatch(r"[1-9][0-9]{0,2}", v):
        raise ValueError(f"max_steps: {v!r} is not a number of steps from 1 to 999")
    return int(v)


def _pause():
    v = _option("pause", PAUSE)
    if not re.fullmatch(r"[0-9](\.[0-9]{1,2})?", v):
        raise ValueError(f"pause: {v!r} is not a number of seconds under 10")
    return float(v)


def job_of(dir):
    """The job a unit's folder belongs to: <job>/<trial>/workspace."""
    return os.path.basename(os.path.dirname(os.path.dirname(os.path.abspath(dir)))) or None


# -- the port over the trial's desktop ----------------------------------------------------------------------------------
class PortDesktop:
    """The five operations the port's bridge calls, over the trial's desktop; the desktop's refusal of a read is the
    port's (the agent's error)."""

    def __init__(self, d, refused):
        self.d, self.refused = d, refused

    def python(self, code):
        return self.d.python(code, PYTHON_SECONDS)

    def upload(self, path, data):
        self.d.upload(path, data)

    def read(self, path):
        try:
            return self.d.read(path)
        except self.refused as e:
            raise _port().Refused(str(e))

    def capture(self):
        return self.d.capture()

    def password(self):
        return ""


def _bridge_class():
    global _BRIDGE
    if _BRIDGE is not None:
        return _BRIDGE
    port = _port()

    class TrialBridge(port.Bridge):
        """The port's bridge, which the launcher's end also ends: no call is taken any more, the calls waiting are
        refused, and the progress file says what ended the loop."""

        def stop(self, by):
            with self.change:
                if self.ended is None and not self.ended_by:
                    self.ended_by = by
                refused = self._end("the task has ended")
                self._progress()
            for c in refused:
                c.answer(error=self.ended)

    _BRIDGE = TrialBridge
    return TrialBridge


class Unit:
    """One trial: its desktop, the launcher in it and, for an agent, the port's server and the loop."""

    def __init__(self, unit, dir, attempt=None, participant=None):
        self.unit = unit
        self.condition, self.task, self.entry = _task(unit)
        self.run_index = attempt - 1 if isinstance(attempt, int) and not isinstance(attempt, bool) and attempt >= 1 else 0
        self.agent = participant != "control"
        self.trial = os.path.dirname(os.path.abspath(dir))
        self.job = job_of(dir)
        self.spec = _spec(self.task)
        self.seconds = _seconds(self.spec, manifest().get("limits"))
        self.steps = _steps() if self.agent else int(STEPS)
        self.pause = _pause() if self.agent else float(PAUSE)
        self.d = self.bridge = self.server = self.loop = self.watch = self.folder = None
        self.image_id, self.brief, self.error = None, "", None
        self.ending, self.stopped = threading.Event(), threading.Event()

    def path(self, name):
        return os.path.join(self.trial, name)

    def socket(self):
        return os.path.join(self.folder, "s")

    def start(self):
        desk = desktop()
        os.makedirs(self.path("verifier"), exist_ok=True)
        for name in (PROGRESS, CALLS, PREPARED, ACTIONS):        # an earlier try's, in the same folder
            if os.path.lexists(self.path(name)):
                os.remove(self.path(name))
        upstream = os.environ["NULLIUS_UPSTREAM"]
        reference, self.image_id = desk.image(_option("desktop_image", "build"), upstream, self.path("verifier"))
        self.d = desk.Desktop.start(reference, job=self.job)
        self.d.put_bench(os.path.join(upstream, BENCH_DIR))
        self.brief = self.d.launch(self.task, self.run_index, self.condition)
        if self.agent:
            self.serve(desk)

    def serve(self, desk):
        port = _port()
        self.folder = tempfile.mkdtemp(prefix="nullius-cdb-")     # the socket's, this user's alone
        held = os.path.join(os.path.dirname(self.trial), HELD)
        os.makedirs(held, mode=0o700, exist_ok=True)
        self.bridge = _bridge_class()(PortDesktop(self.d, desk.Refused), self.steps, self.pause, desk.SCREEN, desk.FOLDER,
                                      held=port.Held(held), progress=self.path(PROGRESS), window=True,
                                      prepared=self.path(PREPARED))
        self.server = port.Server(self.bridge, self.socket(), log=self.path(CALLS))
        self.loop = threading.Thread(target=self.run_loop, name=f"cdb-loop-{self.task}", daemon=True)
        self.watch = threading.Thread(target=self.watch_launcher, name=f"cdb-watch-{self.task}", daemon=True)
        self.loop.start()
        self.watch.start()

    def prompt(self):
        rules = RULES_SITE if self.spec.get("browser") is True else RULES
        budget = BUDGET.format(minutes=self.seconds // 60, steps=self.steps)
        return f"{self.brief.strip()}\n\n{USE}\n\n{rules}\n{budget}"

    def reply(self):
        if not self.agent:
            return {"prompt": self.prompt()}
        pipe = os.path.join(os.path.dirname(os.path.abspath(_port().__file__)), "computer_pipe.py")
        return {"prompt": self.prompt(), "mcp": [{"name": "computer", "server": "computer", "args": [pipe, self.socket()]}],
                "agent_seconds": self.seconds, "progress": PROGRESS}

    def task_given(self):
        self.bridge.close_window()

    # -- the loop ---------------------------------------------------------------------------------------------------
    def run_loop(self):
        """A step from the bridge, its actions run one at a time with the pause after each, until the bridge takes no
        more (done, fail, the budget, the turn's end, the launcher's end). An action the desktop did not end in time
        leaves its call answered with an error, and the next step goes on."""
        prefix = _port().PREPARE
        try:
            while True:
                response, actions = self.bridge.predict("", None)
                if not actions:                              # the bridge has ended: no call is taken any more
                    break
                step, stop = self.bridge.used, False
                actions = iter(actions)
                try:
                    for action in actions:
                        if action in ("DONE", "FAIL"):
                            stop = True
                            break
                        if self.stopped.is_set():            # the launcher ended the trial: nothing more runs
                            break
                        if action == "WAIT":
                            time.sleep(self.pause)
                            continue
                        start = time.time()
                        reply = self.d.python(prefix.format(code=action), PYTHON_SECONDS)
                        self.record(step, start, action, reply)
                        if reply is None:                    # not ended in time: the call is answered with an error
                            break
                        time.sleep(self.pause)
                finally:
                    close = getattr(actions, "close", None)
                    if close is not None:
                        close()
                if stop:
                    break
        except Exception as e:                               # the desktop failed: the unit says so at score
            self.error = f"{type(e).__name__}: {e}"
        finally:
            self.bridge.finish()

    def record(self, step, start, action, reply):
        """One line of the actions' record: the step, the times, the code and how it ended, credential-shaped
        values replaced."""
        line = {"step": step, "start": start, "end": time.time(), "action": _redact(action),
                "returncode": None if reply is None else reply.get("returncode")}
        if reply is None:
            line["timed_out"] = True
        elif reply.get("returncode"):
            line["error"] = _redact(str(reply.get("error") or reply.get("output") or ""))[-2000:]
        with open(self.path(ACTIONS), "a", encoding="utf-8") as f:
            f.write(json.dumps(line) + "\n")

    def watch_launcher(self):
        """The launcher's end, looked for until the unit ends: its guard stopped the gate, or it failed."""
        while not self.ending.wait(WATCH_SECONDS):
            try:
                ended = self.d.launcher_ended()
            except Exception:                                # a look that failed: the next one may not
                continue
            if ended:
                self.stopped.set()
                self.bridge.stop(LAUNCHER)
                return

    # -- the end ----------------------------------------------------------------------------------------------------
    def end(self, wait=LOOP_SECONDS):
        """The turn's end, the loop's (``wait`` seconds at most), the server's."""
        self.ending.set()
        if self.bridge is not None:
            self.bridge.end_turn()
        if self.loop is not None:
            self.loop.join(wait)
            if self.loop.is_alive() and self.error is None:
                self.error = f"the loop did not end within {wait:g} s of the turn's end"
        if self.watch is not None:
            self.watch.join(WATCH_SECONDS + 5)
        if self.server is not None:
            self.server.close()
            self.server = None

    def keep(self):
        """The launcher's trial folder copied into verifier/cdb: the names kept, or None when the copy failed."""
        if self.d is None:
            return None
        try:
            return self.d.collect(self.path(EVIDENCE), max_bytes=EVIDENCE_BYTES)
        except Exception as e:                               # the copy is evidence; it cannot change the verdict
            sys.stderr.write(f"cua-driver-bench: {self.unit}: the trial's folder was not copied: {e}\n")
            return None

    def remove(self):
        """The desktop and the socket's folder gone; an error is said once both were tried."""
        failed = None
        if self.d is not None:
            try:
                self.d.remove()
            except Exception as e:
                failed = e
            self.d = None
        if self.folder is not None:
            shutil.rmtree(self.folder, ignore_errors=True)
            self.folder = None
        if failed is not None:
            raise failed

    def close(self):
        """The unit ended without its score: everything it holds let go, the loop not waited for."""
        try:
            self.end(wait=1)
        finally:
            self.remove()


OPEN = {}                                    # unit -> Unit, while it is set up and not scored
OPEN_LOCK = threading.Lock()


def close_all(*, shutdown=True):
    """Every unit still open let go, then every client of the engine still running ended."""
    with OPEN_LOCK:
        held = list(OPEN.values())
        OPEN.clear()
    for u in held:
        try:
            u.close()
        except Exception as e:
            sys.stderr.write(f"cua-driver-bench: {u.unit} could not be let go: {e}\n")
    if _DESKTOP is not None:
        _DESKTOP.end_clients(closing=shutdown)


def _setup_failed(e):
    """What a setup that failed says: the launcher's harness error when it ended first."""
    row = getattr(e, "row", None)
    if isinstance(row, dict) and row.get("harness_error"):
        return RuntimeError(f"the bench's launcher ended before the task was ready: {str(row['harness_error'])[:500]}")
    return e


def setup(unit, dir, attempt=None, participant=None, **_):
    close_all(shutdown=False)                    # one unit at a time: the one before never reached score
    u = Unit(unit, dir, attempt, participant)
    with OPEN_LOCK:
        OPEN[unit] = u
    try:
        u.start()
    except BaseException as e:
        with OPEN_LOCK:
            OPEN.pop(unit, None)
        u.keep()                                 # what the launcher wrote, when it ran
        try:
            u.close()
        except Exception as removal:
            sys.stderr.write(f"cua-driver-bench: {unit}: its desktop could not be removed: {removal}\n")
        said = _setup_failed(e) if isinstance(e, Exception) else e
        if said is not e:
            raise said from e
        raise
    return u.reply()


def task_given(unit, dir, **_):
    """The agent is being given its task: the window before it closes."""
    with OPEN_LOCK:
        u = OPEN.get(unit)
    if u is None or u.bridge is None:
        raise ValueError(f"{unit} was not set up for an agent")
    u.task_given()
    return {}


def _checks(evaluation):
    """The checker's named checks, each passed or not: the task's own as bare values or as records with ``passed``."""
    raw = evaluation.get("checks") if isinstance(evaluation, dict) else None
    if not isinstance(raw, dict):
        return {}
    return {str(n): bool(c.get("passed")) if isinstance(c, dict) else bool(c) for n, c in raw.items()}


def verdict(u, row, kept):
    """The row's verdict and its detail; a harness error is the environment's, a row of another trial the
    verifier's."""
    if not isinstance(row, dict):
        raise RuntimeError("VerifierError: the trial's row is not an object")
    if row.get("harness_error"):
        raise RuntimeError("EnvironmentSetupError: the bench's launcher reports a harness error, which is no one's "
                           f"result: {str(row['harness_error'])[:500]}")
    for key, want in (("task", u.task), ("run_index", u.run_index), ("condition", u.condition)):
        if row.get(key) != want:
            raise RuntimeError(f"VerifierError: the trial's row is of {key} {row.get(key)!r}, not {want!r}")
    evaluation = row.get("evaluation") if isinstance(row.get("evaluation"), dict) else {}
    guard = row.get("gui_only") if isinstance(row.get("gui_only"), dict) else {}
    a15 = row.get("a15") if isinstance(row.get("a15"), dict) else {}
    a22 = row.get("a22") if isinstance(row.get("a22"), dict) else {}
    measured = row.get("background") if isinstance(row.get("background"), dict) else {}
    passed = row.get("passed") is True
    b = u.bridge
    detail = {"condition": u.condition, "task": u.task, "run_index": u.run_index, "seed": row.get("seed"),
              "score": evaluation.get("score"), "checks": _checks(evaluation),
              "checker_passed": row.get("checker_passed"), "violations": list(guard.get("violations") or []),
              "criteria": a22.get("criteria"), "unmeasured": a22.get("unmeasured"), "measured": measured.get("measured"),
              "a15": a15, "success_regardless_of_focus": a15.get("success_regardless_of_focus"),
              "timed_out": row.get("timed_out"), "image": u.image_id,
              "steps": b.used if b is not None else 0, "steps_limit": b.steps if b is not None else u.steps,
              "ended": b.ended_by if b is not None else "", "kept": kept}
    if not passed:
        if detail["violations"]:
            detail["why"] = "the guard saw " + "; ".join(f"{v.get('kind')}: {v.get('detail')}" for v in
                                                         detail["violations"][:3] if isinstance(v, dict))
        else:
            failed = [n for n, ok in detail["checks"].items() if not ok]
            detail["why"] = "the task's checker did not pass" + (f": {', '.join(failed)}" if failed else "")
    return {"reward": 1.0 if passed else 0.0, "passed": passed, "detail": detail}


def score(unit, dir, transcript=None, answer=None, **_):
    with OPEN_LOCK:
        u = OPEN.pop(unit, None)
    if u is None:
        raise ValueError(f"{unit} was not set up")
    row = failed = kept = None
    try:
        u.end()
        try:
            row = u.d.finish()
        except Exception as e:
            failed = f"VerifierError: the trial's row could not be read: {e}"
        kept = u.keep()
    finally:
        u.remove()
    if failed:
        raise RuntimeError(failed)
    if u.error:
        raise RuntimeError(f"EnvironmentSetupError: the desktop failed during the task: {u.error}")
    return verdict(u, row, kept)


def end(unit, dir, **_):
    """Release the desktop before this attempt is archived for a retry."""
    close_all(shutdown=False)
    return {}


HANDLERS = {"units": units, "describe": describe, "setup": setup, "task_given": task_given, "score": score, "end": end}


def main():
    from nullius.standard.jsonl import serve
    atexit.register(close_all)
    serve(HANDLERS)


if __name__ == "__main__":
    main()
